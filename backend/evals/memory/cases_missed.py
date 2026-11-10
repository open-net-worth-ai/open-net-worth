"""The shapes an audit of real chats found the extractor missing (ADR amendment 12): an application
awaiting a decision, an offer turned down, an item-level category choice no payee rule can express,
and a passing remark that changes a stored figure. Invented data."""

from __future__ import annotations

from backend.evals.memory import checks as c
from backend.evals.memory import checks_keep as k
from backend.evals.memory.spec import QUALITY, Case, live

E31_PRIYA = "mem_e31a01"

MISSED_EXTRACT_CASES: tuple[Case, ...] = (
    Case(
        "E28",
        "extract",
        "an application now awaiting a decision becomes one Recent entry, without Lucius's gloss",
        turns=(
            (
                "user",
                "Northwind offered S$40,000 over 24 months for the renovation, S$2,900 interest "
                "in total. Should I go for it?",
            ),
            (
                "assistant",
                "Yes — it's the cheaper offer. One catch: Northwind charges 2% on whatever you "
                "repay early, so plan to run it the full 24 months.",
            ),
            ("user", "Ok, I applied with Northwind just now. Waiting for their answer."),
            (
                "assistant",
                "Noted — that loan is what keeps the renovation covered. Tell me the approved "
                "amount and rate when it comes.",
            ),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("Northwind", name="it names the lender"),
            c.mentions("applied", "awaiting", "decision", name="it says a decision is pending"),
            c.never_stored(
                r"keeps the renovation covered",
                name="Lucius's explanation is not part of the fact",
                severity=QUALITY,
            ),
            k.in_house_voice(),
            c.within_limit(),
        ),
    ),
    Case(
        "E29",
        "extract",
        "an offer the person turned down is said in the Recent entry for the plan they chose",
        turns=(
            ("user", "Acme came back with only S$10,000. I said no to them."),
            (
                "assistant",
                "Understood — Acme's S$10,000 is off the table. Northwind's S$40,000 is the one "
                "that covers the renovation.",
            ),
            ("user", "Yes, Northwind it is. I'll apply with them this week."),
            ("assistant", "Sounds good."),
        ),
        checks=(
            k.open_items_added(1, "exactly one Recent entry"),
            c.mentions("Acme", name="it says the Acme offer was turned down"),
            c.mentions("Northwind", name="it names the plan chosen"),
        ),
    ),
    Case(
        "E30",
        "extract",
        "an item-level category choice no payee rule can express becomes a Bookkeeping fact",
        turns=(
            (
                "assistant",
                "Logged the FreshMart delivery (S$18.60) under Household Supplies — tissues and "
                "dish soap.",
            ),
            ("user", "tissues and dish soap -> groceries for me"),
            ("assistant", "Done — moved it to Groceries."),
        ),
        checks=(
            c.filed_under("money", "bookkeeping", "groceries", "spending", "categor"),
            c.mentions("Groceries", name="it names the category"),
            c.mentions("tissue", "dish soap", name="it names the items"),
            k.open_items_added(0, "no Recent entry for a finished recategorisation"),
        ),
    ),
    Case(
        "E31",
        "extract",
        "a passing remark that fixes a varying figure updates the stored fact in place",
        facts=(
            live(
                E31_PRIYA,
                "Flatmate Priya pays by PayNow each month: rent S$500, internet S$10, plus a "
                "utilities share that varies by month.",
                "money",
                "Housing",
            ),
        ),
        turns=(
            (
                "assistant",
                "This month's utilities bill makes Priya's share S$34. Shall I ask her for that?",
            ),
            ("user", "it varies, but just keep S$30 flat for her"),
            ("assistant", "OK — S$30 flat for utilities from now on, S$540 a month in total."),
        ),
        checks=(
            c.updated_in_place(
                E31_PRIYA,
                now=("30",),
                gone=("varies by month",),
                name="the fact now says a flat S$30",
            ),
            c.no_new_facts("no second fact about the same arrangement"),
        ),
    ),
)
