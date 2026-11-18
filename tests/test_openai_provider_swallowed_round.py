"""A round the upstream host BILLED but delivered nothing for is retried once on another host.

Live, 2026-09-22 03:43–03:50, run_47f8605fdd98 (z-ai/glm-5.3-flash over OpenRouter): four
follow-up turns each ended on a round that reported 15–17 completion tokens — one real tool
call's worth — with ``content: ""``, no reasoning, no ``tool_calls`` and ``finish_reason:
stop``. Replayed offline against the same conversation: the host OpenRouter had routed to
(``provider: "CoreWeave"``) did it 3/3; with that host excluded, the very same request came
back as a proper ``search_docs`` tool call 2/2. The model was calling a tool every time; the
serving stack in front of it ate the call and reported a clean stop.

This is a TRANSPORT retry, not the capability fallback the RFC keeps out of adapters
(``tests/test_char_openai_provider_reactive_image_strip.py``): same model, same messages,
same tools — the only change is an OpenRouter routing hint excluding the host that just
swallowed the output, and only when the response actually named one. Bounded to one retry.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from backend.ai.models.openai_provider import OpenAIProvider


def _usage(completion: int, prompt: int = 1000, cost: float = 0.01):
    return NS(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=NS(cached_tokens=0),
        cost=cost,
    )


def _chunk(*, content=None, tool_calls=None, finish=None, usage=None, provider=None):
    delta = NS(content=content, reasoning=None, tool_calls=tool_calls or [])
    choice = NS(delta=delta, finish_reason=finish)
    chunk = NS(choices=[choice], usage=usage)
    if provider is not None:
        chunk.provider = provider
    return chunk


def _swallowed(provider="CoreWeave"):
    """What CoreWeave returned, verbatim in shape: two chunks, empty content, stop, 17 billed."""
    return [
        _chunk(content="", finish="stop", provider=provider),
        _chunk(content="", finish="stop", provider=provider, usage=_usage(17)),
    ]


def _real_call(provider="OpenInference", *, finish="tool_calls", arguments='{"query": "x"}'):
    tc = NS(index=0, id="call_1", function=NS(name="search_docs", arguments=arguments))
    return [
        _chunk(content=" ", tool_calls=[tc], provider=provider),
        _chunk(finish=finish, provider=provider, usage=_usage(25)),
    ]


class _Stream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def close(self):
        self.closed = True


class _Completions:
    def __init__(self, *streams, fail_after_first: Exception | None = None):
        self._streams = list(streams)
        self.calls: list[dict] = []
        self._fail_after_first = fail_after_first

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > 1 and self._fail_after_first is not None:
            raise self._fail_after_first
        return _Stream(self._streams.pop(0))


def _provider(completions: _Completions) -> OpenAIProvider:
    p = OpenAIProvider(base_url="https://openrouter.ai/api/v1", api_key="sk-test")
    p._client = NS(chat=NS(completions=completions))
    return p


async def _events(p: OpenAIProvider):
    return [
        ev
        async for ev in p.stream_chat(
            [{"role": "user", "content": "attach them"}], [], system="", model="z-ai/glm-5.3-flash"
        )
    ]


@pytest.mark.asyncio
async def test_a_swallowed_round_is_retried_once_with_that_host_excluded():
    completions = _Completions(_swallowed(), _real_call())
    events = await _events(_provider(completions))

    assert len(completions.calls) == 2
    retry = completions.calls[1]
    assert retry["extra_body"]["provider"] == {"ignore": ["CoreWeave"]}
    # Same request otherwise — no model switch, no rebuilt messages.
    assert retry["model"] == completions.calls[0]["model"]
    assert retry["messages"] == completions.calls[0]["messages"]

    tool_uses = [e for e in events if e["type"] == "tool_use"]
    assert [t["name"] for t in tool_uses] == ["search_docs"]
    assert events[-1] == {"type": "done", "stop_reason": "tool_use"}


@pytest.mark.asyncio
async def test_the_swallowed_attempt_is_still_paid_for():
    """We were billed for both rounds; the usage event must say so."""
    completions = _Completions(_swallowed(), _real_call())
    events = await _events(_provider(completions))
    usage = [e for e in events if e["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["output_tokens"] == 17 + 25
    assert usage[0]["input_tokens"] == 2000
    assert usage[0]["cost_usd"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_an_existing_ignore_list_is_extended_not_replaced():
    completions = _Completions(_swallowed(), _real_call())
    p = _provider(completions)
    p._cache_routing_fields = lambda *a, **k: {
        "provider": {"ignore": ["SomeoneElse"], "order": ["A"]}
    }
    await _events(p)
    assert completions.calls[1]["extra_body"]["provider"] == {
        "ignore": ["SomeoneElse", "CoreWeave"],
        "order": ["A"],
    }


@pytest.mark.asyncio
async def test_only_one_retry_even_if_the_second_host_swallows_too():
    completions = _Completions(_swallowed("CoreWeave"), _swallowed("OtherHost"))
    events = await _events(_provider(completions))
    assert len(completions.calls) == 2
    # Nothing delivered either time: the loop's own empty-reply gate takes it from here.
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}
    assert not [e for e in events if e["type"] in ("text_delta", "tool_use")]


@pytest.mark.asyncio
async def test_no_retry_when_the_response_names_no_host():
    """A plain OpenAI-compatible endpoint (no OpenRouter `provider` field) has nothing to
    route around; an empty round there is simply an empty round."""
    completions = _Completions(_swallowed(provider=None))
    events = await _events(_provider(completions))
    assert len(completions.calls) == 1
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}


@pytest.mark.asyncio
async def test_no_retry_when_the_round_delivered_something():
    """Billed AND delivered is a normal round — even a one-space content delta counts."""
    completions = _Completions(_real_call())
    await _events(_provider(completions))
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_no_retry_when_nothing_was_billed():
    """Zero completion tokens and nothing delivered is an empty round, not a swallowed one."""
    completions = _Completions([_chunk(content="", finish="stop", provider="X", usage=_usage(0))])
    await _events(_provider(completions))
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_a_failed_retry_falls_back_to_the_empty_round():
    """If OpenRouter has no other host ("No endpoints found"), the retry must not turn a
    silent round into a crashed turn — surface the empty round and let the gate nudge."""
    completions = _Completions(_swallowed(), fail_after_first=RuntimeError("No endpoints found"))
    events = await _events(_provider(completions))
    assert len(completions.calls) == 2
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("stop", "tool_use"),
        (None, "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "end_turn"),
    ],
)
async def test_structured_tool_call_finish_reason(finish, expected):
    events = await _events(_provider(_Completions(_real_call(finish=finish))))

    calls = [event for event in events if event["type"] in ("tool_use_start", "tool_use")]
    if expected == "tool_use":
        assert calls == [
            {"type": kind, "id": "call_1", "name": "search_docs", "input": {"query": "x"}}
            for kind in ("tool_use_start", "tool_use")
        ]
    else:
        assert not calls
    assert events[-1] == {"type": "done", "stop_reason": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [None, "stop", "tool_calls"])
async def test_incomplete_arguments_are_not_emitted(finish):
    completions = _Completions(_real_call(finish=finish, arguments='{"query":'))
    events = await _events(_provider(completions))

    assert not any(event["type"].startswith("tool_use") for event in events)
    assert events[-1] == {"type": "done", "stop_reason": "end_turn"}


@pytest.mark.asyncio
async def test_gemini_calls_without_indexes_keep_distinct_arguments_and_signatures():
    first = NS(
        id="call_1",
        function=NS(name="list_accounts", arguments="{}"),
        extra_content={"google": {"thought_signature": "signature"}},
    )
    second = NS(id="call_2", function=NS(name="find_tools", arguments='{"query":'))
    tail = NS(id=None, function=NS(name=None, arguments='"accounts"}'))
    events = await _events(
        _provider(
            _Completions(
                [
                    _chunk(tool_calls=[first, second]),
                    _chunk(tool_calls=[tail], finish="stop"),
                ]
            )
        )
    )

    assert [event for event in events if event["type"] == "tool_use"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "list_accounts",
            "input": {},
            "google_thought_signature": "signature",
        },
        {"type": "tool_use", "id": "call_2", "name": "find_tools", "input": {"query": "accounts"}},
    ]
    assert events[-1] == {"type": "done", "stop_reason": "tool_use"}
