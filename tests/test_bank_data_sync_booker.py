"""Bank data sync — booking a bank connector's rows without AI (ADR 2026-11-13).

One run per scenario through the real runner, connector_fetch and ledger choke; only the bank
is fake (``tests/bank_sync_fakes.py``). Each acceptance item of the feature has a test here."""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError
from backend.services import bank_data_sync, import_runs
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


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    return fake


def _run(trigger: str = "manual") -> dict:
    return runner.run_now(GC, trigger, runner.opted_accounts("gocardless"))


def _live(account_id: str | None = None) -> list[dict]:
    out = []
    for t in ledger_service.transactions.all():
        if t.get("deleted") or t.get("id") in ledger_service.opening_txn_ids():
            continue
        if account_id and account_id not in (t.get("from_account_id"), t.get("to_account_id")):
            continue
        out.append(t)
    return out


def _balance(account_id: str) -> float:
    return round(ledger_service.balances()[account_id], 2)


pytestmark = pytest.mark.flags(bank_data_sync=True)


def test_first_import_books_the_history_and_keeps_todays_balance(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-40.00, days_ago(75), "ACME GROCER", tid="t1"),
        txn(-12.50, days_ago(40), "ACME CAFE", tid="t2"),
        txn(2500.00, days_ago(20), "ACME PAYROLL", tid="t3"),
    ]
    settings.save("gocardless", mode="data")
    before = _balance(main)

    result = _run()

    assert result["status"] == "ok"
    assert result["counts"]["booked"] == 3
    assert {t["date"] for t in _live(main)} == {days_ago(75), days_ago(40), days_ago(20)}
    account = ledger_service.accounts.find_by_id(main)
    assert ledger_service.opening_date(account, ledger_service.transactions.all()) == days_ago(75)
    assert _balance(main) == before
    run = import_runs.get(result["run_id"])
    assert run["status"] == "done" and run["kind"] == "sync" and run["counts"]["booked"] == 3


def test_what_the_sync_books_is_cleared(bank):
    """Cleared means settled at the bank, whoever entered it — and the sync books only settled
    rows, so each is cleared and the account's Cleared balance reads as the bank's."""
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-12.50, days_ago(4), "ACME CAFE", tid="c1")]
    settings.save("gocardless", mode="data")
    _run()
    synced = [t for t in _live(main) if t.get("source") == "feed"]
    assert synced and all(t.get("cleared") is True for t in synced)


def test_a_rerun_books_nothing_and_a_new_row_books_once(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-9.99, days_ago(3), "ACME STREAMING", tid="s1")]
    settings.save("gocardless", mode="data")
    assert _run()["counts"]["booked"] == 1

    again = _run()
    assert again["counts"]["booked"] == 0
    assert again["counts"]["already_booked"] == 1

    bank.booked[ACC_MAIN].append(txn(-4.20, days_ago(1), "ACME BAKERY", tid="s2"))
    assert _run()["counts"]["booked"] == 1
    assert _run()["counts"]["booked"] == 0
    assert len(_live(main)) == 2


def test_an_unticked_account_is_never_fetched_or_booked(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    saver = linked_account("ACME Saver", ACC_SAVER)
    bank.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]
    bank.booked[ACC_SAVER] = [txn(100.00, days_ago(2), "INTEREST", tid="i1")]
    settings.save("gocardless", mode="data", excluded_account_ids=[saver])

    _run()

    assert bank.reads == [ACC_MAIN]
    assert _live(saver) == []
    assert len(_live(main)) == 1


def test_a_deleted_transaction_is_not_booked_again(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-30.00, days_ago(5), "ACME GYM", tid="g1")]
    settings.save("gocardless", mode="data")
    _run()
    [booked] = _live(main)
    ledger_service.bulk_delete_transactions([booked["id"]])

    result = _run()

    assert result["counts"]["booked"] == 0
    assert result["counts"]["deleted_before"] == 1
    assert _live(main) == []


