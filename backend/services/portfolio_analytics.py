"""Portfolio ANALYTICS — allocation, reachability, venues, KPIs, risk flags, concentration.

Views over the one row projection. Named for the DATA they describe, not for a screen: the
Overview renders most of them, the Investments tab renders the rest, and neither owns them.

Every figure here used to be a `.reduce()` in a widget. They are computed once, from the ONE
live-priced row projection (`rows.projected_rows`), so two widgets can no longer disagree
about the same number.

Net worth is NOT here at all. It has ONE endpoint — `GET /api/net-worth` — and this payload
deliberately does not echo it: a second path to the same number is a second chance to render a
different one. The Overview headline and the Accounts header used to take different paths (this
payload vs `/api/net-worth`), which meant one was live-priced and the other last-known, so the
two tabs could show different figures at the same moment. `bands.net_worth` stays: that is the
banded reconstruction, a different figure with a different definition.
"""

from __future__ import annotations

import logging

from backend.core.fx import base_currency
from backend.db import read_cache
from backend.models.account import UNTRACKED_HOLDINGS_BAND
from backend.services import cashflow as cashflow_service
from backend.services import investments_book as investments_book_service
from backend.services import ledger as ledger_service
from backend.services import profile as profile_service
from backend.services.portfolio_rows import projected_rows
from backend.services.rebalance import DRIFT_FLAG_PP, NON_INVESTMENT_CLASSES

logger = logging.getLogger(__name__)

CONTINGENT_FLAG_PCT = 25.0
# A buffer is thin when it covers fewer months than this of the user's own spending — never a
# flat amount, which meant nothing across base currencies and fired on a day-one book.
THIN_CASH_MONTHS = 3
MAX_TREEMAP_LEAVES = 60
# `untracked_holdings`: a Brokerage total account's holdings not entered yet — still invested.
_INVESTMENT_CLASSES = frozenset({"equity", "crypto", "commodity", UNTRACKED_HOLDINGS_BAND})
# The two bands a liability account can carry (`models.account.ACCOUNT_BAND`). An overpaid card
# has a POSITIVE balance, so the sign test alone does not exclude it from reach.
_DEBT_CLASSES = frozenset({"credit", "loan"})
_DEBT_EPSILON = 0.005


def _pct(part: float, whole: float) -> float:
    return round((part / whole) * 100, 4) if whole else 0.0


def _breakdown(buckets: dict[str, float], total: float, colors: dict[str, str] | None = None):
    rows = []
    for key, value in buckets.items():
        row = {"key": key, "value": round(value, 2), "pct": _pct(value, total)}
        if colors and colors.get(key):
            row["color"] = colors[key]
        rows.append(row)
    return {"total": round(total, 2), "rows": rows}


