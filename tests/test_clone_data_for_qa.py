"""Regression test for clone_data_for_qa runtime-state exclusions (CH-04 fix).

Ensures the clone DROPS runtime/session dirs (_DROP_DIRS) and runtime files
(_DROP_FILES that are not secrets) while KEEPING financial data files.

All I/O goes to tmp_path — the live DATA_DIR is never touched.

Run:
    .venv/bin/pytest tests/test_clone_data_for_qa.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from backend.scripts.clone_data_for_qa import _DROP_DIRS, clone


def _make_source(tmp_path: Path) -> Path:
    """Build a minimal fake source dir that mimics the real DATA_DIR layout."""
    src = tmp_path / "source"
    src.mkdir()

    # Financial data files — must be KEPT
    financial = {
        "accounts.json": {"items": [{"id": "acc1", "name": "Checking"}]},
        "transactions.json": {"items": [{"id": "t1", "amount": 100}]},
        "profile.json": {"base_currency": "USD"},
    }
    for name, data in financial.items():
        (src / name).write_text(json.dumps(data))

    # Runtime files that must be DROPPED
    (src / "cost_log.json").write_text(json.dumps({"entries": []}))
    (src / "connector_sessions.json").write_text(json.dumps({}))
    (src / "import_batches.json").write_text(json.dumps({}))
    (src / "auth.json").write_text(json.dumps({"password_hash": "hashed"}))

    # Runtime dirs that must be DROPPED
    sessions_dir = src / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "abc123.json").write_text(
        json.dumps(
            {
                "id": "abc123",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "The 'openai' package is required...",
                    }
                ],
            }
        )
    )

    checkpoints_dir = src / "checkpoints"
    checkpoints_dir.mkdir()
    (checkpoints_dir / "index.json").write_text(json.dumps({}))

    backups_dir = src / "backups"
    backups_dir.mkdir()
    (backups_dir / "pre-migration-2026-01-01").mkdir()

    # 2026-08-22: statements_parsed/ is vestigial post-revision-0046 (the live sidecar
    # moved to files/statements/<id>.parsed.json) — only stale *.migrated-<ts> trail
    # copies remain here, dead weight that must not be duplicated into a QA clone.
    statements_parsed_dir = src / "statements_parsed"
    statements_parsed_dir.mkdir()
    (statements_parsed_dir / "stmt_abc.json.migrated-20260822T000000Z").write_text(
        json.dumps({"rows": []})
    )

    # 2026-08-21: statements/ (like artifacts/attachments/receipts) moved under files/ —
    # see docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md. attachments/ is
    # a sibling under files/ that must be KEPT (only statements/ is transient upload cache),
    # proving clone()'s "files" special-case drops selectively, not the whole directory.
    # 2026-08-22 (consolidate-derived-data-storage): statements/ itself is now PARTIALLY
    # kept too — the raw uploaded file is still dropped, but each row's `<id>.parsed.json`
    # (the extracted transaction facts, formerly in the separate, always-kept
    # statements_parsed/ dir) now lives right next to it and must survive the clone.
    files_dir = src / "files"
    files_dir.mkdir()
    statements_dir = files_dir / "statements"
    statements_dir.mkdir()
    (statements_dir / "upload-abc.csv").write_text("date,amount\n2026-01-01,100")
    (statements_dir / "upload-abc.parsed.json").write_text(json.dumps({"rows": []}))
    attachments_dir = files_dir / "attachments"
    attachments_dir.mkdir()
    (attachments_dir / "att_abc.jpg").write_bytes(b"fake-jpeg-bytes")
    (attachments_dir / "att_abc.parsed.json").write_text(json.dumps({"kind": "text"}))

    # A non-session dir that must be KEPT
    examples_dir = src / "examples"
    examples_dir.mkdir()
    (examples_dir / "accounts.json").write_text(json.dumps({"items": []}))

    return src


def test_sessions_dir_excluded(tmp_path: Path) -> None:
    """The sessions/ dir (stale chat conversations) must not appear in the clone."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert not (dest / "sessions").exists(), (
        "sessions/ should be excluded from QA clone to prevent stale conversations"
    )


