"""Portfolio / positions / actions tools."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools._shared import no_op_write_result, svc
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_batch_only
from backend.models.account import ACCOUNT_BAND

logger = logging.getLogger(__name__)

# The seven account types, mirrored from ACCOUNT_BAND's own keys so this enum can never
# drift from the store's — the same construction as `ai/tools/accounts.py`. mypy cannot
# statically resolve a Literal built from a runtime tuple; the schema-derivation golden
# test proves it at runtime.
_AccountType = Literal[tuple(sorted(ACCOUNT_BAND))]  # type: ignore[valid-type]

# Batch-call caps (P4 tool-pluralization sweep). Each guards a loop that does real
# per-item work (a store write, or in resolve_instruments's case a live network fetch) —
# generous enough for "resolve my whole portfolio's tickers in one call" (the ask's own
# 20-holding example) without letting one call fan out into an unbounded amount of work.
_MAX_RESOLVE_BATCH = 50
_MAX_INSTRUMENT_UPDATE_BATCH = 50
_MAX_POSITION_ADD_BATCH = 50
_MAX_POSITION_UPDATE_BATCH = 50
_MAX_POSITION_REMOVE_BATCH = 50

# Arity-naming fixup (harness reliability program, owner incident 2026-07-19 — "why is
# delete_account still singular / account_id even though it's a list?"): a tool whose
# array argument is the SET OF ENTITIES it acts on must have a plural NAME too, not just
# a plural param. `resolve_instrument`/`update_instrument` each gained a batch array
# (`queries`/`updates`) where every item names a DIFFERENT instrument — an entity set, per
# the rule — so both are renamed here (`resolve_instruments`/`update_instruments`), with
# the old names migrated onto them (a tool name is persisted data — see registry.py).
#
# `add_positions/update/remove` (this file) and `acquire_lots/dispose/delete/
# dispose_undo` (lots.py) have the IDENTICAL shape — a batch array of independent
# entities (`adds`/`updates`/`removals`/`acquisitions`/`disposals`/`deletions`/
# `event_ids`) — but are DELIBERATELY LEFT SINGULAR: each pairs with a `propose_*` tool of
# the SAME name that previews exactly one item (there is no `propose_position_adds` /
# `propose_lot_acquisitions` — the interactive preview-then-confirm flow is inherently
# single-item, while the apply side ALSO accepts a batch for unattended/imported flows).
# Renaming only the apply half would break that propose->apply name symmetry, which is
# what lets the model find the tool that commits a given proposal. The batch param name
# itself already discloses the arity honestly (`adds`, `acquisitions`, ...), and every
# tool description says so explicitly — unlike `resolve_instrument`/`update_instrument`,
# which have no propose/apply pairing constraining their name.


class GetPortfolioInput(ToolInput):
    """Schema-of-record for ``get_portfolio`` (see ``schema_from_model``)."""

    query: Optional[str] = Field(
        default=None,
        description=(
            "Case-insensitive substring over symbol/ticker or label/name (e.g. 'AAPL', 'apple')."
        ),
    )
    asset_class: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "Exact asset class, e.g. 'equity', 'crypto', 'bond', 'property'. Pass a LIST to match any of several (e.g. ['equity', 'crypto']) instead of one call per class."
        ),
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Only positions in this account. For several accounts use account_ids.",
    )
    account_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Only positions in ANY of these accounts (from list_accounts) — one call instead of one per custodian."
        ),
    )
    vesting_state: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "Entitlement, not liquidity: 'vested' rows are OWNED, count normally in any total. 'unvested' = UNVESTED equity-grant units, forfeited if the grantee leaves the company — NOT owned yet. A `vesting_state='unvested'` row must be EXCLUDED from any portfolio total, allocation breakdown, or 'what am I worth' answer; get_net_worth/get_net_worth_history already report its value separately as `unvested_base` — quote that instead of summing it in yourself. Pass a LIST to match either."
        ),
    )


@tool(
    "get_portfolio",
    description=(
        "Get the user's portfolio — positions with label, category, asset_class, quantity, price, cost_basis, currency, ticker, vesting_state, and `thesis` (the user's own investment thesis read at today's price — role, reason, breaks_if, levels, stance; judge any move against it, never the move alone) when one exists.\n\nReturns EVERY position when called with no arguments (that is the right call for a portfolio total or an allocation breakdown). When you want one holding, filter instead: query='AAPL' matches symbol or name, or narrow with asset_class / account_id / vesting_state. asset_class, vesting_state and account_ids each accept a LIST as well as a single value (OR'd within the field) — 'what do I hold at IBKR or Binance' is ONE call with account_ids=[a,b], never one per account. The response reports `showing` and `total`, so a filtered read never looks like the whole book.\n\nVESTING_STATE MATTERS FOR ANY TOTAL: 'vested' rows are OWNED; 'unvested' rows are equity-grant units NOT owned yet (forfeited if the person leaves). NEVER sum an unvested row into a portfolio total, allocation breakdown, or 'what am I worth' answer. `summary.owned_base`/`summary.unvested_base` already do this split over whatever positions this call returned — use them instead of hand-summing price×quantity. get_net_worth / get_net_worth_history report the same split as `net_worth`/`unvested_base`.\n\nTODAY (unfiltered): `today` {pl_base, pct, top_movers} is the screens' figure; use it, never get_returns."
    ),
    input_schema=schema_of(GetPortfolioInput, tool_name="get_portfolio"),
)
async def get_portfolio(input_data: dict):
    # Read read_rows / _NAMES_CACHE through the package namespace so the
    # characterization tests' monkeypatch of backend.ai.tools.read_rows is observed.
    import backend.ai.tools as _root
    from backend.core.fx import base_currency
    from backend.services import positions as positions_service
    from backend.services.fx_cache import load_fx_cache

    rows = _root.read_rows()
    total = len(rows)
    base = base_currency()
    rates = load_fx_cache()  # one rate set for the whole response
    selected = _filter_positions(rows, input_data)
    projected = []
    for row in selected:
        ticker = row.get("ticker", "")
        p = _project_position(row, base, rates)
        if ticker and ticker in _root._NAMES_CACHE:
            p["company_name"] = _root._NAMES_CACHE[ticker]
        projected.append(p)
    _attach_theses(selected, projected)

    # summary is SERVER-computed over exactly the rows this call is returning (the full
    # book when unfiltered, the matched subset when filtered) — a guarantee, not just
    # description text, that a model reading this response can get owned-vs-unvested
    # right without re-deriving the bucket rule itself. Reconciles with get_net_worth's
    # net_worth/unvested_base fold when called unfiltered (same MARKET_ASSET_CLASSES gate
    # and per-row valuation — see positions_service.owned_vs_unvested_base).
    out: dict = {
        "positions": projected,
        "showing": len(projected),
        "total": total,
        "summary": positions_service.owned_vs_unvested_base(selected),
    }
    if not _has_position_filter(input_data):
        out["today"] = _book_today(base)
    if _has_position_filter(input_data):
        if not projected:
            out["note"] = (
                f"0 of {total} positions match these filters — the positions DO exist, your filter excluded them. Loosen or drop the filter and retry. Do NOT report that the user holds nothing."
                if total
                else "This book has no positions yet."
            )
        elif len(projected) < total:
            out["note"] = (
                f"Showing {len(projected)} of {total} positions — filtered, not truncated. `summary` above covers only this subset too — do not use it (or the rows) to compute a whole-portfolio total; call unfiltered for that."
            )
    return json.dumps(out, ensure_ascii=False), None


def _attach_theses(rows: list[dict], projected: list[dict]) -> None:
    """A position whose instrument has an investment thesis carries ``thesis``: the user's role,
    reason, breaks-if and levels, read at this row's price (``services.thesis_judgment``)."""
    from backend.services import thesis_judgment

    judgments = thesis_judgment.book_judgments(rows)
    if not judgments:
        return
    for row, p in zip(rows, projected):
        j = judgments.get((row.get("ticker") or row.get("symbol") or "").strip().upper())
        if j:
            p["thesis"] = thesis_judgment.compact(j)


def _book_today(base: str) -> dict:
    """Today's move of the investment book: THE figure the Dashboard and Investments show
    (`investments_book.today_move` over the same live projection, ADR
    2026-09-25-concentration-today-and-benchmark-have-one-authority). Asked "how are my
    investments doing today", Lucius answered from get_returns' calendar window instead — since
    midnight, so US stocks read flat through a Singapore morning while the screens showed the
    last session's move (screens -S$250, Lucius +S$126; QA 2026-10-14)."""
    from backend.services import investments_book, portfolio_rows

    try:
        rows = portfolio_rows.projected_rows(base)["rows"]
    except Exception:  # noqa: BLE001 — the positions above still answer; say today is unknown
        logger.warning("get_portfolio: today's move unavailable", exc_info=True)
        return {"has_data": False}
    invested = [r for r in rows if investments_book.is_invested(r)]
    move = investments_book.today_move(invested)
    movers = sorted(
        (r for r in invested if r.get("daily_pl")), key=lambda r: abs(r["daily_pl"]), reverse=True
    )
    return {
        "pl_base": move["pl"],
        "pct": move["pct"],
        "has_data": move["has_data"],
        "top_movers": [
            {
                "symbol": r.get("symbol") or r.get("ticker") or r.get("label"),
                "pl_base": round(r["daily_pl"], 2),
                "pct": r.get("daily_pl_pct"),
            }
            for r in movers[:5]
        ],
    }


