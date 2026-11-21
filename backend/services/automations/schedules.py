"""Pure Automation trigger validation, descriptions, and schedule math."""

from __future__ import annotations

import calendar
import math
import re
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from backend.core.user_time import user_zone
from backend.services.automations.txn_conditions import MATCH_MODES, validate_conditions

MIN_INTERVAL_SECONDS = 60
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_SCHEDULE_TYPES = frozenset({"once", "daily", "weekdays", "weekly", "monthly", "interval", "rrule"})
_EVENT_TYPES = frozenset({"txn", "balance", "price"})
_EVENT_LIMITS = frozenset({"each", "hour", "day"})
_TRANSACTION_OPERATIONS = frozenset({"create", "update", "delete"})
_TRANSACTION_KINDS = frozenset({"expense", "income", "transfer", "adjustment", "investment"})
_TRANSACTION_DIRECTIONS = frozenset({"in", "out", "debit", "credit"})
_CROSSING_DIRECTIONS = frozenset({"above", "below"})
# above/below: a price level. move/rise/fall: a percent vs. the previous close — move is
# either way, rise only up, fall only down.
_PRICE_DIRECTIONS = frozenset({"above", "below", "move", "rise", "fall"})
_TRANSACTION_WHEN_KEYS = frozenset(
    {
        "operation",
        "account",
        "account_id",
        "direction",
        "over",
        "contains",
        "description_contains",
        "keywords",
        "description_regex",
        "memo_contains",
        "memo_keywords",
        "memo_regex",
        "kind",
        "currency",
        "min_amount",
        "max_amount",
        "conditions",
        "match",
    }
)
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


def validate_trigger(trigger: dict, now: datetime | None = None) -> dict:
    """Return a validated trigger copy or raise ``ValueError`` before any write."""
    if not isinstance(trigger, dict):
        raise ValueError("trigger must be an object")
    kind = trigger.get("kind")
    if kind == "manual":
        _reject_unknown(trigger, {"kind"})
        return {"kind": "manual"}
    if kind == "event":
        _reject_unknown(trigger, {"kind", "event", "when", "limit", "sequence"})
        event = trigger.get("event")
        if not isinstance(event, str) or event not in _EVENT_TYPES:
            raise ValueError(f"unknown automation event {event!r}")
        when = trigger.get("when")
        if not isinstance(when, dict):
            raise ValueError("event trigger requires an object 'when'")
        limit = trigger.get("limit")
        if not isinstance(limit, str) or limit not in _EVENT_LIMITS:
            raise ValueError("event trigger limit must be each, hour, or day")
        normalized_when = _validate_event_when(event, when)
        result = {
            "kind": "event",
            "event": event,
            "when": normalized_when,
            "limit": limit,
        }
        sequence = trigger.get("sequence")
        if sequence is not None:
            if not isinstance(sequence, dict):
                raise ValueError("event trigger sequence must be an object")
            _reject_unknown(sequence, {"group", "priority", "stop"})
            raw_group = sequence.get("group")
            if not isinstance(raw_group, str):
                raise ValueError("event trigger sequence requires a group")
            group = raw_group.strip()
            priority = sequence.get("priority", 100)
            stop = sequence.get("stop", False)
            if not group:
                raise ValueError("event trigger sequence requires a group")
            if isinstance(priority, bool) or not isinstance(priority, int):
                raise ValueError("event trigger sequence priority must be an integer")
            if not isinstance(stop, bool):
                raise ValueError("event trigger sequence stop must be a boolean")
            result["sequence"] = {"group": group, "priority": priority, "stop": stop}
        return result
    if kind != "schedule":
        raise ValueError(
            f"unknown automation trigger kind {kind!r} — 'kind' must be manual, schedule, or "
            "event. A schedule trigger also needs 'type': "
            '{"kind": "schedule", "type": "once", "at": "2026-08-19T09:00:00+08:00"}'
        )
    return _validate_schedule_trigger(trigger, now=now)


def _validate_event_when(event: str, when: dict) -> dict:
    if event == "txn":
        return _validate_transaction_when(when)
    if event == "balance":
        return _validate_balance_when(when)
    return _validate_price_when(when)


