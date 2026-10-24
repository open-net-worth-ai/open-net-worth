"""A public demo (DEMO_MODE) hands every visitor the operator (a published password, or none at
all under DEMO_OPEN_ACCESS — tests/test_demo_open_access.py), so each visitor IS the
operator. These pin what such a visitor still cannot do: read a stored secret back, move where
the AI key is sent (a repointed base_url delivers it to the visitor's server), add an MCP
server (stdio = arbitrary local code), switch the sandbox on, or change the shared password —
and that all of it is INERT off a demo.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.api.demo_lockdown import is_demo_locked, refuse_on_demo
from backend.core.errors import ConfigurationError
from backend.db import app_config_store
from backend.db.model_config_store import ModelConfigStore

_KEY = "sk-or-test-not-a-real-key"


@pytest.fixture
def book(db_isolated, monkeypatch):
    for key in (
        "AUTH_DISABLED",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_SESSION_KEY",
        "DEMO_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    store = ModelConfigStore()
    store.update_provider("openrouter", {"api_key": _KEY, "model": "anthropic/claude-sonnet-5.5"})
    store.update({"default_model": "openrouter:anthropic/claude-sonnet-5.5"})
    yield store
    config._settings = None


@pytest.fixture
def demo(book):
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings
    return book


def _client() -> TestClient:
    """Gated exactly like main.py: require_auth, then the demo lockdown."""
    from backend.api._deps import require_auth
    from backend.api.mcp_client_api import router as mcp_router
    from backend.api.models import router as models_router

    app = FastAPI(dependencies=[Depends(require_auth), Depends(refuse_on_demo)])
    app.include_router(models_router)
    app.include_router(mcp_router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/model-config/providers/openrouter/reveal-key"),
        ("GET", "/api/connectors/telegram/secret/bot_token"),
        ("PUT", "/api/model-config"),
        ("PUT", "/api/model-config/providers/openrouter"),
        ("DELETE", "/api/model-config/providers/openrouter/key"),
        ("POST", "/api/model-config/providers/custom/validate"),
        ("POST", "/api/mcp-clients"),
        ("POST", "/api/mcp-clients/probe"),
        ("PATCH", "/api/mcp-clients/srv_1"),
        ("POST", "/api/auth/change-password"),
        ("POST", "/api/auth/remove-password"),
        ("PUT", "/api/auth/settings"),
        ("POST", "/api/shortcuts/prepare"),
        ("GET", "/api/shortcuts/setup"),
        ("GET", "/api/shortcuts/file"),
        ("GET", "/api/shortcuts/setup-code"),
        ("GET", "/api/auth/api-token"),
        ("POST", "/api/auth/api-token/rotate"),
        ("GET", "/api/terminal"),
        ("GET", "/api/terminal/status"),
        ("POST", "/api/move/secrets/export"),
        ("POST", "/api/move/export-nonce"),
        ("POST", "/api/move/begin"),
        ("POST", "/api/move/mv_1/secrets"),
        ("PUT", "/api/move/mv_1/parts/3"),
        ("POST", "/api/builder/runs"),
        ("GET", "/api/builder/status"),
        ("POST", "/api/reset"),
        ("POST", "/api/backup/b1/restore"),
        ("POST", "/api/data/import"),
        ("POST", "/api/automations"),
        ("POST", "/api/automations/a1/run"),
        ("POST", "/api/extensions/install"),
        ("PUT", "/api/channels/telegram"),
        ("PUT", "/api/plaid/config"),
        ("GET", "/api/logs"),
        ("POST", "/mcp"),
        ("POST", "/api/some-router-nobody-classified/act"),
    ],
)
def test_locked_routes(method, path):
    assert is_demo_locked(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/model-config"),
        ("GET", "/api/model-config/providers"),
        ("GET", "/api/mcp-clients"),
        ("POST", "/api/chat"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/settings"),
        ("PUT", "/api/dashboard/layout"),
        ("POST", "/api/transactions"),
        ("PUT", "/api/planning/targets/env_1"),
        ("DELETE", "/api/chat/s1"),
        ("GET", "/api/automations"),
    ],
)
def test_the_demo_itself_stays_usable(method, path):
    assert not is_demo_locked(method, path)


def test_main_app_wires_the_lockdown():
    from backend.main import app

    assert any(d.dependency is refuse_on_demo for d in app.router.dependencies)


def test_a_demo_visitor_cannot_reveal_or_repoint_the_key(demo):
    client = _client()
    assert client.get("/api/model-config/providers/openrouter/reveal-key").status_code == 403
    r = client.put(
        "/api/model-config/providers/openrouter", json={"base_url": "https://evil.example/v1"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "demo_locked"
    assert client.post("/api/mcp-clients/probe", json={}).status_code == 403
    # Reading the (secret-free) settings still works, and nothing moved.
    assert client.get("/api/model-config").status_code == 200
    assert demo.resolved()["base_url"] == "https://openrouter.ai/api/v1"
    assert demo.resolved()["api_key"] == _KEY


def test_a_demo_visitor_reaches_neither_the_terminal_nor_the_move_protocol(demo):
    from starlette.websockets import WebSocketDisconnect

    from backend.api._deps import require_auth
    from backend.api.move import router as move_router
    from backend.api.terminal import router as terminal_router

    app = FastAPI(dependencies=[Depends(require_auth), Depends(refuse_on_demo)])
    app.include_router(move_router)
    app.include_router(terminal_router)
    client = TestClient(app)
    for path in ("/api/move/export-nonce", "/api/move/secrets/export", "/api/move/begin"):
        r = client.post(path, headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "demo_locked", path
    assert client.get("/api/terminal/status").status_code == 403
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/terminal"):
        pass


def test_off_a_demo_the_operator_keeps_every_affordance(book):
    client = _client()
    r = client.get("/api/model-config/providers/openrouter/reveal-key")
    assert r.status_code == 200 and r.json()["api_key"] == _KEY


def test_lucius_tools_cannot_move_the_key_either(demo):
    """update_model_config / set_provider_credentials write through the store, not a route."""
    with pytest.raises(ConfigurationError):
        demo.update_provider("openrouter", {"base_url": "https://evil.example/v1"})
    with pytest.raises(ConfigurationError):
        demo.update({"default_model": "custom:x", "base_url": "https://evil.example/v1"})
    with pytest.raises(ConfigurationError):
        demo.remove_provider_key("openrouter")
    with pytest.raises(ConfigurationError):
        demo.remove_provider("openrouter")
    assert demo.resolved()["base_url"] == "https://openrouter.ai/api/v1"


@pytest.mark.parametrize(
    ("reader", "flag"),
    [
        (app_config_store.code_execution_enabled, app_config_store.CODE_EXEC_ENABLED_FLAG),
        (app_config_store.script_skills_enabled, app_config_store.SCRIPT_SKILLS_FLAG),
    ],
)
def test_the_sandbox_stays_off_on_a_demo_even_if_switched_on(book, reader, flag):
    app_config_store.AppConfigStore().set_flag(flag, True)
    assert reader() is True
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings
    assert reader() is False
