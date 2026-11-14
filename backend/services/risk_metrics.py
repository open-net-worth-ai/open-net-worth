"""
risk_metrics — risk-adjusted metrics engine (Investments redesign, sys-risk-metrics-engine).

Turns the daily total-return series into the four headline risk numbers:
  Volatility · Beta vs SPY · Sharpe · Max drawdown
plus a curated canonical-benchmark roster and a deterministic plain-English summary.

No LLM, no store, no migration. Pure computed-not-stored read feature — same posture as
returns.py and networth_history.py. All monetary math was already done upstream; this engine
consumes dimensionless fractions only.

Layering: services → db → core (no api/ai import). Sibling imports (returns, daily_total_value,
portfolio_history) are lazy-imported inside functions to avoid cycles.

RFC: docs/rfc/investments/sys-risk-metrics-engine.md

── Annualization factor ────────────────────────────────────────────────────────────────────
The series produced by daily_total_value (and the portfolio_history fallback) is carry-forward
filled: one point per CALENDAR DAY, not per trading day. Therefore the correct annualization
factor is √365, not the equity-convention √252. Using √252 on a calendar-day series would
under-annualize volatility by √(365/252) ≈ 1.20×. ANNUALIZATION_FACTOR = 365.

Resolved open question from RFC §Open Questions: "√252 vs √365 — depends on series grain."
Decision: √365 (calendar grain). See RFC §Implementation Notes.

── Statistical convention ──────────────────────────────────────────────────────────────────
`volatility_annualized` uses SAMPLE standard deviation (÷(n−1)), the standard convention for
return vol estimation from a finite sample. On short windows (e.g. 7-day / 6 observations)
the population estimator (÷n) understates vol by ~10–20%. Beta (cov/var) is unaffected: the
(n−1) factor cancels in both numerator and denominator.
"""

from __future__ import annotations

import logging
import math
from datetime import date

from backend.core.user_time import user_today

_log = logging.getLogger(__name__)

# ── Module constants (single sources of truth, RFC §Schema) ─────────────────────────

RISK_FREE_RATE: float = 0.035  # Sharpe rf; candidate for profile.json later
ANNUALIZATION_FACTOR: int = 365  # calendar-day grain — matches the carry-forward-filled
# series from daily_total_value (one point per cal-day)
MIN_VALUE_POINTS: int = 2  # mirrors returns.MIN_VALUE_POINTS
CANONICAL_BETA_SYMBOL: str = "SPY"  # Beta is always vs this, never the visual benchmark

# Roster key → resolvable instrument symbol. Server-authoritative — the FE renders from
# GET /api/returns/benchmarks so the label list is never hard-coded in two places.
BENCHMARK_ROSTER: dict[str, str] = {
    "SPY": "SPY",  # S&P 500 — market anchor and canonical Beta denominator
    "VWRA": "VWRA",  # Vanguard FTSE All-World (acc)
    "QQQ": "QQQ",  # Nasdaq-100
    "ACWI": "ACWI",  # MSCI All-Country World
    "BTC": "BTC",  # crypto-beta reference
    # "" (none) → no visual benchmark; Beta still computed vs the canonical SPY anchor
}


def preferred_benchmark(profile: dict | None = None) -> str:
    """The user's benchmark key (profile `preferred_benchmark`, set from the Performance picker),
    validated against the roster — the ONE benchmark every "vs the market" figure uses."""
    if profile is None:
        from backend.services.profile import read_profile

        profile = read_profile()
    key = (profile or {}).get("preferred_benchmark") or "SPY"
    return key if BENCHMARK_ROSTER.get(key) else "SPY"


# Sharpe clamping band: prevents a near-zero vol denominator from producing an absurd ratio.
_SHARPE_CLAMP: float = 10.0

# Human labels for the benchmark roster (key → display label for GET /api/returns/benchmarks).
_ROSTER_LABELS: dict[str, str] = {
    "SPY": "SPY • S&P 500",
    "VWRA": "VWRA • Vanguard All-World",
    "QQQ": "QQQ • Nasdaq-100",
    "ACWI": "ACWI • MSCI All-Country World",
    "BTC": "BTC • Bitcoin",
    "": "No benchmark",
}


