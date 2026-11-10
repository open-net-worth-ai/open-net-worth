from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai import _confirm_gate as surface_policy
from backend.ai.automation_runner_adapter import _canonical_always_allowed_tools
from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import registry
from backend.core.automation_runner import (
    AutomationApprovalContext,
    bind_automation_approval,
    reset_automation_approval,
)
from backend.services.tool_permissions import CORE_CONNECTOR_ID, set_tool_permission


def _bind(mode: str, *always_allowed: str):
    return bind_automation_approval(
        AutomationApprovalContext(
            automation_id="automation_a",
            run_id="run_a",
            session_id="automation-run-a",
            mode=mode,
            always_allowed_tools=frozenset(always_allowed),
        )
    )


def test_automation_surface_can_render_the_existing_approval_card():
    assert surface_policy.can_render_card("automation") is True


def test_automation_policy_is_local_and_manual_honours_its_local_grant(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    # A REAL global permission (not a mock of a retired predicate — `granted_scope` no
    # longer exists post the 2026-08-14 one-permission-store merge). It must never be
    # consulted at all for this decision: Automation-local authority wins BEFORE the global
    # table, so the assertion below is proving non-consultation, not agreement with it.
    set_tool_permission(CORE_CONNECTOR_ID, "create_payees", "always_allow")
    token = _bind("manual", "create_payees")
    try:
        assert gate.automation_gate_verdict("create_payees") == "allow"
    finally:
        reset_automation_approval(token)


def test_manual_gates_an_ungranted_sensitive_tool_even_with_a_global_grant(
    monkeypatch, db_isolated
):
    """The mirror of the test above: a real GLOBAL ``always_allow`` — the kind granted in an
    unrelated chat — must not leak into a run that never listed the tool as locally allowed.
    Manual still pauses on it, because the Automation's own (empty) always_allowed list is
    decides here, not the global table."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_payees", "always_allow")
    token = _bind("manual")
    try:
        assert gate.automation_gate_verdict("create_payees") == "gate"
    finally:
        reset_automation_approval(token)


def test_automation_approval_context_does_not_leak_after_its_turn(monkeypatch):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    token = _bind("skip")
    assert gate.automation_gate_verdict("create_payees") == "allow"

    reset_automation_approval(token)

    assert gate.automation_gate_verdict("create_payees") is None


def test_notify_permission_descriptor_becomes_an_automation_local_tool_grant():
    always_allowed = _canonical_always_allowed_tools(
        [
            {
                "src": "Open Net Worth",
                "desc": "Send this commitment reminder.",
                "capability": "notify",
                "tools": ["telegram_send_message", "webhook_send_message"],
            }
        ]
    )

    assert always_allowed == frozenset({"telegram_send_message", "webhook_send_message"})


def test_auto_approves_ordinary_sensitive_tools_but_not_hardline_tools(monkeypatch):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)

    token = _bind("auto")
    try:
        assert gate.automation_gate_verdict("create_payees") == "allow"
        assert gate.automation_gate_verdict("delete_transactions_where") == "gate"
    finally:
        reset_automation_approval(token)


def test_skip_bypasses_approval_even_for_hardline_tools(monkeypatch):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    token = _bind("skip")
    try:
        assert gate.automation_gate_verdict("delete_transactions_where") == "allow"
    finally:
        reset_automation_approval(token)


@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
@pytest.mark.parametrize(
    "policy_tool",
    ["create_automation", "update_automations", "delete_automations", "run_automation"],
)
def test_a_run_can_never_write_the_policy_that_governs_runs(
    monkeypatch, mode, policy_tool, db_isolated
):
    """Security regression — privilege escalation, found by review before ship.

    ``auto`` allowed every non-hardline tool, and writing an Automation is a non-hardline
    tool. So an auto-mode Automation reading UNTRUSTED input (an email body it was built to
    ingest) could be steered to create_automation(permission_mode="skip") and run_automation it —
    and that child run cleared the hardline tier, reaching delete_transactions_where with no
    card ever shown. The same call could rewrite a DIFFERENT, manual-mode Automation's
    approval mode, crossing the owner's own boundary permanently.

    ``skip`` remains the owner's choice to make on an Automation the owner configured. It is
    not a choice a run may make for itself, in any mode, with any standing grant — a REAL
    global ``always_allow`` included, which is what makes "even with a global grant" true
    here rather than merely asserted against a mocked predicate.
    """
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, policy_tool, "always_allow")
    token = _bind(mode, policy_tool)  # even listed as an Always-allowed local grant
    try:
        assert gate.automation_gate_verdict(policy_tool) == "gate"
    finally:
        reset_automation_approval(token)


def test_the_model_cannot_smuggle_a_standing_permission_through_an_update(monkeypatch):
    """``always_allowed`` is the Automation's standing list of tools that skip the card,
    written ONLY by an actual
    approval tap. It is absent from the tool schema — but NestedInput emits no
    ``additionalProperties`` and dispatch strips that keyword anyway, so the schema was never
    the defence. A whitelist in the handler is."""
    from backend.ai.tools import automations as automation_tools

    captured: list[dict] = []

    def _fake_apply(change):
        captured.append(change)
        return {"id": change["id"]}

    monkeypatch.setattr("backend.services.automations.service.apply_automation_change", _fake_apply)
    asyncio.run(
        automation_tools.update_automations(
            {
                "updates": [
                    {
                        "id": "automation_a",
                        "name": "Renamed",
                        "always_allowed": [
                            {"src": "x", "desc": "d", "tools": ["delete_transactions_where"]}
                        ],
                    }
                ]
            }
        )
    )

    assert captured, "update never reached the service"
    assert captured[0]["changes"] == {"name": "Renamed"}
    assert "always_allowed" not in captured[0]["changes"]


@pytest.fixture()
def automation_dispatch(tmp_path, monkeypatch):
    calls: list[tuple[str, object]] = []

    async def fake_notify(message: str, *, channels=None, title=None):
        calls.append((message, channels))
        return {"in_app": {"sent": 1}}

    monkeypatch.setattr("backend.paths.PENDING_PLANS_JSON", tmp_path / "pending_plans.json")
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "automation")
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "automation-run-a")
    monkeypatch.setattr("backend.services.notify.notify", fake_notify)
    monkeypatch.setattr(registry, "_record_call", lambda *args, **kwargs: None)
    return calls


def _dispatch_notify(mode: str, *always_allowed: str):
    token = _bind(mode, *always_allowed)
    try:
        return asyncio.run(
            registry.dispatch(
                "telegram_send_message",
                {"message": "Commitment due"},
                tool_use_id=f"toolu_{mode}_telegram_send_message",
            )
        )
    finally:
        reset_automation_approval(token)


def _self_staging_preview_verdict(mode: str, tool_name: str, *always_allowed: str) -> bool:
    from backend.core.tool_use_context import set_tool_use_id

    approval_token = _bind(mode, *always_allowed)
    call_token = registry._active_call.set({"invoked": tool_name, "canonical": tool_name})
    # A pending card is KEYED by the tool_use_id, so a call without one can never be staged
    # and is never promised a card. Dispatch always binds a real id before a handler runs.
    set_tool_use_id("toolu_automation_test")
    try:
        return registry.should_preview_write()
    finally:
        set_tool_use_id(None)
        registry._active_call.reset(call_token)
        reset_automation_approval(approval_token)


def test_manual_pauses_an_ungranted_notification_at_dispatch(automation_dispatch):
    text, proposed = _dispatch_notify("manual")

    assert text is None
    assert proposed and proposed["grant_tool"] == "telegram_send_message"
    assert automation_dispatch == []


def test_a_manual_local_grant_executes_at_dispatch_without_leaking_global_authority(
    automation_dispatch,
):
    text, proposed = _dispatch_notify("manual", "telegram_send_message")

    assert json.loads(text)["ok"] is True
    assert proposed is None
    assert automation_dispatch == [("Commitment due", ["telegram"])]


def test_self_staging_tools_obey_the_same_manual_auto_skip_policy(automation_dispatch, monkeypatch):
    monkeypatch.setattr(surface_policy, "is_hardline_blocked", lambda _name: False)

    assert _self_staging_preview_verdict("manual", "open_option") is True
    assert _self_staging_preview_verdict("manual", "open_option", "open_option") is False
    assert _self_staging_preview_verdict("auto", "open_option") is False

    monkeypatch.setattr(surface_policy, "is_hardline_blocked", lambda _name: True)
    assert _self_staging_preview_verdict("auto", "open_option") is True
    assert _self_staging_preview_verdict("skip", "open_option") is False


def test_auto_executes_an_ordinary_sensitive_tool_at_dispatch(automation_dispatch):
    text, proposed = _dispatch_notify("auto")

    assert json.loads(text)["ok"] is True
    assert proposed is None
    assert automation_dispatch == [("Commitment due", ["telegram"])]


def test_auto_still_pauses_a_hardline_tool_at_dispatch(automation_dispatch):
    token = _bind("auto")
    try:
        text, proposed = asyncio.run(
            registry.dispatch(
                "delete_transactions_where",
                {"date_from": "2026-08-01"},
                tool_use_id="toolu_auto_hardline",
            )
        )
    finally:
        reset_automation_approval(token)

    assert text is None
    assert proposed and proposed["grant_tool"] == "delete_transactions_where"


def test_skip_executes_instead_of_returning_a_synthetic_skip_result(
    automation_dispatch, monkeypatch
):
    monkeypatch.setattr(surface_policy, "is_hardline_blocked", lambda _name: True)

    text, proposed = _dispatch_notify("skip")

    assert json.loads(text)["ok"] is True
    assert proposed is None
    assert automation_dispatch == [("Commitment due", ["telegram"])]


# ── deny > ask > allow, with no specificity tiebreak (owner decision, 2026-08-27) ──────────
#
# An explicitly STORED `needs_approval` outranks both the Automation's own standing
# `always_allowed` grant and its `skip`/`auto` mode — the same precedence the owner's
# reference model (Claude Code's) uses, and the same shape `blocked` already had.
#
# The load-bearing distinction is EXPLICIT vs DEFAULTED. `needs_approval` is also what an
# undecided tool falls back to, so keying this on `effective_level` would fire for nearly
# every tool and silently turn `skip`/`auto`/`always_allowed` into no-ops. The tests below
# pin BOTH directions, because only the pair proves the rule is the narrow one.


def _verdict(tool: str, mode: str, *always_allowed: str):
    token = _bind(mode, *always_allowed)
    try:
        return gate.automation_gate_verdict(tool)
    finally:
        reset_automation_approval(token)


def test_an_explicit_ask_gates_even_in_a_skip_run(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "needs_approval")

    assert _verdict("create_account", "skip") == "gate"
    assert _verdict("create_account", "auto") == "gate"


def test_the_automations_own_always_grant_outranks_an_explicit_global_ask(monkeypatch, db_isolated):
    """Contradictory intent — a standing global *ask me* and a per-Automation *stop asking
    me* for the same tool — resolved toward the AUTOMATION.

    Specificity breaks this tie, deliberately unlike the mode case above. The local grant is
    written by a human tapping *Always* on a confirm card that showed exactly what would run,
    for one named Automation; the global level is a standing default set elsewhere, at
    another time, about every caller at once. The reverse order was implemented first and
    rejected on 2026-08-27 once the truth table made its consequence visible: it made a real
    tap on a real card silently do nothing, with no way for the owner to tell.
    """
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "needs_approval")

    assert _verdict("create_account", "skip", "create_account") == "allow"
    assert _verdict("create_account", "manual", "create_account") == "allow"
    # …and with NO local grant the global ask still wins over the mode.
    assert _verdict("create_account", "skip") == "gate"


def test_a_policy_tool_still_gates_even_with_a_local_grant(monkeypatch, db_isolated):
    """The one thing a local grant must NEVER buy. `always_allowed` now sits above the
    explicit-ask check, so this pins that it did not also climb above the self-escalation
    guard — a run that could grant itself `update_automations` could rewrite its own mode."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)

    assert _verdict("update_automations", "skip", "update_automations") == "gate"


def test_an_UNDECIDED_tool_is_untouched_by_the_rule(monkeypatch, db_isolated):
    """THE guard on the guard.

    `needs_approval` is the DEFAULT for every undecided gated tool. If this rule keyed on the
    effective level instead of a stored row, every one of them would gate and `skip`/`auto`
    would do nothing at all — the modes would still exist and silently stop working. Nothing
    is stored here, so the mode must still decide.
    """
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)

    assert gate.effective_level("create_account") == "needs_approval"  # defaulted, not stored
    assert _verdict("create_account", "skip") == "allow"
    assert _verdict("create_account", "auto") == "allow"
    assert _verdict("create_account", "manual") == "gate"


def test_an_explicit_always_allow_still_runs_in_a_skip_run(monkeypatch, db_isolated):
    """The rule is about *ask*, not about every stored level: an owner who explicitly said
    `always_allow` gets `always_allow`. (A `manual` run still pauses on it — that older,
    deliberate property is covered above and is unchanged by this.)"""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "always_allow")

    assert _verdict("create_account", "skip") == "allow"


def test_an_unreadable_permission_store_gates_rather_than_allows(monkeypatch, db_isolated):
    """Fail CLOSED — the direction every other read in this file fails in."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    monkeypatch.setattr(
        gate, "_stored_level", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert _verdict("create_account", "skip") == "gate"
