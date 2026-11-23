"""Native tool calls on the Claude CLI (flag ``claude_cli_native_tools``), against a fake CLI.

The fake speaks what the real ``claude`` 2.1.329 does on its stream-json pipe (probed): it asks the
host for ``initialize`` / ``tools/list`` over ``control_request`` messages, announces the model's
calls in ``assistant`` events, then sends each ``tools/call`` ONE AT A TIME and waits for the host's
``control_response`` before the next (the CLI runs a model's parallel calls serially). The model
"decides" from a directive in the user message: ``CALLS: [[name, args], ...]``.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from backend.ai.models import claude_live_session as cls
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.core.feature_flags import override
from tests.fake_exe import python_cli

TOOL_A = {"name": "list_accounts", "description": "List accounts.", "input_schema": {}}
TOOL_B = {
    "name": "get_spending_summary",
    "description": "Spending for one month.",
    "input_schema": {"type": "object", "properties": {"month": {"type": "integer"}}},
}
FIND = {"name": "find_tools", "description": "Load tools.", "input_schema": {}}


def _fake_cli(tmp_path):
    return python_cli(
        tmp_path / "claude",
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            here = pathlib.Path(__file__).resolve().parent
            args = sys.argv[1:]
            system = pathlib.Path(args[args.index("--system-prompt-file") + 1]).read_text()
            with open(here / "spawns.jsonl", "a") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "argv": args, "system": system}) + "\\n")
            rpc_id = [0]

            def out(obj):
                sys.stdout.write(json.dumps(obj) + "\\n")
                sys.stdout.flush()

            def read():
                line = sys.stdin.readline()
                if not line:
                    sys.exit(0)
                with open(here / "received.jsonl", "a") as fh:
                    fh.write(json.dumps({"pid": os.getpid(), "msg": json.loads(line)}) + "\\n")
                return json.loads(line)

            def ask(message):
                rpc_id[0] += 1
                out({"type": "control_request", "request_id": "r%d" % rpc_id[0],
                     "request": {"subtype": "mcp_message", "server_name": "lucius",
                                 "message": message}})
                while True:
                    msg = read()
                    if msg.get("type") == "control_response":
                        return msg["response"]["response"]["mcp_response"]

            def usage(n):
                return {"input_tokens": n, "output_tokens": 5, "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 10}

            def turn(text):
                if "[end of result" in text:
                    for ev in (
                        {"type": "stream_event", "event": {"type": "message_start",
                            "message": {"model": "claude-haiku-5-5", "usage": usage(3)}}},
                        {"type": "stream_event", "event": {"type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": "recovered from transcript"}}},
                        {"type": "result", "result": "ok", "total_cost_usd": 0.01,
                         "usage": usage(3), "stop_reason": "end_turn"},
                    ):
                        out(ev)
                    return
                directive = text.split("CALLS:", 1)[1] if "CALLS:" in text else "[]"
                calls = json.loads(directive.strip().splitlines()[0])
                if not calls:
                    for ev in (
                        {"type": "stream_event", "event": {"type": "message_start",
                            "message": {"model": "claude-haiku-5-5", "usage": usage(3)}}},
                        {"type": "stream_event", "event": {"type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": "plain answer"}}},
                        {"type": "result", "result": "plain answer", "total_cost_usd": 0.01,
                         "usage": usage(3), "stop_reason": "end_turn"},
                    ):
                        out(ev)
                    return
                for i, (name, arguments) in enumerate(c for c in calls if c[0].startswith("ghost_")):
                    # A tool the CLI was never given: it refuses the call itself and goes on.
                    out({"type": "stream_event", "event": {"type": "message_start",
                         "message": {"model": "claude-haiku-5-5", "usage": usage(6)}}})
                    out({"type": "stream_event", "event": {"type": "content_block_start",
                         "content_block": {"type": "tool_use", "id": "g%d" % i,
                                           "name": "mcp__lucius__" + name}}})
                    out({"type": "assistant", "message": {"content": [{"type": "tool_use",
                         "id": "g%d" % i, "name": "mcp__lucius__" + name, "input": arguments}]}})
                    out({"type": "stream_event", "event": {"type": "message_delta",
                         "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}})
                    out({"type": "user", "message": {"content": [{"type": "tool_result",
                         "tool_use_id": "g%d" % i, "is_error": True}]}})
                calls = [c for c in calls if not c[0].startswith("ghost_")]
                out({"type": "stream_event", "event": {"type": "message_start",
                     "message": {"model": "claude-haiku-5-5", "usage": usage(7)}}})
                ids = []
                for i, (name, arguments) in enumerate(calls):
                    cid = "toolu_%d_%d" % (os.getpid(), len(ids) + rpc_id[0] * 10 + i)
                    ids.append(cid)
                    out({"type": "stream_event", "event": {"type": "content_block_start",
                         "content_block": {"type": "tool_use", "id": cid,
                                           "name": "mcp__lucius__" + name}}})
                    out({"type": "assistant", "message": {"content": [{"type": "tool_use",
                         "id": cid, "name": "mcp__lucius__" + name, "input": arguments}]}})
                out({"type": "stream_event", "event": {"type": "message_delta",
                     "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}})
                seen = []
                if "CANCEL" in text:
                    rpc_id[0] += 1
                    out({"type": "control_request", "request_id": "r%d" % rpc_id[0],
                         "request": {"subtype": "mcp_message", "server_name": "lucius",
                                     "message": {"jsonrpc": "2.0", "id": 77, "method": "tools/call",
                                                 "params": {"name": calls[0][0], "arguments": {},
                                                            "_meta": {"claudecode/toolUseId": ids[0]}}}}})
                    out({"type": "control_cancel_request", "request_id": "r%d" % rpc_id[0]})
                    out({"type": "stream_event", "event": {"type": "message_start",
                         "message": {"model": "claude-haiku-5-5", "usage": usage(2)}}})
                    out({"type": "stream_event", "event": {"type": "content_block_delta",
                         "delta": {"type": "text_delta", "text": "the tool was cancelled"}}})
                    out({"type": "result", "result": "x", "total_cost_usd": 0.02,
                         "usage": usage(2), "stop_reason": "end_turn"})
                    return
                for cid, (name, arguments) in zip(ids, calls):
                    meta = {} if "NOMETA" in text else {"_meta": {"claudecode/toolUseId": cid}}
                    reply = ask({"jsonrpc": "2.0", "id": 100 + len(seen), "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments, **meta}})
                    seen.append(reply["result"]["content"][0]["text"])
                    if "SKIP" in text:
                        break
                out({"type": "stream_event", "event": {"type": "message_start",
                     "message": {"model": "claude-haiku-5-5", "usage": usage(2)}}})
                out({"type": "stream_event", "event": {"type": "content_block_delta",
                     "delta": {"type": "text_delta", "text": "got: " + " | ".join(seen)}}})
                out({"type": "stream_event", "event": {"type": "message_delta",
                     "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}})
                out({"type": "result", "result": "done", "total_cost_usd": 0.05,
                     "usage": usage(9), "stop_reason": "end_turn"})

            started = False
            while True:
                msg = read()
                if msg.get("type") != "user":
                    continue
                if not started:
                    started = True
                    out({"type": "system", "subtype": "init", "apiKeySource": "none"})
                    ask({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"protocolVersion": "2025-11-25"}})
                    out({"type": "control_request", "request_id": "n1", "request": {
                         "subtype": "mcp_message", "server_name": "lucius",
                         "message": {"jsonrpc": "2.0", "method": "notifications/initialized"}}})
                    read()
                    listed = ask({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                    with open(here / "listed.json", "w") as fh:
                        json.dump(listed, fh)
                content = msg["message"]["content"]
                turn("".join(p.get("text") or "" for p in content))
            """
        ).strip(),
    )


