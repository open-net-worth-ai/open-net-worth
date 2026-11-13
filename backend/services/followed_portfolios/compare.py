"""A followed portfolio beside the owner's own book: what they share, how each is spread, and
how each line swings (owner, 2026-11-13). Read-only, and every figure comes from the app's one
authority for it: the owner's holdings and weights from ``investments_book`` (the same
denominator the Portfolio brief and the risk engine use), sectors from the price service's
sector cache (what the Investments allocation reads), returns from ``services.returns``, and
the risk figures from ``risk_metrics``' own functions applied to the lines the chart draws.

``band`` narrows the owner's side to one asset type (``equity``, ``crypto``, ``commodity``):
their stocks against your stocks, or against your crypto, never a whole book with cash in it.
``all_assets`` adds your owned assets to the RETURN line only: they have no tickers, so the
holdings, overlap and sectors read your investments, as with no band.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import investments_book, prices, risk_metrics
from backend.services import positions as positions_service
from backend.services import returns as returns_service
from backend.services.followed_portfolios import performance, preview, share_classes
from backend.services.followed_portfolios.model import build_model, normalize_symbol

_TOP = 5
# The risk row reads every line on one weekly grid: a fund's copied line is weekly in its
# early years and the owner's daily, and a swing measured on different spacings is not
# comparable. Fewer weeks than this is too short to call a swing (the row says so).
_WEEKS_PER_YEAR = 52
_MIN_WEEKS = 8
_MARKET_BANDS = ("equity", "crypto", "commodity")
_BAND_LABEL = {"crypto": "Crypto", "commodity": "Commodities", "fund": "Funds & ETFs"}


def compare(kind: str, ref: str, *, since: str | None = None, band: str | None = None) -> dict:
    row, _complete = preview.page_row(kind, ref)
    theirs = _their_weights(row)
    unmatched = _unmatched(row)
    yours, your_classes, your_sectors = _your_weights(band)
    return {
        "band": band,
        "bands_held": _bands_held(),
        "overlap": _overlap(theirs, yours, unmatched),
        "spread": {
            "theirs": _with_sectors(_spread(theirs, unmatched["count"]), {}, {}),
            "yours": _with_sectors(_spread(yours), your_classes, your_sectors),
        },
        "sectors": {
            "theirs": _sectors(theirs),
            "yours": _sectors(yours, your_classes, your_sectors),
        },
        "risk": _risk(row, since=since, band=band),
    }


# ── Holdings: theirs and yours, as {symbol: weight 0-1} ────────────────────────────────────


def _their_weights(row: dict) -> dict[str, float]:
    """Every holding of their newest report and its share of the whole portfolio."""
    model = build_model(share_classes.grouped(row), user_today(), None) or {}
    out: dict[str, float] = {}
    for h in model.get("holdings") or []:
        sym = normalize_symbol(h.get("symbol"))
        if sym:
            out[sym] = out.get(sym, 0.0) + float(h.get("weight") or 0)
    return out


def _unmatched(row: dict) -> dict:
    """Their positions with no ticker yet (a fund of thousands is matched a few at a time, in the
    background): counted, never dropped, since they cannot be compared with yours until then.
    Two share classes of one company are one position, as the card counts them."""
    current = (share_classes.grouped(row).get("current") or {}) if row.get("current") else {}
    lines = current.get("positions") or []
    total = float(current.get("total_value") or sum(float(p.get("value") or 0) for p in lines))
    loose = [p for p in lines if not normalize_symbol(p.get("symbol"))]
    return {
        "count": len(loose),
        "weight": round(sum(float(p.get("value") or 0) for p in loose) / total, 6)
        if total
        else 0.0,
    }


def _your_weights(
    band: str | None,
) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
    """Your invested book (or one asset type of it): each name's share, the same instrument
    across accounts counted once — ``concentration_by_name`` with every holding kept."""
    rows = positions_service.hydrated()
    projected, grand = investments_book.project_invested(rows, base_currency())
    if band in _MARKET_BANDS:
        projected = [p for p in projected if (p.get("asset_class") or "") == band]
        grand = round(sum(float(p["base_value"]) for p in projected), 2)
    ranked = investments_book.concentration_by_name(projected, grand, 100.0, names_only=False)
    # A diversified fund is many issuers in one holding (the concentration authority's own
    # test): it has no one sector, so it is its own slice.
    classes = {
        normalize_symbol(investments_book.book_symbol(p)): (
            "fund" if investments_book.is_diversified_fund(p) else p.get("asset_class") or ""
        )
        for p in projected
    }
    # Each holding's sector as your book already records it, else as the price service knows its
    # LISTING (a German share is ACME.DE, never the bare ACME it was asked as: a large holding
    # showed as "Other", owner 2026-11-13). The bare symbol is the last resort, in _sectors.
    sectors: dict[str, str] = {}
    by_listing: dict[str, str] = {}
    for p in projected:
        sym = normalize_symbol(investments_book.book_symbol(p))
        if p.get("sector"):
            sectors.setdefault(sym, str(p["sector"]))
        elif p.get("price_source_id") and classes.get(sym) == "equity":
            # Only a single stock has a sector to find: crypto, commodities and funds are
            # slices of their own, and asking about them only failed (BITCOIN, ETHEREUM…).
            by_listing.setdefault(sym, str(p["price_source_id"]))
    if by_listing:
        listed = prices.company_sectors(list(by_listing.values()), fetch_missing=False)
        for sym, listing in by_listing.items():
            if listed.get(listing):
                sectors.setdefault(sym, listed[listing])
        unknown = [listing for listing in by_listing.values() if not listed.get(listing)]
        if unknown:
            # Looked up by the LISTING, in the background, so the next view has them: the bare
            # symbol fails for a foreign share exactly as it did here.
            threading.Thread(
                target=prices.company_sectors,
                args=(unknown,),
                kwargs={"fetch_missing": True},
                daemon=True,
            ).start()
    out: dict[str, float] = {}
    for r in ranked:
        sym = normalize_symbol(r["name"])
        if sym:
            out[sym] = out.get(sym, 0.0) + r["pct"] / 100
    return out, classes, sectors


def _bands_held() -> list[str]:
    """The asset types you hold today, largest first: what "Compare with" offers."""
    rows = positions_service.hydrated()
    projected, _grand = investments_book.project_invested(rows, base_currency())
    totals: dict[str, float] = {}
    for p in projected:
        cls = p.get("asset_class") or ""
        if cls in ("equity", "crypto", "commodity"):
            totals[cls] = totals.get(cls, 0.0) + float(p["base_value"])
    return [c for c, v in sorted(totals.items(), key=lambda x: -x[1]) if v > 0]


def _overlap(
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


def _spread(weights: dict[str, float], unmatched: int = 0) -> dict:
    """How a book is spread, in shares, so a fund of thousands and a book of twenty compare
    (owner, 2026-11-13). ``unmatched``: their positions with no ticker yet, still holdings."""
    ranked = sorted(weights.items(), key=lambda x: -x[1])
    return {
        "count": len(ranked) + unmatched,
        "top5": round(sum(w for _s, w in ranked[:_TOP]), 6),
        "largest": {"symbol": ranked[0][0], "weight": round(ranked[0][1], 6)} if ranked else None,
        # The five largest, for the side-by-side table: what each is really betting on.
        "top": [{"symbol": s, "weight": round(w, 6)} for s, w in ranked[:_TOP]],
    }


def _sectors(
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
    for sym, w in weights.items():
        sector = _sector_label(sym, classes, known)
        by[sector] = by.get(sector, 0.0) + w
    return [
        {"sector": s, "weight": round(w, 6)} for s, w in sorted(by.items(), key=lambda x: -x[1])
    ]


def _sector_label(sym: str, classes: dict[str, str], known: dict[str, str]) -> str:
    """One holding's sector, by the same rule the sector list sums them with."""
    return _BAND_LABEL.get(classes.get(sym, "")) or known.get(sym) or "Other"


