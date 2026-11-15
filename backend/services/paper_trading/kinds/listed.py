"""Stocks, ETFs, ETPs, REITs and ADRs on any exchange the app prices — and commodity funds,
which trade exactly like them. Priced from the live quote (bid/ask when Yahoo has them); trades
in its exchange's regular session, pre/post-market only when the portfolio allows it.
"""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading.kinds.base import BaseKind
from backend.services.paper_trading.model import PaperAsset, PaperQuote, Session


class ListedKind(BaseKind):
    key = "listed"
    basis = "quote"

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        if quote is None:
            return Session(is_open=False, state="UNKNOWN")
        extended_ok = bool(asset.extra.get("extended_ok"))
        state = (quote.session_state or "UNKNOWN").upper()
        is_open = state == "REGULAR" or (extended_ok and state in ("PRE", "POST") and quote.is_open)
        return Session(
            is_open=is_open, state=state, next_open=quote.next_open, timezone=quote.timezone
        )
