"""What Lucius's side of a call reports to the voice: shared by the app and the worker process."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeskEvent:
    kind: str  # "text" | "tool" | "tool_done" | "approval" | "error" | "end"
    text: str = ""
    ref: str = ""
    noun: str = ""
