"""DATA SAFETY preamble — import this BEFORE any ``backend.*`` module.

``backend/core/paths.py`` resolves the data folders from the environment exactly ONCE, at import
time. Naming a throwaway ``ONW_ROOT`` and setting ``ONW_QA_MODE=1`` first makes the backend
hard-abort if anything still resolves into the live ``~/.open-net-worth`` tree (CLAUDE.md: "A
scratch run isolates with ONW_ROOT and ONW_QA_MODE=1"). No server is started, so no port is used.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_LIVE_MARKER = "/.open-net-worth"
_SCRUBBED = (
    "DATA_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "BACKUPS_DIR",
    "ONW_DATABASE_URL",
    "DEMO_MODE",
    "DEMO_OPEN_ACCESS",
    "AI_MEMORY_MODEL",
)


def _isolate() -> Path:
    if "backend.core.paths" in sys.modules:
        raise SystemExit(
            "REFUSING to run: backend.core.paths was imported before the memory eval isolated "
            "its data root. Start it with `python3 -m backend.evals.memory`."
        )
    for var in _SCRUBBED:
        os.environ.pop(var, None)
    root = Path(tempfile.mkdtemp(prefix="onw-memory-eval-")).resolve()
    os.environ["ONW_ROOT"] = str(root)
    os.environ["ONW_QA_MODE"] = "1"
    os.environ["AUTH_DISABLED"] = "true"
    os.environ["ONW_DISABLE_TELEGRAM"] = "1"
    os.environ["ONW_CLAUDE_CLI_SESSIONS"] = "0"
    if _LIVE_MARKER in str(root):
        raise SystemExit(f"REFUSING to run: scratch root {root} is inside the live instance tree")
    return root


EVAL_ROOT: Path = _isolate()
KEEP = False


def _cleanup() -> None:
    if not KEEP:
        shutil.rmtree(EVAL_ROOT, ignore_errors=True)


atexit.register(_cleanup)


def assert_isolated() -> None:
    """Called once the backend is imported: every folder it resolved must be under the scratch
    root. Anything else aborts the run before a single row is written."""
    from backend.core import paths

    for name in ("DATA_DIR", "CACHE_DIR"):
        folder = Path(getattr(paths, name)).resolve()
        if EVAL_ROOT != folder and EVAL_ROOT not in folder.parents:
            raise SystemExit(f"REFUSING to run: {name}={folder} is outside the scratch root")
        if _LIVE_MARKER in str(folder):
            raise SystemExit(f"REFUSING to run: {name}={folder} points into the live tree")
    db = Path(paths.DB_FILE).resolve()
    if EVAL_ROOT not in db.parents:
        raise SystemExit(f"REFUSING to run: the database {db} is outside the scratch root")
