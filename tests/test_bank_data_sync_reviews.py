"""Bank data sync — what the bank changes after booking waits for the user (ADR 2026-11-13).

A changed or vanished row, a pending row that posted at another amount, and a row the duplicate
guard matched all land in the review list; the ledger is unchanged until an item is accepted."""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError
from backend.services import bank_data_sync
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import runner, settings
from tests.bank_sync_fakes import (
    ACC_MAIN,
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


def test_a_changed_amount_waits_for_review_and_applies_only_on_accept(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-25.00, days_ago(5), "ACME HARDWARE", tid="h1")]
    _run()
    bank.booked[ACC_MAIN] = [txn(-27.50, days_ago(5), "ACME HARDWARE", tid="h1")]

    result = _run()

    assert result["counts"]["to_review"] == 1
    [item] = bank_data_sync.list_reviews("gocardless")
    assert item["kind"] == "modified"
    [row] = _live(main)
    assert row["from_amount"] == pytest.approx(25.00)  # untouched until accepted

    bank_data_sync.accept_review(item["id"])
    [row] = _live(main)
    assert row["from_amount"] == pytest.approx(27.50)
    assert bank_data_sync.list_reviews("gocardless") == []


def test_dismissing_a_change_keeps_the_ledger(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-25.00, days_ago(5), "ACME HARDWARE", tid="h1")]
    _run()
    bank.booked[ACC_MAIN] = [txn(-27.50, days_ago(5), "ACME HARDWARE", tid="h1")]
    _run()
    [item] = bank_data_sync.list_reviews("gocardless")
    bank_data_sync.dismiss_review(item["id"])
    [row] = _live(main)
    assert row["from_amount"] == pytest.approx(25.00)
    with pytest.raises(ConflictError):
        bank_data_sync.accept_review(item["id"])


def test_a_vanished_row_waits_for_review_and_is_deleted_only_on_accept(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-60.00, days_ago(10), "ACME GARAGE", tid="v1"),
        txn(-1.00, days_ago(1), "ACME PARKING", tid="v2"),
    ]
    _run()
    bank.booked[ACC_MAIN] = [txn(-1.00, days_ago(1), "ACME PARKING", tid="v2")]
    _run()  # first miss
    assert bank_data_sync.list_reviews("gocardless") == []
    _run()  # second complete miss → vanished
    [item] = bank_data_sync.list_reviews("gocardless")
    assert item["kind"] == "vanished"
    assert len(_live(main)) == 2

    bank_data_sync.accept_review(item["id"])
    assert [t["description"] for t in _live(main)] == ["ACME PARKING"]
    assert _run()["counts"]["booked"] == 0


def test_a_row_matching_a_hand_typed_one_asks_instead_of_booking(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(4),
            "description": "ACME BOOKS",
            "from_account_id": main,
            "from_amount": 18.00,
            "from_currency": "GBP",
            "source": "manual",
        }
    )
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS", tid="d1")]

    result = _run()

    assert result["counts"]["booked"] == 0
    [item] = bank_data_sync.list_reviews("gocardless")
    assert item["kind"] == "duplicate"
    assert len(_live(main)) == 1
    assert _run()["counts"]["booked"] == 0  # held while it waits
    assert len(bank_data_sync.list_reviews("gocardless")) == 1

    bank_data_sync.accept_review(item["id"])
    [row] = _live(main)
    assert any(e.get("ref") == f"{ACC_MAIN}:d1" for e in row.get("external_refs") or [])
    assert _run()["counts"]["booked"] == 0


def test_a_duplicate_can_be_booked_as_its_own_transaction(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(4),
            "description": "ACME BOOKS",
            "from_account_id": main,
            "from_amount": 18.00,
            "from_currency": "GBP",
            "source": "manual",
        }
    )
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS", tid="d1")]
    _run()
    [item] = bank_data_sync.list_reviews("gocardless")
    bank_data_sync.book_review(item["id"])
    assert len(_live(main)) == 2
    assert _run()["counts"]["booked"] == 0


def test_a_dismissed_duplicate_is_not_asked_again_or_booked(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(4),
            "description": "ACME BOOKS",
            "from_account_id": main,
            "from_amount": 18.00,
            "from_currency": "GBP",
            "source": "manual",
        }
    )
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS", tid="d1")]
    _run()
    [item] = bank_data_sync.list_reviews("gocardless")
    bank_data_sync.dismiss_review(item["id"])
    result = _run()
    assert result["counts"]["booked"] == 0 and result["counts"]["to_review"] == 0
    assert bank_data_sync.list_reviews("gocardless") == []
    assert len(_live(main)) == 1
