"""
Commitments funding engine (RFC Phase 2) — the "can I cover the next installment,
or should I sell/borrow?" brain.

Stateless functions over CommitmentsStore + the ledger + live position values. All the
correctness subtleties live here (and are unit-tested in tests/test_commitments_service.py):

  - Funding-source split: `cash`/`income` are baseline-backed (already in the projection)
    and are NEVER emitted as inflow events; only NEW-MONEY (`asset_sale`/`loan`/`windfall`)
    becomes a dated inflow. This dissolves double-counting. Applies to `projected_events()`
    (the household Cash Flow overlay) ONLY — see the next bullet for `coverage()`'s own verdict.
  - EVERY funding source (asset_sale included) is valued from its own stored amount+currency —
    the frozen plan, never live-priced (owner decision 2026-07-26; see `live_source_value`).
  - Coverage runs a CASH-ONLY projection (include_classes=∅) so an asset_sale of an asset class
    can't be double-counted against that class also sitting in the starting balance.
  - "Paid" is decided by `_is_paid()` EVERYWHERE — ledger-anchored (`paid_txn_id`) or the bare
    `status == "paid"` flag the UI's "Mark paid" action sets — so `upcoming_events()`,
    `projected_events()` and `_plan_fidelity_events()` can never disagree on which installments
    are still outflows.
  - "Arrived" is decided by `funding_arrived()` EVERYWHERE — a `realized_txn_id` or the
    `status == "done"` flag — so the readers of "money still to come" can never disagree on
    which funding rows are still to come (ADR 2026-10-14-one-definition-per-fact-across-screens).
  - auto_pay / goal commitments are trackers — they emit no projection events.
  - `coverage()`'s PER-COMMITMENT verdict (`commitments[].at_risk/shortfall_base/
    earliest_shortfall_date`) is PLAN-FIDELITY ONLY (2026-07-31 redesign, owner decision after
    tracing a mislabeled badge, refined same-day after the owner flagged that anchoring on
    today's REAL cash let unrelated household debt (e.g. margin balances) leak into a
    single-commitment verdict): starts at $0, NOT today's real cash — THIS commitment's own
    dated events only — every funding_plan entry (ALL types, income/set-aside included) as
    inflows, unpaid installments as outflows — nothing else. No household income/expense
    modeling, no historical-average override, no household cash position at all, floored at $0
    (this commitment's own plan going upside-down), never the household `low_balance_threshold`.
    A prior installment's funding is consumed by that installment first — only the remainder
    carries forward — so this is a running balance, not a per-installment allocation table. The
    PORTFOLIO-level fields (`total_shortfall_base`,
    `min_balance_base`, `timeline`) are UNCHANGED — still the full household-blended
    projection, floor-aware. See `_plan_fidelity_verdict` and
    docs/adr/2026-07-31-commitment-coverage-plan-fidelity.md.

See docs/rfc/commitments-and-funding-planning.md.
"""

from __future__ import annotations

from datetime import date

from backend.core.user_time import user_today
from backend.db import read_cache
from backend.db.commitments_store import CommitmentsStore
from backend.services import ledger as svc

# kinds whose installments are real, lumpy, NOT-in-the-baseline outflows → projected.
_PROJECTED_KINDS = {"purchase", "loan_repayment"}
# funding source types that are genuinely NEW money (not already in the projection baseline).
_NEW_MONEY_TYPES = {"asset_sale", "loan", "windfall"}

_TODAY = None  # test seam

# The ONE default look-ahead for the portfolio funding verdict. The Commitments tab's horizon
# toggle starts here and the Overview "Funding shortfall" flag reads it unparameterised, so the
# two screens quote the same figure for the same book unless the user widens the toggle.
COVERAGE_HORIZON_MONTHS = 12

# The ONE "due soon" window. The Dashboard's Next-30-days card, the Commitments masthead's
# "Due in 30 days" tile and the daily briefing all read `due_summary()` at this width.
DUE_WINDOW_DAYS = 30


