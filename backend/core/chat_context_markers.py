"""What marks a user-turn text block as MODEL context rather than something the person said.

A user turn in the transcript carries more than the sentence the reader typed: the per-turn
``<turn_context>`` snapshot, a resolved @-mention dump, a screen snapshot, a skill's rendered
body, an attachment's extracted text, and harness-authored notes. Every reader that wants "what
was actually said" — the chat bubbles on reload, the fold's verbatim record, and the search over
past conversations — skips these by the same definition, kept here so it is written once.

A leaf of ``core``: stdlib plus ``core.untrusted`` only, so ``ai``, ``db`` and ``services`` can all
import it.
"""

from __future__ import annotations

import re

from backend.core.untrusted import SCREEN_BLOCK_PREFIXES

TURN_CONTEXT_OPEN = "<turn_context>"
MENTION_OPEN = "«mention:"

HARNESS_NOTE_OPEN = "«harness_note»"
HARNESS_NOTE_CLOSE = "«/harness_note»"

# Harness-authored bodies that PREDATE the wrapper above (2026-08-21) and are therefore stored
# BARE in older transcripts, recognised by their literal opening clause.
LEGACY_HARNESS_OPENINGS = (
    "[Automated monitor — the following message was delivered",
    # Matched on each note's full opening clause rather than a bare "[System: " prefix, so a
    # person who happens to type that is not silenced.
    "[System: User confirmed and applied via chat:",
    "[System: User discarded the proposed change via chat:",
    "[System: Denied — the user declined to run",
)

# The onboarding kickoff sentinel; matched by EXACT equality so a person who merely begins a
# message with the token is not silenced.
ONBOARDING_INIT = "__ONBOARDING_INIT__"

SKILL_OPENINGS = ("[SKILL INVOCATION:", "[ACTIVE SKILL CONTEXT:")

# The header the app writes before an attachment's extracted text. Anywhere in a block means the
# block carries a file's contents, whatever else surrounds it.
ATTACHED_HEADER = "[Attached "
ATTACHMENT_NOTE = "[Attachment "

USER_MSG_FENCE_LABEL = "user_msg"
_USER_MSG_FENCE_TOKEN = re.compile(rf"«/?{USER_MSG_FENCE_LABEL}:[0-9a-fA-F]+»")

# A block that BEGINS with one of these (after leading whitespace) is model context.
CONTEXT_OPENINGS = (
    TURN_CONTEXT_OPEN,
    MENTION_OPEN,
    *SCREEN_BLOCK_PREFIXES,
    *SKILL_OPENINGS,
    *LEGACY_HARNESS_OPENINGS,
)

# A block that CONTAINS one of these anywhere is model context.
CONTEXT_ANYWHERE = (HARNESS_NOTE_OPEN, ATTACHED_HEADER, ATTACHMENT_NOTE)


def is_model_context_block(text: str) -> bool:
    """True for a ``<turn_context>`` snapshot, a resolved @-mention block or a screen-context
    snapshot — what ``api/chat`` appends onto a user turn so the model can see it."""
    return text.lstrip().startswith((TURN_CONTEXT_OPEN, MENTION_OPEN, *SCREEN_BLOCK_PREFIXES))


def is_not_said(text: str) -> bool:
    """True when a text block is anything the person did not say: model context, a skill body, a
    harness note, an attachment's contents or the onboarding sentinel."""
    body = text or ""
    return (
        body.strip() == ONBOARDING_INIT
        or body.lstrip().startswith(CONTEXT_OPENINGS)
        or any(marker in body for marker in CONTEXT_ANYWHERE)
    )


def strip_user_msg_fence_tokens(text: str) -> str:
    """Drop the ``«user_msg:hex»`` / ``«/user_msg:hex»`` plumbing around a mid-turn message."""
    return _USER_MSG_FENCE_TOKEN.sub("", text).strip()
