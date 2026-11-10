"""An account's provider link carries its connector's state, so a removed or paused connector
never still reads as live on the account (header marker, sidebar icon).

``connected`` — the connector exists and is switched on; ``paused`` — it exists, switched off;
``disconnected`` — there is no connector any more (removed, or never set up)."""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service

LINK = {"source": "plaid", "ref": "p1", "name": "Gold Checking", "mask": "0000"}


@pytest.fixture(autouse=True)
def isolate(db_isolated):
    """Fresh, isolated DB per test."""


def _status() -> str:
    [entry] = ledger_service._hydrated_external_ids([LINK])
    return entry["status"]


def test_an_enabled_connector_reads_connected():
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "c", "secret": "s", "enabled": True})
    assert _status() == "connected"


def test_a_switched_off_connector_reads_paused():
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"client_id": "c", "secret": "s", "enabled": False})
    assert _status() == "paused"


def test_a_removed_connector_reads_disconnected():
    from backend.db.plaid_config_store import PlaidConfigStore

    store = PlaidConfigStore()
    store.update({"client_id": "c", "secret": "s", "enabled": True})
    store.reset()  # what Remove does
    assert _status() == "disconnected"


def test_the_label_still_comes_with_it():
    [entry] = ledger_service._hydrated_external_ids([LINK])
    assert entry["provider_label"] == "Plaid"
    assert entry["ref"] == "p1"


def test_an_account_with_no_links_never_reads_the_connector_table(monkeypatch):
    from backend.services import accounts as accounts_service

    def _boom():
        raise AssertionError("no links → no connector read")

    monkeypatch.setattr(accounts_service, "link_statuses", _boom)
    assert ledger_service._hydrated_external_ids([]) == []
