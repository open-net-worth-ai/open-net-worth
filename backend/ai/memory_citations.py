"""Usage citations: ``<memory-used>m7a88f8 m3c01d2</memory-used>`` at the end of a reply names the
remembered facts that shaped it.

The tag is machine syntax. ``CitationStripper`` removes it from the live stream (the tag can arrive
split across deltas, so any tail that could be its start is held until it is settled),
``split_citations`` removes it from the saved text, and ``note_cited_facts`` resolves the handles
and stamps ``last_used_at`` on exactly the facts that were cited — a fact that was only injected
into the prompt is never marked. A handle is a prefix of ``fact_handle(id)``; one that names no
live fact, or more than one, is ignored. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``.
"""

from __future__ import annotations

import asyncio
import logging
import re

from backend.ai.memory_render import fact_handle
from backend.ai.memory_write import apply_memory_change
from backend.db import MemoryStore
from backend.db.memory_settings_store import memory_writes_enabled

logger = logging.getLogger(__name__)

TAG = "memory-used"

# Models vary the tag's case and spacing and sometimes wrap it in backticks; all of it is hidden.
_OPEN_RE = re.compile(rf"`?<\s*{TAG}\s*>", re.IGNORECASE)
_CLOSE_RE = re.compile(rf"<\s*/\s*{TAG}\s*>", re.IGNORECASE)
_TAG = re.compile(rf"`?<\s*{TAG}\s*>(.*?)<\s*/\s*{TAG}\s*>`?", re.IGNORECASE | re.DOTALL)
_UNCLOSED = re.compile(rf"`?<\s*{TAG}\s*>.*\Z", re.IGNORECASE | re.DOTALL)
_HANDLE = re.compile(r"m[0-9a-z]+", re.IGNORECASE)
_MIN_HANDLE_CHARS = 3
_SCAN_TAIL = 64


def _prefixes(word: str) -> str:
    pattern = ""
    for ch in reversed(word):
        pattern = f"(?:{re.escape(ch)}{pattern})?"
    return pattern


# Every string that is a proper start of an opening tag: "`", "<", "< mem", "<memory-used ".
_OPEN_START = re.compile(rf"`?(?:<(?:\s*{_prefixes(TAG)}|\s*{TAG}\s*))?", re.IGNORECASE)


def _handles_in(body: str) -> list[str]:
    return _HANDLE.findall(body)


def _partial_open(text: str) -> int:
    """How many trailing characters of ``text`` could still become an opening tag."""
    tail = text[-_SCAN_TAIL:]
    for i, ch in enumerate(tail):
        if ch in "`<" and _OPEN_START.fullmatch(tail[i:]):
            return len(tail) - i
    return 0


class CitationStripper:
    """The live stream minus the citation tag, fed delta by delta.

    ``feed`` returns what is safe to show now; ``flush`` ends the round, releasing a held tail that
    turned out not to be the tag and dropping a tag the reply never closed. ``handles`` collects
    the cited handles in order.
    """

    def __init__(self) -> None:
        self._held = ""
        self._inside = False
        self._eat_tick = False
        self.handles: list[str] = []

    def feed(self, text: str) -> str:
        out: list[str] = []
        buf, self._held = self._held + text, ""
        if self._eat_tick and buf:
            self._eat_tick = False
            buf = buf[1:] if buf[0] == "`" else buf
        while buf:
            if self._inside:
                end = _CLOSE_RE.search(buf)
                if end is None:
                    self._held = buf
                    break
                self.handles += _handles_in(buf[: end.start()])
                buf, self._inside = buf[end.end() :], False
                if buf.startswith("`"):
                    buf = buf[1:]
                elif not buf:
                    self._eat_tick = True
                continue
            start = _OPEN_RE.search(buf)
            if start is not None:
                out.append(buf[: start.start()])
                buf, self._inside = buf[start.end() :], True
                continue
            keep = _partial_open(buf)
            out.append(buf[: len(buf) - keep])
            self._held = buf[len(buf) - keep :]
            break
        return "".join(out)

    def flush(self) -> str:
        """End of the round. An unclosed tag is dropped — it is never prose."""
        held, inside = self._held, self._inside
        self._held, self._inside, self._eat_tick = "", False, False
        if inside:
            logger.debug("memory citation tag was never closed; dropped")
            return ""
        return held


def split_citations(text: str) -> tuple[str, list[str]]:
    """``(text without the tag, the handles it cited)`` — for the saved reply."""
    if not text or not _OPEN_RE.search(text):
        return text, []
    handles: list[str] = []
    for m in _TAG.finditer(text):
        handles += _handles_in(m.group(1))
    cleaned = _UNCLOSED.sub("", _TAG.sub("", text))
    return (cleaned.rstrip() if cleaned != text else text), handles


def resolve_handles(handles: list[str], live_facts: list[dict]) -> list[str]:
    """The ids the handles name: each handle must be a prefix of exactly one live fact's handle."""
    by_id = {f["id"]: fact_handle(f["id"]) for f in live_facts if f.get("id")}
    ids: list[str] = []
    for raw in handles:
        handle = (raw or "").strip().lower()
        if len(handle) < _MIN_HANDLE_CHARS or not handle.startswith("m"):
            logger.debug("memory citation %r ignored: too short", raw)
            continue
        found = [fid for fid, h in by_id.items() if h.startswith(handle)]
        if len(found) != 1:
            logger.debug("memory citation %r ignored: matches %d facts", raw, len(found))
            continue
        ids += [fid for fid in found if fid not in ids]
    return ids


def _touch(handles: list[str]) -> int:
    ids = resolve_handles(handles, MemoryStore().active_facts())
    if not ids:
        return 0
    apply_memory_change({"touch": {"used": ids}}, source="implicit", strict=False)
    return len(ids)


async def note_cited_facts(handles: list[str]) -> int:
    """Stamp ``last_used_at`` on the live facts ``handles`` name; returns how many. The write is
    synchronous, so it runs in a thread rather than on the event loop. Never raises — memory
    bookkeeping must not break a reply."""
    if not handles or not memory_writes_enabled():
        return 0
    try:
        return await asyncio.to_thread(_touch, handles)
    except Exception:
        logger.debug("could not record memory usage", exc_info=True)
        return 0
