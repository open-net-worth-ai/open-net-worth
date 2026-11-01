"""A step the person took themselves, outcome still to come, is a Recent entry — not "conversation
state" (ADR amendment 13). The rule used to skip "what is being waited on", which dropped a real
"I applied, waiting for the result"; E28 pins that one shape, these pin the rule. The last two
are the other side: Lucius's own unanswered offer, and a step Lucius already recorded on a plan,
stay out. Invented data."""

from __future__ import annotations

from backend.evals.memory import checks as c
from backend.evals.memory import checks_keep as k
from backend.evals.memory.spec import QUALITY, Case

WAITING_EXTRACT_CASES: tuple[Case, ...] = (
    Case(
        "E32",
        "extract",
        "a dispute the person raised by phone, answer pending, becomes one Recent entry",
        turns=(
            (
                "user",
                "I called Acme Bank this morning to dispute the S$84.00 QuickShip charge on my "
                "card. They said they'll get back to me within 10 working days.",
            ),
            (
                "assistant",
                "Got it. If they reverse it, it will show as an S$84.00 credit on the card.",
            ),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("disput", name="it says a dispute was raised"),
            c.mentions("84", name="it carries the amount"),
            c.mentions("QuickShip", "Acme", name="it names the charge or the bank"),
        ),
    ),
    Case(
        "E33",
        "extract",
        "a fee waiver the person asked for, no reply yet, becomes one Recent entry",
        turns=(
            (
                "user",
                "I've emailed Northwind asking them to waive the S$150 annual fee on my card. No "
                "reply yet.",
            ),
            ("assistant", "Many banks waive it on the first request, so that's a good start."),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("waive", "waiver", name="it says a waiver was asked for"),
            c.mentions("150", name="it carries the fee"),
        ),
    ),
    Case(
        "E34",
        "extract",
        "a claim the person submitted, decision weeks away, becomes one Recent entry without "
        "Lucius's gloss",
        turns=(
            (
                "user",
                "Submitted my course-fee tax relief claim for S$2,400 today. The tax office says "
                "4 to 6 weeks.",
            ),
            (
                "assistant",
                "Thanks. Once it's approved, the relief lowers your next assessment.",
            ),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("2,400", "2400", name="it carries the amount"),
            c.mentions("relief", "claim", name="it says what was submitted"),
            c.never_stored(
                r"lowers your next assessment",
                name="Lucius's explanation is not part of the fact",
                severity=QUALITY,
            ),
        ),
    ),
    Case(
        "E35",
        "extract",
        "an offer of Lucius's the person did not take up stays out of memory",
        turns=(
            (
                "assistant",
                "Your Acme card's S$150 annual fee posts next week. Want me to draft an email "
                "asking them to waive it?",
            ),
            ("user", "not now"),
            ("assistant", "No problem."),
        ),
        checks=(c.no_new_facts("nothing stored for an offer not taken up"),),
    ),
    Case(
        "E36",
        "extract",
        "a step Lucius recorded on a plan in the same chat is not repeated as a Recent entry",
        turns=(
            ("user", "I applied for the Northwind loan today, waiting for their answer."),
            (
                "assistant",
                "I've marked 'Apply for the Northwind loan' done on your Renovation plan. The "
                "'Loan disbursed' step stays open, due 15 November.",
            ),
            ("user", "thanks"),
        ),
        checks=(k.open_items_added(0, "no Recent entry: the plan already shows it"),),
    ),
)
