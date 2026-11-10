"""What Lucius's side of a call reports to the voice: shared by the app and the worker process."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MARKDOWN = re.compile(r"[*_`#>|]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_LONE_LINK = re.compile(r"^[ \t]*[-*>•]*[ \t]*[*_]*\[[^\]]+\]\([^)]+\)[*_]*[.!]?[ \t]*$", re.M)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*$", re.M)


def speakable(text: str) -> str:
    """What is read aloud. A link inside a sentence is read as its label. One on a line of its own
    is a button, not words: "review and submit the instruction" was read out after the sentence
    had already said so (owner, 2026-11-05). A table's rows are what they asked to SEE, so they are
    shown and not read (owner, 2026-11-06). Unless that is all there is: never silence."""
    without = _TABLE_ROW.sub("", _LONE_LINK.sub("", text))
    return _MARKDOWN.sub("", _LINK.sub(r"\1", without if without.strip() else text)).strip()


@dataclass
class DeskEvent:
    kind: str  # "text" | "tool" | "tool_done" | "approval" | "card_open" | "error" | "end"
    text: str = ""
    ref: str = ""
    noun: str = ""
    readable: bool = True  # a card: it says enough to be approved out loud
    changes: bool = False  # a tool that can change the owner's data, or one we can't vouch for
