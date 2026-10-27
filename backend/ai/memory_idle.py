"""When memory is written: after a conversation goes quiet, and when an automation run ends.

The sweep reads each conversation's newest message time and its extraction cursor, and runs
the extractor on what is past the cursor once nothing has been said for ``MEMORY_IDLE_MINUTES``.
The cursor advances only after a pass that succeeded, so a failed one is retried by the next
sweep (up to ``MAX_FAILED_EXTRACTIONS`` in a row) and a restart never extracts the same messages
twice. Captures are never swept. Nothing here runs per turn.

``memory_sweep_loop`` is started in the one worker that owns the scheduler lock (``main.py``);
in any other it would double-extract. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``
(amendment 4).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from backend.ai import memory
from backend.ai.memory_structure import MEMORY_IDLE_MINUTES, MEMORY_SWEEP_SECONDS
from backend.db.memory_settings_store import memory_writes_enabled
from backend.db.session_store import SessionStore

logger = logging.getLogger(__name__)

# A conversation untouched for longer than this is not picked up by the sweep: a first sweep
# on an old book would otherwise pay one model call for every conversation ever held.
SWEEP_LOOKBACK_DAYS = 14

# After this many extractions of one conversation fail in a row (a reply cut off or unparseable
# every time), it is not tried again until it has new messages — otherwise the same paid call
# would repeat every sweep for the whole lookback window. The count lives on the conversation's
# extraction cursor, so a restart keeps it; a pass that succeeds clears it.
MAX_FAILED_EXTRACTIONS = 3

# The sessions an extraction is running for right now — the sweep and an automation run's own
# completion must not both read the same messages.
_in_flight: set[str] = set()

# Extractions handed off by a finished automation run. A task only the loop references can be
# garbage-collected mid-flight, so each is held here until it is done.
_background: set[asyncio.Task] = set()


def _parse(stamp) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _is_internal(session_id: str) -> bool:
    from backend.core.chat_session import is_internal_session

    return is_internal_session(session_id)


def _due(candidate: dict, now: datetime) -> bool:
    """Idle for long enough, recent enough, and something past the cursor. Never a Capture (its
    user turn is the app's own prompt template, not words the user typed), and never a
    conversation whose extraction has failed ``MAX_FAILED_EXTRACTIONS`` times in a row until it
    has messages that were not there at the last failure."""
    if candidate["meta"].get("origin") == "capture":
        return False
    if candidate["count"] <= candidate["cursor"]:
        return False
    if candidate.get("failures", 0) >= MAX_FAILED_EXTRACTIONS and candidate[
        "count"
    ] <= candidate.get("failed_len", 0):
        return False
    last = _parse(candidate["last_message_at"]) or _parse(candidate["meta"].get("updated_at"))
    if last is None:
        return False
    if now - last < timedelta(minutes=MEMORY_IDLE_MINUTES):
        return False
    return now - last <= timedelta(days=SWEEP_LOOKBACK_DAYS)


async def extract_session(session_id: str) -> bool:
    """Run the extractor over a session's messages past its cursor. True when the pass
    concluded (the cursor now sits at the end of the conversation).

    Best-effort: a failure is logged and swallowed, and leaves the cursor where it was so the
    next sweep retries. A no-op while memory is off, and while the same session is already being
    extracted.
    """
    if session_id in _in_flight or not memory_writes_enabled():
        return False
    _in_flight.add(session_id)
    history: list = []
    try:
        store = SessionStore()
        history = await asyncio.to_thread(store.get_messages, session_id)
        if not history:
            return False
        await memory.extract_and_merge(history, session_id=session_id)
        concluded = await asyncio.to_thread(memory._load_cursor, session_id) >= len(history)
    except Exception:
        logger.exception("memory extraction failed for session %s (non-fatal)", session_id[:8])
        concluded = False
    finally:
        _in_flight.discard(session_id)
    if history and not concluded:
        await asyncio.to_thread(_note_failure, session_id, len(history))
    return concluded


def _note_failure(session_id: str, length: int) -> None:
    """Count one more failed extraction on the session's cursor doc. Best-effort."""
    try:
        store = SessionStore()
        doc = store.get_extraction_cursor(session_id)
        doc = doc if isinstance(doc, dict) else {}
        failures = int(doc.get("failures") or 0) + 1
        store.set_extraction_cursor(
            session_id,
            {
                "last_extracted_len": int(doc.get("last_extracted_len") or 0),
                "failures": failures,
                "failed_len": length,
            },
        )
    except Exception:
        logger.debug("could not record a failed extraction", exc_info=True)
        return
    if failures >= MAX_FAILED_EXTRACTIONS:
        logger.warning(
            "memory extraction failed %d times in a row for session %s; waiting for new messages",
            failures,
            session_id[:8],
        )


def schedule_extraction(session_id: str) -> None:
    """Extract a session in the background, after the caller has gone on. A run that has
    settled must not wait on a model call, and a Stop or shutdown during that call must not
    touch the run — the sweep picks the session up if this task is lost."""
    task = asyncio.get_running_loop().create_task(
        extract_session(session_id), name=f"memory-extract-{session_id[:8]}"
    )
    _background.add(task)
    task.add_done_callback(_background_done)


def _background_done(task: asyncio.Task) -> None:
    _background.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("background memory extraction failed", exc_info=task.exception())


async def cancel_background() -> None:
    """Cancel every handed-off extraction and wait for them to stop (shutdown)."""
    tasks = list(_background)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def sweep_idle_conversations(now: datetime | None = None) -> int:
    """Extract every conversation that has gone quiet. Returns how many were extracted."""
    if not memory_writes_enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    candidates = await asyncio.to_thread(SessionStore().extraction_candidates)
    ran = 0
    for candidate in candidates:
        session_id = candidate["id"]
        if _is_internal(session_id) or not _due(candidate, now):
            continue
        if await extract_session(session_id):
            ran += 1
    if ran:
        logger.info("memory sweep: extracted %d idle conversation(s)", ran)
    return ran


async def memory_sweep_loop() -> None:
    """Sweep at startup, then every ``MEMORY_SWEEP_SECONDS``; the daily tidy rides the same
    loop, and so does sorting any legacy facts still unsorted, then refreshing the topic
    summaries. A no-op pass while "Generate memory from chats" is off."""
    from backend.ai import memory_sort
    from backend.ai.memory_tidy import tidy

    while True:
        try:
            if memory_writes_enabled():
                await sweep_idle_conversations()
                await tidy()
                await memory_sort.sort_tick()
                await refresh_topic_summaries()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("memory sweep failed (non-fatal)")
        await asyncio.sleep(MEMORY_SWEEP_SECONDS)


async def refresh_topic_summaries() -> None:
    """Bring each topic's one-line summary up to date. A failure is logged and swallowed: a
    summary is display-only and is retried next sweep."""
    from backend.ai.memory_summaries import refresh_summaries

    try:
        await refresh_summaries()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("memory topic summaries failed (non-fatal)")
