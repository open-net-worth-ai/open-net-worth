"""GoCardlessConfigStore — where each field lives, write-only secrets, the list of banks, the
"linked" rule and the public view (RFC connectors-table). No network."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_store(db_isolated):
    pass


def _store():
    from backend.db.gocardless_config_store import GoCardlessConfigStore

    return GoCardlessConfigStore()


def _add(store, rid="REQ-1", inst="ACME_BANK_GB", **kw):
    return store.add_bank(
        rid,
        institution_id=inst,
        institution_name=kw.pop("name", "ACME Bank"),
        country="GB",
        consent_expires_at=kw.pop("expires", None),
        **kw,
    )


def test_defaults_when_no_row():
    cfg = _store().get()
    assert cfg["enabled"] is False
    assert (cfg["secret_id"], cfg["secret_key"], cfg["requisitions"]) == ("", "", [])
    assert cfg["accounts_meta"] == {} and cfg["banks"] == {}
    assert cfg["last_status"] == "never_run"
    assert _store().banks() == []


def test_each_field_lands_in_its_own_column():
    from backend.db.connector_store import ConnectorStore
    from backend.db.gocardless_config_store import bank_key

    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY", "enabled": True})
    key = _add(store, expires="2027-01-01T00:00:00+00:00")
    row = ConnectorStore().get("gocardless")
    assert row["config"] == {"secret_id": "SID"}
    assert key == bank_key("REQ-1")
    assert row["state"]["banks"][key]["status"] == "CR"
    assert row["state"]["banks"][key]["institution_name"] == "ACME Bank"
    assert "REQ-1" not in repr(row["state"])
    assert ConnectorStore().secrets("gocardless") == {
        "secret_key": "SKEY",
        "requisitions": [{"requisition_id": "REQ-1", "replaces": None}],
    }


def test_the_bank_list_cannot_be_written_through_update():
    store = _store()
    store.update({"requisitions": [{"requisition_id": "X"}], "banks": {"k": {}}})
    assert store.banks() == []


def test_a_blank_secret_key_keeps_the_saved_one():
    store = _store()
    store.update({"secret_key": "SKEY"})
    store.update({"secret_key": "   "})
    assert store.secret_key() == "SKEY"


def test_configured_needs_credentials_and_a_bank_linked_needs_one_ln():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    assert store.has_credentials() and not store.is_configured()
    one = _add(store)
    two = _add(store, "REQ-2", "OTHER_BANK_GB", name="Other Bank")
    assert store.is_configured() and not store.has_link()
    store.update_bank(two, status="LN")
    assert store.has_link()
    store.update_bank(two, status="EX")
    store.update_bank(one, status="LN")
    assert store.has_link()
    assert [b["status"] for b in store.banks()] == ["LN", "EX"]


def test_banks_keep_link_order_and_updates_touch_only_their_bank():
    store = _store()
    one = _add(store)
    two = _add(store, "REQ-2", "OTHER_BANK_GB", name="Other Bank")
    store.update_bank(one, status="LN", account_ids=["A1", "A2"], linked_at="2026-11-01")
    banks = store.banks()
    assert [b["key"] for b in banks] == [one, two]
    assert banks[0]["account_ids"] == ["A1", "A2"] and banks[1]["account_ids"] == []
    assert banks[1]["status"] == "CR"


def test_dropping_a_bank_prunes_only_accounts_no_other_bank_lists():
    store = _store()
    one = _add(store)
    two = _add(store, "REQ-2", "OTHER_BANK_GB")
    store.update_bank(one, account_ids=["A1", "SHARED"])
    store.update_bank(two, account_ids=["B1", "SHARED"])
    for a in ("A1", "B1", "SHARED"):
        store.cache_account_meta(a, {"name": a})
    store.drop_bank(one)
    assert [b["key"] for b in store.banks()] == [two]
    assert set(store.accounts_meta()) == {"B1", "SHARED"}


def test_dropping_the_bank_a_renewal_replaces_clears_the_pointer():
    store = _store()
    old = _add(store)
    new = _add(store, "REQ-2", replaces="REQ-1")
    store.drop_bank(old)
    assert store.bank(new)["replaces"] is None


def test_dropping_the_last_bank_clears_the_secret():
    from backend.db.connector_store import ConnectorStore

    store = _store()
    store.update({"secret_key": "SKEY"})
    key = _add(store)
    store.drop_bank(key)
    assert ConnectorStore().secrets("gocardless") == {"secret_key": "SKEY"}
    assert store.get()["banks"] == {}


def test_the_token_is_saved_and_cleared_together_with_its_expiry():
    store = _store()
    store.save_token("TOK", "2027-01-01T00:00:00+00:00")
    assert (store.get()["access_token"], store.get()["access_expires_at"]) == (
        "TOK",
        "2027-01-01T00:00:00+00:00",
    )
    store.clear_token()
    assert (store.get()["access_token"], store.get()["access_expires_at"]) == ("", None)


def test_public_never_returns_a_secret():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    store.save_token("TOK", None)
    key = _add(store)
    _add(store, "REQ-2", replaces="REQ-1")
    store.update_bank(key, status="LN", account_ids=["A1"])
    public = store.public()
    flat = repr(public)
    for secret in ("SKEY", "TOK", "REQ-1", "REQ-2"):
        assert secret not in flat
    assert public["has_credentials"] is True and public["has_link"] is True
    assert public["secret_id"] == "SID"
    assert [
        (b["institution_name"], b["status"], b["accounts"], b["renewing"]) for b in public["banks"]
    ] == [
        ("ACME Bank", "LN", 1, False),
        ("ACME Bank", "CR", 0, True),
    ]


def test_record_status_sets_the_last_fetched_fields_and_clears_the_caution():
    store = _store()
    store.update({"last_caution": "Other Bank: expired"})
    assert store.get()["last_caution"] == "Other Bank: expired"
    store.record_status("rate_limited", error="later")
    cfg = store.get()
    assert (cfg["last_status"], cfg["last_error"], cfg["last_caution"]) == (
        "rate_limited",
        "later",
        None,
    )
    assert cfg["last_synced_at"]


def test_reset_erases_the_credentials_and_banks():
    store = _store()
    store.update({"secret_id": "SID", "secret_key": "SKEY"})
    _add(store)
    store.reset()
    assert store.get()["secret_key"] == "" and store.banks() == []
