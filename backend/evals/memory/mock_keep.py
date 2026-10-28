"""Canned replies for the keep-test cases (``cases_keep``): ``good`` follows the rules, ``sloppy``
keeps what the old memory used to keep."""

from __future__ import annotations

NONE = {"new": [], "updates": [], "remove": [], "mentioned": []}


def _new(content: str, section: str, topic: str | None = None, **extra) -> dict:
    return {**NONE, "new": [{"content": content, "section": section, "topic": topic, **extra}]}


EXTRACT_KEEP: dict[str, tuple[dict, dict]] = {
    "E19": (
        NONE,
        _new(
            "User does not expect retry loops on the broker login blocker; will paste statements",
            "you",
            "Preferences",
        ),
    ),
    "E20": (
        NONE,
        _new(
            "As of today: Maybank Lotus Bistro line likely matches the 28 Sep entry; "
            "advisor offered to link them, awaiting the user",
            "open_items",
            quote="Hmm, not sure.",
        ),
    ),
    "E21": (
        _new(
            "Call the insurer about the renewal",
            "open_items",
            quote="I'll call the insurer tomorrow about the renewal",
        ),
        _new("User will call the insurer about the renewal", "plans", "Insurance"),
    ),
    "E22": (
        NONE,
        _new(
            "Decide on price alerts ~15% below current prices for RIVN and SOFI",
            "open_items",
            quote="Maybe, I'll think about it.",
        ),
    ),
    "E23": (
        _new(
            "Splits shared bills with the flatmate item by item, each share shown, not lump sums",
            "money",
            "Housing",
        ),
        _new(
            "User wants flatmate requests itemised, like the FairPrice one on 12 Sep",
            "you",
            "Preferences",
        ),
    ),
}


def sort_s03(persona_good: bool) -> dict:
    if persona_good:
        return {
            "facts": [
                {
                    "id": "mem_s03a01",
                    "section": "you",
                    "topic": "Preferences",
                    "content": "Prefers concise answers; lead with the headline answer.",
                },
                {
                    "id": "mem_s03a02",
                    "section": "money",
                    "topic": "Housing",
                    "content": "Flatmate Priya pays S$540 a month by PayNow near month-end.",
                },
                {"id": "mem_s03a03", "retire": "ledger"},
                {"id": "mem_s03a04", "retire": "stale"},
                {"id": "mem_s03a05", "retire": "stale"},
                {"id": "mem_s03a06", "retire": "stale"},
            ]
        }
    return {
        "facts": [
            {"id": "mem_s03a01", "section": "you", "topic": "Preferences"},
            {"id": "mem_s03a02", "section": "money", "topic": "Housing"},
            {"id": "mem_s03a03", "section": "open_items"},
            {"id": "mem_s03a04", "section": "open_items"},
            {"id": "mem_s03a05", "section": "you", "topic": "Preferences"},
            {"id": "mem_s03a06", "section": "you", "topic": "Preferences"},
        ]
    }


__all__ = ["EXTRACT_KEEP", "sort_s03"]
