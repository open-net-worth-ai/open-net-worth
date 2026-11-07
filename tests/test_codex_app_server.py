"""Tests for the Codex app-server transport.

The wire shapes asserted here are not invented: they were captured from a live
``codex app-server`` handshake on ``codex-cli 0.146.0`` and cross-checked against
``codex app-server generate-json-schema``. See ADR 2026-08-01-codex-app-server-transport.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from backend.ai.models import codex_app_server as cas
from backend.ai.models import codex_cli_provider as ccp
from backend.core.errors import CliNotSignedInError, ExternalServiceError

THREAD_ID = "019fbbae-29a8-7c73-821d-aef48de1a574"
TURN_ID = "019fbbae-29cf-7991-8ee7-c323140a9b42"


# ── notification classification ───────────────────────────────────────────────


def test_agent_message_delta_is_visible_text():
    ev = cas.classify(
        {"method": "item/agentMessage/delta", "params": {"threadId": THREAD_ID, "delta": "Hi"}}
    )
    assert ev == {"kind": "text", "text": "Hi", "usage": {}}


@pytest.mark.parametrize(
    "method",
    ["item/reasoning/summaryTextDelta", "item/reasoning/textDelta"],
)
def test_both_reasoning_deltas_become_thinking(method):
    """This is the whole point of the transport — `codex exec --json` emits neither."""
    ev = cas.classify({"method": method, "params": {"threadId": THREAD_ID, "delta": "**Why**"}})
    assert ev["kind"] == "thinking"
    assert ev["text"] == "**Why**"


def test_token_usage_prefers_the_turn_not_the_thread_total():
    ev = cas.classify(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": THREAD_ID,
                "turnId": TURN_ID,
                "tokenUsage": {
                    "last": {"inputTokens": 10, "cachedInputTokens": 4, "outputTokens": 7},
                    "total": {"inputTokens": 999, "cachedInputTokens": 999, "outputTokens": 999},
                },
            },
        }
    )
    assert ev["kind"] == "usage"
    assert cas.usage_fields(ev["usage"]) == {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 7,
    }


def test_turn_completed_is_terminal_and_carries_the_fallback_text():
    ev = cas.classify(
        {
            "method": "turn/completed",
            "params": {
                "threadId": THREAD_ID,
                "turn": {
                    "status": "completed",
                    "items": [{"type": "agentMessage", "text": "final"}],
                },
            },
        }
    )
    assert ev["kind"] == "done"
    assert ev["text"] == "final"


def test_a_failed_turn_is_an_error_not_a_silent_completion():
    ev = cas.classify(
        {
            "method": "turn/completed",
            "params": {
                "threadId": THREAD_ID,
                "turn": {"status": "failed", "error": {"message": "usageLimitExceeded"}},
            },
        }
    )
    assert ev["kind"] == "error"
    assert "usageLimitExceeded" in ev["text"]


def test_the_message_phase_is_surfaced_from_item_started():
    """The delta notification carries no phase — `item/started` is the only chance."""
    ev = cas.classify(
        {
            "method": "item/started",
            "params": {
                "threadId": THREAD_ID,
                "item": {"type": "agentMessage", "text": "", "phase": "commentary"},
            },
        }
    )
    assert ev == {"kind": "phase", "text": "commentary", "usage": {}}


def test_a_completed_message_carries_its_phase_too():
    ev = cas.classify(
        {
            "method": "item/completed",
            "params": {
                "threadId": THREAD_ID,
                "item": {"type": "agentMessage", "text": "done", "phase": "final_answer"},
            },
        }
    )
    assert ev["kind"] == "message"
    assert ev["phase"] == "final_answer"


def test_a_build_that_reports_no_phase_yields_an_empty_one_not_a_crash():
    """The schema warns providers do not emit `phase` consistently."""
    ev = cas.classify(
        {
            "method": "item/completed",
            "params": {"threadId": THREAD_ID, "item": {"type": "agentMessage", "text": "hi"}},
        }
    )
    assert ev["kind"] == "message"
    assert ev["phase"] == ""


def test_unknown_notifications_are_ignored_not_fatal():
    # The server emits plenty we do not model (mcpServer/startupStatus/updated,
    # account/rateLimits/updated, thread/status/changed …). None may break a turn.
    for method in (
        "mcpServer/startupStatus/updated",
        "account/rateLimits/updated",
        "thread/status/changed",
        "item/started",
    ):
        assert cas.classify({"method": method, "params": {"threadId": THREAD_ID}})["kind"] == (
            "ignore"
        )


# ── effort ladder ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given,expected",
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        # `model/list` advertises xhigh/max/ultra for gpt-5.6-sol, so unlike the exec
        # transport (whose `-c model_reasoning_effort` rejects them) these pass THROUGH.
        ("xhigh", "xhigh"),
        ("max", "max"),
        # "no reasoning" floors to low rather than passing nothing: passing nothing hands
        # the turn Codex's OWN default, which is higher.
        ("none", "low"),
        ("off", "low"),
        ("", ""),
        ("bogus", ""),
    ],
)
def test_effort_maps_one_to_one(given, expected):
    assert cas._effort(given) == expected


def test_the_exec_transport_still_folds_what_it_cannot_send():
    """Guard the asymmetry: it is deliberate, not a missed update."""
    assert ccp._EFFORT_MAP["xhigh"] == "high"
    assert cas._effort("xhigh") == "xhigh"


# ── a scripted app-server over real pipes ─────────────────────────────────────

_FAKE_SERVER = r"""
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

THREAD = "%(thread)s"
TURNS = %(script)r
RECORD = "%(record)s"
turn_n = 0

