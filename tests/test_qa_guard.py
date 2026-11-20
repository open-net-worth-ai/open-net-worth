"""Regression tests for the ONW_QA_MODE data-safety guard.

Root-cause: a QA run wrote a test transaction ("QA Test Coffee") into the
owner's real ledger because the QA server was booted without DATA_DIR being
overridden. The guard implemented here makes this structurally impossible:
when ONW_QA_MODE=1, the app refuses to start / import if DATA_DIR resolves to
the real owner dir or the repo seed dir.

Test groups:
  1. assert_qa_data_dir_safe() — unit-tests for the guard function itself.
  1b. assert_qa_cache_dir_safe() / assert_qa_log_dir_safe() — the CACHE_DIR/LOGS_DIR
      guards: .env points all three runtime dirs at the live ~/.open-net-worth tree,
      so a QA boot that isolates only DATA_DIR would leak QA cache/logs into the live
      instance. These guards make that fail-closed.
  1c. assert_qa_database_url_safe() — the ONW_DATABASE_URL guard: the database
      location can be overridden independently of DATA_DIR, so the three dir
      guards above cannot catch a QA boot whose ONW_DATABASE_URL still points at
      the live sqlite file. Plain string parsing only (core/ is sqlalchemy-free).
  1d. import backend.core.paths — proves the guards are WIRED at import time, not
      just callable (a live CACHE_DIR/LOGS_DIR/ONW_DATABASE_URL under ONW_QA_MODE
      aborts the import).
  2. clone_data_for_qa._assert_dest_is_temp() — unit-tests for the clone guard.
  3. boot_app.sh — bash -n syntax check.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

# The two protected dirs (match what core/config.py defines).
_HOME_REAL = Path.home() / ".open-net-worth" / "data"
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
_REPO_SEED = _BACKEND / "data"


# ── 1. assert_qa_data_dir_safe() ─────────────────────────────────────────────


def _run_guard(data_dir: Path, qa_mode: bool) -> tuple[bool, str]:
    """Run assert_qa_data_dir_safe in a subprocess (avoids mutating Settings singleton)."""
    script = (
        "import sys, os\n"
        f"os.environ['DATA_DIR'] = {str(data_dir)!r}\n"
        f"os.environ['ONW_QA_MODE'] = {'1' if qa_mode else '0'!r}\n"
        # Force fresh singleton so the env overrides take effect.
        "import importlib\n"
        "import backend.core.config as cfg\n"
        "cfg._settings = None\n"
        "from backend.core.config import assert_qa_data_dir_safe\n"
        f"assert_qa_data_dir_safe(cfg._env_path('DATA_DIR', cfg._DEFAULT_DATA_DIR))\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0
    output = result.stdout + result.stderr
    return ok, output


def test_guard_allows_temp_dir_in_qa_mode(tmp_path):
    """A fresh tmp_path is allowed when ONW_QA_MODE=1."""
    ok, output = _run_guard(tmp_path, qa_mode=True)
    assert ok, f"Expected guard to allow tmp_path but it rejected it:\n{output}"
    assert "OK" in output


def test_guard_rejects_real_home_dir_in_qa_mode():
    """Refuse ~/ .open-net-worth/data when ONW_QA_MODE=1."""
    ok, output = _run_guard(_HOME_REAL, qa_mode=True)
    assert not ok, "Expected guard to reject real home dir but it allowed it"
    assert "ONW_QA_MODE=1" in output or "REAL" in output or "protected" in output.lower()


def test_guard_rejects_repo_seed_in_qa_mode():
    """Refuse backend/data (the in-repo seed) when ONW_QA_MODE=1."""
    ok, output = _run_guard(_REPO_SEED, qa_mode=True)
    assert not ok, "Expected guard to reject repo seed dir but it allowed it"


def test_guard_noop_without_qa_mode():
    """Without ONW_QA_MODE the guard is a no-op — production boots unaffected."""
    # Even the real home dir should be allowed when ONW_QA_MODE is not set.
    ok, output = _run_guard(_HOME_REAL, qa_mode=False)
    assert ok, f"Guard should be a no-op in non-QA mode:\n{output}"


def test_guard_rejects_subdir_of_home_onw_in_qa_mode(tmp_path):
    """Paths inside ~/.open-net-worth/ are also rejected when ONW_QA_MODE=1."""
    # We construct a fake subdir path; the guard checks the string prefix,
    # so the directory doesn't need to exist.
    fake_subdir = Path.home() / ".open-net-worth" / "data" / "subdir"
    ok, output = _run_guard(fake_subdir, qa_mode=True)
    assert not ok, "Expected guard to reject a subdir of ~/.open-net-worth/"


# ── 1b. assert_qa_cache_dir_safe() / assert_qa_log_dir_safe() ────────────────

_HOME_CACHE = Path.home() / ".open-net-worth" / "cache"
_HOME_LOGS = Path.home() / ".open-net-worth" / "logs"


def _run_dir_guard(func: str, dir_path: Path, qa_mode: bool) -> tuple[bool, str]:
    """Run a cache/log dir guard in a subprocess against a fresh Settings singleton."""
    script = (
        "import os\n"
        f"os.environ['ONW_QA_MODE'] = {'1' if qa_mode else '0'!r}\n"
        "import backend.core.config as cfg\n"
        "cfg._settings = None\n"  # force re-read of ONW_QA_MODE
        f"from backend.core.config import {func}\n"
        "from pathlib import Path\n"
        f"{func}(Path({str(dir_path)!r}))\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def test_cache_guard_rejects_live_cache_in_qa_mode():
    ok, out = _run_dir_guard("assert_qa_cache_dir_safe", _HOME_CACHE, qa_mode=True)
    assert not ok, "cache guard must reject ~/.open-net-worth/cache under ONW_QA_MODE"
    assert "CACHE_DIR" in out


def test_cache_guard_allows_temp_in_qa_mode(tmp_path):
    ok, out = _run_dir_guard("assert_qa_cache_dir_safe", tmp_path / "cache", qa_mode=True)
    assert ok, f"cache guard must allow a tmp dir:\n{out}"


def test_cache_guard_noop_without_qa_mode():
    ok, _ = _run_dir_guard("assert_qa_cache_dir_safe", _HOME_CACHE, qa_mode=False)
    assert ok, "cache guard must be a no-op outside QA mode"


def test_log_guard_rejects_live_logs_in_qa_mode():
    ok, out = _run_dir_guard("assert_qa_log_dir_safe", _HOME_LOGS, qa_mode=True)
    assert not ok, "log guard must reject ~/.open-net-worth/logs under ONW_QA_MODE"
    assert "LOGS_DIR" in out


def test_log_guard_allows_temp_in_qa_mode(tmp_path):
    ok, out = _run_dir_guard("assert_qa_log_dir_safe", tmp_path / "logs", qa_mode=True)
    assert ok, f"log guard must allow a tmp dir:\n{out}"


def test_log_guard_noop_without_qa_mode():
    ok, _ = _run_dir_guard("assert_qa_log_dir_safe", _HOME_LOGS, qa_mode=False)
    assert ok, "log guard must be a no-op outside QA mode"


# ── 1c. assert_qa_database_url_safe() ────────────────────────────────────────
# The three dir guards above cannot catch ONW_DATABASE_URL: the database
# location can be overridden independently of DATA_DIR, so a QA boot that
# isolates DATA_DIR/CACHE_DIR/LOGS_DIR but leaves ONW_DATABASE_URL pointing at
# the live sqlite file would still write QA rows into the operator's real
# database (storage plan 1 final-review CRITICAL/IMPORTANT #3).

_HOME_DB_URL = f"sqlite:///{Path.home()}/.open-net-worth/data/onw.db"


def _run_url_guard(database_url, qa_mode: bool) -> tuple[bool, str]:
    """Run assert_qa_database_url_safe in a subprocess against a fresh Settings
    singleton — mirrors _run_dir_guard above."""
    script = (
        "import os\n"
        f"os.environ['ONW_QA_MODE'] = {'1' if qa_mode else '0'!r}\n"
        "import backend.core.config as cfg\n"
        "cfg._settings = None\n"  # force re-read of ONW_QA_MODE
        "from backend.core.config import assert_qa_database_url_safe\n"
        f"assert_qa_database_url_safe({database_url!r})\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def test_database_url_guard_rejects_live_db_in_qa_mode():
    ok, out = _run_url_guard(_HOME_DB_URL, qa_mode=True)
    assert not ok, "database URL guard must reject the live onw.db under ONW_QA_MODE"
    assert "ONW_DATABASE_URL" in out


def test_database_url_guard_allows_tmp_sqlite_url_in_qa_mode(tmp_path):
    ok, out = _run_url_guard(f"sqlite:///{tmp_path}/onw.db", qa_mode=True)
    assert ok, f"database URL guard must allow a tmp sqlite path:\n{out}"


def test_database_url_guard_noop_without_qa_mode():
    ok, _ = _run_url_guard(_HOME_DB_URL, qa_mode=False)
    assert ok, "database URL guard must be a no-op outside QA mode"


def test_database_url_guard_allows_non_sqlite_url_in_qa_mode():
    """A deliberately provisioned QA Postgres server is legitimate — path
    containment cannot judge it, so non-sqlite URLs pass regardless of where
    they point."""
    ok, out = _run_url_guard("postgresql+psycopg://onw:onw@localhost:55432/onw_test", qa_mode=True)
    assert ok, f"database URL guard must allow a non-sqlite URL:\n{out}"


def test_database_url_guard_rejects_tilde_form_live_db_in_qa_mode():
    """Plan 1 parked residual: an unexpanded ``~/...`` form (e.g. typed straight into a
    ``.env``, never passed through a shell that would expand it) must be caught too.
    ``Path.resolve()`` alone does NOT expand ``~`` — it resolves relative to the CWD, so
    ``sqlite:///~/.open-net-worth/data/onw.db`` silently resolved to something like
    ``<cwd>/~/.open-net-worth/...`` and sailed past the containment check. The fix inserts
    ``.expanduser()`` before that check, mirroring how ``_env_path`` already expands
    DATA_DIR/CACHE_DIR/LOGS_DIR for the sibling dir-guards."""
    ok, out = _run_url_guard("sqlite:///~/.open-net-worth/data/onw.db", qa_mode=True)
    assert not ok, "database URL guard must reject the tilde-form live db path under ONW_QA_MODE"
    assert "ONW_DATABASE_URL" in out


# ── 1d. import-time wiring (paths.py) ────────────────────────────────────────


def test_paths_import_aborts_on_live_cache_dir_in_qa_mode(tmp_path):
    """`import backend.core.paths` must abort if CACHE_DIR is the live tree (QA mode).

    DATA_DIR + LOGS_DIR are safe tmp dirs, so the abort can only come from the
    CACHE_DIR guard — proving it is wired at import time, not merely callable.
    """
    script = (
        "import os\n"
        f"os.environ['DATA_DIR'] = {str(tmp_path / 'data')!r}\n"
        f"os.environ['CACHE_DIR'] = {str(_HOME_CACHE)!r}\n"
        f"os.environ['LOGS_DIR'] = {str(tmp_path / 'logs')!r}\n"
        "os.environ['ONW_QA_MODE'] = '1'\n"
        "import backend.core.paths\n"
        "print('IMPORTED')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode != 0, "paths import must abort on a live CACHE_DIR under QA mode"
    assert "IMPORTED" not in result.stdout
    assert "CACHE_DIR" in (result.stdout + result.stderr)


def test_paths_import_aborts_on_live_logs_dir_in_qa_mode(tmp_path):
    """`import backend.core.paths` must abort if LOGS_DIR is the live tree (QA mode)."""
    script = (
        "import os\n"
        f"os.environ['DATA_DIR'] = {str(tmp_path / 'data')!r}\n"
        f"os.environ['CACHE_DIR'] = {str(tmp_path / 'cache')!r}\n"
        f"os.environ['LOGS_DIR'] = {str(_HOME_LOGS)!r}\n"
        "os.environ['ONW_QA_MODE'] = '1'\n"
        "import backend.core.paths\n"
        "print('IMPORTED')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode != 0, "paths import must abort on a live LOGS_DIR under QA mode"
    assert "IMPORTED" not in result.stdout
    assert "LOGS_DIR" in (result.stdout + result.stderr)


def test_paths_import_aborts_on_live_database_url_in_qa_mode(tmp_path):
    """`import backend.core.paths` must abort if ONW_DATABASE_URL is a sqlite
    path inside the live tree (QA mode). DATA_DIR/CACHE_DIR/LOGS_DIR are safe
    tmp dirs, so the abort can only come from the ONW_DATABASE_URL guard —
    proving it is wired at import time, not merely callable.
    """
    script = (
        "import os\n"
        f"os.environ['DATA_DIR'] = {str(tmp_path / 'data')!r}\n"
        f"os.environ['CACHE_DIR'] = {str(tmp_path / 'cache')!r}\n"
        f"os.environ['LOGS_DIR'] = {str(tmp_path / 'logs')!r}\n"
        f"os.environ['ONW_DATABASE_URL'] = {_HOME_DB_URL!r}\n"
        "os.environ['ONW_QA_MODE'] = '1'\n"
        "import backend.core.paths\n"
        "print('IMPORTED')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode != 0, (
        "paths import must abort on a live ONW_DATABASE_URL under QA mode"
    )
    assert "IMPORTED" not in result.stdout
    assert "ONW_DATABASE_URL" in (result.stdout + result.stderr)


# ── 2. clone_data_for_qa._assert_dest_is_temp() ──────────────────────────────


def test_clone_guard_allows_temp_dir(tmp_path):
    """clone guard allows a fresh tmp_path."""
    from backend.scripts.clone_data_for_qa import _assert_dest_is_temp

    # Should not raise.
    _assert_dest_is_temp(tmp_path / "qa-clone")


def test_clone_guard_rejects_real_home_dir():
    """clone guard refuses to clone into ~/ .open-net-worth/data."""
    from backend.scripts.clone_data_for_qa import _assert_dest_is_temp

    with pytest.raises(SystemExit) as exc_info:
        _assert_dest_is_temp(_HOME_REAL)
    assert exc_info.value.code != 0 or str(exc_info.value)


def test_clone_guard_rejects_repo_seed():
    """clone guard refuses to clone into backend/data."""
    from backend.scripts.clone_data_for_qa import _assert_dest_is_temp

    with pytest.raises(SystemExit):
        _assert_dest_is_temp(_REPO_SEED)


def test_clone_guard_rejects_subdir_of_home_onw():
    """clone guard refuses a subdir of ~/.open-net-worth/."""
    from backend.scripts.clone_data_for_qa import _assert_dest_is_temp

    fake_subdir = Path.home() / ".open-net-worth" / "data" / "subclone"
    with pytest.raises(SystemExit):
        _assert_dest_is_temp(fake_subdir)


# ── 2b. clone_data_for_qa.seed_fx_cache() ────────────────────────────────────


def test_seed_fx_cache_writes_the_rates_into_the_clone_cache_dir(tmp_path, monkeypatch):
    """`make qa-e2e`/`make qa-manual` boot the clone with `CACHE_DIR=<dest>-cache`. Empty, that
    leaves `to_base` with no rates, passing every non-base amount through 1:1 — so every QA cash
    figure reads inflated, a harness artefact that looks exactly like a money bug."""
    from backend.scripts import clone_data_for_qa as svc

    live_cache = tmp_path / "live-cache"
    live_cache.mkdir()
    (live_cache / "fx_cache.json").write_text('{"SGD": 1.0, "USD": 0.74}')
    monkeypatch.setitem(sys.modules, "backend.core.paths", _FakePaths(live_cache))

    dest = tmp_path / "onw-qa-data-abc"
    dest.mkdir()
    written = svc.seed_fx_cache(dest)

    assert written is not None
    seeded = tmp_path / "onw-qa-data-abc-cache" / "fx_cache.json"
    assert seeded.is_file()
    assert '"USD": 0.74' in seeded.read_text()


def test_seed_fx_cache_is_a_no_op_when_the_live_cache_has_no_rates(tmp_path, monkeypatch):
    """A fresh install has never fetched FX. Seeding must stay silent, not create an empty
    file the app would then read as "no rates known, and that is authoritative"."""
    from backend.scripts import clone_data_for_qa as svc

    live_cache = tmp_path / "live-cache"
    live_cache.mkdir()
    monkeypatch.setitem(sys.modules, "backend.core.paths", _FakePaths(live_cache))

    dest = tmp_path / "onw-qa-data-xyz"
    dest.mkdir()
    assert svc.seed_fx_cache(dest) is None
    assert not (tmp_path / "onw-qa-data-xyz-cache").exists()


class _FakePaths:
    """Stands in for `backend.core.paths` so the helper's own import resolves to a tmp
    CACHE_DIR — importing the real module would resolve (and create) the LIVE one."""

    def __init__(self, cache_dir: Path):
        self.CACHE_DIR = cache_dir


# ── 3. boot_app.sh — bash syntax check ───────────────────────────────────────


@pytest.mark.skipif(
    os.name == "nt",
    reason="boot_app.sh is a bash QA script; on a Windows PATH `bash` is the WSL stub",
)
def test_boot_app_sh_bash_syntax():
    """boot_app.sh passes `bash -n` (no syntax errors)."""
    boot_script = Path(__file__).resolve().parent.parent / "qa" / "browser-use" / "boot_app.sh"
    if not boot_script.exists():
        pytest.skip("boot_app.sh not present in this tree")
    result = subprocess.run(
        ["bash", "-n", str(boot_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed on boot_app.sh:\n{result.stdout}\n{result.stderr}"
    )
