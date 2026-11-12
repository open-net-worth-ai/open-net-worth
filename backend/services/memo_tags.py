"""Tags written into a note as ``#name``, the way Actual Budget keeps them (PURE).

Actual has no tag field: a tag is ``#`` and the text up to the next space or ``#``, and ``##`` is
a literal ``#``. A tag starts a word, so a link's ``/#section`` is not one. Tags read here are only
ever ADDED to a ledger row's own: one the person removed here comes back only when the note does.
"""

from __future__ import annotations

import re

MAX_TAG_CHARS = 64
MAX_TAGS = 20
_TAG = re.compile(r"(?<![^\s])#([^\s#]+)")


def tags_in(text: str | None) -> list[str]:
    out: list[str] = []
    for name in _TAG.findall(text or ""):
        name = name[:MAX_TAG_CHARS]
        if name not in out:
            out.append(name)
    return out[:MAX_TAGS]


def tags_of(row: dict) -> list[str]:
    """The tags in a row's note and its split lines' notes."""
    out: list[str] = []
    notes = [row.get("memo")] + [s.get("memo") for s in row.get("splits") or []]
    for note in notes:
        for name in tags_in(note):
            if name not in out:
                out.append(name)
    return out[:MAX_TAGS]


def with_tags(existing: list[str] | None, found: list[str]) -> list[str]:
    """``existing`` and then each found tag it lacks: never drops or reorders a tag."""
    merged = list(existing or [])
    for name in found:
        if name not in merged:
            merged.append(name)
    return merged
