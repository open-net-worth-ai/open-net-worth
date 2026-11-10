"""The facts the daily briefing is told, each read from the authority its screen reads.

The briefing is narration of figures the app has already computed. Persona QA (2026-10-14, a real
book) caught it contradicting the Dashboard it sits on: "nothing falls due in the next fortnight"
beside a calendar with a loan auto-debit tomorrow and six overdue items; a holding "across lots"
at S$120K where Investments showed ~S$68K (it summed unvested grant rows and the same name at
several accounts); a monthly budget and an "over budget" category the Budget screen never showed.
Each block below therefore calls the service the matching screen calls, and no block does money
math of its own:

* due and overdue  — ``commitments.due_summary`` (the ONE overdue / due-in-30-days definition
  the Next-30-days card and the Commitments screen read) and the action log;
* holdings         — ``investments_book`` (owned, market-valued, merged by name, once);
* this month's budget — the home engine's month view (``planning.budget_month`` for Zero-Based,
  ``budget_engines.service.proportional_month`` for 50/30/20) — never a second budget definition.

Money is written ``S$12,508`` throughout, so the note never mixes it with ``SGD 12,508``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from backend.services import commitments as commitments_service
from backend.services import planning as planning_service
from backend.services import recurring_baseline, thesis_judgment
from backend.services.actions import read_actions
from backend.services.budget_engines import base as budget_engines
from backend.services.budget_engines import service as budget_engines_service
from backend.services.commitments import DUE_WINDOW_DAYS

_SYMBOLS = {
    "SGD": "S$",
    "USD": "US$",
    "AUD": "A$",
    "CAD": "C$",
    "HKD": "HK$",
    "NZD": "NZ$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
}
logger = logging.getLogger(__name__)

_EPSILON = 0.005
_OVERDUE_LISTED = 6
_UPCOMING_LISTED = 12
_HOLDINGS_LISTED = 12
_FLAGGED_LISTED = 5


def money(amount: float, currency: str) -> str:
    """``S$12,508`` / ``-US$300`` / ``CHF 1,200`` — the one notation the briefing uses."""
    ccy = (currency or "").upper()
    sign = "-" if round(amount) < 0 else ""
    symbol = _SYMBOLS.get(ccy)
    body = f"{abs(round(amount)):,}"
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{ccy} {body}"


def holdings_lines(book: dict, base: str) -> list[str]:
    """One line per OWNED holding, biggest first, from the Investments book's merged rows —
    the figures the Investments screen shows. ``native:`` is given for a foreign holding so the
    note never re-labels a base-currency value with the foreign code."""
    rows = sorted(book["merged_rows"], key=lambda r: r["value"], reverse=True)
    lines = []
    for r in rows[:_HOLDINGS_LISTED]:
        currency = (r["currency"] or base).upper()
        native = (
            f", native: {currency} {round(r['qty'] * r['price']):,}"
            if currency != base and r["price"]
            else ""
        )
        lines.append(
            f"  {r['name'] or r['symbol']} ({r['symbol']}) — {money(r['value'], base)} "
            f"({r['weight']:.1f}% of invested), today {r['day_pct']:+.1f}%, "
            f"currency: {currency}{native}"
        )
    if len(rows) > _HOLDINGS_LISTED:
        lines.append(f"  (+{len(rows) - _HOLDINGS_LISTED} smaller holdings)")
    return lines


def _mover(row: dict) -> str:
    return f"{row['name'] or row['symbol']} ({row['symbol']}) {row['day_pct']:+.1f}%"


def movers_lines(book: dict) -> list[str]:
    """Today's move for EVERY owned holding, weakest first — not the largest dozen by value.

    The holdings list is cut to the biggest positions, so the weakest line in it was not the
    weakest holding: the note once named a -3.3% name while a smaller position sat at -8.5%.
    The extremes are stated outright and the whole ranking follows, from the same merged rows
    the Investments screen shows. Holdings with no day change (a flat or unpriced name) are
    counted, not ranked."""
    moved = sorted(
        (r for r in book["merged_rows"] if r["day_pct"]),
        key=lambda r: (r["day_pct"], r["symbol"]),
    )
    flat = len(book["merged_rows"]) - len(moved)
    if not moved:
        return []
    lines = [
        f"  Weakest holding today: {_mover(moved[0])}. Strongest: {_mover(moved[-1])}.",
        "  All moves, weakest first: " + "; ".join(_mover(r) for r in moved),
    ]
    if flat:
        lines.append(f"  ({flat} more holdings with no move today)")
    return lines


def thesis_judgments(book: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """``({symbol: judgment}, {symbol: name})`` for every owned holding (its Investments row's
    price) and watched ticker with an investment thesis — {} when the book has none."""
    from backend.services.watch_signals import watched_items
    from backend.services.watchlist import read_watchlist

    rows = book["merged_rows"]
    held = {(r.get("symbol") or "").upper() for r in rows}
    try:
        watched = watched_items(read_watchlist(), held, include_auto=False)
    except Exception:
        logger.warning("briefing watchlist unavailable for theses", exc_info=True)
        watched = []
    judgments = thesis_judgment.book_judgments(rows, watched)
    names = {(r.get("symbol") or "").upper(): r.get("name") or "" for r in rows}
    names.update({(i.get("value") or "").upper(): i.get("label") or "" for i in watched})
    return judgments, {t: names.get(t, "") for t in judgments}


def thesis_lines(judgments: dict[str, dict], names: dict[str, str]) -> list[str]:
    """One cited line per thesis — the briefing's view of where each price sits against it."""
    return [
        f"  {names.get(t) or t} ({t}, {'held' if j['held'] else 'watched'}): "
        f"{thesis_judgment.cite_line(j)}"
        for t, j in judgments.items()
    ]


