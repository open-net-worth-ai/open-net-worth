"""Regression tests for INT1 — the 2026-07-15 xAI batch-submit incident.

Root cause: the batch dispatcher routed EVERY ``openai_compatible`` transport
unconditionally to the OpenAI-native batch adapter. When the owner switched to xAI
(``openai_compatible`` + ``base_url=https://api.x.ai/v1``) every intel scan's batch
submit failed with a 422 (xAI's ``/v1/batches`` requires ``name``, not
``endpoint``/``completion_window``) — the exception propagated all the way up and
``intel_agent`` gave up silently. Separately, ``intel_agent`` was leaking
Anthropic-shaped ``tools``/``tool_choice`` wire format through the (supposedly
provider-neutral) ``extra_params`` field, so even a fixed submit would have failed
against any OpenAI-shape chat body.

This file covers:
- ``routing.resolve_adapter_key`` — the capability matrix (pure, no mocking).
- ``dispatch`` — routes to the right adapter by ACTUAL config, prefixes/parses
  handles so a collect after a provider switch still finds the right adapter, and
  degrades an unrecognized openai_compatible endpoint to the sync fallback instead
  of guessing a wire format.
- ``BatchOutputSchema`` translation per adapter (Anthropic tool_use, OpenAI/xAI
  forced function-calling) + collect-side extraction back into ``BatchResult.text``.
- ``intel_agent`` no longer passes Anthropic wire format in ``extra_params``.

No network. Anything that would need a live key is ``@pytest.mark.integration``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock
from unittest.mock import patch as mpatch

import pytest

from backend.ai.batch import routing
from backend.core.llm_seam import BatchOutputSchema, BatchRequest, BatchResult

# ---------------------------------------------------------------------------
# routing.resolve_adapter_key — the capability matrix, pure function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport,base_url,expected",
    [
        ("anthropic", "", "anthropic"),
        # The exact July-9 incident config.
        ("openai_compatible", "https://api.x.ai/v1", "xai"),
        ("openai_compatible", "https://api.openai.com/v1", "openai"),
        # Every OTHER openai_compatible endpoint degrades to sync — batch is a cost
        # optimization, never a correctness dependency.
        ("openai_compatible", "https://generativelanguage.googleapis.com/v1beta/openai/", "sync"),
        ("openai_compatible", "http://localhost:11434/v1", "sync"),
        ("openai_compatible", "https://api.z.ai/api/paas/v4/", "sync"),
        ("openai_compatible", "https://openrouter.ai/api/v1", "sync"),
        ("openai_compatible", "http://localhost:4000", "sync"),
        ("openai_compatible", "http://my-custom-gateway.internal:9000", "sync"),
        ("openai_compatible", "", "sync"),
        ("unknown_transport", "https://api.x.ai/v1", "sync"),
    ],
)
def test_resolve_adapter_key_capability_matrix(transport, base_url, expected):
    assert routing.resolve_adapter_key(transport, base_url) == expected


def test_resolve_adapter_key_case_insensitive_host():
    """A base_url with an unusual case shouldn't dodge the xAI match."""
    assert routing.resolve_adapter_key("openai_compatible", "https://API.X.AI/v1") == "xai"


# ---------------------------------------------------------------------------
# dispatch — routes by ACTUAL config, prefixes/parses handles
# ---------------------------------------------------------------------------


def _fake_model_config(provider: str, base_url: str):
    fake = MagicMock()
    fake.resolved.return_value = {
        "provider": provider,
        "model": "some-model",
        "base_url": base_url,
        "api_key": "k",
    }
    return fake


