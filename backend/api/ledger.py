"""Financial ledger — accounts, two-leg transactions, budgets, net worth, summaries.

Backed by the account model (see .claude/rfcs/account-ledger.md):
  - Accounts (AccountStore) carry a native currency + an `opening_txn_id` pointer;
    the opening amount, the tracking start and the balance are all computed (never
    stored) by ledger_service.
  - Transactions (TransactionStore) are two-leg: expense / income / transfer / adjustment.
  - Net worth = add-everything fold over account balances + data.csv market values,
    normalised to profile.base_currency.

This module stays thin: all cross-store logic lives in backend/services/ledger.py.
"""

from __future__ import annotations

import logging
from typing import Annotated, List, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from backend.core import audit_context
from backend.core.errors import (
    DuplicateTransactionError,
    OpenNetWorthError,
    PreviouslyDeletedError,
    to_http_status,
)
from backend.core.txn_sources import display_source, ext_ref_pairs
from backend.core.user_time import user_today
from backend.models.transaction import TxnKind
from backend.services import audit as audit_svc
from backend.services import categories as cat_svc
from backend.services import connector_snapshots, email_snapshots, payee_registry, transaction_purge
from backend.services import institutions as institution_svc
from backend.services import ledger as svc
from backend.services.budgets import EXPENSE_CATEGORIES  # noqa: F401  (re-export for importers)
from backend.services.fx_cache import load_fx_cache
from backend.services.labels import label_txns


async def _ui_audit_scope() -> None:
    """Router-level dependency: every direct REST call to a ledger route is a human acting
    through the app UI. Stamp actor=user / surface=ui so transaction writes on this path are
    attributed correctly (transaction-provenance RFC). Request-isolated — the ContextVar set
    here lives only for this request's task context. AI-tool-driven writes hit the chat router
    instead (assistant.run sets chat:web), so this never clobbers them.

    MUST be ``async`` — a sync dependency runs in its own threadpool-copied context, so a
    ContextVar set there is discarded before the (also threadpool-run) endpoint sees it. An
    async dependency runs in the request's task context, which the endpoint's copy inherits."""
    audit_context.set_audit_scope("user", "ui")


router = APIRouter(dependencies=[Depends(_ui_audit_scope)])

ALL_KINDS = {"expense", "income", "transfer", "adjustment", "investment"}


def category_to_kind() -> dict[str, str]:
    """Every category id → its kind, read LIVE from the store (category-opaque-ids migration,
    decision #3 — the old ``CATEGORY_TO_KIND`` module-level dict hardcoded a handful of
    built-in slugs at import time; with per-install opaque ids that would be permanently
    wrong/empty, so this is now a function, not a constant). ``_kind_for``/``get_category_kind``
    don't use this — they delegate straight to ``cat_svc.kind_for()`` for a single lookup."""
    return {
        c["id"]: c.get("kind", "expense") for c in cat_svc.get_all_categories(include_hidden=True)
    }


def _kind_for(category: str | None) -> str:
    """Return the transaction kind for a category (store-backed via services.categories)."""
    return cat_svc.kind_for(category)


def get_all_categories(include_hidden: bool = False) -> list[dict]:
    """All categories visible to the user (store-backed, P1 read-flip).

    Delegates to ``services.categories.get_all_categories`` which reads from the
    CategoryStore and applies live profile filters (deleted/hidden/color overrides)
    on top for P1 transition safety.
    """
    return cat_svc.get_all_categories(include_hidden=include_hidden)


def get_category_kind(category_id: str) -> str:
    """Return the transaction kind for a category category_id (store-backed, falls back to 'expense')."""
    return cat_svc.kind_for(category_id)


# ── Thin pass-throughs kept for existing importers ──────────────────────────────


def read_transactions() -> list:
    return svc.transactions.all()


def write_transactions(data: list) -> None:
    svc.transactions.replace_all(data)


# ── Pydantic models ──────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    name: str
    type: str = "cash"
    institution: str = ""
    currency: Optional[str] = (
        None  # None → falls back to the user's base_currency() in _make_account
    )
    initial_balance: float = Field(allow_inf_nan=False)
    initial_balance_date: Optional[str] = None
    credit_limit: Optional[float] = None
    last_four: Optional[str] = None
    note: str = ""
    # Accounts redesign (additive metadata; never feeds balance/net-worth math).
    # `connected` is intentionally ABSENT — derived from `external_ids`, never caller-writable
    # (RFC connectors-as-data-sources §D).
    external_ids: list[dict] = []
    apr: Optional[float] = None
    principal: Optional[float] = None
    payoff_date: Optional[str] = None
    min_payment: Optional[float] = None
    apy: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_color(cls, value):
        if isinstance(value, dict):
            if "color" in value:
                raise ValueError("Account colour has been removed and cannot be set.")
            if "platform" in value:
                raise ValueError("Account platform has been renamed to institution.")
        return value


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    institution: Optional[str] = None
    initial_balance: Optional[float] = Field(default=None, allow_inf_nan=False)
    initial_balance_date: Optional[str] = None
    credit_limit: Optional[float] = None
    last_four: Optional[str] = None
    note: Optional[str] = None
    archived: Optional[bool] = None
    # Credit card statement cycle config (credit only)
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    payment_due_date: Optional[str] = None
    # Accounts redesign (additive metadata). `connected` is intentionally ABSENT — derived,
    # never caller-writable (RFC connectors-as-data-sources §D). `external_ids`, when given,
    # REPLACES the whole stored list (link/unlink both go through this one field).
    external_ids: Optional[list[dict]] = None
    apr: Optional[float] = None
    principal: Optional[float] = None
    payoff_date: Optional[str] = None
    min_payment: Optional[float] = None
    apy: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_type_change(cls, value):
        if isinstance(value, dict) and "type" in value:
            raise ValueError(
                "Account type is set when you create the account and cannot be changed. "
                "Create a new account if it needs a different type."
            )
        if isinstance(value, dict) and "currency" in value:
            raise ValueError(
                "Account currency is set when you create the account and cannot be changed. "
                "Create a new account if it needs a different currency."
            )
        if isinstance(value, dict) and "platform" in value:
            raise ValueError("Account platform has been renamed to institution.")
        if isinstance(value, dict) and "color" in value:
            raise ValueError("Account colour has been removed and cannot be set.")
        return value


class InstitutionRename(BaseModel):
    name: str


class ReconcileBody(BaseModel):
    real_balance: float  # asserted native-currency balance
    date: Optional[str] = None
    against: str = "cleared"  # "cleared" (YNAB reconcile-to-cleared) | "working" (legacy true-up)


class DescribeAccountBody(BaseModel):
    text: str  # free-text description of one account → an editable form draft (never creates)


class ClearedBody(BaseModel):
    cleared: bool  # the new reconciliation state for the transaction


class DetachRefBody(BaseModel):
    # Caps (security review L4) — FastAPI validates a Pydantic body field for real (unlike an
    # AI-tool schema's advisory max_length), so these are actual enforcement, not just a hint.
    source: Optional[str] = Field(default=None, max_length=64)
    ref: str = Field(max_length=512)


