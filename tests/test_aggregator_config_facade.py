"""The nine aggregator settings stores are facades over ``ConnectorStore`` (RFC
connectors-table): each field lands exactly where the migration placed it — ``enabled`` on the
row, settings in ``config``, app-written fields in ``state``, credentials and machine-minted
tokens in ``connector_secrets`` — and nothing sensitive reaches the readable ``connectors`` table.
"""

from __future__ import annotations

import json

import pytest

from backend.db.actual_budget_config_store import ActualBudgetConfigStore
from backend.db.alpaca_config_store import AlpacaConfigStore
from backend.db.connector_store import ConnectorStore
from backend.db.enable_banking_config_store import EnableBankingConfigStore
from backend.db.ibkr_config_store import IbkrConfigStore
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.db.lunch_money_config_store import LunchMoneyConfigStore
from backend.db.plaid_config_store import PlaidConfigStore
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.db.ynab_config_store import YnabConfigStore
from backend.db.yodlee_config_store import YodleeConfigStore
from backend.services.connector_catalog import CATALOG

STORES = [
    SimpleFinConfigStore,
    PlaidConfigStore,
    LunchMoneyConfigStore,
    LunchFlowConfigStore,
    YnabConfigStore,
    ActualBudgetConfigStore,
    EnableBankingConfigStore,
    YodleeConfigStore,
    AlpacaConfigStore,
    IbkrConfigStore,
]
_IDS = [s.KIND for s in STORES]

# One value for every writable field, so a single update() exercises the whole partition.
_VALUES = {
    "client_id": "cid-1",
    "environment": "production",
    "api_endpoint": "production",
    "username": "user-1",
    "email": "me@example.com",
    "application_id": "app-1",
    "aspsp": "Some Bank",
    "country": "FI",
    "budget_id": "budget-1",
    "server_url": "https://actual.acme.test",
    "flex_query_id": "987",
    "base_url": "https://flex.example/ws",
    "last_synced_at": "2026-10-10T00:00:00+00:00",
    "last_status": "ok",
    "last_error": "boom",
    "last_caution": "looks backwards",
    "accounts_meta": {"101": {"connection_id": 9, "institution_name": "Chase", "name": "C"}},
    "user_id": "u1",
    "token_expires_at": 123.0,
    "budget_name": "Personal",
    "budget_currency": "USD",
    "cash": 10.5,
    "buying_power": 20.5,
}


def _value(key: str) -> object:
    return _VALUES.get(key, f"secret-{key}")


def _values(cls) -> dict:
    values = {k: _value(k) for k in set(cls.DEFAULTS) - set(cls.READ_ONLY_KEYS) - {"enabled"}}
    if cls is AlpacaConfigStore:
        values["environment"] = "live"
    return values


def _free_text_secret(cls) -> str:
    """A secret that accepts any string; an address is validated, so it is not one."""
    return next(k for k in cls.SECRET_KEYS if k not in cls.READ_ONLY_KEYS and k != "server_url")


@pytest.fixture(autouse=True)
def _db(db_isolated):
    pass


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_every_default_has_exactly_one_home(cls):
    homes = [*cls.CONFIG_KEYS, *cls.SECRET_KEYS, *cls.STATE_KEYS]
    assert len(homes) == len(set(homes))
    assert set(homes) | {"enabled"} == set(cls.DEFAULTS)


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_kind_label_and_secrets_agree_with_the_catalog(cls):
    kind = CATALOG[cls.KIND]
    assert cls.LABEL == kind.label
    assert {f.name for f in kind.secret_fields} == set(cls.SECRET_KEYS)


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_nothing_is_stored_until_the_first_write(cls):
    store = cls()
    assert store.get() == cls.DEFAULTS
    assert ConnectorStore().get(cls.KIND) is None


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_fields_land_where_the_migration_places_them(cls):
    store = cls()
    values = _values(cls)
    store.update({"enabled": True, **values})

    row = ConnectorStore().get(cls.KIND)
    assert row["kind"] == cls.KIND
    assert row["name"] == cls.LABEL
    assert row["enabled"] is True
    assert row["config"] == {k: values[k] for k in cls.CONFIG_KEYS}
    assert row["state"] == {k: values[k] for k in cls.STATE_KEYS}
    secrets = ConnectorStore().secrets(cls.KIND)
    assert secrets == {k: values[k] for k in cls.SECRET_KEYS if k in values}

    # The readable table never carries a credential.
    dumped = json.dumps(row)
    assert not any(str(v) in dumped for v in secrets.values())


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_get_returns_the_merged_view_and_public_never_a_secret(cls):
    store = cls()
    values = _values(cls)
    store.update(values)

    merged = store.get()
    for key, value in values.items():
        assert merged[key] == value

    public = json.dumps(store.public())
    for key in cls.SECRET_KEYS:
        if key in values:
            assert str(values[key]) not in public


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_a_blank_secret_keeps_the_saved_one(cls):
    store = cls()
    secret = _free_text_secret(cls)
    store.update({secret: "keep-me"})
    store.update({secret: "   ", "enabled": True})
    assert store.get()[secret] == "keep-me"
    assert ConnectorStore().secrets(cls.KIND)[secret] == "keep-me"


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_reset_deletes_the_row_and_its_secrets(cls):
    store = cls()
    secret = _free_text_secret(cls)
    store.update({secret: "x", "enabled": True})
    store.reset()
    assert ConnectorStore().get(cls.KIND) is None
    assert ConnectorStore().secrets(cls.KIND) == {}
    assert store.get()[secret] == ""


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_last_error_is_redacted_before_it_is_stored(cls):
    cls().record_status("connect_error", error="GET https://user:hunter2@bank.example/x failed")
    stored = ConnectorStore().get(cls.KIND)["state"]["last_error"]
    assert "hunter2" not in stored
    assert "hunter2" not in cls().get()["last_error"]


@pytest.mark.parametrize("cls", STORES, ids=_IDS)
def test_an_update_to_one_field_leaves_the_others(cls):
    store = cls()
    store.update({"enabled": True})
    store.record_status("ok")
    store.update({"enabled": False})
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["last_status"] == "ok"
