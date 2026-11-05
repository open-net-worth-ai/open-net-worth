"""The Actual Budget connector: its flag, config store, catalog entry and REST surface.

The server address is a credential (it can carry userinfo), so all four of server address,
password, token and encryption password live in ``connector_secrets``; the store's ``public()``
reports only that they are set. Every number and name here is made up.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.actual_budget_api import router
from backend.api.demo_lockdown import is_demo_locked
from backend.core import feature_flags
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.services import actual_flag
from backend.services.actual_sync.client import plain_http_notice
from backend.services.connector_catalog import CATALOG
from tests.flags import flags

SECRETS = {
    "server_url": "https://alice:hunter2@actual.acme.test:5006/sync",
    "password": "PASSWORD-SENTINEL",
    "token": "TOKEN-SENTINEL",
    "encryption_password": "ENCRYPTION-SENTINEL",
}


@pytest.fixture
def store(db_isolated):
    return ActualBudgetConfigStore()


@pytest.fixture
def client(db_isolated):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def behind_gateway(monkeypatch):
    monkeypatch.setattr("backend.core.config.gateway_active", lambda: True)


def _no_secret_in(payload: object) -> None:
    text = repr(payload)
    for value in SECRETS.values():
        assert value not in text
    assert "hunter2" not in text and "alice" not in text


# ── the flag ─────────────────────────────────────────────────────────────────


def test_the_flag_ships_on_and_dated():
    flag = feature_flags.get("actual_budget_sync")
    assert flag.default is True and flag.kind == "release"
    assert flag.added == date(2026, 11, 5) and flag.remove_by == date(2027, 3, 1)
    assert actual_flag.ACTUAL_FLAG == "actual_budget_sync"


def test_gated_refuses_while_the_flag_is_off():
    calls = []
    guarded = actual_flag.gated(lambda: calls.append(1) or "ran")
    assert guarded() == "ran"
    with flags(actual_budget_sync=False):
        assert actual_flag.on() is False
        with pytest.raises(NotFoundError):
            guarded()
    assert calls == [1]


# ── the store ────────────────────────────────────────────────────────────────


def test_defaults(store):
    assert store.KIND == "actual_budget"
    assert store.is_configured() is False
    assert store.is_active() is False
    assert store.budget_id() is None
    assert store.credentials() == {
        "server_url": "",
        "password": "",
        "token": "",
        "encryption_password": "",
    }


def test_public_reports_presence_and_a_bare_host_only(store):
    store.update({"enabled": True, **SECRETS})
    pub = store.public()
    assert pub["has_password"] is pub["has_token"] is True
    assert pub["has_encryption_password"] is pub["has_server_url"] is True
    assert pub["server_host"] == "actual.acme.test:5006"
    assert pub["available"] is True and pub["enabled"] is True
    for secret in ("server_url", "password", "token", "encryption_password"):
        assert secret not in pub
    _no_secret_in(pub)


def test_credentials_are_for_server_side_use(store):
    store.update(SECRETS)
    assert store.credentials() == SECRETS


def test_a_blank_secret_leaves_the_saved_one(store):
    store.update(SECRETS)
    store.update({"password": "", "token": "   ", "encryption_password": "", "server_url": ""})
    assert store.credentials() == SECRETS


def test_configured_needs_an_address_and_one_way_to_sign_in(store):
    store.update({"server_url": "http://192.168.1.5:5006"})
    assert store.is_configured() is False
    store.update({"password": "pw"})
    assert store.is_configured() is True
    store.reset()
    store.update({"server_url": "http://192.168.1.5:5006", "token": "tok"})
    assert store.is_configured() is True
    store.reset()
    store.update({"password": "pw", "token": "tok"})
    assert store.is_configured() is False


def test_the_budget_choice_is_a_setting_not_a_secret(store):
    store.update({"budget_id": "sync-id-1", "budget_name": "ACME Home", "budget_currency": "USD"})
    assert store.budget_id() == "sync-id-1"
    pub = store.public()
    assert pub["budget_id"] == "sync-id-1" and pub["budget_name"] == "ACME Home"
    store.update({"budget_id": ""})
    assert store.budget_id() is None and store.public()["budget_name"] is None


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("ACTUAL_SERVER_URL", "https://env.acme.test")
    monkeypatch.setenv("ACTUAL_PASSWORD", "env-pw")
    assert ActualBudgetConfigStore().credentials()["server_url"] == ""


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://actual.acme.test",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "actual.acme.test:5006",
        "http://",
        "https:///path-only",
        "//actual.acme.test",
    ],
)
def test_a_server_url_must_be_http_or_https_with_a_host(store, bad):
    with pytest.raises(ValidationError):
        store.update({"server_url": bad})
    assert store.credentials()["server_url"] == ""


def test_a_private_address_is_normal(store):
    for url in ("http://localhost:5006", "http://192.168.1.5:5006", "https://actual.lan"):
        store.update({"server_url": url})
        assert store.credentials()["server_url"] == url


def test_behind_the_gateway_it_is_unavailable_and_refuses_config(store, behind_gateway):
    assert store.available() is False
    assert store.public()["available"] is False
    with pytest.raises(ConflictError):
        store.update({"server_url": "http://192.168.1.5:5006"})
    assert store.credentials()["server_url"] == ""


# ── the catalog kind ─────────────────────────────────────────────────────────


def test_the_catalog_kind_keeps_all_four_in_connector_secrets():
    kind = CATALOG["actual_budget"]
    assert {f.name for f in kind.secret_fields} == set(SECRETS)
    assert all(f.revealable for f in kind.secret_fields)
    assert kind.config_keys == ("budget_id",)
    assert kind.moved_state == ("budget_name", "budget_currency")
    assert kind.label == "Actual Budget" == ActualBudgetConfigStore.LABEL
    from backend.services.connector_copy import TOOLS

    assert TOOLS.get("actual_budget", []) == []


def test_catalog_configured_rule_matches_the_store(store):
    from backend.services import connectors

    def view():
        return next(c for c in connectors.list_connectors() if c.id == "actual_budget")

    assert view().status == "not_configured"
    store.update(
        {"server_url": "http://192.168.1.5:5006", "token": SECRETS["token"], "enabled": True}
    )
    assert view().status == "active"
    _no_secret_in(view().model_dump())


def test_flag_off_hides_the_connector_from_the_hub(store):
    from backend.services import connectors

    with flags(actual_budget_sync=False):
        assert "actual_budget" not in {c.id for c in connectors.list_connectors()}
    assert "actual_budget" in {c.id for c in connectors.list_connectors()}


def test_transactions_from_actual_are_a_feed_with_authoritative_ids():
    from backend.core.txn_sources import canonical_writer, is_authoritative_source

    assert canonical_writer("actual_budget") == "feed"
    assert is_authoritative_source("actual_budget") is True


# ── the REST surface ─────────────────────────────────────────────────────────


def test_status_never_carries_a_secret(client, store):
    r = client.get("/api/actual-budget/status")
    assert r.status_code == 200
    assert r.json()["has_password"] is False and r.json()["available"] is True
    store.update(SECRETS)
    body = client.get("/api/actual-budget/status").json()
    assert body["has_password"] is True and body["server_host"] == "actual.acme.test:5006"
    _no_secret_in(body)


def test_put_config_saves_enables_and_echoes_no_secret(client, store):
    r = client.put("/api/actual-budget/config", json=SECRETS)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["configured"] is True
    _no_secret_in(body)
    assert store.credentials() == SECRETS

    r = client.put("/api/actual-budget/config", json={"password": "", "enabled": False})
    assert r.json()["enabled"] is False and r.json()["has_password"] is True
    assert store.credentials() == SECRETS


def test_put_config_rejects_a_bad_address(client, store):
    r = client.put("/api/actual-budget/config", json={"server_url": "ftp://actual.acme.test"})
    assert r.status_code == 422
    assert store.credentials()["server_url"] == ""


def test_budgets_runs_the_seam_off_the_event_loop(client, store, monkeypatch):
    from backend.services import actual_sync

    monkeypatch.setattr(
        actual_sync, "list_budgets", lambda: [{"id": "b1", "name": "ACME Home", "currency": "USD"}]
    )
    r = client.get("/api/actual-budget/budgets")
    assert r.status_code == 200
    assert r.json() == {"budgets": [{"id": "b1", "name": "ACME Home", "currency": "USD"}]}


def test_budgets_without_a_saved_sign_in_is_a_plain_422(client):
    r = client.get("/api/actual-budget/budgets")
    assert r.status_code == 422 and "password or token" in r.json()["detail"]


BUDGETS = [
    {"id": "f1", "name": "ACME Home", "currency": None, "encrypted": False},
    {"id": "f2", "name": "ACME Vault", "currency": None, "encrypted": True},
]


@pytest.fixture
def server(monkeypatch):
    """The Actual client seam, faked: a fixed budget list and a currency read from the copy."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from backend.services.actual_sync import client as actual_client

    seen = {"signed_in_with": []}

    def list_budgets(store=None):
        seen["signed_in_with"].append(ActualBudgetConfigStore().credentials()["password"])
        return list(BUDGETS)

    @contextmanager
    def open_copy(store, budget_id):
        if seen.get("open_error"):
            raise seen["open_error"]
        yield SimpleNamespace(currency="EUR")

    monkeypatch.setattr(actual_client, "list_budgets", list_budgets)
    monkeypatch.setattr(actual_client, "open_copy", open_copy)
    return seen


