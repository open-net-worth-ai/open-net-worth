"""A public demo's book is shared by every visitor, so its base currency is locked: neither the
Settings screen (PUT /api/profile) nor Lucius (update_profile) may change it, while every other
profile field stays editable. Off a demo, nothing changes.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.ai.tools import execute_tool
from backend.services.profile import read_profile, write_profile


@pytest.fixture
def usd_book(tmp_path, monkeypatch):
    import backend.core.paths as core_paths

    fx_path = tmp_path / "fx_cache.json"
    monkeypatch.setattr(core_paths, "FX_CACHE_JSON", fx_path)
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    config._settings = None
    write_profile({"base_currency": "USD", "name": "ACME"})
    yield
    config._settings = None


@pytest.fixture
def demo(usd_book):
    settings = config.Settings()
    object.__setattr__(settings, "demo_mode", True)
    config._settings = settings


def _client() -> TestClient:
    from backend.api.profile import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _tool(**changes):
    text, _ = asyncio.run(execute_tool("update_profile", {"changes": changes}))
    return json.loads(text)


def test_settings_screen_cannot_change_base_currency_on_demo(demo):
    res = _client().put("/api/profile", json={"base_currency": "INR"})
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "demo_locked"
    assert read_profile()["base_currency"] == "USD"


def test_lucius_cannot_change_base_currency_on_demo(demo):
    out = _tool(base_currency="INR")
    assert out["ok"] is False
    assert read_profile()["base_currency"] == "USD"


def test_other_profile_fields_stay_editable_on_demo(demo):
    assert (
        _client().put("/api/profile", json={"base_currency": "USD", "name": "Ada"}).status_code
        == 200
    )
    assert _tool(name="Grace")["ok"] is True
    profile = read_profile()
    assert profile["name"] == "Grace"
    assert profile["base_currency"] == "USD"


def test_base_currency_changes_normally_off_demo(usd_book):
    assert _client().put("/api/profile", json={"base_currency": "EUR"}).status_code == 200
    assert read_profile()["base_currency"] == "EUR"
    assert _tool(base_currency="GBP")["ok"] is True
    assert read_profile()["base_currency"] == "GBP"