def _vesting_of(input_data: dict) -> str:
    """Caller-supplied entitlement, tolerating the pre-split `bucket` spelling.

    A tool name/param is persisted data — it lives in agents.json, skill frontmatter and
    in-flight transcripts — so `bucket` keeps working rather than hard-failing a caller
    written against the old schema. `locked` was the only value that ever meant "not
    entitled"; everything else maps to vested.
    """
    state = input_data.get("vesting_state")
    if state in ("vested", "unvested"):
        return state
    return "unvested" if input_data.get("bucket") == "locked" else "vested"


_POSITION_FILTER_KEYS = ("query", "asset_class", "account_id", "account_ids", "vesting_state")


def _has_position_filter(input_data: dict) -> bool:
    for key in _POSITION_FILTER_KEYS:
        value = input_data.get(key)
        if isinstance(value, (list, tuple, set)):
            if any(str(v or "").strip() for v in value):
                return True
        elif str(value or "").strip():
            return True
    return False


def _norm_many(value) -> set[str]:
    """Normalize a filter that may arrive as a bare scalar OR a list into a lowercase set.

    Mirrors ``ledger.py``'s ``_as_filter_list`` intent (both shapes kept deliberately —
    a single value is the common case and must stay trivial) but returns a set since every
    caller here only needs OR-membership, not the scalar/list shape back.
    """
    if value is None:
        return set()
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return {str(v).strip().lower() for v in items if str(v).strip()}


def _filter_positions(rows: list[dict], input_data: dict) -> list[dict]:
    """Row selection for get_portfolio. Selection only — never field projection (see
    _project_position's 2026-07-09 scar)."""

    def norm(v) -> str:
        return str(v or "").strip().lower()

    out = rows
    query = norm(input_data.get("query"))
    if query:
        out = [
            r
            for r in out
            if query in norm(r.get("symbol"))
            or query in norm(r.get("ticker"))
            or query in norm(r.get("label"))
            or query in norm(r.get("name"))
        ]
    for field in ("asset_class", "vesting_state"):
        wanted = _norm_many(input_data.get(field))
        if wanted:
            out = [r for r in out if norm(r.get(field)) in wanted]
    # account_id (singular, kept for backward compat) and account_ids (the batch form) are
    # OR'd together into one wanted-set — a caller may use either or both.
    account_wanted = _norm_many(input_data.get("account_id")) | _norm_many(
        input_data.get("account_ids")
    )
    if account_wanted:
        out = [r for r in out if norm(r.get("account_id")) in account_wanted]
    return out


class GetFixedIncomeInput(ToolInput):
    """Schema-of-record for ``get_fixed_income`` (see ``schema_from_model``)."""


@tool(
    "get_fixed_income",
    description=(
        "The fixed-income (bond) lens: every bond holding valued in base currency — type (market/ssb/tbill/etf/fd), coupon rate, maturity date, days-to-maturity, next coupon — plus the by-subtype split, cumulative maturity buckets (≤30d/≤90d/≤1y), and the upcoming-maturity ladder. Use to answer 'what bonds do I hold', 'what matures soon', or 'how much fixed-income income is coming'. Already excludes any unvested (vesting_state=unvested) holding — not owned yet, same rule as get_net_worth."
    ),
    input_schema=schema_of(GetFixedIncomeInput, tool_name="get_fixed_income"),
)
async def get_fixed_income(input_data: dict):
    from backend.services import fixed_income as fixed_income_service

    return json.dumps(fixed_income_service.summary(), ensure_ascii=False, default=str), None


def _project_position(row: dict, base: str, rates: dict) -> dict:
    """Return the full stored position row PLUS its money, computed here.

    A prior curated allowlist silently hid note/institution/price_source_id/quote_per_unit (and
    the legacy "category" field the tool's own description promises) from every agent reading
    the portfolio (root-caused 2026-07-09) — return everything and let callers ignore what
    they don't need.

    The money is the important part. This used to hand the model `quantity`, `price` and
    `currency` and nothing else — so answering "what is this position worth?" in the base
    currency required the model to do qty x price x quote_per_unit AND an FX conversion, using
    a rate the payload never contained. It could not get that right; it could only guess. That
    is the same defect that shipped a S$120,000 net-worth headline from a client-side re-derive
    (ADR 2026-07-31-overview-aggregates-are-server-computed) — a consumer deriving money the
    server should have handed it. `value_row` is the ONE canonical per-row fold, shared with
    net worth and the portfolio reads, so a tool answer cannot disagree with the UI.

    Uses cached prices/FX — the same freshness `read_rows()` already had, so this adds figures
    without adding a vendor round trip to every tool call.
    """
    from backend.services import portfolio_rows as portfolio_rows_service

    out = portfolio_rows_service.value_row(row, base, rates)
    # A share-of-portfolio weight is only meaningful against the WHOLE book. This projection is
    # applied to whatever subset the caller filtered to, so the field would read 0 (or worse,
    # look authoritative) on a filtered call — drop it and let `summary` carry the totals.
    out.pop("pct_of_portfolio", None)
    # Omit ticker when it duplicates symbol (saves ~8 chars × all rows).
    if row.get("ticker") and row.get("ticker") == row.get("symbol"):
        out.pop("ticker", None)
    return out


class GetReturnsInput(ToolInput):
    """Schema-of-record for ``get_returns`` (see ``schema_from_model``)."""

    period: Optional[Literal["7D", "1M", "3M", "YTD", "1Y", "ALL"]] = Field(
        default=None, description="Return window (default 1Y)"
    )
    benchmark: Optional[str] = Field(
        default=None,
        description="Optional benchmark instrument id or symbol (e.g. 'SPY') to compare against.",
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "First day of an exact window (YYYY-MM-DD), instead of period — e.g. September is "
            "date_from=2026-09-01, date_to=2026-09-30. Measured from the previous day's close."
        ),
    )
    date_to: Optional[str] = Field(
        default=None, description="Last day of the window (YYYY-MM-DD); default today."
    )
    by_holding: Optional[bool] = Field(
        default=None,
        description=(
            "Also return `holdings`: each investment's start/end value, bought/sold, gain and "
            "return over the SAME window — how each holding did this period, not since purchase."
        ),
    )


@tool(
    "get_returns",
    description=(
        "Investment book performance over calendar days, not today's move (use get_portfolio's `today`). TWR (time-weighted, strips deposit timing — the 'how did investments do' number), MWR/XIRR (personal IRR including deposit timing — only present for spans ≥ 30 days), period dollar gain, and start/end value in the base currency. Periods: 7D|1M|3M|YTD|1Y|ALL (default 1Y). Fresh books are clamped to 'Since <inception>' rather than refusing; check effective_start + label in the payload. annualized=true only at ≥360 days. twr/mwr are null when price history is insufficient (data_quality.sufficient=false) — say so, don't quote a number. Optional benchmark (instrument id or symbol) adds a comparison block with portfolio_twr/benchmark_twr/excess_return (null if benchmark lacks history). The series[] field carries portfolio + benchmark as 0-BASED cumulative return FRACTIONS for charting — the window opens at 0.0 and 0.07 means +7%, NOT an index rebased to 100. Already excludes unvested (vesting_state=unvested) equity-grant units from value, TWR and MWR/XIRR alike — not owned yet, same rule as get_net_worth. EXACT WINDOWS: pass date_from/date_to instead of period for a calendar month or any range. PER HOLDING: by_holding=true adds `holdings` — each investment's gain_base (end − start value, net of its buys and sales) and return_pct (Modified Dietz) over the same window, plus price_change_pct; the holdings' gains add up to period_gain_base."
    ),
    input_schema=schema_of(GetReturnsInput, tool_name="get_returns"),
)
async def get_returns(input_data: dict):
    from backend.core.errors import ValidationError
    from backend.services import returns as returns_service

    period = (input_data.get("period") or "1Y").upper()
    if period not in returns_service.PERIODS:
        period = "1Y"
    benchmark = (input_data.get("benchmark") or "").strip() or None
    try:
        out = returns_service.portfolio_returns_with_benchmark(
            period,
            benchmark=benchmark,
            date_from=input_data.get("date_from") or None,
            date_to=input_data.get("date_to") or None,
            by_holding=bool(input_data.get("by_holding")),
        )
    except ValidationError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    holdings = out.get("holdings")
    if holdings and len(holdings) > _MAX_HOLDING_ROWS:
        half = _MAX_HOLDING_ROWS // 2
        out["holdings"] = holdings[:half] + holdings[-half:]
        out["holdings_omitted"] = len(holdings) - _MAX_HOLDING_ROWS
        out["holdings_note"] = (
            f"Showing the {half} best and {half} worst; {out['holdings_omitted']} holdings in "
            "between are omitted."
        )
    return json.dumps(out, ensure_ascii=False), None


