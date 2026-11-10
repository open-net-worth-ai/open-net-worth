"""The "i" on a row the Auto judge decided, allowed or asked: the verdict is stored with the
conversation, rides the live ``tool_done`` event, and comes back on the tool block after a reload
(``permissions_v2``). A call it asked about also gets the reason back on its answered card.
"""

from __future__ import annotations

import pytest

from backend.ai import assistant, auto_judge, turns
from backend.ai.auto_judge import JudgeVerdict
from backend.core.chat_session import clear_session_id, set_session_id
from backend.core.tool_use_context import set_tool_use_id
from backend.services import auto_approvals, tool_resolutions

SESSION = "auto-approval-hint-session"

_TRANSCRIPT = [
    {"role": "user", "content": "add a 4.50 coffee at ACME Cafe"},
    {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "call_judged", "name": "add_transactions", "input": {}},
            {"type": "tool_use", "id": "call_read", "name": "get_net_worth", "input": {}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_judged", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "call_read", "content": "{}"},
        ],
    },
    {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
]


@pytest.fixture(autouse=True)
def _clean_state():
    def clear():
        auto_judge._current.set(None)
        set_tool_use_id(None)
        clear_session_id()
        auto_approvals._live.clear()

    clear()
    yield
    clear()


@pytest.fixture
def transcript(monkeypatch, db_isolated):
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in _TRANSCRIPT])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )


def _hold(tool_use_id: str, name: str, verdict: JudgeVerdict) -> None:
    set_tool_use_id(tool_use_id)
    auto_judge._current.set((tool_use_id, name, verdict))


def _tool_blocks(messages):
    return {
        b["id"]: b for m in messages for b in (m.get("blocks") or []) if b.get("type") == "tool"
    }


def test_an_allow_that_lets_a_call_run_is_remembered_with_its_reason(db_isolated):
    set_session_id(SESSION)
    _hold(
        "call_1",
        "add_transactions",
        JudgeVerdict("allow", "Adds the coffee you asked for.", "claude_cli:sonnet"),
    )

    assert auto_judge.gate_verdict_for("add_transactions") == "allow"

    assert auto_approvals.for_session(SESSION) == {
        "call_1": {"reason": "Adds the coffee you asked for.", "model": "sonnet"}
    }


def test_an_ask_is_remembered_on_the_call_with_its_reason(db_isolated):
    """A card is deleted once it is answered; the verdict belongs to the call and outlives it."""
    set_session_id(SESSION)
    _hold(
        "call_1",
        "add_transactions",
        JudgeVerdict("ask", "Not what the owner asked for.", "claude_cli:sonnet"),
    )

    assert auto_judge.gate_verdict_for("add_transactions") == "gate"

    asked = {"reason": "Not what the owner asked for.", "model": "sonnet", "verdict": "ask"}
    assert auto_approvals.for_session(SESSION) == {"call_1": asked}
    assert auto_approvals.take_live("call_1") == asked


def test_no_verdict_in_hand_is_not_remembered_as_anything(db_isolated):
    set_session_id(SESSION)
    set_tool_use_id("call_1")

    assert auto_judge.gate_verdict_for("add_transactions") == "gate"

    assert auto_approvals.for_session(SESSION) == {}


def test_a_verdict_for_another_call_is_not_remembered_for_this_one(db_isolated):
    set_session_id(SESSION)
    _hold("call_1", "add_transactions", JudgeVerdict("allow", "fine"))
    set_tool_use_id("call_2")

    assert auto_judge.gate_verdict_for("add_transactions") == "gate"
    assert auto_approvals.for_session(SESSION) == {}


def test_the_live_tool_done_carries_the_verdict_once(db_isolated):
    auto_approvals.record(SESSION, "call_1", "Adds the coffee you asked for.", "claude_cli:sonnet")
    done = {"type": "tool_done", "id": "call_1", "result": "{}"}

    first = turns._with_auto_approval(done)
    again = turns._with_auto_approval(done)

    assert first["autoApproval"] == {"reason": "Adds the coffee you asked for.", "model": "sonnet"}
    assert "autoApproval" not in again
    assert "autoApproval" not in done


def test_a_tool_done_for_an_unjudged_call_is_untouched():
    done = {"type": "tool_done", "id": "call_read", "result": "{}"}

    assert turns._with_auto_approval(done) is done


def test_the_reason_is_defanged_because_the_model_wrote_it_from_the_arguments_and_kept_whole(
    db_isolated,
):
    hostile = "Fine\x1b[31m\x00 » ok " + "x" * 400

    auto_approvals.record(SESSION, "call_1", hostile, "claude_cli:sonnet")

    reason = auto_approvals.for_session(SESSION)["call_1"]["reason"]
    assert "\x1b" not in reason and "\x00" not in reason
    assert reason.endswith("x" * 400)