def record(kind, params):
    with open(RECORD, "a") as fh:
        fh.write(json.dumps({"kind": kind, "params": params}) + "\n")

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"codexHome": "x"}})
    elif method == "thread/start":
        # The real server emits thread/started BEFORE the response on some builds — the
        # client must tolerate either order.
        send({"jsonrpc": "2.0", "method": "thread/started",
              "params": {"threadId": THREAD, "thread": {"id": THREAD}}})
        send({"jsonrpc": "2.0", "id": mid, "result": {"thread": {"id": THREAD}}})
        record("thread/start", msg.get("params") or {})
    elif method == "turn/interrupt":
        record("turn/interrupt", msg.get("params") or {})
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "turn/start":
        record("turn/start", msg.get("params") or {})
        send({"jsonrpc": "2.0", "id": mid, "result": {"turn": {"id": "turn-1"}}})
        script = TURNS[min(turn_n, len(TURNS) - 1)]
        turn_n += 1
        for note in script:
            if note.get("method") == "__die__":
                # Stand in for the server dying mid-turn (crash, OOM, kill).
                sys.exit(1)
            note.setdefault("params", {})["threadId"] = THREAD
            send({"jsonrpc": "2.0", **note})
"""


def _fake_server(tmp_path, script: list, name: str = "codex"):
    """Write an executable stand-in for the `codex` binary that speaks the protocol.

    ``script`` is either one turn's notifications, or a LIST of per-turn scripts when a
    test needs the second ``turn/start`` (a continuation) to answer differently. The last
    entry repeats if more turns arrive than were scripted.

    Returns ``(path, record_file)``; the server appends every request it received to
    ``record_file`` as JSONL. It records to a FILE rather than stderr because the client
    owns the stderr pipe (a second reader on the same StreamReader raises).
    """
    turns = script if (script and isinstance(script[0], list)) else [script]
    record = tmp_path / f"{name}_received.jsonl"
    py = tmp_path / f"{name}_server.py"
    py.write_text(
        _FAKE_SERVER % {"thread": THREAD_ID, "script": turns, "record": record},
        encoding="utf-8",
    )
    exe = tmp_path / name
    exe.write_text(
        f'#!/bin/sh\nexec {sys.executable} "{py}"\n',
        encoding="utf-8",
    )
    exe.chmod(0o755)
    return str(exe), record


def _received(record, kind: str) -> dict:
    return _received_all(record, kind)[0]


def _received_all(record, kind: str) -> list:
    rows = [
        json.loads(line)["params"]
        for line in record.read_text().splitlines()
        if json.loads(line)["kind"] == kind
    ]
    if not rows:
        raise AssertionError(f"the server never received a {kind} request")
    return rows


def _delta(text):
    return {"method": "item/agentMessage/delta", "params": {"delta": text}}


def _usage(inp=100, cached=40, out=7):
    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "tokenUsage": {
                "last": {"inputTokens": inp, "cachedInputTokens": cached, "outputTokens": out}
            }
        },
    }


def _completed(items=None):
    return {
        "method": "turn/completed",
        "params": {"turn": {"status": "completed", "items": items or []}},
    }


@pytest.fixture(autouse=True)
def _reset_transport_state(monkeypatch):
    # Retries are real `asyncio.sleep`s; a failing-transport test would otherwise pay for
    # them. The delay is the only thing stubbed — the attempt COUNT stays honest.
    monkeypatch.setattr(ccp, "_APP_SERVER_RETRY_DELAY", 0, raising=False)
    cas._clients.clear()
    cas._semaphores.clear()
    yield
    cas._clients.clear()
    cas._semaphores.clear()


async def _drain_stream(provider, **kw):
    return [e async for e in provider.stream_chat(**kw)]


async def test_a_turn_streams_deltas_thinking_and_usage(tmp_path, monkeypatch):
    cli, _record = _fake_server(
        tmp_path,
        [
            {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "**Thinking**"}},
            _delta("Hello"),
            _delta(" world"),
            _usage(),
            _completed([{"type": "agentMessage", "text": "Hello world"}]),
        ],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="be brief",
        model="gpt-5.6-sol",
        effort="high",
    )
    kinds = [e["type"] for e in events]
    assert "thinking_delta" in kinds, "reasoning must reach the UI — this is the whole point"
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking_delta")
    # Every reasoning part opens its own block; a leading blank line is inert in markdown.
    assert thinking.strip() == "**Thinking**"
    # Streamed incrementally, not flushed as one lump at the end.
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["Hello", " world"]
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 100
    assert usage["cache_read_tokens"] == 40
    assert usage["output_tokens"] == 7
    assert usage["cost_basis"] == "plan_included"  # no stored key → the ChatGPT plan
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}


async def test_the_thread_is_started_isolated_and_read_only(tmp_path):
    cli, record = _fake_server(tmp_path, [_delta("ok"), _usage(), _completed()])
    client = cas.client_for(cli)
    events = [
        e
        async for e in client.run_turn(
            instructions="SYSTEM TEXT",
            transcript="Human: hi\n\nAssistant:",
            model="gpt-5.6-sol",
            effort="medium",
        )
    ]
    assert any(e["kind"] == "done" for e in events)
    await client.aclose()
    params = _received(record, "thread/start")
    turn = _received(record, "turn/start")

    # A model backend never edits anything and never stalls on an approval prompt.
    assert params["sandbox"] == "read-only"
    assert params["approvalPolicy"] == "never"
    assert params["ephemeral"] is True
    # The operator's MCP servers, web tool, project docs and history stay out.
    assert params["config"]["mcp_servers"] == {}
    assert params["config"]["tools"]["web_search"] is False
    assert params["config"]["project_doc_max_bytes"] == 0
    # baseInstructions REPLACES Codex's built-in agent preamble (14k → ~10k input tokens).
    assert params["baseInstructions"] == "SYSTEM TEXT"
    # Reasoning summaries are what make thinking visible at all.
    assert turn["summary"] == "detailed"
    assert turn["effort"] == "medium"
    assert turn["input"] == [{"type": "text", "text": "Human: hi\n\nAssistant:"}]


async def test_stream_chat_sends_images_as_local_image_inputs(tmp_path):
    """The preferred app-server transport supports native localImage turn inputs."""
    import base64

    cli, record = _fake_server(tmp_path, [_delta("seen"), _usage(), _completed()])
    provider = ccp.CodexCliProvider(cli_path=cli)
    await _drain_stream(
        provider,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this receipt"},
                    {
                        "type": "image",
                        "source": {
                            "media_type": "image/png",
                            "data": base64.b64encode(b"app-server-pixel").decode(),
                        },
                    },
                ],
            }
        ],
        tools=[],
        system="",
        model="gpt-5.6-sol",
    )

    turn = _received(record, "turn/start")
    assert "image omitted" not in turn["input"][0]["text"]
    assert turn["input"][1]["type"] == "localImage"
    assert turn["input"][1]["path"].endswith("image_0.png")


async def test_the_materialized_image_directory_is_removed_after_a_successful_turn(tmp_path):
    """Phase-0 pin (RFC cross-channel-attachments item 3): ``run_turn``'s image_dir is a
    PRIVATE scratch directory (``onw-codex-app-images-*``) created only for this turn's
    localImage inputs — it must not survive the turn that used it, success included."""
    import base64

    cli, record = _fake_server(tmp_path, [_delta("seen"), _usage(), _completed()])
    provider = ccp.CodexCliProvider(cli_path=cli)
    await _drain_stream(
        provider,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this"},
                    {
                        "type": "image",
                        "source": {
                            "media_type": "image/png",
                            "data": base64.b64encode(b"cleaned-up-pixel").decode(),
                        },
                    },
                ],
            }
        ],
        tools=[],
        system="",
        model="gpt-5.6-sol",
    )

    turn = _received(record, "turn/start")
    image_path = Path(turn["input"][1]["path"])
    assert image_path.parent.name.startswith("onw-codex-app-images-")
    assert not image_path.exists()
    assert not image_path.parent.exists()


async def test_the_materialized_image_directory_is_removed_when_the_turn_is_abandoned(
    tmp_path,
):
    """Same private-scratch-directory contract as the success-path test above, but for a
    turn that is CANCELLED mid-flight — the case the RFC calls out as easy to miss.
    ``run_turn`` is an async generator suspended on ``queue.get()`` while the (fake) server
    is still "thinking" and sends nothing further; abandoning it here must still run the
    ``finally`` that removes image_dir, not leak it because the turn never reached a natural
    ``done``/``error``.

    A spy on ``write_image_files`` observes materialization AS IT HAPPENS (the file must
    exist at that exact moment) — disk state cannot be inspected AFTER abandonment because
    ``asyncio.wait_for``'s own timeout-cancellation already unwinds into the generator and
    runs its ``finally`` before returning control here, matching the same subtlety pinned
    for the exec transport in ``test_codex_cli_provider.py``.
    """
    import asyncio
    import base64
    import unittest.mock

    cli, _record = _fake_server(tmp_path, [])  # acks turn/start, then sends nothing more
    client = cas.client_for(cli)
    data = base64.b64encode(b"abandoned-pixel").decode()

    captured = {}
    original_write = cas.write_image_files

    def _spy(directory, images):
        paths = original_write(directory, images)
        captured["dir"] = Path(directory)
        captured["existed_and_matched"] = [
            os.path.exists(p) and open(p, "rb").read() == b"abandoned-pixel" for p in paths
        ]
        return paths

    with unittest.mock.patch.object(cas, "write_image_files", _spy):
        agen = client.run_turn(
            instructions="s",
            transcript="t",
            model="m",
            effort="low",
            images=[{"media_type": "image/png", "data": data}],
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        await agen.aclose()
    await client.aclose()

    assert captured["existed_and_matched"] == [True]  # really materialized, not vacuous
    assert captured["dir"].name.startswith("onw-codex-app-images-")
    assert not captured["dir"].exists()


async def test_a_tool_call_is_scanned_out_of_the_visible_stream(tmp_path):
    call = '<tool_call>{"name": "get_transactions", "input": {"limit": 5}}</tool_call>'
    cli, _record = _fake_server(
        tmp_path,
        [_delta("Let me look. "), _delta(call), _usage(), _completed()],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "spending?"}],
        tools=[{"name": "get_transactions", "description": "d", "input_schema": {}}],
        system="s",
        model="gpt-5.6-sol",
        effort="low",
    )
    visible = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "<tool_call>" not in visible, "the sentinel must never reach the user"
    assert visible == "Let me look. "
    use = next(e for e in events if e["type"] == "tool_use")
    assert use["name"] == "get_transactions"
    assert use["input"] == {"limit": 5}
    assert events[-1]["stop_reason"] == "tool_use"


async def test_every_turn_carries_the_tool_manifest_across_model_switch_and_respawn(tmp_path):
    """Tools ride in each turn's `baseInstructions` on a FRESH ephemeral thread — nothing is
    registered once at thread start, so a follow-up, a model switch (terra -> luna) or a
    respawned child cannot come back with an empty tool surface."""
    cli, record = _fake_server(tmp_path, [_delta("ok"), _usage(), _completed()])
    provider = ccp.CodexCliProvider(cli_path=cli)
    tools = [{"name": "find_tools", "description": "d", "input_schema": {}}]

    async def _turn(model):
        await _drain_stream(
            provider,
            messages=[{"role": "user", "content": "go"}],
            tools=tools,
            system="s",
            model=model,
            effort="low",
        )

    await _turn("gpt-5.6-terra")
    await _turn("gpt-6-luna")
    client = cas.client_for(cli)
    client._proc.kill()
    await client._proc.wait()
    await _turn("gpt-6-luna")
    await client.aclose()

    starts = _received_all(record, "thread/start")
    assert [p["model"] for p in starts] == ["gpt-5.6-terra", "gpt-6-luna", "gpt-6-luna"]
    for params in starts:
        assert params["ephemeral"] is True
        assert "### find_tools" in params["baseInstructions"]


async def test_a_turn_that_ends_without_turn_completed_is_an_error(tmp_path):
    """A truncated stream must never be recorded as a clean turn with no usage."""
    cli, _record = _fake_server(tmp_path, [_delta("half an ans"), {"method": "__die__"}])
    client = cas.client_for(cli)
    with pytest.raises(ExternalServiceError, match="exited mid-turn|truncated"):
        async for _ in client.run_turn(instructions="s", transcript="t", model="m", effort="low"):
            pass
    await client.aclose()


async def test_the_process_is_reused_across_turns(tmp_path):
    """The point of the transport: no cold start, no re-init per turn."""
    cli, _record = _fake_server(tmp_path, [_delta("ok"), _usage(), _completed()])
    client = cas.client_for(cli)
    pids = []
    for _ in range(3):
        async for _ev in client.run_turn(instructions="s", transcript="t", model="m", effort="low"):
            pass
        pids.append(client._proc.pid)
    await client.aclose()
    assert len(set(pids)) == 1, f"a new process per turn defeats the purpose: {pids}"


async def test_a_dead_server_is_respawned_on_the_next_turn(tmp_path):
    cli, _record = _fake_server(tmp_path, [_delta("ok"), _usage(), _completed()])
    client = cas.client_for(cli)
    async for _ in client.run_turn(instructions="s", transcript="t", model="m", effort="low"):
        pass
    first = client._proc.pid
    client._proc.kill()
    await client._proc.wait()

    async for _ in client.run_turn(instructions="s", transcript="t", model="m", effort="low"):
        pass
    second = client._proc.pid
    assert client.alive, "the client must come back up rather than stay dead"
    await client.aclose()
    assert second != first


async def test_a_failing_app_server_retries_then_raises_instead_of_using_exec(
    tmp_path, monkeypatch
):
    """A broken app-server RETRIES `_APP_SERVER_ATTEMPTS` times, then surfaces the error.

    Owner, 2026-08-09: "let's not fallback to codex cli or whatever if codex app server
    failed ... maybe retry 3 times then show error to user". The old behaviour flipped a
    sticky process-wide switch on the first error and silently served every later turn from
    `codex exec` — which streams nothing — with a single WARNING as the only trace.
    """
    exe = tmp_path / "codex"
    exe.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    exe.chmod(0o755)

    attempts = {"n": 0}

    async def _boom(self, *a, **kw):
        attempts["n"] += 1
        raise ExternalServiceError("app-server reported an error: boom")
        yield  # pragma: no cover

    async def _fake_exec(self, *a, **kw):
        raise AssertionError("exec must NOT serve a turn the app-server failed")
        yield  # pragma: no cover

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_app_server", _boom)
    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_exec", _fake_exec)
    provider = ccp.CodexCliProvider(cli_path=str(exe))

    with pytest.raises(ExternalServiceError) as err:
        await _drain_stream(
            provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="s",
            model="m",
            effort="low",
        )

    assert attempts["n"] == ccp._APP_SERVER_ATTEMPTS == 3
    # The message names the cause and points at fixing the CLI...
    assert "boom" in str(err.value)
    assert "codex --version" in str(err.value)
    # ...and must NEVER offer exec as a workaround (owner, 2026-08-09: "i don't want users to
    # switch to exec, app server must work"). Suggesting the degraded transport is the same
    # silent fallback this test exists to prevent, just spelled out as advice.
    assert "exec" not in str(err.value)
    assert "ONW_CODEX_TRANSPORT" not in str(err.value)


async def test_a_transient_app_server_failure_is_retried_not_surfaced(tmp_path, monkeypatch):
    """Two of three attempts may fail without the user ever seeing it — that's the point of
    retrying rather than failing on the first hiccup."""
    exe = tmp_path / "codex"
    exe.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    exe.chmod(0o755)

    attempts = {"n": 0}

    async def _flaky(self, *a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ExternalServiceError("transient")
        yield {"type": "text_delta", "text": "recovered"}
        yield {"type": "done", "stop_reason": "end_turn"}

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_app_server", _flaky)
    provider = ccp.CodexCliProvider(cli_path=str(exe))
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="low",
    )
    assert attempts["n"] == 3
    assert events[0]["text"] == "recovered"


async def test_a_failure_after_content_reached_the_user_is_never_retried(tmp_path, monkeypatch):
    """Retrying a half-streamed turn would repeat the text the user already read."""
    exe = tmp_path / "codex"
    exe.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    exe.chmod(0o755)

    attempts = {"n": 0}

    async def _dies_midway(self, *a, **kw):
        attempts["n"] += 1
        yield {"type": "text_delta", "text": "half an answer"}
        raise ExternalServiceError("died mid-turn")

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_app_server", _dies_midway)
    provider = ccp.CodexCliProvider(cli_path=str(exe))

    with pytest.raises(ExternalServiceError):
        await _drain_stream(
            provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="s",
            model="m",
            effort="low",
        )
    assert attempts["n"] == 1


async def test_pinning_the_transport_to_exec_skips_the_app_server(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_CODEX_TRANSPORT", "exec")
    assert ccp._preferred_transport() == "exec"

    async def _boom(self, *a, **kw):
        raise AssertionError("the app-server must not be consulted when pinned to exec")
        yield  # pragma: no cover

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_app_server", _boom)

    async def _fake_exec(self, instructions, transcript, model, effort, images=None):
        yield {"type": "done", "stop_reason": "end_turn"}

    monkeypatch.setattr(ccp.CodexCliProvider, "_stream_exec", _fake_exec)
    provider = ccp.CodexCliProvider(cli_path="codex")
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="low",
    )
    assert events == [{"type": "done", "stop_reason": "end_turn"}]


# ── isolated CODEX_HOME ───────────────────────────────────────────────────────


def test_the_isolated_home_is_seeded_with_an_empty_config(monkeypatch, tmp_path):
    from backend.ai.models import codex_home

    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    home = codex_home.isolated_home()
    config = tmp_path / "codex-home" / "config.toml"
    # An EMPTY config.toml, not a missing one — a missing file is what makes Codex fall
    # back to the operator's, plugins and skills included.
    assert config.exists()
    assert "[plugins" not in config.read_text()
    assert home == str(tmp_path / "codex-home")


def test_the_operators_login_is_linked_not_copied(monkeypatch, tmp_path):
    from backend.ai.models import codex_home

    operator = tmp_path / "dot-codex"
    operator.mkdir()
    (operator / "auth.json").write_text('{"tokens": "real"}', encoding="utf-8")
    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setattr(codex_home, "_operator_home", lambda: operator)

    codex_home.isolated_home()
    linked = tmp_path / "codex-home" / "auth.json"
    assert linked.exists()
    # A symlink, so a rotated refresh token stays in ONE place — a copy would drift.
    assert linked.is_symlink()
    assert linked.resolve() == (operator / "auth.json").resolve()


def test_a_missing_operator_login_is_not_fatal(monkeypatch, tmp_path):
    """A self-hoster may be running on a stored API key instead of `codex login`."""
    from backend.ai.models import codex_home

    monkeypatch.setattr(codex_home, "_HOME_DIR", tmp_path / "codex-home")
    monkeypatch.setattr(codex_home, "_operator_home", lambda: tmp_path / "nope")
    assert codex_home.isolated_home() == str(tmp_path / "codex-home")


def test_the_child_env_points_codex_at_our_home_and_keeps_real_home(monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "/the/operators/codex")
    monkeypatch.setenv("ONW_SECRET", "leaked")
    env = cas.child_env("")
    assert env["CODEX_HOME"] == cas.isolated_home()
    assert env["CODEX_HOME"] != "/the/operators/codex"
    assert "ONW_SECRET" not in env
    if "HOME" in os.environ:
        assert env["HOME"] == os.environ["HOME"]


# ── the preamble-abandoned turn ───────────────────────────────────────────────


def _msg_started(phase):
    return {"method": "item/started", "params": {"item": {"type": "agentMessage", "phase": phase}}}


async def test_a_turn_abandoned_after_a_preamble_is_continued_once(tmp_path):
    """The reported bug: "I'll search the ledger…" and then the turn just ends.

    An agentic model narrates before it acts, and we strip its native tools, so after the
    preamble there is nothing for it to invoke. Codex marks that message `commentary` —
    "additional tool calls or assistant output may follow" — so it is NOT an answer.
    """
    cli, record = _fake_server(
        tmp_path,
        [
            # Turn 1: a preamble, then the turn completes with nothing done.
            [
                _msg_started("commentary"),
                _delta("I'll search the April ledger for that."),
                _usage(),
                _completed(),
            ],
            # Turn 2 (the continuation): it actually answers.
            [
                _msg_started("final_answer"),
                _delta("Found it: three rows sum to S$3.29."),
                _usage(),
                _completed(),
            ],
        ],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "check my ledger"}],
        tools=[{"name": "get_transactions", "description": "d", "input_schema": {}}],
        system="s",
        model="gpt-5.6-sol",
        effort="low",
    )
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "Found it" in text, "the turn must not stop at the preamble"

    turns = _received_all(record, "turn/start")
    assert len(turns) == 2, "exactly one continuation"
    nudge = turns[1]["input"][0]["text"]
    assert "Continue" in nudge and "<tool_call>" in nudge
    # The continuation runs on the SAME thread — the preamble is still in its context.
    assert turns[1]["threadId"] == turns[0]["threadId"]


async def test_a_final_answer_turn_is_never_continued(tmp_path):
    cli, record = _fake_server(
        tmp_path,
        [_msg_started("final_answer"), _delta("Here is the answer."), _usage(), _completed()],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="low",
    )
    assert len(_received_all(record, "turn/start")) == 1


async def test_a_preamble_that_DID_call_a_tool_is_not_continued(tmp_path):
    """Narration plus a real tool call is a healthy turn — the host loop takes it from here."""
    call = '<tool_call>{"name": "get_transactions", "input": {}}</tool_call>'
    cli, record = _fake_server(
        tmp_path,
        [_msg_started("commentary"), _delta("Let me look. " + call), _usage(), _completed()],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "get_transactions", "description": "d", "input_schema": {}}],
        system="s",
        model="m",
        effort="low",
    )
    assert len(_received_all(record, "turn/start")) == 1
    assert events[-1]["stop_reason"] == "tool_use"


async def test_a_model_that_narrates_twice_is_not_chased_forever(tmp_path):
    """Every continuation is a real request against a rate-limited quota."""
    cli, record = _fake_server(
        tmp_path,
        [[_msg_started("commentary"), _delta("I'll get to it."), _usage(), _completed()]],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="low",
    )
    assert len(_received_all(record, "turn/start")) == 1 + cas._MAX_CONTINUATIONS


async def test_a_build_reporting_no_phase_is_left_alone(tmp_path):
    """`phase` unknown means "do nothing" — never guess a turn was abandoned."""
    cli, record = _fake_server(tmp_path, [_delta("Some reply."), _usage(), _completed()])
    provider = ccp.CodexCliProvider(cli_path=cli)
    await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="low",
    )
    assert len(_received_all(record, "turn/start")) == 1


def test_the_manifest_forbids_announcing_instead_of_acting():
    """The prompt-side half of the same fix — it applies to every CLI transport."""
    from backend.ai.models.cli_tool_protocol import render_tool_manifest

    manifest = render_tool_manifest([{"name": "t", "description": "d", "input_schema": {}}])
    assert "Do NOT announce what you are about to do" in manifest


# ── reasoning parts ───────────────────────────────────────────────────────────


def test_a_reasoning_delta_carries_its_part_identity():
    ev = cas.classify(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {"threadId": THREAD_ID, "delta": "x", "summaryIndex": 2},
        }
    )
    assert ev["part"] == ("item/reasoning/summaryTextDelta", None, 2)


def test_summary_and_raw_parts_with_the_same_index_are_different_parts():
    """Both are numbered from 0, so the index alone would collide."""
    a = cas.classify(
        {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "a", "summaryIndex": 0}}
    )
    b = cas.classify(
        {"method": "item/reasoning/textDelta", "params": {"delta": "b", "contentIndex": 0}}
    )
    assert a["part"] != b["part"]


def _reason(text, index=0):
    return {
        "method": "item/reasoning/summaryTextDelta",
        "params": {"delta": text, "summaryIndex": index},
    }


async def test_separate_reasoning_parts_do_not_run_together(tmp_path):
    """Owner-reported: "…discrepancy****Confirming…" — two headlines fused into one line."""
    cli, _record = _fake_server(
        tmp_path,
        [
            _reason("**Analyzing starting balance discrepancy**", 0),
            _reason("**Confirming starting balance correction**", 1),
            _delta("Yes — the S$3.29 difference is the Starting Balance."),
            _usage(),
            _completed(),
        ],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "why?"}],
        tools=[],
        system="s",
        model="m",
        effort="high",
    )
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking_delta")
    assert "****" not in thinking
    assert thinking.strip() == (
        "**Analyzing starting balance discrepancy**\n\n**Confirming starting balance correction**"
    )


async def test_deltas_within_one_part_are_not_broken_up(tmp_path):
    """The separator marks a part BOUNDARY — mid-sentence chunks must stay contiguous."""
    cli, _record = _fake_server(
        tmp_path,
        [_reason("Check", 0), _reason("ing the ", 0), _reason("ledger", 0), _usage(), _completed()],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="high",
    )
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking_delta")
    assert thinking.strip() == "Checking the ledger"


async def test_reasoning_does_not_fuse_across_the_turns_of_a_tool_loop(tmp_path):
    """Owner-reported: "…accounts schemaVerifying original account balance date…".

    One assistant message spans MANY model turns when tools are called, and the consumer
    concatenates thinking across all of them. A per-turn part index resets each turn, so
    the boundary has to survive the reset.
    """
    cli, _record = _fake_server(
        tmp_path,
        [
            [_reason("**Planning tool update for accounts schema**", 0), _usage(), _completed()],
            [_reason("**Verifying original account balance date**", 0), _usage(), _completed()],
        ],
    )
    client = cas.client_for(cli)
    collected = []
    for _ in range(2):
        async for ev in client.run_turn(instructions="s", transcript="t", model="m", effort="high"):
            if ev["kind"] == "thinking":
                collected.append(ev["text"])
    await client.aclose()

    thinking = "".join(collected)
    assert "schema**\n\n**Verifying" in thinking, f"headlines fused across turns: {thinking!r}"
    assert "**Planning tool update for accounts schema****Verifying" not in thinking


# ── model-telemetry instrumentation: Site A, `_complete_app_server` (`complete()`, not
# `stream_chat`) — RFC 2026-09-04-ai-model-performance-telemetry §5.2. `complete()` has NO
# coverage via the real subprocess `_fake_server` harness above (only `stream_chat` does), and
# reproducing "the generator ends with no `done` kind" through a real subprocess would mean
# waiting out the client's own timeout — indistinguishable from a hung server and slow. A
# minimal fake `AppServerClient` swapped in at `codex_app_server.client_for` is the direct,
# deterministic way to drive `_complete_app_server`'s own truncation branch. ──────────────────


class _TruncatingAppServerClient:
    """`run_turn` yields a message but never a `done` kind — `turn.completed` stays False."""

    async def run_turn(self, **kwargs):  # noqa: ARG002 - fake, args unused
        yield {"kind": "message", "text": "partial", "usage": {}}


class _SucceedingAppServerClient:
    async def run_turn(self, **kwargs):  # noqa: ARG002 - fake, args unused
        yield {"kind": "done", "text": "ok", "usage": {}}


def _spy_on_model_telemetry(monkeypatch):
    from backend.db.model_telemetry_store import ModelTelemetryStore

    calls: list[tuple[str, bool]] = []

    def _record_sample(self, model, error=False, **kwargs):  # noqa: ARG001 - spy signature
        calls.append((model, error))

    monkeypatch.setattr(ModelTelemetryStore, "record_sample", _record_sample)
    return calls


async def test_complete_app_server_records_telemetry_per_attempt_then_raises(monkeypatch):
    """AC 8: a turn that ends without `turn/completed` records one telemetry error PER
    attempt — including the final, budget-exhausting one — then raises."""
    telemetry_calls = _spy_on_model_telemetry(monkeypatch)
    monkeypatch.setattr(cas, "client_for", lambda *a, **kw: _TruncatingAppServerClient())
    provider = ccp.CodexCliProvider(cli_path="codex")

    with pytest.raises(ExternalServiceError, match="truncated"):
        await provider.complete(
            "hi",
            model="gpt-5.1-codex",
            response_format={"type": "json_object"},
        )

    assert telemetry_calls == [("gpt-5.1-codex", True)] * ccp._APP_SERVER_ATTEMPTS


async def test_complete_app_server_unstructured_truncation_does_not_count_as_json_retry(
    monkeypatch,
):
    """A plain-text completion has no JSON contract, so its retry stays out of the metric."""
    telemetry_calls = _spy_on_model_telemetry(monkeypatch)
    monkeypatch.setattr(cas, "client_for", lambda *a, **kw: _TruncatingAppServerClient())
    provider = ccp.CodexCliProvider(cli_path="codex")

    with pytest.raises(ExternalServiceError, match="truncated"):
        await provider.complete("hi", model="gpt-5.1-codex")

    assert telemetry_calls == []


async def test_complete_app_server_success_records_zero_telemetry_errors(monkeypatch):
    """AC 9's app-server analogue: a clean first attempt records nothing."""
    telemetry_calls = _spy_on_model_telemetry(monkeypatch)
    monkeypatch.setattr(cas, "client_for", lambda *a, **kw: _SucceedingAppServerClient())
    provider = ccp.CodexCliProvider(cli_path="codex")

    text = await provider.complete("hi", model="gpt-5.1-codex")

    assert text == "ok"
    assert telemetry_calls == []


