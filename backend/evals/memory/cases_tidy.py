"""Stage ``tidy``: the daily clean-up merges duplicate facts and near-duplicate topic names. The
safety bar is the other half of the job — a fact that says something no other fact says must
survive, and no topic may lose more than half of its facts. T03 runs the topic-summary pass that
rides the same sweep: one short line per topic that describes it and restates no number."""

from __future__ import annotations

from backend.evals.memory import checks_sort as c
from backend.evals.memory.spec import Case, live

T01_DUP_A = "mem_t01a01"
T01_DUP_B = "mem_t01a02"

_T01 = (
    live(T01_DUP_A, "Rents a two-bedroom flat in Tampines", "money", "Housing"),
    live(T01_DUP_B, "Rents a 2-bedroom flat in Tampines", "money", "Housing"),
    live("mem_t01a03", "Splits the rent 60/40 with a flatmate", "money", "Housing"),
    live("mem_t01a04", "The landlord is Mr Lim", "money", "Home"),
    live("mem_t01a05", "Pays the rent on the 3rd of each month", "money", "housing"),
    live("mem_t01b01", "Prefers short answers on reconciliation", "you", "Preferences"),
    live("mem_t01b02", "Likes tables over bullet lists", "you", "Preferences"),
    live("mem_t01c01", "Works at Marlowe Analytics as a data engineer", "you", "Profile"),
    live("mem_t01d01", "Is saving for a kitchen renovation starting mid-2027", "plans", "Kitchen"),
)

_T02 = (
    live("mem_t02b01", "Prefers short answers on reconciliation", "you", "Preferences"),
    live("mem_t02b02", "Likes tables over bullet lists", "you", "Preferences"),
    live("mem_t02b03", "Wants the key number before the reasoning", "you", "Preferences"),
    live("mem_t02c01", "Works at Marlowe Analytics as a data engineer", "you", "Profile"),
    live("mem_t02c02", "Speaks English and Mandarin at home", "you", "Profile"),
    live("mem_t02a01", "Rents a two-bedroom flat in Tampines", "money", "Housing"),
    live("mem_t02a02", "Pays the rent on the 3rd of each month", "money", "Housing"),
    live("mem_t02d01", "Plans a trip to Hokkaido in December", "plans", "Hokkaido trip"),
)

_T03 = (
    live("mem_t03a01", "Works at Marlowe Analytics as a data engineer", "you", "Profile"),
    live("mem_t03a02", "Speaks English and Mandarin at home", "you", "Profile"),
    live("mem_t03a03", "Has lived in Singapore for 12 years, moved from Penang", "you", "Profile"),
)

TIDY_CASES: tuple[Case, ...] = (
    Case(
        "T01",
        "tidy",
        "two near-duplicate facts merge to one and 'Home' folds into 'Housing'; every distinct "
        "fact survives",
        facts=_T01,
        checks=(
            c.merged_to_one((T01_DUP_A, T01_DUP_B), "the duplicate pair merges to one fact"),
            c.topics_unified("money", ("housing", "home"), "Home and Housing become one topic"),
            c.all_survive(
                (
                    ("mem_t01a03", "60/40"),
                    ("mem_t01a04", "Mr Lim"),
                    ("mem_t01a05", "3rd"),
                    ("mem_t01b01", "short answers"),
                    ("mem_t01b02", "tables"),
                    ("mem_t01c01", "Marlowe"),
                    ("mem_t01d01", "kitchen"),
                ),
                "every distinct fact survives",
            ),
            c.no_topic_gutted(),
        ),
    ),
    Case(
        "T02",
        "tidy",
        "memory with nothing to merge is left exactly as it was",
        facts=_T02,
        checks=(
            c.all_survive(
                (
                    ("mem_t02b01", "short answers"),
                    ("mem_t02b02", "tables"),
                    ("mem_t02b03", "key number"),
                    ("mem_t02c01", "Marlowe"),
                    ("mem_t02c02", "Mandarin"),
                    ("mem_t02a01", "Tampines"),
                    ("mem_t02a02", "3rd"),
                    ("mem_t02d01", "Hokkaido"),
                ),
                "every distinct fact survives",
            ),
            c.no_topic_gutted(),
            c.unchanged("no fact is merged, retopiced or reworded"),
        ),
    ),
    Case(
        "T03",
        "tidy",
        "the You · Profile topic gets a one-line summary that describes it without its numbers",
        facts=_T03,
        summaries=True,
        checks=(
            c.summary_written(
                "you", "Profile", 120, "the topic has a summary of 120 chars or less"
            ),
            c.summary_restates_no_number(
                "you", "Profile", "the summary restates no number from the facts"
            ),
        ),
    ),
)
