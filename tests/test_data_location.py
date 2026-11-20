"""Inspecting a candidate data folder before pointing an instance at it.

The decision this serves: a folder that already holds a book is either exactly what the user
wants (adopt my existing data on a new machine) or exactly what they don't (a second app on
one folder). The app cannot tell those apart, so it reports what is there and the person
chooses. These pin that the report is honest and, critically, that LOOKING never WRITES.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from pathlib import Path

from backend.services.data_location import inspect


def _write_pointer(pointer, root):
    """The pointer is READ-ONLY to the app now, so a test that needs one writes the file."""
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"instance_root": str(root)}))


def _make_book(dirpath, accounts: int = 3, txns: int = 7, nested: bool = False):
    """`nested=True` builds the real shape: a working folder with data/ inside it."""
    if nested:
        dirpath = dirpath / "data"
        dirpath.mkdir(parents=True, exist_ok=True)
    db = dirpath / "onw.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("create table accounts (id text)")
        conn.execute("create table transactions (id text)")
        conn.executemany("insert into accounts values (?)", [(str(i),) for i in range(accounts)])
        conn.executemany("insert into transactions values (?)", [(str(i),) for i in range(txns)])
    conn.close()
    return db


def test_a_folder_holding_a_book_reports_what_is_in_it(tmp_path):
    _make_book(tmp_path, accounts=3, txns=7)
    r = inspect(str(tmp_path))
    assert r.exists and r.has_book
    assert r.accounts == 3
    assert r.transactions == 7
    assert r.last_used


def test_an_empty_folder_is_reported_as_free(tmp_path):
    r = inspect(str(tmp_path))
    assert r.exists is True
    assert r.has_book is False
    assert r.accounts is None


def test_a_folder_that_does_not_exist_yet_is_fine_if_its_parent_is_writable(tmp_path):
    r = inspect(str(tmp_path / "not-created-yet"))
    assert r.exists is False
    assert r.writable is True
    assert r.error is None


def test_an_uncreatable_path_is_reported_before_the_user_commits_to_it(tmp_path):
    blocker = tmp_path / "a-file-not-a-folder"
    blocker.write_text("x")
    r = inspect(str(blocker / "not" / "writable" / "here"))
    assert r.exists is False
    assert r.writable is False


def test_inspecting_never_creates_or_modifies_a_database(tmp_path):
    """The whole point is to LOOK. sqlite3.connect() on a missing file would CREATE it, so the
    read-only file: URI here is load-bearing, not a nicety."""
    db = _make_book(tmp_path)
    before = db.stat().st_mtime_ns

    inspect(str(tmp_path))

    assert db.stat().st_mtime_ns == before
    assert not (tmp_path / "onw.db-wal").exists()


def test_inspecting_an_empty_folder_does_not_conjure_a_database(tmp_path):
    inspect(str(tmp_path))
    # Not an emptiness check on the whole dir — a conftest fixture puts its own scratch
    # folder here. What matters is that inspecting conjured no database.
    assert not (tmp_path / "onw.db").exists()
    assert not (tmp_path / "onw.db-wal").exists()


def test_a_file_masquerading_as_the_folder_is_refused(tmp_path):
    f = tmp_path / "notadir.txt"
    f.write_text("hi")
    r = inspect(str(f))
    assert r.has_book is False
    assert r.error == "not a folder"


def test_a_corrupt_or_foreign_sqlite_file_is_not_claimed_as_a_book(tmp_path):
    """A folder can hold an onw.db that is not ours (or is damaged). Reporting it as a book
    would tell the user their data is there when it is not."""
    (tmp_path / "onw.db").write_bytes(b"this is not a database")
    r = inspect(str(tmp_path))
    assert r.has_book is False
    assert r.accounts is None


def test_the_pointer_is_read_after_env_but_before_the_default(tmp_path, monkeypatch):
    """Resolution order matters: an explicit DATA_DIR (a container, the QA harness) must still
    win over a recorded pick, or an operator's override silently stops working."""
    from backend.core import config

    pointer = tmp_path / "cfg" / "location.json"
    monkeypatch.setattr(config, "POINTER_FILE", pointer)
    _write_pointer(pointer, tmp_path / "pointed")

    monkeypatch.delenv("DATA_DIR", raising=False)
    # The pointer records the WORKING FOLDER; the data dir is data/ inside it.
    assert config._resolve_data_dir() == tmp_path / "pointed" / "data"

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "from-env"))
    assert config._resolve_data_dir() == tmp_path / "from-env"


def test_a_damaged_pointer_falls_through_instead_of_taking_the_app_down(tmp_path, monkeypatch):
    from backend.core import config

    pointer = tmp_path / "cfg" / "location.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{ not json at all")
    monkeypatch.setattr(config, "POINTER_FILE", pointer)

    assert config.read_pointer() is None


def test_a_working_folder_reports_the_book_inside_its_data_dir(tmp_path):
    """The real shape: you pick a working folder, and data/ cache/ logs/ live inside it. An
    inspector that only looked for <path>/onw.db would call a populated instance empty."""
    _make_book(tmp_path, accounts=4, txns=9, nested=True)
    r = inspect(str(tmp_path))
    assert r.has_book is True
    assert r.accounts == 4
    assert r.transactions == 9


def test_pointing_straight_at_a_data_dir_still_works(tmp_path):
    """DATA_DIR (and a pre-2026-08-23 pointer) name the data directory itself."""
    _make_book(tmp_path, accounts=2, txns=5)
    assert inspect(str(tmp_path)).accounts == 2


