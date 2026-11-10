"""The Singapore relocation chapter: Alex and Sam's second life, written into the demo book.

A household that moved to Singapore a few months ago and still earns, banks and invests in
three currencies. It exists so the demo shows what the rest of the book (one USD household
with a few foreign wallets) cannot: SGD and EUR accounts that keep their own legs, an
EUR→SGD transfer, ETFs quoted in their home currencies, a receipt linked to the charge it
evidences, a Private document, and a plan that earmarks cash instead of adding it twice.

Everything is written through the same chokes as the rest of the build (accounts, the ledger,
the lot acquisition that books its own settlement leg, the Vault, receipts), so it is a book
the app could have produced. Dates are anchor-relative like everything else: the chapter opens
three full calendar months before the anchor and repeats monthly up to it, so the nightly
rebuild always lands on a household that is mid-routine. It runs LAST in ``build`` because
its budget step re-balances Ready to Assign over the finished book.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.core.user_time import user_today
from backend.scripts.demo import markets, rows, singapore_documents, singapore_plan
from backend.scripts.demo._timeline import Timeline, add_months, on_business_day

log = logging.getLogger(__name__)

CHAPTER_MONTHS = 3  # complete months before the anchor's own
INSTITUTION = "Harbour Financial (fictional)"
NOTE = "Synthetic demo; Singapore relocation chapter. No real personal records."
TAGS = ["singapore"]

ACCOUNTS: list[dict] = [
    {"key": "everyday", "name": "Singapore Everyday", "type": "cash", "currency": "SGD"},
    {"key": "runway", "name": "Singapore Runway", "type": "cash", "currency": "SGD"},
    {
        "key": "card",
        "name": "Harbour Rewards Card",
        "type": "credit",
        "currency": "SGD",
        "credit_limit": 18000.0,
        "payment_due_day": 15,
    },
    {"key": "asia", "name": "Asia Brokerage SGD", "type": "investment", "currency": "SGD"},
    {"key": "consulting", "name": "Consulting EUR", "type": "cash", "currency": "EUR"},
    {"key": "global", "name": "Global Brokerage EUR", "type": "investment", "currency": "EUR"},
]
# Native opening balances, on the timeline's first day like every other account in the book.
OPENING = {
    "everyday": 18200.0,
    "runway": 42000.0,
    "card": 0.0,
    "asia": 30000.0,
    "consulting": 4400.0,
    "global": 9000.0,
}

# (handle, label, kind, color)
CATEGORIES: list[tuple[str, str, str, str]] = [
    ("salary", "Singapore Salary", "income", "#e2554f"),
    ("bills", "Singapore Bills", "expense", "#9a7fd0"),
    ("dining", "Singapore Dining", "expense", "#1d9e75"),
    ("transport", "Singapore Transport", "expense", "#22b8cf"),
    ("consulting", "Consulting Income", "income", "#34b08a"),
    ("software", "Software & Services", "expense", "#d4537e"),
]

# The ETFs are real, SGD- and EUR-quoted tickers (like every instrument in the book). Their
# history is generated on the same bridge as the rest and anchored on today's real price online.
ETFS: list[dict] = [
    {
        "symbol": "ES3.SI",
        "name": "SPDR Straits Times Index ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "ES3.SI",
        "quote_currency": "SGD",
        "anchor": 4.18,
        "drift": 0.09,
        "vol": 0.12,
        "precision": 3,
        "account": "asia",
        "quantity": 1500.0,
        "fees": 4.0,
    },
    {
        "symbol": "EUNL.DE",
        "name": "iShares Core MSCI World UCITS ETF",
        "asset_class": "equity",
        "asset_subclass": "etf",
        "sector": "",
        "price_source": "yahoo",
        "price_source_id": "EUNL.DE",
        "quote_currency": "EUR",
        "anchor": 111.2,
        "drift": 0.10,
        "vol": 0.15,
        "precision": 2,
        "account": "global",
        "quantity": 40.0,
        "fees": 4.0,
    },
]

# The monthly routine: (day of month, description, category handle, SGD amount).
SALARY = (2, "Harbour Software - Salary", "salary", 11800.0)
RENT = (3, "Harbour Residences - Rent", "bills", 3200.0)
SPENDS = [
    (5, "Kopi Lane", "dining", 8.4),
    (8, "Bayview Hawker Centre", "dining", 14.8),
    (11, "MetroRide", "transport", 24.6),
    (14, "Forge Cloud", "software", 39.0),
    (17, "Fibre Harbour", "bills", 59.9),
    (20, "Garden Fresh Market", "dining", 86.4),
]
INVOICE = (20, "Atlas Studio - Consulting Invoice", 2400.0)  # EUR, last complete month
CONVERSION = (22, "Consulting proceeds EUR to SGD", 1000.0, 1460.0)  # EUR out, SGD in
RECEIPTED = ("Kopi Lane", 12.8)  # booked on the anchor itself, with its receipt


def _seed_categories() -> dict[str, str]:
    from backend.services import categories as categories_service

    existing = {c["label"]: c["id"] for c in categories_service.all_category_rows()}
    out: dict[str, str] = {}
    for handle, label, kind, color in CATEGORIES:
        if label in existing:
            out[handle] = existing[label]
            continue
        out[handle] = categories_service.create_category(label, kind=kind, color=color)["id"]
    return out


def _seed_accounts(tl: Timeline) -> dict[str, str]:
    from backend.services import accounts as accounts_service

    out: dict[str, str] = {}
    for spec in ACCOUNTS:
        data = {k: v for k, v in spec.items() if k != "key"}
        data.update(institution=INSTITUTION, note=NOTE)
        data["initial_balance"] = OPENING[spec["key"]]
        data["initial_balance_date"] = tl.start.isoformat()
        out[spec["key"]] = accounts_service.create_account(data)["id"]
    return out


def _seed_etfs(tl: Timeline, offline: bool) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Instrument rows and their generated history. Returns symbol → id and symbol → series."""
    from backend.db.instrument_store import InstrumentStore
    from backend.db.price_history_store import PriceHistoryStore

    anchors = {} if offline else markets.live_anchors(ETFS)
    store, history = InstrumentStore(), PriceHistoryStore()
    ids: dict[str, str] = {}
    series: dict[str, list[dict]] = {}
    for spec in ETFS:
        ids[spec["symbol"]] = store.create(
            {
                k: spec[k]
                for k in (
                    "symbol",
                    "name",
                    "asset_class",
                    "asset_subclass",
                    "sector",
                    "quote_currency",
                    "price_source",
                    "price_source_id",
                    "precision",
                )
            }
        )["id"]
        series[spec["symbol"]] = markets.price_series(spec, tl, anchors.get(spec["symbol"]))
        history.record_backfill_series(
            spec["price_source_id"], series[spec["symbol"]], source="backfill"
        )
    return ids, series


