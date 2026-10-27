"""Build the whole demo book, in dependency order, through the app's own write paths.

The order in ``build`` is the DEPENDENCY order, not a preference. Each step below is there
because the one after it cannot run first:

* accounts before the ledger — every leg names one;
* instrument rows before the live-price lookup — it routes a coin by reading its row;
* prices before the trade plan — a buy is priced off that day's close;
* trades before the ledger — the cash legs that settle them are ordinary transactions;
* the ledger before envelopes — the envelope seed mints one per category with activity;
* the card payments last among the transactions — they are computed FROM the charges;
* the Singapore chapter after the plan — it budgets cash that is already in the book.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from backend.scripts.demo import (
    assistant,
    automations,
    cast,
    dashboard,
    investments,
    ledger_income,
    ledger_lifestyle,
    markets,
    owned_assets,
    planning,
    rows,
    singapore,
)
from backend.scripts.demo import ledger_spending as spend
from backend.scripts.demo._timeline import Timeline, on_business_day

log = logging.getLogger(__name__)

# Transactions are written in chunks: one bulk call resolves accounts/categories/FX once,
# but a single 2,000-row call also builds one enormous in-memory batch. This is the
# compromise that keeps both the write and the memory profile sane.
CHUNK = 400


def book_is_empty() -> bool:
    """True when nothing of substance exists yet. The demo build REFUSES a populated book —
    the same promise ``/api/demo/seed`` has always made, restated against the DB stores that
    replaced the files it used to check."""
    from backend.db.account_store import AccountStore
    from backend.db.position_store import PositionStore
    from backend.db.transaction_store import TransactionStore

    return not (AccountStore().count() or TransactionStore().count() or PositionStore().count())


def _seed_profile(tl: Timeline) -> None:
    from backend.services import fx_cache
    from backend.services.profile import read_profile_raw, write_profile

    write_profile({**read_profile_raw(), **cast.profile(tl)})
    fx_cache.save_fx_cache(cast.FX_RATES)


def _seed_categories() -> dict[str, str]:
    """Seed the built-ins, then add the handful a household needs that they leave out.

    A fresh book has no categories at all — the app seeds the built-ins in its lifespan, which
    a builder that never starts the app does not get. Returns handle → category id; the
    generators name categories by handle so a rename is one line here.
    """
    from backend.services import categories as categories_service

    categories_service.ensure_builtins_seeded()
    # The demo's scripted ledger still uses the historical category ids. They are
    # demo content now, not part of a new user's starter budget.
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    store = CategoryStore()
    present = {row["id"] for row in store.all()}
    store.bulk_insert([row for row in BUILTIN_CATEGORIES if row["id"] not in present])
    handles = dict(cast.BUILTIN)
    existing = {c["label"]: c["id"] for c in categories_service.all_category_rows()}
    for handle, label, kind, color in cast.CUSTOM_CATEGORIES:
        if label in existing:
            handles[handle] = existing[label]
            continue
        created = categories_service.create_category(label, kind=kind, color=color)
        handles[handle] = created["id"]
    return handles


def _seed_accounts(tl: Timeline) -> dict[str, str]:
    from backend.services import accounts as accounts_service

    out: dict[str, str] = {}
    for spec in cast.ACCOUNTS:
        data = {k: v for k, v in spec.items() if k not in ("key", "opening", "color")}
        data["initial_balance"] = spec["opening"]
        data["initial_balance_date"] = tl.start.isoformat()
        out[spec["key"]] = accounts_service.create_account(data)["id"]
    return out


def _card_payments(tl: Timeline, A: dict, card_rows: list[dict]) -> list[dict]:
    """Pay the card in full, one cycle behind — the statement closes on the 18th and the
    payment lands on the 12th of the following month, which is what the account's own
    ``statement_close_day``/``payment_due_day`` claim."""
    charges: dict[tuple[int, int], float] = {}
    for row in card_rows:
        if row.get("from_account_id") != A["card"]:
            continue
        d = date.fromisoformat(row["date"])
        # A charge on the 19th or later belongs to NEXT month's statement.
        cycle = d if d.day <= 18 else (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        charges[(cycle.year, cycle.month)] = charges.get((cycle.year, cycle.month), 0.0) + float(
            row["from_amount"]
        )

    out = []
    opening_carry = 1240.0  # the balance the account opened with
    for (year, month), amount in sorted(charges.items()):
        due = date(year + (month == 12), (month % 12) + 1, 12)
        d = tl.clamp(on_business_day(due))
        if d is None:
            continue
        total = round(amount + opening_carry, 2)
        opening_carry = 0.0
        out.append(
            rows.transfer(
                d,
                A["checking"],
                A["card"],
                total,
                "Sapphire Card — Payment",
                note="Statement paid in full.",
            )
        )
    return out


def _build_ledger(
    tl: Timeline, A: dict, C: dict, trades: list[dict], trims: list[dict]
) -> list[dict]:
    """Every transaction the book carries, in one list, sorted by date."""
    out: list[dict] = []
    for fn in (
        ledger_income.salary,
        ledger_income.bonus,
        ledger_income.freelance,
        ledger_income.euro_costs,
        ledger_income.interest,
        ledger_income.mortgage,
        ledger_income.student_loan,
        ledger_income.utilities,
        spend.groceries,
        spend.dining,
        spend.transport,
        spend.subscriptions,
        spend.health,
        ledger_lifestyle.shopping,
        ledger_lifestyle.entertainment,
        ledger_lifestyle.pets,
        ledger_lifestyle.hobby,
        ledger_lifestyle.giving,
        ledger_lifestyle.gifts,
        ledger_lifestyle.travel,
        ledger_lifestyle.taxes,
        ledger_lifestyle.cash,
        ledger_lifestyle.split_runs,
    ):
        out.extend(fn(tl, A, C))
    out.extend(ledger_income.household_funding(tl, A))
    out.extend(ledger_income.freelance_repatriation(tl, A))
    out.extend(ledger_income.sweeps(tl, A, trades))
    out.extend(ledger_income.fx_top_ups(tl, A))
    out.extend(_investment_cash_legs(A, trades, trims))
    out.extend(_retirement_contributions(tl, A, trades))
    out.extend(_card_payments(tl, A, out))
    out.sort(key=lambda r: (r["date"], r.get("description", "")))
    return out


def _investment_cash_legs(A: dict, trades: list[dict], trims: list[dict]) -> list[dict]:
    """The cash that settles each trade. The 401(k) is funded separately (below), so its buys
    still debit its own cash — the two lines net to the holding."""
    out = [
        rows.buy(
            order["date"],
            A[order["account"]],
            order["gross"],
            f"Buy {order['quantity']:g} {order['symbol']}",
            note=f"{order['symbol']} @ {order['unit_cost']:.2f} USD",
            tags=["trade"],
        )
        for order in trades
    ]
    out += [
        rows.sell(
            trim["date"],
            A[trim["account"]],
            round(trim["gross"] - trim["fees"], 2),
            f"Sell {trim['quantity']:g} {trim['symbol']}",
            note=f"{trim['symbol']} @ {trim['unit_price']:.2f} USD — rebalancing trim",
            tags=["trade"],
        )
        for trim in trims
    ]
    return out


def _retirement_contributions(tl: Timeline, A: dict, trades: list[dict]) -> list[dict]:
    """401(k) deferral + employer match, as an ADJUSTMENT.

    Not income: the salary rows are already net of the deferral, and the match never passes
    through a pay cheque at all. An adjustment moves the balance without claiming the
    household earned it twice — which is exactly the kind's job.
    """
    monthly: dict[str, float] = {}
    for order in trades:
        if order["account"] != "401k":
            continue
        monthly[order["date"].isoformat()] = (
            monthly.get(order["date"].isoformat(), 0.0) + order["gross"]
        )
    return [
        rows._row(
            date=d,
            kind="adjustment",
            description="Cloudflare — 401(k) contribution",
            to_account_id=A["401k"],
            to_amount=round(amount, 2),
            note="Payroll deferral plus the employer match.",
        )
        for d, amount in sorted(monthly.items())
    ]


def _write_transactions(txns: list[dict]) -> int:
    from backend.services import ledger as ledger_service

    written = 0
    for i in range(0, len(txns), CHUNK):
        created, _ = ledger_service.bulk_create_transactions(txns[i : i + CHUNK])
        written += len(created)
    return written


def build(today: date | None = None, *, offline: bool = False) -> dict:
    """Build the entire demo book. Returns a summary of what was written.

    ``offline=True`` skips the live-price lookup, which is what makes a build reproducible
    from ``(today, seed)`` alone — tests use it. Online, the generated price history is
    anchored on today's REAL price per ticker so it joins the live series without a seam.
    """
    from backend.services import envelopes as envelopes_service

    tl = Timeline.anchored_on(today or date.today())
    report: dict = {"anchor": tl.today.isoformat(), "start": tl.start.isoformat()}

    _seed_profile(tl)
    C = _seed_categories()
    # The boot's first-run envelope seed. It is a no-op once ANY envelope exists, and creating
    # a credit card mints its payment envelope — so it has to run before the accounts do.
    envelopes_service.ensure_envelopes_migrated()
    A = _seed_accounts(tl)
    report["accounts"] = len(A)
    report["categories"] = len(C)
    report["owned_assets"] = len(owned_assets.seed(tl, A, offline=offline))

    # Instruments FIRST: the live-price lookup routes a CoinGecko coin by reading its
    # instrument row, so anchoring the history depends on those rows already existing.
    instruments = investments.create_instruments(tl)
    report["instruments"] = len(instruments)
    anchors = {} if offline else markets.live_anchors()
    report["live_anchored"] = len(anchors)
    series = {
        spec["symbol"]: markets.price_series(spec, tl, anchors.get(spec["symbol"]))
        for spec in markets.INSTRUMENTS
    }
    report["price_points"] = investments.record_history(series)
    trades = markets.build_trades(tl, series)
    trims = markets.build_trims(tl, series, trades)

    txns = _build_ledger(tl, A, C, trades, trims)
    report["transactions"] = _write_transactions(txns)

    positions = investments.create_positions(trades, A, instruments)
    report["positions"] = len(positions)
    report["lots"] = investments.book_lots(trades, positions)
    report["trims"] = investments.book_trims(trims, positions)
    report["dividends"] = investments.book_dividends(tl, positions, trades)
    report["grant"] = investments.book_grant(tl, A, instruments, series)
    report["classified"] = investments.categorize_investment_income(C)

    report["plan"] = planning.seed_envelopes_and_budget(tl)
    report["goals"] = planning.seed_goals(tl, A)
    report["commitments"] = planning.seed_commitments(tl, A, positions)
    report["watchlist"] = planning.seed_watchlist()
    report["dashboard_views"] = dashboard.seed_views()

    # The Singapore chapter goes in LAST among the money: its budget step re-balances Ready to
    # Assign over the finished book, and the counts above must keep naming what the DB holds.
    report["singapore"] = singapore.seed(tl, offline=offline)
    for key in ("accounts", "categories", "transactions", "instruments"):
        report[key] += report["singapore"][key]

    # The assistant side: standing work, watch levels, and what it already knows. An
    # empty Automations tab shows a feature that exists; these show one in use.
    report["automations"] = automations.seed_automations(C)
    report["alerts"] = assistant.seed_alerts(
        instruments, {sym: rows[-1]["price"] for sym, rows in series.items() if rows}
    )
    report["memory"] = assistant.seed_memory()
    return report
