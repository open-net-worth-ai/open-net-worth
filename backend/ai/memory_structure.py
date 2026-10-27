"""What memory is made of: five fixed sections, a free-text topic per fact, the limits, and the
ONE routing guide that decides whether a fact belongs in memory at all.

Leaf of the memory modules (imports nothing from them) so the write choke, the renderer, the
extractor and the tools all read the same constants. ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 4 wins).
"""

from __future__ import annotations

import re

SECTIONS: dict[str, str] = {
    "you": "You",
    "money": "Money",
    "plans": "Plans",
    "open_items": "Open items",
    "other": "Other",
}

SUGGESTED_TOPICS: dict[str, tuple[str, ...]] = {
    "you": ("About you", "Preferences"),
    "money": (
        "Housing",
        "Family",
        "Investing",
        "Tax",
        "Cards & payments",
        "Income & work",
        "Bookkeeping",
    ),
    "plans": (),
    "open_items": (),
    "other": (),
}

OPEN_ITEMS = "open_items"

FACT_MAX_CHARS = 280
TOPIC_MAX_CHARS = 60

OPEN_ITEM_DEFAULT_DAYS = 14
OPEN_ITEM_MAX_DAYS = 30
PLAN_STALE_DAYS = 90
UNUSED_DAYS = 90
RETIRED_PURGE_DAYS = 30

MEMORY_IDLE_MINUTES = 30
MEMORY_SWEEP_SECONDS = 300

STATES = ("live", "unsorted", "retired")

RETIRE_REASONS = (
    "profile",
    "instructions",
    "payee_rule",
    "automation",
    "ledger",
    "resolved",
    "stale",
    "unused",
)


def normalise_section(value) -> str | None:
    """The section key for ``value`` (a key or its label, any case), or None."""
    if not isinstance(value, str):
        return None
    key = re.sub(r"[\s\-]+", "_", value.strip().lower())
    return key if key in SECTIONS else None


def normalise_topic(name) -> str:
    """A topic name as it is stored and shown: trimmed, inner whitespace collapsed, bounded."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split())[:TOPIC_MAX_CHARS].strip()


def topic_key(name) -> str:
    """What two spellings of one topic have in common — "Housing" and " housing" match."""
    return normalise_topic(name).casefold()


def fact_label(fact: dict) -> str:
    """Where a fact sits, for a model-facing listing: ``money · Housing``, ``open_items``,
    or ``unsorted`` for a legacy fact not yet filed."""
    section = fact.get("section")
    if section not in SECTIONS:
        return "unsorted"
    return f"{section} · {fact['topic']}" if fact.get("topic") else section


# (when the fact is…, where it goes, the same home in few words). First match wins: a profile
# field beats an instruction, an instruction beats memory.
_ROUTES: tuple[tuple[str, str, str], ...] = (
    (
        "something the profile has a field for (name, date of birth, residence, nationality, "
        "tax residency, employment, marital status, income, expenses, saving, risk, retirement "
        "age, target allocation, briefing time, benchmark)",
        "Profile field (update_profile) — not memory",
        "a profile field → update_profile",
    ),
    (
        "a standing rule the user states for how you should behave, which they would expect to "
        'find and edit themselves ("always…", "never…")',
        "Instructions for Lucius (update_profile notes) — not memory",
        "a standing rule → Instructions for Lucius (update_profile notes)",
    ),
    (
        "how a payee or bank text is categorised or named",
        "Payee rule / default — not memory",
        "a payee or bank-text rule → a Payee rule",
    ),
    (
        "something that should happen on a schedule (a booking, a report, a reminder)",
        "Automation — not memory",
        "something scheduled → an Automation",
    ),
    (
        "a balance, holding, price, transaction, trade, or a task that was done",
        "Nowhere — the ledger already has it",
        "balances, holdings, prices, trades, finished tasks → nowhere (the ledger has them)",
    ),
    (
        "a goal, commitment or plan figure the Plan screen holds",
        "Plan / goals / commitments — not memory",
        "a goal or commitment → Plan / goals / commitments",
    ),
    (
        "the state of this conversation (what is being waited on, what was offered)",
        "Nowhere",
        "conversation state → nowhere",
    ),
    (
        "about the user but no profile field fits (an employer, languages, who shares the "
        "household, personal context)",
        "Memory · You · About you",
        "about the user, no profile field → You · About you",
    ),
    (
        "an observation about how the user likes to work, inferred or said in passing — written "
        'in the third person ("Prefers short answers on reconciliation")',
        "Memory · You · Preferences",
        "how they like to work → You · Preferences (third person)",
    ),
    (
        "an intention, a big decision or an upcoming life event",
        "Memory · Plans (one topic per plan)",
        "an intention or upcoming event → Plans",
    ),
    (
        "something unresolved the user will expect you to come back to",
        "Memory · Open items (no topic; expires after 14 days)",
        "something to come back to → Open items (expires in 14 days)",
    ),
    (
        "a fact about their money life that nothing above holds (housing, family, investing, "
        "tax, cards and payments, income and work, a bookkeeping convention no payee rule can "
        "express)",
        "Memory · Money · a topic name",
        "money life nothing above holds → Money · a topic name",
    ),
    (
        "anything else worth keeping",
        "Memory · Other",
        "anything else worth keeping → Other",
    ),
)

_GUIDE_HEADER = "Where a fact goes — the first line that matches wins:"

ROUTING_GUIDE: str = "\n".join(
    [_GUIDE_HEADER]
    + [f"{i}. If the fact is {when}: {home}." for i, (when, home, _) in enumerate(_ROUTES, start=1)]
)


def routing_guide_for_tool() -> str:
    """The same rows, compressed for a tool description."""
    rows = "; ".join(f"{i}) {short}" for i, (_, _, short) in enumerate(_ROUTES, start=1))
    return (
        f"Where a fact goes (first match wins): {rows}. "
        "Only You, Money, Plans, Open items and Other are memory."
    )
