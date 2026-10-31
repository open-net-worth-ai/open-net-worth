"""Rebuilding the approval card when a conversation is reopened.

A gated tool_use is left result-less in the transcript until the human answers (RFC
2026-07-22), and confirm cards were never persisted — so reopening a parked Automation run
showed the tool pills, no card, and no way to answer it. The run could only be resumed by
the tab that happened to be open when it stalled.

But "no result" alone does NOT mean "waiting for you": a call whose process died mid-flight
looks identical. Live, a run killed by a server restart came back offering Allow/Deny for a
turn that no longer existed. So a card is rebuilt only when a gate says the call is really
parked — and there are two gates, each with its own record.
"""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from backend.ai import assistant

pytestmark = pytest.mark.flags(permissions_v2=True)


SESSION = "automation-run-run_test"

_TRANSCRIPT = [
    {"role": "user", "content": "do the thing"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Sending the summary."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "telegram_send_message",
                "input": {"message": "hi"},
            },
        ],
    },
]


@pytest.fixture
def transcript(monkeypatch):
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in _TRANSCRIPT])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})


@pytest.fixture
def no_staged_plan(monkeypatch):
    class _Store:
        def get_pending(self, _tool_use_id):
            return None

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", lambda: _Store())


@pytest.fixture
def not_an_automation(monkeypatch):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )


def _card(messages):
    return (messages[-1].get("proposedChanges") or [None])[0]


def test_a_call_staged_in_the_pending_store_rebuilds_its_card(
    transcript, not_an_automation, monkeypatch
):
    class _Store:
        def get_pending(self, tool_use_id):
            return {"state": "awaiting_approval"} if tool_use_id == "call_1" else None

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", lambda: _Store())
    card = _card(assistant.rehydrate_messages(SESSION))

    assert card["grant_tool"] == "telegram_send_message"
    assert card["tool_use_id"] == "call_1"
    assert card["display"]["arguments"] == {"message": "hi"}


def test_a_parked_automation_run_rebuilds_its_card(transcript, no_staged_plan, monkeypatch):
    """An Automation's ids never reach the pending store — the run row is its record, and
    requiring a staged entry hid the card on exactly the runs that needed one."""
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["call_1"]}),
    )
    assert _card(assistant.rehydrate_messages(SESSION))["tool_use_id"] == "call_1"


def test_a_parked_automation_runs_card_names_its_automation_so_always_allow_lands_on_it(
    transcript, no_staged_plan, monkeypatch
):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: (
            {"id": "automation_a"},
            {"status": "waiting", "pending_tool_ids": ["call_1"]},
        ),
    )
    assert _card(assistant.rehydrate_messages(SESSION))["automation_id"] == "automation_a"


def test_a_plain_conversations_card_names_no_automation(transcript, not_an_automation, monkeypatch):
    class _Store:
        def get_pending(self, _tool_use_id):
            return {"state": "awaiting_approval"}

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", lambda: _Store())
    assert "automation_id" not in _card(assistant.rehydrate_messages(SESSION))


def test_the_message_is_marked_awaiting_so_the_bubble_reads_as_unfinished(
    transcript, no_staged_plan, monkeypatch
):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["call_1"]}),
    )
    assert assistant.rehydrate_messages(SESSION)[-1]["awaitingApproval"] is True


def test_a_run_killed_mid_call_does_NOT_offer_to_approve_anything(
    transcript, no_staged_plan, monkeypatch
):
    """The regression case. A server restart leaves a result-less tool_use behind, identical in the
    transcript to a paused one — and the card offered to approve a turn that was gone."""
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "failed", "pending_tool_ids": []}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_finished_run_offers_nothing(transcript, no_staged_plan, monkeypatch):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "completed", "pending_tool_ids": []}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_waiting_run_waiting_on_a_DIFFERENT_call_does_not_claim_this_one(
    transcript, no_staged_plan, monkeypatch
):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({}, {"status": "waiting", "pending_tool_ids": ["some_other_id"]}),
    )
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_an_ordinary_conversation_is_untouched(transcript, no_staged_plan, not_an_automation):
    assert _card(assistant.rehydrate_messages(SESSION)) is None
    assert "awaitingApproval" not in assistant.rehydrate_messages(SESSION)[-1]


def test_a_completed_call_is_never_a_question(monkeypatch, no_staged_plan, not_an_automation):
    """A tool_use WITH its result is answered — the gate check should not even be reached."""
    monkeypatch.setattr(
        assistant,
        "_load_session",
        lambda _sid: [
            *_TRANSCRIPT,
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "sent"}],
            },
        ],
    )
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})

    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_a_broken_gate_lookup_fails_safe_with_no_card(transcript, monkeypatch):
    """Better a re-ask than an Allow button wired to nothing."""

    def _boom():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("backend.db.pending_plan_store.PendingPlanStore", _boom)
    monkeypatch.setattr("backend.services.automations.runs.automation_run_for_session", _boom)

    assert _card(assistant.rehydrate_messages(SESSION)) is None


# ── The run header ────────────────────────────────────────────────────────────
# The opening message renders as "Ran <name>" linking to the run. The ids come from the
# server; the panel must never parse them out of the session id.


