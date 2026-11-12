"""The Accounts screen's "Link to a bank account…" list (``services/connector_linking.py``):
every connected connector's accounts and what each is linked to — the same read the
Connectors screen makes per connector, so the two can never disagree."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.services import connector_linking
from backend.services import ledger as ledger_service
from backend.services.aggregation import simplefin as simplefin_svc
from backend.services.aggregation.providers import FetchWindow


@pytest.fixture(autouse=True)
def isolate(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")


def _connect_simplefin(monkeypatch, *, fails: bool = False):
    from backend.core.errors import ExternalServiceError
    from backend.db.simplefin_config_store import SimpleFinConfigStore

    SimpleFinConfigStore().update(
        {"access_url": "https://user:pass@bridge.example.org/simplefin", "enabled": True}
    )

    def window(*a, **k):
        if fails:
            raise ExternalServiceError("SimpleFIN HTTP 503")
        return FetchWindow(
            accounts=[
                {
                    "provider_account_id": "sf-1",
                    "name": "Checking",
                    "mask": "1234",
                    "currency": "USD",
                    "balance": 10.0,
                },
                {
                    "provider_account_id": "sf-2",
                    "name": "Savings",
                    "mask": "5678",
                    "currency": "USD",
                    "balance": 20.0,
                },
            ]
        )

    monkeypatch.setattr(simplefin_svc, "fetch_window", window)


def _account_linked_to(ref: str) -> str:
    from backend.services import accounts as accounts_service

    return accounts_service.create_account(
        {
            "name": "Everyday",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 0,
            "external_ids": [{"source": "simplefin", "ref": ref}],
        }
    )["id"]


def test_nothing_connected_lists_nothing():
    assert connector_linking.linkable_accounts() == []


def test_a_connected_connector_lists_its_accounts_and_their_links(monkeypatch):
    _connect_simplefin(monkeypatch)
    linked_to = _account_linked_to("sf-1")
    groups = connector_linking.linkable_accounts()
    assert [g["provider"] for g in groups] == ["simplefin"]
    by_id = {a["provider_account_id"]: a for a in groups[0]["accounts"]}
    assert by_id["sf-1"]["linked_account_id"] == linked_to
    assert by_id["sf-2"]["linked_account_id"] is None


def test_a_failing_provider_still_appears_with_its_status(monkeypatch):
    _connect_simplefin(monkeypatch, fails=True)
    _account_linked_to("sf-1")
    [group] = connector_linking.linkable_accounts()
    assert group["status"] != "ok"
    assert [a["provider_account_id"] for a in group["accounts"]] == ["sf-1"]


def test_the_route_returns_the_same_list(monkeypatch):
    from backend.main import app

    _connect_simplefin(monkeypatch)
    body = TestClient(app).get("/api/linkable-bank-accounts").json()
    assert [g["provider"] for g in body["connectors"]] == ["simplefin"]
