"""Bank data sync — a card payment pairs once its pending leg settles, a pair the wording doesn't
prove is asked about, and a card's available credit is never its balance (ADR 2026-11-13,
Amendment 19). Real runner, connector_fetch and ledger chokes; only the bank is fake
(``tests/bank_sync_fakes.py``). Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services import match_review
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import balances, review_actions, reviews, runner, settings
from backend.services.bank_data_sync import status as sync_status
from tests.bank_sync_fakes import (
    ACC_MAIN,
    ACC_SAVER,
    FakeBank,
    configure_gocardless,
    days_ago,
    linked_account,
    txn,
)

GC = PROVIDERS["gocardless"]

# Its accounts hold foreign currencies: seed synthetic rates, never a live FX fetch.
pytestmark = [pytest.mark.flags(bank_data_sync=True), pytest.mark.usefixtures("fx_rates")]


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    settings.save("gocardless", mode="data")
    return fake


def _card(limit: float | None = None) -> str:
    return accounts_service.create_account(
        {
            "name": "ACME Card",
            "kind": "liability",
            "type": "credit",
            "currency": "GBP",
            "initial_balance": -500.00,
            "credit_limit": limit,
            "external_ids": [{"source": "gocardless", "ref": ACC_SAVER}],
        }
    )["id"]


def _run() -> dict:
    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(kind: str | None = None) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and t["id"] not in opening and (kind is None or t["kind"] == kind)
    ]


# ── a leg that was pending pairs when it settles ─────────────────────────────────────────────


def test_a_card_side_that_was_pending_pairs_when_it_settles(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    bank.booked[ACC_MAIN] = [txn(-312.40, days_ago(3), "DIRECT DEBIT ACME CARD", tid="o1")]
    bank.pending[ACC_SAVER] = [txn(312.40, days_ago(2), "DIRECT DEBIT PAYMENT")]
    _run()
    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]  # still pending: apart

    bank.pending[ACC_SAVER] = []
    bank.booked[ACC_SAVER] = [txn(312.40, days_ago(2), "DIRECT DEBIT PAYMENT", tid="i1")]
    result = _run()

    (transfer,) = _live()
    assert transfer["kind"] == "transfer"
    assert (transfer["from_account_id"], transfer["to_account_id"]) == (main, card)
    assert transfer["from_amount"] == transfer["to_amount"] == 312.40
    assert result["counts"]["card_payments"] == 1


def test_both_legs_pending_pair_when_they_settle_in_one_run(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.pending[ACC_MAIN] = [txn(-80.00, days_ago(2), "ACME CARD PAYMENT")]
    bank.pending[ACC_SAVER] = [txn(80.00, days_ago(2), "PAYMENT RECEIVED")]
    _run()
    bank.pending = {ACC_MAIN: [], ACC_SAVER: []}
    bank.booked[ACC_MAIN] = [txn(-80.00, days_ago(2), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(80.00, days_ago(1), "PAYMENT RECEIVED", tid="i1")]

    _run()

    assert [t["kind"] for t in _live()] == ["transfer"]


def test_undo_splits_a_transfer_the_run_made_from_two_booked_rows(bank):
    from backend.services.bank_data_sync import undo

    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-45.00, days_ago(3), "ACME CARD PAYMENT", tid="o1")]
    bank.pending[ACC_SAVER] = [txn(45.00, days_ago(2), "PAYMENT RECEIVED")]
    _run()
    bank.pending[ACC_SAVER] = []
    bank.booked[ACC_SAVER] = [txn(45.00, days_ago(2), "PAYMENT RECEIVED", tid="i1")]
    second = _run()
    assert [t["kind"] for t in _live()] == ["transfer"]

    undo.undo_run("gocardless", second["run_id"])

    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]


def test_a_payment_landing_four_days_later_still_pairs(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-60.00, days_ago(6), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(60.00, days_ago(2), "PAYMENT RECEIVED", tid="i1")]

    _run()

    assert [t["kind"] for t in _live()] == ["transfer"]


# ── a pair the wording doesn't prove is asked about ──────────────────────────────────────────


def _unworded(bank) -> None:
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-210.00, days_ago(3), "ACME BANK DD 1234", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(210.00, days_ago(2), "ACME 9876 CR", tid="i1")]


def test_a_pair_without_payment_wording_is_asked_about_not_paired(bank):
    _unworded(bank)

    _run()

    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]
    (item,) = reviews.open_items("gocardless")
    assert item["kind"] == "transfer_pair"
    assert item["detail"]["current"]["amount"] == 210.00
    assert item["detail"]["other"]["amount"] == -210.00


def test_accepting_the_pair_makes_one_transfer(bank):
    _unworded(bank)
    _run()
    (item,) = reviews.open_items("gocardless")

    review_actions.accept(item["id"])

    assert [t["kind"] for t in _live()] == ["transfer"]
    assert reviews.open_items("gocardless") == []


def test_a_dismissed_pair_is_not_asked_again(bank):
    _unworded(bank)
    _run()
    (item,) = reviews.open_items("gocardless")
    review_actions.dismiss(item["id"])
    bank.booked[ACC_MAIN].append(txn(-5.00, days_ago(1), "ACME COFFEE", tid="o2"))

    _run()

    assert reviews.open_items("gocardless") == []
    assert sorted(t["kind"] for t in _live()) == ["expense", "expense", "income"]


def test_a_refund_on_the_card_is_never_offered_as_a_transfer(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-30.00, days_ago(2), "ACME SHOP", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(30.00, days_ago(1), "ACME SHOP REFUND", tid="i1")]

    _run()

    assert reviews.open_items("gocardless") == []


# ── "Make transfer" on two rows one sync booked ──────────────────────────────────────────────


def test_two_rows_from_one_sync_run_can_be_made_one_transfer(bank):
    _unworded(bank)
    _run()
    income, expense = _live("income")[0], _live("expense")[0]
    assert income["batch_id"] == expense["batch_id"]  # one run, one batch, one connection

    merged, _info = match_review.merge_transactions_preserving_fields(
        expense["id"], income["id"], reason="make transfer", become_transfer=True
    )

    assert merged["kind"] == "transfer" and [t["kind"] for t in _live()] == ["transfer"]


def test_two_rows_of_one_batch_still_never_merge_as_duplicates(bank):
    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(2), "ACME COFFEE", tid="o1"),
        txn(-5.00, days_ago(2), "ACME COFFEE", tid="o2"),
    ]
    _run()
    a, b = _live("expense")

    with pytest.raises(ValidationError):
        ledger_service.merge_transactions(a["id"], b["id"])


# ── a card's available credit is never its balance ───────────────────────────────────────────


def _only_available(bank) -> None:
    # What a bank that reports a card's credit left (interimAvailable) and nothing booked sends.
    bank.balances[ACC_SAVER] = {"balance": 1200.00, "available_to_spend": 1200.00}


def _balance(account_id: str) -> float:
    account = ledger_service.accounts.find_by_id(account_id)
    txns = ledger_service.transactions.all()
    return ledger_service.account_balance(account, txns, ledger_service.opening_date(account, txns))


def _card_row() -> dict:
    (row,) = [a for a in sync_status("gocardless")["accounts"] if a["name"] == "ACME Card"]
    return row


def test_with_a_credit_limit_the_card_owes_the_limit_less_its_available_credit(bank):
    card = _card(limit=5000.0)
    _only_available(bank)

    _run()

    balance = _balance(card)
    assert round(balance, 2) == -3800.00
    assert _card_row()["balance_state"] == "match"


def test_without_a_credit_limit_the_card_balance_is_left_alone_and_asks_for_one(bank):
    card = _card()
    _only_available(bank)

    _run()

    balance = _balance(card)
    assert balance == -500.00  # never set to -1,200
    row = _card_row()
    assert row["balance_state"] == "needs_limit" and row["bank_balance"] is None
    with pytest.raises(ValidationError, match="credit limit"):
        balances.use_bank(card, balances.stored("gocardless")[card])


def test_a_cards_booked_balance_is_still_what_it_owes(bank):
    card = _card(limit=5000.0)
    bank.balances[ACC_SAVER] = {
        "balance": 3800.00,
        "cleared_balance": 3800.00,
        "available_to_spend": 1200.00,
    }

    _run()

    balance = _balance(card)
    assert round(balance, 2) == -3800.00


def test_a_card_in_credit_reads_as_money_held(bank):
    card = ledger_service.accounts.find_by_id(_card(limit=1000.0))
    target, _settled, why = balances.bank_figure(
        card, {"amount": 1050.0, "available": 1050.0, "cleared": None, "working": None}
    )
    assert target == 50.0 and why is None


def test_a_cash_accounts_one_figure_is_unchanged(bank):
    main = ledger_service.accounts.find_by_id(linked_account("ACME Current", ACC_MAIN))
    target, _settled, why = balances.bank_figure(
        main, {"amount": 990.0, "available": 990.0, "cleared": None, "working": None}
    )
    assert target == 990.0 and why is None
