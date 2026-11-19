"""Mutual funds priced once a day. Orders placed before the fund's cut-off (16:00 New York by
default) fill at that day's NAV, published after the close — forward pricing; later orders, and
orders on a non-trading day, fill at the next day's. There is no order type: a fund order is
always at the next NAV."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.services.paper_trading.kinds.base import BaseKind
from backend.services.paper_trading.model import (
    Decision,
    Fill,
    PaperAsset,
    PaperQuote,
    Session,
    Wait,
)

NAV_TZ = "America/New_York"
NAV_CUTOFF = time(16, 0)


def nav_date_for(placed_at: datetime, cutoff: time = NAV_CUTOFF, tz: str = NAV_TZ) -> date:
    """The NAV date an order placed at ``placed_at`` gets: today if a weekday before the
    cut-off, else the next weekday."""
    local = placed_at.astimezone(ZoneInfo(tz))
    day = local.date()
    if local.weekday() >= 5 or local.time() >= cutoff:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


class FundNavKind(BaseKind):
    key = "fund_nav"
    basis = "nav"

    def session(self, asset: PaperAsset, quote: PaperQuote | None, now: datetime) -> Session:
        local = now.astimezone(ZoneInfo(NAV_TZ))
        open_ = local.weekday() < 5 and local.time() < NAV_CUTOFF
        return Session(is_open=open_, state="NAV", timezone=NAV_TZ, always_open=True)

    def decide(
        self,
        order: dict,
        quote: PaperQuote | None,
        session: Session,
        settings: dict,
        now: datetime,
    ) -> Decision:
        due = order.get("nav_date") or nav_date_for(now).isoformat()
        navs = (quote.navs if quote else {}) or {}
        ready = sorted(d for d in navs if d >= due)
        if not ready:
            when = date.fromisoformat(due)
            day = (
                "tonight's"
                if when == now.astimezone(ZoneInfo(NAV_TZ)).date()
                else (f"{when:%a %d %b}'s")
            )
            return Wait(f"Fills at {day} NAV, published after 16:00 New York.")
        nav_day = ready[0]
        return Fill(
            price=float(navs[nav_day]),
            basis="nav",
            source=quote.source if quote else "nav",
            quote_as_of=quote.as_of if quote else None,
            session="NAV",
            nav_date=nav_day,
        )

    def value(self, quantity: float, quote: PaperQuote | None) -> float | None:
        if quote is None:
            return None
        if quote.navs:
            return float(quantity) * float(quote.navs[max(quote.navs)])
        return super().value(quantity, quote)
