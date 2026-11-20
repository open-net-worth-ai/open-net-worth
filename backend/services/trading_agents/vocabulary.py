"""TradingAgents rates a STOCK; Lucius's take recommends an ACTION on a position. This table is the
bridge — pure, so the take's prompt, its checker and the asset page read one definition.

A rating is the research view of the ticker (Buy / Overweight / Hold / Underweight / Sell). What it
means for the user depends on whether they hold it: a Sell on a stock they do not own is "not a buy
now", never an instruction to sell. ``allowed_actions`` is what the take may recommend for a rating
without an overlay reason (``OVERLAY_REASONS``: concentration, thesis, levels, alerts — Lucius's
own position knowledge — and the two facts about the run itself).
"""

from __future__ import annotations

# (held, rating) → the verdict actions that follow the research, most natural first.
_ALLOWED: dict[bool, dict[str, tuple[str, ...]]] = {
    True: {
        "Buy": ("add", "hold"),
        "Overweight": ("add", "hold"),
        "Hold": ("hold",),
        "Underweight": ("trim", "hold"),
        "Sell": ("sell", "trim"),
    },
    False: {
        "Buy": ("buy", "wait"),
        "Overweight": ("buy", "wait"),
        "Hold": ("wait",),
        "Underweight": ("wait",),
        "Sell": ("wait",),
    },
}

_VIEW: dict[bool, dict[str, str]] = {
    True: {
        "Buy": "research is positive — adding is supported",
        "Overweight": "research leans positive — adding is supported",
        "Hold": "research says keep the position as it is",
        "Underweight": "research leans negative — reducing is supported",
        "Sell": "research says exit or reduce",
    },
    False: {
        "Buy": "positive outlook — a buy is supported",
        "Overweight": "leans positive — a buy is supported",
        "Hold": "neutral outlook — no reason to buy now",
        "Underweight": "leans negative — not a buy now",
        "Sell": "negative outlook — not a buy now",
    },
}

# From most bearish to most bullish — "the nearest allowed action" is measured along it.
_SCALE = {
    True: ("sell", "trim", "hold", "add"),
    False: ("wait", "buy"),
}

OVERLAY_REASONS = (
    "concentration",
    "thesis",
    "levels",
    "alerts",
    "stale_run",
    "position_unseen",
)
NO_OVERLAY = "none"


def allowed_actions(signal: str, held: bool) -> tuple[str, ...]:
    """The verdict actions the research supports for ``signal``; ``()`` for an unknown rating."""
    return _ALLOWED[bool(held)].get(signal, ())


def view(signal: str, held: bool) -> str:
    """One plain line saying what ``signal`` means for this user; ``""`` for an unknown rating."""
    return _VIEW[bool(held)].get(signal, "")


def nearest_allowed(action: str, signal: str, held: bool) -> str:
    """The allowed action closest to ``action`` on the bearish-to-bullish scale, the first listed
    winning a tie. ``action`` itself when the rating is unknown."""
    allowed = allowed_actions(signal, held)
    scale = _SCALE[bool(held)]
    if not allowed or action not in scale:
        return action
    return min(allowed, key=lambda a: abs(scale.index(a) - scale.index(action)))
