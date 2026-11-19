"""Bank data sync — accuracy (ADR 2026-11-13, Amendment 17): a bank row that is the one row you
typed for it is matched with no question, and money moved between two of your own cash
accounts is one transfer. Synthetic ACME data only."""

from __future__ import annotations

import pytest

from backend.services import bank_data_sync
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


def _run() -> dict:
    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(account_id: str) -> list[dict]:
    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def _typed(account: str, amount: float, on: str, text: str = "ACME BOOKS") -> dict:
    return ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": on,
            "description": text,
            "from_account_id": account,
            "from_amount": amount,
            "from_currency": "GBP",
            "source": "manual",
        }
    )


# ── A row you typed, matched with no question when nothing else could be it ──────────────────


def test_the_one_row_you_typed_is_matched_not_asked_about(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    typed = _typed(main, 18.00, days_ago(4))
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS LTD", tid="d1")]

    result = _run()

    assert result["counts"]["booked"] == 0
    assert result["counts"]["claimed"] == 1
    assert bank_data_sync.list_reviews("gocardless") == []
    [row] = _live(main)
    assert row["id"] == typed["id"] and row["description"] == "ACME BOOKS"
    assert row["cleared"] is True
    assert {e["ref"] for e in row["external_refs"]} == {f"{ACC_MAIN}:d1"}
    assert _run()["counts"]["booked"] == 0


def test_two_rows_you_typed_alike_are_asked_about(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _typed(main, 2.40, days_ago(3), "ACME COFFEE")
    _typed(main, 2.40, days_ago(3), "ACME COFFEE")
    bank.booked[ACC_MAIN] = [txn(-2.40, days_ago(3), "ACME COFFEE", tid="c1")]

    result = _run()

    assert result["counts"]["claimed"] == 0
    assert [r["kind"] for r in bank_data_sync.list_reviews("gocardless")] == ["duplicate"]


def test_two_bank_rows_wanting_one_typed_row_are_asked_about(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    _typed(main, 2.40, days_ago(3), "ACME COFFEE")
    bank.booked[ACC_MAIN] = [
        txn(-2.40, days_ago(3), "ACME COFFEE", tid="c1"),
        txn(-2.40, days_ago(3), "ACME COFFEE", tid="c2"),
    ]

    result = _run()

    assert result["counts"]["claimed"] == 0


def test_a_row_another_source_booked_is_never_taken_for_a_typed_one(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(4),
            "description": "ACME BOOKS",
            "from_account_id": main,
            "from_amount": 18.00,
            "from_currency": "GBP",
            "source": "agent",
            "external_refs": [{"source": "email", "ref": "acme-receipt-1"}],
        }
    )
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS", tid="d1")]

    result = _run()

    assert result["counts"]["claimed"] == 0
    assert [r["kind"] for r in bank_data_sync.list_reviews("gocardless")] == ["duplicate"]


# ── Money between two of your own cash accounts is one transfer ─────────────────────────────


def test_a_move_between_two_cash_accounts_is_one_transfer(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_MAIN] = [txn(-200.00, days_ago(2), "TRANSFER TO ACME SAVER", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(200.00, days_ago(1), "FROM ACME CURRENT", tid="i1")]

    result = _run()

    assert result["counts"]["card_payments"] == 1
    [row] = _live(main)
    assert row["kind"] == "transfer"
    assert (row["from_account_id"], row["to_account_id"]) == (main, saver)
    assert _live(saver) == [row]


def test_the_same_amount_without_transfer_wording_stays_two_rows(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_MAIN] = [txn(-200.00, days_ago(2), "ACME RENT", tid="o1")]
    bank.booked[ACC_SAVER] = [txn(200.00, days_ago(1), "ACME PAYROLL", tid="i1")]

    _run()

    assert [t["kind"] for t in _live(main)] == ["expense"]
    assert [t["kind"] for t in _live(saver)] == ["income"]


def test_a_move_that_could_be_two_pairs_pairs_with_nothing(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_MAIN] = [
        txn(-50.00, days_ago(2), "TRANSFER TO ACME SAVER", tid="o1"),
        txn(-50.00, days_ago(1), "TRANSFER TO ACME SAVER", tid="o2"),
    ]
    bank.booked[ACC_SAVER] = [txn(50.00, days_ago(1), "FROM ACME CURRENT", tid="i1")]

    _run()

    assert all(t["kind"] != "transfer" for t in _live(main) + _live(saver))
