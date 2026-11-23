"""Native tool calls on the Claude Code CLI (flag ``claude_cli_native_tools``) — no real CLI.

A fake ``ClaudeSDKClient`` plays the CLI: it streams what the real one streamed in a live
spike (``message_start`` … ``tool_use`` block … ``message_delta`` stop_reason ``tool_use``), then
calls the ``PostToolUse`` hook the way the CLI does and blocks until the HOST answers. That is
the whole contract under test: the round yields a normal ``tool_use`` (bare name, the CLI's id)
and ends; the host's result — not anything the model or the placeholder says — reaches the same
live query on the next round.
"""

from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock
from claude_agent_sdk.types import StreamEvent, SystemMessage

from backend.ai.models import claude_sdk_live as live_mod
from backend.ai.models import claude_sdk_tools as tools_mod
from backend.ai.models.claude_cli_provider import ClaudeCliProvider
from backend.ai.models.claude_sdk_provider import ClaudeSdkProvider
from backend.core.errors import ExternalServiceError

NET_WORTH = {
    "name": "get_net_worth",
    "description": "The user's net worth.",
    "input_schema": {"type": "object", "properties": {"currency": {"type": "string"}}},
}
SEND = {
    "name": "email_send_message",
    "description": "Send a message.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
USER = {"role": "user", "content": "what is my net worth?"}


def _stream(event: dict) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event=event)


def _text_round(text: str):
    yield SystemMessage(subtype="init", data={"apiKeySource": "none"})
    yield _stream(
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 7}},
        }
    )
    yield _stream({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})
    yield _stream(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5},
        }
    )


def _call_round(*calls: tuple[str, str, dict]):
    yield _stream({"type": "message_start", "message": {"usage": {"input_tokens": 2}}})
    for call_id, name, args in calls:
        yield _stream({"type": "content_block_start", "content_block": {"type": "tool_use"}})
        yield AssistantMessage(
            content=[ToolUseBlock(id=call_id, name=tools_mod.PREFIX + name, input=args)],
            model="claude-haiku-5-5",
        )
    yield _stream(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 4},
        }
    )


def _result(cost: float = 0.01) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=2,
        session_id="s",
        stop_reason="end_turn",
        total_cost_usd=cost,
        usage={},
        result="done",
    )


class FakeCli:
    """Records every client it is asked to build, and scripts what each one streams."""

    def __init__(self):
        self.clients: list = []
        self.script = None

    def __call__(self, options):
        client = _Client(options, self)
        self.clients.append(client)
        return client


class _Client:
    def __init__(self, options, owner: FakeCli):
        self.options = options
        self.owner = owner
        self.prompts: list = []
        self.answers: dict[str, list] = {}
        self.disconnected = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.disconnected.set()
        return False

    async def query(self, prompt):
        async for message in prompt:
            self.prompts.append(message)

    async def hook(self, call_id: str, name: str):
        matcher = self.options.hooks["PostToolUse"][0]
        out = await matcher.hooks[0](
            {"tool_name": tools_mod.PREFIX + name, "tool_input": {}}, call_id, None
        )
        self.answers[call_id] = out["hookSpecificOutput"]["updatedMCPToolOutput"]

    async def receive_messages(self):
        async for message in self.owner.script(self):
            yield message


