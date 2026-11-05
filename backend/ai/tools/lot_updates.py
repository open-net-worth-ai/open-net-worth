"""Lot correction tool — ``update_lots``: fix a recorded lot's date, quantity, cost or kind.

A position's quantity and cost basis are derived from its lots (ADR
2026-10-07-a-position-quantity-is-derived-from-its-records), so a wrong lot is fixed on the
LOT, never on the position. The commonest case: a holding added with no purchase date became
an opening balance dated the day it was added, and the user later says when they really
bought it. Writes go through ``lots_service.apply_lot_change({"type": "amend", …})`` — the
same choke the app's lot editor uses — which re-derives the position in the same unit of work.
"""

from __future__ import annotations

import json
from datetime import date
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools import _confirm_gate as _gate
from backend.ai.tools import registry
from backend.ai.tools.lots import _batch_summary, _for_model, _position_display
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of_batch_only
from backend.core.errors import OpenNetWorthError
from backend.core.user_time import user_today
from backend.services import lots as lots_service

_MAX_LOT_UPDATE_BATCH = 100

# The lot's identity — what a sale freezes once it has consumed part of the lot.
_IDENTITY_FIELDS = ("acquired_at", "quantity", "unit_cost", "cost_currency", "fees", "kind")
_EDITABLE_FIELDS = (*_IDENTITY_FIELDS, "note")
_KINDS = ("buy", "drp", "transfer_in", "opening")

_LOT_ID_DESC = "The lot to correct — lot id from get_lots."
_ACQUIRED_AT_DESC = (
    "The real purchase date the user gave, ISO YYYY-MM-DD (not in the future) — never a "
    "guess. The usual fix for an 'opening' lot dated the day the holding was added because "
    "no date was known."
)
_QUANTITY_DESC = "Units in this lot (positive). The position's quantity follows its lots."
_UNIT_COST_DESC = "Cost PER UNIT in cost_currency — not the total paid."
_COST_CURRENCY_DESC = "Three-letter currency of unit_cost."
_FEES_DESC = "Acquisition fees in cost_currency, spread across the lot's units."
_KIND_DESC = (
    "buy | drp | transfer_in | opening. 'opening' = held when tracking started (counts at "
    "market value in returns); 'buy' = a purchase at its cost."
)


class UpdateLotItem(ToolInput):
    """One correction in an ``update_lots`` call."""

    lot_id: str = Field(description=_LOT_ID_DESC)
    acquired_at: Optional[str] = Field(default=None, description=_ACQUIRED_AT_DESC)
    quantity: Optional[float] = Field(default=None, description=_QUANTITY_DESC)
    unit_cost: Optional[float] = Field(default=None, description=_UNIT_COST_DESC)
    cost_currency: Optional[str] = Field(default=None, description=_COST_CURRENCY_DESC)
    fees: Optional[float] = Field(default=None, description=_FEES_DESC)
    kind: Optional[Literal["buy", "drp", "transfer_in", "opening"]] = Field(
        default=None, description=_KIND_DESC
    )
    note: Optional[str] = Field(default=None)


class UpdateLotsInput(ToolInput):
    """Schema-of-record for ``update_lots`` (see ``schema_from_model``)."""

    lot_id: Optional[str] = Field(default=None, description=_LOT_ID_DESC)
    acquired_at: Optional[str] = Field(default=None, description=_ACQUIRED_AT_DESC)
    quantity: Optional[float] = Field(default=None, description=_QUANTITY_DESC)
    unit_cost: Optional[float] = Field(default=None, description=_UNIT_COST_DESC)
    cost_currency: Optional[str] = Field(default=None, description=_COST_CURRENCY_DESC)
    fees: Optional[float] = Field(default=None, description=_FEES_DESC)
    kind: Optional[Literal["buy", "drp", "transfer_in", "opening"]] = Field(
        default=None, description=_KIND_DESC
    )
    note: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None, description="Why the lot is being corrected")
    updates: Optional[List[UpdateLotItem]] = Field(
        default=None,
        min_length=1,
        description=(
            "Corrections, one per lot: [{lot_id, …fields to change}]. One call covers them "
            "all, and raises ONE confirm card for the whole batch."
        ),
    )


def _fields(item: dict) -> dict:
    return {k: item[k] for k in _EDITABLE_FIELDS if item.get(k) is not None}