def _is_paid(inst: dict) -> bool:
    """Single "is this installment paid" test, shared by every projection path
    (`upcoming_events`, `projected_events`, `_plan_fidelity_events`) so they cannot drift
    apart again: a ledger-anchored payment (`paid_txn_id`) OR the bare `status == "paid"` flag
    the UI's "Mark paid" action sets (it never writes `paid_txn_id` — only the separate
    link-transaction path does)."""
    return bool(inst.get("paid_txn_id")) or inst.get("status") == "paid"


is_paid = _is_paid


def funding_arrived(source: dict) -> bool:
    """Single "has this funding money arrived" test, the funding twin of `_is_paid`: a
    ledger-anchored receipt (`realized_txn_id`) OR the bare `status == "done"` flag Lucius's
    `update_funding_source` sets. Arrived money is already in the ledger, so every reader of
    "money still to come" (`upcoming_events`, `projected_events`, `funding_source_overdue`)
    skips it rather than count it a second time. `_plan_fidelity_events` keeps it: that trace
    starts from $0, so the arrived money is the plan's own, not a second copy."""
    return bool(source.get("realized_txn_id")) or source.get("status") == "done"


def _today() -> str:
    return _TODAY or user_today().isoformat()


def _ym(date_str: str) -> str:
    return (date_str or "")[:7]


def live_source_value(source: dict, base: str, rates: dict) -> float:
    """Base-currency value a funding source contributes — ALWAYS from its own stored
    amount+currency (the frozen plan), never live-priced. asset_sale sources store a real
    amount+currency too (the target the user actually intends to raise), exactly like a fixed
    source (cash/income/loan/windfall) — sell_quantity is informational context only, not the
    value source. The plan must not silently drift with the market unless the user
    intentionally edits it (owner decision, 2026-07-26)."""
    amt = source.get("amount")
    if amt in (None, ""):
        return 0.0
    return svc.to_base(float(amt), (source.get("currency") or base), base, rates)


# ── Funding execution state ───────────────────────────────────────────────────


def funding_source_overdue(source: dict, today: str | None = None) -> bool:
    """Has this funding row's date passed without the money being marked as arrived?

    DERIVED, never stored. `FundingSource.status` holds only `planned`/`done` — a stored
    "missed" is correct only if something sweeps the table every day, which is exactly how
    `Installment.status` goes stale. This is right the moment the clock passes the date.

    A disabled source ("not happening" pulled it from the plan) is never overdue: it has been
    consciously removed, not silently missed. An arrived source (`funding_arrived`) is not
    overdue either: the money is here. An undated source cannot be placed on a trace at
    all, which mirrors `projected_events()`'s existing "no ym → no event" handling.
    """
    if source.get("disabled") or funding_arrived(source):
        return False
    available = source.get("available_date")
    if not available:
        return False
    return str(available)[:10] < (today or _today())


# ── Projection events (installments out, new-money in) ─────────────────────────


def projected_events(base: str | None = None, rates: dict | None = None) -> dict:
    """{year_month: [event,…]} for overlay into services.cashflow.build_projection.

    Outflows: unpaid (`_is_paid()` false) installments of purchase/loan_repayment commitments.
    Inflows:  NEW-MONEY funding sources on their available_date.
    cash/income sources and auto_pay/goal commitments emit nothing (already in the baseline)."""
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()

    events: dict = {}

    def add(ym: str, ev: dict):
        events.setdefault(ym, []).append(ev)

    for c in CommitmentsStore().query_commitments(status="active"):
        if c.get("kind") not in _PROJECTED_KINDS:
            continue
        ccy = c.get("currency") or base
        for inst in c.get("installments", []):
            if _is_paid(inst):  # paid (ledger-anchored or status flag) → cash already left
                continue
            ym = _ym(inst.get("due_date"))
            if not ym:
                continue
            add(
                ym,
                {
                    "label": f"{c['name']} — {inst.get('label', '')}".strip(" —"),
                    "category": "Commitment",
                    "direction": "out",
                    "amount_base": svc.to_base(float(inst.get("amount") or 0), ccy, base, rates),
                    "note": "",
                    "status": "committed",
                    "commitment_id": c["id"],
                },
            )
        for src in c.get("funding_plan", []):
            if src.get("disabled"):  # "not happening" pulled this source — no longer counted
                continue
            if funding_arrived(src):  # already in the ledger — projecting it again counts it twice
                continue
            if src.get("type") not in _NEW_MONEY_TYPES:
                continue
            ym = _ym(src.get("available_date"))
            if not ym:
                continue
            add(
                ym,
                {
                    "label": f"{c['name']} — {src.get('label', '')}".strip(" —"),
                    "category": "Funding",
                    "direction": "in",
                    "amount_base": live_source_value(src, base, rates),
                    "note": "",
                    "status": "funding",
                    "commitment_id": c["id"],
                },
            )
    return events


