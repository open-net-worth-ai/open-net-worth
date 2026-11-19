"""Portfolio compare — the pure figures for two books side by side: overlap (active share),
how each is spread, sectors, and each line's swings on a weekly grid. Lifted from Copy Trading's
``followed_portfolios/compare.py`` so "You vs them" there and a paper portfolio's Compare tab
compute every figure with the same code and can never disagree. Reads the price service's
sector cache and ``risk_metrics``; writes nothing.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta

from backend.services import prices, risk_metrics

TOP = 5
# Every line is read on one weekly grid: a swing measured on different spacings is not
# comparable. Fewer weeks than this is too short to call a swing.
WEEKS_PER_YEAR = 52
MIN_WEEKS = 8
_BAND_LABEL = {"crypto": "Crypto", "commodity": "Commodities", "fund": "Funds & ETFs"}


def overlap(
    theirs: dict[str, float], yours: dict[str, float], unmatched: dict | None = None
) -> dict:
    shared = sorted((s for s in theirs if s in yours), key=lambda s: -theirs[s])
    return {
        # Active share: how much of one you would trade to turn it into the other (0 = the same
        # holdings at the same weights, 1 = nothing in common), the standard measure.
        "active_share": round(1 - sum(min(theirs[s], yours[s]) for s in shared), 6),
        "shared_count": len(shared),
        # Every position they hold, those with no ticker yet included: a count of the matched
        # ones alone said "8 of 69" for a fund of 6,292 (owner, 2026-11-13).
        "their_count": len(theirs) + (unmatched or {}).get("count", 0),
        "unmatched_count": (unmatched or {}).get("count", 0),
        "unmatched_weight": (unmatched or {}).get("weight", 0.0),
        # How much of THEIR portfolio you also hold, and how much of YOURS that is.
        "their_weight_held": round(sum(theirs[s] for s in shared), 6),
        "your_weight_shared": round(sum(yours[s] for s in shared), 6),
        # Every one, nothing cut (owner, 2026-11-13): a list that stopped at 8 gave the wrong
        # idea of how much you share.
        "shared": [
            {"symbol": s, "theirs": round(theirs[s], 6), "yours": round(yours[s], 6)}
            for s in shared
        ],
    }


def spread(weights: dict[str, float], unmatched: int = 0) -> dict:
    """How a book is spread, in shares, so a fund of thousands and a book of twenty compare
    (owner, 2026-11-13). ``unmatched``: their positions with no ticker yet, still holdings."""
    ranked = sorted(weights.items(), key=lambda x: -x[1])
    return {
        "count": len(ranked) + unmatched,
        "top5": round(sum(w for _s, w in ranked[:TOP]), 6),
        "largest": {"symbol": ranked[0][0], "weight": round(ranked[0][1], 6)} if ranked else None,
        # The five largest, for the side-by-side table: what each is really betting on.
        "top": [{"symbol": s, "weight": round(w, 6)} for s, w in ranked[:TOP]],
    }


def sectors(
    weights: dict[str, float],
    classes: dict[str, str] | None = None,
    own: dict[str, str] | None = None,
) -> list[dict]:
    """Each sector's share, largest first. Crypto and commodities have no sector: each is one
    slice under its own name, never lumped into "Other"."""
    classes = classes or {}
    known = {**prices.company_sectors(list(weights), fetch_missing=False), **(own or {})}
    missing = [s for s in weights if s not in known and classes.get(s, "equity") == "equity"]
    if missing:
        # Looking sectors up takes seconds: the section opens now and has them next time.
        threading.Thread(
            target=prices.company_sectors,
            args=(missing,),
            kwargs={"fetch_missing": True},
            daemon=True,
        ).start()
    by: dict[str, float] = {}
    members: dict[str, list[dict]] = {}
    for sym, w in weights.items():
        sector = sector_label(sym, classes, known)
        by[sector] = by.get(sector, 0.0) + w
        members.setdefault(sector, []).append({"symbol": sym, "weight": round(w, 6)})
    # Each sector names every stock in it, largest first: a share alone left "which stocks are
    # these?" unanswered (owner, 2026-11-14).
    return [
        {
            "sector": s,
            "weight": round(w, 6),
            "holdings": sorted(members[s], key=lambda h: -h["weight"]),
        }
        for s, w in sorted(by.items(), key=lambda x: -x[1])
    ]


def sector_label(sym: str, classes: dict[str, str], known: dict[str, str]) -> str:
    """One holding's sector, by the same rule the sector list sums them with."""
    return _BAND_LABEL.get(classes.get(sym, "")) or known.get(sym) or "Other"


