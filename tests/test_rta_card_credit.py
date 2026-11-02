"""A card holding credit is budget money in Ready to Assign, as in YNAB (ADR
docs/adr/2026-11-02-a-card-in-credit-is-budget-money.md).

Each case below is a YNAB behaviour observed on a test plan on 2026-11-02, scaled down:

* overpaying a card leaves the overpaid amount in the budget as card credit — the payment
  envelope still goes negative and is charged at the roll, and the credit offsets that charge;
* a spend on a card in credit is paid from the credit first — the spending envelope drops, the
  payment envelope does not move, Ready to Assign does not move;
* a refund to a card in credit adds to the credit — the payment envelope does not move;
* a spend larger than the free credit moves only the part that becomes debt into the payment
  envelope, after first filling this month's negative payment envelope, so the envelope ends
  holding exactly the new debt (YNAB: credit 1,375, payment category −100, a 1,400 spend → the
  category reads +25 and Ready to Assign drops 100).

Bank opens with 1,000 SGD, Amex at 0, floor 2026-06 (the shared ``env`` fixture).
"""

from __future__ import annotations

from backend.services import planning
from tests.test_credit_card_payment_envelope import (
    _cat,
    _env,
    _expense,
    _row_by_env,
    _sync_and_card_env_id,
    _transfer,
)
from tests.test_credit_card_payment_envelope import env as _card_env

# Bound under its own name, as the property tests do: pytest finds a fixture by its module-level
# name, and a bare imported `env` reads to ruff as shadowed by every test's parameter (F811).
env = _card_env

FOOD = "cat_13095ba70000"
TRANSPORT = "cat_db5642ab0000"


def _assign(month, key, amount):
    planning.apply_budget_change(
        {"type": "assign", "month": month, "envelope_id": _env(key), "amount": amount}
    )


def _card_refund(date, key, amount):
    return {
        "kind": "income",
        "date": date,
        "category": key,
        "to_account_id": "amex",
        "to_amount": amount,
        "to_currency": "SGD",
        "base_amount": amount,
    }


def _closes(table):
    """The identity with the card-credit term: nothing is left over."""
    return round(
        table["ready_to_assign"]
        + table["assigned_in_future_months"]
        + table["held_in_envelopes"]
        + table["held_outside_envelopes"]
        + table["card_spend_not_covered"]
        + table["pre_budget_baseline"]
        - table["on_budget_cash"]
        - table["card_credit"],
        2,
    )


def test_an_overpaid_card_keeps_the_money_in_the_budget(env):
    """June: 150 paid to a card that owes nothing. The payment envelope holds −150 in-month and
    is charged at the roll, as before — but the 150 now sits on the card as credit, which is
    budget money, so July is back at 1,000 instead of losing 150 for good."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    env.set_txns([_transfer("2026-06-20", "bank", "amex", 150)])

    june = planning.budget_month("2026-06")
    assert _row_by_env(june, card_env_id)["available"] == -150.0
    assert june["on_budget_cash"] == 850.0
    assert june["card_credit"] == 150.0
    assert june["ready_to_assign"] == 1150.0  # the −150 is held in-month, as YNAB shows it
    assert _closes(june) == 0

    july = planning.budget_month("2026-07")
    assert _row_by_env(july, card_env_id)["available"] == 0.0
    assert july["card_credit"] == 150.0
    assert july["ready_to_assign"] == 1000.0  # was 850: the overpayment was lost
    assert _closes(july) == 0


def test_a_spend_on_a_card_in_credit_is_paid_from_the_credit(env):
    """YNAB probe 1: Groceries −30, payment category unchanged, card credit −30, RTA unchanged."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    _assign("2026-07", FOOD, 100)
    base = [_transfer("2026-06-20", "bank", "amex", 150)]
    env.set_txns(base)
    before = planning.budget_month("2026-07")

    env.set_txns([*base, _expense("2026-07-10", FOOD, 30, from_account_id="amex")])
    july = planning.budget_month("2026-07")
    assert _cat(july, FOOD)["available"] == 70.0
    assert _row_by_env(july, card_env_id)["available"] == 0.0  # nothing moved: no debt
    assert july["card_credit"] == 120.0
    assert july["card_spend_not_covered"] == 0.0
    assert july["ready_to_assign"] == before["ready_to_assign"]
    assert _closes(july) == 0