def _class_and_liquidity(rows: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """Asset-class and reachability bands, both over VESTED rows only.

    Unvested grant units are excluded from BOTH. They are excluded from the denominator
    (`portfolio_total`) because they are not owned yet (ADR 2026-07-31-unvested-equity-
    excluded-from-net-worth), so counting them in a numerator produced a share-of-net-worth
    bar for value that is absent from net worth — the allocation percentages summed past
    100% and the equity band read ~S$65K above the backend's own `equity` figure. The old
    client-side `classBreakdown` excluded them from reach but not from classes; that
    asymmetry was the bug.
    """
    classes: dict[str, float] = {}
    liquidity: dict[str, float] = {"liquid": 0.0, "semi_liquid": 0.0, "illiquid": 0.0}
    for r in rows:
        if r.get("vesting_state") == "unvested":
            continue
        # Allocation spans the whole book, liabilities included — it reconciles to net worth.
        classes[r["asset_class"]] = classes.get(r["asset_class"], 0.0) + r["base_value"]

        # Reach spans ASSETS ONLY. A debt is not money you can get to, so it has no
        # reachability — and putting it in one made the answer nonsense: a credit-card and
        # margin balance summed into `liquid` drove that bucket NEGATIVE (-S$70K against
        # +S$240K illiquid), and since the widget only draws positive bars, the whole chart
        # collapsed to a single "illiquid" bar reading 150% of net worth. Every holding looked
        # unreachable. Excluding liabilities here (and from the denominator below) is what
        # makes the three bars sum to 100% of what you actually hold.
        if r["base_value"] <= 0 or r["asset_class"] in _DEBT_CLASSES:
            continue
        bucket = r.get("liquidity") or "liquid"
        liquidity[bucket] = liquidity.get(bucket, 0.0) + r["base_value"]
    return classes, liquidity


def _allocation(classes: dict[str, float]) -> dict:
    """Asset shares of the assets themselves (they sum to 100%) with what is owed as its OWN
    figure, `owed` (<= 0), never a negative percentage: dividing by net worth made a cash-and-card
    book read Cash 122% and Credit -22% (persona QA, 2026-10-14). `total + owed` is net worth."""
    assets = {k: v for k, v in classes.items() if v > 0}
    owed = sum(v for v in classes.values() if v < 0)
    return {**_breakdown(assets, sum(assets.values())), "owed": round(owed, 2)}


def _venues(rows: list[dict]) -> tuple[dict[str, float], dict[str, str]]:
    """Where the money is held, by venue. Venues that net negative (a margin account) are
    dropped, so the DENOMINATOR must be the kept venues' own sum — dividing them by net worth
    instead made the shares total 124%, the same mistake that broke the reach chart.

    Owned-asset rows (`account_type == "owned_asset"`, the tag `portfolio_rows._asset_as_row`
    stamps) are excluded — a house or a watch is not held AT a venue, and falling back to
    `category` (the asset's own name, since it has no `institution`) minted a one-off "venue"
    per asset."""
    venues: dict[str, float] = {}
    colors: dict[str, str] = {}
    for r in rows:
        # Not-yet-vested grants are not held anywhere yet (ADR 2026-07-31).
        if r.get("account_type") == "owned_asset" or r.get("vesting_state") == "unvested":
            continue
        key = (r.get("institution") or r.get("category") or "").strip() or "Unassigned"
        venues[key] = venues.get(key, 0.0) + r["base_value"]
        if r.get("color") and key not in colors:
            colors[key] = r["color"]
    return {k: v for k, v in venues.items() if v > 0}, colors


def _market_pl_rows(rows: list[dict]) -> list[dict]:
    # Vested only: an unvested grant's paper gain is not yours yet
    # (ADR 2026-07-31-unvested-equity-excluded-from-net-worth). Owned-asset rows are excluded
    # too (mirrors investments_book's own `r.get("instrument_id")` gate) — `_asset_as_row`
    # stamps a `pl` on every owned asset with a known cost so the row also fuels allocation
    # and reach, but that gain already has a single owner, `owned_assets.summary()`'s own
    # `asset_gain` loop; folding it into `upl` here as well double-counted it.
    return [
        r
        for r in rows
        if r.get("pl") is not None
        and r.get("vesting_state") != "unvested"
        and r.get("account_type") != "owned_asset"
    ]


def market_upl(rows: list[dict]) -> float:
    """The market's unrealized P&L over a projection's rows — `kpis.upl`."""
    return round(sum(r["pl"] for r in _market_pl_rows(rows)), 2)


def _debt_rows(rows: list[dict]) -> list[dict]:
    """Liabilities proper: `credit` and `loan` accounts that owe money. An overdrawn CASH account
    is already netted into cash on hand (a negative `cash` band), so counting it here as well
    made "debt" disagree with net worth's own liabilities."""
    return [
        r for r in rows if r["asset_class"] in _DEBT_CLASSES and r["base_value"] < -_DEBT_EPSILON
    ]


def _kpis(rows: list[dict], bands: dict) -> dict:
    market_rows = _market_pl_rows(rows)
    # Today's move has ONE authority — the investments book (vested market positions). Summing
    # every row's `daily_pl` here folded an unvested grant's paper move into "Today".
    today = investments_book_service.today_move(
        [r for r in rows if investments_book_service.is_invested(r)]
    )
    debt_rows = _debt_rows(rows)
    classes = bands.get("classes") or {}
    return {
        "today": today["pl"],
        "today_pct": today["pct"],
        "has_market": today["has_data"],
        "upl": market_upl(rows),
        "upl_known": bool(market_rows),
        # Cash on hand — Σ `cash` accounts, the SAME definition as
        # `cashflow.cash_balance_base` (ADR 2026-09-07-seven-account-types-one-axis). Settlement
        # cash is part of an investment account's value, never spendable cash.
        "cash": round(classes.get("cash") or 0.0, 2),
        "debt": round(abs(sum(r["base_value"] for r in debt_rows)), 2),
        "debt_accounts": len({r.get("account_id") or r.get("label") for r in debt_rows}),
    }


def _holdings_total(rows: list[dict]) -> float:
    """Gross vested assets — what you HOLD, liabilities excluded, unvested grants excluded."""
    return sum(
        r["base_value"]
        for r in rows
        if r["base_value"] > 0 and r.get("vesting_state") != "unvested"
    )


def _card_debt_covered(debt_total: float, book: ledger_service.LedgerBook | None = None) -> float:
    """How much of the book's debt its credit cards' payment envelopes already hold, in base
    currency (0 when nothing is owed, or no card has such an envelope). The Budget's Credit
    Card Payments rows are the authority (``planning.card_payment_coverage``); a failed read
    leaves the flag as it always was rather than breaking the Dashboard."""
    if debt_total >= -_DEBT_EPSILON:
        return 0.0
    from backend.core.user_time import user_today
    from backend.services import planning

    try:
        coverage = planning.card_payment_coverage(user_today().strftime("%Y-%m"), book=book)
    except Exception:
        logger.warning("card payment coverage unavailable", exc_info=True)
        return 0.0
    return round(sum(min(c["owed"], c["held"]) for c in coverage), 2)


def _attention(
    rows: list[dict], runway: dict | None = None, book: ledger_service.LedgerBook | None = None
) -> dict:
    # The concentration call is NOT here: the Portfolio Brief (`portfolio_brief`, reading
    # `investments_book.concentration_by_name`) is its one authority, and Needs attention lists the
    # brief's open decisions. A second, gross-holdings-based flag said "All clear" while
    # Investments showed "1 to decide" (persona QA, 2026-10-14).
    #
    # The denominator below is gross holdings, NOT net worth (`portfolio_total`, assets minus
    # debt): a mortgage large enough to push net worth toward or below zero made the share
    # negative or absurd. Same reasoning as `reach` and `venues`.
    holdings_total = _holdings_total(rows)
    # "Contingent" = not fully in your control yet: vested-but-undelivered grant shares plus
    # money owed to you — the two cases the old overloaded `bucket=="contingent"` covered.
    contingent = sum(
        r["base_value"]
        for r in rows
        if r.get("liquidity") == "semi_liquid" or r["asset_class"] == "receivable"
    )
    debt_total = sum(r["base_value"] for r in _debt_rows(rows))
    # The cash buffer is the Cash Flow runway (its cash divided by its burn), not a second
    # division of its own: this flag once said "about 1.9 months" beside Cash Flow's 2.1.
    cash_total = (
        runway["cash"]
        if runway
        else sum(r["base_value"] for r in rows if r["asset_class"] == "cash")
    )
    runway_months = runway["months"] if runway else None
    debt_covered = _card_debt_covered(debt_total, book)
    return {
        "holdings_total": round(holdings_total, 2),
        "debt_total": round(debt_total, 2),
        # The part of that debt already set aside in the cards' payment envelopes, and whether
        # that is all of it: a covered debt reads "covered", not "monitor repayment".
        "debt_covered_total": debt_covered,
        "debt_covered": debt_total < -_DEBT_EPSILON and debt_covered >= -debt_total - _DEBT_EPSILON,
        "contingent_total": round(contingent, 2),
        "contingent_pct": _pct(contingent, holdings_total),
        "contingent_flagged": _pct(contingent, holdings_total) >= CONTINGENT_FLAG_PCT,
        "cash_total": round(cash_total, 2),
        # How many months of the user's own spending the cash covers — what "thin" is
        # measured in, so the flag can say it. None while spending is unknown.
        "cash_months": runway_months,
        "thin_cash": runway_months is not None
        and 0 < cash_total
        and runway_months < THIN_CASH_MONTHS,
        # Cards whose spending the runway cannot see yet (an empty card flatters it).
        "cards_missing_spending": runway["cards_missing_spending"] if runway else [],
        "has_investments": any(
            r["asset_class"] in _INVESTMENT_CLASSES and r["base_value"] > 0 for r in rows
        ),
    }


def _debts(rows: list[dict]) -> dict:
    debts = sorted(_debt_rows(rows), key=lambda r: r["base_value"])
    return {
        "total": round(sum(r["base_value"] for r in debts), 2),
        "rows": [
            {
                "key": (r.get("account_id") or r.get("label") or "") + (r.get("label") or ""),
                "label": r.get("label") or "",
                "value": r["base_value"],
                "color": r.get("color") or "",
            }
            for r in debts
        ],
    }


def _fx_exposure(rows: list[dict], base: str) -> list[dict]:
    """Value held per non-base currency — the "$X held" note beside each FX pair."""
    by_ccy: dict[str, float] = {}
    for r in rows:
        # Vested only, same as every other fold here
        # (ADR 2026-07-31-unvested-equity-excluded-from-net-worth).
        if r.get("vesting_state") == "unvested":
            continue
        ccy = (r.get("currency") or "").upper()
        if not ccy or ccy == base:
            continue
        by_ccy[ccy] = by_ccy.get(ccy, 0.0) + r["base_value"]
    return [
        {"currency": c, "value": round(v, 2)}
        for c, v in sorted(by_ccy.items(), key=lambda kv: kv[1], reverse=True)
    ]


def _instrument_key(row: dict) -> str:
    """What makes two rows the same HOLDING: the instrument, wherever it is held."""
    return str(
        row.get("instrument_id")
        or row.get("symbol")
        or row.get("ticker")
        or row.get("position_id")
        or row.get("label")
        or ""
    )


def _group_by_instrument(rows: list[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_instrument_key(r), []).append(r)
    return list(groups.values())


def _merged_pl(members: list[dict]) -> tuple[float | None, float | None]:
    """(P&L, P&L %) of a holding spread over several positions. One leg with an unknown cost
    makes the whole unknown; the percent is recomputed on the summed base cost — never an
    average of percentages."""
    if any(m.get("pl") is None for m in members):
        return None, None
    pl = sum(m["pl"] for m in members)
    cost = sum(m["base_value"] for m in members) - pl
    return round(pl, 2), (round((pl / cost) * 100, 4) if cost > 0 else None)


def _gains(rows: list[dict]) -> dict:
    """Unrealized P&L per INSTRUMENT, biggest |P&L| first. Owned-asset rows are excluded (they
    carry their own `pl`, owned by `owned_assets.summary()`'s `asset_gain` loop — see
    `_kpis`'s comment on the same exclusion), as are unvested grants; one instrument held in
    several accounts is one line, not three rows with no account to tell them apart."""
    holdings = []
    for members in _group_by_instrument([r for r in _market_pl_rows(rows) if r["base_value"] != 0]):
        pl, pl_pct = _merged_pl(members)
        holdings.append(
            {
                "key": _row_key(max(members, key=lambda r: r["base_value"])),
                "value": round(sum(m["base_value"] for m in members), 2),
                "pl": pl,
                "pl_pct": pl_pct,
            }
        )
    holdings.sort(key=lambda h: abs(h["pl"] or 0.0), reverse=True)
    return {
        "total_pl": round(sum(h["pl"] or 0.0 for h in holdings), 2),
        "rows": holdings,
    }


def _row_key(row: dict) -> str:
    return str(row.get("position_id") or row.get("instrument_id") or row.get("label") or "")


def _movers(rows: list[dict]) -> dict:
    """Today's movers, one row per INSTRUMENT, gainers first then losers.

    An instrument held across several accounts is several positions with an identical
    day-over-day % but different $ moves. Collapsing them is a SUM of money, so it happens
    here rather than in the shared list component: each entry carries the group's TOTAL move,
    its % (on the prior-close value) and its value, keyed by the largest single contributor
    (for stable display fields). Vested market positions only — the same set as
    `kpis.today` and the investments book (`investments_book.is_invested`).
    """
    day_rows = [
        r for r in rows if investments_book_service.is_invested(r) and r.get("daily_pl") is not None
    ]
    movers = []
    for members in _group_by_instrument(day_rows):
        move = investments_book_service.today_move(members)
        movers.append(
            {
                "key": _row_key(max(members, key=lambda r: abs(r["daily_pl"]))),
                "day_pl": move["pl"],
                "day_pct": move["pct"],
                "value": round(sum(m["base_value"] for m in members), 2),
            }
        )
    gainers = sorted((m for m in movers if m["day_pl"] > 0), key=lambda m: -m["day_pl"])
    losers = sorted((m for m in movers if m["day_pl"] < 0), key=lambda m: m["day_pl"])
    ordered = [*gainers, *losers]
    return {
        # Per-instrument totals; the caller already holds the position rows from
        # GET /api/portfolio and looks the display fields up by `key`.
        "rows": ordered,
        "gainers": len(gainers),
        "losers": len(losers),
        "max_abs": round(max((abs(m["day_pl"]) for m in ordered), default=0.0), 2),
    }


def _treemap_leaf(members: list[dict], shown_total: float) -> dict:
    """One tile: an instrument's positions inside ONE group, summed. Within a sector or an
    asset type the same ETF held at three accounts is one tile; under the Account facet the
    group already separates them, so each account keeps its own."""
    value = round(sum(m["base_value"] for m in members), 2)
    pl, pl_pct = _merged_pl(members)
    day_rows = [m for m in members if m.get("daily_pl") is not None]
    return {
        "key": _row_key(max(members, key=lambda r: r["base_value"])),
        "value": value,
        "pct_of_shown": _pct(value, shown_total),
        "day_pl": round(sum(m["daily_pl"] for m in day_rows), 2) if day_rows else None,
        "pl": pl,
        "pl_pct": pl_pct,
        "positions": len(members),
    }


def _holdings_map(rows: list[dict]) -> dict:
    """Proportional treemap groups per facet, with every share computed server-side.

    Vested holdings only (an unvested grant is not yours yet — ADR
    2026-07-31-unvested-equity-excluded-from-net-worth), and one tile per instrument: the cap
    keeps the largest `MAX_TREEMAP_LEAVES` INSTRUMENTS, each with every position it is held in."""
    held = [
        r
        for r in rows
        if r.get("instrument_id") and r["base_value"] > 0 and r.get("vesting_state") != "unvested"
    ]
    instruments = sorted(
        _group_by_instrument(held),
        key=lambda members: sum(m["base_value"] for m in members),
        reverse=True,
    )
    capped_groups = instruments[:MAX_TREEMAP_LEAVES]
    capped = [r for members in capped_groups for r in members]
    total = sum(r["base_value"] for r in held)
    shown_total = sum(r["base_value"] for r in capped)

    facets = {
        "sector": lambda r: r.get("sector") or "Other",
        "asset_class": lambda r: r["asset_class"],
        "account": lambda r: r.get("account_name") or r.get("institution") or "Unassigned",
        "none": lambda _r: "__all__",
    }
    out: dict[str, list[dict]] = {}
    for facet, key_of in facets.items():
        buckets: dict[str, dict[str, list[dict]]] = {}
        for r in capped:
            buckets.setdefault(key_of(r), {}).setdefault(_instrument_key(r), []).append(r)
        groups = []
        for key, by_instrument in buckets.items():
            leaves = [_treemap_leaf(members, shown_total) for members in by_instrument.values()]
            leaves.sort(key=lambda leaf: leaf["value"], reverse=True)
            groups.append(
                {
                    "key": key,
                    "total": round(sum(leaf["value"] for leaf in leaves), 2),
                    "leaves": leaves,
                }
            )
        groups.sort(key=lambda g: g["total"], reverse=True)
        out[facet] = groups
    return {
        "total": round(total, 2),
        "shown_total": round(shown_total, 2),
        "shown": len(capped_groups),
        "holdings": len(instruments),
        "groups": out,
    }


def _drift(rows: list[dict], targets: dict[str, float]) -> dict:
    """Actual-vs-target mix of the INVESTMENT book and the trade size that would close each
    gap — same book as `rebalance._book_by_class`, not the whole portfolio."""
    sums: dict[str, float] = {}
    total = 0.0
    for r in rows:
        # Vested only, for the same reason the allocation bands are: an unvested grant is not
        # yours to rebalance, so counting it would suggest trimming equity you cannot sell.
        # Property/cash/receivable/other assets (a car, a watch) are never rebalanced
        # (rebalance.NON_INVESTMENT_CLASSES) — counting them dilutes every investment class's
        # current_pct and can surface e.g. a house as the suggested "trade".
        if (
            r["base_value"] <= 0
            or r.get("vesting_state") == "unvested"
            or r["asset_class"] in NON_INVESTMENT_CLASSES
        ):
            continue
        sums[r["asset_class"]] = sums.get(r["asset_class"], 0.0) + r["base_value"]
        total += r["base_value"]

    has_targets = any(v > 0 for v in (targets or {}).values())
    keys = sorted(set(sums) | {k for k, v in (targets or {}).items() if v > 0})
    drift_rows = []
    for key in keys:
        current = _pct(sums.get(key, 0.0), total)
        # `targets` is a FRACTION 0-1 (profile.normalize_target_allocation) — scale to a
        # percent to compare against `current_pct`, same as rebalance.py does.
        target = float((targets or {}).get(key) or 0.0) * 100.0
        drift_pp = round(current - target, 4)
        drift_rows.append(
            {
                "key": key,
                "current_pct": current,
                "target_pct": target,
                "drift_pp": drift_pp,
                "trade_base": round((-drift_pp / 100) * total, 2),
                "actionable": abs(drift_pp) >= DRIFT_FLAG_PP,
            }
        )
    drift_rows.sort(key=lambda r: abs(r["drift_pp"]), reverse=True)
    trades = sorted(
        (r for r in drift_rows if r["actionable"]),
        key=lambda r: abs(r["trade_base"]),
        reverse=True,
    )
    return {
        "total": round(total, 2),
        "has_targets": has_targets,
        "threshold_pp": DRIFT_FLAG_PP,
        "rows": drift_rows,
        "top_trade": trades[0] if trades else None,
    }


@read_cache.cached(lambda base=None, book=None: base, when=lambda *a, **kw: kw.get("book") is None)
def market_book(base: str | None = None, *, book: ledger_service.LedgerBook | None = None) -> dict:
    """The investments book and the market's unrealized P&L (`kpis.upl`) from ONE projection —
    for a caller that shows only those two figures (the Assets summary) and must not pay for
    the rest of the Overview's folds, a second net-worth fold (`net_worth_bands`) among them.
    The same projection and the same book function as `portfolio_analytics`, so the figures are
    identical to its `book` and `kpis.upl`. `book` is the ledger snapshot a caller that already
    read it shares (`ledger.read_book`)."""
    base = (base or base_currency()).upper()
    projection = projected_rows(base, book=book)

    return {
        "book": investments_book_service.investments_book(base, None, projection),
        "upl": market_upl(projection["rows"]),
    }


@read_cache.cached(lambda base=None, guideline_pct=None: (base, guideline_pct))
def portfolio_analytics(base: str | None = None, guideline_pct: float | None = None) -> dict:
    """Every portfolio-derived number the Overview renders, in one payload.

    The `rows` list carries per-row money already converted/valued server-side, so a widget
    that renders a LIST (movers, unrealized gains, the treemap) sorts and labels rows without
    ever doing arithmetic on money.
    """
    base = (base or base_currency()).upper()
    # ONE read of the ledger for the whole payload: the projection, the bands, the runway and
    # the card coverage each used to load the full transaction table for themselves.
    ledger_book = ledger_service.read_book()
    projection = projected_rows(base, book=ledger_book)
    rows = projection["rows"]
    portfolio_total = projection["portfolio_total"]

    bands = ledger_service.net_worth_bands(base, book=ledger_book)
    classes, liquidity = _class_and_liquidity(rows)
    venues, venue_colors = _venues(rows)
    targets = (profile_service.read_profile() or {}).get("target_allocation") or {}

    # The book (invested holdings, grouped) is a view of the SAME projection — folded here so a
    # caller gets one payload instead of a second endpoint recomputing the whole fold.
    book = investments_book_service.investments_book(base, guideline_pct, projection)

    return {
        "base_currency": base,
        "is_live": projection["is_live"],
        "book": book,
        "unvested_base": projection["unvested_base"],
        "portfolio_total": portfolio_total,
        "bands": bands,
        "kpis": _kpis(rows, bands),
        "allocation": _allocation(classes),
        # Denominator is what you HOLD, not net worth: the buckets exclude liabilities, so
        # dividing by a net-worth total (assets MINUS debt) would push the shares past 100%.
        "reach": _breakdown(liquidity, round(sum(liquidity.values()), 2)),
        "venues": _breakdown(venues, round(sum(venues.values()), 2), venue_colors),
        "attention": _attention(rows, cashflow_service.cash_runway(book=ledger_book), ledger_book),
        "debts": _debts(rows),
        "fx_exposure": _fx_exposure(rows, base),
        "gains": _gains(rows),
        "movers": _movers(rows),
        "holdings_map": _holdings_map(rows),
        "drift": _drift(rows, targets),
    }
