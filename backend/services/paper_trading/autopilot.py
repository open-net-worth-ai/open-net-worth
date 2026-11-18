"""Autopilot: a paper portfolio that Lucius reviews and trades on his own, within its mandate.

Two modes (owner, 2026-11-18): **manual** — only the owner and chat requests trade, as before —
and **auto** — on a schedule Lucius reviews the portfolio, places the trades that keep the
mandate, and leaves a memo of what he looked at and why. It is a setting of the portfolio,
never an Automation: it lives on the row (``autopilot``, additive) and goes with it.

This module holds the settings, the schedule and the run log — every write through
``apply_paper_change`` (ops ``autopilot`` and ``autopilot_run``). The review itself is
``autopilot_run``. The limits that matter are enforced here and in the paper choke, never left
to the model: Auto needs a mandate, a review can never break it and trades only while it runs,
one review runs at a time, and two failed reviews in a row pause it. There is no cap on how many
orders a review places (owner, 2026-11-18)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from backend.core.errors import ConflictError, ValidationError
from backend.core.ids import new_id
from backend.core.user_time import user_now
from backend.db.engine import locked_unit_of_work
from backend.db.paper_store import ALL_TABLES, PaperPortfolioStore

MODES = ("manual", "auto")
CADENCES = ("daily", "weekly")
# Reviews run at this time in the owner's zone: daily on weekdays, weekly on Mondays.
RUN_AT = time(10, 0)
MAX_RUNS_KEPT = 20
FAILURES_TO_PAUSE = 2
MAX_INSTRUCTIONS = 600
MAX_MEMO = 4000
# A review stuck past this long is treated as dead (the server restarted mid-review).
STALE_RUN = timedelta(minutes=30)

DEFAULT = {
    "mode": "manual",
    "cadence": "daily",
    "instructions": "",
    "next_run_at": None,
    "running_since": None,
    "failures": 0,
    "paused_reason": None,
    "runs": [],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def of(row: dict) -> dict:
    return {**DEFAULT, **(row.get("autopilot") or {})}


def next_slot(cadence: str, after: datetime) -> datetime:
    """The next review time strictly after ``after``, in UTC."""
    local = after.astimezone(user_now().tzinfo)
    day = local.date()
    for _ in range(14):
        at = datetime.combine(day, RUN_AT, tzinfo=local.tzinfo)
        weekday_ok = day.weekday() < 5 if cadence == "daily" else day.weekday() == 0
        if weekday_ok and at > local:
            return at.astimezone(timezone.utc)
        day += timedelta(days=1)
    raise AssertionError("no review slot within two weeks")


def view(row: dict) -> dict:
    """What the page and Lucius read: the settings, when it runs next, and its last reviews."""
    a = of(row)
    return {
        "mode": a["mode"],
        "cadence": a["cadence"],
        "instructions": a["instructions"],
        "next_run_at": a["next_run_at"] if a["mode"] == "auto" and not a["paused_reason"] else None,
        "running": bool(a["running_since"]),
        # Running, or due within two minutes: the page polls until its memo arrives.
        "review_soon": a["mode"] == "auto"
        and not a["paused_reason"]
        and (
            bool(a["running_since"])
            or bool(
                a["next_run_at"]
                and datetime.fromisoformat(a["next_run_at"]) - _now() < timedelta(minutes=2)
            )
        ),
        "paused_reason": a["paused_reason"],
        "runs": list(reversed(a["runs"]))[:MAX_RUNS_KEPT],
    }


# ── settings (op "autopilot") ───────────────────────────────────────────────────────────────


def apply_settings(change: dict) -> dict:
    """Set the mode, cadence or instructions. Auto needs a mandate: the mandate is what the
    owner approved in advance. Setting Auto again resumes a paused autopilot."""
    from backend.services.paper_trading import service

    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(change.get("id"), conn=conn)
        if row.get("status") != "active":
            raise ConflictError("An archived paper portfolio has no autopilot.")
        a = of(row)
        mode = str(change.get("mode") or a["mode"])
        cadence = str(change.get("cadence") or a["cadence"])
        if mode not in MODES:
            raise ValidationError("Autopilot is manual or auto.")
        if cadence not in CADENCES:
            raise ValidationError("Autopilot reviews daily or weekly.")
        if mode == "auto" and not row.get("mandate"):
            raise ValidationError(
                "Set a mandate first: autopilot trades only within the limits you set."
            )
        if "instructions" in change:
            text = str(change.get("instructions") or "").strip()
            if len(text) > MAX_INSTRUCTIONS:
                raise ValidationError(f"Keep the instructions under {MAX_INSTRUCTIONS} characters.")
            a["instructions"] = text
        rescheduled = mode == "auto" and (
            a["mode"] != "auto" or cadence != a["cadence"] or a["paused_reason"]
        )
        a.update(mode=mode, cadence=cadence)
        if mode == "auto" and (rescheduled or not a["next_run_at"]):
            a.update(next_run_at=next_slot(cadence, _now()).isoformat())
        if mode == "auto" and a["paused_reason"]:
            a.update(paused_reason=None, failures=0)
        if mode == "manual":
            a.update(next_run_at=None)
        return _save(row, a, conn=conn)


def run_soon(pid: str) -> dict:
    """Review now: the next scheduler pass (within a minute) runs it."""
    from backend.services.paper_trading import service

    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        if row.get("status") != "active" or a["mode"] != "auto":
            raise ConflictError("Turn autopilot on (Auto) to run a review.")
        if a["running_since"]:
            raise ConflictError("A review is already running.")
        if a["paused_reason"]:  # Review now on a paused autopilot resumes it
            a.update(paused_reason=None, failures=0)
        a.update(next_run_at=_now().isoformat())
        return _save(row, a, conn=conn)


# ── runs (op "autopilot_run") ───────────────────────────────────────────────────────────────


def due(now: datetime) -> list[dict]:
    """Active auto portfolios whose review is due and not running, and not paused."""
    out = []
    for row in PaperPortfolioStore().all():
        a = of(row)
        if row.get("status") != "active" or a["mode"] != "auto" or a["paused_reason"]:
            continue
        running = a["running_since"]
        if running and now - datetime.fromisoformat(running) < STALE_RUN:
            continue
        if a["next_run_at"] and datetime.fromisoformat(a["next_run_at"]) <= now:
            out.append(row)
    return out


def start(pid: str, now: datetime) -> dict | None:
    """Claim the review: mark it running and move the schedule on. None if another pass
    already claimed it, or it is no longer due."""
    from backend.services.paper_trading import service

    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        running = a["running_since"]
        if row.get("status") != "active" or a["mode"] != "auto" or a["paused_reason"]:
            return None
        if running and now - datetime.fromisoformat(running) < STALE_RUN:
            return None
        a.update(
            running_since=now.isoformat(), next_run_at=next_slot(a["cadence"], now).isoformat()
        )
        return _save(row, a, conn=conn)


def finish(pid: str, record: dict) -> dict:
    """Log a finished review and release it. Two failures in a row pause the autopilot."""
    from backend.services.paper_trading import service

    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        entry = {
            "id": new_id("papr"),
            "started_at": a["running_since"] or record.get("started_at"),
            "finished_at": _now().isoformat(),
            "status": record["status"],
            "memo": str(record.get("memo") or "")[:MAX_MEMO],
            "orders": record.get("orders") or [],
            "error": record.get("error"),
        }
        failures = a["failures"] + 1 if record["status"] == "failed" else 0
        paused = a["paused_reason"]
        if failures >= FAILURES_TO_PAUSE:
            paused = "Paused after two failed reviews in a row. Turn it on again to resume."
        a.update(
            running_since=None,
            failures=failures,
            paused_reason=paused,
            runs=[*a["runs"], entry][-MAX_RUNS_KEPT:],
        )
        return _save(row, a, conn=conn)


def run_started_at(row: dict) -> str | None:
    """While a review runs, when it began: the paper choke counts its orders from then."""
    return of(row)["running_since"]


def _save(row: dict, a: dict, *, conn) -> dict:
    return (
        PaperPortfolioStore().update_by_id(
            row["id"], {"autopilot": a, "updated_at": _now().isoformat()}, conn=conn
        )
        or row
    )
