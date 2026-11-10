"""The Investments book — market holdings grouped, weighted and totalled, server-side.

Built on `portfolio_rows.projected_rows()`, so a holding is valued exactly once for the whole
app. What this adds on top is the book-level arithmetic the Investments tab used to do in the
browser (`useInvestmentsBook.ts`): the grand total, each row's portfolio weight, per-asset-class
sections and their subtotals, today's P&L, unrealized P&L, single-name concentration,
allocation-vs-target, bond YTM, and the fold that collapses one instrument held at several
accounts into a single row.

See ADR 2026-07-31-overview-aggregates-are-server-computed — the same rule as the Overview: a
widget renders a number the server computed.

Deliberately NOT here: the subtotal of a set the USER filtered (a search box, an account chip).
That is a selection the server has no name for, so summing already-server-valued rows for it in
the browser is not a second implementation of anything — there is no server-side counterpart to
drift from. Every figure with a canonical definition lives here.
"""

from __future__ import annotations

from datetime import date

from backend.core.errors import MissingFxRateError
from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import portfolio_rows as portfolio_rows_service
from backend.services import profile as profile_service
from backend.services import untracked_holdings
from backend.services.classification import FUND_SUBCLASSES
from backend.services.rebalance import DRIFT_FLAG_PP

# Single-name concentration above this share of the book is flagged. Mirrors the Investments
# tab's own guideline default; the caller may override it from the profile.
CONCENTRATION_GUIDELINE_PCT = 20.0
MOVERS_LIMIT = 5
RISK_LIMIT = 5
_DEFAULT_FACE_VALUE = 1000.0
# Asset classes that ARE the investment book. Cash/receivable/liability rows are accounts, and
# belong to net worth, not to "what am I invested in".
MARKET_CLASSES = frozenset({"equity", "bond", "commodity", "crypto", "option"})


def _approx_ytm(
    coupon_rate_pct: float,
    price_base: float,
    qty: float,
    face_value: float,
    years_to_maturity: float,
) -> float | None:
    """Approximate yield to maturity: (coupon + (face − price)/years) / ((face + price)/2).

    The standard approximation, not a solved IRR — enough for a column, and honest about it.
    Returns None when any input makes it meaningless.
    """
    if years_to_maturity <= 0 or qty <= 0 or face_value <= 0 or coupon_rate_pct <= 0:
        return None
    face = face_value * qty
    denominator = (face + price_base) / 2
    if denominator == 0:
        return None
    coupon_amount = (coupon_rate_pct / 100) * face
    return ((coupon_amount + (face - price_base) / years_to_maturity) / denominator) * 100


def _years_to_maturity(maturity: str | None) -> float | None:
    if not maturity:
        return None
    try:
        return (date.fromisoformat(maturity[:10]) - user_today()).days / 365.25
    except (TypeError, ValueError):
        return None


