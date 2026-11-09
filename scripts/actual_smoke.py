#!/usr/bin/env python3
"""Run the live Actual suite (tests/actual_live): ``make actual-smoke``.

Opt-in, never part of ``make check``. It needs Node 22 or newer and a network connection the first
time: the pinned server and official client (``tests/actual_live/node/package-lock.json``) are
installed once into a cache folder outside the repo, ``ONW_ACTUAL_SMOKE_CACHE`` (default
``~/.cache/open-net-worth-actual-smoke``). Every budget and book is made up and thrown away with the
run. Arguments after ``--`` go to pytest, for example ``-- -k tracking``. ``--install-only`` installs
and prints the folder (the browser fixture ``qa/browser-use/fixtures/verify_actual_budget_flow.py``
uses it).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing backend.core reaches the data dirs; point them at scratch FIRST.
_SCRATCH = tempfile.mkdtemp(prefix="onw-actual-smoke-")
for _var, _sub in (("DATA_DIR", "data"), ("CACHE_DIR", "cache"), ("LOGS_DIR", "logs")):
    os.environ[_var] = f"{_SCRATCH}/{_sub}"
os.environ["ONW_QA_MODE"] = "1"

from backend.core.process_tree import kill_tree, spawn_kwargs  # noqa: E402

NODE_FILES = ROOT / "tests" / "actual_live" / "node"
CACHE = Path(
    os.environ.get("ONW_ACTUAL_SMOKE_CACHE")
    or Path.home() / ".cache" / "open-net-worth-actual-smoke"
)
INSTALL_SECONDS = 600
RUN_SECONDS = 2400
MIN_NODE = 22


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python3"
    return str(venv if venv.exists() else sys.executable)


def _check_node() -> None:
    node = shutil.which("node")
    if not node:
        raise SystemExit("actual-smoke needs Node 22 or newer on PATH.")
    version = subprocess.run(  # noqa: S603
        [node, "--version"], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < MIN_NODE:
        raise SystemExit(f"actual-smoke needs Node {MIN_NODE} or newer, this is {version}.")


def installed() -> Path:
    """The folder holding the pinned packages, installed from the lockfile the first time."""
    lock = (NODE_FILES / "package-lock.json").read_bytes()
    folder = CACHE / hashlib.sha256(lock).hexdigest()[:16]
    if (folder / ".installed").exists():
        return folder
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy(NODE_FILES / name, folder / name)
    print(f"actual-smoke: installing the pinned Actual packages into {folder}", flush=True)
    subprocess.run(  # noqa: S603
        ["npm", "ci", "--no-audit", "--no-fund"],  # noqa: S607
        cwd=folder,
        timeout=INSTALL_SECONDS,
        check=True,
    )
    (folder / ".installed").write_text("ok\n", encoding="utf-8")
    return folder


def main() -> int:
    _check_node()
    if "--install-only" in sys.argv:
        print(installed())
        return 0
    env = {**os.environ, "ONW_ACTUAL_LIVE": "1", "ONW_ACTUAL_NODE_DIR": str(installed())}
    extra = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    argv = [_python(), "-m", "pytest", "tests/actual_live", "-v", "-rfEw"]
    argv += ["--no-header", "-p", "no:cacheprovider", "--timeout=600", *extra]
    process = subprocess.Popen(argv, cwd=ROOT, env=env, **spawn_kwargs())  # noqa: S603
    try:
        return process.wait(timeout=RUN_SECONDS)
    except subprocess.TimeoutExpired:
        kill_tree(process.pid)
        process.wait()
        print(f"actual-smoke: timed out after {RUN_SECONDS}s; every process was stopped")
        return 124
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
