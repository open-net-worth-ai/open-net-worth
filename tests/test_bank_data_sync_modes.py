"""Bank data sync — one sync mode per connector, the read budget, and backoff (ADR 2026-11-13)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from backend.core.errors import ConflictError
from backend.services import bank_data_sync
from backend.services import ledger as ledger_service
from backend.services.aggregation import gocardless as gocardless_svc
from backend.services.aggregation.providers import PROVIDERS
from backend.services.automations.service import apply_automation_change
from backend.services.bank_data_sync import budget
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

SYNC_TEMPLATE = {
    "name": "Sync GoCardless",
    "instructions": (
        "Fetch new GoCardless transactions and book them using the book-transactions skill. "
        "Message me only when something was booked or something went wrong."
    ),
    "trigger": {"kind": "schedule", "type": "interval", "seconds": 43200},
    "execution": {"kind": "lucius"},
    "permission_mode": "auto",
}


@pytest.fixture
def bank(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    linked_account("ACME Current", ACC_MAIN)
    fake.booked[ACC_MAIN] = [txn(-5.00, days_ago(2), "ACME KIOSK", tid="k1")]
    return fake


def _wait_for_runs() -> None:
    for t in threading.enumerate():
        if t.name.startswith("bank-sync-"):
            t.join(timeout=20)


def _automation(**overrides) -> dict:
    return apply_automation_change({"op": "create", "automation": {**SYNC_TEMPLATE, **overrides}})


# ── one mode at a time ───────────────────────────────────────────────────────────────


def test_sync_without_ai_turns_the_lucius_sync_off_and_says_so(bank):
    lucius = _automation()
    assert bank_data_sync.status("gocardless")["mode"] == "lucius"

    result = bank_data_sync.set_mode("gocardless", "data")

    assert result["mode"] == "data"
    assert result["paused_automations"] == ["Sync GoCardless"]
    from backend.db.automation_store import AutomationStore

    assert AutomationStore().find_by_id(lucius["id"])["status"] == "paused"


def test_a_lucius_sync_cannot_be_turned_on_while_data_sync_is(bank):
    paused = _automation(status="paused")
    bank_data_sync.set_mode("gocardless", "data")
    with pytest.raises(ConflictError, match="already syncs without AI"):
        _automation()
    with pytest.raises(ConflictError, match="choose Lucius on the GoCardless connector"):
        apply_automation_change({"op": "resume", "id": paused["id"]})


def test_choosing_lucius_turns_data_sync_off_and_resumes_its_automation(bank):
    _automation()
    bank_data_sync.set_mode("gocardless", "data")
    result = bank_data_sync.set_mode("gocardless", "lucius")
    assert result["mode"] == "lucius"
    assert result["resumed_automation"] == "Sync GoCardless"
    assert result["needs_template"] is False


def test_choosing_lucius_with_no_automation_asks_for_the_template(bank):
    result = bank_data_sync.set_mode("gocardless", "lucius")
    assert result["mode"] == "off"
    assert result["needs_template"] is True


def test_an_unrelated_automation_is_left_alone(bank):
    other = _automation(name="Weekly review", instructions="Summarise my spending every Sunday.")
    result = bank_data_sync.set_mode("gocardless", "data")
    assert result["paused_automations"] == []
    from backend.db.automation_store import AutomationStore

    assert AutomationStore().find_by_id(other["id"])["status"] == "active"


# ── the read budget ──────────────────────────────────────────────────────────────────


def test_sync_now_spam_stays_inside_four_reads_a_day_with_a_clear_message(bank):
    bank_data_sync.set_mode("gocardless", "data")
    for _ in range(4):
        bank_data_sync.run_now("gocardless")
        _wait_for_runs()
    with pytest.raises(ConflictError, match="read each account 4 times a day"):
        bank_data_sync.run_now("gocardless")
    assert bank.reads.count(ACC_MAIN) == 4
    status = bank_data_sync.status("gocardless")
    assert status["accounts"][0]["reads_left"] == 0
    assert status["accounts"][0]["next_read_at"]


def test_the_scheduler_respects_the_budget_too(bank):
    bank_data_sync.set_mode("gocardless", "data")
    for _ in range(4):
        bank_data_sync.run_now("gocardless")
        _wait_for_runs()
    budget.update("gocardless", next_due_at=None)  # due now
    assert bank_data_sync.run_if_due() is False
    assert bank.reads.count(ACC_MAIN) == 4


def test_reads_older_than_a_day_free_up(bank):
    at = budget.now()
    for _ in range(4):
        budget.note_read(GC, ACC_MAIN, at - timedelta(hours=25))
    assert budget.reads_left(GC, ACC_MAIN, at) == (4, None)


def test_sync_now_is_refused_unless_the_connector_is_in_data_mode(bank):
    with pytest.raises(ConflictError, match="isn't set to Sync without AI"):
        bank_data_sync.run_now("gocardless")


# ── schedule + backoff ───────────────────────────────────────────────────────────────


def test_the_tick_runs_when_due_and_then_waits_twelve_hours(bank):
    bank_data_sync.set_mode("gocardless", "data")
    assert bank_data_sync.run_if_due() is True
    assert bank_data_sync.run_if_due() is False
    st = budget.state("gocardless")
    gap = budget._parse(st["next_due_at"]) - budget._parse(st["last_run_at"])
    assert gap == timedelta(hours=12)
    assert len(ledger_service.transactions.all()) >= 2  # opening + the kiosk row


def test_a_rate_limit_backs_off_instead_of_retrying(bank):
    bank.fail_with = gocardless_svc.ExternalServiceError(
        "GoCardless HTTP 429 — the bank's daily limit for this account resets in about 90 min"
    )
    bank_data_sync.set_mode("gocardless", "data")
    assert bank_data_sync.run_if_due() is True
    for _ in range(5):
        assert bank_data_sync.run_if_due() is False
    assert bank.reads == [ACC_MAIN]
    st = budget.state("gocardless")
    assert st["last_status"] == "rate_limited"
    assert budget._parse(st["next_due_at"]) - budget._parse(st["last_run_at"]) == timedelta(
        minutes=95
    )


def test_an_auth_error_waits_a_day(bank):
    bank.fail_with = gocardless_svc.ExternalServiceError("GoCardless HTTP 401")
    bank_data_sync.set_mode("gocardless", "data")
    bank_data_sync.run_if_due()
    st = budget.state("gocardless")
    assert st["last_status"] == "auth_error"
    assert budget._parse(st["next_due_at"]) - budget._parse(st["last_run_at"]) == timedelta(
        hours=24
    )
    assert "401" in (bank_data_sync.status("gocardless")["last_error"] or "")


# ── setup: bring in the history before turning the sync on ───────────────────────────


def test_setup_walks_accounts_then_history_then_sync(bank):
    assert bank_data_sync.status("gocardless")["stage"] == "history"
    started = bank_data_sync.import_history("gocardless")
    assert started["accounts"] == 1
    _wait_for_runs()
    st = bank_data_sync.status("gocardless")
    assert st["mode"] == "off"  # importing doesn't turn the sync on
    assert st["accounts"][0]["imported"] is True
    assert st["stage"] == "sync"
    assert st["history_days"] == 90
    bank_data_sync.set_mode("gocardless", "data")
    assert bank_data_sync.status("gocardless")["stage"] == "done"


def test_history_import_is_refused_while_lucius_syncs_the_bank(bank):
    _automation()
    with pytest.raises(ConflictError, match="Lucius Automation syncs GoCardless"):
        bank_data_sync.import_history("gocardless")


def test_no_ticked_account_is_the_accounts_step(bank):
    account_id = bank_data_sync.status("gocardless")["accounts"][0]["account_id"]
    bank_data_sync.set_account_sync("gocardless", account_id, False)
    assert bank_data_sync.status("gocardless")["stage"] == "accounts"