class TransactionCreate(BaseModel):
    # Control flag, NOT a stored field — `add_transaction` pops it before the choke sees the
    # payload. The pre-write duplicate guard (ADR 2026-08-01) refuses a row matching an
    # existing one; the AI tool has carried this escape hatch since day one, and without it a
    # REST/UI caller is refused with no way to say "yes, book it anyway".
    force: bool = False
    resurrect: bool = False
    date: str
    kind: str = "expense"  # expense | income | transfer | adjustment
    category: Optional[str] = None
    # Explicit payee entity link (P4-PAYEE) — WINS over description-text auto-resolution at
    # the create choke point (backend.services.ledger.create_transaction). None/omitted
    # leaves the existing auto-resolve-from-description behavior unchanged (PAYEEPOST1).
    payee_id: Optional[str] = None
    # A clean payee NAME (no id yet) — the choke resolves/creates the entity and drops this
    # transient field. Lets an importer keep the RAW descriptor in `description` (cross-source
    # dedup) while attaching a clean merchant. Ignored when payee_id is given. Never stored
    # as a column.
    payee: Optional[str] = None
    # Provenance split (ADR 2026-07-17): `description` = bank/matching string, `memo` =
    # personal display-only text.
    description: str = ""
    memo: str = ""
    from_account_id: Optional[str] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_account_id: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    # Original foreign charge before conversion (display/audit only). Explicit value wins over the
    # choke point's auto-capture; None lets the choke point auto-capture a converted foreign leg.
    original_amount: Optional[float] = None
    original_currency: Optional[str] = None
    ticker: Optional[str] = None
    quantity: Optional[float] = None
    tags: List[str] = []  # free-form labels (additive; rules engine + manual — P2-SP1)
    # Split allocation (P3-SP) — [{category, amount, note?}] in the leg's native currency. The
    # create_transaction choke point runs _validate_splits (Σ == leg amount, valid categories,
    # kind gate). None/omitted = unsplit (the common case).
    splits: Optional[List[dict]] = None
    source: str = "manual"
    # Provenance/dedup pairs — ONE list of {source, ref}, [0] = the creation-time origin
    # (ADR 2026-07-21). Each pair is an L0 idempotency key at the create choke.
    external_refs: List[dict] = []
    # Imported/AI rows land flagged for review (Accounts redesign). None = source-derived default.
    needs_review: Optional[bool] = None
    # Foreign-currency base value (ADR 2026-07-09): the base-currency (e.g. SGD) amount the user
    # actually transacted at. Stamped once and preserved on later edits; sending it here sets it
    # explicitly instead of auto-converting at the app's FX cache.
    base_amount: Optional[float] = None


class TransactionUpdate(BaseModel):
    date: Optional[str] = None
    kind: Optional[TxnKind] = None
    category: Optional[str] = None
    payee_id: Optional[str] = None  # set/clear the payee entity link (P4-PAYEE)
    description: Optional[str] = None  # bank/matching string (ADR 2026-07-17)
    memo: Optional[str] = None  # personal, display-only
    from_account_id: Optional[str] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_account_id: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    original_amount: Optional[float] = None  # foreign charge before conversion (display/audit)
    original_currency: Optional[str] = None
    tags: Optional[List[str]] = None  # replace the txn's tags (additive field — P2-SP1)
    # Set/replace the split allocation (P3-SP). [] clears it back to a plain category; None =
    # leave splits untouched (the update PUT uses exclude_none, so an omitted field is preserved).
    splits: Optional[List[dict]] = None
    # Review state (Accounts redesign): drawer Approve clears it (False); reject = delete.
    needs_review: Optional[bool] = None
    # Reconciliation flag — the drawer's Edit saves it with the form. Absent here, pydantic
    # dropped it and the PUT answered 200 having changed nothing.
    cleared: Optional[bool] = None
    # Foreign-currency base value (ADR 2026-07-09): the base-currency (e.g. SGD) amount the user
    # actually transacted at. Preserved on edits that don't touch the native leg; sending it here
    # is the explicit "Value in SGD" override.
    base_amount: Optional[float] = None
    # Append-only provenance patch. Existing pairs are preserved; duplicates are ignored.
    external_refs: Optional[List[dict]] = None


class BulkTransactionImport(BaseModel):
    transactions: List[TransactionCreate]
    # Caller-supplied source override (must be in KNOWN_SOURCES to be honored; otherwise
    # defaults to 'api').  Stops the endpoint force-stamping 'csv_import' on all rows.
    source: Optional[str] = None
    # Book every row even when the pre-write duplicate guard (ADR 2026-08-01) matches it
    # against an existing one. Same escape hatch the AI tool and the single-row POST carry;
    # without it a REST caller can be refused with no way to proceed.
    force: bool = False


class BulkTxnChanges(BaseModel):
    """The multi-edit applied to every selected row (all optional; at least one must be
    supplied)."""

    category: Optional[str] = None  # SET the category (validated; skipped on adjustment only)
    tags: Optional[List[str]] = None  # ADD these tags (additive union — never clobbers)
    cleared: Optional[bool] = None  # SET the cleared flag
    payee_id: Optional[str] = (
        None  # LINK the payee entity (validated; skipped on transfer/adjustment)
    )
    needs_review: Optional[bool] = None  # SET review flag (False = Approve; reject = bulk-delete)
    to_account_id: Optional[str] = None  # MOVE to this account (re-point the account-bearing leg)
    description: Optional[str] = None  # SET the bank/matching description on every selected row
    memo: Optional[str] = None  # SET the personal memo on every selected row


class BulkTxnUpdate(BaseModel):
    ids: List[str]
    changes: BulkTxnChanges


class BulkTxnUpdateFlat(BaseModel):
    """Flat bulk-update body for ``POST /api/transactions/bulk-update`` (CONTRACT.md): the
    change fields ride alongside ``ids`` instead of nested under ``changes``."""

    ids: List[str]
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    cleared: Optional[bool] = None
    payee_id: Optional[str] = None
    needs_review: Optional[bool] = None
    to_account_id: Optional[str] = None
    description: Optional[str] = None
    memo: Optional[str] = None


class BulkTxnDelete(BaseModel):
    ids: List[str]


class CategoryRename(BaseModel):
    new_label: Optional[str] = None
    merge_into: Optional[str] = None  # fold this category into an existing, different one


# ── Accounts ───────────────────────────────────────────────────────────────────


@router.get("/api/categories")
def list_categories_endpoint(include_hidden: bool = False):
    """List all categories. Hidden categories are excluded by default; pass
    ``include_hidden=true`` to include them (e.g. the category manager UI)."""
    return get_all_categories(include_hidden=include_hidden)


@router.post("/api/categories/{category_id}/hide")
def hide_category_endpoint(category_id: str):
    """Hide a category from default pickable / budget views. Data is untouched.
    The structural ``split`` category_id → 422."""
    try:
        return cat_svc.set_category_hidden(category_id, hidden=True)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/categories/{category_id}/unhide")
def unhide_category_endpoint(category_id: str):
    """Restore a previously-hidden category to default views."""
    try:
        return cat_svc.set_category_hidden(category_id, hidden=False)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/categories/{category_id}/color")
def set_category_color_endpoint(category_id: str, body: dict):
    """Persist a category's swatch colour (custom entry, or a built-in override).
    Body: ``{"color": "#3b86dd"}``. Non-hex → 422; unknown category_id → 404."""
    try:
        return cat_svc.set_category_color(category_id, (body or {}).get("color") or "")
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/categories")
def add_category_endpoint(body: dict):
    """Create a custom category (thin over categories_service; checkpoint-covered). An opaque
    id is minted server-side — any caller-supplied ``category_id`` is ignored (no slug derivation)."""
    if "group" in body:
        raise HTTPException(
            400,
            "a category belongs to no group — groups hold envelopes; link the category "
            "to an envelope instead",
        )
    try:
        return cat_svc.create_category(
            label=body.get("label"),
            kind=body.get("kind", "expense"),
            color=body.get("color") or "",
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.patch("/api/categories/{category_id}")
def rename_category_endpoint(category_id: str, body: CategoryRename):
    """Relabel a category, or fold it into another (``merge_into``) — every reference (txns,
    the legacy per-category budget cap, group membership, rules, envelope mapping) moves
    atomically on a merge. The structural ``split`` category_id → 422."""
    try:
        return cat_svc.rename_category(
            category_id=category_id,
            new_label=body.new_label,
            merge_into=body.merge_into,
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.delete("/api/categories/{category_id}")
def remove_category_endpoint(
    category_id: str, reassign: bool = False, reassign_to: Optional[str] = None
):
    """Delete a category (custom OR a built-in SPENDING category). In-use (referenced by any
    transaction, top-level OR in a split line) → 409 unless ``reassign=true`` (those
    transactions move to ``reassign_to``, default ``None`` → unsorted). Scrubs
    budgets/targets/assignments/groups/rules. The structural ``split`` category_id → 422; unknown
    reassign target → 422; missing category_id → 404."""
    try:
        return cat_svc.delete_category(
            category_id=category_id, reassign=reassign, reassign_to=reassign_to
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/accounts")
def get_accounts(include_archived: bool = False):
    """All accounts with computed native balance + base-currency value."""
    import time as _time

    _perf_log = __import__("logging").getLogger(__name__)
    _t0 = _time.perf_counter()
    txns = svc.transactions.all()
    _t1 = _time.perf_counter()
    accts = svc.accounts.query_accounts(include_archived=include_archived)
    _t2 = _time.perf_counter()
    # ONE opening index for the whole list — otherwise every account rescans the txn list to
    # resolve its own Starting Balance row.
    idx = svc.opening_index(txns, accts)
    _t3 = _time.perf_counter()
    # account_with_balance adds balance/balance_base + cleared_balance/cleared_balance_base
    # (cleared/reconcile MVP) + the derived opening fields, over the shared txn list.
    result = [svc.account_with_balance(a, txns, idx=idx) for a in accts]
    _t4 = _time.perf_counter()
    _perf_log.info(
        "PERF get_accounts txns_all=%.1fms query_accounts=%.1fms opening_index=%.1fms "
        "balance_loop=%.1fms (n_accts=%d, n_txns=%d)",
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        (_t3 - _t2) * 1000,
        (_t4 - _t3) * 1000,
        len(accts),
        len(txns),
    )
    return result


@router.get("/api/institutions")
def get_institutions():
    """Saved institutions available to account creation and editing."""
    return {"institutions": institution_svc.list_institutions()}


@router.patch("/api/institutions/{institution_id}")
def rename_institution(institution_id: str, body: InstitutionRename):
    try:
        return institution_svc.rename_institution(institution_id, body.name)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), str(exc)) from exc


@router.delete("/api/institutions/{institution_id}")
def delete_institution(institution_id: str):
    try:
        return institution_svc.delete_institution(institution_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), str(exc)) from exc


