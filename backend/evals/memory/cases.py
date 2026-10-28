"""The case table: data only. One module per stage so each stays readable; this is the join."""

from __future__ import annotations

from backend.evals.memory.cases_extract import EXTRACT_CASES
from backend.evals.memory.cases_import import IMPORT_CASES
from backend.evals.memory.cases_keep import KEEP_EXTRACT_CASES, KEEP_SORT_CASES
from backend.evals.memory.cases_recent import RECENT_EXTRACT_CASES, RECENT_USE_CASES
from backend.evals.memory.cases_sort import SORT_CASES
from backend.evals.memory.cases_tidy import TIDY_CASES
from backend.evals.memory.cases_use import USE_CASES
from backend.evals.memory.spec import STAGES, Case

CASES: tuple[Case, ...] = (
    *EXTRACT_CASES,
    *KEEP_EXTRACT_CASES,
    *RECENT_EXTRACT_CASES,
    *SORT_CASES,
    *KEEP_SORT_CASES,
    *IMPORT_CASES,
    *TIDY_CASES,
    *USE_CASES,
    *RECENT_USE_CASES,
)


def select(stages: tuple[str, ...] = STAGES, ids: tuple[str, ...] = ()) -> list[Case]:
    chosen = [c for c in CASES if c.stage in stages and (not ids or c.id in ids)]
    unknown = set(ids) - {c.id for c in CASES}
    if unknown:
        raise ValueError(f"unknown case id(s): {sorted(unknown)}")
    return chosen