# ── Earmark reconciliation (cash earmarks vs real liquid balance) ──────────────


def earmark_reconciliation(base: str | None = None, rates: dict | None = None) -> dict:
    """Σ cash earmarks across all active commitments vs actual liquid balance.
    over_committed = you've promised more cash than you hold."""
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    earmarked = 0.0
    for c in CommitmentsStore().query_commitments(status="active"):
        for src in c.get("funding_plan", []):
            if src.get("disabled"):
                continue
            if src.get("type") == "cash" and src.get("amount") not in (None, ""):
                earmarked += svc.to_base(
                    float(src["amount"]), src.get("currency") or base, base, rates
                )
    from backend.services.cashflow import cash_balance_base

    liquid = cash_balance_base(base)
    return {
        "earmarked_base": round(earmarked, 2),
        "liquid_base": round(liquid, 2),
        "over_committed": round(earmarked, 2) > round(liquid, 2),
        "excess_base": round(max(0.0, earmarked - liquid), 2),
    }


# ── Plan vs actual: detect ledger payments not yet linked ──────────────────────


def detect_unmarked_payments(
    base: str | None = None,
    rates: dict | None = None,
    amount_tol: float = 0.02,
    day_window: int = 7,
) -> list[dict]:
    """Ledger transactions that look like an installment payment (base amount within
    `amount_tol`, date within `day_window` of due_date) but aren't linked via paid_txn_id."""
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    txns = [t for t in svc.transactions.all() if not t.get("deleted")]
    linked = {
        i.get("paid_txn_id")
        for c in CommitmentsStore().all()
        for i in c.get("installments", [])
        if i.get("paid_txn_id")
    }

    def parse(d):
        try:
            return date.fromisoformat(d[:10])
        except (TypeError, ValueError):
            return None

    suggestions: list[dict] = []
    for c in CommitmentsStore().query_commitments(status="active"):
        if c.get("kind") not in _PROJECTED_KINDS:
            continue
        ccy = c.get("currency") or base
        for inst in c.get("installments", []):
            if inst.get("paid_txn_id"):
                continue
            due = parse(inst.get("due_date"))
            if due is None:
                continue
            target = svc.to_base(float(inst.get("amount") or 0), ccy, base, rates)
            if target <= 0:
                continue
            for t in txns:
                if t.get("id") in linked:
                    continue
                # Recompute the paid amount into the current base from the native leg
                # (canonical path, like the rest of this module) — a stale stored
                # base_amount would mismatch the freshly recomputed target after a
                # base-currency change.
                paid = svc.txn_base_amount(t, base, rates)
                if paid <= 0:
                    continue
                if abs(paid - target) / target > amount_tol:
                    continue
                td = parse(t.get("date"))
                if td is None or abs((td - due).days) > day_window:
                    continue
                suggestions.append(
                    {
                        "commitment_id": c["id"],
                        "commitment_name": c["name"],
                        "installment_id": inst["id"],
                        "installment_label": inst.get("label", ""),
                        "txn_id": t.get("id"),
                        "txn_date": t.get("date"),
                        "amount_base": round(paid, 2),
                    }
                )
                break  # one match per installment
    return suggestions


# ── Loan amortization (Phase 3: loan-as-commitment) ─────────────────────────────


