"""Repair price history stored in pence (or cents / agorot) before the feed converted it.

Until 2026-11-22 a London listing Yahoo quotes in ``GBp`` was stored as GBP with its pence
figures, so every recorded close for it is 100x too high. The feed now converts at the
boundary (``services/yahoo_units.py``); this rewrites what was already stored.

Self-checking, so it runs on every boot with no marker: for each stored series on a venue
Yahoo quotes in a minor unit, it refetches the daily closes (now in the major currency) and
compares them date by date with what is stored. Only when a stored close sits ~100x above
the fresh one is the series replaced — with the fresh fetch, never by dividing in place, so
a series that mixes old pence points and new pound points comes out whole. A fetch that
fails or returns nothing changes nothing. A series already in pounds matches and is left
alone, which is what makes a second run a no-op.
"""

from __future__ import annotations

import logging
import time

from backend.core.config import get_settings
from backend.core.user_time import user_today

_log = logging.getLogger(__name__)

# Yahoo suffixes of the venues it quotes in a minor unit: London (main + IOB), Johannesburg,
# Tel Aviv. A listing there may still be quoted in the major unit (an LSE ETF in GBP) — the
# date-by-date comparison, not the suffix, decides whether a series is repaired.
_MINOR_UNIT_SUFFIXES = (".L", ".IL", ".JO", ".TA")

# A stored close this many times the fresh one is a pence figure, not a market move.
_LOW_RATIO, _HIGH_RATIO = 30.0, 300.0

_PACE_SECS = 0.5


def _stored_in_minor_unit(stored: list[dict], fresh: list[dict]) -> bool:
    fresh_by_date = {p["date"]: p["price"] for p in fresh if p.get("price")}
    for point in stored:
        fresh_price = fresh_by_date.get(point.get("date"))
        price = point.get("price")
        if fresh_price and price and _LOW_RATIO <= price / fresh_price <= _HIGH_RATIO:
            return True
    return False


def repair_series(price_source_id: str) -> int:
    """Replace one series if it was stored in the minor unit. Returns the points written
    (0 when nothing needed repair or the fetch failed)."""
    from backend.db.price_history_store import PriceHistoryStore
    from backend.services import prices

    store = PriceHistoryStore()
    stored = store.series(price_source_id)
    if not stored:
        return 0
    try:
        fresh = prices._yf_history(price_source_id, prices._BACKFILL_TIMEFRAME, ohlcv=False)
    except Exception as exc:
        _log.warning(
            "minor-unit repair: fetch failed for %s (%s)", price_source_id, type(exc).__name__
        )
        return 0
    fresh = prices._settled_only(fresh, user_today().isoformat())
    if not fresh or not _stored_in_minor_unit(stored, fresh):
        return 0
    store.delete_series(price_source_id)
    written = store.record_backfill_series(price_source_id, fresh)
    _log.info(
        "minor-unit repair: %s re-stored in its major currency (%d point(s) replaced by %d)",
        price_source_id,
        len(stored),
        written,
    )
    return written


def repair_minor_unit_history(*, pace: float = _PACE_SECS) -> dict[str, int]:
    """Check every stored series on a minor-unit venue; ``{price_source_id: points written}``
    for the ones repaired. Never under QA mode (no network there)."""
    if get_settings().onw_qa_mode:
        return {}
    from backend.db.price_history_store import PriceHistoryStore

    try:
        ids = sorted(PriceHistoryStore().latest_map())
    except Exception as exc:
        _log.warning("minor-unit repair: store read failed (%s)", type(exc).__name__)
        return {}
    repaired: dict[str, int] = {}
    for pid in ids:
        if not pid.upper().endswith(_MINOR_UNIT_SUFFIXES):
            continue
        try:
            written = repair_series(pid)
        except Exception as exc:
            _log.warning("minor-unit repair: %s failed (%s)", pid, type(exc).__name__)
            continue
        if written:
            repaired[pid] = written
        time.sleep(pace)
    return repaired
