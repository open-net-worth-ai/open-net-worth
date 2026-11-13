"""When each connector's data sync runs next, and how many bank reads each account has left.

Runtime state (``runtime_state`` namespace ``bank_data_sync``, checkpoint-excluded: a rewind must
never hand back reads a bank already counted). Per provider: ``{last_run_at, next_due_at,
last_status, last_error, failures, reads: {provider_account_id: [iso, …]}}``.

Reads are counted in a ROLLING 24 h, per provider account — every transaction read the sync
makes, scheduled or "Sync now", is noted BEFORE the call, so a read the bank counted is never
forgotten because the call then failed. Failures back off instead of retrying: a sync never
loops on a bank that said no."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state
from backend.services.aggregation.providers import Provider

NAMESPACE = "bank_data_sync"
WINDOW = timedelta(hours=24)
_lock = threading.Lock()
_MINUTES = re.compile(r"resets in about (\d+) min")


def _store() -> NamespacedSettingsStore:
    return NamespacedSettingsStore(NAMESPACE, table=runtime_state)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: object) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def state(key: str) -> dict:
    return dict((_store().get() or {}).get(key) or {})


def update(key: str, **fields) -> dict:
    with _lock:
        store = _store()
        doc = dict(store.get() or {})
        entry = {**dict(doc.get(key) or {}), **fields}
        doc[key] = entry
        store.save(doc)
    return entry


def _recent(stamps: list, at: datetime) -> list[datetime]:
    out = [d for d in (_parse(s) for s in stamps or []) if d is not None]
    return sorted(d for d in out if at - d < WINDOW)


def reads_left(p: Provider, provider_account_id: str, at: Optional[datetime] = None):
    """``(left, next_read_at)`` — ``left`` is None when the provider has no app-side cap;
    ``next_read_at`` is when the oldest counted read leaves the 24 h window (None if a read is
    possible now)."""
    if p.daily_reads is None:
        return None, None
    at = at or now()
    recent = _recent((state(p.key).get("reads") or {}).get(provider_account_id) or [], at)
    left = max(0, p.daily_reads - len(recent))
    return left, (None if left else recent[0] + WINDOW)


def note_read(p: Provider, provider_account_id: str, at: Optional[datetime] = None) -> None:
    at = at or now()
    with _lock:
        store = _store()
        doc = dict(store.get() or {})
        entry = dict(doc.get(p.key) or {})
        reads = dict(entry.get("reads") or {})
        kept = [_iso(d) for d in _recent(reads.get(provider_account_id) or [], at)]
        reads[provider_account_id] = kept + [_iso(at)]
        entry["reads"] = reads
        doc[p.key] = entry
        store.save(doc)


def interval(p: Provider) -> timedelta:
    return timedelta(hours=p.sync_interval_hours)


def backoff(p: Provider, status: str, failures: int, error: Optional[str]) -> timedelta:
    """How long to wait after a failed run. Never shorter than 30 minutes, never a loop."""
    if status == "rate_limited":
        match = _MINUTES.search(error or "")
        return timedelta(minutes=int(match.group(1)) + 5) if match else timedelta(hours=12)
    if status in ("auth_error", "reconnect_needed"):
        return timedelta(hours=24)
    return min(interval(p), timedelta(minutes=30) * (2 ** max(0, failures - 1)))


def is_due(p: Provider, at: Optional[datetime] = None) -> bool:
    at = at or now()
    due = _parse(state(p.key).get("next_due_at"))
    return due is None or at >= due


def record_outcome(p: Provider, status: str, error: Optional[str], at: Optional[datetime] = None):
    """After a run: when the next one is due, and the failure streak behind any backoff."""
    at = at or now()
    prior = state(p.key)
    if status in ("ok", "partial"):
        failures, wait = 0, interval(p)
    else:
        failures = int(prior.get("failures") or 0) + 1
        wait = backoff(p, status, failures, error)
    return update(
        p.key,
        last_run_at=_iso(at),
        next_due_at=_iso(at + wait),
        last_status=status,
        last_error=error,
        failures=failures,
    )


def schedule_from_now(p: Provider, at: Optional[datetime] = None) -> None:
    """Turning data sync on: the first run is due now."""
    update(p.key, next_due_at=_iso(at or now()), failures=0)