def _add_months(iso_date: str, n: int) -> str:
    d = date.fromisoformat(iso_date[:10])
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    # clamp day to the target month's length
    from calendar import monthrange

    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def amortization_schedule(
    principal: float,
    annual_rate_pct: float,
    months: int,
    start_date: str,
    label_prefix: str = "Payment",
) -> tuple[list[dict], float]:
    """Standard fixed-payment amortization → (installments, monthly_payment).

    Deterministic (not LLM arithmetic) so a modelled loan's payback is exact. Each installment
    is an outflow of the level payment, with principal/interest split in the description."""
    months = max(1, int(months))
    r = (annual_rate_pct or 0) / 100 / 12
    if r == 0:
        pmt = principal / months
    else:
        pmt = principal * r / (1 - (1 + r) ** -months)
    pmt = round(pmt, 2)
    schedule, bal = [], principal
    for i in range(months):
        interest = round(bal * r, 2)
        princ = round(pmt - interest, 2)
        bal = round(bal - princ, 2)
        schedule.append(
            {
                "label": f"{label_prefix} {i + 1}/{months}",
                "due_date": _add_months(start_date, i),
                "amount": pmt if i < months - 1 else round(pmt + bal, 2),  # absorb rounding in last
                "description": f"principal {princ:,.2f} + interest {interest:,.2f}",
            }
        )
    if schedule:  # final payment clears any residual balance
        schedule[-1]["amount"] = round(schedule[-1]["amount"], 2)
    return schedule, pmt


# ── Upcoming events/actions (cross-commitment, for Overview + reminders) ────────


def _funding_keys(commitment: dict, base: str, rates: dict) -> set[tuple[str, float]]:
    """(date, base amount) of every funding source on the commitment. A to-do carrying the same
    pair is a reminder of that money COMING IN ("loan disbursed", "set aside $X"), never an
    outflow of its own — the funding source is the one row that reports it."""
    return {
        (src["available_date"][:10], round(live_source_value(src, base, rates), 2))
        for src in commitment.get("funding_plan", [])
        if src.get("available_date")
    }


def upcoming_events(
    days: int = 60, base: str | None = None, rates: dict | None = None, include_overdue: bool = True
) -> list[dict]:
    """Flatten unpaid installments + pending actions + funding draws across all active
    commitments into one date-sorted feed within the window (overdue items included).
    Each: {commitment_id, commitment_name, commitment_kind, kind, label, category, date,
    amount_base, status} plus the addressable row id (installment_id / action_id) so the
    UI can patch it — commitment_kind lets it pick the honest control (tracker kinds get
    a bare mark-done; projected kinds need a ledger-linked payment)."""
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    today = date.fromisoformat(_today())

    def parse(d):
        try:
            return date.fromisoformat((d or "")[:10])
        except ValueError:
            return None

    out: list[dict] = []
    for c in CommitmentsStore().query_commitments(status="active"):
        ccy = c.get("currency") or base
        name = c["name"]
        ckind = c.get("kind")
        funded = _funding_keys(c, base, rates)
        for inst in c.get("installments", []):
            if _is_paid(inst):
                continue
            d = parse(inst.get("due_date"))
            if d is None:
                continue
            out.append(
                {
                    "commitment_id": c["id"],
                    "commitment_name": name,
                    "commitment_kind": ckind,
                    "kind": "installment",
                    "installment_id": inst.get("id"),
                    "label": inst.get("label", ""),
                    "category": "Payment",
                    "date": inst.get("due_date")[:10],
                    "amount_base": svc.to_base(float(inst.get("amount") or 0), ccy, base, rates),
                    "status": inst.get("status", "upcoming"),
                }
            )
        for a in c.get("actions", []):
            if a.get("status", "pending") != "pending":  # done, or "missed" (= not happening)
                continue
            d = parse(a.get("due_date"))
            if d is None:
                continue
            amt = a.get("amount")
            amount_base = (
                svc.to_base(float(amt), a.get("currency") or base, base, rates)
                if amt not in (None, "")
                else None
            )
            if amount_base is not None and (d.isoformat(), round(amount_base, 2)) in funded:
                continue
            out.append(
                {
                    "commitment_id": c["id"],
                    "commitment_name": name,
                    "commitment_kind": ckind,
                    "kind": "action",
                    "action_id": a.get("id"),
                    "label": a.get("label", ""),
                    "category": a.get("category", ""),
                    "date": a.get("due_date")[:10],
                    "amount_base": amount_base,
                    "status": a.get("status", "pending"),
                    "counterparty": a.get("counterparty", ""),
                }
            )
        for src in c.get("funding_plan", []):
            if src.get("disabled") or funding_arrived(src):
                continue
            if src.get("type") not in _NEW_MONEY_TYPES:
                continue
            d = parse(src.get("available_date"))
            if d is None:
                continue
            out.append(
                {
                    "commitment_id": c["id"],
                    "commitment_name": name,
                    "commitment_kind": ckind,
                    "kind": "funding",
                    "label": src.get("label", ""),
                    "category": src.get("type"),
                    "date": src.get("available_date")[:10],
                    "amount_base": live_source_value(src, base, rates),
                    "status": src.get("status") or "planned",
                }
            )

    horizon = today.toordinal() + max(0, days)
    feed = []
    for e in out:
        d = date.fromisoformat(e["date"])
        if d.toordinal() > horizon:
            continue
        if not include_overdue and d < today:
            continue
        feed.append(e)
    feed.sort(key=lambda e: e["date"])
    return feed


