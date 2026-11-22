"""Open Net Worth AI with a pasted key (desktop / self-hosted).

The key is stored like every other provider key (the secret ``model_config`` row) and never
leaves the server again: not in an API response, a log line, or what the model can read. The
endpoint defaults to the public relay and must be https except to the overlay hub or this
machine. Models come from the relay's ``GET /v1/models``. Synthetic values only.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.api.models as models_api
import backend.core.config as config
from backend.core.sensitive import SECRET_TABLES
from backend.db.model_config_store import ModelConfigStore

KEY = "sk-test-pasted-key"
PUBLIC = "https://ai.opennetworth.ai/v1"
RELAY_MODELS = {"object": "list", "data": [{"id": "claude-haiku-5.5"}, {"id": "acme-flash"}]}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(models_api, "MODELS_CACHE_JSON", tmp_path / "models_cache.json")
    monkeypatch.delenv("ONW_TEST_DEFAULT_MODEL", raising=False)
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
    # Public hosts resolve without DNS in a test.
    monkeypatch.setattr(models_api, "_assert_endpoint_allowed", lambda _url: None)
    config._settings = None
    yield monkeypatch
    config._settings = None


def _relay(monkeypatch) -> list[tuple[str, dict]]:
    """A fake relay that answers /v1/models for the pasted key and refuses anything else."""
    seen: list[tuple[str, dict]] = []

    def fake_get(url, headers=None, **_kwargs):
        seen.append((url, dict(headers or {})))
        request = httpx.Request("GET", url)
        if (headers or {}).get("Authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": {"message": "bad key"}}, request=request)
        return httpx.Response(200, json=RELAY_MODELS, request=request)

    monkeypatch.setattr(models_api.httpx, "get", fake_get)
    return seen


def _client() -> TestClient:
    from backend.api._deps import require_auth

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(models_api.router)
    return TestClient(app)


def _save(client: TestClient, **body):
    return client.put("/api/model-config/providers/onw_ai", json={"enabled": True, **body})


def test_a_pasted_key_validates_and_lists_the_relays_models(env):
    seen = _relay(env)
    client = _client()
    resp = _save(client, base_url=PUBLIC, api_key=KEY, model="claude-haiku-5.5")
    assert resp.json()["key_status"] == "valid"
    assert resp.json()["models"] == ["claude-haiku-5.5", "acme-flash"]
    assert seen[0] == (PUBLIC + "/models", {"Authorization": f"Bearer {KEY}"})

    env.setattr(models_api, "_read_cache", lambda _key: None)
    rows = client.get("/api/models?all=1").json()["models"]
    assert [r["id"] for r in rows if r["provider_key"] == "onw_ai"] == [
        "claude-haiku-5.5",
        "acme-flash",
    ]


def test_a_rejected_key_says_so(env):
    _relay(env)
    resp = _save(_client(), base_url=PUBLIC, api_key="sk-test-wrong-key")
    assert resp.json()["key_status"] == "invalid"


def test_the_key_never_leaves_the_server(env, caplog):
    _relay(env)
    client = _client()
    caplog.set_level(logging.DEBUG)
    texts = [_save(client, base_url=PUBLIC, api_key=KEY, model="acme-flash").text]
    client.put("/api/model-config", json={"default_model": "onw_ai:acme-flash"})
    for path in (
        "/api/model-config",
        "/api/model-config/providers",
        "/api/models?all=1",
        "/api/models",
        "/api/model-config/onw-ai",
    ):
        texts.append(client.get(path).text)
    texts.append(client.post("/api/model-config/providers/onw_ai/validate").text)
    for text in texts:
        assert KEY not in text
    assert KEY not in caplog.text
    # Stored where every provider key is — the secret config row — and only there.
    assert ModelConfigStore().read_stored_api_key("onw_ai") == KEY
    assert "secret_config" in SECRET_TABLES


def test_the_model_cannot_read_the_key(env):
    from backend.ai.tools.model_config import get_model_config

    ModelConfigStore().update_provider("onw_ai", {"api_key": KEY})
    text, _ = asyncio.run(get_model_config({}))
    assert KEY not in text
    assert json.loads(text)["configured_providers"]["onw_ai"]["has_api_key"] is True


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (PUBLIC, True),
        ("https://relay.example.test/v1", True),
        ("http://10.99.0.1:4000/v1", True),
        ("http://localhost:4000/v1", True),
        ("http://127.0.0.1:4000/v1", True),
        ("http://relay.example.test/v1", False),
        ("http://10.99.0.2:4000/v1", False),
        ("ftp://ai.opennetworth.ai/v1", False),
    ],
)
def test_the_endpoint_must_be_https_except_overlay_or_local(env, url, ok):
    _relay(env)
    status = _save(_client(), base_url=url, api_key=KEY).status_code
    assert (status == 200) is ok, (url, status)


def test_moving_the_endpoint_without_a_new_key_drops_the_old_one(env):
    _relay(env)
    client = _client()
    _save(client, base_url=PUBLIC, api_key=KEY)
    _save(client, base_url="https://relay.example.test/v1")
    assert ModelConfigStore().read_stored_api_key("onw_ai") is None


def test_the_environment_wins_over_a_pasted_key(env):
    ModelConfigStore().update_provider("onw_ai", {"api_key": KEY, "base_url": PUBLIC})
    env.setenv("ONW_AI_BASE_URL", "http://10.99.0.1:4000/v1")
    env.setenv("ONW_AI_KEY", "sk-test-managed-key")
    env.setenv("ONW_AI_MODELS", json.dumps([{"id": "acme-flash", "label": "Acme"}]))
    creds = ModelConfigStore().resolve_for_provider("onw_ai")
    assert creds["base_url"] == "http://10.99.0.1:4000/v1"
    assert creds["api_key"] == "sk-test-managed-key"
    row = _client().get("/api/model-config/providers").json()["configured_providers"]["onw_ai"]
    assert row["managed"] is True