def _rows(tmp_path, name):
    path = tmp_path / name
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _users(tmp_path) -> list[str]:
    """The text of each user message written to a CLI process, in order."""
    return [
        "".join(p.get("text") or "" for p in r["msg"]["message"]["content"])
        for r in _rows(tmp_path, "received.jsonl")
        if r["msg"].get("type") == "user"
    ]


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _calls(*calls) -> dict:
    return _user("question CALLS: " + json.dumps([list(c) for c in calls]))


def _called(events) -> list[dict]:
    return [e for e in events if e["type"] == "tool_use"]


def _after(events: list[dict], *, say: str = "") -> list[dict]:
    """The transcript tail ``assistant.run`` would write: the model's calls, then their results."""
    uses = _called(events)
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": u["id"], "name": u["name"], "input": u["input"]}
                for u in uses
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": u["id"], "content": f"result of {u['name']}"}
                for u in uses
            ],
        },
    ]


async def _round(provider, messages, tools):
    """One host round, read the way ``assistant.run`` does: up to ``done`` and no further."""
    events = []
    async for event in provider.stream_chat(
        messages, tools=tools, system="SYS", model="haiku", effort="medium"
    ):
        events.append(event)
        if event["type"] == "done":
            break
    return events


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "1")
    cls._pools.clear()
    yield
    for pool in cls._pools.values():
        for session in pool.all():
            if session.proc is not None and session.proc.returncode is None:
                session.proc.kill()
    cls._pools.clear()


