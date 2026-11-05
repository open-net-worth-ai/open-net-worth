"""What Lucius's side of a call reports to the voice: shared by the app and the worker process."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MARKDOWN = re.compile(r"[*_`#>|]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_LONE_LINK = re.compile(r"^[ \t]*[-*>•]*[ \t]*[*_]*\[[^\]]+\]\([^)]+\)[*_]*[.!]?[ \t]*$", re.M)


def speakable(text: str) -> str:
    """What is read aloud. A link inside a sentence is read as its label. One on a line of its own
    is a button, not words: "review and submit the instruction" was read out after the sentence
    had already said so (owner, 2026-11-05). Unless it is all there is: never silence."""
    without = _LONE_LINK.sub("", text)
    return _MARKDOWN.sub("", _LINK.sub(r"\1", without if without.strip() else text)).strip()


@dataclass
class DeskEvent:
    kind: str  # "text" | "tool" | "tool_done" | "approval" | "error" | "end"
    text: str = ""
    ref: str = ""
    noun: str = ""
