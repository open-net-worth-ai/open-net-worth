"""The Assets register's summary block — server money math only (ADR
2026-09-06-assets-tab-faithful-port, the `Summary` block): the own/owe/net-worth headline, the
unrealized figure, the four class cards, the debt rows and the composition rail. The client
never derives any of it — the rail's percentages included.

``summary`` takes the live asset rows, the net-worth fold, the market book and a
``LedgerSnapshot`` as input rather than reading them, so the page read
(``services.owned_assets.get_assets_page``) folds each of them ONCE and this module never has
to import ``services.owned_assets`` back.
"""

from __future__ import annotations

import math

from backend.core.errors import MissingFxRateError
from backend.core.fx import to_base
from backend.db.account_store import AccountStore
from backend.services import ledger as ledger_service
from backend.services.owned_assets_valuation import value_as_of, values_by_category_from_rows
from backend.services.untracked_holdings import BAND as UNTRACKED_BAND
from backend.services.untracked_holdings import LABEL as UNTRACKED_LABEL


class LedgerSnapshot:
    """The ledger reads one Assets page shares: ONE accounts read, ONE transactions load and
    ONE FX snapshot. Every balance the page shows — each loan, the proceeds picker's cash
    accounts, the Cash card — folds this one load; each used to reload the whole ledger for its
    own balance (``ledger.account_with_balance`` with no transactions list), so the page's cost
    grew with the number of accounts."""

    def __init__(self, base: str, *, rates: dict, book: ledger_service.LedgerBook | None = None):
        self.base = base
        self.rates = rates
        if book is None:
            self.accounts = AccountStore().all()
            self.txns = ledger_service.transactions.all()
        else:
            self.accounts, self.txns = book.accounts, book.txns
        live = [a for a in self.accounts if not a.get("archived")]
        cutoffs = ledger_service.cutoffs_by_account(live, self.txns)
        self._native = ledger_service.balances_by_account(live, self.txns, cutoffs)
        self._balances: dict[str, float] = {}

    def of_type(self, type_: str) -> list[dict]:
        """``AccountStore.query_accounts(type=type_)`` over the one read — non-archived only."""
        return [a for a in self.accounts if not a.get("archived") and a.get("type") == type_]

    def balance_base(self, account: dict) -> float:
        """A non-archived account's ``ledger.account_with_balance(...)["balance_base"]`` from
        the shared load: the same fold from the account's tracking start, at this snapshot's
        rates."""
        account_id = account["id"]
        if account_id not in self._balances:
            native = self._native[account_id]
            currency = account.get("currency") or self.base
            self._balances[account_id] = to_base(native, currency, self.base, self.rates)
        return self._balances[account_id]


def _cash_yield_label(accounts: list[dict]) -> str | None:
    weighted, weight = 0.0, 0.0
    for account in accounts:
        apy = account.get("apy")
        balance = account.get("balance_base") or 0.0
        if apy is None or balance <= 0:
            continue
        weighted += apy * balance
        weight += balance
    if weight <= 0:
        return None
    return f"{weighted / weight:.1f}% blended"


def _cash_card_accounts(ledger: LedgerSnapshot) -> list[dict]:
    """Every non-archived `cash` and `fixed_deposit` account, in ANY currency, with its
    balance — exactly the set the "Cash & Savings" headline sums, so the card's count, its
    sub-label and its blended APY describe that same set.

    Deliberately NOT ``cash_accounts()``: that list is base-currency `cash` only for the dispose-
    proceeds picker's own FX reason, and reusing it here printed "3 accounts" and a cash-only APY
    under a figure that summed six accounts including the deposits."""
    accounts = ledger.of_type("cash") + ledger.of_type("fixed_deposit")
    return [{**a, "balance_base": ledger.balance_base(a)} for a in accounts]


def _cash_card(bd: dict, cash_sub: str, cash_accts: list[dict], has_deposits: bool) -> dict:
    cash_on_hand = round(bd.get("cash") or 0.0, 2)
    fixed_deposits = round(bd.get("fixed_deposit") or 0.0, 2)
    rows = None
    if has_deposits:
        rows = [
            {"key": "cash_on_hand", "label": "Cash on hand", "value": cash_on_hand},
            {"key": "fixed_deposits", "label": "Fixed deposits", "value": fixed_deposits},
        ]
    return {
        "value": round(cash_on_hand + fixed_deposits, 2),
        "sub": cash_sub,
        "yield_label": _cash_yield_label(cash_accts),
        "gain": None,
        "gain_pct": None,
        "as_of": "today",
        "count_label": f"{len(cash_accts)} accounts",
        "rows": rows,
    }