@pytest.fixture
def provider(tmp_path):
    return ClaudeCliProvider(cli_path=str(_fake_cli(tmp_path)))


pytestmark = pytest.mark.flags(claude_cli_native_tools=True)


async def test_tools_are_native_not_text_and_the_handshake_is_answered(tmp_path, provider):
    await _round(provider, [_calls()], [TOOL_A, TOOL_B])

    spawn = _rows(tmp_path, "spawns.jsonl")[0]
    argv = spawn["argv"]
    assert json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["lucius"] == {
        "type": "sdk",
        "name": "lucius",
    }
    assert argv[argv.index("--allowedTools") + 1] == "mcp__lucius__*"
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
    assert "### list_accounts" not in spawn["system"], "no text manifest"
    assert "use_tool" in spawn["system"]
    listed = json.loads((tmp_path / "listed.json").read_text())["result"]["tools"]
    assert [t["name"] for t in listed] == ["list_accounts", "get_spending_summary", "use_tool"]
    assert listed[1]["inputSchema"]["properties"]["month"] == {"type": "integer"}
    assert listed[0]["inputSchema"] == {"type": "object", "properties": {}}


async def test_a_call_parks_the_process_and_the_next_round_answers_it(tmp_path, provider):
    messages = [_calls(("list_accounts", {"x": 1}))]
    first = await _round(provider, messages, [TOOL_A])

    use = _called(first)[0]
    assert (use["name"], use["input"]) == ("list_accounts", {"x": 1})
    assert [e["type"] for e in first if e["type"] == "tool_use_start"] == ["tool_use_start"]
    assert first[-1] == {"type": "done", "stop_reason": "tool_use"}
    assert first[-2]["type"] == "usage" and first[-2]["cost_reported"] is False

    second = await _round(provider, messages + _after(first), [TOOL_A])

    assert "".join(e.get("text", "") for e in second if e["type"] == "text_delta") == (
        "got: result of list_accounts"
    )
    assert second[-1] == {"type": "done", "stop_reason": "end_turn"}
    assert len(_rows(tmp_path, "spawns.jsonl")) == 1, "the same process, the same query"
    assert len(_users(tmp_path)) == 1, "the result was an answer, not a new user message"


async def test_a_turns_cost_is_the_clis_own_figure(provider):
    messages = [_calls(("list_accounts", {}))]
    first = await _round(provider, messages, [TOOL_A])
    second = await _round(provider, messages + _after(first), [TOOL_A])

    usages = [e for e in first + second if e["type"] == "usage"]
    assert len(usages) == 2 and usages[1]["cost_reported"] is True
    assert sum(u["cost_usd"] for u in usages) == pytest.approx(0.05)
    assert usages[0]["output_tokens"] == 9 and usages[1]["output_tokens"] == 4


