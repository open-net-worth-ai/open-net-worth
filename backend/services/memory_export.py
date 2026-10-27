"""Memory as Markdown: a zip with one file per section and topic, for taking it elsewhere.

Reads the stored facts directly (services sit below ``ai``, so the section list is not
imported: a section key is shown as its words, ``open_items`` → "Open items"). Facts still
waiting to be filed go in ``not-sorted-yet.md``; retired and expired facts are left out.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

from backend.core.user_time import user_today
from backend.db import MemoryStore

OPEN_ITEMS = "open_items"
OPEN_ITEMS_FILE = "open-items.md"
UNSORTED_FILE = "not-sorted-yet.md"
_SLUG_MAX = 60


def _slug(text: str) -> str:
    slug = re.sub(r"[\W_]+", "-", text.casefold()).strip("-")[:_SLUG_MAX].strip("-")
    return slug or "topic"


def _label(section: str) -> str:
    return section.replace("_", " ").capitalize()


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
    if fact.get("state") != "live" or not isinstance(section, str) or not section:
        return None
    topic = " ".join(str(fact.get("topic") or "").split())
    return section, topic.casefold() if section != OPEN_ITEMS else ""


def _documents(facts: list[dict]) -> dict[str, tuple[str, list[dict]]]:
    """filename → (title, facts), each file's facts oldest first."""
    ordered = sorted(facts, key=lambda f: (str(f.get("created_at") or ""), str(f.get("id") or "")))
    groups: dict[tuple, tuple[str, list[dict]]] = {}
    for fact in ordered:
        key = _group_key(fact)
        if key is None:
            title = "Not sorted yet"
        elif key[0] == OPEN_ITEMS:
            title = "Open items"
        else:
            topic = " ".join(str(fact.get("topic") or "").split())
            title = f"{_label(key[0])} · {topic}" if topic else _label(key[0])
        groups.setdefault(key or ("", ""), (title, []))[1].append(fact)
    docs: dict[str, tuple[str, list[dict]]] = {OPEN_ITEMS_FILE: ("Open items", [])}
    for key, (title, members) in groups.items():
        if key == ("", ""):
            name = UNSORTED_FILE
        elif key[0] == OPEN_ITEMS:
            name = OPEN_ITEMS_FILE
        else:
            base = f"{_slug(key[0])}-{_slug(key[1])}" if key[1] else _slug(key[0])
            name, n = f"{base}.md", 2
            while name in docs:
                name, n = f"{base}-{n}.md", n + 1
        docs[name] = (title, members)
    return docs


def build_memory_export(today: date | None = None) -> bytes:
    """The zip, built in memory. Open items are always present, even when empty."""
    day = today or user_today()
    docs = _documents(MemoryStore().remembered_facts())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(docs):
            title, members = docs[name]
            body = "\n".join([f"# {title}", "", *(_line(f) for f in members)]) + "\n"
            info = zipfile.ZipInfo(
                name, date_time=(max(day.year, 1980), day.month, day.day, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    return buffer.getvalue()