_MAX_HOLDING_ROWS = 40


class GetNetWorthHistoryInput(ToolInput):
    """Schema-of-record for ``get_net_worth_history`` (see ``schema_from_model``)."""

    period: Optional[Literal["1M", "3M", "YTD", "1Y", "ALL"]] = Field(
        default=None, description="History window (default 1Y)"
    )


@tool(
    "get_net_worth_history",
    description=(
        "Get the user's NET WORTH OVER TIME — the monthly reconstructed series (cash + receivables + market positions − liabilities), each point in the base currency. Use this to answer 'how has my net worth changed?' / 'is my wealth growing?' and to read the trajectory. Each point: {date, net_worth, accounts (cash+settlement_cash+receivable+liabilities, the account-only basis), liabilities (raw-negative), classes (per-asset-class band), unvested_base (unvested equity, see below), market_tracked (whether market value is included yet)}. IMPORTANT: classes.cash is cash on hand (bank/e-wallet); classes.settlement_cash is brokerage/exchange settlement cash (negative when margin is drawn) and belongs to investments, not cash; classes.fixed_deposit / classes.retirement are locked. unvested_base (unvested RSU/option grant units, vesting_state=unvested) is EXCLUDED from net_worth and every classes band — forfeited if the person leaves the company, so not owned yet; mention it only as a separate, not-yet-owned figure, never as part of net worth. Periods: 1M | 3M | YTD | 1Y | ALL (default 1Y). This is the wealth TRAJECTORY, distinct from get_returns (investment PERFORMANCE/TWR). A young/empty book returns an empty history."
    ),
    input_schema=schema_of(GetNetWorthHistoryInput, tool_name="get_net_worth_history"),
)
async def get_net_worth_history(input_data: dict):
    from backend.services import ledger as ledger_service
    from backend.services import returns as returns_service

    period = (input_data.get("period") or "1Y").upper()
    if period not in returns_service.PERIODS:
        period = "1Y"

    history = ledger_service.net_worth_history()
    if period != "ALL" and history:
        start, _end = returns_service.resolve_period(period)
        history = [p for p in history if p.get("date", "") >= start]

    out = {
        "history": history,
        "base_currency": ledger_service.base_currency(),
        "period": period,
    }
    return json.dumps(out, ensure_ascii=False), None


class GetActionsInput(ToolInput):
    """Schema-of-record for ``get_actions`` (see ``schema_from_model``)."""


@tool(
    "get_actions",
    description="Get the property action log — all scheduled tasks with due dates and status.",
    input_schema=schema_of(GetActionsInput, tool_name="get_actions"),
)
async def get_actions(input_data: dict):
    import backend.ai.tools as _root

    actions = _root.read_actions()
    return json.dumps(actions, ensure_ascii=False), None


class UpdateActionStatusInput(ToolInput):
    """Schema-of-record for ``update_action_status`` (see ``schema_from_model``)."""

    action_id: int = Field(
        description=(
            "The property action to update, by its INTEGER `id` from get_actions (these ids are plain integers, not the `xxx_<hex>` ids other stores use). An id no action carries is reported as not found, never created."
        )
    )
    status: Literal["Done", "Not Started"] = Field(
        description=(
            "The status to set, replacing whatever the action currently has. These two values are the whole vocabulary — there is no in-progress state, and 'Not Started' is how you re-open an action marked Done."
        )
    )


@tool(
    "update_action_status",
    description="Mark a property action as Done or Not Started.",
    input_schema=schema_of(UpdateActionStatusInput, tool_name="update_action_status"),
    mutates=("portfolio",),
)
async def update_action_status(input_data: dict):
    import backend.ai.tools as _root

    action_id = int(input_data["action_id"])
    status = input_data["status"]
    actions = _root.read_actions()
    for a in actions:
        if a["id"] == action_id:
            a["status"] = status
            _root.write_actions(actions)
            return json.dumps({"ok": True, "action_id": action_id, "status": status}), None
    return json.dumps({"ok": False, "error": "action not found"}), None


class ResolveInstrumentItem(ToolInput):
    """One query in a batched ``resolve_instruments`` call — same fields as the single-item
    form, kept in sync with ``ResolveInstrumentInput`` deliberately (a batch item is a
    self-contained resolve, since two tickers in one call can need different overrides —
    e.g. one plain equity, one physical metal with its own unit)."""

    query: str = Field(
        description="Ticker (AAPL, ONWAI.DE), coin name (Solana), or metal ('gold','silver')"
    )
    asset_class: Optional[str] = Field(default=None, description="Economic EXPOSURE — see above.")
    asset_subclass: Optional[str] = Field(default=None, description="Wrapper/form — see above.")
    price_source: Optional[str] = Field(
        default=None, description="Optional override: yahoo|coingecko|manual"
    )
    price_source_id: Optional[str] = Field(
        default=None, description="Optional override: yahoo symbol or coingecko id"
    )
    quote_currency: Optional[str] = Field(
        default=None,
        description=(
            "3-letter ISO code the instrument is PRICED in (EUR, SGD, USD). REQUIRED with "
            "price_source='manual' — there is no feed to fetch it from and the call is "
            "REFUSED without it. On a yahoo/coingecko instrument the feed's own answer "
            "always wins and this is ignored."
        ),
    )
    unit: Optional[str] = Field(default=None, description="Holding unit for physical metal.")
    allow_duplicate_symbol: Optional[bool] = Field(
        default=None, description="true only for a confirmed genuinely-different asset."
    )


class ResolveInstrumentInput(ToolInput):
    """Schema-of-record for ``resolve_instruments`` (see ``schema_from_model``)."""

    query: Optional[str] = Field(
        default=None,
        description=(
            "Ticker (AAPL, ONWAI.DE), coin name (Solana), or metal ('gold','silver'). Required unless `queries` (the batch form) is used instead."
        ),
    )
    asset_class: Optional[str] = Field(
        default=None,
        description=(
            "Economic EXPOSURE (the grouping axis): equity|commodity|crypto|bond|cash|property. A fund takes its underlying exposure — a gold ETF is 'commodity', NOT 'etf'."
        ),
    )
    asset_subclass: Optional[str] = Field(
        default=None,
        description=(
            "Wrapper/form: stock|etf|etc|etp|coin|future|physical|reit. Set 'etf'/'etc' for funds while asset_class holds the exposure."
        ),
    )
    price_source: Optional[str] = Field(
        default=None, description="Optional override: yahoo|coingecko|manual"
    )
    price_source_id: Optional[str] = Field(
        default=None, description="Optional override: yahoo symbol or coingecko id"
    )
    quote_currency: Optional[str] = Field(
        default=None,
        description=(
            "3-letter ISO code the instrument is PRICED in (EUR, SGD, USD). REQUIRED with "
            "price_source='manual' — there is no feed to fetch it from and the call is "
            "REFUSED without it. On a yahoo/coingecko instrument the feed's own answer "
            "always wins and this is ignored."
        ),
    )
    unit: Optional[str] = Field(
        default=None,
        description=(
            "Holding unit for physical metal: g|kg|ozt|tael|luong|tola|baht. Omit for stocks/crypto (counted in shares/coins)."
        ),
    )
    allow_duplicate_symbol: Optional[bool] = Field(
        default=None,
        description=(
            "Set true only after a prior call errored with 'symbol already exists' AND the user confirmed this is a genuinely different asset (e.g. same ticker, different exchange)."
        ),
    )
    queries: Optional[List[Union[str, ResolveInstrumentItem]]] = Field(
        default=None,
        description=(
            "Batch form: resolve MULTIPLE tickers/coins in ONE call instead of one call per symbol (e.g. every holding in a freshly-imported portfolio). Each item is either a bare ticker string (uses no overrides) or an object with its own asset_class/price_source/price_source_id/unit/allow_duplicate_symbol. Every item resolves INDEPENDENTLY — a per-symbol refusal (e.g. an ambiguous duplicate ticker REFUSED without allow_duplicate_symbol) is reported for THAT item only in `results`; every other item still resolves. Omit the single `query` field when using this."
        ),
    )