def with_sectors(spread: dict, classes: dict[str, str], own: dict[str, str]) -> dict:
    """Each top holding with its sector: the page colours its bar as that sector's, so the five
    tie to the sector list above them (owner, 2026-11-13)."""
    syms = [t["symbol"] for t in spread["top"]]
    known = {**prices.company_sectors(syms, fetch_missing=False), **own}
    spread["top"] = [
        {**t, "sector": sector_label(t["symbol"], classes, known)} for t in spread["top"]
    ]
    return spread


def weekly(start: str, end: str) -> list[str]:
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=7)
    if out and out[-1] != end:
        out.append(end)
    return out


def levels(line: dict[str, float | None], grid: list[str]) -> list[tuple[str, float]]:
    """``(date, 1 + cumulative return)`` on the grid, carrying the latest value on or before
    each date; dates before the line starts are left out, never invented."""
    dated = sorted((d, v) for d, v in line.items() if v is not None)
    out: list[tuple[str, float]] = []
    i, current = 0, None
    for g in grid:
        while i < len(dated) and dated[i][0] <= g:
            current = dated[i][1]
            i += 1
        if current is not None:
            out.append((g, 1.0 + float(current)))
    return out


def line_risk(levels: list[tuple[str, float]], anchor: list[tuple[str, float]]) -> dict | None:
    if len(levels) < MIN_WEEKS:
        return None
    dates, weekly = period_returns(levels)
    a_dates, a_weekly = period_returns(anchor)
    vol = risk_metrics.volatility_annualized(weekly, factor=WEEKS_PER_YEAR)
    mdd = risk_metrics.max_drawdown([lv / levels[0][1] for _d, lv in levels])
    p, a = risk_metrics.align_vectors(dates, weekly, a_dates, a_weekly)
    beta = risk_metrics.beta_vs(p, a) if len(p) >= MIN_WEEKS else None
    # Return per unit of swing (the risk engine's own Sharpe, its risk-free rate): the line's
    # return over the range, as a yearly rate, against its yearly swings.
    years = len(weekly) / WEEKS_PER_YEAR
    growth = levels[-1][1] / levels[0][1] if levels[0][1] > 0 else None
    yearly = growth ** (1 / years) - 1 if growth and growth > 0 and years > 0 else None
    sharpe = risk_metrics.sharpe(yearly, vol)
    return {
        "volatility": round(vol, 6) if vol is not None else None,
        "max_drawdown": round(mdd, 6) if mdd is not None else None,
        "beta": round(beta, 6) if beta is not None else None,
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
    }


def correlation(a: list[tuple[str, float]], b: list[tuple[str, float]]) -> float | None:
    """Pearson correlation of two lines' weekly returns over the weeks both have."""
    a_dates, a_ret = period_returns(a)
    b_dates, b_ret = period_returns(b)
    x, y = risk_metrics.align_vectors(a_dates, a_ret, b_dates, b_ret)
    n = len(x)
    if n < MIN_WEEKS:
        return None
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / (vx * vy) ** 0.5, 6)


def period_returns(levels: list[tuple[str, float]]) -> tuple[list[str], list[float]]:
    dates: list[str] = []
    out: list[float] = []
    for (_d0, v0), (d1, v1) in zip(levels, levels[1:]):
        if v0 > 0:
            dates.append(d1)
            out.append(v1 / v0 - 1.0)
    return dates, out
