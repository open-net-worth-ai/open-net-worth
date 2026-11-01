"""REST smoke for the GoCardless endpoints — status / config / institutions / link / check-link.

The service functions that call GoCardless are mocked; the store is real and DB-isolated."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.errors import ValidationError


@pytest.fixture
def client(db_isolated, monkeypatch):
    from backend.services.aggregation import gocardless as svc

    monkeypatch.setattr(
        svc, "list_institutions", lambda country: [{"id": "ACME_BANK_GB", "name": "ACME Bank"}]
    )
    calls = {}

    def _start_link(institution_id, country, redirect):
        calls["link"] = (institution_id, country, redirect)
        return {"link": "https://ob.gocardless.com/x"}

    monkeypatch.setattr(svc, "start_link", _start_link)
    app = FastAPI()
    from backend.api.gocardless_api import router

    app.include_router(router)
    test_client = TestClient(app)
    test_client.calls = calls
    return test_client


def _store():
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    return GoCardlessConfigStore()


def test_status_default_not_configured(client):
    body = client.get("/api/gocardless/status").json()
    assert body["configured"] is False
    assert body["has_credentials"] is False


def test_put_saves_credentials_and_never_echoes_the_key(client):
    body = client.put(
        "/api/gocardless/config", json={"secret_id": "SID", "secret_key": "SKEY", "enabled": True}
    ).json()
    assert body["has_credentials"] is True and body["secret_id"] == "SID"
    assert "SKEY" not in repr(body)


def test_changing_the_key_forgets_the_cached_token(client):
    client.put("/api/gocardless/config", json={"secret_id": "SID", "secret_key": "SKEY"})
    _store().save_token("TOK", None)
    client.put("/api/gocardless/config", json={"secret_key": ""})  # blank keeps the key …
    assert _store().get()["access_token"] == "TOK"  # … and the token
    client.put("/api/gocardless/config", json={"secret_key": "NEW"})
    assert _store().get()["access_token"] == ""


def test_institutions_and_link(client):
    assert (
        client.get("/api/gocardless/institutions?country=GB").json()["institutions"][0]["id"]
        == "ACME_BANK_GB"
    )
    resp = client.post(
        "/api/gocardless/link",
        json={"institution_id": "ACME_BANK_GB", "country": "GB", "redirect": "http://localhost"},
    )
    assert resp.json() == {"link": "https://ob.gocardless.com/x"}
    assert client.calls["link"] == ("ACME_BANK_GB", "GB", "http://localhost")


def test_a_service_validation_error_is_a_422(client, monkeypatch):
    from backend.services.aggregation import gocardless as svc

    def _bad(country):
        raise ValidationError("country must be a 2-letter ISO code")

    monkeypatch.setattr(svc, "list_institutions", _bad)
    resp = client.get("/api/gocardless/institutions?country=GBR")
    assert resp.status_code == 422
    assert "2-letter" in resp.json()["detail"]


def test_check_link_without_a_requisition_is_refused(client):
    resp = client.post("/api/gocardless/check-link")
    assert resp.status_code == 422