def _resolved_instrument_fields(inst: dict, created: bool) -> dict:
    """The shared success shape both the single and batch resolve paths return."""
    return {
        "resolved": True,
        "created": created,
        "instrument_id": inst["id"],
        "symbol": inst["symbol"],
        "name": inst["name"],
        "asset_class": inst["asset_class"],
        "price_source": inst["price_source"],
        "price_source_id": inst["price_source_id"],
        "quote_currency": inst["quote_currency"],
        "unit": inst.get("unit", ""),
        "quote_per_unit": inst.get("quote_per_unit", 1.0),
    }


def _resolve_batch_item(_resolve, item: dict) -> dict:
    """Resolve ONE query for the BATCH path, shaping either a resolved-instrument row or a
    per-item error — NEVER raising, so a batch caller can keep going past a single bad
    symbol (domain landmine: a stock-ticker collision REFUSES via ValidationError, which
    must be surfaced per symbol, not collapsed into a whole-call failure). The single-query
    legacy path below does NOT go through this — it keeps letting ValidationError propagate
    unchanged, exactly as it did before batching existed."""
    from backend.core.errors import ValidationError

    query = str(item.get("query") or "").strip()
    entry: dict = {"query": query}
    try:
        inst, created = _resolve(
            query,
            item.get("asset_class"),
            item.get("price_source"),
            item.get("price_source_id"),
            item.get("unit"),
            item.get("asset_subclass"),
            allow_duplicate_symbol=bool(item.get("allow_duplicate_symbol")),
            quote_currency=item.get("quote_currency"),
        )
    except ValidationError as exc:
        entry["resolved"] = False
        entry["error"] = exc.message or str(exc)
        for key in ("existing_instrument_id", "existing_symbol", "existing_name"):
            if key in exc.context:
                entry[key] = exc.context[key]
        return entry
    if inst is None:
        entry["resolved"] = False
        entry["error"] = (
            "Couldn't resolve that to a tradeable instrument. Ask the user for the exact ticker + exchange, or provide price_source + price_source_id."
        )
        return entry
    entry.update(_resolved_instrument_fields(inst, created))
    return entry


@tool(
    "resolve_instruments",
    description=(
        "Resolve a user-typed ticker or coin to a canonical instrument with a live price source — the FETCH-FACTS half of the instrument pair (update_instruments edits the record). Call this FIRST when adding a holding, then pass the returned instrument_id in the add_positions row — it guarantees a live price (essential for crypto — the right CoinGecko id can't be guessed). For ambiguous/dual-listed tickers pass price_source ('yahoo'|'coingecko'|'manual') + price_source_id: the pin picks the feed ENTRY, but its facts — quote currency above all — are still fetched from that source. An EXISTING row is refreshed with what its feed reports (a wrong quote_currency heals here); a NEW one is REFUSED when the source won't confirm the id — an instrument is never priced in a guessed currency. price_source='manual' (private assets) additionally needs quote_currency — there is no feed to fetch it from. For PHYSICAL precious metals, pass query 'gold'/'silver'/'platinum'/'palladium' (live spot, quoted per troy ounce) PLUS unit as the user counts it ('g','kg','ozt','tael','luong','tola','baht'). For a FUND (ETF/ETC/ETP/mutual fund), set asset_class to its UNDERLYING EXPOSURE, not the wrapper: a physically-backed metal or broad-commodity fund (PHAU, GLD, IAU, SGLN, SLV…) is asset_class='commodity'; the wrapper goes in asset_subclass ('etf'/'etc'). \n\nRESOLVING MANY TICKERS? Pass `queries` (a list) instead of N separate calls — each ticker resolves independently and a bad one doesn't block the rest."
    ),
    input_schema=schema_of(ResolveInstrumentInput, tool_name="resolve_instruments"),
    # Mints a new instrument row when the query resolves to one we don't have yet.
    mutates=("portfolio",),
)
async def resolve_instruments(input_data: dict):
    from backend.services.instruments import resolve_instrument as _resolve

    raw_queries = input_data.get("queries")
    if raw_queries:
        if len(raw_queries) > _MAX_RESOLVE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_queries)} queries exceeds the {_MAX_RESOLVE_BATCH}-query cap for one call — split into multiple calls."
                    )
                }
            ), None
        results = [
            _resolve_batch_item(_resolve, {"query": raw} if isinstance(raw, str) else dict(raw))
            for raw in raw_queries
        ]
        resolved_n = sum(1 for r in results if r.get("resolved"))
        created_n = sum(1 for r in results if r.get("created"))
        failed_n = len(results) - resolved_n
        out: dict = {
            "results": results,
            "showing": len(results),
            "total": len(results),
            "resolved_count": resolved_n,
            "created_count": created_n,
            "failed_count": failed_n,
        }
        if failed_n:
            out["note"] = (
                f"{failed_n} of {len(results)} could not be resolved — see each item's own `error`. The rest resolved fine; this is NOT a whole-call failure."
            )
        return json.dumps(out, ensure_ascii=False), None

    # Legacy single-query path — byte-identical to the pre-batch behavior (a symbol-collision
    # ValidationError still propagates and is caught generically by the dispatch loop, exactly
    # as before; only the NEW batch path above surfaces it per-item instead).
    inst, created = _resolve(
        input_data.get("query", ""),
        input_data.get("asset_class"),
        input_data.get("price_source"),
        input_data.get("price_source_id"),
        input_data.get("unit"),
        input_data.get("asset_subclass"),
        allow_duplicate_symbol=bool(input_data.get("allow_duplicate_symbol")),
        quote_currency=input_data.get("quote_currency"),
    )
    if inst is None:
        return json.dumps(
            {
                "resolved": False,
                "message": "Couldn't resolve that to a tradeable instrument. Ask the user for the exact ticker + exchange, or provide price_source + price_source_id.",
            }
        ), None
    return json.dumps(_resolved_instrument_fields(inst, created)), None


# Instrument-metadata field prose, shared by the root schema and each `updates` batch item.
# Facts verified against services.instruments.update_instrument (whitelist + null handling),
# services.income (dividend units) and services.prices (the enrichment overwrite).
_INSTR_DIVIDEND_DESC = "Expected annual dividend as a CASH AMOUNT PER UNIT in the instrument's quote currency (2.40 = $2.40/share/year), NOT a yield or a percent. Drives the income forecast and dividend calendar. Setting it here protects it from a live price refresh — the fetched figure will never overwrite it once you've set it explicitly."
_INSTR_BOND_TERMS_DESC = "Fixed-income terms — set asset_class='bond' first or they are DROPPED. A bond's mark is derived from these: valuation inputs, not labels."
_INSTR_REBIND_CC_DESC = (
    "The currency the instrument is PRICED in (3-letter ISO). Refused once price history "
    "exists — stored points are denominated in the old currency, so relabelling would "
    "re-denominate every historical valuation; re-bind price_source/price_source_id to "
    "a series in the right currency instead. On a yahoo/coingecko instrument the feed's "
    "answer wins."
)
_INSTR_REBIND_SOURCE_DESC = (
    "Price feed: 'yahoo' | 'coingecko' (quoted USD) | 'manual' (manual_price is the "
    "mark). Changing it or price_source_id re-binds pricing identity: the old price "
    "series is dropped (when no other instrument reads it) and the new key's is "
    "fetched; a collision on the new key, or a yahoo id the feed won't price, is REFUSED."
)
_INSTR_MANUAL_PRICE_DESC = (
    "Price per unit in quote_currency for a manual instrument — the only thing that values "
    "it. Required to switch a feed-priced instrument to price_source='manual' (a pin); the "
    "feed's history is kept, so re-binding back finds it again. Never put a price in "
    "price_source_id."
)
_INSTR_REBIND_ID_DESC = (
    "The feed entry's id (yahoo symbol like 'KSTL.DE', coingecko id like 'bitcoin'). A "
    "yahoo re-bind must be CONFIRMED by a live fetch — an id yahoo won't price is refused."
)


class BondTermsInput(ToolInput):
    """Nested ``bond_terms`` object for ``update_instruments``."""

    subtype: Optional[Literal["", "market", "ssb", "tbill", "etf", "fd"]] = Field(
        default=None,
        description=(
            "Decides how the mark is DERIVED: ssb = always par (100); tbill = zero-coupon, accretes issue_price→100 over issue→maturity; fd = 100 plus simple interest accrued to date; market and etf are quoted instead (manual clean price / Yahoo). Wrong subtype means a wrong holding value."
        ),
    )
    face_value: Optional[float] = Field(
        default=None,
        description=(
            "Par per HOLDING UNIT, default 100. A market bond uses 1000 with quantity = number of bonds; ssb/tbill/fd use 1 with quantity = principal. Multiplies every coupon (rate × face × quantity), so a wrong value scales income wrongly."
        ),
    )
    coupon_rate_annual: Optional[float] = Field(
        default=None,
        description="Annual coupon as a FRACTION, not a percent: 0.035 means 3.5%.",
    )
    coupon_freq_months: Optional[int] = Field(
        default=None,
        description=(
            "Months between coupons (6 = semi-annual, 12 = annual). Omitted: no coupon schedule or calendar rows. Annual income is face x rate regardless."
        ),
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="Issue date, ISO YYYY-MM-DD. Start of a tbill's accretion / fd's interest.",
    )
    maturity_date: Optional[str] = Field(
        default=None,
        description=(
            "Maturity date, ISO YYYY-MM-DD. Anchors the coupon dates walking back from it, and ends a tbill's accretion. Omitted on a tbill: it holds flat at issue_price."
        ),
    )
    issue_price: Optional[float] = Field(
        default=None,
        description=(
            "Price paid at issue as a PERCENT OF PAR (98.5 = 98.5% of par), default 100 — not a currency amount. Drives the tbill discount that accretes to par."
        ),
    )


