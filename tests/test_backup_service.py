"""services/backup.py — the full-data disaster-recovery backup (zipped).

Fully isolated: DATA_DIR/BACKUPS_DIR are monkeypatched into tmp_path, and the
AppConfigStore instance is separately tmp-pathed, so these never touch live data
(project rule: no destructive tests on live data).
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.db.app_config_store import BACKUP_ENABLED_FLAG, AppConfigStore
from backend.services import backup as backup_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """AppConfigStore is DB-backed now (runtime_state/"app_config") — db_isolated gives
    this test its own fresh database, so backup_service's bare ``AppConfigStore()`` call
    needs no extra redirection. ``cfg_path`` is kept (though unused) purely so every
    existing 3-tuple ``data_dir, backups_dir, cfg_path = isolated`` unpacking site below is
    unchanged."""
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    data_dir.mkdir()
    backups_dir.mkdir()
    (data_dir / "accounts.json").write_text("[]")
    (data_dir / "transactions.json").write_text("[]")

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints"})

    cfg_path = tmp_path / "app_config.json"
    return data_dir, backups_dir, cfg_path


def _name(dt: datetime) -> str:
    return dt.strftime(backup_service._TIMESTAMP_FMT) + backup_service._SUFFIX


def test_run_backup_zips_data_dir(isolated):
    data_dir, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now)

    assert result["ok"] is True
    assert result["file_count"] == 2
    dest = backups_dir / _name(now)
    assert dest.is_file()
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == ["accounts.json", "transactions.json"]
        assert zf.read("accounts.json") == b"[]"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows is the ACL")
def test_run_backup_is_owner_only_whatever_the_umask(isolated):
    """A backup carries the secret tables, so neither it nor its folder is group/other
    readable — including a folder an older version left at 0755 with zips already in it."""
    _, backups_dir, _ = isolated
    os.chmod(backups_dir, 0o755)
    old = backups_dir / _name(datetime(2026, 7, 1, tzinfo=timezone.utc))
    old.write_bytes(b"")
    os.chmod(old, 0o644)
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    assert backup_service.run_backup(now=now)["ok"] is True

    assert backups_dir.stat().st_mode & 0o777 == 0o700
    assert (backups_dir / _name(now)).stat().st_mode & 0o777 == 0o600


def test_run_backup_excludes_its_own_backups_dir(isolated):
    """A zip of DATA_DIR must never nest backups inside backups (infinite growth)."""
    data_dir, backups_dir, _ = isolated
    # A leftover file from a previous (pre-zip) backup layout, still on disk.
    (backups_dir / "stale.txt").write_text("x")
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    backup_service.run_backup(now=now)
    dest = backups_dir / _name(now)

    with zipfile.ZipFile(dest) as zf:
        assert not any(n.startswith("backups/") for n in zf.namelist())


def test_run_backup_skips_when_disabled(isolated):
    _, _, cfg_path = isolated
    AppConfigStore().set_flag(BACKUP_ENABLED_FLAG, False)

    result = backup_service.run_backup()

    assert result == {"ok": False, "skipped": "disabled"}


def test_force_runs_even_when_disabled(isolated):
    _, backups_dir, cfg_path = isolated
    AppConfigStore().set_flag(BACKUP_ENABLED_FLAG, False)
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now, force=True)

    assert result["ok"] is True
    assert (backups_dir / _name(now)).is_file()


def test_prune_removes_only_backups_older_than_retention(isolated):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=5)
    (backups_dir / _name(old)).write_text("x")
    (backups_dir / _name(recent)).write_text("x")

    removed = backup_service._prune_old_backups(retain_days=30, now=now)

    assert removed == [old.strftime(backup_service._TIMESTAMP_FMT)]
    assert (backups_dir / _name(recent)).exists()
    assert not (backups_dir / _name(old)).exists()


def test_prune_leaves_non_backup_files_alone(isolated):
    _, backups_dir, _ = isolated
    (backups_dir / "not-a-backup.txt").write_text("x")

    removed = backup_service._prune_old_backups(retain_days=1)

    assert removed == []
    assert (backups_dir / "not-a-backup.txt").exists()


def test_run_backup_prunes_old_backups_after_creating_new_one(isolated):
    _, backups_dir, _ = isolated
    old = datetime(2026, 5, 1, tzinfo=timezone.utc)
    (backups_dir / _name(old)).write_text("x")
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now)

    assert result["pruned"] == [old.strftime(backup_service._TIMESTAMP_FMT)]
    assert not (backups_dir / _name(old)).exists()


def test_run_backup_refuses_duplicate_timestamp(isolated):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
    (backups_dir / _name(now)).write_text("x")

    result = backup_service.run_backup(now=now)

    assert result == {
        "ok": False,
        "error": f"backup {now.strftime(backup_service._TIMESTAMP_FMT)} already exists",
    }


def test_list_backups_newest_first(isolated):
    _, backups_dir, _ = isolated
    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 5, tzinfo=timezone.utc)
    for ts in (older, newer):
        with zipfile.ZipFile(backups_dir / _name(ts), "w") as zf:
            zf.writestr("accounts.json", "[]")

    listed = backup_service.list_backups()

    assert [b["name"] for b in listed] == [
        newer.strftime(backup_service._TIMESTAMP_FMT),
        older.strftime(backup_service._TIMESTAMP_FMT),
    ]
    assert listed[0]["file_count"] == 1


def test_list_backups_skips_corrupt_zip(isolated):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
    (backups_dir / _name(now)).write_text("not a real zip")

    assert backup_service.list_backups() == []


# ── Legacy trail files never ride along in the nightly zip ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "auth.json.migrated-20260810T083821Z",
        "statements_parsed/foo.json.removed-20260822T000000Z",
        "email_ingest.pre-cursor-reset-20260723-185559.json",
        "accounts.json.corrupt-20260701T120000Z",
        "transactions.json.bak-20260629-120000",
        "onw.db.pre0057",
    ],
)
def test_is_legacy_trail_file_matches_every_marker_shape(name):
    assert backup_service._is_legacy_trail_file(Path(f"/data/{name}"))


@pytest.mark.parametrize(
    "name", ["accounts.json", "onw.db", "onw.db-wal", "onw.db-shm", "email_ingest.json"]
)
def test_is_legacy_trail_file_does_not_over_match(name):
    assert not backup_service._is_legacy_trail_file(Path(f"/data/{name}"))


def test_run_backup_skips_legacy_trail_files(isolated):
    data_dir, backups_dir, _ = isolated
    (data_dir / "auth.json.migrated-20260810T083821Z").write_text("{}")
    (data_dir / "accounts.json.corrupt-20260701T120000Z").write_text("junk")
    (data_dir / "onw.db.pre0057").write_text("junk")
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now)

    assert result["ok"] is True
    with zipfile.ZipFile(backups_dir / _name(now)) as zf:
        assert sorted(zf.namelist()) == ["accounts.json", "transactions.json"]


def test_run_backup_skips_retired_sessions_dir(isolated, monkeypatch):
    data_dir, backups_dir, _ = isolated
    monkeypatch.setattr(
        backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints", "sessions"}
    )
    (data_dir / "sessions").mkdir()
    (data_dir / "sessions" / "abc123.json").write_text("{}")
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now)

    assert result["ok"] is True
    with zipfile.ZipFile(backups_dir / _name(now)) as zf:
        assert sorted(zf.namelist()) == ["accounts.json", "transactions.json"]


# ── Atomic write (.partial -> rename) ────────────────────────────────────────────────────


def test_run_backup_leaves_no_partial_file_behind_on_success(isolated):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    result = backup_service.run_backup(now=now)

    assert result["ok"] is True
    assert not list(backups_dir.glob("*.partial"))
    assert (backups_dir / _name(now)).is_file()


def test_run_backup_removes_its_own_partial_on_failure(isolated, monkeypatch):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)

    def _boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup_service, "_zip_live_db", _boom)

    result = backup_service.run_backup(now=now)

    assert result["ok"] is False
    assert not list(backups_dir.glob("*.partial"))
    assert not (backups_dir / _name(now)).exists()


# ── Sweeping stale tmp*/*.partial orphans left by a killed run ──────────────────────────


def test_run_backup_sweeps_stale_orphans_but_not_fresh_ones(isolated):
    _, backups_dir, _ = isolated
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
    stale_ts = now.timestamp() - backup_service._STALE_TEMP_AGE_S - 60
    fresh_ts = now.timestamp() - 5

    stale_dir = backups_dir / "tmpABCDEF12"
    stale_dir.mkdir()
    (stale_dir / "onw.db").write_text("orphaned snapshot")
    stale_partial = backups_dir / "20260101T000000Z.zip.partial"
    stale_partial.write_text("truncated")
    os.utime(stale_dir, (stale_ts, stale_ts))
    os.utime(stale_partial, (stale_ts, stale_ts))

    fresh_dir = backups_dir / "tmpFRESH999"
    fresh_dir.mkdir()
    fresh_partial = backups_dir / "20260706T032900Z.zip.partial"
    fresh_partial.write_text("still writing")
    os.utime(fresh_dir, (fresh_ts, fresh_ts))
    os.utime(fresh_partial, (fresh_ts, fresh_ts))

    result = backup_service.run_backup(now=now)

    assert result["ok"] is True
    assert set(result["swept"]) == {stale_dir.name, stale_partial.name}
    assert not stale_dir.exists()
    assert not stale_partial.exists()
    assert fresh_dir.exists()
    assert fresh_partial.exists()


def test_sweep_stale_temp_is_a_noop_when_backups_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", tmp_path / "does-not-exist")
    assert backup_service._sweep_stale_temp() == []
