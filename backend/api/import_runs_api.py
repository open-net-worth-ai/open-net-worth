"""Import runs REST — the history and reports on the Imports & sync screen.

Thin router over ``services.import_runs``:
  * ``GET  /api/imports/runs`` — newest first, one summary per run; ``archived=true`` includes
    the archived ones. ``provider`` narrows to one importer.
  * ``GET  /api/imports/runs/{id}`` — the whole run record: log, counts, problems, verification.
  * ``POST /api/imports/runs/{id}/archive`` / ``/unarchive`` — hide a finished run from the
    default list, or bring it back. A run is never deleted.

Starting a run stays with the provider (``POST /api/ynab/migration/*``).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import import_runs

router = APIRouter()


def _call(fn, *args):
    try:
        return fn(*args)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/imports/runs")
def list_import_runs(provider: Optional[str] = None, archived: bool = False):
    return {"runs": import_runs.list_runs(provider, include_archived=archived)}


@router.get("/api/imports/runs/{run_id}")
def get_import_run(run_id: str):
    return _call(import_runs.get, run_id)


@router.post("/api/imports/runs/{run_id}/archive")
def archive_import_run(run_id: str):
    return _call(import_runs.archive, run_id)


@router.post("/api/imports/runs/{run_id}/unarchive")
def unarchive_import_run(run_id: str):
    return _call(import_runs.unarchive, run_id)
