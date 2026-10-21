"""Run records of provider imports (``import_runs``): one row per Import / Verify / Preview run.

The lookup fields are real columns, so the history list and the polls are SQL over those columns
and never deserialize a log or a verification; ``detail`` holds the heavy parts a run opened as a
report reads. A run is handed around as ONE dict, columns and detail together; this store splits
and joins it. Data access only — ``services/import_runs.py`` owns the lifecycle. Written by a
background worker outside any Lucius turn, so nothing here fires the checkpoint hook.
"""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from backend.db.engine import get_engine
from backend.db.tables import import_runs

COLUMNS = (
    "id",
    "provider",
    "kind",
    "trigger",
    "status",
    "started_at",
    "finished_at",
    "plan_id",
    "plan_name",
    "outcome",
    "archived_at",
    "phase",
    "progress",
    "error",
)
DETAIL = (
    "log",
    "counts",
    "problems",
    "warnings",
    "book_state",
    "backup_name",
    "preview",
    "verification",
)


def _split(run: dict) -> tuple[dict, dict]:
    unknown = set(run) - set(COLUMNS) - set(DETAIL)
    if unknown:
        raise ValueError(f"unknown import run field(s): {', '.join(sorted(unknown))}")
    return (
        {k: v for k, v in run.items() if k in COLUMNS},
        {k: v for k, v in run.items() if k in DETAIL},
    )


def _join(row) -> dict:
    data = dict(row._mapping)
    detail = data.pop("detail")
    return {**data, **detail}


class ImportRunStore:
    def __init__(self, engine: Engine | None = None):
        self._engine = engine

    def _eng(self) -> Engine:
        return self._engine or get_engine()

    def insert(self, run: dict) -> dict:
        columns, detail = _split(run)
        with self._eng().begin() as conn:
            conn.execute(insert(import_runs).values(**columns, detail=detail))
        return run

    def update(self, run_id: str, fields: dict) -> None:
        columns, detail = _split(fields)
        with self._eng().begin() as conn:
            if detail:
                current = conn.execute(
                    select(import_runs.c.detail).where(import_runs.c.id == run_id)
                ).scalar_one_or_none()
                if current is None:
                    return
                columns["detail"] = {**current, **detail}
            if columns:
                conn.execute(
                    update(import_runs).where(import_runs.c.id == run_id).values(**columns)
                )

    def get(self, run_id: str) -> dict | None:
        with self._eng().connect() as conn:
            row = conn.execute(select(import_runs).where(import_runs.c.id == run_id)).first()
        return _join(row) if row else None

    def latest(self, provider: str) -> dict | None:
        query = (
            select(import_runs)
            .where(import_runs.c.provider == provider)
            .order_by(import_runs.c.started_at.desc(), import_runs.c.id.desc())
            .limit(1)
        )
        with self._eng().connect() as conn:
            row = conn.execute(query).first()
        return _join(row) if row else None

    def summaries(self, provider: str | None, include_archived: bool, limit: int) -> list[dict]:
        """Newest first, the light columns only."""
        query = select(*(import_runs.c[name] for name in COLUMNS))
        if provider:
            query = query.where(import_runs.c.provider == provider)
        if not include_archived:
            query = query.where(import_runs.c.archived_at.is_(None))
        query = query.order_by(import_runs.c.started_at.desc(), import_runs.c.id.desc())
        with self._eng().connect() as conn:
            return [dict(r._mapping) for r in conn.execute(query.limit(limit))]

    def has_running(self, provider: str) -> bool:
        query = select(import_runs.c.id).where(
            import_runs.c.provider == provider, import_runs.c.status == "running"
        )
        with self._eng().connect() as conn:
            return conn.execute(query.limit(1)).first() is not None

    def running_ids(self) -> list[str]:
        with self._eng().connect() as conn:
            rows = conn.execute(select(import_runs.c.id).where(import_runs.c.status == "running"))
            return [r.id for r in rows]
