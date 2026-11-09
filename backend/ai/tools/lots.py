"""Tax-lot + realized-P&L tools (P2-INV1c-a).

Lucius reads lots / realized P&L and, on an interactive/connector turn, PREVIEWS an
acquisition or disposal before writing it (a disposal moves money/tax-relevant state):

  * ``get_lots`` / ``get_realized_pnl``       — reads (proposed_change = None)
  * ``acquire_lots`` / ``dispose_lots`` / ``delete_lots`` /
    ``undo_lot_disposals`` — ONE tool per write (harness reliability program,
    2026-07-19 — collapses the former ``propose_lot_*``/``apply_lot_*`` pairs; see
    ``portfolio.py``'s ``remove_positions`` for the full rationale). On a
    card-capable/attended turn: PREVIEWS (computes the realized-gain impact — which lots
    get consumed, the per-lot long/short term, the total gain — and returns it as a
    ``proposed_change``; writes NOTHING) and PAUSES on its own ``tool_use_id``.
    ``assistant.resolve_pending_tool_use`` re-runs it on approval with the STORED args —
    the model is never in that path. An unattended/automated caller writes immediately, no
    card — mirrors today's direct-apply behavior for broker syncs / imports. Skill
    activation is not an exception to confirmation. All routes through
    ``services.lots.apply_lot_change`` (checkpoint-covered, idempotency-keyed).

ALL realized-gain arithmetic, lot selection, holding-period classification and FX live
in ``services/lots.py`` — this module only marshals I/O and shapes the confirm card
(RFC P2-INV1 §9). Respects the dependency rule (ai → services → db → core).

**Arity naming (harness reliability program, 2026-07-19 owner incident — "why is
delete_account still singular / account_id a list?"):** ``acquire_lots``
(``acquisitions``), ``dispose_lots`` (``disposals``), ``delete_lots``
(``deletions``) and ``undo_lot_disposals`` (``event_ids``) all batch over an array
of independent entities — the SAME shape that got ``portfolio.py``'s
``resolve_instrument``/``update_instrument`` renamed to their plural forms. These four
are DELIBERATELY LEFT SINGULAR anyway: each pairs with a same-named ``propose_lot_*``
tool that previews exactly ONE item (there is no ``propose_lot_acquisitions`` —  the
interactive preview-then-confirm flow is inherently single-item; the apply side ALSO
takes a batch for unattended/imported flows, e.g. booking a whole broker statement).
Renaming only the apply half would break the propose→apply name symmetry the model
relies on to find the tool that commits a given proposal, and the batch param name
already discloses the arity honestly in both the schema and the tool description. See
``portfolio.py``'s identical note for the full reasoning.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_batch_only

# Domain validation errors compute_disposal raises (bad lot selection / oversell).
from backend.core.errors import OpenNetWorthError
from backend.services import labels, lots_window
from backend.services import lots as lots_service

# Batch-call caps (P4 tool-pluralization sweep) — see portfolio.py's identical note. These
# four guard the write-side batch loops below; get_lots' own cap is smaller since it also
# multiplies by the per-position lot-row limit.
_MAX_LOTS_POSITION_BATCH = 20
_MAX_LOT_ACQUIRE_BATCH = 100
_MAX_LOT_DISPOSE_BATCH = 100
_MAX_LOT_DELETE_BATCH = 100
_MAX_LOT_DISPOSE_UNDO_BATCH = 100


def _position_display(position_id: str) -> dict:
    """Symbol / name / venue for the confirm card. Best-effort: a hydrated row when the
    position exists, else just the id (the math doesn't depend on this)."""
    from backend.services import positions as _ps

    row = _ps.hydrated_one(position_id)
    if not row:
        return {"position_id": position_id}
    return {
        "position_id": position_id,
        "symbol": row.get("symbol") or row.get("ticker", ""),
        "name": row.get("label", ""),
        "venue": row.get("category", ""),
    }


def _account_name(account_id: str | None) -> str | None:
    """Best-effort account name for the confirm card's proceeds destination (INV1-2).
    None when no account was chosen or it can't be resolved (the math doesn't need it)."""
    if not account_id:
        return None
    from backend.core.errors import ValidationError
    from backend.services import accounts as accounts_service
    from backend.services import ledger as _svc

    try:
        resolved = accounts_service.resolve_account_ref(str(account_id))
    except ValidationError:
        return None
    acc = _svc.accounts.find_by_id(resolved) if resolved else None
    return acc.get("name") if acc else None


# ── Reads ─────────────────────────────────────────────────────────────────────


class GetLotsInput(ToolInput):
    """Schema-of-record for ``get_lots`` (see ``schema_from_model``)."""

    position_id: Optional[str] = Field(
        default=None,
        description=(
            "position_id from get_portfolio. Required unless `position_ids` or `ticker` is used."
        ),
    )
    position_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Batch form: lots for SEVERAL positions in one call — e.g. every AAPL position held across custodians. `limit`/`offset`/`open_only` apply to EACH position individually (not across the combined set); each position gets its OWN `totals` block — a cost basis is never meaningfully summed across different instruments/currencies, so there is no combined grand total. Omit the single `position_id` field when using this."
        ),
    )
    ticker: Optional[str] = Field(
        default=None,
        description="Every position holding this ticker, at every custodian.",
    )
    open_only: Optional[bool] = Field(
        default=None,
        description=(
            "Only lots with remaining quantity (default false — closed lots are included). A long-held position accumulates many fully-consumed lots; pass true when you only care about what is still sellable."
        ),
    )
    limit: Optional[int] = Field(
        default=None, description=f"Max lot rows to return (default {_b.DEFAULT_LOT_LIMIT})."
    )
    offset: Optional[int] = Field(
        default=None,
        description=(
            "Zero-based row offset (default 0) — page through with this when a position has more lots than one page holds. Every lot is reachable."
        ),
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "Window mode, with `date_to` and NO position: every lot ACQUIRED in the window across all holdings (ISO YYYY-MM-DD, inclusive) — 'what did I buy this month'."
        ),
    )
    date_to: Optional[str] = Field(
        default=None, description="Window mode: last acquisition date, ISO YYYY-MM-DD, inclusive."
    )
    account_id: Optional[str] = Field(
        default=None,
        description="Window mode only: lots at ONE account (id from list_accounts). Omit for every account.",
    )


