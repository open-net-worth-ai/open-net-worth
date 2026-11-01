"""Canned replies for ``cases_waiting``: ``good`` keeps each step the person took and nothing
else; ``sloppy`` does what the extractor did before amendment 13 — drops the step as "waiting",
or keeps Lucius's offer and the step his plan already shows."""

from __future__ import annotations

_EMPTY = {"new": [], "updates": [], "remove": [], "mentioned": []}


def _recent(content: str, quote: str) -> dict:
    return {
        **_EMPTY,
        "new": [{"content": content, "section": "open_items", "topic": None, "quote": quote}],
    }


EXTRACT_WAITING: dict[str, tuple[dict, dict]] = {
    "E32": (
        _recent(
            "Disputed the S$84.00 QuickShip card charge with Acme Bank by phone; awaiting their "
            "answer, due within 10 working days.",
            "I called Acme Bank this morning to dispute the S$84.00 QuickShip charge",
        ),
        _EMPTY,
    ),
    "E33": (
        _recent(
            "Asked Northwind by email to waive the S$150 card annual fee; no reply yet.",
            "I've emailed Northwind asking them to waive the S$150 annual fee",
        ),
        _EMPTY,
    ),
    "E34": (
        _recent(
            "Submitted a S$2,400 course-fee tax relief claim; the tax office decides in 4 to 6 "
            "weeks.",
            "Submitted my course-fee tax relief claim for S$2,400 today",
        ),
        _recent(
            "Submitted a S$2,400 tax relief claim, which lowers your next assessment.",
            "Submitted my course-fee tax relief claim for S$2,400 today",
        ),
    ),
    "E35": (
        _EMPTY,
        _recent("Lucius offered to draft a fee-waiver email to Acme.", "not now"),
    ),
    "E36": (
        _EMPTY,
        _recent(
            "Applied for the Northwind loan; awaiting their answer.",
            "I applied for the Northwind loan today",
        ),
    ),
}