def _due_line(event: dict, today: date, base: str) -> str:
    due = date.fromisoformat(event["date"])
    delta = (due - today).days
    when = f"{-delta}d overdue" if delta < 0 else f"{delta}d"
    amount = event.get("amount_base")
    paid = f": {money(amount, base)}" if amount is not None else ""
    label = event.get("label") or event.get("category") or ""
    return f"  [{event['date']}, {when}] {event['commitment_name']} — {label}{paid}"


def upcoming_lines(today: date, base: str, rates: dict, days: int = DUE_WINDOW_DAYS) -> list[str]:
    """What is overdue and what falls due within ``days``, from ``commitments.due_summary`` —
    the one definition the Dashboard's Next-30-days card and the Commitments masthead read
    (count, total and window all match) — then the action log. Empty only when ALL are."""
    summary = commitments_service.due_summary(days=days, base=base, rates=rates)
    overdue, coming = summary["overdue"], summary["due"]
    lines: list[str] = []
    if overdue:
        lines.append(
            f"  OVERDUE ({len(overdue)} unpaid or not done, "
            f"{money(summary['overdue_total_base'], base)} in all, oldest first):"
        )
        lines += [_due_line(e, today, base) for e in overdue[:_OVERDUE_LISTED]]
        if len(overdue) > _OVERDUE_LISTED:
            lines.append(f"  (+{len(overdue) - _OVERDUE_LISTED} more overdue)")
    if coming:
        lines.append(
            f"  DUE IN THE NEXT {days} DAYS ({len(coming)}, "
            f"{money(summary['due_total_base'], base)} going out):"
        )
        lines += [_due_line(e, today, base) for e in coming[:_UPCOMING_LISTED]]
        if len(coming) > _UPCOMING_LISTED:
            lines.append(f"  (+{len(coming) - _UPCOMING_LISTED} more)")
    lines += _expected_income_lines(today, base, rates, days)
    funding_late = summary["funding_overdue"]
    if funding_late:
        lines.append(
            f"  FUNDING PAST ITS DATE ({len(funding_late)} planned inflows not marked as "
            "arrived; not part of the overdue count above):"
        )
        lines += [_due_line(e, today, base) for e in funding_late[:_OVERDUE_LISTED]]
    lines += _action_log_lines(today, days)
    return lines


