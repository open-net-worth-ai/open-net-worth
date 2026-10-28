"""The "WHAT YOU REMEMBER" block: every live fact, whole, filed by section and topic, with the
month it was learned and a short handle the model can cite.

Pure — facts in, text and stats out — so the block is byte-stable: the order is
``(created_at, id)`` and nothing here reads ``updated_at``, which every extraction pass touches.
ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 4).
"""

from __future__ import annotations

import re
from datetime import date

from backend.ai.memory_structure import SECTIONS, topic_key

HEADER = (
    "WHAT YOU REMEMBER (reference — apply when relevant; never recite these facts back unprompted):"
)
CONTEXT_NOT_PROOF = (
    "Memory is context, not proof — check the owning record before acting on anything "
    "changeable; if they disagree, the record wins and you fix the memory."
)

HANDLE_HEX_CHARS = 6


def fact_handle(fact_id: str) -> str:
    """``mem_7a88f842d02f`` → ``m7a88f8`` — what the model cites a fact by."""
    raw = fact_id or ""
    body = raw[4:] if raw.startswith("mem_") else raw
    return "m" + re.sub(r"[^0-9A-Za-z]", "", body)[:HANDLE_HEX_CHARS]


def _month(created_at) -> str:
    try:
        return date.fromisoformat(str(created_at)[:10]).strftime("%b %Y")
    except ValueError:
        return ""


def _bullet(fact: dict, handles: bool = True) -> str:
    content = " ".join(str(fact.get("content") or "").split())
    parts = [f"• {content}"]
    month = _month(fact.get("created_at"))
    if month:
        parts.append(f"({month})")
    if fact.get("section") == "open_items" and fact.get("expires_at"):
        parts.append(f"(until {str(fact['expires_at'])[:10]})")
    if handles:
        parts.append(f"[{fact_handle(str(fact.get('id') or ''))}]")
    return " ".join(parts)


def _order(fact: dict) -> tuple:
    return (str(fact.get("created_at") or ""), str(fact.get("id") or ""))


def _section_of(fact: dict) -> str:
    section = fact.get("section")
    return section if section in SECTIONS else "other"


def _topic_groups(facts: list[dict]) -> list[tuple[str, list[dict]]]:
    """Facts with no topic first (under the section heading), then one group per topic,
    alphabetical by match key; the group is shown under the first-seen spelling."""
    names: dict[str, str] = {}
    groups: dict[str, list[dict]] = {}
    for f in sorted(facts, key=_order):
        key = topic_key(f.get("topic"))
        names.setdefault(key, " ".join(str(f.get("topic") or "").split()))
        groups.setdefault(key, []).append(f)
    return [(names[k], groups[k]) for k in sorted(groups)]


def _is_unsorted(fact: dict) -> bool:
    return fact.get("state") == "unsorted" or (not fact.get("state") and not fact.get("section"))


def render_memory_block(facts: list[dict], *, handles: bool = True) -> tuple[str, dict]:
    """``(text, stats)`` for ``facts`` (live and unsorted; retired are ignored). The text is
    "" when there is nothing live and nothing waiting to be sorted.

    ``handles=False`` drops the citation handles, for a reader with no citation rule (the
    daily briefing)."""
    live = [f for f in facts if f.get("state") == "live"]
    unsorted = [f for f in facts if f.get("state") != "live" and _is_unsorted(f)]
    stats = {"facts": len(live), "chars": 0, "by_section": {}, "unsorted": len(unsorted)}
    if not live and not unsorted:
        return "", stats

    lines = ["", HEADER, CONTEXT_NOT_PROOF]
    for key, label in SECTIONS.items():
        members = [f for f in live if _section_of(f) == key]
        if not members:
            continue
        stats["by_section"][key] = len(members)
        lines.append(f"{label}:")
        for name, group in _topic_groups(members):
            if name:
                lines.append(f"  {name}:")
            lines += [_bullet(f, handles) for f in group]
    if unsorted:
        noun = "fact isn't" if len(unsorted) == 1 else "facts aren't"
        lines.append(f"({len(unsorted)} older {noun} sorted yet — recall_memory searches them.)")
    text = "\n".join(lines)
    stats["chars"] = len(text)
    return text, stats
