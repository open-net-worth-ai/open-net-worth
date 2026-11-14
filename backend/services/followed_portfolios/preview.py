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
from backend.services.followed_portfolios.model import build_model, normalize_symbol

_TTL_SECONDS = 12 * 3600
# A fund's card reads the five newest quarters: a 1Y return copied report by report needs the
# four filed in the year and the one in force when it began (owner, 2026-11-13). Its page reads
# the full history. Same fetch, a shorter range; each filing is kept, so the page adds only the
# older ones.
CARD_QUARTERS = 5
# Bumped when a summary's shape or meaning changes, so a cached one is rebuilt (2: the count
# is every position, not the top N; 3: no Form 4 "NONE"
# placeholder among its moves; 4: a fund counts every line of its filing,
# 5: its ring weighs each holding against the whole portfolio; 6: the top 8, not 6;
# 7: a newly featured portfolio's short name; 8: a fund's card reads its whole history).
_SUMMARY_VERSION = 8
_SPARK_POINTS = 48
_SOURCES = {"fund_13f": edgar_13f, "house_member": house_ptr, "etf_daily": etf_daily}
_NORMALIZE = {
    "fund_13f": edgar_13f.normalize_cik,
    "house_member": house_ptr.normalize_ref,
    "etf_daily": etf_daily.normalize_ref,
}
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_summaries: dict[tuple[str, str], tuple[float, str, dict]] = {}
# Every card's last figures, however old (wall-clock stamp): shown at once while a fresh one is
# made, instead of an empty card after a restart or a new day (owner, 2026-11-14).
_last: dict[tuple[str, str], tuple[float, str, dict]] = {}
logger = logging.getLogger(__name__)
# Previews outlive a restart: a cold Discover page would otherwise refetch and reprice every
# filing. It is a cache of public data, safe to delete; wall-clock stamps, not monotonic ones.
_DISK = CACHE_DIR / "followed_portfolio_previews.json"
_disk_lock = threading.Lock()


def ever_opened() -> bool:
    """Whether Copy Trading has been used on this install: its first visit writes the previews
    to disk. The background warm-up waits for it (``service.warm_featured``)."""
    return _DISK.exists()


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
        if not isinstance(item.get("summary"), dict):
            continue
        at = float(item.get("at") or 0)
        age = now_wall - at
        key = (item["summary"]["kind"], item["summary"]["source_ref"])
        _last.setdefault(key, (at, str(item.get("day")), item["summary"]))
        if age < _TTL_SECONDS:
            _summaries.setdefault(key, (now_mono - age, str(item.get("day")), item["summary"]))


def _save_disk() -> None:
    now_wall, now_mono = time.time(), time.monotonic()
    data = {
        "rows": [{"at": now_wall - (now_mono - t), "row": row} for t, row in _cache.values()],
        "summaries": [
            {"at": now_wall - (now_mono - t), "day": day, "summary": s}
            for t, day, s in _summaries.values()
        ]
        # A card not made again yet keeps its last figures on disk too.
        + [
            {"at": at, "day": day, "summary": s}
            for key, (at, day, s) in _last.items()
            if key not in _summaries
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


def _without_placeholder_trades(row: dict) -> dict:
    """A row read back from before Form 4 placeholders ("NONE") were refused: a Form 4 trade
    with no real ticker is left out (its next refresh drops it from the store too)."""
    activity = row.get("activity") or []
    kept = [t for t in activity if t.get("source") != "form4" or normalize_symbol(t.get("symbol"))]
    return row if len(kept) == len(activity) else {**row, "activity": kept}


def preview_row(kind: str, ref: str, *, full: bool = False) -> dict:
    """The follow-shaped row for (kind, ref): the stored one when followed, else a cached fetch.
    Raises what the source raises when it cannot be read."""
    if kind not in KINDS:
        from backend.core.errors import ValidationError

        raise ValidationError(f"Unknown kind '{kind}'.")
    ref = _NORMALIZE[kind](ref)
    stored = FollowedPortfolioStore().find_by_source(kind, ref)
    if stored:
        return _without_placeholder_trades(stored)
    key = (kind, ref)
    with _lock(key):
        hit = _cache.get(key)
        fresh = hit and time.monotonic() - hit[0] < _TTL_SECONDS and _read_as_now(hit[1])
        wanted = edgar_13f.HISTORY_QUARTERS if full else CARD_QUARTERS
        if fresh and (kind != "fund_13f" or _quarters_read(hit[1]) >= wanted):
            # Its featured name is read now, not when it was cached: a portfolio featured after
            # its row was fetched shows its short name at once.
            return _without_placeholder_trades({**hit[1], **_featured(kind, ref)})
        # A page opened after its card builds on the card's read: the five newest filings are
        # kept on disk (a filed report never changes), so only the older ones download, and the
        # tickers the card already matched are not asked about again.
        fetched = (
            _SOURCES[kind].fetch(
                ref, {"cusip_map": (hit[1] if hit else {}).get("cusip_map")}, wanted
            )
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
                    "cusip_map",
                )
            },
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
            "quarters_read": wanted if kind == "fund_13f" else None,
        }
        _cache[key] = (time.monotonic(), row)
        _save_disk()
        if kind == "fund_13f":
            # The page opened on its largest holdings' tickers; the rest are looked up now, and
            # once known the next open reads the (kept) filings again to name them, about 1 s.
            edgar_13f.look_up_later(
                ref, fetched.get("lookups_pending") or [], then=lambda: _forget(key)
            )
        return row


