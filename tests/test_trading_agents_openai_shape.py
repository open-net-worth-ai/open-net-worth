"""TradingAgents bridge: the pure OpenAI ⇄ ONW translation — synthetic data only (ticker ACME)."""

from __future__ import annotations

import json

import pytest

from backend.ai.trading_agents import openai_errors
from backend.ai.trading_agents import openai_shape as shape
from backend.core.errors import (
    BudgetExceededError,
    ConfigurationError,
    ExternalServiceError,
    ProviderTimeoutError,
    ValidationError,
)

QUOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_quote",
        "description": "Quote for a ticker",
        "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}},
    },
}


def _body(messages, **extra):
    return {"model": "onw-quick", "messages": messages, **extra}


def test_system_messages_become_the_system_string_and_text_collapses():
    request = shape.to_onw_request(
        _body(
            [
                {"role": "system", "content": "You are an analyst."},
                {"role": "developer", "content": "Be brief."},
                {"role": "user", "content": "Outlook for ACME?"},
            ]
        )
    )
    assert request.system == "You are an analyst.\n\nBe brief."
    assert request.messages == [{"role": "user", "content": "Outlook for ACME?"}]
    assert request.tools == [] and request.structured_tool is None


def test_a_tool_round_trip_keeps_calls_and_results_paired():
    request = shape.to_onw_request(
        _body(
            [
                {"role": "user", "content": "Quote ACME."},
                {
                    "role": "assistant",
                    "content": "Looking it up.",
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "get_quote", "arguments": '{"ticker": "ACME"}'},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "get_quote", "arguments": "{}"},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_a", "content": "ACME 10.00"},
                {"role": "tool", "tool_call_id": "call_b", "content": "n/a"},
                {"role": "user", "content": "Now conclude."},
            ],
            tools=[QUOTE_TOOL],
        )
    )
    assert request.tools == [
        {
            "name": "get_quote",
            "description": "Quote for a ticker",
            "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}},
        }
    ]
    user, assistant, results = request.messages
    assert assistant["content"][0] == {"type": "text", "text": "Looking it up."}
    assert assistant["content"][1] == {
        "type": "tool_use",
        "id": "call_a",
        "name": "get_quote",
        "input": {"ticker": "ACME"},
    }
    assert assistant["content"][2]["input"] == {}
    # Both results and the follow-up user text are ONE user message, results first.
    assert [b["type"] for b in results["content"]] == ["tool_result", "tool_result", "text"]
    assert results["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call_a",
        "content": "ACME 10.00",
    }
    assert user == {"role": "user", "content": "Quote ACME."}


def test_a_tool_without_parameters_gets_an_empty_object_schema():
    request = shape.to_onw_request(
        _body(
            [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "ping"}}],
        )
    )
    assert request.tools[0]["input_schema"] == {"type": "object", "properties": {}}


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": "hi"},
        {"messages": [{"role": "system", "content": "only system"}]},
        {"messages": [{"role": "user", "content": "x"}], "stream": True},
        {"messages": [{"role": "narrator", "content": "x"}]},
        {"messages": [{"role": "tool", "content": "orphan"}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "[1]"}}],
                }
            ]
        },
        {"messages": [{"role": "user", "content": "x"}], "tools": [{"type": "function"}]},
    ],
)
def test_requests_the_bridge_cannot_serve_are_validation_errors(body):
    with pytest.raises(ValidationError):
        shape.to_onw_request(body)


def test_response_format_json_schema_becomes_a_forced_single_tool():
    schema = {"type": "object", "properties": {"rating": {"type": "string"}}}
    request = shape.to_onw_request(
        _body(
            [{"role": "user", "content": "Rate ACME."}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "Decision", "schema": schema, "strict": True},
            },
        )
    )
    assert request.structured_tool == "Decision"
    assert request.tools[-1]["name"] == "Decision" and request.tools[-1]["input_schema"] == schema
    assert "`Decision`" in request.system

    reply = shape.Reply(
        tool_uses=[{"id": "x", "name": "Decision", "input": {"rating": "Hold"}}], done=True
    )
    completion = shape.chat_completion(reply, request, alias="onw-deep", call_id="c1", created=1)
    choice = completion["choices"][0]
    assert json.loads(choice["message"]["content"]) == {"rating": "Hold"}
    assert "tool_calls" not in choice["message"] and choice["finish_reason"] == "stop"


def test_response_format_json_object_uses_a_permissive_schema_and_plain_text_still_returns():
    request = shape.to_onw_request(
        _body([{"role": "user", "content": "x"}], response_format={"type": "json_object"})
    )
    assert request.tools[-1]["input_schema"]["additionalProperties"] is True
    reply = shape.Reply(text="I would hold.", done=True)
    message = shape.chat_completion(reply, request, alias="onw-deep", call_id="c", created=1)[
        "choices"
    ][0]["message"]
    assert message["content"] == "I would hold."


def test_an_unknown_response_format_is_refused():
    with pytest.raises(ValidationError):
        shape.to_onw_request(
            _body([{"role": "user", "content": "x"}], response_format={"type": "grammar"})
        )