def test_two_identical_rows_book_as_two(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    coffee = txn(-3.00, days_ago(4), "ACME COFFEE")  # no provider id → content key
    bank.booked[ACC_MAIN] = [dict(coffee), dict(coffee)]
    settings.save("gocardless", mode="data")

    assert _run()["counts"]["booked"] == 2
    assert _run()["counts"]["booked"] == 0
    assert len(_live(main)) == 2


def test_a_foreign_charge_keeps_its_original_amount_and_currency(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(
            -86.40,
            days_ago(6),
            "ACME HOTEL PARIS",
            tid="fx1",
            currencyExchange={"instructedAmount": {"amount": "100.00", "currency": "EUR"}},
        )
    ]
    settings.save("gocardless", mode="data")
    _run()
    [row] = _live(main)
    assert row["from_amount"] == pytest.approx(86.40)
    assert row["from_currency"] == "GBP"
    assert row["original_amount"] == pytest.approx(100.0)
    assert row["original_currency"] == "EUR"


def test_pending_rows_are_not_booked(bank):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.pending[ACC_MAIN] = [txn(-15.00, days_ago(0), "ACME TAXI", tid="p1")]
    settings.save("gocardless", mode="data")
    result = _run()
    assert result["counts"]["booked"] == 0
    assert result["counts"]["pending_skipped"] == 1
    assert _live(main) == []


def test_payee_default_categorises_hint_only_when_it_exists_and_none_is_created(bank):
    from backend.services import categories as categories_service
    from backend.services import payee_registry

    main = linked_account("ACME Current", ACC_MAIN)
    airlines = categories_service.create_category("Airlines")["id"]
    dining = categories_service.create_category("Dining")["id"]
    payee = payee_registry.create_payee("ACME Diner", default_category=dining)
    assert payee["id"]
    categories_before = {c["id"] for c in categories_service.all_category_rows()}
    bank.booked[ACC_MAIN] = [
        # the payee's default wins over the bank's hint
        txn(
            -20.00,
            days_ago(3),
            "CARD ACME DINER",
            tid="c1",
            creditorName="ACME Diner",
            merchantCategoryCode="4511",
        ),
        # MCC 4511 → "Airlines", a category this book has
        txn(-210.00, days_ago(3), "ACME AIR", tid="c2", merchantCategoryCode="4511"),
        # MCC 5411 → "Grocery stores and supermarkets", which it doesn't: Uncategorized
        txn(-31.00, days_ago(3), "ACME MART 123", tid="c3", merchantCategoryCode="5411"),
        # no hint at all
        txn(-7.00, days_ago(3), "SOMETHING", tid="c4"),
    ]
    settings.save("gocardless", mode="data")
    _run()
    by_text = {t["description"]: t.get("category") for t in _live(main)}
    assert by_text["CARD ACME DINER"] == dining
    assert by_text["ACME AIR"] == airlines
    assert by_text["ACME MART 123"] is None
    assert by_text["SOMETHING"] is None
    assert {c["id"] for c in categories_service.all_category_rows()} == categories_before


def test_a_failed_booking_leaves_the_ledger_untouched(bank, monkeypatch):
    main = linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [
        txn(-1.00, days_ago(2), "A", tid="f1"),
        txn(-2.00, days_ago(2), "B", tid="f2"),
    ]
    settings.save("gocardless", mode="data")
    real = ledger_service.bulk_create_transactions

    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", explode)
    result = _run()
    assert result["status"] == "error"
    assert _live(main) == []
    assert import_runs.get(result["run_id"])["status"] == "failed"

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", real)
    assert _run()["counts"]["booked"] == 2


def test_run_records_hold_no_secret(bank):
    linked_account("ACME Current", ACC_MAIN)
    bank.fail_with = gocardless_svc.ExternalServiceError("GoCardless HTTP 401")
    settings.save("gocardless", mode="data")
    result = _run()
    run = import_runs.get(result["run_id"])
    text = repr(run)
    assert "SKEY" not in text and "SID" not in text and "req-1" not in text
    assert result["status"] == "auth_error"


@pytest.mark.flags(bank_data_sync=False)
def test_flag_off_changes_nothing(bank):
    linked_account("ACME Current", ACC_MAIN)
    bank.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]
    settings.save("gocardless", mode="data")  # a stored choice the off path never reads
    assert bank_data_sync.run_if_due() is False
    assert bank.reads == []
    with pytest.raises(NotFoundError):
        bank_data_sync.status("gocardless")
    with pytest.raises(NotFoundError):
        bank_data_sync.run_now("gocardless")
    bank_data_sync.refuse_automation_beside_data_sync(
        {"name": "Sync GoCardless", "instructions": "Fetch new GoCardless transactions"}
    )
