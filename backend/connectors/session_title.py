"""A connector conversation is named ONCE, from its channel and start time.

A web chat is topic-scoped — you open a new one per subject, so naming it after its first
message is exactly right, and that is what the frontend does
(``useConversations.updateConversationMeta`` → ``conversationTitleFromFirstMessage``).

A Telegram conversation is not that. It is ONE continuous thread per chat, running for
weeks, and its first message is usually a greeting: a real user conversation opened
with ``Hello``. Naming a month-long thread "Hello" says nothing. Worse, nothing was naming
it at all — the backend never set a title on the connector path, and the frontend, which is
the only thing that generates titles, filters connector conversations out of its list
(``ChatPanel``/``ConversationBrowser`` drop any row carrying ``channel``). So every Telegram
conversation stayed "New conversation" forever (owner, 2026-08-22).

**The chat id is deliberately NOT in the title.** The ``chat_id → session_id`` map lives in
the connector's ``sessions`` secret in ``connector_secrets`` (``session_map``), a
``core.sensitive.SECRET_TABLES`` member, and ``services.backup.write_sanitised_copy`` DELETEs
those rows from the ``onw.db`` inside a downloaded DR archive — specifically so a leaked backup
cannot be used to resume the owner's chat session. Sessions themselves ARE portable
(``services.portability.session_bundle_entries``). Putting the chat id in a session title
would therefore carry it into the very archive that strips the mapping, quietly defeating
an existing control. The START TIME does the disambiguating work anyway: ``/new`` mints a
fresh session for the same chat, so start time is what separates one thread from the next.

Set once, at first sight of the session, and never rewritten — a stable name is the point.
The frontend cannot clobber it either: it only renames a conversation still called exactly
``"New conversation"``.

Deliberately SEPARATE from ``session_map.py``, which must stay AI-FREE
(``services.connectors`` imports it directly, and ``services`` must never reach
``backend.ai`` even transitively — import-linter's layers contract). This module needs
``ai.assistant`` to read and write session meta, exactly like its sibling
``session_reset.py``. Nothing under ``backend.services`` may import either.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from backend.core.user_time import user_now, user_zone

log = logging.getLogger(__name__)

_UNNAMED = "New conversation"


def connector_session_title(connector: str, started: Optional[datetime] = None) -> str:
    """``"Telegram • 22 Aug 2026, 06:29"`` — the channel, then when the thread began.

    Rendered in the owner's timezone (``user_zone()``), matching how
    ``ai/prompts.py`` builds its own TODAY'S DATE line: a time the owner reads should be
    their wall clock, not UTC.
    """
    when = (started or user_now()).astimezone(user_zone())
    # %-d (no zero pad) is a glibc/BSD extension that raises on Windows, so the day is formatted by hand.
    return f"{connector.title()} • {when.day} {when.strftime('%b %Y, %H:%M')}"


def ensure_session_titled(connector: str, session_id: str) -> None:
    """Give this connector conversation its stable name, if it has none yet.

    Idempotent and best-effort: a naming failure must never break an inbound message. A
    conversation that already carries any title — including one an operator typed — is left
    alone, so this can run on every inbound turn without ever fighting a rename.

    Prefers the session's own recorded ``created_at`` over "now", so an EXISTING untitled
    conversation is named after when it actually began rather than whenever this first ran.
    """
    try:
        from backend.ai import assistant

        meta = assistant._session_meta_entry(session_id) or {}
        current = str(meta.get("title") or "").strip()
        if current and current != _UNNAMED:
            return  # already named — never rename

        started: Optional[datetime] = None
        raw = meta.get("created_at")
        if raw:
            try:
                started = datetime.fromisoformat(str(raw))
            except (TypeError, ValueError):
                started = None

        assistant.upsert_session_meta(
            session_id,
            title=connector_session_title(connector, started),
            channel=connector,
        )
    except Exception:  # noqa: BLE001 — a title is cosmetic; an inbound turn is not
        log.debug("could not title %s session %s", connector, session_id, exc_info=True)