async def test_parallel_calls_run_serially_in_the_cli_and_all_get_answered(tmp_path, provider):
    messages = [_calls(("list_accounts", {}), ("get_spending_summary", {"month": 3}))]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])
    assert [u["name"] for u in _called(first)] == ["list_accounts", "get_spending_summary"]

    second = await _round(provider, messages + _after(first), [TOOL_A, TOOL_B])

    text = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert text == "got: result of list_accounts | result of get_spending_summary"
    assert len(_rows(tmp_path, "spawns.jsonl")) == 1


async def test_use_tool_is_the_named_tool_to_everything_downstream(tmp_path, provider):
    messages = [_calls(("use_tool", {"name": "get_spending_summary", "args": {"month": 3}}))]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])

    use = _called(first)[0]
    assert (use["name"], use["input"]) == ("get_spending_summary", {"month": 3})
    start = next(e for e in first if e["type"] == "tool_use_start")
    assert start["name"] == "get_spending_summary" and start["id"] == use["id"]

    second = await _round(provider, messages + _after(first), [TOOL_A, TOOL_B])
    assert "result of get_spending_summary" in "".join(
        e.get("text", "") for e in second if e["type"] == "text_delta"
    )


async def test_a_malformed_use_tool_reaches_the_host_as_an_unknown_tool(provider):
    first = await _round(provider, [_calls(("use_tool", {"args": {}}))], [TOOL_A])
    assert _called(first)[0]["name"] == "use_tool"


async def test_a_tool_loaded_mid_session_is_reachable_without_a_restart(tmp_path, provider):
    messages = [_calls(("find_tools", {"names": ["get_spending_summary"]}))]
    first = await _round(provider, messages, [FIND, TOOL_A])

    # find_tools ran; the next round offers the newly loaded tool as well.
    second = await _round(provider, messages + _after(first), [FIND, TOOL_A, TOOL_B])
    answered = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert "<tools_added>" in answered and '"name":"get_spending_summary"' in answered
    assert "### get_spending_summary" not in answered, "no bare heading to mistake for a callable"
    assert "mcp__lucius__use_tool" in answered, "the model is told how to call it"
    assert "list_accounts" not in answered, "a tool the process already has is not re-sent"

    followup = [
        *messages,
        *_after(first),
        {"role": "assistant", "content": "ok"},
        _calls(("use_tool", {"name": "get_spending_summary", "args": {"month": 1}})),
    ]
    third = await _round(provider, followup, [FIND, TOOL_A, TOOL_B])
    assert _called(third)[0]["name"] == "get_spending_summary"
    assert len(_rows(tmp_path, "spawns.jsonl")) == 1, "no restart for a tool load"


async def test_the_live_session_is_reused_across_user_messages(tmp_path, provider):
    one = [_calls()]
    await _round(provider, one, [TOOL_A])
    two = [*one, {"role": "assistant", "content": "plain answer"}, _calls()]
    await _round(provider, two, [TOOL_A])

    assert len(_rows(tmp_path, "spawns.jsonl")) == 1
    users = _users(tmp_path)
    assert len(users) == 2 and users[1].count("question") == 1


async def test_a_new_message_after_a_stop_starts_a_fresh_process_from_the_transcript(
    tmp_path, provider
):
    messages = [_calls(("list_accounts", {}))]
    first = await _round(provider, messages, [TOOL_A])
    parked = next(iter(cls.pool().all()))

    # Stop: the calls were never answered; the person writes something else.
    stopped = [
        *messages,
        {
            "role": "assistant",
            "content": first
            and [
                {
                    "type": "tool_use",
                    "id": _called(first)[0]["id"],
                    "name": "list_accounts",
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": _called(first)[0]["id"],
                    "content": "stopped",
                }
            ],
        },
        {"role": "assistant", "content": "stopped"},
        _calls(),
    ]
    await _round(provider, stopped, [TOOL_A])

    assert len(_rows(tmp_path, "spawns.jsonl")) == 2
    assert parked.proc is None or parked.proc.returncode is not None, "the parked one is gone"
    full = _users(tmp_path)[-1]
    assert "[called list_accounts" in full and "[result of " in full, "history without text calls"
    assert "<tool_call>" not in full


