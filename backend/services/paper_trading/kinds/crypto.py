"""Crypto, stablecoins included. Priced from the live quote; the market never closes; a market
order fills at the last price moved against you by the slippage setting, to 8 decimals."""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading.kinds.base import BaseKind, slip
from backend.services.paper_trading.model import PaperAsset, PaperQuote, Session


class CryptoKind(BaseKind):
    key = "crypto"
    basis = "quote"
    quantity_decimals = 8

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        return Session(is_open=True, state="OPEN", always_open=True, timezone="UTC")

    def market_price(self, side: str, quote: PaperQuote, settings: dict):
        last = float(quote.last or 0.0)
        s = last * slip(settings)
        return (last + s, s, 0.0) if side == "buy" else (last - s, s, 0.0)

    def decide(self, order, quote, session, settings, now):
        # Around the clock: a crypto quote is never held back as stale while one exists.
        return super().decide(order, quote, session, settings, now)
