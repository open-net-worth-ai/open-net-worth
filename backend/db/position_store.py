"""
PositionStore — holdings (the "how much, at what cost").

Each position links one instrument to one account with a quantity and cost basis.
The same instrument held in three exchanges is one instrument + three positions. Two
grants of the same stock in one account with different cost bases are two positions,
distinguished by `vesting_state`. The lots layer links back via
`lot.position_id` — the reverse FK; positions carry no lot key.

Identity is the UUID `id` — nothing keys on (label, category) anymore.

Money lives in payload (cost_basis/quantity) — exact via JSON round-trip; no promoted
money column. quantity/cost_basis/cost_currency are DERIVED from the position's records
(lots, parked vests, the grant schedule) and written only through ``write_figures``.

Position schema:
    id            : str
    instrument_id : str       FK → instruments.json
    account_id    : str       FK → accounts.json (the venue)
    quantity      : float     real number, not a string
    cost_basis    : float|None avg cost per unit in cost_currency; None/0 = unknown
    cost_currency : str       usually == instrument.quote_currency, not always
    vesting_state : "vested" | "unvested"   — entitlement, NOT liquidity (see below)
    acquired_at   : str|None  optional; enables holding-period later
    created_at    : ISO
    updated_at    : ISO

See .claude/rfcs/instrument-position-data-model.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.errors import DataIntegrityError
from backend.core.holdings_change import notify_holdings_changed
from backend.core.ids import new_id
from backend.db.engine import on_commit
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import positions

VESTING_STATES = {"vested", "unvested"}

# Derived from the position's records — written only through ``write_figures``.
FIGURE_FIELDS = frozenset({"quantity", "cost_basis", "cost_currency"})

# ── Legacy `bucket` -> `vesting_state` ────────────────────────────────────────
# `bucket` used to mean TWO things at once: entitlement on the backend (locked = unvested
# grant units) and liquidity on the frontend (locked = property/CPF). Only the backend
# ever WROTE it, so the stored values carry the entitlement sense and map cleanly.
#
# READ THE `contingent` ROW CAREFULLY: it meant a fully-entitled holding, so it maps to
# VESTED. The word later means "not entitled" in accounting prose; mapping it to
# `unvested` here would silently delete real money from net worth.
_LEGACY_BUCKET_TO_VESTING = {
    "locked": "unvested",
    "liquid": "vested",
    "contingent": "vested",
}


def vesting_state_of(row: dict) -> str:
    """The row's entitlement, tolerating rows written before the split.

    A stored `vesting_state` always wins. Otherwise the legacy `bucket` is mapped. A row
    with neither is `vested` — the safe default, since the only producer of unvested rows
    is the equity choke and it always stamps the field.
    """
    state = row.get("vesting_state")
    if state in VESTING_STATES:
        return state
    return _LEGACY_BUCKET_TO_VESTING.get(row.get("bucket"), "vested")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PositionStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(positions, id_field="id", engine=engine)

    # ── Domain queries ───────────────────────────────────────────────────────

    def query_positions(
        self,
        instrument_id: str | None = None,
        account_id: str | None = None,
        vesting_state: str | None = None,
    ) -> list[dict]:
        items = self.all()
        if instrument_id is not None:
            items = [p for p in items if p.get("instrument_id") == instrument_id]
        if account_id is not None:
            items = [p for p in items if p.get("account_id") == account_id]
        if vesting_state is not None:
            items = [p for p in items if vesting_state_of(p) == vesting_state]
        return items

    def instrument_ids(self) -> list[str]:
        """Distinct instrument ids referenced by any position (for price fetch fan-out)."""
        seen: dict[str, None] = {}
        for p in self.all():
            iid = p.get("instrument_id")
            if iid:
                seen[iid] = None
        return list(seen.keys())

    # ── Writes ───────────────────────────────────────────────────────────────

    def create(self, data: dict) -> dict:
        result = self.insert(_make_position(data))
        _notify_holdings_changed(None)
        return result

    def touch(self, id_val: str, changes: dict, *, conn: Connection | None = None) -> dict | None:
        """update_by_id but always bumps updated_at. REFUSES the derived figures
        (``FIGURE_FIELDS``) — those have one writer, ``write_figures`` (ADR
        2026-10-07-a-position-quantity-is-derived-from-its-records).

        ``conn``, when given, composes this write into a caller's own open transaction
        (Task 4, storage plan 4b — the trade-atomicity choke seam)."""
        figures = FIGURE_FIELDS & set(changes)
        if figures:
            raise DataIntegrityError(
                f"position {', '.join(sorted(figures))} is derived from its records — "
                "only services.position_figures.rederive writes it"
            )
        return self._write(id_val, dict(changes), conn=conn)

    def write_figures(
        self, id_val: str, figures: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """The ONE store entry for quantity / cost_basis / cost_currency. Called only by
        ``services.position_figures.rederive`` (pinned by a repo guard test)."""
        clean = {k: v for k, v in figures.items() if k in FIGURE_FIELDS}
        clean["quantity"] = float(clean.get("quantity") or 0)
        if clean.get("cost_basis") is not None:
            clean["cost_basis"] = float(clean["cost_basis"])
        return self._write(id_val, clean, conn=conn)

    def _write(self, id_val: str, changes: dict, *, conn: Connection | None) -> dict | None:
        result = self.update_by_id(id_val, {**changes, "updated_at": _now()}, conn=conn)
        if result is not None:
            _notify_holdings_changed(conn)
        return result

    def delete(self, id_val: str, *, conn: Connection | None = None) -> bool:
        """Override solely to notify holdings-changed on a REAL delete — see
        _notify_holdings_changed. Otherwise byte-identical to PayloadTableStore.delete."""
        deleted = super().delete(id_val, conn=conn)
        if deleted:
            _notify_holdings_changed(conn)
        return deleted


def _notify_holdings_changed(conn: Connection | None) -> None:
    """Fire core.holdings_change at the right commit boundary — mirrors
    services.ledger._after_outer_commit's composed/deferred split (this is the same class
    of problem: a post-write side effect that needs its OWN transaction).

    ``conn=None`` (create/remove_position, and update_position's plain edits — every call
    site except position_figures.rederive and the equity vest choke) means THIS mutate's
    own ``_use_conn`` already opened AND committed its own independent transaction by the
    time we're back here, so firing immediately is correct — the row is durably there.

    ``conn`` given (position_figures.rederive after a lot acquire/dispose; the equity vest
    apply/undo choke) means we're composed inside a caller's still-OPEN
    ``locked_unit_of_work``. Firing immediately here would run the watchlist read+write
    INSIDE that money choke's own transaction — extending a trade/vest transaction across an
    unrelated domain, which CLAUDE.md rules out — and risks the exact self-deadlock
    ``db.engine.on_commit`` exists to prevent (see that function's module note; empirically
    reproduced there for an equivalent case). Deferring via ``on_commit`` runs it exactly
    once, strictly after the outer transaction actually commits, and not at all on a
    rollback — a trade that never happened reconciles nothing.
    """
    if conn is not None:
        on_commit(notify_holdings_changed)
    else:
        notify_holdings_changed()


def _make_position(data: dict) -> dict:
    # Accepts the legacy `bucket` key so an in-flight caller (or a restored backup) still
    # lands in the right entitlement state instead of silently defaulting to vested.
    vesting = data.get("vesting_state") or _LEGACY_BUCKET_TO_VESTING.get(data.get("bucket"))
    if vesting not in VESTING_STATES:
        vesting = "vested"
    cost_basis = data.get("cost_basis")
    now = _now()
    return {
        "id": data.get("id") or new_id("pos"),
        "instrument_id": data.get("instrument_id", ""),
        "account_id": data.get("account_id", ""),
        "quantity": float(data.get("quantity", 0) or 0),
        "cost_basis": (float(cost_basis) if cost_basis not in (None, "") else None),
        "cost_currency": (data.get("cost_currency") or "").upper() or None,
        "vesting_state": vesting,
        "acquired_at": data.get("acquired_at"),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
    }