async def test_results_that_do_not_cover_every_parked_call_start_over(tmp_path, provider):
    messages = [_calls(("list_accounts", {}), ("get_spending_summary", {}))]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])
    uses = _called(first)
    partial = [
        *messages,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": u["id"], "name": u["name"], "input": {}} for u in uses
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": uses[0]["id"], "content": "only one"}
            ],
        },
    ]
    second = await _round(provider, partial, [TOOL_A, TOOL_B])

    assert len(_rows(tmp_path, "spawns.jsonl")) == 2, "never wait on a result that is not coming"
    assert second[-1]["type"] == "done"


async def test_a_changed_tool_needs_a_fresh_process(tmp_path, provider):
    one = [_calls()]
    await _round(provider, one, [TOOL_A, TOOL_B])
    changed = {**TOOL_B, "description": "Spending, but different."}
    await _round(
        provider,
        [*one, {"role": "assistant", "content": "plain answer"}, _calls()],
        [TOOL_A, changed],
    )

    assert len(_rows(tmp_path, "spawns.jsonl")) == 2


async def test_a_host_error_result_reaches_the_model_as_an_error(tmp_path, provider):
    messages = [_calls(("list_accounts", {}))]
    first = await _round(provider, messages, [TOOL_A])
    after = _after(first)
    after[1]["content"][0]["is_error"] = True
    await _round(provider, messages + after, [TOOL_A])

    answered = [
        r["msg"]
        for r in _rows(tmp_path, "received.jsonl")
        if r["msg"].get("type") == "control_response"
    ][-1]
    assert answered["response"]["response"]["mcp_response"]["result"]["isError"] is True


