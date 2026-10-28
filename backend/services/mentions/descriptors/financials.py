"""Descriptors for service-hydrated types (class (b) per RFC §4.2).

holding, account, instrument, transaction — these are not backed by a simple
JsonStore.all() call; they require service hydration (balance computation,
FX recompute, positions join).  Each uses search_override (the existing
search_* function imported verbatim) and resolve_override (the existing
_resolve_* function imported verbatim) for byte-identical parity.

The ``rows`` + ``find_one`` fields are populated for the generic generic
engine's use (e.g. future _find_by_id fallback or EntityList wiring), but the
search/resolve paths go through the override callables.

RFC 2026-06-25-lucius-generic-entity-registry §4.2 class (b), §4.3.
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.providers import (
    search_account,
    search_holding,
    search_instrument,
    search_transaction,
)
from backend.services.mentions.resolve import (
    _resolve_account,
    _resolve_holding,
    _resolve_instrument,
    _resolve_transaction,
)
from backend.services.mentions.types import SearchQuery

# ── holding (order=1) ─────────────────────────────────────────────────────────
# positions_service.hydrated() is the source; hydrated_one is the find-one path.
# Both search and resolve use override callables for byte-identical parity.
# _resolve_holding does FX recompute + brief/analyst splice; project_fn is not
# used here because _resolve_holding(ref) takes a string ref, not a row dict.


def _holding_rows(query: SearchQuery) -> list[dict]:
    from backend.services import positions as positions_service

    return positions_service.hydrated()


def _holding_find_one(ref: str) -> dict | None:
    from backend.services import positions as positions_service

    return positions_service.hydrated_one(ref)


register(
    EntityDescriptor(
        type="holding",
        label="Holding",
        icon="◆",
        order=1,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_holding_rows,
        find_one=_holding_find_one,
        search_override=search_holding,
        resolve_override=_resolve_holding,
        fields={
            "symbol": FieldSpec("Symbol"),
            "name": FieldSpec("Name"),
            "account": FieldSpec("Account"),
            "asset_class": FieldSpec("Asset class"),
            "quantity": FieldSpec("Quantity", "qty"),
            "currency": FieldSpec("Currency"),
            "price": FieldSpec("Price", "money"),
            "value_base": FieldSpec("Value", "money"),
            "cost_base": FieldSpec("Cost basis", "money"),
            "unrealized_base": FieldSpec("Unrealized P&L", "money"),
            "day_change_base": FieldSpec("Day change", "money"),
        },
    )
)

# ── instrument (order=2) ──────────────────────────────────────────────────────
# InstrumentStore.all() + held-exclusion filter (handled by search_instrument).
# _resolve_instrument does latest price lookup + brief/analyst splice.


def _instrument_find_one(ref: str) -> dict | None:
    from backend.db.instrument_store import InstrumentStore

    return InstrumentStore().find_by_id(ref)


register(
    EntityDescriptor(
        type="instrument",
        label="Security",
        icon="◈",
        order=2,
        tone="invest",
        surfaces=frozenset({"entity"}),
        find_one=_instrument_find_one,
        search_override=search_instrument,
        resolve_override=_resolve_instrument,
        fields={
            "symbol": FieldSpec("Symbol"),
            "name": FieldSpec("Name"),
            "asset_class": FieldSpec("Asset class"),
            "asset_subclass": FieldSpec("Asset subclass"),
            "sector": FieldSpec("Sector"),
            "quote_currency": FieldSpec("Quote currency"),
            "latest_price": FieldSpec("Latest price", "money"),
            "dividend_rate_annual": FieldSpec("Annual dividend rate", "pct"),
        },
    )
)

# ── account (order=3) ─────────────────────────────────────────────────────────
# AccountStore + balance hydration from ledger.
# _resolve_account does ledger.account_with_balance + base_currency recompute.


def _account_find_one(ref: str) -> dict | None:
    from backend.db.account_store import AccountStore
    from backend.services import ledger

    acct = AccountStore().find_by_id(ref)
    if not acct:
        return None
    return ledger.account_with_balance(acct)


register(
    EntityDescriptor(
        type="account",
        label="Account",
        icon="⬡",
        order=3,
        tone="cash",
        surfaces=frozenset({"entity"}),
        find_one=_account_find_one,
        search_override=search_account,
        resolve_override=_resolve_account,
        fields={
            "name": FieldSpec("Name"),
            "kind": FieldSpec("Kind"),
            "type": FieldSpec("Type"),
            "currency": FieldSpec("Currency"),
            "balance": FieldSpec("Balance", "money"),
            "balance_base": FieldSpec("Balance (base)", "money"),
            "cleared_balance_base": FieldSpec("Cleared balance", "money"),
            "custodian": FieldSpec("Custodian"),
        },
    )
)

# ── transaction (order=5) ─────────────────────────────────────────────────────
# TransactionStore.query_txns(q=...) — query-aware rows for server-side pushdown.
# _resolve_transaction does txn_base_amount recompute + FK→name resolution
# (payee_id/account_id → names).


def _transaction_rows(query: SearchQuery) -> list[dict]:
    from backend.services import ledger

    q = (query.q or "").strip() or None
    return ledger.transactions.query_txns(q=q, include_deleted=False)


def _transaction_find_one(ref: str) -> dict | None:
    """Read through the LEDGER's own store handle (``services.ledger.transactions``), not a
    fresh ``TransactionStore()``.

    The write-receipt chip resolves a row that the same turn just wrote, so a second handle
    would describe a different file than the one written — and ``include_deleted=False``
    would additionally hide exactly the row a DELETE receipt has to name. A soft-deleted row
    is returned here on purpose; the search path is unaffected (it uses search_override)."""
    from backend.services import ledger

    return ledger.transactions.find_by_id(ref)


register(
    EntityDescriptor(
        type="transaction",
        label="Transaction",
        icon="↕",
        order=5,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_transaction_rows,
        find_one=_transaction_find_one,
        search_override=search_transaction,
        resolve_override=_resolve_transaction,
        fields={
            "date": FieldSpec("Date", "date"),
            "kind": FieldSpec("Kind"),
            "category": FieldSpec("Category"),
            "amount_base": FieldSpec("Amount (base)", "money"),
            # Native leg amount/currency — the amount AS STATED IN THE SOURCE, distinct
            # from the base-currency-converted amount_base above. Without an explicit
            # currency unit attached, a foreign-currency transaction's mention exposed
            # only a bare converted number that a model then mislabeled as the base
            # currency (see the note in mentions/resolve.py::_resolve_transaction).
            "amount": FieldSpec("Amount", "money"),
            "currency": FieldSpec("Currency"),
            # The pre-conversion foreign charge, when the bank converted at the point of sale.
            # Without it a VND ride on an SGD card reads as an SGD-only row and the real figure
            # survives only in free text (owner report 2026-09-20). Display/audit only.
            "original_amount": FieldSpec("Original charge", "money"),
            "original_currency": FieldSpec("Original currency"),
            "from_amount": FieldSpec("From amount", "money"),
            "from_currency": FieldSpec("From currency"),
            "to_amount": FieldSpec("To amount", "money"),
            "to_currency": FieldSpec("To currency"),
            # Two fields, under their real names. `note` was retired by the 2026-07-17
            # note→description+memo split and the descriptor kept it, so a mention carried
            # the DESCRIPTION under a dead label and no memo at all — see the note in
            # mentions/resolve.py::_resolve_transaction for the incident that surfaced it.
            "description": FieldSpec("Description"),
            "memo": FieldSpec("Memo"),
            "payee": FieldSpec("Payee"),
            "account": FieldSpec("Account"),
            "from_account": FieldSpec("From account"),
            "to_account": FieldSpec("To account"),
            "status": FieldSpec("Status"),
        },
    )
)
