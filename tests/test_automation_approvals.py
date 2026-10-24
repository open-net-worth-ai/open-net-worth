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
from tests.auto_judge_support import parked_verdict, stub_judge


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


@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
def test_an_automation_inherits_a_global_always_allow_in_every_mode(monkeypatch, db_isolated, mode):
    """One value per tool (ADR 2026-10-11-permissions-are-one-value-per-tool): ``always_allow``
    means allow everywhere, unattended included, so a run uses a tool's global Allow like a chat
    does. Until then a ``manual`` Automation still paused on a tool granted in an unrelated chat;
    the mode now decides only what ASK means."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_payees", "always_allow")
    token = _bind(mode)
    try:
        assert gate.automation_gate_verdict("create_payees") == "allow"
    finally:
        reset_automation_approval(token)


def test_manual_gates_a_tool_set_to_ask_that_the_run_never_listed(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
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


def test_auto_follows_the_judges_verdict_for_every_tool_and_fails_closed(monkeypatch):
    """``auto`` has no name list: a bulk delete is decided by the judge like any other tool, and
    a call the judge has not spoken for (no verdict parked) reads ``gate``."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)

    token = _bind("auto")
    try:
        for tool in ("create_payees", "delete_transactions_where"):
            with parked_verdict("allow", tool):
                assert gate.automation_gate_verdict(tool) == "allow"
            with parked_verdict("ask", tool):
                assert gate.automation_gate_verdict(tool) == "gate"
            with parked_verdict(None, tool):
                assert gate.automation_gate_verdict(tool) == "gate"
        # a verdict is bound to the tool it judged: another tool's read finds none
        with parked_verdict("allow", "create_payees"):
            assert gate.automation_gate_verdict("delete_transactions_where") == "gate"
    finally:
        reset_automation_approval(token)


def test_skip_bypasses_approval_even_for_a_bulk_delete(monkeypatch):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    token = _bind("skip")
    try:
        assert gate.automation_gate_verdict("delete_transactions_where") == "allow"
    finally:
        reset_automation_approval(token)