def _book_row(row: dict, grand: float) -> dict:
    """One holding as the book renders it: value, weight, P&L, day move, and bond columns."""
    try:
        qty = float(row.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    value = row["base_value"]

    ytm = None
    if row.get("asset_class") == "bond" and row.get("coupon_rate"):
        years = _years_to_maturity(row.get("maturity_date"))
        if years is not None:
            ytm = _approx_ytm(
                float(row["coupon_rate"]),
                value,
                qty,
                float(row.get("face_value") or _DEFAULT_FACE_VALUE),
                years,
            )

    symbol = row.get("symbol") or row.get("ticker") or row.get("label") or ""
    account = row.get("account_name") or row.get("category") or ""
    return {
        # position_id is unique per backend position, so it distinguishes a vested holding from
        # an unvested grant on the SAME instrument in the SAME account. The fallback keeps keys
        # unique (and entitlement-distinguishing) when it is absent.
        "row_key": row.get("position_id") or f"{symbol}\0{account}\0{row.get('vesting_state')}",
        "position_id": row.get("position_id"),
        "instrument_id": row.get("instrument_id"),
        "symbol": symbol,
        "name": row.get("label") or "",
        "value": value,
        "weight": round((value / grand) * 100, 4) if grand else 0.0,
        # None = cost basis unknown (portfolio_rows leaves it None). It stays None here: a 0.0
        # would render as a green "+0.00 0.00%", claiming break-even for a holding whose cost
        # was never recorded.
        "pl": row.get("pl"),
        "pl_pct": row.get("pl_pct"),
        "day_pct": row.get("daily_pl_pct") or 0.0,
        "day_pl": row.get("daily_pl") or 0.0,
        "cls": row.get("asset_class") or "",
        "sub": row.get("asset_subclass") or row.get("asset_class") or "",
        "sector": row.get("sector") or "",
        "account": account,
        "account_id": row.get("account_id") or "",
        # display_currency (from portfolio_rows._display_ccy) is what `price`/`cost` below are
        # ACTUALLY expressed in — for crypto that's the currency you transacted in, which can
        # differ from the instrument's quote_currency. Labelling CUR from quote_currency alone
        # mislabels those rows (e.g. a Gemini BTC lot bought in SGD showed CUR=USD).
        "currency": row.get("display_currency")
        or row.get("quote_currency")
        or row.get("currency")
        or "",
        "cost_currency": row.get("currency") or "",
        "cost_base": row.get("cost_base"),
        "qty": qty,
        "price": row.get("display_unit_price") or 0.0,
        "cost": row.get("display_unit_cost") or 0.0,
        "vesting_state": row.get("vesting_state") or "vested",
        "coupon": row.get("coupon_rate"),
        "maturity": row.get("maturity_date"),
        "ytm": round(ytm, 1) if ytm is not None else None,
        "merged_count": 1,
    }


def _merge_by_instrument(rows: list[dict], grand: float) -> list[dict]:
    """Collapse one instrument held at several accounts into a single row.

    The custodian model is one position per instrument × account, so the same holding at two
    brokers is two rows. When the active grouping does not itself distinguish accounts, showing
    it twice is wrong — but merging means SUMMING value, P&L, quantity and cost, which is money
    math, which is why it happens here.

    Entitlement-distinguishing: an unvested grant never merges with sellable shares of the same
    instrument. P&L % is recomputed from the summed BASE cost — never an average of percentages.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        key = f"{r['instrument_id'] or r['symbol']}\0{r['vesting_state']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    merged = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            merged.append(members[0])
            continue
        first = members[0]
        value = round(sum(m["value"] for m in members), 2)
        qty = sum(m["qty"] for m in members)
        # The merged P&L is the P&L of the WHOLE holding. One leg with an unknown cost makes
        # it unknown — summing the known legs alone would claim a figure for shares whose
        # cost was never recorded.
        cost_known = all(m["pl"] is not None for m in members)
        pl = round(sum(m["pl"] for m in members), 2) if cost_known else None
        cost_base = sum(m["cost_base"] or 0.0 for m in members) if cost_known else 0.0
        # A per-unit native cost is only representable when every contributing position shares
        # one cost currency; otherwise it collapses to unknown rather than mixing currencies.
        same_ccy = cost_known and all(m["cost_currency"] == first["cost_currency"] for m in members)
        cost_total = sum(m["cost"] * m["qty"] for m in members) if same_ccy else 0.0
        merged.append(
            {
                **first,
                "row_key": f"merged:{key}",
                "position_id": None,  # ambiguous once merged — disables per-position actions
                "value": value,
                "pl": pl,
                "pl_pct": (
                    round((pl / cost_base) * 100, 4) if pl is not None and cost_base > 0 else None
                ),
                "weight": round((value / grand) * 100, 4) if grand else 0.0,
                "qty": qty,
                "cost": round(cost_total / qty, 6) if same_ccy and qty > 0 and cost_total else 0.0,
                "cost_base": round(cost_base, 2) if cost_base else None,
                "account": f"{len(members)} accounts",
                "merged_count": len(members),
            }
        )
    return merged


def _sections(rows: list[dict], grand: float, targets: dict[str, float]) -> list[dict]:
    """Per-asset-class subtotals, their share of the book, and the trade that would close the
    gap to target — the same `drift_pp`/`trade_base` formula as `portfolio_analytics._drift`,
    over this book's total rather than the whole portfolio (MATH-38: a caller that rounds
    `current_pct`/`target_pct` to whole points before sizing a trade compounds the rounding
    into real money — `trade_base` is computed here, at full precision, so no caller has to)."""
    by_class: dict[str, list[dict]] = {}
    for r in rows:
        by_class.setdefault(r["cls"], []).append(r)
    sections = []
    for cls, members in by_class.items():
        subtotal = round(sum(m["value"] for m in members), 2)
        current_pct = round((subtotal / grand) * 100, 4) if grand else 0.0
        # `targets` is a FRACTION 0-1 (profile.normalize_target_allocation) — scale to
        # a percent to compare against `current_pct`, same as rebalance.py does.
        target_pct = float(targets.get(cls) or 0.0) * 100.0
        drift_pp = round(current_pct - target_pct, 4)
        sections.append(
            {
                "cls": cls,
                "subtotal": subtotal,
                "current_pct": current_pct,
                "target_pct": target_pct,
                "drift_pp": drift_pp,
                "trade_base": round((-drift_pp / 100) * grand, 2),
                "row_keys": [m["row_key"] for m in members],
            }
        )
    sections.sort(key=lambda s: s["subtotal"], reverse=True)
    return sections


def _concentration(rows: list[dict], grand: float, guideline_pct: float) -> list[dict]:
    return concentration_by_name(rows, grand, guideline_pct)[:RISK_LIMIT]


# ── The one authority for book-level figures other screens also show ─────────────────────
# ADR 2026-09-25-concentration-today-and-benchmark-have-one-authority: the Portfolio brief,
# the Intel Holdings brief, the risk engine and the watch pulse read concentration, today's
# move and the invested denominator through THESE functions, so no two screens can disagree.


def is_invested(row: dict) -> bool:
    """A row that belongs to the investment book: an instrument-backed market position that is
    actually owned. Accounts, owned assets and unvested grant units are net worth, not the book."""
    return (
        bool(row.get("instrument_id"))
        and row.get("asset_class") in MARKET_CLASSES
        and row.get("vesting_state") != "unvested"
    )


def book_symbol(row: dict) -> str:
    return row.get("symbol") or row.get("ticker") or row.get("label") or ""


def project_invested(
    rows: list[dict], base: str | None = None, rates: dict | None = None
) -> tuple[list[dict], float]:
    """(projected invested rows, grand total) from `positions.hydrated()`-shaped rows, valued by
    the canonical per-row fold (`portfolio_rows.value_row`) — for a caller that holds hydrated
    rows rather than the full projection."""
    base = (base or base_currency()).upper()
    projected = [portfolio_rows_service.value_row(r, base, rates) for r in rows if is_invested(r)]
    return projected, round(sum(p["base_value"] for p in projected), 2)


def project_invested_valued(
    rows: list[dict], base: str | None = None, rates: dict | None = None
) -> tuple[list[dict], float, dict[str, list[str]]]:
    """`project_invested` for a reader that must degrade, not fail, on a missing FX rate:
    (projected rows, grand total, {currency with no rate → instrument ids left out}). A row
    that cannot be valued is NOT counted — never valued 1:1 — so the total and every share
    taken from it describe only the rows that could be."""
    base = (base or base_currency()).upper()
    projected: list[dict] = []
    unvalued: dict[str, list[str]] = {}
    for r in rows:
        if not is_invested(r):
            continue
        try:
            projected.append(portfolio_rows_service.value_row(r, base, rates))
        except MissingFxRateError as exc:
            ids = unvalued.setdefault(str(exc.context["currency"]).upper(), [])
            iid = r.get("instrument_id") or ""
            if iid not in ids:
                ids.append(iid)
    return projected, round(sum(p["base_value"] for p in projected), 2), unvalued


# Fund wrappers over these asset classes are baskets of many issuers. A commodity or crypto
# ETP is one exposure however it is wrapped, so it stays a "name".
_DIVERSIFIED_CLASSES = frozenset({"equity", "bond"})


def is_diversified_fund(row: dict) -> bool:
    """True for an equity or bond fund (ETF, index fund, mutual fund): many issuers in one
    holding, so it is not a single name. `rows` are projected rows (`asset_subclass`,
    `asset_class`) or book rows (`sub`, `cls`)."""
    subclass = (row.get("asset_subclass") or row.get("sub") or "").lower()
    asset_class = (row.get("asset_class") or row.get("cls") or "").lower()
    return subclass in FUND_SUBCLASSES and asset_class in _DIVERSIFIED_CLASSES


def concentration_by_name(
    rows: list[dict], grand: float, guideline_pct: float, *, names_only: bool = True
) -> list[dict]:
    """Single-name exposure, biggest first — the same instrument across accounts counted once.

    `rows` carry `symbol` + `value` (book rows) or `base_value` (projected rows). `hot` is the
    one breach rule: at or above the guideline. A diversified fund is not a name: an all-world
    index ETF at 39% of the book is 39% of the market, not 39% in one company (it was flagged
    "19 points above the guideline, consider trimming"), so it is left out unless `names_only`
    is False, which is for a caller that wants every holding's weight rather than a breach."""
    by_name: dict[str, float] = {}
    ids: dict[str, list[str]] = {}
    for r in rows:
        if names_only and is_diversified_fund(r):
            continue
        name = r.get("symbol") or book_symbol(r)
        value = r["value"] if "value" in r else r["base_value"]
        by_name[name] = by_name.get(name, 0.0) + value
        iid = r.get("instrument_id")
        if iid and iid not in ids.setdefault(name, []):
            ids[name].append(iid)
    ranked = [
        {
            "name": name,
            "value": round(value, 2),
            "pct": round((value / grand) * 100, 4) if grand else 0.0,
            "hot": bool(grand) and (value / grand) * 100 >= guideline_pct,
            "instrument_ids": ids.get(name, []),
        }
        for name, value in by_name.items()
    ]
    ranked.sort(key=lambda r: r["pct"], reverse=True)
    return ranked


