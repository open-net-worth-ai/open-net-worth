"""Cutting a pasted export into pieces the memory model can answer in one reply.

A paste well under the 20,000-character limit can still list more entries than one reply holds,
so it is sorted in pieces of about ``CHUNK_CHARS`` on line boundaries. A category heading
("Projects") stays attached to its entries: a piece that starts mid-category begins with the
heading it belongs under. ``entry_text`` is also how a reply's ``line`` is matched back to the
paste.
"""

from __future__ import annotations

import re

CHUNK_CHARS = 4_000
HEADING_MAX_CHARS = 40

_DATE_PREFIX = re.compile(r"^\s*\[[^\]\n]{0,24}\]\s*[-–—:]?\s*")
_BULLET = re.compile(r"^\s*(?:[-*•]\s+)")


def entry_text(line: str) -> str:
    """A pasted line without its leading ``[date] - `` or bullet, whitespace collapsed."""
    text = _DATE_PREFIX.sub("", line, count=1)
    text = _BULLET.sub("", text, count=1)
    return " ".join(text.split())


def is_heading(line: str) -> bool:
    stripped = line.strip()
    return (
        bool(stripped)
        and len(stripped) <= HEADING_MAX_CHARS
        and not _DATE_PREFIX.match(line)
        and not stripped.endswith((".", "!", "?"))
    )


def missed_lines(chunk: str, answered: set[str]) -> str:
    """The entries of ``chunk`` whose ``entry_text`` (casefolded) is not in ``answered``, each
    led by the heading it falls under — "" when the reply covered every entry."""
    out: list[str] = []
    heading = ""
    for line in chunk.splitlines():
        if not line.strip():
            continue
        if is_heading(line):
            heading = line
            continue
        if entry_text(line).casefold() not in answered:
            if heading and heading not in out:
                out.append(heading)
            out.append(line)
    return "\n".join(out)


def split_paste(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """The paste as one or more pieces, each about ``size`` characters or fewer (a single longer
    line stays whole), cut between lines and led by the heading it falls under."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(text) <= size:
        return ["\n".join(lines)]
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    heading = ""
    for line in lines:
        if is_heading(line):
            heading = line
        if current and used + len(line) + 1 > size:
            chunks.append("\n".join(current))
            current, used = [], 0
            if heading and line != heading:
                current, used = [heading], len(heading) + 1
        current.append(line)
        used += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
