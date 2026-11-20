"""LunchFlowConfigStore — the ``lunch_flow`` connector row (RFC connectors-table), write-only
secret discipline (connectors never read env), the
two-tier credential shape (developer client_id/secret vs OAuth tokens), the
compare-and-swap token refresh, and the ``accounts_meta`` institution bookkeeping (RFC
``connectors-as-data-sources`` — no more ``account_map``/poll interval/stats; the
provider-account → app-account link lives on ``Account.external_ids`` instead).

Mirrors ``tests/test_plaid_config_store.py``'s own contract test shape.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from backend.db.connector_store import ConnectorStore
from backend.db.lunch_flow_config_store import LunchFlowConfigStore


@pytest.fixture
def store(db_isolated):
    return LunchFlowConfigStore()


def test_defaults_when_absent(store):
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["client_id"] == ""
    assert cfg["client_secret"] == ""
    assert cfg["email"] == ""
    assert cfg["access_token"] == ""
    assert cfg["refresh_token"] == ""
    assert cfg["token_expires_at"] is None
    assert cfg["user_id"] == ""
    assert cfg["accounts_meta"] == {}
    assert cfg["last_caution"] is None
    assert store.has_credentials() is False
    assert store.has_link() is False
    assert store.is_configured() is False
    assert store.is_active() is False


def test_env_is_ignored(db_isolated, monkeypatch):
    monkeypatch.setenv("LUNCH_FLOW_CLIENT_ID", "cid-123")
    monkeypatch.setenv("LUNCH_FLOW_CLIENT_SECRET", "sec-abc")
    monkeypatch.setenv("LUNCH_FLOW_EMAIL", "me@example.com")

    cfg = LunchFlowConfigStore().get()
    assert cfg["client_id"] == ""
    assert cfg["client_secret"] == ""
    assert cfg["email"] == ""


def test_public_hides_secret_and_tokens(store):
    store.update({"client_id": "cid-1", "email": "me@example.com", "enabled": True})
    store.update({"client_secret": "super-secret"})
    store.set_tokens(
        access_token="access-xyz", refresh_token="refresh-xyz", expires_at=123.0, user_id="u1"
    )
    pub = store.public()
    assert pub["has_credentials"] is True
    assert pub["has_link"] is True
    assert pub["client_id"] == "cid-1"  # the id is not a secret
    assert pub["email"] == "me@example.com"
    assert pub["user_id"] == "u1"
    assert "client_secret" not in pub
    assert "access_token" not in pub
    assert "refresh_token" not in pub
    assert pub["configured"] is True
    dumped = json.dumps(pub)
    assert "super-secret" not in dumped
    assert "access-xyz" not in dumped
    assert "refresh-xyz" not in dumped


def test_blank_client_secret_keeps_saved(store):
    store.update({"client_id": "cid", "client_secret": "keep-me"})
    store.update({"client_secret": "", "enabled": True})
    assert store.client_secret() == "keep-me"
    assert store.get()["enabled"] is True


def test_update_never_writes_tokens_directly(store):
    """access_token/refresh_token are only ever set via set_tokens/apply_token_refresh — a
    stray attempt to smuggle one through update() is silently dropped, never written."""
    store.update({"access_token": "sneaky", "refresh_token": "sneaky-2", "client_id": "cid"})
    assert store.access_token() == ""
    assert store.refresh_token() == ""
    assert store.get()["client_id"] == "cid"


def test_is_configured_requires_credentials_and_link(store):
    store.update({"client_id": "cid", "client_secret": "skey"})
    assert store.has_credentials() is True
    assert store.is_configured() is False  # no bank linked yet
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=None, user_id="u1")
    assert store.is_configured() is True
    assert store.is_active() is False  # configured but disabled
    store.update({"enabled": True})
    assert store.is_active() is True


def test_record_account_meta_and_connections_grouping(store):
    store.record_account_meta("101", connection_id=9, institution_name="Chase", name="Checking")
    store.record_account_meta("102", connection_id=9, institution_name="Chase", name="Savings")
    store.record_account_meta("201", connection_id=10, institution_name="Compass", name="EUR")
    assert set(store.accounts_meta()) == {"101", "102", "201"}
    # onw_account_id is NOT part of this row — that identity lives on Account.external_ids.
    assert "onw_account_id" not in store.accounts_meta()["101"]

    conns = store.connections()
    assert len(conns) == 2
    chase = next(c for c in conns if c["connection_id"] == 9)
    assert chase["institution_name"] == "Chase"
    assert {a["lunch_flow_account_id"] for a in chase["accounts"]} == {"101", "102"}
    assert len(store.public()["connections"]) == 2


def test_record_account_meta_is_idempotent(store):
    store.record_account_meta("101", connection_id=9, institution_name="Chase", name="Checking")
    store.record_account_meta("101", connection_id=9, institution_name="Chase", name="Checking")
    assert len(store.accounts_meta()) == 1


def test_forget_connection_removes_only_that_connections_accounts(store):
    store.record_account_meta("101", connection_id=9, institution_name="Chase")
    store.record_account_meta("201", connection_id=10, institution_name="Compass")

    forgotten = store.forget_connection(9)
    assert forgotten == ["101"]  # Lunch Flow account ids, not app account ids
    assert store.accounts_meta() == {
        "201": {"connection_id": 10, "institution_name": "Compass", "name": ""}
    }
    assert [c["connection_id"] for c in store.connections()] == [10]


def test_set_tokens_then_apply_token_refresh_cas(store):
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=100.0, user_id="u1")
    # Winning refresh: matches the currently stored access token.
    updated = store.apply_token_refresh(
        expected_access_token="tok-1",
        access_token="tok-2",
        refresh_token="rt-2",
        token_expires_at=200.0,
    )
    assert updated["access_token"] == "tok-2"
    assert store.access_token() == "tok-2"
    assert store.refresh_token() == "rt-2"
    assert store.token_expires_at() == 200.0


def test_apply_token_refresh_is_a_noop_when_it_lost_the_race(store):
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=100.0, user_id="u1")
    # Someone else already refreshed — expected_access_token no longer matches.
    result = store.apply_token_refresh(
        expected_access_token="stale-token-nobody-has",
        access_token="tok-attacker",
        refresh_token="rt-attacker",
        token_expires_at=999.0,
    )
    # The stored token is untouched — the "loser" call did not overwrite the winner's write.
    assert result["access_token"] == "tok-1"
    assert store.access_token() == "tok-1"


def test_record_run_clears_a_stale_error_and_caution(store):
    """record_run's None is a REAL value, not update()'s "leave unchanged" — a later clean
    run must actually clear a previous run's error/caution, not leave it stuck forever."""
    store.record_run(synced_at="t1", status="auth_error", error="boom", caution=None)
    cfg = store.get()
    assert cfg["last_status"] == "auth_error"
    assert cfg["last_error"] == "boom"

    store.record_run(synced_at="t2", status="ok", error=None, caution=None)
    cfg = store.get()
    assert cfg["last_status"] == "ok"
    assert cfg["last_error"] is None  # actually cleared, not silently dropped by update()


