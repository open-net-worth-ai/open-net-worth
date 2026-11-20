"""AlpacaConfigStore — the ``alpaca`` connector row (RFC connectors-table): write-only key
discipline + the read `enabled` switch.

Connectors never read environment variables; the key pair lives in ``connector_secrets``
(public() exposes only has_keys); a blank key in an update keeps the saved one. `db_isolated`
gives each test a fresh, isolated sqlite file.

The `trading_enabled` kill switch was REMOVED 2026-07-19 with the rest of the trading half —
see `docs/adr/2026-07-19-remove-alpaca-trading-half.md`. Its two dedicated tests went with it;
that the flag no longer exists on any surface is pinned by `test_trading_flag_is_gone`.

RFC `connectors-as-data-sources` then retired `poll_interval_seconds` (no more scheduler),
`account_map`/`link_account` (provider-account -> app-account linking is no longer this
store's job), and `stats`/`bump_stats` (running totals had no writer left once the auto-sync
path was retired). In their place is the uniform `record_status(status, error=, cash=,
buying_power=)` surface `connector_fetch.fetch()` calls — see `test_record_status_*` below.
Fetching + booking is a Lucius-turn tool call now (`alpaca_fetch_positions`), never a REST
sync route — see `tests/test_alpaca_api.py` / `tests/test_alpaca_service.py`.
"""

from __future__ import annotations

import json
import os

import pytest

from backend.db.alpaca_config_store import AlpacaConfigStore


@pytest.fixture
def store(db_isolated):
    return AlpacaConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["environment"] == "paper"  # de-risked onboarding default
    assert cfg["api_key_id"] == ""
    assert cfg["secret_key"] == ""
    assert cfg["cash"] is None
    assert cfg["buying_power"] is None
    assert store.is_configured() is False
    assert store.is_active() is False


def test_paper_default_and_base_url(store):
    assert store.environment() == "paper"
    assert store.base_url() == "https://paper-api.alpaca.markets"
    store.update({"environment": "live"})
    assert store.base_url() == "https://api.alpaca.markets"


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secretxyz")
    monkeypatch.setenv("ALPACA_ENABLED", "true")
    monkeypatch.setenv("ALPACA_ENV", "live")

    cfg = AlpacaConfigStore().get()
    assert cfg["api_key_id"] == ""
    assert cfg["secret_key"] == ""
    assert cfg["enabled"] is False
    assert cfg["environment"] == "paper"


def test_public_hides_keys(store):
    store.update({"enabled": True, "api_key_id": "PKSECRET", "secret_key": "topsecret"})
    pub = store.public()
    assert pub["has_keys"] is True
    assert "api_key_id" not in pub
    assert "secret_key" not in pub
    assert pub["enabled"] is True
    assert pub["environment"] == "paper"
    blob = json.dumps(pub)
    assert "PKSECRET" not in blob
    assert "topsecret" not in blob


def test_blank_keys_keep_saved(store):
    store.update({"api_key_id": "keepme", "secret_key": "keepsecret"})
    store.update({"api_key_id": "", "secret_key": "", "enabled": True})
    assert store.keys() == ("keepme", "keepsecret")
    assert store.get()["enabled"] is True


def test_environment_rejects_garbage(store):
    store.update({"environment": "nonsense"})
    assert store.get()["environment"] == "paper"  # unchanged


def test_is_active_requires_enabled_and_keys(store):
    store.update({"api_key_id": "k", "secret_key": "s"})
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_trading_flag_is_gone(store):
    """The removal is the contract: `trading_enabled` is not a default, is not settable
    (unknown keys are dropped by update()), and is not surfaced by public()."""
    from backend.db.alpaca_config_store import DEFAULTS

    assert "trading_enabled" not in DEFAULTS
    assert not hasattr(store, "is_trading_active")
    store.update({"api_key_id": "k", "secret_key": "s", "trading_enabled": True})
    assert "trading_enabled" not in store.get()
    assert "trading_enabled" not in store.public()


def test_record_status_sets_last_fetched_fields(store):
    """The uniform record_status(status, error, cash, buying_power) surface
    connector_fetch.fetch() calls — replaces the old bump_stats()/account_map()
    bookkeeping the retired adapter used (RFC connectors-as-data-sources). Alpaca's
    store keeps cash/buying_power on the status write since there's no other running
    total for a snapshot-only connector."""
    store.record_status("ok", cash=1500.25, buying_power=3000.50)
    pub = store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None
    assert pub["last_synced_at"] is not None
    assert pub["cash"] == 1500.25
    assert pub["buying_power"] == 3000.50


def test_record_status_without_a_reading_leaves_the_last_cash_in_place(store):
    store.record_status("ok", cash=1500.25, buying_power=3000.50)

    store.record_status("ok")  # a run that reported no balance
    store.record_status("ok", cash=1600.0)  # a run that reported only cash

    pub = store.public()
    assert pub["cash"] == 1600.0
    assert pub["buying_power"] == 3000.50
    store.record_status("auth_error", error="bad creds")
    assert store.public()["cash"] == 1600.0


def test_record_status_persists_error_without_cash(store):
    """A failed fetch has no fresh cash/buying_power reading to report — record_status
    only writes those fields on `status == "ok"`."""
    store.record_status("auth_error", error="bad creds")
    pub = store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad creds"
    assert pub["cash"] is None
    assert pub["buying_power"] is None


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_keys_persisted_with_owner_only_perms(store, db_isolated):
    """No more per-store file to chmod — the DB engine restricts the WHOLE sqlite
    file (+ wal/shm) to 0600 on every connect (db.engine._restrict_sqlite_modes)."""
    from backend.db.engine import resolved_sqlite_path

    store.update({"api_key_id": "k", "secret_key": "s"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None
    mode = os.stat(db_path).st_mode & 0o777
    assert mode == 0o600