def test_cache_and_logs_follow_the_recorded_root(tmp_path, monkeypatch):
    """A moved instance must move WHOLE. Recording only the data half would leave cache and
    logs behind at the old default — half an instance in each place."""
    from backend.core import config

    pointer = tmp_path / "cfg" / "location.json"
    monkeypatch.setattr(config, "POINTER_FILE", pointer)
    root = tmp_path / "my-money"
    _write_pointer(pointer, root)

    for var in ("DATA_DIR", "CACHE_DIR", "LOGS_DIR"):
        monkeypatch.delenv(var, raising=False)

    assert config._resolve_data_dir() == root / "data"
    assert config._resolve_sibling("CACHE_DIR", "cache", tmp_path / "fallback") == root / "cache"
    assert config._resolve_sibling("LOGS_DIR", "logs", tmp_path / "fallback") == root / "logs"


def test_a_pre_root_pointer_is_still_understood(tmp_path, monkeypatch):
    """Pointers written before the root change recorded "<root>/data" under a `data_dir` key.
    Reading one must not strand an instance someone already moved."""
    import json

    from backend.core import config

    pointer = tmp_path / "cfg" / "location.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"data_dir": str(tmp_path / "old" / "data")}))
    monkeypatch.setattr(config, "POINTER_FILE", pointer)

    assert config.read_pointer() == tmp_path / "old"


# ── ONW_ROOT: one variable, one working folder ───────────────────────────────────────


def _clear_path_env(monkeypatch):
    for var in ("ONW_ROOT", "DATA_DIR", "CACHE_DIR", "LOGS_DIR", "BACKUPS_DIR"):
        monkeypatch.delenv(var, raising=False)


def test_one_root_env_places_all_four_folders(tmp_path, monkeypatch):
    """The whole point of the root: name one folder and the instance is complete inside it."""
    from backend.core import config

    _clear_path_env(monkeypatch)
    monkeypatch.setenv("ONW_ROOT", str(tmp_path / "money"))

    assert config._resolve_data_dir() == tmp_path / "money" / "data"
    for var, name in (("CACHE_DIR", "cache"), ("LOGS_DIR", "logs"), ("BACKUPS_DIR", "backups")):
        assert config._resolve_sibling(var, name, tmp_path / "nope") == tmp_path / "money" / name


def test_the_root_env_beats_a_recorded_pick(tmp_path, monkeypatch):
    """An operator naming a root on the command line (a container, the QA harness) must never
    be dragged onto a previously-recorded instance — that is the line between an isolated boot
    and one writing real money data."""
    from backend.core import config

    _clear_path_env(monkeypatch)
    pointer = tmp_path / "cfg" / "location.json"
    monkeypatch.setattr(config, "POINTER_FILE", pointer)
    _write_pointer(pointer, tmp_path / "recorded")
    monkeypatch.setenv("ONW_ROOT", str(tmp_path / "forced"))

    assert config._resolve_data_dir() == tmp_path / "forced" / "data"
    assert config.instance_root() == tmp_path / "forced"


def test_backups_is_a_sibling_of_data_not_a_child_of_it(tmp_path, monkeypatch):
    """A snapshot stored inside the directory it snapshots dies with that directory."""
    from backend.core import config

    _clear_path_env(monkeypatch)
    monkeypatch.setenv("ONW_ROOT", str(tmp_path / "money"))

    data = config._resolve_data_dir()
    backups = config._resolve_sibling("BACKUPS_DIR", "backups", tmp_path / "nope")

    assert backups.parent == data.parent
    assert backups not in data.parents


def test_a_legacy_boot_naming_only_data_dir_keeps_its_backups_isolated(tmp_path, monkeypatch):
    """The test harness and the QA targets set DATA_DIR/CACHE_DIR/LOGS_DIR and predate backups
    being a sibling. Falling through to the GLOBAL default would have pointed an isolated
    boot's backups at the REAL instance's folder — data loss wearing a config bug's clothes."""
    from backend.core import config

    _clear_path_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "isolated" / "data"))

    backups = config._resolve_sibling("BACKUPS_DIR", "backups", Path.home() / ".open-net-worth")

    assert backups == tmp_path / "isolated" / "backups"
    assert ".open-net-worth" not in str(backups)


def test_an_explicit_sibling_still_wins_over_inference(tmp_path, monkeypatch):
    from backend.core import config

    _clear_path_env(monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "a" / "data"))
    monkeypatch.setenv("BACKUPS_DIR", str(tmp_path / "elsewhere"))

    assert config._resolve_sibling("BACKUPS_DIR", "backups", tmp_path) == tmp_path / "elsewhere"


def test_nothing_in_the_app_writes_the_machine_wide_pointer(tmp_path):
    """The pointer is READ-ONLY to the app since 2026-08-23.

    It is machine-WIDE: every instance on the machine reads the same file. While first run
    could write it, a disposable QA instance recorded `~/.open-net-worth-3` into it, and the
    real install — whose own DATA_DIR had just been removed from .env as redundant — would
    have booted into an empty folder on its next start and looked exactly like data loss.

    The folder is now the default or ONW_ROOT. An existing pointer is still HONOURED so
    installs that recorded one keep working; nothing can create or change one.
    """
    import backend.core.config as config_mod
    import backend.services.data_location as dl

    assert not hasattr(config_mod, "write_pointer")
    assert not hasattr(dl, "choose")
    assert not hasattr(dl, "suggest_root")

    root = pathlib.Path(config_mod.__file__).resolve().parent.parent
    offenders = [
        f"{path.relative_to(root.parent)}"
        for path in root.rglob("*.py")
        if "POINTER_FILE.write" in path.read_text(encoding="utf-8", errors="ignore")
        or "POINTER_FILE.parent.mkdir" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, f"these write the machine-wide pointer: {offenders}"
