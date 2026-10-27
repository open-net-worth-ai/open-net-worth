"""The daily tidy pass: let unused facts and stale plans fade, then merge what overlaps.

Two deterministic rules need no model — a background-learned fact in Other with no citation,
mention, user edit or filing for ``UNUSED_DAYS`` is retired as ``unused`` (only while citations
are reaching the book at all; the user's own facts and You / Money never fade this way), and a
plan not mentioned, edited or filed for ``PLAN_STALE_DAYS`` is retired as ``stale`` (a retired
fact stays under "Moved out of memory" for ``RETIRED_PURGE_DAYS``, then goes). One memory-model
call then merges duplicate facts and near-duplicate topic names; a merged-away fact is retired
``merged``, never deleted. Every write goes through ``apply_memory_change``; a merge reply that
would retire more than half of any topic is refused whole. Facts still ``unsorted`` are never
touched — the one-time sort owns them. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``
(amendments 4 and 7).
"""

from __future__ import annotations

import asyncio
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


# A user's own signals keep a fact: a citation, the user saying it again, the user editing it,
# and — for a fact that predates the redesign — the day the sort filed it. Being shown in the
# prompt never counts.
_UNUSED_CLOCK = ("last_used_at", "mentioned_at", "edited_at", "sorted_at", "created_at")
_PLAN_CLOCK = ("mentioned_at", "edited_at", "sorted_at", "created_at")


def citations_arriving(facts: list[dict], today: date) -> bool:
    """True when some live fact was cited within ``UNUSED_DAYS``. While none was, usage
    tracking is evidently not reaching the book (a model that never cites), and retiring facts
    as unused would retire them for the missing signal, not for being unused."""
    cutoff = today - timedelta(days=ms.UNUSED_DAYS)
    return any((_day(f.get("last_used_at")) or cutoff) > cutoff for f in facts)


# Only a background-learned fact in Other can fade for want of use: the user's own words, and
# what Lucius knows about them and their money, stay until edited, forgotten or moved out.
# Plans and open items have their own clocks.
_UNUSED_SECTIONS = ("other",)


def _unused_ids(facts: list[dict], today: date) -> list[str]:
    """Implicit Other facts whose newest signal is older than ``UNUSED_DAYS``; nothing at all
    while no fact has been cited lately."""
    if not citations_arriving(facts, today):
        return []
    cutoff = today - timedelta(days=ms.UNUSED_DAYS)
    out = []
    for f in facts:
        if f.get("section") not in _UNUSED_SECTIONS or f.get("source") == "explicit":
            continue
        clock = _latest(f, *_UNUSED_CLOCK)
        if clock is not None and clock < cutoff:
            out.append(f["id"])
    return out


def _stale_plan_ids(facts: list[dict], today: date) -> list[str]:
    """Plans not mentioned, edited or (for a legacy one) filed for ``PLAN_STALE_DAYS``."""
    cutoff = today - timedelta(days=ms.PLAN_STALE_DAYS)
    out = []
    for f in facts:
        if f.get("section") != "plans":
            continue
        clock = _latest(f, *_PLAN_CLOCK)
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
    live = await asyncio.to_thread(store.active_facts, today)
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
    updates = [u for u in parsed.get("updates") or [] if isinstance(u, dict)]
    remove = ms.kept_apart(parsed.get("remove"), updates)
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
        logger.warning("memory tidy: refused a merge that retires over half of a topic")
        return {"merged": False, "refused": True}
    result = await asyncio.to_thread(
        apply_memory_change,
        {"updates": updates, "retire": [{"id": i, "reason": "merged"} for i in remove]},
        source="implicit",
        strict=False,
        today=today,
    )
    return {"merged": True, "updated": result["updated"], "merged_out": result["retired"]}


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


def _retire_and_purge(store: MemoryStore, today_date: date) -> tuple[int, dict]:
    """The deterministic half — no model: drop expired facts, retire unused facts and stale
    plans, purge what has been retired long enough. Synchronous DB work, run off the loop."""
    today = today_date.isoformat()
    removed_expired = memory.purge_expired(store)
    live = store.active_facts(today)
    if live and not citations_arriving(live, today_date):
        logger.info(
            "memory tidy: no fact cited in %d days; not retiring any as unused", ms.UNUSED_DAYS
        )
    retire = [{"id": fid, "reason": "unused"} for fid in _unused_ids(live, today_date)] + [
        {"id": fid, "reason": "stale"} for fid in _stale_plan_ids(live, today_date)
    ]
    purge = _expired_retired_ids(store, today_date)
    counts = apply_memory_change(
        {"retire": retire, "remove": purge}, source="implicit", strict=False, today=today
    )
    return removed_expired, counts


async def tidy(force: bool = False) -> dict:
    """Run the tidy pass once per day. Returns what it did (``ran`` False when memory is off or
    it already ran today). ``force`` skips only the once-a-day check."""
    today_date = user_today()
    today = today_date.isoformat()
    if not memory_writes_enabled() or (not force and await asyncio.to_thread(_already_ran, today)):
        return {"ran": False}

    store = MemoryStore()
    removed_expired, counts = await asyncio.to_thread(_retire_and_purge, store, today_date)
    merge = await _merge_duplicates(store, today)
    await asyncio.to_thread(_record_run, today)
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