_extending: set[tuple[str, str]] = set()
_extending_lock = threading.Lock()
# When reading a fund's older filings last failed: the page stops asking, keeps its five
# quarters, and a later open tries again, so a refusing SEC is never asked every few seconds.
_extend_failed: dict[tuple[str, str], float] = {}
_EXTEND_RETRY_SECONDS = 300


def page_row(kind: str, ref: str) -> tuple[dict, bool]:
    """The row a portfolio's page opens on, and whether it has the whole history. A fund read
    only as far as its card (five quarters) opens on that at once, and its older filings are
    read in the background: the page asks again until it has them (owner, 2026-11-13)."""
    if kind != "fund_13f" or _has_history(kind, ref):
        return preview_row(kind, ref, full=True), True
    row = preview_row(kind, ref)
    if FollowedPortfolioStore().find_by_source(kind, row["source_ref"]):
        return row, True
    failed = _extend_failed.get((kind, row["source_ref"]))
    if failed and time.monotonic() - failed < _EXTEND_RETRY_SECONDS:
        return row, True
    _extend_later(kind, row["source_ref"])
    return row, False


def _has_history(kind: str, ref: str) -> bool:
    hit = _cache.get((kind, _NORMALIZE[kind](ref)))
    return bool(
        hit
        and time.monotonic() - hit[0] < _TTL_SECONDS
        and _quarters_read(hit[1]) >= edgar_13f.HISTORY_QUARTERS
    )


def _extend_later(kind: str, ref: str) -> None:
    """Read the page's whole history on a background thread, once per portfolio at a time."""
    key = (kind, ref)
    with _extending_lock:
        if key in _extending:
            return
        _extending.add(key)

    def run() -> None:
        try:
            preview_row(kind, ref, full=True)
            _extend_failed.pop(key, None)
        except Exception:  # the page keeps its five quarters; a later open asks again
            _extend_failed[key] = time.monotonic()
            logger.warning("followed portfolios: reading older filings failed", exc_info=True)
        finally:
            with _extending_lock:
                _extending.discard(key)

    threading.Thread(target=run, name=f"13f-history-{ref}", daemon=True).start()


def _quarters_read(row: dict) -> int:
    """How many quarters a cached row was read with; a row from before the count was kept
    says only whether it was the page's full read."""
    if row.get("quarters_read"):
        return int(row["quarters_read"])
    return edgar_13f.HISTORY_QUARTERS if row.get("full_history") else 2


def _forget(key: tuple[str, str]) -> None:
    with _lock(key):
        _cache.pop(key, None)
    _save_disk()


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


def kept_summary(kind: str, ref: str) -> dict | None:
    """Today's card, as kept: served as it is, with no call to the source. The scheduler's
    refresh and any open page re-read the filings; a card shown within the day need not."""
    hit = _summaries.get((kind, _NORMALIZE[kind](ref)))
    today = f"{_SUMMARY_VERSION}|{user_today().isoformat()}|"
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS and str(hit[1]).startswith(today):
        return hit[2]
    return None


def last_summary(kind: str, ref: str) -> dict | None:
    """The card's last figures, however old — shown while a fresh one is made."""
    hit = _last.get((kind, _NORMALIZE[kind](ref)))
    return hit[2] if hit else None


