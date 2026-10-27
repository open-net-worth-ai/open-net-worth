"""What memory is made of: five fixed sections, a free-text topic per fact, the limits, and the
ONE routing guide that decides whether a fact belongs in memory at all.

Leaf of the memory modules (imports nothing from them) so the write choke, the renderer, the
extractor and the tools all read the same constants. ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 4 wins).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

SECTIONS: dict[str, str] = {
    "you": "You",
    "money": "Money",
    "plans": "Plans",
    "open_items": "Open items",
    "other": "Other",
}

SUGGESTED_TOPICS: dict[str, tuple[str, ...]] = {
    "you": ("Profile", "Preferences"),
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

# The You topic was called "About you" until 2026-10-27 (ADR amendment 7, R30); a write that still
# names it lands under "Profile", and revision 202610271800 renamed the stored ones.
PROFILE_TOPIC = "Profile"
_RENAMED_YOU_TOPICS = frozenset({"about you"})

FACT_MAX_CHARS = 280
SENSITIVE_TOPICS = (
    "health, religion, political views, sexuality, ethnicity, criminal or legal matters"
)
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
    "merged",
    "identifier",
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


def current_topic(section: str | None, name: str) -> str:
    """``name`` as it is filed today: the You topic once called "About you" is "Profile"."""
    if section == "you" and topic_key(name) in _RENAMED_YOU_TOPICS:
        return PROFILE_TOPIC
    return name


def fingerprint(text) -> str:
    """SHA-256 of a fact's text with case, punctuation and spacing folded away, so "Has a dog
    named Biscuit" and "has a dog, named biscuit!" match, and so do Unicode variants (NFKC). What
    a user forgot is remembered only as this hash; a paraphrase has a different one."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    folded = re.sub(r"[\W_]+", " ", text.casefold()).strip()
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def kept_apart(remove, updates) -> list[str]:
    """The ids a model reply moves out of memory, minus any it also updates: one reply never
    both keeps and retires a fact, and keeping it is the safe reading."""
    kept = {u.get("id") for u in updates or [] if isinstance(u, dict)}
    out: list[str] = []
    for fid in remove or []:
        if isinstance(fid, str) and fid not in kept and fid not in out:
            out.append(fid)
    return out


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
        "something the Settings profile has a field for (name, date of birth, residence, nationality, "
        "tax residency, employment TYPE such as salaried or self-employed, marital status, "
        "income, expenses, saving, risk, retirement age, target allocation, briefing time, "
        "benchmark) — an employer's name, a job title or an industry has no Settings field",
        "a Settings profile field (update_profile) — not memory",
        "a Settings profile field → update_profile",
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
        "a goal or commitment already recorded on the Plan screen (a named goal with its "
        "target, a scheduled commitment)",
        "Plan / goals / commitments — not memory",
        "a goal or commitment → Plan / goals / commitments",
    ),
    (
        "the state of this conversation (what is being waited on, what was offered)",
        "Nowhere",
        "conversation state → nowhere",
    ),
    (
        "about the user but no Settings profile field fits (an employer, languages, who they live "
        "with — partner, children, flatmates — personal context)",
        "Memory · You · Profile",
        "about the user, no Settings field → You · Profile (a memory topic)",
    ),
    (
        "an observation about how the user likes to work, inferred or said in passing — written "
        'in the third person ("Prefers short answers on reconciliation")',
        "Memory · You · Preferences",
        "how they like to work → You · Preferences (third person)",
    ),
    (
        "an intention, a saving aim, a big decision or an upcoming life event — even with a "
        'date or an amount ("putting money aside to study abroad in 2028")',
        "Memory · Plans (one topic per plan)",
        "an intention or upcoming event → Plans",
    ),
    (
        "something unresolved the user will expect you to come back to",
        f"Memory · Open items (no topic; expires after {OPEN_ITEM_DEFAULT_DAYS} days)",
        f"something to come back to → Open items (expires in {OPEN_ITEM_DEFAULT_DAYS} days)",
    ),
    (
        "a fact about their money life that nothing above holds (housing, money they send or "
        "receive within the family, investing, "
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

_GUIDE_HEADER = (
    "Where a fact goes — the first line that matches wins (a Settings profile field and the "
    "memory topic You · Profile are two different homes):"
)

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
