"""The background extractor's prompt and the listing of what is already remembered.

Pure text builders. The listing shows EVERY remembered fact in full — a live one with the
section and topic it is filed under, a legacy one as ``[unsorted]`` so the extractor still
sees it when it checks for a duplicate — because a cap or a cut-off hides exactly the fact
a new one would repeat. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home``.
"""

from __future__ import annotations

from backend.ai import memory_structure as ms

EXTRACT_SYSTEM = (
    "You maintain the long-term memory of a personal financial advisor about their client. "
    "You extract durable facts and dated intentions from a conversation and reconcile them "
    "with what is already remembered. You are precise and conservative — only record things "
    "that are genuinely worth carrying into future conversations."
)


def listing_label(fact: dict) -> str:
    """Where a fact sits, as the extractor reads it: ``Money · Housing``, ``Open items`` or
    ``unsorted`` for a legacy fact not yet filed."""
    section = fact.get("section")
    if fact.get("state") != "live" or section not in ms.SECTIONS:
        return "unsorted"
    label = ms.SECTIONS[section]
    return f"{label} · {fact['topic']}" if fact.get("topic") else label


def existing_listing(facts: list[dict]) -> str:
    """``id: [Money · Housing] content`` per fact, then the topic names already in use."""
    if not facts:
        return "(none yet)"
    lines = [f"{f['id']}: [{listing_label(f)}] {f.get('content', '')}" for f in facts]
    topics: dict[str, dict[str, str]] = {}
    for f in facts:
        if f.get("state") == "live" and f.get("section") in ms.SECTIONS and f.get("topic"):
            topics.setdefault(ms.SECTIONS[f["section"]], {}).setdefault(
                ms.topic_key(f["topic"]), f["topic"]
            )
    if topics:
        used = "; ".join(
            f"{section}: {', '.join(sorted(names.values()))}" for section, names in topics.items()
        )
        lines.append(f"\nTopics in use — {used}")
    return "\n".join(lines)


def extract_prompt(today: str, instructions: str, existing: str, convo: str) -> str:
    suggested = "; ".join(
        f"{ms.SECTIONS[k]}: {', '.join(v)}" for k, v in ms.SUGGESTED_TOPICS.items() if v
    )
    return f"""Today is {today}.

Given this conversation between a user and their financial advisor, extract facts worth
remembering across future conversations: decisions, intentions, plans, constraints, and
important personal/financial context. Ignore one-off data lookups and small talk.

NEVER record a transaction, sale, disposal, purchase, transfer, or rule-creation as a
completed or dated fact — those belong to the system of record (the ledger, lots/realized
events, positions, rules), NOT to memory. If the user merely discussed or proposed an action,
capture it (if at all) as an INTENTION ("user is considering selling X"), never as history
("user sold X on <date>").

Strongly prefer durable, low-churn facts: preferences, goals, risk posture, account
structure, and standing intentions. Prefer durable preferences/context over perishable figures
(share counts, live prices, balances) that the tools read live — those go stale immediately.
AVOID memorizing quantities, share counts, live prices, or account balances — a memorized
figure goes stale the moment the price moves or a trade lands (an "underwater META at $598"
note is wrong tomorrow). Record the durable kernel instead ("holds META as a long-term equity
position"), not the live figure; a fact that carries one is dropped.

{ms.ROUTING_GUIDE}
You only store the lines whose home starts with "Memory". Skip a fact whose home is anything
else: it is written elsewhere, in a conversation.

Never store conversation or run state (what is being waited on, what was offered, what an
automation did), ledger figures (balances, holdings, prices, trades), a task that is finished,
or a full card, account, ID or passport number or a password (the last 4 digits are fine).
Write each Preferences fact in the third person ("Prefers short answers on reconciliation").

The user's Instructions for Lucius are followed on every turn. Do not store what these
instructions already say:
{instructions.strip() or "(none)"}

Each fact is at most {ms.FACT_MAX_CHARS} characters. For each fact choose:
  - section: one of {", ".join(ms.SECTIONS)} (open_items expire after {ms.OPEN_ITEM_DEFAULT_DAYS} days
    unless you give an expires_at; they take no topic)
  - topic: a short name for the subject. Reuse a name from "Topics in use" below when one fits;
    invent a new one only when none does (suggested: {suggested})
  - sensitive: true for health, religion, political views, sexuality, ethnicity, criminal or
    legal matters. Never store a full card, account, ID or passport number or a password.

Decide whether each fact UPDATES an existing memory (reuse its id) or is NEW. If the
conversation contradicts an existing memory, prefer an update (latest wins). If a fact is
no longer true, list its id under "remove". If the conversation re-confirms an existing
memory without changing it, list its id under "mentioned".

Existing memory:
{existing}

Conversation:
{convo}

Return ONLY JSON in this exact shape:
{{
  "new":     [{{"content": "...", "section": "you|money|plans|open_items|other", "topic": "...|null", "expires_at": "YYYY-MM-DD|null", "sensitive": false}}],
  "updates": [{{"id": "...", "content": "...", "section": "...", "topic": "...|null", "expires_at": "YYYY-MM-DD|null"}}],
  "remove":  ["id", ...],
  "mentioned": ["id", ...]
}}
If there is nothing worth remembering, return {{"new": [], "updates": [], "remove": [], "mentioned": []}}."""