class UpdateInstrumentItem(ToolInput):
    """One instrument's edit in a batched ``update_instruments`` call — same metadata
    fields as the single-item form, kept in sync deliberately."""

    instrument_id: str = Field(description="From resolve_instruments or get_portfolio")
    name: Optional[str] = Field(default=None)
    asset_class: Optional[Literal["equity", "commodity", "crypto", "bond", "cash", "property"]] = (
        Field(default=None, description="Economic exposure (the grouping axis)")
    )
    asset_subclass: Optional[
        Literal["stock", "etf", "etc", "etp", "mutual_fund", "reit", "future", "physical", "coin"]
    ] = Field(default=None, description="Wrapper/form, e.g. 'etf' for VOO/QQQ")
    sector: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    dividend_rate_annual: Optional[float] = Field(default=None, description=_INSTR_DIVIDEND_DESC)
    bond_terms: Optional[BondTermsInput] = Field(default=None, description=_INSTR_BOND_TERMS_DESC)
    quote_currency: Optional[str] = Field(default=None, description=_INSTR_REBIND_CC_DESC)
    price_source: Optional[Literal["yahoo", "coingecko", "manual"]] = Field(
        default=None, description=_INSTR_REBIND_SOURCE_DESC
    )
    price_source_id: Optional[str] = Field(default=None, description=_INSTR_REBIND_ID_DESC)
    manual_price: Optional[float] = Field(default=None, description=_INSTR_MANUAL_PRICE_DESC)
    symbol: Optional[str] = Field(
        default=None,
        description="Display ticker. Metadata — pricing identity is (price_source, price_source_id), not the symbol.",
    )
    precision: Optional[int] = Field(
        default=None, description="Display decimals for quantity (0–12)."
    )
    unit: Optional[str] = Field(
        default=None,
        description="Holding unit for physical metal (g|kg|ozt|tael|luong|tola|baht). Recomputes quote_per_unit; '' clears back to native shares/coins.",
    )


class UpdateInstrumentInput(ToolInput):
    """Schema-of-record for ``update_instruments`` (see ``schema_from_model``)."""

    instrument_id: Optional[str] = Field(
        default=None,
        description="Required unless `updates` (the batch form) is used instead.",
    )
    name: Optional[str] = Field(default=None)
    asset_class: Optional[Literal["equity", "commodity", "crypto", "bond", "cash", "property"]] = (
        Field(default=None, description="Economic exposure (the grouping axis)")
    )
    asset_subclass: Optional[
        Literal["stock", "etf", "etc", "etp", "mutual_fund", "reit", "future", "physical", "coin"]
    ] = Field(default=None, description="Wrapper/form, e.g. 'etf' for VOO/QQQ")
    sector: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    dividend_rate_annual: Optional[float] = Field(default=None)
    bond_terms: Optional[BondTermsInput] = Field(
        default=None,
        description=(
            "Fixed-income terms (only on a bond). subtype: market (individual bond, manual clean price), ssb (par-redeemable), tbill (discount→par), etf (bond fund, Yahoo-priced), fd (deposit/CD). coupon_rate_annual is a FRACTION (0.035 = 3.5%). face_value is par per unit (market bonds ~1000; for ssb/tbill/fd use 1 with quantity = principal)."
        ),
    )
    quote_currency: Optional[str] = Field(default=None, description=_INSTR_REBIND_CC_DESC)
    price_source: Optional[Literal["yahoo", "coingecko", "manual"]] = Field(
        default=None, description=_INSTR_REBIND_SOURCE_DESC
    )
    price_source_id: Optional[str] = Field(default=None, description=_INSTR_REBIND_ID_DESC)
    manual_price: Optional[float] = Field(default=None, description=_INSTR_MANUAL_PRICE_DESC)
    symbol: Optional[str] = Field(
        default=None,
        description="Display ticker. Metadata — pricing identity is (price_source, price_source_id), not the symbol.",
    )
    precision: Optional[int] = Field(
        default=None, description="Display decimals for quantity (0–12)."
    )
    unit: Optional[str] = Field(
        default=None,
        description="Holding unit for physical metal (g|kg|ozt|tael|luong|tola|baht). Recomputes quote_per_unit; '' clears back to native shares/coins.",
    )
    updates: Optional[List[UpdateInstrumentItem]] = Field(
        default=None,
        description=(
            "Batch form: edit MULTIPLE instruments in ONE call (e.g. reclassifying several ETFs after enrichment). Each item names its own instrument_id plus the fields to change and is applied INDEPENDENTLY, so one unknown id doesn't block the rest — check each item's own result in `results`. Only the fields you pass change; a null is ignored, so a value cannot be cleared this way."
        ),
    )


@tool(
    "update_instruments",
    description=(
        "Edit an existing instrument's record — the UPDATE half of the instrument pair (resolve_instruments fetches facts). Metadata: name, asset_class (exposure), asset_subclass (wrapper/form; drives the ETF/Fund/REIT badge), sector, note, dividend_rate_annual, bond_terms (fixed-income: face value, coupon, maturity — set asset_class='bond' first; a bond's mark is DERIVED from them), symbol, precision. VALUATION fields are editable too, as a controlled RE-BIND that re-establishes the price series in the new terms instead of silently re-denominating stored history: quote_currency (refused once history exists — stored points are denominated in the old currency), price_source + price_source_id (identity re-bind: the old series is dropped when nothing else reads it and the new key's is fetched; a collision on the new key, or a yahoo id the feed won't price, is REFUSED), manual_price (to pin a price: price_source='manual' + manual_price), unit (recomputes quote_per_unit). Pass only the fields to change; a null is ignored. instrument_id comes from resolve_instruments or get_portfolio. Editing several instruments the same way? Pass `updates` (a list of {instrument_id, ...fields}) instead of one call per instrument."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        UpdateInstrumentInput, array="updates", tool_name="update_instruments"
    ),
    mutates=("portfolio",),
)
async def update_instruments(input_data: dict):
    from backend.services.instruments import update_instrument as _update

    raw_updates = input_data.get("updates")
    if raw_updates:
        if len(raw_updates) > _MAX_INSTRUMENT_UPDATE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_updates)} updates exceeds the "
                        f"{_MAX_INSTRUMENT_UPDATE_BATCH}-update cap for one call — split into multiple calls."
                    )
                }
            ), None
        results = []
        for item in raw_updates:
            instrument_id = item.get("instrument_id", "")
            changes = {k: v for k, v in item.items() if k != "instrument_id"}
            result = _update(instrument_id, changes)
            results.append({"instrument_id": instrument_id, **result})
        succeeded = sum(1 for r in results if r.get("ok"))
        failed = len(results) - succeeded
        out: dict = {
            "results": results,
            "showing": len(results),
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        }
        if failed:
            out["note"] = (
                f"{failed} of {len(results)} updates failed — see each item's own `error`. The rest were applied; this is NOT a whole-call failure."
            )
        return json.dumps(out, ensure_ascii=False), None

    # Legacy single-item path — byte-identical to the pre-batch behavior (forwards every
    # field except instrument_id/updates; services.instruments.update_instrument's own
    # whitelist decides what's actually applied).
    changes = {k: v for k, v in input_data.items() if k not in ("instrument_id", "updates")}
    result = _update(input_data.get("instrument_id", ""), changes)
    return json.dumps(result), None


def _bare_object_schema(schema: dict) -> None:
    """``json_schema_extra`` callable for a free-form dict field whose hand-written
    literal is a BARE ``{"type": "object"}`` — no ``properties``/``additionalProperties``
    at all (the "bare free-form dict" quirk class, ``schema_from_model.py``'s module
    docstring / ADR `2026-07-19-derive-tool-schemas-from-models`). A plain
    ``Dict[str, Any]`` always emits ``additionalProperties: true``, a byte mismatch.
    Strips it from the field's own schema and, for an Optional field, every ``anyOf``
    branch — Pydantic attaches ``json_schema_extra`` to the OUTER ``anyOf[X, null]``
    wrapper, not the inner ``X`` branch."""
    schema.pop("additionalProperties", None)
    for branch in schema.get("anyOf", []):
        if isinstance(branch, dict):
            branch.pop("additionalProperties", None)