def test_a_refund_to_a_card_in_credit_adds_to_the_credit(env):
    """YNAB probe 2: Groceries +30, payment category unchanged, card credit +30, RTA unchanged."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    _assign("2026-07", FOOD, 100)
    base = [_transfer("2026-06-20", "bank", "amex", 150)]
    env.set_txns(base)
    before = planning.budget_month("2026-07")

    env.set_txns([*base, _card_refund("2026-07-10", FOOD, 30)])
    july = planning.budget_month("2026-07")
    assert _cat(july, FOOD)["available"] == 130.0
    assert _row_by_env(july, card_env_id)["available"] == 0.0  # no debt shrank
    assert july["card_credit"] == 180.0
    assert july["ready_to_assign"] == before["ready_to_assign"]
    assert _closes(july) == 0


def test_a_spend_beyond_the_free_credit_funds_only_the_new_debt(env):
    """YNAB probe 3, scaled. July opens with 150 of credit, then 100 more is paid (payment
    envelope −100, credit 250). A 300 Transport spend, assigned 400: 150 of credit is free
    (250 less the −100 not yet charged), so 150 is paid from credit and 150 becomes funded debt;
    the payment envelope goes −100 → +50 = the card's new debt, and RTA drops by the 100 that the
    negative envelope was holding up — exactly what YNAB did (−100 → +25, RTA −100)."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    _assign("2026-07", TRANSPORT, 400)
    base = [
        _transfer("2026-06-20", "bank", "amex", 150),
        _transfer("2026-07-02", "bank", "amex", 100),
    ]
    env.set_txns(base)
    before = planning.budget_month("2026-07")
    assert _row_by_env(before, card_env_id)["available"] == -100.0
    assert before["card_credit"] == 250.0
    assert before["ready_to_assign"] == 700.0

    env.set_txns([*base, _expense("2026-07-10", TRANSPORT, 300, from_account_id="amex")])
    july = planning.budget_month("2026-07")
    assert _cat(july, TRANSPORT)["available"] == 100.0
    assert _row_by_env(july, card_env_id)["available"] == 50.0  # = the card's debt
    assert july["card_credit"] == 0.0
    assert july["card_spend_not_covered"] == 0.0
    assert july["ready_to_assign"] == 600.0
    assert _closes(july) == 0


def test_an_uncategorized_spend_from_card_credit_holds_like_cash(env):
    """Credit is money, so an uncategorized spend paid from it holds until the roll exactly like
    an uncategorized cash spend: RTA does not move this month and drops by it next month."""
    env.set_profile(credit_card_mode="payment_envelope")
    _sync_and_card_env_id()
    base = [_transfer("2026-06-20", "bank", "amex", 150)]
    env.set_txns(base)
    july_before = planning.budget_month("2026-07")["ready_to_assign"]

    env.set_txns([*base, _expense("2026-07-10", None, 40, from_account_id="amex")])
    july = planning.budget_month("2026-07")
    assert july["held_outside_envelopes"] == -40.0
    assert july["ready_to_assign"] == july_before
    assert _closes(july) == 0
    assert planning.budget_month("2026-08")["ready_to_assign"] == july_before - 40


def test_a_card_that_owes_money_works_as_before(env):
    """No credit anywhere: a funded card spend moves its whole amount into the payment envelope
    and Ready to Assign does not move; card credit stays 0."""
    env.set_profile(credit_card_mode="payment_envelope")
    card_env_id = _sync_and_card_env_id()
    _assign("2026-06", FOOD, 200)
    env.set_txns([_expense("2026-06-10", FOOD, 120, from_account_id="amex")])
    june = planning.budget_month("2026-06")
    assert _cat(june, FOOD)["available"] == 80.0
    assert _row_by_env(june, card_env_id)["available"] == 120.0
    assert june["card_credit"] == 0.0
    assert june["ready_to_assign"] == 800.0
    assert _closes(june) == 0
