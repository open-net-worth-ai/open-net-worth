"""Memory as Markdown: a zip with one file per section and topic, for taking it elsewhere.

Facts still waiting to be filed go in ``older-memories.md`` (titled as the memory list labels
them) and open items in ``open-items.md`` (both names are reserved, so no topic can take them);
retired and expired facts are left out. A topic's generated one-line summary, when it has one,
is an italic line under its title. File names are built from slugs only, so no topic can name a
path.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

from backend.ai import memory_structure as ms
from backend.ai.memory_summaries import visible_summaries
from backend.core.user_time import user_today
from backend.db import MemoryStore

OPEN_ITEMS_FILE = "open-items.md"
UNSORTED_FILE = "older-memories.md"
UNSORTED_TITLE = "Older memories — being sorted"
_SLUG_MAX = 60


def _slug(text: str) -> str:
    slug = re.sub(r"[\W_]+", "-", text.casefold()).strip("-")[:_SLUG_MAX].strip("-")
    return slug or "topic"


def _month(created_at) -> str:
    try:
        return date.fromisoformat(str(created_at)[:10]).strftime("%b %Y")
    except ValueError:
        return ""


def _line(fact: dict) -> str:
    text = " ".join(str(fact.get("content") or "").split())
    month = _month(fact.get("created_at"))
    return f"- {text} ({month})" if month else f"- {text}"


def _group_key(fact: dict) -> tuple[str, str] | None:
    """(section, topic key) of a filed fact; None for one still waiting to be sorted."""
    section = fact.get("section")
    if fact.get("state") != "live" or section not in ms.SECTIONS:
        return None
    return section, "" if section == ms.OPEN_ITEMS else ms.topic_key(fact.get("topic"))


def _title(key: tuple[str, str] | None, fact: dict) -> str:
    if key is None:
        return UNSORTED_TITLE
    topic = "" if key[0] == ms.OPEN_ITEMS else ms.normalise_topic(fact.get("topic"))
    label = ms.SECTIONS[key[0]]
    return f"{label} · {topic}" if topic else label


Doc = tuple[str, list[dict], tuple[str, str] | None]


def _documents(facts: list[dict]) -> dict[str, Doc]:
    """filename → (title, facts, group key), each file's facts oldest first."""
    ordered = sorted(facts, key=lambda f: (str(f.get("created_at") or ""), str(f.get("id") or "")))
    groups: dict[tuple | None, tuple[str, list[dict]]] = {}
    for fact in ordered:
        key = _group_key(fact)
        groups.setdefault(key, (_title(key, fact), []))[1].append(fact)
    docs: dict[str, Doc] = {OPEN_ITEMS_FILE: ("Open items", [], None)}
    taken = {OPEN_ITEMS_FILE, UNSORTED_FILE}
    for key, (title, members) in groups.items():
        if key is None:
            docs[UNSORTED_FILE] = (title, members, None)
        elif key[0] == ms.OPEN_ITEMS:
            docs[OPEN_ITEMS_FILE] = (title, members, None)
        else:
            base = f"{key[0]}-{_slug(key[1])}" if key[1] else key[0]
            name, n = f"{base}.md", 2
            while name in taken:
                name, n = f"{base}-{n}.md", n + 1
            taken.add(name)
            docs[name] = (title, members, key)
    return docs


def _summaries() -> dict[tuple[str, str], str]:
    return {(r["section"], r["topic_key"]): r["summary"] for r in visible_summaries()}


def _body(title: str, members: list[dict], summary: str | None) -> str:
    head = [f"# {title}", ""] + ([f"_{summary}_", ""] if summary else [])
    return "\n".join([*head, *(_line(f) for f in members)]) + "\n"


def build_memory_export(today: date | None = None) -> bytes:
    """The zip, built in memory. Open items are always present, even when empty."""
    day = today or user_today()
    docs = _documents(MemoryStore().remembered_facts())
    summaries = _summaries()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(docs):
            title, members, key = docs[name]
            body = _body(title, members, summaries.get(key) if key else None)
            stamp = (max(day.year, 1980), day.month, day.day, 0, 0, 0)
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    return buffer.getvalue()
