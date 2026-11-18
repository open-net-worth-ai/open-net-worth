"""Recent headlines for a paper portfolio's holdings — what an autopilot review reads.

Intel follows the owner's REAL and watched holdings; a paper-only name (one bought only on paper)
has no stored news, so a review saw nothing for it (2026-11-18). This reads each holding's own
feeds instead — the same Google News and Yahoo addresses Intel resolves for a holding
(``services.feeds.resolve_holding_feeds``), fetched over the SSRF-guarded pinned client — and
stores NOTHING: Intel never learns what is held on paper (ADR 2026-11-15-paper-trading-is-a-
simulated-book-never-an-order-path). The addresses are built here from the holding, never chosen
by the model. A feed that fails is skipped, and says so."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from backend.core.pinned_http import pinned_urlopen
from backend.db.instrument_store import InstrumentStore
from backend.services import feeds

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; open-net-worth/1.0; +https://github.com/open-net-worth)"
_MAX_BYTES = 2_000_000
_TTL_S = 900.0  # one fetch per feed per 15 minutes, as Intel's poller does
_cache: dict[str, tuple[float, list[dict]]] = {}
MAX_HOLDINGS = 25
PER_HOLDING = 5
MAX_AGE = timedelta(days=14)


def _fetch(url: str) -> list[dict] | None:
    """One feed's items, or None when it could not be read. Cached per URL."""
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    try:
        with pinned_urlopen(url, headers={"User-Agent": _UA}, timeout=8) as resp:
            items = feeds.parse_rss(resp.read(_MAX_BYTES))
    except Exception as exc:  # noqa: BLE001 — one bad feed never sinks the read
        logger.info("paper news: feed failed %s: %s", url.split("?")[0], type(exc).__name__)
        return None
    _cache[url] = (time.monotonic(), items)
    return items


def _when(item: dict) -> datetime | None:
    raw = item.get("published_date") or ""
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _headlines(items: list[dict], now: datetime) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    dated = [(w, i) for i in items if (w := _when(i)) and now - w <= MAX_AGE]
    for when, item in sorted(dated, key=lambda x: x[0], reverse=True):
        title = (item.get("title") or "").strip()
        key = title.lower()[:80]
        if not title or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": title,
                "source": item.get("source") or "",
                "published": when.date().isoformat(),
                "url": item.get("url") or "",
            }
        )
        if len(out) >= PER_HOLDING:
            break
    return out


def _holding_feeds(holding: dict) -> list[str]:
    key = holding["asset_key"]
    if not key.startswith("inst:") or holding.get("kind") not in ("listed", "fund_nav", "crypto"):
        return []
    inst = InstrumentStore().find_by_id(key[5:]) or {}
    row = {
        "instrument_id": inst.get("id") or key[5:],
        "price_source_id": inst.get("price_source_id") or holding.get("symbol"),
        "label": inst.get("name") or holding.get("name"),
        "asset_class": inst.get("asset_class")
        or ("crypto" if holding["kind"] == "crypto" else "equity"),
    }
    return [f["url"] for f in feeds.resolve_holding_feeds(row) if f.get("url")]


def for_holdings(holdings: list[dict], now: datetime | None = None) -> list[dict]:
    """``[{symbol, name, headlines: [{title, source, published, url}], note}]`` — the newest few
    headlines of the last two weeks per holding, largest holdings first."""
    now = now or datetime.now(timezone.utc)
    ranked = sorted(holdings, key=lambda h: -(h.get("value_home") or 0))[:MAX_HOLDINGS]
    plans = [(h, _holding_feeds(h)) for h in ranked]
    urls = sorted({u for _h, us in plans for u in us})
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict(zip(urls, pool.map(_fetch, urls)))
    out = []
    for h, us in plans:
        got = [fetched[u] for u in us]
        items = [i for g in got if g for i in g]
        heads = _headlines(items, now)
        note = None
        if not us:
            note = "No news feed for this kind of asset."
        elif all(g is None for g in got):
            note = "Its news feeds could not be read just now."
        elif not heads:
            note = "No headlines in the last two weeks."
        out.append(
            {"symbol": h.get("symbol"), "name": h.get("name"), "headlines": heads, "note": note}
        )
    return out