def due_summary(
    days: int = DUE_WINDOW_DAYS, base: str | None = None, rates: dict | None = None
) -> dict:
    """The ONE answer to "what is overdue" and "what is due in the next `days` days".

    - **Obligation** — money OUT of an active commitment: an unpaid installment (`_is_paid()`
      false) or a pending action (done and "not happening" actions are closed), whatever the
      commitment's kind. An auto-pay debit and a goal's set-aside are obligations too: they are
      cash leaving the household on a date, which is what a due-soon figure means. Funding
      draws are money coming IN and are never an obligation — nor is a to-do restating one
      (same date and amount, `_funding_keys`).
    - **Overdue** — an obligation dated before today. An action with no amount counts in the
      number and adds nothing to the total.
    - **Due soon** — an obligation dated today through today + `days`, inclusive.
    - A planned funding inflow whose date has passed is reported separately
      (`funding_overdue`), so "money that never arrived" never inflates the overdue count or
      its total.

    The Dashboard's Next-30-days card, the Commitments masthead, its "needs your attention"
    lane and the daily briefing all read this; none re-derives it."""
    today = date.fromisoformat(_today())
    overdue: list[dict] = []
    due: list[dict] = []
    funding_overdue: list[dict] = []
    funding_due: list[dict] = []
    for e in upcoming_events(days=days, base=base, rates=rates):
        late = date.fromisoformat(e["date"]) < today
        if e["kind"] == "funding":
            (funding_overdue if late else funding_due).append(e)
        else:
            (overdue if late else due).append(e)

    def total(events: list[dict]) -> float:
        return round(sum(float(e["amount_base"] or 0.0) for e in events), 2)

    return {
        "days": days,
        "overdue": overdue,
        "overdue_count": len(overdue),
        "overdue_total_base": total(overdue),
        "due": due,
        "due_count": len(due),
        "due_total_base": total(due),
        "funding_overdue": funding_overdue,
        "funding_due": funding_due,
    }


# ── Per-commitment plan-fidelity verdict ────────────────────────────────────────


def _plan_fidelity_events(commitment: dict, base: str, rates: dict) -> list[tuple[str, float]]:
    """This ONE commitment's own dated cash events — (date, signed base delta) pairs.

    Outflows: every unpaid (`_is_paid()` false) installment, on its due_date.
    Inflows: EVERY funding_plan entry, arrived or not (unlike `projected_events()`, type is NOT
    filtered — an income/set-aside source is the user's real plan and must count), on its
    available_date. Arrived money stays IN: this trace starts from $0, not from today's cash, so
    dropping a loan that arrived would read its own later installments as unfunded.
    An entry with no date can't be placed on the trace and is skipped (matches
    `projected_events()`'s existing "no ym → no event" handling)."""
    ccy = commitment.get("currency") or base
    events: list[tuple[str, float]] = []
    for inst in commitment.get("installments", []):
        if _is_paid(inst):
            continue
        due = inst.get("due_date")
        if not due:
            continue
        events.append((due[:10], -svc.to_base(float(inst.get("amount") or 0), ccy, base, rates)))
    for src in commitment.get("funding_plan", []):
        if src.get("disabled"):
            continue
        avail = src.get("available_date")
        if not avail:
            continue
        events.append((avail[:10], live_source_value(src, base, rates)))
    return events


