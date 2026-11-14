"""Bank data sync — card payments book as transfers, Review teaches the payee, a run can be
undone (ADR 2026-11-13, Amendment 13). Real runner, connector_fetch and ledger chokes; only the
bank is fake (``tests/bank_sync_fakes.py``). Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import runner, settings
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

pytestmark = pytest.mark.flags(bank_data_sync=True)


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    settings.save("gocardless", mode="data")
    return fake


def _card() -> str:
    return accounts_service.create_account(
        {
            "name": "ACME Card",
            "kind": "liability",
            "type": "credit",
            "currency": "GBP",
            "initial_balance": -500.00,
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


def _typed(kind: str, **fields) -> dict:
    return ledger_service.create_transaction(
        {"kind": kind, "date": days_ago(1), "description": "typed by hand", **fields}
    )


# ── 1. card payments are transfers ──────────────────────────────────────────────────────────


def test_a_card_payment_read_from_both_sides_is_one_transfer(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    bank.booked[ACC_MAIN] = [txn(-300.00, days_ago(2), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(300.00, days_ago(1), "Payment Thank You-Mobile", tid="i1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1
    assert _live("income") == [] and _live("expense") == []
    (transfer,) = _live("transfer")
    assert (transfer["from_account_id"], transfer["to_account_id"]) == (main, card)
    assert transfer["from_amount"] == transfer["to_amount"] == 300.00
    assert {e["ref"] for e in transfer["external_refs"]} >= {"o1", "i1"}
    assert transfer["needs_review"] is True
    again = _run()  # both refs are known: nothing books twice
    assert again["counts"]["booked"] == 0 and len(_live()) == 1


def test_a_card_payment_meets_a_row_typed_by_hand(bank):
    main, card = linked_account("ACME Current", ACC_MAIN), _card()
    typed = _typed("expense", from_account_id=main, from_amount=120.0, from_currency="GBP")
    bank.booked[ACC_SAVER] = [txn(120.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1 and result["counts"]["booked"] == 0
    (transfer,) = _live()
    assert transfer["id"] == typed["id"] and transfer["kind"] == "transfer"
    assert transfer["to_account_id"] == card


def test_the_paying_side_arriving_later_pairs_with_the_card_income(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_SAVER] = [txn(75.00, days_ago(2), "ACME PAYMENT THANK YOU", tid="i1")]
    _run()
    assert [t["kind"] for t in _live()] == ["income"]  # no counterpart yet: what the bank says
    bank.booked[ACC_MAIN] = [txn(-75.00, days_ago(2), "ACME CREDIT CARD PAYMENT", tid="o1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1
    (transfer,) = _live()
    assert transfer["kind"] == "transfer" and transfer["from_account_id"] == main


def test_a_merchant_refund_is_not_a_card_payment(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(1), "ACME ELECTRIC BILL PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(40.00, days_ago(1), "ACME SHOP REFUND", tid="i1")]

    _run()

    assert sorted(t["kind"] for t in _live()) == ["expense", "income"]


def test_an_ambiguous_match_pairs_nothing(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [
        txn(-60.00, days_ago(2), "ACME CARD PAYMENT", tid="o1"),
        txn(-60.00, days_ago(1), "ACME CARD PAYMENT", tid="o2"),
    ]
    bank.booked[ACC_SAVER] = [txn(60.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert _live("transfer") == [] and len(_live()) == 3


def test_a_payment_further_than_the_window_is_not_paired(bank):
    linked_account("ACME Current", ACC_MAIN)
    _card()
    bank.booked[ACC_MAIN] = [txn(-60.00, days_ago(9), "ACME CARD PAYMENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(60.00, days_ago(1), "ACME PAYMENT THANK YOU", tid="i1")]

    _run()

    assert _live("transfer") == []


# ── 2. Review teaches the payee ─────────────────────────────────────────────────────────────


def _category(name: str) -> str:
    from backend.services import categories as categories_service

    return categories_service.create_category(name, "expense")["id"]


def _booked(text: str) -> dict:
    return next(t for t in _live("expense") if t["description"] == text)


def test_categorising_a_synced_row_in_review_teaches_its_payee(bank):
    linked_account("ACME Current", ACC_MAIN)
    groceries = _category("Groceries")
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(3), "ACME GROCER", tid="t1")]
    _run()
    first = _booked("ACME GROCER")
    assert first["category"] is None and first["needs_review"] is True

    ledger_service.update_transaction(first["id"], {"category": groceries, "needs_review": False})
    bank.booked[ACC_MAIN].append(txn(-22.00, days_ago(1), "ACME GROCER", tid="t2"))
    _run()

    second = next(t for t in _live("expense") if t["id"] != first["id"])
    assert second["category"] == groceries
    assert second["payee_id"] == first["payee_id"]


def test_review_never_overwrites_a_payees_existing_default(bank):
    from backend.services import payee_registry

    linked_account("ACME Current", ACC_MAIN)
    groceries, dining = _category("Groceries"), _category("Dining")
    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(3), "ACME GROCER", tid="t1")]
    _run()
    row = _booked("ACME GROCER")
    ledger_service.update_transaction(row["id"], {"category": groceries})

    ledger_service.update_transaction(row["id"], {"category": dining})

    assert payee_registry.default_category_for(row["payee_id"], "expense") == groceries
