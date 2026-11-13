"""Proxy headers for an Actual server behind a sign-in proxy: how they are checked and saved, that
they never leave their address, that every request carries them, and the plain error a proxy's
answer turns into. Every name and value is made up."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.actual_budget_api import router
from backend.core.errors import ExternalServiceError, ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore, parse_proxy_headers
from backend.services.actual_sync import client

pytest.importorskip("actual")
pytestmark = pytest.mark.flags(actual_budget_sync=True)

URL = "https://actual.acme.test"
VALUE = "HEADER-SENTINEL-7"
HEADERS = f"X-Acme-Proxy-Token: {VALUE}\n\nX-Acme-Client:  onw "
FILES = {
    "status": "ok",
    "data": [
        {"deleted": 0, "fileId": "f1", "groupId": "g1", "name": "ACME Home", "encryptKeyId": None}
    ],
}


@pytest.fixture
def store(db_isolated):
    return ActualBudgetConfigStore()


@pytest.fixture
def api(db_isolated):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── checking ─────────────────────────────────────────────────────────────────


def test_lines_become_a_header_map():
    assert parse_proxy_headers(HEADERS) == {"X-Acme-Proxy-Token": VALUE, "X-Acme-Client": "onw"}
    assert parse_proxy_headers("Authorization: Basic YWNtZTpwdw==") == {
        "Authorization": "Basic YWNtZTpwdw=="
    }


@pytest.mark.parametrize(
    "text,words",
    [
        (f"X-Acme-Proxy-Token {VALUE}", "line 1"),
        (f"X Acme: {VALUE}", "line 1"),
        ("X-Acme:", "needs a value"),
        (f"X-Acme: {VALUE}\x00", "needs a value"),
        (f"X-Acme: {VALUE}é", "needs a value"),
        (f"Host: {VALUE}", "set by the app"),
        (f"x-actual-token: {VALUE}", "set by the app"),
        (f"X-Acme: {VALUE}\nx-acme: {VALUE}", "listed twice"),
        ("\n".join(f"X-Acme-{n}: {VALUE}" for n in range(11)), "At most 10"),
        (f"X-Acme: {'v' * 5000}", "too long"),
    ],
)
def test_a_bad_header_is_refused_without_its_value(text, words):
    with pytest.raises(ValidationError) as err:
        parse_proxy_headers(text)
    assert words in err.value.message
    assert VALUE not in err.value.message and "vvvv" not in err.value.message


# ── saving ───────────────────────────────────────────────────────────────────


def test_saved_tidily_and_shown_by_name_only(api, store):
    r = api.put(
        "/api/actual-budget/config",
        json={"server_url": URL, "password": "pw", "proxy_headers": HEADERS},
    )
    assert r.status_code == 200
    assert r.json()["has_proxy_headers"] is True
    assert r.json()["proxy_header_names"] == ["X-Acme-Proxy-Token", "X-Acme-Client"]
    assert VALUE not in r.text
    assert (
        store.credentials()["proxy_headers"] == f"X-Acme-Proxy-Token: {VALUE}\nX-Acme-Client: onw"
    )


def test_a_bad_header_saves_nothing(api, store):
    r = api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": "Host: x"})
    assert r.status_code == 422
    assert store.credentials()["server_url"] == ""


def test_a_blank_keeps_them_and_clear_removes_them(api, store):
    api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": HEADERS})
    api.put("/api/actual-budget/config", json={"proxy_headers": "  "})
    assert store.public()["has_proxy_headers"] is True
    r = api.put("/api/actual-budget/config", json={"clear_proxy_headers": True})
    assert r.json()["has_proxy_headers"] is False and r.json()["proxy_header_names"] == []
    assert store.credentials()["proxy_headers"] == ""
    assert store.credentials()["server_url"] == URL


def test_new_headers_beside_clear_win(api, store):
    api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": HEADERS})
    api.put(
        "/api/actual-budget/config",
        json={"clear_proxy_headers": True, "proxy_headers": "X-Acme-New: v2"},
    )
    assert store.credentials()["proxy_headers"] == "X-Acme-New: v2"


@pytest.mark.parametrize("new", ["https://other.acme.test", "https://actual.acme.test:8443"])
def test_they_dont_follow_a_new_address(api, store, new):
    api.put(
        "/api/actual-budget/config",
        json={"server_url": URL, "password": "pw", "proxy_headers": HEADERS},
    )
    api.put("/api/actual-budget/config", json={"server_url": new, "password": "pw2"})
    assert store.credentials()["proxy_headers"] == ""


def test_they_stay_with_the_same_server(api, store):
    api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": HEADERS})
    api.put("/api/actual-budget/config", json={"server_url": f"{URL}/"})
    assert store.public()["has_proxy_headers"] is True


def test_a_new_address_can_bring_its_own(api, store):
    api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": HEADERS})
    api.put(
        "/api/actual-budget/config",
        json={"server_url": "https://other.acme.test", "proxy_headers": "X-Acme-Other: v3"},
    )
    assert store.credentials()["proxy_headers"] == "X-Acme-Other: v3"


# ── sending ──────────────────────────────────────────────────────────────────


def _serve(monkeypatch, answer):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request.url.path)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return seen


def _actual(path: str) -> httpx.Response:
    if path.endswith("/account/login"):
        return httpx.Response(200, json={"status": "ok", "data": {"token": "tok"}})
    if path.endswith("/account/validate"):
        return httpx.Response(200, json={"status": "ok", "data": {"validated": True}})
    if path.endswith("/sync/list-user-files"):
        return httpx.Response(200, json=FILES)
    return httpx.Response(404)


def _configured(store, headers: str | None = HEADERS):
    store.update({"enabled": True, "server_url": URL, "password": "pw"})
    if headers:
        store.update({"proxy_headers": headers})


def test_every_request_carries_them(store, monkeypatch):
    _configured(store)
    seen = _serve(monkeypatch, _actual)
    assert [b["name"] for b in client.list_budgets(store)] == ["ACME Home"]
    paths = [r.url.path for r in seen]
    assert any(p.endswith("/account/login") for p in paths)
    assert any(p.endswith("/sync/list-user-files") for p in paths)
    for request in seen:
        assert request.headers["X-Acme-Proxy-Token"] == VALUE
        assert request.headers["X-Acme-Client"] == "onw"
    assert seen[-1].headers["X-ACTUAL-TOKEN"] == "tok"


def test_none_are_sent_when_none_are_saved(store, monkeypatch):
    _configured(store, headers=None)
    seen = _serve(monkeypatch, _actual)
    client.list_budgets(store)
    assert all("X-Acme-Proxy-Token" not in r.headers for r in seen)


def _redirect_to_login(path: str) -> httpx.Response:
    return httpx.Response(302, headers={"Location": "https://auth.acme.test/login"})


def _login_page(path: str) -> httpx.Response:
    if path.endswith("/account/login") or path.endswith("/account/validate"):
        return _actual(path)
    return httpx.Response(200, text="<html><body>Sign in to ACME</body></html>")


@pytest.mark.parametrize("answer", [_redirect_to_login, _login_page])
def test_a_proxy_answer_says_what_to_do(store, monkeypatch, answer):
    _configured(store, headers=None)
    seen = _serve(monkeypatch, answer)
    with pytest.raises(ExternalServiceError) as err:
        client.list_budgets(store)
    assert "sign-in proxy" in err.value.message and "/sync" in err.value.message
    assert "acme.test" not in err.value.message
    assert all(r.url.host == "actual.acme.test" for r in seen), "a redirect is never followed"
    assert store.public()["last_status"] != "auth_error"


def test_a_refused_replacement_keeps_the_saved_ones(api, store):
    api.put("/api/actual-budget/config", json={"server_url": URL, "proxy_headers": HEADERS})
    r = api.put(
        "/api/actual-budget/config",
        json={"clear_proxy_headers": True, "proxy_headers": "Host: acme"},
    )
    assert r.status_code == 422
    assert VALUE in store.credentials()["proxy_headers"]
