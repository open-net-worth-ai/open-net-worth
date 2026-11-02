"""The sync's persistent state (one ``state`` document) and its YNAB-side metadata (one ``meta``
document), plus the schedule arithmetic. Never holds a credential."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.errors import ValidationError
from backend.db.ynab_sync_store import YnabSyncStateStore

SURFACE = "ynab_sync"  # the audit surface of every write the sync makes (echo suppression)
INTERVALS = {"15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "daily": 86400, "manual": None}
DEFAULT_INTERVAL = "1h"
REQUEST_CAP = 40  # per run; YNAB allows 200 an hour per token

_store = YnabSyncStateStore()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def blank() -> dict:
    return {
        "id": "state",
        "enabled": False,
        "interval": DEFAULT_INTERVAL,
        "plan_id": None,
        "plan_name": None,
        "currency": None,
        "bootstrapped": False,  # the first (catch-up) run has completed
        "server_knowledge": None,
        "audit_cursor": -1,
        "pending": [],  # ledger transactions with a change not yet sent (rate cap / failure)
        "last_run": None,
        "next_run_at": None,
    }


def load() -> dict:
    state = {**blank(), **_store.doc("state")}
    last = state["last_run"]
    if last and last.get("trigger") == "schedule":  # the value before the run records existed
        state["last_run"] = {**last, "trigger": "scheduled"}
    return state


def save(state: dict) -> None:
    _store.save(state)


def load_meta() -> dict:
    return {
        "accounts": [],
        "groups": [],
        "categories": [],
        "payees": {},
        "months": [],
        **_store.doc("meta"),
    }


def save_meta(meta: dict) -> None:
    _store.save({**meta, "id": "meta"})


def reset() -> None:
    """Forget everything about one plan (the plan was changed or the sync was re-pointed)."""
    from backend.db.ynab_sync_store import YnabBaselineStore, YnabConflictStore, YnabMirrorStore

    for store in (
        YnabSyncStateStore(),
        YnabMirrorStore(),
        YnabBaselineStore(),
        YnabConflictStore(),
    ):
        store.replace_all([])


def validate_interval(interval: str) -> str:
    if interval not in INTERVALS:
        raise ValidationError(f"interval must be one of {', '.join(INTERVALS)}")
    return interval


def schedule_next(state: dict, started: datetime) -> None:
    seconds = INTERVALS[state["interval"]]
    state["next_run_at"] = iso(started + timedelta(seconds=seconds)) if seconds else None


def parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def is_due(state: dict, now: Optional[datetime] = None) -> bool:
    """Enabled, on a schedule, and either never run or past ``next_run_at``."""
    if not state["enabled"] or INTERVALS.get(state["interval"]) is None:
        return False
    if not state["next_run_at"]:
        return True
    return (now or _now()) >= parse(state["next_run_at"])