def _return_pct(value: float, gain: float) -> float:
    """A holding's return against what it cost (`value - gain`); 0 with no cost to measure."""
    return round(gain / (value - gain) * 100, 4) if value != gain else 0.0


def _untracked_row(value: float) -> list[dict]:
    """A Brokerage total account's untracked part, as a card row — invested, holdings not
    entered (ADR 2026-11-10, amendment 1). None of it while investment_value_source is off."""
    if not value:
        return []
    return [{"key": UNTRACKED_BAND, "label": UNTRACKED_LABEL, "value": value}]


def _investments_card(
    bd: dict, book: dict, brokerages: set, retirement_ids: set[str], retirement_untracked: float
) -> dict:
    settlement_cash = round(bd.get("settlement_cash") or 0.0, 2)
    untracked = round((bd.get(UNTRACKED_BAND) or 0.0) - retirement_untracked, 2)
    non_retirement = [r for r in book["rows"] if r.get("account_id") not in retirement_ids]
    retirement_value = sum(
        r["value"] for r in book["rows"] if r.get("account_id") in retirement_ids
    )
    positions = round(book["grand"] - retirement_value, 2)
    gain = round(sum(r["pl"] for r in non_retirement if r.get("pl") is not None), 2)
    gain_pct = _return_pct(positions, gain)
    rows = None
    if settlement_cash != 0 or untracked != 0:
        rows = [{"key": "positions", "label": "Positions", "value": positions, "pct": gain_pct}]
        if settlement_cash != 0:
            rows.append(
                {"key": "settlement_cash", "label": "Settlement cash", "value": settlement_cash}
            )
        rows += _untracked_row(untracked)
        gain_pct = None
    return {
        "value": round(positions + settlement_cash + untracked, 2),
        "sub": f"{len(brokerages)} brokerages · {len(non_retirement)} positions",
        "gain": gain,
        "gain_pct": gain_pct,
        "as_of": "today",
        "count_label": f"{len(non_retirement)} positions",
        "rows": rows,
    }


def _retirement_card(
    bd: dict, book: dict, accounts: list[dict], untracked: float = 0.0
) -> dict | None:
    """The retirement accounts: what they hold and the cash beside it. Once both are there it
    splits into Positions and Cash rows, the return moving onto Positions — the Investments
    card's own rule; all cash (a pension with nothing invested) stays one figure, no return.
    A Brokerage total retirement account's untracked part (`untracked`) is its own row,
    never Cash."""
    if not accounts:
        return None
    ids = {a["id"] for a in accounts}
    held = [r for r in book["rows"] if r.get("account_id") in ids]
    positions = round(sum(r["value"] for r in held), 2)
    cash = round(bd.get("retirement") or 0.0, 2)
    priced = [r["pl"] for r in held if r.get("pl") is not None]
    gain = round(sum(priced), 2) if priced else None
    gain_pct = _return_pct(positions, gain) if gain is not None else None
    untracked = round(untracked, 2)
    parts = [
        *(
            [{"key": "positions", "label": "Positions", "value": positions, "pct": gain_pct}]
            if held
            else []
        ),
        *([{"key": "cash", "label": "Cash", "value": cash}] if cash != 0 else []),
        *_untracked_row(untracked),
    ]
    rows = None
    if len(parts) >= 2:
        rows = parts
        gain_pct = None
    return {
        "value": round(positions + cash + untracked, 2),
        "count_label": f"{len(accounts)} accounts",
        "gain": gain,
        "gain_pct": gain_pct,
        "rows": rows,
    }


def _simple_class_card(bd: dict, band: str, accounts: list[dict]) -> dict | None:
    if not accounts:
        return None
    return {"value": round(bd.get(band) or 0.0, 2), "count_label": f"{len(accounts)} accounts"}


def _class_cards(breakdown: dict, book: dict, ledger: LedgerSnapshot, brokerages: set) -> dict:
    bd = breakdown["account_classes"]
    retirement_untracked = breakdown.get("retirement_untracked_base") or 0.0
    cash_accts = _cash_card_accounts(ledger)
    has_deposits = bool(ledger.of_type("fixed_deposit"))
    retirement_accounts = ledger.of_type("retirement")
    retirement_ids = {a["id"] for a in retirement_accounts}
    return {
        "cash": _cash_card(bd, _cash_sub(cash_accts), cash_accts, has_deposits),
        "investments": _investments_card(
            bd, book, brokerages, retirement_ids, retirement_untracked
        ),
        "retirement": _retirement_card(bd, book, retirement_accounts, retirement_untracked),
        "receivable": _simple_class_card(bd, "receivable", ledger.of_type("receivable")),
    }