@pytest.fixture
def cli(monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(live_mod, "ClaudeSDKClient", fake)
    monkeypatch.setattr(ClaudeSdkProvider, "_resolve_cli", lambda self: "/fake/claude")
    yield fake


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    for live in list({id(v): v for v in live_mod._parked.values()}.values()):
        live.close()


async def _round(provider, messages, tools, model="haiku"):
    return [e async for e in provider.stream_chat(messages, tools, "You are Lucius.", model)]


def _types(events):
    return [e["type"] for e in events]


async def test_a_call_is_yielded_by_its_bare_name_and_the_round_parks(cli):
    async def script(client):
        for m in _text_round("Checking."):
            yield m
        for m in _call_round(("toolu_1", "get_net_worth", {"currency": "USD"})):
            yield m
        await client.hook("toolu_1", "get_net_worth")
        yield _result()

    cli.script = script
    events = await _round(ClaudeSdkProvider(), [USER], [NET_WORTH])

    calls = [e for e in events if e["type"] == "tool_use"]
    assert calls == [
        {"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {"currency": "USD"}}
    ]
    assert _types(events)[-2:] == ["usage", "done"]
    assert events[-1]["stop_reason"] == "tool_use"
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "Checking."
    assert live_mod.parked("toolu_1") is not None


async def test_the_hosts_result_resolves_the_parked_call_on_the_same_query(cli):
    async def script(client):
        for m in _call_round(("toolu_1", "get_net_worth", {})):
            yield m
        await client.hook("toolu_1", "get_net_worth")
        for m in _text_round("You have 123."):
            yield m
        yield _result(0.05)

    cli.script = script
    provider = ClaudeSdkProvider()
    first = await _round(provider, [USER], [NET_WORTH])
    assistant = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {}}],
    }
    results = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "123 USD"}],
    }
    second = await _round(provider, [USER, assistant, results], [NET_WORTH])

    assert len(cli.clients) == 1
    assert cli.clients[0].answers["toolu_1"] == [{"type": "text", "text": "123 USD"}]
    assert "".join(e["text"] for e in second if e["type"] == "text_delta") == "You have 123."
    assert second[-1] == {"type": "done", "stop_reason": "end_turn"}
    assert first[-1]["stop_reason"] == "tool_use"
    assert live_mod.parked("toolu_1") is None


async def test_the_last_round_settles_the_cost_the_cli_reports_for_the_whole_query(cli):
    async def script(client):
        for m in _call_round(("toolu_1", "get_net_worth", {})):
            yield m
        await client.hook("toolu_1", "get_net_worth")
        for m in _text_round("ok"):
            yield m
        yield _result(0.05)

    cli.script = script
    provider = ClaudeSdkProvider()
    first = await _round(provider, [USER], [NET_WORTH])
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "1"}],
        },
    ]
    second = await _round(provider, follow, [NET_WORTH])
    usage = (
        [e for e in first if e["type"] == "usage"][0],
        [e for e in second if e["type"] == "usage"][0],
    )
    assert usage[0]["cost_reported"] is False
    assert usage[1]["cost_reported"] is True
    assert usage[0]["cost_usd"] + usage[1]["cost_usd"] == pytest.approx(0.05, abs=1e-9)
    assert usage[1]["input_tokens"] == 3 and usage[1]["cache_read_tokens"] == 7


async def test_parallel_calls_in_one_round_are_all_yielded_and_all_answered(cli):
    async def script(client):
        for m in _call_round(
            ("toolu_a", "get_net_worth", {"currency": "USD"}),
            ("toolu_b", "get_net_worth", {"currency": "EUR"}),
        ):
            yield m
        await asyncio.gather(
            client.hook("toolu_a", "get_net_worth"), client.hook("toolu_b", "get_net_worth")
        )
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    first = await _round(provider, [USER], [NET_WORTH])
    assert [e["id"] for e in first if e["type"] == "tool_use"] == ["toolu_a", "toolu_b"]
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "get_net_worth", "input": {}},
                {"type": "tool_use", "id": "toolu_b", "name": "get_net_worth", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_b", "content": "EUR 9"},
                {"type": "tool_result", "tool_use_id": "toolu_a", "content": "USD 10"},
            ],
        },
    ]
    await _round(provider, follow, [NET_WORTH])
    answers = cli.clients[0].answers
    assert answers["toolu_a"][0]["text"] == "USD 10"
    assert answers["toolu_b"][0]["text"] == "EUR 9"


