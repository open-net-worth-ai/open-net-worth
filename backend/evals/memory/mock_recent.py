"""Canned replies for the Recent cases (``cases_recent``): ``good`` follows the rules, ``sloppy``
falls back on the usual split and leaves the entry open."""

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
}


def recent_script(good: bool, handle: str, round_: Callable[..., list[dict]]) -> list[list[dict]]:
    """U03: the good run closes the entry and books the agreed split; the sloppy one books the
    usual split and leaves the entry open."""
    if good:
        # The first call loads the tool (the discovery gate bounces it); the retry runs it.
        return [
            round_(tool="close_recent_items", args={"handles": [handle]}),
            round_(tool="close_recent_items", args={"handles": [handle]}),
            round_(
                "Booked S$537.40 from Priya as agreed: Rent S$500, Utilities S$15, Phone & "
                "Internet S$10, Groceries S$12.40."
            ),
        ]
    return [
        round_("Booked S$537.40 from Priya: Rent S$500, Utilities S$27.40, Phone & Internet S$10.")
    ]


__all__ = ["EXTRACT_RECENT", "recent_script"]
