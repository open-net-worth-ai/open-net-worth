"""
The no-bucket hold is gated on ACTUAL CASH, not on "every plan-source leg except a
payment-tracked card's own spend".

Regression suite for the 2026-09-10 security review of ADR
2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end. That ADR claimed one
accumulator (`cash_activity_by_key`) could serve both the credit-card coverage cap and the new
no-bucket hold — "one definition of which legs are cash, two readers". It could not: the cap's
predicate is "every plan-source leg EXCEPT a payment-tracked card's own expense", which admits
`credit` accounts, while the identity's cash side (`_cash_as_of`) sums only `type == "cash"`.
Every leg in the gap FABRICATED Ready to Assign — the hold handed back money that never left a
cash account, so RTA rose by the amount of a purely card-side movement.

Three reachable shapes, each reproduced against the pre-fix engine with the numbers below:

    (a) transfer OUT of a live, payment-tracked card to an off-budget account, categorized to an
        UNLINKED category — the transfer branch only checks for money arriving AT a card, so the
        card's own outflow fell through into the categorized-transfer fold.   S$200 → RTA +200
    (b) an ARCHIVED card's uncategorized spend — `sync_card_payment_envelopes` retires an
        archived card's payment envelope and nulls the FK, after which its spend stops taking
        the card branch and takes the cash fold instead.                      S$100 → RTA +100
    (c) an account RETYPED from `cash` to `credit` — `type` is already `credit` (so `_cash_as_of`
        stops counting it) while `payment_envelope_id` is still unset (so the spend still takes
        the cash fold).                                                       S$300 → RTA +300

The fix gates the no-bucket accumulator on `ctx["cash_ids"]` — the EXACT set `_cash_as_of` sums,
so both sides of the identity provably draw from the same account universe — and re-runs
`sync_card_payment_envelopes()` when `update_account` flips an account's type to or from
`credit`, which closes (c) and (b)'s restart-dependence at the root.

TWO MORE SHAPES, from the 2026-09-10 review round that followed. Both are the SAME class — a
reader in this machinery consulting the FULL activity fold where only the cash-only one is
meaningful — reached through code the first three never touched:

    (d) `_unbudgeted_keys`, the INCLUSION filter behind both the held map and the "Outside
        envelopes" display, dropped any key whose FULL activity nets to ~0. Its stated reason
        ("card spend is never positive") only ever held for EXPENSES: a categorized TRANSFER
        arriving at an on-budget account folds POSITIVE, and so does a categorized investment
        SELL. Land either at a `credit` account under the same label as a real cash expense and
        the key vanished from the map AND the display, draining RTA by the held amount with
        nothing on screen to show for it.        S$100 cash spend + S$100 offset → RTA +100
    (e) an INFLOW on an unlinked category never entered the fold at all (`if cat in linked`),
        so a refund could not cancel the hold its own spend had opened: the hold stayed at
        −100 while the refund independently raised RTA through cash — the same S$100 counted
        twice, compounding once per spend/refund cycle in the month.   4 × S$500 → RTA +2,000

(d) is fixed by admitting a key on EITHER fold (`_unbudgeted_keys`) while still valuing it from
the cash-only one; (e) by folding an unlinked inflow into the no-bucket accumulator ALONE
(`fold_no_bucket_only`) — never into Activity (there is no envelope for it to be Activity on)
and never into the card coverage cap's pool. An INFLOW category (blank / "ready_to_assign")
still folds nowhere: it is inflow-to-assign by definition, and netting it into the Uncategorized
bucket would wipe that bucket's hold for anyone whose salary lands uncategorized.

THE CARD OWES S$500 throughout (ADR 2026-11-02-a-card-in-credit-is-budget-money). A card's
balance ABOVE ZERO is budget money now, so money landing on a card that owes nothing really is
money, and does cancel a same-label hold — section (f) pins that. Every guard above is about a
credit-side leg that brought NO money in, which on a card in debt is still exactly the case.

Reuses tests/test_rta_balance_identity.py's isolated `env` fixture and row helpers.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning
from tests.test_rta_balance_identity import _expense, _transfer
from tests.test_rta_balance_identity import env as _isolated_env

# Re-exported under its own name so pytest resolves the fixture by the module-level binding
# (same reason as tests/test_rta_identity_property.py — see its note on F811).
env = _isolated_env

_SUBSCRIPTIONS = "cat_f2ff09940000"  # a REAL, labeled category linked to NO envelope
_MONTH = "2026-06"

_BASELINE_CASH = 1000.0
_BASELINE_RTA = 1000.0

_ACCOUNTS = [
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        "initial_balance": 1000.0,
        "initial_balance_date": "2026-06-01",
    },
    {
        "id": "cc",
        "name": "Citi Rewards",
        "kind": "liability",
        "type": "credit",
        "currency": "SGD",
        "initial_balance": -500.0,  # owes money: a leg landing here shrinks debt, brings no money
        "initial_balance_date": "2026-06-01",
    },
    {
        "id": "brokerage",
        "name": "Vantage Brokerage",
        "kind": "asset",
        "type": "investment",
        "currency": "SGD",
        "initial_balance": 0.0,
    },
]


@pytest.fixture
def book(env):
    """S$1,000 of cash, one live card, one off-budget brokerage, NO envelopes at all — so
    "Subscriptions" is a real labeled category linked to nothing and Ready to Assign is
    exactly the cash on hand."""
    categories_service.ensure_builtins_seeded()
    env.set_envelopes([])
    env.set_accounts(_ACCOUNTS)
    env.set_assignments({})
    env.set_txns([])
    return env


def _table(month=_MONTH):
    """`budget_month`, with the identity re-closed from its own published terms — necessary but
    NOT sufficient here: the identity closes tautologically on every one of these bugs (RTA is
    DEFINED as a function of `held_outside_envelopes`), which is exactly why each test below
    pins the HEADLINE against cash rather than trusting the closure."""
    table = planning.budget_month(month)
    assert table["ready_to_assign"] == pytest.approx(
        table["on_budget_cash"]
        + table["card_credit"]
        - table["pre_budget_baseline"]
        - table["held_in_envelopes"]
        - table["held_outside_envelopes"]
        - table["card_spend_not_covered"]
        - table["assigned_in_future_months"],
        abs=0.005,
    )
    return table


def _outside_rows(table) -> dict:
    """The "Outside envelopes" display rows for LABELED unlinked categories, keyed by category —
    the `__unbudgeted__` group `budget_month` derives from the SAME `_unbudgeted_keys` set the
    held term uses. A category missing from here is missing from the UI."""
    return {c["key"]: c for g in table["groups"] if g.get("unbudgeted") for c in g["categories"]}


def test_the_baseline_book_reads_exactly_as_expected(book):
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── (a) a categorized transfer OUT of a live card ───────────────────────────────────────────


def test_a_card_funded_categorized_transfer_holds_nothing_and_leaves_rta_unmoved(book):
    """S$200 moved from the CARD to an off-budget brokerage, labeled with an unlinked category.
    No cash moved — the card's debt grew — so there is nothing to hold and Ready to Assign must
    not move. Pre-fix the transfer fold credited the no-bucket bucket with −200, handing RTA a
    fabricated +200."""
    row = _transfer("2026-06-15", 200.0, "cc", "brokerage")
    row["category"] = _SUBSCRIPTIONS
    book.set_txns([row])
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH  # the card moved, cash did not
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA


def test_a_cash_funded_categorized_transfer_still_holds(book):
    """The other half of the same branch, so the gate is not simply switched off: the SAME
    transfer funded from cash really does move cash, so the unlinked category holds it."""
    row = _transfer("2026-06-15", 200.0, "bank", "brokerage")
    row["category"] = _SUBSCRIPTIONS
    book.set_txns([row])
    table = _table()
    assert table["on_budget_cash"] == 800.0
    assert table["held_outside_envelopes"] == -200.0
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── (b) an ARCHIVED card's spend ────────────────────────────────────────────────────────────


def _archive_the_card():
    """Archive the card the way the app does — through the account PATCH choke — then run the
    boot sync that retires an archived card's payment envelope and nulls its FK."""
    accounts_service.update_account("cc", {"archived": True})
    planning.sync_card_payment_envelopes()
    assert planning.svc.accounts.find_by_id("cc")["payment_envelope_id"] is None


