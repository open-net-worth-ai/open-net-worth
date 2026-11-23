"""watch_stats — what a ticker's own recent history says about today's move. Pure: no I/O, no clock.

The quote snapshot alone can only say "down 9% today". Measured against the stock's OWN recent
behaviour it can say how unusual that is (a 3× usual-move day for a calm utility vs an ordinary
day for a volatile small cap), whether the move is the company or just the market / its sector,
and what the last few weeks look like (a losing streak, a drawdown from a recent peak, a recent
200-day-average cross, the month vs the benchmark).

Inputs are dated daily closes (``[(iso_date, close), …]``, any order) for the stock and the
benchmark, excluding today — today's move comes from the live quote. Too little history → None
(the rules then fall back to fixed % thresholds; nothing is ever estimated from thin data).
"""

from __future__ import annotations

import math

from backend.services.move_significance import LOOKBACK_RETURNS, usual_move
from backend.services.move_significance import daily_returns as _returns
from backend.services.move_significance import stdev as _stdev

DRAWDOWN_WINDOW = 20
MOMENTUM_WINDOW = 21
CROSS_LOOKBACK = 5  # a 200-day cross stays news for this many sessions
MIN_BETA_OVERLAP = 30


def closes_of(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Positive closes, oldest first."""
    return sorted((d, float(c)) for d, c in series if c and float(c) > 0)


def bench_on(dates: list[str], bench: list[tuple[str, float]]) -> list[float] | None:
    """The benchmark's close on each date (the last one on or before it), or None if it doesn't
    reach back to the first date. Lining up by DATE, never by position: a 7-day benchmark (BTC)
    or one with other holidays (VWRA.L) has a different close count over the same weeks."""
    out: list[float] = []
    j, last = 0, None
    for d in dates:
        while j < len(bench) and bench[j][0] <= d:
            last = bench[j][1]
            j += 1
        if last is None:
            return None
        out.append(last)
    return out


def bench_today(
    bench: list[tuple[str, float]], bench_day: float | None, bench_price: float | None
) -> float | None:
    """The benchmark's level now: its live price, else its last close moved by today's %."""
    if bench_price:
        return float(bench_price)
    if not bench or bench_day is None:
        return None
    return bench[-1][1] * (1 + bench_day / 100)


def _beta(
    stock: list[tuple[str, float]], bench: list[tuple[str, float]]
) -> tuple[float, list[float]] | None:
    """(beta, stock-minus-beta×bench daily residuals) over the dates both series have."""
    b = dict(bench)
    common = [(d, c, b[d]) for d, c in stock if d in b]
    common = common[-(LOOKBACK_RETURNS + 1) :]
    if len(common) < MIN_BETA_OVERLAP + 1:
        return None
    rs = _returns([c for _, c, _ in common])
    rb = _returns([x for _, _, x in common])
    mb = sum(rb) / len(rb)
    ms = sum(rs) / len(rs)
    var_b = sum((x - mb) ** 2 for x in rb)
    if var_b <= 0:
        return None
    beta = sum((s - ms) * (x - mb) for s, x in zip(rs, rb)) / var_b
    return beta, [s - beta * x for s, x in zip(rs, rb)]


def _streak(returns: list[float], today: float | None) -> tuple[int, float]:
    """(signed length, cumulative %) of the run of same-direction days ending today."""
    seq = [*returns, today] if today is not None else list(returns)
    if not seq or seq[-1] == 0:
        return 0, 0.0
    sign = 1 if seq[-1] > 0 else -1
    n, growth = 0, 1.0
    for r in reversed(seq):
        if (r > 0) != (sign > 0) or r == 0:
            break
        n += 1
        growth *= 1 + r / 100.0
    return sign * n, (growth - 1) * 100.0


def _recent_200d_cross(closes: list[float]) -> dict | None:
    """The newest cross of the 200-day average within the last CROSS_LOOKBACK closes."""
    if len(closes) < 200 + CROSS_LOOKBACK:
        return None
    for back in range(CROSS_LOOKBACK):
        i = len(closes) - 1 - back
        avg_now = sum(closes[i - 199 : i + 1]) / 200
        avg_prev = sum(closes[i - 200 : i]) / 200
        prev, cur = closes[i - 1], closes[i]
        if prev < avg_prev and cur >= avg_now:
            return {"direction": "up", "days_ago": back + 1}
        if prev >= avg_prev and cur < avg_now:
            return {"direction": "down", "days_ago": back + 1}
    return None


def history_stats(
    series: list[tuple[str, float]],
    *,
    price: float | None,
    day_pct: float | None,
    bench_series: list[tuple[str, float]] | None = None,
    bench_day: float | None = None,
    bench_price: float | None = None,
    sector_day: float | None = None,
) -> dict | None:
    """Everything the rules read from history, or None when there's too little of it."""
    dated = closes_of(series)
    closes = [c for _, c in dated]
    if not price:
        return None
    sigma = usual_move(closes)
    if not sigma:
        return None
    out: dict = {"sigma": round(sigma, 3), "z": None, "beta": None, "move_kind": None}
    if day_pct is not None:
        out["z"] = round(day_pct / sigma, 2)

    beta = _beta(dated, closes_of(bench_series or []))
    if beta is not None and day_pct is not None:
        b, resid = beta
        idio = _stdev(resid[-LOOKBACK_RETURNS:]) or sigma
        market_part = b * bench_day if bench_day is not None else None
        residual = day_pct - market_part if market_part is not None else None
        out.update(beta=round(b, 2), residual=None if residual is None else round(residual, 2))
        out["move_kind"] = _move_kind(day_pct, residual, idio, sigma, sector_day)

    window = [*closes[-DRAWDOWN_WINDOW:], price]
    peak = max(window)
    out["drawdown_20d"] = round((price / peak - 1) * 100, 2) if peak > 0 else None
    out["drawdown_threshold"] = round(-max(10.0, 2 * sigma * math.sqrt(DRAWDOWN_WINDOW)), 2)

    n, cum = _streak(_returns(closes[-12:]), day_pct)
    out["streak"] = {"days": n, "cum_pct": round(cum, 2)}

    if len(closes) > MOMENTUM_WINDOW:
        mom = (price / closes[-MOMENTUM_WINDOW] - 1) * 100
        out["mom_1m"] = round(mom, 2)
        bench_mom = _bench_momentum(dated, bench_series, bench_day, bench_price)
        if bench_mom is not None:
            out["mom_1m_vs_bench"] = round(mom - bench_mom, 2)
        out["mom_threshold"] = round(max(10.0, 2 * sigma * math.sqrt(MOMENTUM_WINDOW)), 2)

    out["cross_200d"] = _recent_200d_cross(closes)
    return out


def _bench_momentum(
    dated: list[tuple[str, float]],
    bench_series: list[tuple[str, float]] | None,
    bench_day: float | None,
    bench_price: float | None,
) -> float | None:
    """The benchmark's % move since the stock's close MOMENTUM_WINDOW sessions back — the same
    anchor date and the same "now" as ``watch_trend``'s 1M caption, so the two never disagree."""
    if bench_day is None:
        return None
    bench = closes_of(bench_series or [])
    start = bench_on([dated[-MOMENTUM_WINDOW][0]], bench)
    now = bench_today(bench, bench_day, bench_price)
    if not start or not now:
        return None
    return (now / start[0] - 1) * 100


def _move_kind(
    day_pct: float,
    residual: float | None,
    idio: float,
    sigma: float,
    sector_day: float | None,
) -> str | None:
    """'market' when the market explains the move, 'sector' when its sector ETF does, else
    'company'. None when there's no benchmark move to compare with."""
    if residual is None:
        return None
    if abs(residual) < idio:
        return "market"
    if sector_day is not None and abs(day_pct - sector_day) < sigma:
        return "sector"
    return "company"