def test_put_config_picks_a_budget_validated_live_and_caches_its_name_and_currency(
    client, store, server
):
    r = client.put("/api/actual-budget/config", json={**SECRETS, "budget_id": " f2 "})
    assert r.status_code == 200
    body = r.json()
    assert (body["budget_id"], body["budget_name"], body["budget_currency"]) == (
        "f2",
        "ACME Vault",
        "EUR",
    )
    assert server["signed_in_with"] == [SECRETS["password"]]
    _no_secret_in(body)
    assert store.budget_id() == "f2"


def test_put_config_refuses_a_budget_the_sign_in_cannot_see(client, store, server):
    store.update({"budget_id": "f1", "budget_name": "ACME Home"})
    r = client.put("/api/actual-budget/config", json={**SECRETS, "budget_id": "nope"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "ACME Home" in detail and "f2" in detail
    assert store.budget_id() == "f1"
    assert store.get()["budget_name"] == "ACME Home"
    _no_secret_in(detail)


def test_put_config_cannot_read_the_currency_still_picks(client, store, server):
    from backend.core.errors import ExternalServiceError

    server["open_error"] = ExternalServiceError("This budget needs refreshing in Actual first.")
    r = client.put("/api/actual-budget/config", json={**SECRETS, "budget_id": "f1"})
    assert r.status_code == 200
    assert r.json()["budget_id"] == "f1" and r.json()["budget_currency"] is None


def test_put_config_blank_budget_clears_the_pick_and_omitted_keeps_it(client, store, server):
    client.put("/api/actual-budget/config", json={**SECRETS, "budget_id": "f1"})
    assert (
        client.put("/api/actual-budget/config", json={"enabled": True}).json()["budget_id"] == "f1"
    )
    cleared = client.put("/api/actual-budget/config", json={"budget_id": ""}).json()
    assert cleared["budget_id"] is None and cleared["budget_name"] is None
    assert cleared["budget_currency"] is None


def test_put_config_saves_the_sign_in_before_checking_the_budget(client, store, server):
    r = client.put("/api/actual-budget/config", json={**SECRETS, "budget_id": "f1"})
    assert r.status_code == 200
    assert server["signed_in_with"] == ["PASSWORD-SENTINEL"]


def test_put_config_budget_pick_is_refused_behind_the_gateway(client, store, behind_gateway):
    r = client.put("/api/actual-budget/config", json={"budget_id": "f1"})
    assert r.status_code == 409


def test_the_seam_itself_checks_the_flag_and_the_gateway(store, behind_gateway):
    from backend.services import actual_sync

    with pytest.raises(ConflictError):
        actual_sync.list_budgets()
    with pytest.raises(ConflictError):
        with actual_sync.open_copy(None, "f1"):
            pass


ROUTES = [
    ("GET", "/api/actual-budget/status", None),
    ("PUT", "/api/actual-budget/config", {"enabled": True}),
    ("GET", "/api/actual-budget/budgets", None),
]


def test_flag_off_every_route_is_not_found(client):
    with flags(actual_budget_sync=False):
        for method, path, body in ROUTES:
            assert client.request(method, path, json=body).status_code == 404, path


def test_behind_the_gateway_status_says_unavailable_and_the_rest_refuse(
    client, store, behind_gateway
):
    r = client.get("/api/actual-budget/status")
    assert r.status_code == 200 and r.json()["available"] is False
    r = client.put("/api/actual-budget/config", json=SECRETS)
    assert r.status_code == 409
    assert store.credentials()["server_url"] == ""
    assert client.get("/api/actual-budget/budgets").status_code == 409


def test_the_public_demo_locks_the_writes():
    assert is_demo_locked("PUT", "/api/actual-budget/config")
    assert is_demo_locked("POST", "/api/actual-budget/anything")


@pytest.mark.parametrize("route", ["status", "budgets", "migration", "sync"])
def test_the_public_demo_locks_the_reads_too(route):
    """``budgets`` signs in with whatever is saved and answers with the budgets' names."""
    assert is_demo_locked("GET", f"/api/actual-budget/{route}")


# ── switching how you sign in ────────────────────────────────────────────────


def test_a_new_token_replaces_a_saved_password(client, store):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "password": "PASSWORD-SENTINEL"},
    )
    r = client.put("/api/actual-budget/config", json={"token": "TOKEN-SENTINEL"})
    assert r.status_code == 200
    assert r.json()["has_token"] is True and r.json()["has_password"] is False
    assert store.credentials()["password"] == ""
    assert store.credentials()["token"] == "TOKEN-SENTINEL"


