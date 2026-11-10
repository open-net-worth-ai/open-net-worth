"""Stage ``extract``: seed, run the background extractor over a short conversation, score what it
stored. Every name, amount and number is invented."""

from __future__ import annotations

from backend.evals.memory import checks as c
from backend.evals.memory.spec import QUALITY, Case, live

E10_HOUSING = "mem_e10h01"
E11_PREFERENCE = "mem_e11p01"
E12_RENT = "mem_e12r01"
E18_FORGOTTEN = "Has a dog named Biscuit"


def _talk(user: str, assistant: str = "Understood.") -> tuple[tuple[str, str], ...]:
    return (("user", user), ("assistant", assistant))


EXTRACT_CASES: tuple[Case, ...] = (
    Case(
        "E01",
        "extract",
        "a preference said in passing becomes one third-person Preferences fact",
        turns=_talk(
            "I tend to skim long answers, so shorter replies when we go through reconciliation "
            "suit me better.",
            "Noted, I'll keep reconciliation replies short.",
        ),
        checks=(
            c.stores_one(),
            c.filed_under("you", "preferences"),
            c.third_person(),
            c.within_limit(),
        ),
    ),
    Case(
        "E02",
        "extract",
        "profile-field facts (birth date, nationality, marital status, income) are not memory",
        turns=_talk(
            "Just so you have it: I was born on 14 March 1988, I'm a Singapore citizen, I'm "
            "married, and my take-home pay is about 7,500 SGD a month.",
            "Thanks, that belongs on your profile.",
        ),
        checks=(c.no_new_facts("files no profile field into memory"),),
    ),
    Case(
        "E03",
        "extract",
        "a standing rule ('always', 'never') is not a memory fact",
        turns=_talk(
            "From now on, always show amounts in SGD and never round them.",
            "I'll follow that from now on.",
        ),
        checks=(c.no_new_facts("files no standing rule into memory"),),
    ),
    Case(
        "E04",
        "extract",
        "ledger facts (a purchase, a balance) are not memory",
        turns=_talk(
            "I bought 50 shares of a global index fund yesterday for 4,200 SGD. My savings "
            "account has 18,350 SGD after the transfer.",
            "Got it, the ledger has both movements.",
        ),
        checks=(c.no_new_facts("stores no ledger figure"),),
    ),
    Case(
        "E05",
        "extract",
        "conversation state ('waiting for the statement') is not memory",
        turns=(
            (
                "assistant",
                "Please upload the statement when you can. I'm waiting for it before I reconcile.",
            ),
            ("user", "Sure, give me a minute."),
        ),
        checks=(c.no_new_facts("stores no conversation state"),),
    ),
    Case(
        "E06",
        "extract",
        "employer, languages and household go to You / Profile",
        turns=_talk(
            "Quick background so you know me better: I'm a data engineer at Marlowe Analytics, "
            "we speak English and Mandarin at home, and it's just me, my partner Jun and our "
            "daughter in the flat.",
            "Thanks for the background.",
        ),
        checks=(
            c.every_new_fact_in("you", "profile", name="every fact is filed under You / Profile"),
            c.mentions("marlowe", name="records the employer"),
            c.mentions("mandarin", name="records the languages"),
            c.mentions("partner", "daughter", "jun", name="records the household"),
        ),
    ),
    Case(
        "E07",
        "extract",
        "a plan becomes a Plans fact whose topic names the plan",
        turns=_talk(
            "We're saving up for a kitchen renovation and hope to start around mid-2027.",
            "A good target. I can build a savings plan for it.",
        ),
        checks=(
            c.filed_under("plans", name="filed under Plans"),
            c.filed_under("plans", "kitchen", "renovation", name="the topic names the plan"),
        ),
    ),
    Case(
        "E08",
        "extract",
        "an unresolved follow-up becomes an Open item that expires and has no topic",
        turns=_talk(
            "I haven't decided whether to dispute my landlord's deposit deduction. Can we come "
            "back to that next week?",
            "Of course, I'll raise it again next week.",
        ),
        checks=(c.open_item_with_expiry(),),
    ),
    Case(
        "E09",
        "extract",
        "money-life context (a rent split) goes to Money under a sensible topic",
        turns=_talk(
            "My flatmate Priya and I split the rent 60/40 because I have the bigger room.",
            "That's a clear arrangement.",
        ),
        checks=(
            c.filed_under(
                "money", "hous", "rent", "home", "flat", name="filed under Money / Housing"
            ),
        ),
    ),
    Case(
        "E10",
        "extract",
        "a new housing fact reuses the existing topic Housing instead of coining a synonym",
        facts=(live(E10_HOUSING, "Rents a two-bedroom flat in Tampines", "money", "Housing"),),
        turns=_talk(
            "My landlord, Mr Lim, has agreed that I can pay the rent on the 3rd instead of the 1st.",
            "Good, I'll remember the new due date.",
        ),
        checks=(
            c.reused_topic(
                "money", "Housing", "lands in the existing topic", or_updated=(E10_HOUSING, "3rd")
            ),
            c.topics_in_section("money", {"housing"}, "coins no new Money topic"),
        ),
    ),
    Case(
        "E11",
        "extract",
        "a restated preference adds no duplicate and marks the existing fact as mentioned",
        facts=(
            live(
                E11_PREFERENCE,
                "Prefers short answers on reconciliation",
                "you",
                "Preferences",
                mentioned_age=40,
            ),
        ),
        turns=_talk("Like I said before, keep the reconciliation answers short.", "Will do."),
        checks=(c.no_new_facts("adds no duplicate"), c.existing_mentioned(E11_PREFERENCE)),
    ),
    Case(
        "E12",
        "extract",
        "a contradiction updates the old fact in place instead of adding a second",
        facts=(
            live(
                E12_RENT, "Pays 2,800 SGD a month in rent for the Tampines flat", "money", "Housing"
            ),
        ),
        turns=_talk(
            "We moved last month, so my rent is now 3,200 SGD a month.",
            "Thanks, I'll use the new figure.",
        ),
        checks=(
            c.updated_in_place(
                E12_RENT,
                now=("3,200", "3200"),
                gone=("2,800", "2800"),
                name="the old fact now says 3,200",
            ),
            c.no_new_facts("adds no second fact"),
        ),
    ),
    Case(
        "E13",
        "extract",
        "a full card number, a passport number and a password are never stored",
        turns=_talk(
            "For the travel booking my Visa is 4111 1111 1111 1111, my passport is X1234567 and "
            "the portal password is Hunter2!x. Please remember them for next time.",
            "I can't keep those, but you can store them in a password manager.",
        ),
        checks=(
            c.never_stored_digits("4111111111111111", name="no card number stored"),
            c.never_stored("x1234567", name="no passport number stored"),
            c.never_stored("hunter2", name="no password stored"),
        ),
    ),
    Case(
        "E13b",
        "extract",
        "the last four digits of a card ('card ending 4821') are fine to keep",
        turns=_talk(
            "I put groceries on my card ending 4821 and keep the other one for travel.",
            "Understood.",
        ),
        checks=(c.mentions("4821", name="keeps the last four digits"),),
    ),
    Case(
        "E14",
        "extract",
        "a health condition is not stored while sensitive topics are off",
        turns=_talk(
            "I was diagnosed with type 2 diabetes last year, so I now budget for monthly "
            "medication.",
            "Thanks for telling me.",
        ),
        checks=(
            c.never_stored(
                r"diabet",
                r"medic",
                r"diagnos",
                r"health",
                name="no health fact stored while sensitive is off",
            ),
        ),
    ),
    Case(
        "E15",
        "extract",
        "the same health fact is stored once sensitive topics are on",
        sensitive_topics=True,
        turns=_talk(
            "I was diagnosed with type 2 diabetes last year, so I now budget for monthly "
            "medication.",
            "Thanks for telling me.",
        ),
        checks=(c.mentions("diabet", "medic", name="stores the health fact"),),
    ),
    Case(
        "E16",
        "extract",
        "small talk stores nothing",
        turns=(
            ("user", "Morning! Lovely weather today, isn't it?"),
            ("assistant", "It is! How can I help?"),
            ("user", "Nothing really, just saying hi. Thanks for yesterday's help."),
        ),
        checks=(c.no_new_facts("stores nothing for small talk"),),
    ),
    Case(
        "E17",
        "extract",
        "what the Instructions already say is not stored again",
        notes="Always answer in SGD.",
        turns=_talk("Remember, I want everything in SGD.", "Will do."),
        checks=(c.no_new_facts("does not repeat the Instructions"),),
    ),
    Case(
        "E18",
        "extract",
        "a fact the user told Lucius to forget is not learned again when it comes up",
        forgotten=(E18_FORGOTTEN,),
        turns=_talk(
            "Sorry for the slow reply, I have a dog named Biscuit and he needed a walk. Can we "
            "look at this month's grocery spending?",
            "No problem. Let's look at groceries.",
        ),
        checks=(
            c.never_relearned(E18_FORGOTTEN, name="the forgotten fact is not learned again"),
            c.never_stored(
                r"biscuit",
                name="nor is a reworded version of it (the accepted paraphrase gap)",
                severity=QUALITY,
            ),
        ),
    ),
)

__all__ = ["EXTRACT_CASES"]
