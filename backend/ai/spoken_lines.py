"""Lines heard on a voice call beside Lucius's work ("one sec"), kept on the message they go with.

Never entries of their own: a transcript is addressed by position (checkpoints, fold windows, cost
chips), and a line inserted between messages moved all of those. Being a field of a message also
keeps them from every model, which only ever gets a message's role and content, and from anything
else that reads the conversation as what was asked and answered (ADR
``2026-11-06-a-call-keeps-one-ordered-log-and-one-record-per-request``).
"""

from __future__ import annotations

VOICE_LINES_KEY = "voice_lines"
# On an answer written for a call that was never read out: the owner spoke first and moved on.
UNHEARD_KEY = "voice_unheard"
BEFORE_MESSAGE = "before"
AFTER_MESSAGE = "after"


def _shown(session_id: str, index: int, number: int, line: dict) -> dict:
    # Its own ids: numbering it with the messages would renumber every message after it.
    shown = {
        "id": f"{session_id}-line-{index}-{number}",
        "role": "user" if line.get("speaker") == "owner" else "assistant",
        "text": str(line.get("text") or ""),
    }
    if shown["role"] == "assistant":
        shown.update(blocks=[], attachments=[], streaming=False)
    if line.get("at"):
        shown["ts"] = line["at"]
    return shown


def lines_around(session_id: str, index: int, entry: dict) -> tuple[list[dict], list[dict]]:
    """The chat messages to draw before and after a transcript entry."""
    lines = entry.get(VOICE_LINES_KEY)
    if not isinstance(lines, list):
        return [], []
    before, after = [], []
    for number, line in enumerate(lines):
        if isinstance(line, dict):
            side = before if line.get("where") == BEFORE_MESSAGE else after
            side.append(_shown(session_id, index, number, line))
    return before, after
