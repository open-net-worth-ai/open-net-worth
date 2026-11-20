"""PlaidConfigStore — the ``plaid`` connector row (RFC connectors-table): settings in
``connectors.config``, ``secret`` / ``access_token`` / ``item_id`` in ``connector_secrets``,
status in ``connectors.state``. ``record_status(status, error)`` is the one write a fetch
call makes. Connectors never read environment variables, the secret + access token are
write-only (public() exposes only has_secret / has_access_token), and a blank write-only
field in an update keeps the saved one.
"""

from __future__ import annotations

import json
import os

import pytest

from backend.db.connector_store import ConnectorStore
from backend.db.plaid_config_store import PlaidConfigStore


@pytest.fixture
def store(db_isolated):
    """db_isolated gives the store a fresh, isolated sqlite file."""
    return PlaidConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["client_id"] == ""
    assert cfg["secret"] == ""
    assert cfg["environment"] == "sandbox"
    assert cfg["access_token"] == ""
    assert cfg["item_id"] == ""
    assert cfg["last_synced_at"] is None
    assert cfg["last_status"] == "never_run"
    assert cfg["last_error"] is None
    assert store.has_secret() is False
    assert store.has_access_token() is False
    assert store.is_configured() is False
    assert store.is_active() is False


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("PLAID_CLIENT_ID", "cid-123")
    monkeypatch.setenv("PLAID_SECRET", "sec-abc")
    monkeypatch.setenv("PLAID_ENV", "production")

    cfg = PlaidConfigStore().get()
    assert cfg["client_id"] == ""
    assert cfg["secret"] == ""
    assert cfg["environment"] == "sandbox"


def test_public_hides_secret_and_access_token(store):
    store.update(
        {
            "client_id": "cid-1",
            "secret": "super-secret",
            "access_token": "access-xyz",
            "enabled": True,
        }
    )
    pub = store.public()
    assert pub["has_secret"] is True
    assert pub["has_access_token"] is True
    assert pub["client_id"] == "cid-1"  # the id is not a secret
    assert "secret" not in pub
    assert "access_token" not in pub
    assert pub["configured"] is True
    # The secret + access token must never appear in the public view.
    dumped = json.dumps(pub)
    assert "super-secret" not in dumped
    assert "access-xyz" not in dumped


def test_blank_secret_keeps_saved(store):
    store.update({"client_id": "cid", "secret": "keep-me"})
    store.update({"secret": "", "enabled": True})
    assert store.secret() == "keep-me"
    assert store.get()["enabled"] is True
    # An omitted secret (None dropped) also keeps it.
    store.update({"environment": "production"})
    assert store.secret() == "keep-me"


def test_blank_access_token_keeps_saved(store):
    store.update({"client_id": "cid", "secret": "skey", "access_token": "tok-1"})
    store.update({"access_token": ""})
    assert store.access_token() == "tok-1"


def test_environment_validated(store):
    store.update({"environment": "PRODUCTION"})  # normalized to lower
    assert store.environment() == "production"
    store.update({"environment": "bogus"})  # invalid → kept as-is (dropped)
    assert store.environment() == "production"
    assert store.api_base() == "https://production.plaid.com"


def test_is_configured_requires_creds_and_access_token(store):
    store.update({"client_id": "cid", "secret": "skey"})
    assert store.has_secret() is True
    assert store.is_configured() is False  # no Item linked yet
    store.update({"access_token": "tok-1"})
    assert store.is_configured() is True
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_record_status_sets_last_fetched_fields(store):
    """The uniform record_status(status, error) surface a fetch call makes now —
    replaces the old bump_stats()/account_map()/cursor bookkeeping the retired sync
    orchestrator used (RFC connectors-as-data-sources)."""
    store.record_status("ok", error=None)
    pub = store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None
    assert pub["last_synced_at"] is not None


def test_record_status_persists_error(store):
    store.record_status("auth_error", error="bad creds")
    pub = store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad creds"


def test_a_later_successful_run_clears_the_earlier_error(store):
    store.record_status("auth_error", error="bad creds")
    store.record_status("ok")
    pub = store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None


def test_reset_clears_everything(store):
    store.update({"client_id": "cid", "secret": "skey", "access_token": "tok", "enabled": True})
    store.record_status("ok")
    store.reset()
    cfg = store.get()
    assert cfg["client_id"] == ""
    assert cfg["secret"] == ""
    assert cfg["access_token"] == ""
    assert cfg["enabled"] is False
    assert cfg["last_status"] == "never_run"
    # The row and its secrets are gone, not blanked.
    assert ConnectorStore().get("plaid") is None
    assert ConnectorStore().secrets("plaid") == {}


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_secrets_persisted_with_owner_only_perms(store, db_isolated):
    """No more per-store file to chmod — the DB engine restricts the WHOLE sqlite
    file (+ wal/shm) to 0600 on every connect (db.engine._restrict_sqlite_modes)."""
    from backend.db.engine import resolved_sqlite_path

    store.update({"client_id": "cid", "secret": "skey"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None
    mode = os.stat(db_path).st_mode & 0o777
    assert mode == 0o600
