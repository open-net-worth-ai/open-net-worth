"""A linked Brokerage total account follows its connector's total (ADR 2026-11-10).

A fake connector stands in for Plaid, so no network is touched. Figures are made up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.errors import ValidationError
from backend.db.transaction_store import TransactionStore
from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.aggregation import providers
from tests import test_account_value as base
from tests.test_account_value import _account, _add_holding, _instrument, _value

book = base.book  # the shared isolated-book fixture

REPORTED = 123456.78


class _Store:
    def __init__(self, enabled=True):
        self._enabled = enabled

    def is_configured(self):
        return True

    def get(self):
        return {"enabled": self._enabled}


@dataclass
class _FakeProvider:
    balances: dict = field(default_factory=dict)
    currency: str = "USD"
    enabled: bool = True
    fail: str | None = None
    calls: int = 0
    key: str = "plaid"
    label: str = "Plaid"
    disabled: bool = False

    def store(self):
        return _Store(self.enabled)

    def account_balances(self):
        self.calls += 1
        if self.fail:
            raise ValidationError(self.fail)
        return {
            "accounts": [
                {"provider_account_id": ref, "balance": bal, "currency": self.currency}
                for ref, bal in self.balances.items()
            ],
            "complete": True,
        }

    def classify_error(self, exc):
        return "reconnect_needed" if "ITEM_LOGIN_REQUIRED" in str(exc) else "error"


@pytest.fixture
def plaid(book, monkeypatch):
    fake = _FakeProvider(balances={"pa-1": REPORTED})
    monkeypatch.setitem(providers.PROVIDERS, "plaid", fake)
    return fake


def _linked(total=0.0, **extra):
    acct = _account(name="Individual Brokerage", type_="investment", total=total, **extra)
    link = [{"source": "plaid", "ref": "pa-1", "name": "Brokerage", "mask": "4321"}]
    accounts_service.update_account(acct["id"], {"external_ids": link})
    return acct


def _sync_state(account_id):
    acct = ledger_service.account_with_balance(ledger_service.accounts.find_by_id(account_id))
    return acct["value_sync"]


def _sync_rows():
    return [t for t in TransactionStore().all() if t.get("description") == "Total from Plaid"]


@pytest.mark.flags(investment_value_source=True)
def test_the_reported_case_a_linked_brokerage_at_zero_follows_plaids_total(plaid):
    acct = _linked()
    assert _sync_state(acct["id"])["status"] == "waiting"
    sync.run()
    assert _value(acct["id"]) == REPORTED
    state = _sync_state(acct["id"])
    assert state["status"] == "fresh" and state["total"] == REPORTED
    assert state["provider_label"] == "Plaid" and "ref" not in state
    assert len(_sync_rows()) == 1 and _sync_rows()[0]["kind"] == "adjustment"


@pytest.mark.flags(investment_value_source=True)
def test_an_unchanged_total_writes_nothing_and_a_new_one_books_once(plaid):
    acct = _linked()
    sync.run()
    sync.run()
    assert len(_sync_rows()) == 1
    plaid.balances["pa-1"] = 230000.00
    sync.run()
    assert _value(acct["id"]) == 230000.00
    assert len(_sync_rows()) == 2


@pytest.mark.flags(investment_value_source=True)
def test_tracked_holdings_are_part_of_the_synced_total(plaid):
    acct = _linked()
    _add_holding(acct["id"], _instrument())
    sync.run()
    assert _value(acct["id"]) == REPORTED
    assert round(av.ledger_balance(acct["id"]), 2) == round(REPORTED - 3020.0, 2)


@pytest.mark.flags(investment_value_source=True)
def test_a_reported_zero_is_held_until_the_user_answers(plaid):
    acct = _linked(total=5000.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "held_back" and state["held_total"] == 0.0
    assert _value(acct["id"]) == 5000.0
    sync.run()  # the same held figure is not asked about twice
    assert _sync_state(acct["id"])["status"] == "held_back"
    sync.confirm_held(acct["id"], accept=False)
    assert _sync_state(acct["id"])["status"] == "kept"
    sync.run()
    assert _value(acct["id"]) == 5000.0 and _sync_state(acct["id"])["status"] == "kept"


@pytest.mark.flags(investment_value_source=True)
def test_a_zero_on_an_account_already_at_zero_is_not_asked_about(plaid):
    """QA 2026-11-10: the card offered "Keep $0.00 / Use $0.00" — a question about nothing."""
    acct = _linked(total=0.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "fresh" and state["total"] == 0.0
    assert not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
def test_a_big_drop_is_held_and_accepting_it_books_it(plaid):
    acct = _linked()
    sync.run()
    plaid.balances["pa-1"] = 50000.0  # more than a quarter down
    sync.run()
    assert _sync_state(acct["id"])["status"] == "held_back"
    assert _value(acct["id"]) == REPORTED
    sync.confirm_held(acct["id"], accept=True)
    assert _value(acct["id"]) == 50000.0
    assert _sync_state(acct["id"])["status"] == "fresh"
    with pytest.raises(ValidationError):
        sync.confirm_held(acct["id"], accept=True)


@pytest.mark.flags(investment_value_source=True)
def test_another_currency_or_a_failed_read_writes_nothing(plaid):
    acct = _linked(total=5000.0)
    plaid.currency = "EUR"
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "currency_mismatch" and state["reported_currency"] == "EUR"
    plaid.currency, plaid.fail = "USD", "Plaid /accounts/get failed: ITEM_LOGIN_REQUIRED"
    sync.run()
    assert _sync_state(acct["id"])["status"] == "reconnect"
    assert _value(acct["id"]) == 5000.0 and not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
def test_a_paused_connector_writes_nothing(plaid):
    acct = _linked(total=5000.0)
    plaid.enabled = False
    sync.run()
    assert _value(acct["id"]) == 5000.0 and _sync_state(acct["id"])["status"] == "waiting"


@pytest.mark.flags(investment_value_source=True)
def test_a_live_holdings_account_only_records_the_comparison(plaid):
    acct = _linked(total=500.0, value_source="holdings")
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "compare" and state["reported_total"] == REPORTED
    assert _value(acct["id"]) == 500.0 and not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
def test_reconcile_on_a_linked_total_is_replaced_by_the_next_changed_figure(plaid):
    acct = _linked()
    sync.run()
    row = ledger_service.accounts.find_by_id(acct["id"])
    ledger_service.reconcile(acct["id"], av.reconcile_target(row, 225000.0), against="working")
    sync.run()  # Plaid's figure didn't change → the user's stays
    assert _value(acct["id"]) == 225000.0
    plaid.balances["pa-1"] = 226000.0
    sync.run()
    assert _value(acct["id"]) == 226000.0


@pytest.mark.flags(investment_value_source=True)
def test_the_tick_reads_a_new_link_at_once_then_waits_six_hours(plaid):
    acct = _linked()
    now = datetime.now(timezone.utc)
    assert sync.run_if_due(now) is True
    assert _value(acct["id"]) == REPORTED
    calls = plaid.calls
    assert sync.run_if_due(now + timedelta(minutes=5)) is False
    assert plaid.calls == calls
    assert sync.run_if_due(now + timedelta(hours=6, minutes=1)) is True


@pytest.mark.flags(investment_value_source=False)
def test_flag_off_the_tick_never_reads(plaid):
    _linked()
    assert sync.run_if_due() is False
    assert plaid.calls == 0


@pytest.mark.flags(investment_value_source=True)
def test_sync_rows_stay_out_of_spending_and_income(plaid):
    """A synced total moves net worth only: this month's report shows no spend and no income."""
    from backend.core.user_time import user_today
    from backend.services import reports_rollup

    _linked(total=100000.0)  # within the hold limits of the reported total, so it books
    sync.run()
    (row,) = _sync_rows()
    assert row["kind"] == "adjustment"
    summary = reports_rollup.month_summary(user_today().isoformat()[:7])
    assert summary["expense_total"] == 0 and summary["income_total"] == 0, summary