def test_record_run_disable_flips_enabled_off_in_the_same_write(store):
    store.update({"client_id": "cid", "client_secret": "csecret", "enabled": True})
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=None, user_id="u1")
    assert store.is_active() is True

    store.record_run(
        synced_at="t1", status="ok", error=None, caution="looks backwards", disable=True
    )
    cfg = store.get()
    assert cfg["last_caution"] == "looks backwards"
    assert cfg["enabled"] is False
    assert store.is_active() is False


def test_record_status_is_a_thin_wrapper_over_record_run(store):
    """The uniform record_status(status, error) surface connector_fetch.fetch() calls —
    a thin wrapper over record_run with an internally-computed timestamp and no
    caution/disable (a fetch's own FetchWindow.warnings, if any, are applied separately)."""
    store.record_status("ok", error=None)
    cfg = store.get()
    assert cfg["last_status"] == "ok"
    assert cfg["last_error"] is None
    assert cfg["last_synced_at"] is not None

    store.record_status("auth_error", error="bad token")
    cfg = store.get()
    assert cfg["last_status"] == "auth_error"
    assert cfg["last_error"] == "bad token"


def test_reset_erases_credentials_but_keeps_institution_bookkeeping(store):
    store.update({"client_id": "cid", "client_secret": "csecret", "enabled": True})
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=100.0, user_id="u1")
    store.record_account_meta("101", connection_id=9, institution_name="Chase", name="Checking")

    store.reset()

    cfg = store.get()
    assert cfg["client_id"] == ""
    assert cfg["client_secret"] == ""
    assert cfg["access_token"] == ""
    assert cfg["refresh_token"] == ""
    assert cfg["user_id"] == ""
    assert cfg["enabled"] is False
    assert set(store.accounts_meta()) == {"101"}
    assert ConnectorStore().secrets("lunch_flow") == {}


def test_reset_with_nothing_to_keep_removes_the_row(store):
    store.update({"client_id": "cid"})
    store.reset()
    assert ConnectorStore().get("lunch_flow") is None


def test_set_tokens_and_a_refresh_write_the_right_columns(store):
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=100.0, user_id="u1")
    row = ConnectorStore().get("lunch_flow")
    assert row["state"]["token_expires_at"] == 100.0
    assert row["state"]["user_id"] == "u1"
    assert "tok-1" not in json.dumps(row)
    assert ConnectorStore().secrets("lunch_flow") == {
        "access_token": "tok-1",
        "refresh_token": "rt-1",
    }

    # A refresh without an expiry clears the old one rather than keeping a stale deadline.
    store.apply_token_refresh(
        expected_access_token="tok-1",
        access_token="tok-2",
        refresh_token="rt-2",
        token_expires_at=None,
    )
    assert store.token_expires_at() is None
    assert store.access_token() == "tok-2"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_secrets_persisted_with_owner_only_perms(store, db_isolated):
    from backend.db.engine import resolved_sqlite_path

    store.update({"client_id": "cid", "client_secret": "skey"})
    db_path = resolved_sqlite_path(db_isolated)
    assert db_path is not None
    mode = os.stat(db_path).st_mode & 0o777
    assert mode == 0o600


