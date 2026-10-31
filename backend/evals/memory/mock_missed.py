"""Canned replies for the missed-shape cases (``cases_missed``): ``good`` catches each shape;
``sloppy`` does what the extractor did before amendment 12 — keeps nothing, or keeps Lucius's
gloss, or adds a second fact beside the one it should have updated."""

from __future__ import annotations

_EMPTY = {"new": [], "updates": [], "remove": [], "mentioned": []}


def _new(content: str, section: str, topic: str | None = None, quote: str | None = None) -> dict:
    return {
        **_EMPTY,
        "new": [{"content": content, "section": section, "topic": topic, "quote": quote}],
    }


EXTRACT_MISSED: dict[str, tuple[dict, dict]] = {
    "E28": (
        _new(
            "Applied for the Northwind loan (S$40,000, 24 months) for the renovation; awaiting a "
            "decision. 2% early-repayment fee, so treat it as full-term.",
            "open_items",
            quote="Ok, I applied with Northwind just now",
        ),
        _new(
            "Applied for the Northwind loan, which keeps the renovation covered.",
            "open_items",
            quote="Ok, I applied with Northwind just now",
        ),
    ),
    "E29": (
        _new(
            "Going with Northwind (S$40,000) for the renovation and applying this week; turned "
            "down Acme's S$10,000 offer.",
            "open_items",
            quote="Northwind it is. I'll apply with them this week",
        ),
        _EMPTY,
    ),
    "E30": (
        _new("Books tissues and dish soap under Groceries.", "money", "Bookkeeping"),
        _EMPTY,
    ),
    "E31": (
        {
            **_EMPTY,
            "updates": [
                {
                    "id": "mem_e31a01",
                    "content": "Flatmate Priya pays by PayNow each month: rent S$500, internet "
                    "S$10, utilities a flat S$30.",
                    "section": "money",
                    "topic": "Housing",
                }
            ],
        },
        _new("Priya's utilities share is a flat S$30.", "money", "Housing"),
    ),
}
