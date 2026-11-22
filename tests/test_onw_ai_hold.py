"""Out of Open Net Worth AI credit: Automations on it are held, not run into the same refusal.

The hold lasts until the credit resets (Private Cloud, from the cached portal answer) or an hour
(a pasted key, or no reset date known). A successful call or the portal showing credit again
lifts it. While it holds, the scheduler skips those Automations' slots without calling a model.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import openai
import pytest

import backend.services.automations.runs as runs_mod
import backend.services.onw_ai as credit_service
from backend.services.automations.runs import run_due_automations
from backend.services.automations.service import apply_automation_change

BASE = "http://10.99.0.1:4000/v1"
CREDIT_URL = "http://10.99.0.1:8099/internal/agents/ai-credit"
_BUDGET = {"error": {"message": "Budget has been exceeded!", "type": "budget_exceeded"}}
NOW = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, db_isolated):
    monkeypatch.delenv("ONW_TEST_DEFAULT_MODEL", raising=False)
    for name in ("ONW_AI_BASE_URL", "ONW_AI_KEY", "ONW_AI_MODELS", "ONW_AI_CREDIT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credit_service, "_cached", None)
    monkeypatch.setattr(credit_service, "_held_until", None)
    monkeypatch.setattr(credit_service, "_refresh_credit_soon", lambda _url: None)
    return monkeypatch


def _manage(monkeypatch) -> None:
    monkeypatch.setenv("ONW_AI_BASE_URL", BASE)
    monkeypatch.setenv("ONW_AI_KEY", "sk-test-managed-key")
    monkeypatch.setenv("ONW_AI_MODELS", json.dumps([{"id": "acme-flash", "label": "Acme"}]))
    monkeypatch.setenv("ONW_AI_CREDIT_URL", CREDIT_URL)


def _err(url: str = BASE):
    request = httpx.Request("POST", url + "/chat/completions")
    response = httpx.Response(422, request=request, json=_BUDGET)
    return openai.UnprocessableEntityError("Error code: 422", response=response, body=_BUDGET)


def test_a_managed_hold_lasts_until_the_cached_reset(env):
    _manage(env)
    reset = "2026-12-01T00:00:00+00:00"
    env.setattr(
        credit_service, "_cached", (0.0, CREDIT_URL, {"remaining_usd": 0, "resets_at": reset})
    )
    assert credit_service.record_out_of_credit(_err(), now=NOW)
    assert credit_service.held_until() == datetime.fromisoformat(reset)


def test_a_pasted_key_hold_is_an_hour(env):
    assert credit_service.record_out_of_credit(_err("https://ai.opennetworth.ai/v1"), now=NOW)
    assert credit_service.held_until() == NOW + timedelta(hours=1)
    assert not credit_service.hold_active(NOW + timedelta(hours=1, seconds=1))


def test_another_error_holds_nothing(env):
    assert credit_service.record_out_of_credit(RuntimeError("boom"), now=NOW) is None
    assert credit_service.held_until() is None


def test_credit_seen_again_lifts_the_hold(env):
    _manage(env)
    credit_service.record_out_of_credit(_err(), now=NOW)

    def fake_get(url, **_kwargs):
        body = {"remaining_usd": 5, "limit_usd": 10, "resets_at": None}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    env.setattr(credit_service.httpx, "get", fake_get)
    credit_service.status()
    assert credit_service.held_until() is None


def test_a_successful_call_lifts_the_hold(env):
    credit_service.record_out_of_credit(_err("https://ai.opennetworth.ai/v1"), now=NOW)
    credit_service.call_succeeded("https://ai.opennetworth.ai/v1")
    assert credit_service.held_until() is None


def test_only_automations_on_onw_ai_are_held(env):
    _manage(env)
    credit_service.record_out_of_credit(_err(), now=NOW)
    assert credit_service.automation_held({"model": "onw_ai:acme-flash"}, NOW)
    assert not credit_service.automation_held({"model": "anthropic:claude-haiku-5-5"}, NOW)
    # No model of its own: it runs on the instance default, here Open Net Worth AI.
    assert credit_service.automation_held({"model": ""}, NOW)


def _create(name: str, model: str) -> dict:
    return apply_automation_change(
        {
            "op": "create",
            "automation": {
                "name": name,
                "instructions": "Summarize.",
                "model": model,
                "trigger": {"kind": "schedule", "type": "daily", "time": "08:00", "tzid": "UTC"},
                "execution": {"kind": "lucius"},
            },
        }
    )


@pytest.mark.asyncio
async def test_the_scheduler_skips_held_automations_without_calling_a_model(env):
    _manage(env)
    env.setattr(
        "backend.services.automations.service.now_utc",
        lambda: NOW - timedelta(hours=1),
    )
    held = _create("On our AI", "onw_ai:acme-flash")
    other = _create("Elsewhere", "anthropic:claude-haiku-5-5")
    credit_service.record_out_of_credit(_err(), now=NOW - timedelta(minutes=5))
    started: list[str] = []

    async def fake_run(automation_id, **_kwargs):
        started.append(automation_id)
        return {"id": f"run_{automation_id}"}

    env.setattr(runs_mod, "run_automation", fake_run)
    result = await run_due_automations(NOW)
    assert started == [other["id"]]
    assert result["held"] == 1
    assert {"id": held["id"], "held": True} in result["results"]

    # Once the hold is over the slot runs; it was not skipped for good.
    credit_service.call_succeeded(BASE)
    await run_due_automations(NOW + timedelta(minutes=1))
    assert held["id"] in started


class _Stream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_a_call_the_relay_accepts_lifts_the_hold(env):
    from types import SimpleNamespace as NS

    from backend.ai.models.openai_provider import OpenAIProvider

    _manage(env)
    credit_service.record_out_of_credit(_err(), now=NOW)

    async def create(**_kwargs):
        return _Stream()

    provider = OpenAIProvider(base_url=BASE, api_key="sk-test-managed-key")
    provider._client = NS(chat=NS(completions=NS(create=create)))
    async for _ in provider.stream_chat(
        [{"role": "user", "content": "hi"}], [], system="", model="acme-flash"
    ):
        pass
    assert credit_service.held_until() is None