def _expected_income_lines(today: date, base: str, rates: dict, days: int) -> list[str]:
    """Recurring income landing inside the window — the Next-30-days card's own "income
    expected" figure (``recurring_baseline.upcoming_income``). A note that lists only what
    goes out once left the owner's S$21K salary due on the 28th unmentioned."""
    try:
        income = recurring_baseline.upcoming_income(days, base, rates, today=today)
    except Exception:  # noqa: BLE001 — the outflow side still answers
        logger.warning("briefing expected income unavailable", exc_info=True)
        return []
    if not income:
        return []
    total = sum(e["amount_base"] for e in income)
    lines = [f"  INCOMING IN THE NEXT {days} DAYS ({len(income)}, {money(total, base)} coming in):"]
    for e in income[:_UPCOMING_LISTED]:
        delta = (date.fromisoformat(e["date"]) - today).days
        lines.append(f"  [{e['date']}, {delta}d] {e['label']}: {money(e['amount_base'], base)}")
    return lines


def _action_log_lines(today: date, days: int) -> list[str]:
    lines = []
    for a in sorted(read_actions(), key=lambda x: x.get("due", "")):
        if a.get("status") == "Done" or not a.get("due"):
            continue
        try:
            delta = (date.fromisoformat(a["due"]) - today).days
        except ValueError:
            continue
        if 0 <= delta <= days:
            lines.append(f"  [{a['due']}, {delta}d] {a.get('category')} — {a.get('action')}")
    return lines


def _zero_based_lines(ym: str, base: str) -> list[str]:
    table = planning_service.budget_month(ym)
    totals = table.get("totals") or {}
    assigned = float(totals.get("assigned") or 0.0)
    spent = max(0.0, -float(totals.get("activity") or 0.0))
    if assigned < _EPSILON and spent < _EPSILON:
        return []
    lines = [
        f"  Budget method: Zero-Based (Plan › Budget). {ym}: assigned {money(assigned, base)}, "
        f"spent {money(spent, base)}, still to assign "
        f"{money(float(table.get('ready_to_assign') or 0.0), base)}"
    ]
    red = [
        (c["label"], float(c["available"]))
        for g in table.get("groups", [])
        for c in g.get("categories", [])
        if float(c.get("available") or 0.0) < -_EPSILON
    ]
    if red:
        red.sort(key=lambda r: r[1])
        lines.append(
            "  Envelopes overspent (the screen shows these red): "
            + "; ".join(f"{label} {money(avail, base)}" for label, avail in red[:_FLAGGED_LISTED])
        )
    return lines


def _proportional_lines(ym: str, base: str) -> list[str]:
    month = budget_engines_service.proportional_month(ym)
    if not month.get("configured"):
        return []
    groups = [g for g in month["groups"] if not g["excluded"]]
    unassigned = month["unassigned"]
    outflow = sum(g["activity"] for g in groups) + unassigned["uncategorized"]
    outflow += unassigned["no_envelope"]
    income = month["income"]["amount"]
    lines = [
        f"  Budget method: 50/30/20 (Plan › Budget), {ym}: spent {money(outflow, base)} of "
        f"{money(income, base)} income. There is no per-category budget in this method: call "
        "something 'over budget' ONLY if it is listed as over its cap below."
    ]
    for g in groups:
        share = f", target {g['target']:g}% of income" if g["target"] else ""
        if g["activity"] or g["target"]:
            lines.append(f"    {g['name']}: {money(g['activity'], base)}{share}")
    over = [
        (e["label"], e["activity"] - e["cap"])
        for e in month["envelopes"]
        if not e["excluded"] and e["cap"] and e["activity"] > e["cap"] + _EPSILON
    ]
    if over:
        over.sort(key=lambda r: r[1], reverse=True)
        lines.append(
            "  Over their cap: "
            + "; ".join(f"{label} by {money(by, base)}" for label, by in over[:_FLAGGED_LISTED])
        )
    return lines


def budget_lines(ym: str, base: str) -> list[str]:
    """This month in the book's HOME budget method, as its own Budget screen shows it. Empty
    when no budget is set up — the caller then falls back to a plain month-to-date spend."""
    if budget_engines.home_engine_id() == "proportional":
        return _proportional_lines(ym, base)
    return _zero_based_lines(ym, base)


