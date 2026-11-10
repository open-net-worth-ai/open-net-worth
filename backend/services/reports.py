"""
reports_service — the report library (P4-RP1).

A report is a **COMPOSITION** of the existing read services — there is **no new money
math** here. Each builder reads `ledger` (net worth + account balances), `services/returns`
(period TWR/MWR), `services/lots` (realized P&L), `services/income` (dividends/interest) and
`services/budget_lens` (spending vs the book's home budget lens), then shapes a
structured, base-currency-normalized report model. `render_html` turns that model into a
clean, self-contained printable HTML document (inline `<style>`, a print stylesheet — no
external assets, no template engine).

Computed-not-stored (the CLAUDE.md invariant): nothing is persisted, every figure is read
live and FX-normalized to the current base currency by the underlying services. A report is a
read; there is no write choke point.

PDF is deliberately **not** generated server-side: the HTML carries a print stylesheet and the
UI tells the user to use the browser's "Print → Save as PDF" (no heavy/native dependency — see
the P4-RP1 brief). The document is fully styled for paper (A4 margins, table borders, no nav).

Dependency rule (api/ai -> services -> db -> core): imports only sibling services + core/db.
No `api`/`ai` import (Contract 3); pure read + string assembly.

Report types:
  * ``net_worth_statement``  — net worth as-of today: account balances (assets/liabilities) +
                               market positions + a period return block (TWR/MWR/$ gain).
  * ``realized_income``      — realized capital gains + dividend/interest income for a period:
                               per-instrument + by-term / by-kind splits, yield-on-cost.
  * ``spending_summary``     — the month's expense spending vs budget: per-category + by-source,
                               burn status, projected total.
  * ``cash_flow_statement``  — income vs expense over a period, grouped by category, with net
                               cash flow + savings rate (composed from the ledger via
                               ``ledger.txn_base_amount`` over the resolved window).
  * ``budget_vs_actual``     — per Planning category over the period's months: assigned vs
                               activity vs available, with group subtotals + totals (composed
                               from ``planning.budget_month`` — read-only, no apply path).

Design: P4-RP1 — Report library + export.
"""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, timezone

from backend.core.fx import base_currency, txn_split_base_amount
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.app_config_store import AppConfigStore
from backend.services import budget_lens as budget_lens_service
from backend.services import categories as categories_service
from backend.services import income as income_service
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service
from backend.services import planning as planning_service
from backend.services import returns as returns_service
from backend.services.budget_engines import base as budget_engines_base
from backend.services.budget_engines import service as budget_engines_service
from backend.services.fx_cache import load_fx_cache

_log = logging.getLogger(__name__)

# The report catalog — the single source the API list endpoint + the AI tool enum read from.
# A new report = one entry here + one builder in `_BUILDERS`.
REPORT_TYPES: tuple[dict, ...] = (
    {
        "type": "net_worth_statement",
        "title": "Net-worth statement",
        "description": (
            "A point-in-time statement of net worth: account balances (assets and "
            "liabilities) plus market positions, with a period return block."
        ),
        "uses_period": True,
    },
    {
        "type": "realized_income",
        "title": "Realized gains & income summary",
        "description": (
            "Realized capital gains and dividend/interest income over a period — by "
            "instrument, by holding-term, and by income kind, with yield-on-cost."
        ),
        "uses_period": True,
    },
    {
        "type": "spending_summary",
        "title": "Spending summary",
        "description": (
            "The current month's expense spending versus budget — by category and by "
            "account, with burn status and a projected month-end total."
        ),
        "uses_period": False,
    },
    {
        "type": "cash_flow_statement",
        "title": "Cash-flow statement",
        "description": (
            "Income versus expense over a period — grouped by category — with net cash flow "
            "and the savings rate."
        ),
        "uses_period": True,
    },
    {
        "type": "budget_vs_actual",
        "title": "Budget vs actual",
        "description": (
            "Per Planning category over the period's months: assigned versus activity versus "
            "available, with group subtotals and totals."
        ),
        "uses_period": True,
    },
)

# Periods the period-driven reports accept (mirrors returns_service.PERIODS).
PERIODS: tuple[str, ...] = returns_service.PERIODS


def list_reports() -> list[dict]:
    """The available report types (catalog) — what `GET /api/reports` and the tool enum read."""
    return [dict(r) for r in REPORT_TYPES]


def _catalog_entry(report_type: str) -> dict | None:
    return next((r for r in REPORT_TYPES if r["type"] == report_type), None)


# ── Report builders (compose existing read services; no money math here) ─────────────


