"""Two-way screen contract — command manifest, run_screen_command, rehydrated chip."""

from __future__ import annotations

import json
import uuid

from backend.models.screen import ScreenCommand, ScreenCommandParam, ScreenSnapshot
from backend.services.screen_context import format_screen_context


def _snapshot(**kw) -> ScreenSnapshot:
    base = dict(id="plan", title="Plan — Outlook", facts={"Odds": "82%"})
    base.update(kw)
    return ScreenSnapshot(**base)


def test_format_renders_command_manifest():
    snap = _snapshot(
        commands=[
            ScreenCommand(
                name="plan.stage_whatif",
                description="Stage an ephemeral what-if.",
                params=[
                    ScreenCommandParam(name="retireAge", type="number", minimum=1, maximum=150),
                    ScreenCommandParam(
                        name="add_events", type="array", items="{label, amount, age}"
                    ),
                ],
                route="plan-outlook",
                live=True,
            ),
            ScreenCommand(name="plan.reset", description="Back to baseline.", route="plan-outlook"),
        ]
    )
    block = format_screen_context(snap)
    assert "run_screen_command" in block
    assert "plan.stage_whatif" in block
    assert "retireAge:number" in block
    # Array item shapes ride the param spec — the description truncates at 160 chars,
    # so a shape documented only there never reaches the model (Ferrari-purchase repro).
    assert "add_events:array<{label, amount, age}>" in block
    assert "screen not open" in block  # the live=False entry advertises parking


def test_format_drops_invalid_command_names():
    snap = _snapshot(commands=[ScreenCommand(name="Bad Name!", description="x")])
    block = format_screen_context(snap)
    assert "Bad Name!" not in block


def test_format_commands_only_snapshot_is_parked_catalog_not_on_screen():
    # The phone's Lucius tab replaces the screen: it sends the parked catalog with no content.
    # It must reach the model as drivable-but-parked, never as "currently on screen".
    snap = ScreenSnapshot(
        commands=[ScreenCommand(name="plan.stage_whatif", route="plan-outlook", live=False)]
    )
    block = format_screen_context(snap)
    assert "plan.stage_whatif" in block and "screen not open" in block
    assert "run_screen_command" in block
    assert "Currently on screen" not in block
    assert format_screen_context(ScreenSnapshot()) == ""


def test_command_manifest_capped_at_12():
    cmds = [ScreenCommand(name=f"t.c{i}") for i in range(20)]
    block = format_screen_context(_snapshot(commands=cmds))
    assert "t.c11" in block and "t.c12" not in block


def test_session_screen_commands_roundtrip_and_clear():
    from backend.ai import assistant

    sid = f"test-cmds-{uuid.uuid4().hex[:8]}"
    manifest = [{"name": "plan.reset", "params": [], "route": "plan-outlook", "live": True}]
    assistant.upsert_session_meta(sid, screen_commands=manifest)
    assert assistant.session_screen_commands(sid) == manifest
    assistant.upsert_session_meta(sid, screen_commands=[])
    assert assistant.session_screen_commands(sid) == []
    assistant.clear_session(sid)


# ── run_screen_command ────────────────────────────────────────────────────────

WHATIF_CMD = {
    "name": "plan.stage_whatif",
    "description": "stage",
    "route": "plan-outlook",
    "live": True,
    "params": [
        {"name": "retireAge", "type": "number", "required": False, "minimum": 1, "maximum": 150},
        {"name": "add_events", "type": "array"},
        {"name": "crash", "type": "boolean"},
    ],
}


def _bind_manifest(commands: list[dict]) -> str:
    from backend.ai import assistant
    from backend.core import chat_session

    sid = f"test-rsc-{uuid.uuid4().hex[:8]}"
    assistant.upsert_session_meta(sid, screen_commands=commands)
    chat_session.set_session_id(sid)
    return sid


