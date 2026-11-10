"""UAT: "My bank feed reports something weird" (HOLD) and feed states / providers / timing (FEED).

A fake connector stands in for the provider; each provider's own normaliser is called on a
made-up wire payload so the figure the sync sees is the one that provider really produces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.core import data_events
from backend.core.errors import ValidationError
from backend.services import account_value as av
from backend.services import account_value_sync as sync
from backend.services import ledger as ledger_service
from backend.services.aggregation import providers
from tests.account_value_uat_support import *  # noqa: F401,F403
from tests.account_value_uat_support import (
    REPORTED,
    FakeProvider,
    _account,
    _add_holding,
    _instrument,
    _value,
    balance,
    hydrated,
    link_account,
    linked,
    rows,
    signed,
    sync_rows,
    sync_state,
    today,
)

ON = pytest.mark.flags(investment_value_source=True)


def _synced(plaid, total=100000.0, **extra):
    x = linked(**extra)
    plaid.balances["pa-1"] = total
    sync.run()
    assert _value(x["id"]) == total
    return x


def _hold_zero(plaid, x):
    plaid.balances["pa-1"] = 0.0
    sync.run()
    assert sync_state(x["id"])["status"] == "held_back"


# ── HOLD ───────────────────────────────────────────────────────────────────────────────


@ON
@pytest.mark.parametrize(
    ("reported", "delta"),
    [(75000.0, -25000.0), (400000.0, 300000.0)],
    ids=["05-exact-25pct-drop", "07-exact-4x-jump"],
)
def test_hold_05_07_the_exact_boundary_is_applied_once_synced(plaid, reported, delta):
    x = _synced(plaid)
    plaid.balances["pa-1"] = reported
    sync.run()
    assert sync_state(x["id"])["status"] == "fresh" and _value(x["id"]) == reported
    assert signed(sync_rows()[-1], x["id"]) == delta


@ON
def test_hold_10_a_second_suspicious_figure_replaces_the_pending_hold(plaid):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    plaid.balances["pa-1"] = 70000.0  # a 30% drop: also held
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "held_back" and state["held_total"] == 70000.0
    assert _value(x["id"]) == 100000.0 and len(sync_rows()) == 1


@ON
def test_hold_10_an_ordinary_figure_applies_and_clears_the_pending_hold(plaid):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    plaid.balances["pa-1"] = 80000.0  # a 20% drop applies outright
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "fresh" and state["total"] == 80000.0
    assert "held_total" not in state
    assert _value(x["id"]) == 80000.0 and signed(sync_rows()[-1], x["id"]) == -20000.0


@ON
def test_hold_11_the_applied_figure_coming_back_clears_the_hold_and_books_nothing(plaid):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    plaid.balances["pa-1"] = 100000.0
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "fresh" and "held_total" not in state
    assert len(sync_rows()) == 1 and _value(x["id"]) == 100000.0


@ON
def test_hold_13_confirm_is_refused_where_there_is_nothing_to_confirm(plaid, client):
    cash = _account(name="Checking", type_="cash", total=100.0)
    unlinked = _account(name="Unlinked", type_="investment", total=100.0)
    fresh = _synced(plaid)
    path = "/api/accounts/{}/value-sync/confirm"
    for acct_id in (cash["id"], unlinked["id"], fresh["id"]):
        assert client.post(path.format(acct_id), json={"accept": True}).status_code == 422
    assert client.post(path.format("acc_missing"), json={"accept": True}).status_code == 404
    assert _value(fresh["id"]) == 100000.0 and len(sync_rows()) == 1


@ON
def test_hold_14_use_books_against_the_book_as_it_is_now(plaid):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    _add_holding(x["id"], _instrument())  # carved out of the untracked part meanwhile
    assert (balance(x["id"]), _value(x["id"])) == (96980.0, 100000.0)
    sync.confirm_held(x["id"], accept=True)
    assert _value(x["id"]) == 0.0 and balance(x["id"]) == -3020.0


@ON
def test_hold_15_use_with_no_rate_is_refused_and_keeps_the_hold(plaid, client, monkeypatch):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    monkeypatch.setattr(av, "_value_or_none", lambda account: None)
    res = client.post(f"/api/accounts/{x['id']}/value-sync/confirm", json={"accept": True})
    assert res.status_code == 422 and "can't be valued" in res.json()["detail"]
    assert sync_state(x["id"])["status"] == "held_back" and len(sync_rows()) == 1
    assert _value(x["id"]) == 100000.0


@ON
def test_hold_16_audit_names_the_system_tick_and_the_users_answer(plaid, client):
    x = _synced(plaid)
    _hold_zero(plaid, x)
    sync.confirm_held(x["id"], accept=True)
    tick, used = sync_rows()
    (tick_event,) = client.get(f"/api/transactions/{tick['id']}/audit").json()["events"]
    assert (tick_event["actor"], tick_event["surface"]) == ("system", "account_value_sync")
    (use_event,) = client.get(f"/api/transactions/{used['id']}/audit").json()["events"]
    assert (use_event["actor"], use_event["surface"]) == ("user", "account_value_sync")
    assert use_event["note"] == "held-back total"
    manual = ledger_service.create_transaction(
        {
            "date": today(),
            "kind": "income",
            "description": "Gift",
            "to_account_id": x["id"],
            "to_amount": 1.0,
            "to_currency": "USD",
        }
    )
    (event,) = client.get(f"/api/transactions/{manual['id']}/audit").json()["events"]
    assert event["surface"] != "account_value_sync" and event["note"] != "held-back total"


@ON
def test_hold_17_a_run_publishes_once_when_it_changed_something_and_after_a_confirm(
    plaid, monkeypatch
):
    seen = []
    monkeypatch.setattr(data_events, "publish", lambda scopes: seen.append(list(scopes)))
    x = linked()
    sync.run()
    assert seen == [["*"]]
    sync.run()  # nothing changed
    assert seen == [["*"]]
    plaid.balances["pa-1"] = 0.0
    sync.run()  # held: the state changed
    assert seen == [["*"], ["*"]]
    sync.confirm_held(x["id"], accept=False)
    assert seen[-1] == ["*"] and len(seen) == 3


# ── FEED ───────────────────────────────────────────────────────────────────────────────


@ON
def test_feed_07_a_restored_sign_in_goes_back_to_fresh_without_booking(plaid):
    x = _synced(plaid, REPORTED)
    plaid.fail = "Plaid /accounts/get failed: ITEM_LOGIN_REQUIRED"
    sync.run()
    assert sync_state(x["id"])["status"] == "reconnect"
    plaid.fail = None
    sync.run()
    state = sync_state(x["id"])
    assert state["status"] == "fresh" and "error_code" not in state
    assert len(sync_rows()) == 1 and _value(x["id"]) == REPORTED


@ON
def test_feed_10_an_unchanged_total_goes_stale_after_a_week_by_design(plaid):
    x = _synced(plaid, REPORTED)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds")
    state = dict(ledger_service.accounts.find_by_id(x["id"])["value_sync"])
    ledger_service.accounts.update_by_id(x["id"], {"value_sync": {**state, "changed_at": old}})
    sync.run()  # a healthy tick, same total: changed_at is stamped only when the total changes
    view = sync_state(x["id"])
    assert view["status"] == "stale" and view["changed_at"] == old


@pytest.mark.parametrize("outward", [False, True])
def test_feed_09_the_tick_is_registered_only_where_outward_jobs_are_allowed(
    db_isolated, monkeypatch, outward
):
    import backend.scheduler as scheduler
    from tests.test_scheduler_no_ingest_job import _FakeAPScheduler

    fake = _FakeAPScheduler()
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", lambda: fake)
    monkeypatch.setattr(scheduler, "_outward_jobs_allowed", lambda: outward)
    original = scheduler._scheduler
    try:
        scheduler.start()
    finally:
        scheduler._scheduler = original
    ids = {j.get("id") for j in fake.jobs}
    assert ("linked_account_totals" in ids) is outward


def test_feed_09_a_qa_run_is_not_outward_unless_it_opts_in(monkeypatch):
    import backend.scheduler as scheduler
    from backend.core import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(onw_qa_mode=True))
    monkeypatch.delenv("ONW_QA_RUN_SCHEDULES", raising=False)
    assert scheduler._outward_jobs_allowed() is False
    monkeypatch.setenv("ONW_QA_RUN_SCHEDULES", "1")
    assert scheduler._outward_jobs_allowed() is True


def _via(monkeypatch, source, entry, **kwargs):
    fake = FakeProvider(balances={"ref-1": entry}, key=source, **kwargs)
    monkeypatch.setitem(providers.PROVIDERS, source, fake)
    return fake


@ON
def test_feed_12_simplefin_balance_string_is_applied_and_a_points_currency_is_refused(
    book, monkeypatch
):
    from backend.services.aggregation import simplefin

    wire = {"id": "ref-1", "name": "Brokerage", "balance": "123456.78", "currency": "USD"}
    common = simplefin._account_to_common(wire)
    assert (common["balance"], common["currency"]) == (123456.78, "USD")
    x = _account(name="SF", type_="investment", total=0.0)
    link_account(x["id"], "ref-1", source="simplefin")
    _via(monkeypatch, "simplefin", common)
    sync.run()
    assert _value(x["id"]) == REPORTED

    y = _account(name="SF points", type_="investment", total=5000.0)
    link_account(y["id"], "ref-2", source="simplefin")
    points = simplefin._account_to_common(
        {**wire, "id": "ref-2", "currency": "https://b.example/p"}
    )
    _via(monkeypatch, "simplefin", {**points, "provider_account_id": "ref-2"})
    fake = providers.PROVIDERS["simplefin"]
    fake.balances = {"ref-1": common, "ref-2": {**points, "provider_account_id": "ref-2"}}
    sync.run()
    assert sync_state(y["id"])["status"] == "currency_mismatch" and _value(y["id"]) == 5000.0


@ON
def test_feed_13_yodlee_current_balance_is_applied(book, monkeypatch):
    from backend.services.aggregation import yodlee

    wire = {
        "id": 5,
        "accountName": "Brokerage",
        "currentBalance": {"amount": 123456.78, "currency": "USD"},
    }
    common = yodlee._account_to_common(wire)
    assert (common["provider_account_id"], common["balance"]) == ("5", 123456.78)
    x = _account(name="Yodlee", type_="investment", total=0.0)
    link_account(x["id"], "5", source="yodlee")
    fake = FakeProvider(key="yodlee", balances={"5": common})
    monkeypatch.setitem(providers.PROVIDERS, "yodlee", fake)
    sync.run()
    assert _value(x["id"]) == REPORTED


@ON
def test_feed_14_alpaca_portfolio_value_string_is_applied_in_usd_only(eur_book, monkeypatch):
    from backend.services.aggregation import alpaca

    class _Keys:
        def keys(self):
            return ("key", "secret")

        def base_url(self):
            return "https://paper.example"

    monkeypatch.setattr(
        alpaca,
        "fetch_account",
        lambda *_a: {"account_number": "PA1", "portfolio_value": "123456.78", "cash": "0"},
    )
    window = alpaca.fetch_window(_Keys(), today(), today(), None, accounts_only=True)
    (common,) = window.accounts
    assert (common["balance"], common["currency"]) == ("123456.78", "USD")
    usd = _account(name="Alpaca USD", type_="investment", total=0.0)
    link_account(usd["id"], "PA1", source="alpaca")
    fake = FakeProvider(key="alpaca", balances={"PA1": common})
    monkeypatch.setitem(providers.PROVIDERS, "alpaca", fake)
    sync.run()
    assert _value(usd["id"]) == REPORTED

    eur = _account(name="Alpaca EUR", type_="investment", total=500.0, currency="EUR")
    ledger_service.accounts.update_by_id(usd["id"], {"external_ids": []})
    link_account(eur["id"], "PA1", source="alpaca")  # the same account, in another currency
    sync.run()
    assert sync_state(eur["id"])["status"] == "currency_mismatch" and _value(eur["id"]) == 500.0


@ON
def test_feed_15_ynab_milliunits_become_the_real_balance(book, monkeypatch):
    from backend.services.aggregation import ynab_plan

    payload = {
        "accounts": [
            {
                "id": "y-1",
                "name": "Brokerage",
                "type": "otherAsset",
                "on_budget": False,
                "closed": False,
                "balance": 123456780,
                "cleared_balance": 123456780,
            }
        ]
    }
    monkeypatch.setattr(ynab_plan, "_data", lambda store, path: payload)
    (acct,) = ynab_plan.accounts(None, "plan-1")
    assert acct["balance"] == 123456.78  # never 123,456,780
    x = _account(name="YNAB tracking", type_="investment", total=0.0)
    link_account(x["id"], "y-1", source="ynab")
    entry = {"provider_account_id": "y-1", "balance": acct["balance"], "currency": "USD"}
    monkeypatch.setitem(
        providers.PROVIDERS, "ynab", FakeProvider(key="ynab", balances={"y-1": entry})
    )
    sync.run()
    assert _value(x["id"]) == REPORTED


@ON
@pytest.mark.parametrize(
    "source", ["enable_banking", "gocardless", "lunch_flow", "lunch_money", "ibkr"]
)
def test_feed_16_a_connector_with_no_balance_in_its_account_list_is_not_eligible(book, source):
    x = _account(name="Linked", type_="investment", total=5000.0)
    ledger_service.accounts.update_by_id(
        x["id"], {"external_ids": [{"source": source, "ref": "r-1", "name": "Acct"}]}
    )
    assert av.balance_link(ledger_service.accounts.find_by_id(x["id"])) is None
    assert sync.eligible_accounts() == []
    view = hydrated(x["id"])
    assert view["value_sync"] is None and view["value_source"] == "total"
    assert sync.run_if_due() is False and _value(x["id"]) == 5000.0


class _FallbackStore:
    pass


def fallback_window(store, since, before, account, *, accounts_only=False):
    fallback_window.seen = (since, before, account, accounts_only)
    from backend.services.aggregation.providers import FetchWindow

    return FetchWindow(accounts=[{"provider_account_id": "r", "balance": 1.5}], complete=False)


def test_feed_17_the_fallback_reads_the_accounts_only_window_for_today():
    provider = providers.Provider(
        key="simplefin",
        label="SimpleFIN",
        note="",
        lookback_days=14,
        _fetch=("tests.test_account_value_uat_hold_feed", "fallback_window"),
        _store=("tests.test_account_value_uat_hold_feed", "_FallbackStore"),
        _classify=("tests.test_account_value_uat_hold_feed", "_FallbackStore"),
    )
    out = provider.account_balances()
    assert out == {"accounts": [{"provider_account_id": "r", "balance": 1.5}], "complete": False}
    assert fallback_window.seen == (today(), today(), None, True)


def test_feed_18_a_failed_bank_is_marked_for_reconnect_and_the_other_still_answers(monkeypatch):
    from backend.db.plaid_config_store import bank_key
    from backend.services.aggregation import plaid

    def post(store, path, payload):
        if payload["access_token"] == "tok-b":
            raise ValidationError("Plaid failed", plaid_error_code="ITEM_LOGIN_REQUIRED")
        return {
            "accounts": [
                {
                    "account_id": "pa-1",
                    "type": "investment",
                    "balances": {"current": 10.0, "iso_currency_code": "USD"},
                }
            ]
        }

    class _Store:
        reconnect = None

        def items(self):
            return [
                {"item_id": "item-a", "access_token": "tok-a", "institution_name": "A"},
                {"item_id": "item-b", "access_token": "tok-b", "institution_name": "B"},
            ]

        def mark_reconnect(self, *, needed, ok):
            _Store.reconnect = (set(needed), set(ok))

    monkeypatch.setattr(plaid, "_post", post)
    out = plaid.fetch_balances(_Store())
    assert out["complete"] is False and [a["provider_account_id"] for a in out["accounts"]] == [
        "pa-1"
    ]
    assert _Store.reconnect == ({bank_key("item-b")}, {bank_key("item-a")})


@ON
@pytest.mark.parametrize(
    ("ago", "runs"),
    [
        (timedelta(hours=5, minutes=59), False),
        (timedelta(hours=6), True),
        (timedelta(hours=6, minutes=1), True),
    ],
    ids=["5h59", "6h00", "6h01"],
)
def test_feed_19_the_tick_runs_at_six_hours_and_not_before(plaid, ago, runs):
    x = linked()
    start = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    assert sync.run_if_due(start) is True  # the first read; state saved
    calls = plaid.calls
    assert sync.run_if_due(start + ago) is runs
    assert plaid.calls == calls + (1 if runs else 0)
    assert _value(x["id"]) == REPORTED and len(rows("Total from Plaid")) == 1
