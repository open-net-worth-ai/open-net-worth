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
    "open_items": "Recent",
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

# The key stays ``open_items``: the section was "Open items" until amendment 10 widened it to
# Recent context — what was decided or is in flight in the last two weeks — with no data to move.
OPEN_ITEMS = "open_items"
_SECTION_ALIASES = {"recent": OPEN_ITEMS, "recent_context": OPEN_ITEMS}
# Recent entries a chat pass may add, and how many may be live at once (the oldest lapse first).
RECENT_MAX_NEW_PER_PASS = 3
RECENT_MAX_LIVE = 10

# The You topic was called "About you" until 2026-10-27 (ADR amendment 7, R30); a write that still
# names it lands under "Profile", and revision 202610271800 renamed the stored ones.
PROFILE_TOPIC = "Profile"
_RENAMED_YOU_TOPICS = frozenset({"about you"})

# A fact is refused past FACT_MAX_CHARS; every prompt asks for FACT_TARGET_CHARS, so most land
# well under the limit and one that runs a little long is still kept (2026-10-30).
FACT_MAX_CHARS = 500
FACT_TARGET_CHARS = 280
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
    key = _SECTION_ALIASES.get(key, key)
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
        "a balance, holding, price, transaction, trade, a task that was done, or a bookkeeping "
        "follow-up the book already shows (an unmatched or uncleared entry, a pending charge, a "
        "missing memo, a statement or import line to match)",
        "Nowhere — the ledger and its review screens already have it",
        "balances, holdings, prices, trades, finished tasks, bookkeeping follow-ups → nowhere "
        "(the ledger has them)",
    ),
    (
        "a goal or commitment already recorded on the Plan screen (a named goal with its "
        "target, a scheduled commitment)",
        "Plan / goals / commitments — not memory",
        "a goal or commitment → Plan / goals / commitments",
    ),
    (
        "the state of this conversation or a run (what is being waited on, what was offered or "
        "proposed, what awaits the user's confirmation, what an automation flagged)",
        "Nowhere",
        "conversation state → nowhere",
    ),
    (
        "about the user but no Settings profile field fits (an employer, languages, who they live "
        "with — partner, children, flatmates — personal context; NOT rent or bills split with "
        "them, nor money sent between them: those are Money)",
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
        "decided, agreed or asked for in this conversation and still in flight over the next two "
        "weeks, with no screen that shows it — an agreement about a payment someone owes and how "
        "to book it, a decision whose result is still to come, something the user said they will "
        "do or asked to be reminded of",
        f"Memory · Recent (no topic; written ready to act on — who, how much, how to book; lapses "
        f"after {OPEN_ITEM_DEFAULT_DAYS} days)",
        f"decided or in flight, the next two weeks → Recent (lapses in {OPEN_ITEM_DEFAULT_DAYS} "
        "days)",
    ),
    (
        "a fact about their money life that nothing above holds (housing, rent or bills split "
        "with a flatmate or partner, money they send or receive within the family, investing, "
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

# The one test every writer applies (extraction, the upgrade sort, import), amendment 9.
KEEP_TEST = (
    "Keep a fact only when it passes all three: it is about the person or their money life (not "
    "about how Lucius did a task); it will still be true in three months (not a snapshot, an "
    "incident, or a thread left mid-way); and nothing else in the app already holds it. Never "
    "keep: a lesson about how Lucius should do a task, learned from one mistake or incident (that "
    "is a fix to the app, or an Instructions rule if the person states it as one); a problem with "
    "the app or a connector, or how they got around one (a login that kept failing); what Lucius suggested and the person did not take up; research "
    "results (card comparisons, product mechanics); a what-if they were only exploring. Store only "
    'the lasting part: no history tail ("decided on…", "stated…", "awaiting…", "as of…") — keep a '
    "date only when it is the fact itself (a due date, a start date). This test is for long-term "
    "facts; a Recent entry is the one exception to its three months — something decided or still "
    "in flight that matters over the next two weeks."
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
        "Only You, Money, Plans, Recent and Other are memory."
    )