# ── security review 2026-11-10 ─────────────────────────────────────────────────────────


@pytest.mark.flags(investment_value_source=True)
@pytest.mark.parametrize("currency", ["DOGE", "https://bank.example/points", ""])
def test_an_unreadable_or_missing_currency_is_never_booked_as_ours(plaid, currency):
    acct = _linked(total=5000.0)
    plaid.currency = currency
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "currency_mismatch" and "reported_currency" not in state
    assert _value(acct["id"]) == 5000.0 and not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "Infinity"])
def test_a_non_finite_balance_is_no_balance(plaid, bad):
    acct = _linked(total=5000.0)
    plaid.balances["pa-1"] = bad
    sync.run()
    assert _sync_state(acct["id"])["status"] == "no_balance"
    assert not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
def test_linking_the_wrong_account_is_held_not_booked(plaid):
    """A typed $500k total linked to a $5k provider account must not be rewritten silently."""
    acct = _linked(total=500000.0)
    plaid.balances["pa-1"] = 5000.0
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "held_back" and state["held_total"] == 5000.0
    assert _value(acct["id"]) == 500000.0


@pytest.mark.flags(investment_value_source=True)
def test_a_jump_past_four_times_is_held_too(plaid):
    acct = _linked()
    sync.run()  # 0 → REPORTED: an account worth nothing takes its first total
    plaid.balances["pa-1"] = REPORTED * 5
    sync.run()
    assert _sync_state(acct["id"])["status"] == "held_back"
    assert _value(acct["id"]) == REPORTED


