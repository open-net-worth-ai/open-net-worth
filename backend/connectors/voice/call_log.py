"""What was said on a call, and where each line belongs in the chat.

The owner only knows Lucius, so everything they hear belongs in the chat, in the order they heard it
(owner, 2026-11-06). A running turn holds the transcript and overwrites it when it saves, so a line
said during one can't be written then. It waits here, with a place worked out from what the call
knows: the order lines played in, and when each began.

No LiveKit in here, and no words in any log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

# Where a line goes, relative to the messages of its request.
BEFORE: Literal["before"] = "before"  # it began before they asked
IN: Literal["in"] = "in"  # after their words, ahead of the answer
AFTER: Literal["after"] = "after"  # once the answer has been heard

PLACES = frozenset({BEFORE, IN, AFTER})
Place = Literal["before", "in", "after"]
Speaker = Literal["owner", "lucius"]


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Task:
    """One thing the owner asked for. Carrying on or correcting it while it waits keeps the same id
    and adds a revision, so its lines stay with it. What it did and how it ended are its own, never
    the call's: the next request can't inherit them."""

    said: str
    request: str
    kind: str = "question"
    shown: bool = False  # their words are already in the chat
    asked_at: float = 0.0  # when their words reached the call, at the latest the end of them
    id: str = field(default_factory=_new_id)
    revision: int = 1
    continues: bool = False  # adds to their last message instead of opening another
    changed: bool = False  # ran something that can change data
    superseded: bool = (
        False  # they moved past it: its answer isn't read out unless it changed something
    )
    stopped: bool = False  # stopped on purpose, its words carrying on in the next
    answered: bool = False  # its answer has been heard

    def absorb(self, earlier: "Task") -> None:
        """Take over from the request these words carry on from: same place in the chat."""
        self.id, self.asked_at = earlier.id, earlier.asked_at
        self.revision = earlier.revision + 1


@dataclass(frozen=True)
class Card:
    """A change waiting on the owner: what the app needs to resolve it, and what to read out."""

    id: str
    summary: str
    readable: bool = True


@dataclass(frozen=True)
class Line:
    text: str
    speaker: Speaker
    task_id: str | None
    place: Place
    started_at: float


class CallLog:
    def __init__(self) -> None:
        self._tasks: list[Task] = []
        self._waiting: list[Line] = []
        self._started: set[str] = set()

    def add_task(self, task: Task) -> None:
        """Tasks stay in the order asked. A revision replaces its earlier self where that stood."""
        for index, known in enumerate(self._tasks):
            if known.id == task.id:
                task.answered = task.answered or known.answered
                self._tasks[index] = task
                return
        self._tasks.append(task)

    def started(self, task: Task) -> None:
        self._started.add(task.id)

    def _place(self, started_at: float) -> tuple[str | None, Place]:
        if not self._tasks:
            return None, AFTER
        asked = [t for t in self._tasks if t.asked_at <= started_at]
        if not asked:
            return self._tasks[0].id, BEFORE
        newest = asked[-1]
        return newest.id, AFTER if newest.answered else IN

    def add(self, text: str, speaker: Speaker, started_at: float) -> Line:
        """Lines arrive in the order they played: only a line said over the owner starting a new
        request needs its time, to land ahead of those words."""
        task_id, place = self._place(started_at)
        line = Line(text, speaker, task_id, place, started_at)
        self._waiting.append(line)
        return line

    def forget_owner(self, said: str) -> None:
        """Their words turned out to be a request, which carries them: never twice in the chat. Whole
        words only: "ok" said earlier isn't in "book a flight"."""
        words = f" {said} "
        self._waiting = [
            line
            for line in self._waiting
            if not (line.speaker == "owner" and f" {line.text} " in words)
        ]

    def take(self, everything: bool = False) -> list[Line]:
        """The lines to write now, in the order heard. Not those of a request still in the queue:
        its message isn't in the chat, and they'd land ahead of the words they answer."""
        ready = [
            line
            for line in self._waiting
            if everything or line.task_id is None or line.task_id in self._started
        ]
        self._waiting = [line for line in self._waiting if line not in ready]
        return ready

    @property
    def waiting(self) -> bool:
        return bool(self._waiting)
