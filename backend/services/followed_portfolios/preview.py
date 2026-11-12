"""A portfolio before anyone follows it: fetched, shaped like a follow and cached, so Discover can
show how each one has done and its page can open without following it first.

A followed portfolio previews from its stored row. Anything else is fetched once and kept for
12 hours; one lock per portfolio stops a page of cards from fetching the same filing twice.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from backend.core.paths import CACHE_DIR
from backend.core.user_time import user_today
from backend.db.followed_portfolio_store import KINDS, FollowedPortfolioStore
from backend.services.followed_portfolios import (
    edgar_13f,
    etf_daily,
    form4,
    house_ptr,
    performance,
    share_classes,
)
from backend.services.followed_portfolios.model import build_model

_TTL_SECONDS = 12 * 3600
_SPARK_POINTS = 48
_SOURCES = {"fund_13f": edgar_13f, "house_member": house_ptr, "etf_daily": etf_daily}
_NORMALIZE = {
    "fund_13f": edgar_13f.normalize_cik,
    "house_member": house_ptr.normalize_ref,
    "etf_daily": etf_daily.normalize_ref,
}
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_summaries: dict[tuple[str, str], tuple[float, str, dict]] = {}
logger = logging.getLogger(__name__)
# Previews outlive a restart: a cold Discover page would otherwise refetch and reprice every
# filing. It is a cache of public data, safe to delete; wall-clock stamps, not monotonic ones.
_DISK = CACHE_DIR / "followed_portfolio_previews.json"
_disk_lock = threading.Lock()


def _load_disk() -> None:
    try:
        data = json.loads(_DISK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    now_wall, now_mono = time.time(), time.monotonic()
    for item in data.get("rows") or []:
        age = now_wall - float(item.get("at") or 0)
        if age < _TTL_SECONDS and isinstance(item.get("row"), dict):
            key = (item["row"]["kind"], item["row"]["source_ref"])
            _cache.setdefault(key, (now_mono - age, item["row"]))
    for item in data.get("summaries") or []:
        age = now_wall - float(item.get("at") or 0)
        if age < _TTL_SECONDS and isinstance(item.get("summary"), dict):
            key = (item["summary"]["kind"], item["summary"]["source_ref"])
            _summaries.setdefault(key, (now_mono - age, str(item.get("day")), item["summary"]))


def _save_disk() -> None:
    now_wall, now_mono = time.time(), time.monotonic()
    data = {
        "rows": [{"at": now_wall - (now_mono - t), "row": row} for t, row in _cache.values()],
        "summaries": [
            {"at": now_wall - (now_mono - t), "day": day, "summary": s}
            for t, day, s in _summaries.values()
        ],
    }
    with _disk_lock:
        try:
            tmp = _DISK.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(_DISK)
        except OSError:
            logger.warning("followed portfolios: preview cache not saved", exc_info=True)


_load_disk()
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: tuple[str, str]) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _featured(kind: str, ref: str) -> dict:
    for r in edgar_13f.featured() + house_ptr.featured() + etf_daily.featured():
        if r["kind"] == kind and r["source_ref"] == ref:
            return {"name": r["name"], "subtitle": r["subtitle"]}
    return {}


def _read_as_now(row: dict) -> bool:
    """False for a member's preview whose reports were read before the reader last changed
    (options were once dropped): it is read again rather than shown as it was."""
    if row.get("kind") != "house_member":
        return True
    return all(
        (r or {}).get("parser") == house_ptr.PARSER_VERSION
        for r in (row.get("reports") or {}).values()
    )


def preview_row(kind: str, ref: str, *, full: bool = False) -> dict:
    """The follow-shaped row for (kind, ref): the stored one when followed, else a cached fetch.
    Raises what the source raises when it cannot be read."""
    if kind not in KINDS:
        from backend.core.errors import ValidationError

        raise ValidationError(f"Unknown kind '{kind}'.")
    ref = _NORMALIZE[kind](ref)
    stored = FollowedPortfolioStore().find_by_source(kind, ref)
    if stored:
        return stored
    key = (kind, ref)
    with _lock(key):
        hit = _cache.get(key)
        fresh = hit and time.monotonic() - hit[0] < _TTL_SECONDS and _read_as_now(hit[1])
        # A card reads two quarters (fast, and Discover has 19 of them); the page, opened one at
        # a time, reads the year of quarters its stock history needs.
        if fresh and (not full or hit[1].get("full_history") or kind != "fund_13f"):
            return hit[1]
        fetched = (
            _SOURCES[kind].fetch(ref, None, edgar_13f.HISTORY_QUARTERS if full else 2)
            if kind == "fund_13f"
            else _SOURCES[kind].fetch(ref)
        )
        if kind == "fund_13f":
            fetched = {**fetched, **form4.refresh_activity(ref)}
        row = {
            "id": f"preview_{kind}_{ref}".replace("|", "_").replace(" ", "_"),
            "kind": kind,
            "source_ref": ref,
            "name": fetched.get("name") or ref,
            "subtitle": fetched.get("subtitle") or "",
            **_featured(kind, ref),
            **{
                k: fetched.get(k)
                for k in (
                    "current",
                    "previous",
                    "activity",
                    "reports",
                    "history",
                    "history_quarters",
                    "all_positions",
                )
            },
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
            "full_history": full,
        }
        _cache[key] = (time.monotonic(), row)
        _save_disk()
        return row


def _downsample(series: list[dict], points: int) -> list[float]:
    if len(series) <= points:
        return [p["portfolio"] for p in series]
    step = (len(series) - 1) / (points - 1)
    return [series[round(i * step)]["portfolio"] for i in range(points)]


_VERB = {"new": "New", "increased": "Added", "reduced": "Trimmed"}


def _dated_moves(trades: list[dict], n: int) -> list[dict]:
    """Newest first, one per stock and direction: an order filled over several days (and
    Form 4s) is one move, not several."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for t in trades:
        key = (t["symbol"], t["side"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "symbol": t["symbol"],
                "verb": "Bought" if t["side"] == "buy" else "Sold",
                "detail": t["date"],
                "up": t["side"] == "buy",
            }
        )
        if len(out) == n:
            break
    return out