def test_a_new_password_replaces_a_saved_token(client, store):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "token": "TOKEN-SENTINEL"},
    )
    r = client.put("/api/actual-budget/config", json={"password": "PASSWORD-SENTINEL"})
    assert r.json()["has_password"] is True and r.json()["has_token"] is False
    assert store.credentials()["token"] == ""


def test_a_blank_sign_in_replaces_nothing(client, store):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "password": "PASSWORD-SENTINEL"},
    )
    client.put("/api/actual-budget/config", json={"token": "   ", "password": ""})
    assert store.credentials()["password"] == "PASSWORD-SENTINEL"
    assert store.is_configured() is True


def test_an_encryption_change_leaves_the_sign_in(client, store):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "token": "TOKEN-SENTINEL"},
    )
    client.put("/api/actual-budget/config", json={"encryption_password": "ENC"})
    assert store.credentials()["token"] == "TOKEN-SENTINEL"


@pytest.mark.parametrize(
    "new",
    [
        "https://other.acme.test",
        "https://actual.acme.test:8443",
        "http://actual.acme.test",
    ],
)
@pytest.mark.parametrize("saved", [{"token": "TOKEN-SENTINEL"}, {"password": "PASSWORD-SENTINEL"}])
def test_a_saved_sign_in_doesnt_follow_a_new_address(client, store, saved, new):
    client.put(
        "/api/actual-budget/config", json={"server_url": "https://actual.acme.test", **saved}
    )
    r = client.put("/api/actual-budget/config", json={"server_url": new})
    assert r.status_code == 200
    assert r.json()["has_password"] is r.json()["has_token"] is False
    assert r.json()["configured"] is False
    assert store.credentials()["server_url"] == new
    assert store.credentials()["password"] == store.credentials()["token"] == ""


