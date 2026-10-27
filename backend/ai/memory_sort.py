"""Sorting the legacy facts: file each one into a section and topic, or retire it.

A one-time migration stamped every fact written before the redesign ``unsorted``. One
memory-model call reads the oldest of them, with the topics already in use and the same routing
guide extraction follows, and says where each belongs or why it no longer does. Every write goes
through ``apply_memory_change`` as ``implicit`` (so an explicit fact stays explicit); a retired
fact keeps its row for ``RETIRED_PURGE_DAYS``. A reply that is not the expected shape, or an item
that names a section or id it was not given, changes nothing for that item. The idle sweep calls
``sort_remaining`` while any unsorted fact is left; ``backend/scripts/migrate_memory_topics.py``
is the same pass, by hand. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``
(amendment 4).
"""

from __future__ import annotations

import logging

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

SORT_BATCH = 25

_SORT_SYSTEM = (
    "You file a financial advisor's older notes about their client into a tidy long-term "
    "memory. You are conservative: you never invent a fact and never change what a note says."
)

_ITEM_KEYS = ("id", "section", "topic", "content", "retire")


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


def _sort_prompt(batch: list[dict], topics: str, today: str) -> str:
    listing = "\n".join(f"{f['id']}: {f.get('content', '')}" for f in batch)
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
- file it: {{"id": "...", "section": "<key>", "topic": "<name>"}}. Add "content" only to
  rewrite it in the third person, or to cut it under {ms.FACT_MAX_CHARS} characters, keeping its
  meaning. Never add detail it does not already contain.
- retire it, when the guide says it belongs outside memory or it is out of date:
  {{"id": "...", "retire": "<reason>"}} with the reason one of: {", ".join(ms.RETIRE_REASONS)}.
A note you are unsure about: file it under "other" rather than retiring it.

Notes:
{listing}

Return ONLY JSON: {{"facts": [{{"id": "...", "section": "...", "topic": "...", "content": "..."}}]}}"""


def _clean_item(raw, shown: set[str]) -> dict | None:
    """One reply item reduced to the keys it may carry, or None when it is unusable."""
    if not isinstance(raw, dict):
        logger.debug("memory sort: ignored a reply item that is not an object")
        return None
    item = {k: raw[k] for k in _ITEM_KEYS if raw.get(k) is not None}
    fid = item.get("id")
    if not isinstance(fid, str) or fid not in shown:
        logger.debug("memory sort: ignored an id it was not shown: %r", fid)
        return None
    if item.get("retire"):
        reason = item["retire"] if item["retire"] in ms.RETIRE_REASONS else "stale"
        return {"id": fid, "retire": reason}
    out: dict = {"id": fid}
    if "section" in item:
        section = ms.normalise_section(item["section"])
        if section is None:
            logger.debug("memory sort: ignored %s, unknown section %r", fid, item["section"])
            return None
        out["section"] = section
        if isinstance(item.get("topic"), str):
            out["topic"] = item["topic"]
    if isinstance(item.get("content"), str):
        out["content"] = item["content"]
    if len(out) == 1:
        logger.debug("memory sort: %s came back with nothing to change", fid)
        return None
    return out


def _parse_items(reply, shown: set[str]) -> list[dict]:
    parsed = memory._parse_json(reply)
    facts = parsed.get("facts") if isinstance(parsed, dict) else None
    if not isinstance(facts, list):
        logger.warning("memory sort returned a reply that is not the expected shape")
        return []
    return [item for item in (_clean_item(raw, shown) for raw in facts) if item]


async def propose(limit: int = SORT_BATCH, exclude: frozenset[str] | set[str] = frozenset()):
    """Ask the model where the oldest ``limit`` unsorted facts (not in ``exclude``) belong.
    Returns ``(batch, items)``; nothing is written. ``([], [])`` while memory writing is off."""
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
        return batch, []
    return batch, _parse_items(reply, {f["id"] for f in batch})


def to_ops(items: list[dict]) -> dict:
    retire = [{"id": i["id"], "reason": i["retire"]} for i in items if "retire" in i]
    updates = [
        {k: i[k] for k in ("id", "section", "topic", "content") if k in i}
        for i in items
        if "retire" not in i
    ]
    return {"updates": updates, "retire": retire}


async def sort_unsorted(limit: int = SORT_BATCH) -> dict:
    """File the oldest ``limit`` unsorted facts in one model call. Returns how many were
    ``sorted`` (now live), ``retired`` and how many are ``remaining`` unsorted."""
    batch, items = await propose(limit)
    if items:
        apply_memory_change(to_ops(items), source="implicit", strict=False)
    after = {f["id"]: f for f in MemoryStore().all_facts()}
    ids = [f["id"] for f in batch]
    sorted_n = sum(1 for i in ids if after.get(i, {}).get("state") == "live")
    retired_n = sum(1 for i in ids if after.get(i, {}).get("state") == "retired")
    remaining = len(_unsorted(MemoryStore()))
    if batch:
        logger.info(
            "memory sort: %d filed, %d retired, %d left unsorted", sorted_n, retired_n, remaining
        )
    return {"sorted": sorted_n, "retired": retired_n, "remaining": remaining}


async def sort_remaining(limit: int = SORT_BATCH) -> dict:
    """Run ``sort_unsorted`` until nothing is left or a run makes no progress (a model that
    ignores a whole batch would otherwise be asked about the same facts forever)."""
    total = {"sorted": 0, "retired": 0, "remaining": len(_unsorted(MemoryStore()))}
    while total["remaining"]:
        out = await sort_unsorted(limit)
        total["sorted"] += out["sorted"]
        total["retired"] += out["retired"]
        total["remaining"] = out["remaining"]
        if out["sorted"] + out["retired"] == 0:
            break
    return total


def describe(item: dict) -> str:
    """``id | section · topic | new text`` or ``id | retire: reason`` for a printed plan."""
    if "retire" in item:
        return f"{item['id']} | retire: {item['retire']}"
    section = item.get("section", "")
    where = f"{section} · {ms.normalise_topic(item['topic'])}" if item.get("topic") else section
    text = item.get("content") or "(text unchanged)"
    return f"{item['id']} | {where or '(unfiled)'} | {text}"


__all__ = ["describe", "has_unsorted", "propose", "sort_remaining", "sort_unsorted", "to_ops"]
