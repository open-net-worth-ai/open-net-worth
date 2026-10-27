"""The shapes the benchmark is made of — pure data, no backend import."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

SAFETY = "safety"
QUALITY = "quality"
STAGES = ("extract", "sort", "import", "tidy", "use")


@dataclass(frozen=True)
class Check:
    """A named predicate over the outcome. ``test`` returns None when it holds, otherwise the
    observed value that broke it (what the report prints)."""

    name: str
    severity: str
    test: Callable[["Outcome"], str | None]


@dataclass(frozen=True)
class Case:
    id: str
    stage: str
    description: str
    checks: tuple[Check, ...]
    facts: tuple[dict, ...] = ()
    notes: str = ""
    sensitive_topics: bool = False
    turns: tuple[tuple[str, str], ...] = ()
    paste: str = ""
    question: str = ""
    accounts: tuple[str, ...] = ()
    forgotten: tuple[str, ...] = ()  # texts the user told Lucius to forget, seeded as fingerprints
    summaries: bool = False  # a tidy-stage case that runs the topic-summary pass, not the tidy


@dataclass
class Outcome:
    """Everything a check may look at after a case ran."""

    case: Case
    before: dict[str, dict]
    after: list[dict]
    profile_before: dict
    profile: dict
    today: str
    error: str | None = None
    events: list[dict] = field(default_factory=list)
    plan: list[dict] | None = None
    applied: dict | None = None
    run: dict = field(default_factory=dict)
    summaries: list[dict] = field(default_factory=list)

    def new_facts(self) -> list[dict]:
        return [f for f in self.after if f["id"] not in self.before]

    def fact(self, fact_id: str) -> dict | None:
        return next((f for f in self.after if f["id"] == fact_id), None)


def _stamp(days_ago: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.isoformat(timespec="seconds")


def fact(
    fact_id: str,
    content: str,
    *,
    section: str | None = None,
    topic: str | None = None,
    state: str = "live",
    age: float = 3,
    mentioned_age: float | None = None,
    source: str = "implicit",
    expires_at: str | None = None,
) -> dict:
    """One stored memory fact, shaped as the write choke stores it."""
    row = {
        "id": fact_id,
        "content": content,
        "section": section,
        "topic": topic,
        "state": state,
        "source": source,
        "expires_at": expires_at,
        "created_at": _stamp(age),
        "updated_at": _stamp(age),
        "last_used_at": None,
    }
    if mentioned_age is not None:
        row["mentioned_at"] = _stamp(mentioned_age)
    return row


def live(fact_id: str, content: str, section: str, topic: str | None = None, **kw) -> dict:
    return fact(fact_id, content, section=section, topic=topic, **kw)


def legacy(fact_id: str, content: str, age: float = 5, **kw) -> dict:
    """A fact written before the sections existed: ``unsorted``, no section, no topic."""
    return fact(fact_id, content, state="unsorted", age=age, **kw)