@tool(
    "get_lots",
    description=(
        "List the tax lots (acquisition tranches) for a position — each with acquired_at, remaining quantity, effective unit cost, remaining cost basis, and term-to-date (long/short as of today). Call get_portfolio first to get the position_id, or pass `ticker`. Use this BEFORE proposing a specific-lot sale so you can name the right lot ids.\n\nLots come back FIFO-ordered (oldest first — the order a sale consumes them), capped at 50 rows. The `totals` block is computed over ALL lots, not just the returned page, so quantity and cost-basis questions stay correct even when the list is truncated — never sum the rows yourself to answer one.\n\nThe SAME instrument held at several custodians is several positions (the custodian model) — pass `position_ids` (a list) to read lots for all of them in one call instead of one call per account.\n\nWINDOW MODE — `date_from` + `date_to` with no position: every lot acquired in the window across all holdings, oldest first, each with instrument, account and `kind`. A `buy`/`drp` carries `paid` and `paid_base` (base currency at the trade-date rate; fees included); a `transfer_in` (shares moved between brokers) or `grant` (a vest) carries `cost_basis`/`cost_basis_base` instead — it is not money invested. Sales are `get_realized_pnl` over the same dates."
    ),
    input_schema=schema_of(GetLotsInput, tool_name="get_lots"),
)
async def get_lots(input_data: dict):
    raw_position_ids = input_data.get("position_ids")
    ticker = input_data.get("ticker")
    if ticker and not raw_position_ids and not input_data.get("position_id"):
        from backend.services import positions as _ps

        raw_position_ids = _ps.position_ids_for_ticker(ticker)
        if not raw_position_ids:
            return json.dumps(
                {"error": f"no position holds ticker {ticker!r} — get_portfolio lists what is held"}
            ), None
        if len(raw_position_ids) == 1:
            maps = labels.label_maps(instruments=True, accounts=True)
            payload = _get_lots_one(raw_position_ids[0], input_data, maps)
            return json.dumps(payload, ensure_ascii=False), None
    if raw_position_ids:
        if len(raw_position_ids) > _MAX_LOTS_POSITION_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_position_ids)} position_ids exceeds the "
                        f"{_MAX_LOTS_POSITION_BATCH}-position cap for one call — split into multiple calls."
                    )
                }
            ), None
        # ONE map build for the whole batch, not one per position.
        maps = labels.label_maps(instruments=True, accounts=True)
        results = [_get_lots_one(pid, input_data, maps) for pid in raw_position_ids]
        return json.dumps(
            {"results": results, "showing": len(results), "total": len(results)},
            ensure_ascii=False,
        ), None

    position_id = input_data.get("position_id")
    if not position_id and (input_data.get("date_from") or input_data.get("date_to")):
        return await _lots_in_window(input_data), None
    if not position_id:
        return json.dumps({"error": _missing_position_error(input_data)}), None
    maps = labels.label_maps(instruments=True, accounts=True)
    return json.dumps(_get_lots_one(position_id, input_data, maps), ensure_ascii=False), None


def _missing_position_error(input_data: dict) -> str:
    # Dispatch validates types only, so an argument the schema doesn't have reaches the handler
    # silently; naming it is what lets the model fix the call instead of abandoning it.
    message = (
        "position_id is required (or position_ids for a batch, ticker for every position "
        "holding it, or date_from + date_to for every lot acquired in a window)"
    )
    unknown = sorted(set(input_data) - set(GetLotsInput.model_fields))
    if unknown:
        message += f"; not a get_lots argument: {', '.join(unknown)}"
    return message


async def _lots_in_window(input_data: dict) -> str:
    out = await asyncio.to_thread(
        lots_window.lots_acquired,
        input_data.get("date_from") or "",
        input_data.get("date_to") or "",
        input_data.get("account_id"),
    )
    maps = labels.label_maps(instruments=True, accounts=True)
    out["lots"] = [labels.label_instrument_row(lot, maps) for lot in out["lots"]]
    return json.dumps(out, ensure_ascii=False)


def _get_lots_one(position_id: str, input_data: dict, maps: labels.MapSet | None = None) -> dict:
    """The single-position get_lots payload — shared by the singular call and each item
    of a batched ``position_ids`` call. ``totals`` is ALWAYS computed over every lot of
    THIS position (never merged across positions in a batch — see the field's own note)."""
    lots = lots_service.lots_for_position(position_id)
    if input_data.get("open_only"):
        lots = [lot for lot in lots if float(lot.get("remaining_qty") or 0) > 0]

    # Aggregate over the FULL set BEFORE capping. Capping a money-bearing list without
    # this would let the model total a partial page and report a wrong cost basis — the
    # exact failure this bounding work must not introduce.
    totals = _lot_totals(position_id, lots)

    limit = (
        int(input_data["limit"]) if input_data.get("limit") is not None else _b.DEFAULT_LOT_LIMIT
    )
    offset = int(input_data["offset"]) if input_data.get("offset") is not None else 0
    total = len(lots)
    page = lots[offset : offset + limit] if limit > 0 else lots[offset:]
    end = offset + len(page)
    payload = {
        "position_id": position_id,
        # A lot row identifies WHAT was bought only by an opaque uuid; attach the
        # instrument name/ticker + account name beside it (additive — nothing removed).
        "lots": [labels.label_instrument_row(lot, maps) for lot in page],
        "totals": totals,
        "showing": len(page),
        "offset": offset,
        "total": total,
        "has_more": end < total,
    }
    if end < total:
        # A truncated lot list without this note is a tax-reporting hazard, not a UX one:
        # summing a partial page gets both realized gain and the long/short-term split
        # wrong. Say so, give the exact next call, and point at the full-set totals.
        payload["note"] = (
            f"Showing lots {offset + 1}–{end} of {total}. This page is PARTIAL — do NOT compute cost basis, realized gain or holding-period splits from it. Use the "
            f"`totals` block (computed over all {total}) or page with offset={end}."
        )
    return payload


def _lot_totals(position_id: str, lots: list[dict]) -> dict:
    """Quantity/basis aggregates over EVERY lot passed in (never a page).

    ``remaining_basis`` comes from ``lots_service.position_remaining_basis`` — summing the
    unrounded qty × effective-unit-cost of each open lot and rounding ONCE (MATH-44) —
    never a re-sum of the already-2dp-rounded per-lot ``remaining_basis`` values, which
    compounds rounding error (three lots of 3 × 0.335: 3.03 re-summed vs. the service's
    3.02). A cost-unknown lot contributes 0 to that sum (mirroring the service) but is
    COUNTED separately — a basis total silently missing some lots is the lots-domain
    version of a wrong money answer, so the caller is told rather than left to assume the
    figure is complete.
    """
    open_lots = [lot for lot in lots if float(lot.get("remaining_qty") or 0) > 0]
    basis_known = [lot for lot in open_lots if lot.get("remaining_basis") is not None]
    return {
        "lot_count": len(lots),
        "open_lot_count": len(open_lots),
        "remaining_quantity": round(
            sum(float(lot.get("remaining_qty") or 0) for lot in open_lots), 8
        ),
        "remaining_basis": lots_service.position_remaining_basis(position_id),
        "basis_unknown_lot_count": len(open_lots) - len(basis_known),
        "note": (
            "Totals cover every lot, not just the returned page. remaining_basis excludes "
            f"{len(open_lots) - len(basis_known)} open lot(s) with unknown cost."
            if len(basis_known) != len(open_lots)
            else "Totals cover every lot, not just the returned page."
        ),
    }


