"""Reading a plan and mapping it, for one run of the job. A preview's read is kept for a moment, so
the review re-checking a changed link (``recheck_of``) doesn't read the app again."""

from __future__ import annotations

import time

from backend.core.errors import ValidationError
from backend.services.budget_app import current
from backend.services.ynab_migration import mapping, settle

_CACHE_SECONDS = 600
# The newest preview's read: run id -> (read at, app key, plan id, plan summary, plan data).
_read_cache: dict[str, tuple[float, str, str, dict, dict]] = {}


def read(run, store, plan_id: str, recheck_of: str | None) -> tuple[dict, dict]:
    app = current()
    cached = _read_cache.pop(recheck_of, None) if recheck_of else None
    if (
        cached
        and cached[1:3] == (app.key, plan_id)
        and time.monotonic() - cached[0] < _CACHE_SECONDS
    ):
        _read_cache.clear()
        _read_cache[run.id] = cached
        return cached[3], cached[4]
    summary = next((p for p in app.reader.list_plans(store) if p["id"] == plan_id), None)
    if summary is None:
        raise ValidationError(
            f"That {app.name} {app.plan_word} is not visible to the saved {app.saved_sign_in}."
        )
    run.progress(f"reading {app.name}")
    data = app.reader.fetch_plan(store, plan_id, with_budget=True, progress=run.progress)
    _read_cache.clear()
    _read_cache[run.id] = (time.monotonic(), app.key, plan_id, summary, data)
    return summary, data


def load_plan(run, store, plan_id: str, choice: dict, kind: str) -> dict:
    summary, data = read(run, store, plan_id, choice.get("recheck_of"))
    currency, asked = settle.currency(summary, data, choice.get("currency"), kind)
    run.progress("mapping")
    # A tracking budget's figures aren't envelopes: nothing of its months comes across.
    months = [] if data.get("budget_mode") == "tracking" else data["months"]
    plan = mapping.build_plan(
        summary["name"],
        currency,
        data["accounts"],
        data["groups"],
        data["transactions"],
        months,
    )
    return settle.note(plan, data, asked)