def _plan_fidelity_verdict(
    commitment: dict, base: str, rates: dict
) -> tuple[bool, float, str | None]:
    """Walk this commitment's own events (see `_plan_fidelity_events`) starting from $0 — NOT
    today's household cash, which would let unrelated debt/balances (e.g. a margin account)
    leak into a single-commitment verdict. In date order, a same-day funding draw lands BEFORE
    a same-day installment (money should be available for the payment it's meant to cover), so
    a tie never reads as a false gap. Money a prior installment already consumed simply isn't in
    the running balance for the next one — this is a running balance, not a per-installment
    allocation table, so "was this funding already spent on an earlier payment" is answered by
    construction, not tracked separately. Events are applied one CALENDAR DATE at a time — every
    event dated e.g. 2028-09-30 is folded in before the balance is checked, so two outflows
    landing the same day (e.g. Handover + Maintenance) can never have the second one silently
    dropped just because the first alone already tipped the balance negative. Returns (at_risk,
    shortfall_base, earliest_shortfall_date) = the FIRST date the running balance goes negative
    — floored at $0, which is what makes the date and the dollar amount always describe the SAME
    date's events (the historical bug this replaces paired an installment's date with a
    different month's household-projection dollar figure)."""
    events = _plan_fidelity_events(commitment, base, rates)
    events.sort(key=lambda e: e[0])
    balance = 0.0
    i = 0
    while i < len(events):
        ev_date = events[i][0]
        while i < len(events) and events[i][0] == ev_date:
            balance += events[i][1]
            i += 1
        if balance < 0:
            return True, round(-balance, 2), ev_date
    return False, 0.0, None


# ── Coverage verdict (the headline) ─────────────────────────────────────────────


@read_cache.cached(
    lambda months=COVERAGE_HORIZON_MONTHS, base=None, rates=None, floor=None: (
        months,
        base,
        repr(rates),
        floor,
    )
)
def _spans_currencies(base: str, commitments: list[dict]) -> bool:
    """True when the verdict converts something at an exchange rate: an open account, a
    commitment or one of its funding sources is held in a currency other than the base. The
    shortfall then moves a little with every FX refresh, and the UI says "at today's exchange
    rates" instead of presenting the cents as fixed."""
    held = {(a.get("currency") or base).upper() for a in svc.accounts.query_accounts()}
    for c in commitments:
        held.add((c.get("currency") or base).upper())
        held.update((f.get("currency") or base).upper() for f in c.get("funding_plan", []))
    return bool(held - {base})