def _with_sectors(spread: dict, classes: dict[str, str], own: dict[str, str]) -> dict:
    """Each top holding with its sector: the page colours its bar as that sector's, so the five
    tie to the sector list above them (owner, 2026-11-13)."""
    syms = [t["symbol"] for t in spread["top"]]
    known = {**prices.company_sectors(syms, fetch_missing=False), **own}
    spread["top"] = [
        {**t, "sector": _sector_label(t["symbol"], classes, known)} for t in spread["top"]
    ]
    return spread


# ── Risk: the chart's three lines, on one weekly grid ─────────────────────────────────────


def _risk(row: dict, *, since: str | None, band: str | None) -> dict:
    perf = performance.performance_for(row, every_holding=False)
    fund = ((perf.get("periods") or {}).get("ALL") or {}).get("series") or []
    them = {p["date"]: p.get("portfolio") for p in fund}
    spy = {p["date"]: p.get("benchmark") for p in fund}
    mine_series = returns_service.portfolio_returns_with_benchmark("ALL", band=band).get("series")
    mine = {p["date"]: p.get("portfolio") for p in mine_series or []}
    end = user_today().isoformat()
    start = since or min(them, default=end)
    grid = _weekly(start, end)
    spy_levels = _levels(spy, grid)
    their_levels, your_levels = _levels(them, grid), _levels(mine, grid)
    return {
        "since": start,
        "benchmark": perf.get("benchmark"),
        "theirs": _line_risk(their_levels, spy_levels),
        "yours": _line_risk(your_levels, spy_levels),
        "spy": _line_risk(spy_levels, spy_levels),
        # Whether copying them would spread your risk: 1 moves in step with you, 0 unrelated.
        "correlation": _correlation(their_levels, your_levels),
        # Your line starts where your tracking does: a range reaching further back is read
        # only over the part you have.
        "yours_since": min((d for d, v in mine.items() if v is not None), default=None),
    }


