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
EFFORTS = ("low", "medium", "high")
MAX_MODEL = 120
# Reviews run at this time in the owner's zone: daily on weekdays, weekly on Mondays.
RUN_AT = time(10, 0)
MAX_RUNS_KEPT = 20
FAILURES_TO_PAUSE = 2
MAX_INSTRUCTIONS = 600
MAX_MEMO = 1200
MAX_WATCHING = 300
# A review stuck past this long is treated as dead (the server restarted mid-review).
STALE_RUN = timedelta(minutes=30)

DEFAULT = {
    "mode": "manual",
    "cadence": "daily",
    "instructions": "",
    # The model each review runs on: "" is the app's default (Settings → AI), else a
    # model reference as the pickers write it ("<provider>:<model>").
    "model": "",
    "effort": "medium",
    "next_run_at": None,
    "running_since": None,
    # What Lucius recorded with record_autopilot_memo during the running review.
    "memo_draft": None,
    # Why the next review runs early ("Review now", "ACME fell 6.1% today"), and why the running
    # one is running; None = its schedule.
    "pending_trigger": None,
    "running_trigger": None,
    # {reason key: day} — each trigger starts at most one review a day.
    "trigger_seen": {},
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
        "model": a["model"],
        "effort": a["effort"],
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
    """Set the mode, cadence, instructions, model or effort. Auto needs a mandate: the mandate is what the
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
        if "model" in change:
            model = str(change.get("model") or "").strip()
            if len(model) > MAX_MODEL:
                raise ValidationError("That model name is too long.")
            a["model"] = model
        if "effort" in change:
            effort = str(change.get("effort") or "medium")
            if effort not in EFFORTS:
                raise ValidationError("Effort is low, medium or high.")
            a["effort"] = effort
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
        a.update(next_run_at=_now().isoformat(), pending_trigger="Review now")
        return _save(row, a, conn=conn)


def trigger(pid: str, reasons: dict[str, str]) -> bool:
    """Start a review early for the reasons not seen yet today (op "autopilot_trigger").
    Returns whether one was started."""
    from backend.services.paper_trading import service

    today = user_now().date().isoformat()
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        if row.get("status") != "active" or a["mode"] != "auto" or a["paused_reason"]:
            return False
        seen = {k: d for k, d in (a.get("trigger_seen") or {}).items() if d == today}
        new = {k: r for k, r in reasons.items() if k not in seen}
        if not new or a["running_since"]:
            return False
        a.update(
            next_run_at=_now().isoformat(),
            pending_trigger="; ".join(new.values()),
            trigger_seen={**seen, **{k: today for k in new}},
        )
        _save(row, a, conn=conn)
        return True


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
            running_since=now.isoformat(),
            next_run_at=next_slot(a["cadence"], now).isoformat(),
            running_trigger=a.get("pending_trigger"),
            pending_trigger=None,
        )
        return _save(row, a, conn=conn)


def finish(pid: str, record: dict) -> dict:
    """Log a finished review and release it. Two failures in a row pause the autopilot."""
    from backend.services.paper_trading import service

    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        draft = a.get("memo_draft") or {}
        status = record["status"]
        error = record.get("error")
        # The memo is only what Lucius recorded with the memo tool — never his last reply,
        # which carried his working ("Let me redo this properly…") into the log (2026-11-18).
        if status == "done" and not draft.get("memo"):
            status, error = "failed", "Lucius finished without writing his memo."
        entry = {
            "id": new_id("papr"),
            "started_at": a["running_since"] or record.get("started_at"),
            "finished_at": _now().isoformat(),
            "status": status,
            "memo": str(draft.get("memo") or "")[:MAX_MEMO],
            "watching": str(draft.get("watching") or "")[:MAX_WATCHING] or None,
            # Why it ran: its schedule (None), "Review now", or what moved.
            "trigger": a.get("running_trigger"),
            # What it ran on, so two models' reviews can be compared.
            "model": record.get("model") or "",
            "effort": record.get("effort") or "medium",
            "orders": record.get("orders") or [],
            "error": error,
        }
        failures = a["failures"] + 1 if status == "failed" else 0
        paused = a["paused_reason"]
        if failures >= FAILURES_TO_PAUSE:
            paused = "Paused after two failed reviews in a row. Turn it on again to resume."
        a.update(
            running_since=None,
            running_trigger=None,
            memo_draft=None,
            failures=failures,
            paused_reason=paused,
            runs=[*a["runs"], entry][-MAX_RUNS_KEPT:],
        )
        return _save(row, a, conn=conn)


def save_memo(pid: str, memo: str, watching: str | None) -> dict:
    """Record the running review's memo (op "autopilot_memo"). Only during a review; a second
    call replaces the first, so the memo is his final word."""
    from backend.services.paper_trading import service

    text = str(memo or "").strip()
    if not text:
        raise ValidationError("Write the memo: what you checked, what you did and why.")
    if len(text) > MAX_MEMO:
        raise ValidationError(f"Keep the memo under {MAX_MEMO} characters.")
    with locked_unit_of_work(*ALL_TABLES) as conn:
        row = service.portfolio(pid, conn=conn)
        a = of(row)
        if not a["running_since"]:
            raise ConflictError("A memo is recorded only during an autopilot review.")
        a["memo_draft"] = {
            "memo": text,
            "watching": str(watching or "").strip()[:MAX_WATCHING] or None,
        }
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