class GetRealizedPnlInput(ToolInput):
    """Schema-of-record for ``get_realized_pnl`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    date_to: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    instrument_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to disposals of ONE instrument (id from get_portfolio or resolve_instruments). Matches a fully-sold holding too, since realized events outlive the position. Omit for every instrument."
        ),
    )
    account_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to disposals booked at ONE account (id from list_accounts) — the custodian's securities account, not its cash wallet. Omit for every account."
        ),
    )


@tool(
    "get_realized_pnl",
    description=(
        "Realized gains/losses over an optional date range / instrument / account: the disposal events, the long-vs-short split, the per-instrument breakdown, and the total in the portfolio base currency. Base figures follow the historical-rate lock: a gain stamped at disposal is trusted while it matches the current base, recomputed from native + current FX only after a base change or on an unstamped legacy row — a past gain keeps its disposal-date value as rates drift."
    ),
    input_schema=schema_of(GetRealizedPnlInput, tool_name="get_realized_pnl"),
)
async def get_realized_pnl(input_data: dict):
    out = lots_service.realized_pnl(
        date_from=input_data.get("date_from"),
        date_to=input_data.get("date_to"),
        instrument_id=input_data.get("instrument_id"),
        account_id=input_data.get("account_id"),
    )
    return json.dumps(out, ensure_ascii=False), None


class GetFeesSummaryInput(ToolInput):
    """Schema-of-record for ``get_fees_summary`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    date_to: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD (inclusive)")
    instrument_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to fees paid on ONE instrument (id from get_portfolio or resolve_instruments). Omit for every instrument."
        ),
    )
    account_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow to fees paid at ONE account (id from list_accounts). Omit to get every account AND the `by_account` per-venue rollup that lets you compare what each broker costs — filtering to one account collapses that comparison."
        ),
    )


@tool(
    "get_fees_summary",
    description=(
        "Total trading fees/commissions the user has PAID — acquisition lot fees plus disposal (selling) fees — over an optional date range / instrument / account, totalled in the portfolio base currency (recomputed from native legs + current FX, never trusted from storage). These are informational 'fees paid'; they ALREADY amortize into cost basis and reduce sale proceeds in the realized-P&L math, so do NOT subtract them from gains again when reporting. Returns acquisition_fees_base, disposal_fees_base, total_fees_base, a per-instrument breakdown, and by_account — a per-venue rollup (account_name, institution, fees_base, traded notional_base, fee_rate_pct) for comparing what each broker costs the user. NOTE: fees exist only where trades were recorded WITH their commission — if by_account shows zero fees on many trades, the fees were likely never captured, not free."
    ),
    input_schema=schema_of(GetFeesSummaryInput, tool_name="get_fees_summary"),
)
async def get_fees_summary(input_data: dict):
    out = lots_service.fees_summary(
        date_from=input_data.get("date_from"),
        date_to=input_data.get("date_to"),
        instrument_id=input_data.get("instrument_id"),
        account_id=input_data.get("account_id"),
    )
    return json.dumps(out, ensure_ascii=False), None


# ── Propose (approve-tier: return a proposed_change; write in the apply path) ───


def _build_lot_acquire_change(input_data: dict) -> tuple[Optional[dict], Optional[tuple]]:
    """Shared preview-build for the ``acquire_lots`` confirm gate — the former body of
    ``propose_lot_acquire``. Called only when previewing or re-verifying a confirm's
    outcome. Returns ``(change, None)`` or, on a refused double-book,
    ``(None, ready_to_return_tool_result_tuple)`` (preserves the exact `existing_lot_id`
    error shape)."""
    # Surface the identical-lot refusal at propose time (apply enforces it too), so the
    # user never confirms a diff the choke point will reject.
    if not input_data.get("allow_duplicate"):
        dup = lots_service.find_double_book_candidate(
            input_data["position_id"],
            {
                "quantity": input_data["quantity"],
                "unit_cost": input_data.get("unit_cost"),
                "acquired_at": input_data.get("acquired_at", ""),
                "kind": input_data.get("kind", "buy"),
            },
        )
        if dup:
            return None, (
                json.dumps(
                    {
                        "error": (
                            f"an identical open lot already exists on this position "
                            f"(lot_id={dup['id']}, {dup.get('acquired_at')}, "
                            f"qty {dup.get('original_qty')} @ {dup.get('unit_cost')}) — this trade looks already booked; do NOT book it again. Only if the user confirms these are two separate identical fills, re-propose with allow_duplicate=true."
                        ),
                        "existing_lot_id": dup["id"],
                    }
                ),
                None,
            )
    disp = _position_display(input_data["position_id"])
    change = {
        "type": "lot_acquire",
        "position_id": input_data["position_id"],
        "quantity": input_data["quantity"],
        "unit_cost": input_data.get("unit_cost"),
        "cost_currency": input_data.get("cost_currency"),
        "fees": input_data.get("fees", 0),
        "acquired_at": input_data.get("acquired_at", ""),
        "kind": input_data.get("kind", "buy"),
        "idempotency_key": input_data.get("idempotency_key"),
        "allow_duplicate": bool(input_data.get("allow_duplicate", False)),
        "note": input_data.get("note", ""),
        "funding_account_id": input_data.get("funding_account_id") or None,
        "display": {
            **disp,
            "quantity": input_data["quantity"],
            "unit_cost": input_data.get("unit_cost"),
            "cost_currency": input_data.get("cost_currency"),
            # The confirm card must show the fee (a captured fee was previously booked
            # silently — fee-capture sweep 2026-07-13).
            "fees": input_data.get("fees") or 0,
            "acquired_at": input_data.get("acquired_at", ""),
            "kind": input_data.get("kind", "buy"),
            "funding_account_id": input_data.get("funding_account_id") or None,
            "funding_account_name": _account_name(input_data.get("funding_account_id")),
        },
    }
    return change, None


# Shared across propose_lot_dispose and dispose_lots — the "true ONLY for genuinely
# separate fills..." wording is byte-identical on every dispose surface (root, and each
# dispose_lots batch item).
_ALLOW_DUPLICATE_DISPOSAL_DESC = "true ONLY for genuinely separate fills — otherwise a disposal matching an already-recorded sale (same date, qty, price) is REFUSED as a double-book."

# Dispose-side field prose. Shared by the root schema and each `disposals` batch item so the
# two can never drift. Every refusal/default named here is enforced in services/lots.py
# (compute_disposal / _apply_dispose / _maybe_deposit_proceeds), not restated from memory.
_DIS_POSITION_ID_DESC = "Which position (one instrument at one account) is being sold down — position_id from get_portfolio. The same instrument at two custodians is two disposals."
_DIS_QUANTITY_DESC = "Number of UNITS disposed (shares/coins), never the cash received. Selling more than the position's open lots hold is refused before any write."
_DIS_PROCEEDS_PER_UNIT_DESC = "GROSS sale price per unit in proceeds_currency, BEFORE disposal_fees. A taxable sale whose NET (this − disposal_fees/quantity) is not positive is REFUSED: booking a $0 sale would record the whole cost basis as a fabricated loss. To write off or gift a holding for no value use is_taxable=false."
_DIS_PROCEEDS_CURRENCY_DESC = "ISO 4217 code the proceeds and fees are quoted in. Omitted: falls back to the position's cost_currency, then the base currency. Each consumed lot's basis is FX-converted into THIS currency before subtracting — currencies are never mixed."
_DIS_FEES_DESC = "TOTAL selling commission for this disposal in proceeds_currency (not per unit). Reduces the realized gain and any cash deposited. Copy it off the confirmation; omit when the source shows none."
_DIS_DISPOSED_AT_DESC = "Sale date, ISO YYYY-MM-DD. Defaults to TODAY — pass the real trade date for a backdated sale, since this ends the holding-period clock and so decides each consumed lot's long- vs short-term classification."
_DIS_METHOD_DESC = "'fifo' (default) consumes oldest lots first, ordered by acquired_at then created_at. 'specific_id' consumes exactly the lots in lot_selection and REQUIRES it. Any other value is refused. The choice changes which basis is used, so it changes both the realized gain and the long/short split."
_DIS_LOT_SELECTION_DESC = "[{lot_id, qty}] naming exactly which lots to consume — REQUIRED when method is 'specific_id', ignored under fifo. Call get_lots for the ids. The qty values must sum to `quantity` or the disposal is refused."
_DIS_IS_TAXABLE_DESC = "true (default) books a realized gain/loss. false marks a transfer-out or gift: carries the cost basis out and realizes NOTHING (realized_gain is null, not 0), and is the only accepted way to record a disposal with no proceeds."
_DIS_TRANSFER_TO_DESC = "For shares MOVED to another of the user's own accounts (e.g. a workplace custodian → IBKR): the destination account id or exact name from list_accounts. Requires is_taxable=false, proceeds_per_unit=0 and no proceeds_account_id — a transfer moves shares, not money. Activity then shows ONE 'Transfer · <qty> units → <account>' row instead of an anonymous transfer-out. This records WHERE the shares went; it does NOT create the receiving lots — acquire them on the destination position with kind=transfer_in (same unit_cost and acquired_at as the consumed lots, so basis and holding period carry over). Omit for a gift or write-off."
_DIS_PROCEEDS_ACCOUNT_DESC = "Cash account to deposit the NET proceeds into (quantity × proceeds_per_unit − disposal_fees) — account id from list_accounts, normally the custodian's cash wallet. Booked as a transfer dated disposed_at, FX-converted to that account's currency. OMITTING IT BOOKS NO CASH: the lot and cash ledgers are separate, so the sale then removes the units with nothing credited back."
_DIS_IDEMPOTENCY_DESC = "Dedupe key (broker order id, or email id + symbol + date). A second call with the same key is a no-op returning the existing event_id — automated flows MUST pass one so a reprocessed email or retry can never book the same sale twice."


# lot_selection's per-lot entry — a loose (non-ToolInput) nested item shared by
# propose_lot_dispose's root field and dispose_lots's root + batch-item fields; no
# docstring (would leak into the item schema as a "description" key) and no
# additionalProperties, matching the literal's bare {lot_id, qty} shape.
class LotSelectionItem(BaseModel):
    lot_id: Optional[str] = Field(
        default=None,
        description=(
            "Which lot to sell from — lot id from get_lots on this same position. An id that is not an open lot of this position is refused."
        ),
    )
    qty: Optional[float] = Field(
        default=None,
        description=(
            "Units to take from THIS lot (not the whole sale). Across the selection these must sum to the disposal's `quantity` — any under- or over-fill is refused rather than silently adjusted."
        ),
    )


def _build_lot_dispose_change(input_data: dict) -> tuple[Optional[dict], Optional[tuple]]:
    """Shared preview-build for the ``dispose_lots`` confirm gate — the former body of
    ``propose_lot_dispose``. Called only when previewing or re-verifying a confirm's
    outcome (the caller has already ensured ``input_data["idempotency_key"]`` is set — see
    ``dispose_lots``). Returns ``(change, None)`` or, on a refused duplicate-sale/bad
    selection, ``(None, ready_to_return_tool_result_tuple)`` (preserves the exact
    `existing_event_id` error shape)."""
    change = {
        "type": "dispose",
        "position_id": input_data["position_id"],
        "quantity": input_data["quantity"],
        "proceeds_per_unit": input_data.get("proceeds_per_unit", 0),
        "proceeds_currency": input_data.get("proceeds_currency"),
        "disposal_fees": input_data.get("disposal_fees", 0),
        "disposed_at": input_data.get("disposed_at", ""),
        "method": input_data.get("method", "fifo"),
        "lot_selection": input_data.get("lot_selection") or [],
        "is_taxable": input_data.get("is_taxable", True),
        "proceeds_account_id": input_data.get("proceeds_account_id") or None,
        "transfer_to_account_id": input_data.get("transfer_to_account_id") or None,
        "allow_duplicate": bool(input_data.get("allow_duplicate", False)),
        "idempotency_key": input_data.get("idempotency_key"),
        "note": input_data.get("note", ""),
    }
    # Surface the duplicate-sale refusal at propose time (apply enforces it too), so the user
    # never confirms a sale the choke point will reject as an already-booked double-entry.
    if not change["allow_duplicate"]:
        dup = lots_service.find_duplicate_disposal_candidate(change)
        if dup:
            return None, (
                json.dumps(
                    {
                        "error": (
                            f"a disposal of {dup.get('quantity')} units on "
                            f"{dup.get('disposed_at')} @ {dup.get('proceeds_per_unit')} "
                            f"{dup.get('proceeds_currency')} is already recorded on this "
                            f"position (event_id={dup['id']}) — this sale looks already booked; do NOT book it again. Only if the user confirms these are two separate fills, re-propose with allow_duplicate=true."
                        ),
                        "existing_event_id": dup["id"],
                    }
                ),
                None,
            )
    # Compute the realized-gain DRAFT in the service (the math home). A bad selection /
    # oversell raises here — surface it as an error so the user fixes it (no proposal).
    try:
        draft = lots_service.preview_disposal(change)
    except OpenNetWorthError as exc:
        return None, (json.dumps({"error": exc.message or str(exc)}), None)
    if draft.get("error"):
        return None, (
            json.dumps(
                {"error": "position not found — call get_portfolio for current position_ids"}
            ),
            None,
        )

    disp = _position_display(input_data["position_id"])
    change["type"] = "lot_dispose"
    change["display"] = {
        **disp,
        "quantity": draft.get("quantity"),
        "method": draft.get("method"),
        "is_taxable": draft.get("is_taxable"),
        "proceeds_per_unit": draft.get("proceeds_per_unit"),
        "proceeds_currency": draft.get("proceeds_currency"),
        # Fee-capture sweep 2026-07-13: the fee must be visible before booking.
        "disposal_fees": change.get("disposal_fees") or 0,
        # FE-4: the confirm card shows the booked date + total proceeds before applying.
        # disposed_at is always resolved by the service (defaults to today); total_proceeds
        # = qty x proceeds/unit (None for a transfer-out/gift with no proceeds).
        "disposed_at": draft.get("disposed_at"),
        "total_proceeds": (
            (draft.get("quantity") or 0) * draft["proceeds_per_unit"]
            if draft.get("proceeds_per_unit") is not None
            else None
        ),
        "cost_basis_known": draft.get("cost_basis_known"),
        # INV1-2: where the net proceeds land (None = not added to any account). The
        # confirm card shows the destination so the user sees the cash will be booked.
        "proceeds_account_id": change.get("proceeds_account_id"),
        "proceeds_account_name": _account_name(change.get("proceeds_account_id")),
        # A share move names where the units go — the card shows "→ IBKR" so the user
        # confirms the destination, not just the removal. Resolved by the service (id or
        # name), so the apply re-resolves to the same account the card showed.
        "transfer_to_account_id": draft.get("transfer_to_account_id"),
        "transfer_to_account_name": _account_name(draft.get("transfer_to_account_id")),
        # The realized-gain preview the confirm card shows BEFORE applying.
        "consumptions": draft.get("consumptions"),
        "realized_gain": draft.get("realized_gain"),
        "realized_gain_base": draft.get("realized_gain_base"),
        "base_currency": draft.get("base_currency"),
    }
    return change, None


def _build_lot_delete_change(input_data: dict) -> tuple[Optional[dict], Optional[str]]:
    """Shared preview-build for the ``delete_lots`` confirm gate — the former body of
    ``propose_lot_delete``. Called only when previewing or re-verifying a confirm's
    outcome. Returns ``(change, None)`` or ``(None, error_message)``."""
    from backend.db.lot_store import LotStore

    lot_id = input_data.get("lot_id")
    lot = LotStore().find_by_id(lot_id) if lot_id else None
    if not lot:
        return None, "lot not found — call get_lots for current lot ids"
    remaining = float(lot.get("remaining_qty") or 0)
    original = float(lot.get("original_qty") or 0)
    if original > 0 and remaining < original:
        return None, (
            "shares from this lot have been sold, so it can't be removed — undo that sale first "
            "(undo_lot_disposals), or correct the lot with update_lots instead"
        )
    disp = _position_display(input_data.get("position_id", ""))
    change = {
        "type": "lot_delete",
        "lot_id": lot_id,
        "position_id": input_data.get("position_id"),
        "reason": input_data.get("reason", ""),
        "display": {
            **disp,
            "lot_id": lot_id,
            "kind": lot.get("kind", ""),
            "acquired_at": lot.get("acquired_at") or "(no date)",
            "remaining_qty": remaining,
            "unit_cost": lot.get("unit_cost"),
            "cost_currency": lot.get("cost_currency"),
        },
    }
    return change, None


# ── Apply (write ONLY; call after explicit confirmation) ────────────────────────

_POSITION_AFTER = {
    "quantity": "position_quantity_after",
    "cost_basis": "position_cost_basis_after",
    "cost_currency": "position_cost_currency_after",
}


def _for_model(result: dict, **moved) -> dict:
    """A lot write's service reply, relabelled for the model.

    The service spreads the position's re-derived figures into its reply under bare
    ``quantity``/``cost_basis`` — right for the REST caller that redraws the holding, but a
    model reads a bare ``quantity`` as what THIS call moved: a 2-share sale came back as
    ``quantity: 40.1867`` and Lucius told the user the sale had gone wrong (live,
    2026-10-11). So the position's figures are named for what they are, and the call's own
    amount (``quantity_sold`` …) is stated beside them — only when the call really moved
    something: an idempotent replay moved nothing."""
    out = {_POSITION_AFTER.get(k, k): v for k, v in result.items()}
    if out.get("ok") and not out.get("idempotent"):
        out.update({k: v for k, v in moved.items() if v is not None})
    return out


def _batch_summary(results: list[dict]) -> dict:
    """The shared {results, showing, total, succeeded, failed[, note]} envelope every
    batched lot-write below returns — partial failure must stay legible (which of N
    items failed), never collapsed into one whole-call verdict."""
    succeeded = sum(1 for r in results if r.get("ok"))
    failed = len(results) - succeeded
    out: dict = {
        "results": results,
        "showing": len(results),
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
    }
    already_booked = sum(1 for r in results if r.get("ok") and r.get("idempotent"))
    notes = []
    if failed:
        notes.append(
            f"{failed} of {len(results)} failed — see each item's own `error`. The rest succeeded; this is NOT a whole-call failure."
        )
    if already_booked:
        out["already_booked"] = already_booked
        notes.append(
            f"{already_booked} of {len(results)} were ALREADY booked (same idempotency_key) — "
            "nothing new was written for them; do NOT report them as new work."
        )
    if notes:
        out["note"] = " ".join(notes)
    return out


# Acquire-side field prose. Shared by the root schema and each `acquisitions` batch item so
# the two can never drift apart. Every fact here is enforced in services/lots.py
# (_apply_acquire / _find_identical_open_lot) or db/lot_store.py — not restated from memory.
_ACQ_POSITION_ID_DESC = "Which position (one instrument at one account) this lot lands on — position_id from get_portfolio. An unknown id is refused. If the account does not hold this instrument yet, use add_positions instead."
_ACQ_QUANTITY_DESC = "Number of UNITS acquired (shares/coins), never the trade's cash value. Must be positive and finite — zero or negative is refused rather than booking an empty lot."
_ACQ_UNIT_COST_DESC = "Cost per unit in cost_currency, EXCLUDING fees — `fees` amortizes on top (effective basis/unit = unit_cost + fees/quantity). Omit ONLY when the basis is genuinely unknown: it stores as null (0 does too), and every later sale of this lot then reports realized_gain=null rather than a fabricated 0."
_ACQ_COST_CURRENCY_DESC = "ISO 4217 code unit_cost and fees are quoted in (e.g. USD). Omitted: falls back to the position's cost_currency, then the portfolio base currency."
_ACQ_FEES_DESC = "TOTAL acquisition commission for this lot in cost_currency (not per unit), amortized into cost basis. Copy it off the broker confirmation; omit when the source shows none — don't estimate. Default 0."
_ACQ_ACQUIRED_AT_DESC = "Acquisition date, ISO YYYY-MM-DD. Drives FIFO order (oldest sells first) and the holding-period clock classifying a later sale long- vs short-term. Omitted = undated: term reports 'unknown' and the double-book guard cannot fire."
_ACQ_KIND_DESC = "buy (default) | drp (dividend reinvestment) | transfer_in | grant | opening. An unrecognized value is silently stored as buy. The double-book guard covers buy/drp only — grant lots dedupe per tranche, so identical same-day vests are allowed."
# Shared by the root field and each acquisitions[] item — the refusal is enforced at the
# choke point (services.lots._find_identical_open_lot), not just previewed.
_ALLOW_DUPLICATE_ACQUIRE_DESC = "true ONLY for genuinely separate identical fills — otherwise a FULLY-OPEN lot on this position with the same kind, acquired_at, quantity and unit_cost is REFUSED as a double-book (returns existing_lot_id). A partially-sold match counts as a real earlier fill, not a duplicate."
_ACQ_FUNDING_DESC = "Cash account to debit for qty × unit_cost + fees, booked WITH the lot (ticker, quantity, memo). Omit only when that debit is already in the ledger. Unknown unit_cost skips the debit. value_source='total' account: omit it, don't ask — the holding is carved out of the total."


class ApplyLotAcquireItem(ToolInput):
    """One acquisition in a batched ``acquire_lots`` call — same fields as the
    single-item form, kept in sync deliberately."""

    position_id: str = Field(description=_ACQ_POSITION_ID_DESC)
    quantity: float = Field(description=_ACQ_QUANTITY_DESC)
    unit_cost: Optional[float] = Field(default=None, description=_ACQ_UNIT_COST_DESC)
    cost_currency: Optional[str] = Field(default=None, description=_ACQ_COST_CURRENCY_DESC)
    fees: Optional[float] = Field(default=None, description=_ACQ_FEES_DESC)
    acquired_at: Optional[str] = Field(default=None, description=_ACQ_ACQUIRED_AT_DESC)
    kind: Optional[str] = Field(default=None, description=_ACQ_KIND_DESC)
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Per-item dedupe key — never one key for the whole batch (see the tool description).",
    )
    allow_duplicate: Optional[bool] = Field(default=None, description=_ALLOW_DUPLICATE_ACQUIRE_DESC)
    note: Optional[str] = Field(default=None)
    funding_account_id: Optional[str] = Field(default=None, description=_ACQ_FUNDING_DESC)


class ApplyLotAcquireInput(ToolInput):
    """Schema-of-record for ``acquire_lots`` (see ``schema_from_model``)."""

    # NOTE: position_id/quantity stay REQUIRED (no default) even though a batch caller
    # using `acquisitions` omits them — registry.dispatch strips `required` before
    # validating (`_IGNORED_KEYWORDS`), so this is schema metadata only and matches what
    # services.lots.apply_lot_change("acquire") actually enforces (test_domain_introspect.py
    # pins this pair).
    position_id: str = Field(description=_ACQ_POSITION_ID_DESC)
    quantity: float = Field(description=_ACQ_QUANTITY_DESC)
    unit_cost: Optional[float] = Field(default=None, description=_ACQ_UNIT_COST_DESC)
    cost_currency: Optional[str] = Field(default=None, description=_ACQ_COST_CURRENCY_DESC)
    fees: Optional[float] = Field(default=None, description=_ACQ_FEES_DESC)
    acquired_at: Optional[str] = Field(default=None, description=_ACQ_ACQUIRED_AT_DESC)
    kind: Optional[str] = Field(default=None, description=_ACQ_KIND_DESC)
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Dedupe key (broker order id, or email id + symbol + date) — a re-apply with the same key is a no-op returning the existing lot_id."
        ),
    )
    allow_duplicate: Optional[bool] = Field(default=None, description=_ALLOW_DUPLICATE_ACQUIRE_DESC)
    note: Optional[str] = Field(default=None)
    funding_account_id: Optional[str] = Field(default=None, description=_ACQ_FUNDING_DESC)
    acquisitions: Optional[List[ApplyLotAcquireItem]] = Field(
        default=None,
        min_length=1,
        description=(
            "Batch form: record MULTIPLE buys/transfers-in/grants in ONE call (e.g. every trade in an imported broker statement). Every item needs its OWN idempotency_key, and the double-book guard applies PER ITEM — one already-booked trade is refused alone while the rest still record. One call covers them all, and raises ONE confirm card for the whole batch."
        ),
    )


@tool(
    "acquire_lots",
    description=(
        "Record a buy / transfer-in / grant as a tax lot on an existing position (so cost basis is exact, not averaged-away). Use this — NOT add_positions — when the instrument is already held in the account. Identify the position by position_id (from get_portfolio). ALWAYS capture the commission: extract the fee/commission line from a broker confirmation into `fees`; omit if the source shows none, never guess. On an interactive turn this raises a confirm card (no write yet); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller records immediately, no card — pass idempotency_key so a reprocessed email/retry can never double-book. For kind=buy, pass funding_account_id to debit cash in the same write; omit it only when that cash debit is already in the ledger — never add_transactions for a buy's cash (that row is unlinked from the lot). Recording a whole statement's worth of trades? Pass `acquisitions` (a list) instead — one call, and ONE confirm card for the whole batch."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ApplyLotAcquireInput, array="acquisitions", tool_name="acquire_lots"
    ),
    mutates=("portfolio",),
    # propose_lot_acquire is the former FIRST HALF of this merge (harness reliability
    # program, 2026-07-19 — see portfolio.py's remove_positions aliases note for the
    # full rationale; identical shape applied here).
    self_stages=True,
)
async def acquire_lots(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "acquire_lots")
        if entry is None:
            return _gate.unknown_token_result("acquire_lots")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "acquisitions")
    raw_acquisitions = input_data.get("acquisitions")
    if raw_acquisitions:
        if len(raw_acquisitions) > _MAX_LOT_ACQUIRE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_acquisitions)} acquisitions exceeds the "
                        f"{_MAX_LOT_ACQUIRE_BATCH}-acquisition cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "lot_acquire_batch",
                raw_acquisitions,
                [
                    {
                        "position_id": i.get("position_id"),
                        "kind": i.get("kind"),
                        "quantity": i.get("quantity"),
                        "unit_cost": i.get("unit_cost"),
                    }
                    for i in raw_acquisitions
                ],
                "Record acquisitions",
            )
        results = []
        for item in raw_acquisitions:
            try:
                one = _apply_lot_acquire_one(item)
            except OpenNetWorthError as exc:
                one = {"error": exc.message or str(exc)}
            results.append({"position_id": item.get("position_id"), **one})
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    if gated or promised is not None:
        change, err_result = _build_lot_acquire_change(input_data)
        if err_result is not None:
            return err_result
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # e.g. another double-book of the same fill could have landed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("acquire_lots")

    return json.dumps(_apply_lot_acquire_one(input_data), ensure_ascii=False), None


def _apply_lot_acquire_one(input_data: dict) -> dict:
    kind = input_data.get("kind", "buy")
    result = lots_service.apply_lot_change(
        {
            "type": "acquire",
            "position_id": input_data["position_id"],
            "quantity": input_data["quantity"],
            "unit_cost": input_data.get("unit_cost"),
            "cost_currency": input_data.get("cost_currency"),
            "fees": input_data.get("fees", 0),
            "acquired_at": input_data.get("acquired_at", ""),
            "kind": kind,
            "idempotency_key": input_data.get("idempotency_key"),
            "allow_duplicate": bool(input_data.get("allow_duplicate", False)),
            "note": input_data.get("note", ""),
            "funding_account_id": input_data.get("funding_account_id") or None,
            "source": "ai",
        }
    )
    if result.get("idempotent"):
        result["_next_step"] = (
            "This trade was ALREADY booked (same idempotency_key) — no lot was created. Do NOT add a cash transaction for it again."
        )
    elif result.get("cash_leg_id"):
        result["_next_step"] = (
            "Lot recorded and cash debited from funding_account_id — do NOT add a second cash transaction."
        )
    elif kind == "buy":
        qty = input_data["quantity"]
        unit_cost = input_data.get("unit_cost") or 0
        fees = input_data.get("fees") or 0
        ccy = input_data.get("cost_currency", "")
        result["_next_step"] = (
            f"Lot recorded. REQUIRED: call add_transaction (kind=investment, "
            f"amount={qty * unit_cost + fees:.4f}, "
            f"currency={ccy}) to debit the brokerage cash account. Skipping this leaves the account balance wrong."
        )
    return _for_model(result, quantity_acquired=input_data["quantity"])


class ApplyLotDeleteItem(ToolInput):
    """One deletion in a batched ``delete_lots`` call — same fields as the
    single-item form."""

    lot_id: str = Field(
        description=(
            "The lot to remove — lot id from get_lots. Must be FULLY OPEN: a lot any disposal has already consumed part of is refused (close it with dispose_lots instead). Deleting a lot erases its cost basis, so the parent position's quantity and basis are resynced from the lots that remain."
        )
    )
    position_id: str = Field()


class ApplyLotDeleteInput(ToolInput):
    """Schema-of-record for ``delete_lots`` (see ``schema_from_model``)."""

    lot_id: Optional[str] = Field(
        default=None, description="Required unless `deletions` (the batch form) is used."
    )
    position_id: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None, description="Why this lot should be removed")
    deletions: Optional[List[ApplyLotDeleteItem]] = Field(
        default=None,
        description=(
            "Batch form: delete SEVERAL orphaned/duplicate lots in ONE call. Each is deleted independently (a partially-consumed lot is refused for that item only). One call covers them all, and raises ONE confirm card for the whole batch."
        ),
    )


@tool(
    "delete_lots",
    description=(
        "Delete a specific lot that is fully open (no disposals have consumed it). Use this to remove orphaned or duplicate lots — e.g. a spurious 'opening' placeholder left behind when a real buy lot already exists for the same position. Call get_lots first to identify the lot_id. On an interactive turn this raises a confirm card (no write yet); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller deletes immediately, no card. Cleaning up several duplicate/orphaned lots at once? Pass `deletions` (a list) instead — one call, and ONE confirm card for the whole batch."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ApplyLotDeleteInput, array="deletions", tool_name="delete_lots"
    ),
    mutates=("portfolio",),
    # propose_lot_delete is the former FIRST HALF of this merge — see
    # acquire_lots's aliases note (identical rationale, harness reliability program
    # 2026-07-19).
    self_stages=True,
)
async def delete_lots(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "delete_lots")
        if entry is None:
            return _gate.unknown_token_result("delete_lots")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "deletions")
    raw_deletions = input_data.get("deletions")
    if raw_deletions:
        if len(raw_deletions) > _MAX_LOT_DELETE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_deletions)} deletions exceeds the "
                        f"{_MAX_LOT_DELETE_BATCH}-deletion cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "lot_delete_batch",
                raw_deletions,
                [
                    {"lot_id": i.get("lot_id"), "position_id": i.get("position_id")}
                    for i in raw_deletions
                ],
                "Delete lots",
            )
        results = [
            {
                "lot_id": item.get("lot_id"),
                "position_id": item.get("position_id"),
                **_for_model(
                    lots_service.apply_lot_change(
                        {"type": "delete", "lot_id": item.get("lot_id", "")}
                    )
                ),
            }
            for item in raw_deletions
        ]
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    if gated or promised is not None:
        change, error = _build_lot_delete_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        if gated:
            return registry.stage_write(change)
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("delete_lots")

    result = lots_service.apply_lot_change(
        {
            "type": "delete",
            "lot_id": input_data["lot_id"],
        }
    )
    return json.dumps(_for_model(result), ensure_ascii=False), None


# Loose (non-ToolInput) nested batch item — bare fields, no descriptions (except
# allow_duplicate), mirroring the pre-migration literal's _DISPOSAL_ITEM_PROPERTIES dict,
# which was spread onto BOTH the root schema and this item identically. No docstring
# (would leak into the item schema as a "description" key) and no additionalProperties.
class DisposalItem(BaseModel):
    position_id: Optional[str] = Field(default=None, description=_DIS_POSITION_ID_DESC)
    quantity: Optional[float] = Field(default=None, description=_DIS_QUANTITY_DESC)
    proceeds_per_unit: Optional[float] = Field(
        default=None, description=_DIS_PROCEEDS_PER_UNIT_DESC
    )
    proceeds_currency: Optional[str] = Field(default=None, description=_DIS_PROCEEDS_CURRENCY_DESC)
    disposal_fees: Optional[float] = Field(default=None, description=_DIS_FEES_DESC)
    disposed_at: Optional[str] = Field(default=None, description=_DIS_DISPOSED_AT_DESC)
    method: Optional[str] = Field(default=None, description=_DIS_METHOD_DESC)
    lot_selection: Optional[List[LotSelectionItem]] = Field(
        default=None, description=_DIS_LOT_SELECTION_DESC
    )
    is_taxable: Optional[bool] = Field(default=None, description=_DIS_IS_TAXABLE_DESC)
    proceeds_account_id: Optional[str] = Field(default=None, description=_DIS_PROCEEDS_ACCOUNT_DESC)
    transfer_to_account_id: Optional[str] = Field(default=None, description=_DIS_TRANSFER_TO_DESC)
    allow_duplicate: Optional[bool] = Field(
        default=None, description=_ALLOW_DUPLICATE_DISPOSAL_DESC
    )
    idempotency_key: Optional[str] = Field(default=None, description=_DIS_IDEMPOTENCY_DESC)
    note: Optional[str] = Field(default=None)


class ApplyLotDisposeInput(ToolInput):
    """Schema-of-record for ``dispose_lots`` (see ``schema_from_model``)."""

    position_id: str = Field(description=_DIS_POSITION_ID_DESC)
    quantity: float = Field(description=_DIS_QUANTITY_DESC)
    proceeds_per_unit: Optional[float] = Field(
        default=None, description=_DIS_PROCEEDS_PER_UNIT_DESC
    )
    proceeds_currency: Optional[str] = Field(default=None, description=_DIS_PROCEEDS_CURRENCY_DESC)
    disposal_fees: Optional[float] = Field(default=None, description=_DIS_FEES_DESC)
    disposed_at: Optional[str] = Field(default=None, description=_DIS_DISPOSED_AT_DESC)
    method: Optional[str] = Field(default=None, description=_DIS_METHOD_DESC)
    lot_selection: Optional[List[LotSelectionItem]] = Field(
        default=None, description=_DIS_LOT_SELECTION_DESC
    )
    is_taxable: Optional[bool] = Field(default=None, description=_DIS_IS_TAXABLE_DESC)
    proceeds_account_id: Optional[str] = Field(default=None, description=_DIS_PROCEEDS_ACCOUNT_DESC)
    transfer_to_account_id: Optional[str] = Field(default=None, description=_DIS_TRANSFER_TO_DESC)
    allow_duplicate: Optional[bool] = Field(
        default=None, description=_ALLOW_DUPLICATE_DISPOSAL_DESC
    )
    idempotency_key: Optional[str] = Field(default=None, description=_DIS_IDEMPOTENCY_DESC)
    note: Optional[str] = Field(default=None)
    disposals: Optional[List[DisposalItem]] = Field(
        default=None,
        min_length=1,
        description=(
            "Batch form: one call for several disposals, each applied (and double-book-guarded) independently. One call covers them all, and raises ONE confirm card for the whole batch."
        ),
    )


@tool(
    "dispose_lots",
    description=(
        "Record a disposal (a sell, or a transfer-out/gift with is_taxable=false) — consumes the lots and books the realized gain. Identify the position by position_id (from get_portfolio). method 'fifo' (default) consumes oldest lots first; 'specific_id' requires lot_selection [{lot_id, qty}] (call get_lots first to read lot ids) and the qty must sum to quantity. ALWAYS capture the commission: extract the selling fee/commission into `disposal_fees`; omit if the source shows none, never guess. On an interactive turn this raises a confirm card (no write yet) — showing which lots get consumed, the per-lot long/short term, and the total gain in the proceeds and base currency; the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller records immediately, no card — pass idempotency_key so a retry can't double-book. Recording several sales at once (e.g. a batch of fills from a broker statement)? Pass `disposals` (a list) instead — one call, one confirm card for the whole batch, each item keeping its own idempotency_key and double-book guard."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ApplyLotDisposeInput, array="disposals", tool_name="dispose_lots"
    ),
    mutates=("portfolio",),
    # propose_lot_dispose is the former FIRST HALF of this merge — see
    # acquire_lots's aliases note (identical rationale, harness reliability program
    # 2026-07-19). propose_lot_dispose used to MINT a fresh idempotency_key into every
    # proposal (its own schema had no such field) and rely on the model copying it into the
    # apply call; the merge does the SAME minting server-side (see below) instead of
    # trusting the model to echo it back.
    self_stages=True,
)
async def dispose_lots(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "dispose_lots")
        if entry is None:
            return _gate.unknown_token_result("dispose_lots")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "disposals")
    raw_disposals = input_data.get("disposals")
    if raw_disposals:
        if len(raw_disposals) > _MAX_LOT_DISPOSE_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_disposals)} disposals exceeds the "
                        f"{_MAX_LOT_DISPOSE_BATCH}-disposal cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "lot_dispose_batch",
                raw_disposals,
                [
                    {
                        "position_id": i.get("position_id"),
                        "quantity": i.get("quantity"),
                        "proceeds_per_unit": i.get("proceeds_per_unit"),
                    }
                    for i in raw_disposals
                ],
                "Record disposals",
            )
        results = []
        for item in raw_disposals:
            try:
                one = _apply_lot_dispose_one(item)
            except OpenNetWorthError as exc:
                one = {"ok": False, "error": exc.message or str(exc)}
            results.append({"position_id": item.get("position_id"), **one})
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    if gated or promised is not None:
        # Mint the idempotency_key ONCE, here, exactly when propose_lot_dispose used to
        # (a fresh uuid per proposal) — but now it's baked into the STORED args at stage
        # time instead of riding in the model's context for it to copy back on confirm.
        # A no-op on the confirm path itself: staged_args already carries the key this
        # branch minted at preview time.
        if not input_data.get("idempotency_key"):
            input_data = {**input_data, "idempotency_key": str(uuid.uuid4())}
        change, err_result = _build_lot_dispose_change(input_data)
        if err_result is not None:
            return err_result
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # the lot state (or an intervening duplicate sale) could have changed since.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("dispose_lots")

    try:
        result = _apply_lot_dispose_one(input_data)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(result, ensure_ascii=False), None


def _apply_lot_dispose_one(input_data: dict) -> dict:
    result = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": input_data["position_id"],
            "quantity": input_data["quantity"],
            "proceeds_per_unit": input_data.get("proceeds_per_unit", 0),
            "proceeds_currency": input_data.get("proceeds_currency"),
            "disposal_fees": input_data.get("disposal_fees", 0),
            "disposed_at": input_data.get("disposed_at", ""),
            "method": input_data.get("method", "fifo"),
            "lot_selection": input_data.get("lot_selection") or [],
            "is_taxable": input_data.get("is_taxable", True),
            "proceeds_account_id": input_data.get("proceeds_account_id") or None,
            "transfer_to_account_id": input_data.get("transfer_to_account_id") or None,
            "allow_duplicate": bool(input_data.get("allow_duplicate", False)),
            "idempotency_key": input_data.get("idempotency_key"),
            "note": input_data.get("note", ""),
            "source": "ai",
        }
    )
    if result.get("idempotent"):
        result["_next_step"] = (
            "This disposal was ALREADY booked (same idempotency_key) — no lot was consumed and no "
            "proceeds were credited by this call. Do NOT book the proceeds again, and do NOT "
            "report it as a new sale."
        )
    return _for_model(result, quantity_sold=input_data["quantity"])


def _build_lot_dispose_undo_change(input_data: dict) -> tuple[Optional[dict], Optional[str]]:
    """Shared preview-build for the ``undo_lot_disposals`` confirm gate — the former
    body of ``propose_lot_dispose_undo`` (minus the ``idempotency_key`` it used to mint:
    ``lots_service._undo_dispose`` never reads one — dead metadata, dropped here rather
    than carried forward). Returns ``(change, None)`` or ``(None, error_message)``."""
    event_id = input_data.get("event_id")
    ev = lots_service.find_realized_event(event_id) if event_id else None
    if not ev:
        return None, "disposal event not found — call get_realized_pnl for current event ids"

    consumptions = ev.get("consumptions") or []
    will_reverse = [
        f"re-open {len(consumptions)} consumed lot(s), restoring {ev.get('quantity')} units",
    ]
    txn_id = ev.get("txn_id")
    if txn_id:
        will_reverse.append(f"soft-delete the cash proceeds txn (txn_id={txn_id})")
    else:
        will_reverse.append("no cash proceeds txn to reverse")
    will_reverse.append("delete the realized event")

    disp = _position_display(ev.get("position_id", ""))
    change = {
        "type": "lot_dispose_undo",
        "event_id": event_id,
        "display": {
            **disp,
            "event_id": event_id,
            "disposed_at": ev.get("disposed_at"),
            "quantity": ev.get("quantity"),
            "method": ev.get("method"),
            "proceeds_per_unit": ev.get("proceeds_per_unit"),
            "proceeds_currency": ev.get("proceeds_currency"),
            "realized_gain": ev.get("realized_gain"),
            "txn_id": txn_id,
            "will_reverse": will_reverse,
        },
    }
    return change, None


class ApplyLotDisposeUndoInput(ToolInput):
    """Schema-of-record for ``undo_lot_disposals`` (see ``schema_from_model``)."""

    event_id: Optional[str] = Field(
        default=None,
        description="realized-event id from get_realized_pnl. Required unless `event_ids` is used instead.",
    )
    event_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Batch form: undo SEVERAL disposals in ONE call (e.g. reverting an entire botched import batch) — a list of event_ids. Each is undone independently; an event whose consumed lots no longer exist is refused for that item only. One call covers them all, and raises ONE confirm card for the whole batch. Omit the single `event_id` field when using this."
        ),
    )


@tool(
    "undo_lot_disposals",
    description=(
        "REVERSE a disposal (sell / transfer-out) — use this to remove a duplicate or mistaken sale from the realized history. Call get_realized_pnl first to find the event_id. The undo re-opens the lots the disposal consumed (restoring the shares to the position), soft-deletes the cash proceeds transaction if one was booked, and deletes the realized event. REFUSED if a consumed lot no longer exists (the shares can't be restored cleanly). On an interactive turn this raises a confirm card (no write yet, showing exactly what will be reversed); the user's approval then runs it server-side with exactly the values the card showed. Never call it again to confirm and never restate the values — there is nothing for you to pass back. An unattended/automated caller undoes immediately, no card. Undoing several disposals at once? Pass `event_ids` (a list) instead — one call, and ONE confirm card for the whole batch."
    ),
    input_schema=schema_of(ApplyLotDisposeUndoInput, tool_name="undo_lot_disposals"),
    mutates=("portfolio",),
    # propose_lot_dispose_undo is the former FIRST HALF of this merge — see
    # acquire_lots's aliases note (identical rationale, harness reliability program
    # 2026-07-19).
    self_stages=True,
)
async def undo_lot_disposals(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "undo_lot_disposals")
        if entry is None:
            return _gate.unknown_token_result("undo_lot_disposals")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "event_ids", "event_id")
    raw_event_ids = input_data.get("event_ids")
    if raw_event_ids:
        if len(raw_event_ids) > _MAX_LOT_DISPOSE_UNDO_BATCH:
            return json.dumps(
                {
                    "error": (
                        f"{len(raw_event_ids)} event_ids exceeds the "
                        f"{_MAX_LOT_DISPOSE_UNDO_BATCH}-event cap for one call — split into multiple calls."
                    )
                }
            ), None
        if gated:
            return registry.stage_batch(
                "lot_dispose_undo_batch",
                raw_event_ids,
                [{"event_id": e} for e in raw_event_ids],
                "Reverse disposals",
            )
        results = []
        for event_id in raw_event_ids:
            try:
                one = _for_model(
                    lots_service.apply_lot_change({"type": "dispose_undo", "event_id": event_id})
                )
            except OpenNetWorthError as exc:
                one = {"ok": False, "error": exc.message or str(exc)}
            results.append({"event_id": event_id, **one})
        return json.dumps(_batch_summary(results), ensure_ascii=False), None

    if gated or promised is not None:
        change, error = _build_lot_dispose_undo_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        if gated:
            return registry.stage_write(change)
        # Confirming: re-verify the CURRENT resolve still matches what the card promised —
        # the event (or a consumed lot) could have changed since the preview.
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("undo_lot_disposals")

    try:
        result = lots_service.apply_lot_change(
            {"type": "dispose_undo", "event_id": input_data["event_id"]}
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    return json.dumps(_for_model(result), ensure_ascii=False), None