def _build_position_update_change(input_data: dict) -> tuple[Optional[dict], Optional[str]]:
    """Shared preview-build for the ``update_positions`` confirm gate — the former
    body of ``propose_position_update``, now called only when previewing (staging a card)
    or re-verifying a confirm's outcome, never on a plain unattended write (that path is
    unchanged: straight to ``_apply_position``, exactly like the old apply-only tool).
    Returns ``(change, None)`` or ``(None, error_message)``."""
    from backend.core.errors import OpenNetWorthError
    from backend.db.account_store import AccountStore
    from backend.services import positions as _ps

    position_id = input_data.get("position_id")
    row = _ps.hydrated_one(position_id) if position_id else None
    if not row:
        return None, "position not found — call get_portfolio for current position_ids"
    changes = input_data.get("changes") or {}
    # Refuse BEFORE staging a card: a derived figure (or vesting_state) is never editable,
    # so a card for it could only end in an error after the user approved it.
    try:
        _ps.validate_position_update(
            position_id,
            {k: v for k, v in changes.items() if k not in ("account_name", "account_type")},
        )
    except OpenNetWorthError as exc:
        return None, exc.message or str(exc)
    _display_fields = ("quantity", "cost_basis", "cost_currency", "vesting_state")
    before = {k: row.get(k) for k in _display_fields}
    before["cost_currency"] = row.get("currency")
    after = {**before, **{k: v for k, v in changes.items() if k in _display_fields}}

    # Resolve account_name → account_id READ-ONLY. A preview must not write, so an
    # unmatched name is left as account_name for apply_position_change to mint at confirm
    # time (it calls resolve_account); the card says "would create" instead. Resolving
    # eagerly here used to leave a real orphan account behind on every typo'd or rejected
    # proposal.
    resolved_changes = dict(changes)
    account_is_new = False
    if "account_name" in resolved_changes and "account_id" not in resolved_changes:
        target = _ps.find_account(account_name=resolved_changes["account_name"])
        if target:
            resolved_changes["account_id"] = target["id"]
            del resolved_changes["account_name"]
        else:
            account_is_new = True

    # Build from→to account display when the change moves the position.
    account_from = account_to = None
    if "account_id" in resolved_changes or account_is_new:
        acc_store = AccountStore()
        from_acct = acc_store.find_by_id(row.get("account_id", "")) or {}
        to_acct = acc_store.find_by_id(resolved_changes.get("account_id", "")) or {}
        account_from = from_acct.get("name") or row.get("category")
        account_to = to_acct.get("name") or changes.get("account_name")

    change = {
        "type": "update",
        "position_id": position_id,
        "changes": resolved_changes,
        "reason": input_data.get("reason", ""),
        "display": {
            "symbol": row.get("symbol") or row.get("ticker", ""),
            "name": row.get("label", ""),
            "venue": row.get("category", ""),
            "before": before,
            "after": after,
            **(
                {
                    "account_from": account_from,
                    "account_to": account_to,
                    # The card must say this venue does not exist yet and will be created
                    # when the user confirms — nothing has been written at propose time.
                    **({"account_to_is_new": True} if account_is_new else {}),
                }
                if account_to
                else {}
            ),
        },
    }
    return change, None


def _position_update_no_op(position_id: Optional[str], changes: Optional[dict]) -> Optional[dict]:
    """``None`` when ``changes`` carries a real field to apply; otherwise the honest
    no-op result for an EXISTING position. Strips the internal ``_grant_managed_ok``
    bypass flag first (only ``equity.py`` may set it, as a TOP-LEVEL key on the change
    dict it hands ``apply_position_change`` directly — never a field ``update_position``
    itself applies) so a patch whose only key is that flag is still judged empty.

    Deliberately returns ``None`` (proceed normally) rather than a not-found error when
    the position doesn't resolve — the id-only-empty-patch case never reaches this
    function knowing whether the caller wants the gated/staged path's error message or
    the unattended path's; leaving it unhandled here lets whichever path was about to
    run produce its OWN existing not-found result, unchanged."""
    stripped = {k: v for k, v in (changes or {}).items() if k != "_grant_managed_ok"}
    if stripped:
        return None
    from backend.services import positions as _ps

    row = _ps.hydrated_one(position_id) if position_id else None
    if not row:
        return None
    return no_op_write_result({"position_id": position_id})


def _build_position_add_change(input_data: dict) -> tuple[Optional[dict], Optional[tuple]]:
    """Shared preview-build for the ``add_positions`` confirm gate — the former body
    of ``propose_position_add`` (minus the quantity check, which the caller runs
    UNCONDITIONALLY — see ``add_positions``). Called only when previewing or
    re-verifying a confirm's outcome. Returns ``(change, None)`` or, on a refused
    duplicate-holding/unknown-instrument, ``(None, ready_to_return_tool_result_tuple)`` so
    the caller can propagate the EXACT error shape (including ``position_id`` on the
    duplicate case) unchanged."""
    from backend.services import positions as _ps
    from backend.services.xray import get_instrument as _get_inst

    inst = _get_inst(input_data.get("instrument_id", ""))
    if not inst:
        return None, (
            json.dumps({"error": "unknown instrument_id — call resolve_instruments first"}),
            None,
        )
    venue = input_data.get("account_name") or ""
    if not input_data.get("account_id") and venue:
        # The preview names the account the holding will REALLY land in, and the duplicate
        # guard below runs against it; an unmatched name stays a name (apply creates it).
        target = _ps.find_account(account_name=venue)
        if target:
            input_data = {**input_data, "account_id": target["id"]}
    if input_data.get("account_id"):
        _a = svc.accounts.find_by_id(input_data["account_id"])
        if _a:
            venue = _a.get("name") or venue
    # Surface the duplicate-holding refusal at propose time (apply enforces it too), so
    # the user never confirms a diff that the choke point will reject.
    if input_data.get("account_id") and _vesting_of(input_data) == "vested":
        existing = _ps.find_active_liquid_position(inst["id"], input_data["account_id"])
        if existing:
            return None, (
                json.dumps(
                    {
                        "error": (
                            f"{inst.get('symbol') or 'this instrument'} already has an active "
                            f"position in this account (position_id={existing['id']}, "
                            f"qty={existing.get('quantity')}). To record a buy of an existing holding use propose_lot_acquire with that position_id instead."
                        ),
                        "position_id": existing["id"],
                    }
                ),
                None,
            )
    from backend.services.positions import normalize_cost_currency

    cost_currency = normalize_cost_currency(
        input_data["cost_currency"]
        if input_data.get("cost_currency") is not None
        else inst.get("quote_currency")
    )
    change = {
        "type": "add",
        "instrument_id": inst["id"],
        "account_id": input_data.get("account_id"),
        "account_name": input_data.get("account_name"),
        "account_type": input_data.get("account_type"),
        "quantity": input_data["quantity"],
        "cost_basis": input_data.get("cost_basis"),
        "cost_currency": cost_currency,
        "vesting_state": _vesting_of(input_data),
        "acquired_at": input_data.get("acquired_at") or None,
        "fees": input_data.get("fees") or 0,
        "idempotency_key": input_data.get("idempotency_key") or None,
        "reason": input_data.get("reason", ""),
        "display": {
            "symbol": inst.get("symbol", ""),
            "name": inst.get("name", ""),
            "asset_class": inst.get("asset_class", ""),
            "currency": inst.get("quote_currency", ""),
            "venue": venue,
            "quantity": input_data["quantity"],
            "cost_basis": input_data.get("cost_basis"),
            "cost_currency": cost_currency,
            "fees": input_data.get("fees") or 0,
            "acquired_at": input_data.get("acquired_at") or None,
        },
    }
    return change, None


def _apply_position_one_safe(kind: str, input_data: dict) -> dict:
    """``_apply_position_one``, but never raising — for the BATCH loops only.

    ``apply_position_change`` returns an error dict for the EXPECTED refusals (duplicate
    position, position not found), but ``update_position`` can also RAISE ValidationError
    (an unsupported field, or a grant-managed position without the bypass flag). Uncaught,
    one bad item would blow up the whole batch; the singular apply_position_* tools keep
    letting it propagate unchanged (today's behavior, untouched)."""
    from backend.core.errors import OpenNetWorthError

    try:
        return _apply_position_one(kind, input_data)
    except OpenNetWorthError as exc:
        return {"error": exc.message or str(exc)}


def _batch_summary(results: list[dict], *, ok_key: str = "ok") -> dict:
    """The shared {results, showing, total, succeeded, failed[, note]} envelope every
    batched apply below returns — partial failure must stay legible (does the caller know
    WHICH of N items failed), never collapse into one whole-call verdict."""
    succeeded = sum(1 for r in results if r.get(ok_key))
    failed = len(results) - succeeded
    out: dict = {
        "results": results,
        "showing": len(results),
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
    }
    if failed:
        out["note"] = (
            f"{failed} of {len(results)} failed — see each item's own `error`. The rest succeeded; this is NOT a whole-call failure."
        )
    return out


