"""What every paper asset kind shares: the market-order price, limit and stop triggers, the
freshness rule and the one line that says what happens next. Pure — a kind adapter supplies
its own session and execution price, and these rules apply them the same way for every kind.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.services.paper_trading.model import (
    FRESH_SECONDS,
    Decision,
    Fill,
    PaperAsset,
    PaperQuote,
    Reject,
    Session,
    Wait,
)


def city(tz: str) -> str:
    """'America/New_York' → 'New York' — how the ticket names an exchange's clock."""
    return (tz.rsplit("/", 1)[-1] if tz else "").replace("_", " ")


def fmt_when(when: datetime | None, tz: str) -> str:
    """'Mon 09:30 New York', in the exchange's own zone."""
    if when is None:
        return "the next open"
    try:
        local = when.astimezone(ZoneInfo(tz)) if tz else when
    except Exception:  # an unknown zone name reads in UTC rather than failing the ticket
        local = when
    return f"{local:%a %H:%M} {city(tz)}".strip()


def fmt_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.2f}"
    return f"{price:.6g}"


def quote_age(quote: PaperQuote | None, now: datetime) -> float | None:
    if quote is None or quote.as_of is None:
        return None
    return max(0.0, (now - quote.as_of).total_seconds())


def is_stale(quote: PaperQuote | None, now: datetime) -> bool:
    age = quote_age(quote, now)
    return age is None or age > FRESH_SECONDS


def slip(settings: dict) -> float:
    return float(settings.get("slippage_bps") or 0.0) / 10_000.0


class BaseKind:
    """One asset kind. A subclass sets ``key``/``phase``/``basis`` and overrides ``session`` and
    ``market_price``; ``decide`` then applies the shared order rules."""

    key = "base"
    phase = 1
    basis = "quote"
    quantity_decimals = 6

    def owns(self, asset: PaperAsset) -> bool:
        return asset.kind == self.key

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        if quote is None:
            return Session(is_open=False, state="UNKNOWN")
        return Session(
            is_open=quote.is_open,
            state=quote.session_state,
            next_open=quote.next_open,
            timezone=quote.timezone,
        )

    def market_price(
        self, side: str, quote: PaperQuote, settings: dict
    ) -> tuple[float, float, float]:
        """(execution price, slippage per unit, spread or premium per unit) for a market order.
        Default: the ask for a buy and the bid for a sell when the quote has them, else the last
        price moved against you by the slippage setting."""
        last = float(quote.last or 0.0)
        if side == "buy" and quote.ask:
            return float(quote.ask), 0.0, float(quote.ask) - last
        if side == "sell" and quote.bid:
            return float(quote.bid), 0.0, last - float(quote.bid)
        s = last * slip(settings)
        return (last + s, s, 0.0) if side == "buy" else (last - s, s, 0.0)

    def value(self, quantity: float, quote: PaperQuote | None) -> float | None:
        """A holding's value in the quote's currency; None without a price."""
        if quote is None or quote.last is None:
            return None
        return float(quantity) * float(quote.last)

    # ── the shared order rules ────────────────────────────────────────────────────────────

    def closed_reason(self, session: Session) -> str:
        return (
            f"Market closed. Fills at the next open ({fmt_when(session.next_open, session.timezone)}),"
            " at the first price we see."
        )

    def decide(
        self,
        order: dict,
        quote: PaperQuote | None,
        session: Session,
        settings: dict,
        now: datetime,
    ) -> Decision:
        if not session.is_open:
            return Wait(self.closed_reason(session))
        if quote is None or not quote.last:
            return Wait("No price for it yet. It fills once one arrives.")
        if is_stale(quote, now):
            seen = quote.as_of.strftime("%H:%M") if quote.as_of else "the open"
            return Wait(f"No fresh price since {seen}. It fills once one arrives.")
        return self.decide_priced(order, quote, settings, session)

    def decide_priced(
        self, order: dict, quote: PaperQuote, settings: dict, session: Session
    ) -> Decision:
        side = order["side"]
        last = float(quote.last or 0.0)
        price, slippage, spread = self.market_price(side, quote, settings)
        otype = order.get("order_type") or "market"
        if otype == "limit":
            limit = float(order["limit_price"])
            if (side == "buy" and last > limit) or (side == "sell" and last < limit):
                return Wait(
                    f"Waits until the price reaches {fmt_price(limit)}. You can cancel it in Orders."
                )
            # At the limit, or better only when the price gapped through it.
            price = min(price, limit) if side == "buy" else max(price, limit)
        elif otype == "stop":
            stop = float(order["stop_price"])
            if (side == "buy" and last < stop) or (side == "sell" and last > stop):
                return Wait(
                    f"Becomes a market order when the price reaches {fmt_price(stop)}."
                    " You can cancel it in Orders."
                )
        return Fill(
            price=round(price, 8),
            basis=self.basis,
            source=quote.source,
            quote_as_of=quote.as_of,
            session=session.state,
            slippage=round(slippage, 8),
            spread_or_premium=round(spread, 8),
        )

    def next_line(self, decision: Decision) -> str:
        if isinstance(decision, Fill):
            return f"Fills now at about {fmt_price(decision.price)}."
        if isinstance(decision, (Wait, Reject)):
            return decision.reason
        return ""

    def day_expiry(self, session: Session, now: datetime) -> datetime:
        """When a day order lapses: the end of its session day in the exchange's zone (the day
        it was placed if the market is open, else the day of the next open)."""
        tz = ZoneInfo(session.timezone) if session.timezone else None
        anchor = now if session.is_open or session.next_open is None else session.next_open
        local = anchor.astimezone(tz) if tz else anchor
        end = local.replace(hour=23, minute=59, second=59, microsecond=0)
        return end.astimezone(now.tzinfo) if now.tzinfo else end


def weekly_window(
    now: datetime,
    tz: str,
    open_dow: int,
    open_hm: tuple[int, int],
    close_dow: int,
    close_hm: tuple[int, int],
) -> tuple[bool, datetime]:
    """A market open from (open_dow, open_hm) to (close_dow, close_hm) each week in ``tz``
    (Mon=0). Returns (is_open, next_open as UTC-aware datetime)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    week_start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    opens = week_start + timedelta(days=open_dow, hours=open_hm[0], minutes=open_hm[1])
    closes = week_start + timedelta(days=close_dow, hours=close_hm[0], minutes=close_hm[1])
    if closes <= opens:  # a window opening on Sunday closes the following Friday
        closes += timedelta(days=7)
    week = timedelta(days=7)
    for o, c in ((opens - week, closes - week), (opens, closes)):
        if o <= local < c:
            return True, o.astimezone(now.tzinfo)
    nxt = min(o for o in (opens - week, opens, opens + week) if o > local)
    return False, nxt.astimezone(now.tzinfo)


__all__ = [
    "BaseKind",
    "Decision",
    "Fill",
    "Reject",
    "Wait",
    "Session",
    "city",
    "fmt_price",
    "fmt_when",
    "is_stale",
    "quote_age",
    "weekly_window",
]
