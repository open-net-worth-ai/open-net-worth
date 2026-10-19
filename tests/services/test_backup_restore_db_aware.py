"""services/backup.py's table-aware non-restorable guarantee (DR restore fix, task #30).

`core.sensitive.NON_RESTORABLE_FILES` used to be enough on its own: `restore_backup` skipped
any archive path in that set. But four of those datasets — `tool_grants` (absorbed into
`tool_permissions` at revision 0040), `txn_audit`, `mcp_write_log`, `automation_runtime` —
moved from a JSON file into a table inside `onw.db` (the storage-engine migration). `onw.db`
itself is an ordinary, restorable file, so a restore swapped it wholesale and silently
resurrected a revoked permission / erased newer audit rows — exactly the escalation the
file-level guard was built to prevent. `core.sensitive.NON_RESTORABLE_TABLES` +
`services/backup.py`'s capture/re-apply around the db-file swap close that gap; this module
proves it.

Fully isolated, mirroring `tests/test_backup_service.py`'s own `isolated` fixture PLUS
`tests/db/conftest.py::db_isolated` for the database half: `backup_service.DATA_DIR` /
`BACKUPS_DIR` are monkeypatched into `tmp_path`, and the SQLite db itself is redirected via
`ONW_DATABASE_URL` + `dispose_engine()` — pinned so the db file (`tmp_path/test.db`) sits
directly INSIDE the patched `DATA_DIR` (`tmp_path`), exactly like `onw.db` sits directly
inside the real `DATA_DIR` in production. Without that, the live db would never be part of
what `run_backup` walks/zips at all, and this whole module would prove nothing. The real
`~/.open-net-worth` tree is never touched by any of this (project rule: no destructive tests
against live data).
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone

import pytest

from backend.core.ids import new_id
from backend.db.account_store import AccountStore
from backend.db.engine import dispose_engine
from backend.db.transaction_audit_store import TransactionAuditStore
from backend.services import backup as backup_service
from backend.services import tool_permissions as perms

# Deliberately "today, a few hours ago" rather than a fixed historical date: restore_backup's
# OWN safety backup (run with the real wall clock) prunes against the configured retention
# window every time it runs, and a `now=` far in the past would make this module's backups
# look older than that window and risk being pruned out from under the very restore under
# test (the existing restore_backup code already tolerates this via an open zip handle — see
# its own comment — but there is no reason to lean on that edge case here).
_NOW = datetime.now(timezone.utc).replace(hour=3, minute=30, second=0, microsecond=0)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """DATA_DIR == tmp_path, with a real, migrated onw.db (via db_isolated) living inside it
    at tmp_path/test.db — required so run_backup actually walks/zips the database. A db file
    OUTSIDE DATA_DIR is invisible to `_iter_backup_files` by design, both before and after
    this fix."""
    data_dir = tmp_path
    backups_dir = data_dir / "backups"
    backups_dir.mkdir()

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints"})

    # AppConfigStore is DB-backed now (runtime_state/"app_config") — its row lives in the
    # SAME db_isolated-managed onw.db as ToolPermissionStore/TransactionAuditStore/
    # AccountStore below, no separate redirection needed (and none is possible any more —
    # the store no longer takes a path=).

    return data_dir, backups_dir


def _restore_and_reconnect(name: str) -> dict:
    """`restore_backup(name)`, then drop the connection pool — exactly what a fresh request
    arriving after a real restore would get anyway (a new connection). An ALREADY-OPEN
    sqlite connection keeps its own file handle to whatever inode was open at connect time,
    so a raw `os.replace()` swap of the db file underneath it is invisible to that
    connection until it is dropped and reopened — reproduced empirically writing this test
    (without this, EVERY assertion below silently read the pre-restore file forever, inside
    or outside the fix, because nothing in this test process had reconnected). This is not
    a workaround for the fix under test — it is exactly why `restore_backup` must
    `dispose_engine()` around its own swap (see `services/backup.py`): a caller that never
    reconnects never observes ANY restore, protected table or not."""
    result = backup_service.restore_backup(name)
    dispose_engine()
    return result


def _audit_event(evt_id: str, txn_id: str) -> dict:
    return {
        "id": evt_id,
        "txn_id": txn_id,
        "op": "create",
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": "system",
        "surface": "test",
        "source": None,
        "session_id": None,
        "sender_id": None,
        "intent": None,
        "automation": None,
        "automation_id": None,
        "before": None,
        "after": {"id": txn_id},
        "note": None,
    }


# ── 1. A revoked permission survives a restore (the privilege-model proof) ────────────────


def test_revoked_grant_survives_restore(isolated):
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "add_transactions", "always_allow")

    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "add_transactions", "needs_approval")
    assert perms.stored_tool_level(perms.CORE_CONNECTOR_ID, "add_transactions") == "needs_approval"

    result = _restore_and_reconnect(backup["name"])
    assert result["ok"] is True, result

    # Pre-fix: restore swaps onw.db wholesale, resurrecting the permission that existed at
    # backup time — privilege escalation through an unrelated feature. Post-fix:
    # NON_RESTORABLE_TABLES keeps the revoke.
    assert perms.stored_tool_level(perms.CORE_CONNECTOR_ID, "add_transactions") == "needs_approval"


# ── 2. The audit trail is never erased by a restore ────────────────────────────────────────


def test_audit_trail_survives_restore(isolated):
    audit = TransactionAuditStore()
    first = _audit_event(new_id("evt"), "txn_1")
    audit.append(first)

    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    second = _audit_event(new_id("evt"), "txn_2")
    audit.append(second)

    result = _restore_and_reconnect(backup["name"])
    assert result["ok"] is True, result

    ids = {e["id"] for e in audit.all()}
    # Pre-fix: the wholesale onw.db swap reverts txn_audit to the backup's copy, erasing
    # `second` (written after the backup) — a data restore that erases how the data got here.
    # Post-fix: both rows survive.
    assert first["id"] in ids
    assert second["id"] in ids


# ── 3. Ordinary data IS restored (this fix must not make everything non-restorable) ────────


def test_ordinary_table_is_restored(isolated):
    accounts = AccountStore()
    accounts.create(
        {"id": "acc_1", "name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"}
    )

    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    accounts.create(
        {"id": "acc_2", "name": "Savings", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    assert {a["id"] for a in accounts.all()} == {"acc_1", "acc_2"}

    result = _restore_and_reconnect(backup["name"])
    assert result["ok"] is True, result

    # acc_2 was created AFTER the backup — an ordinary (non-protected) table must revert to
    # exactly the backup's contents, same as any other restorable file.
    assert {a["id"] for a in accounts.all()} == {"acc_1"}


# ── 4. Fresh install: nothing to capture, restore must not crash ───────────────────────────


def test_restore_with_no_non_restorable_rows_does_not_crash(isolated):
    assert perms.list_decisions() == []
    assert TransactionAuditStore().all() == []

    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    result = _restore_and_reconnect(backup["name"])

    assert result["ok"] is True, result
    assert perms.list_decisions() == []
    assert TransactionAuditStore().all() == []


# ── 5. A pre-migration backup (no db member at all) is REFUSED, not silently destructive ───
# Security review CRITICAL (disposition c, reproduced): restoring an archive with no onw.db
# member used to delete the live database anyway (an ordinary file, present in DATA_DIR,
# absent from the archive) with nothing to recreate it — and report ok:True, so the operator
# had no signal to reach for the safety backup. Reachable on the default deployment for the
# entire retention window after the storage-engine cutover.


def test_restore_refuses_a_pre_migration_backup_with_no_db_member(isolated):
    data_dir, backups_dir = isolated

    live_db = backup_service._live_db_path()
    assert live_db is not None and live_db.exists()
    live_db_bytes_before = live_db.read_bytes()

    # An ordinary file that must survive the refused restore untouched — proves "nothing was
    # deleted", not merely "the db specifically survived".
    marker = data_dir / "some_legacy_file.json"
    marker.write_text('{"untouched": true}')

    # Hand-built pre-migration-shaped backup: real file(s), but NO member matching the live
    # db at all — run_backup() never produces this shape today (it always includes the live
    # db when one exists, before or after this fix), so this simulates a backup taken before
    # onw.db existed.
    stale_name = "20260101T000000Z"
    stale_zip = backups_dir / f"{stale_name}.zip"
    with zipfile.ZipFile(stale_zip, "w") as zf:
        zf.writestr("accounts.json", "[]")

    result = backup_service.restore_backup(stale_name)

    # Pre-fix: ok is True, the live db is gone (files_written counts everything else,
    # files_removed includes the unlinked db), and nothing recreates it. Post-fix: refused.
    assert result["ok"] is False, result
    assert result.get("error"), result

    # The live db is byte-for-byte unchanged — no swap, no delete-with-nothing-to-restore.
    assert live_db.read_bytes() == live_db_bytes_before
    # Nothing else was touched either — the guard returns before the delete pass runs.
    assert marker.read_text() == '{"untouched": true}'
    # No safety backup was written for a restore that never happened (mirrors the existing
    # zip-slip guard's "a refused restore must leave no trace").
    assert [p.name for p in backups_dir.iterdir()] == [stale_zip.name]
