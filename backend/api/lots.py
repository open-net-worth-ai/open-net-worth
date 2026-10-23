"""
Lots + realized-P&L read API (P2-INV1b).

A THIN router over services.lots — it marshals input and shapes the response; every
realized-gain figure, lot annotation, lot-selection, holding-period and base-currency
conversion is computed in services.lots (the math home), never here. Respects the
dependency rule: imports only the service (api → services), never a store or core.fx
directly for math.

Write routes converge on the single `lots_service.apply_lot_change` choke point
(the same entrypoint the AI apply tools use), so turn-checkpoints cover every lot write
(D5). A disposal is a money/tax-relevant mutation; the write only mutates after the pure
`compute_disposal` validates the selection (raising `InvalidLotSelection` /
`InsufficientLots` → HTTP 422 before any store is touched).

Routes:
    GET    /api/positions/{position_id}/lots
    POST   /api/positions/{position_id}/lots
    POST   /api/instruments/{instrument_id}/lots
    PATCH  /api/lots/{lot_id}
    DELETE /api/lots/{lot_id}
    POST   /api/positions/{position_id}/disposals
    PATCH  /api/disposals/{event_id}
    GET    /api/realized-pnl

See .claude/rfcs/phase2/inv1-tax-lots-pnl.md §10 and
docs/adr/2026-09-11-lot-acquire-optional-cash-debit.md.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.lot import InstrumentLotCreate, LotAmend, LotCreate
from backend.models.realized_event import DisposalCreate
from backend.services import lots as lots_service

router = APIRouter()


def _kind_value(kind) -> str:
    return kind.value if hasattr(kind, "value") else kind


def _acquire_change(body: LotCreate, extra: dict) -> dict:
    return {
        "type": "acquire",
        "quantity": body.quantity,
        "unit_cost": body.unit_cost,
        "cost_currency": body.cost_currency,
        "fees": body.fees,
        "acquired_at": body.acquired_at,
        "kind": _kind_value(body.kind),
        "allow_duplicate": body.allow_duplicate,
        "note": body.note,
        "funding_account_id": body.funding_account_id or None,
        "source": "manual",
        **extra,
    }


def _dispose_fields(body: DisposalCreate) -> dict:
    return {
        "quantity": body.quantity,
        "proceeds_per_unit": body.proceeds_per_unit,
        "proceeds_currency": body.proceeds_currency,
        "disposal_fees": body.disposal_fees,
        "disposed_at": body.disposed_at,
        "method": body.method.value if hasattr(body.method, "value") else body.method,
        "lot_selection": [{"lot_id": s.lot_id, "qty": s.qty} for s in (body.lot_selection or [])],
        "is_taxable": body.is_taxable,
        "proceeds_account_id": body.proceeds_account_id or None,
        "transfer_to_account_id": body.transfer_to_account_id or None,
        "note": body.note,
        "source": "manual",
    }


def _raise_lot_write(result: dict) -> dict:
    if not result.get("error"):
        return result
    if result.get("existing_lot_id") or result.get("existing_event_id"):
        raise HTTPException(409, result["error"])
    # Only a missing thing is a 404; every other refusal is the request being invalid.
    status = 404 if "not found" in (result["error"] or "") else 422
    raise HTTPException(status, result["error"])


@router.get("/api/positions/{position_id}/lots")
def get_position_lots(position_id: str):
    """Open + closed lots for a position, FIFO-ordered, each annotated with effective
    unit cost, remaining basis, and term-to-date (long/short as of today). A pure read:
    viewing a position never writes a lot (the KSTL placeholder, 2026-10-07)."""
    return {"position_id": position_id, "lots": lots_service.lots_for_position(position_id)}


@router.post("/api/positions/{position_id}/lots")
def acquire_lot(position_id: str, body: LotCreate):
    """Record an acquisition (buy / transfer-in / grant) as a new lot, then resync the
    position's aggregate quantity + avg cost. Thin: shapes the change and hands it to the
    `apply_lot_change` choke point (checkpoint-covered) — no cost math here."""
    return _raise_lot_write(
        lots_service.apply_lot_change(_acquire_change(body, {"position_id": position_id}))
    )


@router.post("/api/instruments/{instrument_id}/lots")
def acquire_instrument_lot(instrument_id: str, body: InstrumentLotCreate):
    """First-hold acquire: find-or-create the liquid position for this instrument ×
    account, then record the lot. Never POST /positions then POST /lots (that would
    seed an opening lot and double-book)."""
    return _raise_lot_write(
        lots_service.apply_lot_change(
            _acquire_change(body, {"instrument_id": instrument_id, "account_id": body.account_id})
        )
    )


@router.patch("/api/lots/{lot_id}")
def amend_lot(lot_id: str, body: LotAmend):
    """Amend a fully-open lot (identity + optional cash rewrite) or any lot's note."""
    payload = body.model_dump(exclude_unset=True)
    if "kind" in payload and payload["kind"] is not None:
        payload["kind"] = _kind_value(payload["kind"])
    if "funding_account_id" in payload:
        payload["funding_account_id"] = payload["funding_account_id"] or None
    return _raise_lot_write(
        lots_service.apply_lot_change({"type": "amend", "lot_id": lot_id, **payload})
    )


@router.delete("/api/lots/{lot_id}")
def delete_lot(lot_id: str):
    """Delete a fully-open lot. Partially consumed lots are refused (422)."""
    return _raise_lot_write(lots_service.apply_lot_change({"type": "delete", "lot_id": lot_id}))


@router.post("/api/positions/{position_id}/disposals")
def record_disposal(position_id: str, body: DisposalCreate):
    """Record a disposal (sell / transfer-out / gift): consume lots (FIFO or specific-ID),
    book the realized-gain event, and resync the position — all through the single
    `apply_lot_change` choke point. The service validates the lot selection and raises a
    domain error (→ HTTP 422) BEFORE any write, so an oversell / bad lot id never mutates
    state. The realized-gain arithmetic lives entirely in the service."""
    change = {"type": "dispose", "position_id": position_id, **_dispose_fields(body)}
    try:
        result = lots_service.apply_lot_change(change)
    except OpenNetWorthError as exc:  # InvalidLotSelection / InsufficientLots → 422
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if result.get("error"):
        if result.get("existing_event_id"):
            raise HTTPException(409, result["error"])
        raise HTTPException(404, result["error"])
    return result


@router.patch("/api/disposals/{event_id}")
def amend_disposal(event_id: str, body: DisposalCreate):
    """Edit a sale: undo then rebook in one unit of work (`dispose_amend`)."""
    change = {"type": "dispose_amend", "event_id": event_id, **_dispose_fields(body)}
    try:
        result = lots_service.apply_lot_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return _raise_lot_write(result)


@router.get("/api/realized-pnl")
def get_realized_pnl(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    instrument_id: Optional[str] = None,
    account_id: Optional[str] = None,
    position_id: Optional[str] = None,
    with_predecessors: bool = False,
):
    """Realized gains over a date range / instrument / account / position. Base figures are
    recomputed from the native gain + current FX in the service (the stored snapshot is
    only a write-time cache), so a base-currency change can't stale the report. Pass
    position_id to scope disposals to a single position (so a disposal never bleeds onto a
    sibling position of the same instrument — e.g. an unvested grant); add with_predecessors
    to keep the sale of a holding sold out and rebought at the same account. Returns
    {base_currency, events, realized_total_base, by_term, by_instrument}."""
    return lots_service.realized_pnl(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
        position_id=position_id,
        with_predecessors=with_predecessors,
    )