_LIABILITY_CLASS_LABELS = {"credit": "Credit", "loan": "Loans"}


def _debt_rows(liability_classes: dict) -> list[dict] | None:
    """Mirrors a class card's own `rows` convention (AssetSummaryRow) — a breakdown the
    client never derives itself. `liability_classes` (ledger.net_worth()'s breakdown) is
    stored raw-negative; surfaced only once there are two or more non-zero bands, the same
    "two or more kinds" threshold a class card's own `rows` uses (a single band would just
    repeat the "What you owe" KPI with nothing new to show)."""
    rows = [
        {
            "key": band,
            "label": _LIABILITY_CLASS_LABELS.get(band, band.title()),
            "value": round(abs(val), 2),
        }
        for band, val in liability_classes.items()
        if round(val) != 0
    ]
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows if len(rows) >= 2 else None


def _debt_card(owe: float, liability_classes: dict, ledger: LedgerSnapshot) -> dict | None:
    """The Debt card — Receivables' counterpart: `owe` (the "What you owe" KPI, a positive
    magnitude) with the same Loans/Credit rows as the rail. `None` with no credit or loan
    account, like Receivables with none."""
    accounts = ledger.of_type("credit") + ledger.of_type("loan")
    if not accounts:
        return None
    return {
        "value": round(owe, 2),
        "count_label": f"{len(accounts)} accounts",
        "rows": _debt_rows(liability_classes),
    }


def _largest_remainder_pcts(values: list[tuple[str, float]]) -> list[int]:
    """Whole percentages of `values` (key, value) that sum to exactly 100: floor each share,
    then hand the missing points out one at a time to the largest remainders — ties to the
    larger value, then the key. All zero when there is nothing to divide."""
    total = sum(value for _, value in values)
    if total <= 0:
        return [0] * len(values)
    exact = [value / total * 100 for _, value in values]
    pcts = [math.floor(share) for share in exact]
    order = sorted(
        range(len(values)),
        key=lambda i: (pcts[i] - exact[i], -values[i][1], values[i][0]),
    )
    for i in order[: 100 - sum(pcts)]:
        pcts[i] += 1
    return pcts


def _to_the_cent(parts: list[tuple[str, str, float]], own: float) -> list[tuple[str, str, float]]:
    """The parts are rounded one by one and the categories are converted at the page's FX
    snapshot, while `own` is net worth's own fold — so together they can miss `own` by a few
    cents. The largest part absorbs the difference, so the parts sum to `own` exactly."""
    gap = round(own - sum(value for _, _, value in parts), 2)
    if not parts or not gap:
        return parts
    i = max(range(len(parts)), key=lambda j: parts[j][2])
    key, kind, value = parts[i]
    return [*parts[:i], (key, kind, round(value + gap, 2)), *parts[i + 1 :]]


def _composition(breakdown: dict, rows: list[dict], base: str, rates: dict, *, own: float) -> dict:
    """What `own` is made of, from the SAME fold as `own` (``ledger.net_worth()``'s breakdown):
    four account classes plus one row per owned-asset category, share-adjusted and
    base-converted exactly as net worth counts them, summing to `own` to the cent. A row at or
    below zero is then left out (a margin-negative investments class is not something you
    own), so `total` exceeds `own` only then. Ordered by value, classes before categories,
    then key."""
    ac = breakdown["account_classes"]
    # A Brokerage total account's untracked part is invested: Investments, or Retirement for a
    # retirement account (both 0 while investment_value_source is off).
    retirement_untracked = breakdown.get("retirement_untracked_base", 0.0)
    classes = {
        "cash": ac.get("cash", 0.0) + ac.get("fixed_deposit", 0.0),
        "investments": (
            ac.get("settlement_cash", 0.0)
            + ac.get(UNTRACKED_BAND, 0.0)
            - retirement_untracked
            + breakdown["market_positions"]
            - breakdown.get("retirement_positions_base", 0.0)
            + breakdown["options_mark"]
        ),
        "retirement": (
            ac.get("retirement", 0.0)
            + retirement_untracked
            + breakdown.get("retirement_positions_base", 0.0)
        ),
        "receivable": ac.get("receivable", 0.0),
    }
    parts = [(key, "class", round(value, 2)) for key, value in classes.items()]
    categories = values_by_category_from_rows(rows, base, rates=rates)
    parts += [(key, "category", value) for key, value in categories.items()]
    parts = sorted(
        (part for part in _to_the_cent(parts, own) if part[2] > 0),
        key=lambda part: (-part[2], part[1] != "class", part[0]),
    )
    pcts = _largest_remainder_pcts([(key, value) for key, _, value in parts])
    return {
        "total": round(sum(value for _, _, value in parts), 2),
        "rows": [
            {"key": key, "kind": kind, "value": value, "pct": pct}
            for (key, kind, value), pct in zip(parts, pcts)
        ],
    }


