"""Spending advice before a payment — "Ask Lucius first".

One read: what a planned purchase does to the owner's money, before they pay. It composes the
authorities that already own each figure and recomputes none of them:

* the envelope and Ready to Assign — ``planning.budget_month`` (the Budget screen's table);
* card payment coverage — ``planning.card_payment_coverage``;
* bills and expected income — ``commitments_summary.commitments_overview`` (the Next-30-days
  card's feed: commitments due, recurring bills, card statements, recurring income).

The verdict is one of ``fits`` / ``fits_with_cover`` / ``wait`` / ``over``. ``wait`` fires only
when cash would not cover the bills due before the next expected income, and always carries that
date; an essential purchase never gets it. Writes nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

from backend.core.errors import MissingFxRateError, ValidationError, recovery_hint
from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.services import accounts as accounts_service
from backend.services import categories, commitments_summary, payee_registry, planning
from backend.services import ledger as svc
from backend.services.fx_cache import load_fx_cache

HORIZON_DAYS = 45
RECENT_DAYS = 14
_MAX_COVER = 3
_MAX_RECENT = 3


def spending_advice(
    amount: float,
    currency: str,
    *,
    merchant: str | None = None,
    category: str | None = None,
    pay_with: str | None = None,
    on: date | None = None,
    essential: bool = False,
) -> dict:
    """The advice for spending ``amount`` ``currency`` — see the module docstring."""
    if not amount or float(amount) <= 0:
        raise ValidationError("amount must be a positive number")
    today = on or user_today()
    base, rates = base_currency(), load_fx_cache()
    amount_base = _to_base(float(amount), currency, base, rates)
    month = f"{today.year:04d}-{today.month:02d}"
    table = planning.budget_month(month)

    payee_id = payee_registry.resolve_payee_id(merchant, create=False) if merchant else None
    category_id = _resolve_category(category, payee_id)
    account = _resolve_account(pay_with)
    is_card = bool(account) and account.get("type") == "credit"

    envelope = _envelope(table, category_id, amount_base)
    cover = _cover_options(table, envelope)
    bills = _bills_before_income(today, base, rates, table, 0.0 if is_card else amount_base)
    card = _card(account, month) if is_card else None
    verdict, reasons, options = _verdict(amount_base, base, envelope, cover, bills, card, essential)
    return {
        "verdict": verdict,
        "reasons": reasons,
        "options": options,
        "resolved": {
            "amount_base": amount_base,
            "base_currency": base,
            "category_id": category_id,
            "category": _category_label(category_id),
            "pay_with": account.get("name") if account else "cash",
            "essential": bool(essential),
        },
        "envelope": envelope,
        "cover": cover,
        "bills_before_income": bills,
        "card": card,
        "recent_at_payee": _recent(payee_id, today, base, rates),
        "as_of": today.isoformat(),
    }


def _to_base(amount: float, currency: str, base: str, rates: dict) -> float:
    try:
        return to_base(amount, (currency or base).upper(), base, rates)
    except MissingFxRateError as exc:
        raise ValidationError(
            f"Can't value {currency.upper()} in {base}: no exchange rate is available yet."
        ) from exc


def _resolve_category(ref: str | None, payee_id: str | None) -> str | None:
    """Explicit wins; else the payee's usual category; an unknown explicit ref RAISES."""
    if ref:
        rows = categories.all_category_rows()
        found = categories.resolve_category_ref(ref, rows=rows)
        if found is None:
            labels = [r.get("label") or "" for r in rows]
            raise ValidationError(
                f"Unknown category {ref!r}."
                + recovery_hint(ref, labels, tool_hint="get_budget_month")
            )
        return found
    return payee_registry.default_category_for(payee_id) if payee_id else None


def _resolve_account(ref: str | None) -> dict | None:
    if not ref:
        return None
    account_id = accounts_service.resolve_account_ref(ref)
    if account_id is None:
        names = [a.get("name") or "" for a in svc.accounts.query_accounts()]
        raise ValidationError(
            f"Unknown account {ref!r}." + recovery_hint(ref, names, tool_hint="get_accounts")
        )
    return svc.accounts.find_by_id(account_id)


def _category_label(category_id: str | None) -> str:
    if not category_id:
        return "Uncategorized"
    for row in categories.all_category_rows():
        if row.get("id") == category_id:
            return row.get("label") or category_id
    return category_id


def _rows(table: dict):
    for group in table.get("groups") or []:
        if group.get("unsorted") or group.get("unbudgeted"):
            continue
        yield from group.get("categories") or []


def _envelope(table: dict, category_id: str | None, amount_base: float) -> dict | None:
    """The envelope holding ``category_id`` this month, before and after the purchase."""
    if not category_id:
        return None
    for row in _rows(table):
        if category_id in (row.get("category_ids") or []):
            before = float(row.get("available") or 0.0)
            return {
                "envelope_id": row.get("envelope_id"),
                "label": row.get("label"),
                "available_before": round(before, 2),
                "available_after": round(before - amount_base, 2),
            }
    return {"envelope_id": None, "label": None, "available_before": None, "available_after": None}


def _cover_options(table: dict, envelope: dict | None) -> list[dict]:
    """Where a shortfall could come from: Ready to Assign, then envelopes with room."""
    if not envelope or envelope.get("available_after") is None or envelope["available_after"] >= 0:
        return []
    out = []
    rta = float(table.get("ready_to_assign") or 0.0)
    if rta > 0:
        out.append({"from": "Ready to Assign", "envelope_id": None, "available": round(rta, 2)})
    others = [
        r
        for r in _rows(table)
        if r.get("envelope_id") != envelope.get("envelope_id")
        and not r.get("is_payment_envelope")
        and float(r.get("available") or 0.0) > 0
    ]
    others.sort(key=lambda r: -float(r.get("available") or 0.0))
    for r in others[:_MAX_COVER]:
        out.append(
            {
                "from": r.get("label"),
                "envelope_id": r.get("envelope_id"),
                "available": round(float(r["available"]), 2),
            }
        )
    return out


