"""Provider import runs as persisted records (ADR
``2026-10-21-import-runs-are-persisted-records-archived-not-deleted``).

Every Import / Verify / Preview of a provider's importer is a row of ``import_runs`` from the
moment it starts: its phase and progress, a timestamped log, what it created, what needs attention
and what verification found. The Imports & sync screen lists them and opens one as a report, so a
reload, a second device or a server restart never loses a run. A run still ``running`` when the
server boots was cut short and becomes ``interrupted``. A run is archived (hidden from the default
list), never deleted. A record holds no credential: a provider's token stays in its own config.

Provider-specific work lives with the provider (``services/ynab_migration``); this module is the
lifecycle only.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from backend.core import data_events
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.import_run_store import COLUMNS, ImportRunStore

KINDS = ("import", "verify", "preview")
TRIGGERS = ("manual", "scheduled")
LIST_LIMIT = 200
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start(provider: str, kind: str, plan_id: str, trigger: str = "manual") -> dict:
    """Open a ``running`` record. At most one run per provider is in progress at a time."""
    if kind not in KINDS:
        raise ValidationError(f"kind must be one of {', '.join(KINDS)}")
    if trigger not in TRIGGERS:
        raise ValidationError(f"trigger must be one of {', '.join(TRIGGERS)}")
    store = ImportRunStore()
    with _lock:
        if store.has_running(provider):
            raise ConflictError(f"A {provider} import is already running.")
        run = {
            "id": uuid.uuid4().hex,
            "provider": provider,
            "kind": kind,
            "trigger": trigger,
            "status": "running",
            "started_at": _now(),
            "finished_at": None,
            "plan_id": plan_id,
            "plan_name": "",
            "outcome": "",
            "archived_at": None,
            "phase": "starting",
            "progress": {"done": 0, "total": 0},
            "error": None,
            "log": [],
            "counts": {},
            "problems": [],
            "warnings": [],
            "book_state": None,
            "backup_name": None,
            "preview": None,
            "verification": None,
        }
        store.insert(run)
    return run


def update(run_id: str, **fields) -> None:
    ImportRunStore().update(run_id, fields)


def finish(run_id: str, status: str, **fields) -> None:
    update(run_id, status=status, finished_at=_now(), **fields)
    data_events.publish(["*"])


def get(run_id: str) -> dict:
    run = ImportRunStore().get(run_id)
    if run is None:
        raise NotFoundError("That run does not exist.")
    return run


def latest(provider: str) -> dict | None:
    return ImportRunStore().latest(provider)


def summary(run: dict) -> dict:
    return {k: run.get(k) for k in COLUMNS}


def list_runs(provider: str | None = None, include_archived: bool = False) -> list[dict]:
    """Newest first, the light columns only. Archived runs are left out unless asked for."""
    return ImportRunStore().summaries(provider, include_archived, LIST_LIMIT)


def archive(run_id: str) -> dict:
    run = get(run_id)
    if run["status"] == "running":
        raise ConflictError("A run in progress can't be archived.")
    if not run.get("archived_at"):
        update(run_id, archived_at=_now())
        data_events.publish(["*"])
    return summary(get(run_id))


def unarchive(run_id: str) -> dict:
    get(run_id)
    update(run_id, archived_at=None)
    data_events.publish(["*"])
    return summary(get(run_id))


def recover_interrupted() -> int:
    """Boot: a run still ``running`` belonged to a process that is gone."""
    stuck = ImportRunStore().running_ids()
    for run_id in stuck:
        update(
            run_id,
            status="interrupted",
            finished_at=_now(),
            outcome="Interrupted",
            error="The server stopped while this run was in progress. Run it again to finish: "
            "nothing is booked twice.",
        )
    return len(stuck)
