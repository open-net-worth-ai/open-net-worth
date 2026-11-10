"""Regression tests for the chat-suggestions master switch.

Covers:
  - profile.chat_suggestions_enabled defaults to True
  - PUT /api/profile persists the flag both ways (round-trip)
  - POST /api/chat/suggestions short-circuits to {suggestions: []} when OFF,
    WITHOUT calling the model (proves no Haiku cost is incurred)
  - POST /api/chat/suggestions still reaches the generator when ON
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import chat_suggestions as cs
from backend.api.profile import router as profile_router


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient over a fresh (DB-isolated-by-conftest) profile row with the starter cache
    reset (no live data). Profile READ (services.profile) and WRITE (services.budgets.
    write_profile_raw, the single authoritative write path) both resolve the same DB-backed
    ProfileStore (Wave 5, JSON->DB) now, and conftest's autouse fixture already resets that
    row to {} before every test — no monkeypatch needed."""
    cs._STARTER_CACHE["value"] = None
    cs._STARTER_CACHE["at"] = 0.0

    app = FastAPI()
    app.include_router(profile_router)
    app.include_router(cs.router)
    return TestClient(app)


def test_default_enabled_true(client):
    """A fresh profile reports the chip switch ON."""
    body = client.get("/api/profile").json()
    assert body["chat_suggestions_enabled"] is True


def test_screen_awareness_default_starts_on_and_round_trips(client):
    # An unset key is on: the app reads anything but an explicit False that way.
    assert client.get("/api/profile").json().get("screen_awareness_default", True) is True

    r = client.put("/api/profile", json={"screen_awareness_default": False})
    assert r.status_code == 200
    assert r.json()["profile"]["screen_awareness_default"] is False
    assert client.get("/api/profile").json()["screen_awareness_default"] is False


def test_toggle_round_trips(client):
    """PUT False persists and reads back; PUT True restores."""
    r = client.put("/api/profile", json={"chat_suggestions_enabled": False})
    assert r.status_code == 200
    assert r.json()["profile"]["chat_suggestions_enabled"] is False
    assert client.get("/api/profile").json()["chat_suggestions_enabled"] is False

    client.put("/api/profile", json={"chat_suggestions_enabled": True})
    assert client.get("/api/profile").json()["chat_suggestions_enabled"] is True


def test_partial_update_does_not_clobber_flag(client):
    """An unrelated profile PUT leaves the (disabled) flag intact."""
    client.put("/api/profile", json={"chat_suggestions_enabled": False})
    client.put("/api/profile", json={"risk_tolerance": "aggressive"})
    body = client.get("/api/profile").json()
    assert body["chat_suggestions_enabled"] is False
    assert body["risk_tolerance"] == "aggressive"


def test_disabled_short_circuits_without_model_call(client, monkeypatch):
    """OFF → {suggestions: []} and the generator is never invoked."""
    called: list[int] = []

    async def fake_generate(*_a, **_k):
        called.append(1)
        return ["should not be reached"]

    monkeypatch.setattr(cs, "_generate", fake_generate)
    client.put("/api/profile", json={"chat_suggestions_enabled": False})

    r = client.post("/api/chat/suggestions", json={})
    assert r.status_code == 200
    assert r.json() == {"suggestions": []}
    assert called == []  # gate returned before any model/context work


def test_enabled_reaches_generator(client, monkeypatch):
    """ON (default) + no session → starter mode calls the generator."""
    called: list[int] = []

    async def fake_generate(*_a, **_k):
        called.append(1)
        return ["s1", "s2"]

    monkeypatch.setattr(cs, "_generate", fake_generate)
    monkeypatch.setattr(cs, "_starter_context", lambda: "ctx")

    r = client.post("/api/chat/suggestions", json={})
    assert r.status_code == 200
    assert r.json() == {"suggestions": ["s1", "s2"]}
    assert called == [1]