def test_dispatch_submit_routes_xai_config_to_xai_adapter():
    """Replay of the exact 2026-07-09 live config: openai_compatible + api.x.ai — must
    route to xai_batch, NOT openai_batch."""
    from backend.ai.batch import dispatch

    called = {}

    async def _fake_xai_submit(task, requests):
        called["adapter"] = "xai"
        return "xai-real-handle-1"

    async def _fake_openai_submit(task, requests):
        called["adapter"] = "openai"
        return "openai-real-handle-1"

    with (
        mpatch(
            "backend.db.model_config_store.ModelConfigStore",
            lambda: _fake_model_config("openai_compatible", "https://api.x.ai/v1"),
        ),
        mpatch("backend.ai.batch.xai_batch.submit", _fake_xai_submit),
        mpatch("backend.ai.batch.openai_batch.submit", _fake_openai_submit),
    ):
        submit, _collect = dispatch.build_batch_hooks()
        handle = asyncio.run(submit("intel", [BatchRequest(custom_id="c1", prompt="p")]))

    assert called["adapter"] == "xai"
    assert handle == "xai:xai-real-handle-1"


def test_dispatch_submit_routes_real_openai_to_openai_adapter():
    from backend.ai.batch import dispatch

    async def _fake_openai_submit(task, requests):
        return "openai-real-handle-2"

    with (
        mpatch(
            "backend.db.model_config_store.ModelConfigStore",
            lambda: _fake_model_config("openai_compatible", "https://api.openai.com/v1"),
        ),
        mpatch("backend.ai.batch.openai_batch.submit", _fake_openai_submit),
    ):
        submit, _collect = dispatch.build_batch_hooks()
        handle = asyncio.run(submit("intel", [BatchRequest(custom_id="c1", prompt="p")]))

    assert handle == "openai:openai-real-handle-2"


def test_dispatch_submit_routes_anthropic_to_anthropic_adapter():
    from backend.ai.batch import dispatch

    async def _fake_anthropic_submit(task, requests):
        return "anthropic-real-handle"

    with (
        mpatch(
            "backend.db.model_config_store.ModelConfigStore",
            lambda: _fake_model_config("anthropic", ""),
        ),
        mpatch("backend.ai.batch.anthropic_batch.submit", _fake_anthropic_submit),
    ):
        submit, _collect = dispatch.build_batch_hooks()
        handle = asyncio.run(submit("intel", [BatchRequest(custom_id="c1", prompt="p")]))

    assert handle == "anthropic:anthropic-real-handle"


def test_dispatch_submit_unknown_openai_compatible_uses_sync_fallback():
    """An unrecognized openai_compatible endpoint (LiteLLM, a custom gateway, ...) must
    NOT be handed to openai_batch (which assumes the real OpenAI batch-lifecycle shape)
    — it degrades to the seam's sync per-request fallback instead."""
    from backend.ai.batch import dispatch

    openai_called = []

    async def _fake_openai_submit(task, requests):
        openai_called.append(True)
        return "should-not-be-called"

    async def _fake_complete(prompt, **_):
        return "sync reply"

    from backend.core import llm_seam as _seam

    orig_complete = _seam._complete
    try:
        _seam._complete = _fake_complete
        with (
            mpatch(
                "backend.db.model_config_store.ModelConfigStore",
                lambda: _fake_model_config("openai_compatible", "http://localhost:4000"),
            ),
            mpatch("backend.ai.batch.openai_batch.submit", _fake_openai_submit),
        ):
            submit, _collect = dispatch.build_batch_hooks()
            handle = asyncio.run(submit("intel", [BatchRequest(custom_id="c1", prompt="p")]))
    finally:
        _seam._complete = orig_complete

    assert openai_called == [], (
        "an unrecognized openai_compatible endpoint must not reach openai_batch"
    )
    assert handle.startswith(_seam.SYNC_HANDLE_PREFIX)


