"""Lucius's YNAB reads: the plans the token sees, one plan's accounts and budget, and plan_id on
the account list and the fetch. The connector holds only the token; the plan is named per call.
Driven against the YNAB stub, through the real tool registry."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.errors import ValidationError
from backend.db.ynab_config_store import YnabConfigStore
from backend.services.aggregation import ynab as ynab_service
from tests import ynab_stub


@pytest.fixture
def stub(db_isolated, monkeypatch):
    server, base_url, _calls = ynab_stub.serve()
    monkeypatch.setattr(ynab_service, "API_BASE", base_url)
    YnabConfigStore().update({"access_token": ynab_stub.TOKEN, "enabled": True})
    yield server
    server.shutdown()


def run(name: str, args: dict) -> dict:
    text, proposed = asyncio.run(execute_tool(name, args))
    assert proposed is None
    return json.loads(text)


def test_list_plans_names_every_plan_the_token_sees(stub):
    plans = run("ynab_list_plans", {})["plans"]
    assert [p["id"] for p in plans] == [ynab_stub.PLAN]
    assert plans[0]["name"] and plans[0]["currency"]


def test_get_plan_reads_accounts_and_this_months_budget(stub):
    result = run("ynab_get_plan", {})
    assert result["plan"]["id"] == ynab_stub.PLAN
    names = {a["name"] for a in result["accounts"]}
    assert "Checking" in names and all("balance" in a for a in result["accounts"])
    budget = result["budget"]
    assert budget["month"] and "ready_to_assign" in budget
    assert budget["categories"] and all(c["group"] for c in budget["categories"])
    assert not any(c["hidden"] for c in budget["categories"])


def test_get_plan_refuses_a_plan_the_token_cannot_see(stub):
    with pytest.raises(ValidationError, match="No YNAB plan"):
        run("ynab_get_plan", {"plan_id": "not-a-plan"})


def test_list_accounts_lists_the_plans_real_accounts_with_balances(stub):
    """It used to list only accounts with a transaction dated today, so most plans read empty."""
    result = run("ynab_list_accounts", {"plan_id": ynab_stub.PLAN})
    assert result["status"] == "ok"
    assert {a["name"] for a in result["accounts"]} >= {"Checking", "Savings"}
    assert all(a["balance"] is not None for a in result["accounts"])


def test_fetch_transactions_reads_the_named_plan(stub):
    result = run("ynab_fetch_transactions", {"plan_id": ynab_stub.PLAN, "since": "2000-01-01"})
    assert result["status"] == "ok" and result["transactions"]


def test_without_a_token_the_reads_say_how_to_connect(db_isolated):
    with pytest.raises(ValidationError, match="Connectors → YNAB"):
        run("ynab_list_plans", {})
