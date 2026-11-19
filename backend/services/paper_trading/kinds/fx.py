"""Currencies: hold another currency as cash. A conversion fills at the live rate moved against
you by the FX spread setting, any time from Sunday 17:00 to Friday 17:00 New York. The quote's
``last`` is the price of one unit of the currency bought, in the currency paid."""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading.kinds.base import BaseKind, weekly_window
from backend.services.paper_trading.model import PaperAsset, PaperQuote, Session

FX_TZ = "America/New_York"


def fx_session(now: datetime) -> Session:
    is_open, nxt = weekly_window(now, FX_TZ, 6, (17, 0), 4, (17, 0))
    return Session(
        is_open=is_open, state="OPEN" if is_open else "CLOSED", next_open=nxt, timezone=FX_TZ
    )


class FxKind(BaseKind):
    key = "fx"
    basis = "quote"

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        return fx_session(now)

    def market_price(self, side: str, quote: PaperQuote, settings: dict):
        last = float(quote.last or 0.0)
        spread = last * float(settings.get("fx_spread_pct") or 0.0) / 100.0
        return (last + spread, 0.0, spread) if side == "buy" else (last - spread, 0.0, spread)

    def decide_priced(self, order, quote, settings, session):
        # A conversion is always at the market: no limit or stop.
        return super().decide_priced({**order, "order_type": "market"}, quote, settings, session)