def _next_business_day(d: date) -> date:
    """A weekend payday or rent debit settles on Monday. The shared ``on_business_day`` steps
    BACK instead, which from the 2nd or 3rd would cross into the previous month and leave a
    month with two paydays and the next with none."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _first_month(tl: Timeline) -> date:
    return add_months(tl.today.replace(day=1), -CHAPTER_MONTHS)


def _ledger(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """The routine, month by month, then the one-offs. Dated from the anchor, never from today."""
    months = [add_months(_first_month(tl), i) for i in range(CHAPTER_MONTHS + 1)]
    out: list[dict] = []

    def put(d: date, row: dict) -> None:
        if tl.clamp(d) is not None:
            out.append(row)

    for month in months:
        day, label, handle, amount = SALARY
        d = _next_business_day(month.replace(day=day))
        put(d, rows.income(d, A["everyday"], amount, label, C[handle], tags=TAGS))
        day, label, handle, amount = RENT
        d = _next_business_day(month.replace(day=day))
        put(d, rows.expense(d, A["everyday"], amount, label, C[handle], tags=TAGS))
        for day, label, handle, amount in SPENDS:
            d = month.replace(day=day)
            put(d, rows.expense(d, A["everyday"], amount, label, C[handle], tags=TAGS))

    last_complete = months[-2]
    day, label, amount = INVOICE
    d = last_complete.replace(day=day)
    put(d, rows.income(d, A["consulting"], amount, label, C["consulting"], tags=TAGS))
    day, label, eur, sgd = CONVERSION
    d = last_complete.replace(day=day)
    put(d, rows.transfer(d, A["consulting"], A["everyday"], eur, label, to_amount=sgd, tags=TAGS))

    label, amount = RECEIPTED
    put(
        tl.today,
        rows.expense(tl.today, A["everyday"], amount, label, C["dining"], tags=TAGS),
    )
    return out


def _buy_etfs(
    tl: Timeline, A: dict, ids: dict[str, str], series: dict[str, list[dict]]
) -> dict[str, dict]:
    """One lot per ETF, through the acquisition choke — which books the settlement debit too.
    Returns each buy's facts for the trade note."""
    from backend.services import lots as lots_service

    # A lot records units already held, so the choke refuses a date after the user's today —
    # which an `--as-of` anchor pinned ahead of it would otherwise hand it.
    bought_on = min(on_business_day(_first_month(tl).replace(day=15)), user_today())
    facts: dict[str, dict] = {}
    for spec in ETFS:
        symbol = spec["symbol"]
        cost = round(markets.price_on(series[symbol], bought_on), spec["precision"])
        result = lots_service.apply_lot_change(
            {
                "type": "acquire",
                "instrument_id": ids[symbol],
                "account_id": A[spec["account"]],
                "funding_account_id": A[spec["account"]],
                "quantity": spec["quantity"],
                "unit_cost": cost,
                "cost_currency": spec["quote_currency"],
                "acquired_at": bought_on.isoformat(),
                "fees": spec["fees"],
                "kind": "buy",
                "note": NOTE,
                "idempotency_key": f"demo-singapore:v1:{symbol}",
            }
        )
        if result.get("error"):
            raise RuntimeError(f"singapore lot acquire failed for {symbol}: {result['error']}")
        facts[symbol] = {
            "qty": spec["quantity"],
            "fees": spec["fees"],
            "cost": cost,
            "price": series[symbol][-1]["price"],
        }
    return facts