def _unbind(sid: str) -> None:
    from backend.ai import assistant
    from backend.core import chat_session

    chat_session.clear_session_id()
    assistant.clear_session(sid)


async def _run(name: str, args: dict | None = None) -> dict:
    from backend.ai.tools.screen import run_screen_command

    payload: dict = {"name": name}
    if args is not None:
        payload["args"] = args
    text, proposed = await run_screen_command(payload)
    assert proposed is None
    return json.loads(text)


async def test_run_screen_command_dispatched():
    sid = _bind_manifest([WHATIF_CMD])
    try:
        out = await _run("plan.stage_whatif", {"retireAge": 45})
    finally:
        _unbind(sid)
    assert out["status"] == "dispatched"
    assert out["command"] == {"name": "plan.stage_whatif", "args": {"retireAge": 45}}


async def test_run_screen_command_parked_offers_route():
    sid = _bind_manifest([dict(WHATIF_CMD, live=False)])
    try:
        out = await _run("plan.stage_whatif", {"retireAge": 45})
    finally:
        _unbind(sid)
    assert out["status"] == "parked"
    assert out["open"] == {"route": "plan-outlook"}


async def test_run_screen_command_arg_validation():
    sid = _bind_manifest([WHATIF_CMD])
    try:
        assert "unknown args" in (await _run("plan.stage_whatif", {"nope": 1}))["error"]
        assert "must be number" in (await _run("plan.stage_whatif", {"retireAge": "45"}))["error"]
        assert "above maximum" in (await _run("plan.stage_whatif", {"retireAge": 400}))["error"]
        assert "must be boolean" in (await _run("plan.stage_whatif", {"crash": "yes"}))["error"]
        assert "unknown screen command" in (await _run("plan.nope"))["error"]
    finally:
        _unbind(sid)


async def test_run_screen_command_accepts_and_validates_object_args():
    command = {
        "name": "automations.review_draft",
        "description": "review",
        "route": "automations",
        "live": True,
        "params": [{"name": "automation", "type": "object", "required": True}],
    }
    sid = _bind_manifest([command])
    try:
        out = await _run(
            "automations.review_draft",
            {"automation": {"name": "Morning brief", "trigger": {"kind": "manual"}}},
        )
        assert out["status"] == "dispatched"
        invalid = await _run("automations.review_draft", {"automation": []})
        assert "must be object" in invalid["error"]
    finally:
        _unbind(sid)


async def test_run_screen_command_no_screen():
    from backend.core import chat_session

    chat_session.clear_session_id()
    out = await _run("plan.reset")
    assert out["status"] == "no_screen"


# ── Dispatch-loop SSE integration (REAL assistant.run, scripted provider) ─────


class _ScriptedProvider:
    """Emit per-turn scripted tool_use lists, then text — so the REAL dispatch loop
    runs end-to-end (discovery gate, execution, SSE emission) with no LLM/network."""

    def __init__(self, turns: list[list[tuple[str, dict]]]):
        self._turns = turns
        self._i = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        i = self._i
        self._i += 1
        if i < len(self._turns):
            for j, (name, inp) in enumerate(self._turns[i]):
                yield {"type": "tool_use", "id": f"t{i}-{j}", "name": name, "input": inp}
            yield {"type": "done", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "stop_reason": "end_turn"}

    async def complete(self, prompt, system="", model="", max_tokens=2048, source="complete"):
        return ""


def _run_loop(monkeypatch, turns, *, sid, stub_execute=None):
    """Drive one interactive assistant.run turn; return the full event list."""
    import asyncio

    import backend.ai.assistant as assistant

    if stub_execute is not None:
        monkeypatch.setattr(assistant, "execute_tool", stub_execute)
    monkeypatch.setattr(assistant, "get_provider", lambda *a, **k: _ScriptedProvider(turns))

    events: list[dict] = []

    async def _go():
        try:
            async for ev in assistant.run(
                session_id=sid, new_messages=[{"role": "user", "content": "go"}], system="s"
            ):
                events.append(ev)
        finally:
            assistant.SESSIONS.pop(sid, None)

    asyncio.run(_go())
    return events