@router.get("/api/accounts/tracking-health")
def get_tracking_health():
    """Per-account hidden-leg counts + book-level straddle/hidden totals — surfaces
    transactions summarized behind a tracking start so nothing hides silently.
    Registered before the dynamic ``/{account_id}`` route so the literal path wins."""
    from backend.services import tracking_start

    return tracking_start.tracking_health()


@router.get("/api/accounts/{account_id}")
def get_account(account_id: str):
    """One account with computed balances — the single-account refresh the client calls after
    a transaction write to update that account's sidebar balance (was missing → 404, so
    balances went stale after an edit)."""
    a = svc.accounts.find_by_id(account_id)
    if a is None:
        raise HTTPException(404, "Account not found")
    return svc.account_with_balance(a)


@router.post("/api/accounts/describe")
async def describe_account_endpoint(body: DescribeAccountBody):
    """Free-text → an editable account-form draft (Accounts redesign, handoff §04). NEVER
    creates the account — returns structured fields for the Add-account modal to pre-fill."""
    from backend.services import account_describe

    try:
        return await account_describe.describe_account(body.text)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/accounts")
def add_account(body: AccountCreate):
    # AccountCreate intentionally omits credit-card cycle fields. They are optional metadata
    # configured later in Edit account; saving them never creates an Automation implicitly.
    from backend.services import accounts as accounts_service

    # Account + Starting Balance row + pointer are ONE database transaction in the service,
    # never two store writes from a router.
    side_effects: dict = {}
    try:
        account = accounts_service.create_account(body.model_dump(), side_effects=side_effects)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    # Return the HYDRATED account (balance/cleared_balance + base, + the DERIVED opening
    # fields) so the client's optimistic state update has real numbers.
    body_out = svc.account_with_balance(account)
    if side_effects.get("unrelated_retired_payment_envelopes"):
        # A new credit account's own boot-time sync is a GLOBAL reconciliation pass — it can
        # incidentally retire a DIFFERENT, already-stale card's payment envelope in the same
        # pass (review 2026-09-10: this used to be dropped on the floor entirely). Never this
        # account's own envelope (it's brand new), so it rides under its own key rather than
        # the `retired_payment_envelopes` name used for a write's OWN destructive side effect.
        body_out["unrelated_retired_payment_envelopes"] = side_effects[
            "unrelated_retired_payment_envelopes"
        ]
    if side_effects.get("payment_envelope_sync_warning"):
        body_out["payment_envelope_sync_warning"] = side_effects["payment_envelope_sync_warning"]
    return body_out