def card_ready(kind: str, ref: str) -> bool:
    """Whether this portfolio's card is answered from what is kept, with nothing to fetch: the
    gallery says "loading" only for cards that really go to the SEC or the House. A card shown
    from its last figures while it is made again counts as answered."""
    if kept_summary(kind, ref) is not None or last_summary(kind, ref) is not None:
        return True
    hit = _cache.get((kind, ref))
    return bool(
        hit
        and time.monotonic() - hit[0] < _TTL_SECONDS
        and _read_as_now(hit[1])
        and (kind != "fund_13f" or _quarters_read(hit[1]) >= CARD_QUARTERS)
    )


def cached_figures(kind: str, ref: str) -> dict:
    """What a sort needs from a card, if it was already priced — never fetches (the directory
    lists every portfolio at once). ``{}`` when nothing is cached yet."""
    hit = _summaries.get((kind, ref))
    card = hit[2] if hit and time.monotonic() - hit[0] < _TTL_SECONDS else last_summary(kind, ref)
    if card is None:
        return {}
    return {"return_12m": card.get("return_12m"), "latest_at": card.get("latest_at")}


def summary(row: dict) -> dict:
    """A card's figures, cached with the row (they change only with prices and filings)."""
    key = (row["kind"], row["source_ref"])
    today = user_today().isoformat()
    hit = _summaries.get(key)
    # A new dated trade (a Form 4) changes the card without a new quarterly filing.
    newest = max((str(t.get("filed_at")) for t in row.get("activity") or []), default="")
    stamp = f"{_SUMMARY_VERSION}|{today}|{(row.get('current') or {}).get('filing_id')}|{newest}"
    if hit and hit[1] == stamp and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[2]
    out = _summary(row)
    if out.get("return_12m") is not None:
        _summaries[key] = (time.monotonic(), stamp, out)
        _last[key] = (time.time(), stamp, out)
        _save_disk()
    return out


def _position_count(row: dict, model: dict | None, holdings: list) -> int:
    """Every holding in the filing, as the page counts them: two share classes of one company
    are one, and a line whose ticker a card has not looked up yet still counts."""
    lines = (row.get("current") or {}).get("positions") or []
    if row["kind"] != "fund_13f" or not lines:
        return (model or {}).get("positions") or len(holdings)
    symbols = {normalize_symbol(p.get("symbol")) for p in lines}
    return len(symbols - {""}) + sum(1 for p in lines if not normalize_symbol(p.get("symbol")))


_RING_SLICES = 8


def _ring(row: dict) -> list[dict]:
    """The card's ring: the largest holdings, each with its real share of the WHOLE portfolio
    (a slice's tooltip says it). The card sizes them against each other, so the ring shows a
    fund's biggest bets relative to one another, full even for a fund of hundreds of stocks."""
    every = build_model(row, user_today(), None) or {}
    holdings = every.get("holdings", [])[:_RING_SLICES]
    return [{"symbol": h["symbol"], "weight": h["weight"]} for h in holdings]


def _summary(source: dict) -> dict:
    row = share_classes.grouped(source)
    model = build_model(row, user_today())
    perf = performance.performance_for(source, every_holding=False)
    year = (perf.get("periods") or {}).get("12M") or {}
    ok = year.get("status") == "ok"
    holdings = (model or {}).get("holdings") or []
    return {
        "kind": row["kind"],
        "source_ref": row["source_ref"],
        "name": row["name"],
        "subtitle": row.get("subtitle") or "",
        "estimated": bool(model and model["estimated"]),
        # Every position it holds: ``holdings`` is only the top N the model keeps, and a card
        # looks up tickers for only its largest holdings, so a fund counts its filing's lines.
        "holdings_count": _position_count(row, model, holdings),
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
        "weights": _ring(row),
    }


def warm(entries: list[dict]) -> int:
    """Fetch and price every entry ahead of a visit (the scheduler's job). One failing is skipped."""
    done = 0
    for entry in entries:
        try:
            # The whole page, not only the card: a featured portfolio opens at once, its year of
            # filings and every ticker read ahead of the visit.
            summary(preview_row(entry["kind"], entry["source_ref"], full=True))
            done += 1
        except Exception:  # a source that is down must not stop the rest
            continue
    return done
