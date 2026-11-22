"""Open Net Worth AI on a Private Cloud agent: the environment IS the configuration.

Endpoint, key and models come from ONW_AI_*; they win over anything stored, are not editable,
and the key never reaches a response. Synthetic values only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.api.models as models_api
import backend.core.config as config
from backend.core import onw_ai
from backend.db.model_config_store import ModelConfigStore

BASE = "http://10.99.0.1:4000/v1"
KEY = "sk-test-managed-key"
MODELS = [
    {"id": "claude-haiku-5.5", "label": "Claude Haiku 5.5"},
    {"id": "acme-flash", "label": "Acme Flash"},
]
_ENV = (
    "ONW_AI_BASE_URL",
    "ONW_AI_KEY",
    "ONW_AI_MODELS",
    "ONW_AI_REQUEST_EXTRAS",
    "ONW_AI_CREDIT_URL",
)


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(models_api, "MODELS_CACHE_JSON", tmp_path / "models_cache.json")
    # The unconfigured case: no conftest-supplied default model.
    monkeypatch.delenv("ONW_TEST_DEFAULT_MODEL", raising=False)
    for name in (*_ENV, "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTH_PASSWORD", "AUTH_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    config._settings = None
    yield monkeypatch
    config._settings = None


def _manage(monkeypatch, models=MODELS, extras=None) -> None:
    monkeypatch.setenv("ONW_AI_BASE_URL", BASE)
    monkeypatch.setenv("ONW_AI_KEY", KEY)
    monkeypatch.setenv("ONW_AI_MODELS", json.dumps(models))
    if extras is not None:
        monkeypatch.setenv("ONW_AI_REQUEST_EXTRAS", json.dumps(extras))


def _client() -> TestClient:
    from backend.api._deps import require_auth

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(models_api.router)
    return TestClient(app)


def _no_network(monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise AssertionError("a managed provider's models must not be fetched")

    monkeypatch.setattr(httpx, "get", refuse)


def test_not_managed_without_the_environment(env):
    body = _client().get("/api/model-config/providers").json()
    assert "onw_ai" not in body["configured_providers"]
    assert "onw_ai" not in ModelConfigStore().configured_provider_keys()
    # Still offered for a pasted key, with the public endpoint as its default.
    entry = next(e for e in body["catalog"] if e["key"] == "onw_ai")
    assert entry["label"] == "Open Net Worth AI"
    assert entry["base_url"] == "https://ai.opennetworth.ai/v1"


@pytest.mark.parametrize("missing", ["ONW_AI_BASE_URL", "ONW_AI_KEY", "ONW_AI_MODELS"])
def test_a_partial_environment_is_not_managed(env, missing):
    _manage(env)
    env.delenv(missing)
    assert onw_ai.managed() is None


def test_listed_and_connected_with_the_environment(env):
    _manage(env)
    resp = _client().get("/api/model-config/providers")
    row = resp.json()["configured_providers"]["onw_ai"]
    assert row["managed"] is True
    assert row["has_api_key"] is True
    assert row["key_status"] == "valid"
    assert "onw_ai" in ModelConfigStore().configured_provider_keys()
    assert KEY not in resp.text


def test_first_run_preselects_haiku_without_writing_anything(env):
    _manage(env)
    body = _client().get("/api/model-config").json()
    assert body["default_model"] == "onw_ai:claude-haiku-5.5"
    assert body["has_api_key"] is True
    resolved = ModelConfigStore().resolved()
    assert (resolved["base_url"], resolved["api_key"]) == (BASE, KEY)
    assert ModelConfigStore().get().get("default_model") in (None, "")


def test_preselects_the_first_model_when_haiku_is_not_offered(env):
    _manage(env, models=[{"id": "acme-flash", "label": "Acme Flash"}])
    assert ModelConfigStore().public()["default_model"] == "onw_ai:acme-flash"


def test_a_stored_default_is_never_overridden(env):
    ModelConfigStore().update({"default_model": "anthropic:claude-sonnet-5-5", "api_key": "sk-x"})
    _manage(env)
    assert ModelConfigStore().public()["default_model"] == "anthropic:claude-sonnet-5-5"


def test_models_are_exactly_the_environments(env):
    _manage(env)
    _no_network(env)
    rows = _client().get("/api/models?all=1").json()["models"]
    mine = [(r["id"], r.get("label")) for r in rows if r["provider_key"] == "onw_ai"]
    assert mine == [(m["id"], m["label"]) for m in MODELS]
    single = _client().get("/api/models").json()
    assert [r["id"] for r in single["models"]] == [m["id"] for m in MODELS]


def test_the_environment_wins_over_a_stored_key(env):
    ModelConfigStore().update_provider("onw_ai", {"api_key": "sk-stored-old", "model": "x"})
    _manage(env)
    creds = ModelConfigStore().resolve_for_provider("onw_ai")
    assert (creds["base_url"], creds["api_key"]) == (BASE, KEY)


def test_a_managed_provider_cannot_be_edited_or_removed(env):
    _manage(env)
    client = _client()
    for body in ({"api_key": "sk-other"}, {"base_url": "https://evil.example/v1"}):
        assert client.put("/api/model-config/providers/onw_ai", json=body).status_code == 409
    assert client.delete("/api/model-config/providers/onw_ai").status_code == 409
    assert client.delete("/api/model-config/providers/onw_ai/key").status_code == 409
    # Nothing to reveal: the environment's key is not a stored key.
    assert client.get("/api/model-config/providers/onw_ai/reveal-key").status_code == 404


def test_the_managed_endpoint_is_attributed_to_onw_ai(env):
    from backend.db.model_config_store import _infer_provider_key, provider_label_for

    _manage(env)
    assert _infer_provider_key("openai_compatible", BASE) == "onw_ai"
    assert provider_label_for("openai_compatible", BASE) == "Open Net Worth AI"


class _Stream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_request_body_carries_the_extras_and_streams(env):
    from backend.ai.models.openai_provider import OpenAIProvider

    _manage(env, extras={"x": {"y": 1}})
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return _Stream()

    provider = OpenAIProvider(base_url=BASE, api_key=KEY)
    provider._client = NS(chat=NS(completions=NS(create=create)))
    async for _ in provider.stream_chat(
        [{"role": "user", "content": "hi"}], [], system="", model="acme-flash"
    ):
        pass
    assert calls and calls[0]["stream"] is True
    assert calls[0]["extra_body"]["x"] == {"y": 1}


def test_extras_reach_only_the_managed_endpoint(env):
    _manage(env, extras={"x": {"y": 1}})
    assert onw_ai.request_extras_for(BASE + "/") == {"x": {"y": 1}}
    assert onw_ai.request_extras_for("https://api.openai.com/v1") == {}


def test_deep_merge_keeps_nested_keys():
    merged = onw_ai.deep_merge({"provider": {"a": 1}, "k": 1}, {"provider": {"b": 2}})
    assert merged == {"provider": {"a": 1, "b": 2}, "k": 1}


_NEW_CODE = (
    "backend/core/onw_ai.py",
    "backend/services/onw_ai.py",
    "frontend/src/components/tabs/settings/sections/OnwAiCredit.tsx",
    "frontend/src/components/tabs/settings/sections/onwAiStatus.ts",
    "docs/product/getting-started/open-net-worth-ai.md",
)


def test_no_upstream_vendor_is_named_in_the_new_code():
    root = Path(__file__).resolve().parents[1]
    for rel in _NEW_CODE:
        text = (root / rel).read_text(encoding="utf-8").lower()
        assert not re.search(r"open\s*router", text), rel