def coverage(
    months: int = COVERAGE_HORIZON_MONTHS,
    base: str | None = None,
    rates: dict | None = None,
    floor: float | None = None,
) -> dict:
    """Per-commitment + portfolio funding verdict. See module docstring for the invariants —
    in short, the two halves of this response measure DIFFERENT things and must not be
    conflated:

    - `commitments[].at_risk/shortfall_base/earliest_shortfall_date` (per-commitment) is
      PLAN-FIDELITY: starts at $0 (the household cash position, incl. unrelated debt like a
      margin balance, is deliberately EXCLUDED) + this commitment's own dated events only,
      floored at $0. `floor` has NO effect on these — see `_plan_fidelity_verdict`.
    - `at_risk`/`total_shortfall_base`/`min_balance_base`/`timeline` (portfolio-level) is the
      full household-blended cash-only projection (income/expense baseline + commitment
      overlay), floor-aware: "at risk" means the projected cushion dips below the CASH FLOOR
      (`low_balance_threshold`, or the caller's `floor` override), not merely below zero.
      `floor=0` recovers the prior negative-balance-only reading exactly.
    - Top-level `at_risk`/`earliest_shortfall_date` share the SAME household basis as
      `total_shortfall_base`/`min_balance_base` (first month the household timeline dips
      below `threshold`) — deliberately NOT folded from the per-commitment verdicts any more,
      so this whole portfolio block stays internally self-consistent (the exact class of bug
      this redesign fixes). "Which commitment is driving this" is a client-side concern now
      that the two bases differ — see `coverageEngine.ts`'s `deriveClientCoverage`.
    - `total_shortfall_base` is the DEEPEST gap to the floor anywhere in the `horizon_months`
      window, reached in `lowest_balance_month` — NOT the gap at `earliest_shortfall_date`
      (the FIRST month the cushion dips below the floor, usually far shallower). Every
      surface quoting the figure names both months and the window; a bare "short $X by <first
      dip>" paired the trough's dollars with the first dip's date."""
    import time as _time

    _perf_log = __import__("logging").getLogger(__name__)
    _t0 = _time.perf_counter()
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    events = projected_events(base, rates)
    _t1 = _time.perf_counter()

    from backend.services.cashflow import build_projection

    proj = build_projection(months=months, history_months=0, extra_events=events)
    _t2 = _time.perf_counter()
    bal_by_ym = {m["year_month"]: m["balance"] for m in proj["months"]}
    threshold = float(floor) if floor is not None else proj.get("low_balance_threshold", 0)

    portfolio_at_risk = False
    portfolio_earliest: str | None = None
    for ym in sorted(bal_by_ym):
        if bal_by_ym[ym] < threshold:
            portfolio_at_risk = True
            portfolio_earliest = f"{ym}-01"
            break

    commitments_out: list[dict] = []
    _active = CommitmentsStore().query_commitments(status="active")
    _t3 = _time.perf_counter()
    for c in _active:
        # Only _PROJECTED_KINDS are evaluated by the engine; goal/auto_pay are trackers it
        # has NO opinion on. assessed=False tells consumers "don't render a covered/at-risk
        # verdict" — at_risk=False alone is ambiguous.
        assessed = c.get("kind") in _PROJECTED_KINDS
        at_risk, worst, earliest = (
            _plan_fidelity_verdict(c, base, rates) if assessed else (False, 0.0, None)
        )
        commitments_out.append(
            {
                "id": c["id"],
                "assessed": assessed,
                "at_risk": at_risk,
                "shortfall_base": round(worst, 2),
                "earliest_shortfall_date": earliest,
            }
        )

    _t4 = _time.perf_counter()
    min_balance = min(bal_by_ym.values()) if bal_by_ym else 0.0
    lowest_month = min(bal_by_ym, key=lambda ym: (bal_by_ym[ym], ym)) if bal_by_ym else None
    recon = earmark_reconciliation(base, rates)
    _t5 = _time.perf_counter()
    _perf_log.info(
        "PERF coverage projected_events=%.1fms build_projection=%.1fms query_active=%.1fms "
        "per_commitment_loop=%.1fms(n=%d) reconciliation=%.1fms",
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        (_t3 - _t2) * 1000,
        (_t4 - _t3) * 1000,
        len(_active),
        (_t5 - _t4) * 1000,
    )
    timeline = [
        {
            "year_month": m["year_month"],
            "balance": m["balance"],
            "total_in": m["total_in"],
            "total_out": m["total_out"],
            # Recomputed against THIS call's threshold (the caller's `floor` override, when
            # given) — never trust `m["is_tight"]` as-is, since build_projection() always
            # derives it from the profile's stored threshold and would silently disagree with
            # a caller-supplied floor otherwise.
            "is_tight": 0 < m["balance"] < threshold,
        }
        for m in proj["months"]
    ]

    return {
        "base_currency": base,
        "at_risk": portfolio_at_risk,
        "earliest_shortfall_date": portfolio_earliest,
        "total_shortfall_base": round(max(0.0, threshold - min_balance), 2),
        "min_balance_base": round(min_balance, 2),
        "lowest_balance_month": lowest_month,
        "horizon_months": months,
        "low_balance_threshold": threshold,
        "over_committed": recon["over_committed"],
        "reconciliation": recon,
        "spans_currencies": _spans_currencies(base, _active),
        "commitments": commitments_out,
        "timeline": timeline,
    }


