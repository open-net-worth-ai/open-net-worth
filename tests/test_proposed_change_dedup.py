"""In-turn proposed-change dedup (backlog bug — chat emits DUPLICATE proposed-change cards).

A model can emit two ``tool_use`` blocks proposing the SAME change in ONE response (e.g.
two ``create_automation`` for the same Automation with slightly different details). The assistant
tool loop used to emit a ``proposed_change`` card for EACH, so the user saw two confirm
cards for one proposal. The fix dedups by a ``(type, identity)`` signature within the turn:
the SECOND same-identity proposal emits NO card and the model is told it already proposed it,
while LEGITIMATELY-DISTINCT proposals (different agents / two distinct positions) still each
get their own card.

Offline + isolated: the provider + ``execute_tool`` are stubbed (no LLM, no network).

Run:
    .venv/bin/python3 -m pytest tests/test_proposed_change_dedup.py -v
"""

from __future__ import annotations

import asyncio
import json


class _ScriptedProvider:
    """Emits a fixed list of tool_use calls on the FIRST stream, then plain text after (so the
    assistant tool loop terminates)."""

    def __init__(self, tool_calls):
        self._tool_calls = tool_calls
        self._turn = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self._turn += 1
        if self._turn == 1:
            for i, (name, inp) in enumerate(self._tool_calls):
                yield {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "stop_reason": "end_turn"}


def _drive(monkeypatch, tool_calls, change_for):
    """Run one assistant.run turn with a scripted provider and a stubbed execute_tool that
    returns the proposed change ``change_for(name, inp)`` (None = no proposal). Returns the list
    of proposed_change events emitted and the list of tool_result contents fed back to the model.
    """
    import backend.ai.assistant as assistant

    async def fake_execute_tool(name, inp, tool_use_id=None):
        change = change_for(name, inp)
        return json.dumps({"proposed": bool(change)}), change

    monkeypatch.setattr(assistant, "get_provider", lambda: _ScriptedProvider(tool_calls))
    monkeypatch.setattr(assistant, "execute_tool", fake_execute_tool)
    # Treat all tools as already loaded: this test exercises the in-turn proposal dedup,
    # not the interactive discovery gate (which would otherwise bounce a non-core tool's
    # first blind call before it can produce a proposed_change).
    monkeypatch.setattr(assistant, "_active_tools", lambda: list(assistant.TOOL_SCHEMAS))

    proposed: list[dict] = []
    results: list[str] = []

    async def _go():
        try:
            async for ev in assistant.run(
                session_id="test-dedup",
                new_messages=[{"role": "user", "text": "go"}],
                system="s",
            ):
                if ev.get("type") == "proposed_change":
                    proposed.append(ev["change"])
            # Pull the tool_result contents the model was fed from the saved session.
            for msg in assistant.SESSIONS.get("test-dedup", []):
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") == "tool_result":
                            results.append(part.get("content", ""))
        finally:
            assistant.SESSIONS.pop("test-dedup", None)

    asyncio.run(_go())
    return proposed, results


def _automation_create(name):
    return {
        "type": "automation_create",
        "automation": {"name": name},
        "display": {"name": name},
    }


def test_two_same_identity_proposals_emit_one_card(monkeypatch):
    """Two create_automation for the SAME Automation (different details) → ONE card."""
    calls = [
        ("create_automation", {"name": "Vantage Brokerage Trade Logger", "effort": "low"}),
        ("create_automation", {"name": "Vantage Brokerage Trade Logger", "effort": "high"}),
    ]

    def change_for(name, inp):
        return _automation_create(inp["name"])

    proposed, results = _drive(monkeypatch, calls, change_for)
    assert len(proposed) == 1
    assert proposed[0]["automation"]["name"] == "Vantage Brokerage Trade Logger"
    # The duplicate's tool_result tells the model not to re-propose.
    assert any("already proposed this change" in r for r in results)


def test_two_distinct_automations_emit_two_cards(monkeypatch):
    """Two create_automation for DIFFERENT Automations → TWO cards (not collapsed)."""
    calls = [
        ("create_automation", {"name": "Automation A"}),
        ("create_automation", {"name": "Automation B"}),
    ]

    def change_for(name, inp):
        return _automation_create(inp["name"])

    proposed, _ = _drive(monkeypatch, calls, change_for)
    assert len(proposed) == 2
    assert {c["automation"]["name"] for c in proposed} == {"Automation A", "Automation B"}


def test_two_distinct_positions_emit_two_cards(monkeypatch):
    """Two update_positions calls for DIFFERENT positions → TWO cards (identity in the
    sig). Uses the CANONICAL tool name deliberately — propose_position_update is now an
    ALIAS (2026-07-19 propose_*/apply_* merge), and this test's fake ``_active_tools()``
    (a literal snapshot of TOOL_SCHEMAS, no alias awareness) would otherwise trip the
    discovery gate's write-bounce on the alias's first use in a turn, which is a SEPARATE,
    already-covered mechanism (test_screen_commands.py / _is_known_tool), not what this
    dedup test is about."""
    calls = [
        ("update_positions", {"position_id": "p1"}),
        ("update_positions", {"position_id": "p2"}),
    ]

    def change_for(name, inp):
        return {
            "type": "update",
            "position_id": inp["position_id"],
            "changes": {"quantity": 1},
            "display": {},
        }

    proposed, _ = _drive(monkeypatch, calls, change_for)
    assert len(proposed) == 2
    assert {c["position_id"] for c in proposed} == {"p1", "p2"}


def test_two_same_position_proposals_emit_one_card(monkeypatch):
    """Two updates to the SAME position in one response → ONE card. Uses the CANONICAL
    tool name — see test_two_distinct_positions_emit_two_cards's note on why the alias
    would trip an unrelated discovery-gate mechanism in this fake-``_active_tools`` setup."""
    calls = [
        ("update_positions", {"position_id": "p1"}),
        ("update_positions", {"position_id": "p1"}),
    ]

    def change_for(name, inp):
        return {
            "type": "update",
            "position_id": inp["position_id"],
            "changes": {"quantity": 1},
            "display": {},
        }

    proposed, results = _drive(monkeypatch, calls, change_for)
    assert len(proposed) == 1
    assert any("already proposed this change" in r for r in results)
