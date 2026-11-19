"""Private and manual-priced assets (pre-IPO shares, a flat, art, an unlisted fund). Traded at
the price you enter, any time; valued at your newest mark, or with a proxy, at that mark moved
by the proxy's return since. Labelled Your price everywhere."""

from __future__ import annotations

from datetime import datetime

from backend.services.paper_trading.kinds.base import BaseKind
from backend.services.paper_trading.model import (
    Decision,
    Fill,
    PaperAsset,
    PaperQuote,
    Reject,
    Session,
)


def proxied_price(mark: float, proxy_at_mark: float | None, proxy_now: float | None) -> float:
    """The mark moved by the proxy's return since the mark; the mark alone without a proxy."""
    if mark and proxy_at_mark and proxy_now and proxy_at_mark > 0:
        return mark * (proxy_now / proxy_at_mark)
    return mark


class ManualKind(BaseKind):
    key = "manual"
    basis = "yours"

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        return Session(is_open=True, state="OPEN", always_open=True)

    def decide(
        self,
        order: dict,
        quote: PaperQuote | None,
        session: Session,
        settings: dict,
        now: datetime,
    ) -> Decision:
        price = order.get("price")
        if price is None and quote is not None and quote.last:
            price = quote.last
        if price is None or float(price) <= 0:
            return Reject("Enter the price you are trading at.")
        return Fill(
            price=float(price),
            basis="yours",
            source="your price",
            quote_as_of=now,
            session="OPEN",
        )

    def next_line(self, decision: Decision) -> str:
        if isinstance(decision, Fill):
            return f"Fills now at your price, {decision.price:,.2f}."
        return super().next_line(decision)
