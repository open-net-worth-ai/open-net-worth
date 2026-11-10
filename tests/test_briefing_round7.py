"""Daily briefing fixes from persona QA round 7 (2026-10-14), synthetic data.

* It stayed stale after an EDIT: a transaction labelled before the Dashboard opened was still on
  the note's to-do list. The activity fingerprint now carries how many transactions are still
  uncategorised, and the context states the count (or that there are none).
* It was wrong about a FUNDED card: the card's payment envelope held the full balance, yet the
  note said nothing was free to absorb it and the Needs-attention liabilities flag said "monitor
  repayment". The context now carries each card's payment-envelope coverage, and the liabilities
  flag (``portfolio_analytics`` attention) says when the debt is covered.
"""

from __future__ import annotations

import pytest

from backend.services import briefing_book_state as book_state
from backend.services import briefing_context as facts
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services import portfolio_analytics as analytics
from tests.test_credit_card_payment_envelope import (
    _DEFAULT_ACCOUNTS,
    _sync_and_card_env_id,
)
from tests.test_credit_card_payment_envelope import env as _card_book

env = _card_book  # the payment-envelope book fixture, under the name its tests use

# ── an edit makes the note stale ──────────────────────────────────────────────


def _label(txn: dict) -> None:
    category = categories_service.create_category(label="Transfers in", kind="income")
    ledger_service.update_transaction(txn["id"], {"category": category["id"]})


def _unsorted(description: str = "PayNow from Sam") -> dict:
    return ledger_service.create_transaction(
        {
            "date": "2026-10-10",
            "kind": "income",
            "description": description,
            "to_account_id": "Current",
            "to_amount": 40.0,
            "to_currency": "SGD",
        }
    )


@pytest.fixture
def book(db_isolated):
    from backend.services import accounts as accounts_service

    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 500.0}
    )


def test_labelling_a_transaction_changes_the_activity_fingerprint(book):
    txn = _unsorted()
    before = book_state.activity_fingerprint("SGD")

    _label(txn)

    assert book_state.activity_fingerprint("SGD") != before


def test_the_context_names_what_is_unsorted_and_then_says_nothing_is(book):
    txn = _unsorted()
    lines = book_state.uncategorized_lines("SGD")
    assert lines[0].startswith("  Uncategorised: 1 transaction (S$40)")
    assert "PayNow from Sam" in lines[0]

    _label(txn)

    assert (
        "do NOT tell the client to label or sort anything"
        in book_state.uncategorized_lines("SGD")[0]
    )


# ── a funded card is covered ──────────────────────────────────────────────────


def _funded_card(env, held: float) -> str:
    env.set_profile(credit_card_mode="payment_envelope")
    env.set_accounts(
        [
            {**_DEFAULT_ACCOUNTS[0], "initial_balance": 2000.0},
            {**_DEFAULT_ACCOUNTS[1]},
            {**_DEFAULT_ACCOUNTS[2], "initial_balance": -392.0},
        ]
    )
    env_id = _sync_and_card_env_id()
    planning.assignments_store.set_assigned("2026-10", env_id, held)
    return env_id


def test_a_funded_card_reads_covered_in_the_budgets_own_rows(env):
    _funded_card(env, 392.0)

    (card,) = planning.card_payment_coverage("2026-10")

    assert (card["owed"], card["held"], card["underfunded"]) == (392.0, 392.0, 0.0)


def test_the_briefing_says_a_funded_card_is_covered_not_a_risk(env, monkeypatch):
    _funded_card(env, 392.0)
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "zero_based")

    lines = facts.cash_availability_lines("2026-10", "SGD", card_owed=392.0)
    text = "\n".join(lines)

    assert "owes S$392, its payment envelope holds S$392 (fully covered)" in text
    assert "say the cards are covered" in text
    assert "say nothing is free to absorb it" in text  # as a thing NOT to say
    assert "cash can take on" not in text


def test_a_half_funded_card_states_the_part_still_uncovered(env, monkeypatch):
    _funded_card(env, 100.0)
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "zero_based")

    text = "\n".join(facts.cash_availability_lines("2026-10", "SGD", card_owed=392.0))

    assert "short by S$292" in text
    assert "of which S$292 is not yet set aside in a payment envelope" in text


def test_the_briefing_flags_a_card_bill_due_soon_not_a_card_balance(monkeypatch):
    """Owner decision (2026-10-16): the briefing carries the Dashboard's flags, and a card
    balance alone is no longer one — a statement due within a week or overdue is."""
    monkeypatch.setattr(
        "backend.services.portfolio_analytics.portfolio_analytics",
        lambda base: {"attention": {"debt_total": -392.0, "debt_covered": False}},
    )
    monkeypatch.setattr(book_state.commitments_service, "coverage", lambda base: {"at_risk": False})
    due = [
        {
            "account_id": "m",
            "label": "Master",
            "amount_base": 300.0,
            "estimated": False,
            "days_until_due": -2,
        },
        {
            "account_id": "v",
            "label": "Visa",
            "amount_base": 640.0,
            "estimated": True,
            "days_until_due": 3,
        },
    ]
    monkeypatch.setattr(book_state.upcoming_bills, "cards_due_soon", lambda *a, **k: due)

    texts = [f["text"] for f in book_state.attention_flags("SGD")]

    assert texts == [
        "Card bill: Master, S$300 still owed on the last statement, overdue by 2 days.",
        "Card bill: Visa, S$640 still owed on the last statement, due in 3 days (estimated "
        "from the card's statement cycle).",
    ]
    assert not any("Outstanding liabilities" in t for t in texts)


def test_dashboard_attention_marks_debt_covered_only_when_every_dollar_is(monkeypatch):
    rows = [{"asset_class": "credit", "base_value": -392.0, "label": "Visa", "account_id": "v"}]
    monkeypatch.setattr(
        planning,
        "card_payment_coverage",
        lambda month, **_: [{"owed": 392.0, "held": 392.0, "underfunded": 0.0}],
    )
    att = analytics._attention(rows)
    assert att["debt_covered"] is True and att["debt_covered_total"] == 392.0

    monkeypatch.setattr(
        planning,
        "card_payment_coverage",
        lambda month, **_: [{"owed": 392.0, "held": 100.0, "underfunded": 292.0}],
    )
    att = analytics._attention(rows)
    assert att["debt_covered"] is False and att["debt_covered_total"] == 100.0

    assert analytics._attention([])["debt_covered"] is False