# ── Thin CRUD delegates (ai/tools must not import db directly) ─────────────────


def query_commitments(kind: str | None = None, status: str | None = None) -> list[dict]:
    return CommitmentsStore().query_commitments(kind=kind, status=status)


def get_commitment(commitment_id: str) -> dict | None:
    return CommitmentsStore().find_by_id(commitment_id)


def create_commitment(data: dict) -> dict:
    return CommitmentsStore().create(data)


def update_commitment(commitment_id: str, changes: dict) -> dict | None:
    return CommitmentsStore().update_commitment(commitment_id, changes)


def delete_commitment(commitment_id: str) -> bool:
    return CommitmentsStore().delete(commitment_id)


def _require_paid_txns(installments: list) -> None:
    """A ``paid_txn_id`` is what marks an installment paid (``_is_paid``), so it must name a
    live ledger transaction — a bogus id used to mark the installment paid and drop it from
    projected cash flow (2026-10-13 audit). RAISES ``ValidationError`` before any write."""
    refs = [i.get("paid_txn_id") for i in installments or [] if isinstance(i, dict)]
    refs = [r for r in refs if r]
    if not refs:
        return
    from backend.core.errors import ValidationError
    from backend.db.transaction_store import TransactionStore

    store = TransactionStore()
    for txn_id in refs:
        txn = store.find_by_id(str(txn_id))
        if txn is None or txn.get("deleted"):
            raise ValidationError(
                f"paid_txn_id {txn_id!r} is not a transaction in the ledger — link the "
                "transaction that paid it (get_transactions), or mark it paid without a link.",
                paid_txn_id=txn_id,
            )


def set_installments(commitment_id: str, installments: list) -> dict | None:
    _require_paid_txns(installments)
    return CommitmentsStore().set_installments(commitment_id, installments)


def update_installment(commitment_id: str, installment_id: str, changes: dict) -> dict | None:
    # Owner decision (2026-07-03): NO commitment→category coupling yet — auto-stamping the
    # synced budget category at the paid_txn_id link moment was built and then REVERTED;
    # it returns when commitments get properly implemented. Plain delegate until then.
    _require_paid_txns([changes])
    return CommitmentsStore().update_installment(commitment_id, installment_id, changes)


def set_funding_plan(commitment_id: str, funding_plan: list) -> dict | None:
    return CommitmentsStore().set_funding_plan(commitment_id, funding_plan)


def update_funding_source(commitment_id: str, funding_id: str, changes: dict) -> dict | None:
    return CommitmentsStore().update_funding_source(commitment_id, funding_id, changes)


def add_funding_sources(commitment_id: str, sources: list) -> dict | None:
    return CommitmentsStore().add_funding_sources(commitment_id, sources)


def remove_funding_sources(commitment_id: str, funding_ids: list) -> tuple[dict | None, list[str]]:
    return CommitmentsStore().remove_funding_sources(commitment_id, funding_ids)


def add_installments(commitment_id: str, installments: list) -> dict | None:
    _require_paid_txns(installments)
    return CommitmentsStore().add_installments(commitment_id, installments)


def remove_installments(
    commitment_id: str, installment_ids: list
) -> tuple[dict | None, list[str], list[str]]:
    return CommitmentsStore().remove_installments(commitment_id, installment_ids)


def add_actions(commitment_id: str, actions: list) -> dict | None:
    return CommitmentsStore().add_actions(commitment_id, actions)


def remove_actions(
    commitment_id: str, action_ids: list
) -> tuple[dict | None, list[str], list[str]]:
    return CommitmentsStore().remove_actions(commitment_id, action_ids)


def set_actions(commitment_id: str, actions: list) -> dict | None:
    return CommitmentsStore().set_actions(commitment_id, actions)


def update_action(commitment_id: str, action_id: str, changes: dict) -> dict | None:
    return CommitmentsStore().update_action(commitment_id, action_id, changes)


def set_loan(commitment_id: str, loan: dict | None) -> dict | None:
    return CommitmentsStore().set_loan(commitment_id, loan)