@router.post("/api/accounts/{account_id}/align-tracking-start")
def align_tracking_start_route(account_id: str, body: dict):
    """Move an account's tracking start earlier and rewind its opening (Preserve) so
    the current balance is unchanged; previously-hidden legs now count."""
    from backend.services import tracking_start

    try:
        return tracking_start.align_tracking_start(
            account_id, body["new_date"], allow_summarized=bool(body.get("allow_summarized"))
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.put("/api/accounts/{account_id}")
def update_account(account_id: str, body: AccountUpdate):
    from backend.services import accounts as accounts_service

    existing = svc.accounts.find_by_id(account_id)
    if existing is None:
        raise HTTPException(404, "Account not found")

    changes = body.model_dump(exclude_none=True)

    # The opening is ONE fact on its own transaction: neither the amount nor the tracking
    # start is a stored account field, so both are popped out of the plain patch and routed
    # through set_account_opening — the only writer of either.
    opening_touched = "initial_balance" in changes or "initial_balance_date" in changes
    opening_amount_in = changes.pop("initial_balance", None)
    as_of_in = changes.pop("initial_balance_date", None)
    if opening_touched:
        # opening_for_edit, not opening_amount/opening_date: an account whose pointer dangles
        # must still be repairable through this endpoint, and the loud read would raise before
        # the write choke got the chance to re-mint.
        cur_amount, cur_date = svc.opening_for_edit(existing)
        amount = opening_amount_in if opening_amount_in is not None else cur_amount
        svc.set_account_opening(account_id, amount, as_of_in or cur_date)

    # Routed through accounts_service.update_account, not the bare store, so the external_ids
    # uniqueness guard and link-time fill apply on this REST path too.
    try:
        updated = (
            accounts_service.update_account(account_id, changes)
            if changes
            else svc.accounts.find_by_id(account_id)
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Account not found")

    # Return the HYDRATED account (balance/cleared_balance + base) so a client editing an
    # account doesn't replace its row with a balance-less one that renders as "NaN".
    body_out = svc.account_with_balance(updated)
    return body_out


@router.delete("/api/accounts/{account_id}")
def delete_account(account_id: str, force: bool = False):
    try:
        deleted = svc.delete_account(account_id, force=force)
    except OpenNetWorthError as exc:
        if exc.context.get("error_code") == "account_net_worth_linked":
            # A DIFFERENT structured shape from the transaction-conflict one below — on
            # purpose. This refusal has nothing `force=true` can fix (a linked loan/position/
            # lot/option), so it must never be mistaken for the other one: a client that
            # keyed off "any structured 409" and defaulted to offering a force-delete escalation
            # would show a "Force delete and remove your transactions" button that just 409s
            # again, burying the REAL reason (security review 2026-09-06, second pass finding
            # #2 — reproduced over the wire against an account blocked only by a linked
            # mortgage, zero transactions).
            raise HTTPException(
                to_http_status(exc),
                detail={
                    "error": "account_net_worth_linked",
                    "message": exc.message,
                    "linked_asset_ids": exc.context.get("linked_asset_ids", []),
                    "open_position_count": exc.context.get("open_position_count", 0),
                    "open_lot_count": exc.context.get("open_lot_count", 0),
                    "open_option_count": exc.context.get("open_option_count", 0),
                },
            ) from exc
        if "transaction_count" in exc.context:
            # 409 with the EVIDENCE attached, not just prose — mirrors add_transaction's own
            # PreviouslyDeletedError/DuplicateTransactionError shape above. The frontend needs
            # the real transaction count and the other accounts a shared transfer also touches
            # to build its force-delete confirmation without ever regexing a message that
            # embeds a free-text, user-chosen account name (security review 2026-09-06,
            # MEDIUM-3 — a name like "Fund 1 transaction reserve" broke a prose-parsing regex).
            raise HTTPException(
                to_http_status(exc),
                detail={
                    "error": exc.code,
                    "message": exc.message,
                    "transaction_count": exc.context.get("transaction_count"),
                    "other_accounts": exc.context.get("other_accounts", []),
                },
            ) from exc
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@router.get("/api/accounts/{account_id}/statement")
def get_account_statement(account_id: str):
    """Statement summary for a configured credit card. Returns null (204) when the account
    is not a credit card or has no cycle config. Never 500s on unconfigured accounts."""
    from dataclasses import asdict

    from fastapi.responses import Response

    from backend.services.card_statements import statement_summary

    acc = svc.accounts.find_by_id(account_id)
    if acc is None:
        raise HTTPException(404, "Account not found")
    summary = statement_summary(account_id)
    if summary is None:
        return Response(status_code=204)
    return asdict(summary)


@router.get("/api/accounts/{account_id}/balance")
def get_account_balance(account_id: str):
    acc = svc.accounts.find_by_id(account_id)
    if acc is None:
        raise HTTPException(404, "Account not found")
    txns = svc.transactions.all()
    bal = svc.account_balance(acc, txns, svc.opening_date(acc, txns))
    return {
        "account_id": account_id,
        "balance": bal,
        "currency": acc.get("currency"),
        "balance_base": svc.to_base(bal, acc.get("currency") or svc.base_currency()),
        "base_currency": svc.base_currency(),
    }


@router.post("/api/accounts/{account_id}/reconcile")
def reconcile_account(account_id: str, body: ReconcileBody):
    """Reconcile to an asserted real balance (cleared/reconcile MVP). Diffs it against the
    account's CLEARED balance (default), emits an auto-cleared `adjustment` on a mismatch,
    and locks the cleared set. ``against="working"`` diffs the full computed balance."""
    if svc.accounts.find_by_id(account_id) is None:
        raise HTTPException(404, "Account not found")
    adjustment = svc.reconcile(account_id, body.real_balance, body.date, against=body.against)
    acct = svc.accounts.find_by_id(account_id)
    txns = svc.transactions.all()
    cutoff = svc.opening_date(acct, txns)
    return {
        "ok": True,
        "adjustment": adjustment,  # None if already in sync
        "balance": svc.account_balance(acct, txns, cutoff),
        "cleared_balance": svc.cleared_account_balance(acct, txns, cutoff),
    }


# Legacy alias so existing callers (frontend source dropdown) keep working until
# they migrate to /api/accounts. Returns the account list (no balances).
@router.get("/api/payment-sources")
def get_payment_sources():
    return svc.accounts.query_accounts()


# ── Net worth ──────────────────────────────────────────────────────────────────


@router.get("/api/net-worth")
def get_net_worth():
    # The fold classifies every stored account, so a row this build cannot type
    # (`ledger.account_kind_and_band`) raises here. Uncaught, FastAPI returned a bare
    # "500 Internal Server Error" with no body — the message naming the offending account,
    # the seven known types and the recovery path never reached the person who had to act
    # on it. This is the ONE read every tab makes on load, so it is where they meet it.
    try:
        return svc.net_worth()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Transactions ─────────────────────────────────────────────────────────────


# The free-text matcher moved into services/ledger.py so the REST route and its service
# twin (which the AI's get_transactions calls) share ONE implementation — a search that
# scoped differently depending on which door you came through would be a silent data
# discrepancy, and q_field's description/memo split is exactly where that would bite.
_txn_matches_q = svc.txn_matches_q


# Registered before the dynamic /api/transactions/{txn_id} route so the literal path wins.
@router.get("/api/transactions/spend-by-week")
def get_spend_by_week(q: str = "", weeks: int = 6):
    """Expense transactions matching `q` (description/payee/memo/category) summed into
    Monday-start weeks. An empty query returns empty weeks, never the whole book."""
    from backend.services import spend_search as spend_search_service

    return spend_search_service.spend_search(q, weeks=weeks)


@router.get("/api/transactions/recently-added")
def get_recently_added_transactions(
    limit: int = Query(8, ge=1, le=500),
    offset: int = Query(0, ge=0),
    needs_review: Optional[bool] = None,
    automation_only: bool = False,
    exclude_openings: bool = False,
) -> dict:
    """Last `limit` transactions by INSERTION time (`created_at`), not transaction date — the
    "recently added" dashboard widget's feed. A backfilled import lands here even when its own
    date is weeks old; use plain `GET /api/transactions` for "recent by date" instead.
    `exclude_openings` leaves out every account's Starting Balance row, which is a balance, not
    something that was added or spent."""
    _opening_ids = svc.opening_txn_ids()
    page, total = svc.transactions.recent_page_by_created_at(
        limit=limit,
        offset=offset,
        needs_review=needs_review,
        automation_only=automation_only,
        exclude_ids=_opening_ids if exclude_openings else frozenset(),
    )
    _base = svc.base_currency()
    _rates = load_fx_cache()
    # The review queue names the automation that fired each row ("via …"), read from the
    # create audit event in one query — the same label the drawer's timeline shows. For ANY
    # review query, not only the automation-scoped one: the Accounts tab reviews everything
    # flagged, and its cards still say which automation wrote the rows that one did.
    _provenance = (
        audit_svc.creation_provenance([t["id"] for t in page if t.get("id")])
        if automation_only or needs_review
        else {}
    )
    for t in page:
        if t.get("kind") not in ("transfer",):
            t["base_amount"] = svc.txn_base_amount(t, _base, _rates)
            t["base_currency"] = _base
        t["needs_review"] = bool(t.get("needs_review", False))
        t["source"] = display_source(
            "automation" if automation_only else t.get("source"),
            kind=t.get("kind"),
            is_opening=t.get("id") in _opening_ids,
        )
        if automation_only or needs_review:
            origin = _provenance.get(t.get("id"), {})
            t["via"] = origin.get("via")
            t["automation_id"] = origin.get("automation_id")
    return {
        "transactions": label_txns(page),
        "total": total,
        "has_more": offset + len(page) < total,
    }


@router.get("/api/transactions")
def get_transactions(
    month: Optional[int] = None,
    year: Optional[int] = None,
    # kind/category/account_ids/tag/payee_id/source are repeated query params
    # (?category=a&category=b -> OR'd). `Annotated[..., Query()] = None` (rather than
    # `= Query(default=None)`) keeps the real Python default a plain `None` — a direct
    # call (the AI tool's twin, tests) that omits one gets `None`, never FastAPI's
    # `Query` sentinel object, while HTTP requests still get full Query-param parsing
    # (repeated key -> list; a single value is NEVER comma-split — a category key or a
    # free-form tag may legitimately contain a literal comma, e.g. a user-typed label
    # like "Food, Dining" normalizes to the key `food,_dining` with nothing stripped).
    kind: Annotated[Optional[List[str]], Query()] = None,
    category: Annotated[Optional[List[str]], Query()] = None,
    account_id: Optional[str] = None,
    account_ids: Annotated[Optional[List[str]], Query()] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    q: Optional[str] = None,
    q_field: str = "any",
    tag: Annotated[Optional[List[str]], Query()] = None,
    payee_id: Annotated[Optional[List[str]], Query()] = None,
    source: Annotated[Optional[List[str]], Query()] = None,
    cleared: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    uncategorized: Optional[bool] = None,
    amount: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    logger.info(
        "get_transactions: month=%s year=%s kind=%s category=%s account_id=%s account_ids=%s "
        "date_from=%s date_to=%s min_amount=%s max_amount=%s q=%s q_field=%s tag=%s "
        "payee_id=%s source=%s cleared=%s needs_review=%s uncategorized=%s amount=%s "
        "batch_id=%s limit=%s offset=%s",
        month,
        year,
        kind,
        category,
        account_id,
        account_ids,
        date_from,
        date_to,
        min_amount,
        max_amount,
        q,
        q_field,
        tag,
        payee_id,
        source,
        cleared,
        needs_review,
        uncategorized,
        amount,
        batch_id,
        limit,
        offset,
    )
    # Fast path: when every active filter is SQL-pushable, skip materializing the whole
    # filtered set in Python — query the database for exactly one page, its total, and its
    # date span directly (query_txns_page). q/tag/source/batch_id/amount each need a
    # cross-store join, a JSON-array containment, or the FX-recomputed magnitude / display-
    # projected value — none of which is a single portable SQL WHERE clause — so a request
    # using any of them takes the slow path below, byte-for-byte unchanged from before.
    _needle_probe = (q or "").strip().lower()
    _amount_filter_probe = min_amount is not None or max_amount is not None or bool(amount)
    _fast_path_blocked = bool(_needle_probe or tag or source or batch_id or _amount_filter_probe)
    if not _fast_path_blocked and limit:
        _search_filter_probe = (
            bool(_needle_probe)
            or cleared is not None
            or needs_review is not None
            or uncategorized is not None
        )
        # `and month is None and year is None` scopes the WHOLE OR'd group — an explicit
        # month/year always wins over "this filter implies all-time" (e.g.
        # uncategorized=True alone is all-time, but uncategorized=True + month=6 is scoped
        # to June). Matches the slow path's skip_date_filter formula exactly.
        _skip_date_filter_probe = (
            (account_id or account_ids or limit or payee_id or _search_filter_probe)
            and month is None
            and year is None
        )
        date_from_eff, date_to_eff = date_from, date_to
        if not date_from_eff and not date_to_eff and not _skip_date_filter_probe:
            # A month/year prefix match ("2026-08") is equivalent to the explicit range
            # ["2026-08-01", "2026-08-31"] under lexical ISO-date comparison — no month has
            # a 32nd, so the upper bound never spills into the next month.
            _today = user_today()
            _m = month or _today.month
            _y = year or _today.year
            date_from_eff, date_to_eff = f"{_y}-{_m:02d}-01", f"{_y}-{_m:02d}-31"
        page, total, first_date, last_date, to_sum, from_sum = svc.transactions.query_txns_page(
            kind=kind,
            category=svc.resolve_category_refs(category),
            account_id=account_id,
            account_ids=account_ids,
            payee_id=payee_id,
            date_from=date_from_eff,
            date_to=date_to_eff,
            cleared=cleared,
            needs_review=needs_review,
            uncategorized=uncategorized,
            limit=limit,
            offset=offset or 0,
        )
        _base = svc.base_currency()
        _rates = load_fx_cache()
        _opening_ids = svc.opening_txn_ids()
        for t in page:
            if t.get("kind") not in ("transfer",):
                t["base_amount"] = svc.txn_base_amount(t, _base, _rates)
                t["base_currency"] = _base
            t["needs_review"] = bool(t.get("needs_review", False))
            t["source"] = display_source(
                t.get("source"), kind=t.get("kind"), is_opening=t.get("id") in _opening_ids
            )
        start = offset or 0
        result = {
            "transactions": label_txns(page),
            "total": total,
            "has_more": start + len(page) < total,
            "first_date": first_date,
            "last_date": last_date,
        }
        if account_id:
            # `to_sum`/`from_sum` are the SQL twin of `period_net`'s own fold, computed over
            # the full filtered set (not just `page`) — pass the already-summed native net
            # through so `period_net` only has to do the account-currency→base conversion.
            net, net_base = svc.period_net(None, account_id, _base, _rates, net=to_sum - from_sum)
            result["period_net"] = net
            if svc.accounts.find_by_id(account_id):
                result["period_net_base"] = net_base
        logger.info(
            "get_transactions: returning %d/%d (page start=%d) [db-paginated]",
            len(page),
            total,
            start,
        )
        return result

    txns = svc.transactions.query_txns(
        kind=kind,
        category=svc.resolve_category_refs(category),
        account_id=account_id,
        account_ids=account_ids,
        tag=tag,
        payee_id=payee_id,
    )

    # Free-text search: a cross-store join (payee name / category label), so it runs here
    # as a post-filter rather than being pushed into query_txns (kept AND'd with every other
    # filter above — order doesn't matter for a pure filter chain).
    needle = (q or "").strip().lower()
    if needle:
        qf = (
            q_field
            if q_field in ("any", "payee", "description", "memo", "note", "category")
            else "any"
        )
        payee_names: dict[str, str] = {}
        cat_labels: dict[str, str] = {}
        if qf in ("any", "payee"):
            payee_names = payee_registry.payee_names()
        if qf in ("any", "category"):
            cat_labels = {
                c["id"]: c.get("label", "") for c in get_all_categories(include_hidden=True)
            }
        txns = [t for t in txns if _txn_matches_q(t, needle, qf, payee_names, cat_labels)]

    if date_from or date_to:
        # Explicit date range takes precedence over month/year
        if date_from:
            txns = [t for t in txns if t.get("date", "") >= date_from]
        if date_to:
            txns = [t for t in txns if t.get("date", "") <= date_to]
    else:
        # Skip the month filter when fetching all-time history:
        #   - single account drill-down (account_id set, no explicit date range)
        #   - "All accounts" summary with a limit (limit set, no explicit date range)
        #   - an amount filter is set ("find big transactions" is all-time)
        #   - a text/tag/source/cleared/review/uncategorized search is set ("find every Uber
        #     charge" — or every unsorted row — is all-time)
        # Otherwise apply month/year filter, defaulting to the current month.
        amount_filter = min_amount is not None or max_amount is not None or bool(amount)
        search_filter = (
            bool(needle)
            or bool(tag)
            or bool(source)
            or cleared is not None
            or needs_review is not None
            or uncategorized is not None
            or bool(batch_id)
        )
        skip_date_filter = (
            (account_id or account_ids or limit or amount_filter or search_filter or payee_id)
            and month is None
            and year is None
        )
        if not skip_date_filter:
            today = user_today()
            m = month or today.month
            y = year or today.year
            prefix = f"{y}-{m:02d}"
            txns = [t for t in txns if t.get("date", "").startswith(prefix)]

    # Re-base the reporting amount into the CURRENT base currency. The stored base_amount is a
    # snapshot in whatever base was active when the txn was written, so after a base-currency
    # change it's stale; recompute from the native leg (transfers keep null — no P&L amount).
    # Done before the amount filter so a base-currency min/max compares in the current base.
    _base = svc.base_currency()
    _rates = load_fx_cache()
    _opening_ids = svc.opening_txn_ids()
    for t in txns:
        if t.get("kind") not in ("transfer",):
            t["base_amount"] = svc.txn_base_amount(t, _base, _rates)
            t["base_currency"] = _base
        # Accounts redesign: project the stored internal source onto the register's 8-value
        # display vocab, and surface needs_review (default False for legacy rows). Display-only
        # — the stored row is untouched (these are freshly-deserialized dicts per request).
        t["needs_review"] = bool(t.get("needs_review", False))
        t["source"] = display_source(
            t.get("source"), kind=t.get("kind"), is_opening=t.get("id") in _opening_ids
        )

    if cleared is not None:
        txns = [t for t in txns if bool(t.get("cleared", False)) == cleared]
    if needs_review is not None:
        txns = [t for t in txns if t["needs_review"] == needs_review]
    if uncategorized is not None:
        txns = [t for t in txns if svc.is_uncategorized(t) == uncategorized]
    if source:
        wanted_sources = set(source)
        txns = [t for t in txns if t["source"] in wanted_sources]
    if batch_id:
        # A merged/linked row is never stamped with batch_id. We deliberately do
        # NOT retag an existing row's batch_id on merge, since that field means
        # "originally created by this batch" and a merge must not overwrite that provenance.
        # The batch's own record still carries exactly which existing rows THIS commit
        # touched — its `merges` list — so "view this import" unions both: rows created by
        # the batch, and rows this batch linked to.
        batch = svc.import_batches.find_by_id(batch_id)
        merged_ids = {m["txn_id"] for m in (batch.get("merges") or [])} if batch else set()
        txns = [t for t in txns if t.get("batch_id") == batch_id or t["id"] in merged_ids]

    if min_amount is not None or max_amount is not None:
        if min_amount is not None:
            txns = [t for t in txns if svc.txn_magnitude(t, account_id) >= min_amount]
        if max_amount is not None:
            txns = [t for t in txns if svc.txn_magnitude(t, account_id) <= max_amount]

    if amount:
        constraints = [
            c
            for raw in amount.split(",")
            if raw.strip() and (c := svc.parse_amount_constraint(raw))
        ]
        for op, direction, value in constraints:
            txns = [
                t
                for t in txns
                if svc.matches_amount_constraint(t, account_id, op, direction, value)
            ]

    txns.sort(
        key=lambda t: (t["date"], abs(float(t.get("from_amount") or t.get("to_amount") or 0))),
        reverse=True,
    )
    if limit:
        total = len(txns)
        start = offset or 0
        page = txns[start : start + limit]
        # `first_date` = the oldest date in the FULL filtered set, not just this page. The
        # list is already sorted desc and fully in memory here, so it costs nothing — and it
        # spares the client from inferring it, which it cannot do: it only ever sees one page
        # (newest-first), so its earliest row is merely the oldest of the newest N.
        result: dict = {
            "transactions": label_txns(page),
            "total": total,
            "has_more": start + len(page) < total,
            "first_date": txns[-1]["date"] if txns else None,
            "last_date": txns[0]["date"] if txns else None,
        }
        if account_id:
            net, net_base = svc.period_net(txns, account_id, _base, _rates)
            result["period_net"] = net
            if svc.accounts.find_by_id(account_id):
                result["period_net_base"] = net_base
        logger.info("get_transactions: returning %d/%d (page start=%d)", len(page), total, start)
        return result
    logger.info("get_transactions: returning %d transactions", len(txns))
    return label_txns(txns)


@router.post("/api/transactions")
def add_transaction(body: TransactionCreate):
    payload = body.model_dump()
    force = bool(payload.pop("force", False))
    resurrect = bool(payload.pop("resurrect", False))
    try:
        created = svc.create_transaction(payload, force=force, resurrect=resurrect)
    except PreviouslyDeletedError as exc:
        raise HTTPException(
            to_http_status(exc),
            detail={
                "error": exc.code,
                "message": exc.message,
                "deleted_id": exc.context.get("deleted_txn_id"),
                "rejection_id": exc.context.get("rejection_id"),
                "rejected_at": exc.context.get("rejected_at"),
                "retry_with": {"resurrect": True},
            },
        ) from exc
    except DuplicateTransactionError as exc:
        # 409 with the EVIDENCE attached, not just prose: the client needs the row it collided
        # with to render "you already have this — book anyway?" without a second round trip.
        # `exc.message` already carries the recovery sentence for a human/model reader.
        raise HTTPException(
            to_http_status(exc),
            detail={
                "error": exc.code,
                "message": exc.message,
                "duplicate_of": exc.context.get("existing_id"),
                "existing": exc.context.get("existing"),
                "retry_with": {"force": True},
            },
        ) from exc
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    # Dedup / match engine (RFC transaction-dedup-engine): surface a cross-source duplicate or
    # transfer pair against the existing ledger — created-then-flagged into the one review queue,
    # so a MANUAL add that duplicates an imported charge is caught too (every surface, not just
    # imports). Side-effect only; the row is returned unchanged and the UI reads the queue from
    # GET /api/matches/reviews. Best-effort: never break the create.
    try:
        from backend.services import match_review

        match_review.review_created_rows([created])
    except Exception:  # noqa: BLE001 — surfacing must never break a completed create
        # Logged, never raised: a silent failure here is indistinguishable from "no duplicate
        # found", which is how a dedup net that never ran passes for one that found nothing.
        logger.exception("manual add: dedup review pass failed for txn %s", created.get("id"))
    # Tracking-start honesty: flag (never drop) a leg that lands before its account's
    # start, or a transfer that straddles one. Non-blocking; the row is written either way.
    try:
        from backend.services import tracking_start

        flags = tracking_start.flags_for_txn(
            created, svc.accounts.query_accounts(), svc.transactions.all()
        )
        if flags:
            created = {**created, "warnings": flags}
    except Exception:  # noqa: BLE001
        pass
    return created


@router.put("/api/transactions/{txn_id}")
def update_transaction(txn_id: str, body: TransactionUpdate):
    # Thin over the single per-txn edit choke point (svc.update_transaction): re-runs the
    # create logic so legs + base snapshot stay consistent, fires spend-rules, and records
    # a category correction (P2-SP2).
    try:
        patch = body.model_dump(exclude_none=True)
        for clearable in (
            "category",
            "payee_id",
            "from_account_id",
            "from_amount",
            "from_currency",
            "to_account_id",
            "to_amount",
            "to_currency",
            "original_amount",
            "original_currency",
        ):
            if clearable in body.model_fields_set and getattr(body, clearable) is None:
                patch[clearable] = None
        rebuilt = svc.update_transaction(txn_id, patch)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if rebuilt is None:
        raise HTTPException(404, "Transaction not found")
    return rebuilt


@router.post("/api/transactions/{txn_id}/refs/detach")
def detach_transaction_ref(txn_id: str, body: DetachRefBody):
    """Unlink one provenance pair from a transaction (ADR 2026-09-07, CDS-5) — the drawer's
    "Unlink source" action. Thin over ``services.ledger.detach_external_ref``: removes exactly
    that pair, audits it, and records a ``different`` decision so a future re-fetch of the same
    provider row books on its own instead of re-colliding with this row."""
    try:
        result = svc.detach_external_ref(txn_id, body.source, body.ref)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.patch("/api/transactions/{txn_id}/cleared")
def set_transaction_cleared(txn_id: str, body: ClearedBody):
    """Toggle a transaction's cleared (reconciliation) state. Thin over the ledger choke
    point; never moves the working balance (cleared/reconcile MVP)."""
    try:
        updated = svc.set_transaction_cleared(txn_id, body.cleared)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Transaction not found")
    return updated


@router.delete("/api/transactions/{txn_id}")
def delete_transaction(txn_id: str):
    logger.info("delete_transaction: txn_id=%s", txn_id)
    # Route through the service choke point (not transactions.soft_delete directly) so the
    # delete is recorded in the provenance/audit trail (transaction-provenance RFC).
    try:
        result = svc.bulk_delete_transactions([txn_id])
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if txn_id in result["not_found"]:
        logger.warning("delete_transaction: not found txn_id=%s", txn_id)
        raise HTTPException(404, "Transaction not found")
    logger.info("delete_transaction: soft-deleted txn_id=%s", txn_id)
    return {"ok": True}


class PurgeTransactionsBody(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=500)


@router.post("/api/transactions/purge")
def purge_transactions(body: PurgeTransactionsBody, request: Request):
    """Delete PERMANENTLY: the rows, their edit history's contents, checkpoint images and
    everything stored only about them (``services.transaction_purge``). No undo. An ordinary
    ``DELETE`` stays the soft delete. ``X-Requested-With`` is required on top of the
    app-wide same-origin rule: an irreversible erase must not be reachable by a plain form."""
    if request.headers.get("x-requested-with", "").lower() != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")
    try:
        result = transaction_purge.purge_transactions(body.ids)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return {"ok": True, **result}


@router.get("/api/transactions/{txn_id}/audit")
def get_transaction_audit(txn_id: str):
    """Provenance / audit trail for one transaction: how it was created and every edit or
    deletion since (actor, surface, session, intent, before/after diff). Falls back to a
    best-effort reconstruction for rows that predate the feature (transaction-provenance RFC)."""
    return audit_svc.trace(txn_id)


@router.get("/api/transactions/{txn_id}/activity")
def get_transaction_activity(txn_id: str):
    """Provenance + activity timeline for one transaction, shaped for the redesign drawer
    (Accounts redesign): ``{source, addedBy, createdAt, updatedAt, activity:[...]}`` derived
    from ``txn_audit.json`` (best-effort minimal trail for rows that predate the feature)."""
    result = audit_svc.activity(txn_id)
    if result is None:
        raise HTTPException(404, "Transaction not found")
    return result


def _email_source_entry(row: dict) -> dict:
    # `source`/`ref` (ADR 2026-09-07, CDS-5): explicit top-level identity fields on EVERY
    # entry — available or not — so the drawer's "Unlink source" action can post
    # {source, ref} straight back to POST .../refs/detach without re-deriving it from
    # kind-specific fields (`message_id` here, `provider`+the snapshot key on connector's
    # own entry). `ref` is the stored external_refs[].ref value verbatim.
    ref = row.get("message_id") or ""
    if row.get("unavailable"):
        return {
            "kind": "email",
            "source": "email",
            "ref": ref,
            "message_id": ref,
            "unavailable": True,
        }
    return {
        "kind": "email",
        "source": "email",
        "ref": ref,
        "message_id": row["message_id"],
        "subject": row.get("subject") or "",
        "from": row.get("from_addr") or "",
        "received_at": row.get("received_at") or "",
        "body_text": row.get("body_text") or "",
        "unavailable": False,
    }


@router.get("/api/transactions/{txn_id}/sources")
def get_transaction_sources(txn_id: str):
    """Every source snapshot behind a transaction booked (or merge-enriched) from email or a
    bank/broker connector — one ``entries[]`` item per provenance pair, kind-tagged
    (``"email"`` — unchanged EM1 shape — or ``"connector"``: RFC connectors-as-data-sources
    §B). Was ``GET .../source-emails`` (``{"emails": [...]}``); renamed with the connector
    union per the UI spec — no compat shim. The frontend call site (``TxnDetail.tsx``) moves
    to this path in the UI lane (L3), tracked as a temporary exemption in
    ``tests/test_route_path_consistency.py``'s ``_PENDING_PARALLEL_LANE_ROUTES`` until that
    lane lands. Email entries are oldest-received-first; connector entries follow in the
    transaction's ``external_refs`` order. A pruned/never-stashed snapshot of EITHER kind is
    an explicit ``unavailable: true`` entry, never a silent omission. One 404 covers the
    nothing-to-show cases: the transaction doesn't exist, or carries no provenance of either
    kind.

    EVERY entry — either kind, available or not — carries explicit top-level ``source``/
    ``ref`` fields (ADR 2026-09-07, CDS-5): the drawer's "Unlink source" action posts exactly
    those two values, unchanged, straight to ``POST .../refs/detach``."""
    email_rows = email_snapshots.snapshots_for_transaction(txn_id) or []
    connector_entries = connector_snapshots.snapshots_for_transaction(txn_id) or []
    if not email_rows and not connector_entries:
        raise HTTPException(404, "No source snapshots for this transaction")
    entries = [_email_source_entry(row) for row in email_rows] + connector_entries
    return {"entries": entries}


@router.get("/api/transactions/{txn_id}/statements")
def get_transaction_statements(txn_id: str):
    """The statement(s) this transaction is ON — the register's counterpart to the source-email
    panel, which an email-provenance row has had all along while a statement-linked row showed
    nothing.

    ``on_statements`` answers "which statement covers this row", NOT "where did this row come
    FROM" — a different question, and conflating them was the 2026-07-31 bug. A row Lucius
    logged from an email alert and a statement later confirmed is on that statement, and says
    so, while its ``source`` still correctly reads ``ai``.

    An empty list is the honest answer for a hand entry, an agent booking, a bank-feed sync, or
    an import whose statement record was deleted — the panel renders nothing rather than
    substituting the row's origin. 404 only when the transaction itself does not exist."""
    from backend.services.statements import ledger_statement_links as links

    txn = svc.transactions.find_by_id(txn_id)
    if txn is None:
        raise HTTPException(404, f"Transaction {txn_id} not found")
    found = links.on_statements(txn, links.StatementRefIndex())
    return {"statements": (found or {}).get("statements") or []}


@router.delete("/api/transactions")
def bulk_delete_transactions(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    """Bulk soft-delete transactions matching all supplied filters. At least one
    filter is required to prevent accidental wipe of the entire ledger — enforced by
    the service choke point (services.ledger.delete_transactions_where), not here."""
    # Route through the resolving service choke point (not transactions.bulk_delete_where
    # directly, CATF2) — a caller-supplied category LABEL must resolve to its stored opaque
    # id, and an unresolvable ref (or a missing-filter call) must refuse loudly rather than
    # silently matching 0 — or ALL — rows.
    try:
        deleted = svc.delete_transactions_where(
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            kind=kind,
            category=category,
            batch_id=batch_id,
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    logger.info(
        "bulk_delete_transactions: deleted=%d filters=date_from=%s date_to=%s "
        "account_id=%s kind=%s category=%s batch_id=%s",
        deleted,
        date_from,
        date_to,
        account_id,
        kind,
        category,
        batch_id,
    )
    return {"ok": True, "deleted": deleted}


@router.post("/api/transactions/bulk")
def bulk_add_transactions(body: BulkTransactionImport):
    """Bulk-create transactions.

    F24 fixes (additive — response shape is backward compatible):
      (a) L0 skip: rows any of whose external_refs (source, ref) pairs is already present are
          counted as ``skipped`` instead of creating a duplicate.
      (b) A real uuid batch_id is stamped on the batch. The source is taken from
          body.source if it is in KNOWN_SOURCES, otherwise defaults to 'api' (stops
          force-stamping 'csv_import' on every external POST).
      (c) After the write, ``match_review.review_created_rows`` is called best-effort
          so newly-created rows enter the review queue; failure never aborts the write.
    """
    import uuid as _uuid

    from backend.core.txn_sources import canonical_writer

    _BULK_MAX = 5000
    if len(body.transactions) > _BULK_MAX:
        raise HTTPException(422, f"batch too large: max {_BULK_MAX} rows per request")

    logger.info("bulk_add_transactions: received %d transactions", len(body.transactions))

    # F24(b): pick an honest source (never force csv_import) and stamp a real batch id.
    #
    # A source we do not recognise is KEPT, not rewritten (RFC 2026-08-02). Replacing it with
    # "api" erased the caller's identity at the door — which is how `pdf_import` reached storage
    # as `api` and how a user-installed integration would be recorded as an anonymous HTTP
    # write. `canonical_writer` maps retired values onto the current vocabulary; anything else
    # passes through untouched and is treated as UNKNOWN downstream (reviewed, never "You").
    raw_source = body.source or ""
    effective_source = canonical_writer(raw_source) or raw_source or "api"
    batch_id = str(_uuid.uuid4())

    # Build the known-refs set in one pass (O(n) over the store, O(1) per-row lookup).
    known_refs: set[tuple[str | None, str]] = set()
    for t in svc.transactions.all():
        if t.get("deleted"):
            continue
        known_refs |= ext_ref_pairs(t)

    # F24(a): L0 skip — filter rows any of whose external_refs pairs is already in the ledger
    # OR already seen in this batch (intra-batch dedup: two rows with the same pair in one
    # request must not both be inserted).
    rows_to_create: list[dict] = []
    skipped_ext: list[dict] = []
    seen_in_batch: set[tuple[str | None, str]] = set()
    for item in body.transactions:
        d = item.model_dump()
        pairs = ext_ref_pairs(d)
        if pairs and any(key in known_refs or key in seen_in_batch for key in pairs):
            skipped_ext.append(
                {
                    "external_refs": [
                        {"source": src, "ref": ref}
                        for src, ref in sorted(pairs, key=lambda pr: (pr[0] or "", pr[1]))
                    ],
                    "reason": "exact external ref already present",
                }
            )
            continue
        seen_in_batch |= pairs
        # Source resolution: body.source wins over per-row source when body.source was
        # explicitly supplied (not empty).  A per-row source that differs from the default
        # "manual" wins when no body.source is given (callers can set per-row sources).
        per_row_src = d.get("source") or ""
        if raw_source:
            # body.source was explicitly supplied — use it for the whole batch.
            d["source"] = effective_source
        elif per_row_src:
            d["source"] = canonical_writer(per_row_src) or per_row_src
        else:
            d["source"] = effective_source
        rows_to_create.append(d)

    created, refused = svc.bulk_create_transactions(
        rows_to_create, batch_id=batch_id, force=bool(body.force)
    )

    # Security-review fix: `refused` (the semantic-duplicate / previously-deleted guard's
    # own skip list) was never folded into the response — a row refused by the strong-
    # duplicate guard came back as created=0/skipped=0/skipped_details=[], and the
    # attach-refs hint on `_duplicate_reason` never reached a REST caller at all. One
    # entry per refused row, pairs sorted with the null-safe key (M1: a bare sorted() over
    # (source, ref) pairs raises TypeError the moment a null-source pair sits beside a
    # normal one).
    for row in refused:
        pairs = sorted(ext_ref_pairs(row), key=lambda pr: (pr[0] or "", pr[1]))
        entry: dict = {
            "external_refs": [{"source": s, "ref": r} for s, r in pairs],
            "reason": row.get("_duplicate_reason") or "refused",
        }
        duplicate_id = row.get("_duplicate_of")
        if duplicate_id:
            if row.get("_previously_deleted"):
                entry["deleted_id"] = duplicate_id
            else:
                entry["duplicate_of"] = duplicate_id
        skipped_ext.append(entry)

    logger.info(
        "bulk_add_transactions: created=%d skipped_ext=%d refused=%d batch_id=%s source=%s",
        len(created),
        len(skipped_ext) - len(refused),
        len(refused),
        batch_id,
        effective_source,
    )

    # F24(c): feed the review queue best-effort — the write already happened.
    if created:
        try:
            from backend.services import match_review as mr

            mr.review_created_rows(created, batch_id=batch_id)
        except Exception:
            logger.exception("bulk_add_transactions: match_review failed (non-fatal)")

    return {
        "created": len(created),
        "skipped": len(skipped_ext),
        "batch_id": batch_id,
        "transactions": created,
        "skipped_details": skipped_ext,
    }


@router.patch("/api/transactions/bulk")
def bulk_update_transactions_route(body: BulkTxnUpdate):
    """Apply one label-only change set (category / additive tags / cleared) to many
    transactions at once. Loops the per-txn edit choke points so spend-rules + correction
    capture + turn-checkpoints fire per row. Returns a per-id result summary."""
    changes = body.changes.model_dump(exclude_none=True)
    try:
        result = svc.bulk_update_transactions(body.ids, changes)
    except OpenNetWorthError as e:
        raise HTTPException(to_http_status(e), str(e))
    logger.info(
        "bulk_update_transactions: requested=%d updated=%d not_found=%d skipped=%d",
        len(body.ids),
        result["count"],
        len(result["not_found"]),
        len(result["skipped"]),
    )
    return result


@router.post("/api/transactions/bulk-update")
def bulk_update_transactions_flat(body: BulkTxnUpdateFlat):
    """Flat-body bulk update (CONTRACT.md): the change fields ride alongside ``ids``. Same
    semantics as ``PATCH /api/transactions/bulk`` — category / additive tags / cleared /
    payee_id / needs_review (approve) / to_account_id (move). Returns a per-id summary."""
    changes = body.model_dump(exclude_none=True)
    changes.pop("ids", None)
    try:
        result = svc.bulk_update_transactions(body.ids, changes)
    except OpenNetWorthError as e:
        raise HTTPException(to_http_status(e), str(e))
    logger.info(
        "bulk_update_transactions(flat): requested=%d updated=%d not_found=%d skipped=%d",
        len(body.ids),
        result["count"],
        len(result["not_found"]),
        len(result["skipped"]),
    )
    return result


@router.post("/api/transactions/bulk-delete")
def bulk_delete_transactions_by_ids(body: BulkTxnDelete):
    """Soft-delete many transactions by explicit id list (the multi-select register delete).
    Distinct from the filter-based DELETE /api/transactions; at least one id is required."""
    try:
        result = svc.bulk_delete_transactions(body.ids)
    except OpenNetWorthError as e:
        raise HTTPException(to_http_status(e), str(e))
    logger.info(
        "bulk_delete_transactions_by_ids: requested=%d deleted=%d not_found=%d",
        len(body.ids),
        result["count"],
        len(result["not_found"]),
    )
    return result


# ── Import batches ────────────────────────────────────────────────────────────


@router.get("/api/transactions/batches")
def list_import_batches(status: Optional[str] = None):
    return svc.import_batches.list_batches(status=status)


@router.post("/api/transactions/batches/{batch_id}/rollback")
def rollback_import_batch(batch_id: str):
    batch = svc.import_batches.find_by_id(batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found")
    if batch.get("status") == "rolled_back":
        raise HTTPException(409, "Batch is already rolled back")
    txns = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    txn_ids = [t["id"] for t in txns]
    deleted, _ = svc.transactions.bulk_soft_delete(txn_ids)
    svc.import_batches.set_status(batch_id, "rolled_back")
    logger.info("rollback_import_batch: batch=%s deleted=%d", batch_id, len(deleted))
    return {"ok": True, "batch_id": batch_id, "deleted": len(deleted)}


@router.post("/api/transactions/batches/{batch_id}/rollforward")
def rollforward_import_batch(batch_id: str):
    batch = svc.import_batches.find_by_id(batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found")
    if batch.get("status") == "active":
        raise HTTPException(409, "Batch is already active")
    txns = svc.transactions.query_by_batch(batch_id, include_deleted=True)
    txn_ids = [t["id"] for t in txns if t.get("deleted")]
    restored, _ = svc.transactions.bulk_set_deleted(txn_ids, deleted=False)
    svc.import_batches.set_status(batch_id, "active")
    logger.info("rollforward_import_batch: batch=%s restored=%d", batch_id, len(restored))
    return {"ok": True, "batch_id": batch_id, "restored": len(restored)}


@router.post("/api/transactions/batches/{batch_id}/mark-reviewed")
def mark_batch_reviewed(batch_id: str):
    try:
        result = svc.mark_batch_reviewed(batch_id)
    except OpenNetWorthError as e:
        raise HTTPException(to_http_status(e), str(e))
    logger.info("mark_batch_reviewed: batch=%s updated=%d", batch_id, result["updated"])
    return result


# ── Ledger summary (P&L: income in / expense out; transfers + adjustments aside) ─


@router.get("/api/ledger/summary")
def get_ledger_summary(month: Optional[int] = None, year: Optional[int] = None):
    return svc.get_ledger_summary(month=month, year=year)


# ── Uncategorized summary ─────────────────────────────────────────────────────


@router.get("/api/transactions/uncategorized-summary")
def get_uncategorized_summary(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 5,
):
    """Count + total (base) of top-level uncategorized expense/income transactions.

    A split transaction (category=split sentinel) is never counted; only truly
    un-attributed single-leg expense/income rows are included.

    Query params:
    - date_from / date_to: ISO date bounds (YYYY-MM-DD, both optional).
    - limit: number of most-recent rows returned in the ``recent`` peek (default 5;
      pass 0 to omit).
    """
    return svc.uncategorized_summary(
        date_from=date_from,
        date_to=date_to,
        limit=limit or None,
    )


# ── Single transaction fetch ──────────────────────────────────────────────────
# Registered LAST — after every literal-suffixed /api/transactions/... route above (batches,
# uncategorized-summary, bulk, bulk-update, bulk-delete) — so this dynamic {txn_id} segment
# never shadows them (same reasoning as get_tracking_health vs GET /api/accounts/{account_id}).


@router.get("/api/transactions/{txn_id}")
def get_transaction_by_id(txn_id: str):
    """Fetch one transaction by id, projected exactly like a row from GET /api/transactions
    (current-base amount, display source, needs_review default). The import-review match line's
    "existing transaction" link needs to open a row that may not be on the currently loaded
    register page. 404 on missing/deleted."""
    t = svc.get_transaction(txn_id)
    if t is None:
        raise HTTPException(404, "Transaction not found")
    t = dict(t)
    base = svc.base_currency()
    if t.get("kind") not in ("transfer",):
        t["base_amount"] = svc.txn_base_amount(t, base, load_fx_cache())
        t["base_currency"] = base
    t["needs_review"] = bool(t.get("needs_review", False))
    t["source"] = display_source(
        t.get("source"), kind=t.get("kind"), is_opening=t.get("id") in svc.opening_txn_ids()
    )
    return label_txns([t])[0]


# ── Learned per-row category suggestions ─────────────────────────────────────────
#
# Restored 2026-08-04. This endpoint lived in api/rules.py and was deleted wholesale with
# it (f9b5d790) — collateral, not intent: the feature is about LEARNING a category from the
# user's own history, and never had anything to do with the rules engine beyond sharing a
# file. The service behind it (services/categorize.py) stayed live and tested throughout,
# and frontend/src/components/tabs/CategorySuggestions.tsx still calls this path.
@router.get("/api/category-suggestions")
def get_category_suggestions(min_confidence: float = 0.6, limit: int = 50):
    """Learned category suggestions for UNCATEGORIZED transactions — for each blank
    expense/income row, a category drawn from the user's OWN history of that payee
    (corrections + already-categorized rows) with a confidence score.

    Read-only; ``{suggestions: []}`` on an empty/young book — never 500. APPLYING reuses
    PATCH /api/transactions/bulk (set category on the confirmed ids) — there is NO write
    here.
    """
    from backend.services import categorize as categorize_service

    suggestions = categorize_service.suggest_categories(min_confidence=min_confidence, limit=limit)
    return {"suggestions": suggestions, "count": len(suggestions)}
