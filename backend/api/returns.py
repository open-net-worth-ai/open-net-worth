"""
Period-returns read API (P2-INV3 + sys-risk-metrics-engine).

A THIN router over services.returns (and services.risk_metrics) — it validates the period
enum and shapes the response; every figure (the TWR chain-link, the XIRR root-find, the
period $ gain, FX normalization, the data-quality gate, and all risk math) is computed in
the services layer, never here. Respects the dependency rule: imports only services
(api -> services), no store / no FX math directly.

Routes:
    GET /api/returns?period={7D|1M|3M|YTD|1Y|ALL}[&benchmark=<roster key|id|symbol>][&risk=true]
        [&band={equity|crypto|commodity|all_assets}]
        Returns the period-returns payload. With `risk=true`, attaches a `risk` block from
        risk_metrics.portfolio_risk(period, benchmark). Existing callers (no `risk` param)
        are byte-for-byte unaffected — the `risk` key is simply absent.

    GET /api/returns/benchmarks
        Returns the curated benchmark roster (server-authoritative list of {key, label,
        symbol} entries). The FE renders the picker from this — never a hard-coded duplicate.

A bad period → 422 (FastAPI enum validation). Empty data → 200 with twr/mwr/risk null
(never 500), mirroring /api/networth/history. An unresolvable or insufficient benchmark
yields comparison=null / risk.benchmark.resolved=false — never a fabricated number.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi import APIRouter

from backend.services import returns as returns_service
from backend.services import risk_metrics

router = APIRouter()


class Period(str, Enum):
    D7 = "7D"
    M1 = "1M"
    M3 = "3M"
    YTD = "YTD"
    Y1 = "1Y"
    ALL = "ALL"


class Band(str, Enum):
    """One asset type of the book (``services.returns._MARKET_BANDS``), or ``all_assets``: the
    book plus every owned asset."""

    EQUITY = "equity"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    ALL_ASSETS = "all_assets"


@router.get("/api/returns/benchmarks")
def get_benchmarks():
    """The curated benchmark roster — server-authoritative so the FE never hard-codes it.

    Returns [{key, label, symbol}] in display order, including the empty-key 'none' option.
    """
    return {"benchmarks": risk_metrics.benchmark_roster()}


@router.get("/api/returns")
def get_returns(
    period: Period = Period.Y1,
    benchmark: Optional[str] = None,
    risk: bool = False,
    band: Optional[Band] = None,
):
    """Whole-investment-book TWR + MWR/XIRR + period $ gain over the window.

    twr/mwr are null when data_quality.sufficient is false — never a fabricated return.
    Pass `benchmark` (roster key, instrument id, or symbol) to add a `comparison` block.
    Pass `risk=true` to attach a `risk` block with the four risk-adjusted metrics.

    The benchmark param accepts a roster key ("SPY"), an instrument id, or a raw symbol —
    the router maps a roster key through BENCHMARK_ROSTER before handing it to the resolver
    so the FE can send the friendly key while legacy callers still pass an id/symbol.
    """
    # Roster-key alias: if the benchmark param is a roster key, resolve to symbol first.
    resolved_benchmark: str | None = benchmark
    if benchmark and benchmark in risk_metrics.BENCHMARK_ROSTER:
        resolved_benchmark = risk_metrics.BENCHMARK_ROSTER[benchmark] or None

    out = returns_service.portfolio_returns_with_benchmark(
        period.value, benchmark=resolved_benchmark, band=band.value if band else None
    )

    if risk:
        try:
            out["risk"] = risk_metrics.portfolio_risk(period.value, benchmark=benchmark)
        except Exception:
            out["risk"] = None

    return out