def _weekly(start: str, end: str) -> list[str]:
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=7)
    if out and out[-1] != end:
        out.append(end)
    return out


def _levels(line: dict[str, float | None], grid: list[str]) -> list[tuple[str, float]]:
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


def _line_risk(levels: list[tuple[str, float]], anchor: list[tuple[str, float]]) -> dict | None:
    if len(levels) < _MIN_WEEKS:
        return None
    dates, weekly = _period_returns(levels)
    a_dates, a_weekly = _period_returns(anchor)
    vol = risk_metrics.volatility_annualized(weekly, factor=_WEEKS_PER_YEAR)
    mdd = risk_metrics.max_drawdown([lv / levels[0][1] for _d, lv in levels])
    p, a = risk_metrics.align_vectors(dates, weekly, a_dates, a_weekly)
    beta = risk_metrics.beta_vs(p, a) if len(p) >= _MIN_WEEKS else None
    # Return per unit of swing (the risk engine's own Sharpe, its risk-free rate): the line's
    # return over the range, as a yearly rate, against its yearly swings.
    years = len(weekly) / _WEEKS_PER_YEAR
    growth = levels[-1][1] / levels[0][1] if levels[0][1] > 0 else None
    yearly = growth ** (1 / years) - 1 if growth and growth > 0 and years > 0 else None
    sharpe = risk_metrics.sharpe(yearly, vol)
    return {
        "volatility": round(vol, 6) if vol is not None else None,
        "max_drawdown": round(mdd, 6) if mdd is not None else None,
        "beta": round(beta, 6) if beta is not None else None,
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
    }


def _correlation(a: list[tuple[str, float]], b: list[tuple[str, float]]) -> float | None:
    """Pearson correlation of two lines' weekly returns over the weeks both have."""
    a_dates, a_ret = _period_returns(a)
    b_dates, b_ret = _period_returns(b)
    x, y = risk_metrics.align_vectors(a_dates, a_ret, b_dates, b_ret)
    n = len(x)
    if n < _MIN_WEEKS:
        return None
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / (vx * vy) ** 0.5, 6)


def _period_returns(levels: list[tuple[str, float]]) -> tuple[list[str], list[float]]:
    dates: list[str] = []
    out: list[float] = []
    for (_d0, v0), (d1, v1) in zip(levels, levels[1:]):
        if v0 > 0:
            dates.append(d1)
            out.append(v1 / v0 - 1.0)
    return dates, out
