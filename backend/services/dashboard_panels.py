"""Dashboard panels: every figure and verdict a widget shows, computed here, never in the browser.

A panel is one query plus a view (the Grafana model). The query is a `GET /api/query` source
(`services/data_query.SOURCES`, named `panel_*`); the view only formats and draws what it gets.
Each function below wraps the SAME service read the widget used before and adds what the
browser used to derive from it: a window cut by the user's clock, a share, a gap, a verdict.
Behind the `dashboard_server_panels` flag — the switch is in the frontend widget registry, so
with it off nothing calls these (ADR
`2026-11-22-a-dashboard-widget-is-a-server-query-and-a-view`).
"""

from __future__ import annotations

from datetime import date

from backend.core.user_time import user_today

UPCOMING_DAYS = 30
TOP_SPENDERS = 3
# A category is "ahead of pace" once its spent share runs 10% past the share of the month gone.
PACE_TOLERANCE = 1.1
_INCOMING = {"income", "funding"}


def _r2(v: float) -> float:
    return round(float(v), 2)


# ── Next 30 days ────────────────────────────────────────────────────────────────────────


def upcoming() -> dict:
    """The Next-30-days list and its totals, from one `commitments_overview` read. The window is
    today through +30 days on the USER's clock — the same window `due_summary` sums for "going
    out", so the list and the total under it can't disagree on which day a payment falls."""
    from backend.services import commitments_summary

    s = commitments_summary.commitments_overview()
    today = user_today()
    rows: list[dict] = []
    for e in s["events"]:
        days = (date.fromisoformat(e["date"]) - today).days
        if 0 <= days <= UPCOMING_DAYS:
            rows.append(_row(e, e["kind"], e.get("commitment_name") or "", days))
    for e in s["bills"]:
        rows.append(_row(e, "bill", "Recurring bill", _days(e, today)))
    for e in s["card_bills"]:
        sub = "Card bill (estimated)" if e.get("estimated") else "Card bill"
        rows.append(_row(e, "card", sub, _days(e, today)))
    for e in s["expected_income"]:
        rows.append(_row(e, "income", "Recurring income", _days(e, today)))
    rows.sort(key=lambda r: r["date"])
    no_dated_bills = (
        not s["events"] and s["overdue_count"] == 0 and not s["bills"] and not s["card_bills"]
    )
    return {
        "base_currency": s["base_currency"],
        "days": UPCOMING_DAYS,
        "rows": rows,
        "going_out_total_base": s["going_out_total_base"],
        "coming_in_total_base": s["expected_income_total_base"],
        "overdue_count": s["overdue_count"],
        "overdue_total_base": s["overdue_total_base"],
        "no_dated_bills": no_dated_bills,
    }


def _days(e: dict, today: date) -> int:
    return (date.fromisoformat(e["date"]) - today).days


def _row(e: dict, kind: str, sub: str, days: int) -> dict:
    return {
        "date": e["date"],
        "days": days,
        "kind": kind,
        "direction": "in" if kind in _INCOMING else "out",
        "label": e.get("label") or "",
        "sub": sub,
        "amount_base": e.get("amount_base"),
    }


# ── Currencies ──────────────────────────────────────────────────────────────────────────


def fx(pairs: list[str] | None = None, flipped: list[str] | None = None) -> dict:
    """Each held currency against the base, then each watched `B/Q` pair, with its live cross
    rate already taken (and turned round for a pair the user flipped). A rate with no live
    quote is `None` — never a guess."""
    from backend.core.fx import base_currency
    from backend.services import portfolio_analytics
    from backend.services.prices import fetch_live

    base = base_currency()
    exposure = portfolio_analytics.portfolio_analytics(None, None).get("fx_exposure") or []
    held = {e["currency"]: e["value"] for e in exposure}
    candidates = [(c, base, True) for c in held] + [
        (b, q, False) for b, q in (_pair(p) for p in pairs or []) if b and q
    ]
    seen: set[str] = set()
    chosen: list[tuple[str, str, bool]] = []
    for b, q, is_held in candidates:
        key = f"{b}/{q}"
        if b != q and key not in seen:
            seen.add(key)
            chosen.append((b, q, is_held))
    wanted = sorted({c for b, q, _ in chosen for c in (b, q) if c != base})
    quotes: dict = {}
    error = None
    if wanted:
        try:
            quotes = fetch_live([], wanted, allow_stale=True).get("fx") or {}
        except Exception as exc:  # a dead price source is "no rate", not a failed panel
            error = str(exc) or "Couldn't fetch live rates"
    flip = set(flipped or [])
    rows = []
    for b, q, is_held in chosen:
        key = f"{b}/{q}"
        frm, to = (q, b) if key in flip else (b, q)
        rows.append(
            {
                "key": key,
                "from": frm,
                "to": to,
                "held": is_held,
                **_held(held.get(b) if is_held else None),
                "rate": _cross(frm, to, base, quotes),
            }
        )
    return {"base_currency": base, "rows": rows, "error": error if not quotes else None}


