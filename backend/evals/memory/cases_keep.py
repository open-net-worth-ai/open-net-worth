"""The keep test (amendment 9), as cases: what fails it stores nothing, an Open item needs the
person's own words, and older notes in the old framing are filed in the house voice or moved
out. Every text is invented, shaped like what the old memory used to keep."""

from __future__ import annotations

from backend.evals.memory import checks as c
from backend.evals.memory import checks_keep as k
from backend.evals.memory import checks_sort as cs
from backend.evals.memory.spec import QUALITY, Case, legacy


def _talk(*turns: str) -> tuple[tuple[str, str], ...]:
    return tuple(("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(turns))


KEEP_EXTRACT_CASES: tuple[Case, ...] = (
    Case(
        "E19",
        "extract",
        "an incident and the lesson Lucius draws from it store nothing",
        turns=_talk(
            "The broker login keeps failing again. Don't keep retrying it, I'll just paste the "
            "statement in myself.",
            "Understood — I'll stop retrying and use the statement you paste.",
            "Also the browser connector closed its tab between calls, so the screenshot never "
            "came through.",
            "Thanks, noted. Paste the statement whenever you're ready.",
        ),
        checks=(c.no_new_facts("stores nothing from an incident"),),
    ),
    Case(
        "E20",
        "extract",
        "a bookkeeping thread left mid-way stores nothing — the ledger shows it",
        turns=_talk(
            "What's this 1,250,000 VND Lotus Bistro line on the Maybank statement?",
            "It posted on 2 Oct and is probably the uncleared 28 Sep entry for the same amount. "
            "Shall I link them?",
            "Hmm, not sure.",
            "No problem — it stays uncleared until you decide.",
        ),
        checks=(c.no_new_facts("stores nothing for an open bookkeeping thread"),),
    ),
    Case(
        "E21",
        "extract",
        "the person asking to be reminded makes exactly one Open item",
        turns=_talk(
            "I'll call the insurer tomorrow about the renewal — remind me if I forget.",
            "Will do.",
        ),
        checks=(
            k.open_items_added(1, "exactly one open item"),
            c.open_item_with_expiry(),
            k.in_house_voice(),
        ),
    ),
    Case(
        "E22",
        "extract",
        "what Lucius offered and the person did not take up is not an Open item",
        turns=_talk(
            "How are my speculative holdings doing?",
            "RIVN and SOFI are up this week. Want me to set price alerts about 15% below the "
            "current prices?",
            "Maybe, I'll think about it.",
            "Sure — just say the word.",
        ),
        checks=(k.open_items_added(0, "no open item for an offer"),),
    ),
    Case(
        "E23",
        "extract",
        "a lasting preference is stored as its kernel, in the house voice, with no history",
        turns=_talk(
            "My flatmate and I split shared bills item by item — each item and each of our "
            "shares, like the FairPrice one on 12 Sep — because lump sums confuse him.",
            "That makes sense.",
        ),
        checks=(
            c.stores_some("stores the preference"),
            k.in_house_voice(),
            c.never_stored(
                r"12 sep|fairprice", name="without the example's history", severity=QUALITY
            ),
        ),
    ),
)

S03_FACTS = (
    ("mem_s03a01", "User prefers concise answers; lead with the headline answer."),
    ("mem_s03a02", "User's flatmate Priya pays S$540 a month by PayNow near month-end."),
    (
        "mem_s03a03",
        "As of 2026-09-30: Maybank statement shows 1,250,000 VND 'Lotus Bistro' posted 2 Oct; "
        "likely the uncleared 28 Sep entry. Unconfirmed.",
    ),
    (
        "mem_s03a04",
        "Advisor proposed price alerts ~15% below current prices on RIVN and SOFI; user hasn't "
        "decided.",
    ),
    (
        "mem_s03a05",
        "User clarified tolerance for the broker login blocker (2026-07-14): willing to paste "
        "statements manually.",
    ),
    (
        "mem_s03a06",
        "Batch checks miss single-row amount errors; when a discrepancy is reported, compare that "
        "exact row.",
    ),
)

S03_CHECKS = (
    cs.none_unsorted(),
    cs.filed_as("mem_s03a01", "you", "pref"),
    cs.filed_as("mem_s03a02", "money"),
    cs.retired_as("mem_s03a03", "ledger", "stale", "resolved"),
    cs.retired_as("mem_s03a04", "stale", "resolved"),
    cs.retired_as("mem_s03a05", "stale", "resolved"),
    cs.retired_as("mem_s03a06", "stale", "instructions"),
    k.filed_in_voice(("mem_s03a01", "mem_s03a02"), "the filed notes lose the old framing"),
    k.none_filed_as_open_items(),
    cs.none_deleted(),
)

KEEP_SORT_CASES: tuple[Case, ...] = (
    Case(
        "S03",
        "sort",
        "older notes in the old framing: the lasting ones are filed in the house voice, a "
        "bookkeeping thread, an offer, an incident and a lesson move out, none becomes an open item",
        facts=tuple(legacy(fid, text, age=40 - i) for i, (fid, text) in enumerate(S03_FACTS)),
        checks=S03_CHECKS,
    ),
)

__all__ = ["KEEP_EXTRACT_CASES", "KEEP_SORT_CASES", "S03_FACTS"]
