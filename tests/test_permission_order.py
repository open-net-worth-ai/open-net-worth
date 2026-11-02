"""The order of checks — every row of both tables, in every mode (ADR
``2026-10-11-permissions-are-one-value-per-tool``).

Chat:
  1. the tool's value is Block            -> refused
  2. the tool's value is Always allow     -> runs
  3. the tool's value is Ask              -> the conversation's mode (manual: a card, auto: the
                                             judge decides, skip: it runs)

Automation run:
  1. the tool's value is Block            -> refused
  2. on this Automation's Blocked list    -> refused ("not allowed in this Automation")
  3. on its Always allowed list           -> runs
  4. the tool's value is Always allow     -> runs
  5. the tool's value is Ask              -> the Automation's mode

The one hardcoded rule left: ``create_automation`` / ``update_automations`` / ``delete_automations``
/ ``run_automation`` are never put to the judge — in ``auto`` they are always a card; in
``manual`` / ``skip`` they behave like any other tool; this Automation's own Always allowed list
(step 3) and the tool's own Always allow (step 4) still win.

Everything here drives the REAL ``registry.dispatch`` (or the same predicates it consults, for the
four tools whose handlers would otherwise act), with the judge stubbed — no model, no network.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate_v2 as gate
from backend.ai.tools import registry
from backend.core.automation_runner import (
    AutomationApprovalContext,
    bind_automation_approval,
    reset_automation_approval,
)
from backend.core.conversation_approval import (
    ConversationApprovalContext,
    bind_conversation_approval,
    reset_conversation_approval,
)
from backend.core.tool_use_context import set_tool_use_id
from backend.services import tool_permissions_v2 as perms
from tests.auto_judge_support import parked_verdict, stub_judge

pytestmark = pytest.mark.flags(permissions_v2=True)


MODES = ("manual", "auto", "skip")
TOOL = "create_payees"  # an ordinary write: seeded Ask
ARGS = {"name": "Acme"}
CORE = perms.CORE_CONNECTOR_ID
# The tools that author or start an Automation. They used to be the one hardcoded exception
# (never judged in auto); they are ordinary Ask tools now, and these tests pin that.
AUTOMATION_TOOLS = [
    "create_automation",
    "delete_automations",
    "run_automation",
    "update_automations",
]

_ids = iter(f"toolu_order_{i}" for i in range(1, 100_000))


@pytest.fixture()
def surface(db_isolated, monkeypatch):
    """A card-capable surface, an empty permission table, and a judge that is never consulted
    unless a test stubs it."""
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "sess-order")
    judged: list[str] = []
    stub_judge(monkeypatch, "ask", judged)
    yield judged
    set_tool_use_id(None)


class _Bound:
    def __init__(self, bind, reset, context):
        self._bind, self._reset, self._context = bind, reset, context

    def __enter__(self):
        self._token = self._bind(self._context)

    def __exit__(self, *exc):
        self._reset(self._token)


def chat(mode: str) -> _Bound:
    return _Bound(
        bind_conversation_approval,
        reset_conversation_approval,
        ConversationApprovalContext(session_id="sess-order", mode=mode),
    )


def automation(mode: str, *, allowed=(), blocked=()) -> _Bound:
    return _Bound(
        bind_automation_approval,
        reset_automation_approval,
        AutomationApprovalContext(
            automation_id="auto_1",
            run_id="run_1",
            session_id="sess-order",
            mode=mode,
            always_allowed_tools=frozenset(allowed),
            blocked_tools=frozenset(blocked),
        ),
    )


async def dispatch(tool: str = TOOL, args=None):
    """``(outcome, text, proposed)``: outcome is ``card`` / ``refused`` / ``ran``."""
    text, proposed = await registry.dispatch(tool, args or ARGS, tool_use_id=next(_ids))
    if text is None:
        return "card", text, proposed
    if '"blocked_by"' in text:
        return "refused", text, proposed
    return "ran", text, proposed


# ── Chat ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_chat_1_block_is_refused_in_every_mode(surface, mode):
    perms.set_tool_permission(CORE, TOOL, "blocked")
    with chat(mode):
        outcome, text, _ = await dispatch()

    assert outcome == "refused"
    assert json.loads(text)["blocked_by"] == "tool_permission"
    assert surface == []  # never put to the judge


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_chat_2_always_allow_runs_in_every_mode(surface, mode):
    perms.set_tool_permission(CORE, TOOL, "always_allow")
    with chat(mode):
        outcome, _, _ = await dispatch()

    assert outcome == "ran"
    assert surface == []  # Allow is never put to the judge either


@pytest.mark.asyncio
async def test_chat_3_ask_in_manual_is_a_card(surface):
    perms.set_tool_permission(CORE, TOOL, "needs_approval")
    with chat("manual"):
        outcome, _, proposed = await dispatch()

    assert outcome == "card" and proposed["grant_tool"] == TOOL
    assert surface == []


@pytest.mark.asyncio
async def test_chat_3_ask_in_skip_runs(surface):
    perms.set_tool_permission(CORE, TOOL, "needs_approval")
    with chat("skip"):
        outcome, _, _ = await dispatch()

    assert outcome == "ran" and surface == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "outcome"), [("allow", "ran"), ("ask", "card")])
async def test_chat_3_ask_in_auto_is_the_judges_to_decide(surface, monkeypatch, verdict, outcome):
    stub_judge(monkeypatch, verdict, surface)
    with chat("auto"):
        got, _, proposed = await dispatch()  # seeded Ask: no row at all

    assert got == outcome
    assert surface == [TOOL]
    if outcome == "card":
        # Auto's card is the Manual card: the judge's reason rides on nothing the owner sees.
        assert proposed["reason"] == f"{TOOL} needs your approval before it runs."


@pytest.mark.asyncio
async def test_chat_3_ask_in_auto_with_a_failed_judge_is_a_card(surface, monkeypatch):
    async def broken(name, input_data):
        raise RuntimeError("provider down")

    from backend.ai import auto_judge

    monkeypatch.setattr(auto_judge, "judge_call", broken)
    with chat("auto"):
        outcome, _, _ = await dispatch()

    assert outcome == "card"


@pytest.mark.asyncio
async def test_a_stored_ask_and_a_seeded_ask_are_the_same_ask(surface, monkeypatch):
    """The old rule that a STORED needs_approval outranked skip is gone: Ask means the same
    everywhere, whoever wrote it."""
    perms.set_tool_permission(CORE, TOOL, "needs_approval")
    with chat("skip"):
        stored, _, _ = await dispatch()
    perms.reset_tool_permission(CORE, TOOL)
    with chat("skip"):
        seeded, _, _ = await dispatch()

    assert stored == seeded == "ran"


# ── Automation run ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_automation_1_global_block_is_refused_even_on_the_automations_allowed_list(
    surface, mode
):
    perms.set_tool_permission(CORE, TOOL, "blocked")
    with automation(mode, allowed=[TOOL]):
        outcome, text, _ = await dispatch()

    assert outcome == "refused"
    assert json.loads(text)["blocked_by"] == "tool_permission"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["blocked", "needs_approval", "always_allow"])
@pytest.mark.parametrize("mode", MODES)
async def test_automation_2_the_automations_blocked_list_refuses_whatever_the_global_value(
    surface, mode, value
):
    perms.set_tool_permission(CORE, TOOL, value)
    with automation(mode, blocked=[TOOL], allowed=[TOOL]):  # blocked is checked first
        outcome, text, proposed = await dispatch()

    assert outcome == "refused" and proposed is None
    body = json.loads(text)
    if value != "blocked":
        assert body["blocked_by"] == "automation"
        assert "not allowed in this Automation" in body["error"]
        # A result, not a crash: it tells the run to carry on without the tool.
        assert "carry on with the rest of the task" in body["error"]


@pytest.mark.asyncio
async def test_automation_2_other_tools_still_run_in_an_automation_that_blocks_one(surface):
    perms.set_tool_permission(CORE, "create_categories", "always_allow")
    with automation("manual", blocked=[TOOL]):
        outcome, _, _ = await dispatch("create_categories", {"names": ["Fun"]})

    assert outcome == "ran"


@pytest.mark.asyncio
async def test_automation_2_a_block_added_while_a_card_waited_refuses_the_redemption(surface):
    with automation("manual"):
        outcome, _, proposed = await dispatch()
    assert outcome == "card"

    with automation("manual", blocked=[TOOL]):
        text, _ = await registry.dispatch(
            TOOL,
            {**ARGS, gate.CONFIRM_TOKEN_KEY: proposed["tool_use_id"]},
            tool_use_id=proposed["tool_use_id"],
        )

    assert json.loads(text)["blocked_by"] == "automation"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_automation_3_its_own_always_allowed_list_runs_an_ask_tool(surface, mode):
    perms.set_tool_permission(CORE, TOOL, "needs_approval")
    with automation(mode, allowed=[TOOL]):
        outcome, _, _ = await dispatch()

    assert outcome == "ran"
    assert surface == []  # not even in auto


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_automation_4_a_global_always_allow_runs_in_every_mode(surface, mode):
    """Automations now INHERIT a global Allow: ``always_allow`` means allow everywhere,
    unattended included."""
    perms.set_tool_permission(CORE, TOOL, "always_allow")
    with automation(mode):
        outcome, _, _ = await dispatch()

    assert outcome == "ran" and surface == []


@pytest.mark.asyncio
async def test_automation_5_ask_in_manual_is_a_card(surface):
    with automation("manual"):
        outcome, _, proposed = await dispatch()

    assert outcome == "card" and proposed["automation_id"] == "auto_1"
    assert surface == []


@pytest.mark.asyncio
async def test_automation_5_ask_in_skip_runs(surface):
    with automation("skip"):
        outcome, _, _ = await dispatch()

    assert outcome == "ran" and surface == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "outcome"), [("allow", "ran"), ("ask", "card")])
async def test_automation_5_ask_in_auto_is_the_judges_to_decide(
    surface, monkeypatch, verdict, outcome
):
    stub_judge(monkeypatch, verdict, surface)
    with automation("auto"):
        got, _, _ = await dispatch()

    assert got == outcome and surface == [TOOL]


# ── The Automation tools are ordinary tools: no exception, in any mode ─────────────────


def _gates(tool: str) -> bool:
    set_tool_use_id("toolu_policy")
    return gate.should_gate_dispatch(tool)


def test_there_is_no_policy_tool_exception_left():
    assert not hasattr(gate, "AUTOMATION_POLICY_TOOLS")
    for tool in AUTOMATION_TOOLS:
        assert perms.seed_level(tool) == "needs_approval", tool


@pytest.mark.parametrize("tool", AUTOMATION_TOOLS)
@pytest.mark.parametrize("where", ["chat", "automation"])
@pytest.mark.parametrize(("verdict", "gated"), [("allow", False), ("ask", True)])
def test_automation_tools_in_auto_are_judged_like_any_other_tool(
    surface, tool, where, verdict, gated
):
    ctx = chat("auto") if where == "chat" else automation("auto")
    with ctx, parked_verdict(verdict, tool, "toolu_policy"):
        assert gate.auto_judgment_needed(tool) is True
        assert gate.should_gate_dispatch(tool) is gated


@pytest.mark.parametrize("tool", AUTOMATION_TOOLS)
@pytest.mark.parametrize("where", ["chat", "automation"])
def test_automation_tools_in_manual_ask_and_in_skip_run_like_any_other_tool(surface, tool, where):
    make = chat if where == "chat" else automation
    with make("manual"):
        assert _gates(tool) is True
    with make("skip"):
        assert _gates(tool) is False


@pytest.mark.parametrize("tool", AUTOMATION_TOOLS)
def test_an_entry_on_the_automations_own_always_allowed_list_wins_in_auto(surface, tool):
    with automation("auto", allowed=[tool]):
        assert _gates(tool) is False


@pytest.mark.parametrize("tool", AUTOMATION_TOOLS)
@pytest.mark.parametrize("where", ["chat", "automation"])
def test_an_automation_tool_set_to_always_allow_runs_and_block_refuses(surface, tool, where):
    ctx = chat if where == "chat" else automation
    perms.set_tool_permission(CORE, tool, "always_allow")
    with ctx("auto"):
        assert _gates(tool) is False
    perms.set_tool_permission(CORE, tool, "blocked")
    with ctx("auto"):
        assert gate.effective_level(tool) == "blocked"


@pytest.mark.asyncio
async def test_an_automation_tool_blocked_in_this_automation_is_refused_with_the_clear_result(
    surface,
):
    with automation("skip", blocked=["update_automations"]):
        outcome, text, _ = await dispatch("update_automations", {"updates": []})

    assert outcome == "refused"
    assert json.loads(text)["blocked_by"] == "automation"
