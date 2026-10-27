"""The permission gate is consumed at DISPATCH, so coverage is structural (E4/E5).

The defect these pin: ``should_gate`` had exactly TWO call sites (``add_transactions`` and
``update_transactions``) while ``is_gated_tool`` said True for 163 tools. Executed with the
gate armed and no grants, ``create_automation`` and ``create_account`` returned ``ok:true`` and
WROTE their stores with no card, and ``email_send_message`` sent real mail — the one effect in this
codebase that cannot be undone.

Every test here drives the REAL ``registry.dispatch``, never the predicate alone: a predicate
that returns True while nothing consumes it is exactly the bug.

RFC 2026-07-22 (approval-as-pending-tool_use) replaced the confirm_token RE-CALL with a
tool_use_id-keyed PAUSE: a gated dispatch now returns ``(None, proposed_change)`` — no fake
tool_result — and the change carries ``tool_use_id`` (not a token) for the frontend/connector
to bind the card to. Redemption is SERVER-ONLY: it still arrives as
``{"confirm_token": tool_use_id}`` on a re-dispatch (the internal channel
``assistant.resolve_pending_tool_use`` uses), so these tests simulate that server-side
redemption directly via ``registry.dispatch`` — never a model-initiated re-call.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import registry


@pytest.fixture()
def armed(db_isolated, monkeypatch):
    """Gate armed, card-capable surface, EMPTY permission table.

    Every real call site constructs ToolPermissionStore() fresh with engine=None (never
    cached), so db_isolated's engine redirect alone gives an isolated, empty table —
    no class-level substitution needed.
    """
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "sess-gate")
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)


_next_id = iter(f"toolu_test_{i}" for i in range(1, 100_000))


async def _dispatch(name, args, tool_use_id=None):
    """Drive ``registry.dispatch`` with a bound ``tool_use_id`` — a fresh one per call unless
    the caller passes one explicitly (to simulate the SAME paused call being resolved)."""
    return await registry.dispatch(name, args, tool_use_id=tool_use_id or next(_next_id))


# ── E4: the 161 tools that never asked ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        (
            "create_automation",
            {
                "name": "Watch for x",
                "instructions": "Review new transactions whose description contains x.",
                "trigger": {"kind": "manual"},
            },
        ),
        (
            "create_account",
            {
                "name": "X",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 0,
            },
        ),
        ("create_payees", {"name": "Acme"}),
    ],
)
async def test_a_gated_write_stages_a_card_instead_of_executing(armed, tool, args):
    text, proposed = await _dispatch(tool, args, tool_use_id="toolu_stage")
    # PAUSE, not a fake result: no tool_result at all (RFC 2026-07-22 §4.2).
    assert text is None
    assert proposed and proposed["type"] == "tool_permission"
    # `grant_tool` (not `tool`) is what makes both the web card and the connector path render
    # the four-way Allow once / This session / Always / Deny buttons — they key on that key's
    # PRESENCE, so getting the name wrong yields a card with no grant options at all.
    assert proposed["grant_tool"] == tool
    # The card binds to the SAME tool_use_id the pending record is keyed on — never a minted
    # token the model has to carry.
    assert proposed["tool_use_id"] == "toolu_stage"
    assert "confirm_token" not in proposed
    # D1, live incident 2026-07-20: a card missing `session_id` posts `session_id: undefined`
    # to `POST /api/grants`, and a session-scoped grant against no id is silently refused —
    # the tab reported success while nothing was ever stored. Asserted on the REAL card
    # `stage_generic` produced (the `armed` fixture stubs `get_session_id` above), not a
    # fixture that hands the field to itself.
    assert proposed["session_id"] == "sess-gate"


@pytest.mark.asyncio
async def test_email_send_message_is_gated_and_never_reaches_smtp(armed, monkeypatch):
    """E5. ``email_send_message`` has no ``mutates=`` tag (no local domain to refetch), so a
    ``bool(mutates)``-keyed rule would miss it — ``EXTERNAL_EFFECT_TOOLS`` is what catches
    it, and this asserts the predicate is actually CONSUMED, not merely correct."""
    import smtplib

    def boom(*a, **k):
        raise AssertionError("SMTP was contacted for a call the user never approved")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)

    text, proposed = await _dispatch(
        "email_send_message", {"to": "x@example.com", "subject": "s", "body": "b"}
    )
    assert text is None
    assert proposed["type"] == "tool_permission"


@pytest.mark.asyncio
async def test_a_read_is_never_gated(armed):
    text, proposed = await _dispatch("list_accounts", {})
    assert proposed is None
    assert text is not None
    assert "pending_confirmation" not in text


# ── Grants short-circuit the gate ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_granted_tool_executes_with_no_card(armed):
    from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission

    set_tool_permission(CORE_CONNECTOR_ID, "create_automation", "always_allow")
    text, proposed = await _dispatch(
        "create_automation",
        {
            "name": "Watch for x",
            "instructions": "Review new transactions whose description contains x.",
            "trigger": {"kind": "manual"},
        },
    )
    assert proposed is None
    assert text is not None
    assert "pending_confirmation" not in text


@pytest.mark.asyncio
async def test_yolo_short_circuits_every_tool(armed):
    from backend.services.tool_permissions import CORE_CONNECTOR_ID, YOLO_TOOL, set_tool_permission

    set_tool_permission(CORE_CONNECTOR_ID, YOLO_TOOL, "always_allow")
    text, proposed = await _dispatch("create_payees", {"name": "Acme"})
    assert proposed is None
    assert text is not None
    assert "pending_confirmation" not in text


# ── The confirm half: stored args, not the caller's ───────────────────────────


@pytest.mark.asyncio
async def test_the_confirm_executes_the_STORED_args_not_the_resent_ones(armed):
    """The security property, unchanged from the ledger gate: a card showing one thing cannot
    commit another, however the caller re-states it on the redeeming call."""
    _, proposed = await _dispatch(
        "create_payees", {"name": "Approved Co"}, tool_use_id="toolu_confirm_stored"
    )
    tool_use_id = proposed["tool_use_id"]

    text, _ = await _dispatch(
        "create_payees",
        {"name": "Substituted Evil Co", "confirm_token": tool_use_id},
        tool_use_id=tool_use_id,
    )
    assert "Approved Co" in text
    assert "Substituted Evil Co" not in text


@pytest.mark.asyncio
async def test_a_token_is_single_use(armed):
    _, proposed = await _dispatch(
        "create_payees", {"name": "Once Co"}, tool_use_id="toolu_single_use"
    )
    tool_use_id = proposed["tool_use_id"]
    await _dispatch(
        "create_payees",
        {"name": "Once Co", "confirm_token": tool_use_id},
        tool_use_id=tool_use_id,
    )

    text, _ = await _dispatch(
        "create_payees",
        {"name": "Once Co", "confirm_token": tool_use_id},
        tool_use_id=tool_use_id,
    )
    assert json.loads(text)["ok"] is False
    assert "could not be applied" in text or "expired" in text or "already-used" in text


@pytest.mark.asyncio
async def test_an_unknown_token_refuses_rather_than_executing_the_raw_args(armed):
    """A token that does not resolve must never degrade into an ungated write."""
    text, _ = await _dispatch(
        "create_payees", {"name": "Smuggled Co", "confirm_token": "not-a-real-tool-use-id"}
    )
    assert json.loads(text)["ok"] is False


# ── A call the handler would refuse never stages ──────────────────────────────

_NO_TYPE_TRIGGER = {
    "name": "Friday spending check",
    "instructions": "Check whether spending is on track for the month and send a short note.",
    "trigger": {"kind": "schedule", "time": "18:00", "day": "fri"},
}


@pytest.mark.asyncio
async def test_an_invalid_automation_trigger_is_refused_before_any_card(armed):
    """Demo incident 2026-10-26: a schedule trigger with no 'type' was STAGED, the user tapped
    Allow once, and only then did the handler refuse it — an approval that could only ever run
    the refusal. The error must reach the model at once, with no pending record to approve."""
    from backend.db.automation_store import AutomationStore
    from backend.db.pending_plan_store import PendingPlanStore

    text, proposed = await _dispatch(
        "create_automation", _NO_TYPE_TRIGGER, tool_use_id="toolu_bad_trigger"
    )

    # A real result, not the (None, change) pause the turn loop logs as tool_awaiting_approval.
    assert proposed is None
    assert text is not None
    assert PendingPlanStore().get_pending("toolu_bad_trigger") is None
    body = json.loads(text)
    assert body["ok"] is False
    assert "a schedule trigger needs a 'type' key" in body["error"]
    assert AutomationStore().all() == []


@pytest.mark.asyncio
async def test_a_precheck_that_crashes_refuses_rather_than_running_ungated(armed, monkeypatch):
    """`_permission_gate` fails OPEN on an exception, so one escaping the precheck would run
    the write with no card at all. The precheck's failure must stay a refusal."""
    from backend.db.automation_store import AutomationStore

    def boom(_args):
        raise RuntimeError("precheck exploded")

    monkeypatch.setattr(registry, "tool_precheck", lambda _name: boom)
    text, proposed = await _dispatch(
        "create_automation",
        {**_NO_TYPE_TRIGGER, "trigger": {"kind": "manual"}},
        tool_use_id="toolu_precheck_crash",
    )

    assert proposed is None
    assert json.loads(text) == {"ok": False, "error": "precheck exploded"}
    assert AutomationStore().all() == []


