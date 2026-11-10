"""AI-written conversation titles — the "Cash drift after March import" a chat app shows in
place of the first 60 characters of whatever the person typed.

Generated ONCE, from the opening message of an in-app conversation, in the background while
the turn itself runs: the client still paints its own first-message title instantly, and this
replaces it when it lands. Never for an Automation run or a connector chat — a Telegram thread
is one long-lived conversation spanning every topic, so a topic title would be wrong there
(``connectors/session_title.py``).

Ownership lives in ``meta.title_source`` (``SessionStore.write_title``): a title the person
typed is never replaced, and a late client-side first-message write can't clobber this one.

Model: the ``title`` task pin, else the instance default — the call is ~200 tokens in and a
handful out, so even a flagship costs a fraction of a cent and no per-provider "cheap
sibling" table is worth maintaining.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from pydantic import BaseModel

from backend.ai.structured import complete_model
from backend.ai.tool_text import is_harness_note

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 60
_MAX_PROMPT_CHARS = 1500

_SYSTEM = """\
You name conversations between a person and Lucius, their personal finance assistant. \
Write a title for the conversation that starts with the person's message below.
- 2 to 6 words, sentence case (capitalize only the first word and proper nouns).
- Name the TOPIC, not the request: "Cash drift after March import", not "Help me check \
my cash". No filler like "Question about" or "Chat about".
- Keep tickers, account, merchant and product names exactly as written.
- No quotes, no emoji, no trailing punctuation.
- Use the language the person wrote in.
Return ONLY a JSON object: {"title": "..."}"""

# Same shape as the composer's serialized chips (`makeMentionTokenRE` in
# frontend/src/types/mentions.ts): [type: ref|label].
_MENTION_TOKEN = re.compile(r"\[([a-z_]+):\s*([^\]|]*)(?:\|([^\]]*))?\]")
_LEADING_SLASH_COMMAND = re.compile(r"^/[a-z0-9]+(?:-[a-z0-9]+)*(?:\s+|$)", re.IGNORECASE)
_WRAPPING = "\"'`“”‘’*_ "
_TRAILING = ".!?:;,…-–— "

# Session ids with a title call in flight (one call per conversation, however many sends race).
_pending: set[str] = set()
_tasks: set[asyncio.Task] = set()
# session id → when its AI title was written, kept for ``_RECENT_SECONDS``. Surfaced by GET
# /api/sessions/live: a title can land after its turn ended (and after the listing refresh
# that ending triggered), so the client re-reads the listing when it sees a new entry here.
_RECENT_SECONDS = 60.0
_recent: dict[str, float] = {}


class _Title(BaseModel):
    title: str


def recently_titled() -> dict[str, float]:
    cutoff = time.time() - _RECENT_SECONDS
    for session_id in [sid for sid, at in _recent.items() if at < cutoff]:
        _recent.pop(session_id, None)
    return dict(_recent)


def _visible_text(message: str) -> str:
    """What the person actually wrote: chips rendered as their label, leading skill
    selectors dropped (a bare `/tax-audit` has no topic of its own to name)."""
    text = _MENTION_TOKEN.sub(lambda m: f" {(m.group(3) or m.group(2) or '').strip()} ", message)
    text = re.sub(r"\s+", " ", text).strip()
    while True:
        slash = _LEADING_SLASH_COMMAND.match(text)
        if not slash:
            return text
        text = text[slash.end() :].strip()


def clean_title(raw: str) -> str:
    """Normalize a model's reply into a listing-safe title, or "" when nothing usable is left.

    The client re-normalizes every title it reads and PATCHes back any difference, so this
    must already be a fixed point of that pass: no chip tokens, no leading slash, collapsed
    whitespace, at most ``MAX_TITLE_CHARS``."""
    title = _MENTION_TOKEN.sub(" ", raw or "")
    title = re.sub(r"\s+", " ", title).strip().strip(_WRAPPING).rstrip(_TRAILING)
    title = title.lstrip("/").strip()
    if len(title) > MAX_TITLE_CHARS:
        cut = title[:MAX_TITLE_CHARS]
        title = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(_TRAILING)
    return title


def _title_model() -> str:
    try:
        from backend.db.model_config_store import ModelConfigStore

        return ModelConfigStore().task_model_override("title") or ""
    except Exception:  # noqa: BLE001 — an unreadable config means the default, not no title
        return ""


async def generate_title(opening_message: str) -> str:
    """A title for a conversation opening with ``opening_message``, or "" on any failure."""
    text = _visible_text(opening_message)
    if not text:
        return ""
    result = await complete_model(
        f"The person's opening message:\n{text[:_MAX_PROMPT_CHARS]}",
        _Title,
        system=_SYSTEM,
        model=_title_model(),
        max_tokens=100,
        source="chat_title",
        effort="none",  # naming a topic needs no reasoning; thinking only adds latency
    )
    return clean_title(result.title) if result is not None else ""


async def _title_session(session_id: str, opening_message: str) -> None:
    from backend.ai.assistant import _store

    try:
        title = await generate_title(opening_message)
        if title and _store().write_title(session_id, title, source="ai"):
            _recent[session_id] = time.time()
            logger.info("titled session %s", session_id)
    except Exception:  # noqa: BLE001 — a title is cosmetic; never surface a failure
        logger.warning("could not title session %s", session_id, exc_info=True)


def set_title(session_id: str, title: str, *, auto: bool) -> bool:
    """A title from the client: typed by the person (``auto=False``, always wins) or derived
    by the client itself (``auto=True``, yields to an AI title and a rename). Best-effort,
    like the rest of the listing metadata — returns whether it was written."""
    from backend.ai.assistant import _store

    try:
        return _store().write_title(session_id, title, source="auto" if auto else "user")
    except Exception:  # noqa: BLE001 — listing metadata never fails the request
        logger.warning("could not write title for session %s", session_id, exc_info=True)
        return False


def title_from_request(session_id: str, request: str) -> bool:
    """Name a call from its first real request. Its opening words are a greeting, and every call
    came out titled "Hi, Lucius. How are you doing?" (owner, 2026-11-07). Best-effort, like any title;
    once a title is the AI's or the person's, no model is asked again."""
    from backend.ai.assistant import _store

    try:
        meta = _store().get_meta(session_id) or {}
    except Exception:  # noqa: BLE001 — listing metadata never fails the request
        logger.warning("could not read the title of session %s", session_id, exc_info=True)
        return False
    if meta.get("title_source") in ("user", "ai"):
        return False
    return schedule_title(session_id, request)


def schedule_title(session_id: str, opening_message: str) -> bool:
    """Start titling ``session_id`` in the background. Returns whether a task was started.

    The caller decides that this IS the opening message of an in-app conversation; this
    only refuses what can never be titled (a harness note, a selector-only message) and a
    session that already has a call in flight."""
    if not session_id or session_id in _pending or is_harness_note(opening_message):
        return False
    if not _visible_text(opening_message):
        return False
    _pending.add(session_id)
    task = asyncio.get_running_loop().create_task(_title_session(session_id, opening_message))
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        _pending.discard(session_id)

    task.add_done_callback(_done)
    return True
