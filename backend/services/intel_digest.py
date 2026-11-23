"""
intel_digest — deterministic portfolio intelligence brief (no LLM).

Computes:
  - market backdrop: benchmark price return (always) + crypto proxy (if user holds crypto)
  - return drivers: top positions by signed day/period base-currency value change
  - top movers: by day_change_pct

The narration lives in monitoring/intel_digest_narration.py (LLM seam, interactive-only).

Dependency rule: imports only services/ db/ core/ — no api, no ai, no monitoring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.core.fx import to_base
from backend.services import investments_book
from backend.services import ledger as ledger_service
from backend.services.fx_cache import load_fx_cache
from backend.services.positions import hydrated as hydrated_positions
from backend.services.profile import read_profile

logger = logging.getLogger(__name__)

# Known market-index instrument classes or symbols to try as a benchmark proxy.
# The digest looks for any instrument in the user's InstrumentStore with one of these
# symbols (case-insensitive); the first match is used. Never fabricates a benchmark.
_BENCHMARK_SYMBOLS = ("SPY", "VTI", "IWDA", "ACWI", "QQQ", "CSPX", "ES3.SI", "STI")
_CRYPTO_BENCHMARK_SYMBOLS = ("BTC", "ETH", "bitcoin", "ethereum")


def _find_benchmark_instrument(symbols: tuple[str, ...]) -> dict | None:
    """Find the first matching instrument from a list of symbol candidates."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    for sym in symbols:
        inst = store.find_by_symbol(sym)
        if inst:
            return inst
    return None


def _row_base_value(row: dict, base: str, rates: dict) -> float:
    return ledger_service.row_base_value(row, base, rates=rates)


def _signed_day_change_base(row: dict, base: str, rates: dict) -> float | None:
    """Signed day change in base currency for a single row. None if no prior price."""
    day_change = row.get("day_change")
    if day_change is None:
        return None
    ccy = row.get("quote_currency") or row.get("currency") or base
    return to_base(float(day_change), ccy, base, rates=rates)


def _compute_today_pl(rows: list[dict], base: str, rates: dict) -> dict:
    """Today's P&L of the invested book — read from the ONE authority the Investments masthead
    and Portfolio brief also use (`investments_book.today_move`): invested rows only, percent of
    the prior close. `pl_pct` stays a FRACTION on this wire (the brief card formats it so)."""
    projected, _grand = investments_book.project_invested(rows, base, rates)
    today = investments_book.today_move(projected)
    return {
        "pl_base": today["pl"],
        "pl_pct": round(today["pct"] / 100, 6),
        "has_data": today["has_data"],
    }


def _compute_portfolio_return(period: str) -> dict | None:
    """Portfolio TWR for the digest period, or null when insufficient history.

    Delegates entirely to services.returns.portfolio_returns() (local price_history
    only — no network). Returns {period_return: float} when twr is available,
    null otherwise (front-end handles null gracefully).
    """
    try:
        from backend.services.returns import portfolio_returns

        result = portfolio_returns(period=period)
        twr_block = result.get("twr")
        if twr_block is None:
            return None
        return {"period_return": twr_block["period_return"]}
    except Exception:
        logger.debug("intel_digest: portfolio_return computation failed", exc_info=True)
        return None