def test_an_archived_cards_uncategorized_spend_holds_nothing_and_leaves_rta_unmoved(book):
    """S$100 swiped on a card that has since been archived. Its payment envelope is retired and
    its FK nulled, so the spend no longer takes the card branch — but it is still a CARD, and a
    card never had cash to lose. Pre-fix the Uncategorized bucket held −100 and RTA read 1,100."""
    book.set_txns(
        [
            {
                "kind": "expense",
                "date": "2026-06-15",
                "category": None,
                "from_account_id": "cc",
                "from_amount": 100.0,
                "from_currency": "SGD",
            }
        ]
    )
    _archive_the_card()
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA


def test_an_archived_cards_spend_reads_the_same_before_and_after_the_boot_sync(book):
    """The restart-dependence the classification bug carried: the SAME book must not read
    differently either side of the boot sync that retires the archived card's envelope."""
    book.set_txns([_expense("2026-06-15", _SUBSCRIPTIONS, 100.0, frm="cc")])
    accounts_service.update_account("cc", {"archived": True})
    before = _table()["ready_to_assign"]
    planning.sync_card_payment_envelopes()
    assert _table()["ready_to_assign"] == before == _BASELINE_RTA


# ── (c) account type is creation-only ───────────────────────────────────────────────────────


def test_retyping_an_account_is_refused(book):
    with pytest.raises(ValidationError, match="cannot be changed"):
        accounts_service.update_account("cc", {"type": "cash"})
    assert planning.svc.accounts.find_by_id("cc")["type"] == "credit"