def today_move(projected: list[dict]) -> dict:
    """Today's move of the book: the summed base-currency day P&L, and that P&L as a percent of
    the book's value at the prior close (not today's value — a +10% day reads +10%, not +9.1%).
    `has_data` is False when no row carries a day change."""
    moves = [p for p in projected if p.get("daily_pl") is not None]
    pl = round(sum((p["daily_pl"] for p in moves), 0.0), 2)
    prior = sum(p["base_value"] - p["daily_pl"] for p in moves if p["daily_pl"])
    return {
        "pl": pl,
        "pct": round((pl / prior) * 100, 4) if prior else 0.0,
        "has_data": bool(moves),
    }


def concentration_guideline(profile: dict | None = None) -> float:
    """The user's single-name guideline (Investments → Risk card), else the default."""
    if profile is None:
        profile = profile_service.read_profile() or {}
    try:
        return float(profile.get("risk_concentration_guideline") or CONCENTRATION_GUIDELINE_PCT)
    except (TypeError, ValueError):
        return CONCENTRATION_GUIDELINE_PCT


def investments_book(
    base: str | None = None,
    guideline_pct: float | None = None,
    projection: dict | None = None,
) -> dict:
    """Every book-level figure the Investments tab renders."""
    base = (base or base_currency()).upper()
    guideline = float(guideline_pct or CONCENTRATION_GUIDELINE_PCT)

    # `projection` lets a caller that already folded the book (portfolio_analytics) pass it in.
    # Without it this would value every holding a second time inside one request.
    if projection is None:
        projection = portfolio_rows_service.projected_rows(base)
    # The book is the INVESTED holdings — market positions only, and only what is actually
    # owned. Accounts are net worth. `vesting_state="unvested"` positions (equity-grant units
    # not yet vested, forfeited if the grantee leaves) are excluded here for the same reason
    # every other reader of these rows excludes them — portfolio_rows.projected_rows's own
    # `portfolio_total`, portfolio_analytics, macro_exposure, risk_signals, asset_detail (ADR
    # 2026-07-31-unvested-equity-excluded-from-net-worth). Left in, an unvested tranche read as
    # an ordinary sellable holding, inflating the book's market value and flagging a
    # concentration risk on shares that are not owned yet.
    invested = [r for r in projection["rows"] if is_invested(r)]
    grand = round(sum(r["base_value"] for r in invested), 2)

    rows = [_book_row(r, grand) for r in invested]
    merged = _merge_by_instrument(rows, grand)
    targets = (profile_service.read_profile() or {}).get("target_allocation") or {}

    today = today_move(invested)
    total_pl = round(sum(r["pl"] for r in rows if r["pl"] is not None), 2)
    movers = sorted(rows, key=lambda r: abs(r["day_pct"]), reverse=True)[:MOVERS_LIMIT]
    concentration = _concentration(merged, grand, guideline)
    sections = _sections(rows, grand, targets)

    out = {
        "base_currency": base,
        "is_live": projection["is_live"],
        "grand": grand,
        "empty": not rows,
        # The book's share of everything you own — numerator and denominator both come from the
        # SAME live-priced projection, so the percentage can never compare two price snapshots.
        "portfolio_total": projection["portfolio_total"],
        "pct_of_portfolio": (
            round((grand / projection["portfolio_total"]) * 100, 4)
            if projection["portfolio_total"]
            else 0.0
        ),
        "rows": rows,
        "merged_rows": merged,
        "sections": sections,
        "today_pl": today["pl"],
        "today_pct": today["pct"],
        "total_pl": total_pl,
        # Unrealized P&L as a return on what was paid, not on today's value.
        "total_pl_pct": (
            round((total_pl / (grand - total_pl)) * 100, 4) if (grand - total_pl) else 0.0
        ),
        "movers": [m["row_key"] for m in movers],
        "concentration": concentration,
        "concentration_guideline_pct": guideline,
        "top_name": concentration[0] if concentration else None,
        # Mirrors portfolio_analytics().drift.threshold_pp — the ONE constant
        # (rebalance.DRIFT_FLAG_PP), so every surface flags "off target" at the same drift.
        "threshold_pp": DRIFT_FLAG_PP,
        "allocation": [
            {
                "cls": s["cls"],
                "current_pct": s["current_pct"],
                "target_pct": s["target_pct"],
                "drift_pp": s["drift_pp"],
                "trade_base": s["trade_base"],
                "subtotal": s["subtotal"],
            }
            for s in sections
        ],
    }
    excluded = untracked_holdings.untracked_excluded(projection["rows"])
    if excluded is not None:
        # A Brokerage total account's untracked part: invested, but in no known class, so it is
        # NOT in `grand` — the denominator of every weight, section, concentration and today's
        # move above. It is the allocation's "Not broken down" slice, its share taken of
        # everything invested (ADR 2026-11-10, amendment 2). Absent while the flag is off.
        invested = grand + excluded["total"]
        out["untracked_excluded"] = {
            **excluded,
            "pct_of_invested": (
                round((excluded["total"] / invested) * 100, 4) if invested > 0 else 0.0
            ),
        }
    return out
