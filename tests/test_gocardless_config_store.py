"""GoCardlessConfigStore — where each field lives, write-only secrets, the "linked" rule and
the public view (RFC connectors-table). No network."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_store(db_isolated):
    pass


def _store():
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    return GoCardlessConfigStore()


def test_defaults_when_no_row():
    cfg = _store().get()
    assert cfg["enabled"] is False
    assert (cfg["secret_id"], cfg["secret_key"], cfg["requisition_id"]) == ("", "", "")
    assert cfg["accounts_meta"] == {}
    assert cfg["last_status"] == "never_run"


def test_each_field_lands_in_its_own_column():
    from backend.db.connector_store import ConnectorStore

    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY", "enabled": True})
    store.start_link("ACME_BANK_GB", "GB", "REQ-1", "2027-01-01T00:00:00+00:00")
    row = ConnectorStore().get("gocardless")
    assert row["config"] == {"secret_id": "SID", "institution_id": "ACME_BANK_GB", "country": "GB"}
    assert row["state"]["requisition_status"] == "CR"
    assert ConnectorStore().secrets("gocardless") == {
        "secret_key": "SKEY",
        "requisition_id": "REQ-1",
    }


def test_a_blank_secret_key_keeps_the_saved_one():
    store = _store()
    store.update({"secret_key": "SKEY"})
    store.update({"secret_key": "   "})
    assert store.secret_key() == "SKEY"


def test_configured_needs_credentials_and_a_requisition_linked_needs_ln():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    assert store.has_credentials() and not store.is_configured()
    store.start_link("ACME_BANK_GB", "GB", "REQ-1", None)
    assert store.is_configured() and not store.has_link()
    store.record_requisition_status("LN")
    assert store.has_link()
    store.record_requisition_status("EX")
    assert store.is_configured() and not store.has_link()


def test_the_token_is_saved_and_cleared_together_with_its_expiry():
    store = _store()
    store.save_token("TOK", "2027-01-01T00:00:00+00:00")
    assert (store.get()["access_token"], store.get()["access_expires_at"]) == (
        "TOK",
        "2027-01-01T00:00:00+00:00",
    )
    store.clear_token()
    assert (store.get()["access_token"], store.get()["access_expires_at"]) == ("", None)


def test_account_meta_is_merged_per_account_and_reset_by_a_new_link():
    store = _store()
    store.cache_account_meta("A1", {"name": "one"})
    store.cache_account_meta("A2", {"name": "two"})
    assert set(store.accounts_meta()) == {"A1", "A2"}
    store.start_link("ACME_BANK_GB", "GB", "REQ-2", None)
    assert store.accounts_meta() == {}


def test_public_never_returns_a_secret():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    store.save_token("TOK", None)
    store.start_link("ACME_BANK_GB", "GB", "REQ-1", None)
    public = store.public()
    flat = repr(public)
    for secret in ("SKEY", "TOK", "REQ-1"):
        assert secret not in flat
    assert public["has_credentials"] is True
    assert public["has_requisition"] is True
    assert public["secret_id"] == "SID"


def test_record_status_sets_the_last_fetched_fields():
    store = _store()
    store.record_status("rate_limited", error="later")
    cfg = store.get()
    assert (cfg["last_status"], cfg["last_error"]) == ("rate_limited", "later")
    assert cfg["last_synced_at"]


def test_reset_erases_the_credentials():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    store.reset()
    assert store.get()["secret_key"] == ""
