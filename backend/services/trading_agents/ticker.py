"""The ticker of a TradingAgents run: its one accepted spelling, and the instrument ONW knows it as."""

from __future__ import annotations

import re
from typing import Optional

from backend.core.errors import ValidationError
from backend.db.instrument_store import InstrumentStore

_TICKER = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")


def normalize(ticker: object) -> str:
    """Upper-cased and checked. Anything else is refused: the ticker is the one free-text value a
    run sends to its data vendors, and it becomes a file name in the run's scratch folder — so it
    holds at least one letter or digit, which also rules out ``.`` and ``..``."""
    symbol = str(ticker or "").strip().upper()
    if not _TICKER.match(symbol) or not any(ch.isalnum() for ch in symbol):
        raise ValidationError(
            "A ticker is 1 to 15 letters, digits or . - ^ = (for example ACME or ACME.L).",
            field="ticker",
        )
    return symbol


def instrument(ticker: str) -> Optional[dict]:
    """The instrument ONW holds for this symbol, or ``None`` when it knows none."""
    return InstrumentStore().find_by_symbol(ticker)