def test_a_new_address_keeps_the_encryption_password(client, store):
    client.put(
        "/api/actual-budget/config",
        json={
            "server_url": "https://actual.acme.test",
            "password": "PASSWORD-SENTINEL",
            "encryption_password": "ENC",
        },
    )
    client.put("/api/actual-budget/config", json={"server_url": "https://other.acme.test"})
    assert store.credentials()["encryption_password"] == "ENC"


def test_a_new_address_can_bring_its_own_sign_in(client, store):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "password": "PASSWORD-SENTINEL"},
    )
    r = client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://other.acme.test", "password": "ANOTHER-SENTINEL"},
    )
    assert r.json()["configured"] is True
    assert store.credentials()["password"] == "ANOTHER-SENTINEL"


@pytest.mark.parametrize(
    "same",
    [
        "https://actual.acme.test",
        "https://actual.acme.test/",
        "https://actual.acme.test:443",
        "https://ACTUAL.acme.test",
        "https://actual.acme.test/sync",
        "https://user@actual.acme.test",
    ],
)
def test_the_same_server_written_another_way_keeps_the_sign_in(client, store, same):
    client.put(
        "/api/actual-budget/config",
        json={"server_url": "https://actual.acme.test", "password": "PASSWORD-SENTINEL"},
    )
    client.put("/api/actual-budget/config", json={"server_url": same})
    assert store.credentials()["password"] == "PASSWORD-SENTINEL"


def test_the_first_address_clears_nothing(store):
    store.update({"password": "PASSWORD-SENTINEL"})
    store.update({"server_url": "https://actual.acme.test"})
    assert store.credentials()["password"] == "PASSWORD-SENTINEL"


def test_public_says_where_a_sign_in_would_go_without_the_userinfo(store):
    store.update(SECRETS)
    assert store.public()["server_origin"] == "https://actual.acme.test:5006"
    store.update({"server_url": "https://[::1]:443/x"})
    assert store.public()["server_origin"] == "https://[::1]"
    _no_secret_in(store.public()["server_origin"])


# ── a saved address that isn't encrypted ─────────────────────────────────────


@pytest.mark.parametrize(
    "url, plain",
    [
        ("http://192.168.1.20:5006", True),
        ("http://actual.acme.test", True),
        ("https://actual.acme.test", False),
        ("http://localhost:5006", False),
        ("http://actual.localhost:5006", False),
        ("http://127.0.0.1:5006", False),
        ("http://127.8.8.8:5006", False),
        ("http://[::1]:5006", False),
    ],
)
def test_public_says_when_a_saved_address_is_plain_http(store, url, plain):
    store.update({"server_url": url})
    assert store.public()["server_plain_http"] is plain
    assert bool(plain_http_notice(url)) is plain


def test_public_says_no_plain_http_with_nothing_saved(store):
    assert store.public()["server_plain_http"] is False
