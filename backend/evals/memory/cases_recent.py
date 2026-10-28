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
E25_TODO = "mem_e25a01"
U04_AGREED, U05_AGREED, U06_AGREED = "mem_u04b02", "mem_u05b02", "mem_u06b02"

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
    Case(
        "E25",
        "extract",
        "a vague Recent to-do the conversation settles is upgraded in place, ready to book",
        facts=(
            live(
                E25_TODO,
                "Collect Priya's share of the FairPrice run.",
                "open_items",
                expires_at="2099-12-31",
            ),
        ),
        turns=(
            (
                "user",
                "The FairPrice run came to S$24.80, so Priya owes half, S$12.40. When she pays, "
                "book it back to Groceries.",
            ),
            ("assistant", "Noted: S$12.40 from Priya for the FairPrice run, back to Groceries."),
        ),
        checks=(
            c.updated_in_place(E25_TODO, now=("12.40",), gone=(), name="it carries the amount"),
            c.updated_in_place(
                E25_TODO, now=("Groceries",), gone=(), name="it says where the share is booked"
            ),
            k.open_items_added(0, "no second entry about the same thing"),
        ),
    ),
    Case(
        "E26",
        "extract",
        "an agreement the person typed with a typo still becomes a Recent entry",
        turns=(
            ("user", "How much is Priya's share this month?"),
            (
                "assistant",
                "S$540 as usual: rent S$500, utilities S$30, internet S$10. She was away two "
                "weeks — want me to lower her utilities?",
            ),
            (
                "user",
                "yes ok, utliities only S$15 for her this month, rest as usual, book it like that "
                "when she pays",
            ),
            ("assistant", "Done — S$525 this month: rent S$500, utilities S$15, internet S$10."),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("525", "15", name="it carries the agreed figures"),
        ),
    ),
    Case(
        "E27",
        "extract",
        "a split Lucius proposed and the person only hummed at is not a Recent entry",
        turns=(
            ("user", "Priya's paying late this month I think."),
            (
                "assistant",
                "Want me to book her PayNow as rent S$500 and utilities S$40 when it comes, since "
                "the bill was higher?",
            ),
            ("user", "Hmm, maybe. Not sure yet."),
            ("assistant", "No problem — just say the word."),
        ),
        checks=(k.open_items_added(0, "no Recent entry for a proposal left open"),),
    ),
)


def _priya(usual: str, agreed: str) -> tuple[dict, dict]:
    """The standing arrangement and November's agreed booking, as U03 seeds them."""
    return (
        live(
            usual,
            "Flatmate Priya pays S$540 a month by PayNow: rent S$500, utilities S$30, internet "
            "S$10.",
            "money",
            "Housing",
        ),
        live(
            agreed,
            "When Priya's PayNow for November arrives (S$537.40): book Rent S$500, Utilities "
            "S$15, Phone & Internet S$10, Groceries S$12.40.",
            "open_items",
            expires_at="2099-12-31",
        ),
    )


def _notification(amount: str, sender: str) -> str:
    return (
        f"Notification from DBS digibank: You received S${amount} from {sender} via PayNow to "
        "your DBS Multiplier account. Book what moved, then reply with one short line saying "
        "what you booked."
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
    Case(
        "U04",
        "use",
        "the agreed payment arrives a little over: the agreed split is booked, the difference "
        "on a line of its own, and the entry is closed",
        facts=_priya("mem_u04a01", U04_AGREED),
        accounts=("DBS Multiplier",),
        unattended=True,
        question=_notification("540.00", "PRIYA NAIR"),
        checks=(
            r.recent_closed(U04_AGREED, "the Recent entry is closed"),
            r.run_mentions(r"12\.40", "it books the agreed split"),
            r.run_mentions(r"2\.60", "the S$2.60 over the agreed amount is its own line"),
        ),
    ),
    Case(
        "U05",
        "use",
        "a part payment far short of the agreed amount does not fulfil the entry, which stays open",
        facts=_priya("mem_u05a01", U05_AGREED),
        accounts=("DBS Multiplier",),
        unattended=True,
        question=_notification("200.00", "PRIYA NAIR"),
        checks=(r.recent_open(U05_AGREED, "the Recent entry stays open for the rest"),),
    ),
    Case(
        "U06",
        "use",
        "a payment from someone else leaves an unrelated Recent entry alone",
        facts=_priya("mem_u06a01", U06_AGREED),
        accounts=("DBS Multiplier",),
        unattended=True,
        question=_notification("80.00", "DANIEL TAN"),
        checks=(
            r.recent_open(U06_AGREED, "Priya's entry stays open"),
            r.usual_fact_kept(U06_AGREED, "Priya's entry is not rewritten"),
        ),
    ),
)

__all__ = ["RECENT_EXTRACT_CASES", "RECENT_USE_CASES"]