# Loose (non-ToolInput) nested batch item — no docstring (would leak into the item schema
# as a "description" key; only ToolInput suppresses that) and no additionalProperties.
class ApplyPositionUpdateItem(BaseModel):
    position_id: str = Field(description="Which position to edit — position_id from get_portfolio.")
    changes: Dict[str, Any] = Field(
        json_schema_extra=_bare_object_schema,
        description=(
            "Fields to change: account_id, account_name. Any other key is REFUSED. quantity, cost_basis and cost_currency are NEVER editable — they are derived from the position's records (its lots; for a grant, its vesting schedule and vests), so record what happened instead with acquire_lots / dispose_lots, correct a wrong lot with update_lots, or use the grant tools. vesting_state is set only by the grant tools."
        ),
    )


class ApplyPositionUpdateInput(ToolInput):
    """Schema-of-record for ``update_positions`` (see ``schema_from_model``)."""

    position_id: Optional[str] = Field(
        default=None,
        description="position_id from get_portfolio. Required unless `updates` is used instead.",
    )
    changes: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Fields to change: account_id, account_name. account_id moves to an existing account (its lots move with it); account_name creates/reuses by name. quantity/cost_basis/cost_currency/vesting_state are REFUSED "
            '(derived from records). e.g. {"account_id": "acc-123"}'
        ),
        json_schema_extra=_bare_object_schema,
    )
    reason: Optional[str] = Field(default=None, description="Why this change is needed")
    updates: Optional[List[ApplyPositionUpdateItem]] = Field(
        default=None,
        description=(
            "Batch form: [{position_id, changes}, …] — one call for several positions. One call covers them all, and raises ONE confirm card for the whole batch. Omit position_id/changes when using this."
        ),
    )


@tool(
    "update_positions",
    description=(
        "Move an existing position to another account (account_id/account_name). quantity, cost_basis and cost_currency are DERIVED from the position's records and are always REFUSED — record a buy/sale instead (acquire_lots / dispose_lots), or correct a wrong lot's date/quantity/cost with update_lots. vesting_state belongs to the grant tools. Moving a position to another account moves its lots too. Identify the position by its position_id (from get_portfolio). To move a position to a different custodian, pass account_id (from list_accounts) or account_name inside changes. On an interactive turn this raises a confirm card (no write yet, shows from→to accounts if moving venues); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller applies immediately, no card. For several positions at once, pass `updates` (a list of {position_id, changes}) instead — one call, and ONE confirm card for the whole batch."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ApplyPositionUpdateInput, array="updates", tool_name="update_positions"
    ),
    mutates=("portfolio",),
    # propose_position_update is the former FIRST HALF of this merge — see
    # remove_positions's aliases note (identical rationale, harness reliability
    # program 2026-07-19).
    self_stages=True,
)
async def update_positions(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "update_positions")
        if entry is None:
            return _gate.unknown_token_result("update_positions")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "updates")
    raw_updates = input_data.get("updates")
    if raw_updates:
        if len(raw_updates) > _MAX_POSITION_UPDATE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_updates)} updates exceeds the "
                        f"{_MAX_POSITION_UPDATE_BATCH}-update cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "update_batch",
                raw_updates,
                [
                    {"position_id": i.get("position_id"), "changes": i.get("changes")}
                    for i in raw_updates
                ],
                "Update positions",
            )
        results = []
        for item in raw_updates:
            no_op = _position_update_no_op(item.get("position_id"), item.get("changes"))
            row_result = no_op if no_op is not None else _apply_position_one_safe("update", item)
            results.append({"position_id": item.get("position_id"), **row_result})
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    # A structurally-empty patch (id-only, or only the internal _grant_managed_ok flag)
    # cannot change the row no matter what runs next — a "touch" bumps updated_at and
    # nothing else, byte-indistinguishable from a real edit if reported as plain success.
    # Short-circuit BEFORE staging a card (an empty-patch confirm card would be a
    # confirmation for a write that alters nothing) or writing (the unattended path).
    no_op = _position_update_no_op(input_data.get("position_id"), input_data.get("changes"))
    if no_op is not None:
        return json.dumps(no_op, ensure_ascii=False), None

    if gated or promised is not None:
        change, error = _build_position_update_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # the position (or the target venue) could have changed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("update_positions")

    return await _apply_position("update", input_data)


# Position-add field prose, shared by the root schema and each `adds` batch item. Facts
# verified against services.positions.apply_position_change("add") / add_position and
# db/position_store (the cost_basis per-unit rule).
_ADD_INSTRUMENT_ID_DESC = "What is held — instrument_id from resolve_instruments (call it first for a ticker) or get_portfolio. An unknown id is refused rather than minting an instrument."
_ADD_ACCOUNT_ID_DESC = "Where it is held — account id from list_accounts. Use the custodian's SECURITIES account, never its cash wallet: positions and lots live on the securities account."
_ADD_ACCOUNT_NAME_DESC = "Alternative to account_id: an existing securities account's exact name or institution (several matches are REFUSED, naming each), else CREATES a securities account of this name (e.g. 'IBKR'). Prefer account_id — a typo here creates a second account."
_ADD_ACCOUNT_TYPE_DESC = "Type used ONLY when account_name has to CREATE the account; ignored otherwise. Almost always 'investment' (a brokerage or crypto exchange — holdings plus settlement cash), which is the default. 'retirement' for a CPF/SRS/401k that holds funds. The other five hold no market positions. The new account's currency comes from the instrument's quote currency."
_ADD_QUANTITY_DESC = "Units held (shares/coins), not the position's cash value. For a bond priced off bond_terms with face_value 1 (ssb/tbill/fd) this is the principal."
_ADD_COST_BASIS_DESC = "AVERAGE COST PER UNIT in cost_currency (defaults to the instrument's quote currency) — NOT the total amount paid. Divide the total by quantity before passing it. 0 or omitted stores as unknown, which makes unrealized P&L and any later realized gain report null rather than a wrong number."
_ADD_COST_CURRENCY_DESC = "Original currency of the supplied per-unit cost basis. Preserve it even when the instrument is quoted in another currency; never convert historical cost at today's FX rate. Omit only when the cost is already in the quote currency."
_ADD_VESTING_DESC = "'vested' (default — the holding is the user's). 'unvested' is REFUSED here: unvested units come from an equity grant's vesting schedule, so create the grant instead. This is ENTITLEMENT, not liquidity — property is illiquid but perfectly vested. A second VESTED position for the same instrument at the same account is REFUSED with the existing position_id — a further buy of a holding already tracked is an acquire_lots call, not another position."
_ADD_ACQUIRED_AT_DESC = "Acquisition date, ISO YYYY-MM-DD. Supplying it also records a real buy LOT for this quantity and cost (and any idempotency_key lands on that lot, so a later acquire_lots for the same trade dedupes instead of double-booking). ONLY a date the user actually gave — never a guess or a placeholder (not an account-opening date, not today, not an estimate the user didn't state). Unknown → OMIT it: the holding is then recorded as an OPENING BALANCE lot dated today (held when tracking started), which is the honest record; when the user later knows the real purchase date, set it on that lot with update_lots."
_ADD_FEES_DESC = (
    "Acquisition commission in cost_currency, amortized into the auto-created lot's basis."
)


class ApplyPositionAddItem(ToolInput):
    """One position in a batched ``add_positions`` call — same fields as the
    single-item form, kept in sync deliberately."""

    instrument_id: str = Field(description=_ADD_INSTRUMENT_ID_DESC)
    account_id: Optional[str] = Field(default=None, description=_ADD_ACCOUNT_ID_DESC)
    account_name: Optional[str] = Field(default=None, description=_ADD_ACCOUNT_NAME_DESC)
    account_type: Optional[_AccountType] = Field(default=None, description=_ADD_ACCOUNT_TYPE_DESC)  # type: ignore[valid-type]
    quantity: float = Field(description=_ADD_QUANTITY_DESC)
    cost_basis: Optional[float] = Field(default=None, description=_ADD_COST_BASIS_DESC)
    cost_currency: Optional[str] = Field(default=None, description=_ADD_COST_CURRENCY_DESC)
    vesting_state: Optional[str] = Field(default=None, description=_ADD_VESTING_DESC)
    acquired_at: Optional[str] = Field(default=None, description=_ADD_ACQUIRED_AT_DESC)
    fees: Optional[float] = Field(default=None, description=_ADD_FEES_DESC)
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Per-item dedupe key (e.g. the broker order id) — never one key for the whole batch.",
    )


