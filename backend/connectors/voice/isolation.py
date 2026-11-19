"""When LiveKit refuses voice isolation, new calls use Standard Krisp until it's worth trying again.

A refusal looks the same whether a project has used its free minutes or the network blinked, and a
call that tries and is refused goes without Krisp altogether. So one refusal waits an hour, which a
blink recovers from, and a second in a row waits for next month, when the allowance should be back:
a project past its allowance loses at most two calls to it, however rarely its owner calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.core.user_time import user_now
from backend.db.voice_config_store import VoiceConfigStore

RETRY_SOON = timedelta(hours=1)


def _next_month(now: datetime) -> datetime:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=32)).replace(day=1)


def note_refused() -> None:
    now = user_now()

    def change(refusal: dict | None) -> dict | None:
        refusal = refusal or {}
        until = _until(refusal)
        # A second call already under way when the first was refused: it's the same refusal.
        if until and until > now:
            return refusal
        count = int(refusal.get("count") or 0) + 1
        until = now + RETRY_SOON if count == 1 else _next_month(now)
        return {"count": count, "until": until.isoformat()}

    VoiceConfigStore().update_isolation_refusal(change)


def note_working() -> None:
    VoiceConfigStore().update_isolation_refusal(lambda _refusal: None)


def forget() -> None:
    """Picking the option again asks for a fresh try: the owner may have changed plan."""
    note_working()


def _until(refusal: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(refusal.get("until")))
    except ValueError:
        return None


def standard_until() -> str:
    """When voice isolation is next tried, while new calls are on Standard; otherwise blank."""
    refusal = VoiceConfigStore().isolation_refusal()
    until = _until(refusal)
    return str(refusal["until"]) if until and until > user_now() else ""


def filter_for_call(choice: str | None) -> str | None:
    return "standard" if choice == "isolation" and standard_until() else choice
