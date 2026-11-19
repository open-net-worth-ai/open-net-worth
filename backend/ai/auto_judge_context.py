"""What the Auto judge reads around a chat call: the conversation as it happened, and the rows a
call names.

The owner's typed words are still the only statement of intent. Lucius's own messages are
context — the question a "yes" answers, the list a "the ACME Mart one" points into — so the judge
can tell what the owner agreed to (owner, 2026-11-15: a bare "Yep" to Lucius's "approve it?" was
asked about because the judge never saw the question). Each message is fenced on its own under a
label this module writes, so Lucius's text cannot forge a line in the owner's name.

Only chat reaches here. An Automation run's intent is its stored instructions and nothing else
(``auto_judge_prompt.owner_intent``): its Lucius text is shaped by the trigger payload.
ADR ``2026-10-10-auto-approval-is-a-per-call-judge``, amendment 2026-11-15.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.core.untrusted import sanitize_untrusted

logger = logging.getLogger(__name__)

#: A Lucius message is clipped from the FRONT: the question the owner answers is at its end,
#: after whatever table or explanation came before it.
MAX_LUCIUS_MESSAGE_CHARS = 1500
#: How many rows a call names that the judge is told about, by date, payee and amount.
MAX_NAMED_ROWS = 25

_TXN_ID = re.compile(r"\btxn_[0-9a-f]{12}\b")


@dataclass(frozen=True)
class Said:
    """One message of the conversation: who said it and what, already clipped and sanitized."""

    speaker: Literal["owner", "lucius"]
    text: str


def _lucius_text(content: Any) -> str:
    """The prose of a stored assistant row — its text blocks, never a tool call or thinking."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if isinstance(p, str) and p.strip()).strip()


def clip_tail(text: str, limit: int) -> str:
    text = sanitize_untrusted(text or "", len(text or "") + 1)
    return text if len(text) <= limit else "[earlier text cut]…" + text[-limit:]


def conversation_from_rows(rows: list[dict], owner_limit: int, owner_chars: int) -> list[Said]:
    """The last ``owner_limit`` owner messages and Lucius's messages before and between them, in
    order. Consecutive Lucius rows (one turn's steps) read as one message. Empty when the owner
    said nothing readable: Lucius's text alone is never a reason to allow."""
    from backend.ai.auto_judge_prompt import clip, typed_text
    from backend.ai.tool_text import is_harness_note

    said: list[Said] = []
    for row in rows:
        role = row.get("role")
        if role == "user" and not row.get("_onw_skill"):
            text = typed_text(row.get("content")).strip()
            if text and not is_harness_note(text):
                said.append(Said("owner", clip(text, owner_chars)))
        elif role == "assistant":
            text = _lucius_text(row.get("content"))
            if not text:
                continue
            if said and said[-1].speaker == "lucius":
                text = f"{said.pop().text}\n{text}"
            said.append(Said("lucius", text))
    owner_at = [i for i, s in enumerate(said) if s.speaker == "owner"]
    if not owner_at:
        return []
    start = owner_at[-owner_limit] if len(owner_at) >= owner_limit else owner_at[0]
    if start > 0 and said[start - 1].speaker == "lucius":
        start -= 1
    return [
        s if s.speaker == "owner" else Said("lucius", clip_tail(s.text, MAX_LUCIUS_MESSAGE_CHARS))
        for s in said[start:]
    ]


def named_rows(args: Any) -> list[str]:
    """``txn_… : date · payee · amount · review state · memo`` for each transaction the call's
    arguments name, read from the owner's data now — so "the ACME Mart one" can be matched to an
    id, and an edit an earlier call already made is visible. An id that names no live row says so. Never raises: a row the judge cannot see is just not described."""
    ids = list(dict.fromkeys(_TXN_ID.findall(_flatten(args))))
    if not ids:
        return []
    try:
        from backend.ai.tools._shared import svc
        from backend.ai.tools.review import row_title
    except Exception:  # noqa: BLE001 — a judge fault degrades to less context, never a write
        logger.warning("auto judge: rows unreadable", exc_info=True)
        return []
    lines = []
    for txn_id in ids[:MAX_NAMED_ROWS]:
        try:
            row = svc.transactions.find_by_id(txn_id)
            if row is None or row.get("deleted"):
                lines.append(f"{txn_id}: (no such transaction)")
                continue
            state = "needs review" if row.get("needs_review") else "reviewed"
            memo = " ".join(str(row.get("memo") or "").split())[:80]
            lines.append(
                f"{txn_id}: {row_title(txn_id, row)} · {state}"
                + (f" · memo: {memo}" if memo else "")
            )
        except Exception:  # noqa: BLE001 — see above
            logger.warning("auto judge: row %s unreadable", txn_id, exc_info=True)
    if len(ids) > MAX_NAMED_ROWS:
        lines.append(f"… and {len(ids) - MAX_NAMED_ROWS} more transaction id(s)")
    return lines


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return value if isinstance(value, str) else ""
