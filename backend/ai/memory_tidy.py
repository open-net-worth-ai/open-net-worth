"""The daily tidy pass: let unused facts and stale plans fade, then merge what overlaps.

Two deterministic rules need no model — a live fact Lucius has not cited for ``UNUSED_DAYS`` is
retired as ``unused``, and a plan not mentioned for ``PLAN_STALE_DAYS`` is retired as ``stale``
(a retired fact stays under "Moved out of memory" for ``RETIRED_PURGE_DAYS``, then goes). One
memory-model call then merges duplicate facts and near-duplicate topic names. Every write goes
through ``apply_memory_change``; a merge reply that would remove more than half of any topic is
refused whole. Facts still ``unsorted`` are never touched — the one-time sort owns them.
ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 4).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_consolidate_state_store import MemoryConsolidateStateStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

_TIDY_SYSTEM = (
    "You are the hygiene pass over a financial advisor's long-term memory about their client. "
    "You merge duplicates and near-duplicate topic names and never invent facts."
)


def _day(stamp) -> date | None:
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def _latest(fact: dict, *fields: str) -> date | None:
    days = [d for d in (_day(fact.get(f)) for f in fields if fact.get(f)) if d]
    return max(days) if days else None


def _unused_ids(facts: list[dict], today: date) -> list[str]:
    """Live facts (not plans — their own rule governs) whose last use, sorting or creation is
    older than ``UNUSED_DAYS``. Being shown in the prompt never counts; only a citation does."""
    cutoff = today - timedelta(days=ms.UNUSED_DAYS)
    out = []
    for f in facts:
        if f.get("section") in (ms.OPEN_ITEMS, "plans"):
            continue
        clock = _latest(f, "last_used_at", "sorted_at", "created_at")
        if clock is not None and clock < cutoff:
            out.append(f["id"])
    return out


def _stale_plan_ids(facts: list[dict], today: date) -> list[str]:
    cutoff = today - timedelta(days=ms.PLAN_STALE_DAYS)
    out = []
    for f in facts:
        if f.get("section") != "plans":
            continue
        clock = _latest(f, "mentioned_at", "created_at")
        if clock is not None and clock < cutoff:
            out.append(f["id"])
    return out


def _expired_retired_ids(store: MemoryStore, today: date) -> list[str]:
    cutoff = today - timedelta(days=ms.RETIRED_PURGE_DAYS)
    return [
        f["id"]
        for f in store.all_facts()
        if f.get("state") == "retired" and (_day(f.get("retired_at")) or today) < cutoff
    ]


def _topic_key_of(fact: dict) -> tuple[str, str]:
    return fact.get("section") or "", ms.topic_key(fact.get("topic"))


def would_gut_a_topic(live: list[dict], removed: set[str]) -> bool:
    """True when ``removed`` takes out more than half of any one topic's live facts."""
    groups: dict[tuple[str, str], list[str]] = {}
    for f in live:
        groups.setdefault(_topic_key_of(f), []).append(f["id"])
    return any(
        len(removed.intersection(ids)) * 2 > len(ids)
        for ids in groups.values()
        if removed.intersection(ids)
    )


def _merge_prompt(live: list[dict]) -> str:
    listing = json.dumps(
        [
            {
                "id": f["id"],
                "section": f.get("section"),
                "topic": f.get("topic"),
                "content": f.get("content", ""),
            }
            for f in live
        ],
        ensure_ascii=False,
    )
    return f"""Clean up this financial advisor's memory about their client.

Each fact has a section, a topic and a text. Do two things, and only these:
- Merge duplicates: when several facts say the same thing, keep the best-worded one (reuse
  its id, rewriting its content only if the others add something), and list the rest under
  "remove". On a contradiction keep the newer wording.
- Merge near-duplicate topic names ("Home" and "Housing"): update the facts of the lesser
  name to the other with {{"id", "topic"}}.
Never delete a fact that says something no other fact says. Never invent a fact. A fact that
needs no change must not appear in the reply.

Current memory (JSON):
{listing}

Return ONLY JSON:
{{
  "updates": [{{"id": "...", "content": "optional rewrite", "topic": "optional new topic"}}],
  "remove":  ["id", ...]
}}
Nothing to change → {{"updates": [], "remove": []}}."""


async def _merge_duplicates(store: MemoryStore, today: str) -> dict:
    live = store.active_facts(today)
    if len(live) < 2:
        return {"merged": False}
    try:
        reply = await memory._provider().complete(
            _merge_prompt(live),
            system=_TIDY_SYSTEM,
            model=memory._memory_model(),
            max_tokens=2048,
            source="memory_tidy",
            response_format=memory._MEMORY_JSON,
            effort="none",
        )
    except Exception as exc:
        logger.warning("memory tidy LLM call failed: %s", exc)
        return {"merged": False}
    parsed = memory._parse_json(reply)
    if not isinstance(parsed, dict):
        logger.warning("memory tidy returned an unparseable reply")
        return {"merged": False}
    shown = {f["id"] for f in live}
    remove = [i for i in parsed.get("remove") or [] if isinstance(i, str)]
    updates = [u for u in parsed.get("updates") or [] if isinstance(u, dict)]
    dropped = [i for i in remove if i not in shown] + [
        u.get("id") for u in updates if u.get("id") not in shown
    ]
    if dropped:
        logger.debug("memory tidy: ignored ids it was not shown: %s", dropped)
    remove = [i for i in remove if i in shown]
    updates = [
        {k: u[k] for k in ("id", "content", "topic") if k in u}
        for u in updates
        if u.get("id") in shown
    ]
    if would_gut_a_topic(live, set(remove)):
        logger.warning("memory tidy: refused a merge that removes over half of a topic")
        return {"merged": False, "refused": True}
    result = apply_memory_change(
        {"updates": updates, "remove": remove}, source="implicit", strict=False, today=today
    )
    return {"merged": True, "updated": result["updated"], "removed": result["removed"]}


def _already_ran(today: str) -> bool:
    try:
        return (MemoryConsolidateStateStore().get() or {}).get("last_tidy") == today
    except Exception:
        logger.warning("could not read the tidy date; tidying anyway", exc_info=True)
        return False


def _record_run(today: str) -> None:
    try:
        MemoryConsolidateStateStore().save({"last_tidy": today})
    except Exception:
        logger.warning("could not persist the tidy date", exc_info=True)


async def tidy(force: bool = False) -> dict:
    """Run the tidy pass once per day. Returns what it did (``ran`` False when memory is off or
    it already ran today). ``force`` skips only the once-a-day check."""
    today_date = user_today()
    today = today_date.isoformat()
    if not memory_writes_enabled() or (not force and _already_ran(today)):
        return {"ran": False}

    store = MemoryStore()
    removed_expired = memory.purge_expired(store)
    live = store.active_facts(today)
    retire = [{"id": fid, "reason": "unused"} for fid in _unused_ids(live, today_date)] + [
        {"id": fid, "reason": "stale"} for fid in _stale_plan_ids(live, today_date)
    ]
    purge = _expired_retired_ids(store, today_date)
    counts = apply_memory_change(
        {"retire": retire, "remove": purge}, source="implicit", strict=False, today=today
    )
    merge = await _merge_duplicates(store, today)
    _record_run(today)
    logger.info(
        "memory tidy: %d expired, %d retired, %d purged, merge=%s",
        removed_expired,
        counts["retired"],
        counts["removed"],
        merge,
    )
    return {
        "ran": True,
        "expired": removed_expired,
        "retired": counts["retired"],
        "purged": counts["removed"],
        **merge,
    }