class ApplyPositionAddInput(ToolInput):
    """Schema-of-record for ``add_positions`` (see ``schema_from_model``)."""

    # NOTE: instrument_id/quantity stay REQUIRED (no default) even though a batch caller
    # using `adds` omits them — registry.dispatch strips `required` before validating
    # (ai/tools/registry.py's `_IGNORED_KEYWORDS`), so this is schema metadata only and
    # matches what services.positions.apply_position_change("add") actually enforces
    # (test_domain_introspect.py pins this pair). Making them Optional here would make
    # the schema claim less than the choke truly requires for the single-item form.
    instrument_id: str = Field()
    account_id: Optional[str] = Field(default=None)
    account_name: Optional[str] = Field(default=None)
    account_type: Optional[_AccountType] = Field(default=None)  # type: ignore[valid-type]
    quantity: float = Field()
    cost_basis: Optional[float] = Field(default=None)
    cost_currency: Optional[str] = Field(default=None, description=_ADD_COST_CURRENCY_DESC)
    vesting_state: Optional[str] = Field(default=None)
    acquired_at: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD — copy from the proposed change"
    )
    fees: Optional[float] = Field(
        default=None, description="Acquisition fee — copy from the proposed change"
    )
    idempotency_key: Optional[str] = Field(
        default=None, description="Copy from the proposed change"
    )
    adds: Optional[List[ApplyPositionAddItem]] = Field(
        default=None,
        min_length=1,
        description=(
            "Batch form: add MULTIPLE new holdings in ONE call (e.g. every position in a freshly-imported portfolio). Every item needs its OWN idempotency_key. One call covers them all, and raises ONE confirm card for the whole batch. Each is applied independently, so an instrument×account already holding an active position is refused alone (buy more via acquire_lots) while the rest still add."
        ),
    )


@tool(
    "add_positions",
    description=(
        "Add a NEW holding — an instrument NOT already held in that account. Buying MORE of an existing holding is a lot acquisition: use acquire_lots with its position_id from get_portfolio (a duplicate add is refused). ALWAYS call resolve_instruments first to get an instrument_id (this pins a live price source — essential for crypto). Identify the venue with account_id (from list_accounts) when it already exists, otherwise pass account_name and account_type and it will be created. Account = the custodian's securities account (e.g. 'IBKR'), never a currency wallet. On an interactive turn this raises a confirm card (no write yet); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller adds immediately, no card. REFUSED when an active position for the same instrument+account already exists. Every add records the holding's lot (lot_id in the result): with acquired_at a buy lot, without it an opening-balance lot dated today — NEVER also call acquire_lots for the same trade. If the user doesn't know when they bought it, leave acquired_at out — never invent a placeholder date. Automated flows: pass idempotency_key so a retry/reprocess is a no-op. Adding a whole portfolio's worth of new holdings? Pass `adds` (a list) instead — one call, and ONE confirm card for the whole batch."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ApplyPositionAddInput, array="adds", tool_name="add_positions"
    ),
    mutates=("portfolio",),
    # propose_position_add is the former FIRST HALF of this merge — see
    # remove_positions's aliases note (identical rationale, harness reliability
    # program 2026-07-19).
    self_stages=True,
)
async def add_positions(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "add_positions")
        if entry is None:
            return _gate.unknown_token_result("add_positions")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "adds")
    raw_adds = input_data.get("adds")
    if raw_adds:
        if len(raw_adds) > _MAX_POSITION_ADD_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_adds)} adds exceeds the {_MAX_POSITION_ADD_BATCH}-add cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "add_batch",
                raw_adds,
                [
                    {
                        "instrument_id": i.get("instrument_id"),
                        "quantity": i.get("quantity"),
                        "cost_basis": i.get("cost_basis"),
                        "cost_currency": i.get("cost_currency"),
                        "account_id": i.get("account_id"),
                    }
                    for i in raw_adds
                ],
                "Add positions",
            )
        results = [
            {
                "instrument_id": item.get("instrument_id"),
                **_apply_position_one_safe("add", item),
            }
            for item in raw_adds
        ]
        return json.dumps(_batch_summary(results), ensure_ascii=False), None
    import math

    quantity = input_data.get("quantity")
    if quantity is None or not math.isfinite(float(quantity)) or float(quantity) <= 0:
        # The choke (positions_service.apply_position_change) accepts quantity=0 for the
        # internal shell-position-then-lots pattern (equity grants; see
        # tests/test_lot_position_add_regression.py), so it can't enforce this itself —
        # but a caller of THIS tool is always recording a real holding, and the schema
        # already declares quantity required. Without this, a missing/zero/negative
        # quantity silently books a zero-unit position (money bug: found by AST-diffing
        # the derived shape against the hand-written schema).
        return json.dumps(
            {
                "error": (
                    "quantity must be a positive number — a position add needs the real quantity acquired"
                )
            }
        ), None

    if gated or promised is not None:
        change, err_result = _build_position_add_change(input_data)
        if err_result is not None:
            return err_result
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # e.g. the same instrument×account could have acquired an active position since.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("add_positions")

    return await _apply_position("add", input_data)


class ApplyPositionRemoveInput(ToolInput):
    """Schema-of-record for ``remove_positions`` (see ``schema_from_model``)."""

    position_id: Optional[str] = Field(
        default=None,
        description=(
            "position_id from get_portfolio. Required unless `removals` (the batch form) is used instead."
        ),
    )
    reason: Optional[str] = Field(default=None, description="Why this change is needed")
    removals: Optional[List[str]] = Field(
        default=None,
        description=(
            "Batch form: remove SEVERAL positions in ONE call — a list of position_ids. Each is removed independently; an unknown id is reported for that item only. One call covers them all, and raises ONE confirm card for the whole batch. Omit the single `position_id` field when using this."
        ),
    )


@tool(
    "remove_positions",
    description=(
        "Remove a position, identified by position_id (from get_portfolio). On an interactive turn this raises a confirm card (no write yet); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller (no user watching) removes immediately, no card. Removing several positions at once? Pass `removals` (a list of position_ids) instead of one call per position — one call, and ONE confirm card for the whole batch."
    ),
    input_schema=schema_of(ApplyPositionRemoveInput, tool_name="remove_positions"),
    mutates=("portfolio",),
    # propose_position_remove is the former FIRST HALF of this merge (harness reliability
    # program, 2026-07-19: 14 propose_/apply_ pairs collapsed into one confirm-token'd tool
    # each, mirroring add_transactions/update_transactions — docs/adr/
    # 2026-07-19-ledger-confirm-gate-one-tool-stored-args.md). A tool name is persisted data
    # (agents.json allowlists, skill frontmatter, in-flight transcripts), so the old name
    # was migrated onto this one — see registry.py's "Tool names are FINAL" note.
    self_stages=True,
)
async def remove_positions(input_data: dict):
    from backend.services import positions as _ps

    # Confirm gate, step 2 — a token means the user tapped ✓ on a preview card. Pop the
    # STORED args and re-verify THOSE resolve to the same outcome shown; everything else on
    # this call is discarded, so the removal that lands is exactly the one confirmed.
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "remove_positions")
        if entry is None:
            return _gate.unknown_token_result("remove_positions")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "removals", "position_id")
    raw_removals = input_data.get("removals")
    if raw_removals:
        if len(raw_removals) > _MAX_POSITION_REMOVE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_removals)} removals exceeds the "
                        f"{_MAX_POSITION_REMOVE_BATCH}-removal cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "remove_batch",
                raw_removals,
                [{"position_id": pid} for pid in raw_removals],
                "Remove positions",
            )
        results = [
            {
                "position_id": position_id,
                **_apply_position_one_safe("remove", {"position_id": position_id}),
            }
            for position_id in raw_removals
        ]
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    if gated or promised is not None:
        position_id = input_data.get("position_id")
        row = _ps.hydrated_one(position_id) if position_id else None
        if not row:
            return _gate.preview_failed_result(
                "position not found — call get_portfolio for current position_ids"
            )
        change = {
            "type": "remove",
            "position_id": position_id,
            "reason": input_data.get("reason", ""),
            "display": {
                "symbol": row.get("symbol") or row.get("ticker", ""),
                "name": row.get("label", ""),
                "venue": row.get("category", ""),
                "quantity": row.get("quantity"),
            },
        }
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # the position could have changed (a buy/sale, another removal) since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("remove_positions")

    return await _apply_position("remove", input_data)


def _apply_position_one(kind: str, input_data: dict) -> dict:
    from backend.services import positions as _ps

    data = dict(input_data)
    if "changes" in data:
        changes = dict(data["changes"])
        changes.pop("_grant_managed_ok", None)  # strip bypass flag — only equity.py may set it
        data["changes"] = changes
    return _ps.apply_position_change({"type": kind, **data})


async def _apply_position(kind: str, input_data: dict):
    return json.dumps(_apply_position_one(kind, input_data)), None