# ── (d) a same-label CREDIT-side credit cancels the FULL fold, but not the cash one ─────────
#
# Each shape below pairs a real S$100 CASH expense on the unlinked category with a S$100
# same-month, same-label movement that lands ON a `credit` account — so the FULL fold nets to
# 0.00 while the cash fold still holds the whole −100. Pre-fix `_unbudgeted_keys` dropped the
# key on that 0.00, taking its held figure AND its display row with it, and RTA was drained by
# exactly the amount that should have been held.


def _cash_spend():
    return _expense("2026-06-15", _SUBSCRIPTIONS, 100.0, frm="bank")


def _assert_the_hold_survived_the_offset(table, *, activity: float):
    """The three (d) shapes differ only in HOW the credit-side leg arrives — the outcome they
    must all reach is identical: cash is down 100, the bucket holds the whole 100, RTA has not
    moved, and the category is still a visible row carrying that hold."""
    assert table["on_budget_cash"] == 900.0  # only the cash expense moved cash
    assert table["held_outside_envelopes"] == -100.0
    assert table["ready_to_assign"] == _BASELINE_RTA
    row = _outside_rows(table).get(_SUBSCRIPTIONS)
    assert row is not None, "the category was dropped from Outside envelopes entirely"
    assert row["available"] == -100.0  # the hold is ON the row, not just in the total
    assert row["activity"] == pytest.approx(activity, abs=0.005)


def test_a_transfer_into_an_archived_card_cannot_cancel_a_cash_holds_key(book):
    """A S$100 categorized transfer from an off-budget brokerage INTO an archived card, sharing
    the label of an unrelated S$100 cash expense. The card is on-budget by type but carries no
    payment envelope, so the transfer takes the categorized-transfer XOR branch and folds
    POSITIVE — netting the full map to 0.00 while no cash arrived anywhere."""
    _archive_the_card()
    offset = _transfer("2026-06-20", 100.0, "brokerage", "cc")
    offset["category"] = _SUBSCRIPTIONS
    book.set_txns([_cash_spend(), offset])
    _assert_the_hold_survived_the_offset(_table(), activity=0.0)


def test_an_investment_sell_settling_on_a_live_card_cannot_cancel_a_cash_holds_key(book):
    """The same cancellation via a categorized investment SELL settling onto a LIVE,
    payment-tracked card. The investment branch never consults `card_pay_env_of` at all — unlike
    the expense and transfer branches — so being payment-tracked does not route this leg away
    from the category the way it routes a card payment."""
    offset = {
        "kind": "investment",
        "date": "2026-06-20",
        "category": _SUBSCRIPTIONS,
        "to_account_id": "cc",
        "to_amount": 100.0,
        "to_currency": "SGD",
    }
    book.set_txns([_cash_spend(), offset])
    _assert_the_hold_survived_the_offset(_table(), activity=0.0)