# ── Structural coverage: the point of moving the gate to dispatch ─────────────


def test_every_tool_needing_approval_is_reached_by_the_gate(monkeypatch):
    """The regression guard. Before E4 this would have reported 161 unreachable tools.

    A tool is covered if dispatch gates it OR it stages its own (richer, resolved) card.
    Nothing may be in neither set.
    """
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)
    from backend.ai import _confirm_gate as policy

    uncovered = [
        name
        for name, t in registry._REGISTRY.items()
        if policy.needs_approval(name, writes=bool(t.mutates))
        and not gate.is_gated_tool(name)
        and not gate.self_stages(name)
    ]
    assert uncovered == []


def test_self_staging_tools_are_detected_from_the_registry_flag_not_a_hand_list():
    """``self_stages`` reads the registry's own ``self_stages=`` declaration (RFC 2026-07-22
    §4.6+) — NOT a schema sniff any more, since ``add_transactions``/``update_transactions``
    stopped advertising ``confirm_token`` in their schema once redemption became server-only,
    so a schema sniff could no longer tell them apart from an ordinary gated tool."""
    assert gate.self_stages("add_transactions") is True
    assert gate.self_stages("update_transactions") is True
    assert gate.self_stages("create_automation") is False
    assert gate.self_stages("email_send_message") is False


