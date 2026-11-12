"""REST smoke for the Web Search connector endpoints (GET status / per-provider PUT / active).

TestClient on a bare app with ONLY the search-config router mounted; the store is isolated to
tmp_path. Verifies: the per-provider catalog + has_key shape, that setting a provider's key is
write-only (never returned) + auto-enables it, that multiple providers enable independently,
that ``set_default`` (and the active-only PUT) pick the tool's provider, and that a blank key
keeps the saved one.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    app = FastAPI()
    from backend.api.search_config_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default(client):
    r = client.get("/api/search/config")
    assert r.status_code == 200
    body = r.json()
    # Zero-config: the key-free DuckDuckGo default is active out of the box.
    assert body["default_provider"] == "duckduckgo"
    ids = [p["id"] for p in body["providers"]]
    assert ids == [
        "duckduckgo",
        "tavily",
        "perplexity",
        "serper",
        "google_pse",
        "firecrawl",
        "keirolabs",
    ]
    by_id = {p["id"]: p for p in body["providers"]}
    # DuckDuckGo: key-free, enabled-by-default.
    assert by_id["duckduckgo"]["needs_api_key"] is False
    assert by_id["duckduckgo"]["has_key"] is True
    assert by_id["duckduckgo"]["enabled"] is True
    # The keyed providers default off + keyless.
    assert all(
        p["has_key"] is False and p["enabled"] is False
        for p in body["providers"]
        if p["id"] != "duckduckgo"
    )


def test_put_provider_key_write_only_and_auto_enables(client):
    r = client.put("/api/search/config/serper", json={"api_key": "serper-secret"})
    assert r.status_code == 200
    body = r.json()
    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["serper"]["enabled"] is True  # a fresh key auto-enables
    assert by_id["serper"]["has_key"] is True
    # A configured keyed provider takes precedence over the key-free default.
    assert body["default_provider"] == "serper"
    # The raw key never appears in the response.
    assert "serper-secret" not in json.dumps(body)


def test_providers_enable_independently(client):
    client.put("/api/search/config/tavily", json={"api_key": "t"})
    client.put("/api/search/config/serper", json={"api_key": "s"})
    body = client.get("/api/search/config").json()
    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["tavily"]["enabled"] is True
    assert by_id["serper"]["enabled"] is True
    # Disable just one.
    client.put("/api/search/config/tavily", json={"enabled": False})
    by_id = {p["id"]: p for p in client.get("/api/search/config").json()["providers"]}
    assert by_id["tavily"]["enabled"] is False
    assert by_id["serper"]["enabled"] is True


def test_set_default_via_provider_put(client):
    client.put("/api/search/config/tavily", json={"api_key": "t"})
    client.put("/api/search/config/serper", json={"api_key": "s"})
    r = client.put("/api/search/config/serper", json={"set_default": True})
    assert r.json()["default_provider"] == "serper"


def test_set_default_via_root_put(client):
    client.put("/api/search/config/tavily", json={"api_key": "t"})
    client.put("/api/search/config/serper", json={"api_key": "s"})
    r = client.put("/api/search/config", json={"default_provider": "serper"})
    assert r.status_code == 200
    assert r.json()["default_provider"] == "serper"


def test_google_pse_extra_field_roundtrips(client):
    r = client.put(
        "/api/search/config/google_pse",
        json={"api_key": "k", "extra": {"cx": "engine-9"}},
    )
    body = r.json()
    pse = next(p for p in body["providers"] if p["id"] == "google_pse")
    cx = next(f for f in pse["extra_fields"] if f["key"] == "cx")
    assert cx["value"] == "engine-9"  # non-secret value echoed back


def test_blank_key_keeps_saved(client):
    client.put("/api/search/config/tavily", json={"api_key": "tvly-keep"})
    r = client.put("/api/search/config/tavily", json={"api_key": "", "enabled": False})
    body = r.json()
    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["tavily"]["has_key"] is True  # still set
    assert by_id["tavily"]["enabled"] is False
