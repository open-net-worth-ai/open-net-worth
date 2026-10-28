"""Recent context (amendment 10), as cases: a chat that agrees how a payment will be booked leaves
one Recent entry ready to act on, and a later run — here a Capture-style bank notification — books
the payment as agreed rather than by the usual split, then closes the entry. Invented data."""

from __future__ import annotations

from backend.evals.memory import checks as c
from backend.evals.memory import checks_keep as k
from backend.evals.memory import checks_recent as r
from backend.evals.memory.spec import Case, live

U03_USUAL = "mem_u03a01"
U03_AGREED = "mem_u03b02"

E24_TURNS = (
    (
        "user",
        "Send Priya the November request: rent S$500 as usual, utilities only S$15 since she was "
        "away half the month, internet S$10, plus her S$12.40 half of the FairPrice run. Total "
        "S$537.40.",
    ),
    (
        "assistant",
        "Here's the message for Priya: rent S$500, utilities S$15, internet S$10 and S$12.40 for "
        "the FairPrice run — S$537.40 in total.",
    ),
)

RECENT_EXTRACT_CASES: tuple[Case, ...] = (
    Case(
        "E24",
        "extract",
        "an agreed payment split becomes one Recent entry, ready to book when the money arrives",
        turns=E24_TURNS,
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("537.40", name="it carries the agreed total"),
            c.mentions("12.40", name="it carries the agreed split, the grocery share included"),
            k.in_house_voice(),
        ),
    ),
)

RECENT_USE_CASES: tuple[Case, ...] = (
    Case(
        "U03",
        "use",
        "a bank notification for the agreed payment is booked as agreed, not by the usual split, "
        "and the Recent entry it fulfils is closed",
        facts=(
            live(
                U03_USUAL,
                "Flatmate Priya pays S$540 a month by PayNow: rent S$500, utilities S$30, internet "
                "S$10.",
                "money",
                "Housing",
            ),
            live(
                U03_AGREED,
                "When Priya's PayNow for November arrives (S$537.40): book Rent S$500, Utilities "
                "S$15, Phone & Internet S$10, Groceries S$12.40.",
                "open_items",
                expires_at="2099-12-31",
            ),
        ),
        accounts=("DBS Multiplier",),
        unattended=True,
        question=(
            "Notification from DBS digibank: You received S$537.40 from PRIYA NAIR via PayNow to "
            "your DBS Multiplier account. Book what moved, then reply with one short line saying "
            "what you booked."
        ),
        checks=(
            r.recent_closed(U03_AGREED, "the Recent entry the run acted on is closed"),
            r.run_mentions(
                r"12\.40",
                "it books the agreed split (the S$12.40 grocery share), not the usual one",
            ),
            r.usual_fact_kept(U03_USUAL, "the standing arrangement stays as it was"),
        ),
    ),
)

__all__ = ["RECENT_EXTRACT_CASES", "RECENT_USE_CASES"]
