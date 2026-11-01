"""Cash-flow projection engine (RFC P1-A5 §3.2/§3.3).

The financial math behind ``GET /api/cashflow``: the spendable-cash starting balance,
the 3-month rolling-average recurring baseline, the historical month-end fold, and the
month-by-month forward projection. Moved out of ``api/cashflow.py`` (a router) so it
lives in ``services/`` and so ``services/commitments`` can call it without an up-import
into ``api/`` (the V3 dependency-rule violation). The router shrinks to request-parsing,
``build_projection(...)``, and response shaping.

Builds a month-by-month cash flow table = past actuals + forward projection:
  - history (past N months) → real income/expense flow + real month-end cash balance
                              (folded from the ledger via account_balance_as_of)
  - projection (current + future) built from:
      - ledger accounts     → starting cash balance (`cash`-type accounts only), plus the two
                              opt-in runway rungs — fixed deposits and investments
      - transactions.json   → 3-month rolling average for recurring income + expenses
      - profile.json        → fallback income/expense values when no transaction history
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date
from typing import Literal, Optional

from backend.core.user_time import user_today
from backend.db import read_cache
from backend.models.account import ON_BUDGET_TYPES, SPENDABLE_TYPES
from backend.services import ledger as svc
from backend.services.budgets import is_spending_expense
from backend.services.card_coverage import cards_without_activity
from backend.services.fx_cache import load_fx_cache

logger = logging.getLogger(__name__)

# Unset means no floor of the user's own: a balance is only "short" below zero. A flat 5,000
# fallback raised a red "Funding shortfall ... of your S$5,000 cash floor" on day one for a
# new user holding S$2,150 — a number nobody chose (persona QA 2026-10-13).
LOW_BALANCE_THRESHOLD_DEFAULT = 0
PROJECTION_MONTHS = 40
ROLLING_MONTHS = 3  # how many full calendar months to average
HISTORY_MONTHS = 6  # how many past months of actuals to prepend


# ---------------------------------------------------------------------------
# Starting balance helpers (ADR 2026-09-07-seven-account-types-one-axis)
# ---------------------------------------------------------------------------


def _account_total(
    base: str, types: frozenset[str] | set[str], txns: list[dict] | None = None
) -> float:
    """Σ balance (base ccy) of every non-archived account whose type is in ``types``."""
    accts = [a for a in svc.accounts.query_accounts() if a.get("type") in types]
    if txns is None:
        txns = svc.transactions.all()
    cutoffs = svc.cutoffs_by_account(accts, txns)
    rates = load_fx_cache()
    balances = svc.balances_by_account(accts, txns, cutoffs)
    total = 0.0
    for a in accts:
        total += svc.to_base(balances[a["id"]], a.get("currency", base), base, rates=rates)
    return round(total, 2)


def cash_balance_base(base: str, txns: list[dict] | None = None) -> float:
    """Cash on hand — Σ `cash` accounts. The ONE spendable-cash figure (Accounts, Assets, Plan,
    the AI snapshot all read this). Never nets a liability or settlement cash."""
    return _account_total(base, SPENDABLE_TYPES, txns)


def fixed_deposit_base(base: str, txns: list[dict] | None = None) -> float:
    """Σ `fixed_deposit` accounts — a separate runway rung: locked cash, sellable in days to
    weeks (breaking the term), not instantly spendable like ``cash_balance_base``."""
    return _account_total(base, {"fixed_deposit"}, txns)


def _positions_runway_base(base: str) -> float:
    """Vested market positions held in an `investment` account (base ccy), excluding property —
    what could be sold in days.

    Reuses ``ledger.row_base_value`` (the ONE canonical qty × price × quote_per_unit fold,
    shared with net worth and the X-ray) over every hydrated position row, excluding unvested
    equity-grant units (not owned yet — ADR 2026-07-31-unvested-equity-excluded-from-net-worth)
    and property (illiquid — can't be sold quickly).

    The HOLDING ACCOUNT decides too. A `retirement` account holds real positions (CPF unit
    trusts, a 401k fund) and none of them are spendable at any notice
    (ADR 2026-09-07-seven-account-types-one-axis), so folding in every hydrated row put locked
    retirement money into the runway."""
    from backend.services import positions as positions_service

    investment_ids = {
        a["id"] for a in svc.accounts.query_accounts() if a.get("type") == "investment"
    }
    rates = load_fx_cache()
    total = 0.0
    for row in positions_service.hydrated():
        if row.get("vesting_state") == "unvested" or row.get("asset_class") == "property":
            continue
        if row.get("account_id") not in investment_ids:
            continue
        total += svc.row_base_value(row, base, rates=rates)
    return round(total, 2)


def investments_runway_base(base: str, txns: list[dict] | None = None) -> float:
    """Positions + settlement cash of every `investment` account — debt travels with its
    collateral, so a drawn margin debit enters the runway exactly when the holdings do."""
    return round(_positions_runway_base(base) + _account_total(base, {"investment"}, txns), 2)


# ── Credit-card treatment (ADR 2026-07-09; the "signed" mode was removed 2026-07-10) ──
# There is ONE credit-card engine: YNAB-faithful payment envelopes. A payment-tracked card LEAVES
# the on-budget cash sum (see is_cash_account below) and a per-card "Credit Card Payments" envelope
# holds the earmark instead, so standing card debt no longer subtracts from Ready-to-Assign — it
# surfaces as the payment envelope's Underfunded. The old "signed" mode (a card's negative balance
# netting against cash) was removed after the payment-envelope engine was verified YNAB-exact
# against real YNAB books; the `credit_card_mode` flag and the branch are gone.


def is_payment_tracked_card(account: dict) -> bool:
    """Every `credit` account is payment-tracked (ADR 2026-09-07 widens ADR 2026-07-09's v1,
    which was `credit_card` only) — its debt is earmarked in a real per-card "Credit Card
    Payments" envelope instead of netting against cash (see ``is_cash_account``)."""
    return account.get("type") == "credit"


# ── On-budget classification (balance-based RTA) ──────────────────────────────────
# An account is ON-BUDGET when the plan assigns money from its balance; OFF-BUDGET
# ("tracking") accounts hold value that lives outside the envelope system. This is the
# derivation half of the balance identity RTA = on_budget_cash − baseline − Σ available.
# Type-only, no override (ADR 2026-09-07-seven-account-types-one-axis): `cash` and `credit`
# are the whole on-budget pool. Every other type (fixed_deposit, investment, retirement,
# receivable, loan) is off-budget/tracking — value that lives outside the plan.
#
# is_on_budget is the base derivation — kept as a thin predicate so its ~15 callers never churn.
# Two facades split off it for the two things a leg can mean to the plan (ADR 2026-07-09):
#   * is_cash_account  — does this account's balance join the CASH side of the RTA identity? A
#     payment-tracked card is EXCLUDED (its debt is earmarked in a real payment envelope instead —
#     see services.planning._iter_category_contributions), so it differs from is_on_budget only for
#     those cards.
#   * is_plan_source    — does this account's ledger legs still fill/drain a Planning envelope's
#     Activity? Currently IDENTICAL to is_on_budget (a card's own spending category still drains;
#     the auto-move fold then credits the card's payment envelope the same amount, keeping the
#     balance identity whole) — kept as a named concept distinct from the cash side.


def is_on_budget(account: dict) -> bool:
    """Is this account part of the ON-BUDGET pool the plan assigns from? Type-only:
    `cash` and `credit` — a card's negative balance is EARMARKED in its payment envelope
    (see ``is_cash_account``), not netted directly, but it still occupies the on-budget pool
    so its own spending category can drain (see ``is_plan_source``)."""
    return account.get("type") in ON_BUDGET_TYPES


def is_cash_account(account: dict) -> bool:
    """Does this account's balance join the CASH side of the balance-based RTA identity?

    Type-only: `cash`. A `credit` account is on-budget (``is_on_budget``) but excluded here —
    its debt is earmarked in a real per-card "Credit Card Payments" envelope instead (ADR
    2026-07-09), so it must not ALSO net against cash (that would double-count: the money would
    be both "yours" in cash AND reserved in the envelope).

    Feeds ``planning._cash_as_of`` and ``on_budget_cash_base``/``cash_balance_base``-adjacent
    reporting."""
    return account.get("type") == "cash"


def is_plan_source(account: dict) -> bool:
    """Does this account's ledger legs still fill/drain a Planning envelope's Activity?

    Identical to ``is_on_budget`` — a payment-tracked card is excluded from CASH
    (``is_cash_account``) but its own spending category must still drain when a card purchase
    posts; ``planning._iter_category_contributions``'s auto-move fold then credits the card's
    payment envelope the SAME amount, keeping the identity whole.

    Feeds ``planning.plan_leg_ok`` (also reused directly by 50/30/20's own activity fold,
    ``budget_engines.proportional_fold`` — ADR 2026-09-25)."""
    return is_on_budget(account)


def on_budget_cash_base(base: str, as_of_month: str | None = None) -> float:
    """Σ balances (base ccy) of every CASH account (``is_cash_account``) — the spendable pool
    the balance-based RTA identity draws from. A `credit` account is excluded entirely (its
    debt lives in a real payment envelope instead — see ``is_cash_account``). Off-budget/tracking
    accounts (fixed_deposit, investment, retirement, receivable, loan) are always excluded —
    their value lives outside the plan and never dilutes Ready-to-Assign.

    ``as_of_month`` (``YYYY-MM``) computes the balance as of the END of that month — only txns
    dated in or before it count — so per-month RTA reads the on-budget cash at that point in
    time (later months' cash accumulates the carry naturally). None → current (all txns).

    NOTE: this CURRENT-runway helper excludes archived accounts; ``planning._cash_as_of`` (the
    RTA identity's cash side) deliberately INCLUDES them so historical months stay correct —
    a documented divergence, not drift."""
    all_txns = svc.transactions.all()
    accts = svc.accounts.all()
    # Cutoffs come from the FULL book; only the FOLD is capped at the month.
    cutoffs = svc.cutoffs_by_account(accts, all_txns)
    txns = (
        [t for t in all_txns if (t.get("date") or "")[:7] <= as_of_month]
        if as_of_month
        else all_txns
    )
    balances = svc.balances_by_account(accts, txns, cutoffs)
    total = 0.0
    for a in accts:
        if a.get("archived") or not is_cash_account(a):
            continue
        total += svc.to_base(balances[a["id"]], a.get("currency", base), base)
    return round(total, 2)


# ---------------------------------------------------------------------------
# Recurring baseline — 3-month rolling average from transaction history
# ---------------------------------------------------------------------------


def _load_profile() -> dict:
    from backend.services.profile import read_profile_raw

    return read_profile_raw()


def _month_range(year: int, month: int) -> tuple:
    last = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _months_with_data(txns: list) -> int:
    """Count distinct calendar months represented in a transaction list (min 1)."""
    months = {t.get("date", "")[:7] for t in txns if t.get("date")}
    return max(len(months), 1)


def _add_months(year: int, month: int, n: int) -> tuple:
    month += n
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def _sub_months(year: int, month: int, n: int) -> tuple:
    return _add_months(year, month, -n)


def _rolling_avg_income(base: str, all_txns: list[dict] | None = None) -> Optional[float]:
    """Average monthly income (base currency) over the last ROLLING_MONTHS full months.

    Divides by months with actual data, not the window size, so sparse history
    doesn't artificially deflate the average.

    NOT the projection baseline any more (.claude/rfcs/2026-07-27-cashflow-stream-model.md) —
    it averages EVERY income transaction, recurring or not, which extrapolated one-off
    proceeds forward forever. Retained as the *unclassified* headline figure reported on
    ``actual_income_avg`` (what genuinely landed per month, the honest actuals number) and as
    the empty-book probe that selects the profile fallback.

    `all_txns` (optional): an already-loaded, already-deleted-filtered full list — filtered
    in-memory via ``_filter_txns`` instead of re-querying the store when given."""
    today = user_today()
    end_year, end_month = _sub_months(today.year, today.month, 1)
    start_year, start_month = _sub_months(end_year, end_month, ROLLING_MONTHS - 1)

    date_from, _ = _month_range(start_year, start_month)
    _, date_to = _month_range(end_year, end_month)

    txns = (
        _filter_txns(all_txns, kind="income", date_from=date_from, date_to=date_to)
        if all_txns is not None
        else svc.transactions.query_txns(kind="income", date_from=date_from, date_to=date_to)
    )
    if not txns:
        return None

    rates = load_fx_cache()
    total = sum(svc.txn_base_amount(t, base, rates) for t in txns)
    divisor = _months_with_data(txns)
    return round(total / divisor, 2)


def _rolling_avg_expenses(base: str, all_txns: list[dict] | None = None) -> Optional[float]:
    """Average monthly expenses (base currency) over the last ROLLING_MONTHS full months.

    Divides by months with actual data, not the window size.

    `all_txns` (optional): see ``_rolling_avg_income``."""
    today = user_today()
    end_year, end_month = _sub_months(today.year, today.month, 1)
    start_year, start_month = _sub_months(end_year, end_month, ROLLING_MONTHS - 1)

    date_from, _ = _month_range(start_year, start_month)
    _, date_to = _month_range(end_year, end_month)

    # Exclude the investment leg (a security buy is cash → asset, not spending) so a lumpy stock
    # buy never inflates the recurring expense baseline. Filter BEFORE the empty check so a window
    # of only investment buys yields no baseline (None → profile fallback), matching income.
    raw = (
        _filter_txns(all_txns, kind="expense", date_from=date_from, date_to=date_to)
        if all_txns is not None
        else svc.transactions.query_txns(kind="expense", date_from=date_from, date_to=date_to)
    )
    txns = [t for t in raw if is_spending_expense(t)]
    if not txns:
        return None

    rates = load_fx_cache()
    total = sum(svc.txn_base_amount(t, base, rates) for t in txns)
    divisor = _months_with_data(txns)
    return round(total / divisor, 2)


def ledger_monthly_actuals(base: str) -> dict:
    """What the ledger says landed per month over the last ROLLING_MONTHS full months —
    income, spending and their difference (None where the ledger has no data). The honest
    counterpart to the profile's STATED income/expenses, which nothing ever checks."""
    income = _rolling_avg_income(base)
    spending = _rolling_avg_expenses(base)
    net = round(income - spending, 2) if income is not None and spending is not None else None
    return {"months": ROLLING_MONTHS, "income": income, "spending": spending, "net": net}


def runway_from_projection(projection: dict) -> dict:
    """The cash runway as one record: spendable cash, the monthly burn it is divided by, and the
    months that lasts (``None`` while there is no burn to divide by).

    Every surface that quotes a runway reads this — the Cash Flow screen's hero, the Dashboard's
    runway widget and its "Thin cash buffer" flag — so they cannot divide different cash by a
    different burn (the flag used to average ALL spending including one-offs, the screen the
    classified baseline: "about 1.9 months" beside 2.1). The burn is the projection's
    ``monthly_expenses_avg``: named streams plus the variable residual, growth-compounded by
    ``expense_growth_pct``. ``cards_missing_spending`` names the cards whose spending the burn
    cannot see yet."""
    return {
        "cash": float(projection.get("liquid_balance_base") or 0.0),
        "burn": float(projection.get("monthly_expenses_avg") or 0.0),
        "months": projection.get("income_runway_months"),
        "expense_growth_pct": float(projection.get("expense_growth_pct") or 0.0),
        "cards_missing_spending": projection.get("cards_missing_spending") or [],
    }


def cash_runway(*, book: svc.LedgerBook | None = None) -> dict:
    """The saved-plan cash runway (cash only; the Cash Flow toggles for fixed deposits and
    investments are what-ifs on that screen, not part of this). `book` is the ledger snapshot a
    caller that already read it shares (`ledger.read_book`)."""
    all_txns = None if book is None else [t for t in book.txns if not t.get("deleted")]
    return runway_from_projection(build_projection(months=1, history_months=0, all_txns=all_txns))


# ---------------------------------------------------------------------------
# Main projection builder
# ---------------------------------------------------------------------------


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%b %Y")


DEFAULT_EXPENSE_GROWTH_PCT = 3.0  # annual %, applied as monthly compounding
DEFAULT_INCOME_GROWTH_PCT = 0.0

# Shown beside the variable (residual) buckets so they read as an average of many irregular
# items rather than a single mysterious charge.
_VARIABLE_NOTE = "3-month average of irregular items"

# The "we did not classify" shape — an empty book (profile fallback) or a classification
# failure. `classified=False` is what makes every downstream branch fall back to the legacy
# flat average, so it must stay a complete, safe-to-read dict.
_UNCLASSIFIED: dict = {
    "classified": False,
    "streams": [],
    "candidates": [],
    "variable_in": 0.0,
    "variable_out": 0.0,
    "one_offs": [],
}


def _classified_baseline(
    base: str, today: date, baseline_source: str, all_txns: list[dict] | None = None
) -> dict:
    """Split the ledger into streams / variable residual / one-offs.

    Fails OPEN and LOUD-IN-LOGS: any error inside classification degrades to the legacy flat
    average (``classified=False``) rather than taking the whole projection — and therefore
    every screen that reads it — down with it.
    """
    if baseline_source == "profile":
        return _UNCLASSIFIED  # empty book: nothing to classify, profile figures stand
    try:
        from backend.services.recurring_baseline import resolve_baseline

        result = resolve_baseline(base, load_fx_cache(), today, all_txns)
        return {**result, "classified": True}
    except Exception:  # pragma: no cover - defensive
        logger.exception("cashflow: recurring-stream classification failed; using flat average")
        return _UNCLASSIFIED


def _stream_month_map(streams: list, today: date, months: int) -> dict:
    """{year_month: {"in": total, "out": total, "events": [...]}} over the horizon.

    ``today`` is also passed down as the already-posted cutoff (see
    ``recurring.stream_month_amounts``) — the in-progress month only carries streams that
    HAVEN'T occurred yet, since an occurrence on or before today is already in the real
    transactions ``_current_month_actuals`` folds into the same month.
    """
    from backend.services.recurring import stream_month_amounts

    start_ym = _ym(today.year, today.month)
    out: dict[str, dict] = {}
    for stream in streams:
        direction = stream.get("direction") or "out"
        for ym, amount in stream_month_amounts(stream, start_ym, months, today=today).items():
            slot = out.setdefault(ym, {"in": 0.0, "out": 0.0, "events": []})
            slot[direction] = round(slot[direction] + amount, 2)
            slot["events"].append(
                {
                    "label": stream.get("label") or "Recurring",
                    "category": "Income" if direction == "in" else "Expenses",
                    "direction": direction,
                    "amount_base": round(amount, 2),
                    "note": f"{stream.get('cadence', '')} · recurring".strip(" ·"),
                    "status": "recurring",
                }
            )
    return out


def _stream_events(slot: dict) -> list:
    """A stream's events for one month, at their real dated amount — never smoothed.

    The in-progress month's already-elapsed occurrences never reach here at all: they're
    excluded upstream by ``stream_month_amounts``'s ``today`` cutoff, since they're already
    in the real actuals folded into the same month (counting them again would double-book).
    """
    events = []
    for ev in slot.get("events") or []:
        amount = round(float(ev["amount_base"]), 2)
        if amount > 0:
            events.append({**ev, "amount_base": amount})
    return events


def _monthly_equivalent(stream_months: dict, direction: str, months: int) -> float:
    """Average monthly contribution of all streams on one side, over the horizon."""
    if months <= 0:
        return 0.0
    total = sum(slot.get(direction, 0.0) for slot in stream_months.values())
    return round(total / months, 2)


def expense_at_month(monthly_expenses: float, expense_growth_pct: float, m: int) -> float:
    """Expected expense at (0-indexed) month `m` under a flat annual growth rate, compounded
    monthly. Shared with `cashflow_projections.runway()` so its plotted trajectory depletes
    cash at the SAME rate `_runway_with_growth` uses for the headline `runway_months`."""
    monthly_rate = expense_growth_pct / 100 / 12
    return monthly_expenses * ((1 + monthly_rate) ** m)


def _runway_with_growth(
    balance: float, monthly_expenses: float, expense_growth_pct: float
) -> Optional[float]:
    """Iterative runway: how many months until balance hits zero, accounting for expense growth."""
    if monthly_expenses <= 0:
        return None
    if balance <= 0:
        return 0.0  # already exhausted, not a negative runway
    if expense_growth_pct == 0:
        return round(balance / monthly_expenses, 1)
    remaining = balance
    for m in range(10_000):
        exp = expense_at_month(monthly_expenses, expense_growth_pct, m)
        remaining -= exp
        if remaining <= 0:
            fraction = (remaining + exp) / exp  # portion of this month covered
            return round(m + fraction, 1)
    return None  # effectively unlimited


def _category_label(cat: str, label_map: dict[str, str] | None = None) -> str:
    """Human label for a stored category id — mirrors ``services.planning._category_label``.
    Categories are opaque ids (``cat_xxx``); this is a display fallback, not a persisted
    value, so an unrecognized key title-cases as a last resort.

    Pass a pre-built ``{id: label}`` map (``_category_label_map()``) when labelling many
    transactions in a loop — the default (no map) does a single-row store lookup per call,
    which is fine for one-off use but was measured reloading the entire category store from
    disk 798 times over a 12-month history (2026-07-27 perf investigation)."""
    if label_map is not None:
        label = label_map.get(cat)
    else:
        from backend.db.category_store import CategoryStore

        row = CategoryStore().get(cat)
        label = row.get("label") if row else None
    if label and str(label).strip():
        return str(label)
    return cat.replace("_", " ").title()


def _category_label_map() -> dict[str, str]:
    """``{category_id: label}`` for every category, loaded ONCE — the batch counterpart to
    ``_category_label``'s per-call store lookup.

    NOT routed through ``services.labels.label_maps`` (the repo's usual "build once per
    response" convention for this exact shape) despite being the same pattern: that helper
    goes through ``services.categories.get_all_categories``, which imports ``CategoryStore``
    at MODULE level — a test mocking ``backend.db.category_store.CategoryStore`` (patching the
    origin module) never reaches it, since the importing module's name binding was already
    resolved at import time. This function keeps the LAZY in-function import
    (``from backend.db.category_store import CategoryStore``) the original code used, which a
    same-target monkeypatch correctly intercepts on every call — verified: routing through
    ``label_maps`` broke 5 existing tests in ``test_cashflow_current_month.py``."""
    from backend.db.category_store import CategoryStore

    return {r["id"]: r.get("label") for r in CategoryStore().all()}


def _filter_txns(txns: list[dict], *, kind: str, date_from: str, date_to: str) -> list[dict]:
    """In-memory equivalent of ``TransactionStore.query_txns(kind=, date_from=, date_to=)``
    over an already-loaded, already-deleted-filtered list — avoids re-reading the whole store
    from disk for every month of a historical loop."""
    return [t for t in txns if t.get("kind") == kind and date_from <= t.get("date", "") <= date_to]


def _historical_months(
    base: str, n: int, low_balance_threshold: float, all_txns: list[dict] | None = None
) -> list[dict]:
    """Real per-month cash flow for the ``n`` calendar months before the current month.

    In/Out/Net come from actual income/expense transactions (``base_amount``). The
    ending balance is the *real* liquid cash position at each month-end, folded from the
    full ledger via ``account_balance_as_of`` (so it reflects transfers and investment
    moves too — not just the income/expense flow shown). Cash-only: market positions
    aren't priced historically, so they're excluded from the historical balance.
    """
    if n <= 0:
        return []

    accts = [a for a in svc.accounts.query_accounts() if a.get("type") == "cash"]
    if all_txns is None:
        all_txns = [t for t in svc.transactions.all() if not t.get("deleted")]
    _cutoffs = svc.cutoffs_by_account(accts, all_txns)
    label_map = _category_label_map()
    rates = load_fx_cache()
    today = user_today()
    rows: list[dict] = []

    for k in range(n, 0, -1):  # oldest → newest
        y, m = _sub_months(today.year, today.month, k)
        date_from, date_to = _month_range(y, m)

        inc_txns = _filter_txns(all_txns, kind="income", date_from=date_from, date_to=date_to)
        # Investment buys are excluded from the In/Out/Net flow (not spending); the month-end
        # `end_bal` below still folds them via account_balance_as_of, since the cash did leave.
        exp_txns = [
            t
            for t in _filter_txns(all_txns, kind="expense", date_from=date_from, date_to=date_to)
            if is_spending_expense(t)
        ]

        total_in = round(sum(svc.txn_base_amount(t, base, rates) for t in inc_txns), 2)
        total_out = round(sum(svc.txn_base_amount(t, base, rates) for t in exp_txns), 2)
        net = round(total_in - total_out, 2)

        # Real cash balance at month-end (full ledger fold, capped at date_to).
        end_bal = round(
            sum(
                svc.to_base(
                    svc.account_balance_as_of(a, date_to, all_txns, _cutoffs[a["id"]]),
                    a.get("currency", base),
                    base,
                )
                for a in accts
            ),
            2,
        )

        # No cash account had begun tracking by this month-end (a statement imported for the
        # months before an account's Starting Balance): the balance is 0 because nothing is
        # tracked yet, not because the cash was 0, and the screen must not print it as one.
        balance_tracked = any(c is None or c <= date_to for c in _cutoffs.values())

        events = [
            {
                "label": t.get("description") or "Income",
                "category": (
                    _category_label(t["category"], label_map) if t.get("category") else "Income"
                ),
                "direction": "in",
                "amount_base": svc.txn_base_amount(t, base, rates),
                "note": t.get("date", ""),
                "status": "actual",
            }
            for t in inc_txns
        ] + [
            {
                "label": t.get("description") or "Expense",
                "category": (
                    _category_label(t["category"], label_map) if t.get("category") else "Expenses"
                ),
                "direction": "out",
                "amount_base": svc.txn_base_amount(t, base, rates),
                "note": t.get("date", ""),
                "status": "actual",
            }
            for t in exp_txns
        ]

        rows.append(
            {
                "year_month": _ym(y, m),
                "label": _label(y, m),
                "is_current": False,
                "is_history": True,
                "events": events,
                "total_in": total_in,
                "total_out": total_out,
                "net": net,
                "balance": end_bal,
                "balance_tracked": balance_tracked,
                "is_tight": 0 < end_bal < low_balance_threshold,
                "is_negative": end_bal < 0,
            }
        )

    # Drop leading months that predate the transaction history (no flow at all) — a
    # flow view shouldn't open with empty months.
    first = 0
    while (
        first < len(rows)
        and not rows[first]["events"]
        and rows[first]["total_in"] == 0
        and rows[first]["total_out"] == 0
    ):
        first += 1
    return rows[first:]


def _current_month_actuals(
    base: str, today: date, all_txns: list[dict] | None = None
) -> tuple[list[dict], float, float]:
    """Real income/expense events from day 1 through ``today`` for the in-progress month.

    Mirrors ``_historical_months``' per-month query, bounded at today instead of month-end
    since the month hasn't finished yet — the caller prorates the recurring baseline over
    the days still remaining.
    """
    date_from, _ = _month_range(today.year, today.month)
    date_to = today.isoformat()
    rates = load_fx_cache()
    label_map = _category_label_map()

    if all_txns is None:
        inc_txns = svc.transactions.query_txns(kind="income", date_from=date_from, date_to=date_to)
        raw_exp = svc.transactions.query_txns(kind="expense", date_from=date_from, date_to=date_to)
    else:
        inc_txns = _filter_txns(all_txns, kind="income", date_from=date_from, date_to=date_to)
        raw_exp = _filter_txns(all_txns, kind="expense", date_from=date_from, date_to=date_to)
    exp_txns = [t for t in raw_exp if is_spending_expense(t)]

    events = [
        {
            "label": t.get("description") or "Income",
            "category": (
                _category_label(t["category"], label_map) if t.get("category") else "Income"
            ),
            "direction": "in",
            "amount_base": svc.txn_base_amount(t, base, rates),
            "note": t.get("date", ""),
            "status": "actual",
        }
        for t in inc_txns
    ] + [
        {
            "label": t.get("description") or "Expense",
            "category": (
                _category_label(t["category"], label_map) if t.get("category") else "Expenses"
            ),
            "direction": "out",
            "amount_base": svc.txn_base_amount(t, base, rates),
            "note": t.get("date", ""),
            "status": "actual",
        }
        for t in exp_txns
    ]
    total_in = round(sum(svc.txn_base_amount(t, base, rates) for t in inc_txns), 2)
    total_out = round(sum(svc.txn_base_amount(t, base, rates) for t in exp_txns), 2)
    return events, total_in, total_out


@read_cache.cached(
    # `all_txns` is deliberately absent from the key: a preloaded ledger is never memoised
    # (the `when` guard below computes it directly — read_cache's documented contract for
    # pre-loaded mutable inputs).
    lambda months=PROJECTION_MONTHS, income_override=None, expense_override=None, include_fixed_deposits=False, include_investments=False, expense_growth_pct=DEFAULT_EXPENSE_GROWTH_PCT, income_growth_pct=DEFAULT_INCOME_GROWTH_PCT, history_months=HISTORY_MONTHS, extra_events=None, all_txns=None: (
        months,
        income_override,
        expense_override,
        bool(include_fixed_deposits),
        bool(include_investments),
        expense_growth_pct,
        income_growth_pct,
        history_months,
        repr(extra_events) if extra_events else None,
    ),
    when=lambda *a, **kw: kw.get("all_txns") is None,
)
def build_projection(
    months: int = PROJECTION_MONTHS,
    income_override: Optional[float] = None,
    expense_override: Optional[float] = None,
    *,
    include_fixed_deposits: bool = False,
    include_investments: bool = False,
    expense_growth_pct: float = DEFAULT_EXPENSE_GROWTH_PCT,
    income_growth_pct: float = DEFAULT_INCOME_GROWTH_PCT,
    history_months: int = HISTORY_MONTHS,
    extra_events: Optional[dict] = None,
    all_txns: Optional[list[dict]] = None,
) -> dict:
    # extra_events: {year_month: [event,…]} overlaid onto the projection (dependency-inverted —
    # cashflow stays ignorant of where they come from). Commitment installments (outflows) and
    # funding draws (inflows) ride in this way; see backend/services/commitments.projected_events.
    if extra_events is None:
        extra_events = {}

    import time as _time

    _perf_log = __import__("logging").getLogger(__name__)
    profile = _load_profile()
    today = user_today()
    base = svc.base_currency()
    # AFTER the profile read: `load_book` must time the ledger load and nothing else, or the
    # field lies about which leg is slow — the whole point of this log line.
    _perf_t0 = _time.perf_counter()

    # ONE load of the book for this whole projection. Every leg below used to fetch its own:
    # profiled 2026-08-11 at 4978ms total, of which transaction_store.all() was TEN calls /
    # 2743ms and query_txns EIGHT calls / 1785ms — 91% of the runtime spent re-reading and
    # re-deserialising the SAME 2003 rows. One load is ~85ms and one pass over the result is
    # ~5ms, so the work was never the problem; the repetition was.
    #
    # The `txns`/`all_txns` seams every helper below accepts exist so THIS load is the only
    # one — every helper is passed this list rather than reloading its own. Deleted-filtered
    # here because that is the contract every one of them states
    # (_filter_txns: "already-loaded, already-deleted-filtered"; query_txns defaults
    # include_deleted=False, so an unfiltered list would silently change the numbers).
    # A caller composing several ledger folds (forecast scorecard) passes its own load in.
    if all_txns is None:
        all_txns = [t for t in svc.transactions.all() if not t.get("deleted")]
    _perf_t_loaded = _time.perf_counter()

    # --- Starting balance: cash on hand + the two opt-in runway rungs ---
    # Both rungs are computed unconditionally: the response reports each one whether or not its
    # toggle is on (that is what the toggle's "+S$x" hint renders), so a toggle-gated local
    # meant computing the OFF rung a second time in the response dict. The toggle applies to
    # the SUM, not to the figure.
    cash_base = cash_balance_base(base, all_txns)
    _perf_t1 = _time.perf_counter()
    fd_base = fixed_deposit_base(base, all_txns)
    inv_base = investments_runway_base(base, all_txns)
    _perf_t2 = _time.perf_counter()
    liquid_balance_base = round(
        cash_base
        + (fd_base if include_fixed_deposits else 0.0)
        + (inv_base if include_investments else 0.0),
        2,
    )

    # --- Recurring baseline: transactions → profile fallback ---
    txn_income = _rolling_avg_income(base, all_txns)
    _perf_t3 = _time.perf_counter()
    txn_expenses = _rolling_avg_expenses(base, all_txns)
    _perf_t4 = _time.perf_counter()

    profile_income = float(profile.get("monthly_net_income") or 0)
    profile_expenses = float(profile.get("monthly_expenses") or 0)

    actual_income = txn_income if txn_income is not None else profile_income
    actual_expenses = txn_expenses if txn_expenses is not None else profile_expenses

    if txn_income is not None and txn_expenses is not None:
        baseline_source: Literal["transactions", "profile", "mixed"] = "transactions"
    elif txn_income is None and txn_expenses is None:
        baseline_source = "profile"
    else:
        baseline_source = "mixed"

    # --- Classify that flow into streams / variable / one-offs -------------------------
    # The flat `actual_*` averages above stay the reported ACTUALS, but they are no longer
    # what gets extrapolated: a one-off (an equity-sale proceed, a one-time repair) inside
    # the window would otherwise repeat every month for the whole horizon. `streams` land on
    # their own calendar months; only the genuinely-irregular residual is spread flat.
    # An empty book keeps the profile fallback untouched.
    # See .claude/rfcs/2026-07-27-cashflow-stream-model.md.
    classified = _classified_baseline(base, today, baseline_source, all_txns)
    stream_months = _stream_month_map(classified["streams"], today, months)
    flat_income = classified["variable_in"] if classified["classified"] else actual_income
    flat_expenses = classified["variable_out"] if classified["classified"] else actual_expenses

    # --- Apply simulation overrides ---
    # An explicit override replaces the WHOLE side (streams included) — it is the caller
    # saying "assume this much per month", so streams must not be added on top of it.
    simulation_active = income_override is not None or expense_override is not None
    monthly_income = float(income_override) if income_override is not None else flat_income
    monthly_expenses = float(expense_override) if expense_override is not None else flat_expenses
    if income_override is not None:
        stream_months = {ym: {**v, "in": 0.0} for ym, v in stream_months.items()}
    if expense_override is not None:
        stream_months = {ym: {**v, "out": 0.0} for ym, v in stream_months.items()}

    # --- Income runway (iterative to account for expense growth) ---
    # Runway must see the FULL monthly outflow, not just the variable slice — rent and
    # subscriptions burn cash too. `effective_*` = flat residual + every stream's own
    # monthly equivalent, and is also what the response reports as the headline average.
    effective_income = round(monthly_income + _monthly_equivalent(stream_months, "in", months), 2)
    effective_expenses = round(
        monthly_expenses + _monthly_equivalent(stream_months, "out", months), 2
    )
    income_runway = _runway_with_growth(liquid_balance_base, effective_expenses, expense_growth_pct)

    low_balance_threshold = float(
        profile.get("cashflow_low_balance_threshold") or LOW_BALANCE_THRESHOLD_DEFAULT
    )
    window_y, window_m = _sub_months(today.year, today.month, ROLLING_MONTHS)
    cards_missing_spending = cards_without_activity(
        _month_range(window_y, window_m)[0], today.isoformat(), all_txns
    )

    # --- Past actuals (real per-month flow + real month-end cash balance) ---
    _perf_t5 = _time.perf_counter()
    history = _historical_months(base, history_months, low_balance_threshold, all_txns)
    _perf_t6 = _time.perf_counter()
    _perf_log.info(
        "PERF build_projection load_book=%.1fms cash_balance=%.1fms runway_rungs=%.1fms "
        "rolling_income=%.1fms rolling_expenses=%.1fms classify_streams_etc=%.1fms "
        "historical_months=%.1fms(n=%d)",
        (_perf_t_loaded - _perf_t0) * 1000,
        (_perf_t1 - _perf_t_loaded) * 1000,
        (_perf_t2 - _perf_t1) * 1000,
        (_perf_t3 - _perf_t2) * 1000,
        (_perf_t4 - _perf_t3) * 1000,
        (_perf_t5 - _perf_t4) * 1000,
        (_perf_t6 - _perf_t5) * 1000,
        history_months,
    )

    # --- Month-by-month projection ---
    result = list(history)
    balance = liquid_balance_base
    cur_year, cur_month = today.year, today.month

    exp_monthly_rate = expense_growth_pct / 100 / 12
    inc_monthly_rate = income_growth_pct / 100 / 12

    for i in range(months):
        y, m = _add_months(cur_year, cur_month, i)
        ym = _ym(y, m)
        is_current = y == cur_year and m == cur_month
        events = []
        actual_net = 0.0

        if is_current:
            # The month is in progress: fold real transactions from day 1 through today into
            # the row (so "TODAY" surfaces what's actually happened, not just an average), and
            # prorate the recurring baseline over the days still remaining. `balance` below
            # starts from today's REAL cash position, which already reflects the elapsed actual
            # flow — adding the full month's average on top would double-count those days.
            actual_events, actual_in, actual_out = _current_month_actuals(base, today, all_txns)
            events.extend(actual_events)
            actual_net = actual_in - actual_out

            days_in_month = monthrange(y, m)[1]
            remaining_days = max(days_in_month - today.day, 0)
            prorate = remaining_days / days_in_month
            month_income = monthly_income * prorate
            month_expenses = monthly_expenses * prorate
        else:
            prorate = 1.0
            month_income = monthly_income * ((1 + inc_monthly_rate) ** i)
            month_expenses = monthly_expenses * ((1 + exp_monthly_rate) ** i)

        # Each recurring stream lands as its OWN named event, in the months its cadence
        # actually falls in (an annual bonus in one month, not 1/12th every month), so the
        # projection is legible: "Salary", "Rent", "Netflix" — never one opaque blob.
        for ev in _stream_events(stream_months.get(ym, {})):
            events.append(ev)

        # The VARIABLE bucket: everything left after the named streams and the excluded
        # one-offs — irregular income and everyday spending, which only mean anything as an
        # average. The note says so explicitly: a bare "Other income +S$1,234" is a number the
        # user cannot place (owner: "where is this coming from?"), while naming it as a
        # 3-month average of irregular items is checkable against the register.
        if month_income > 0:
            events.append(
                {
                    "label": "Irregular income" if classified["classified"] else "Salary",
                    "category": "Income",
                    "direction": "in",
                    "note": _VARIABLE_NOTE if classified["classified"] else "",
                    # Third group: neither a dated one-off nor a named stream, but an average
                    # over many items — the UI separates it so it never reads as a single bill.
                    "variable": bool(classified["classified"]),
                    "amount_base": round(month_income, 2),
                    "status": "recurring",
                }
            )

        if month_expenses > 0:
            events.append(
                {
                    "label": "Everyday spending" if classified["classified"] else "Living expenses",
                    "category": "Expenses",
                    "direction": "out",
                    "note": _VARIABLE_NOTE if classified["classified"] else "",
                    "variable": bool(classified["classified"]),
                    "amount_base": round(month_expenses, 2),
                    "status": "recurring",
                }
            )

        # Overlay committed obligations + funding draws falling in this month.
        for ev in extra_events.get(ym, []):
            events.append(ev)

        total_in = sum(e["amount_base"] for e in events if e["direction"] == "in")
        total_out = sum(e["amount_base"] for e in events if e["direction"] == "out")
        net = total_in - total_out
        balance += net - actual_net

        result.append(
            {
                "year_month": ym,
                "label": _label(y, m),
                "is_current": is_current,
                "is_history": False,
                "events": events,
                "total_in": round(total_in, 2),
                "total_out": round(total_out, 2),
                "net": round(net, 2),
                "balance": round(balance, 2),
                "is_tight": 0 < balance < low_balance_threshold,
                "is_negative": balance < 0,
            }
        )

    return {
        "months": result,
        "low_balance_threshold": low_balance_threshold,
        "cash_balance_base": cash_base,
        "fixed_deposit_base": fd_base,
        "investments_base": inv_base,
        "include": {
            "fixed_deposits": include_fixed_deposits,
            "investments": include_investments,
        },
        "liquid_balance_base": liquid_balance_base,
        "actual_income_avg": actual_income,
        "actual_expenses_avg": actual_expenses,
        # The headline monthly figures now mean "streams + variable residual" — the full
        # expected monthly flow. Unclassified books report the flat average, unchanged.
        "monthly_income_avg": effective_income,
        "monthly_expenses_avg": effective_expenses,
        "baseline_source": baseline_source,
        # --- Stream classification (additive; every key above is unchanged) ---
        "classified": classified["classified"],
        "streams": classified["streams"],
        "stream_candidates": classified["candidates"],
        "variable_income_avg": classified["variable_in"],
        "variable_expenses_avg": classified["variable_out"],
        "excluded_one_offs": classified["one_offs"],
        "income_runway_months": income_runway,
        "simulation_active": simulation_active,
        "income_override": income_override,
        "expense_override": expense_override,
        "expense_growth_pct": expense_growth_pct,
        "income_growth_pct": income_growth_pct,
        "history_months": len(history),
        # Cards with no purchases in the window the burn is averaged over — their spending is
        # not in `monthly_expenses_avg`, so the runway is flattered until it is imported.
        "cards_missing_spending": cards_missing_spending,
    }