# ── Pure math helpers ──────────────────────────────────────────────────────────────


def daily_returns(
    series: list[dict],
    flows: list[tuple[str, float]],
) -> list[float]:
    """Per-day total-return fractions of a value series, cash-flows neutralized.

    Reuses returns._subperiod_return (the extracted shared helper) so this vector and the
    TWR chain can NEVER diverge — single code path, not two.

    series — [{date, value}] sorted ascending (same shape as returns.value_series output)
    flows  — [(iso_date, amount)] pocket-convention (deposit = negative amount)

    Returns a list of floats one shorter than `series` (no return on the first point).
    Sub-periods where the previous value ≤ 0 contribute 0.0 (no base → neutral, not None).
    """
    from backend.services.returns import _subperiod_return

    result: list[float] = []
    for i in range(1, len(series)):
        v_prev = float(series[i - 1]["value"])
        v_now = float(series[i]["value"])
        t0 = series[i - 1]["date"]
        t1 = series[i]["date"]
        net_added = -sum(amt for (d, amt) in flows if t0 < d <= t1)
        r = _subperiod_return(v_prev, v_now, net_added)
        result.append(r if r is not None else 0.0)
    return result


def volatility_annualized(
    daily: list[float],
    factor: int = ANNUALIZATION_FACTOR,
) -> float | None:
    """Annualized volatility: sample_stdev(daily) × √factor.

    Uses sample variance ÷(n−1) — the standard estimator for return series. On a 1-return
    window the sample variance is undefined, so we require at least MIN_VALUE_POINTS (2)
    return observations. Fewer returns → None (not 0.0, which would be a false "calm" signal).
    """
    n = len(daily)
    if n < MIN_VALUE_POINTS:
        return None
    mean = sum(daily) / n
    variance = sum((x - mean) ** 2 for x in daily) / (n - 1)
    sigma = math.sqrt(variance)
    return sigma * math.sqrt(factor)


def beta_vs(daily: list[float], anchor_daily: list[float]) -> float | None:
    """cov(daily, anchor_daily) / var(anchor_daily) over the ALIGNED window.

    The two vectors must already be date-aligned (same length, same dates). Alignment
    is performed by the caller (portfolio_risk) — here we just do the math.

    None when the aligned window is too short or var(anchor) == 0 (flat anchor series).
    """
    n = len(daily)
    if n < MIN_VALUE_POINTS or len(anchor_daily) != n:
        return None
    mean_p = sum(daily) / n
    mean_a = sum(anchor_daily) / n
    cov = sum((daily[i] - mean_p) * (anchor_daily[i] - mean_a) for i in range(n)) / n
    var_a = sum((anchor_daily[i] - mean_a) ** 2 for i in range(n)) / n
    if var_a == 0:
        return None
    return cov / var_a


def sharpe(
    annualized_return: float | None,
    annualized_vol: float | None,
    rf: float = RISK_FREE_RATE,
) -> float | None:
    """(annualized_return − rf) / annualized_vol, clamped to [-_SHARPE_CLAMP, _SHARPE_CLAMP].

    None when vol is None or effectively zero (< 1e-9).
    """
    if annualized_vol is None or annualized_vol < 1e-9:
        return None
    if annualized_return is None:
        return None
    raw = (annualized_return - rf) / annualized_vol
    return max(-_SHARPE_CLAMP, min(_SHARPE_CLAMP, raw))


def max_drawdown(cumulative_path: list[float]) -> float | None:
    """min_t (level_t / running_peak_t − 1) on the TR cumulative path (1-based levels).

    cumulative_path — list of tr_index values (e.g. 1.0, 1.05, 0.98, …); None values skipped.
    None on an empty/degenerate path or when fewer than MIN_VALUE_POINTS non-None levels.
    Always ≤ 0.
    """
    levels = [v for v in cumulative_path if v is not None]
    if len(levels) < MIN_VALUE_POINTS:
        return None
    peak = levels[0]
    mdd = 0.0
    for level in levels[1:]:
        if level > peak:
            peak = level
        if peak > 0:
            dd = level / peak - 1.0
            if dd < mdd:
                mdd = dd
    return mdd


