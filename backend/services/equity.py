"""
equity_service — equity-compensation grants, vesting schedules, vest events (feat/equity-comp).

All grant/vest writes funnel through ``apply_equity_change(change)`` — the single checkpoint-
covered choke point. It composes the existing lots/positions choke points in a fixed store
order that guarantees idempotency and undo (RFC §3).

``vest_apply`` is the hardest correctness problem: the stores must agree. The approach is
validate-before-write + last-write-idempotency-key + turn-checkpoint backstop (same posture as
``income._apply_record`` and ``lots._apply_dispose``). A vest acquires the FMV lot and flips the
unvested units to vested; it writes NO ledger transaction, whatever the grant's ``tax_event``
(ADR 2026-09-22-a-vest-books-no-income-transaction — shares are delivered, not cash, and
booking an inflow double-counted the vest in net worth). ``vest_undo`` still reverses the
income txn of any vest recorded BEFORE that change.

IMPORT DISCIPLINE: ``ledger_service`` and ``lots_service`` are lazy-imported (services-tier
siblings; importing them at module level would form a cycle and violate Contract 3). Other imports
are downward (db + core) only — no api/ai.

Reads (``upcoming_vests``, ``fmv_prefill``, ``preview_vest``) live here.
Analytics (``equity_report``) live in ``backend/services/equity_report.py`` (extracted to keep
each file ≤400 lines per CLAUDE.md).
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Connection

from backend.core.errors import NotFoundError, ValidationError
from backend.core.fx import base_currency, to_base
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.core.write_receipt import record_write
from backend.db.engine import locked_unit_of_work
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.tables import grants as grants_tbl
from backend.db.tables import lots as lots_tbl
from backend.db.tables import positions as positions_tbl
from backend.db.tables import transactions as txns_tbl
from backend.db.tables import txn_audit as txn_audit_tbl
from backend.db.tables import vest_events as vest_events_tbl
from backend.db.vest_event_store import VestEventStore
from backend.services import equity_release, position_figures

_VALID_TAX_EVENTS = {"at_vest", "deferred"}
_VALID_STATUSES = {"active", "fully_vested", "cancelled", "forfeited"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Module-level store handles (monkeypatch targets for tests, mirrors income_service pattern).
grants = GrantStore()
vest_events = VestEventStore()
instruments = InstrumentStore()
positions = PositionStore()
price_history = PriceHistoryStore()


# ── Apply choke point ──────────────────────────────────────────────────────────────


def apply_equity_change(change: dict) -> dict:
    """The single write entrypoint for all equity mutations.

    ``change["type"]`` ∈ {grant_create, grant_update, grant_cancel, vest_apply, vest_undo,
    release_apply}.
    Shared by REST routes and AI apply tools so every write is checkpoint-covered through
    JsonStore._save_raw → fire_write_hook.
    """
    ctype = change.get("type")
    if ctype == "grant_create":
        return _record(_grant_create(change), ctype)
    if ctype == "grant_update":
        return _record(_grant_update(change), ctype)
    if ctype == "grant_cancel":
        return _record(_grant_cancel(change), ctype)
    if ctype == "vest_apply":
        return _record(_vest_apply(change), ctype)
    if ctype == "vest_undo":
        return _record(_vest_undo(change), ctype)
    if ctype == "release_apply":
        return _record(equity_release.apply_release(change), ctype)
    return {"error": f"unknown change type: {ctype}"}


# (op, entity_type, result key carrying the id) per change type, for the write-receipt chip.
_RECEIPT_MAP = {
    "grant_create": ("create", "grant", "grant_id"),
    "grant_update": ("update", "grant", "grant_id"),
    "grant_cancel": ("delete", "grant", "grant_id"),
    "vest_apply": ("create", "vest", "vest_event_id"),
    "vest_undo": ("delete", "vest", "vest_event_id"),
    # A release annotates an existing vest with its delivery outcome — it is an update of
    # that one economic event, not a second vest.
    "release_apply": ("update", "vest", "vest_event_id"),
}


def _record(result: dict, ctype: str) -> dict:
    """Report this equity write onto the turn's receipt (a no-op outside an assistant turn).

    Records ONLY the grant/vest entity. A vest ALSO mints an FMV-basis lot, which funnels
    through its own choke (``lots``) and reports itself — the point of a multi-type chip.
    Recording it here as well would double-count one economic event. (Until 2026-09-22 a
    vest booked an income transaction too; it no longer writes any ledger row.)"""
    try:
        mapped = _RECEIPT_MAP.get(ctype)
        if not mapped or not isinstance(result, dict) or result.get("error"):
            return result
        op, entity_type, id_key = mapped
        record_write(op, result.get(id_key) or "", entity_type=entity_type, meta={"verb": ctype})
    except Exception:  # noqa: BLE001 - describing a write must never be able to fail it
        pass
    return result


# ── grant_create ────────────────────────────────────────────────────────────────────


def _grant_create(change: dict) -> dict:
    """Create a grant and its locked position (no money movement; no lot)."""
    # Idempotency BEFORE any write — a double-submitted create would otherwise mint a
    # second grant AND a second locked position (grants were the one money-fact store
    # without a dedupe key; lots/vests/income/options all had one).
    idem_key = (change.get("idempotency_key") or "").strip()
    if idem_key:
        existing = grants.find_by_idempotency_key(idem_key)
        if existing:
            return {
                "ok": True,
                "grant_id": existing["id"],
                "locked_position_id": existing.get("locked_position_id"),
                "deduped": True,
            }

    inst = instruments.find_by_id(change.get("instrument_id", ""))
    if not inst:
        raise ValidationError("instrument not found", instrument_id=change.get("instrument_id"))

    tax_event = change.get("tax_event", "at_vest")
    if tax_event not in _VALID_TAX_EVENTS:
        raise ValidationError(f"tax_event must be one of {_VALID_TAX_EVENTS}")

    raw_tranches = change.get("tranches") or []
    tranches = _stamp_tranches(raw_tranches)
    total_units = float(change.get("total_units") or 0)
    tranche_sum = sum(float(t.get("units") or 0) for t in tranches)
    if abs(tranche_sum - total_units) > 0.01:
        raise ValidationError(
            "Σ tranche.units must equal total_units (±0.01)",
            total_units=total_units,
            tranche_sum=round(tranche_sum, 4),
        )

    # Adopt an existing locked position or create one.
    locked_position_id: Optional[str] = change.get("locked_position_id")
    if locked_position_id:
        _assert_adoptable(locked_position_id)
    else:
        # account_id or account_name required when creating a new locked position.
        if not change.get("account_id") and not change.get("account_name"):
            raise ValidationError(
                "an account is required — which custodian holds this grant?",
                hint="pass account_id (from list_accounts) or account_name",
            )
        locked_position_id = _create_locked_position(inst, change, total_units)

    account_id = _resolve_account_id({**change, "locked_position_id": locked_position_id}, inst)
    grant_price_per_unit = _validate_grant_price(change.get("grant_price_per_unit"))

    grant_id = new_id("grant")
    now = _now()
    grant = grants.append(
        {
            "id": grant_id,
            "type": change.get("grant_type", "rsu") or "rsu",
            "instrument_id": inst["id"],
            "grant_date": change.get("grant_date", user_today().isoformat()),
            "total_units": total_units,
            "currency": (change.get("currency") or inst.get("quote_currency") or "").upper(),
            "grant_price_per_unit": grant_price_per_unit,
            "tax_event": tax_event,
            "status": "active",
            "release_required": bool(change.get("release_required", False)),
            "locked_position_id": locked_position_id,
            "pending_position_id": None,
            "account_id": account_id,
            "tranches": tranches,
            "note": change.get("note", ""),
            "idempotency_key": idem_key,
            "created_at": now,
            "updated_at": now,
        }
    )

    position_figures.rederive(locked_position_id)

    # Seed the daily vest-reminder Automation on first grant creation.
    from backend.services.automations.service import seed_vest_reminder

    seed_vest_reminder()

    return {"ok": True, "grant_id": grant["id"], "locked_position_id": locked_position_id}


def _assert_adoptable(position_id: str) -> None:
    """A grant adopts only an UNVESTED row with no lots and no other grant: once adopted its
    units follow the grant's schedule, so adopting an owned, lot-backed holding would
    overwrite its quantity and strand its lots."""
    from backend.db.lot_store import LotStore
    from backend.db.position_store import vesting_state_of

    pos = positions.find_by_id(position_id)
    if not pos:
        raise ValidationError("locked_position_id not found", locked_position_id=position_id)
    owned = any(
        position_id in (g.get("locked_position_id"), g.get("pending_position_id"))
        for g in grants.all()
    )
    if vesting_state_of(pos) != "unvested" or owned or LotStore().lots_for_position(position_id):
        raise ValidationError(
            "only an unvested position with no lots and no grant can be adopted as a grant's "
            "unvested units — leave locked_position_id out and the grant creates its own",
            locked_position_id=position_id,
        )


def _edited_tranches(grant: dict, raw: list[dict]) -> list[dict]:
    """A schedule edit keeps each tranche's VEST STATE from the store — never from the edit.
    Whether a tranche vested is a fact only a recorded vest establishes (and its undo
    reverses); taking it from the payload re-counted vested units as unvested (an edit
    without the flags) or dropped units with no vest behind them (an edit that set them).
    A vested tranche cannot be dropped or resized until its vest is undone."""
    stored = {t.get("id"): t for t in grant.get("tranches") or [] if t.get("id")}
    edited = _stamp_tranches(raw)
    for t in edited:
        before = stored.get(t["id"])
        t["vested"] = bool(before and before.get("vested"))
        t["vest_event_id"] = before.get("vest_event_id") if before else None
        if t["vested"] and abs(float(t.get("units") or 0) - float(before.get("units") or 0)) > 1e-9:
            raise ValidationError(
                "this tranche has already vested — undo its vest before changing its units",
                tranche_id=t["id"],
            )
    kept = {t["id"] for t in edited}
    dropped = [tid for tid, t in stored.items() if t.get("vested") and tid not in kept]
    if dropped:
        raise ValidationError(
            "a vested tranche can't be removed from the schedule — undo its vest first",
            tranche_id=dropped[0],
        )
    return edited


def _stamp_tranches(raw: list[dict]) -> list[dict]:
    """Ensure every tranche has a stable id (a nested sub-object, not a store entity — ADR 0011)."""
    out = []
    for t in raw:
        out.append(
            {
                **t,
                "id": t.get("id") or str(uuid.uuid4()),
                "vested": bool(t.get("vested", False)),
                "vest_event_id": t.get("vest_event_id"),
            }
        )
    return out


def _create_locked_position(inst: dict, change: dict, total_units: float) -> str:
    """Create the vesting_state=unvested position for unvested units (no lot)."""
    from backend.services import positions as positions_service

    result = positions_service.apply_position_change(
        {
            "type": "add",
            "instrument_id": inst["id"],
            "account_id": change.get("account_id"),
            "account_name": change.get("account_name"),
            "vesting_state": "unvested",
            # A shell: its units are re-derived from the grant's tranches once it exists.
            "_grant_managed_ok": True,
        }
    )
    if result.get("error"):
        raise ValidationError(f"failed to create locked position: {result['error']}")
    return result["position_id"]


def _resolve_account_id(change: dict, inst: dict) -> Optional[str]:
    if change.get("account_id"):
        from backend.services.accounts import require_account_id

        return require_account_id(change["account_id"])
    # Derive from the locked position — adopted, or just created from an account_name.
    locked_id = change.get("locked_position_id")
    if locked_id:
        pos = positions.find_by_id(locked_id)
        if pos:
            return pos.get("account_id")
    return None


# ── grant_update / grant_cancel ─────────────────────────────────────────────────────


def _validate_grant_price(raw) -> Optional[float]:
    """Validate and return grant_price_per_unit; None when not provided."""
    if raw is None:
        return None
    val = float(raw)
    if not math.isfinite(val) or val < 0:
        raise ValidationError("grant_price_per_unit must be a finite number ≥ 0")
    return val


def _grant_update(change: dict) -> dict:
    grant_id = change.get("grant_id") or change.get("id")
    grant = grants.find_by_id(grant_id) if grant_id else None
    if not grant:
        raise NotFoundError("grant not found", grant_id=grant_id)
    updates: dict = {}
    for field in ("grant_date", "currency", "note"):
        if field in change:
            updates[field] = change[field]
    if "grant_price_per_unit" in change:
        updates["grant_price_per_unit"] = _validate_grant_price(change["grant_price_per_unit"])
    if "tax_event" in change:
        if change["tax_event"] not in _VALID_TAX_EVENTS:
            raise ValidationError(f"tax_event must be one of {_VALID_TAX_EVENTS}")
        updates["tax_event"] = change["tax_event"]
    if "release_required" in change:
        # Only the FUTURE vests of this grant change shape. Vests already recorded keep
        # whatever they did at write time — flipping the flag must never silently move
        # already-delivered shares back into pending (or vice versa).
        updates["release_required"] = bool(change["release_required"])
    if "tranches" in change:
        updates["tranches"] = _edited_tranches(grant, change["tranches"])

    # Account move: update grant.account_id AND relocate the locked position.
    if "account_id" in change or "account_name" in change:
        from backend.services import positions as positions_service

        target = positions_service.resolve_account(
            account_id=change.get("account_id"),
            account_name=change.get("account_name"),
        )
        updates["account_id"] = target["id"]
        # The grant's OWN rows move with it — the unvested units AND the vested-but-not-yet-
        # delivered ones (which used to stay behind at the old custodian). Shares already
        # delivered are a holding of their own and stay where they were delivered.
        for key in ("locked_position_id", "pending_position_id"):
            own_pos_id = grant.get(key)
            if own_pos_id and positions.find_by_id(own_pos_id):
                positions_service.apply_position_change(
                    {
                        "type": "update",
                        "position_id": own_pos_id,
                        "changes": {"account_id": target["id"]},
                        "_grant_managed_ok": True,
                    }
                )

    if not updates:
        return {"ok": True, "grant_id": grant_id, "changed": 0}
    with locked_unit_of_work(grants_tbl, positions_tbl) as conn:
        grants.update_grant(grant_id, updates, conn=conn)
        if "tranches" in updates and grant.get("locked_position_id"):
            position_figures.rederive(grant["locked_position_id"], conn=conn)
    return {"ok": True, "grant_id": grant_id, "changed": len(updates)}


def _grant_cancel(change: dict) -> dict:
    grant_id = change.get("grant_id") or change.get("id")
    grant = grants.find_by_id(grant_id) if grant_id else None
    if not grant:
        raise NotFoundError("grant not found", grant_id=grant_id)
    # Cancelled = forfeited: the unvested units stop counting (ADR 2026-10-07).
    with locked_unit_of_work(grants_tbl, positions_tbl) as conn:
        grants.update_grant(grant_id, {"status": "cancelled"}, conn=conn)
        if grant.get("locked_position_id"):
            position_figures.rederive(grant["locked_position_id"], conn=conn)
    return {"ok": True, "grant_id": grant_id, "status": "cancelled"}


# ── vest_apply — the four-store atomic action ───────────────────────────────────────


def _vest_apply(change: dict) -> dict:  # noqa: C901 — ordered choke-point sequence, complexity intentional
    """Apply a vest: FMV lot + locked decrement + vest_event. NO ledger transaction.

    A vest is paid in SHARES, not cash. Booking the compensation as an inflow to the
    custodian account (what this did until 2026-09-22) put money in an account that never
    received any, while the shares it bought were ALSO counted — net worth moved by twice
    the vest (ADR 2026-09-22-a-vest-books-no-income-transaction). The vest's tax facts
    (units, FMV, currency, dates, gross gain, the grant's tax_event) all live on the
    vest_event and drive ``equity_report`` unchanged; they are information, not a
    money movement. ``txn_id`` stays None — vest_events written BEFORE this change keep
    theirs, and every reader still honours it."""
    # Step 0: idempotency checks (before any write)
    key = change.get("idempotency_key")
    if key:
        existing = vest_events.find_by_idempotency_key(key)
        if existing:
            return {"ok": True, "vest_event_id": existing["id"], "idempotent": True}

    grant_id = change.get("grant_id")
    tranche_id = change.get("tranche_id")
    if grant_id and tranche_id:
        existing = vest_events.find_by_grant_tranche(grant_id, tranche_id)
        if existing:
            return {"ok": True, "vest_event_id": existing["id"], "idempotent": True}

    # Step 1: pure validation (nothing written yet)
    grant, tranche, vest_units, fmv_per_unit, fmv_currency, vest_date, account_id = _validate_vest(
        change
    )

    price_paid = float(change.get("price_paid_per_unit") or 0.0)
    gross_gain_native = round(vest_units * (fmv_per_unit - price_paid), 2)
    base = base_currency()

    from backend.services import lots as lots_service

    rates = lots_service._load_rates()
    base_amount = to_base(gross_gain_native, fmv_currency, base, rates=rates)
    source = change.get("source", "manual")

    # Step 2 (PRE-transaction, Task 4, storage plan 4b): where the vested shares WILL
    # land — resolve (or lazily create) the destination position SHELL. Idempotent +
    # inert-if-orphaned if a later step in this same attempt fails and rolls back (see
    # equity_release.resolve_pending_position's docstring) — kept OUTSIDE the composed
    # transaction below so its own side effects (grant-managed position add, price
    # backfill trigger) never run under a foreign conn.
    pending_release = bool(grant.get("release_required"))
    pending_pos_id: Optional[str] = None
    liquid_pos_id: Optional[str] = None
    if pending_release:
        pending_pos_id = equity_release.resolve_pending_position(grant, account_id)
    else:
        liquid_pos_id = _find_or_create_liquid_position(grant, account_id)
        _assert_no_existing_vest_lot(liquid_pos_id, tranche_id)

    # TASK 4: the FMV-lot mint (or the pending-position quantity change) + the
    # locked-position decrement + the vest_event append + the grant's tranche-state update
    # commit as ONE transaction — "the stores must agree" (module docstring) is a database
    # guarantee, not just a design intent. `transactions`/`txn_audit` are NOT in the lock
    # set: a vest writes no ledger row at all (see this function's docstring).
    lot_id: Optional[str] = None
    with locked_unit_of_work(grants_tbl, lots_tbl, positions_tbl, vest_events_tbl) as conn:
        # Step 3: where the vested shares land.
        #
        #   one-step (release_required=False, the default and the majority of plans
        #       worldwide): straight to an FMV-basis lot on the LIQUID sibling position —
        #       unchanged.
        #   two-step (release_required=True): the shares are not delivered yet, so they
        #       park in the pending-release position (vested, semi-liquid) and NO lot
        #       exists yet. Booking a liquid lot here would claim they are sellable,
        #       which is the bug this path exists to fix.
        if not pending_release:
            lot_id = equity_release.create_vest_lot(
                position_id=liquid_pos_id,
                grant_id=grant["id"],
                tranche_id=tranche_id,
                units=vest_units,
                unit_cost=fmv_per_unit,
                cost_currency=fmv_currency,
                acquired_at=vest_date,
                txn_id=None,
                conn=conn,
            )

        # Step 4: append vest_event + mark tranche consumed (one grant write, last step)
        ev = vest_events.append(
            {
                "grant_id": grant["id"],
                "tranche_id": tranche_id,
                "date": vest_date,
                "units": vest_units,
                "fmv_per_unit": fmv_per_unit,
                "fmv_currency": fmv_currency,
                "price_paid_per_unit": price_paid,
                "units_withheld": float(change.get("units_withheld") or 0.0),
                "gross_gain_native": gross_gain_native,
                # Always None for new events. The field stays in the shape because events
                # written BEFORE 2026-09-22 carry a real id that _vest_undo and
                # equity_report still honour.
                "txn_id": None,
                "lot_id": lot_id,
                "locked_position_id": grant["locked_position_id"],
                "pending_release": pending_release,
                "expected_release_date": change.get("expected_release_date") or None,
                # One-step delivery is immediate, but it is still a RELEASE fact. Persist
                # it instead of making readers infer delivery from the existence of a lot.
                "released_on": None if pending_release else vest_date,
                "pending_position_id": pending_pos_id,
                "idempotency_key": key,
                "source": source,
                "base_amount": round(base_amount, 2),
                "base_currency": base,
                "note": change.get("note", ""),
            },
            conn=conn,
        )
        _mark_tranche_vested(grant, tranche_id, ev["id"], vest_units, conn=conn)

        # Step 5: the locked (and, two-step, the pending) position now follow the records
        # just written — the tranche state and the parked vest event. Never ± arithmetic on
        # the stored number (ADR 2026-10-07-a-position-quantity-is-derived-from-its-records).
        position_figures.rederive(grant["locked_position_id"], conn=conn)
        if pending_pos_id:
            position_figures.rederive(pending_pos_id, conn=conn)

    return {
        "ok": True,
        "vest_event_id": ev["id"],
        "grant_id": grant["id"],
        "tranche_id": tranche_id,
        "txn_id": None,
        "lot_id": lot_id,
        "gross_gain_native": gross_gain_native,
        "gross_gain_base": round(base_amount, 2),
        "base_currency": base,
        "pending_release": pending_release,
        "expected_release_date": change.get("expected_release_date") or None,
        "released_on": None if pending_release else vest_date,
    }


def _validate_vest(change: dict):
    """Pure validation. Returns (grant, tranche, units, fmv, fmv_ccy, vest_date, account_id)."""
    grant_id = change.get("grant_id")
    tranche_id = change.get("tranche_id")
    grant = grants.find_by_id(grant_id) if grant_id else None
    if not grant:
        raise NotFoundError("grant not found", grant_id=grant_id)
    if grant.get("status") not in ("active",):
        raise ValidationError(
            f"grant is not active (status={grant.get('status')})", grant_id=grant_id
        )

    tranche = next((t for t in (grant.get("tranches") or []) if t.get("id") == tranche_id), None)
    if not tranche:
        raise NotFoundError("tranche not found", tranche_id=tranche_id)
    if tranche.get("vested"):
        raise ValidationError("tranche already vested", tranche_id=tranche_id)

    tranche_units = float(tranche.get("units") or 0)
    vest_units = float(change.get("units") or tranche_units or 0)
    if vest_units <= 0:
        raise ValidationError("units must be positive")
    if vest_units > tranche_units + 1e-9:
        raise ValidationError(
            "cannot vest more units than the tranche",
            requested=vest_units,
            tranche_units=tranche_units,
        )

    fmv_per_unit = change.get("fmv_per_unit")
    if fmv_per_unit is None:
        raise ValidationError("fmv_per_unit is required")
    fmv_per_unit = float(fmv_per_unit)
    if not math.isfinite(fmv_per_unit) or fmv_per_unit < 0:
        raise ValidationError("fmv_per_unit must be a finite number ≥ 0")

    price_paid_raw = change.get("price_paid_per_unit")
    if price_paid_raw is not None:
        price_paid_val = float(price_paid_raw)
        if not math.isfinite(price_paid_val) or price_paid_val < 0:
            raise ValidationError("price_paid_per_unit must be a finite number ≥ 0")

    inst = instruments.find_by_id(grant.get("instrument_id", ""))
    default_ccy = (inst or {}).get("quote_currency", "")
    fmv_currency = (change.get("fmv_currency") or default_ccy or "").upper()
    if not fmv_currency:
        raise ValidationError("fmv_currency is required")

    vest_date = change.get("vest_date") or tranche.get("vest_date") or user_today().isoformat()

    # Derive account from the locked position's CURRENT account_id so a position moved
    # between custodians vests into the correct account without grant surgery.
    locked_pos = positions.find_by_id(grant.get("locked_position_id", ""))
    account_id = (locked_pos or {}).get("account_id") or grant.get("account_id")
    if not account_id:
        raise ValidationError("cannot determine account_id for vest; set account_id on grant")

    return grant, tranche, vest_units, fmv_per_unit, fmv_currency, vest_date, account_id


# Position/lot plumbing lives in equity_release so the vest and the release paths create
# the SAME shapes through one implementation (a divergence here would mean a released lot
# that differs from a vested one).
_find_or_create_liquid_position = equity_release.find_or_create_liquid_position
_assert_no_existing_vest_lot = equity_release.assert_no_existing_vest_lot


def _mark_tranche_vested(
    grant: dict,
    tranche_id: str,
    vest_event_id: str,
    vest_units: float,
    *,
    conn: Connection | None = None,
) -> None:
    """Mark a tranche vested.

    On a full vest the tranche is flipped to vested=True in-place. On a partial vest
    the tranche is split: the vested portion (vest_units, vested=True) replaces the
    original tranche, and a new remainder tranche (same vest_date + kind, vested=False,
    new uuid) is inserted immediately after it so the invariant
    ``locked_position.quantity == Σ(tranche.units where not vested)`` is preserved and
    the remainder stays visible in upcoming_vests.

    grant.total_units is the grant-level total and is NOT changed by a split.
    """
    tranches = [dict(t) for t in (grant.get("tranches") or [])]
    new_tranches: list[dict] = []
    for t in tranches:
        if t["id"] != tranche_id:
            new_tranches.append(t)
            continue

        tranche_units = float(t.get("units") or 0)
        remainder = round(tranche_units - vest_units, 10)
        is_partial = remainder > 1e-9

        vested_tranche = {**t, "units": vest_units, "vested": True, "vest_event_id": vest_event_id}
        new_tranches.append(vested_tranche)

        if is_partial:
            new_tranches.append(
                {
                    "id": str(uuid.uuid4()),
                    "vest_date": t.get("vest_date"),
                    "kind": t.get("kind", "time"),
                    "units": remainder,
                    "vested": False,
                    "vest_event_id": None,
                }
            )

    all_vested = all(t.get("vested") for t in new_tranches)
    grants.update_grant(
        grant["id"],
        {
            "tranches": new_tranches,
            "status": "fully_vested" if all_vested else grant.get("status", "active"),
        },
        conn=conn,
    )


# ── vest_undo ────────────────────────────────────────────────────────────────────────


def _vest_undo(change: dict) -> dict:
    """Reverse a vest event without orphans. Reverse order: locked++ → return the shares →
    delete txn → flip tranche → delete vest_event.

    "Return the shares" has two shapes: a delivered vest (or any one-step vest) deletes its
    FMV lot; a vest still awaiting release has no lot yet and instead drains its units back
    out of the pending position. Undoing a RELEASED two-step vest still deletes the lot —
    the release is undone along with the vest it belongs to, since a delivery with no vest
    behind it is not a state worth keeping."""
    vest_event_id = change.get("vest_event_id") or change.get("id")
    ev = vest_events.find_by_id(vest_event_id) if vest_event_id else None
    if not ev:
        raise NotFoundError("vest event not found", vest_event_id=vest_event_id)

    lot_id = ev.get("lot_id")
    txn_id = ev.get("txn_id")
    # TASK 4 (storage plan 4b): "composes the same set it reverses" — the locked-position
    # re-increment + the lot delete/pending-unpark + the income-txn reversal + the grant's
    # tranche-state flip + the vest_event delete now commit as ONE transaction. txn_audit
    # is locked for the same reason _vest_apply locks it (bulk_delete_transactions,
    # composed via conn=, writes `transactions` AND `txn_audit` as one unit).
    with locked_unit_of_work(
        grants_tbl, lots_tbl, positions_tbl, txns_tbl, vest_events_tbl, txn_audit_tbl
    ) as conn:
        # Step 1: return the shares — a delivered vest deletes its lot (which refuses if
        # partially consumed) through the conn=-accepting core instead of re-entering
        # apply_lot_change's own top-level transaction (self-deadlock guard, as on the mint
        # side). Parked units have no lot: deleting the event below is what un-parks them.
        if not ev.get("pending_release") and lot_id:
            from backend.services import lots as lots_service

            result = lots_service._delete_lot_core(lot_id, conn=conn)
            if result.get("error"):
                raise ValidationError(
                    f"cannot undo: {result['error']} — dispose/adjust manually",
                    lot_id=lot_id,
                )

        # Step 3: soft-delete the income txn — present only on events written before
        # 2026-09-22, when a vest still booked one.
        if txn_id:
            from backend.services import ledger as ledger_service

            ledger_service.bulk_delete_transactions([txn_id], conn=conn, reject_identities=False)

        # Step 4: flip the tranche back + update grant status
        grant = grants.find_by_id(ev["grant_id"], conn=conn)
        if grant:
            tranches = [dict(t) for t in (grant.get("tranches") or [])]
            for t in tranches:
                if t.get("id") == ev.get("tranche_id"):
                    t["vested"] = False
                    t["vest_event_id"] = None
            new_status = "active" if grant.get("status") == "fully_vested" else grant.get("status")
            grants.update_grant(
                grant["id"], {"tranches": tranches, "status": new_status}, conn=conn
            )

        # Step 5: delete vest_event
        vest_events.delete_event(vest_event_id, conn=conn)

        # Step 6: the locked and pending positions follow the records just restored.
        for position_id in (ev.get("locked_position_id"), ev.get("pending_position_id")):
            if position_id:
                position_figures.rederive(position_id, conn=conn)

    return {"ok": True, "vest_event_id": vest_event_id, "txn_id": txn_id, "lot_id": lot_id}


# ── Hydrated grants list (read — computed fields for the REST response) ──────────────


def hydrated_grants() -> list[dict]:
    """Return all grants enriched with computed fields the API and UI need.

    Each grant carries: ``symbol``, ``total_units``, ``vested_units``,
    ``unvested_units``, ``next_vest_date`` (ISO or None), ``account_id``,
    ``account_name`` (effective — derived from locked position when available),
    and each tranche gains a ``status`` field:
    "vested" | "upcoming" | "performance_pending".
    """
    from backend.db.account_store import AccountStore

    insts = {i["id"]: i for i in instruments.all()}
    accts = {a["id"]: a for a in AccountStore().all()}
    pending_by_grant: dict[str, float] = {}
    pending_event_ids: set[str] = set()
    for ev in vest_events.query_pending_releases():
        gid = ev.get("grant_id") or ""
        pending_by_grant[gid] = pending_by_grant.get(gid, 0.0) + float(ev.get("units") or 0)
        pending_event_ids.add(ev.get("id") or "")
    result = []
    for grant in grants.all():
        inst = insts.get(grant.get("instrument_id", "")) or {}
        symbol = inst.get("symbol") or inst.get("name") or ""
        tranches = grant.get("tranches") or []
        vested_units = sum(float(t.get("units") or 0) for t in tranches if t.get("vested"))
        unvested_units = sum(float(t.get("units") or 0) for t in tranches if not t.get("vested"))
        upcoming = sorted(
            (t for t in tranches if not t.get("vested")),
            key=lambda t: t.get("vest_date") or "",
        )
        next_vest_date = upcoming[0]["vest_date"] if upcoming else None

        enriched_tranches = []
        for t in tranches:
            if t.get("vested"):
                # "vested" alone would tell the operator the shares are theirs to sell.
                # A pending release is a distinct, actionable state.
                status = (
                    "pending_release" if t.get("vest_event_id") in pending_event_ids else "vested"
                )
            elif t.get("kind") == "performance":
                status = "performance_pending"
            else:
                status = "upcoming"
            enriched_tranches.append({**t, "status": status})

        # Derive effective account from locked position's current account_id.
        locked_pos = positions.find_by_id(grant.get("locked_position_id") or "")
        effective_account_id = (locked_pos or {}).get("account_id") or grant.get("account_id")
        effective_account = accts.get(effective_account_id or "") or {}

        result.append(
            {
                **grant,
                "symbol": symbol,
                "vested_units": vested_units,
                "unvested_units": unvested_units,
                # Vested but not yet delivered. A subset of vested_units, NOT a third
                # slice of the grant — the UI shows it as "of which N awaiting release".
                "pending_release_units": round(pending_by_grant.get(grant.get("id", ""), 0.0), 10),
                "next_vest_date": next_vest_date,
                "tranches": enriched_tranches,
                "account_id": effective_account_id,
                "account_name": effective_account.get("name", ""),
            }
        )
    return result


def vest_history() -> dict:
    """Persisted vest events, newest first, shaped for the equity history UI.

    Release state comes only from each immutable event's delivery facts. A later edit to
    ``grant.release_required`` changes future vests, never the history of an existing one.
    Current indicative value is optional: without a cached market price the response says
    ``None`` instead of presenting vest-date FMV as though it were current.
    """
    from backend.services import lots as lots_service

    base = base_currency()
    rates = lots_service._load_rates()
    grant_rows = {grant["id"]: grant for grant in grants.all()}
    insts = {inst["id"]: inst for inst in instruments.all()}
    price_map = price_history.latest_map()
    today = user_today().isoformat()
    items = []

    for ev in reversed(vest_events.query_events()):
        grant = grant_rows.get(ev.get("grant_id", "")) or {}
        instrument_id = grant.get("instrument_id")
        inst = insts.get(instrument_id or "") or {}
        units = float(ev.get("units") or 0)
        price_source_id = inst.get("price_source_id") or ""
        last_price = price_map.get(price_source_id) if price_source_id else None
        indicative_value_base = None
        quote_currency = (inst.get("quote_currency") or base).upper()
        conversion_available = quote_currency == base or bool(
            rates.get(quote_currency) and rates.get(base)
        )
        if last_price is not None and units and conversion_available:
            indicative_value_base = round(
                to_base(last_price * units, quote_currency, base, rates=rates),
                2,
            )

        pending_release = ev.get("pending_release")
        awaiting_release = pending_release is True
        released_on = ev.get("released_on")
        if released_on:
            release_status = "released"
            release_mode = None
        elif awaiting_release:
            release_status = "awaiting_release"
            release_mode = "move_pending"
        elif pending_release is False:
            # Explicit false was the original one-step delivery fact. Keep historical
            # compatibility while all new one-step events persist released_on directly.
            release_status = "released"
            release_mode = None
            released_on = ev.get("date")
        else:
            # Legacy vest events predate explicit delivery facts. A lot proves cost basis,
            # not when shares landed; keep the release date actionable instead of guessing.
            release_status = "not_released"
            release_mode = "record_only"
        expected_release_date = ev.get("expected_release_date")
        items.append(
            {
                "vest_event_id": ev["id"],
                "grant_id": ev.get("grant_id"),
                "tranche_id": ev.get("tranche_id"),
                "symbol": inst.get("symbol", ""),
                "instrument_id": instrument_id,
                "vest_date": ev.get("date"),
                "units": units,
                "fmv_per_unit": ev.get("fmv_per_unit"),
                "fmv_currency": ev.get("fmv_currency"),
                "release_status": release_status,
                "release_mode": release_mode,
                "released_on": released_on,
                "expected_release_date": expected_release_date,
                "overdue": bool(
                    awaiting_release and expected_release_date and expected_release_date < today
                ),
                "indicative_value_base": indicative_value_base,
                "base_currency": base,
            }
        )

    return {"total": len(items), "items": items}


# ── Preview (pure — no writes) ───────────────────────────────────────────────────────


def preview_vest(change: dict) -> dict:
    """Pure preview of a vest_apply (no writes). Returns the confirm-card shape."""
    try:
        grant, tranche, vest_units, fmv_per_unit, fmv_currency, vest_date, account_id = (
            _validate_vest(change)
        )
    except (NotFoundError, ValidationError) as exc:
        return {"error": str(exc)}

    from backend.services import lots as lots_service

    base = base_currency()
    rates = lots_service._load_rates()
    price_paid = float(change.get("price_paid_per_unit") or 0.0)
    gross_gain_native = round(vest_units * (fmv_per_unit - price_paid), 2)
    base_amount = round(to_base(gross_gain_native, fmv_currency, base, rates=rates), 2)
    tax_event = grant.get("tax_event", "at_vest")

    inst = instruments.find_by_id(grant.get("instrument_id", "")) or {}
    return {
        "grant_id": grant["id"],
        "tranche_id": tranche["id"],
        "instrument_id": grant["instrument_id"],
        "symbol": inst.get("symbol", ""),
        "vest_date": vest_date,
        "units": vest_units,
        "fmv_per_unit": fmv_per_unit,
        "fmv_currency": fmv_currency,
        "price_paid_per_unit": price_paid,
        "units_withheld": float(change.get("units_withheld") or 0.0),
        "gross_gain_native": gross_gain_native,
        "base_amount": base_amount,
        "base_currency": base,
        # tax_event is INFORMATION — it no longer changes what the write books (ADR
        # 2026-09-22-a-vest-books-no-income-transaction). Nothing here may promise a
        # ledger row: the preview must render exactly what the write stores.
        "tax_event": tax_event,
        # Tells the confirm card whether these shares become sellable now or park as
        # pending — the single most misleading thing to get wrong on a vest card.
        "release_required": bool(grant.get("release_required")),
        "expected_release_date": change.get("expected_release_date") or None,
    }


# ── Upcoming vests (calendar — computed, not stored) ─────────────────────────────────


def upcoming_vests(window_days: Optional[int] = None) -> list[dict]:
    """Unvested tranches of active grants sorted by vest_date. Annotates indicative value
    at current price via PriceHistoryStore.latest_map() × FX. ``window_days=None`` returns
    all; ``window_days=0`` returns only today (for the vest_reminder sweep)."""
    today = user_today()
    today_str = today.isoformat()
    from backend.services import lots as lots_service

    base = base_currency()
    rates = lots_service._load_rates()
    price_map = price_history.latest_map()

    insts = {i["id"]: i for i in instruments.all()}
    result = []
    for grant in grants.query_grants(status="active"):
        inst = insts.get(grant.get("instrument_id", ""))
        symbol = (inst or {}).get("symbol", "")
        pid = (inst or {}).get("price_source_id", "")
        last_price = price_map.get(pid) if pid else None
        ccy = (inst or {}).get("quote_currency", base)

        for tranche in grant.get("tranches") or []:
            if tranche.get("vested"):
                continue
            vd = tranche.get("vest_date", "")
            if window_days is not None:
                if window_days == 0 and vd != today_str:
                    continue
                if window_days > 0:
                    cutoff = date.fromordinal(today.toordinal() + window_days).isoformat()
                    if vd > cutoff:
                        continue
            units = float(tranche.get("units") or 0)
            indicative_value_base = None
            if last_price is not None and units:
                native_value = last_price * units
                indicative_value_base = round(to_base(native_value, ccy, base, rates=rates), 2)
            result.append(
                {
                    "grant_id": grant["id"],
                    "tranche_id": tranche["id"],
                    "symbol": symbol,
                    "instrument_id": grant["instrument_id"],
                    "vest_date": vd,
                    "units": units,
                    "indicative_value_base": indicative_value_base,
                    "overdue": vd < today_str and not tranche.get("vested"),
                    # Without these an agent can't tell a guaranteed time-based vest from a
                    # performance-conditional one and would overstate certainty (root-caused
                    # 2026-07-09) — list_grants already carries both, this just matches it here.
                    "kind": tranche.get("kind", "time"),
                    "condition_note": tranche.get("condition_note"),
                }
            )
    result.sort(key=lambda r: r["vest_date"])
    return result


# ── FMV prefill ──────────────────────────────────────────────────────────────────────


def fmv_prefill(grant_id: str, tranche_id: str) -> dict:
    """Return {fmv_per_unit, fmv_currency, as_of, source} from price history at/before
    the tranche's vest_date. ``fmv_per_unit=None, source='none'`` when no price."""
    grant = grants.find_by_id(grant_id)
    if not grant:
        raise NotFoundError("grant not found", grant_id=grant_id)
    tranche = next((t for t in (grant.get("tranches") or []) if t.get("id") == tranche_id), None)
    if not tranche:
        raise NotFoundError("tranche not found", tranche_id=tranche_id)

    vest_date = tranche.get("vest_date") or user_today().isoformat()
    inst = instruments.find_by_id(grant.get("instrument_id", ""))
    if not inst:
        return {"fmv_per_unit": None, "fmv_currency": None, "as_of": None, "source": "none"}

    fmv_currency = (inst.get("quote_currency") or "").upper()
    pid = inst.get("price_source_id") or ""
    if not pid:
        return {
            "fmv_per_unit": None,
            "fmv_currency": fmv_currency or None,
            "as_of": None,
            "source": "none",
        }

    as_of_map = price_history.as_of_maps([vest_date])
    price_on_day = as_of_map.get(vest_date, {}).get(pid)
    if price_on_day is None:
        return {
            "fmv_per_unit": None,
            "fmv_currency": fmv_currency or None,
            "as_of": None,
            "source": "none",
        }

    # Resolve the actual date of the price point used (may be a prior-close, not vest_date).
    actual_date = vest_date
    for pt in reversed(price_history.series(pid)):
        if pt.get("date", "") <= vest_date:
            actual_date = pt["date"]
            break

    return {
        "fmv_per_unit": float(price_on_day),
        "fmv_currency": fmv_currency,
        "as_of": actual_date,
        "source": "price_history",
    }
