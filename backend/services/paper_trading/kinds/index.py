"""Indices as synthetic trackers (S&P 500, Nikkei 225): the index level itself, with no fee,
in the index's session. Labelled Synthetic everywhere: no fund tracks it exactly, and a price
index pays no dividends."""

from __future__ import annotations

from backend.services.paper_trading.kinds.listed import ListedKind
from backend.services.paper_trading.model import PaperQuote


class IndexKind(ListedKind):
    key = "index"
    basis = "synthetic"

    def market_price(self, side: str, quote: PaperQuote, settings: dict):
        return float(quote.last or 0.0), 0.0, 0.0
