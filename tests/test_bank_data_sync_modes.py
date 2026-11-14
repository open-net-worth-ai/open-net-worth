"""Bank data sync — one sync mode per connector, the read budget, and backoff (ADR 2026-11-13)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from backend.core.errors import ConflictError, ValidationError
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


# ── the sync and a Lucius Automation are independent ─────────────────────────────────
# (ADR 2026-11-13, Amendment 6): nothing detects, pauses or refuses one for the other.


def test_turning_the_sync_on_leaves_a_lucius_automation_running(bank):
    lucius = _automation()
    result = bank_data_sync.set_mode("gocardless", "data")
    assert result["mode"] == "data"
    assert "paused_automations" not in result
    from backend.db.automation_store import AutomationStore

    assert AutomationStore().find_by_id(lucius["id"])["status"] == "active"


def test_a_lucius_automation_can_be_created_and_resumed_while_the_sync_is_on(bank):
    paused = _automation(status="paused")
    bank_data_sync.set_mode("gocardless", "data")
    _automation()
    apply_automation_change({"op": "resume", "id": paused["id"]})


def test_lucius_is_not_a_sync_mode(bank):
    _automation()
    assert bank_data_sync.status("gocardless")["mode"] == "off"
    with pytest.raises(ValidationError):
        bank_data_sync.set_mode("gocardless", "lucius")


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
    with pytest.raises(ConflictError, match="The GoCardless sync is off"):
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
    # Off again is a set-up sync that is off, never back to the setup steps.
    bank_data_sync.set_mode("gocardless", "off")
    st = bank_data_sync.status("gocardless")
    assert (st["mode"], st["stage"]) == ("off", "done")


def test_no_ticked_account_is_the_accounts_step(bank):
    account_id = bank_data_sync.status("gocardless")["accounts"][0]["account_id"]
    bank_data_sync.set_account_sync("gocardless", account_id, False)
    assert bank_data_sync.status("gocardless")["stage"] == "accounts"


# ── schedule: the user picks how often, within what the bank allows ─────────────────


def test_the_schedule_defaults_to_the_provider_and_offers_only_what_reads_allow(bank):
    st = bank_data_sync.status("gocardless")
    assert (st["interval_hours"], st["intervals"]) == (12, [6, 12, 24])
    plaid = PROVIDERS["plaid"]
    assert bank_data_sync.settings.interval_choices(plaid) == [1, 6, 12, 24]


def test_a_new_schedule_moves_the_next_run_from_the_last_one(bank):
    bank_data_sync.set_mode("gocardless", "data")
    bank_data_sync.run_now("gocardless")
    _wait_for_runs()
    last = budget._parse(budget.state("gocardless")["last_run_at"])
    st = bank_data_sync.set_interval("gocardless", 24)
    assert st["interval_hours"] == 24
    assert budget._parse(budget.state("gocardless")["next_due_at"]) == last + timedelta(hours=24)


def test_a_schedule_the_bank_cant_afford_is_refused(bank):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError, match="every 6, 12, 24 hours"):
        bank_data_sync.set_interval("gocardless", 1)
    assert bank_data_sync.status("gocardless")["interval_hours"] == 12


def test_each_account_says_which_bank_it_is_at(bank):
    from backend.services import accounts as accounts_service

    account_id = bank_data_sync.status("gocardless")["accounts"][0]["account_id"]
    accounts_service.update_account(account_id, {"institution": "ACME Bank"})
    [acct] = bank_data_sync.status("gocardless")["accounts"]
    assert acct["bank"] == "ACME Bank"


def test_each_account_says_when_its_bank_access_ends_and_when_it_has(bank):
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    store = GoCardlessConfigStore()
    [b] = store.banks()
    store.update_bank(b["key"], consent_expires_at="2099-02-11T00:00:00+00:00")
    [acct] = bank_data_sync.status("gocardless")["accounts"]
    assert acct["access_ends_at"].startswith("2099-02-11")
    assert (acct["access_ended"], acct["access_renew_soon"]) == (False, False)

    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    store.update_bank(b["key"], consent_expires_at=soon)
    assert bank_data_sync.status("gocardless")["accounts"][0]["access_renew_soon"] is True

    store.update_bank(b["key"], status="EX")
    [acct] = bank_data_sync.status("gocardless")["accounts"]
    assert acct["access_ended"] is True
