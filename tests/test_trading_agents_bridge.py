"""TradingAgents LLM bridge — aliases, auth, caps, cost and logging. Fake provider, synthetic data
only (ticker ACME); nothing here touches a network beyond one loopback socket."""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import replace

import httpx
import pytest

from backend.ai.trading_agents import bridge_http, bridge_models, llm_bridge
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


@pytest.fixture(autouse=True)
def cheap_models(monkeypatch):
    """A price so low that a call's worst case never decides a test that is about something else."""
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (0.001, 0.001))


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


async def test_concurrent_calls_cannot_overshoot_the_cap(db_isolated, monkeypatch):
    """Each call reserves its worst case first: at 200 USD per million output tokens one call is
    reserved at 6.4 against a limit of 10, so three at once are not three calls in flight. The
    first runs and costs 6.0; what is left (4.0) is less than the next call's worst case, so the
    other two are refused rather than let through to overshoot."""
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (200.0, 200.0))
    provider = FakeProvider(_answer("ok", cost=6.0), delay=0.05)
    bridge = LlmBridge(
        run_id=RUN_ID,
        models=_models(provider),
        concurrency=4,
        limits=RunLimits(max_metered_usd=10.0),
    )
    async with _client(bridge) as client:
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=_body()) for _ in range(3)]
        )
    assert sorted(r.status_code for r in responses) == [200, 400, 400]
    assert len(provider.calls) == 1 and provider.peak == 1
    assert bridge.metered_usd == pytest.approx(6.0)
    assert bridge.refusal_kind == "budget"


async def test_a_call_that_fits_beside_the_one_in_flight_runs_alongside_it(
    db_isolated, monkeypatch
):
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (1.0, 1.0))
    provider = FakeProvider(delay=0.05)
    bridge = LlmBridge(
        run_id=RUN_ID,
        models=_models(provider),
        concurrency=4,
        limits=RunLimits(max_metered_usd=10.0),
    )
    async with _client(bridge) as client:
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=_body()) for _ in range(3)]
        )
    assert [r.status_code for r in responses] == [200, 200, 200] and provider.peak == 3


async def test_what_is_left_of_the_month_limits_the_run_with_its_own_message(db_isolated):
    bridge, provider = _bridge(limits=RunLimits(max_metered_usd=5.0, month_remaining_usd=0.03))
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 400] and len(provider.calls) == 2
    assert "monthly cap" in bridge.refusal_text and bridge.refusal_kind == "budget"


async def test_a_plan_or_local_model_is_not_held_to_a_dollar_limit(db_isolated):
    provider = FakeProvider()
    models = {
        alias: replace(model, plan_included=True) for alias, model in _models(provider).items()
    }
    bridge = LlmBridge(
        run_id=RUN_ID, models=models, concurrency=2, limits=RunLimits(max_metered_usd=0.0)
    )
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 200]


async def test_a_body_over_the_size_limit_is_refused_before_a_provider_is_called(db_isolated):
    bridge, provider = _bridge()
    big = _body(content="x" * (bridge_http.MAX_BODY_BYTES + 1))
    response = await _post(bridge, big)
    assert response.status_code == 400 and "too large" in response.json()["error"]["message"]
    assert provider.calls == [] and bridge.calls_admitted == 0


# ── every call is recorded, once ─────────────────────────────────────────────────────────


async def test_a_call_cut_off_under_the_bridge_is_still_finished_and_recorded_once(db_isolated):
    seen: list[CallRecord] = []
    provider = FakeProvider(delay=0.2)
    bridge, _ = _bridge(provider, on_call=seen.append)
    async with _client(bridge) as client:
        pending = asyncio.create_task(client.post("/v1/chat/completions", json=_body()))
        while not provider.calls:
            await asyncio.sleep(0.01)
        pending.cancel()  # the worker was killed under its own request
        await asyncio.gather(pending, return_exceptions=True)
        await bridge.stop()  # waits for the call going
    assert len(CostLogStore().all()) == 1
    assert [r.failed for r in seen] == [False] and seen[0].cost_usd == 0.02


