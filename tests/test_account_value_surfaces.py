"""Brokerage total / Live holdings on every surface that reads or writes it (ADR 2026-11-10):
the REST routes, Lucius's tools, Plaid's free balance read and the scheduler. Synthetic data."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import accounts as account_tools
from backend.api.ledger import router as ledger_router
from backend.core.errors import ValidationError
from backend.services import account_value as av
from backend.services.aggregation import plaid
from tests import test_account_value as base
from tests.test_account_value import _account, _add_holding, _instrument, _value

book = base.book  # the shared isolated-book fixture


@pytest.fixture
def client(book):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _tool(fn, payload):
    text, _ = asyncio.run(fn(payload))
    return json.loads(text)


# ── REST ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.flags(investment_value_source=True)
def test_rest_create_reads_back_brokerage_total_and_switches_with_a_preview(client):
    made = client.post(
        "/api/accounts",
        json={
            "name": "Old 401(k)",
            "type": "retirement",
            "currency": "USD",
            "initial_balance": 8000,
            "initial_balance_date": "2026-01-01",
        },
    ).json()
    assert made["value_source"] == "total"
    _add_holding(made["id"], _instrument())
    listed = {a["id"]: a for a in client.get("/api/accounts").json()}
    assert listed[made["id"]]["balance"] == 4980.0  # the untracked part
    preview = client.get(
        f"/api/accounts/{made['id']}/value-switch", params={"to": "holdings"}
    ).json()
    assert preview["adjustment"] == -4980.0 and preview["value_after"] == 3020.0
    switched = client.put(f"/api/accounts/{made['id']}", json={"value_source": "holdings"}).json()
    assert (
        switched["value_source"] == "holdings" and switched["value_switch"]["adjustment"] == -4980.0
    )
    assert _value(made["id"]) == 3020.0


@pytest.mark.flags(investment_value_source=True)
def test_rest_reconcile_takes_the_real_total(client):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    out = client.post(f"/api/accounts/{acct['id']}/reconcile", json={"real_total": 9100}).json()
    assert out["ok"] and out["balance"] == 6080.0
    assert _value(acct["id"]) == 9100.0


@pytest.mark.flags(investment_value_source=True)
def test_rest_reconcile_total_is_refused_on_a_cash_account(client):
    cash = _account(name="Checking", type_="cash", total=100.0)
    res = client.post(f"/api/accounts/{cash['id']}/reconcile", json={"real_total": 50})
    assert res.status_code == 422  # ValidationError → 422 (core.errors.to_http_status)


@pytest.mark.flags(investment_value_source=False)
def test_rest_flag_off_refuses_value_source_and_confirm(client):
    acct = _account()
    assert (
        client.put(f"/api/accounts/{acct['id']}", json={"value_source": "holdings"}).status_code
        == 422
    )
    res = client.post(f"/api/accounts/{acct['id']}/value-sync/confirm", json={"accept": True})
    assert res.status_code == 422  # ValidationError → 422 (core.errors.to_http_status)
    assert "value_source" not in client.get(f"/api/accounts/{acct['id']}").json()


# ── Lucius ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.flags(investment_value_source=True)
def test_list_accounts_tells_lucius_the_untracked_part_is_not_cash(book):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    out = _tool(account_tools.list_accounts, {"ids": [acct["id"]]})
    (row,) = out["accounts"]
    assert row["value_source"] == "total"
    assert row["value_breakdown"] == {
        "tracked_holdings": 3020.0,
        "untracked": 4980.0,
        "total": 8000.0,
        "currency": "USD",
    }


@pytest.mark.flags(investment_value_source=True)
def test_reconcile_tool_refuses_a_balance_and_takes_a_total(book):
    acct = _account()
    refused = _tool(
        account_tools.reconcile_accounts, {"account_id": acct["id"], "real_balance": 10}
    )
    assert refused["ok"] is False and "real_total" in refused["error"]
    dry = _tool(
        account_tools.reconcile_accounts,
        {"account_id": acct["id"], "real_total": 8500, "dry_run": True},
    )
    assert dry["ok"] and dry["delta"] == 500.0 and dry["real_total"] == 8500.0
    _tool(account_tools.reconcile_accounts, {"account_id": acct["id"], "real_total": 8500})
    assert _value(acct["id"]) == 8500.0


@pytest.mark.flags(investment_value_source=True)
def test_update_accounts_switches_and_reports_what_it_cleared(book):
    acct = _account()
    _add_holding(acct["id"], _instrument())
    out = _tool(
        account_tools.update_accounts,
        {"updates": [{"account_id": acct["id"], "value_source": "holdings"}]},
    )
    assert out["ok"] and out["account"]["value_switch"]["adjustment"] == -4980.0


@pytest.mark.flags(investment_value_source=True)
def test_create_account_tool_defaults_to_brokerage_total(book):
    out = _tool(
        account_tools.create_account,
        {"name": "Brokerage", "type": "investment", "currency": "USD", "initial_balance": 1234.56},
    )
    assert out["ok"] and out["account"]["value_source"] == "total"


@pytest.mark.flags(investment_value_source=False)
def test_flag_off_reconcile_tool_is_unchanged(book):
    acct = _account()
    out = _tool(account_tools.reconcile_accounts, {"account_id": acct["id"], "real_balance": 9000})
    assert out["ok"] and _value(acct["id"]) == 9000.0


# ── Plaid's free balance read ──────────────────────────────────────────────────────────


class _PlaidStore:
    def __init__(self):
        self.reconnect = None

    def items(self):
        return [
            {"item_id": "item-a", "access_token": "tok-a", "institution_name": "Acme Securities"},
            {"item_id": "item-b", "access_token": "tok-b", "institution_name": "Other Bank"},
        ]

    def mark_reconnect(self, *, needed, ok):
        self.reconnect = (set(needed), set(ok))


def test_plaid_fetch_balances_reads_accounts_get_for_every_bank(monkeypatch):
    calls = []

    def fake_post(store, path, payload):
        calls.append((path, payload["access_token"]))
        if payload["access_token"] == "tok-b":
            raise ValidationError("Plaid failed", plaid_error_code="ITEM_LOGIN_REQUIRED")
        return {
            "accounts": [
                {
                    "account_id": "pa-1",
                    "name": "Brokerage",
                    "type": "investment",
                    "subtype": "brokerage",
                    "balances": {"current": 123456.78, "iso_currency_code": "USD"},
                }
            ]
        }

    monkeypatch.setattr(plaid, "_post", fake_post)
    store = _PlaidStore()
    out = plaid.fetch_balances(store)
    assert calls == [("/accounts/get", "tok-a"), ("/accounts/get", "tok-b")]
    assert out["complete"] is False
    (acct,) = out["accounts"]
    assert acct["provider_account_id"] == "pa-1" and acct["balance"] == 123456.78
    assert acct["account_type"] == "investment" and acct["institution"] == "Acme Securities"
    assert store.reconnect[0] and store.reconnect[1]


def test_plaid_fetch_balances_raises_when_every_bank_fails(monkeypatch):
    def fail(store, path, payload):
        raise ValidationError("could not reach Plaid: timeout")

    monkeypatch.setattr(plaid, "_post", fail)
    with pytest.raises(ValidationError):
        plaid.fetch_balances(_PlaidStore())


# ── scheduler ──────────────────────────────────────────────────────────────────────────


def test_scheduler_registers_the_linked_totals_tick(db_isolated, monkeypatch):
    import backend.scheduler as scheduler
    from tests.test_scheduler_no_ingest_job import _FakeAPScheduler

    fake = _FakeAPScheduler()
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", lambda: fake)
    monkeypatch.setattr(scheduler, "_outward_jobs_allowed", lambda: True)
    original = scheduler._scheduler
    try:
        scheduler.start()
    finally:
        scheduler._scheduler = original
    ids = {j.get("id") for j in fake.jobs}
    assert "linked_account_totals" in ids
    assert av.FLAG == "investment_value_source"