def test_calls_judged_together_are_all_remembered(db_isolated):
    for i in range(5):
        auto_approvals.record(SESSION, f"call_{i}", f"reason {i}")

    assert set(auto_approvals.for_session(SESSION)) == {f"call_{i}" for i in range(5)}


def test_the_live_buffer_is_bounded_when_calls_never_finish():
    for i in range(auto_approvals._LIVE_CAP + 10):
        auto_approvals.record("", f"call_{i}", "r")

    assert len(auto_approvals._live) == auto_approvals._LIVE_CAP
    assert auto_approvals.take_live("call_0") is None


@pytest.mark.flags(permissions_v2=True)
def test_after_a_reload_the_judged_call_keeps_its_reason_and_the_read_gets_nothing(transcript):
    auto_approvals.record(
        SESSION, "call_judged", "Adds the coffee you asked for.", "claude_cli:sonnet"
    )

    blocks = _tool_blocks(assistant.rehydrate_messages(SESSION))

    assert blocks["call_judged"]["autoApproval"] == {
        "reason": "Adds the coffee you asked for.",
        "model": "sonnet",
    }
    assert "autoApproval" not in blocks["call_read"]


@pytest.mark.flags(permissions_v2=False)
def test_with_the_flag_off_a_reload_shows_no_hint(transcript):
    auto_approvals.record(SESSION, "call_judged", "Adds the coffee you asked for.")

    blocks = _tool_blocks(assistant.rehydrate_messages(SESSION))

    assert "autoApproval" not in blocks["call_judged"]


def _cards(messages):
    return {
        b["change"]["id"]: b["change"]
        for m in messages
        for b in (m.get("blocks") or [])
        if b.get("type") == "change"
    }


def _asked(tool_use_id: str = "call_judged") -> None:
    auto_approvals.record(
        SESSION, tool_use_id, "A live order is not cheap to undo.", "claude_cli:sonnet", "ask"
    )


@pytest.mark.flags(permissions_v2=True)
@pytest.mark.parametrize(
    "answer", [tool_resolutions.APPROVED, tool_resolutions.ALWAYS_ALLOWED, tool_resolutions.DENIED]
)
def test_an_answered_card_keeps_why_auto_asked_after_a_reload(transcript, answer):
    _asked()
    tool_resolutions.record(SESSION, "call_judged", answer)

    messages = assistant.rehydrate_messages(SESSION)

    card = _cards(messages)["call_judged"]
    assert card["judge_reason"] == "A live order is not cheap to undo."
    assert card["resolution"] == answer
    assert _tool_blocks(messages)["call_judged"]["autoApproval"]["verdict"] == "ask"


@pytest.mark.flags(permissions_v2=True)
def test_a_call_asked_about_and_never_answered_keeps_its_reason_on_the_row(transcript):
    """Its card is gone with the turn (``abort_stale_pending``), which is no reason to lose why."""
    _asked()

    messages = assistant.rehydrate_messages(SESSION)

    assert _cards(messages) == {}
    assert _tool_blocks(messages)["call_judged"]["autoApproval"] == {
        "reason": "A live order is not cheap to undo.",
        "model": "sonnet",
        "verdict": "ask",
    }


@pytest.mark.flags(permissions_v2=True)
def test_a_card_answered_for_a_call_auto_allowed_or_never_judged_says_nothing_about_asking(
    transcript,
):
    auto_approvals.record(SESSION, "call_judged", "Adds the coffee you asked for.")
    tool_resolutions.record(SESSION, "call_judged", tool_resolutions.APPROVED)
    tool_resolutions.record(SESSION, "call_read", tool_resolutions.APPROVED)

    cards = _cards(assistant.rehydrate_messages(SESSION))

    assert "judge_reason" not in cards["call_judged"]
    assert "judge_reason" not in cards["call_read"]


@pytest.mark.flags(permissions_v2=False)
def test_with_the_flag_off_a_rebuilt_card_never_carries_a_stored_reason(transcript):
    """Flipping the flag off keeps the old code untouched, whatever v2 stored while it was on."""
    _asked()

    card = assistant._card_from_call(SESSION, {"id": "call_judged", "name": "add_transactions"}, "")

    assert "judge_reason" not in card


def test_a_connector_call_auto_asked_about_is_remembered_through_the_one_chain(db_isolated):
    """External tools bypass ``registry.dispatch``; they still reach the same verdict function."""
    from backend.ai.tools import _confirm_gate_v2 as gate
    from backend.core.conversation_approval import (
        ConversationApprovalContext,
        bind_conversation_approval,
        reset_conversation_approval,
    )

    name = "mcp__broker__create_order"
    set_session_id(SESSION)
    token = bind_conversation_approval(ConversationApprovalContext(SESSION, "auto"))
    try:
        _hold("call_1", name, JudgeVerdict("ask", "A live order.", "claude_cli:sonnet"))
        assert gate.external_decision(name) == "gate"
    finally:
        reset_conversation_approval(token)

    assert auto_approvals.for_session(SESSION)["call_1"]["verdict"] == "ask"