def test_checkpoints_dir_excluded(tmp_path: Path) -> None:
    """checkpoints/ is runtime-only and must not appear in the clone."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert not (dest / "checkpoints").exists()


def test_statements_parsed_dir_excluded(tmp_path: Path) -> None:
    """statements_parsed/ is vestigial post-0046 (only stale migration-trail copies) —
    the live content already survives the clone once, correctly, under
    files/statements/<id>.parsed.json (see test_statements_dir_excluded)."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert not (dest / "statements_parsed").exists()


def test_backups_dir_excluded(tmp_path: Path) -> None:
    """backups/ must not appear in the clone."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert not (dest / "backups").exists()


def test_statements_dir_excluded(tmp_path: Path) -> None:
    """files/statements/'s raw uploaded files (transient upload cache) must not appear in
    the clone, but each row's `.parsed.json` sidecar (the extracted transaction facts,
    useful for QA decision-review scenarios) must survive — proving the drop is a
    selective, per-file filter within files/statements/, not a whole-subdirectory skip.
    files/attachments/ (real, wanted chat-attachment data, both halves) must still be
    kept in full, proving the partial filter is statements-specific."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert not (dest / "files" / "statements" / "upload-abc.csv").exists()
    assert (dest / "files" / "statements" / "upload-abc.parsed.json").exists()
    assert (dest / "files" / "attachments" / "att_abc.jpg").exists()
    assert (dest / "files" / "attachments" / "att_abc.parsed.json").exists()


def test_runtime_files_excluded(tmp_path: Path) -> None:
    """cost_log.json, connector_sessions.json, import_batches.json must be dropped."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    for name in ("cost_log.json", "connector_sessions.json", "import_batches.json"):
        assert not (dest / name).exists(), f"{name} should be excluded from QA clone"


def test_financial_data_kept(tmp_path: Path) -> None:
    """Financial JSON files (accounts, transactions, profile) must survive the clone."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    for name in ("accounts.json", "transactions.json", "profile.json"):
        assert (dest / name).exists(), f"{name} should be present in QA clone"
        data = json.loads((dest / name).read_text())
        assert data  # non-empty


def test_non_session_dir_kept(tmp_path: Path) -> None:
    """examples/ (financial seed data) must be preserved in the clone."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    assert (dest / "examples").is_dir()
    assert (dest / "examples" / "accounts.json").exists()


def test_drop_dirs_constant_contains_sessions() -> None:
    """_DROP_DIRS must include 'sessions' — the direct cause of CH-04 failures."""
    assert "sessions" in _DROP_DIRS


def test_drop_dirs_contains_checkpoints_and_backups() -> None:
    """_DROP_DIRS must also cover checkpoints and backups."""
    assert "checkpoints" in _DROP_DIRS
    assert "backups" in _DROP_DIRS


def test_secret_scrubbing_still_works(tmp_path: Path) -> None:
    """Secret-scrubbing behavior must be intact after the exclusion changes."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    clone(src, dest)
    # profile.json has no secrets — must survive untouched
    profile = json.loads((dest / "profile.json").read_text())
    assert profile.get("base_currency") == "USD"


def test_sqlite_clone_keeps_financial_rows_but_strips_secret_and_runtime_tables(
    tmp_path: Path,
) -> None:
    """The DB-backed cutover must not make a QA clone carry credentials or permissions.

    ``tool_permissions`` stands in for "a live approval/runtime table that must be
    stripped" — the role a synthetic ``tool_grants`` table used to play here before that
    store was absorbed into ``tool_permissions`` and deleted (2026-08-14 one-permission-
    store merge). ``tool_permissions`` is a REAL member of
    ``core.sensitive.NON_RESTORABLE_TABLES`` (unlike the now entirely-retired
    ``tool_grants``, which ``clone()`` would no longer have any reason to strip), so this
    keeps proving the actual stripping mechanism against a table that really exists.
    """
    src = _make_source(tmp_path)
    live_db = src / "onw.db"
    sentinel = "cg-demo-secret-sentinel"
    with sqlite3.connect(live_db) as conn:
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO accounts VALUES ('acc-db', 'Brokerage')")
        conn.execute("CREATE TABLE secret_config (namespace TEXT, payload TEXT)")
        conn.execute("INSERT INTO secret_config VALUES ('market_data', ?)", (sentinel,))
        conn.execute("CREATE TABLE tool_permissions (id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO tool_permissions VALUES ('core:send_email', 'live approval')")

    dest = tmp_path / "dest"
    clone(src, dest)

    cloned_db = dest / "onw.db"
    with sqlite3.connect(cloned_db) as conn:
        assert conn.execute("SELECT name FROM accounts").fetchall() == [("Brokerage",)]
        assert conn.execute("SELECT * FROM secret_config").fetchall() == []
        assert conn.execute("SELECT * FROM tool_permissions").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert sentinel.encode() not in cloned_db.read_bytes()