def risk_summary(
    vol: float | None,
    beta: float | None,
    sharpe_ratio: float | None,
    mdd: float | None,
    short_window: bool = False,
) -> str | None:
    """Deterministic plain-English one-liner. The ONLY place risk phrasing lives.

    Returns None when all metrics are None (nothing to say). The `short_window` flag tags
    Sharpe as "(short window)" when the span < 360d (cumulative vs annualized basis mismatch).
    """
    if all(v is None for v in (vol, beta, sharpe_ratio, mdd)):
        return None

    parts: list[str] = []

    if sharpe_ratio is not None:
        if sharpe_ratio >= 2.0:
            parts.append("excellent return for the risk taken")
        elif sharpe_ratio >= 1.0:
            parts.append("solid return for the risk taken")
        elif sharpe_ratio >= 0.0:
            parts.append("modest return for the risk taken")
        else:
            parts.append("return below the risk-free rate")
        if short_window:
            parts[-1] += " (short window)"

    if beta is not None:
        pct = abs(round((beta - 1.0) * 100))
        if beta > 1.05:
            parts.append(f"swings ~{pct}% more than the market")
        elif beta < 0.95:
            parts.append(f"swings ~{pct}% less than the market")
        else:
            parts.append("tracks the market closely")

    if mdd is not None:
        pct = round(abs(mdd) * 100)
        parts.append(f"worst dip ~−{pct}%")

    if not parts and vol is not None:
        pct = round(vol * 100, 1)
        parts.append(f"annualized volatility ~{pct}%")

    return " • ".join(parts) if parts else None


# ── Benchmark roster (server-authoritative) ────────────────────────────────────────


def benchmark_roster() -> list[dict]:
    """The curated benchmark roster — one entry per key including the empty-key 'none' option.

    Returned in display order so the FE can render the picker without sorting.
    """
    order = ["SPY", "VWRA", "QQQ", "ACWI", "BTC", ""]
    return [
        {
            "key": k,
            "label": _ROSTER_LABELS.get(k, k),
            "symbol": BENCHMARK_ROSTER.get(k, ""),
        }
        for k in order
    ]


# ── Internal helpers ────────────────────────────────────────────────────────────────


def _align_vectors(
    port_dates: list[str],
    port_returns: list[float],
    anchor_dates: list[str],
    anchor_returns: list[float],
) -> tuple[list[float], list[float]]:
    """Return the date-intersection of two (date, return) vectors, sorted ascending.

    Only dates present in BOTH vectors are kept. The returns at those dates are aligned.
    """
    anchor_map = dict(zip(anchor_dates, anchor_returns))
    aligned_p: list[float] = []
    aligned_a: list[float] = []
    for d, r in zip(port_dates, port_returns):
        if d in anchor_map:
            aligned_p.append(r)
            aligned_a.append(anchor_map[d])
    return aligned_p, aligned_a


# Public for a caller that lines up its own two return vectors (the followed-portfolio compare).
align_vectors = _align_vectors


def _daily_return_series_from_value_series(
    start: str,
    end: str,
) -> tuple[list[str], list[float], list[float | None], bool]:
    """Derive the daily return vector + TR cumulative path from the value series.

    First tries the income-overlaid series from daily_total_value (the rich feed).
    Falls back gracefully to price-only via portfolio_history if the feed is empty.

    Returns (dates, daily_returns_list, tr_index_list, income_overlay_flag).
    dates and daily_returns have the same length (N-1 where N = value points);
    tr_index has length N (the full path including the first point).
    """
    try:
        from backend.services import daily_total_value as dtv

        result = dtv.total_value_series(start, end)
        series = result.get("series") or []
        if len(series) >= MIN_VALUE_POINTS:
            dates = [pt["date"] for pt in series[1:]]  # skip first (no return on day 0)
            dr = [pt["daily_return"] for pt in series[1:]]
            daily = [float(r) if r is not None else 0.0 for r in dr]
            tr_path = [pt.get("tr_index") for pt in series]
            return dates, daily, tr_path, True
    except Exception:
        _log.debug("daily_total_value unavailable; falling back to price-only series")

    from backend.core.fx import base_currency
    from backend.services import portfolio_history as ph
    from backend.services.returns import cash_flows as _cash_flows

    ph_result = ph.portfolio_value_series(start, end, "daily")
    pts = ph_result.get("series") or []
    if len(pts) < MIN_VALUE_POINTS:
        return [], [], [], False

    # Map to the {date, value} shape expected by daily_returns().
    series = [{"date": pt["date"], "value": pt.get("value_base") or 0} for pt in pts]

    # Load real cash flows so deposits/withdrawals are neutralized, not treated as returns.
    # This mirrors how portfolio_returns() uses cash_flows() — same flow convention.
    try:
        flows, _ = _cash_flows(start, end, base_currency())
    except Exception:
        flows = []

    dates = [pt["date"] for pt in series[1:]]
    daily = daily_returns(series, flows)

    # Build a simple 1-based TR path for max_drawdown.
    base_val = float(pts[0].get("value_base") or 0)
    tr_path: list[float | None] = []
    for pt in pts:
        v = float(pt.get("value_base") or 0)
        tr_path.append(round(v / base_val, 8) if base_val > 0 else None)

    return dates, daily, tr_path, False


