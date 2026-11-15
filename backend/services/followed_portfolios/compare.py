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

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import investments_book, prices
from backend.services import positions as positions_service
from backend.services import returns as returns_service
from backend.services.followed_portfolios import performance, preview, share_classes
from backend.services.followed_portfolios.model import build_model, normalize_symbol
from backend.services.portfolio_compare import correlation as _correlation
from backend.services.portfolio_compare import levels as _levels
from backend.services.portfolio_compare import line_risk as _line_risk
from backend.services.portfolio_compare import overlap as _overlap
from backend.services.portfolio_compare import sectors as _sectors
from backend.services.portfolio_compare import spread as _spread
from backend.services.portfolio_compare import weekly as _weekly
from backend.services.portfolio_compare import with_sectors as _with_sectors

_MARKET_BANDS = ("equity", "crypto", "commodity")


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