async def test_a_raising_telemetry_write_never_perturbs_complete_app_server(monkeypatch):
    """AC 10, app-server transport: a forced exception inside the telemetry write is
    swallowed — `complete()`'s own retry/raise behavior is unaffected."""

    def _raise(self, model, error=False, **kwargs):
        raise RuntimeError("telemetry backend is down")

    from backend.db.model_telemetry_store import ModelTelemetryStore

    monkeypatch.setattr(ModelTelemetryStore, "record_sample", _raise)
    monkeypatch.setattr(cas, "client_for", lambda *a, **kw: _TruncatingAppServerClient())
    provider = ccp.CodexCliProvider(cli_path="codex")

    with pytest.raises(ExternalServiceError, match="truncated"):
        await provider.complete("hi", model="gpt-5.1-codex")


def _reason_in_item(item_id, text, index=0):
    return {
        "method": "item/reasoning/summaryTextDelta",
        "params": {"itemId": item_id, "delta": text, "summaryIndex": index},
    }


async def test_same_summary_index_in_different_reasoning_items_do_not_run_together(tmp_path):
    """Every reasoning item numbers its parts from 0, so index alone cannot tell
    "…function inspection" + "Filtering tool names…" apart (owner-reported)."""
    cli, _record = _fake_server(
        tmp_path,
        [
            _reason_in_item("rs_1", "**Planning function inspection**", 0),
            _reason_in_item("rs_2", "**Filtering tool names**", 0),
            _usage(),
            _completed(),
        ],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="s",
        model="m",
        effort="high",
    )
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking_delta")
    assert thinking.strip() == "**Planning function inspection**\n\n**Filtering tool names**"


