"""Bank data sync — regressions for the pre-ship security review (ADR 2026-11-13).

Each test names the finding it pins: a failed run still backs off (M2), a bank read is counted
once per call and the account probe is skipped (M3), a decision runs once (M1) and only on the
version you saw (L1), a booking that committed is never reported as unbooked and its alignment
is retried (L2), a pending→posted link stays on its own account (L3), and the follow-up applies
only categories it offered (L5)."""

from __future__ import annotations

import asyncio

import pytest

from backend.core import headless_turn
from backend.core.errors import ConflictError
from backend.services import bank_data_sync, connector_fetch, import_runs
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import booker, budget, reviews, runner, settings
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
    fake.probes = 0
    real = fake.fetch_window

    def counting(store, since, before, account, *, accounts_only=False):
        if accounts_only:
            fake.probes += 1
        return real(store, since, before, account, accounts_only=accounts_only)

    monkeypatch.setattr(gocardless_svc, "fetch_window", counting)
    configure_gocardless()
    fake.account = linked_account("ACME Current", ACC_MAIN)
    settings.save("gocardless", mode="data")
    yield fake
    headless_turn.register_runner(None)


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


def _duplicate_item(bank) -> dict:
    ledger_service.create_transaction(
        {
            "kind": "expense",
            "date": days_ago(4),
            "description": "ACME BOOKS",
            "from_account_id": bank.account,
            "from_amount": 18.00,
            "from_currency": "GBP",
            "source": "manual",
        }
    )
    bank.booked[ACC_MAIN] = [txn(-18.00, days_ago(4), "ACME BOOKS", tid="d1")]
    _run()
    [item] = bank_data_sync.list_reviews("gocardless")
    return item


# ── M2 ───────────────────────────────────────────────────────────────────────────────


def test_a_failure_before_booking_still_backs_off(bank, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("snapshot store unavailable")

    monkeypatch.setattr(runner, "_fetch", broken)
    assert bank_data_sync.run_if_due() is True
    st = budget.state("gocardless")
    assert st["last_status"] == "error"
    assert budget._parse(st["next_due_at"]) > budget._parse(st["last_run_at"])
    for _ in range(3):
        assert bank_data_sync.run_if_due() is False  # no retry loop
    assert import_runs.latest("gocardless", ("sync",))["status"] == "failed"


# ── M3 ───────────────────────────────────────────────────────────────────────────────


def test_one_run_is_one_bank_read_and_no_probe(bank):
    bank.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]
    _run()
    assert bank.reads == [ACC_MAIN]
    assert bank.probes == 0
    assert budget.reads_left(GC, ACC_MAIN)[0] == 3


def test_a_lucius_fetch_spends_the_same_daily_budget(bank):
    connector_fetch.fetch("gocardless")
    assert budget.reads_left(GC, ACC_MAIN)[0] == 3


# ── M1 / L1 ──────────────────────────────────────────────────────────────────────────


def test_a_decision_runs_once_even_when_sent_twice(bank, monkeypatch):
    item = _duplicate_item(bank)
    real = ledger_service.bulk_create_transactions
    second: list[Exception] = []

    def reentrant(*args, **kwargs):
        try:
            bank_data_sync.book_review(item["id"])  # the second click, mid-write
        except ConflictError as exc:
            second.append(exc)
        return real(*args, **kwargs)

    monkeypatch.setattr(ledger_service, "bulk_create_transactions", reentrant)
    bank_data_sync.book_review(item["id"])
    assert second, "the second decision was not refused"
    assert len(_live(bank.account)) == 2  # the hand-typed row + ONE bank row


def test_a_failed_decision_puts_the_item_back(bank, monkeypatch):
    item = _duplicate_item(bank)

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ledger_service, "attach_external_refs", boom)
    with pytest.raises(RuntimeError):
        bank_data_sync.accept_review(item["id"])
    assert [r["id"] for r in bank_data_sync.list_reviews("gocardless")] == [item["id"]]


def test_a_decision_on_an_older_version_is_refused(bank):
    item = _duplicate_item(bank)
    with pytest.raises(ConflictError, match="changed this item"):
        bank_data_sync.accept_review(item["id"], seen="2000-01-01T00:00:00+00:00")
    bank_data_sync.accept_review(item["id"], seen=item["updated_at"])


# ── L2 ───────────────────────────────────────────────────────────────────────────────


def test_a_booking_that_committed_is_never_reported_as_unbooked(bank, monkeypatch):
    bank.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]

    def broken(*args, **kwargs):
        raise RuntimeError("review store unavailable")

    monkeypatch.setattr(booker, "_classify_skipped", broken)
    result = _run()
    assert result["counts"]["booked"] == 1
    assert len(_live(bank.account)) == 1


def test_a_first_import_alignment_that_failed_is_retried(bank, monkeypatch):
    from backend.services import tracking_start

    bank.booked[ACC_MAIN] = [txn(-40.00, days_ago(60), "ACME GROCER", tid="g1")]
    real = tracking_start.align_tracking_start
    monkeypatch.setattr(
        tracking_start, "align_tracking_start", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    _run()
    account = ledger_service.accounts.find_by_id(bank.account)
    assert ledger_service.opening_date(account, ledger_service.transactions.all()) > days_ago(60)
    assert budget.state("gocardless")["pending_align"] == [bank.account]

    monkeypatch.setattr(tracking_start, "align_tracking_start", real)
    _run()
    account = ledger_service.accounts.find_by_id(bank.account)
    assert ledger_service.opening_date(account, ledger_service.transactions.all()) == days_ago(60)
    assert budget.state("gocardless")["pending_align"] == []


# ── L5 ───────────────────────────────────────────────────────────────────────────────


def test_the_follow_up_never_applies_a_hidden_category(bank):
    from backend.services.bank_data_sync import followup

    secret = categories_service.create_category("Hidden Bucket")["id"]
    categories_service.set_category_hidden(secret, True)
    settings.save("gocardless", lucius_followup=True)
    bank.booked[ACC_MAIN] = [txn(-9.00, days_ago(2), "ACME THING", tid="h1")]
    result = _run()
    [row] = result["created"]

    async def lucius(prompt, **kwargs):
        assert "Hidden Bucket" not in prompt
        return f'{{"{row["id"]}": "Hidden Bucket"}}'

    headless_turn.register_runner(lucius)
    asyncio.run(bank_data_sync.run_followups())
    assert ledger_service.transactions.find_by_id(row["id"]).get("category") is None
    assert followup.apply({row["id"]: "Hidden Bucket"}, [row["id"]], []) == 0


def test_two_runs_before_a_tick_keep_both_follow_ups(bank):
    settings.save("gocardless", lucius_followup=True)
    bank.booked[ACC_MAIN] = [txn(-9.00, days_ago(2), "ACME ONE", tid="o1")]
    _run()
    bank.booked[ACC_MAIN].append(txn(-8.00, days_ago(1), "ACME TWO", tid="o2"))
    _run()
    assert len(budget.state("gocardless")["pending_followups"]) == 2
    assert reviews.open_count("gocardless") == 0