def _asset_gain(rows: list[dict], base: str, rates: dict) -> float:
    """Σ over the live, cost-known owned assets of (value − cost) × share, base-converted (an
    asset's cost and value are native currency)."""
    gain = 0.0
    for asset in rows:
        value = value_as_of(asset, None)
        if asset.get("cost") is None or value is None:
            continue
        share = (asset.get("share_pct") if asset.get("share_pct") is not None else 100) / 100.0
        gain += to_base(
            (value - asset["cost"]) * share,
            asset.get("currency") or base,
            base,
            rates=rates,
        )
    return gain


def base_figures(asset: dict, base: str, rates: dict) -> dict:
    """One projected asset's figures in the base currency, at the page's rates — its whole
    value (the register orders its cards by it) and the owner's share of its value and of its
    cost (a category stack sums them), converted exactly as net worth converts. A rate the page
    doesn't have leaves the figure None rather than a guess."""
    share = (asset.get("share_pct") if asset.get("share_pct") is not None else 100) / 100.0
    currency = asset.get("currency") or base

    def convert(amount: float | None) -> float | None:
        if amount is None:
            return None
        try:
            return to_base(amount, currency, base, rates=rates)
        except MissingFxRateError:
            return None

    whole = convert(asset.get("value"))
    cost = convert(asset.get("cost"))
    return {
        "base_value": whole,
        "base_share_value": None if whole is None else round(whole * share, 2),
        "base_share_cost": None if cost is None else round(cost * share, 2),
    }


def _cash_sub(cash_accts: list[dict]) -> str:
    names: list[str] = []
    for account in cash_accts:
        label = (account.get("institution") or account.get("name") or "").strip()
        if label and label not in names:
            names.append(label)
    cash_sub = f"{len(cash_accts)} accounts"
    if names:
        cash_sub += " · " + ", ".join(names[:3])
    return cash_sub


def summary(
    rows: list[dict], base: str, *, net_worth: dict, market: dict, ledger: LedgerSnapshot
) -> dict:
    """`rows` are the live (non-archived) STORED asset payloads. `net_worth` is
    ``ledger.net_worth(base)`` — the ONE net-worth fold the headline, the class cards, the debt
    rows and the composition all read. `market` is ``portfolio_analytics.market_book(base)``:
    the investments book and the market's unrealized P&L. The conversions made here — the class
    cards' accounts, the asset gain, the composition's categories — use the `ledger` snapshot's
    rates; ``ledger.net_worth()`` loads the FX cache itself a moment later, so the composition
    is reconciled to `own` to the cent."""
    breakdown = net_worth["breakdown"]
    own = round(
        breakdown["accounts_asset"] + breakdown["market_positions"] + breakdown["options_mark"], 2
    )
    owe = abs(breakdown["accounts_liability"])
    book = market["book"]
    unrealized = round(market["upl"] + _asset_gain(rows, base, ledger.rates), 2)

    account_rows = {a["id"]: a for a in ledger.accounts}
    brokerages = {
        (account_rows.get(row.get("account_id") or "") or {}).get("institution")
        for row in book["rows"]
        if row.get("account_id")
        and (account_rows.get(row["account_id"]) or {}).get("institution")
        and (account_rows.get(row["account_id"]) or {}).get("type") != "retirement"
    }

    return {
        "own": own,
        "owe": round(owe, 2),
        "net_worth": net_worth["net_worth"],
        "unrealized": unrealized,
        "classes": {
            **_class_cards(breakdown, book, ledger, brokerages),
            "debt": _debt_card(owe, breakdown.get("liability_classes", {}), ledger),
        },
        "debt": _debt_rows(breakdown.get("liability_classes", {})),
        "composition": _composition(breakdown, rows, base, ledger.rates, own=own),
    }