def test_loop_emits_screen_command_sse(monkeypatch):
    from backend.ai import assistant

    sid = f"test-loop-sc-{uuid.uuid4().hex[:8]}"
    assistant.upsert_session_meta(sid, screen_commands=[WHATIF_CMD])
    try:
        call = ("run_screen_command", {"name": "plan.stage_whatif", "args": {"retireAge": 45}})
        # run_screen_command is not in the CORE offer → the discovery gate bounces the
        # first call (auto-load + "call it again"); the scripted retry then executes.
        events = _run_loop(monkeypatch, [[call], [call]], sid=sid)
    finally:
        assistant.clear_session(sid)
    sc = [e for e in events if e.get("type") == "screen_command"]
    assert len(sc) == 1
    assert sc[0]["status"] == "dispatched"
    assert sc[0]["command"] == {"name": "plan.stage_whatif", "args": {"retireAge": 45}}
    # An ephemeral screen command mutates nothing → no data_changed this turn.
    assert not any(e.get("type") == "data_changed" for e in events)


def test_loop_emits_deduped_data_changed_before_turn_done(monkeypatch):
    async def stub_execute(name, inp):
        return json.dumps({"ok": True}), None

    sid = f"test-loop-dc-{uuid.uuid4().hex[:8]}"
    # Both tools are in the CORE offer (no discovery-gate bounce) and carry overlapping
    # but unequal domain tags — the turn must emit ONE deduped, sorted domains list.
    # NB: this used to call `create_payee`, which the 2026-07-19 CORE diet demoted out of
    # the offer (2.4% session spread). A non-CORE tool is auto-loaded and BOUNCED on its
    # first call, so it never executes and contributes no domain — the assertion below
    # then failed for a reason that had nothing to do with the dedup it exists to guard.
    events = _run_loop(
        monkeypatch,
        [
            [
                ("add_transactions", {"amount": 1}),
                ("add_transactions", {"amount": 2}),
                ("import_transactions", {"rows": []}),
            ]
        ],
        sid=sid,
        stub_execute=stub_execute,
    )
    dc = [e for e in events if e.get("type") == "data_changed"]
    assert len(dc) == 1
    assert dc[0]["domains"] == ["ledger", "payees"]
    types = [e.get("type") for e in events]
    assert types.index("data_changed") < types.index("turn_done")


def test_loop_read_only_turn_emits_no_data_changed(monkeypatch):
    async def stub_execute(name, inp):
        return json.dumps({"rows": []}), None

    sid = f"test-loop-ro-{uuid.uuid4().hex[:8]}"
    events = _run_loop(
        monkeypatch, [[("get_transactions", {})]], sid=sid, stub_execute=stub_execute
    )
    assert not any(e.get("type") == "data_changed" for e in events)
    assert any(e.get("type") == "turn_done" for e in events)


# ── POST /api/chat end-to-end stash (hermetic — assistant.run spied out) ──────