def _net_worth_statement(period: str, base: str) -> dict:
    """As-of net worth: account balances split by kind + market positions + a return block."""
    nw = ledger_service.net_worth(base)
    txns = ledger_service.transactions.all()
    accts = AccountStore().query_accounts()
    idx = ledger_service.opening_index(txns, accts)

    assets: list[dict] = []
    liabilities: list[dict] = []
    for a, hydrated in zip(accts, ledger_service.accounts_with_balances(accts, txns, idx=idx)):
        row = {
            "name": a.get("name") or "Account",
            "type": a.get("type"),
            "currency": a.get("currency") or base,
            "balance": round(hydrated.get("balance") or 0.0, 2),
            "balance_base": round(hydrated.get("balance_base") or 0.0, 2),
        }
        # liabilities are stored raw-negative; the net-worth fold cares only about `kind`.
        kind, _band = ledger_service.account_kind_and_band(a)
        (liabilities if kind == "liability" else assets).append(row)

    # Sort largest-first within each band so the heaviest lines read at the top.
    assets.sort(key=lambda r: r["balance_base"], reverse=True)
    liabilities.sort(key=lambda r: r["balance_base"])

    returns_block = returns_service.portfolio_returns(period)
    returns_block.pop("_value_pts", None)
    returns_block.pop("_flows", None)

    return {
        "net_worth": nw["net_worth"],
        "breakdown": nw["breakdown"],
        "assets": assets,
        "liabilities": liabilities,
        "returns": returns_block,
    }


def _realized_income(period: str, base: str) -> dict:
    """Realized gains + income over the resolved (start, end) window."""
    start, end = returns_service.resolve_period(period)
    realized = lots_service.realized_pnl(date_from=start, date_to=end, base=base)
    income = income_service.income_summary(date_from=start, date_to=end, base=base)
    return {
        "start": start,
        "end": end,
        "realized": realized,
        "income": income,
    }


def _spending_summary(period: str, base: str) -> dict:
    """The current month's expense-vs-budget summary (the period selector is N/A — always the
    current month; we surface that fact in the report meta).

    Sourced from the book's HOME budget lens (`budget_lens.home_spending_summary`) —
    RFC 2026-08-22-pluggable-budget-engines.md §8.1. This used to read
    `services.budgets.spending_summary`, an unrelated per-category-LIMIT system the seven
    Overview widgets never agreed with; both now read the same reality."""
    today = user_today()
    summary = budget_lens_service.home_spending_summary(
        month=today.month, year=today.year, base=base
    )
    return {"summary": summary}