@pytest.mark.parametrize(
    "automation_tool",
    ["create_automation", "update_automations", "delete_automations", "run_automation"],
)
def test_the_automation_tools_follow_the_mode_like_every_other_tool(
    monkeypatch, automation_tool, db_isolated
):
    """No exception: the tools that write Automations are judged in ``auto`` like any other Ask
    tool (allow runs, ask or no verdict is a card), gate in ``manual`` and run in ``skip``. What
    protects the owner is that the judge is TOLD an Automation call grants unattended access
    (test_auto_judge_consequence), not a rule that it never sees the call."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    for verdict, expected in (("allow", "allow"), ("ask", "gate"), (None, "gate")):
        token = _bind("auto")
        try:
            with parked_verdict(verdict, automation_tool):
                assert gate.automation_gate_verdict(automation_tool) == expected, verdict
                assert gate.automation_gate_verdict(automation_tool, stage_only=True) == "auto"
        finally:
            reset_automation_approval(token)
    for mode, expected in (("manual", "gate"), ("skip", "allow")):
        token = _bind(mode)
        try:
            assert gate.automation_gate_verdict(automation_tool) == expected, mode
        finally:
            reset_automation_approval(token)


@pytest.mark.parametrize(
    "policy_tool",
    ["create_automation", "update_automations", "delete_automations", "run_automation"],
)
@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
def test_an_explicit_grant_still_runs_a_policy_tool(monkeypatch, policy_tool, mode, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    token = _bind(mode, policy_tool)  # on this Automation's own Always allowed list
    try:
        assert gate.automation_gate_verdict(policy_tool) == "allow"
    finally:
        reset_automation_approval(token)
    set_tool_permission(CORE_CONNECTOR_ID, policy_tool, "always_allow")
    token = _bind(mode)
    try:
        assert gate.automation_gate_verdict(policy_tool) == "allow"
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
        return ["in_app"]

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


def _self_staging_preview_verdict(
    mode: str, tool_name: str, *always_allowed: str, judged: str | None = None
) -> bool:
    approval_token = _bind(mode, *always_allowed)
    call_token = registry._active_call.set({"invoked": tool_name, "canonical": tool_name})
    try:
        # A pending card is KEYED by the tool_use_id, so a call without one can never be staged
        # and is never promised a card. Dispatch always binds a real id before a handler runs
        # (and, in `auto`, parks the judge's verdict for it first — `judged`).
        with parked_verdict(judged, tool_name, "toolu_automation_test"):
            return registry.should_preview_write()
    finally:
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


def test_self_staging_tools_obey_the_same_manual_auto_skip_policy(automation_dispatch):
    assert _self_staging_preview_verdict("manual", "open_option") is True
    assert _self_staging_preview_verdict("manual", "open_option", "open_option") is False
    assert _self_staging_preview_verdict("auto", "open_option", judged="allow") is False
    assert _self_staging_preview_verdict("auto", "open_option", judged="ask") is True
    # No verdict parked (the judge failed or never ran) previews: fail closed.
    assert _self_staging_preview_verdict("auto", "open_option") is True
    assert _self_staging_preview_verdict("skip", "open_option") is False


def test_auto_executes_an_ordinary_sensitive_tool_at_dispatch(automation_dispatch, monkeypatch):
    judged: list[str] = []
    stub_judge(monkeypatch, "allow", judged)

    text, proposed = _dispatch_notify("auto")

    assert json.loads(text)["ok"] is True
    assert proposed is None
    assert automation_dispatch == [("Commitment due", ["telegram"])]
    assert judged == ["telegram_send_message"]


def _dispatch_bulk_delete(tool_use_id: str):
    token = _bind("auto")
    try:
        return asyncio.run(
            registry.dispatch(
                "delete_transactions_where",
                # `preview` keeps an allowed run from deleting anything.
                {"date_from": "2026-08-01", "preview": True},
                tool_use_id=tool_use_id,
            )
        )
    finally:
        reset_automation_approval(token)


def test_auto_judges_a_bulk_delete_like_any_other_tool_at_dispatch(
    automation_dispatch, monkeypatch
):
    """No name list: ``delete_transactions_where`` pauses when the judge asks and runs when it
    allows."""
    stub_judge(monkeypatch, "ask")
    text, proposed = _dispatch_bulk_delete("toolu_auto_ask")

    assert text is None
    assert proposed and proposed["grant_tool"] == "delete_transactions_where"

    stub_judge(monkeypatch, "allow")
    text, proposed = _dispatch_bulk_delete("toolu_auto_allow")

    assert proposed is None
    assert json.loads(text)["ok"] is True


def test_skip_executes_instead_of_returning_a_synthetic_skip_result(
    automation_dispatch, monkeypatch
):
    judged: list[str] = []
    stub_judge(monkeypatch, "ask", judged)

    text, proposed = _dispatch_notify("skip")

    assert json.loads(text)["ok"] is True
    assert proposed is None
    assert automation_dispatch == [("Commitment due", ["telegram"])]
    assert judged == [], "skip proceeds without judging anything"


# ── the Automation order: Allow runs, Ask is the mode's (ADR 2026-10-11) ───────────────────────
#
# A stored Ask no longer outranks an Automation's ``skip`` / ``auto`` mode: Ask means the same
# thing whoever wrote it, and the mode decides it. The full table, driven through the real
# dispatch, is ``tests/test_permission_order.py``; the tests below pin the verdict function.


def _verdict(tool: str, mode: str, *always_allowed: str, judged: str | None = None):
    token = _bind(mode, *always_allowed)
    try:
        with parked_verdict(judged, tool):
            return gate.automation_gate_verdict(tool)
    finally:
        reset_automation_approval(token)


def test_a_stored_ask_means_what_a_seeded_ask_means_in_a_run(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    seeded = [
        _verdict("create_account", "skip"),
        _verdict("create_account", "auto", judged="allow"),
    ]
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "needs_approval")

    assert (
        [
            _verdict("create_account", "skip"),
            _verdict("create_account", "auto", judged="allow"),
        ]
        == seeded
        == ["allow", "allow"]
    )
    assert _verdict("create_account", "manual") == "gate"
    assert _verdict("create_account", "auto") == "gate"  # no verdict: the judge failed closed


def test_the_automations_own_always_grant_runs_a_tool_the_owner_set_to_ask(
    monkeypatch, db_isolated
):
    """Step 3 of the order: this Automation's own Always allowed list runs an Ask tool in every
    mode — it was written by a human tapping Always on a card that showed exactly what would run."""
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "needs_approval")

    for mode in ("manual", "auto", "skip"):
        assert _verdict("create_account", mode, "create_account") == "allow", mode
    # …and with NO local grant, Ask is the mode's to decide.
    assert _verdict("create_account", "manual") == "gate"


def test_a_seeded_ask_follows_the_mode(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)

    assert gate.effective_level("create_account") == "needs_approval"  # the seed, no row
    assert _verdict("create_account", "skip") == "allow"
    assert _verdict("create_account", "auto", judged="allow") == "allow"
    assert _verdict("create_account", "auto") == "gate"  # no verdict yet: the judge decides
    assert _verdict("create_account", "manual") == "gate"


def test_an_explicit_always_allow_runs_in_every_run_mode(monkeypatch, db_isolated):
    monkeypatch.setattr(gate, "is_gated_tool", lambda _name: True)
    set_tool_permission(CORE_CONNECTOR_ID, "create_account", "always_allow")

    for mode in ("manual", "auto", "skip"):
        assert _verdict("create_account", mode) == "allow", mode


def test_an_unreadable_permission_store_raises_so_the_dispatch_gate_refuses(
    monkeypatch, db_isolated
):
    """Fail CLOSED: an unreadable table is neither permission nor the seed (which would run a tool
    the owner Blocked). ``effective_level`` raises and ``registry.dispatch`` refuses the call."""
    import asyncio
    import json

    from backend.ai.tools import registry

    monkeypatch.setattr(
        "backend.services.tool_permissions.stored_tool_level",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        gate.effective_level("create_account")
    text, proposed = asyncio.run(registry.dispatch("get_net_worth", {}, tool_use_id="toolu_unread"))
    assert proposed is None and "nothing ran" in json.loads(text)["error"]
