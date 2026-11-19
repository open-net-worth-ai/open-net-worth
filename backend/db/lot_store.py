"""
LotStore — acquisition lots (the per-tranche cost-basis inventory backing a position).

A position aggregates `quantity` + a single avg `cost_basis`; a lot is one dated
acquisition (a buy/transfer-in/grant) with its OWN unit cost, currency, and
acquisition date. Selling consumes lots (FIFO or specific-ID) and books a realized
gain — that is what tax, holding-period, and true-return math depend on. Lots are
*mutable*: a lot's `remaining_qty` shrinks as it is disposed; a closed lot
(`remaining_qty == 0`) stays for audit/holding-period history.

Identity is the UUID `id`. The relationship is the reverse FK `lot.position_id`
(the position's reserved `lot_id` is now ignored). See
.claude/rfcs/phase2/inv1-tax-lots-pnl.md §7.1.

Money in payload (unit_cost/fees/fx_rate) — exact via JSON round-trip; no promoted
money column.

Lot schema:
    id            : str
    position_id   : str       FK → positions.json (the instrument × account inventory)
    instrument_id : str       denormalized FK → instruments.json
    account_id    : str       denormalized FK → accounts.json (the venue)
    acquired_at   : str       "YYYY-MM-DD" acquisition date (FIFO order + holding period);
                              "" = unknown (migration sentinel)
    original_qty  : float     qty at acquisition (immutable)
    remaining_qty : float     qty not yet disposed; 0 = closed
    unit_cost     : float|None cost per unit in cost_currency; None = unknown
    cost_currency : str       native currency of the cost basis
    fees          : float     acquisition fees in cost_currency (amortized into basis)
    kind          : "buy" | "drp" (dividend reinvestment) | "transfer_in" | "grant" |
                    "opening" (migration synthetic)
    txn_id        : str|None   the purchase cash row; a real column + FK → transactions.id
    idempotency_key : str|None client dedupe key (broker order id, email id…) — a
                              re-acquire with the same key is a no-op
    note          : str
    created_at    : ISO
    updated_at    : ISO

Checkpoint-covered (real user data).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Connection
from sqlalchemy import update as sa_update

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import lots

LOT_KINDS = {"buy", "drp", "transfer_in", "grant", "opening"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LotStore(BookPayloadTableStore):
    """``txn_id`` is a real FK column and the authority for the link; the payload keeps a copy
    for raw JSON readers, which an ON DELETE SET NULL can leave stale, so every read takes the
    column."""

    def __init__(self, engine=None):
        super().__init__(lots, id_field="id", engine=engine)

    def _insert_row(self, seq: int, id_val: Any, item: dict) -> dict:
        return {**super()._insert_row(seq, id_val, item), "txn_id": item.get("txn_id")}

    def _payload_values(self, item: dict) -> dict:
        return {"payload": item, "txn_id": item.get("txn_id")}

    def _row_item(self, row: Any) -> dict:
        return {**row.payload, "txn_id": row.txn_id}

    def _row(self, conn, id_val: str) -> dict | None:
        row = super()._row(conn, id_val)
        return _with_column_link(row) if row else None

    def _rows_map(self, conn, ids: list[str]) -> dict[str, dict]:
        return {k: _with_column_link(r) for k, r in super()._rows_map(conn, ids).items()}

    # ── Domain queries ────────────────────────────────────────────────────────

    def lots_for_position(
        self, position_id: str, include_closed: bool = True, *, conn: Connection | None = None
    ) -> list[dict]:
        """All lots for a position, FIFO-ordered (acquired_at, then created_at).

        `include_closed=False` returns only lots with remaining_qty > 0.

        ``conn``, when given, reads THROUGH the caller's own open transaction (Task 4,
        storage plan 4b) — load-bearing for ``position_figures.rederive`` when
        called right after ``bulk_consume``/``bulk_restore`` wrote the SAME lots in the
        still-open transaction (a fresh, conn-less read would see the pre-write state).
        ``conn=None`` is byte-identical to before.
        """
        items = [lot for lot in self.all(conn=conn) if lot.get("position_id") == position_id]
        if not include_closed:
            items = [lot for lot in items if float(lot.get("remaining_qty") or 0) > 0]
        return _fifo_sorted(items)

    def open_lots_for_position(
        self, position_id: str, *, conn: Connection | None = None
    ) -> list[dict]:
        """Open lots (remaining_qty > 0) for a position, FIFO-ordered — the disposal queue."""
        return self.lots_for_position(position_id, include_closed=False, conn=conn)

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Dedupe lookup for acquire re-runs (mirrors RealizedEventStore, AC-16).
        Empty key never matches."""
        if not key:
            return None
        for lot in self.all():
            if lot.get("idempotency_key") == key:
                return lot
        return None

    # ── Writes ────────────────────────────────────────────────────────────────

    def create(self, data: dict) -> dict:
        return self.insert(_make_lot(data))

    def touch(self, id_val: str, changes: dict, *, conn: Connection | None = None) -> dict | None:
        """update_by_id but always bumps updated_at."""
        return self.update_by_id(id_val, {**changes, "updated_at": _now()}, conn=conn)

    def bulk_consume(self, consumptions: list[tuple[str, float]], *, conn=None) -> list[dict]:
        """Decrement `remaining_qty` for several lots in a SINGLE read-write cycle.

        `consumptions` is a list of (lot_id, qty_to_take). Atomic w.r.t. the table:
        either all decrements land or none do (one transaction). Caller is
        responsible for having validated take <= remaining (see
        services.lots.select_lots_specific / select_lots_fifo).

        ``conn`` composes this write into a caller's own open transaction — the
        choke-point seam Task 4's trade-atomicity work threads through (a sell's
        lot-consumption + position update + realized-event write commit together).
        ``conn=None`` (the default, and every call site before Task 4) is
        byte-identical to the pre-existing standalone behavior: takes this table's
        lock (via ``_use_conn``) for the full read-modify-write — the same guarantee
        BUD-SWEEP-01 (2026-07-04) established, so two disposals racing against the
        SAME lot (or against a concurrent ``bulk_restore``/``touch``) can never
        last-writer-wins clobber each other.
        """
        by_lot: dict[str, float] = {}
        for lot_id, qty in consumptions:
            by_lot[lot_id] = by_lot.get(lot_id, 0.0) + float(qty)
        now = _now()
        touched: list[dict] = []
        with self._use_conn(conn) as conn:
            rows = self._rows_map(conn, list(by_lot))
            self._fire_hook(conn, [(lid, rows[lid]) for lid in rows])
            for lid, row in rows.items():
                lot = row["payload"]
                remaining = float(lot.get("remaining_qty") or 0) - by_lot[lid]
                # Clamp tiny negative float dust to 0; gross over-consumption is a caller
                # bug guarded upstream, but never persist a negative remaining_qty.
                merged = {
                    **lot,
                    "remaining_qty": 0.0 if abs(remaining) < 1e-9 or remaining < 0 else remaining,
                    "updated_at": now,
                }
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == lid).values(payload=merged)
                )
                touched.append(merged)
        return touched

    def bulk_restore(self, restorations: list[tuple[str, float]], *, conn=None) -> list[dict]:
        """Re-open lots by INCREMENTING `remaining_qty` — the inverse of bulk_consume, used
        to reverse a disposal (dispose_undo). Atomic w.r.t. the table (one transaction).

        `restorations` is a list of (lot_id, qty_to_give_back). A lot's remaining_qty is
        capped at its original_qty so an undo can never over-fill a lot beyond what it held.

        ``conn`` composes this write into a caller's own open transaction — see
        ``bulk_consume``'s docstring for the full contract; ``conn=None`` is
        byte-identical to the pre-existing standalone behavior (BUD-SWEEP-01,
        2026-07-04).
        """
        by_lot: dict[str, float] = {}
        for lot_id, qty in restorations:
            by_lot[lot_id] = by_lot.get(lot_id, 0.0) + float(qty)
        now = _now()
        touched: list[dict] = []
        with self._use_conn(conn) as conn:
            rows = self._rows_map(conn, list(by_lot))
            self._fire_hook(conn, [(lid, rows[lid]) for lid in rows])
            for lid, row in rows.items():
                lot = row["payload"]
                restored = float(lot.get("remaining_qty") or 0) + by_lot[lid]
                original = float(lot.get("original_qty") or 0)
                if original and restored > original:
                    restored = original
                merged = {**lot, "remaining_qty": restored, "updated_at": now}
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == lid).values(payload=merged)
                )
                touched.append(merged)
        return touched


