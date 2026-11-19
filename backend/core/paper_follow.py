"""Tickers held on paper, as NAMES only — the seam Intel follows them through.

A ticker held in a paper portfolio is followed the way a watched ticker is: its news polled at
the same cadence, stored and filtered the same way, shown on its asset page. Only the NAME
crosses: never a paper quantity, value or result (ADR 2026-11-15-paper-trading-is-a-simulated-
book-never-an-order-path, amended 2026-11-18). Paper trading registers the provider at boot
(``services.paper_trading.follow.register``), so ``monitoring`` reads it without importing paper.

A row is ``{instrument_id, price_source_id, label, asset_class}``. No provider registered, or a
provider that fails, is ``[]``: following paper tickers must never sink an Intel poll.

A ``core`` leaf: imports nothing internal."""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

Provider = Callable[[], list[dict]]
_provider: Optional[Provider] = None


def register_provider(fn: Optional[Provider]) -> None:
    """Install (or clear, with None) the provider of tickers held on paper."""
    global _provider
    _provider = fn


def followed() -> list[dict]:
    """The tickers held on paper right now, or [] when there is no provider or it failed."""
    if _provider is None:
        return []
    try:
        return list(_provider() or [])
    except Exception:  # noqa: BLE001 — never sink the caller's poll
        logger.exception("paper_follow: provider failed")
        return []