def _refusal(lot: dict, fields: dict) -> str | None:
    """Everything the choke would refuse, said BEFORE a card is staged — plus what a tool
    must not do that the app's editor can: re-shape a vest's lot behind its vest event."""
    if not fields:
        return "nothing to change — pass at least one of: " + ", ".join(_EDITABLE_FIELDS)
    identity = [k for k in _IDENTITY_FIELDS if k in fields]
    sold = float(lot.get("original_qty") or 0) - float(lot.get("remaining_qty") or 0)
    if "quantity" in fields and float(fields["quantity"]) + 1e-9 < sold:
        return (
            f"{sold:g} units of this lot have already been sold, so it can't hold fewer than "
            f"{sold:g} — edit or undo the sale first (undo_lot_disposals)"
        )
    if identity and lot.get("kind") == "grant":
        return (
            "this lot was created by a vest and follows its vest record — undo the vest "
            "(undo_vest) and record it again instead of editing the lot"
        )
    if "acquired_at" in fields:
        try:
            when = date.fromisoformat(str(fields["acquired_at"]))
        except ValueError:
            return "acquired_at must be an ISO date, YYYY-MM-DD"
        if when > user_today():
            return "acquired_at can't be in the future"
    if "kind" in fields and fields["kind"] not in _KINDS:
        return f"kind must be one of: {', '.join(_KINDS)}"
    if "quantity" in fields and not float(fields["quantity"]) > 0:
        return "quantity must be a positive number of units"
    return None


def _build_lot_update_change(item: dict) -> tuple[Optional[dict], Optional[str]]:
    """The confirm card for one correction: each changed field as ``before → after``."""
    from backend.db.lot_store import LotStore

    lot_id = item.get("lot_id")
    lot = LotStore().find_by_id(lot_id) if lot_id else None
    if not lot:
        return None, "lot not found — call get_lots for current lot ids"
    fields = _fields(item)
    refused = _refusal(lot, fields)
    if refused:
        return None, refused
    before = {**lot, "quantity": lot.get("original_qty")}
    # Names, not ids: "Bought Jun 12, 2026 · 10 units" tells a person which lot this is; the
    # lot and position UUIDs on the card read as noise (seen live, 2026-10-13).
    display = {
        k: v for k, v in _position_display(lot.get("position_id", "")).items() if k != "position_id"
    }
    display["lot"] = _lot_label(lot)
    for key, value in fields.items():
        if _same(before.get(key), value):
            continue  # a no-op field ("USD → USD") is noise on the card, not a change
        display[key] = f"{before.get(key) if before.get(key) not in (None, '') else '—'} → {value}"
    sales = lots_service.preview_sold_lot_amend(lot, fields)
    if sales:
        # One table row per sale that used this lot — the user sees what the correction does
        # to their realized gains before approving it.
        display["sales_updated"] = [_sale_row(sale) for sale in sales]
    return {
        "type": "lot_update",
        "lot_id": lot_id,
        "position_id": lot.get("position_id"),
        "reason": item.get("reason", ""),
        "display": display,
        # Sales whose GAIN this moves — two such lots in one batch would preview against each
        # other's pre-change gain. A date-only correction moves no gain and batches freely.
        "sales": [
            {"event_id": sale["event_id"]}
            for sale in sales
            if not _same(sale.get("realized_gain_before"), sale.get("realized_gain_after"))
            or not _same(
                sale.get("realized_gain_base_before"), sale.get("realized_gain_base_after")
            )
        ],
    }, None


def _lot_label(lot: dict) -> str:
    when = lot.get("acquired_at") or ""
    try:
        if when:
            d = date.fromisoformat(when[:10])
            when = f"{d:%b} {d.day}, {d.year}"  # %-d isn't portable (Windows)
        else:
            when = ", date unknown"
    except ValueError:
        pass
    kind = {"opening": "Opening balance", "transfer_in": "Transferred in", "drp": "Reinvested"}.get(
        lot.get("kind") or "", "Bought"
    )
    sold = float(lot.get("original_qty") or 0) - float(lot.get("remaining_qty") or 0)
    qty = f"{float(lot.get('original_qty') or 0):g} units"
    sep = "" if when.startswith(",") else " "
    return f"{kind}{sep}{when} · {qty}" + (f" ({sold:g} sold)" if sold > 0 else "")


def _sale_row(sale: dict) -> dict:
    before, after = sale.get("realized_gain_before"), sale.get("realized_gain_after")
    ccy = sale.get("currency") or ""

    def money(v):
        return "unknown" if v is None else f"{v:+,.2f} {ccy}".strip()

    return {
        "sale_date": sale.get("disposed_at"),
        "realized_gain": (
            f"{money(before)} (unchanged)"
            if _same(before, after)
            else f"{money(before)} → {money(after)}"
        ),
    }


