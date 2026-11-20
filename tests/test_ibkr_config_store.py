"""Tests for IbkrConfigStore — CRUD, secret discipline, env ignored.

RFC connectors-as-data-sources retired poll_interval_seconds (no more scheduler),
link_account/account_map (provider-account -> app-account linking now lives on
Account.external_ids), and bump_stats/stats (no writer left once auto-sync was retired).
record_status(status, error=) is the uniform surface connector_fetch.fetch() calls now."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def tmp_store(db_isolated):
    """An IbkrConfigStore backed by the per-test isolated database (no side-effects on
    live data) — the ``ibkr`` row of the connectors table (RFC connectors-table)."""
    from backend.db.ibkr_config_store import IbkrConfigStore

    return IbkrConfigStore()


def test_defaults_on_fresh_store(tmp_store):
    cfg = tmp_store.get()
    assert cfg["enabled"] is False
    assert cfg["flex_token"] == ""
    assert cfg["flex_query_id"] == ""
    assert cfg["last_status"] == "never_run"
    assert cfg["last_synced_at"] is None


def test_env_is_ignored(tmp_store, monkeypatch):
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "env-token")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setenv("IBKR_ENABLED", "true")
    cfg = tmp_store.get()
    assert cfg["flex_token"] == ""
    assert cfg["flex_query_id"] == ""
    assert cfg["enabled"] is False


def test_update_stores_token_and_query_id(tmp_store):
    tmp_store.update({"flex_token": "TOKEN123", "flex_query_id": "456789"})
    cfg = tmp_store.get()
    assert cfg["flex_token"] == "TOKEN123"
    assert cfg["flex_query_id"] == "456789"


def test_blank_token_preserves_saved_value(tmp_store):
    tmp_store.update({"flex_token": "ORIGINAL"})
    tmp_store.update({"flex_token": ""})  # blank = keep
    assert tmp_store.flex_token() == "ORIGINAL"


def test_update_ignores_none_values(tmp_store):
    tmp_store.update({"flex_query_id": "111", "enabled": None})
    cfg = tmp_store.get()
    assert cfg["flex_query_id"] == "111"
    assert cfg["enabled"] is False  # None is ignored, default kept


def test_is_configured_needs_both_fields(tmp_store):
    assert tmp_store.is_configured() is False
    tmp_store.update({"flex_token": "T"})
    assert tmp_store.is_configured() is False  # needs query_id too
    tmp_store.update({"flex_query_id": "Q"})
    assert tmp_store.is_configured() is True


def test_is_active_needs_enabled_and_configured(tmp_store):
    tmp_store.update({"flex_token": "T", "flex_query_id": "Q"})
    assert tmp_store.is_active() is False  # enabled=False
    tmp_store.update({"enabled": True})
    assert tmp_store.is_active() is True


def test_public_never_returns_token(tmp_store):
    tmp_store.update({"flex_token": "SUPERSECRET", "flex_query_id": "123"})
    pub = tmp_store.public()
    # Token must not appear in the public view.
    assert "flex_token" not in pub
    assert "SUPERSECRET" not in str(pub)
    assert pub["has_token"] is True
    # Query ID is not a secret.
    assert pub["flex_query_id"] == "123"


def test_record_status_sets_last_fetched_fields(tmp_store):
    """The uniform record_status(status, error=) surface connector_fetch.fetch() calls --
    replaces the old bump_stats()/account_map() bookkeeping the retired sync orchestration
    used (RFC connectors-as-data-sources). Provider-account linking now lives on
    Account.external_ids, and a fetch tool's own result carries counts -- this store no
    longer tracks a running total (positions_synced/positions_closed had no writer left)."""
    tmp_store.record_status("ok")
    pub = tmp_store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None
    assert pub["last_synced_at"] is not None


def test_record_status_persists_error(tmp_store):
    tmp_store.record_status("auth_error", error="bad flex token")
    pub = tmp_store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad flex token"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_chmod_600_applied(tmp_store, db_isolated):
    """The sqlite DATABASE FILE — not a per-store path, since ibkr_config is a
    connectors/connector_secrets rows now — must be owner-read-only after any
    write. db.engine._restrict_sqlite_modes applies this on every connect: "the same
    at-rest posture auth.json already has" (that module's own docstring, FR-I3)."""
    import os
    import stat

    from backend.db.engine import resolved_sqlite_path

    tmp_store.update({"flex_query_id": "987"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None, "db_isolated must be a sqlite URL for this test to mean anything"
    mode = os.stat(db_path).st_mode
    assert not (mode & stat.S_IRWXG), "group bits must be 0"
    assert not (mode & stat.S_IRWXO), "other bits must be 0"


def test_unknown_keys_ignored(tmp_store):
    tmp_store.update({"flex_token": "T", "flex_query_id": "Q", "unsupported_field": "foo"})
    cfg = tmp_store.get()
    assert "unsupported_field" not in cfg


def test_trading_flag_is_gone(tmp_store):
    """The removal is the contract: `trading_enabled` is not a default, is not settable
    through `update()`, and is not exposed on any read surface. It gated a capability that
    never existed — no order tool, no order wire call. See
    docs/adr/2026-07-20-remove-ibkr-dead-trading-flag.md."""
    from backend.db.ibkr_config_store import DEFAULTS

    assert "trading_enabled" not in DEFAULTS
    assert not hasattr(tmp_store, "is_trading_active")
    tmp_store.update({"flex_token": "T", "flex_query_id": "Q", "trading_enabled": True})
    assert "trading_enabled" not in tmp_store.get()
    assert "trading_enabled" not in tmp_store.public()
    # The read-sync half is untouched by the removal.
    assert tmp_store.is_configured() is True
    assert tmp_store.public()["has_token"] is True