def test_events_fold_into_a_tool_call_completion_with_summed_usage():
    builder = shape.ReplyBuilder()
    for event in (
        {"type": "thinking_delta", "text": "hmm"},
        {"type": "text_delta", "text": "Checking "},
        {"type": "text_delta", "text": "ACME."},
        {"type": "tool_use_start", "id": "t1", "name": "get_quote"},
        {"type": "tool_use", "id": "t1", "name": "get_quote", "input": {"ticker": "ACME"}},
        {"type": "tool_use", "id": "t1", "name": "get_quote", "input": {"ticker": "ACME"}},
        {
            "type": "usage",
            "input_tokens": 10,
            "cache_creation_tokens": 5,
            "cache_read_tokens": 3,
            "output_tokens": 7,
            "cost_usd": 0.01,
            "cost_basis": "plan_included",
        },
        {"type": "done", "stop_reason": "tool_use"},
    ):
        builder.add(event)
    request = shape.OnwRequest(system="", messages=[], tools=[])
    completion = shape.chat_completion(
        builder.reply, request, alias="onw-quick", call_id="onwcall_1", created=5
    )
    assert completion["model"] == "onw-quick" and completion["id"] == "onwcall_1"
    choice = completion["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "Checking ACME."
    first, second = choice["message"]["tool_calls"]
    assert first["id"] == "t1" and first["type"] == "function"
    assert json.loads(first["function"]["arguments"]) == {"ticker": "ACME"}
    assert second["id"] != "t1"  # a duplicate id from a provider is re-minted
    assert completion["usage"] == {"prompt_tokens": 18, "completion_tokens": 7, "total_tokens": 25}
    assert builder.reply.usage.cost_usd == 0.01
    assert builder.reply.usage.cost_basis == "plan_included"


def test_a_plain_answer_has_stop_and_truncation_is_length():
    request = shape.OnwRequest(system="", messages=[], tools=[])
    done = shape.Reply(text="Hold.", done=True, stop_reason="end_turn")
    assert (
        shape.chat_completion(done, request, alias="a", call_id="c", created=1)["choices"][0][
            "finish_reason"
        ]
        == "stop"
    )
    cut = shape.Reply(text="Ho", done=True, stop_reason="max_tokens")
    assert (
        shape.chat_completion(cut, request, alias="a", call_id="c", created=1)["choices"][0][
            "finish_reason"
        ]
        == "length"
    )


def test_models_list_names_only_the_given_aliases():
    body = shape.models_list(["onw-deep", "onw-quick"])
    assert [m["id"] for m in body["data"]] == ["onw-deep", "onw-quick"]


class _Upstream(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("secret sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 leaked")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (_Upstream(429), 429, "rate_limit_exceeded"),
        (_Upstream(400), 400, "invalid_request"),
        (_Upstream(500), 502, "upstream_error"),
        (ExternalServiceError("Claude Code CLI hit its usage limit."), 429, "rate_limit_exceeded"),
        (ExternalServiceError("Codex crashed."), 502, "upstream_error"),
        (ProviderTimeoutError("Timed out after 300s."), 502, "upstream_timeout"),
        (ConfigurationError("No model is configured."), 502, "upstream_error"),
        (ValidationError("bad"), 400, "invalid_request"),
        (BudgetExceededError("cap"), 400, "run_cap_reached"),
        (RuntimeError("anything"), 502, "upstream_error"),
    ],
)
def test_provider_failures_map_to_the_right_status(exc, status, code):
    got_status, body = openai_errors.classify_error(exc)
    assert (got_status, body["error"]["code"]) == (status, code)


def test_error_messages_never_carry_a_key_or_an_unknown_exceptions_text():
    _, body = openai_errors.classify_error(_Upstream(500))
    assert "sk-ant" not in json.dumps(body) and "leaked" not in json.dumps(body)
    message = openai_errors.sanitize(
        "Bearer abcdefghijklmnop failed at https://u:p@host.example/x?k=v\n" * 20
    )
    assert "abcdefghijklmnop" not in message and "u:p@" not in message and "\n" not in message
    assert len(message) <= 300


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "tool_calls": "oops"},
        {"role": "assistant", "tool_calls": ["not-an-object"]},
        {"role": "assistant", "tool_calls": [None]},
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": "not-an-object"}]},
        {"role": "assistant", "tool_calls": {"id": "c1"}},
        {"role": "assistant", "content": 7},
        {"role": "assistant", "content": {"text": "x"}},
        {"role": "user", "content": [None]},
        {"role": "tool", "tool_call_id": "c1", "content": {"a": 1}},
    ],
)
def test_a_malformed_assistant_or_tool_message_is_a_validation_error_not_a_crash(message):
    body = {"messages": [{"role": "user", "content": "hi"}, message]}
    with pytest.raises(ValidationError):
        shape.to_onw_request(body)


@pytest.mark.parametrize(
    "extra",
    [
        {"tools": "get_quote"},
        {"tools": 3},
        {"tools": [None]},
        {"response_format": {"type": "json_schema", "json_schema": ["x"]}},
        {"response_format": {"type": "json_schema", "json_schema": "x"}},
    ],
)
def test_malformed_tools_and_response_format_are_validation_errors_not_a_crash(extra):
    with pytest.raises(ValidationError):
        shape.to_onw_request({"messages": [{"role": "user", "content": "hi"}], **extra})