def _held(net: float | None) -> dict:
    """What the book holds in a currency, net of what it owes in it: a net-negative position (an
    overdrawn wallet, a loan in that currency) is owed, never a negative amount "held"."""
    if net is None:
        return {"held_state": None, "held_amount_base": None}
    return {"held_state": "owed" if net < 0 else "held", "held_amount_base": _r2(abs(net))}


def _pair(raw: str) -> tuple[str, str]:
    parts = [p.strip().upper() for p in str(raw).split("/")]
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def _cross(frm: str, to: str, base: str, quotes: dict) -> float | None:
    """Units of `to` per one `frm`; each quote is base units per one unit of its currency."""
    rf = 1.0 if frm == base else quotes.get(frm)
    rt = 1.0 if to == base else quotes.get(to)
    if not rf or not rt:
        return None
    return float(rf) / float(rt)


# ── Monthly report ──────────────────────────────────────────────────────────────────────


def report(
    months: int = 6,
    merchant_limit: int = 10,
    period: str | None = None,
    tags: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict:
    """`GET /api/reports/rollup`'s answer plus what the report widgets used to work out:
    `spending` — the month (or period) the spending widgets show, with each category's share of
    it, and whether it fell back to an earlier month because nothing is spent yet this month;
    `savings_view` — the savings figure the gauge shows, with the gap when more went out than
    came in."""
    from backend.services import reports_rollup

    data = reports_rollup.reports_aggregates(
        months=months,
        merchant_limit=merchant_limit,
        period=period,
        tags=tags,
        categories=categories,
    )
    return {**data, "spending": _spending(data), "savings_view": _savings(data)}


def _spending(data: dict) -> dict:
    p = data.get("period")
    if p:
        point, frm, to, fell_back, merchants = p, p["from"], p["to"], False, p.get("merchants")
    else:
        ym = data.get("spending_month")
        point = next((x for x in data.get("points") or [] if x["ym"] == ym), None)
        point = point or data.get("current") or {}
        frm = to = point.get("ym")
        fell_back = ym != data.get("current_month")
        merchants = data.get("merchants")
    total = float(point.get("expense_total") or 0.0)
    rows = [
        {**r, "pct": _r2(r["amount"] / total * 100) if total else 0.0}
        for r in point.get("expense_rows") or []
    ]
    return {
        "from": frm,
        "to": to,
        "fell_back": fell_back,
        "spending_month": data.get("spending_month"),
        "current_month": data.get("current_month"),
        "expense_rows": rows,
        "expense_total": _r2(total),
        "merchants": merchants or [],
    }


def _savings(data: dict) -> dict | None:
    period = data.get("period") or {}
    s = period.get("savings") or data.get("savings")
    if not s:
        return None
    gap = _r2(s["spent"] - s["income"]) if s.get("spent", 0) > s.get("income", 0) else 0.0
    # The trailing average is the saving habit, the same whatever the period.
    trailing = (data.get("savings") or {}).get("trailing")
    if trailing and trailing.get("rate_pct") is not None:
        trailing = {**trailing, "rate_pct_rounded": round(trailing["rate_pct"])}
    return {
        **s,
        "trailing": trailing,
        "rate_pct_rounded": round(s.get("rate_pct") or 0),
        "verdict": "overspent" if gap > 0 else "saved",
        "overspent": gap,
    }


# ── Crossover ───────────────────────────────────────────────────────────────────────────


def crossover() -> dict:
    """When passive income covers baseline spending. The headline is decided here: "now" when it
    already covers it, the projected FIRE year while the FIRE number is still ahead, else "not
    yet". The FIRE projection reads the book's own net worth — never a figure the browser
    rounded and sent back."""
    from backend.services import cashflow_projections

    c = cashflow_projections.projections()["crossover"]
    out = {
        **c,
        "coverage_pct_rounded": round(c.get("coverage_pct") or 0),
        "coverage_bar_pct": max(0, min(100, round(c.get("coverage_pct") or 0))),
        "headline": "not_yet",
        "year": None,
        "projected": False,
    }
    if not c.get("known"):
        return out
    if (c.get("coverage_pct") or 0) >= 100:
        return {**out, "headline": "now"}
    fire = fire_for_book()
    years = fire.get("years_to_fire") if fire else None
    if fire and fire.get("progress_pct", 100) < 100 and years:
        return {
            **out,
            "headline": "year",
            "year": user_today().year + round(years),
            "projected": True,
        }
    return out


def fire_for_book() -> dict | None:
    """`GET /api/fire` for the book's own net worth (the Dashboard's `net_worth` figure). None
    for a book at or below zero, where there is nothing to project."""
    from backend.services import ledger
    from backend.services.fire import _load_profile, compute_fire, derive_fire_inputs

    nw = float(ledger.net_worth().get("net_worth") or 0.0)
    if nw <= 0:
        return None
    profile = _load_profile()
    return compute_fire(current_nw=round(nw), **derive_fire_inputs(profile))


# ── Spending pace ───────────────────────────────────────────────────────────────────────


def spending_pace(categories: list[str] | None = None) -> dict:
    """This month's envelopes for the pace widget: the ones picked by name, else the top
    spenders, each with its pace state already judged — "over" when the envelope is overspent,
    "ahead" when its spent share runs past the month's elapsed share by `PACE_TOLERANCE`, else
    "on_pace"."""
    from backend.services import planning_summary

    s = planning_summary.budget_aggregates(None)
    budgeted = s["envelopes"]["budgeted_rows"]
    picked = [c for c in categories or [] if c]
    by_label = {r["label"]: r for r in budgeted}
    matched = [by_label[c] for c in picked if c in by_label]
    unmatched: list[str] = []
    if not matched:
        matched = sorted(budgeted, key=lambda r: r["spent"], reverse=True)[:TOP_SPENDERS]
        unmatched = picked
    pace = float(s["pace_pct"])
    rows = [
        {
            **r,
            "state": _pace_state(r, pace),
            "fill_pct_rounded": round(r["fill_pct"]),
            "bar_pct": min(100.0, float(r["fill_pct"])),
        }
        for r in matched
    ]
    return {
        "month": s["month"],
        "day_of_month": s["day_of_month"],
        "days_in_month": s["days_in_month"],
        "pace_pct": pace,
        "pace_pct_rounded": round(pace),
        "tick_pct": min(100, round(pace)),
        "rows": rows,
        "unmatched": unmatched,
    }


def _pace_state(row: dict, pace_pct: float) -> str:
    if row.get("over"):
        return "over"
    if float(row["fill_pct"]) > pace_pct * PACE_TOLERANCE:
        return "ahead"
    return "on_pace"


# ── FIRE projection ─────────────────────────────────────────────────────────────────────


def fire() -> dict:
    """The FIRE card: `GET /api/fire` for the book's own net worth, with the figures the card
    prints already rounded. `state` says why there is nothing to project: no positive net worth
    yet, or no retirement spending set."""
    result = fire_for_book()
    if result is None:
        return {"state": "no_net_worth"}
    if (result.get("monthly_retirement_spending") or 0) <= 0:
        return {"state": "no_spending"}
    age = result.get("projected_fire_age")
    years = result.get("years_to_fire")
    return {
        **result,
        "state": "ok",
        "fi_age_rounded": round(age) if age is not None else None,
        "years_to_fire_rounded": round(years, 1) if years is not None else None,
    }
