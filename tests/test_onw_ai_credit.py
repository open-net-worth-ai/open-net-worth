"""Open Net Worth AI credit: what is left, and the one sentence when it runs out.

LiteLLM 1.110.2 refuses a key over budget with HTTP 422, type ``budget_exceeded`` and
"Budget has been exceeded! …" (older releases: 400 "ExceededBudget"; configurable to 429).
Synthetic keys and figures only.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.api.models as models_api
import backend.services.onw_ai as credit_service
from backend.ai import assistant
from backend.ai.automation_runner_adapter import HELD_SUMMARY, automation_runner
from backend.api.chat import _friendly_error
from backend.core.automation_runner import AutomationRunnerRequest
from backend.db.model_config_store import ModelConfigStore

BASE = "http://10.99.0.1:4000/v1"
CREDIT_URL = "http://10.99.0.1:8099/internal/agents/ai-credit"
PASTED_KEY = "sk-test-pasted-key"
_BUDGET = {
    "error": {
        "message": "Budget has been exceeded! Current cost: 10.01, Max budget: 10.0",
        "type": "budget_exceeded",
        "param": None,
        "code": "422",
    }
}


@pytest.fixture
def env(monkeypatch, db_isolated):
    for name in (
        "ONW_AI_BASE_URL",
        "ONW_AI_KEY",
        "ONW_AI_MODELS",
        "ONW_AI_REQUEST_EXTRAS",
        "ONW_AI_CREDIT_URL",
        "AUTH_PASSWORD",
        "AUTH_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credit_service, "_cached", None)
    return monkeypatch


def _manage(monkeypatch, credit_url: str = "") -> None:
    monkeypatch.setenv("ONW_AI_BASE_URL", BASE)
    monkeypatch.setenv("ONW_AI_KEY", "sk-test-managed-key")
    monkeypatch.setenv("ONW_AI_MODELS", json.dumps([{"id": "acme-flash", "label": "Acme"}]))
    if credit_url:
        monkeypatch.setenv("ONW_AI_CREDIT_URL", credit_url)


def _err(status: int, body: dict | str, url: str = BASE + "/chat/completions"):
    request = httpx.Request("POST", url)
    if isinstance(body, dict):
        response = httpx.Response(status, request=request, json=body)
    else:
        response = httpx.Response(status, request=request, text=body)
    cls = {
        400: openai.BadRequestError,
        422: openai.UnprocessableEntityError,
        429: openai.RateLimitError,
    }.get(status, openai.APIStatusError)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def _portal(monkeypatch, status: int = 200, body: dict | None = None) -> list[str]:
    seen: list[str] = []

    def fake_get(url, **_kwargs):
        seen.append(url)
        return httpx.Response(status, json=body or {}, request=httpx.Request("GET", url))

    monkeypatch.setattr(credit_service.httpx, "get", fake_get)
    return seen


_VARIANTS = [
    (422, _BUDGET),
    (400, {"error": {"message": "Key over budget.", "type": "budget_exceeded", "code": "400"}}),
    (429, {"error": {"message": "Budget has been exceeded!", "type": "budget_exceeded"}}),
    (429, {"error": {"message": "Budget has been exceeded! Current cost: 3"}}),
    (402, "Payment Required"),
]


@pytest.mark.parametrize(("status", "body"), _VARIANTS)
def test_every_budget_variant_maps_to_the_managed_message_once(env, status, body):
    _manage(env)
    message, setup_hint = _friendly_error(_err(status, body))
    assert message == (
        "This month's AI credit is used up. It resets next month, "
        "or add credit on cloud.opennetworth.ai."
    )
    assert message.count("credit is used up") == 1
    assert setup_hint is True


def test_the_reset_date_comes_from_the_cached_portal_answer(env):
    _manage(env, CREDIT_URL)
    _portal(
        env, body={"remaining_usd": 0, "limit_usd": 10, "resets_at": "2026-12-01T00:00:00+00:00"}
    )
    _client().get("/api/model-config/onw-ai")  # the Settings read fills the cache
    message, _ = _friendly_error(_err(422, _BUDGET))
    assert "It resets on December 1, or add credit" in message


def test_mapping_an_error_does_no_network_or_database_io(env, monkeypatch):
    """It runs on the event loop: a cold cache means "next month" and a refresh elsewhere."""
    _manage(env, CREDIT_URL)

    def no_network(*_a, **_k):
        raise AssertionError("network I/O while mapping an error")

    def no_database(*_a, **_k):
        raise AssertionError("database read while mapping an error")

    refreshes: list[str] = []
    monkeypatch.setattr(credit_service.httpx, "get", no_network)
    monkeypatch.setattr(credit_service, "ModelConfigStore", no_database)
    monkeypatch.setattr(credit_service, "_refresh_credit_soon", refreshes.append)
    message = credit_service.out_of_credit_message(_err(422, _BUDGET))
    assert message and "It resets next month" in message
    assert refreshes == [CREDIT_URL]
    for name in ("ONW_AI_BASE_URL", "ONW_AI_KEY", "ONW_AI_MODELS", "ONW_AI_CREDIT_URL"):
        monkeypatch.delenv(name)
    pasted = _err(422, _BUDGET, "https://ai.opennetworth.ai/v1/chat/completions")
    assert credit_service.out_of_credit_message(pasted) == credit_service.PASTED_KEY_OUT_OF_CREDIT


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"error": {"message": "Budget has been exceeded! (quoted by a user)"}}),
        (422, {"error": {"message": "ExceededBudget budget_exceeded", "type": "invalid_request"}}),
        (400, "Budget has been exceeded!"),
    ],
)
def test_free_text_alone_is_not_out_of_credit_on_400_or_422(env, status, body):
    _manage(env)
    assert credit_service.out_of_credit_message(_err(status, body)) is None


@pytest.mark.parametrize(("status", "body"), _VARIANTS)
def test_a_pasted_key_gets_its_own_message(env, status, body):
    ModelConfigStore().update_provider("onw_ai", {"api_key": PASTED_KEY})
    url = "https://ai.opennetworth.ai/v1/chat/completions"
    message, _ = _friendly_error(_err(status, body, url))
    assert message == credit_service.PASTED_KEY_OUT_OF_CREDIT
    assert PASTED_KEY not in message


def test_another_gateways_budget_error_is_not_ours(env):
    _manage(env)
    message, _ = _friendly_error(_err(422, _BUDGET, "http://localhost:4000/chat/completions"))
    assert "cloud.opennetworth.ai" not in message


def test_an_ordinary_bad_request_is_not_out_of_credit(env):
    _manage(env)
    message, _ = _friendly_error(_err(400, {"error": {"message": "context too long"}}))
    assert "credit" not in message


@pytest.mark.asyncio
async def test_an_automation_is_held_not_retried(env, monkeypatch):
    _manage(env)
    session_id = "automation-run-out-of-credit"
    assistant.SESSIONS[session_id] = [{"role": "user", "content": "Run it"}]
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda *args, **kwargs: None)
    monkeypatch.setattr(assistant, "save_session_durable", lambda *args, **kwargs: None)
    calls: list[int] = []

    async def refused_run(*args, **kwargs):
        calls.append(1)
        raise _err(422, _BUDGET)
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(assistant, "run", refused_run)
    result = await automation_runner(
        AutomationRunnerRequest(
            automation_id="automation_credit",
            run_id="run_credit",
            session_id=session_id,
            title="Run",
            instructions="Run it",
            model="onw_ai:acme-flash",
        )
    )
    assert calls == [1]
    assert result.status == "failed"
    assert result.summary == HELD_SUMMARY
    assert result.stats == {"held": "ai_credit"}
    assert result.error.startswith("This month's AI credit is used up.")


def _client() -> TestClient:
    from backend.api._deps import require_auth

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(models_api.router)
    return TestClient(app)


def test_the_credit_route_returns_the_portals_figures(env):
    _manage(env, CREDIT_URL)
    seen = _portal(
        env, body={"remaining_usd": 4.2, "limit_usd": 10.0, "resets_at": "2026-12-01T00:00:00Z"}
    )
    body = _client().get("/api/model-config/onw-ai").json()
    assert body == {
        "managed": True,
        "credit": {"remaining_usd": 4.2, "limit_usd": 10.0, "resets_at": "2026-12-01T00:00:00Z"},
    }
    assert seen == [CREDIT_URL]


@pytest.mark.parametrize("status", [404, 503])
def test_no_figure_when_the_portal_cannot_say(env, status):
    _manage(env, CREDIT_URL)
    _portal(env, status=status, body={"error": "unavailable"})
    assert _client().get("/api/model-config/onw-ai").json() == {"managed": True, "credit": None}


def test_no_figure_without_a_credit_url(env):
    _manage(env)
    seen = _portal(env)
    assert _client().get("/api/model-config/onw-ai").json()["credit"] is None
    assert seen == []


def test_a_pasted_key_shows_no_figure_yet(env):
    ModelConfigStore().update_provider("onw_ai", {"api_key": PASTED_KEY})
    seen = _portal(env)
    resp = _client().get("/api/model-config/onw-ai")
    assert resp.json() == {"managed": False, "credit": None}
    assert PASTED_KEY not in resp.text
    assert seen == []