async def test_a_failed_host_call_stays_failed_for_the_model(cli):
    async def script(client):
        for m in _call_round(("toolu_1", "email_send_message", {})):
            yield m
        await client.hook("toolu_1", "email_send_message")
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    await _round(provider, [USER], [SEND])
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "email_send_message", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "Denied by the user.",
                    "is_error": True,
                }
            ],
        },
    ]
    await _round(provider, follow, [SEND])
    assert cli.clients[0].answers["toolu_1"][0]["text"] == "Tool error: Denied by the user."


async def test_the_resume_after_an_approval_continues_even_at_another_effort(cli):
    """The resolve endpoint carries no effort: the profile default must not cost the query."""

    async def script(client):
        for m in _call_round(("toolu_1", "get_net_worth", {})):
            yield m
        await client.hook("toolu_1", "get_net_worth")
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    stream = provider.stream_chat([USER], [NET_WORTH], "You are Lucius.", "haiku", "high")
    [e async for e in stream]
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "1"}],
        },
    ]
    [
        e
        async for e in provider.stream_chat(
            follow, [NET_WORTH], "You are Lucius.", "haiku", "medium"
        )
    ]
    assert len(cli.clients) == 1
    assert cli.clients[0].options.effort == "high"


async def test_the_hook_waits_longer_than_the_park_limit(cli, monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_SDK_PARK_SECONDS", "900")

    async def script(client):
        for m in _text_round("hi"):
            yield m
        yield _result()

    cli.script = script
    await _round(ClaudeSdkProvider(), [USER], [NET_WORTH])
    matcher = cli.clients[0].options.hooks["PostToolUse"][0]
    assert matcher.timeout >= 900 + 60


async def test_a_missing_result_never_resumes_it_a_fresh_query_starts(cli):
    """A call still awaiting approval: its result is absent, so the parked query is closed and
    the round is rebuilt from the transcript — never answered with a guess."""

    async def script(client):
        if len(cli.clients) == 1:
            for m in _call_round(("toolu_1", "email_send_message", {})):
                yield m
            await client.hook("toolu_1", "email_send_message")
        else:
            for m in _text_round("fresh"):
                yield m
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    await _round(provider, [USER], [SEND])
    first = cli.clients[0]
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "email_send_message", "input": {}}
            ],
        },
        {"role": "user", "content": "never mind"},
    ]
    events = await _round(provider, follow, [SEND])

    assert len(cli.clients) == 2
    await asyncio.wait_for(first.disconnected.wait(), 2)
    assert "toolu_1" not in first.answers
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "fresh"


async def test_a_tool_loaded_mid_turn_starts_a_fresh_query_that_offers_it(cli):
    async def script(client):
        if len(cli.clients) == 1:
            for m in _call_round(("toolu_1", "get_net_worth", {})):
                yield m
            await client.hook("toolu_1", "get_net_worth")
        else:
            for m in _text_round("done"):
                yield m
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    await _round(provider, [USER], [NET_WORTH])
    follow = [
        USER,
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_net_worth", "input": {}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "1"}],
        },
    ]
    await _round(provider, follow, [NET_WORTH, SEND])

    assert len(cli.clients) == 2
    text = cli.clients[1].prompts[0]["message"]["content"][0]["text"]
    assert "[called get_net_worth (id toolu_1)" in text and "<tool_call>" not in text


async def test_stopping_a_round_releases_the_parked_call_and_the_cli(cli):
    async def script(client):
        for m in _text_round("thinking…"):
            yield m
        await asyncio.sleep(30)
        yield _result()

    cli.script = script
    provider = ClaudeSdkProvider()
    stream = provider.stream_chat([USER], [NET_WORTH], "sys", "haiku")
    assert (await stream.__anext__())["type"] == "text_delta"
    await stream.aclose()
    await asyncio.wait_for(cli.clients[0].disconnected.wait(), 2)


