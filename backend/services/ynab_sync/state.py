"""The sync's persistent state (one ``state`` document) and its YNAB-side metadata (one ``meta``
document), plus the schedule arithmetic. Never holds a credential."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.core.errors import ValidationError
from backend.services.budget_app import YNAB, current

# YNAB's audit surface, for what still imports it; a run writes and skips ``current().surface``.
SURFACE = YNAB.surface
INTERVALS = {"15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "daily": 86400, "manual": None}
DEFAULT_INTERVAL = "1h"
# Which way changes flow. One way skips the other way's sends or pulls; a change on both sides
# still goes to the review list (ADR 2026-10-20 amendment, 2026-11-02).
DIRECTIONS = ("both", "from_ynab", "to_ynab")
REQUEST_CAP = YNAB.caps.request_cap  # per run; YNAB allows 200 an hour per token


def _store():
    return current().stores.state()


def request_cap() -> int:
    """What one run may spend. YNAB's is this module's ``REQUEST_CAP``, read on each run."""
    app = current()
    return REQUEST_CAP if app is YNAB else app.caps.request_cap


def _now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def blank() -> dict:
    return {
        "id": "state",
        "enabled": False,
        "interval": DEFAULT_INTERVAL,
        "direction": "both",
        "plan_id": None,
        "plan_name": None,
        "currency": None,
        "bootstrapped": False,  # the first (catch-up) run has completed
        "server_knowledge": None,
        "audit_cursor": -1,
        "pending": [],  # ledger transactions with a change not yet sent (rate cap / failure)
        # What one way held back, so switching back to Both ways still sends or pulls it: ledger
        # transaction ids not sent, YNAB transaction ids not pulled, and YNAB's Assigned figures
        # ([month, category, milliunits]) not pulled.
        "held": [],
        "held_ynab": [],
        "held_assigned": [],
        # The plan was changed from another (``copies_keep_ids``): the first run that could delete
        # here holds every removal it finds, until one such run succeeds.
        "switched": False,
        # The last read held no account linked here (``budget_scoped_accounts``): the plan was
        # never imported, so the run stopped before pairing or writing anything.
        "awaiting_import": False,
        "last_run": None,
        "next_run_at": None,
    }


def load() -> dict:
    state = {**blank(), **_store().doc("state")}
    last = state["last_run"]
    if last and last.get("trigger") == "schedule":  # the value before the run records existed
        state["last_run"] = {**last, "trigger": "scheduled"}
    return state


def save(state: dict) -> None:
    _store().save(state)


def load_meta() -> dict:
    return {
        "accounts": [],
        "groups": [],
        "categories": [],
        "payees": {},
        "months": [],
        **_store().doc("meta"),
    }


def save_meta(meta: dict) -> None:
    _store().save({**meta, "id": "meta"})


def reset() -> None:
    """Forget everything about one plan (the plan was changed or the sync was re-pointed). How
    YNAB's categories and groups pair with ours is kept, for every plan: the import wrote it, and
    turning the sync on (for this plan or one used before) must find it."""
    from backend.services import ynab_pairs

    kept = ynab_pairs.keep_for()
    stores = current().stores
    for store in (stores.state(), stores.mirror(), stores.baselines(), stores.conflicts()):
        store.replace_all([])
    ynab_pairs.save(kept)


def validate_direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        raise ValidationError(f"direction must be one of {', '.join(DIRECTIONS)}")
    return direction


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
