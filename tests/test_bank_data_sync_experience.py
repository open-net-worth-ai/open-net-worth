"""Bank data sync — experience fixes after the 2026-11-15 reviews. Synthetic ACME data only."""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.services.aggregation.providers import PROVIDERS
from backend.services.bank_data_sync import budget

pytestmark = pytest.mark.flags(bank_data_sync=True)
GC = PROVIDERS["gocardless"]


# ── Renewing a bank's access wakes a sync that was waiting a day for it ─────────────────────


def test_renewing_access_makes_a_waiting_sync_due_now(db_isolated):
    budget.record_outcome(GC, "reconnect_needed", "ACME Bank: access ended")
    assert not budget.is_due(GC)
    budget.access_renewed("gocardless")
    assert budget.is_due(GC)
    assert budget.state("gocardless")["failures"] == 0


def test_renewing_access_leaves_a_healthy_schedule_alone(db_isolated):
    budget.record_outcome(GC, "ok", None)
    due = budget.state("gocardless")["next_due_at"]
    budget.access_renewed("gocardless")
    assert budget.state("gocardless")["next_due_at"] == due
    assert not budget.is_due(GC, budget.now() + timedelta(minutes=1))


# ── Plaid: cents, its own error codes, plain words ──────────────────────────────────────────


def test_plaid_amounts_are_booked_in_cents():
    from backend.services.aggregation import plaid

    assert plaid._txn_amount({"amount": 97.66696, "iso_currency_code": "USD"}) == -97.67
    assert plaid._txn_amount({"amount": 0.00012, "unofficial_currency_code": "BTC"}) == -0.00012


@pytest.mark.parametrize(
    "code, status",
    [
        ("ITEM_LOGIN_REQUIRED", "reconnect_needed"),
        ("ITEM_LOCKED", "reconnect_needed"),
        ("NO_ACCOUNTS", "reconnect_needed"),
        ("ITEM_NOT_FOUND", "reconnect_needed"),
        ("INVALID_API_KEYS", "auth_error"),
        ("INSTITUTION_DOWN", "connect_error"),
        ("PRODUCT_NOT_READY", "connect_error"),
    ],
)
def test_plaid_errors_are_read_by_their_code_not_the_http_400(code, status):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import plaid

    exc = ValidationError(
        f"Plaid /transactions/get failed: HTTP 400 — {code}", plaid_error_code=code
    )
    assert plaid.classify_error(exc) == status


def test_plaid_failures_are_told_in_plain_words():
    from backend.core.errors import ValidationError
    from backend.services.aggregation import plaid

    login = ValidationError(
        "Plaid … HTTP 400 — the login details of this item have changed",
        plaid_error_code="ITEM_LOGIN_REQUIRED",
    )
    assert "sign in again" in plaid._plain(login)
    assert "HTTP" not in plaid._plain(login)


# ── A catch-up reaches as far back as the provider serves ───────────────────────────────────


def test_plaid_catches_up_past_ninety_days(monkeypatch):
    from backend.core.user_time import user_today
    from backend.services import connector_window
    from backend.services import ledger as ledger_service

    newest = user_today() - timedelta(days=200)
    monkeypatch.setattr(ledger_service, "newest_booked_date", lambda *a, **k: newest)
    plaid = PROVIDERS["plaid"]
    since, _, warning = connector_window.default_window(plaid, None, None)
    assert since == (newest - timedelta(days=plaid.lookback_days)).isoformat()
    assert warning is None

    gocardless = PROVIDERS["gocardless"]
    since, _, warning = connector_window.default_window(gocardless, None, None)
    assert since == (user_today() - timedelta(days=90)).isoformat()
    assert warning and "statement" in warning


# ── A read the bank cuts short is never taken as whole ──────────────────────────────────────


@pytest.fixture
def bank(db_isolated, monkeypatch):
    from backend.core import headless_turn
    from backend.services import ledger as ledger_service
    from backend.services.aggregation import gocardless as gocardless_svc
    from backend.services.bank_data_sync import settings
    from tests.bank_sync_fakes import ACC_MAIN, FakeBank, configure_gocardless, linked_account

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "GBP")
    fake = FakeBank()
    monkeypatch.setattr(gocardless_svc, "fetch_window", fake.fetch_window)
    configure_gocardless()
    fake.account = linked_account("ACME Current", ACC_MAIN)
    settings.save("gocardless", mode="data")
    yield fake
    headless_turn.register_runner(None)


def _run() -> dict:
    from backend.services.bank_data_sync import runner

    return runner.run_now(GC, "manual", runner.opted_accounts("gocardless"))


def _live(account_id: str) -> list[dict]:
    from backend.services import ledger as ledger_service

    opening = ledger_service.opening_txn_ids()
    return [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted")
        and t["id"] not in opening
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
    ]


def test_a_first_import_cut_short_books_nothing_and_is_read_whole_next_time(bank):
    from tests.bank_sync_fakes import ACC_MAIN, days_ago, txn

    bank.booked[ACC_MAIN] = [
        txn(-5.00, days_ago(80), "ACME OLD", tid="o1"),
        txn(-6.00, days_ago(2), "ACME NEW", tid="n1"),
    ]
    bank.cut_short = {ACC_MAIN}
    _run()
    assert _live(bank.account) == []
    reads = budget.state("gocardless")
    assert reads["account_reads"][bank.account]["status"] == "connect_error"
    assert bank.account not in (reads.get("history_read") or {})

    bank.cut_short = set()
    result = _run()
    assert result["counts"]["booked"] == 2  # the whole history, the oldest row included


def test_a_later_read_cut_short_never_drops_a_pending_booking(bank):
    from tests.bank_sync_fakes import ACC_MAIN, days_ago, txn

    bank.pending[ACC_MAIN] = [txn(-9.99, days_ago(1), "ACME STREAM")]
    _run()
    assert len(_live(bank.account)) == 1
    bank.pending[ACC_MAIN] = []  # not in a list the bank cut short
    bank.cut_short = {ACC_MAIN}
    result = _run()
    assert result["counts"].get("pending_removed", 0) == 0
    assert len(_live(bank.account)) == 1


def test_plaid_another_banks_failure_never_cuts_this_accounts_read_short(monkeypatch):
    from backend.services.aggregation import plaid

    def fetched(store, **kw):
        return {
            "transactions": [],
            "accounts": {"acme-ok": {"account_id": "acme-ok", "balances": {}}},
            "warnings": ["Other Bank: the bank needs you to sign in again"],
            "complete": False,  # the other bank failed
            "cut_short": set(),
            "error": None,
        }

    monkeypatch.setattr(plaid, "fetch_transactions_get", fetched)
    window = plaid.fetch_window(None, "2026-11-01", "2026-11-14", "acme-ok")
    assert window.complete is True

    monkeypatch.setattr(
        plaid,
        "fetch_transactions_get",
        lambda store, **kw: {**fetched(store), "cut_short": {"acme-ok"}},
    )
    assert plaid.fetch_window(None, "2026-11-01", "2026-11-14", "acme-ok").complete is False
