"""TradingAgents LLM bridge — aliases, auth, caps, cost and logging. Fake provider, synthetic data
only (ticker ACME); nothing here touches a network beyond one loopback socket."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from backend.ai.trading_agents.llm_bridge import (
    ALIAS_DEEP,
    ALIAS_QUICK,
    BridgeModel,
    CallRecord,
    LlmBridge,
    RunLimits,
)
from backend.core.errors import ExternalServiceError
from backend.core.one_shot_turn import one_shot_active
from backend.db.cost_log_store import CostLogStore

RUN_ID = "tar_0123456789ab"
SECRET_PROMPT = "ACME position 123456.78 USD marker-4f9c"


class FakeProvider:
    """Yields scripted events and records what it was called with."""

    def __init__(self, events=None, error=None, delay=0.0):
        self.events = events if events is not None else _answer("Hold ACME.")
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []
        self.one_shot_seen: list[bool] = []
        self.active = 0
        self.peak = 0

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.calls.append({"messages": messages, "tools": tools, "system": system, "model": model})
        self.one_shot_seen.append(one_shot_active())
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            for event in self.events:
                yield event
        finally:
            self.active -= 1


def _usage(basis=None, cost=0.02):
    event = {
        "type": "usage",
        "input_tokens": 100,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 40,
        "output_tokens": 25,
        "cost_usd": cost,
    }
    if basis:
        event["cost_basis"] = basis
    return event


def _answer(text, **usage):
    return [{"type": "text_delta", "text": text}, _usage(**usage), {"type": "done"}]


def _models(deep: FakeProvider, quick: FakeProvider | None = None, transport="anthropic"):
    quick = quick or deep
    return {
        ALIAS_DEEP: BridgeModel(
            ALIAS_DEEP, "pk_a:deep-model", "deep-model", "Provider A", transport, deep
        ),
        ALIAS_QUICK: BridgeModel(
            ALIAS_QUICK, "pk_a:quick-model", "quick-model", "Provider A", transport, quick
        ),
    }


def _body(model=ALIAS_QUICK, content=SECRET_PROMPT, **extra):
    return {"model": model, "messages": [{"role": "user", "content": content}], **extra}


def _client(bridge: LlmBridge, token=None, peer="127.0.0.1"):
    transport = httpx.ASGITransport(app=bridge.app, client=(peer, 40000))
    headers = {"Authorization": f"Bearer {token or bridge.token}"}
    return httpx.AsyncClient(transport=transport, base_url="http://bridge", headers=headers)


def _bridge(provider=None, **kwargs):
    provider = provider or FakeProvider()
    kwargs.setdefault("concurrency", 2)
    return LlmBridge(run_id=RUN_ID, models=_models(provider), **kwargs), provider


async def _post(bridge, body, **client_kwargs):
    async with _client(bridge, **client_kwargs) as client:
        return await client.post("/v1/chat/completions", json=body)


# ── aliases ──────────────────────────────────────────────────────────────────────────────


async def test_each_alias_maps_to_its_own_resolved_model(db_isolated):
    deep, quick = FakeProvider(), FakeProvider()
    bridge = LlmBridge(run_id=RUN_ID, models=_models(deep, quick), concurrency=2)
    assert (await _post(bridge, _body(ALIAS_DEEP))).status_code == 200
    assert (await _post(bridge, _body(ALIAS_QUICK))).status_code == 200
    assert [c["model"] for c in deep.calls] == ["deep-model"]
    assert [c["model"] for c in quick.calls] == ["quick-model"]


@pytest.mark.parametrize("name", ["gpt-4o", "pk_a:deep-model", "deep-model", "", None])
async def test_an_unknown_model_is_a_400_and_never_reaches_a_provider(db_isolated, name):
    bridge, provider = _bridge()
    response = await _post(bridge, _body(name))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "model_not_found"
    assert provider.calls == []


async def test_models_endpoint_lists_only_the_two_aliases(db_isolated):
    bridge, _ = _bridge()
    async with _client(bridge) as client:
        response = await client.get("/v1/models")
    assert [m["id"] for m in response.json()["data"]] == [ALIAS_DEEP, ALIAS_QUICK]


async def test_the_completion_is_openai_shaped(db_isolated):
    bridge, _ = _bridge()
    body = (await _post(bridge, _body())).json()
    assert body["id"].startswith("onwcall_") and body["model"] == ALIAS_QUICK
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hold ACME."}
    assert body["usage"] == {"prompt_tokens": 150, "completion_tokens": 25, "total_tokens": 175}


async def test_tool_calls_come_back_and_no_tool_is_run(db_isolated):
    provider = FakeProvider(
        [
            {"type": "tool_use", "id": "t1", "name": "get_quote", "input": {"ticker": "ACME"}},
            _usage(),
            {"type": "done", "stop_reason": "tool_use"},
        ]
    )
    bridge, _ = _bridge(provider)
    tool = {"type": "function", "function": {"name": "get_quote", "parameters": {}}}
    body = (await _post(bridge, _body(tools=[tool]))).json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_quote"
    assert provider.calls[0]["tools"][0]["name"] == "get_quote"
    assert len(provider.calls) == 1  # one request is one provider call, never a loop


async def test_every_call_is_a_one_shot_turn(db_isolated):
    bridge, provider = _bridge()
    await _post(bridge, _body())
    assert provider.one_shot_seen == [True]
    assert one_shot_active() is False


# ── auth ─────────────────────────────────────────────────────────────────────────────────


async def test_a_wrong_token_is_401(db_isolated):
    bridge, provider = _bridge()
    response = await _post(bridge, _body(), token="wrong-token")
    assert response.status_code == 401
    assert provider.calls == []


async def test_no_authorization_header_at_all_is_401(db_isolated):
    bridge, _ = _bridge()
    transport = httpx.ASGITransport(app=bridge.app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge") as client:
        assert (await client.get("/v1/models")).status_code == 401


async def test_a_non_loopback_peer_is_403_even_with_the_right_token(db_isolated):
    bridge, provider = _bridge()
    for peer in ("203.0.113.9", "10.0.0.4", "2001:db8::1"):
        response = await _post(bridge, _body(), peer=peer)
        assert response.status_code == 403
    assert provider.calls == []


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
async def test_loopback_peers_are_served(db_isolated, peer):
    bridge, _ = _bridge()
    assert (await _post(bridge, _body(), peer=peer)).status_code == 200


# ── cost ─────────────────────────────────────────────────────────────────────────────────


async def test_exactly_one_cost_record_per_call_labelled_for_the_run(db_isolated):
    bridge, _ = _bridge()
    await _post(bridge, _body())
    await _post(bridge, _body(ALIAS_DEEP))
    rows = CostLogStore().all()
    assert len(rows) == 2
    row = rows[0]
    assert (row["source"], row["surface"], row["session_id"]) == (
        "trading_agents",
        "trading_agents",
        RUN_ID,
    )
    assert row["model"] == "quick-model" and rows[1]["model"] == "deep-model"
    assert row["provider"] == "Provider A"
    assert (row["input_tokens"], row["output_tokens"]) == (100, 25)
    assert (row["cache_creation_tokens"], row["cache_read_tokens"]) == (10, 40)
    assert row["cost_usd"] == 0.02 and "cost_basis" not in row


async def test_a_cli_subscription_call_is_stamped_plan_included(db_isolated):
    bridge, _ = _bridge(FakeProvider(_answer("ok", basis="plan_included")))
    await _post(bridge, _body())
    (row,) = CostLogStore().all()
    assert row["cost_basis"] == "plan_included"


async def test_a_failed_call_records_no_cost(db_isolated):
    bridge, _ = _bridge(FakeProvider(error=ExternalServiceError("Codex crashed.")))
    response = await _post(bridge, _body())
    assert response.status_code == 502
    assert CostLogStore().all() == []


async def test_a_provider_that_records_its_own_rows_is_not_recorded_twice(db_isolated):
    provider = FakeProvider()
    provider.RECORDS_COST_IN_STREAM_CHAT = True
    bridge, _ = _bridge(provider)
    await _post(bridge, _body())
    assert CostLogStore().all() == []


async def test_the_runner_hears_about_each_call(db_isolated):
    seen: list[CallRecord] = []
    bridge, _ = _bridge(on_call=seen.append)
    await _post(bridge, _body(ALIAS_DEEP))
    (record,) = seen
    assert record.alias == ALIAS_DEEP and record.model_ref == "pk_a:deep-model"
    assert (record.input_tokens, record.output_tokens, record.cost_usd) == (100, 25, 0.02)
    assert record.call_id.startswith("onwcall_") and record.latency_ms >= 0


async def test_a_failing_runner_callback_never_fails_the_call(db_isolated):
    def boom(_record):
        raise RuntimeError("bookkeeping bug")

    bridge, _ = _bridge(on_call=boom)
    assert (await _post(bridge, _body())).status_code == 200


# ── caps ─────────────────────────────────────────────────────────────────────────────────


async def test_calls_are_refused_after_the_call_cap(db_isolated):
    bridge, provider = _bridge(limits=RunLimits(max_calls=2))
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 400]
    refused = await _post(bridge, _body())
    assert refused.json()["error"]["code"] == "run_cap_reached"
    assert len(provider.calls) == 2


async def test_calls_are_refused_after_the_metered_spend_cap(db_isolated):
    bridge, provider = _bridge(limits=RunLimits(max_metered_usd=0.03))
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 400]  # 0.02 < 0.03, then 0.04 >= 0.03
    assert bridge.metered_usd == pytest.approx(0.04)
    assert len(provider.calls) == 2


async def test_plan_included_calls_never_count_toward_the_spend_cap(db_isolated):
    bridge, provider = _bridge(
        FakeProvider(_answer("ok", basis="plan_included", cost=5.0)),
        limits=RunLimits(max_metered_usd=0.01),
    )
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 200] and bridge.metered_usd == 0.0
    assert len(provider.calls) == 3


async def test_the_runner_can_refuse_every_later_call(db_isolated):
    bridge, provider = _bridge()
    assert (await _post(bridge, _body())).status_code == 200
    bridge.refuse("The monthly cap was reached.")
    response = await _post(bridge, _body())
    assert response.status_code == 400
    assert "monthly cap" in response.json()["error"]["message"]
    assert len(provider.calls) == 1


async def test_a_malformed_request_does_not_use_up_a_call(db_isolated):
    bridge, _ = _bridge(limits=RunLimits(max_calls=1))
    assert (await _post(bridge, {"model": ALIAS_QUICK, "messages": []})).status_code == 400
    assert bridge.calls_admitted == 0
    assert (await _post(bridge, _body())).status_code == 200


# ── concurrency and errors ───────────────────────────────────────────────────────────────


async def test_concurrency_is_bounded_per_run(db_isolated):
    provider = FakeProvider(delay=0.05)
    bridge = LlmBridge(run_id=RUN_ID, models=_models(provider), concurrency=2)
    async with _client(bridge) as client:
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=_body()) for _ in range(6)]
        )
    assert all(r.status_code == 200 for r in responses)
    assert provider.peak == 2


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ExternalServiceError("Provider says: rate limit exceeded, retry later."), 429),
        (ExternalServiceError("Codex crashed."), 502),
        (RuntimeError("raw internal text with secret-xyz"), 502),
    ],
)
async def test_provider_errors_map_to_openai_errors(db_isolated, error, status):
    bridge, _ = _bridge(FakeProvider(error=error))
    response = await _post(bridge, _body())
    assert response.status_code == status
    assert "secret-xyz" not in response.text


async def test_an_empty_reply_is_a_502_so_the_worker_retries(db_isolated):
    bridge, _ = _bridge(FakeProvider([{"type": "done"}]))
    assert (await _post(bridge, _body())).status_code == 502


async def test_a_streaming_request_is_a_400(db_isolated):
    bridge, provider = _bridge()
    assert (await _post(bridge, _body(stream=True))).status_code == 400
    assert provider.calls == []


# ── logging ──────────────────────────────────────────────────────────────────────────────


async def test_no_message_body_or_reply_is_ever_logged(db_isolated, caplog):
    caplog.set_level(logging.DEBUG)
    provider = FakeProvider(_answer("reply-marker-77ab"))
    bridge, _ = _bridge(provider)
    await _post(bridge, _body())
    await _post(bridge, _body(stream=True))
    failing, _ = _bridge(FakeProvider(error=ExternalServiceError("boom")))
    await _post(failing, _body())
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "marker-4f9c" not in logged and "123456.78" not in logged
    assert "reply-marker-77ab" not in logged
    assert "bridge_call " in logged and "bridge_call_failed" in logged
    assert bridge.token not in logged


# ── real loopback server ─────────────────────────────────────────────────────────────────


async def test_the_server_binds_loopback_on_a_random_port_and_stops(db_isolated):
    bridge, _ = _bridge()
    async with bridge:
        assert bridge.base_url.startswith("http://127.0.0.1:") and not bridge.base_url.endswith(
            ":0/v1"
        )
        async with httpx.AsyncClient() as client:
            ok = await client.post(
                f"{bridge.base_url}/chat/completions",
                json=_body(),
                headers={"Authorization": f"Bearer {bridge.token}"},
            )
            denied = await client.get(f"{bridge.base_url}/models")
        assert ok.status_code == 200 and denied.status_code == 401
        port = bridge.base_url.split(":")[2].split("/")[0]
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{port}/v1/models")


async def test_two_runs_get_different_ports_and_tokens(db_isolated):
    first, _ = _bridge()
    second, _ = _bridge()
    async with first, second:
        assert first.base_url != second.base_url and first.token != second.token


async def test_starting_a_bridge_does_not_take_the_process_signal_handlers(db_isolated):
    import signal

    before = signal.getsignal(signal.SIGINT)
    bridge, _ = _bridge()
    async with bridge:
        assert signal.getsignal(signal.SIGINT) is before