def test_dispatch_submit_request_pinned_to_another_provider_uses_sync_fallback():
    """A task model pinned to a provider other than the instance default (the default is
    Anthropic here) must not ride the DEFAULT's batch API with someone else's model id —
    it runs through the sync fallback, which resolves the ref to its own provider."""
    from backend.ai.batch import dispatch
    from backend.core import llm_seam as _seam
    from backend.db.model_config_store import ModelConfigStore

    ModelConfigStore().update_provider("openai", {"api_key": "sk-oai"})
    anthropic_called = []
    seen_models = []

    async def _fake_anthropic_submit(task, requests):
        anthropic_called.append(True)
        return "should-not-be-called"

    async def _fake_complete(prompt, *, model="", **_):
        seen_models.append(model)
        return "sync reply"

    orig_complete = _seam._complete
    try:
        _seam._complete = _fake_complete
        with mpatch("backend.ai.batch.anthropic_batch.submit", _fake_anthropic_submit):
            submit, _collect = dispatch.build_batch_hooks()
            req = BatchRequest(custom_id="c1", prompt="p", model="openai:gpt-4o-mini")
            handle = asyncio.run(submit("intel", [req]))
    finally:
        _seam._complete = orig_complete

    assert anthropic_called == []
    assert seen_models == ["openai:gpt-4o-mini"]
    assert handle.startswith(_seam.SYNC_HANDLE_PREFIX)


def test_dispatch_collect_parses_adapter_prefix():
    """A collect call routes by the HANDLE's own adapter prefix — not the current config
    — so a batch minted before a provider switch still resolves to the adapter that
    actually holds it."""
    from backend.ai.batch import dispatch

    async def _fake_xai_collect(handle):
        assert handle == "real-handle-xyz"
        return [BatchResult(custom_id="c1", text="ok")]

    # Config now points at Anthropic, but the in-flight handle was minted under xai.
    with (
        mpatch(
            "backend.db.model_config_store.ModelConfigStore",
            lambda: _fake_model_config("anthropic", ""),
        ),
        mpatch("backend.ai.batch.xai_batch.collect", _fake_xai_collect),
    ):
        _submit, collect = dispatch.build_batch_hooks()
        result = asyncio.run(collect("xai:real-handle-xyz"))

    assert result[0].custom_id == "c1"


def test_dispatch_collect_sync_handle_bypasses_adapter_lookup():
    from backend.ai.batch import dispatch
    from backend.core import llm_seam as _seam

    async def _fake_complete(prompt, **_):
        return "reply"

    orig_complete = _seam._complete
    try:
        _seam._complete = _fake_complete
        _submit, collect = dispatch.build_batch_hooks()
        handle = asyncio.run(_seam.sync_fallback_submit([BatchRequest(custom_id="s1", prompt="p")]))
        result = asyncio.run(collect(handle))
    finally:
        _seam._complete = orig_complete

    assert result[0].custom_id == "s1"
    assert result[0].text == "reply"


def test_dispatch_collect_unrecognized_prefix_returns_empty():
    from backend.ai.batch import dispatch

    _submit, collect = dispatch.build_batch_hooks()
    result = asyncio.run(collect("totally-unknown-handle-format"))
    assert result == []


@pytest.mark.parametrize(
    "crafted_handle",
    [
        "xai:../../etc/passwd",
        "xai:" + "a" * 200,  # absurd length
        "xai:has spaces",
        "xai:semi;colon",
    ],
)
def test_dispatch_collect_rejects_crafted_handle_before_adapter_call(crafted_handle):
    """Security LOW: a handle whose real-handle segment isn't plain alnum/underscore
    (path traversal, absurd length, injection characters) must never reach an
    adapter's URL-path interpolation — reject and degrade like an unrecognized
    prefix, never call the adapter."""
    from backend.ai.batch import dispatch

    adapter_called = []

    async def _fake_xai_collect(handle):
        adapter_called.append(handle)
        return []

    with mpatch("backend.ai.batch.xai_batch.collect", _fake_xai_collect):
        _submit, collect = dispatch.build_batch_hooks()
        result = asyncio.run(collect(crafted_handle))

    assert result == []
    assert adapter_called == [], (
        f"adapter must not be called with a crafted handle, got {adapter_called}"
    )