async def test_stop_waits_for_a_call_going_but_not_forever(db_isolated, monkeypatch):
    monkeypatch.setattr(llm_bridge, "_INFLIGHT_WAIT_SECONDS", 0.1)
    seen: list[CallRecord] = []
    bridge, provider = _bridge(FakeProvider(delay=30), on_call=seen.append)
    async with _client(bridge) as client:
        pending = asyncio.create_task(client.post("/v1/chat/completions", json=_body()))
        while not provider.calls:
            await asyncio.sleep(0.01)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await bridge.stop()
    assert [r.failed for r in seen] == [True] and CostLogStore().all() == []


async def test_a_failed_call_counts_as_a_call_for_the_cap_and_for_the_record(db_isolated):
    seen: list[CallRecord] = []
    bridge, provider = _bridge(
        FakeProvider(error=ExternalServiceError("Codex crashed.")),
        limits=RunLimits(max_calls=2),
        on_call=seen.append,
    )
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [502, 502, 400]
    assert bridge.calls_admitted == len(seen) == 2
    assert all(r.failed and r.input_tokens == 0 for r in seen)
    assert CostLogStore().all() == []


async def test_a_call_with_no_price_and_no_reported_cost_is_unpriced(db_isolated, monkeypatch):
    seen: list[CallRecord] = []
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: None)
    bridge, _ = _bridge(FakeProvider(_answer("ok", cost=0.0)), on_call=seen.append)
    await _post(bridge, _body())
    reported = [{"type": "text_delta", "text": "ok"}, {**_usage(), "cost_reported": True}]
    other, _ = _bridge(FakeProvider([*reported, {"type": "done"}]), on_call=seen.append)
    await _post(other, _body())
    plan, _ = _bridge(FakeProvider(_answer("ok", basis="plan_included")), on_call=seen.append)
    await _post(plan, _body())
    assert [r.unpriced for r in seen] == [True, False, False]


async def test_a_priced_call_is_not_unpriced(db_isolated, monkeypatch):
    seen: list[CallRecord] = []
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (1.0, 2.0))
    bridge, _ = _bridge(on_call=seen.append)
    await _post(bridge, _body())
    assert [r.unpriced for r in seen] == [False]


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


async def test_a_bridge_that_never_starts_gives_its_socket_back(db_isolated, monkeypatch):
    made: list[socket.socket] = []
    real = socket.socket

    class Recording(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    async def never(self, sockets=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(llm_bridge.socket, "socket", Recording)
    monkeypatch.setattr(llm_bridge.QuietServer, "serve", never)
    monkeypatch.setattr(llm_bridge, "_START_TIMEOUT_SECONDS", 0.05)
    bridge, _ = _bridge()
    with pytest.raises(ExternalServiceError):
        await bridge.start()
    assert made and all(sock.fileno() == -1 for sock in made)


async def test_the_server_does_not_trust_forwarding_headers(db_isolated):
    bridge, _ = _bridge()
    async with bridge:
        assert bridge._server.config.proxy_headers is False


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


# ── the reservation is the provider's own ceiling, never the worker's ─────────────────────


async def test_a_workers_own_max_tokens_never_lowers_the_reservation(db_isolated, monkeypatch):
    """At 100 USD per million output tokens the Anthropic ceiling (32,000) is 3.2 USD, over a limit
    of 1.0 — so the call is refused even though the worker claimed a reply of one token."""
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (0.0, 100.0))
    bridge, provider = _bridge(limits=RunLimits(max_metered_usd=1.0))
    for key in ("max_tokens", "max_completion_tokens"):
        response = await _post(bridge, _body(**{key: 1}))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "run_cap_reached"
    assert provider.calls == [] and bridge.calls_admitted == 0


def test_the_worst_case_ignores_the_request_and_follows_the_providers_ceiling(monkeypatch):
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (0.0, 100.0))
    anthropic = _models(FakeProvider())[ALIAS_DEEP]
    other = replace(anthropic, transport="openai_compatible")
    assert anthropic.worst_case_usd(0) == pytest.approx(3.2)
    assert other.worst_case_usd(0) == pytest.approx(6.4)
    assert anthropic.worst_case_usd(0) == anthropic.worst_case_usd(0)