async def test_reasoning_in_a_continuation_turn_does_not_fuse_onto_the_first(tmp_path):
    """A continuation re-numbers its summary parts from 0 — it must still open a new block."""
    cli, _record = _fake_server(
        tmp_path,
        [
            [
                _reason("**Planning Gmail lookup**", 0),
                _msg_started("commentary"),
                _delta("I'll check."),
                _usage(),
                _completed(),
            ],
            [
                _reason("**Planning Gmail MCP schema**", 0),
                _msg_started("final_answer"),
                _delta("Done."),
                _usage(),
                _completed(),
            ],
        ],
    )
    provider = ccp.CodexCliProvider(cli_path=cli)
    events = await _drain_stream(
        provider,
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "t", "description": "d", "input_schema": {}}],
        system="s",
        model="m",
        effort="low",
    )
    thinking = "".join(e["text"] for e in events if e["type"] == "thinking_delta")
    assert "****" not in thinking
    assert thinking.strip() == "**Planning Gmail lookup**\n\n**Planning Gmail MCP schema**"


_UNAUTHORIZED = (
    "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header, "
    "url: https://api.openai.com/v1/responses"
)


def _rejected_turn():
    return {
        "method": "turn/completed",
        "params": {"turn": {"status": "failed", "error": {"message": _UNAUTHORIZED}}},
    }