def latest_moves(row: dict, model: dict | None, n: int = 2) -> list[dict]:
    """The moves a card shows, newest first: a member's trades; a fund's dated trades made
    after its latest quarter (its Form 4s), else that quarter's largest changes."""
    trades = sorted(
        (
            t
            for t in row.get("activity") or []
            if t.get("counted") and t.get("symbol") and t.get("side") in ("buy", "sell")
        ),
        key=lambda t: str(t.get("date")),
        reverse=True,
    )
    if row["kind"] == "house_member":
        return _dated_moves(trades, n)
    as_of = str((model or {}).get("as_of") or "")
    newer = [t for t in trades if str(t.get("date")) > as_of]
    if newer:
        return _dated_moves(newer, n)
    changed = [
        h
        for h in (model or {}).get("holdings") or []
        if h["change"] != "unchanged" and h.get("change_value")
    ]
    changed.sort(key=lambda h: -abs(h["change_value"]))
    return [
        {
            "symbol": h["symbol"],
            "verb": _VERB[h["change"]],
            "detail": h.get("shares_change_pct"),
            "up": h["change_value"] > 0,
        }
        for h in changed[:n]
    ]


def latest_at(row: dict, model: dict | None) -> str | None:
    """The newest day anything about this portfolio became public: its filing or a trade's."""
    days = [str(t.get("filed_at")) for t in row.get("activity") or [] if t.get("filed_at")]
    if (model or {}).get("filed_at"):
        days.append(str(model["filed_at"]))
    return max(days) if days else None


def cached_figures(kind: str, ref: str) -> dict:
    """What a sort needs from a card, if it was already priced — never fetches (the directory
    lists every portfolio at once). ``{}`` when nothing is cached yet."""
    hit = _summaries.get((kind, ref))
    if not hit or time.monotonic() - hit[0] >= _TTL_SECONDS:
        return {}
    return {"return_12m": hit[2].get("return_12m"), "latest_at": hit[2].get("latest_at")}


def summary(row: dict) -> dict:
    """A card's figures, cached with the row (they change only with prices and filings)."""
    key = (row["kind"], row["source_ref"])
    today = user_today().isoformat()
    hit = _summaries.get(key)
    # A new dated trade (a Form 4) changes the card without a new quarterly filing.
    newest = max((str(t.get("filed_at")) for t in row.get("activity") or []), default="")
    stamp = f"{today}|{(row.get('current') or {}).get('filing_id')}|{newest}"
    if hit and hit[1] == stamp and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[2]
    out = _summary(row)
    if out.get("return_12m") is not None:
        _summaries[key] = (time.monotonic(), stamp, out)
        _save_disk()
    return out


def _summary(row: dict) -> dict:
    model = build_model(share_classes.grouped(row), user_today())
    perf = performance.performance_for(row)
    year = (perf.get("periods") or {}).get("12M") or {}
    ok = year.get("status") == "ok"
    holdings = (model or {}).get("holdings") or []
    return {
        "kind": row["kind"],
        "source_ref": row["source_ref"],
        "name": row["name"],
        "subtitle": row.get("subtitle") or "",
        "estimated": bool(model and model["estimated"]),
        "holdings_count": len(holdings),
        "top": [{"symbol": h["symbol"], "weight": h["weight"]} for h in holdings[:3]],
        "total_value": (row.get("current") or {}).get("total_value")
        if row["kind"] == "fund_13f"
        else None,
        "as_of": (model or {}).get("as_of"),
        "latest_at": latest_at(row, model),
        "filed_at": (model or {}).get("filed_at"),
        "benchmark": perf.get("benchmark"),
        "return_12m": year.get("return") if ok else None,
        "benchmark_12m": year.get("benchmark_return") if ok else None,
        "spark": _downsample(year.get("series") or [], _SPARK_POINTS) if ok else [],
        "moves": latest_moves(row, model),
        "weights": [{"symbol": h["symbol"], "weight": h["weight"]} for h in holdings[:6]],
    }


def warm(entries: list[dict]) -> int:
    """Fetch and price every entry ahead of a visit (the scheduler's job). One failing is skipped."""
    done = 0
    for entry in entries:
        try:
            summary(preview_row(entry["kind"], entry["source_ref"]))
            done += 1
        except Exception:  # a source that is down must not stop the rest
            continue
    return done