def test_chat_endpoint_stashes_and_clears_command_manifest(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.ai.assistant as assistant
    import backend.ai.turns as turns
    import backend.api.chat as chat_mod

    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    captured: dict = {}

    async def fake_run(session_id, new_messages, system, **kw):
        captured["new_messages"] = new_messages
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    # Argument-shaping test, not the concurrent shape — see test_chat_mentions.py.
    # monkeypatch (not a bare set_inline_for_tests(True)) so this reverts even on failure.
    monkeypatch.setattr(turns, "_inline", True)
    app = FastAPI()
    app.include_router(chat_mod.router)
    client = TestClient(app)

    sid = f"test-stash-{uuid.uuid4().hex[:8]}"
    screen = {
        "id": "plan",
        "title": "Plan — Outlook",
        "commands": [
            {
                "name": "plan.stage_whatif",
                "description": "Stage a what-if.",
                "params": [{"name": "retireAge", "type": "number"}],
                "route": "plan-outlook",
                "live": True,
            },
            # A name the regex rejects must be dropped from the STASH too (the same
            # gate the prose renderer applies) — never silently invocable.
            {"name": "Bad Name!", "description": "x", "params": [], "route": "", "live": True},
        ],
    }
    r = client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "messages": [{"role": "user", "content": "what if I retire at 45?"}],
            "screen": screen,
        },
    )
    assert r.status_code == 202
    stashed = assistant.session_screen_commands(sid)
    assert [c["name"] for c in stashed] == ["plan.stage_whatif"]
    assert stashed[0]["live"] is True
    # The prose block advertises the command on the user turn.
    content = captured["new_messages"][-1]["content"]
    text = (
        content
        if isinstance(content, str)
        else "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    )
    assert "plan.stage_whatif" in text
    assert "run_screen_command" in text

    # A no-screen turn clears the stash — a stale manifest is never invocable.
    r2 = client.post(
        "/api/chat",
        json={"session_id": sid, "messages": [{"role": "user", "content": "thanks"}]},
    )
    assert r2.status_code == 202
    assert assistant.session_screen_commands(sid) == []
    assistant.clear_session(sid)


def test_rehydrate_appends_parked_chip_block():
    from backend.ai.assistant import _maybe_append_screen_command_block

    blocks: list = []
    parked = json.dumps(
        {
            "status": "parked",
            "command": {"name": "plan.stage_whatif", "args": {"retireAge": 45}},
            "open": {"route": "plan-outlook"},
        }
    )
    _maybe_append_screen_command_block(blocks, parked)
    assert blocks == [
        {
            "type": "screen_command",
            "command": {"name": "plan.stage_whatif", "args": {"retireAge": 45}},
            "open": {"route": "plan-outlook"},
        }
    ]
    # dispatched (live) commands are ephemeral — no block
    dispatched = json.dumps({"status": "dispatched", "command": {"name": "plan.reset", "args": {}}})
    _maybe_append_screen_command_block(blocks, dispatched)
    assert len(blocks) == 1
    _maybe_append_screen_command_block(None, parked)  # tolerates missing owner
    _maybe_append_screen_command_block(blocks, "not json")
    assert len(blocks) == 1
    # …except a connector setup: its card names the connector and carries Connect, so it stays
    # even when Connectors was already open and the form opened at once (owner, 2026-10-31).
    setup = json.dumps(
        {
            "status": "dispatched",
            "command": {"name": "connectors.open_setup", "args": {"id": "mcp-webull"}},
        }
    )
    _maybe_append_screen_command_block(blocks, setup)
    assert blocks[-1] == {
        "type": "screen_command",
        "command": {"name": "connectors.open_setup", "args": {"id": "mcp-webull"}},
        "open": {"route": "connectors"},
    }


def test_every_screen_block_is_recognized_and_hidden_from_display():
    # The commands-only block opened with a new header that the "[Currently on screen" filters
    # missed, so the phone Lucius tab's "Try again" bubble showed the whole command catalog.
    from backend.ai.assistant import _extract_user_text
    from backend.ai.smalltalk import is_smalltalk_turn
    from backend.core.untrusted import is_screen_context_block

    parked = ScreenSnapshot(
        commands=[ScreenCommand(name="plan.stage_whatif", route="plan-outlook", live=False)]
    )
    for block in (format_screen_context(parked), format_screen_context(_snapshot())):
        assert is_screen_context_block(block)
        content = [{"type": "text", "text": "Try again"}, {"type": "text", "text": block}]
        assert _extract_user_text(content) == "Try again"
        hello = [{"role": "user", "content": [{"type": "text", "text": "hi"}, content[1]]}]
        assert is_smalltalk_turn(hello)