@pytest.mark.asyncio
async def test_a_turn_with_no_login_says_sign_in_and_marks_the_child_for_a_reread(tmp_path):
    """The frames are the ones `codex-cli 0.160.0` really sends with an empty CODEX_HOME."""
    cli, _record = _fake_server(tmp_path, [_rejected_turn()])
    provider = ccp.CodexCliProvider(cli_path=cli)

    with pytest.raises(CliNotSignedInError, match="Codex isn't signed in"):
        await _drain_stream(
            provider,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="s",
            model="m",
            effort="low",
        )

    client = cas.client_for(cli)
    assert client._login_stale is True
    await client.aclose()


@pytest.mark.asyncio
async def test_a_complete_with_no_login_says_sign_in_too(tmp_path):
    cli, _record = _fake_server(tmp_path, [_rejected_turn()])
    provider = ccp.CodexCliProvider(cli_path=cli)

    with pytest.raises(CliNotSignedInError):
        await provider.complete("hi", system="s", model="m", effort="low")
    await cas.client_for(cli).aclose()


# ── Codex's own retries ───────────────────────────────────────────────────────
#
# Frame shapes measured on codex-cli 0.160.0 against a server that drops the stream. Codex says
# "Reconnecting... n/m" with `willRetry: true` and carries on by itself. Before the drop reaches
# any text the retry is invisible and the turn completes. After text it re-sends the whole reply
# as a new message, so there is no way to continue without showing it twice.