def card_coverage_lines(ym: str, base: str) -> list[str]:
    """Each card's balance against what its payment envelope holds, from the Budget's own
    Credit Card Payments rows (``planning.card_payment_coverage``). A card whose envelope holds
    what it owes is covered: the note must say so, not call it a risk or ask cash to absorb it."""
    lines = []
    for c in planning_service.card_payment_coverage(ym):
        if c["owed"] < _EPSILON:
            continue
        status = (
            "fully covered"
            if c["underfunded"] < _EPSILON
            else f"short by {money(c['underfunded'], base)}"
        )
        lines.append(
            f"  {c['name']}: owes {money(c['owed'], base)}, its payment envelope holds "
            f"{money(c['held'], base)} ({status})"
        )
    return lines


def cash_availability_lines(ym: str, base: str, card_owed: float) -> list[str]:
    """Whether cash can take on a card balance is a Ready-to-Assign question, never a question
    of account balances: money already assigned to envelopes is spoken for. A new book's note
    once said cash "can absorb the card comfortably" with every dollar assigned. A card whose
    payment envelope already holds its balance needs no cash at all (a funded S$392 card was
    described as "nothing is free to absorb it"). Empty unless the home method is Zero-Based
    and money has been assigned — with no budget there is no Ready to Assign to speak of, and
    50/30/20 has no such concept."""
    if budget_engines.home_engine_id() == "proportional":
        return []
    table = planning_service.budget_month(ym)
    if float((table.get("totals") or {}).get("assigned") or 0.0) < _EPSILON:
        return []
    ready = float(table.get("ready_to_assign") or 0.0)
    lines = [f"  Ready to Assign (cash not yet given a job): {money(ready, base)}"]
    if card_owed <= _EPSILON:
        return lines
    coverage = planning_service.card_payment_coverage(ym)
    covered = sum(min(c["owed"], c["held"]) for c in coverage)
    lines += card_coverage_lines(ym, base)
    uncovered = max(0.0, card_owed - covered)
    if uncovered < _EPSILON:
        lines.append(
            f"  Credit cards owe {money(card_owed, base)} and every dollar is already set aside "
            "in their payment envelopes: say the cards are covered; do NOT call the balance a "
            "risk, say nothing is free to absorb it, or tell the client to move cash for it."
        )
        return lines
    verdict = (
        f"cash can take on at most {money(ready, base)} of it without drawing on assigned money"
        if ready > _EPSILON
        else "every dollar of cash is already assigned, so do NOT say cash can absorb it"
    )
    part = (
        f", of which {money(uncovered, base)} is not yet set aside in a payment envelope"
        if covered >= _EPSILON
        else ""
    )
    lines.append(f"  Credit cards owe {money(card_owed, base)}{part}: {verdict}.")
    return lines


def book_fingerprint(ym: str) -> str:
    """A short digest of the STRUCTURE the note narrates: which accounts exist and of what type,
    which categories sit in which envelopes, whether any money is assigned, how many positions
    are held, and what is overdue. The cached note is stale when it changes — a new user's note,
    written before their cards and envelopes existed, once kept advising "link your categories"
    after they had. Balances and prices are deliberately absent: they move all day and never
    make the narrative wrong."""
    from backend.db.account_store import AccountStore
    from backend.db.budget_assignments_store import BudgetAssignmentsStore
    from backend.db.envelope_store import EnvelopeStore
    from backend.db.position_store import PositionStore

    summary = commitments_service.due_summary()
    structure = {
        "accounts": sorted(
            (a["id"], a.get("type"), bool(a.get("archived"))) for a in AccountStore().all()
        ),
        "envelopes": sorted(
            (e["id"], sorted(e.get("category_ids") or []), bool(e.get("archived")))
            for e in EnvelopeStore().all()
        ),
        "assigned": any(v for v in BudgetAssignmentsStore().month_map(ym).values()),
        "positions": len(PositionStore().all()),
        "overdue": summary["overdue_count"],
        "due": summary["due_count"],
        "budget_engine": budget_engines.home_engine_id(),
    }
    return hashlib.sha256(json.dumps(structure, default=str).encode()).hexdigest()[:16]
