"""Price alerts and assistant memory for the demo.

Two more surfaces that read as empty features unless something is in them. Both are modelled
on what a real instance accumulates:

* **Alerts** cluster around a holding — a stop-loss, an entry level, a take-profit — rather
  than one lonely threshold, because that is how somebody actually watches a position.
* **Memory** is what the assistant has been told and should not have to be told again. It is
  the difference between an assistant that knows the book and one that greets you as a
  stranger every morning.

Everything here is fabricated and consistent with the rest of Alex's book.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# (symbol, op, multiple-of-today's-price, note).
#
# RELATIVE, not absolute. An alert written as "NVDA >= 260" is only sensible against the price
# it was written for; the moment a ticker's real price is somewhere else, every level is either
# already triggered or absurdly far away. Multiples keep the whole set meaningful whatever the
# market is doing on the day the demo is built.
ALERTS: list[tuple[str, str, float, str]] = [
    ("VOO", ">=", 1.09, "Trim back toward the 65% equity target if it gets here."),
    ("VOO", "<=", 0.90, "A 10% drawdown — top up rather than panic."),
    ("NVDA", ">=", 1.22, "Take-profit zone. Position is already oversized."),
    ("NVDA", "<=", 0.78, "Stop thinking about it below here; the thesis was momentum."),
    ("AAPL", ">=", 1.15, "Trim to fund the Lakeside instalment."),
    ("AAPL", "<=", 0.88, "Long-term entry zone."),
    ("NET", ">=", 1.20, "Vested RSUs worth selling into strength."),
    ("NET", "<=", 0.75, "Concentration risk — employer AND shareholder."),
    ("BTC", ">=", 1.30, "Rebalance crypto back under 8% of the book."),
    ("BTC", "<=", 0.65, "Halve the DCA rather than stop it."),
    ("SCHD", "<=", 0.92, "Yield above 4.5% here — add."),
    ("GLDM", ">=", 1.14, "The hedge has done its job."),
]

# What the assistant has been told about this household.
MEMORY: list[str] = [
    "Alex is married to Sam; the Joint Household account covers shared bills and neither of "
    "us treats it as personal spending.",
    "Target retirement is 52, on roughly $74k a year in today's money.",
    "The emergency fund is six months of core spending and is not to be suggested as a source "
    "for anything except a genuine emergency or the kitchen renovation.",
    "Do not suggest selling RSU shares in the first 30 days after a vest — we hold for the "
    "long-term treatment.",
    "Concentration matters here: Alex both works at Cloudflare and holds its stock, so flag "
    "NET exposure above 15% of the investment book.",
    "Freelance income from the Berlin client is quarterly and irregular — do not treat a quiet "
    "month as a drop in income.",
    "The Lakeside apartment instalments are the priority call on cash until handover.",
    "Prefer plain figures over percentages when reporting spending; percentages hide the size "
    "of a change.",
    "Sam handles the utilities and does not want reminders about them.",
    "We review the budget on the last Sunday of the month, so anything non-urgent can wait "
    "for that rather than interrupting.",
]


def seed_alerts(instruments: dict, prices: dict[str, float]) -> int:
    """Create the price alerts, priced off each ticker's own current level.

    ``prices`` is symbol → the last close in the seeded series, so a level lands the same
    distance from the market whatever the market happens to be.
    """
    from backend.db.alert_store import AlertStore

    store = AlertStore()
    made = 0
    for symbol, op, multiple, note in ALERTS:
        instrument_id = instruments.get(symbol)
        current = prices.get(symbol)
        if not instrument_id or not current:
            continue
        value = round(current * multiple, 2 if current < 1000 else 0)
        try:
            store.create(
                {
                    "ticker": symbol,
                    "instrument_id": instrument_id,
                    "op": op,
                    "value": value,
                    "note": note,
                    "asset_class": "crypto" if symbol in ("BTC", "ETH") else "equity",
                }
            )
            made += 1
        except Exception as exc:  # noqa: BLE001 — an alert is decoration, never a build blocker
            log.warning("demo alert %s %s %s skipped: %s", symbol, op, value, exc)
    return made


def seed_memory() -> int:
    """Give the assistant the standing facts it should not have to be told twice."""
    from backend.ai import memory as memory_module

    made = 0
    for fact in MEMORY:
        try:
            memory_module.add_explicit(fact, section="other")
            made += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("demo memory entry skipped: %s", exc)
    return made