def _retrying(n=1, details="stream disconnected before completion: stream closed early"):
    return {
        "method": "error",
        "params": {
            "error": {
                "message": f"Reconnecting... {n}/5",
                "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": None}},
                "additionalDetails": details,
            },
            "willRetry": True,
        },
    }


def _gave_up(message="stream disconnected before completion: stream closed early"):
    return {
        "method": "error",
        "params": {
            "error": {"message": message, "codexErrorInfo": "other", "additionalDetails": None},
            "willRetry": False,
        },
    }


def _reasoning(text):
    return {"method": "item/reasoning/summaryTextDelta", "params": {"delta": text}}


_CHAT = {
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [],
    "system": "s",
    "model": "m",
    "effort": "low",
}


def _visible(events) -> str:
    return "".join(e["text"] for e in events if e.get("type") == "text_delta")


def test_a_frame_codex_will_retry_is_not_an_error():
    ev = cas.classify(_retrying())
    assert ev["kind"] == "retrying"
    assert ev["text"] == "stream disconnected before completion: stream closed early"


def test_a_frame_codex_gave_up_on_reads_as_its_message_not_as_json():
    ev = cas.classify(_gave_up("usage limit reached"))
    assert ev == {"kind": "error", "text": "usage limit reached", "usage": {}}


