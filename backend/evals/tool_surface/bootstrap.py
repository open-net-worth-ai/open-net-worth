"""DATA SAFETY preamble — import this BEFORE any ``backend.*`` module.

``backend/core/paths.py`` resolves ``DATA_DIR``/``CACHE_DIR``/``LOGS_DIR`` from the
environment exactly ONCE, at import time. So the only way to guarantee an eval run
cannot touch the operator's live ``~/.open-net-worth`` tree is to set all three
BEFORE the first backend import — the same trick ``tests/conftest.py`` and
``backend/scripts/dedup_eval.py`` use.

We additionally set ``ONW_QA_MODE=1``, which makes ``core.config.assert_qa_data_dir_safe``
(called from ``core/paths.py`` at import) hard-abort the process if any of the three
resolves into the live instance tree. That turns "we remembered to isolate" from a
convention into a structural guarantee, per the CLAUDE.md QA rules.

This module is import-order-sensitive by design. Every other module in this package
imports it first.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_LIVE_MARKER = "/.open-net-worth/"


def _isolate() -> Path:
    """Point DATA_DIR/CACHE_DIR/LOGS_DIR at a throwaway root and return it.

    ADOPTS an existing throwaway isolation rather than fighting it. Under pytest,
    ``tests/conftest.py`` has already redirected all three to its own temp root before
    any test module is imported. Overwriting those would be a process-global side effect
    of merely IMPORTING this package — ``core.paths`` freezes its constants at first
    import, so whichever ran first would silently decide the data directory for every
    other test in the session. Adopting is the composable choice; the isolation
    guarantee is identical either way, because we only adopt a path that is already
    outside the live tree (and re-assert that below).
    """
    existing = os.environ.get("ONW_EVAL_ROOT")
    adopted = _adoptable_root()
    if existing:
        root = Path(existing)
    elif adopted is not None:
        root = adopted
        os.environ["ONW_EVAL_ROOT"] = str(root)
    else:
        root = Path(tempfile.mkdtemp(prefix="onw-tool-surface-eval-"))
        os.environ["ONW_EVAL_ROOT"] = str(root)

    os.environ["ONW_QA_MODE"] = "1"
    os.environ["DATA_DIR"] = str(root / "data")
    os.environ["CACHE_DIR"] = str(root / "cache")
    os.environ["LOGS_DIR"] = str(root / "logs")
    # Never let a background scheduler/connector wake up during an eval run.
    os.environ.setdefault("ONW_DISABLE_TELEGRAM", "1")
    os.environ.setdefault("AUTH_DISABLED", "true")

    for var in ("DATA_DIR", "CACHE_DIR", "LOGS_DIR"):
        value = os.environ[var]
        if _LIVE_MARKER in value:  # pragma: no cover - defensive
            raise SystemExit(f"REFUSING to run: {var}={value} points into the live instance tree")

    for sub in ("data", "cache", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _adoptable_root() -> Path | None:
    """The parent of an already-isolated throwaway DATA_DIR, if one is set.

    Only adopts when DATA_DIR is set, is NOT in the live tree, and its parent also holds
    the sibling cache/logs dirs — i.e. it really is an isolation root of the shape
    conftest builds, not an arbitrary directory that happens to be exported.
    """
    data_dir = os.environ.get("DATA_DIR", "")
    if not data_dir or _LIVE_MARKER in data_dir:
        return None
    candidate = Path(data_dir)
    if candidate.name != "data":
        return None
    root = candidate.parent
    cache_dir = os.environ.get("CACHE_DIR", "")
    if not cache_dir or Path(cache_dir).parent != root:
        return None
    return root


EVAL_ROOT: Path = _isolate()


def reset_data_dir() -> Path:
    """Wipe and recreate DATA_DIR/CACHE_DIR in place, for a clean per-run fixture.

    Recreates the SAME paths rather than minting new ones, because ``core.paths``
    has already frozen the path constants at import — a new directory would simply
    not be read. Safe: ``_isolate`` proved these are throwaway paths.
    """
    import shutil

    from backend.db.engine import dispose_engine

    # Windows refuses to unlink a file a connection still holds open (rmtree below uses
    # ignore_errors, so the stale onw.db would silently survive with its rows).
    dispose_engine()
    for var in ("DATA_DIR", "CACHE_DIR"):
        path = Path(os.environ[var])
        if _LIVE_MARKER in str(path):  # pragma: no cover - defensive, belt and braces
            raise SystemExit(f"REFUSING to wipe {var}={path}")
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
    _remigrate_sqlite_db()
    return EVAL_ROOT


def _remigrate_sqlite_db() -> None:
    """The rmtree above just deleted DATA_DIR/onw.db (when this eval root ADOPTED the
    pytest root conftest's shared DATA_DIR, per ``_adoptable_root`` — the common case
    under pytest) along with every JSON store. A missing JSON store FILE reads back as
    empty for free; a missing sqlite TABLE is a hard error on first query — mirrors
    tests/conftest.py's own ``_migrate_default_evidence_db`` session fixture, which
    migrates this exact shared path once at session start. Re-running the migration
    here restores the same "missing = empty" default after THIS wipe, not just the
    session's first one.

    ``dispose_engine()`` first: a connection opened before the rmtree keeps working
    against the now-unlinked inode on POSIX (an open fd survives unlink() — the
    process still holds the old file open even though its directory entry is gone),
    which would silently mask a genuinely broken path via a lucky "ghost" handle
    rather than actually fixing it for whichever caller opens the NEXT connection.
    Disposing forces that next connection to resolve the CURRENT, freshly-migrated
    file. Cheap and idempotent either way — this only ever runs against the shared
    default url (this package never overrides ONW_DATABASE_URL)."""
    from alembic import command

    from backend.db.alembic_config import make_config
    from backend.db.engine import database_url, dispose_engine

    dispose_engine()
    command.upgrade(make_config(database_url()), "head")
