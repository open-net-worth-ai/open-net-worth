"""EnableBankingConfigStore — CRUD + write-only-secret discipline + record_status +
public() over the ``enable_banking`` connector row (RFC connectors-table) — see the store's
own module docstring. Mirrors tests/test_yodlee.py::TestYodleeConfigStore. No network.
"""

from __future__ import annotations

import os

import pytest

_PEM = "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def isolate_store(db_isolated):
    pass


def _store():
    from backend.db.enable_banking_config_store import EnableBankingConfigStore

    return EnableBankingConfigStore()


def test_defaults_when_no_row():
    cfg = _store().get()
    assert cfg["enabled"] is False
    assert cfg["application_id"] == ""
    assert cfg["private_key"] == ""
    assert cfg["aspsp"] == ""
    assert cfg["country"] == ""
    assert cfg["session_id"] == ""
    assert cfg["last_status"] == "never_run"
    assert cfg["last_error"] is None


def test_update_and_persist():
    store = _store()
    store.update({"application_id": "app-1", "private_key": _PEM, "enabled": True})
    cfg = _store().get()
    assert cfg["application_id"] == "app-1"
    assert cfg["private_key"] == _PEM
    assert cfg["enabled"] is True


def test_private_key_is_write_only_and_blank_keeps_saved():
    store = _store()
    store.update({"application_id": "app-1", "private_key": _PEM})
    # A blank private_key leaves the saved one unchanged.
    store.update({"application_id": "app-1", "private_key": ""})
    assert _store().private_key() == _PEM
    # public() never echoes the key — only has_credentials.
    pub = store.public()
    assert "private_key" not in pub
    assert pub["has_credentials"] is True
    assert pub["application_id"] == "app-1"  # the id is not the secret


def test_is_configured_requires_creds_and_session():
    store = _store()
    store.update({"application_id": "app-1", "private_key": _PEM})
    assert store.has_credentials() is True
    assert store.is_configured() is False  # no session yet
    store.update({"session_id": "sess-9"})
    assert store.is_configured() is True
    assert store.is_active() is False  # not enabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_record_status_sets_last_fetched_fields():
    """The uniform record_status(status, error) surface a fetch tool calls — replaces the
    retired bump_stats()/account_map() bookkeeping the old scheduler-driven adapter used
    (RFC connectors-as-data-sources)."""
    store = _store()
    store.record_status("ok", error=None)
    pub = store.public()
    assert pub["last_status"] == "ok"
    assert pub["last_error"] is None
    assert pub["last_synced_at"] is not None


def test_record_status_persists_error():
    store = _store()
    store.record_status("auth_error", error="bad creds")
    pub = store.public()
    assert pub["last_status"] == "auth_error"
    assert pub["last_error"] == "bad creds"


def test_public_never_returns_secrets():
    store = _store()
    store.update(
        {
            "application_id": "app-1",
            "private_key": _PEM,
            "session_id": "sess-1",
            "aspsp": "Bank A",
            "country": "DE",
        }
    )
    pub = store.public()
    assert "private_key" not in pub
    assert "session_id" not in pub
    assert pub["application_id"] == "app-1"
    assert pub["aspsp"] == "Bank A"
    assert pub["country"] == "DE"
    assert pub["has_session"] is True


def test_reset_clears_everything():
    store = _store()
    store.update(
        {
            "application_id": "app-1",
            "private_key": _PEM,
            "session_id": "sess-1",
            "enabled": True,
        }
    )
    store.reset()
    cfg = _store().get()
    assert cfg["application_id"] == ""
    assert cfg["private_key"] == ""
    assert cfg["session_id"] == ""
    assert cfg["enabled"] is False


def test_env_is_ignored(monkeypatch):
    monkeypatch.setenv("ENABLEBANKING_APPLICATION_ID", "env-app")
    monkeypatch.setenv("ENABLEBANKING_PRIVATE_KEY", "line1\\nline2")
    cfg = _store().get()
    assert cfg["application_id"] == ""
    assert cfg["private_key"] == ""


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_chmod_applied(db_isolated):
    """No more per-store file to chmod — the DB engine restricts the WHOLE sqlite file
    (+ wal/shm) to 0600 on every connect (db.engine._restrict_sqlite_modes), the DB-era
    equivalent of the old per-JSON-file chmod(0o600). Mirrors
    tests/test_yodlee.py::TestYodleeConfigStore.test_chmod_applied."""
    import os
    import stat

    from backend.db.engine import resolved_sqlite_path

    store = _store()
    store.update({"application_id": "x"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None
    mode = os.stat(db_path).st_mode
    assert not (mode & stat.S_IRWXG)
    assert not (mode & stat.S_IRWXO)