def test_an_error_frame_with_no_retry_field_is_still_an_error():
    ev = cas.classify({"method": "error", "params": {"message": "boom"}})
    assert ev["kind"] == "error" and ev["text"] == "boom"


def test_additional_details_are_kept_beside_a_vague_message():
    frame = _gave_up("request failed")
    frame["params"]["error"]["additionalDetails"] = "connection reset"
    assert cas.classify(frame)["text"] == "request failed: connection reset"


def test_details_already_in_the_message_are_not_repeated():
    frame = _gave_up("request failed: connection reset")
    frame["params"]["error"]["additionalDetails"] = "connection reset"
    assert cas.classify(frame)["text"] == "request failed: connection reset"


@pytest.mark.asyncio
async def test_a_retry_before_any_text_finishes_the_turn_normally(tmp_path):
    cli, record = _fake_server(
        tmp_path,
        [_retrying(1), _retrying(2), _delta("all "), _delta("good"), _usage(), _completed()],
    )
    events = await _drain_stream(ccp.CodexCliProvider(cli_path=cli), **_CHAT)
    assert _visible(events) == "all good"
    assert len(_received_all(record, "turn/start")) == 1
    await cas.client_for(cli).aclose()


@pytest.mark.asyncio
async def test_a_retry_after_only_reasoning_still_finishes_normally(tmp_path):
    cli, _record = _fake_server(
        tmp_path, [_reasoning("thinking"), _retrying(), _delta("answer"), _usage(), _completed()]
    )
    events = await _drain_stream(ccp.CodexCliProvider(cli_path=cli), **_CHAT)
    assert _visible(events) == "answer"
    await cas.client_for(cli).aclose()