def _validate_transaction_when(when: dict) -> dict:
    _reject_unknown(when, _TRANSACTION_WHEN_KEYS)
    out = dict(when)
    operation = out.get("operation", "create")
    if isinstance(operation, str):
        operations = [operation]
    elif isinstance(operation, list) and operation:
        operations = operation
    else:
        raise ValueError("transaction event operation must be create, update, delete, or a list")
    if any(not isinstance(item, str) or item not in _TRANSACTION_OPERATIONS for item in operations):
        raise ValueError("transaction event operation must be create, update, delete, or a list")
    normalized_operations = list(dict.fromkeys(operations))
    out["operation"] = (
        normalized_operations[0] if len(normalized_operations) == 1 else normalized_operations
    )

    _validate_reference_aliases(out, ("account", "account_id"), "transaction account")
    _validate_optional_enum(
        out, "direction", _TRANSACTION_DIRECTIONS, "transaction event direction"
    )
    _validate_optional_enum(out, "kind", _TRANSACTION_KINDS, "transaction event kind")
    for field in ("contains", "description_contains", "memo_contains"):
        _validate_optional_string(out, field, f"transaction event {field}")
    for field in ("description_regex", "memo_regex"):
        _validate_optional_regex(out, field, f"transaction event {field}")
    for field in ("keywords", "memo_keywords"):
        _validate_optional_string_list(out, field, f"transaction event {field}")
    if out.get("match") is not None and out["match"] not in MATCH_MODES:
        raise ValueError("transaction event match must be all or any")
    if out.get("conditions") is not None:
        out["conditions"] = validate_conditions(out["conditions"], out.get("match") or "all")
        if not out["conditions"]:
            del out["conditions"]
    if out.get("currency") is not None:
        currency = out["currency"]
        if not isinstance(currency, str) or _CURRENCY_RE.fullmatch(currency) is None:
            raise ValueError("transaction event currency must be a 3-letter code")
        out["currency"] = currency.upper()
    for field in ("over", "min_amount", "max_amount"):
        if out.get(field) is not None:
            out[field] = _non_negative_number(out[field], f"transaction event {field}")
    minimum = out.get("min_amount")
    maximum = out.get("max_amount")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("transaction event min_amount cannot be greater than max_amount")
    return out


def _validate_balance_when(when: dict) -> dict:
    _reject_unknown(when, {"account", "account_id", "direction", "amount", "threshold"})
    out = dict(when)
    _validate_reference_aliases(
        out, ("account", "account_id"), "balance event account", required=True
    )
    _validate_required_enum(out, "direction", _CROSSING_DIRECTIONS, "balance event direction")
    _validate_required_number_aliases(out, ("amount", "threshold"), "balance event amount")
    return out


def _validate_price_when(when: dict) -> dict:
    _reject_unknown(when, {"symbol", "direction", "value"})
    out = dict(when)
    _validate_required_string(out, "symbol", "price event symbol")
    _validate_required_enum(out, "direction", _PRICE_DIRECTIONS, "price event direction")
    if "value" not in out:
        raise ValueError("price event requires value")
    out["value"] = _non_negative_number(out["value"], "price event value")
    return out


def _validate_reference_aliases(
    value: dict, fields: tuple[str, ...], label: str, *, required: bool = False
) -> None:
    active: list[str] = []
    for field in fields:
        if field not in value or value[field] is None:
            continue
        raw = value[field]
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{label} must be a non-empty string")
        normalized = raw.strip()
        value[field] = normalized
        active.append(normalized)
    if required and not active:
        raise ValueError(f"{label} is required")
    if len(set(active)) > 1:
        raise ValueError(f"{label} aliases must refer to the same value")


def _validate_required_string(value: dict, field: str, label: str) -> None:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value[field] = raw.strip()


def _validate_optional_string(value: dict, field: str, label: str) -> None:
    if field not in value or value[field] is None:
        return
    raw = value[field]
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty string or null")


def _validate_optional_regex(value: dict, field: str, label: str) -> None:
    _validate_optional_string(value, field, label)
    pattern = value.get(field)
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def _validate_optional_string_list(value: dict, field: str, label: str) -> None:
    if field not in value or value[field] is None:
        return
    items = value[field]
    if not isinstance(items, list) or any(
        not isinstance(item, str) or not item.strip() for item in items
    ):
        raise ValueError(f"{label} must be a list of non-empty strings or null")


