"""Regression test for a security-review finding (L1): the zero-config instance root
(``~/.open-net-worth``) must be created OWNER-ONLY (0700), not left at the process
umask default (typically 0755 — group/other-readable, e.g. macOS's shared "staff"
group everyone's account belongs to). Every leaf under this tree already assumes
owner-only (``onw.db`` 0600, ``files/`` 0700, ``logs/`` 0700) — the root itself was the
one gap, and this repo now creates it on every fresh install (backend/core/paths.py).

Run entirely in a subprocess against a FAKE ``$HOME`` (never the real one), so this
(a) never touches real developer state and (b) exercises the actual import-time
directory-creation code path, exactly like a genuine first boot.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _boot_against_fake_home(fake_home: Path) -> subprocess.CompletedProcess[str]:
    script = (
        "import os\n"
        "for var in ('DATA_DIR', 'CACHE_DIR', 'LOGS_DIR', 'ONW_DATABASE_URL', 'ONW_QA_MODE'):\n"
        "    os.environ.pop(var, None)\n"
        f"os.environ['HOME'] = {str(fake_home)!r}\n"
        "import backend.core.paths\n"  # the module under test — creates dirs at import time
        "print('OK')\n"
    )
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_fresh_instance_root_is_created_owner_only(tmp_path):
    fake_home = tmp_path / "fresh-home"
    fake_home.mkdir()
    root = fake_home / ".open-net-worth"
    assert not root.exists()

    result = _boot_against_fake_home(fake_home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert root.is_dir()
    mode = stat.S_IMODE(root.stat().st_mode)
    assert mode == 0o700, f"expected a fresh instance root at 0o700, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_existing_instance_root_permissions_are_left_alone(tmp_path):
    """A pre-existing root — a pre-fix install, or one an operator deliberately
    customized — must never be silently re-chmodded on a later boot."""
    fake_home = tmp_path / "existing-home"
    fake_home.mkdir()
    root = fake_home / ".open-net-worth"
    root.mkdir()
    os.chmod(root, 0o755)  # simulate a pre-fix (or deliberately widened) root

    result = _boot_against_fake_home(fake_home)
    assert result.returncode == 0, result.stdout + result.stderr
    mode = stat.S_IMODE(root.stat().st_mode)
    assert mode == 0o755, f"expected the pre-existing mode to survive untouched, got {oct(mode)}"