def test_clone_drops_migrated_secret_files_using_central_policy(tmp_path: Path) -> None:
    """A migrated bearer credential must not evade key-name-based JSON scrubbing."""
    src = _make_source(tmp_path)
    migrated = src / "simplefin_config.json.migrated-20260812T120000Z"
    migrated.write_text(json.dumps({"access_url": "https://token@example.test/claim"}))

    dest = tmp_path / "dest"
    clone(src, dest)

    assert not (dest / migrated.name).exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_clone_is_owner_only_on_disk(tmp_path: Path) -> None:
    """A local QA snapshot contains the full financial book and cannot be world-readable."""
    src = _make_source(tmp_path)
    with sqlite3.connect(src / "onw.db") as conn:
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO accounts VALUES ('acc-db')")

    dest = tmp_path / "dest"
    clone(src, dest)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    assert stat.S_IMODE((dest / "onw.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((dest / "accounts.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((dest / "examples").stat().st_mode) == 0o700


def test_failed_sqlite_sanitisation_publishes_no_clone(tmp_path: Path) -> None:
    """A purge failure must not strand a readable raw DB or a partial destination."""
    src = _make_source(tmp_path)
    sentinel = "must-not-survive-failed-clone"
    with sqlite3.connect(src / "onw.db") as conn:
        conn.execute("CREATE TABLE secret_config (namespace TEXT, payload TEXT)")
        conn.execute("INSERT INTO secret_config VALUES ('market_data', ?)", (sentinel,))
        conn.execute(
            """CREATE TRIGGER reject_secret_purge
            BEFORE DELETE ON secret_config
            BEGIN SELECT RAISE(ABORT, 'purge rejected'); END"""
        )

    dest = tmp_path / "dest"
    try:
        clone(src, dest)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("failure injection must abort the QA clone")

    assert not dest.exists()
    assert not any(tmp_path.glob(".dest-building-*"))


def test_clone_rejects_source_as_destination_without_deleting_it(tmp_path: Path) -> None:
    """A caller typo must never turn the clone's destination cleanup into data loss."""
    src = _make_source(tmp_path)

    try:
        clone(src, src)
    except SystemExit as error:
        assert "overlaps source" in str(error)
    else:
        raise AssertionError("source=destination must be refused")

    assert src.is_dir()
    assert (src / "accounts.json").is_file()


def test_clone_rejects_destination_ancestor_of_source(tmp_path: Path) -> None:
    """Clearing an ancestor destination would recursively delete the live source."""
    container = tmp_path / "container"
    container.mkdir()
    src = _make_source(container)

    try:
        clone(src, container)
    except SystemExit as error:
        assert "overlaps source" in str(error)
    else:
        raise AssertionError("a destination above source must be refused")

    assert (src / "transactions.json").is_file()


def test_clone_rejects_destination_inside_source(tmp_path: Path) -> None:
    """A clone must not recursively copy its own destination back into itself."""
    src = _make_source(tmp_path)
    dest = src / "nested-clone"

    try:
        clone(src, dest)
    except SystemExit as error:
        assert "overlaps source" in str(error)
    else:
        raise AssertionError("a destination below source must be refused")

    assert not dest.exists()
    assert (src / "profile.json").is_file()
