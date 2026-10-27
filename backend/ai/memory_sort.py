"""Sorting the legacy facts: file each one into a section and topic, or retire it.

A one-time migration stamped every fact written before the redesign ``unsorted``. One
memory-model call reads the oldest of them, with the topics already in use and the same routing
guide extraction follows, and says where each belongs or why it no longer does. Every write goes
through ``apply_memory_change`` as ``implicit`` (so an explicit fact stays explicit); a retired
fact keeps its row for ``RETIRED_PURGE_DAYS``. The sort never rewords a fact, with one exception:
a fact over ``FACT_MAX_CHARS`` may come back shortened (filing one runs the guards a new fact
meets, so it cannot be filed long; one carrying a full card, account or ID number is retired
``identifier``). A reply that is not the expected shape, or an item
that names a section or id it was not given, changes nothing for that item. The idle sweep calls
``sort_remaining`` while any unsorted fact is left; ``backend/scripts/migrate_memory_topics.py``
is the same pass, by hand. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``
(amendment 4).
"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

SORT_BATCH = 25
SORT_COOLDOWN_SECONDS = 3600

# Monotonic time before which the sweep does not try sorting again: a run that filed nothing
# (model down, or every fact skipped) would otherwise be repeated every tick.
_retry_after = 0.0

_SORT_SYSTEM = (
    "You file a financial advisor's older notes about their client into a tidy long-term "
    "memory. You are conservative: you never invent a fact and never change what a note says."
)

_ITEM_KEYS = ("id", "section", "topic", "content", "retire")
# `unused` and `merged` are the tidy's own verdicts: the sort has no usage data and no merge.
_TIDY_ONLY_REASONS = ("unused", "merged")
SORT_RETIRE_REASONS = tuple(r for r in ms.RETIRE_REASONS if r not in _TIDY_ONLY_REASONS)


def _unsorted(store: MemoryStore) -> list[dict]:
    """Remembered facts not yet filed, oldest first — a missing ``state`` predates the
    migration and counts."""
    rows = [f for f in store.remembered_facts() if f.get("state") != "live"]
    return sorted(rows, key=lambda f: (f.get("created_at", ""), f.get("id", "")))


def has_unsorted() -> bool:
    return bool(_unsorted(MemoryStore()))


def _topics_in_use(store: MemoryStore) -> str:
    seen: dict[str, dict[str, str]] = {}
    for f in store.active_facts():
        if f.get("section") in ms.SECTIONS and f.get("topic"):
            seen.setdefault(f["section"], {}).setdefault(ms.topic_key(f["topic"]), f["topic"])
    if not seen:
        return "(none yet)"
    return "; ".join(
        f"{ms.SECTIONS[section]}: {', '.join(sorted(names.values()))}"
        for section, names in seen.items()
    )


def _too_long(fact: dict) -> bool:
    return len(" ".join(str(fact.get("content") or "").split())) > ms.FACT_MAX_CHARS


def _listed(fact: dict) -> str:
    mark = f" [over {ms.FACT_MAX_CHARS} characters]" if _too_long(fact) else ""
    return f"{fact['id']}{mark}: {fact.get('content', '')}"


def _sort_prompt(batch: list[dict], topics: str, today: str) -> str:
    listing = "\n".join(_listed(f) for f in batch)
    suggested = "; ".join(
        f"{ms.SECTIONS[k]}: {', '.join(v)}" for k, v in ms.SUGGESTED_TOPICS.items() if v
    )
    sections = ", ".join(f'"{k}" ({v})' for k, v in ms.SECTIONS.items())
    return f"""Today is {today}.

These notes about the client were written before memory was organised into sections. For
each one decide where it belongs, following the same rules used for new notes.

{ms.ROUTING_GUIDE}

Sections: {sections}. A section's notes are grouped under a short topic name
(1-3 words); open_items take no topic. Suggested topics — {suggested}. Reuse a topic already in
use rather than coining a near-duplicate.

Topics in use: {topics}

