"""GET/PUT /api/feature-flags — the owner's Experimental switches, gated like main.py."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.api.demo_lockdown import is_demo_locked, refuse_on_demo

NAME = "example_flag"


@pytest.fixture
def env(db_isolated, monkeypatch):
    for key in (
        "AUTH_DISABLED",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
        "DEMO_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield monkeypatch
    config._settings = None


def _client(remote: bool = False) -> TestClient:
    from backend.api._deps import require_auth
    from backend.api.feature_flags import router

    app = FastAPI(dependencies=[Depends(require_auth), Depends(refuse_on_demo)])
    app.include_router(router)
    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


def _row(body: dict) -> dict:
    return next(r for r in body["flags"] if r["name"] == NAME)


def test_list_shape(env):
    r = _client().get("/api/feature-flags")
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    row = _row(body)
    assert row == {
        "name": NAME,
        "description": row["description"],
        "kind": "release",
        "default": False,
        "enabled": False,
        "source": "default",
        "stored": None,
        "env_var": "ONW_FLAG_EXAMPLE_FLAG",
        "owner": "tests",
        "added": "2026-10-25",
        "remove_by": "2099-12-31",
    }


def test_put_sets_then_null_resets(env):
    client = _client()
    r = client.put(f"/api/feature-flags/{NAME}", json={"enabled": True})
    assert r.status_code == 200
    assert (r.json()["enabled"], r.json()["source"], r.json()["stored"]) == (True, "stored", True)
    assert _row(client.get("/api/feature-flags").json())["enabled"] is True
    r = client.put(f"/api/feature-flags/{NAME}", json={"enabled": None})
    assert (r.json()["enabled"], r.json()["source"]) == (False, "default")


def test_put_requires_the_field(env):
    assert _client().put(f"/api/feature-flags/{NAME}", json={}).status_code == 422


def test_put_reports_an_env_override_that_outranks_it(env):
    env.setenv("ONW_FLAG_EXAMPLE_FLAG", "0")
    body = _client().put(f"/api/feature-flags/{NAME}", json={"enabled": True}).json()
    assert (body["enabled"], body["source"], body["stored"]) == (False, "env", True)


def test_a_malformed_env_value_does_not_break_the_list(env):
    env.setenv("ONW_FLAG_EXAMPLE_FLAG", "yes")
    r = _client().get("/api/feature-flags")
    assert r.status_code == 200
    assert (_row(r.json())["enabled"], _row(r.json())["source"]) == (False, "default")


def test_unknown_flag_is_404(env):
    assert _client().put("/api/feature-flags/nope_flag", json={"enabled": True}).status_code == 404


def test_both_routes_are_auth_gated(env):
    client = _client(remote=True)
    assert client.get("/api/feature-flags").status_code == 401
    assert client.put(f"/api/feature-flags/{NAME}", json={"enabled": True}).status_code == 401


def test_demo_reads_defaults_and_refuses_writes(env):
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings
    assert is_demo_locked("PUT", f"/api/feature-flags/{NAME}")
    assert not is_demo_locked("GET", "/api/feature-flags")
    client = _client()
    r = client.put(f"/api/feature-flags/{NAME}", json={"enabled": True})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "demo_locked"
    body = client.get("/api/feature-flags").json()
    assert body["locked"] is True
    assert (_row(body)["enabled"], _row(body)["source"]) == (False, "demo")