def _bills_before_income(
    today: date, base: str, rates: dict, table: dict, cash_outflow: float
) -> dict:
    """Cash after this purchase and every bill due before the next expected income."""
    feed = commitments_summary.commitments_overview(days=HORIZON_DAYS, strip_days=HORIZON_DAYS)
    incomes = [e for e in feed.get("expected_income") or [] if e["date"] > today.isoformat()]
    next_income = incomes[0]["date"] if incomes else None
    through = next_income or (today + timedelta(days=30)).isoformat()
    due = [
        {"label": _bill_label(e), "amount_base": float(e["amount_base"]), "due": e["date"]}
        for e in (feed.get("overdue") or [])
        + _feed_due(feed, today)
        + (feed.get("bills") or [])
        + (feed.get("card_bills") or [])
        if e.get("amount_base") and e.get("date") and e["date"] <= through
    ]
    due.sort(key=lambda e: e["due"])
    cash = float(table.get("on_budget_cash") or 0.0)
    total = round(sum(e["amount_base"] for e in due), 2)
    return {
        "next_income": next_income,
        "due": due,
        "due_total": total,
        "cash_now": round(cash, 2),
        "cash_after_purchase_and_bills": round(cash - cash_outflow - total, 2),
    }


def _bill_label(event: dict) -> str:
    name, label = event.get("commitment_name"), event.get("label")
    return " — ".join(x for x in (name, label) if x) or "A bill"


def _feed_due(feed: dict, today: date) -> list[dict]:
    """Commitments due from ``today`` on (earlier ones are the feed's ``overdue``). The feed
    only carries their total for the strip, so the events are the window's non-funding ones."""
    start = today.isoformat()
    return [
        e
        for e in feed.get("events") or []
        if e.get("kind") != "funding" and e.get("date", "") >= start
    ]


def _card(account: dict, month: str) -> dict:
    """The card's payment coverage this month, and its APR when one is known."""
    row = next(
        (c for c in planning.card_payment_coverage(month) if c["account_id"] == account["id"]),
        None,
    )
    underfunded = round(float((row or {}).get("underfunded") or 0.0), 2)
    apr = account.get("apr")
    warning = None
    if underfunded > 0:
        warning = f"{account.get('name')}'s payment is already {underfunded:,.2f} short" + (
            f"; an unpaid balance costs {apr:g}% a year" if apr else ""
        )
    return {
        "use": account.get("name"),
        "payment_tracked": row is not None,
        "owed": round(float((row or {}).get("owed") or 0.0), 2),
        "underfunded": underfunded,
        "apr": apr,
        "warning": warning,
    }


def _recent(payee_id: str | None, today: date, base: str, rates: dict) -> list[dict]:
    if not payee_id:
        return []
    since = (today - timedelta(days=RECENT_DAYS)).isoformat()
    rows = svc.transactions.query_txns(kind="expense", payee_id=payee_id, date_from=since)
    rows = sorted(rows, key=lambda t: t.get("date") or "", reverse=True)[:_MAX_RECENT]
    return [
        {"date": (t.get("date") or "")[:10], "amount_base": svc.txn_base_amount(t, base, rates)}
        for t in rows
    ]


def _money(value: float, base: str) -> str:
    return f"{value:,.2f} {base}"


def _verdict(amount, base, envelope, cover, bills, card, essential):
    """(verdict, reasons, options) — the strictest condition wins."""
    reasons: list[str] = []
    options: list[dict] = []
    short_cash = bills["cash_after_purchase_and_bills"] < 0
    shortfall = (
        -envelope["available_after"]
        if envelope
        and envelope.get("available_after") is not None
        and envelope["available_after"] < 0
        else 0.0
    )
    if short_cash:
        reasons.append(_cash_reason(bills, base))
        if bills["next_income"] and not essential:
            options.append({"action": "wait_until", "date": bills["next_income"]})
            verdict = "wait"
        else:
            verdict = "over"
    elif shortfall > 0:
        coverable = sum(c["available"] for c in cover)
        reasons.append(f"{envelope['label']} is {_money(shortfall, base)} short for this.")
        options.extend(
            {
                "action": "cover_from",
                "source": c["from"],
                "envelope_id": c["envelope_id"],
                "amount": round(min(shortfall, c["available"]), 2),
            }
            for c in cover
        )
        verdict = "fits_with_cover" if coverable >= shortfall else "over"
    else:
        verdict = "fits"
        reasons.append(_fits_reason(envelope, base))
    if card and card.get("warning"):
        reasons.append(card["warning"] + ".")
    options.append({"action": "buy_anyway"})
    return verdict, reasons, options


def _cash_reason(bills: dict, base: str) -> str:
    gap = -bills["cash_after_purchase_and_bills"]
    first = bills["due"][0] if bills["due"] else None
    head = (
        f"{first['label']} ({_money(first['amount_base'], base)}) is due {first['due']}"
        if first
        else "Bills are due"
    )
    until = f" until {bills['next_income']}" if bills["next_income"] else ""
    return f"{head}, and cash would be {_money(gap, base)} short{until}."


def _fits_reason(envelope: dict | None, base: str) -> str:
    if envelope is None:
        return "No category was given, so only your cash and bills were checked."
    if envelope.get("available_after") is None:
        return "This category has no envelope, so only your cash and bills were checked."
    left = _money(envelope["available_after"], base)
    return f"{envelope['label']} has room: {left} left after this."
