"""DATA SAFETY preamble — call ``isolate`` BEFORE any other ``backend`` import.

``backend/core/paths.py`` resolves the data folders once, at import. Synthetic runs get a fresh
throwaway ``ONW_ROOT``; history runs point at a QA CLONE of the owner's book (``make qa-data`` /
``backend.scripts.clone_data_for_qa``) — never the live tree, which ``ONW_QA_MODE=1`` makes the
backend refuse on its own as well.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_LIVE_MARKER = "/.open-net-worth"
_SCRUBBED = (
    "DATA_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "BACKUPS_DIR",
    "ONW_ROOT",
    "ONW_DATABASE_URL",
    "DEMO_MODE",
    "DEMO_OPEN_ACCESS",
)


def isolate(history_dir: str | None) -> Path:
    """Point the backend at a scratch book (or the given clone) and return its data folder."""
    if "backend.core.paths" in sys.modules:
        raise SystemExit("REFUSING to run: start it with `python3 -m backend.evals.delivery`.")
    for var in _SCRUBBED:
        os.environ.pop(var, None)
    os.environ.update(
        ONW_QA_MODE="1", AUTH_DISABLED="true", ONW_DISABLE_TELEGRAM="1", ONW_CLAUDE_CLI_SESSIONS="0"
    )
    if history_dir:
        data = Path(history_dir).resolve()
        if _LIVE_MARKER in str(data) or not (data / "onw.db").is_file():
            raise SystemExit(f"REFUSING to run: {data} is not a QA clone (needs onw.db, not live)")
        scratch = Path(tempfile.mkdtemp(prefix="onw-delivery-eval-")).resolve()
        # A `cache/` next to the clone is used when present — e.g. with a copied fx_cache.json,
        # so holdings in other currencies can be valued; else a throwaway one.
        cache = data.parent / "cache" if (data.parent / "cache").is_dir() else scratch / "cache"
        os.environ.update(DATA_DIR=str(data), CACHE_DIR=str(cache), LOGS_DIR=str(scratch / "logs"))
        return data
    root = Path(tempfile.mkdtemp(prefix="onw-delivery-eval-")).resolve()
    os.environ["ONW_ROOT"] = str(root)
    return root


def assert_isolated(data: Path) -> None:
    from backend.core import paths

    db = Path(paths.DB_FILE).resolve()
    if _LIVE_MARKER in str(db) or data not in db.parents:
        raise SystemExit(f"REFUSING to run: the database {db} is not the eval's own book")
