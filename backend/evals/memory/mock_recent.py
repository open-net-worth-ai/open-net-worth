"""Canned replies for the Recent cases (``cases_recent``): ``good`` follows the rules; ``sloppy``
falls back on the usual split and leaves a fulfilled entry open, or closes one nothing fulfilled."""

from __future__ import annotations

from typing import Callable

_ENTRY = (
    "When Priya's PayNow for November arrives (S$537.40): book Rent S$500, Utilities S$15, "
    "Phone & Internet S$10, Groceries S$12.40."
)
_QUOTE = "Send Priya the November request: rent S$500 as usual, utilities only S$15"
_EMPTY = {"new": [], "updates": [], "remove": [], "mentioned": []}

EXTRACT_RECENT: dict[str, tuple[dict, dict]] = {
    "E24": (
        {**_EMPTY, "new": [{"content": _ENTRY, "section": "open_items", "quote": _QUOTE}]},
        {
            **_EMPTY,
            "new": [
                {
                    "content": "User sent Priya the November request",
                    "section": "open_items",
                    "quote": _QUOTE,
                }
            ],
        },
    ),
    "E25": (
        {
            **_EMPTY,
            "updates": [
                {
                    "id": "mem_e25a01",
                    "content": "When Priya pays her S$12.40 half of the S$24.80 FairPrice run: "
                    "book it to Groceries.",
                    "section": "open_items",
                }
            ],
        },
        {**_EMPTY, "mentioned": ["mem_e25a01"]},
    ),
    "E26": (
        {
            **_EMPTY,
            "new": [
                {
                    "content": "When Priya's PayNow for this month arrives (S$525): book Rent "
                    "S$500, Utilities S$15, Phone & Internet S$10.",
                    "section": "open_items",
                    # The model tidies the person's typo when it copies them; the guard allows it.
                    "quote": "yes ok, utilities only S$15 for her this month",
                }
            ],
        },
        _EMPTY,
    ),
    "E27": (
        _EMPTY,
        {
            **_EMPTY,
            "new": [
                {
                    "content": "When Priya's PayNow comes: book Rent S$500, Utilities S$40.",
                    "section": "open_items",
                    "quote": "Priya's paying late this month I think.",
                }
            ],
        },
    ),
}

# What each use case's run says it booked: (good, sloppy), and whether that run closes the entry.
_BOOKED: dict[str, tuple[tuple[str, bool], tuple[str, bool]]] = {
    "U03": (
        (
            "Booked S$537.40 from Priya as agreed: Rent S$500, Utilities S$15, Phone & Internet "
            "S$10, Groceries S$12.40.",
            True,
        ),
        (
            "Booked S$537.40 from Priya: Rent S$500, Utilities S$27.40, Phone & Internet S$10.",
            False,
        ),
    ),
    "U04": (
        (
            "Booked S$540 from Priya as agreed: Rent S$500, Utilities S$15, Phone & Internet S$10, "
            "Groceries S$12.40, and S$2.60 over the agreed S$537.40 on a line of its own.",
            True,
        ),
        ("Booked S$540 from Priya: Rent S$500, Utilities S$30, Phone & Internet S$10.", False),
    ),
    "U05": (
        ("Booked S$200 from Priya as a part payment toward rent.", False),
        ("Booked S$200 from Priya as her November payment.", True),
    ),
    "U06": (
        ("Booked S$80 received from Daniel Tan.", False),
        ("Booked S$80 from Daniel Tan.", True),
    ),
}

RECENT_USE_IDS = tuple(_BOOKED)


def recent_script(
    case_id: str, good: bool, handle: str, round_: Callable[..., list[dict]]
) -> list[list[dict]]:
    """A use case's rounds: book as ``_BOOKED`` says, closing the entry when that run does."""
    text, closes = _BOOKED[case_id][0 if good else 1]
    # The first call loads the tool (the discovery gate bounces it); the retry runs it.
    close = round_(tool="close_recent_items", args={"handles": [handle]})
    return [close, close, round_(text)] if closes else [round_(text)]


__all__ = ["EXTRACT_RECENT", "RECENT_USE_IDS", "recent_script"]
