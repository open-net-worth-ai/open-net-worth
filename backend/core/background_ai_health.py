"""How each background AI task has been doing — for Settings → AI → Background AI tasks.

Background structured calls (the analyst, the news screen, briefings, …) used to fail in
silence: on 2026-11-19 Claude refused the analyst ~1.5k times a day for six days and nothing on
screen said so. `llm_seam.complete_json` records every call's outcome here, keyed by `source`.

In memory only, by design: recording costs no database write (the server had just been starved
by database load), and the record resets when the server restarts — the panel says so. Only a
classified failure KIND (and, for a refusal, its category code) is kept, never provider text,
which can echo the prompt and so the user's data. Thread-safe; bounded per source.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

WINDOW = timedelta(hours=24)
MAX_EVENTS_PER_SOURCE = 2000

_STARTED_AT = datetime.now(timezone.utc)
_lock = threading.Lock()
_events: dict[str, deque] = {}  # source -> deque[(at, ok)]
_last: dict[str, dict] = {}  # source -> {success_at, failure_at, kind, detail}

_REFUSAL = re.compile(
    r"usage polic|legal/aup|can't respond to this message|cannot respond to this message",
    re.IGNORECASE,
)
_REFUSAL_CATEGORY = re.compile(r"\[([a-z][a-z0-9_]{2,40})\]")
_RATE_LIMIT = re.compile(r"\b429\b|rate.?limit|too many requests|quota", re.IGNORECASE)
_AUTH = re.compile(
    r"\b40[13]\b|unauthori[sz]ed|forbidden|api.?key|not logged in|sign in", re.IGNORECASE
)
_TIMEOUT = re.compile(r"timed out|timeout", re.IGNORECASE)


def classify(exc: BaseException) -> tuple[str, str]:
    """(kind, detail) for a failed call. `detail` is a refusal's category code or the
    exception's type name — never the message itself."""
    text = f"{type(exc).__name__}: {exc}"
    if _REFUSAL.search(text):
        found = _REFUSAL_CATEGORY.search(text)
        return "refused", found.group(1) if found else ""
    if _RATE_LIMIT.search(text):
        return "rate_limited", ""
    if _AUTH.search(text):
        return "auth", ""
    if isinstance(exc, TimeoutError) or _TIMEOUT.search(text):
        return "timeout", ""
    return "error", type(exc).__name__


def record_success(source: str, *, at: datetime | None = None) -> None:
    _record(source, True, at or datetime.now(timezone.utc), None)


def record_failure(source: str, failure: tuple[str, str], *, at: datetime | None = None) -> None:
    _record(source, False, at or datetime.now(timezone.utc), failure)


def _record(source: str, ok: bool, at: datetime, failure: tuple[str, str] | None) -> None:
    key = source or "unknown"
    with _lock:
        events = _events.setdefault(key, deque(maxlen=MAX_EVENTS_PER_SOURCE))
        events.append((at, ok))
        last = _last.setdefault(key, {})
        if ok:
            if last.get("success_at") is None or at >= last["success_at"]:
                last["success_at"] = at
        elif last.get("failure_at") is None or at >= last["failure_at"]:
            last["failure_at"] = at
            last["kind"], last["detail"] = failure or ("error", "")


def snapshot(*, now: datetime | None = None) -> list[dict]:
    """One row per task that has run since the server started, most troubled first."""
    now = now or datetime.now(timezone.utc)
    since = now - WINDOW
    with _lock:
        rows = [_row(src, list(ev), dict(_last.get(src, {})), since) for src, ev in _events.items()]
    order = {"failing": 0, "recovering": 1, "working": 2}
    return sorted(rows, key=lambda r: (order[r["status"]], -r["failures_24h"], r["source"]))


def _row(source: str, events: list, last: dict, since: datetime) -> dict:
    recent = [ok for at, ok in events if at >= since]
    failures = sum(1 for ok in recent if not ok)
    success_at, failure_at = last.get("success_at"), last.get("failure_at")
    if failure_at is not None and (success_at is None or failure_at > success_at):
        status = "failing"
    elif failures:
        status = "recovering"
    else:
        status = "working"
    return {
        "source": source,
        "status": status,
        "calls_24h": len(recent),
        "failures_24h": failures,
        "last_success_at": success_at.isoformat() if success_at else None,
        "last_failure_at": failure_at.isoformat() if failure_at else None,
        "last_failure_kind": last.get("kind") if failure_at else None,
        "last_failure_detail": last.get("detail") if failure_at else None,
    }


def started_at() -> str:
    return _STARTED_AT.isoformat()


def _events_kept(source: str) -> int:
    with _lock:
        return len(_events.get(source, ()))


def reset_for_tests() -> None:
    with _lock:
        _events.clear()
        _last.clear()
