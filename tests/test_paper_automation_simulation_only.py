"""A ``simulation_only`` Automation (the two paper templates) can never reach a real write, an
outbound tool or an external MCP tool — an MCP order tool included — whatever its instructions
say and whichever permission engine is on. It may read, write paper and keep its own notes.
The flag is fixed when the Automation is created: no update can switch it off."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import simulation_guard
from backend.ai.tools import execute_tool
from backend.core.automation_runner import (
    AutomationApprovalContext,
    bind_automation_approval,
    reset_automation_approval,
)
from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from tests.flags import flags


def _bound(simulation_only: bool, mode: str = "skip"):
    return bind_automation_approval(
        AutomationApprovalContext(
            automation_id="auto_paper",
            run_id="r1",
            session_id="s1",
            mode=mode,
            simulation_only=simulation_only,
        )
    )


def _call(name, inp):
    text, _ = asyncio.run(execute_tool(name, inp))
    return json.loads(text) if text else None


@pytest.mark.parametrize("v2", [True, False])
def test_a_real_write_is_refused_and_nothing_is_written(paper_db, v2):
    token = _bound(True)
    try:
        with flags(permissions_v2=v2):
            out = _call(
                "create_account",
                {"name": "Sneaky", "type": "cash", "currency": "USD", "initial_balance": 1},
            )
    finally:
        reset_automation_approval(token)
    assert out["refused_by"] == "simulation_only"
    assert AccountStore().all() == []


@pytest.mark.parametrize("v2", [True, False])
def test_paper_writes_and_reads_run_in_a_paper_automation(paper_db, v2):
    token = _bound(True)
    try:
        with flags(permissions_v2=v2):
            made = _call(
                "create_paper_portfolio", {"name": "Bot", "cash": 1000, "home_currency": "USD"}
            )
            placed = _call(
                "place_paper_orders",
                {
                    "portfolio": "Bot",
                    "orders": [{"asset": "inst:inst_acme", "side": "buy", "quantity": 1}],
                },
            )
            read = _call("get_paper_portfolios", {})
    finally:
        reset_automation_approval(token)
    assert made["portfolio"]["created_by"] == "automation:auto_paper"
    assert placed["results"][0]["order"]["origin"] == "automation:auto_paper"
    assert read["portfolios"][0]["name"] == "Bot"


@pytest.mark.parametrize(
    "name, allowed",
    [
        ("get_paper_results", True),
        ("place_paper_orders", True),
        ("delete_paper_portfolios", True),  # still paper; it asks by its own seed
        ("remember_fact", True),  # the workspace
        ("get_net_worth", True),  # a read
        ("add_transactions", False),
        ("create_account", False),
        ("set_followed_portfolios", False),
        ("create_automation", False),
        ("email_send_message", False),  # leaves the install
        ("telegram_send_message", False),
        ("run_code", False),
        ("mcp__alpaca__place_order", False),  # an external MCP order tool
        ("mcp__ibkr__place_order", False),
        ("no_such_tool", False),
    ],
)
def test_what_a_paper_automation_may_call(name, allowed):
    assert simulation_guard.allows(name) is allowed


def test_the_external_mcp_path_is_refused_before_any_engine():
    token = _bound(True)
    try:
        refusal = simulation_guard.refusal("mcp__alpaca__place_order")
    finally:
        reset_automation_approval(token)
    assert refusal is not None and "paper only" in json.loads(refusal[0])["error"]


def test_an_ordinary_automation_and_a_chat_are_untouched():
    assert simulation_guard.refusal("create_account") is None
    token = _bound(False)
    try:
        assert simulation_guard.refusal("mcp__alpaca__place_order") is None
    finally:
        reset_automation_approval(token)


def _automation(**extra):
    return {
        "name": "Paper strategy",
        "instructions": "Run the buy-zone rules on paper.",
        "trigger": {"kind": "manual"},
        **extra,
    }


def test_simulation_only_is_fixed_at_creation(paper_db):
    from backend.services.automations.runs import request_for_run
    from backend.services.automations.service import apply_automation_change

    created = apply_automation_change(
        {
            "op": "create",
            "automation": _automation(simulation_only=True, paper_portfolio_id="ppf_x"),
        }
    )
    assert created["simulation_only"] is True and created["paper_portfolio_id"] == "ppf_x"
    with pytest.raises(ValidationError, match="unknown automation fields"):
        apply_automation_change(
            {"op": "update", "id": created["id"], "changes": {"simulation_only": False}}
        )
    edited = apply_automation_change(
        {"op": "update", "id": created["id"], "changes": {"instructions": "Now trade for real."}}
    )
    assert edited["simulation_only"] is True
    request = request_for_run(edited, {"id": "run1", "session_id": "s1"})
    assert request.simulation_only is True


def test_an_automation_is_not_simulation_only_unless_created_so(paper_db):
    from backend.services.automations.service import apply_automation_change

    created = apply_automation_change({"op": "create", "automation": _automation()})
    assert created["simulation_only"] is False
