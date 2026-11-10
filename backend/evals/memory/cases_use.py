"""Stage ``use``: a full chat turn in the scratch book — the real system prompt (memory block and
citing rule included), the real tool registry, the chosen model. The expensive stage."""

from __future__ import annotations

from backend.evals.memory import checks_use as c
from backend.evals.memory.spec import Case, live

U01_RENT = "mem_u01a01"
U01_EMPLOYER = "mem_u01b02"
U01_BADMINTON = "mem_u01c03"
U01_STYLE = "mem_u01d04"
U02_BANKS = "mem_u02a01"

_U01_IDS = (U01_RENT, U01_EMPLOYER, U01_BADMINTON, U01_STYLE)

USE_CASES: tuple[Case, ...] = (
    Case(
        "U01",
        "use",
        "a reply that leans on one remembered fact cites it; the citation tag never reaches the "
        "reader and only that fact is marked used",
        facts=(
            live(U01_RENT, "Splits the rent 60/40 with flatmate Priya", "money", "Housing"),
            live(U01_EMPLOYER, "Works at Marlowe Analytics as a data engineer", "you", "Profile"),
            live(U01_BADMINTON, "Plays badminton on Saturday mornings", "you", "Profile"),
            live(U01_STYLE, "Prefers short answers on reconciliation", "you", "Preferences"),
        ),
        question="How should I split this month's rent with my flatmate?",
        checks=(
            c.reply_has_no_tag(_U01_IDS, "the citation tag and handles never reach the reader"),
            c.cited_fact_stamped(U01_RENT, "the fact the reply used is marked used"),
            c.unrelated_not_stamped(
                (U01_EMPLOYER, U01_BADMINTON), "unrelated facts are not marked used"
            ),
        ),
    ),
    Case(
        "U02",
        "use",
        "memory is context, not proof: asked for a count the book holds, the model reads the book "
        "and answers with the book's number, not the remembered one",
        facts=(live(U02_BANKS, "Has two accounts at DBS", "you", "Profile"),),
        accounts=("DBS Multiplier", "DBS Savings", "DBS Vickers Cash"),
        question="How many DBS accounts do I have?",
        checks=(
            c.reply_has_no_tag((U02_BANKS,), "the citation tag and handles never reach the reader"),
            c.read_the_book("an account read happens before the answer"),
            c.answer_matches_book(3, "the answer gives the book's count (3), not memory's (2)"),
        ),
    ),
)