def _same(before, after) -> bool:
    if before in (None, "") or after in (None, ""):
        return before in (None, "") and after in (None, "")
    try:
        return abs(float(before) - float(after)) < 1e-9
    except (TypeError, ValueError):
        return str(before).strip().upper() == str(after).strip().upper()


def _apply_or_refuse(item: dict) -> dict:
    """A choke's refusal is this item's result, so Lucius reads the reason and the rest of a batch
    still applies. Each item is its own unit of work: a refused one rolled back whole."""
    try:
        return _apply(item)
    except OpenNetWorthError as exc:
        return {"error": exc.message or str(exc)}


def _apply(item: dict) -> dict:
    """Validate, then write — on EVERY path, an unattended one included (no card there)."""
    _, error = _build_lot_update_change(item)
    if error is not None:
        return {"ok": False, "error": error}
    return _for_model(
        lots_service.apply_lot_change(
            {"type": "amend", "lot_id": item.get("lot_id", ""), **_fields(item), "source": "ai"}
        )
    )


@tool(
    "update_lots",
    description=(
        "Correct a recorded lot — its purchase date, quantity, unit cost, fees, kind or note — "
        "including a lot whose shares have been sold (sold lots are correctable). "
        "A position's quantity and cost basis are DERIVED from its lots, so this is how a "
        "wrong holding is fixed (update_positions refuses those fields). Typical use: the "
        "user tells you when they really bought a holding that was added without a date — "
        "set acquired_at on its 'opening' lot (and kind='buy' with its cost if they know "
        "it). Call get_lots first for the lot_id. For a sold lot, the sales that used it keep "
        "the units they took; their lines for this lot are re-dated and, if cost changed, "
        "their realized gain is recomputed — the card shows each sale's gain before → after. "
        "Its quantity can't go below the units already sold. Refused for a vest's lot (undo "
        "and re-record the vest). Linked purchase cash is rewritten to match. On an "
        "interactive turn this raises a confirm card (no write yet); the user's approval "
        "then runs it server-side with exactly the values the card showed. Never call it "
        "again to confirm and never restate the values. An unattended/automated caller "
        "applies immediately, no card. Several lots? Pass `updates` — one call, one card."
    ),
    input_schema=schema_of_batch_only(UpdateLotsInput, array="updates", tool_name="update_lots"),
    mutates=("portfolio",),
    self_stages=True,
)
async def update_lots(input_data: dict):
    promised: dict | None = None
    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "update_lots")
        if entry is None:
            return _gate.unknown_token_result("update_lots")
        input_data = _gate.staged_args(entry)
        promised = entry.get("outcome") or {}
    else:
        gated = registry.should_preview_write()

    input_data = registry.one_item(input_data, "updates")
    items = input_data.get("updates")
    if items:
        return _batch(items, gated)

    if gated or promised is not None:
        change, error = _build_lot_update_change(input_data)
        if error is not None:
            return _gate.preview_failed_result(error)
        if gated:
            return registry.stage_write(change)
        if change["display"] != promised:
            return _gate.outcome_mismatch_result("update_lots")
    return json.dumps(_apply_or_refuse(input_data), ensure_ascii=False), None


def _batch(items: list[dict], gated: bool):
    if len(items) > _MAX_LOT_UPDATE_BATCH:
        return json.dumps(
            {
                "error": (
                    f"{len(items)} updates exceeds the {_MAX_LOT_UPDATE_BATCH}-update cap for "
                    "one call — split into multiple calls."
                )
            }
        ), None
    if gated:
        rows = []
        seen_sales: set[str] = set()
        for item in items:
            change, error = _build_lot_update_change(item)
            if error is not None:
                return _gate.preview_failed_result(f"lot {item.get('lot_id')}: {error}")
            # Each row's before → after is previewed against the stored book; two lots that fed
            # the same sale would each show a "before" the other has already changed.
            sales = {s["event_id"] for s in change.get("sales") or []}
            if sales & seen_sales:
                return _gate.preview_failed_result(
                    "two of these lots were sold in the same sale and both change its gain — "
                    "correct their cost one call at a time so the card shows each change correctly"
                )
            seen_sales |= sales
            rows.append(change["display"])
        return registry.stage_batch("lot_update_batch", items, rows, "Correct lots")
    results = [{"lot_id": item.get("lot_id"), **_apply_or_refuse(item)} for item in items]
    return json.dumps(_batch_summary(results), ensure_ascii=False), None