async def test_a_parked_query_that_is_never_claimed_expires(cli, monkeypatch):
    monkeypatch.setenv("ONW_CLAUDE_SDK_PARK_SECONDS", "1")

    async def script(client):
        for m in _call_round(("toolu_1", "get_net_worth", {})):
            yield m
        with pytest.raises(asyncio.CancelledError):
            await client.hook("toolu_1", "get_net_worth")

    cli.script = script
    await _round(ClaudeSdkProvider(), [USER], [NET_WORTH])
    assert live_mod.parked("toolu_1") is not None
    await asyncio.wait_for(cli.clients[0].disconnected.wait(), 3)
    assert live_mod.parked("toolu_1") is None


async def test_a_cli_that_ends_without_a_result_is_an_error_not_a_hang(cli):
    async def script(client):
        for m in _text_round("half"):
            yield m

    cli.script = script
    with pytest.raises(ExternalServiceError, match="without a result"):
        await _round(ClaudeSdkProvider(), [USER], [NET_WORTH])


async def test_an_error_result_is_raised_with_the_clis_words(cli):
    async def script(client):
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="s",
            result="Not logged in · Please run /login",
        )

    cli.script = script
    with pytest.raises(ExternalServiceError):
        await _round(ClaudeSdkProvider(), [USER], [NET_WORTH])


async def test_no_tools_is_the_text_providers_round_untouched(cli, monkeypatch):
    seen = {}

    async def fake_super(self, messages, tools, system, model, effort="medium"):
        seen["args"] = (messages, tools, system, model)
        yield {"type": "done"}

    monkeypatch.setattr(ClaudeCliProvider, "stream_chat", fake_super)
    events = await _round(ClaudeSdkProvider(), [USER], [])
    assert events == [{"type": "done"}] and seen["args"][1] == []
    assert cli.clients == []


async def test_the_query_is_locked_down(cli, monkeypatch):
    monkeypatch.setenv("ONW_SECRET_SETTING", "hunter2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "abc")

    async def script(client):
        for m in _text_round("hi"):
            yield m
        yield _result()

    cli.script = script
    await _round(ClaudeSdkProvider(api_key=""), [USER], [NET_WORTH, SEND])
    options = cli.clients[0].options

    assert options.tools == []
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.allowed_tools == ["mcp__lucius__*"]
    assert set(options.mcp_servers) == {"lucius"}
    assert options.env["ONW_SECRET_SETTING"] == ""
    assert options.env["AWS_SECRET_ACCESS_KEY"] == ""
    assert options.env["ENABLE_TOOL_SEARCH"] == "false"
    assert "ANTHROPIC_API_KEY" not in options.env or not options.env["ANTHROPIC_API_KEY"]

    deny = options.hooks["PreToolUse"][0].hooks[0]
    ours = await deny({"tool_name": "mcp__lucius__get_net_worth"}, "t", None)
    other = await deny({"tool_name": "Bash"}, "t", None)
    foreign = await deny({"tool_name": "mcp__lucius__not_offered"}, "t", None)
    assert ours == {}
    assert other["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert foreign["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_long_or_odd_tool_name_gets_a_stable_wire_name():
    long = "rest__swagger_petstore__" + "x" * 60
    wire = tools_mod.wire_name(long)
    assert len(tools_mod.PREFIX + wire) <= 64 and wire == tools_mod.wire_name(long)
    assert tools_mod.wire_name("get_net_worth") == "get_net_worth"
    assert tools_mod.host_names([{"name": long}]) == {wire: long}


def test_a_tool_result_with_an_image_keeps_it():
    parts = tools_mod.tool_content(
        [
            {"type": "text", "text": "chart"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "QQ"},
            },
        ],
        False,
    )
    assert parts[1] == {"type": "image", "data": "QQ", "mimeType": "image/png"}


@pytest.mark.parametrize("flag_on", [False, True])
def test_the_flag_chooses_the_provider_at_the_one_seam(flag_on):
    from backend.ai import assistant
    from tests.flags import flags

    with flags(claude_cli_native_tools=flag_on):
        provider = assistant._build_provider(transport="claude_cli", base_url="", api_key="")
    assert (type(provider) is ClaudeSdkProvider) is flag_on
    assert isinstance(provider, ClaudeCliProvider)
