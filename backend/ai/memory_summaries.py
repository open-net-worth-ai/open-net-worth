"""The one-line Summary on each memory topic's page ("Who they are — work, background, languages").

Display-only: a summary is never a fact, never passes through the fact write choke and is never
put in Lucius's prompt. The idle sweep calls ``refresh_summaries`` after extraction, the sort and
the tidy. A topic needs a summary when it has no row or its facts changed since the row was
written (a digest over its live fact ids and text); up to ``MAX_TOPICS_PER_CALL`` such topics go
in ONE memory-model call and the rest wait for the next sweep. A returned line is kept only for a
topic that was sent, within ``SUMMARY_MAX_CHARS``, and with no identifier and no run of more than
three digits; anything else is skipped and asked for again next sweep. A topic with no live facts
left loses its row. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 8).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from backend.ai import memory
from backend.ai import memory_structure as ms
from backend.ai.memory_write import has_identifier
from backend.core import data_events
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled
from backend.db.memory_topic_summary_store import MemoryTopicSummaryStore

logger = logging.getLogger(__name__)

MAX_TOPICS_PER_CALL = 20
SUMMARY_MAX_CHARS = 120
_LONG_DIGITS = re.compile(r"\d{4,}")

_SUMMARY_SYSTEM = (
    "You write the one-line description shown at the top of each topic in a financial "
    "advisor's memory about their client. You describe what a topic covers; you never restate "
    "its facts."
)

Key = tuple[str, str]


def digest(facts: list[dict]) -> str:
    """SHA-256 over a topic's live fact ids and text, order-independent."""
    pairs = sorted((str(f.get("id") or ""), str(f.get("content") or "")) for f in facts)
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()


def live_topics(facts: list[dict]) -> dict[Key, dict]:
    """(section, topic key) → {section, topic, facts} for every named topic of live facts.
    Open items have no topics and an unsorted fact has no home yet, so neither gets one."""
    out: dict[Key, dict] = {}
    for f in facts:
        section = f.get("section")
        if f.get("state") != "live" or section not in ms.SECTIONS or section == ms.OPEN_ITEMS:
            continue
        key = ms.topic_key(f.get("topic"))
        if not key:
            continue
        group = out.setdefault(
            (section, key),
            {"section": section, "topic": ms.normalise_topic(f.get("topic")), "facts": []},
        )
        group["facts"].append(f)
    return out


def _prompt(batch: list[dict]) -> str:
    listing = json.dumps(
        [
            {
                "section": t["section"],
                "topic": t["topic"],
                "facts": [str(f.get("content") or "") for f in t["facts"]],
            }
            for t in batch
        ],
        ensure_ascii=False,
        indent=1,
    )
    return f"""Write a one-line summary for each topic below.

Each summary says what the topic covers, in at most 90 characters, in the style of
"Who they are — location, employer, background, languages". Describe the kinds of things the
topic holds; do not restate the facts. Never include numbers, amounts, dates, account or card
numbers, or any other identifier.

TOPICS (JSON):
{listing}

Return ONLY JSON, one item per topic, with "section" and "topic" exactly as given:
{{"summaries": [{{"section": "...", "topic": "...", "summary": "..."}}]}}"""


def _clean(text) -> str | None:
    """A summary fit to show, or None."""
    if not isinstance(text, str):
        return None
    line = " ".join(text.split())
    if not line or len(line) > SUMMARY_MAX_CHARS:
        return None
    if has_identifier(line) or _LONG_DIGITS.search(line):
        return None
    return line


def _accepted(parsed, sent: dict[Key, dict]) -> dict[Key, str]:
    items = parsed.get("summaries") if isinstance(parsed, dict) else None
    out: dict[Key, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = (ms.normalise_section(item.get("section")) or "", ms.topic_key(item.get("topic")))
        line = _clean(item.get("summary"))
        if key in sent and key not in out and line:
            out[key] = line
    return out


async def _ask(batch: list[dict]) -> dict[Key, str]:
    sent = {(t["section"], ms.topic_key(t["topic"])): t for t in batch}
    try:
        reply = await memory._provider().complete(
            _prompt(batch),
            system=_SUMMARY_SYSTEM,
            model=memory._memory_model(),
            max_tokens=2048,
            source="memory_summary",
            response_format=memory._MEMORY_JSON,
            effort="none",
        )
    except Exception as exc:
        logger.warning("memory summary LLM call failed: %s", type(exc).__name__)
        return {}
    parsed = memory._parse_json(reply)
    if not isinstance(parsed, dict):
        logger.warning("memory summary returned an unparseable reply")
        return {}
    return _accepted(parsed, sent)


def _plan(store: MemoryTopicSummaryStore) -> tuple[list[dict], list[Key], int]:
    """(the topics to send now, the rows to delete, how many topics are dirty). Sync DB reads."""
    topics = live_topics(MemoryStore().active_facts(user_today().isoformat()))
    rows = {(r["section"], r["topic_key"]): r for r in store.all()}
    dirty = []
    for key, topic in topics.items():
        topic["digest"] = digest(topic["facts"])
        if rows.get(key, {}).get("facts_digest") != topic["digest"]:
            newest = max(str(f.get("updated_at") or "") for f in topic["facts"])
            dirty.append((key in rows, newest, topic))
    # Topics with no summary yet first, then the most recently changed.
    dirty.sort(key=lambda d: d[1], reverse=True)
    dirty.sort(key=lambda d: d[0])
    batch = [d[2] for d in dirty[:MAX_TOPICS_PER_CALL]]
    return batch, [k for k in rows if k not in topics], len(dirty)


def _write(store: MemoryTopicSummaryStore, batch: list[dict], lines: dict[Key, str]) -> int:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for topic in batch:
        key = (topic["section"], ms.topic_key(topic["topic"]))
        if key in lines:
            rows.append(
                {
                    "section": key[0],
                    "topic_key": key[1],
                    "topic": topic["topic"],
                    "summary": lines[key],
                    "facts_digest": topic["digest"],
                    "updated_at": stamp,
                }
            )
    return store.upsert(rows)


async def refresh_summaries() -> dict:
    """Bring every topic's summary up to date with its facts. Returns counts; ``ran`` is False
    while memory is off. One model call at most, one data-version event at most."""
    if not memory_writes_enabled():
        return {"ran": False}
    store = MemoryTopicSummaryStore()
    batch, gone, dirty = await asyncio.to_thread(_plan, store)
    lines = await _ask(batch) if batch else {}
    written = await asyncio.to_thread(_write, store, batch, lines) if lines else 0
    deleted = await asyncio.to_thread(store.delete, gone) if gone else 0
    if written or deleted:
        data_events.publish(("memory",))
    out = {
        "ran": True,
        "dirty": dirty,
        "sent": len(batch),
        "written": written,
        "skipped": len(batch) - written,
        "deleted": deleted,
    }
    if batch or deleted:
        logger.info(
            "memory summaries: %d dirty, %d sent, %d written, %d skipped, %d deleted",
            dirty,
            len(batch),
            written,
            out["skipped"],
            deleted,
        )
    return out


def forget_topic(section: str, topic: str) -> int:
    """Drop one topic's summary (the user deleted the topic)."""
    return MemoryTopicSummaryStore().delete([(section, ms.topic_key(topic))])


def forget_all() -> int:
    """Drop every summary (the user forgot everything)."""
    return MemoryTopicSummaryStore().clear()
