"""What the extractor reads of one text block: the person's words, not the app's plumbing.

A user turn carries blocks the person never typed — the screen-command catalog, a screen
snapshot, a ``<turn_context>``, an @-mention or a skill body — and on a chat channel a fenced
quote of the message being replied to. Left in, they made up two-thirds of what the extractor
read (one 70-message chat: 44,910 characters, 15,721 of them the conversation), burying the short
turns where a decision is made. An attachment's extracted text stays: it can be what a decision
is about.
"""

from __future__ import annotations

import re

from backend.core.chat_context_markers import CONTEXT_OPENINGS, strip_user_msg_fence_tokens

REPLY_QUOTE_KEEP_CHARS = 160

_REPLY_QUOTE = re.compile(
    r"«reply_quote:(?P<nonce>[0-9a-fA-F]+)»\s*(?:\[[^\]\n]*\]\s*)?(?P<body>.*?)«/reply_quote:(?P=nonce)»",
    re.S,
)


def _short_quote(match: re.Match) -> str:
    body = " ".join(match.group("body").split())
    if len(body) > REPLY_QUOTE_KEEP_CHARS:
        body = body[:REPLY_QUOTE_KEEP_CHARS].rstrip() + "…"
    return f"(replying to: {body}) " if body else ""


def readable_block(role: str, text: str) -> str:
    """``text`` as the extractor should read it, or "" for a block that is only plumbing."""
    if role != "user":
        return text.strip()
    if text.lstrip().startswith(CONTEXT_OPENINGS):
        return ""
    return strip_user_msg_fence_tokens(_REPLY_QUOTE.sub(_short_quote, text))


__all__ = ["readable_block"]