def build_digest(
    period: str = "1M",
    rows: list[dict] | None = None,
    rates: dict | None = None,
) -> dict:
    """Build a deterministic intel digest (no LLM, no search, no writes).

    Returns:
    {
      "generated_at": ISO str,
      "base_currency": str,
      "period": str,
      "market_backdrop": {
        "benchmark": <benchmark_return payload | null>,
        "crypto_benchmark": <benchmark_return payload | null>
      },
      "return_drivers": [
        {instrument_id, label, ticker, contribution_base, contribution_pct, direction}
      ],
      "top_movers": [
        {instrument_id, label, ticker, day_change_base, day_change_pct}
      ],
      "today": {
        "pl_base": float,    # signed sum of all day changes in base currency
        "pl_pct": float,     # pl_base / total portfolio base value (fraction, e.g. 0.0076)
        "has_data": bool,    # False when no row has a day_change
      },
      "portfolio_return": {"period_return": float} | null,
      "narration": null,        # filled by narrate() in monitoring/
      "data_quality": {
        "has_price_history": bool,
        "rows_count": int,
        "rows_with_day_change": int,
        "benchmark_sufficient": bool,
        "period": str,
      }
    }
    """
    if rows is None:
        rows = hydrated_positions()
    if rates is None:
        rates = load_fx_cache()

    profile = read_profile()
    base = (profile.get("base_currency") or "SGD").upper()
    period = (period or "1M").upper()

    # Unvested equity-grant units are not owned yet (ADR
    # 2026-07-31-unvested-equity-excluded-from-net-worth) — excluded here for the same reason
    # macro_exposure/risk_signals exclude them: not a real driver/mover, and not part of the
    # denominator other holdings' contributions are measured against.
    liquid = [
        r for r in rows if float(r.get("price") or 0) > 0 and r.get("vesting_state") != "unvested"
    ]
    has_crypto = any((r.get("asset_class") or "").lower() == "crypto" for r in liquid)

    # ── Market backdrop ──────────────────────────────────────────────────────
    benchmark_payload = None
    crypto_benchmark_payload = None
    try:
        from backend.services.returns import benchmark_return
        from backend.services.risk_metrics import benchmark_instrument, preferred_benchmark

        # The user's own benchmark first (the Performance picker) — the SAME index every other
        # "vs the market" figure uses; the fixed list only backs it up when it isn't priced.
        bench_inst = benchmark_instrument(
            preferred_benchmark(profile)
        ) or _find_benchmark_instrument(_BENCHMARK_SYMBOLS)
        if bench_inst:
            ref = bench_inst.get("id") or bench_inst.get("symbol") or ""
            if ref:
                benchmark_payload = benchmark_return(ref, period=period)
    except Exception:
        logger.debug("intel_digest: benchmark return failed", exc_info=True)

    if has_crypto:
        try:
            from backend.services.returns import benchmark_return

            crypto_inst = _find_benchmark_instrument(_CRYPTO_BENCHMARK_SYMBOLS)
            if crypto_inst:
                ref = crypto_inst.get("id") or crypto_inst.get("symbol") or ""
                if ref:
                    crypto_benchmark_payload = benchmark_return(ref, period=period)
        except Exception:
            logger.debug("intel_digest: crypto benchmark return failed", exc_info=True)

    # ── Return drivers ───────────────────────────────────────────────────────
    # Primary: signed day_change × to_base (exact).
    # Degrade: when no day change, rank by weight (position size).
    rows_with_day_change = [r for r in liquid if r.get("day_change") is not None]
    total_base = sum(_row_base_value(r, base, rates) for r in liquid)

    if rows_with_day_change:
        driver_rows = sorted(
            liquid,
            key=lambda r: abs(_signed_day_change_base(r, base, rates) or 0.0),
            reverse=True,
        )
        return_drivers = []
        for row in driver_rows[:10]:
            dc_base = _signed_day_change_base(row, base, rates)
            bv = _row_base_value(row, base, rates)
            return_drivers.append(
                {
                    "instrument_id": row.get("instrument_id") or "",
                    "label": row.get("label") or "",
                    "ticker": row.get("ticker") or "",
                    "contribution_base": round(dc_base or 0.0, 2),
                    "contribution_pct": (
                        round(dc_base / total_base * 100, 2)
                        if total_base and dc_base is not None
                        else 0.0
                    ),
                    "direction": (
                        "up"
                        if (dc_base or 0.0) > 0
                        else ("down" if (dc_base or 0.0) < 0 else "flat")
                    ),
                    "degraded": False,
                }
            )
    else:
        # Degrade: weight-ranked (no day_change available — report as degraded)
        driver_rows = sorted(
            liquid,
            key=lambda r: _row_base_value(r, base, rates),
            reverse=True,
        )
        return_drivers = []
        for row in driver_rows[:10]:
            bv = _row_base_value(row, base, rates)
            return_drivers.append(
                {
                    "instrument_id": row.get("instrument_id") or "",
                    "label": row.get("label") or "",
                    "ticker": row.get("ticker") or "",
                    "contribution_base": None,
                    "contribution_pct": round(bv / total_base * 100, 2) if total_base else 0.0,
                    "direction": "unknown",
                    "degraded": True,
                }
            )

    # ── Top movers ────────────────────────────────────────────────────────────
    mover_rows = sorted(
        [r for r in liquid if r.get("day_change_pct") is not None],
        key=lambda r: abs(float(r.get("day_change_pct") or 0.0)),
        reverse=True,
    )
    top_movers = []
    for row in mover_rows[:5]:
        dc_base = _signed_day_change_base(row, base, rates)
        top_movers.append(
            {
                "instrument_id": row.get("instrument_id") or "",
                "label": row.get("label") or "",
                "ticker": row.get("ticker") or "",
                "day_change_base": round(dc_base, 2) if dc_base is not None else None,
                "day_change_pct": round(float(row.get("day_change_pct") or 0.0), 4),
            }
        )

    benchmark_sufficient = bool(
        benchmark_payload and benchmark_payload.get("data_quality", {}).get("sufficient")
    )

    # ── Today's P&L ──────────────────────────────────────────────────────────
    today_pl = _compute_today_pl(rows, base, rates)

    # ── Portfolio TWR for the period ─────────────────────────────────────────
    portfolio_return = _compute_portfolio_return(period)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_currency": base,
        "period": period,
        "market_backdrop": {
            "benchmark": benchmark_payload,
            "crypto_benchmark": crypto_benchmark_payload,
        },
        "return_drivers": return_drivers,
        "top_movers": top_movers,
        "today": today_pl,
        "portfolio_return": portfolio_return,
        "narration": None,
        "data_quality": {
            "has_price_history": len(liquid) > 0,
            "rows_count": len(liquid),
            "rows_with_day_change": len(rows_with_day_change),
            "benchmark_sufficient": benchmark_sufficient,
            "period": period,
        },
    }