@pytest.mark.flags(investment_value_source=True)
def test_a_reading_that_raises_is_recorded_and_not_retried_every_minute(plaid, monkeypatch):
    acct = _linked()

    def boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sync, "_apply_total", boom)
    now = datetime.now(timezone.utc)
    assert sync.run_if_due(now) is True
    assert _sync_state(acct["id"])["status"] == "error"
    calls = plaid.calls
    assert sync.run_if_due(now + timedelta(minutes=1)) is False
    assert plaid.calls == calls


@pytest.mark.flags(investment_value_source=True)
def test_no_rate_for_the_holdings_writes_nothing(plaid, monkeypatch):
    acct = _linked()
    monkeypatch.setattr(av, "_value_or_none", lambda account: None)
    sync.run()
    state = _sync_state(acct["id"])
    assert state["status"] == "error" and state["error_code"] == "no_rate"
    assert not _sync_rows()


@pytest.mark.flags(investment_value_source=True)
def test_confirm_is_refused_once_the_account_is_on_live_holdings(plaid):
    acct = _linked(total=5000.0)
    plaid.balances["pa-1"] = 0.0
    sync.run()
    ledger_service.accounts.update_by_id(acct["id"], {"value_source": "holdings"})
    with pytest.raises(ValidationError):
        sync.confirm_held(acct["id"], accept=True)
    assert _value(acct["id"]) == 5000.0


@pytest.mark.flags(investment_value_source=True)
def test_a_failed_edit_never_clears_the_untracked_part(plaid, monkeypatch):
    """The edit, the switch and its adjustment commit or roll back together."""
    acct = _account()
    _add_holding(acct["id"], _instrument())

    def fail(*_a, **_k):
        raise ValidationError("institution refused")

    monkeypatch.setattr(accounts_service, "_resolve_institution_patch", fail)
    with pytest.raises(ValidationError):
        accounts_service.update_account(
            acct["id"], {"value_source": "holdings", "institution": "Acme"}
        )
    assert _value(acct["id"]) == 8000.0
    assert ledger_service.accounts.find_by_id(acct["id"])["value_source"] == "total"
