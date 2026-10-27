"""Stage ``sort``: legacy facts (written before sections existed, state ``unsorted``) go through
the one-time sort. The sort FILES a fact or RETIRES it with a reason; it never rewrites a text and
never deletes. Texts are invented and already in the third person, so any change to one is the
model editing, not the prompt's permitted tidy-up."""

from __future__ import annotations

from backend.evals.memory import checks_sort as c
from backend.evals.memory.spec import Case, legacy

S01_FACTS = (
    ("mem_s01a01", "Prefers short answers on reconciliation"),
    ("mem_s01a02", "Was born in March 1988"),
    ("mem_s01a03", "Works at Marlowe Analytics as a data engineer"),
    ("mem_s01a04", "Is saving for a kitchen renovation in mid-2027"),
    ("mem_s01a05", "Splits the rent 60/40 with a flatmate"),
    ("mem_s01a06", "The deposit dispute with the landlord was settled and the deposit refunded"),
    ("mem_s01a07", "Savings account balance is 18,350 SGD"),
    ("mem_s01a08", "Bought 40 shares of a global index fund on 3 March"),
    ("mem_s01a09", "Always show amounts in SGD"),
    ("mem_s01a10", "Uses the card ending 4821 for groceries"),
    ("mem_s01a11", "Plans a trip to Hokkaido in December"),
    ("mem_s01a12", "Speaks English and Mandarin at home"),
    ("mem_s01a13", "Categorise anything from Cold Storage as Groceries"),
    ("mem_s01a14", "Net income is about 7,500 SGD a month"),
)

S02_PREFERENCES = (
    "Likes tables over bullet lists for comparisons",
    "Prefers answers in plain English without jargon",
    "Wants the key number first, then the reasoning",
    "Likes a one-line takeaway at the end of an answer",
    "Prefers fewer follow-up questions on routine tasks",
    "Likes charts only when they show a trend",
    "Prefers dates written as 5 Mar 2027",
    "Likes a short checklist after a long answer",
    "Prefers rounding to whole dollars in summaries",
    "Likes comparisons against the previous month",
)
S02_LEDGER = (
    "Credit card balance is 1,240 SGD",
    "Bought 12 shares of a bond fund last week",
    "Paid 650 SGD for the electricity bill in May",
    "Emergency fund holds 9,800 SGD",
    "Sold 5 shares of a tech fund on 2 April",
    "Transferred 2,000 SGD to the savings account",
    "Index fund position is worth 31,400 SGD",
    "Received a 480 SGD refund from the airline",
    "Paid 1,150 SGD towards the car loan",
    "Brokerage cash balance is 3,300 SGD",
)
S02_PLANS = (
    "Plans to buy a flat in 2029",
    "Intends to take a sabbatical in early 2028",
    "Is saving for a wedding in late 2027",
    "Plans to move to a bigger flat next year",
    "Intends to learn to sail next year",
    "Is considering a master's degree starting in 2027",
    "Plans a family trip to Japan next spring",
    "Intends to renovate the bathroom in 2028",
    "Plans to switch to a four-day week",
    "Is saving for a second car",
)


def s02_facts() -> tuple[dict, ...]:
    rows, n = [], 0
    for group in (S02_PREFERENCES, S02_LEDGER, S02_PLANS):
        for text in group:
            rows.append(legacy(f"mem_s02{n:03d}", text, age=60 - n))
            n += 1
    return tuple(rows)


def _s01() -> tuple[dict, ...]:
    return tuple(
        legacy(fid, text, age=60 - i, source="explicit" if i == 9 else "implicit")
        for i, (fid, text) in enumerate(S01_FACTS)
    )


S01_CHECKS = (
    c.none_unsorted(),
    c.filed_as("mem_s01a01", "you", "pref"),
    c.filed_as("mem_s01a03", "you", "about"),
    c.filed_as("mem_s01a04", "plans"),
    c.filed_as("mem_s01a05", "money", "hous", "rent", "home", "flat"),
    c.filed_as("mem_s01a10", "money"),
    c.filed_as("mem_s01a11", "plans"),
    c.filed_as("mem_s01a12", "you", "about"),
    c.retired_as("mem_s01a02", "profile"),
    c.retired_as("mem_s01a14", "profile"),
    c.retired_as("mem_s01a06", "resolved", "stale"),
    c.retired_as("mem_s01a07", "ledger"),
    c.retired_as("mem_s01a08", "ledger"),
    c.retired_as("mem_s01a09", "instructions"),
    c.retired_as("mem_s01a13", "payee_rule"),
    c.text_untouched(),
    c.none_deleted(),
)


def _s02_checks() -> tuple:
    ids = [f"mem_s02{i:03d}" for i in range(30)]
    return (
        c.none_unsorted("all 30 facts are filed or retired across two batches"),
        c.group_filed(tuple(ids[:10]), "you", "pref", name="the 10 preferences are filed"),
        c.group_retired(tuple(ids[10:20]), "ledger", name="the 10 ledger figures are retired"),
        c.group_filed(tuple(ids[20:]), "plans", name="the 10 plans are filed"),
        c.text_untouched(),
        c.none_deleted(),
    )


SORT_CASES: tuple[Case, ...] = (
    Case(
        "S01",
        "sort",
        "14 legacy facts: durable ones are filed, profile / ledger / rule / payee / resolved "
        "ones are retired with the right reason, no text is touched",
        facts=_s01(),
        checks=S01_CHECKS,
    ),
    Case(
        "S02",
        "sort",
        "30 legacy facts need two batches; every one is handled and none is edited",
        facts=s02_facts(),
        checks=_s02_checks(),
    ),
)