def test_dispatch_collect_accepts_well_formed_real_handle():
    """Sanity check the validator isn't over-broad — a normal provider id still works."""
    from backend.ai.batch import dispatch

    async def _fake_xai_collect(handle):
        assert handle == "msgbatch_01SQNbGTaPbdpATmXzBGsXbV"
        return [BatchResult(custom_id="c1", text="ok")]

    with mpatch("backend.ai.batch.xai_batch.collect", _fake_xai_collect):
        _submit, collect = dispatch.build_batch_hooks()
        result = asyncio.run(collect("xai:msgbatch_01SQNbGTaPbdpATmXzBGsXbV"))

    assert result[0].custom_id == "c1"


# ---------------------------------------------------------------------------
# BatchOutputSchema translation — the neutral structured-output contract
# ---------------------------------------------------------------------------

_SCHEMA = BatchOutputSchema(
    name="write_stories", description="test tool", schema={"type": "object", "properties": {}}
)


def test_openai_shape_build_chat_body_translates_output_schema_to_forced_function_tool():
    from backend.ai.batch._openai_shape import build_chat_body

    req = BatchRequest(custom_id="x", prompt="p", output_schema=_SCHEMA)
    body = build_chat_body(req, "gpt-4o-mini")

    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "write_stories",
                "description": "test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "write_stories"}}


def test_openai_shape_build_chat_body_no_schema_omits_tools():
    from backend.ai.batch._openai_shape import build_chat_body

    req = BatchRequest(custom_id="x", prompt="p")
    body = build_chat_body(req, "gpt-4o-mini")

    assert "tools" not in body
    assert "tool_choice" not in body


def test_openai_shape_extra_params_wins_over_output_schema():
    """Pin current (deliberate) behavior: when a caller sets BOTH output_schema AND
    extra_params, extra_params — applied last via dict.update — wins deterministically.
    No caller does this today; this just locks in the precedence so it can't silently
    flip in a future refactor."""
    from backend.ai.batch._openai_shape import build_chat_body

    req = BatchRequest(
        custom_id="x",
        prompt="p",
        output_schema=_SCHEMA,
        extra_params={"tool_choice": "auto", "tools": []},
    )
    body = build_chat_body(req, "gpt-4o-mini")

    assert body["tool_choice"] == "auto"
    assert body["tools"] == []


def test_openai_shape_extract_chat_text_prefers_tool_call_arguments():
    from backend.ai.batch._openai_shape import extract_chat_text

    message = {"content": None, "tool_calls": [{"function": {"arguments": '{"a": 1}'}}]}
    assert extract_chat_text(message) == '{"a": 1}'


def test_openai_shape_extract_chat_text_falls_back_to_content():
    from backend.ai.batch._openai_shape import extract_chat_text

    assert extract_chat_text({"content": "plain text"}) == "plain text"


