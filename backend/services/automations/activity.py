"""Cross-automation Activity summary for the Lucius landing screen.

Two sections, both built from data Automations already persists — nothing new is stored:
runs currently ``running``/``waiting``, plus runs that finished (``completed``/``failed``/
``cancelled``) within the last ``_RECENT_WINDOW`` — across EVERY automation (Active), and
automations with an upcoming schedule (Scheduled). Kept out of ``runs.py`` (already at 703
lines, over the ~400-line file cap) as a clearly-named sibling module rather than growing that
file further.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_store import AutomationStore
from backend.db.session_store import SessionStore

logger = logging.getLogger(__name__)

_IN_FLIGHT_STATUSES = ("running", "waiting")
# Owner call (2026-08-20): a finished run shouldn't vanish from Active the instant it lands —
# "did that just run OK?" is exactly the question this panel exists to answer at a glance,
# without a trip to the Automations tab. 4 hours (revised from an initial 30-minute guess,
# 2026-08-21 — owner: wanted it to match the reference mockup's "4 hours ago" items still
# showing) is long enough to still be relevant well after you've moved on to something else,
# without becoming a full-day activity log.
#
# `cancelled` belongs here too, not just completed/failed (owner report 2026-08-22: an
# automation run that "stopped because [the] server restarted" — a graceful shutdown/reload
# journals every in-flight turn `cancelled` via ai/turns.py::shutdown(), backend/main.py's
# lifespan teardown — was invisible in Active despite finishing 30 minutes earlier). The SAME
# status also covers a deliberate user Stop (ai/automation_runner_adapter.py's own
# `self.cancelled` comment: "the user stopped this turn") — this codebase does not currently
# distinguish the two at the status level, but BOTH are exactly the "did that run actually
# finish OK?" question this panel exists to answer; excluding either was the bug, not a
# deliberate choice to hide one of them.
_RECENT_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_RECENT_WINDOW = timedelta(hours=4)
# Owner call (2026-10-14): a failure waits in Needs attention for at most a day. Most clear
# sooner — a later good run settles them — but a paused or finished automation never runs
# again, and its failure would otherwise sit there forever. The history list keeps "Failed".
# Owner call (2026-11-06): opening the run's conversation also clears it — the row exists to get
# you to the failure. A later failure is a new moment, so it comes back.
_FAILED_WINDOW = timedelta(hours=24)
_FETCH_LIMIT = 20
# Generous multiple of _FETCH_LIMIT: terminal runs finish far more often than ones stay
# running/waiting, so a plain _FETCH_LIMIT-sized fetch across all 4 statuses could get crowded
# out by old completions before the recency filter below ever runs. Trimmed back down to
# _FETCH_LIMIT after filtering.
_RAW_FETCH_LIMIT = _FETCH_LIMIT * 4


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_recent_terminal(run: dict, now: datetime) -> bool:
    finished_at = run.get("finished_at")
    if not finished_at:
        return False
    try:
        finished = _parse_iso(finished_at)
    except ValueError:
        return False
    return now - finished <= _RECENT_WINDOW


def _row(run: dict, automations: dict) -> dict | None:
    automation = automations.get(run.get("automation_id"))
    if automation is None:
        # Defensive only — the automation was deleted out from under a run that outlived it.
        # Never happens on the live delete path (assert_automation_delete_safe refuses to
        # delete while a run is still `running`), but a landing-screen panel should skip a
        # dangling row rather than crash or show a nameless one.
        return None
    return {
        "automation_id": automation["id"],
        "automation_name": automation.get("name") or automation["id"],
        "run_id": run["id"],
        # The run's own conversation — the Lucius landing screen's Active row uses this to
        # switch the side panel to the actual run transcript when opened, instead of leaving
        # whatever draft conversation was showing (owner report 2026-08-22: tapping an Active
        # row opened the right automation/run in the Automations tab, but the Lucius panel
        # beside it still showed a blank "New conversation").
        "session_id": run.get("session_id"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


def _active_items(now: datetime) -> list[dict]:
    run_store = AutomationRunStore()
    automations = {row["id"]: row for row in AutomationStore().all()}

    # In-flight runs (running/waiting) are fetched and included WITHOUT competing against
    # terminal ones for the _FETCH_LIMIT cap — a run that's still going, or waiting on YOUR
    # approval, must never be pushed out of view by a burst of completed/failed noise from a
    # high-frequency automation (owner report 2026-08-22: a run genuinely `running` right then
    # was missing from Active — a noisy email-triggered automation's 20+ MORE RECENTLY-STARTED
    # completed/failed runs had already filled the shared cap by the time this one, started
    # earlier and still going, was reached). `list_by_statuses` already returns
    # most-recently-STARTED first; `waiting` is sorted ahead of `running` on top of that — it
    # needs a human decision, `running` is merely in progress (matches the reference mockup,
    # where a `waiting` row from hours ago still led a `running` row from minutes ago).
    in_flight_runs = run_store.list_by_statuses(list(_IN_FLIGHT_STATUSES), _RAW_FETCH_LIMIT)
    in_flight_runs.sort(key=lambda run: run.get("status") != "waiting")
    in_flight = [item for run in in_flight_runs if (item := _row(run, automations)) is not None]

    remaining = max(0, _FETCH_LIMIT - len(in_flight))
    recent_terminal: list[dict] = []
    if remaining:
        terminal_runs = run_store.list_by_statuses(
            list(_RECENT_TERMINAL_STATUSES), _RAW_FETCH_LIMIT
        )
        for run in terminal_runs:
            if not _is_recent_terminal(run, now):
                continue
            item = _row(run, automations)
            if item is None:
                continue
            recent_terminal.append(item)
            if len(recent_terminal) >= remaining:
                break

    return in_flight + recent_terminal


def _scheduled_items(now: datetime) -> list[dict]:
    candidates = []
    for automation in AutomationStore().all():
        if automation.get("status") != "active":
            continue
        next_run_at = automation.get("next_run_at")
        if not next_run_at:
            continue
        try:
            when = _parse_iso(next_run_at)
        except ValueError:
            logger.warning(
                "automation %s has an unparseable next_run_at %r; skipped from Activity",
                automation.get("id"),
                next_run_at,
            )
            continue
        if when <= now:
            continue
        candidates.append((when, automation))
    candidates.sort(key=lambda pair: pair[0])
    return [
        {
            "automation_id": automation["id"],
            "automation_name": automation.get("name") or automation["id"],
            "next_run_at": automation["next_run_at"],
        }
        for _when, automation in candidates[:_FETCH_LIMIT]
    ]


def _failed_within(run: dict, now: datetime) -> bool:
    when = run.get("finished_at") or run.get("started_at")
    try:
        return bool(when) and now - _parse_iso(when) <= _FAILED_WINDOW
    except ValueError:
        return False


def _read_since_it_ended(run: dict) -> bool:
    """Has the person opened this run's conversation since the run ended? Anything unreadable
    answers no: an unseen failure must stay listed, and one bad row must not blank the panel."""
    session_id = run.get("session_id")
    ended = run.get("finished_at") or run.get("started_at")
    if not session_id or not ended:
        return False
    try:
        read_at = (SessionStore().get_meta(session_id) or {}).get("last_read_at")
        return bool(read_at) and _parse_iso(read_at) > _parse_iso(ended)
    except Exception:
        logger.warning("activity: cannot read %r's read stamp", session_id, exc_info=True)
        return False


def _failed_items(now: datetime) -> list[dict]:
    """Every automation whose LATEST run failed in the last ``_FAILED_WINDOW`` and that hasn't
    been opened since, newest first.

    A failure also leaves when a later run — a scheduled one, Run now, or a retry in the
    run's own conversation, which settles that same row — succeeds. A recurring automation
    that keeps failing comes back, since each failure is fresh."""
    latest: dict[str, dict] = {}
    for run in AutomationRunStore().outcomes():
        if run.get("automation_id"):
            latest[run["automation_id"]] = run  # oldest first, so the last one wins
    automations = {row["id"]: row for row in AutomationStore().all()}
    items = []
    for automation_id, run in latest.items():
        automation = automations.get(automation_id)  # all() already drops one being deleted
        if run.get("status") != "failed" or automation is None:
            continue
        if not _failed_within(run, now) or _read_since_it_ended(run):
            continue
        items.append(
            {
                "automation_id": automation_id,
                "automation_name": automation.get("name") or automation_id,
                "run_id": run["id"],
                "session_id": run.get("session_id"),
                "status": "failed",
                "started_at": run.get("started_at") or "",
                "finished_at": run.get("finished_at"),
                "error": run.get("error") or None,
            }
        )
    items.sort(key=lambda item: item["finished_at"] or item["started_at"], reverse=True)
    return items[:_FETCH_LIMIT]


def run_status_by_session() -> dict[str, str]:
    """``{session_id: status}`` for every Automation run's conversation — the outcome the
    conversation list shows, which a turn's own ``last_turn_status`` can't carry (a run fails
    when its plan is left unfinished or a restart cuts it, after a turn that said ``ok``)."""
    return {
        run["session_id"]: run["status"]
        for run in AutomationRunStore().outcomes()
        if run.get("session_id") and run.get("status")
    }


def automation_activity() -> dict:
    """``{"active": [...], "scheduled": [...], "failed": [...]}`` — the Lucius landing screen's
    Activity panel. Failed: see ``_failed_items`` — it feeds Needs attention.

    Active: every ``running``/``waiting`` run, unconditionally and never crowded out by the cap
    (``waiting`` first, then ``running``, each most-recently-started first) — THEN, filling
    whatever's left of ``_FETCH_LIMIT``, ``completed``/``failed``/``cancelled`` runs that
    finished within ``_RECENT_WINDOW``, most-recently-started first. Scheduled:
    ``status="active"`` automations with a non-null, strictly-future ``next_run_at``, soonest
    first. Both capped at
    ``_FETCH_LIMIT`` — the frontend shows a smaller page of that with its own "Show more".
    """
    now = datetime.now(timezone.utc)
    return {
        "active": _active_items(now),
        "scheduled": _scheduled_items(now),
        "failed": _failed_items(now),
    }