def _benchmark_daily_returns(
    symbol: str, start: str, end: str
) -> tuple[list[str], list[float]] | None:
    """Fetch a benchmark's daily price-return vector over [start, end].

    Returns (dates, daily_returns) or None when the instrument is not found, has no
    price_source_id, or has too few price points — graceful degrade, no synthetic data.
    """
    from backend.db.instrument_store import InstrumentStore
    from backend.services.returns import benchmark_price_series

    inst = InstrumentStore().find_by_symbol(symbol)
    if not inst:
        return None

    pid = inst.get("price_source_id") or ""
    if not pid:
        return None

    prices = benchmark_price_series(pid, start, end)
    if len(prices) < MIN_VALUE_POINTS:
        return None

    dates: list[str] = []
    returns: list[float] = []
    for i in range(1, len(prices)):
        p0 = float(prices[i - 1]["price"])
        p1 = float(prices[i]["price"])
        if p0 > 0:
            dates.append(prices[i]["date"])
            returns.append(p1 / p0 - 1.0)
    return (dates, returns) if len(dates) >= MIN_VALUE_POINTS else None


def _spy_daily_returns(start: str, end: str) -> tuple[list[str], list[float]] | None:
    """Fetch SPY's daily price-return vector over [start, end] for Beta computation.

    Returns (dates, daily_returns) or None when SPY is not in InstrumentStore or has
    too few price points — graceful degrade (`spy_anchored=false`), no synthetic data.
    """
    return _benchmark_daily_returns(CANONICAL_BETA_SYMBOL, start, end)


# ── Public orchestrator ────────────────────────────────────────────────────────────