def test_anthropic_batch_submit_translates_output_schema_to_forced_tool_use():
    """anthropic_batch.submit must translate BatchOutputSchema into Anthropic's own
    tools/tool_choice shape — never receive it pre-translated from the caller."""
    captured = {}

    class _FakeBatches:
        async def create(self, *, requests):
            captured["requests"] = requests
            return MagicMock(id="anthropic-batch-1")

    class _FakeClient:
        messages = MagicMock(batches=_FakeBatches())

    with (
        mpatch("anthropic.AsyncAnthropic", lambda **kw: _FakeClient()),
        mpatch("backend.db.provider_keys.effective_api_key", lambda provider: "fake-key"),
    ):
        from backend.ai.batch import anthropic_batch

        reqs = [
            BatchRequest(custom_id="a1", prompt="p", model="claude-test", output_schema=_SCHEMA)
        ]
        asyncio.run(anthropic_batch.submit("intel", reqs))

    params = captured["requests"][0]["params"]
    assert params["tools"] == [
        {
            "name": "write_stories",
            "description": "test tool",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert params["tool_choice"] == {"type": "tool", "name": "write_stories"}


def test_anthropic_batch_extra_params_wins_over_output_schema():
    """Same precedence pin as the OpenAI-shape adapter: extra_params applied last wins
    over an output_schema-derived tools/tool_choice."""
    captured = {}

    class _FakeBatches:
        async def create(self, *, requests):
            captured["requests"] = requests
            return MagicMock(id="anthropic-batch-2")

    class _FakeClient:
        messages = MagicMock(batches=_FakeBatches())

    with (
        mpatch("anthropic.AsyncAnthropic", lambda **kw: _FakeClient()),
        mpatch("backend.db.provider_keys.effective_api_key", lambda provider: "fake-key"),
    ):
        from backend.ai.batch import anthropic_batch

        reqs = [
            BatchRequest(
                custom_id="a2",
                prompt="p",
                model="claude-test",
                output_schema=_SCHEMA,
                extra_params={"tool_choice": "auto", "tools": []},
            )
        ]
        asyncio.run(anthropic_batch.submit("intel", reqs))

    params = captured["requests"][0]["params"]
    assert params["tool_choice"] == "auto"
    assert params["tools"] == []


# ---------------------------------------------------------------------------
# xai_batch — submit + collect against the documented (not OpenAI-shape) API
# ---------------------------------------------------------------------------


class _FakeXaiResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeXaiClient:
    """Minimal async httpx.AsyncClient stand-in — routes by path, records calls."""

    def __init__(self, post_map=None, get_map=None):
        self._post_map = post_map or {}
        self._get_map = get_map or {}
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, path, **kwargs):
        self.post_calls.append((path, kwargs))
        return self._post_map[path]

    async def get(self, path, **kwargs):
        self.get_calls.append((path, kwargs))
        # /batches/{id}/results is called possibly more than once (pagination); the
        # status endpoint /batches/{id} is called once. A get_map value may be a
        # callable(**kwargs) -> response for tests that need per-call dynamic
        # behavior (pagination sequences); otherwise it's returned as-is.
        resp = self._get_map[path]
        return resp(**kwargs) if callable(resp) else resp


def test_xai_batch_submit_sends_name_not_endpoint_or_completion_window():
    """The exact incident assertion: xAI's batch-create body must carry `name` and must
    NOT carry `endpoint`/`completion_window` (those are OpenAI-native fields xAI's API
    refuses per docs.x.ai/developers/advanced-api-usage/batch-api)."""
    fake_client = _FakeXaiClient(
        post_map={
            "/files": _FakeXaiResponse({"id": "file-xai-1"}),
            "/batches": _FakeXaiResponse({"batch_id": "batch-xai-1"}),
        }
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "model": "grok-4.5",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
    ):
        from backend.ai.batch import xai_batch

        reqs = [BatchRequest(custom_id="intel-1", prompt="p", output_schema=_SCHEMA)]
        handle = asyncio.run(xai_batch.submit("intel", reqs))

    assert handle == "batch-xai-1"
    batch_create_path, batch_create_kwargs = fake_client.post_calls[1]
    assert batch_create_path == "/batches"
    body = batch_create_kwargs["json"]
    assert "name" in body
    assert "endpoint" not in body
    assert "completion_window" not in body
    assert body["input_file_id"] == "file-xai-1"

    # The per-line JSONL body IS chat-completions shape, forced-tool-call included.
    jsonl_path, jsonl_kwargs = fake_client.post_calls[0]
    assert jsonl_path == "/files"
    jsonl_text = jsonl_kwargs["files"]["file"][1].decode("utf-8")
    line = json.loads(jsonl_text)
    assert line["url"] == "/v1/chat/completions"
    assert line["body"]["tool_choice"]["function"]["name"] == "write_stories"


def test_xai_batch_collect_pending_when_num_pending_positive():
    from backend.core.llm_seam import BatchPending

    fake_client = _FakeXaiClient(
        get_map={"/batches/batch-xai-2": _FakeXaiResponse({"state": {"num_pending": 3}})}
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
        "model": "grok-4.5",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
    ):
        from backend.ai.batch import xai_batch

        result = asyncio.run(xai_batch.collect("batch-xai-2"))

    assert isinstance(result, BatchPending)


def test_xai_batch_collect_extracts_tool_call_arguments_and_records_cost():
    """Collect-side extraction: the xAI results wrapper (`batch_result.response.
    chat_get_completion`) must unwrap to the same BatchResult.text contract as the
    other adapters — a JSON string, ready for the caller's json.loads."""
    fake_client = _FakeXaiClient(
        get_map={
            "/batches/batch-xai-3": _FakeXaiResponse({"state": {"num_pending": 0}}),
            "/batches/batch-xai-3/results": _FakeXaiResponse(
                {
                    "results": [
                        {
                            "custom_id": "intel-1",
                            "batch_result": {
                                "response": {
                                    "chat_get_completion": {
                                        "model": "grok-4.5",
                                        "choices": [
                                            {
                                                "message": {
                                                    "tool_calls": [
                                                        {
                                                            "function": {
                                                                "arguments": '{"stories": []}'
                                                            }
                                                        }
                                                    ]
                                                },
                                                "finish_reason": "tool_calls",
                                            }
                                        ],
                                        "usage": {
                                            "prompt_tokens": 12,
                                            "completion_tokens": 4,
                                        },
                                    }
                                }
                            },
                        }
                    ],
                    "pagination_token": None,
                }
            ),
        }
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
        "model": "grok-4.5",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
        mpatch("backend.ai.cost_tracker.record", MagicMock()) as fake_record,
    ):
        from backend.ai.batch import xai_batch

        results = asyncio.run(xai_batch.collect("batch-xai-3"))

    assert len(results) == 1
    assert results[0].custom_id == "intel-1"
    assert results[0].text == '{"stories": []}'
    assert results[0].stop_reason == "tool_calls"
    fake_record.assert_called_once_with(
        "batch", "grok-4.5", 12, 4, batch=True, provider="xAI (Grok)"
    )


def test_xai_batch_collect_surfaces_error_message():
    fake_client = _FakeXaiClient(
        get_map={
            "/batches/batch-xai-4": _FakeXaiResponse({"state": {"num_pending": 0}}),
            "/batches/batch-xai-4/results": _FakeXaiResponse(
                {
                    "results": [
                        {"custom_id": "intel-err", "error_message": "rate limited"},
                    ],
                    "pagination_token": None,
                }
            ),
        }
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
        "model": "grok-4.5",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
    ):
        from backend.ai.batch import xai_batch

        results = asyncio.run(xai_batch.collect("batch-xai-4"))

    assert len(results) == 1
    assert results[0].text is None
    assert results[0].error == "rate limited"


def test_xai_batch_collect_repeating_pagination_token_terminates_with_warning(caplog):
    """High-priority should-fix regression: a non-advancing (repeating verbatim)
    pagination_token must not hang the reap sweep forever — collect() must
    terminate early, log a WARNING, and return whatever it accumulated."""
    import logging as _logging

    call_count = {"n": 0}

    def _results_page(**kwargs):
        call_count["n"] += 1
        # Always echoes back the SAME token regardless of what was requested —
        # a buggy/hostile endpoint that never actually advances.
        return _FakeXaiResponse(
            {
                "results": [{"custom_id": f"intel-{call_count['n']}", "batch_result": {}}],
                "pagination_token": "stuck-token",
            }
        )

    fake_client = _FakeXaiClient(
        get_map={
            "/batches/batch-xai-loop": _FakeXaiResponse({"state": {"num_pending": 0}}),
            "/batches/batch-xai-loop/results": _results_page,
        }
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
        "model": "grok-4.5",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
        caplog.at_level(_logging.WARNING, logger="backend.ai.batch.xai_batch"),
    ):
        from backend.ai.batch import xai_batch

        results = asyncio.run(xai_batch.collect("batch-xai-loop"))

    # Page 1 has no prior token to compare against (accepted); page 2 echoes the
    # SAME token as page 1 — detected as non-advancing and stopped there. Two
    # calls total, much sooner than the page cap, and definitely does not hang.
    assert call_count["n"] == 2
    assert len(results) == 2
    assert any("did not advance" in r.message for r in caplog.records)


def test_xai_batch_collect_hits_page_cap_with_warning_when_token_keeps_changing():
    """A token that keeps changing (never repeats, never terminates) must still be
    bounded by _MAX_RESULT_PAGES — otherwise an adversarial/misbehaving endpoint that
    varies the token could evade the repeat-detection and still hang forever."""
    call_count = {"n": 0}

    def _results_page(**kwargs):
        call_count["n"] += 1
        return _FakeXaiResponse(
            {
                "results": [{"custom_id": f"intel-{call_count['n']}", "batch_result": {}}],
                "pagination_token": f"token-{call_count['n']}",  # always a NEW token
            }
        )

    fake_client = _FakeXaiClient(
        get_map={
            "/batches/batch-xai-forever": _FakeXaiResponse({"state": {"num_pending": 0}}),
            "/batches/batch-xai-forever/results": _results_page,
        }
    )
    fake_config = MagicMock()
    fake_config.resolved.return_value = {
        "provider": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key": "xai-key",
        "model": "grok-4.5",
    }

    with (
        mpatch("backend.db.model_config_store.ModelConfigStore", lambda: fake_config),
        mpatch("httpx.AsyncClient", lambda **kw: fake_client),
    ):
        from backend.ai.batch import xai_batch

        results = asyncio.run(xai_batch.collect("batch-xai-forever"))

    assert call_count["n"] == xai_batch._MAX_RESULT_PAGES
    assert len(results) == xai_batch._MAX_RESULT_PAGES


# ---------------------------------------------------------------------------
# intel_agent no longer leaks Anthropic wire format through extra_params
# ---------------------------------------------------------------------------


def test_intel_output_schema_is_neutral_not_anthropic_dict():
    from backend.monitoring.intel_agent import _OUTPUT_SCHEMA

    assert isinstance(_OUTPUT_SCHEMA, BatchOutputSchema)
    assert _OUTPUT_SCHEMA.name == "write_stories"


def test_intel_submit_and_poll_uses_output_schema_not_extra_params():
    """Regression: intel_agent's BatchRequest must carry the neutral output_schema and
    an EMPTY extra_params — the old Anthropic-shaped {"tools": [...], "tool_choice":
    {"type": "tool", ...}} dict must not appear in extra_params anymore."""
    captured_requests: list[BatchRequest] = []

    async def _fake_submit(task, requests):
        captured_requests.extend(requests)
        return "some-handle"

    async def _fake_collect(handle):
        return [BatchResult(custom_id=captured_requests[0].custom_id, text='{"stories": []}')]

    from backend.core import llm_seam as _seam

    orig_submit = _seam._submit_batch
    orig_collect = _seam._collect_batch
    try:
        _seam._submit_batch = _fake_submit
        _seam._collect_batch = _fake_collect

        from backend.monitoring.intel_agent import _submit_and_poll

        asyncio.run(
            _submit_and_poll(
                prompt="test",
                system="",
                model="",
                max_tokens=100,
                source="test",
                task="intel",
                url_to_date={},
            )
        )
    finally:
        _seam._submit_batch = orig_submit
        _seam._collect_batch = orig_collect

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.extra_params == {}
    assert req.output_schema is not None
    assert req.output_schema.name == "write_stories"