def _with_column_link(row: dict) -> dict:
    return {**row, "payload": {**row["payload"], "txn_id": row["txn_id"]}}


def _fifo_sorted(items: list[dict]) -> list[dict]:
    """Order lots by acquired_at, then created_at — the FIFO disposal queue. An empty
    acquired_at (unknown date) sorts FIRST so undated migration lots are consumed before
    dated ones (a stable, deterministic choice)."""
    return sorted(
        items,
        key=lambda lot: (lot.get("acquired_at") or "", lot.get("created_at") or ""),
    )


def _make_lot(data: dict) -> dict:
    kind = data.get("kind", "buy")
    if kind not in LOT_KINDS:
        kind = "buy"
    unit_cost = data.get("unit_cost")
    original_qty = float(data.get("original_qty", data.get("quantity", 0)) or 0)
    # remaining defaults to original on creation; an explicit remaining_qty (rehydration) wins.
    remaining = data.get("remaining_qty")
    remaining_qty = float(remaining) if remaining is not None else original_qty
    now = _now()
    return {
        "id": data.get("id") or new_id("lot"),
        "position_id": data.get("position_id", ""),
        "instrument_id": data.get("instrument_id", ""),
        "account_id": data.get("account_id", ""),
        "acquired_at": data.get("acquired_at") or "",
        "original_qty": original_qty,
        "remaining_qty": remaining_qty,
        # 0/blank cost = unknown → None (mirrors positions_service.coerce_cost).
        "unit_cost": (float(unit_cost) if unit_cost not in (None, "", 0, 0.0) else None),
        "cost_currency": (data.get("cost_currency") or "").upper() or None,
        "fees": float(data.get("fees", 0) or 0),
        # Historical-rate lock (ADR 2026-07-15, mirrors transactions.json): the
        # cost_currency→base FX rate in effect when the lot was WRITTEN, plus the base it
        # was written against. TRUSTED for base-currency gain math while base_currency
        # still matches the current base; None on legacy/opening lots (readers fall back
        # to current-rate conversion, the pre-lock behavior).
        "fx_rate": (float(data["fx_rate"]) if data.get("fx_rate") else None),
        "base_currency": (data.get("base_currency") or "").upper() or None,
        "kind": kind,
        "txn_id": data.get("txn_id"),
        "idempotency_key": data.get("idempotency_key"),
        "note": data.get("note", ""),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
    }
