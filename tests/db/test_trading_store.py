"""Storage boundaries for trading operational records, using isolated synthetic data."""

import pytest
from sqlalchemy import delete

from backend.ai.checkpoint._policy import _EXCLUDED_TABLES
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.sensitive import NON_RESTORABLE_TABLES, SECRET_TABLES
from backend.db.sql_console import DENIED_TABLES, execute_readonly
from backend.db.tables import TRADING_TABLES
from backend.db.trading_store import TradingStore, trading_transaction


def test_trading_store_scopes_and_updates_records(db_isolated):
    store = TradingStore("workspaces")
    store.insert({"id": "tw_one", "scope_id": "scope_a", "name": "One"})
    store.insert({"id": "tw_two", "scope_id": "scope_b", "name": "Two"})
    store.update_by_id("tw_one", {"name": "Updated"})
    assert [row["id"] for row in store.list_scope("scope_a")] == ["tw_one"]
    assert store.required("tw_one")["name"] == "Updated"
    assert store.delete("tw_one")
    with pytest.raises(NotFoundError):
        store.required("tw_one")


def test_transaction_fails_closed_without_ownership_row(db_isolated):
    with trading_transaction() as conn:
        conn.execute(delete(TRADING_TABLES["trading_control"]))
    with pytest.raises(ConflictError):
        with trading_transaction():
            pytest.fail("A transaction without the lock row must not proceed")


def test_operational_data_is_protected_from_restore_and_checkpoint():
    operational = set(TRADING_TABLES) - {"trading_control"}
    assert operational <= NON_RESTORABLE_TABLES
    assert set(TRADING_TABLES) <= _EXCLUDED_TABLES
    assert "trading_grants" in SECRET_TABLES & DENIED_TABLES


def test_grants_are_not_readable_from_lucius_sql(db_isolated):
    TradingStore("grants").insert({"id": "grant_synthetic", "scope_id": "run_synthetic"})
    with pytest.raises(ValidationError):
        execute_readonly("SELECT * FROM trading_grants")
