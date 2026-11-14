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