def portfolio_risk(
    period: str = "1Y",
    benchmark: str | None = None,
    *,
    today: date | None = None,
) -> dict:
    """Compute the risk-metrics block for the §schema dict.

    Pulls the daily total-return vector + TR cumulative path from the series feed /
    portfolio_history, computes Beta against the SELECTED benchmark (the user's visual
    pick), or falls back to SPY when no benchmark is selected or it has no price series.

    Beta denominator selection:
      - benchmark provided and resolvable with sufficient price series → use it
      - otherwise fall back to the canonical SPY anchor

    Returns the §schema dict. On any exception the outer handler should catch and return
    null (see api/returns.py); internally we return a null-filled dict on data-insufficient
    paths so the caller always gets the full shape.
    """
    from backend.services import returns as returns_svc
    from backend.services.returns import _resolve_benchmark_instrument

    t = today or user_today()
    start, end = returns_svc.resolve_period(period, today=t)

    # ── Daily return vector + TR path ────────────────────────────────────────────
    port_dates, port_daily, tr_path, income_overlay = _daily_return_series_from_value_series(
        start, end
    )

    value_points = len(port_dates)
    sufficient = value_points >= MIN_VALUE_POINTS

    # ── Volatility ────────────────────────────────────────────────────────────────
    vol = volatility_annualized(port_daily) if sufficient else None

    # ── Beta — use the selected benchmark, fall back to SPY ───────────────────────
    # Resolve the visual benchmark symbol first so we can try it before falling back.
    beta_symbol = CANONICAL_BETA_SYMBOL  # default
    if benchmark:
        bench_symbol_candidate = BENCHMARK_ROSTER.get(benchmark, benchmark)
        if bench_symbol_candidate:
            beta_symbol = bench_symbol_candidate

    beta_result = _benchmark_daily_returns(beta_symbol, start, end)
    # If the selected benchmark had no usable series, fall back to the canonical anchor.
    if beta_result is None and beta_symbol != CANONICAL_BETA_SYMBOL:
        beta_symbol = CANONICAL_BETA_SYMBOL
        beta_result = _benchmark_daily_returns(CANONICAL_BETA_SYMBOL, start, end)

    beta_anchored = beta_result is not None
    beta: float | None = None
    if beta_anchored and sufficient:
        beta_dates, beta_daily = beta_result
        aligned_p, aligned_a = _align_vectors(port_dates, port_daily, beta_dates, beta_daily)
        if len(aligned_p) >= MIN_VALUE_POINTS:
            beta = beta_vs(aligned_p, aligned_a)
        else:
            beta_anchored = False

    # ── Sharpe ────────────────────────────────────────────────────────────────────
    # Annualized return from portfolio TWR (consistent with what the user sees on screen).
    sharpe_val: float | None = None
    short_window = False
    try:
        port_ret = returns_svc.portfolio_returns(period, today=t)
        twr_block = port_ret.get("twr")
        annualized_flag = bool(port_ret.get("annualized", False))
        if twr_block is not None:
            ann_ret = (
                twr_block.get("annualized") if annualized_flag else twr_block.get("period_return")
            )
            short_window = not annualized_flag
            sharpe_val = sharpe(ann_ret, vol)
    except Exception:
        _log.debug("portfolio_returns unavailable for Sharpe numerator")

    # ── Max drawdown ──────────────────────────────────────────────────────────────
    mdd = max_drawdown(tr_path) if tr_path and len(tr_path) >= MIN_VALUE_POINTS else None

    # ── Summary ───────────────────────────────────────────────────────────────────
    summary = risk_summary(vol, beta, sharpe_val, mdd, short_window=short_window)

    # ── Benchmark block (the user's VISUAL pick) ──────────────────────────────────
    benchmark_block: dict = {"key": "", "symbol": "", "instrument_id": "", "resolved": False}
    bench_resolved = False
    if benchmark:
        # Roster-key alias: map a roster key through BENCHMARK_ROSTER, else pass through.
        bench_symbol = BENCHMARK_ROSTER.get(benchmark, benchmark)
        bench_key = benchmark if benchmark in BENCHMARK_ROSTER else ""
        inst = _resolve_benchmark_instrument(bench_symbol)
        if inst:
            benchmark_block = {
                "key": bench_key,
                "symbol": inst.get("symbol") or bench_symbol,
                "instrument_id": inst.get("id") or "",
                "resolved": True,
            }
            bench_resolved = True
        else:
            benchmark_block = {
                "key": bench_key,
                "symbol": bench_symbol,
                "instrument_id": "",
                "resolved": False,
            }

    return {
        "period": period.upper(),
        "volatility": round(vol, 6) if vol is not None else None,
        "beta_vs_spy": round(beta, 6) if beta is not None else None,
        "beta_symbol": beta_symbol,  # the actual symbol beta was computed against
        "sharpe": round(sharpe_val, 6) if sharpe_val is not None else None,
        "max_drawdown": round(mdd, 6) if mdd is not None else None,
        "summary": summary,
        "benchmark": benchmark_block,
        "annualization_factor": ANNUALIZATION_FACTOR,
        "risk_free_rate": RISK_FREE_RATE,
        "data_quality": {
            "value_points": value_points,
            "sufficient": sufficient,
            "spy_anchored": beta_anchored,
            "benchmark_resolved": bench_resolved,
            "fx_approximate": False,  # passthrough placeholder (series feed sets this)
            "has_unrecorded_risk": False,  # passthrough placeholder
            "income_overlay": income_overlay,
        },
    }