def test_a_transfer_into_an_unarchived_card_with_no_envelope_yet_cannot_cancel_it_either(book):
    """The third door into the same gap, and the one that needs no archiving: `update_account`
    re-syncs payment envelopes on a TYPE flip only, so un-archiving a card leaves it live,
    on-budget and `payment_envelope_id`-less until the next boot. Same shape, same outcome."""
    _archive_the_card()
    accounts_service.update_account("cc", {"archived": False})
    assert planning.svc.accounts.find_by_id("cc")["payment_envelope_id"] is None
    offset = _transfer("2026-06-20", 100.0, "brokerage", "cc")
    offset["category"] = _SUBSCRIPTIONS
    book.set_txns([_cash_spend(), offset])
    _assert_the_hold_survived_the_offset(_table(), activity=0.0)


def test_a_partial_offset_was_never_broken_and_still_is_not(book):
    """The control the review used to localise (d) to the near-zero threshold: at S$50 the full
    fold reads −50, clears the filter on its own, and the key was never dropped. It must still
    hold the FULL cash hole — the offset never moved cash, so it never shrank the hold."""
    _archive_the_card()
    offset = _transfer("2026-06-20", 50.0, "brokerage", "cc")
    offset["category"] = _SUBSCRIPTIONS
    book.set_txns([_cash_spend(), offset])
    _assert_the_hold_survived_the_offset(_table(), activity=-50.0)


def test_a_transfer_paying_a_LIVE_card_never_reaches_the_category_at_all(book):
    """The other control: against a live, payment-tracked card the SAME transfer is a card
    PAYMENT, routed to the payment envelope before the XOR branch is reached, so it never
    touches this category's activity in either fold. Funded from cash, so cash really does move
    — and the hold on the unrelated expense stands untouched beside it."""
    offset = _transfer("2026-06-20", 100.0, "bank", "cc")
    offset["category"] = _SUBSCRIPTIONS
    book.set_txns([_cash_spend(), offset])
    table = _table()
    assert table["on_budget_cash"] == 800.0  # 100 spent + 100 paid to the card
    assert table["held_outside_envelopes"] == -100.0
    row = _outside_rows(table)[_SUBSCRIPTIONS]
    assert (row["activity"], row["available"]) == (-100.0, -100.0)


