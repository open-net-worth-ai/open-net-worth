"""Physical precious metals by the gram, ounce or tael. Priced from spot (converted to the
asset's unit); trades in spot hours (Sunday 18:00 to Friday 17:00 New York); a buy pays the
dealer premium and a sell gets spot less the dealer discount."""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading.kinds.base import BaseKind, weekly_window
from backend.services.paper_trading.model import PaperAsset, PaperQuote, Session

SPOT_TZ = "America/New_York"


class MetalKind(BaseKind):
    key = "metal"
    basis = "quote"

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        is_open, nxt = weekly_window(now, SPOT_TZ, 6, (18, 0), 4, (17, 0))
        return Session(
            is_open=is_open, state="OPEN" if is_open else "CLOSED", next_open=nxt, timezone=SPOT_TZ
        )

    def market_price(self, side: str, quote: PaperQuote, settings: dict):
        spot = float(quote.last or 0.0)
        if side == "buy":
            prem = spot * float(settings.get("metal_premium_buy_pct") or 0.0) / 100.0
            return spot + prem, 0.0, prem
        disc = spot * float(settings.get("metal_discount_sell_pct") or 0.0) / 100.0
        return spot - disc, 0.0, disc