def test_the_opening_message_carries_the_automation_name_and_ids(transcript, monkeypatch):
    # rehydrate_messages resolves the automation brief via _session_meta_entry (a direct
    # keyed lookup), not _read_index (a full-listing scan) — see assistant.py's own
    # automation_brief block.
    monkeypatch.setattr(
        assistant,
        "_session_meta_entry",
        lambda _sid: {"id": SESSION, "source": "automation", "title": "Log transactions"},
    )
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _sid: ({"id": "task-0fae2fb5"}, {"id": "run_a440", "status": "completed"}),
    )

    assert assistant.rehydrate_messages(SESSION)[0]["automationBrief"] == {
        "name": "Log transactions",
        "automationId": "task-0fae2fb5",
        "runId": "run_a440",
    }


def test_an_unresolvable_run_still_gets_a_header_just_not_a_link(transcript, monkeypatch):
    """Best-effort: losing the link is a smaller loss than losing the header."""
    monkeypatch.setattr(
        assistant,
        "_session_meta_entry",
        lambda _sid: {"id": SESSION, "source": "automation", "title": "Log transactions"},
    )
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )

    assert assistant.rehydrate_messages(SESSION)[0]["automationBrief"] == {
        "name": "Log transactions"
    }


def test_an_ordinary_conversation_gets_no_header(transcript, monkeypatch):
    monkeypatch.setattr(assistant, "_read_index", lambda: [{"id": SESSION, "source": "chat"}])

    assert "automationBrief" not in assistant.rehydrate_messages(SESSION)[0]


# ── An ANSWERED card comes back with the answer the owner gave ─────────────────────────────

_ANSWERED = [
    {"role": "user", "content": "do the thing"},
    {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "call_a", "name": "create_payees", "input": {"n": 1}},
            {"type": "tool_use", "id": "call_b", "name": "create_payees", "input": {"n": 2}},
            {"type": "tool_use", "id": "call_c", "name": "create_payees", "input": {"n": 3}},
            {"type": "tool_use", "id": "call_d", "name": "get_net_worth", "input": {}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "call_b", "content": "{}"},
            {"type": "tool_result", "tool_use_id": "call_c", "content": "declined"},
            {"type": "tool_result", "tool_use_id": "call_d", "content": "{}"},
        ],
    },
    {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
]


@pytest.fixture
def answered(monkeypatch, db_isolated):
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in _ANSWERED])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )


def _cards(messages):
    return {c["id"]: c for m in messages for c in (m.get("proposedChanges") or [])}


def test_each_answer_is_stored_and_comes_back_as_its_own_label(answered):
    from backend.services import tool_resolutions as tr

    tr.record(SESSION, "call_a", tr.APPROVED)
    tr.record(SESSION, "call_b", tr.ALWAYS_ALLOWED)
    tr.record(SESSION, "call_c", tr.DENIED)

    cards = _cards(assistant.rehydrate_messages(SESSION))

    assert {k: (v["status"], v["resolution"]) for k, v in cards.items()} == {
        "call_a": ("confirmed", "approved"),
        "call_b": ("confirmed", "always_allowed"),
        "call_c": ("dismissed", "denied"),
    }
    assert cards["call_a"]["grant_tool"] == "create_payees"
    assert cards["call_a"]["display"]["arguments"] == {"n": 1}


def test_a_call_nobody_answered_gets_no_card(answered):
    assert _cards(assistant.rehydrate_messages(SESSION)) == {}


def test_the_card_sits_right_after_its_tool_pill(answered):
    from backend.services import tool_resolutions as tr

    tr.record(SESSION, "call_b", tr.APPROVED)
    message = next(m for m in assistant.rehydrate_messages(SESSION) if m.get("blocks"))
    kinds = [(b["type"], (b.get("id") or b.get("change", {}).get("id"))) for b in message["blocks"]]
    assert kinds.index(("change", "call_b")) == kinds.index(("tool", "call_b")) + 1


def test_a_stored_answer_for_a_call_with_no_result_makes_no_answered_card(
    transcript, not_an_automation, no_staged_plan
):
    from backend.services import tool_resolutions as tr

    tr.record(SESSION, "call_1", tr.APPROVED)
    assert _card(assistant.rehydrate_messages(SESSION)) is None


def test_the_resolve_route_stores_the_answer_only_for_a_call_that_is_waiting(
    monkeypatch, db_isolated
):
    """A stale or superseded tap must not leave a label claiming something ran."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import tool_resolutions as tr

    monkeypatch.setattr(assistant, "_is_awaiting_approval", lambda _sid, tid: tid == "call_wait")
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )
    monkeypatch.setattr(assistant, "resolve_pending_tool_use", _empty_stream)
    # No lifespan: booting the app would seed the starter skills into the shared skills dir.
    with nullcontext(TestClient(app)) as client:
        client.post(
            "/api/chat/sess-r/resolve",
            json={"tool_use_id": "call_wait", "decision": "approve", "always": True},
        )
        client.post(
            "/api/chat/sess-r/resolve", json={"tool_use_id": "call_gone", "decision": "deny"}
        )
        client.post(
            "/api/chat/sess-r/resolve", json={"tool_use_id": "call_wait", "decision": "nope"}
        )

    assert tr.for_session("sess-r") == {"call_wait": tr.ALWAYS_ALLOWED}


async def _empty_stream(*_a, **_k):
    return
    yield  # pragma: no cover — makes this an async generator


def test_resolution_for_maps_the_three_answers():
    from backend.services import tool_resolutions as tr

    assert tr.resolution_for("approve", always=False) == tr.APPROVED
    assert tr.resolution_for("approve", always=True) == tr.ALWAYS_ALLOWED
    assert tr.resolution_for("deny", always=True) == tr.DENIED  # nothing is stored on a Deny
    assert tr.resolution_for("shrug", always=False) is None