# ── concurrency: every writer on this row is now ONE serialized transaction ──────────────
#
# Security review finding (2026-08-17): apply_token_refresh's compare-and-swap correctly
# guarded refresh-vs-refresh, but every OTHER writer (update/set_tokens/record_account_meta/
# forget_connection) did a plain get()-then-save() as TWO separate transactions — a refresh
# landing in the gap between them was silently reverted by the writer's stale in-memory read
# (proven live: a snapshot held across the gap, saved back after a concurrent
# apply_token_refresh, reverted the rotated refresh_token). These tests pin that every writer
# now holds ONE serialized transaction across its own read+write, so no other writer can land
# in that gap.


def test_update_cannot_clobber_a_concurrent_token_refresh(store, db_isolated, monkeypatch):
    """Regression for the reviewer-demonstrated clobber. update() must not be a bare
    get()-then-save() — while it holds ITS OWN serialized transaction (proven below by the
    refresh being unable to complete until update() releases it), a concurrent
    apply_token_refresh() must be forced to wait, never interleave, so update()'s eventual
    write can never carry a stale pre-refresh token snapshot over the refresh's write."""
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=None, user_id="u1")

    entered_update = threading.Event()
    release_update = threading.Event()
    calls = {"n": 0}

    original_get = LunchFlowConfigStore.get

    def paused_get(self, *, conn=None):
        result = original_get(self, conn=conn)
        calls["n"] += 1
        if calls["n"] == 1:
            entered_update.set()
            assert release_update.wait(timeout=5), "test itself never released update()"
        return result

    monkeypatch.setattr(LunchFlowConfigStore, "get", paused_get)

    def do_update():
        LunchFlowConfigStore().update({"email": "concurrent@example.com"})

    refresh_done = threading.Event()

    def do_refresh():
        LunchFlowConfigStore().apply_token_refresh(
            expected_access_token="tok-1",
            access_token="tok-2",
            refresh_token="rt-2",
            token_expires_at=200.0,
        )
        refresh_done.set()

    t_update = threading.Thread(target=do_update)
    t_refresh = None
    try:
        t_update.start()
        assert entered_update.wait(timeout=5), "update() never reached its read"

        t_refresh = threading.Thread(target=do_refresh)
        t_refresh.start()
        # update() is paused mid-transaction (past its own read, still holding the
        # serialized lock). If update() were still two separate round trips, the refresh
        # would sail through here — it must instead be blocked until update() finishes.
        assert not refresh_done.wait(timeout=0.3), (
            "a concurrent apply_token_refresh completed WHILE update() was mid-transaction "
            "— update() is not holding one serialized transaction across its own read+write"
        )
    finally:
        release_update.set()
        t_update.join(timeout=5)
        if t_refresh is not None:
            t_refresh.join(timeout=5)

    # The refresh's write survives update()'s write — neither reverted the other.
    final = LunchFlowConfigStore()
    assert final.access_token() == "tok-2"
    assert final.refresh_token() == "rt-2"
    assert final.get()["email"] == "concurrent@example.com"


def test_record_account_meta_cannot_clobber_a_concurrent_token_refresh(
    store, db_isolated, monkeypatch
):
    """Same clobber shape as update() above, for record_account_meta() specifically — its
    ``accounts_meta`` merge is computed FROM a read, so a stale read is doubly dangerous
    (dropped institution bookkeeping AND a reverted token) if it is not one serialized
    transaction."""
    store.set_tokens(access_token="tok-1", refresh_token="rt-1", expires_at=None, user_id="u1")

    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    original_get = LunchFlowConfigStore.get

    def paused_get(self, *, conn=None):
        result = original_get(self, conn=conn)
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            assert release.wait(timeout=5), "test itself never released record_account_meta()"
        return result

    monkeypatch.setattr(LunchFlowConfigStore, "get", paused_get)

    def do_record():
        LunchFlowConfigStore().record_account_meta("101", connection_id=9, institution_name="Chase")

    refresh_done = threading.Event()

    def do_refresh():
        LunchFlowConfigStore().apply_token_refresh(
            expected_access_token="tok-1",
            access_token="tok-2",
            refresh_token="rt-2",
            token_expires_at=200.0,
        )
        refresh_done.set()

    t_record = threading.Thread(target=do_record)
    t_refresh = None
    try:
        t_record.start()
        assert entered.wait(timeout=5), "record_account_meta() never reached its read"

        t_refresh = threading.Thread(target=do_refresh)
        t_refresh.start()
        assert not refresh_done.wait(timeout=0.3), (
            "a concurrent apply_token_refresh completed WHILE record_account_meta() was "
            "mid-transaction — record_account_meta() is not holding one serialized transaction"
        )
    finally:
        release.set()
        t_record.join(timeout=5)
        if t_refresh is not None:
            t_refresh.join(timeout=5)

    final = LunchFlowConfigStore()
    assert final.access_token() == "tok-2"
    assert final.refresh_token() == "rt-2"
    assert final.accounts_meta()["101"]["connection_id"] == 9