async def test_a_single_call_that_cannot_be_afforded_is_refused_even_with_nothing_outstanding(
    db_isolated, monkeypatch
):
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: (0.0, 100.0))
    bridge, provider = _bridge(limits=RunLimits(max_metered_usd=5.0, month_remaining_usd=3.0))
    response = await _post(bridge, _body())
    assert response.status_code == 400 and "monthly cap" in response.json()["error"]["message"]
    assert provider.calls == []
    roomy, provider = _bridge(limits=RunLimits(max_metered_usd=5.0))
    assert (await _post(roomy, _body())).status_code == 200


# ── a local endpoint is still held to the call cap and to what a proxy reports ────────────


def _local_models(provider, url="http://127.0.0.1:11434/v1", transport="openai_compatible"):
    return {
        alias: replace(model, base_url=url, transport=transport)
        for alias, model in _models(provider).items()
    }


async def test_a_local_model_is_still_held_to_the_call_cap(db_isolated):
    provider = FakeProvider()
    bridge = LlmBridge(
        run_id=RUN_ID, models=_local_models(provider), concurrency=2, limits=RunLimits(max_calls=2)
    )
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 400] and len(provider.calls) == 2


async def test_a_loopback_proxy_that_reports_a_cost_is_held_to_the_dollar_limit(
    db_isolated, monkeypatch
):
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: None)
    provider = FakeProvider(_answer("ok", cost=0.02))
    bridge = LlmBridge(
        run_id=RUN_ID,
        models=_local_models(provider),
        concurrency=2,
        limits=RunLimits(max_metered_usd=0.03),
    )
    codes = [(await _post(bridge, _body())).status_code for _ in range(3)]
    assert codes == [200, 200, 400] and bridge.metered_usd == pytest.approx(0.04)


async def test_an_unpriced_model_on_loopback_is_allowed_but_on_a_lookalike_host_it_is_not(
    db_isolated, monkeypatch
):
    monkeypatch.setattr(bridge_models, "list_price_rates", lambda *a, **k: None)
    limits = RunLimits(max_metered_usd=1.0)
    loopback = LlmBridge(
        run_id=RUN_ID, models=_local_models(FakeProvider()), concurrency=2, limits=limits
    )
    assert (await _post(loopback, _body())).status_code == 200
    provider = FakeProvider()
    lookalike = LlmBridge(
        run_id=RUN_ID,
        models=_local_models(provider, "http://localhost.evil.example:11434/v1"),
        concurrency=2,
        limits=limits,
    )
    assert (await _post(lookalike, _body())).status_code == 400
    assert provider.calls == []


# ── a hung provider never holds the run open ──────────────────────────────────────────────


class DeafProvider:
    """A provider (or CLI child) that swallows every cancel and never answers."""

    def __init__(self):
        self.started = asyncio.Event()
        self.released = False  # the test lets go at its end, so no task outlives it

    async def stream_chat(self, messages, tools, system, model, effort="medium"):
        self.started.set()
        while not self.released:
            try:
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                continue
        yield {"type": "done"}


async def test_stop_gives_up_on_a_provider_that_ignores_cancel(db_isolated, monkeypatch):
    monkeypatch.setattr(llm_bridge, "_INFLIGHT_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(llm_bridge, "_CANCEL_WAIT_SECONDS", 0.1)
    seen: list[CallRecord] = []
    provider = DeafProvider()
    bridge = LlmBridge(run_id=RUN_ID, models=_models(provider), concurrency=2, on_call=seen.append)
    try:
        async with _client(bridge) as client:
            pending = asyncio.create_task(client.post("/v1/chat/completions", json=_body()))
            await asyncio.wait_for(provider.started.wait(), 5)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(bridge.stop(), 5)
        assert bridge._inflight == set()
        assert [r.failed for r in seen] == [True]
    finally:
        provider.released = True


async def test_a_call_past_its_overall_timeout_is_a_failed_call_not_a_hang(
    db_isolated, monkeypatch
):
    monkeypatch.setattr(llm_bridge, "_CALL_TIMEOUT_SECONDS", 0.1)
    seen: list[CallRecord] = []
    provider = DeafProvider()
    bridge = LlmBridge(run_id=RUN_ID, models=_models(provider), concurrency=2, on_call=seen.append)
    try:
        response = await asyncio.wait_for(_post(bridge, _body()), 5)
        assert response.status_code == 502
        assert [r.failed for r in seen] == [True] and bridge._reserved_usd == 0
    finally:
        provider.released = True
