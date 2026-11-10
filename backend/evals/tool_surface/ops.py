"""THE SHARED OP REGISTRY — the single execution layer every arm routes through.

This module is what makes the comparison mean anything. All three surfaces (A: many
named tools, B: few fat tools, C: describe/apply) are *projections* of this one table.
They cannot diverge in business logic because they literally share these handlers, and
every handler calls the SAME production service choke point the real app calls
(``ledger.create_transaction``, ``planning.apply_budget_change``,
``lots_service.apply_lot_change``, ...).

So the only variable across arms is the SHAPE OF THE SURFACE the model sees: how many
tools, how fat, and whether the per-op schema is inline or fetched at runtime.

Design notes that apply EQUALLY to every arm (and so cannot bias the comparison):

* ``account_id`` is required on write ops — no name resolution. This is faithful to the
  production tools, and it keeps "number of round-trips" a meaningful metric (the model
  must actually look accounts up rather than guessing a name string).
* ``category`` accepts a label OR an opaque id, because the production write choke does
  exactly that via ``services.categories.resolve_category_ref``.
* Every handler returns a plain JSON-serialisable dict. Errors are returned as
  ``{"error": "..."}`` rather than raised, so error *presentation* is identical across
  arms and only error *frequency* varies.

Tiering (``core`` vs ``rare``) mirrors production's 19-of-271 always-loaded split: a
``rare`` op is not in the default offer and must be discovered before it can be called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.evals.tool_surface import bootstrap  # noqa: F401  (must precede backend imports)


@dataclass(frozen=True)
class Op:
    """One capability, independent of how any surface exposes it."""

    name: str  # canonical op id, e.g. "txn.add"
    tool_name: str  # arm A's tool name, e.g. "add_transaction"
    domain: str  # arm B's grouping key, e.g. "txns"
    action: str  # arm B's action discriminator, e.g. "add"
    tier: str  # "core" (always offered) | "rare" (must be discovered)
    summary: str
    properties: dict[str, Any]
    required: list[str] = field(default_factory=list)
    handler: Callable[[dict], dict] = None  # type: ignore[assignment]
    writes: bool = False

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": dict(self.properties),
            "required": list(self.required),
        }


_OPS: dict[str, Op] = {}


def op(**kwargs: Any) -> Callable[[Callable[[dict], dict]], Callable[[dict], dict]]:
    def wrap(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        entry = Op(handler=fn, **kwargs)
        if entry.name in _OPS:  # pragma: no cover - defensive
            raise ValueError(f"duplicate op {entry.name}")
        _OPS[entry.name] = entry
        return fn

    return wrap


def all_ops() -> dict[str, Op]:
    return dict(_OPS)


def get_op(name: str) -> Op | None:
    return _OPS.get(name)


def core_ops() -> list[Op]:
    return [o for o in _OPS.values() if o.tier == "core"]


def rare_ops() -> list[Op]:
    return [o for o in _OPS.values() if o.tier == "rare"]


# ── Shared param fragments ───────────────────────────────────────────────────

_ACCOUNT_ID = {"type": "string", "description": "Account id (from list_accounts)."}
_DATE = {"type": "string", "description": "ISO date, YYYY-MM-DD."}
_CATEGORY = {
    "type": "string",
    "description": "Category label or id. Optional — omit to leave the row unsorted.",
}
_MONTH = {"type": "string", "description": "Budget month, YYYY-MM."}


def _err(message: str) -> dict:
    return {"error": message}


def _svc() -> Any:
    from backend.services import ledger

    return ledger


# ── Ledger reads ─────────────────────────────────────────────────────────────


@op(
    name="accounts.list",
    tool_name="list_accounts",
    domain="accounts",
    action="list",
    tier="core",
    summary="List every account with its current balance, currency and kind.",
    properties={
        "include_archived": {"type": "boolean", "description": "Include archived accounts."}
    },
)
def _accounts_list(params: dict) -> dict:
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services.ledger import account_with_balance, opening_index

    rows = AccountStore().query_accounts(include_archived=bool(params.get("include_archived")))
    txns = TransactionStore().all()
    idx = opening_index(txns, rows)
    return {
        "accounts": [
            {
                "id": r["id"],
                "name": r.get("name"),
                "kind": r.get("kind"),
                "type": r.get("type"),
                "currency": r.get("currency"),
                "balance": account_with_balance(r, txns, idx=idx).get("balance"),
            }
            for r in rows
        ]
    }


@op(
    name="categories.list",
    tool_name="list_categories",
    domain="categories",
    action="list",
    tier="core",
    summary="List available transaction categories with their kind.",
    properties={},
)
def _categories_list(params: dict) -> dict:
    from backend.services.categories import get_all_categories

    return {
        "categories": [
            {"id": c["id"], "label": c.get("label"), "kind": c.get("kind")}
            for c in get_all_categories()
            if c["id"] != "split"
        ]
    }


@op(
    name="txn.list",
    tool_name="get_transactions",
    domain="txns",
    action="list",
    tier="core",
    summary="Search transactions by date range, account, category, kind or free text.",
    properties={
        "date_from": _DATE,
        "date_to": _DATE,
        "account_id": _ACCOUNT_ID,
        "category": _CATEGORY,
        "kind": {
            "type": "string",
            "enum": ["expense", "income", "transfer", "adjustment", "investment"],
        },
        "q": {"type": "string", "description": "Free-text search over the description."},
        "limit": {"type": "integer", "description": "Max rows to return (default 50)."},
    },
)
def _txn_list(params: dict) -> dict:
    result = _svc().get_transactions(
        date_from=params.get("date_from"),
        date_to=params.get("date_to"),
        account_id=params.get("account_id"),
        category=params.get("category"),
        kind=params.get("kind"),
        q=params.get("q"),
        limit=int(params.get("limit") or 50),
    )
    rows = result.get("transactions", [])
    return {
        "transactions": [
            {
                "id": t.get("id"),
                "date": t.get("date"),
                "kind": t.get("kind"),
                "description": t.get("description"),
                "category": t.get("category"),
                "from_account_id": t.get("from_account_id"),
                "from_amount": t.get("from_amount"),
                "to_account_id": t.get("to_account_id"),
                "to_amount": t.get("to_amount"),
                "base_amount": t.get("base_amount"),
            }
            for t in rows
        ],
        "count": len(rows),
    }


@op(
    name="networth.get",
    tool_name="get_net_worth",
    domain="reports",
    action="net_worth",
    tier="core",
    summary="Current net worth in base currency, with the asset/liability/market breakdown.",
    properties={},
)
def _networth_get(params: dict) -> dict:
    return _svc().net_worth()


@op(
    name="budget.month",
    tool_name="get_budget_month",
    domain="budget",
    action="month",
    tier="core",
    summary="Budget for a month: every envelope's assigned, activity and available, plus ready-to-assign.",
    properties={"month": _MONTH},
    required=["month"],
)
def _budget_month(params: dict) -> dict:
    from backend.services import planning

    month = params.get("month")
    if not month:
        return _err("month is required (YYYY-MM)")
    data = planning.budget_month(month)
    rows = []
    for group in data.get("groups", []):
        for row in group.get("categories", []):
            rows.append(
                {
                    "envelope_id": row.get("envelope_id") or row.get("key"),
                    "label": row.get("label"),
                    "assigned": row.get("assigned"),
                    "activity": row.get("activity"),
                    "available": row.get("available"),
                    "group": group.get("name"),
                }
            )
    return {
        "month": data.get("month"),
        "ready_to_assign": data.get("ready_to_assign"),
        "base_currency": data.get("base_currency"),
        "envelopes": rows,
        "totals": data.get("totals"),
    }


@op(
    name="positions.list",
    tool_name="get_portfolio",
    domain="invest",
    action="positions",
    tier="core",
    summary="List investment positions with quantity, cost basis and current market value.",
    properties={},
)
def _positions_list(params: dict) -> dict:
    from backend.services import positions as positions_service

    rows = positions_service.hydrated()
    out = []
    for r in rows:
        quantity = r.get("quantity") or 0.0
        price = r.get("price") or 0.0
        out.append(
            {
                "position_id": r.get("position_id"),
                "symbol": r.get("symbol"),
                "name": r.get("label"),
                "account_id": r.get("account_id"),
                "account_name": r.get("account_name"),
                "quantity": quantity,
                # NOTE: the position store holds cost_basis PER UNIT, not total.
                "unit_cost": r.get("cost_basis"),
                "price": price,
                "currency": r.get("currency"),
                "market_value": round(quantity * price, 2),
            }
        )
    return {"positions": out}


@op(
    name="lots.realized_pnl",
    tool_name="get_realized_pnl",
    domain="invest",
    action="realized_pnl",
    tier="rare",
    summary="Realized capital gains/losses from disposals over a date range, in base currency.",
    properties={
        "date_from": _DATE,
        "date_to": _DATE,
        "instrument_id": {"type": "string", "description": "Restrict to one instrument."},
    },
)
def _realized_pnl(params: dict) -> dict:
    from backend.services import lots as lots_service

    result = lots_service.realized_pnl(
        date_from=params.get("date_from"),
        date_to=params.get("date_to"),
        instrument_id=params.get("instrument_id"),
    )
    return {
        "realized_total_base": result.get("realized_total_base"),
        "event_count": len(result.get("events", [])),
        "by_term": result.get("by_term"),
    }


@op(
    name="lots.list",
    tool_name="get_lots",
    domain="invest",
    action="lots",
    tier="rare",
    summary="List open tax lots for a position, with acquisition date, quantity and unit cost.",
    properties={"position_id": {"type": "string", "description": "Position id."}},
    required=["position_id"],
)
def _lots_list(params: dict) -> dict:
    from backend.db.lot_store import LotStore

    position_id = params.get("position_id")
    rows = [
        lot
        for lot in LotStore().all()
        if lot.get("position_id") == position_id and not lot.get("deleted")
    ]
    return {
        "lots": [
            {
                "lot_id": lot.get("id"),
                "acquired_at": lot.get("acquired_at"),
                "original_qty": lot.get("original_qty"),
                "remaining_qty": lot.get("remaining_qty"),
                "unit_cost": lot.get("unit_cost"),
                "cost_currency": lot.get("cost_currency"),
                "kind": lot.get("kind"),
            }
            for lot in rows
        ]
    }


# ── Ledger writes ────────────────────────────────────────────────────────────

_TXN_PROPS = {
    "date": _DATE,
    "kind": {
        "type": "string",
        "enum": ["expense", "income", "transfer", "adjustment", "investment"],
        "description": "Defaults to expense. A transfer REQUIRES both from_account_id and to_account_id.",
    },
    "description": {"type": "string", "description": "The bank-origin / matching string."},
    "memo": {"type": "string", "description": "The user's personal annotation. Display-only."},
    "category": _CATEGORY,
    "from_account_id": _ACCOUNT_ID,
    "from_amount": {
        "type": "number",
        "description": "Positive magnitude leaving the from account.",
    },
    "from_currency": {"type": "string", "description": "ISO currency of the from leg."},
    "to_account_id": _ACCOUNT_ID,
    "to_amount": {
        "type": "number",
        "description": "Positive magnitude arriving in the to account.",
    },
    "to_currency": {"type": "string", "description": "ISO currency of the to leg."},
    "original_amount": {
        "type": "number",
        "description": "Pre-conversion foreign charge amount (display/audit only).",
    },
    "original_currency": {"type": "string", "description": "Currency of original_amount."},
    "external_refs": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": 'Provenance namespace (e.g. "email").',
                },
                "ref": {"type": "string", "description": "Raw per-event id under source."},
            },
            "required": ["source", "ref"],
        },
        "description": "Provenance/idempotency {source, ref} pairs. A repeat write carrying an already-present pair is a safe no-op (dedup).",
    },
    "tags": {"type": "array", "items": {"type": "string"}},
}


def _create_txn(data: dict) -> dict:
    from backend.core.errors import ValidationError

    try:
        row = _svc().create_transaction(dict(data))
    except ValidationError as exc:
        return _err(f"validation: {exc}")
    except ValueError as exc:
        return _err(f"invalid: {exc}")
    return {
        "ok": True,
        "id": row.get("id"),
        "date": row.get("date"),
        "kind": row.get("kind"),
        "category": row.get("category"),
        "base_amount": row.get("base_amount"),
    }


@op(
    name="txn.add",
    tool_name="add_transaction",
    domain="txns",
    action="add",
    tier="core",
    summary="Record ONE transaction (expense, income, transfer, investment or adjustment).",
    properties=dict(_TXN_PROPS),
    required=["date"],
    writes=True,
)
def _txn_add(params: dict) -> dict:
    return _create_txn(params)


@op(
    name="txn.add_many",
    tool_name="add_transactions",
    domain="txns",
    action="add_many",
    tier="core",
    summary="Record MANY transactions in one call — the statement-import path. Set dedup_by_external_ref to skip rows already booked.",
    properties={
        "transactions": {
            "type": "array",
            "description": "The rows to book. Each item takes the same fields as add_transaction.",
            "items": {"type": "object", "properties": dict(_TXN_PROPS)},
        },
        "dedup_by_external_ref": {
            "type": "boolean",
            "description": "Skip any row any of whose external_refs pairs is already in the ledger.",
        },
        "allow_duplicate": {
            "type": "boolean",
            "description": (
                "Book every row even if it looks like a transaction already in the ledger. "
                "Only after the user has confirmed the rows are intentional."
            ),
        },
    },
    required=["transactions"],
    writes=True,
)
def _txn_add_many(params: dict) -> dict:
    from backend.core.errors import ValidationError

    rows = params.get("transactions") or []
    if not isinstance(rows, list):
        return _err("transactions must be an array")
    try:
        created, skipped = _svc().bulk_create_transactions(
            [dict(r) for r in rows],
            dedup_by_external_ref=bool(params.get("dedup_by_external_ref")),
            allow_duplicate=bool(params.get("allow_duplicate")),
        )
    except ValidationError as exc:
        return _err(f"validation: {exc}")
    except ValueError as exc:
        return _err(f"invalid: {exc}")
    return {
        "ok": True,
        "created": len(created),
        "skipped": len(skipped),
        "ids": [r.get("id") for r in created],
    }


@op(
    name="txn.update",
    tool_name="update_transaction",
    domain="txns",
    action="update",
    tier="core",
    summary="Change fields on an existing transaction.",
    properties={
        "id": {"type": "string", "description": "Transaction id."},
        "changes": {
            "type": "object",
            "description": "Fields to change — same field names as add_transaction.",
            "properties": dict(_TXN_PROPS),
        },
    },
    required=["id", "changes"],
    writes=True,
)
def _txn_update(params: dict) -> dict:
    from backend.core.errors import ConflictError, ValidationError

    try:
        row = _svc().update_transaction(params.get("id"), dict(params.get("changes") or {}))
    except ConflictError as exc:
        return _err(f"refused: {exc}")
    except ValidationError as exc:
        return _err(f"validation: {exc}")
    if row is None:
        return _err("no such transaction")
    return {"ok": True, "id": row.get("id")}


@op(
    name="txn.delete",
    tool_name="delete_transaction",
    domain="txns",
    action="delete",
    tier="rare",
    summary="Soft-delete a transaction by id.",
    properties={"id": {"type": "string", "description": "Transaction id."}},
    required=["id"],
    writes=True,
)
def _txn_delete(params: dict) -> dict:
    from backend.db.transaction_store import TransactionStore

    txn_id = params.get("id")
    store = TransactionStore()
    if store.find_by_id(txn_id) is None:
        return _err("no such transaction")
    store.update_by_id(txn_id, {"deleted": True})
    return {"ok": True, "id": txn_id}


# ── Budget writes ────────────────────────────────────────────────────────────


@op(
    name="budget.assign",
    tool_name="assign_budget",
    domain="budget",
    action="assign",
    tier="core",
    summary="Set the amount assigned to one envelope for a month (absolute, not a delta).",
    properties={
        "month": _MONTH,
        "envelope_id": {"type": "string", "description": "Envelope id (from get_budget_month)."},
        "amount": {"type": "number", "description": "Absolute amount to assign. Must be >= 0."},
    },
    required=["month", "envelope_id", "amount"],
    writes=True,
)
def _budget_assign(params: dict) -> dict:
    return _budget_change(
        {
            "type": "assign",
            "month": params.get("month"),
            "envelope_id": params.get("envelope_id"),
            "amount": params.get("amount"),
        }
    )


@op(
    name="budget.move_money",
    tool_name="move_money",
    domain="budget",
    action="move_money",
    tier="core",
    summary="Move assigned money between two envelopes in a month. Use '__rta' on one end for ready-to-assign.",
    properties={
        "month": _MONTH,
        "from_envelope_id": {"type": "string", "description": "Source envelope id, or '__rta'."},
        "to_envelope_id": {"type": "string", "description": "Destination envelope id, or '__rta'."},
        "amount": {"type": "number", "description": "Amount to move. Must be > 0."},
    },
    required=["month", "from_envelope_id", "to_envelope_id", "amount"],
    writes=True,
)
def _budget_move(params: dict) -> dict:
    return _budget_change(
        {
            "type": "move_money",
            "month": params.get("month"),
            "from_envelope_id": params.get("from_envelope_id"),
            "to_envelope_id": params.get("to_envelope_id"),
            "amount": params.get("amount"),
        }
    )


@op(
    name="budget.set_target",
    tool_name="set_envelope_target",
    domain="budget",
    action="set_target",
    tier="rare",
    summary="Set a funding target on an envelope (monthly, weekly, yearly or by_date).",
    properties={
        "envelope_id": {"type": "string", "description": "Envelope id."},
        "target_type": {"type": "string", "enum": ["monthly", "weekly", "yearly", "by_date"]},
        "amount": {"type": "number", "description": "Target amount. Must be > 0."},
        "date": {"type": "string", "description": "Required for by_date targets (YYYY-MM-DD)."},
    },
    required=["envelope_id", "target_type", "amount"],
    writes=True,
)
def _budget_target(params: dict) -> dict:
    change = {
        "type": "set_target",
        "envelope_id": params.get("envelope_id"),
        "target_type": params.get("target_type"),
        "amount": params.get("amount"),
    }
    if params.get("date"):
        change["date"] = params["date"]
    return _budget_change(change)


@op(
    name="budget.create_envelope",
    tool_name="create_envelope",
    domain="budget",
    action="create_envelope",
    tier="rare",
    summary="Create a new (initially empty) budget envelope.",
    properties={
        "name": {"type": "string", "description": "Envelope name."},
        "group_id": {"type": "string", "description": "Optional group to place it in."},
    },
    required=["name"],
    writes=True,
)
def _budget_create_envelope(params: dict) -> dict:
    return _budget_change(
        {"type": "envelope_create", "name": params.get("name"), "group_id": params.get("group_id")}
    )


def _budget_change(change: dict) -> dict:
    from backend.core.errors import ConflictError, NotFoundError, ValidationError
    from backend.services import planning

    try:
        return planning.apply_budget_change({k: v for k, v in change.items() if v is not None})
    except ValidationError as exc:
        return _err(f"validation: {exc}")
    except NotFoundError as exc:
        return _err(f"not found: {exc}")
    except ConflictError as exc:
        return _err(f"refused: {exc}")


# ── Investment writes ────────────────────────────────────────────────────────


@op(
    name="lots.acquire",
    tool_name="record_lot_acquire",
    domain="invest",
    action="acquire",
    tier="rare",
    summary="Record an acquired tax lot on a position (a buy). Does NOT book the cash leg — add that separately.",
    properties={
        "position_id": {"type": "string", "description": "Position id."},
        "quantity": {"type": "number", "description": "Units acquired."},
        "unit_cost": {"type": "number", "description": "Cost per unit in cost_currency."},
        "cost_currency": {"type": "string", "description": "Currency of unit_cost."},
        "acquired_at": _DATE,
        "fees": {"type": "number", "description": "Transaction fees."},
    },
    required=["position_id", "quantity", "unit_cost"],
    writes=True,
)
def _lot_acquire(params: dict) -> dict:
    return _lot_change(
        {
            "type": "acquire",
            "position_id": params.get("position_id"),
            "quantity": params.get("quantity"),
            "unit_cost": params.get("unit_cost"),
            "cost_currency": params.get("cost_currency"),
            "acquired_at": params.get("acquired_at"),
            "fees": params.get("fees"),
            "kind": "buy",
        }
    )


@op(
    name="lots.dispose",
    tool_name="record_lot_dispose",
    domain="invest",
    action="dispose",
    tier="rare",
    summary="Record a sale against a position's tax lots (FIFO by default) and compute the realized gain.",
    properties={
        "position_id": {"type": "string", "description": "Position id."},
        "quantity": {"type": "number", "description": "Units sold."},
        "proceeds_per_unit": {"type": "number", "description": "Sale price per unit."},
        "proceeds_currency": {"type": "string", "description": "Currency of the proceeds."},
        "disposed_at": _DATE,
        "method": {
            "type": "string",
            "enum": ["fifo", "specific_id"],
            "description": "Lot selection method.",
        },
        "disposal_fees": {"type": "number", "description": "Selling fees."},
        "proceeds_account_id": {
            "type": "string",
            "description": "Optional cash account to book the sale proceeds into.",
        },
    },
    required=["position_id", "quantity", "proceeds_per_unit"],
    writes=True,
)
def _lot_dispose(params: dict) -> dict:
    return _lot_change(
        {
            "type": "dispose",
            "position_id": params.get("position_id"),
            "quantity": params.get("quantity"),
            "proceeds_per_unit": params.get("proceeds_per_unit"),
            "proceeds_currency": params.get("proceeds_currency"),
            "disposed_at": params.get("disposed_at"),
            "method": params.get("method") or "fifo",
            "disposal_fees": params.get("disposal_fees"),
            "proceeds_account_id": params.get("proceeds_account_id"),
        }
    )


def _lot_change(change: dict) -> dict:
    from backend.core.errors import ValidationError
    from backend.services import lots as lots_service

    try:
        result = lots_service.apply_lot_change({k: v for k, v in change.items() if v is not None})
    except ValidationError as exc:
        return _err(f"validation: {exc}")
    return result
