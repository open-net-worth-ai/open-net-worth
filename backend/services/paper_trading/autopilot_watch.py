"""What starts an autopilot review between its scheduled ones: a paper holding that moved 5% or
more today, one past the mandate's stop loss, or a mandate limit broken — Intel's kind of alert,
sent to the autopilot instead of to the owner. Checked every few minutes for each portfolio on
Auto; each reason starts at most one review a day (``autopilot.trigger``).

Reads the paper book's own valuation and the price service's batched quote snapshot (yesterday's
close beside today's price, the same one-request-per-50-symbols call the watch engine makes);
writes only through ``apply_paper_change``."""

from __future__ import annotations

import logging
from datetime import datetime

from backend.db.instrument_store import InstrumentStore
from backend.db.paper_store import PaperPortfolioStore
from backend.services import prices
from backend.services.paper_trading import autopilot, mandate, service, valuation

logger = logging.getLogger(__name__)

DAY_MOVE = 0.05


def _day_moves(holdings: list[dict]) -> dict[str, float]:
    """{display symbol: today's move as a fraction} for listed holdings with a snapshot."""
    store = InstrumentStore()
    listing: dict[str, str] = {}
    for h in holdings:
        if h.get("kind") != "listed" or not h["asset_key"].startswith("inst:"):
            continue
        inst = store.find_by_id(h["asset_key"][5:]) or {}
        sym = inst.get("price_source_id") or h.get("symbol")
        if sym:
            listing[str(h.get("symbol") or sym)] = sym
    snaps = prices.fetch_quote_snapshots(list(listing.values())) if listing else {}
    out = {}
    for shown, sym in listing.items():
        snap = snaps.get(sym) or {}
        price, prev = snap.get("price"), snap.get("prev_close")
        if price and prev:
            out[shown] = float(price) / float(prev) - 1
    return out


def reasons_for(row: dict, now: datetime) -> dict[str, str]:
    """{key: reason} for every condition that calls for a review now (key = one per day each)."""
    m = row.get("mandate") or {}
    val = valuation.value(row, service.derive(row["id"]), now)
    held = [h for h in val["holdings"] if (h.get("quantity") or 0) > 0]
    out: dict[str, str] = {}
    for sym, move in _day_moves(held).items():
        if abs(move) >= DAY_MOVE:
            verb = "rose" if move > 0 else "fell"
            out[f"move:{sym}"] = f"{sym} {verb} {abs(move) * 100:.1f}% today"
    stop = m.get("stop_loss_pct")
    if stop is not None:
        for h in held:
            if h.get("pnl_pct") is not None and h["pnl_pct"] <= -stop / 100:
                out[f"stop:{h.get('symbol')}"] = (
                    f"{h.get('symbol')} is past the {stop:g}% stop loss "
                    f"({h['pnl_pct'] * 100:.1f}% from cost)"
                )
    status = mandate.status(m, val) or {}
    for r in status.get("rules") or []:
        if not r["ok"] and r["rule"] != "stop_loss_pct":
            out[f"limit:{r['rule']}"] = f"{r['label']} is broken ({r['detail']})"
    return out


def check(now: datetime) -> list[str]:
    """Start a review for every Auto portfolio with a new reason. Returns the ids started."""
    started = []
    for row in PaperPortfolioStore().all():
        a = autopilot.of(row)
        if row.get("status") != "active" or a["mode"] != "auto" or a["paused_reason"]:
            continue
        if a["running_since"]:
            continue
        try:
            found = reasons_for(row, now)
        except Exception:  # noqa: BLE001 — one portfolio's check never stops the others
            logger.exception("autopilot watch: %s failed", row["id"])
            continue
        if found and service.apply_paper_change(
            {"op": "autopilot_trigger", "id": row["id"], "reasons": found}
        ).get("started"):
            started.append(row["id"])
    return started