# ── The approval-model's own predicate, pure (ported from the retired
# tests/test_tool_grants.py — these never touched the grant/permission STORE, only
# `_confirm_gate.needs_approval`/`is_gated_tool`, so the merge that deleted `ToolGrantStore`
# left them unaffected in substance; they moved here because their old home imported it) ──


def test_write_tools_need_approval():
    from backend.ai import _confirm_gate as policy

    assert policy.needs_approval("add_transactions", writes=True) is True


def test_read_tools_never_need_approval():
    from backend.ai import _confirm_gate as policy

    assert policy.needs_approval("get_transactions", writes=False) is False


def test_email_send_message_needs_approval_despite_declaring_no_mutated_domain():
    """The gap a bool(mutates) predicate misses — it sends mail that cannot be unsent."""
    from backend.ai import _confirm_gate as policy

    assert policy.needs_approval("email_send_message", writes=False) is True


def test_every_no_domain_write_tool_has_a_gating_verdict():
    """A new NO_DOMAIN_WRITE_TOOLS entry must not ship without deciding if it is gated.

    ``email_send_message`` is gated (real outbound SMTP). The rest are sandboxed or turn-local, and
    each is named here so adding another fails until someone rules on it.

    Ruling for add_run_tasks / update_run_task (2026-08-02): UNGATED. They write the running
    Automation's own task checklist — turn-local, no store, no external effect, and the
    thing they report on is a run the owner already approved. A card there would ask for
    permission to draw a progress bar.
    """
    from backend.ai import _confirm_gate as policy
    from tests.test_tool_domains import NO_DOMAIN_WRITE_TOOLS

    ungated = {
        "find_tools",
        "activate_skill",
        "run_script",
        "run_code",
        "run_screen_command",
        "add_run_tasks",
        "update_run_task",
    }
    assert set(NO_DOMAIN_WRITE_TOOLS) == policy.EXTERNAL_EFFECT_TOOLS | ungated


def test_the_write_set_is_read_from_the_registry_not_a_second_list():
    """The classification source is each tool's own mutates= tag — the same one
    tests/test_tool_domains.py enforces. If this drifts, the two would silently disagree."""
    from backend.ai import _confirm_gate as policy
    from backend.ai.tools.registry import mutating_tool_domains

    tagged = mutating_tool_domains()
    assert policy.needs_approval("add_transactions", writes=bool(tagged.get("add_transactions")))
    assert not policy.needs_approval("get_portfolio", writes=bool(tagged.get("get_portfolio")))


def test_the_write_set_is_ARMED_and_reads_are_still_free():
    """Pins the armed state (the RFC's sequencing precondition — the Permissions surface
    must exist before the gate flips — is long since met) and that reads stay free."""
    assert gate.GATE_ALL_WRITES is True
    assert gate.is_gated_tool("get_transactions") is False
    assert gate.is_gated_tool("get_portfolio") is False


def test_arming_the_write_set_gates_writes_and_leaves_reads_alone(monkeypatch):
    monkeypatch.setattr(gate, "GATE_ALL_WRITES", True)
    assert gate.is_gated_tool("add_transactions") is True
    assert gate.is_gated_tool("email_send_message") is True
    assert gate.is_gated_tool("get_transactions") is False
    assert gate.is_gated_tool("get_portfolio") is False