def _file_documents(tl: Timeline, txn_id: str, facts: dict[str, dict]) -> int:
    """The eight Documents, and the receipt promoted from the Kopi Lane PDF onto its charge."""
    from backend.services import receipts as receipts_service

    shown = singapore_documents.Facts(
        today=tl.today,
        paid=add_months(tl.today.replace(day=1), -1).replace(day=INVOICE[0]),
        coffee=RECEIPTED[1],
        rent=RENT[3],
        goals=tuple(
            (g["name"], g["target_amount"], g["current_amount"]) for g in singapore_plan.GOALS
        ),
        sg_etf=facts["ES3.SI"],
        eu_etf=facts["EUNL.DE"],
    )
    filed = singapore_documents.file_documents(shown)
    result = receipts_service.apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": filed["kopi-lane-receipt.pdf"],
            "transaction_id": txn_id,
        }
    )
    if result.get("error"):
        raise RuntimeError(f"singapore receipt link failed: {result['error']}")
    return len(filed)


def seed(tl: Timeline, *, offline: bool = False) -> dict:
    """Write the whole chapter. Returns what it added, for the build report."""
    from backend.services import ledger as ledger_service

    C = _seed_categories()
    A = _seed_accounts(tl)
    ids, series = _seed_etfs(tl, offline)
    created, _ = ledger_service.bulk_create_transactions(_ledger(tl, A, C))
    facts = _buy_etfs(tl, A, ids, series)
    receipted = next(
        t
        for t in created
        if t["date"] == tl.today.isoformat()
        and t.get("from_amount") == RECEIPTED[1]
        and t.get("description") == RECEIPTED[0]
    )
    documents = _file_documents(tl, receipted["id"], facts)
    return {
        "accounts": len(A),
        "categories": len(CATEGORIES),
        "transactions": len(created) + len(ETFS),
        "instruments": len(ids),
        "documents": documents,
        **singapore_plan.seed(tl, C, _first_month(tl)),
    }
