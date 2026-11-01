"""Investment theses for the demo: why Alex owns each holding, and the prices that matter.

A thesis is the feature that turns "you own 40 units" into "you own it because, and you would
sell if": an empty "Your Thesis" card on every asset page shows a feature that exists; these
show one in use. They are also what Lucius judges advice against (ADR
``2026-10-31-advice-on-a-ticker-is-judged-against-its-thesis``), so a visitor who asks "should I
trim NVDA?" gets an answer that cites Alex's own reasoning.

Written through ``apply_thesis_change`` like a thesis typed into the asset page. Ten holdings
carry one and two (BND, ETH) deliberately do not, which is what a real book looks like.

Every price level is a MULTIPLE of the holding's own close on the anchor day, never an absolute
number: the book is re-dated daily and an online build anchors its history on the live price, so
a hard-coded "trim above 260" would be absurd or already crossed the day the market moved. The
multiples for the tickers that also carry a price alert (``assistant.ALERTS``) are the alert's
own, so the two surfaces tell one story about the same position. The close is read from the
recorded history, which is the series this build wrote, so the Singapore ETFs (whose series the
chapter keeps to itself) price the same way as the rest. Text that names a year derives it from
the grant ladder, never a literal.
"""

from __future__ import annotations

import logging

from backend.core.errors import OpenNetWorthError
from backend.scripts.demo import markets
from backend.scripts.demo._timeline import Timeline

log = logging.getLogger(__name__)

# (symbol, role, reason, breaks_if, {thesis price field: multiple of the anchor-day close}).
# Reasons name a holding period the way a person writes one ("20+ years"), which is what the
# judgment reads the horizon from; both texts stay under the 280-character limit.
THESES: list[tuple[str, str, str, str, dict[str, float]]] = [
    (
        "VOO",
        "core",
        "The base of the portfolio: the whole US market for a few basis points a year, bought "
        "every month in all three accounts. Holding 20+ years. I add on schedule and only trim "
        "to rebalance.",
        "Vanguard stops tracking the index closely, or its fee stops being among the cheapest.",
        {"buy_below": 0.90, "trim_above": 1.09},
    ),
    (
        "VXUS",
        "core",
        "Everything outside the US, so the book is not one country's bet. Holding 10+ years as a "
        "diversifier. I trimmed it once to rebalance and keep adding every month.",
        "The fund stops covering developed and emerging markets together, or its fee rises well "
        "above comparable funds.",
        {},
    ),
    (
        "SCHD",
        "core",
        "Quality US dividend payers for the income sleeve. Holding long-term: the dividend is the "
        "point, so a lower price is a reason to add, not to worry.",
        "The fund cuts its dividend, or the index drops its quality screens and starts chasing "
        "yield.",
        {"buy_below": 0.92, "fair_value": 1.10},
    ),
    (
        "AAPL",
        "core",
        "The one stock I would hold through almost anything: a cash-rich ecosystem with sticky "
        "customers and steady buybacks. Holding 5+ years. Bought in two steps; I trim only to "
        "fund the Lakeside instalments.",
        "Services growth stalls for several quarters in a row, or a regulator forces a major "
        "change to how the App Store earns.",
        {"buy_below": 0.88, "fair_value": 1.04, "trim_above": 1.15},
    ),
    (
        "NVDA",
        "satellite",
        "A momentum bet on the AI buildout that has already run a long way. It is oversized for "
        "the book, so I have trimmed once and will trim again into strength. Holding 2-3 years, "
        "not forever.",
        "Big cloud customers cut their capex guidance, or a rival wins the next large training "
        "cluster.",
        {"stop_below": 0.78, "trim_above": 1.22},
    ),
    (
        "NET",
        "satellite",
        "I work here, so my pay already rides on this company. Vested RSUs are sold into "
        "strength once the 30-day hold is up; the last tranche vests in {last_vest_year}. Keep "
        "the name under 15% of the book and hold the rest 3+ years.",
        "Revenue growth slows sharply for two quarters running, or a flagship enterprise "
        "customer leaves.",
        {"stop_below": 0.75, "trim_above": 1.20},
    ),
    (
        "GLDM",
        "satellite",
        "A small hedge against inflation and a weak dollar. It earns nothing, so it stays at a "
        "few percent of the book. Holding 5+ years as insurance, not as a trade.",
        "Real interest rates rise and stay high for a long stretch, or bonds start doing the "
        "hedging job again.",
        {"trim_above": 1.14},
    ),
    (
        "BTC",
        "speculative",
        "A small, high-risk bet bought a little every month and sized to stay under 8% of the "
        "book. Holding 5+ years. A 30% month is the price of admission, not a reason to sell.",
        "A serious protocol flaw, or a rule change that stops ordinary people holding it directly.",
        {"stop_below": 0.55, "trim_above": 1.30},
    ),
    (
        "ES3.SI",
        "core",
        "Singapore's blue chips in one low-cost fund: the home-market leg now that we live here, "
        "with a steady dividend. Holding 5+ years.",
        "The index stops being a basket of Singapore's largest companies, or the dividend is "
        "cut two years running.",
        {"buy_below": 0.94, "fair_value": 1.08},
    ),
    (
        "EUNL.DE",
        "core",
        "The world in one fund, held in euros next to the consulting income. Developed-market "
        "stocks, accumulating, no decisions to make. Holding 15+ years.",
        "The index is swapped for something narrower, or the fee rises well above comparable "
        "world trackers.",
        {"buy_below": 0.92},
    ),
]


def _nice(value: float) -> float:
    """Round a level to the granularity a person would type: 3.85, 26.5, 555, 3,350, 91,500."""
    for below, step in ((10, 0.05), (100, 0.5), (1000, 5), (10000, 50)):
        if value < below:
            return round(round(value / step) * step, 2)
    return float(round(value / 500) * 500)


def _history(pid: str) -> list[dict]:
    from backend.db.price_history_store import PriceHistoryStore

    return PriceHistoryStore().series(pid)


def _last_vest_year(tl: Timeline) -> int:
    schedule = markets.grant_schedule(tl, _history(markets.EMPLOYER_SPEC["price_source_id"]))
    return int(schedule["tranches"][-1]["vest_date"][:4])


def seed(tl: Timeline) -> int:
    """Write every thesis whose instrument is in the book. Returns how many were written.

    Runs after the Singapore chapter, whose ETFs are among the holdings that carry one.
    """
    from backend.db.instrument_store import InstrumentStore
    from backend.services import investment_theses

    store = InstrumentStore()
    ctx = {"last_vest_year": _last_vest_year(tl)}
    written = 0
    for symbol, role, reason, breaks_if, multiples in THESES:
        inst = store.find_by_symbol(symbol)
        history = _history(inst["price_source_id"]) if inst else []
        close = markets.price_on(history, tl.today) if history else None
        if not inst or not close:
            log.warning("demo thesis for %s skipped: no instrument or price in the book", symbol)
            continue
        fields = {
            "role": role,
            "reason": reason.format(**ctx),
            "breaks_if": breaks_if,
            **{field: _nice(close * multiple) for field, multiple in multiples.items()},
        }
        try:
            investment_theses.apply_thesis_change(
                {"op": "set", "instrument_id": inst["id"], "fields": fields}
            )
            written += 1
        except OpenNetWorthError as exc:  # decoration, never a build blocker
            log.warning("demo thesis for %s skipped: %s", symbol, exc)
    return written