def _text_cli(tmp_path):
    """A `claude` that knows only the text protocol: it records its spawn and answers."""
    return python_cli(
        tmp_path / "claude",
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import json, pathlib, sys
            here = pathlib.Path(__file__).resolve().parent
            args = sys.argv[1:]
            system = pathlib.Path(args[args.index("--system-prompt-file") + 1]).read_text()
            with open(here / "spawns.jsonl", "a") as fh:
                fh.write(json.dumps({"argv": args, "system": system}) + "\\n")
            for line in sys.stdin:
                print(json.dumps({"type": "result", "result": "hi", "total_cost_usd": 0.01,
                                  "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
            """
        ).strip(),
    )


async def test_flag_off_runs_the_text_protocol_untouched(tmp_path):
    provider = ClaudeCliProvider(cli_path=str(_text_cli(tmp_path)))
    with override(claude_cli_native_tools=False):
        await _round(provider, [_user("hi")], [TOOL_A])

    spawn = _rows(tmp_path, "spawns.jsonl")[0]
    assert "--mcp-config" not in spawn["argv"] and "--allowedTools" not in spawn["argv"]
    assert "### list_accounts" in spawn["system"], "the text manifest, as before"


async def test_no_live_sessions_means_the_text_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_CLI_SESSIONS", "0")
    provider = ClaudeCliProvider(cli_path=str(_text_cli(tmp_path)))
    await _round(provider, [_user("hi")], [TOOL_A])

    spawn = _rows(tmp_path, "spawns.jsonl")[0]
    assert "--mcp-config" not in spawn["argv"] and "### list_accounts" in spawn["system"]


@pytest.mark.parametrize(
    ("cli_name", "arguments", "expected"),
    [
        ("mcp__lucius__list_accounts", {"a": 1}, ("list_accounts", {"a": 1})),
        (
            "mcp__lucius__use_tool",
            {"name": "get_x", "args": {"m": 1}},
            ("get_x", {"m": 1}),
        ),
        ("mcp__lucius__use_tool", {"name": " mcp__lucius__get_x ", "args": {}}, ("get_x", {})),
        ("mcp__lucius__use_tool", {"name": "get_x", "args": "oops"}, ("get_x", {})),
        ("mcp__lucius__use_tool", {"args": {}}, ("use_tool", {"args": {}})),
        ("mcp__lucius__list_accounts", None, ("list_accounts", {})),
    ],
)
def test_unwrap_names_the_real_tool(cli_name, arguments, expected):
    from backend.ai.models import claude_native_wire as wire

    assert wire.unwrap(cli_name, arguments) == expected


def test_a_notification_gets_no_answer_and_an_unknown_method_an_error():
    from backend.ai.models import claude_native_wire as wire

    assert wire.reply({"jsonrpc": "2.0", "method": "notifications/initialized"}, []) is None
    refused = wire.reply({"jsonrpc": "2.0", "id": 5, "method": "resources/list"}, [])
    assert refused is not None and refused["error"]["code"] == -32601


async def test_stopping_a_round_kills_its_process_and_leaves_nothing_parked(tmp_path, provider):
    stream = provider.stream_chat(
        [_calls(("list_accounts", {}))], tools=[TOOL_A], system="SYS", model="haiku", effort="low"
    )
    first = await stream.__anext__()
    assert first["type"] == "tool_use_start"
    session = next(iter(cls.pool().all()))
    await stream.aclose()

    assert cls.pool().all() == [], "a stopped round is not kept"
    assert session.proc is None or session.proc.returncode is not None


async def test_calls_the_cli_refused_itself_do_not_end_the_round_early(tmp_path, provider):
    """Live (Sonnet, 2026-11-24): the model first called a tool loaded mid-session by its own
    name; the CLI refused that call itself, its message ended on `tool_use`, and the next message
    carried two real calls. The round ended after the first, the second `tools/call` was never
    answered, and the turn sat until the 300 s ceiling."""
    messages = [
        _calls(("ghost_get_x", {}), ("list_accounts", {}), ("get_spending_summary", {"month": 3}))
    ]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])

    assert [u["name"] for u in _called(first)] == ["list_accounts", "get_spending_summary"]
    second = await _round(provider, messages + _after(first), [TOOL_A, TOOL_B])
    text = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert text == "got: result of list_accounts | result of get_spending_summary"


async def test_a_call_the_host_never_saw_is_refused_not_waited_on(tmp_path, provider):
    messages = [_calls(("list_accounts", {}))]
    first = await _round(provider, messages, [TOOL_A])
    session = next(iter(cls.pool().all()))

    await session._called(
        "rq", {"id": 9, "params": {"name": "x", "_meta": {"claudecode/toolUseId": "toolu_stray"}}}
    )

    assert "toolu_stray" not in session.waiting, "answered at once, with an error"
    sent = [r["msg"] for r in _rows(tmp_path, "received.jsonl")]
    assert first[-1]["type"] == "done" and sent


def test_every_sentence_that_names_the_wrapper_uses_the_name_the_cli_knows():
    """Sonnet follows our text literally: told "call `use_tool`", it wrote that bare name, the CLI
    answered "No such tool available: use_tool" itself, and the model repeated the call with the
    right name — one more full request per occurrence (+17% cost). The CLI's name for the tool is
    `mcp__lucius__use_tool`."""
    import re

    from backend.ai.models import claude_native_round as rnd
    from backend.ai.models import claude_native_wire as wire
    from backend.ai.models.cli_tool_protocol import render_tool_addendum

    bare = re.compile(r"(?<!mcp__lucius__)\buse_tool\b")
    spoken = [
        rnd.instructions("SYS"),
        render_tool_addendum([TOOL_B], via_tool=wire.CALLABLE),
        *(t["description"] for t in wire.tool_payload([TOOL_A])),
    ]
    for text in spoken:
        assert not bare.search(text), text
    assert wire.CALLABLE in spoken[0] and wire.CALLABLE in spoken[1]
    assert "find_tools" in spoken[0], "told to load a tool before calling it"


async def test_a_call_without_a_tool_use_id_is_still_matched_by_name(tmp_path, provider):
    """A CLI that drops `claudecode/toolUseId` must not turn every host-run call into a failure
    the model then retries: the answered call is found again by its name."""
    messages = [
        _user("NOMETA CALLS: " + json.dumps([["list_accounts", {}], ["get_spending_summary", {}]]))
    ]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])
    second = await _round(provider, messages + _after(first), [TOOL_A, TOOL_B])

    text = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert text == "got: result of list_accounts | result of get_spending_summary"
    assert len(_called(first)) == 2 and not _called(second), "each call ran once"
    assert len(_rows(tmp_path, "spawns.jsonl")) == 1


async def test_a_call_the_cli_cancelled_is_rebuilt_from_the_transcript_not_retried(
    tmp_path, provider
):
    """The host ran the call; the CLI cancelled it and told the model it failed. The process is
    retired and the round starts again from the transcript, where the model reads the REAL result
    — and the host is never asked to run that call a second time."""
    messages = [_user("CANCEL CALLS: " + json.dumps([["list_accounts", {}]]))]
    first = await _round(provider, messages, [TOOL_A])
    second = await _round(provider, messages + _after(first), [TOOL_A])

    text = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert text == "recovered from transcript", "the model never saw the cancellation"
    assert "cancelled" not in text
    assert not _called(second), "the host runs the call once"
    assert len(_rows(tmp_path, "spawns.jsonl")) == 2
    assert "[result of " in _users(tmp_path)[-1], "history carries the real result"


async def test_malformed_control_frames_are_answered_not_fatal(tmp_path):
    from backend.ai.models.claude_native_session import NativeSession

    sent: list[dict] = []
    session = NativeSession(["claude"], {}, str(tmp_path))

    async def capture(payload):
        sent.append(payload)

    session._send = capture
    for junk in (
        {"type": "control_request", "request_id": "a", "request": "junk"},
        {
            "type": "control_request",
            "request_id": "b",
            "request": {"subtype": "mcp_message", "server_name": "lucius", "message": "junk"},
        },
        {
            "type": "control_request",
            "request_id": "c",
            "request": {
                "subtype": "mcp_message",
                "server_name": "lucius",
                "message": {
                    "id": 1,
                    "method": "tools/call",
                    "params": [1],
                },
            },
        },
        {
            "type": "control_request",
            "request_id": "d",
            "request": {
                "subtype": "mcp_message",
                "server_name": "lucius",
                "message": {"id": 2, "method": "tools/call", "params": {"_meta": "x"}},
            },
        },
        {"type": "control_cancel_request", "request_id": None},
    ):
        assert await session.intercept(junk) is True
    assert len(sent) == 4 and sent[0]["response"]["subtype"] == "error"
    assert not session.waiting


def test_a_tool_use_without_an_id_is_skipped_not_a_crash():
    from backend.ai.models import claude_native_round as rnd

    r = rnd.Round(frozenset({"list_accounts"}))
    blocks = [
        {"type": "tool_use", "name": "mcp__lucius__list_accounts", "input": {}},
        {"type": "tool_use", "id": 5, "name": "mcp__lucius__list_accounts", "input": {}},
        "junk",
    ]
    assert list(rnd._assistant({"content": blocks}, r)) == []
    assert list(rnd._assistant({"content": "not a list"}, r)) == []


async def test_a_result_the_cli_never_asked_for_is_rebuilt_not_left_to_a_retry(tmp_path, provider):
    messages = [
        _user("SKIP CALLS: " + json.dumps([["list_accounts", {}], ["get_spending_summary", {}]]))
    ]
    first = await _round(provider, messages, [TOOL_A, TOOL_B])
    second = await _round(provider, messages + _after(first), [TOOL_A, TOOL_B])

    text = "".join(e.get("text", "") for e in second if e["type"] == "text_delta")
    assert text == "recovered from transcript" and not _called(second)
    assert len(_rows(tmp_path, "spawns.jsonl")) == 2