def _validate_optional_enum(value: dict, field: str, allowed: frozenset[str], label: str) -> None:
    if field not in value or value[field] is None:
        return
    raw = value[field]
    if not isinstance(raw, str) or raw not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")


def _validate_required_enum(value: dict, field: str, allowed: frozenset[str], label: str) -> None:
    raw = value.get(field)
    if not isinstance(raw, str) or raw not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")


def _validate_required_number_aliases(value: dict, fields: tuple[str, ...], label: str) -> None:
    active: list[float] = []
    for field in fields:
        if field not in value or value[field] is None:
            continue
        normalized = _finite_number(value[field], label)
        value[field] = normalized
        active.append(normalized)
    if not active:
        raise ValueError(f"{label} is required")
    if len(set(active)) > 1:
        raise ValueError(f"{label} aliases must have the same value")


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be a finite number")
    return normalized


def _non_negative_number(value: object, label: str) -> float:
    normalized = _finite_number(value, label)
    if normalized < 0:
        raise ValueError(f"{label} must be a non-negative number")
    return normalized


def _non_negative_integer(value: object, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        normalized = int(value)
    else:
        raise ValueError(f"{label} must be a non-negative integer")
    if normalized < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{label} must be no greater than {maximum}")
    return normalized


def compute_next_run(
    trigger: dict,
    after: datetime,
    created_at: str | datetime | None = None,
    not_before: datetime | None = None,
) -> str | None:
    """Return the next strictly future scheduled occurrence as an ISO-8601 string.

    ``after`` anchors the schedule GRID; ``not_before`` (when given) is the floor the answer
    must clear. They differ whenever an Automation has fallen behind, and keeping them
    separate is the whole point: rescheduling off the run's own ``scheduled_for`` is what
    stops a daily 09:00 job from drifting later every day, but on its own it advances by
    exactly ONE occurrence, so a job blocked for five days is handed a next_run_at that is
    still four days in the past. The scheduler sees that as due, fires, advances one more
    day, and repeats — five separate paid runs, each reporting on a different stale date,
    each notifying. Live incident: "Daily Trades Summary" parked on an approval card from
    2026-08-04 to 2026-08-09 and then ran five back-to-back catch-up runs.

    Passing ``not_before=now`` keeps the grid and skips the backlog, so a job that fell
    behind resumes at its next real slot instead of replaying history.
    """
    validated = validate_trigger(trigger, now=after)
    if validated["kind"] != "schedule":
        return None
    schedule_type = validated["type"]
    tz = _timezone(validated.get("tzid"))
    local_after = _as_timezone(after, tz)
    # For every type below except ``interval`` the phase lives in the TRIGGER (time, day) or
    # in the rrule anchor — never in ``after`` — so raising the starting point to the floor
    # skips the whole backlog in one step and still lands on the correct slot.
    floor = local_after
    if not_before is not None:
        floor = max(floor, _as_timezone(not_before, tz))
    if schedule_type == "once":
        at = _parse_datetime(validated["at"], "once at")
        at = _as_timezone(at, tz)
        return at.isoformat() if at > floor else None
    if schedule_type == "interval":
        # ``interval`` is the one type whose phase IS ``after``, so it cannot use the floor
        # as a starting point without silently re-phasing the cadence. Jump a whole number
        # of periods instead: the grid is preserved exactly, and the result clears the floor.
        seconds = validated["seconds"]
        periods = 1
        if floor > local_after:
            periods = max(1, math.ceil((floor - local_after).total_seconds() / seconds))
            if local_after + timedelta(seconds=seconds * periods) <= floor:
                periods += 1
        return (local_after + timedelta(seconds=seconds * periods)).isoformat()
    if schedule_type == "rrule":
        anchor = validated.get("dtstart") or created_at or local_after
        anchor_dt = _as_timezone(_parse_datetime(anchor, "rrule dtstart"), tz)
        rule = rrulestr(validated["rrule"], dtstart=anchor_dt)
        next_run = rule.after(floor, inc=False)
        return next_run.astimezone(tz).isoformat() if next_run else None

    hour, minute = _time_parts(validated["time"])
    if schedule_type == "daily":
        candidate = floor.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= floor:
            candidate += timedelta(days=1)
        return candidate.isoformat()
    if schedule_type == "weekdays":
        candidate = floor.replace(hour=hour, minute=minute, second=0, microsecond=0)
        while candidate <= floor or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate.isoformat()
    if schedule_type == "weekly":
        candidate = floor.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidate += timedelta(days=(_WEEKDAYS[validated["day"]] - candidate.weekday()) % 7)
        if candidate <= floor:
            candidate += timedelta(days=7)
        return candidate.isoformat()
    day = validated["day"]
    candidate = _monthly_candidate(floor.year, floor.month, day, hour, minute, tz)
    if candidate <= floor:
        year, month = _next_month(floor.year, floor.month)
        candidate = _monthly_candidate(year, month, day, hour, minute, tz)
    return candidate.isoformat()


def ran_slot_floor(trigger: dict, scheduled_for: datetime) -> datetime:
    """Where the slot that just ran sits on the trigger's CURRENT clock — a reschedule floor.

    A trigger without ``tzid`` follows the machine's local zone, so its slots are wall-clock
    times ("09:00 on the 28th"), not instants. When the machine's zone moves between the
    run and its reschedule, the slot stamped 09:00+08:00 is 09:00+07:00 on the new clock —
    an hour LATER, so flooring on the stamped instant alone hands the same occurrence back
    and it runs twice (live incident 2026-09-28, "Monthly Kestrel Dash Salary"). An
    ``interval`` slot is an instant, and a ``tzid`` pins the clock, so both keep the stamp.
    """
    validated = validate_trigger(trigger, now=scheduled_for)
    tz = _timezone(validated.get("tzid"))
    if validated.get("tzid") is not None or validated.get("type") == "interval":
        return _as_timezone(scheduled_for, tz)
    return scheduled_for.replace(tzinfo=tz)


def describe_trigger(trigger: dict) -> str:
    """Return a compact, user-facing summary for a validated Automation trigger."""
    trigger = validate_trigger(trigger)
    if trigger["kind"] == "manual":
        return "Manual"
    if trigger["kind"] == "event":
        subject = trigger["when"].get("symbol") or trigger["event"]
        batch = {"each": "each event", "hour": "hourly batch", "day": "daily batch"}[
            trigger["limit"]
        ]
        return f"When {subject} {trigger['event']} changes ({batch})"
    schedule_type = trigger["type"]
    if schedule_type == "once":
        return f"Once at {trigger['at']}"
    if schedule_type == "daily":
        return f"Daily at {trigger['time']}"
    if schedule_type == "weekdays":
        return f"Weekdays at {trigger['time']}"
    if schedule_type == "weekly":
        return f"Weekly on {trigger['day'].title()} at {trigger['time']}"
    if schedule_type == "monthly":
        return f"Monthly on day {trigger['day']} at {trigger['time']}"
    if schedule_type == "interval":
        return f"Every {trigger['seconds']} seconds"
    return f"RRULE: {trigger['rrule']}"


def _validate_schedule_trigger(trigger: dict, now: datetime | None) -> dict:
    schedule_type = trigger.get("type")
    if schedule_type not in _SCHEDULE_TYPES:
        raise ValueError(
            f"unknown automation schedule type {schedule_type!r} — a schedule trigger needs a "
            "'type' key, one of: "
            'once {"at": ISO-8601}, daily/weekdays {"time": "HH:MM", "tzid"?}, '
            'weekly {"time", "day": mon..sun, "tzid"?}, '
            'monthly {"time", "day": 1-31, "tzid"?}, '
            'interval {"seconds": >=60}, rrule {"rrule", "tzid"?}'
        )
    allowed = {"kind", "type"}
    if schedule_type == "once":
        allowed.add("at")
    elif schedule_type in {"daily", "weekdays"}:
        allowed.update({"time", "tzid"})
    elif schedule_type == "weekly":
        allowed.update({"time", "day", "tzid"})
    elif schedule_type == "monthly":
        allowed.update({"time", "day", "tzid"})
    elif schedule_type == "interval":
        allowed.add("seconds")
    else:
        allowed.update({"rrule", "dtstart", "tzid"})
    _reject_unknown(trigger, allowed)
    out = {"kind": "schedule", "type": schedule_type}
    if schedule_type == "once":
        at = _parse_datetime(trigger.get("at"), "once at")
        out["at"] = at.isoformat()
    elif schedule_type in {"daily", "weekdays"}:
        out["time"] = _validate_time(trigger.get("time"))
        out.update(_timezone_field(trigger, now))
    elif schedule_type == "weekly":
        out["time"] = _validate_time(trigger.get("time"))
        day = str(trigger.get("day") or "").strip().lower()[:3]
        if day not in _WEEKDAYS:
            raise ValueError("weekly schedule day must be mon through sun")
        out["day"] = day
        out.update(_timezone_field(trigger, now))
    elif schedule_type == "monthly":
        out["time"] = _validate_time(trigger.get("time"))
        try:
            day = int(trigger.get("day"))
        except (TypeError, ValueError) as exc:
            raise ValueError("monthly schedule day must be 1 through 31") from exc
        if not 1 <= day <= 31:
            raise ValueError("monthly schedule day must be 1 through 31")
        out["day"] = day
        out.update(_timezone_field(trigger, now))
    elif schedule_type == "interval":
        try:
            seconds = int(trigger.get("seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError("interval schedule requires integer seconds") from exc
        if seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval seconds must be >= {MIN_INTERVAL_SECONDS}")
        out["seconds"] = seconds
    else:
        body = str(trigger.get("rrule") or "").strip()
        if not body or body.upper().startswith("RRULE:"):
            raise ValueError("rrule must be a non-empty body without a leading RRULE:")
        if not any(part.strip().upper().startswith("FREQ=") for part in body.split(";")):
            raise ValueError("rrule requires FREQ")
        if "FREQ=SECONDLY" in body.upper() or "FREQ=MINUTELY" in body.upper():
            raise ValueError("rrule frequency must not be sub-minute")
        out["rrule"] = body
        if trigger.get("dtstart") is not None:
            out["dtstart"] = _parse_datetime(trigger["dtstart"], "rrule dtstart").isoformat()
        out.update(_timezone_field(trigger, now))
        try:
            rrulestr(
                body,
                dtstart=_as_timezone(
                    _parse_datetime(out.get("dtstart") or now or datetime.now(), "rrule dtstart"),
                    _timezone(out.get("tzid")),
                ),
            )
        except Exception as exc:
            raise ValueError(f"invalid rrule: {exc}") from exc
    return out


def _reject_unknown(value: dict, allowed: set[str] | frozenset[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        rendered = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"unknown trigger keys: {rendered}")


def _timezone_field(trigger: dict, now: datetime | None) -> dict:
    if "tzid" not in trigger:
        return {}
    tzid = trigger["tzid"]
    if not isinstance(tzid, str) or not tzid.strip():
        raise ValueError("tzid must be a non-empty IANA timezone name")
    normalized = tzid.strip()
    _timezone(normalized)
    return {"tzid": normalized}


def _local_tz() -> tzinfo:
    """The owner's timezone — the server's own when none is stored — for a schedule that names none."""
    return user_zone() or datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def _timezone(tzid: str | None) -> tzinfo:
    if tzid is not None:
        try:
            return ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"unknown timezone {tzid!r}") from exc
    return _local_tz()


def _parse_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} requires an ISO-8601 datetime")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {label} datetime {value!r}") from exc


def _as_timezone(value: datetime, tz: tzinfo) -> datetime:
    return value.astimezone(tz) if value.tzinfo is not None else value.replace(tzinfo=tz)


def _validate_time(value: object) -> str:
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        raise ValueError("schedule time must be HH:MM")
    return value


def _time_parts(value: str) -> tuple[int, int]:
    return int(value[:2]), int(value[3:])


def _monthly_candidate(
    year: int, month: int, day: int, hour: int, minute: int, tz: tzinfo
) -> datetime:
    return datetime(
        year, month, min(day, calendar.monthrange(year, month)[1]), hour, minute, tzinfo=tz
    )


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)