def test_a_category_with_neither_activity_nor_a_hold_still_mints_no_row(book):
    """The exclusion the widened filter must NOT lose: a key that nets to ~0 on BOTH folds is
    still dropped. Two same-label cash legs that cancel each other leave nothing to show and
    nothing to hold."""
    book.set_txns(
        [
            _cash_spend(),
            {
                "kind": "transfer",
                "date": "2026-06-20",
                "category": _SUBSCRIPTIONS,
                "from_account_id": "brokerage",
                "from_amount": 100.0,
                "from_currency": "SGD",
                "to_account_id": "bank",
                "to_amount": 100.0,
                "to_currency": "SGD",
            },
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH  # 100 out, 100 back in
    assert table["held_outside_envelopes"] == 0.0
    assert _outside_rows(table) == {}
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── (e) an inflow on an unlinked category cancels its own hold ──────────────────────────────


def _refund(date, amount, category=_SUBSCRIPTIONS, to="bank"):
    return {
        "kind": "income",
        "date": date,
        "category": category,
        "to_account_id": to,
        "to_amount": amount,
        "to_currency": "SGD",
    }


def test_a_refund_on_an_unlinked_category_cancels_the_hold_its_own_spend_opened(book):
    """S$100 spent on an unlinked category, S$100 refunded to the same label, same month. Cash
    is back where it started and nothing is outstanding, so the bucket must hold nothing and RTA
    must read exactly its pre-spend value. Pre-fix the inflow never entered the fold: the hold
    stayed at −100 AND the refund raised RTA through cash, reading 1,100 on 1,000 of cash."""
    book.set_txns([_cash_spend(), _refund("2026-06-20", 100.0)])
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA
    # Still LISTED — the spend is real and the user may still want to file it. It just holds
    # nothing now, exactly like a card-only spend does.
    row = _outside_rows(table)[_SUBSCRIPTIONS]
    assert (row["activity"], row["available"]) == (-100.0, 0.0)


def test_repeated_spend_refund_cycles_on_one_unlinked_category_do_not_compound(book):
    """The compounding form: four S$500 spend/refund pairs in one month. Every pair nets to zero
    cash, so RTA must not move at all. Pre-fix each pair added its own uncancelled −500 hold —
    RTA read 3,000 against 1,000 of cash."""
    rows = []
    for i in range(4):
        rows.append(_expense(f"2026-06-{5 + i:02d}", _SUBSCRIPTIONS, 500.0, frm="bank"))
        rows.append(_refund(f"2026-06-{6 + i:02d}", 500.0))
    book.set_txns(rows)
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA


def test_a_lone_inflow_on_an_unlinked_category_still_raises_rta_and_mints_no_row(book):
    """The control the `min(0, ·)` clamp exists for: a genuine reimbursement with NOTHING to
    offset is unassigned cash and must raise RTA by its full amount. It holds nothing (a bucket
    nothing can be assigned to never hoards a surplus) and mints no display row — the cash-only
    half of `_unbudgeted_keys`'s filter is deliberately one-sided so this stays true."""
    book.set_txns([_refund("2026-06-20", 100.0)])
    table = _table()
    assert table["on_budget_cash"] == 1100.0
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == 1100.0
    assert _outside_rows(table) == {}


def test_an_over_refund_cancels_the_hole_and_leaves_the_surplus_in_rta(book):
    """Between the two: S$100 spent, S$150 back. The hole closes and the extra S$50 is
    unassigned cash — held clamps at 0 rather than hoarding a surplus."""
    book.set_txns([_cash_spend(), _refund("2026-06-20", 150.0)])
    table = _table()
    assert table["on_budget_cash"] == 1050.0
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == 1050.0


def test_an_inflow_arriving_on_a_CARD_cannot_cancel_a_cash_hold(book):
    """The gate stays a cash gate on the inflow side too: a refund credited to the CARD restored
    no cash, so the cash hole is still open and still held."""
    book.set_txns([_cash_spend(), _refund("2026-06-20", 100.0, to="cc")])
    table = _table()
    assert table["on_budget_cash"] == 900.0
    assert table["held_outside_envelopes"] == -100.0
    assert table["ready_to_assign"] == _BASELINE_RTA


def test_uncategorized_income_does_not_cancel_the_unsorted_bucket(book):
    """The deliberate carve-out. Blank is an INFLOW category (`_INFLOW_CATEGORIES`), not a label
    a refund shares with a spend: salary landing uncategorized is inflow-to-assign, and netting
    it into the Uncategorized bucket would wipe that bucket's hold for every book that has any. The
    S$200 uncategorized spend must still hold, beside S$3,000 of uncategorized income."""
    book.set_txns(
        [
            _refund("2026-06-02", 3000.0, category=""),
            _expense("2026-06-15", None, 200.0, frm="bank"),
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == 3800.0
    assert table["held_outside_envelopes"] == -200.0
    assert table["ready_to_assign"] == 4000.0


def test_a_refund_on_a_LINKED_category_still_credits_its_envelope(book):
    """The half that must not change: on a category WITH an envelope the refund is still real
    Activity on that envelope's row, not a silent no-bucket cancellation."""
    envelope = envelopes_service.create_envelope("Subscriptions")
    envelopes_service.add_category(envelope["id"], _SUBSCRIPTIONS)
    book.set_txns([_cash_spend(), _refund("2026-06-20", 100.0)])
    table = _table()
    row = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if c.get("envelope_id") == envelope["id"]
    )
    assert row["activity"] == 0.0  # −100 spend + 100 refund, both on the envelope
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── (f) the hold reserves the MOVING LEG, at the rate the CASH side is counting ─────────────
#
# The 2026-09-10 differential fuzz round. (a)–(e) all asked "WHICH legs may the hold reserve
# against"; this one asks "HOW MUCH does it reserve". A transfer's stamped `base_amount` is
# written by `ledger._transfer_base_value` from WHICHEVER LEG HAPPENS TO BE IN BASE CURRENCY —
# not necessarily the leg that moved on-budget. Folding that magnitude into the cash-only
# accumulator reserved a different number than `_cash_as_of` counted, in either direction:
#
#   (f1) a cross-currency transfer whose ON-BUDGET leg is the foreign one: the stamp came from
#        the OTHER (base-currency) leg, at the BANK's rate.       US$1,000 → RTA +S$12
#   (f2) a same-currency transfer whose legs DIFFER (a wire fee eaten in transit): the stamp
#        came from the leg that left an OFF-BUDGET account.       S$10 of real cash drained
#   (f3) a stamp that predates a BASE-CURRENCY CHANGE, read raw. The historical-rate lock
#        (`core.fx.txn_base_amount`) trusts `base_amount` only while `base_currency` still
#        matches the current base; this branch read the field with no such check, so every
#        categorized transfer in the book kept quoting its old-base number as if it were the
#        new base.                                        S$100 read as US$100 → RTA +~US$26
#
# The stamped figure is still what the ENVELOPE sees (the historical-rate lock, ADR
# 2026-07-09 — an envelope's Activity is fixed on the day it was written); only the cash-only
# accumulator switches to the moving leg's own native amount at the CURRENT rate, which is
# exactly the arithmetic `_cash_as_of` does on that same account.

_BANK_USD = {
    "id": "bank_usd",
    "name": "Bank USD",
    "kind": "asset",
    "type": "cash",
    "currency": "USD",
    "initial_balance": 2000.0,
    "initial_balance_date": "2026-06-01",
}


def _stamped_transfer(date, frm, to, from_amount, to_amount, *, from_ccy, to_ccy, base_amount):
    """A categorized transfer carrying the write-time stamp `resolve_transaction_row` writes —
    `base_amount` from whichever leg was already in base currency (`_transfer_base_value`),
    `base_currency` from the base it was written under."""
    return {
        "kind": "transfer",
        "date": date,
        "category": _SUBSCRIPTIONS,
        "from_account_id": frm,
        "from_amount": from_amount,
        "from_currency": from_ccy,
        "to_account_id": to,
        "to_amount": to_amount,
        "to_currency": to_ccy,
        "base_amount": base_amount,
        "base_currency": "SGD",
    }


def test_a_cross_currency_transfer_holds_the_moving_leg_not_the_stamped_other_leg(book):
    """US$1,000 leaves an on-budget USD cash account and S$1,362 lands off-budget — the bank's
    own rate (1.362), not the book's (1.35). `_transfer_base_value` prefers the leg already in
    base, so the row is stamped 1,362: the ARRIVING, off-budget leg. The cash side, meanwhile,
    revalues what is LEFT in the USD account at the book's rate — it saw S$1,350 leave. Holding
    1,362 against a 1,350 hole handed Ready to Assign a fabricated S$12."""
    book.set_accounts([*_ACCOUNTS, _BANK_USD])
    book.set_txns(
        [
            _stamped_transfer(
                "2026-06-15",
                "bank_usd",
                "brokerage",
                1000.0,
                1362.0,
                from_ccy="USD",
                to_ccy="SGD",
                base_amount=1362.0,
            )
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == 2350.0  # 1,000 SGD + 1,000 USD × 1.35
    assert table["held_outside_envelopes"] == -1350.0  # NOT −1,362, the stamped other leg
    assert table["ready_to_assign"] == 3700.0  # NOT 3,712
    # The envelope-side figure is untouched: Activity stays locked to the stamp (ADR
    # 2026-07-09), so only the cash-only accumulator moved.
    assert _outside_rows(table)[_SUBSCRIPTIONS]["activity"] == -1362.0


def test_a_transfer_that_loses_a_fee_in_transit_holds_only_what_arrived(book):
    """No FX at all: S$1,000 leaves an off-budget account and S$990 arrives on-budget, a S$10
    wire fee eaten in between. Both legs are in base, so the stamp took the FROM leg — the
    off-budget one. Netting 1,000 of arrival against a 1,000 cash hole closed the hold
    completely, draining S$10 of Ready to Assign that is still sitting in the bank."""
    book.set_txns(
        [
            _expense("2026-06-10", _SUBSCRIPTIONS, 1000.0, frm="bank"),
            _stamped_transfer(
                "2026-06-20",
                "brokerage",
                "bank",
                1000.0,
                990.0,
                from_ccy="SGD",
                to_ccy="SGD",
                base_amount=1000.0,
            ),
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == 990.0  # 1,000 − 1,000 spent + 990 arrived
    assert table["held_outside_envelopes"] == -10.0  # NOT 0.00
    assert table["ready_to_assign"] == 1000.0  # NOT 990.00


def test_a_stamp_written_under_a_previous_base_currency_is_recomputed_not_read_raw(
    book, monkeypatch
):
    """The historical-rate lock's own precondition, missing from this branch: a switch of base
    currency clears the FX cache but deliberately does NOT re-stamp existing rows, so
    `base_amount` means nothing until `base_currency` is checked against the CURRENT base. The
    same S$100 transfer must hold S$100 under an SGD book and US$74.07 under a USD one — read
    raw it held "100" in both, inflating RTA by ~US$26 on this row alone."""
    row = _stamped_transfer(
        "2026-06-15",
        "bank",
        "brokerage",
        100.0,
        100.0,
        from_ccy="SGD",
        to_ccy="SGD",
        base_amount=100.0,
    )
    book.set_txns([row])
    assert _table()["held_outside_envelopes"] == -100.0
    assert _table()["ready_to_assign"] == _BASELINE_RTA

    monkeypatch.setattr(planning.svc, "base_currency", lambda: "USD")
    table = _table()
    assert table["on_budget_cash"] == pytest.approx(666.67, abs=0.005)  # 900 SGD ÷ 1.35
    assert table["held_outside_envelopes"] == pytest.approx(-74.07, abs=0.005)  # NOT −100
    assert table["ready_to_assign"] == pytest.approx(740.74, abs=0.01)  # NOT 766.67


# ── (f) the same inflows on a card IN CREDIT are money ──────────────────────────────────────
#
# ADR 2026-11-02-a-card-in-credit-is-budget-money: a card's balance above zero is budget money.
# Money landing on a card that owes nothing is new credit, so it cancels its label's hold like a
# cash refund would — and Ready to Assign moves by exactly zero, never by a fabricated amount.


def _card_in_credit(book, credit=200.0):
    book.set_accounts(
        [a if a["id"] != "cc" else {**a, "initial_balance": credit} for a in _ACCOUNTS]
    )


def test_a_refund_to_a_card_in_credit_is_money_and_cancels_the_hold(book):
    _card_in_credit(book)
    book.set_txns([_cash_spend()])
    before = _table()["ready_to_assign"]
    assert before == 1200.0  # 1,000 cash + 200 credit; the 100 spend is held

    book.set_txns([_cash_spend(), _refund("2026-06-20", 100.0, to="cc")])
    table = _table()
    assert table["card_credit"] == 300.0
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == before


def test_a_transfer_into_a_card_in_credit_is_money_and_cancels_the_hold(book):
    _card_in_credit(book)
    _archive_the_card()
    book.set_txns([_cash_spend()])
    before = _table()["ready_to_assign"]

    offset = _transfer("2026-06-20", 100.0, "brokerage", "cc")
    offset["category"] = _SUBSCRIPTIONS
    book.set_txns([_cash_spend(), offset])
    table = _table()
    assert table["card_credit"] == 300.0
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == before


def test_an_inflow_partly_paying_debt_cancels_only_the_part_that_lands_as_credit(book):
    """The card owes 500; a 550 refund pays the debt and leaves 50 of credit. Only that 50 is
    money, so the 100 hold shrinks to −50 and Ready to Assign moves by nothing."""
    book.set_txns([_cash_spend()])
    before = _table()["ready_to_assign"]

    book.set_txns([_cash_spend(), _refund("2026-06-20", 550.0, to="cc")])
    table = _table()
    assert table["card_credit"] == 50.0
    assert table["held_outside_envelopes"] == -50.0
    assert table["ready_to_assign"] == before
