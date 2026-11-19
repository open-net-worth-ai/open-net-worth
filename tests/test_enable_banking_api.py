"""REST smoke for the Enable Banking endpoints (status / config / institutions / link /
complete) + the account-linking front-door (institutions / providers).

TestClient on a bare app with the enable_banking + account_linking routers mounted; the
store is DB-backed (secret_config table) and isolated via db_isolated. The JWT/link/session
calls are mocked (NO real network). Verifies: the private_key is write-only (never
returned), config reports has_credentials, the link flow returns the consent url, complete
persists a session, institutions list, and the front-door routes a country to its
providers. Fetching + booking transactions is no longer a REST action (RFC
connectors-as-data-sources) — there is no more ``/sync`` route; a Lucius turn calls
``enable_banking_fetch_transactions`` and books via ``add_transactions`` instead.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PEM = "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----"


@pytest.fixture
def client(db_isolated, monkeypatch):

    from backend.services.aggregation import enable_banking as svc

    monkeypatch.setattr(
        svc,
        "list_institutions",
        lambda country: [{"id": "Bank A", "name": "Bank A", "country": country, "logo": None}],
    )
    monkeypatch.setattr(
        svc,
        "start_authorization",
        lambda aspsp, country, redirect: {
            "url": "https://bank.example/auth/abc",
            "authorization_id": "AUTH-1",
            "aspsp": aspsp,
        },
    )

    def _fake_create_session(code, state=""):
        # Mirror the real create_session's persistence side-effect (the route returns the
        # public status, which reads has_session from the store).
        from backend.db.enable_banking_config_store import EnableBankingConfigStore

        EnableBankingConfigStore().update({"session_id": "SESS-7"})
        return {"session_id": "SESS-7", "accounts": []}

    monkeypatch.setattr(svc, "create_session", _fake_create_session)

    app = FastAPI()
    from backend.api.account_linking_api import router as al_router
    from backend.api.enable_banking_api import router as eb_router

    app.include_router(eb_router)
    app.include_router(al_router)
    return TestClient(app)


def test_status_default_not_configured(client):
    r = client.get("/api/enable-banking/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["has_credentials"] is False
    assert "private_key" not in body


def test_put_sets_creds_and_hides_key(client):
    r = client.put(
        "/api/enable-banking/config",
        json={"application_id": "app-1", "private_key": _PEM, "enabled": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_credentials"] is True
    assert body["application_id"] == "app-1"
    assert body["enabled"] is True
    assert "private_key" not in body
    assert _PEM not in json.dumps(body)


def test_put_blank_key_keeps_saved(client):
    client.put("/api/enable-banking/config", json={"application_id": "app-1", "private_key": _PEM})
    r = client.put("/api/enable-banking/config", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["has_credentials"] is True
    assert body["enabled"] is False


def test_institutions_list(client):
    r = client.get("/api/enable-banking/institutions", params={"country": "DE"})
    assert r.status_code == 200
    assert r.json()["institutions"][0]["id"] == "Bank A"


def test_link_returns_consent_url(client):
    r = client.post("/api/enable-banking/link", json={"aspsp": "Bank A", "country": "DE"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://bank.example/auth/abc"


def test_link_names_the_address_to_add_when_enable_banking_refuses_it(client, monkeypatch):
    """The form shows the address with a Copy button, so it comes back as data, not prose."""
    from backend.core.errors import RedirectNotRegisteredError
    from backend.services.aggregation import enable_banking as svc

    url = "http://localhost:8080/connectors/callback/enable_banking"

    def _refuse(aspsp, country, redirect):
        raise RedirectNotRegisteredError("add it", redirect_url=redirect)

    monkeypatch.setattr(svc, "start_authorization", _refuse)
    r = client.post(
        "/api/enable-banking/link", json={"aspsp": "Bank A", "country": "DE", "redirect": url}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "redirect_not_registered",
        "message": "add it",
        "redirect_url": url,
    }


def test_complete_persists_session_and_returns_public(client):
    client.put("/api/enable-banking/config", json={"application_id": "app-1", "private_key": _PEM})
    r = client.post("/api/enable-banking/complete", json={"code": "code-xyz"})
    assert r.status_code == 200
    body = r.json()
    # Returns the PUBLIC status — the session id is never echoed, only has_session.
    assert body["has_session"] is True
    assert "session_id" not in body


def test_institutions_bad_country_returns_422(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.services.aggregation import enable_banking as svc

    def _raise(country):
        raise ValidationError("country must be a 2-letter ISO code")

    monkeypatch.setattr(svc, "list_institutions", _raise)
    r = client.get("/api/enable-banking/institutions", params={"country": "DEU"})
    assert r.status_code == 422


# ── account-linking front-door ───────────────────────────────────────────────────────


def test_front_door_routes_us_to_plaid_simplefin(client):
    r = client.get("/api/account-linking/institutions", params={"country": "US"})
    assert r.status_code == 200
    body = r.json()
    provider_ids = [p["provider"] for p in body["providers"]]
    assert provider_ids == ["plaid", "simplefin"]
    assert body["manual_import"] is True


def test_front_door_routes_eu_to_enable_banking(client):
    # Configure Enable Banking creds so its per-bank list is federated into the search.
    client.put("/api/enable-banking/config", json={"application_id": "app-1", "private_key": _PEM})
    r = client.get("/api/account-linking/institutions", params={"country": "DE"})
    assert r.status_code == 200
    body = r.json()
    provider_ids = [p["provider"] for p in body["providers"]]
    assert provider_ids == ["enable_banking"]
    # Enable Banking is configured → its per-bank rows (mocked list_institutions) federate in.
    assert body["institutions"][0]["provider"] == "enable_banking"


def test_front_door_brazil_uncovered_since_pluggy_removal(client):
    """Brazil (and any other genuinely unmapped country) gets an HONEST empty provider
    list — every remaining provider is regional (US/CA, EU/UK), so there is no default
    to guess. manual_import stays True so the front-door never dead-ends."""
    r = client.get("/api/account-linking/institutions", params={"country": "BR"})
    assert r.status_code == 200
    body = r.json()
    assert body["providers"] == []
    assert body["institutions"] == []
    assert body["manual_import"] is True


def test_front_door_provider_map(client):
    r = client.get("/api/account-linking/providers", params={"country": "US"})
    assert r.status_code == 200
    body = r.json()
    assert "US" in body["map"]
    assert body["providers"][0]["provider"] == "plaid"


def test_remove_bank_route_returns_the_new_status(client, monkeypatch):
    from backend.services.aggregation import enable_banking as svc

    monkeypatch.setattr(
        svc, "remove_bank", lambda key: {"removed": True, "revoked": True, "banks_left": 0}
    )
    r = client.post("/api/enable-banking/banks/abc123/remove")
    assert r.status_code == 200
    assert r.json()["removed"] is True and "banks" in r.json()["status"]


def test_remove_unknown_bank_is_422(client):
    client.put("/api/enable-banking/config", json={"application_id": "app-1", "private_key": _PEM})
    r = client.post("/api/enable-banking/banks/nope/remove")
    assert r.status_code == 422


def test_switching_to_an_app_enable_banking_refuses_is_422_and_saves_nothing(client, monkeypatch):
    from backend.core.errors import ValidationError
    from backend.db.enable_banking_config_store import EnableBankingConfigStore
    from backend.services.aggregation import enable_banking_app

    store = EnableBankingConfigStore()
    store.update({"application_id": "OLD-APP", "private_key": _PEM})
    store.add_bank(
        "SESS-1", aspsp="ACME Bank", country="FI", valid_until=None, linked_at="x", account_ids=[]
    )

    def _refuse(application_id, private_key):
        raise ValidationError("Enable Banking refused the new app: HTTP 403. Nothing was changed.")

    monkeypatch.setattr(enable_banking_app, "_check_application", _refuse)
    r = client.put(
        "/api/enable-banking/config", json={"application_id": "TYPO", "private_key": _PEM}
    )
    assert r.status_code == 422
    assert "Nothing was changed" in r.json()["detail"]
    assert EnableBankingConfigStore().application_id() == "OLD-APP"
    assert client.get("/api/enable-banking/status").json()["banks"][0]["access_ended"] is None