For each note, reply with ONE of:
- file it: {{"id": "...", "section": "<key>", "topic": "<name>"}}. Never change its words —
  except a note marked [over {ms.FACT_MAX_CHARS} characters], which MUST carry a "content"
  that says the same thing in at most {ms.FACT_MAX_CHARS} characters, adding nothing.
- retire it, when the guide says it belongs outside memory or it is out of date:
  {{"id": "...", "retire": "<reason>"}} with the reason one of: {", ".join(SORT_RETIRE_REASONS)}.
  Retire only when EVERYTHING the note says already lives in that home: "profile" only for a
  Settings profile field itself (an employer, job or language is filed under You · Profile), "ledger" only for a balance,
  holding, price, trade or finished task — a plan or aim is never "ledger", even with an
  amount or a date.
A note you are unsure about: file it under "other" rather than retiring it.

Notes:
{listing}

Return ONLY JSON: {{"facts": [{{"id": "...", "section": "...", "topic": "..."}}]}}"""


def _shortened(raw, original: dict) -> str | None:
    """The sort's one licence to edit: new wording for a note over the length limit, itself
    within it. Anything else it proposes for the text is dropped."""
    if not isinstance(raw, str) or not _too_long(original):
        return None
    text = " ".join(raw.split())
    return text if 0 < len(text) <= ms.FACT_MAX_CHARS else None


def _clean_item(raw, shown: dict[str, dict]) -> dict | None:
    """One reply item reduced to the keys it may carry, or None when it is unusable. ``shown``
    maps each offered id to its fact."""
    if not isinstance(raw, dict):
        logger.debug("memory sort: ignored a reply item that is not an object")
        return None
    item = {k: raw[k] for k in _ITEM_KEYS if raw.get(k) is not None}
    fid = item.get("id")
    if not isinstance(fid, str) or fid not in shown:
        logger.debug("memory sort: ignored an id it was not shown: %r", fid)
        return None
    if item.get("retire"):
        if item["retire"] in _TIDY_ONLY_REASONS:
            logger.debug("memory sort: ignored %s, a tidy-only reason %r", fid, item["retire"])
            return None
        reason = item["retire"] if item["retire"] in ms.RETIRE_REASONS else "stale"
        return {"id": fid, "retire": reason}
    if "section" not in item:
        logger.debug("memory sort: %s came back with nothing to change", fid)
        return None
    section = ms.normalise_section(item["section"])
    if section is None:
        logger.debug("memory sort: ignored %s, unknown section %r", fid, item["section"])
        return None
    out: dict = {"id": fid, "section": section}
    if isinstance(item.get("topic"), str):
        out["topic"] = item["topic"]
    content = _shortened(item.get("content"), shown[fid])
    if content is not None:
        out["content"] = content
    return out


def _parse_items(reply, shown: dict[str, dict]) -> list[dict] | None:
    parsed = memory._parse_json(reply)
    facts = parsed.get("facts") if isinstance(parsed, dict) else None
    if not isinstance(facts, list):
        logger.warning("memory sort returned a reply that is not the expected shape")
        return None
    return [item for item in (_clean_item(raw, shown) for raw in facts) if item]


async def propose(limit: int = SORT_BATCH, exclude: frozenset[str] | set[str] = frozenset()):
    """Ask the model where the oldest ``limit`` unsorted facts (not in ``exclude``) belong.
    Returns ``(batch, items)``; nothing is written. ``items`` is ``None`` when the call failed
    or the reply was not the expected shape, ``[]`` when the model had nothing to say, and the
    whole result is ``([], [])`` while memory writing is off."""
    if not memory_writes_enabled():
        return [], []
    store = MemoryStore()
    batch = [f for f in _unsorted(store) if f["id"] not in exclude][: max(1, int(limit))]
    if not batch:
        return [], []
    prompt = _sort_prompt(batch, _topics_in_use(store), user_today().isoformat())
    try:
        reply = await memory._provider().complete(
            prompt,
            system=_SORT_SYSTEM,
            model=memory._memory_model(),
            max_tokens=4096,
            source="memory_sort",
            response_format=memory._MEMORY_JSON,
            effort="none",
        )
    except Exception as exc:
        logger.warning("memory sort LLM call failed: %s", exc)
        return batch, None
    return batch, _parse_items(reply, {f["id"]: f for f in batch})


def to_ops(items: list[dict]) -> dict:
    retire = [{"id": i["id"], "reason": i["retire"]} for i in items if "retire" in i]
    updates = [
        {k: i[k] for k in ("id", "section", "topic", "content") if k in i}
        for i in items
        if "retire" not in i
    ]
    return {"updates": updates, "retire": retire}


async def sort_unsorted(limit: int = SORT_BATCH, exclude=frozenset()) -> dict:
    """File the oldest ``limit`` unsorted facts (not in ``exclude``) in one model call.
    Returns how many were ``sorted`` (now live), ``retired``, how many are ``remaining``
    unsorted, the ``offered`` ids, and ``failed`` when the model call or reply was unusable."""
    batch, items = await propose(limit, exclude)
    if items:
        await asyncio.to_thread(apply_memory_change, to_ops(items), source="implicit", strict=False)
    after = {f["id"]: f for f in MemoryStore().all_facts()}
    ids = [f["id"] for f in batch]
    sorted_n = sum(1 for i in ids if after.get(i, {}).get("state") == "live")
    retired_n = sum(1 for i in ids if after.get(i, {}).get("state") == "retired")
    remaining = len(_unsorted(MemoryStore()))
    if batch:
        logger.info(
            "memory sort: %d filed, %d retired, %d left unsorted", sorted_n, retired_n, remaining
        )
    return {
        "sorted": sorted_n,
        "retired": retired_n,
        "remaining": remaining,
        "offered": ids,
        "failed": bool(batch) and items is None,
    }


async def sort_remaining(limit: int = SORT_BATCH) -> dict:
    """Offer every unsorted fact once, then — when that pass filed something — once more for
    whatever the model skipped. A fact is never offered a third time in the same run, so one it
    keeps skipping or the choke keeps refusing cannot hold back the ones behind it; a pass that
    filed nothing is not repeated (the cooldown takes over), and the run stops early when the
    model call itself fails."""
    total = {"sorted": 0, "retired": 0, "remaining": len(_unsorted(MemoryStore()))}
    for attempt in range(2):
        if attempt and not total["sorted"] + total["retired"]:
            break
        offered: set[str] = set()
        while total["remaining"]:
            out = await sort_unsorted(limit, offered)
            if not out["offered"]:
                break
            offered.update(out["offered"])
            total["sorted"] += out["sorted"]
            total["retired"] += out["retired"]
            total["remaining"] = out["remaining"]
            if out["failed"]:
                return total
        if not total["remaining"]:
            break
    return total


async def sort_tick() -> dict | None:
    """The sweep's sorting step: run ``sort_remaining`` while any fact is unsorted, unless the
    last run filed nothing less than ``SORT_COOLDOWN_SECONDS`` ago. Returns None when skipped."""
    global _retry_after
    if time.monotonic() < _retry_after or not await asyncio.to_thread(has_unsorted):
        return None
    out = await sort_remaining()
    progressed = out["sorted"] + out["retired"] > 0
    _retry_after = 0.0 if progressed else time.monotonic() + SORT_COOLDOWN_SECONDS
    return out


def describe(item: dict) -> str:
    """``id | section · topic | new text`` or ``id | retire: reason`` for a printed plan."""
    if "retire" in item:
        return f"{item['id']} | retire: {item['retire']}"
    section = item.get("section", "")
    where = f"{section} · {ms.normalise_topic(item['topic'])}" if item.get("topic") else section
    text = item.get("content") or "(text unchanged)"
    return f"{item['id']} | {where or '(unfiled)'} | {text}"


__all__ = ["describe", "has_unsorted", "propose", "sort_remaining", "sort_unsorted", "to_ops"]
