"""REST smoke for the Plaid endpoints (status / config / link / exchange).

TestClient on a bare app with ONLY the plaid router mounted; PlaidConfigStore isolated
via db_isolated. The link/exchange calls are mocked (NO real network). Verifies: the
secret is write-only (never returned), config reports has_secret, the link flow returns
the Hosted Link URL, exchange persists the access token, and a blank secret keeps the
saved one. Fetching + booking transactions is no longer a REST action (RFC
connectors-as-data-sources) — see tests/test_connector_fetch_tools.py for
plaid_fetch_transactions.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_isolated, monkeypatch):

    from backend.services.aggregation import plaid as svc

    monkeypatch.setattr(
        svc,
        "create_link_token",
        lambda redirect="": {
            "link": "https://hosted.plaid.com/link/1",
            "link_token": "link-1",
            "expiration": None,
        },
    )
    monkeypatch.setattr(
        svc,
        "exchange_public_token",
        lambda public_token: {"item_id": "item-9", "has_access_token": True},
    )

    monkeypatch.setattr(
        svc,
        "complete_hosted_link",
        lambda: {"status": "linked", "item_id": "item-9", "has_access_token": True},
    )

    app = FastAPI()
    from backend.api.plaid_api import router

    app.include_router(router)
    return TestClient(app)


def test_status_default_not_configured(client):
    r = client.get("/api/plaid/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_secret"] is False
    assert body["has_access_token"] is False
    assert body["environment"] == "sandbox"
    assert "secret" not in body
    assert "access_token" not in body


def test_put_sets_creds_and_hides_secret(client):
    r = client.put(
        "/api/plaid/config",
        json={
            "client_id": "cid",
            "secret": "super-secret",
            "environment": "production",
            "enabled": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_secret"] is True
    assert body["client_id"] == "cid"
    assert body["environment"] == "production"
    assert body["enabled"] is True
    # The secret must never be returned.
    assert "secret" not in body
    assert "super-secret" not in json.dumps(body)


def test_put_blank_secret_keeps_saved(client):
    client.put("/api/plaid/config", json={"client_id": "cid", "secret": "keep"})
    r = client.put("/api/plaid/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_secret"] is True
    assert body["enabled"] is False


def test_link_returns_hosted_url(client):
    r = client.post("/api/plaid/link", json={"redirect": "https://app/connectors"})
    assert r.status_code == 200
    body = r.json()
    assert body["link"] == "https://hosted.plaid.com/link/1"


def test_exchange_persists_access(client):
    r = client.post("/api/plaid/exchange", json={"public_token": "public-xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body["item_id"] == "item-9"
    assert body["has_access_token"] is True


def test_link_bad_config_returns_422(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import plaid as svc

    def _raise(redirect=""):
        raise ValidationError("Plaid client_id / secret are not configured")

    monkeypatch.setattr(svc, "create_link_token", _raise)
    r = client.post("/api/plaid/link", json={})
    assert r.status_code == 422


def test_link_complete_returns_the_linked_item(client):
    r = client.post("/api/plaid/link/complete")
    assert r.status_code == 200
    assert r.json() == {"status": "linked", "item_id": "item-9", "has_access_token": True}


def test_link_complete_without_a_link_returns_422(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import plaid as svc

    def _raise():
        raise ValidationError("No Plaid link is in progress")

    monkeypatch.setattr(svc, "complete_hosted_link", _raise)
    assert client.post("/api/plaid/link/complete").status_code == 422