@pytest.mark.asyncio
async def test_a_retry_after_text_stops_rather_than_showing_the_reply_twice(tmp_path):
    cli, record = _fake_server(
        tmp_path,
        [_delta("Your net worth "), _retrying(), _delta("Your net worth "), _delta("is ACME.")],
    )
    seen = []
    with pytest.raises(ExternalServiceError, match="dropped partway through the reply") as err:
        async for event in ccp.CodexCliProvider(cli_path=cli).stream_chat(**_CHAT):
            seen.append(event)
    assert _visible(seen).strip() == "Your net worth"
    assert "Reconnecting" not in str(err.value)
    await _wait_for_interrupt(record)
    await cas.client_for(cli).aclose()


async def _wait_for_interrupt(record) -> None:
    """The model must not keep running a reply nobody will read."""
    for _ in range(100):
        if '"turn/interrupt"' in record.read_text():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the abandoned turn was never interrupted")


@pytest.mark.asyncio
async def test_retries_that_run_out_end_in_one_readable_error(tmp_path):
    cli, _record = _fake_server(
        tmp_path, [_retrying(1), _retrying(2), _gave_up("usage limit reached")]
    )
    with pytest.raises(ExternalServiceError) as err:
        await _drain_stream(ccp.CodexCliProvider(cli_path=cli), **_CHAT)
    assert "usage limit reached" in str(err.value)
    assert "codexErrorInfo" not in str(err.value)
    assert "Reconnecting" not in str(err.value)
    await cas.client_for(cli).aclose()


@pytest.mark.asyncio
async def test_a_retry_that_never_comes_back_hits_the_turn_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(cas, "_timeout_seconds", lambda: 0.5)
    cli, _record = _fake_server(tmp_path, [_retrying()])
    with pytest.raises(ExternalServiceError, match="timed out"):
        await _drain_stream(ccp.CodexCliProvider(cli_path=cli), **_CHAT)
    await cas.client_for(cli).aclose()


@pytest.mark.asyncio
async def test_an_exhausted_login_rejection_during_retries_still_says_sign_in(tmp_path):
    cli, _record = _fake_server(
        tmp_path, [_retrying(details=_UNAUTHORIZED), _gave_up(_UNAUTHORIZED)]
    )
    with pytest.raises(CliNotSignedInError):
        await _drain_stream(ccp.CodexCliProvider(cli_path=cli), **_CHAT)
    await cas.client_for(cli).aclose()
