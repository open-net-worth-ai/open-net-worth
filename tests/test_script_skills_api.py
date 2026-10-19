"""Script-skills instance kill-switch API — GET/PUT /api/config/script-skills.

Hermetic: each test redirects AppConfigStore to a tmp file so the live data dir
is never touched (the conftest backstop also guards this). Auth-gated: the PUT
must be inaccessible to unauthenticated remote callers.

What is proven:
  - GET returns ``enabled: false`` on a fresh instance (default OFF).
  - PUT true/false toggles the flag and returns the new value.
  - Round-trip: a fresh GET after PUT reflects the persisted state.
  - The kill-switch flag is NOT visible in the public branding GET /api/config
    (branding and kill-switch are kept in separate projections).
  - GET is AUTH-GATED (unlike the public /api/config branding endpoint which is
    auth-exempt). A remote, unauthenticated caller cannot read the flag.
  - PUT is AUTH-GATED. A remote, unauthenticated caller cannot flip the switch.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config


@pytest.fixture
def cfg_env(tmp_path, monkeypatch, db_isolated):
    """AppConfigStore is DB-backed (runtime_state, namespace "app_config") — db_isolated
    gives this test its own fresh database. Clear auth + branding env."""
    for key in (
        "APP_NAME",
        "APP_ACCENT_COLOR",
        "APP_LOGO_URL",
        "AUTH_DISABLED",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    yield monkeypatch
    config._settings = None


def _client(remote: bool = False) -> TestClient:
    """Minimal app with only the config router — exercises the real gate/exemption.
    ``remote=True`` simulates a request from a non-loopback address."""
    from fastapi import Depends, FastAPI

    from backend.api._deps import require_auth
    from backend.api.config import router as config_router

    app = FastAPI(dependencies=[Depends(require_auth)])
    app.include_router(config_router)

    if remote:
        return TestClient(app, client=("203.0.113.7", 9999))
    return TestClient(app)


# ── GET default ──────────────────────────────────────────────────────────────


def test_get_script_skills_defaults_off(cfg_env):
    """A fresh instance returns enabled=false — default OFF (safe)."""
    r = _client().get("/api/config/script-skills")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


# ── PUT round-trip ────────────────────────────────────────────────────────────


def test_put_script_skills_enable(cfg_env):
    """PUT {enabled: true} turns the switch on and returns the new state."""
    r = _client().put("/api/config/script-skills", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"enabled": True}


def test_put_script_skills_disable(cfg_env):
    """PUT {enabled: false} turns the switch off."""
    client = _client()
    # First turn it on.
    client.put("/api/config/script-skills", json={"enabled": True})
    # Then turn it back off.
    r = client.put("/api/config/script-skills", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_put_script_skills_round_trips_via_get(cfg_env):
    """A GET after PUT reflects the persisted state (file-authoritative)."""
    client = _client()
    client.put("/api/config/script-skills", json={"enabled": True})
    r = client.get("/api/config/script-skills")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_put_script_skills_preserves_branding(cfg_env):
    """Toggling the kill switch leaves unrelated branding fields intact."""
    client = _client()
    client.put("/api/config", json={"app_name": "Acme Wealth", "accent_color": "#abcdef"})
    client.put("/api/config/script-skills", json={"enabled": True})
    branding = client.get("/api/config").json()
    assert branding["app_name"] == "Acme Wealth"
    assert branding["accent_color"] == "#abcdef"
    # Kill switch is NOT in the public branding projection.
    assert "script_skills_enabled" not in branding
    assert "enabled" not in branding


# ── Flag NOT in public /api/config branding ──────────────────────────────────


def test_script_skills_flag_absent_from_public_branding(cfg_env):
    """The kill-switch flag must never ride GET /api/config (auth-exempt, login-screen safe)."""
    client = _client()
    client.put("/api/config/script-skills", json={"enabled": True})
    branding = client.get("/api/config").json()
    assert set(branding.keys()) == {"app_name", "accent_color", "logo_url"}


# ── Auth gate ────────────────────────────────────────────────────────────────


def test_get_script_skills_gated_remote(cfg_env):
    """GET /api/config/script-skills is NOT auth-exempt — a remote unauthenticated
    request is refused (unlike the public branding GET /api/config)."""
    r = _client(remote=True).get("/api/config/script-skills")
    assert r.status_code == 401


def test_put_script_skills_gated_remote(cfg_env):
    """PUT /api/config/script-skills is auth-gated — cannot be flipped from outside."""
    r = _client(remote=True).put("/api/config/script-skills", json={"enabled": True})
    assert r.status_code == 401


# ── Module helper ─────────────────────────────────────────────────────────────


def test_script_skills_enabled_helper_reflects_store(db_isolated):
    """The module-level helper ``script_skills_enabled()`` reads the flag off the
    (DB-backed) store, matching the canonical read used by ``run_script``."""
    from backend.db.app_config_store import (
        SCRIPT_SKILLS_FLAG,
        AppConfigStore,
        script_skills_enabled,
    )

    assert script_skills_enabled() is False  # absent row → default off
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, True)
    assert script_skills_enabled() is True
    AppConfigStore().set_flag(SCRIPT_SKILLS_FLAG, False)
    assert script_skills_enabled() is False


# ── The unattended gate is gone ────────────────────────────────────────────────


def test_the_unattended_routes_are_gone(cfg_env):
    client = _client()
    assert client.get("/api/config/script-skills-unattended").status_code in (404, 405)
    assert client.put(
        "/api/config/script-skills-unattended", json={"enabled": True}
    ).status_code in (404, 405)