def _months_in_window(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM strings spanning [start, end] (ISO date inputs).

    Bounded by construction (a period never resolves to more than the ALL window); a defensive
    cap keeps a pathological start from looping unboundedly."""
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out: list[str] = []
    cy, cm = sy, sm
    while (cy, cm) <= (ey, em) and len(out) < 1200:
        out.append(f"{cy:04d}-{cm:02d}")
        cy, cm = (cy + 1, 1) if cm == 12 else (cy, cm + 1)
    return out


def _category_label_map() -> dict[str, str]:
    """``{category_id: label}`` for every category, loaded ONCE per report — mirrors
    ``cashflow._category_label_map`` (same shape, built from the shared
    ``services.categories`` read path rather than a fresh store lookup)."""
    return {r["id"]: r.get("label") for r in categories_service.all_category_rows()}


def _category_label(cat: str, label_map: dict[str, str]) -> str:
    """Human label for a stored category id (mirrors ``cashflow._category_label``). An id with
    no store row — the synthetic "unsorted" bucket, or an unrecognized key — falls back to a
    title-cased rendering of the key itself, never the raw id."""
    label = label_map.get(cat)
    if label and str(label).strip():
        return str(label)
    if cat == "unsorted":
        return "Uncategorized"
    return cat.replace("_", " ").title()


def _cash_flow_statement(period: str, base: str) -> dict:
    """Income vs expense over the resolved window, grouped by category, with net + savings rate.

    Composed from the ledger: each non-deleted income/expense leg is valued in the CURRENT base
    via ``ledger.txn_base_amount`` (the canonical money function — survives a base change; never
    the stored snapshot). A split leg is expanded into its own lines via
    ``core.fx.txn_split_base_amount`` (same primitive ``planning._iter_category_contributions``
    uses) so the breakdown reflects each split's OWN category instead of lumping the whole leg
    under the "split" sentinel — the totals below are unaffected either way, since a split's
    lines always sum back to the leg's ``txn_base_amount``. Transfers/adjustments are net-zero
    P&L and excluded. No new money math: the per-leg/per-line conversion is the existing service
    primitive; this is a fold + grouping."""
    start, end = returns_service.resolve_period(period)
    rates = load_fx_cache()
    label_map = _category_label_map()

    income_by_cat: dict[str, float] = {}
    expense_by_cat: dict[str, float] = {}
    total_income = total_expense = 0.0
    for t in ledger_service.transactions.all():
        if t.get("deleted"):
            continue
        d = t.get("date") or ""
        if not (start <= d[:10] <= end):
            continue
        kind = t.get("kind")
        if kind not in ("income", "expense"):
            continue
        by_cat = income_by_cat if kind == "income" else expense_by_cat
        splits = t.get("splits")
        lines = (
            [(s.get("category"), txn_split_base_amount(t, s, base, rates)) for s in splits]
            if splits
            else [(t.get("category"), ledger_service.txn_base_amount(t, base, rates))]
        )
        for cat, amt in lines:
            cat = cat or "unsorted"
            by_cat[cat] = by_cat.get(cat, 0.0) + amt
            if kind == "income":
                total_income += amt
            else:
                total_expense += amt

    def _rows(by_cat: dict[str, float]) -> list[dict]:
        return [
            {
                "category": cat,
                "category_label": _category_label(cat, label_map),
                "amount": round(amt, 2),
            }
            for cat, amt in sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)
        ]

    net = total_income - total_expense
    # Savings rate = net / income (only meaningful when there's income in the window).
    savings_rate_pct = (net / total_income * 100) if total_income > 0 else None

    return {
        "start": start,
        "end": end,
        "income_total": round(total_income, 2),
        "expense_total": round(total_expense, 2),
        "net": round(net, 2),
        "savings_rate_pct": round(savings_rate_pct, 1) if savings_rate_pct is not None else None,
        "income_by_category": _rows(income_by_cat),
        "expense_by_category": _rows(expense_by_cat),
    }


def _budget_vs_actual(period: str, base: str, engine_id: str | None = None) -> dict:
    """Per Planning category over the period's months: assigned vs activity vs available.

    Engine-parameterized (RFC 2026-08-22-pluggable-budget-engines.md Decision G / Phase 4):
    defaults to the book's HOME lens instead of hardcoding zero-based. The zero-based path
    (`_budget_vs_actual_zero_based`) is UNCHANGED — composed PURELY from
    ``planning.budget_month`` (the read table), no write/apply path touched, exactly as
    before this RFC, so its numbers/shape never move. A different home engine folds through
    its own ``ViewModel`` instead (`_budget_vs_actual_generic`)."""
    resolved_engine_id = engine_id or budget_engines_base.home_engine_id()
    start, end = returns_service.resolve_period(period)
    months = _months_in_window(start, end)
    if not months:
        months = [end[:7]]

    if resolved_engine_id == "zero_based":
        return _budget_vs_actual_zero_based(start, end, months)
    return _budget_vs_actual_generic(start, end, months, resolved_engine_id)


def _budget_vs_actual_zero_based(start: str, end: str, months: list[str]) -> dict:
    """Composed PURELY from ``planning.budget_month`` (the read table) — no write/apply path is
    touched. Assigned + Activity are SUMMED across every month in the window (both are flows);
    Available is taken from the LATEST month's table (a running envelope balance, not additive),
    and the group structure follows that latest month. An empty/young book → empty groups +
    zero totals (``budget_month`` already degrades to zeros, never raises)."""
    # Accumulate per-category assigned/activity across the window; the latest table seeds
    # available + the group layout.
    acc: dict[str, dict] = {}
    latest = planning_service.budget_month(months[-1])
    for m in months:
        table = latest if m == months[-1] else planning_service.budget_month(m)
        for g in table.get("groups", []):
            for c in g.get("categories", []):
                key = c["key"]
                entry = acc.setdefault(key, {"assigned": 0.0, "activity": 0.0})
                entry["assigned"] += c.get("assigned") or 0.0
                entry["activity"] += c.get("activity") or 0.0

    groups_out: list[dict] = []
    total_assigned = total_activity = total_available = 0.0
    for g in latest.get("groups", []):
        cat_rows: list[dict] = []
        g_assigned = g_activity = g_available = 0.0
        for c in g.get("categories", []):
            key = c["key"]
            a = acc.get(key, {"assigned": 0.0, "activity": 0.0})
            assigned = round(a["assigned"], 2)
            activity = round(a["activity"], 2)
            available = round(c.get("available") or 0.0, 2)
            cat_rows.append(
                {
                    "key": key,
                    "label": c.get("label") or key,
                    "assigned": assigned,
                    "activity": activity,
                    "available": available,
                }
            )
            g_assigned += assigned
            g_activity += activity
            g_available += available
        groups_out.append(
            {
                "name": g.get("name", ""),
                "categories": cat_rows,
                "subtotal": {
                    "assigned": round(g_assigned, 2),
                    "activity": round(g_activity, 2),
                    "available": round(g_available, 2),
                },
            }
        )
        total_assigned += g_assigned
        total_activity += g_activity
        total_available += g_available

    return {
        "start": start,
        "end": end,
        "months": months,
        "groups": groups_out,
        "totals": {
            "assigned": round(total_assigned, 2),
            "activity": round(total_activity, 2),
            "available": round(total_available, 2),
        },
    }


def _budget_vs_actual_generic(start: str, end: str, months: list[str], engine_id: str) -> dict:
    """Same shape/window semantics as `_budget_vs_actual_zero_based`, generalized to any other
    engine's ``ViewModel``: every ``Row.measures`` key is summed across the window (a flow)
    under its own name. An engine with no per-category money pair (50/30/20) naturally
    produces empty category rows here — an honest gap, not a forced number."""
    views = [budget_engines_service.get_view(engine_id, m) for m in months]
    latest = views[-1]

    # Column labels come from the engine's own Measure.label — never hardcoded — so the
    # HTML renderer (`_render_budget_vs_actual`) needs no per-engine branch either; insertion
    # order follows each engine's own Row.measures order.
    measure_labels: dict[str, str] = {}
    acc: dict[str, dict[str, float]] = {}
    for vm in views:
        for group in vm.groups:
            for row in group.rows:
                entry = acc.setdefault(row.id, {})
                for measure in row.measures:
                    if measure.amount_base is None:
                        continue
                    measure_labels.setdefault(measure.key, measure.label)
                    entry[measure.key] = entry.get(measure.key, 0.0) + measure.amount_base

    groups_out: list[dict] = []
    totals: dict[str, float] = {}
    for group in latest.groups:
        cat_rows: list[dict] = []
        g_totals: dict[str, float] = {}
        for row in group.rows:
            values = {k: round(v, 2) for k, v in acc.get(row.id, {}).items()}
            cat_rows.append({"key": row.id, "label": row.label, **values})
            for k, v in values.items():
                g_totals[k] = g_totals.get(k, 0.0) + v
        g_totals = {k: round(v, 2) for k, v in g_totals.items()}
        groups_out.append({"name": group.label, "categories": cat_rows, "subtotal": g_totals})
        for k, v in g_totals.items():
            totals[k] = totals.get(k, 0.0) + v

    return {
        "start": start,
        "end": end,
        "months": months,
        "engine_id": engine_id,
        "measure_labels": measure_labels,
        "groups": groups_out,
        "totals": {k: round(v, 2) for k, v in totals.items()},
    }


_BUILDERS = {
    "net_worth_statement": _net_worth_statement,
    "realized_income": _realized_income,
    "spending_summary": _spending_summary,
    "cash_flow_statement": _cash_flow_statement,
    "budget_vs_actual": _budget_vs_actual,
}


def build_report(report_type: str, period: str = "1Y") -> dict:
    """Assemble a structured report model for `report_type` over `period`.

    Returns ``{type, title, description, period, base_currency, generated_at, app_name, data}``
    where ``data`` is the per-report composed payload. Raises ``ValueError`` on an unknown
    report type (the API/tool validate the enum upstream; this is the defensive last gate).
    An empty book degrades gracefully — the underlying services return zeroed totals, never
    raise — so the report renders with empty tables rather than 500ing.
    """
    entry = _catalog_entry(report_type)
    if entry is None:
        raise ValueError(f"unknown report type: {report_type}")

    base = base_currency()
    p = (period or "1Y").upper()
    if entry["uses_period"] and p not in PERIODS:
        p = "1Y"

    data = _BUILDERS[report_type](p, base)
    app_name = (AppConfigStore().get() or {}).get("app_name") or "Open Net Worth"

    return {
        "type": report_type,
        "title": entry["title"],
        "description": entry["description"],
        "uses_period": entry["uses_period"],
        "period": p if entry["uses_period"] else None,
        "base_currency": base,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_name": app_name,
        "data": data,
    }


# ── HTML rendering (self-contained printable document; no template engine) ───────────


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _money(amount, base: str) -> str:
    """Format a base-currency amount with thousands separators (e.g. ``SGD 1,234.56``).
    A None amount reads as ``—`` (unknown cost basis, not a fabricated 0)."""
    if amount is None:
        return "—"
    try:
        return f"{base} {float(amount):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}%"
    except (TypeError, ValueError):
        return "—"


def _section(title: str, body: str) -> str:
    return f"<section><h2>{_esc(title)}</h2>{body}</section>"


def _kv_row(label: str, value: str, *, strong: bool = False) -> str:
    cls = ' class="strong"' if strong else ""
    return f'<tr{cls}><th scope="row">{_esc(label)}</th><td class="num">{value}</td></tr>'


_ACCOUNT_CLASS_LABELS = {
    "cash": "Cash on hand",
    "fixed_deposit": "Fixed deposits",
    "settlement_cash": "Settlement cash",
    "retirement": "Retirement",
    "receivable": "Receivables",
}
_LIABILITY_CLASS_LABELS = {"credit": "Credit", "loan": "Loans"}


def _render_net_worth(data: dict, base: str) -> str:
    bd = data["breakdown"]
    # Split out of the lump "Total assets"/"Total liabilities" lines so this statement
    # never reads as if a real-estate/receivable/settlement-cash account were plain
    # cash, or as if every liability type carried the same rate (ADR
    # 2026-07-31-liquid-net-worth-bands; mirrors ai/prompts.py's system-prompt snapshot).
    class_rows = "".join(
        _kv_row(f"— {_ACCOUNT_CLASS_LABELS.get(cls, cls)}", _money(val, base))
        for cls, val in bd.get("account_classes", {}).items()
        if round(val) != 0
    )
    liability_rows = "".join(
        _kv_row(f"— {_LIABILITY_CLASS_LABELS.get(cls, cls)}", _money(val, base))
        for cls, val in bd.get("liability_classes", {}).items()
        if round(val) != 0
    )
    summary = (
        '<table class="kv">'
        + _kv_row("Total assets (accounts)", _money(bd.get("accounts_asset"), base))
        + class_rows
        + _kv_row("Total liabilities (accounts)", _money(bd.get("accounts_liability"), base))
        + liability_rows
        + _kv_row("Market positions", _money(bd.get("market_positions"), base))
        + _kv_row("Net worth", _money(data.get("net_worth"), base), strong=True)
        + "</table>"
    )

    def _acct_table(rows: list[dict], empty: str) -> str:
        if not rows:
            return f'<p class="empty">{_esc(empty)}</p>'
        body = "".join(
            "<tr>"
            f"<td>{_esc(r['name'])}</td>"
            f"<td>{_esc(r.get('type') or '')}</td>"
            f'<td class="num">{_esc(r["currency"])} {r["balance"]:,.2f}</td>'
            f'<td class="num">{_money(r["balance_base"], base)}</td>'
            "</tr>"
            for r in rows
        )
        return (
            '<table class="grid"><thead><tr>'
            "<th>Account</th><th>Type</th>"
            '<th class="num">Native balance</th>'
            f'<th class="num">In {_esc(base)}</th>'
            "</tr></thead><tbody>" + body + "</tbody></table>"
        )

    rb = data.get("returns") or {}
    dq = rb.get("data_quality") or {}
    if dq.get("sufficient"):
        # TWR/XIRR come back as FRACTIONS (returns_service) — render as percentages here
        # (presentation only; no money math).
        twr_block = rb.get("twr") or {}
        twr_frac = twr_block.get("annualized")
        twr_pct = twr_frac * 100 if isinstance(twr_frac, (int, float)) else None
        mwr = (rb.get("mwr") or {}).get("xirr") if rb.get("mwr") else None
        mwr_pct = mwr * 100 if isinstance(mwr, (int, float)) else None
        returns_html = (
            '<table class="kv">'
            + _kv_row(
                f"Period gain ({_esc(rb.get('period'))})",
                _money(rb.get("period_gain_base"), base),
            )
            + _kv_row("Time-weighted return (TWR)", _pct(twr_pct))
            + _kv_row("Money-weighted return (XIRR)", _pct(mwr_pct))
            + "</table>"
        )
    else:
        returns_html = (
            '<p class="empty">Not enough price history in this window to compute a reliable '
            "return yet.</p>"
        )

    return (
        _section("Summary", summary)
        + _section("Assets", _acct_table(data["assets"], "No asset accounts."))
        + _section("Liabilities", _acct_table(data["liabilities"], "No liability accounts."))
        + _section("Investment performance", returns_html)
    )


def _render_realized_income(data: dict, base: str) -> str:
    realized = data["realized"]
    income = data["income"]

    by_term = realized.get("by_term") or {}
    realized_summary = (
        '<table class="kv">'
        + _kv_row(
            "Total realized gain", _money(realized.get("realized_total_base"), base), strong=True
        )
        + _kv_row("Long-term", _money(by_term.get("long"), base))
        + _kv_row("Short-term", _money(by_term.get("short"), base))
        + _kv_row("Unknown term", _money(by_term.get("unknown"), base))
        + "</table>"
    )

    events = realized.get("events") or []
    if events:
        ev_body = "".join(
            "<tr>"
            f"<td>{_esc(e.get('disposed_at') or '')}</td>"
            f"<td>{_esc(e.get('instrument_name') or e.get('instrument_symbol') or '')}</td>"
            f'<td class="num">{_esc(e.get("quantity") or 0)}</td>'
            f'<td class="num">{_money(e.get("realized_gain_base"), base)}</td>'
            "</tr>"
            for e in events
        )
        ev_table = (
            '<table class="grid"><thead><tr>'
            "<th>Date</th><th>Instrument</th>"
            '<th class="num">Quantity</th><th class="num">Realized gain</th>'
            "</tr></thead><tbody>" + ev_body + "</tbody></table>"
        )
    else:
        ev_table = '<p class="empty">No disposals in this period.</p>'

    income_summary = (
        '<table class="kv">'
        + _kv_row("Total income (net)", _money(income.get("total_base"), base), strong=True)
        + _kv_row("Gross", _money(income.get("gross_base"), base))
        + _kv_row("Tax withheld", _money(income.get("tax_withheld_base"), base))
        + _kv_row("Trailing 12 months", _money(income.get("trailing_12m_base"), base))
        + _kv_row("Yield on cost", _pct(income.get("yield_on_cost_pct")))
        + "</table>"
    )

    by_kind = income.get("by_kind") or {}
    if by_kind:
        kind_body = "".join(
            f'<tr><td>{_esc(k)}</td><td class="num">{_money(v, base)}</td></tr>'
            for k, v in sorted(by_kind.items(), key=lambda kv: kv[1], reverse=True)
        )
        kind_table = (
            '<table class="grid"><thead><tr><th>Income kind</th>'
            '<th class="num">Net amount</th></tr></thead><tbody>' + kind_body + "</tbody></table>"
        )
    else:
        kind_table = '<p class="empty">No income events in this period.</p>'

    return (
        _section("Realized capital gains", realized_summary)
        + _section("Disposals", ev_table)
        + _section("Income summary", income_summary)
        + _section("Income by kind", kind_table)
    )


def _render_spending(data: dict, base: str) -> str:
    s = data["summary"]
    month_name = date(s["year"], s["month"], 1).strftime("%B %Y")

    overview = (
        '<table class="kv">'
        + _kv_row("Month", _esc(month_name))
        + _kv_row("Total spent", _money(s.get("total_spent"), base), strong=True)
        + _kv_row("Total available (budget)", _money(s.get("total_available"), base))
        + _kv_row("Budget used", _pct(s.get("pct_budget_used")))
        + _kv_row("Daily burn rate", _money(s.get("daily_rate"), base))
        + _kv_row("Projected month-end total", _money(s.get("projected_total"), base))
        + "</table>"
    )

    cats = [c for c in (s.get("by_category") or []) if c.get("spent") or c.get("limit")]
    if cats:
        cat_body = "".join(
            "<tr>"
            f"<td>{_esc(c.get('category'))}</td>"
            f'<td class="num">{_money(c.get("spent"), base)}</td>'
            f'<td class="num">{_money(c.get("available"), base) if c.get("is_expense") else "—"}</td>'
            f'<td class="num">{_pct(c.get("pct_used"))}</td>'
            f"<td>{_esc((c.get('status') or '').replace('_', ' '))}</td>"
            "</tr>"
            for c in cats
        )
        cat_table = (
            '<table class="grid"><thead><tr>'
            "<th>Category</th>"
            '<th class="num">Spent</th><th class="num">Available</th>'
            '<th class="num">Used</th><th>Status</th>'
            "</tr></thead><tbody>" + cat_body + "</tbody></table>"
        )
    else:
        cat_table = '<p class="empty">No spending recorded this month.</p>'

    sources = [src for src in (s.get("by_source") or []) if src.get("spent")]
    if sources:
        src_body = "".join(
            "<tr>"
            f"<td>{_esc(src.get('name'))}</td>"
            f'<td class="num">{_money(src.get("spent"), base)}</td>'
            f'<td class="num">{_esc(src.get("count") or 0)}</td>'
            "</tr>"
            for src in sources
        )
        src_table = (
            '<table class="grid"><thead><tr><th>Account</th>'
            '<th class="num">Spent</th><th class="num">Transactions</th>'
            "</tr></thead><tbody>" + src_body + "</tbody></table>"
        )
    else:
        src_table = '<p class="empty">No spending by account this month.</p>'

    return (
        _section("Overview", overview)
        + _section("By category", cat_table)
        + _section("By account", src_table)
    )


def _render_cash_flow(data: dict, base: str) -> str:
    overview = (
        '<table class="kv">'
        + _kv_row("Period", f"{_esc(data.get('start'))} → {_esc(data.get('end'))}")
        + _kv_row("Total income", _money(data.get("income_total"), base))
        + _kv_row("Total expense", _money(data.get("expense_total"), base))
        + _kv_row("Net cash flow", _money(data.get("net"), base), strong=True)
        + _kv_row("Savings rate", _pct(data.get("savings_rate_pct")))
        + "</table>"
    )

    def _cat_table(rows: list[dict], empty: str) -> str:
        if not rows:
            return f'<p class="empty">{_esc(empty)}</p>'
        body = "".join(
            "<tr>"
            f"<td>{_esc(r.get('category_label') or (r.get('category') or '').replace('_', ' '))}</td>"
            f'<td class="num">{_money(r.get("amount"), base)}</td>'
            "</tr>"
            for r in rows
        )
        return (
            '<table class="grid"><thead><tr><th>Category</th>'
            f'<th class="num">Amount ({_esc(base)})</th>'
            "</tr></thead><tbody>" + body + "</tbody></table>"
        )

    return (
        _section("Overview", overview)
        + _section("Income by category", _cat_table(data["income_by_category"], "No income."))
        + _section("Expense by category", _cat_table(data["expense_by_category"], "No expenses."))
    )


def _render_budget_vs_actual(data: dict, base: str) -> str:
    """Dispatches on ``data["engine_id"]`` (RFC 2026-08-22, Decision G): zero-based's own
    builder (``_budget_vs_actual_zero_based``) never sets that key, so its fixed
    assigned/activity/available shape renders exactly as before this dispatch existed.
    Any other engine's ``_budget_vs_actual_generic`` output — envelope, 50/30/20, or a
    future engine — renders through ``_render_budget_vs_actual_generic`` instead, whose
    column set/labels are engine-supplied rather than hardcoded."""
    months = data.get("months") or []
    span = f"{months[0]} → {months[-1]}" if months else "—"
    if data.get("engine_id"):
        return _render_budget_vs_actual_generic(data, base, span)
    overview = (
        '<table class="kv">'
        + _kv_row("Months", _esc(span))
        + _kv_row("Total assigned", _money(data["totals"].get("assigned"), base))
        + _kv_row("Total activity", _money(data["totals"].get("activity"), base))
        + _kv_row("Total available", _money(data["totals"].get("available"), base), strong=True)
        + "</table>"
    )

    groups = data.get("groups") or []
    if not any(g.get("categories") for g in groups):
        body = '<p class="empty">No Planning categories budgeted for this period.</p>'
    else:
        chunks: list[str] = []
        for g in groups:
            cats = g.get("categories") or []
            if not cats:
                continue
            rows = "".join(
                "<tr>"
                f"<td>{_esc(c.get('label') or c.get('key'))}</td>"
                f'<td class="num">{_money(c.get("assigned"), base)}</td>'
                f'<td class="num">{_money(c.get("activity"), base)}</td>'
                f'<td class="num">{_money(c.get("available"), base)}</td>'
                "</tr>"
                for c in cats
            )
            sub = g.get("subtotal") or {}
            rows += (
                '<tr class="strong">'
                f"<td>{_esc(g.get('name') or 'Group')} subtotal</td>"
                f'<td class="num">{_money(sub.get("assigned"), base)}</td>'
                f'<td class="num">{_money(sub.get("activity"), base)}</td>'
                f'<td class="num">{_money(sub.get("available"), base)}</td>'
                "</tr>"
            )
            chunks.append(
                f"<h3>{_esc(g.get('name') or 'Group')}</h3>"
                '<table class="grid"><thead><tr><th>Category</th>'
                '<th class="num">Assigned</th><th class="num">Activity</th>'
                '<th class="num">Available</th>'
                "</tr></thead><tbody>" + rows + "</tbody></table>"
            )
        body = "".join(chunks)

    return _section("Overview", overview) + _section("By category", body)


def _render_budget_vs_actual_generic(data: dict, base: str, span: str) -> str:
    """Renders `_budget_vs_actual_generic`'s engine-shaped rows (RFC 2026-08-22, Decision G).

    The column set AND their labels come from ``data["measure_labels"]`` — engine-supplied,
    in the order each engine's own ``Row.measures`` declares them (funded/spent/left for
    envelope, amount for 50/30/20) — never a hardcoded key list, so a future engine needs no
    change here. A row/group only ever carries the keys that measure actually produced
    (`_budget_vs_actual_generic` never fabricates a zero for a key an engine didn't emit), so
    ``_money`` renders a genuinely-absent value as "—" rather than a fake 0.00. An engine with
    no money measure at all (50/30/20's "savings" group, or a future engine with none) renders
    the same honest empty-table message the zero-based branch uses."""
    labels: dict[str, str] = data.get("measure_labels") or {}
    keys = list(labels.keys())
    totals = data.get("totals") or {}

    overview = (
        '<table class="kv">'
        + _kv_row("Months", _esc(span))
        + "".join(
            _kv_row(f"Total {labels[k]}", _money(totals.get(k), base), strong=(i == len(keys) - 1))
            for i, k in enumerate(keys)
        )
        + "</table>"
    )

    groups = data.get("groups") or []
    if not keys or not any(g.get("categories") for g in groups):
        body = '<p class="empty">No Planning categories budgeted for this period.</p>'
    else:
        head_cols = "".join(f'<th class="num">{_esc(labels[k])}</th>' for k in keys)
        chunks: list[str] = []
        for g in groups:
            cats = g.get("categories") or []
            if not cats:
                continue
            rows = "".join(
                "<tr>"
                f"<td>{_esc(c.get('label') or c.get('key'))}</td>"
                + "".join(f'<td class="num">{_money(c.get(k), base)}</td>' for k in keys)
                + "</tr>"
                for c in cats
            )
            sub = g.get("subtotal") or {}
            rows += (
                '<tr class="strong">'
                f"<td>{_esc(g.get('name') or 'Group')} subtotal</td>"
                + "".join(f'<td class="num">{_money(sub.get(k), base)}</td>' for k in keys)
                + "</tr>"
            )
            chunks.append(
                f"<h3>{_esc(g.get('name') or 'Group')}</h3>"
                f'<table class="grid"><thead><tr><th>Category</th>{head_cols}</tr></thead>'
                "<tbody>" + rows + "</tbody></table>"
            )
        body = "".join(chunks)

    return _section("Overview", overview) + _section("By category", body)


_RENDERERS = {
    "net_worth_statement": _render_net_worth,
    "realized_income": _render_realized_income,
    "spending_summary": _render_spending,
    "cash_flow_statement": _render_cash_flow,
    "budget_vs_actual": _render_budget_vs_actual,
}

# Print-first stylesheet: A4 margins, readable on screen, no nav/chrome, page-break-safe
# tables. Inlined so the document is a single self-contained file the user can save/share.
_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #15181d; background: #f4f5f7; margin: 0; padding: 24px;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.sheet {
  max-width: 820px; margin: 0 auto; background: #fff; padding: 40px 48px;
  border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
}
header.report-head { border-bottom: 2px solid #15181d; padding-bottom: 16px; margin-bottom: 24px; }
header.report-head h1 { font-size: 22px; margin: 0 0 4px; }
header.report-head .brand { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: #6b7280; }
header.report-head .meta { font-size: 12px; color: #6b7280; margin-top: 8px; }
header.report-head .desc { font-size: 13px; color: #374151; margin: 10px 0 0; }
section { margin: 0 0 28px; page-break-inside: avoid; }
section h2 { font-size: 15px; margin: 0 0 10px; padding-bottom: 4px; border-bottom: 1px solid #e5e7eb; }
section h3 { font-size: 13px; margin: 16px 0 6px; color: #374151; }
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 4px; }
table.kv th { text-align: left; font-weight: 500; color: #4b5563; padding: 5px 0; width: 60%; }
table.kv td { padding: 5px 0; }
table.kv tr.strong th, table.kv tr.strong td { font-weight: 700; border-top: 1px solid #e5e7eb; padding-top: 8px; }
table.grid th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: #6b7280; border-bottom: 1px solid #d1d5db; padding: 6px 8px; }
table.grid td { padding: 6px 8px; border-bottom: 1px solid #f0f1f3; }
table.grid tbody tr:last-child td { border-bottom: 1px solid #d1d5db; }
table.grid tr.strong td { font-weight: 700; border-top: 1px solid #d1d5db; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.empty { color: #9ca3af; font-size: 13px; font-style: italic; margin: 4px 0; }
footer.report-foot { margin-top: 32px; padding-top: 12px; border-top: 1px solid #e5e7eb;
  font-size: 11px; color: #9ca3af; }
@media print {
  body { background: #fff; padding: 0; }
  .sheet { box-shadow: none; border-radius: 0; max-width: none; padding: 0; }
  @page { margin: 18mm; }
}
""".strip()


def render_html(report: dict) -> str:
    """Render a built report model (from `build_report`) to a self-contained printable HTML
    document. The body is composed per report type; the chrome (head/style/footer) is shared."""
    base = report["base_currency"]
    renderer = _RENDERERS.get(report["type"])
    body = renderer(report["data"], base) if renderer else "<p>Unknown report.</p>"

    period_line = ""
    if report.get("uses_period") and report.get("period"):
        period_line = f" • Period: {_esc(report['period'])}"

    generated = report.get("generated_at") or ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(report["title"])} — {_esc(report["app_name"])}</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="sheet">
<header class="report-head">
<div class="brand">{_esc(report["app_name"])}</div>
<h1>{_esc(report["title"])}</h1>
<div class="meta">Base currency: {_esc(base)}{period_line}</div>
<p class="desc">{_esc(report["description"])}</p>
</header>
{body}
<footer class="report-foot">
Generated {_esc(generated)} • Figures are computed live and normalized to {_esc(base)}. To save
a PDF, use your browser&rsquo;s Print → &ldquo;Save as PDF&rdquo;.
</footer>
</div>
</body>
</html>"""
