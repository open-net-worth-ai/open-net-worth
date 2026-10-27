"""Accounts + net-worth + reconcile tools.

Batch pluralization + honest naming (harness reliability program, 2026-07-19 owner
complaint): an earlier lane widened ``update_account``/``delete_account``/
``set_transaction_cleared`` to accept a batch by turning their SINGULAR id param into
``anyOf[string, array]`` without renaming the tool — a singular name that secretly accepts a
list is a trap (the model reads ``account_id``, wants to pass several, and stringifies the
list instead of passing a real array; the owner hit this live on ``delete_account``). Fixed
here: the tool name AND the batch param are now both plural.

Two shapes, chosen per tool the same way ``ledger.py``'s ``update_transactions`` merge (and
``payees.py``'s pluralization) already decided it:
  • **A bare list of ids** (``delete_accounts``' ``account_ids``, ``set_transactions_cleared``'s
    ``txn_ids``) — a plain array, no ``anyOf``. There is nothing per-item to vary, so a scalar
    branch would only reintroduce the same ambiguity that caused the bug.
  • **Per-item changes** (``update_accounts``' ``updates: [{account_id, ...fields}]``) —
    different accounts get different edits, so it mirrors ``update_transactions`` exactly:
    one item is the trivial single-edit case, many is a batch of independent per-row patches.
  • ``reconcile_accounts`` needed no shape change — it already took a batch ``accounts``
    array; only the tool name was singular.

A rename is a DATA MIGRATION: rewrite every stored reference with
``tool_renames.RENAMES`` — there is no alias to fall back on (a tool name is
persisted data; the migration is covered by ``tests/test_tool_name_migration.py``).
The sibling rename, ``trace_transaction`` → ``trace_transactions``, lives in ``audit.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools._shared import no_op_write_result, svc
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_batch_only
from backend.models.account import ACCOUNT_BAND

# The seven account types (ADR 2026-09-07-seven-account-types-one-axis) — the ONE
# classification axis. `type` alone now derives kind, net-worth band and budget
# membership; see backend.models.account.kind_of/band_of. Literal mirror of
# ACCOUNT_BAND's keys, built from the SAME dict so the enum values can never drift.
# mypy can't statically resolve a Literal built from a runtime tuple (valid-type) — this
# is a real Python typing.Literal, just not one mypy can see through; verified at runtime
# by the schema-derivation golden test (assert_derived_matches_literal).
_ACCOUNT_TYPES = sorted(ACCOUNT_BAND)
_AccountType = Literal[tuple(_ACCOUNT_TYPES)]  # type: ignore[valid-type]


class ListAccountsInput(ToolInput):
    """Schema-of-record for ``list_accounts`` (see ``schema_from_model``)."""

    query: Optional[str] = Field(
        default=None,
        description=(
            "Case-insensitive substring over account NAME or PLATFORM (e.g. 'meridian', "
            "'ibkr'). The cheapest way to find one account."
        ),
    )
    ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Specific accounts by id OR by exact name (case-insensitive). Names that "
            "match nothing, or that are ambiguous, are reported back rather than guessed."
        ),
    )
    kind: Optional[Literal["asset", "liability"]] = Field(
        default=None, description="Only assets, or only debts."
    )
    type: Optional[str] = Field(
        default=None,
        description=(
            "Exact account type, one of the seven: cash | fixed_deposit | investment | "
            "retirement | receivable | credit | loan."
        ),
    )
    currency: Optional[str] = Field(default=None, description="Exact currency code, e.g. 'SGD'.")
    institution: Optional[str] = Field(
        default=None, description="Exact institution/custodian grouping key, e.g. 'IBKR'."
    )
    include_archived: Optional[bool] = Field(
        default=None, description="Set true to include archived (closed) accounts. Default false."
    )


@tool(
    "list_accounts",
    description=(
        "List all of the user's accounts — cash, fixed deposits, investment (holdings + "
        "settlement cash), retirement, receivables, credit, loans — with current balance in "
        "native currency. Credit/loan accounts are liabilities (negative balance). Look up an "
        "account's name/id/currency before adding a transaction.\n\n"
        "`cleared_balance` counts only cleared rows (`balance` counts all) - compare a bank's "
        "posted balance against it.\n\n"
        "FILTER RATHER THAN FETCH EVERYTHING: query='meridian' or ids=['Meridian Bank "
        "Savings'] for one account; kind='liability' or type='credit' for debts only. An "
        "unfiltered call returns everything — the most wasteful way to answer 'what is my "
        "Meridian Bank balance'. Every response reports `showing`/`total`.\n\n"
        "For a card's due date/overdue status do NOT use `payment_due_day`/"
        "`statement_close_day` here — call get_card_statement_status instead."
    ),
    input_schema=schema_of(ListAccountsInput, tool_name="list_accounts"),
)
async def list_accounts(input_data: dict):
    from backend.core.errors import ValidationError
    from backend.services import accounts as accounts_service

    include_archived = bool(input_data.get("include_archived", False))
    accts = svc.accounts.query_accounts(include_archived=True)
    archived = [a for a in accts if a.get("archived")]
    scope = accts if include_archived else [a for a in accts if not a.get("archived")]
    # `total` is the count in scope BEFORE any filter — the denominator the model needs to
    # tell "no accounts exist" apart from "my filter excluded them all".
    total = len(scope)

    rows, unmatched, ambiguous = _select_accounts(
        input_data, scope, accounts_service, ValidationError
    )

    bals = svc.balances(include_archived=True)
    txns = svc.transactions.all()  # shared read for the derived-opening projection below
    idx = svc.opening_index(txns, accts)
    projected = [_project_account(a, bals.get(a["id"]), txns, idx) for a in rows]

    out: dict = {"accounts": projected, "showing": len(projected), "total": total}
    notes: list[str] = []
    if unmatched:
        notes.append(
            f"No account matches {', '.join(repr(u) for u in unmatched)} — check the name or "
            "call list_accounts with no filters to see what exists."
        )
    notes.extend(ambiguous)
    if not projected:
        # A zero-match must never read as an empty database: state the total that DOES
        # exist and how to get back to it. Otherwise the model concludes the user has no
        # accounts, which is a confidently wrong answer about someone's money.
        if total == 0:
            notes.append(
                "This book has no accounts yet"
                + (" (archived ones are hidden — pass include_archived=true)." if archived else ".")
            )
        else:
            notes.append(
                f"0 of {total} accounts match these filters — the accounts DO exist, your "
                "filter excluded them. Drop or loosen the filter (try a shorter query) and "
                "retry. Do NOT report that the user has no accounts."
            )
    elif len(projected) < total:
        notes.append(f"Showing {len(projected)} of {total} accounts — filtered, not truncated.")
    if not include_archived and archived:
        notes.append(
            f"{len(archived)} archived account(s) hidden — pass include_archived=true to include."
        )
    if notes:
        out["note"] = " ".join(notes)
    return json.dumps(out, ensure_ascii=False), None


def _select_accounts(
    input_data: dict, scope: list[dict], accounts_service, validation_error
) -> tuple[list[dict], list[str], list[str]]:
    """Apply the browse filters, returning (rows, unmatched_refs, ambiguity_notes).

    `ids` resolves through ``services.accounts.resolve_account_ref`` — the SAME matcher the
    ledger write choke uses — so a name means the same thing here as it does when booking a
    transaction. A ref that matches nothing or is ambiguous is REPORTED, never guessed at
    and never silently dropped, which would look identical to "that account doesn't exist".
    """
    refs = [str(r).strip() for r in (input_data.get("ids") or []) if str(r).strip()]
    unmatched: list[str] = []
    ambiguous: list[str] = []
    if refs:
        wanted: list[str] = []
        for ref in refs:
            try:
                hit = accounts_service.resolve_account_ref(ref, rows=scope)
            except validation_error as exc:
                ambiguous.append(str(exc))
                continue
            if hit is None:
                unmatched.append(ref)
            else:
                wanted.append(hit)
        scope = [a for a in scope if a.get("id") in set(wanted)]

    return (
        accounts_service.filter_accounts(
            scope,
            query=input_data.get("query"),
            kind=input_data.get("kind"),
            type=input_data.get("type"),
            currency=input_data.get("currency"),
            institution=input_data.get("institution"),
        ),
        unmatched,
        ambiguous,
    )


def _project_account(a: dict, balance, txns: list[dict], idx: dict | None = None) -> dict:
    out = dict(a)
    # The opening lives on the Starting Balance transaction, not on the account — derive both
    # halves so the AI still sees the account's actual opening and tracking start.
    row = svc._opening_row(a, txns, idx=idx)
    out["initial_balance"] = svc._signed_opening(row)
    out["initial_balance_date"] = row.get("date") if row else None
    out["balance"] = balance
    out["cleared_balance"] = svc.cleared_account_balance(a, txns, out["initial_balance_date"])
    return out


# ── Batch helpers shared by update/delete/reconcile_accounts ─────────────────────
#
# Every write tool below takes a PLURAL id-list param, so "archive these 3 accounts" is one
# call, not the payload repeated 3x — while a single id stays trivial (a one-element list)
# and the RESPONSE still mirrors what a lone-account call always returned: exactly one
# target resolved -> the old singular shape, several -> a per-item batch report.


def _ids_from(input_data: dict, plural_key: str, legacy_key: str) -> list[str]:
    """The plural list param if present, else the legacy scalar-or-list field a pre-rename
    alias call would use (e.g. ``account_id`` before ``delete_accounts`` was
    ``delete_account``). Blanks dropped, order preserved."""
    raw = input_data.get(plural_key)
    if raw is None:
        raw = input_data.get(legacy_key)
    if isinstance(raw, list):
        return [str(r).strip() for r in raw if str(r or "").strip()]
    ref = str(raw or "").strip()
    return [ref] if ref else []


def _resolve_account_ref_for_write(
    ref: str, rows: list[dict]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve one write-target ref (id or name) to an account id.

    Id fast path via ``find_by_id`` FIRST — the same order ``_shared._validate_account_id``
    uses — so a known id never depends on a fresh ``rows`` read (and so a test that fakes
    ``find_by_id`` without seeding a real row keeps working, exactly as it did before this
    file resolved names at all). Falls back to ``services.accounts.resolve_account_ref`` for
    a name. Returns ``(account_id, None)`` on a match, ``(None, None)`` when nothing
    matches, or ``(None, ambiguous_message)`` when the name matches more than one account —
    never guessed, and never allowed to take the rest of a batch down with it."""
    from backend.core.errors import ValidationError
    from backend.services import accounts as accounts_service

    if svc.accounts.find_by_id(ref):
        return ref, None
    try:
        resolved = accounts_service.resolve_account_ref(ref, rows=rows)
    except ValidationError as exc:
        return None, str(exc)
    return resolved, None


class ExternalIdInput(ToolInput):
    """One provider-account link (RFC connectors-as-data-sources §D) — a ``(source, ref)``
    pair may be held by at most one account; linking one already held by another account
    raises, naming the holder."""

    source: str = Field(description="Provider key, e.g. 'plaid', 'simplefin', 'ibkr'.")
    ref: str = Field(description="The provider's own id for this account.")
    name: Optional[str] = Field(default=None, description="Provider-side account name/label.")
    mask: Optional[str] = Field(
        default=None,
        description="Last digits the provider shows for this account. Fills `last_four` on "
        "the account if it's currently blank.",
    )
    institution: Optional[str] = Field(
        default=None,
        description="Provider institution display name. Fills `institution` on the account if "
        "it's currently blank. Never itself stored on the link.",
    )


class CreateAccountInput(ToolInput):
    """Schema-of-record for ``create_account`` (see ``schema_from_model``)."""

    name: str = Field(
        description="Account name. Securities account: custodian name ('IBKR'). Currency wallet: 'IBKR USD'."
    )
    type: _AccountType = Field(  # type: ignore[valid-type]
        description=(
            "cash: bank/e-wallet/physical, spendable, in budget · fixed_deposit: "
            "time-locked cash · investment: brokerage/exchange (holdings + settlement "
            "cash; a broker's currency wallet is ALSO investment, same institution) · "
            "retirement: CPF/SRS/pension · receivable: owed to you · credit: card/BNPL/"
            "line of credit, paid from cash · loan: mortgage/auto/personal/student/margin "
            "loan"
        )
    )
    institution: Optional[str] = Field(
        default=None,
        description=(
            "Institution / custodian grouping key (e.g. 'IBKR', 'Meridian Bank'). Same value on "
            "the securities account and all its currency wallets."
        ),
    )
    currency: Optional[str] = Field(
        default=None,
        description=(
            "3-letter ISO code. Use the account's native currency (default to the user's "
            "base_currency if not specified)."
        ),
    )
    initial_balance: float = Field(
        allow_inf_nan=False,
        description=(
            "Required current balance today, in the account's currency; use 0 when empty. Signed as "
            "written — pass a NEGATIVE number for money owed (a card carrying 5000 of "
            "debt is -5000); the sign is not flipped for you. Stored as a dated "
            "Starting Balance transaction, not a field on the account."
        ),
    )
    initial_balance_date: Optional[str] = Field(
        default=None,
        description=(
            "YYYY-MM-DD tracking-start date — it becomes the Starting Balance "
            "transaction's date, and legs before it are ignored. Defaults to today when an "
            "opening balance is given."
        ),
    )
    last_four: Optional[str] = Field(default=None, description="Last 4 digits (cards only)")
    credit_limit: Optional[float] = Field(
        default=None, description="Credit limit in the card's currency (optional)"
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-form note stored on the account record — display/metadata only. Never read "
            "by balances, net worth, dedup, or rules. Defaults to empty."
        ),
    )
    external_ids: Optional[List[ExternalIdInput]] = Field(
        default=None,
        description=(
            "Provider-account links to attach at creation — rare; usually set later via "
            "update_accounts. Each (source, ref) pair may be linked to only one account; "
            "linking one already held elsewhere raises, naming the holder."
        ),
    )
    apr: Optional[float] = Field(
        default=None, description="Interest rate % — credit and loan accounts."
    )
    principal: Optional[float] = Field(
        default=None, description="Original loan principal (loans only; metadata)."
    )
    payoff_date: Optional[str] = Field(
        default=None, description="Target payoff date YYYY-MM-DD (loans only)."
    )
    min_payment: Optional[float] = Field(
        default=None, description="Minimum periodic payment (loans only; metadata)."
    )
    apy: Optional[float] = Field(
        default=None, description="Yield % — cash and fixed_deposit accounts."
    )


@tool(
    "create_account",
    description=(
        "Register a new account — cash, fixed deposit, investment (brokerage/exchange), "
        "retirement, receivable, credit, or loan. `type` alone determines kind and "
        "net-worth band; credit and loan accounts become liabilities automatically. Call "
        "this when the user mentions an account they own. Set `institution` to the custodian "
        "name (e.g. 'IBKR') on all accounts "
        "sharing a venue. RARE: creating a credit account can incidentally retire a "
        "DIFFERENT, already-stale card's payment envelope as a reconciliation side effect — "
        "the result reports it under `unrelated_retired_payment_envelopes`; always relay it."
    ),
    input_schema=schema_of(CreateAccountInput, tool_name="create_account"),
    mutates=("accounts",),
    # was `add_account` (2026-07-19d naming-close): an account is minted standalone, with no
    # parent to be added TO, so `create_` is the verb — see CLAUDE.md's create-vs-add rule.
)
async def create_account(input_data: dict):
    from backend.core.errors import OpenNetWorthError, ValidationError
    from backend.services import accounts as accounts_service

    side_effects: dict = {}
    try:
        accounts_service.assert_no_server_owned_fields(input_data)
        accounts_service.assert_no_dead_account_fields(input_data)
        accounts_service.assert_no_account_color(input_data)
        if "initial_balance" not in input_data:
            raise ValidationError("initial_balance is required (use 0 when the account is empty)")
        acct_type = input_data.get("type", "cash")
        # ONE service call: the account, its required Starting Balance row and the pointer
        # commit together. Zero is a real opening assertion and defaults to today too.
        # kind/band are derived from `type` alone now
        # (ADR 2026-09-07-seven-account-types-one-axis) — never passed by the caller.
        # `connected` is intentionally never read here — derived, never caller-writable
        # (RFC connectors-as-data-sources §D).
        new_account = await asyncio.to_thread(
            accounts_service.create_account,
            {
                "name": input_data["name"],
                "type": acct_type,
                "institution": input_data.get("institution", ""),
                "currency": input_data.get("currency") or svc.base_currency(),
                "initial_balance": input_data["initial_balance"],
                "initial_balance_date": input_data.get("initial_balance_date"),
                "credit_limit": input_data.get("credit_limit"),
                "last_four": input_data.get("last_four", ""),
                "note": input_data.get("note", ""),
                "external_ids": input_data.get("external_ids") or [],
                "apr": input_data.get("apr"),
                "principal": input_data.get("principal"),
                "payoff_date": input_data.get("payoff_date") or None,
                "min_payment": input_data.get("min_payment"),
                "apy": input_data.get("apy"),
            },
            side_effects=side_effects,
        )
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}, ensure_ascii=False), None
    out: dict = {"ok": True, "account": svc.account_with_balance(new_account)}
    if side_effects.get("unrelated_retired_payment_envelopes"):
        # A new credit account's own sync is a GLOBAL reconciliation pass — it can incidentally
        # retire a DIFFERENT, already-stale card's payment envelope in the same pass (never
        # this brand-new account's own). Disclosed so the model relays the real money lost
        # instead of it evaporating silently (review 2026-09-10).
        out["unrelated_retired_payment_envelopes"] = side_effects[
            "unrelated_retired_payment_envelopes"
        ]
        out["warning"] = (
            "Creating this account also retired ANOTHER account's stale Credit Card Payment "
            "envelope (its assigned amount was zeroed in every month listed) — tell the user "
            "exactly what was zeroed and on which account."
        )
    if side_effects.get("payment_envelope_sync_warning"):
        out["payment_envelope_sync_warning"] = side_effects["payment_envelope_sync_warning"]
        out["warning"] = side_effects["payment_envelope_sync_warning"]["message"]
    return json.dumps(out, ensure_ascii=False), None


# Fields ``update_accounts`` may patch directly via the generic store update. Excludes BOTH
# opening parameters: neither the amount nor the tracking start is a stored account field —
# they are the amount and the date of the Starting Balance transaction, and both are routed
# through set_account_opening below.
_ACCOUNT_UPDATE_FIELDS = frozenset(
    {
        "name",
        "institution",
        "credit_limit",
        "last_four",
        "note",
        "archived",
        "external_ids",
        "apr",
        "principal",
        "payoff_date",
        "min_payment",
        "apy",
        "statement_close_day",
        "payment_due_day",
    }
)

# Everything ``update_accounts`` can move, including the two DERIVED opening fields that live
# on the Starting Balance transaction rather than the account row — enough to tell a real edit
# from a submitted-but-unlanded write.
_ACCOUNT_SNAPSHOT_FIELDS = _ACCOUNT_UPDATE_FIELDS | {
    "initial_balance",
    "initial_balance_date",
}


def _account_write_snapshot(hydrated: dict) -> dict:
    return {k: hydrated.get(k) for k in _ACCOUNT_SNAPSHOT_FIELDS}


def _account_write_snapshot_or_none(account: dict) -> dict | None:
    """The before-snapshot, or None when the account cannot be hydrated at all.

    Hydration raises on a dangling ``opening_txn_id`` — and this write is one of the surfaces
    that REPAIRS that. None never compares equal to the after-snapshot, so an unreadable
    account's repair is reported as the real change it is, never as ``unchanged``."""
    from backend.core.errors import DataIntegrityError

    try:
        return _account_write_snapshot(svc.account_with_balance(account))
    except DataIntegrityError:
        return None


_UNCHANGED_WRITE_MESSAGE = (
    "The write was applied but nothing changed — every field you sent already held that value. "
    "Do NOT re-send the same call expecting a different result; if the account still looks "
    "wrong, read it back with list_accounts and check which field is actually off."
)


class UpdateAccountItem(ToolInput):
    """One account's change in a batch ``update_accounts`` call (see ``UpdateAccountsInput``)."""

    account_id: str = Field(description="Account ID or name from list_accounts.")
    name: Optional[str] = Field(
        default=None,
        description=(
            "New display name. Load-bearing, not cosmetic: account refs resolve by name as "
            "well as id, so renaming changes what later calls resolve, and a name that "
            "duplicates another live account makes BOTH ambiguous — a ref naming them then "
            "fails rather than guessing. Omit to leave unchanged."
        ),
    )
    institution: Optional[str] = Field(
        default=None,
        description=(
            "Custodian grouping key (e.g. 'IBKR'). Same across the securities account and "
            "its currency wallets. '' to ungroup."
        ),
    )
    initial_balance: Optional[float] = Field(
        default=None, allow_inf_nan=False, description="Balance as of initial_balance_date."
    )
    initial_balance_date: Optional[str] = Field(
        default=None,
        description=(
            "YYYY-MM-DD tracking start — the Starting Balance transaction's date. Legs "
            "before it are ignored."
        ),
    )
    credit_limit: Optional[float] = Field(
        default=None,
        description=(
            "The card's credit line, a positive number in the account's own currency. "
            "Reference metadata shown in the account editor — nothing computes utilization "
            "off it and it neither caps spending nor affects balances or net worth."
        ),
    )
    last_four: Optional[str] = Field(
        default=None,
        description=(
            "Last 4 digits of the card/account number. Load-bearing for statement import: "
            "an uploaded statement's detected last-4 is matched against this to pick the "
            "destination account, so a wrong value silently routes rows to the wrong place."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description=(
            "Free-text memo about the account itself (e.g. 'joint with Alex, rent and "
            "bills'), shown in the account editor. REPLACES the existing note wholesale "
            "rather than appending; '' clears it. Unrelated to transaction memos."
        ),
    )
    archived: Optional[bool] = Field(
        default=None, description="Set true to archive (hide) or false to restore."
    )
    external_ids: Optional[List[ExternalIdInput]] = Field(
        default=None,
        description=(
            "REPLACES the account's whole list of provider links — omit to leave unchanged, "
            "pass [] to unlink everything, or the full desired list otherwise. Each "
            "(source, ref) pair may be linked to only one account; linking one already held "
            "elsewhere raises, naming the holder."
        ),
    )
    apr: Optional[float] = Field(
        default=None, description="Interest rate % — credit and loan accounts."
    )
    principal: Optional[float] = Field(
        default=None, description="Original loan principal (metadata)."
    )
    payoff_date: Optional[str] = Field(default=None, description="Target payoff date YYYY-MM-DD.")
    min_payment: Optional[float] = Field(
        default=None, description="Minimum periodic payment (metadata)."
    )
    apy: Optional[float] = Field(
        default=None, description="Yield % — cash and fixed_deposit accounts."
    )
    statement_close_day: Optional[int] = Field(
        default=None, description="Credit: statement close day of month (1–31)."
    )
    payment_due_day: Optional[int] = Field(
        default=None, description="Credit: payment due day of month (1–31)."
    )


class UpdateAccountsInput(ToolInput):
    """Schema-of-record for ``update_accounts`` (see ``schema_from_model``)."""

    updates: List[UpdateAccountItem] = Field(
        min_length=1, description="The edits to apply. One item is the common case."
    )


def _apply_one_account_update(item: dict, rows: list[dict]) -> dict:
    """Apply ONE account's patch — the single-item path of ``update_accounts``.

    Returns exactly one of ``{"account": {...}}`` / ``{"account": {...}, "no_op": True}`` /
    ``{"not_found": ref}`` / ``{"ambiguous": message}`` / ``{"error": message}`` — never
    raises, so a batch loop can classify every outcome without a try/except. Account type is
    creation-only and rejected explicitly, including legacy bare tool calls.
    """
    from backend.core.errors import OpenNetWorthError, ValidationError
    from backend.services import accounts as accounts_service

    ref = str(item.get("account_id") or "").strip()
    if not ref:
        return {"error": "account_id is required"}
    try:
        accounts_service.assert_no_server_owned_fields(item)
        accounts_service.assert_no_dead_account_fields(item)
        accounts_service.assert_no_account_color(item)
    except ValidationError as exc:
        return {"error": exc.message or str(exc)}
    account_id, amb = _resolve_account_ref_for_write(ref, rows)
    if amb:
        return {"ambiguous": amb}
    existing = svc.accounts.find_by_id(account_id) if account_id else None
    if not existing:
        return {"not_found": ref}
    if "type" in item:
        return {"error": accounts_service.ACCOUNT_TYPE_IMMUTABLE_MESSAGE}
    if "currency" in item:
        return {"error": accounts_service.ACCOUNT_CURRENCY_IMMUTABLE_MESSAGE}

    changes = {k: v for k, v in item.items() if k in _ACCOUNT_UPDATE_FIELDS and v is not None}
    opening_touched = "initial_balance" in item or "initial_balance_date" in item

    # An id-only item (no changeable field, and the opening-balance flag — the tool's ONE
    # other effect besides the generic patch — wasn't touched either) writes nothing. Report
    # it as a no-op instead of the pre-fix plain success echoing the unchanged row, which was
    # byte-indistinguishable from a real edit (the update_transactions incident, 2026-07-21).
    if not changes and not opening_touched:
        return {"account": svc.account_with_balance(existing), "no_op": True}

    before = _account_write_snapshot_or_none(existing)
    try:
        if opening_touched:
            # opening_for_edit, not opening_amount/opening_date: an account whose opening
            # pointer dangles must still be REPAIRABLE from here, and the loud read would
            # raise before the write choke could re-mint the row.
            cur_amount, cur_date = svc.opening_for_edit(existing)
            amount = (
                float(item["initial_balance"])
                if item.get("initial_balance") is not None
                else cur_amount
            )
            as_of = item.get("initial_balance_date") or cur_date
            svc.set_account_opening(account_id, amount, as_of)
        acc = (
            # Routed through accounts_service.update_account, not the bare store, so the
            # external_ids uniqueness guard and link-time fill apply here too (RFC
            # connectors-as-data-sources §D — UI/Lucius parity with the REST PATCH route).
            accounts_service.update_account(account_id, changes)
            if changes
            else svc.accounts.find_by_id(account_id)
        )
    except OpenNetWorthError as exc:
        # Report one item's invalid patch without aborting already-applied batch edits.
        return {"error": exc.message or str(exc)}
    hydrated = svc.account_with_balance(acc)
    # A write that was SUBMITTED but did not LAND still reported ok:true with the row echoed
    # back, so a caller re-sending the same edit could loop forever believing each attempt had
    # worked (2026-07-28: initial_balance_date was unwritable for exactly this reason, and the
    # model burned 15 turns on it). Diff what the tool can actually move, before vs after.
    if _account_write_snapshot(hydrated) == before:
        return {"account": hydrated, "no_op": True, "unchanged": True}
    out = {"account": hydrated}
    return out


@tool(
    "update_accounts",
    description=(
        "Edit one or many existing accounts — name, institution (custodian "
        "grouping key), initial_balance, initial_balance_date, credit_limit, "
        "last_four, note, or archived. Pass ONE item in `updates` for a single edit "
        "(the common case), or many — one per account, each with its OWN changes — to edit "
        "several accounts in one call: repeat the same fields in every item for the SAME "
        "change on every account, or vary them for a different change per account. Only the "
        "fields supplied on an item change — everything else on that account is left as-is. "
        "Call list_accounts first to get account IDs.\n\n"
        "Each account is resolved and updated independently — a name that doesn't resolve "
        "or is ambiguous is reported per-item in `not_found`/`ambiguous`, never silently "
        "skipped, and never aborts the rest of the batch. An item with ONLY account_id and "
        "no other field (and no initial_balance/initial_balance_date change) changes "
        "NOTHING — reported as a no-op, never silent success. Account type and currency are "
        "set only when creating an account and cannot be changed here."
    ),
    input_schema=schema_of(UpdateAccountsInput, tool_name="update_accounts"),
    mutates=("accounts",),
)
async def update_accounts(input_data: dict):
    raw_updates = input_data.get("updates")
    if raw_updates is not None:
        items = raw_updates if isinstance(raw_updates, list) else [raw_updates]
    else:
        # Bare legacy `update_account` shape: fields directly at the top level, no wrapper —
        # the pre-rename single-account tool's only shape, and still the trivial way to
        # update one account (no array ceremony).
        bare = dict(input_data)
        items = [bare] if bare.get("account_id") else []

    if not items:
        return json.dumps({"ok": False, "error": "updates (or account_id) is required"}), None

    rows = svc.accounts.all()

    # A lone item takes the single-account path — the exact pre-rename response shape.
    if len(items) == 1:
        result = _apply_one_account_update(items[0], rows)
        if "account" in result:
            if result.get("no_op"):
                return (
                    json.dumps(
                        no_op_write_result(
                            {"account": result["account"]},
                            message=_UNCHANGED_WRITE_MESSAGE if result.get("unchanged") else None,
                        ),
                        ensure_ascii=False,
                    ),
                    None,
                )
            body: dict = {"ok": True, "account": result["account"]}
            return json.dumps(body, ensure_ascii=False), None
        if "ambiguous" in result:
            return json.dumps({"ok": False, "error": result["ambiguous"]}, ensure_ascii=False), None
        # Surface the item's OWN reason (a rejected server-owned field says which one);
        # only a genuinely unresolved ref falls back to the generic hint.
        error = result.get("error") or "account not found — call list_accounts to get valid IDs"
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False), None

    updated: list[dict] = []
    no_ops: list[dict] = []
    not_found: list[str] = []
    ambiguous: list[str] = []
    errors: list[str] = []
    for item in items:
        result = _apply_one_account_update(item, rows)
        if "account" in result:
            (no_ops if result.get("no_op") else updated).append(result["account"])
        elif "ambiguous" in result:
            ambiguous.append(result["ambiguous"])
        elif "not_found" in result:
            not_found.append(result["not_found"])
        else:  # a blank account_id / a rejected field — surfaced, never aborts the batch
            errors.append(result.get("error") or "")

    result_body: dict = {
        "ok": True,
        "updated": updated,
        "count": len(updated),
        "not_found": not_found,
        "ambiguous": ambiguous,
    }
    if errors:
        result_body["errors"] = errors
    if no_ops:
        result_body["no_op"] = no_ops
        result_body["no_op_count"] = len(no_ops)
        result_body["note"] = (
            f"{len(no_ops)} of {len(items)} account(s) carried no changeable field (and no "
            "opening-balance change) and were left as-is — see each unchanged row under "
            "`no_op`."
        )
    return json.dumps(result_body, ensure_ascii=False), None


class DescribeAccountInput(ToolInput):
    """Schema-of-record for ``describe_account`` (see ``schema_from_model``)."""

    text: str = Field(description="The user's free-text description of the account.")


@tool(
    "describe_account",
    description=(
        "Turn a plain-language description of ONE account into a structured, EDITABLE account-form "
        "draft (type, name, currency, initial_balance, institution, last_four, note, "
        "and credit/loan fields when implied). Use this when the user describes an account in words "
        "('my Meridian Bank savings account, about 12k SGD') and you want to pre-fill the Add-account form for "
        "them to confirm. This NEVER creates the account — it only proposes fields; call create_account "
        "after the user confirms. Interactive only (needs the AI provider)."
    ),
    input_schema=schema_of(DescribeAccountInput, tool_name="describe_account"),
)
async def describe_account(input_data: dict):
    from backend.core.errors import OpenNetWorthError
    from backend.services import account_describe

    text = input_data.get("text", "")
    try:
        draft = await account_describe.describe_account(text)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}, ensure_ascii=False), None
    return json.dumps({"ok": True, "draft": draft}, ensure_ascii=False), None


class DeleteAccountsInput(ToolInput):
    """Schema-of-record for ``delete_accounts`` (see ``schema_from_model``)."""

    account_ids: List[str] = Field(
        min_length=1,
        description=(
            "Account IDs or names from list_accounts. Pass one to delete a single account, "
            "or several to delete many at once. Each account is deleted independently — one "
            "still referenced by live transactions is refused on its own (reported in "
            "`refused` with why) without blocking the rest of the batch."
        ),
    )


@tool(
    "delete_accounts",
    description=(
        "Permanently delete one or more accounts. Deletes outright when the account has no "
        "transactions at all, or when its ONLY transaction is its own opening (Starting "
        "Balance) balance — that isn't history worth keeping once the account itself is gone. "
        "Refused per-account (real transaction history, or a loan/position still linked to "
        "it) otherwise — archive instead (update_accounts with archived=true) to keep the "
        "history intact; this tool never force-cascades a delete across real transaction "
        "history — that escalation is a deliberate, explicit two-step confirmation in the app "
        "UI only, never available unattended. Call list_accounts first to get account IDs.\n\n"
        "Each account is deleted independently — a refusal (still referenced) or an "
        "unresolved name never aborts the batch or silently drops the rest; every outcome "
        "is reported per item in `deleted`/`refused`/`not_found`/`ambiguous`."
    ),
    input_schema=schema_of(DeleteAccountsInput, tool_name="delete_accounts"),
    mutates=("accounts",),
)
async def delete_accounts(input_data: dict):
    from backend.core.errors import ConflictError

    refs = _ids_from(input_data, "account_ids", "account_id")
    if not refs:
        return json.dumps({"ok": False, "error": "account_ids is required"}), None

    rows = svc.accounts.all()
    deleted: list[str] = []
    not_found: list[str] = []
    ambiguous: list[str] = []
    refused: list[dict] = []
    for ref in refs:
        account_id, amb = _resolve_account_ref_for_write(ref, rows)
        if amb:
            ambiguous.append(amb)
            continue
        if not account_id:
            not_found.append(ref)
            continue
        try:
            was_deleted = svc.delete_account(account_id)
        except ConflictError as exc:
            refused.append({"account_id": account_id, "reason": exc.message or str(exc)})
            continue
        if was_deleted:
            deleted.append(account_id)
        else:
            not_found.append(ref)

    # A lone id takes the single-account path — the exact pre-rename response shape.
    if len(refs) == 1:
        if deleted:
            return json.dumps({"ok": True, "deleted": deleted[0]}, ensure_ascii=False), None
        if refused:
            return json.dumps(
                {"ok": False, "error": refused[0]["reason"]}, ensure_ascii=False
            ), None
        error = (
            ambiguous[0] if ambiguous else "account not found — call list_accounts to get valid IDs"
        )
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False), None

    return json.dumps(
        {
            "ok": True,
            "deleted": deleted,
            "count": len(deleted),
            "refused": refused,
            "not_found": not_found,
            "ambiguous": ambiguous,
        },
        ensure_ascii=False,
    ), None


class GetNetWorthInput(ToolInput):
    """Schema-of-record for ``get_net_worth`` (see ``schema_from_model``)."""

    as_of: Optional[str] = Field(
        default=None,
        description=(
            "A PAST date (YYYY-MM-DD) for net worth as it stood at that day's end — e.g. the "
            "last day of a reported week or month. Omit for today."
        ),
    )


@tool(
    "get_net_worth",
    description=(
        "Get the user's total net worth in the base currency: account balances (assets "
        "positive, liabilities negative) plus market-valued positions (equities, crypto, "
        "commodities, property). Returns net_worth plus a breakdown: account_classes "
        "(cash/fixed_deposit/settlement_cash/retirement/receivable), liability_classes "
        "(credit/loan), liabilities (each debt: id/name/type/apr/balance_base), "
        "retirement_base (cash plus vested holdings) and market_positions. "
        "net_worth_excl_retirement is the value outside retirement accounts, not a "
        "spendable-cash measure; use net_worth for 'what am I worth?'. "
        "UNVESTED EQUITY (vesting_state=unvested) is EXCLUDED from net_worth/market_positions "
        "— not yet owned; reported separately as unvested_base. "
        "With `as_of` it returns that day's net_worth and class/liability totals."
    ),
    input_schema=schema_of(GetNetWorthInput, tool_name="get_net_worth"),
)
async def get_net_worth(input_data: dict):
    if input_data.get("as_of"):
        out = await asyncio.to_thread(svc.net_worth_on, input_data["as_of"])
        return json.dumps(out, ensure_ascii=False), None
    return json.dumps(svc.net_worth(), ensure_ascii=False), None


class ReconcileItem(ToolInput):
    """One account's target in a batch ``reconcile_accounts`` call."""

    account_id: str = Field(description="Account ID or name from list_accounts.")
    real_balance: float = Field(
        description=(
            "The true current balance in the account's native currency (negative for a "
            "card you owe on)."
        )
    )
    against: Optional[Literal["cleared", "working"]] = Field(
        default=None,
        description="Overrides the call's own `against` for this account only.",
    )
    date: Optional[str] = Field(
        default=None, description="Overrides the call's own `date` for this account only."
    )


class ReconcileAccountsInput(ToolInput):
    """Schema-of-record for ``reconcile_accounts`` (see ``schema_from_model``)."""

    account_id: Optional[str] = Field(
        default=None,
        description="Single-account form: account ID or name from list_accounts.",
    )
    real_balance: Optional[float] = Field(
        default=None,
        description=(
            "Single-account form: the true current balance in the account's native "
            "currency (negative for a card you owe on)."
        ),
    )
    accounts: Optional[List[ReconcileItem]] = Field(
        default=None,
        description=(
            "BATCH form: reconcile several accounts in ONE call, each to its own "
            "real_balance — e.g. checking to 500, savings to 1200, card to -300. When set, "
            "the top-level account_id/real_balance are ignored; against/date still apply "
            "as the shared default for any item that doesn't set its own."
        ),
    )
    against: Optional[Literal["cleared", "working"]] = Field(
        default=None,
        description=(
            "Which balance to diff the asserted figure against: 'cleared' (default — the "
            "reconcile-to-cleared flow) or 'working' (the full computed balance)."
        ),
    )
    date: Optional[str] = Field(
        default=None, description="YYYY-MM-DD for the adjustment. Defaults to today."
    )
    dry_run: Optional[bool] = Field(
        default=None,
        description=(
            "If true, compute and return the delta(s) without writing anything. Always use "
            "this first to show the user what will change before applying."
        ),
    )


def _reconcile_one(
    account_id: str, real_balance: float, against: str, date: Optional[str], dry_run: bool
) -> dict:
    """Compute (dry_run) or apply one account's reconciliation. Returns the per-account
    payload with NO 'ok'/'account_id' wrapper — callers shape that, since the single-item
    legacy response and the batch item response disagree on whether account_id belongs
    in it (see the call sites)."""
    acct = svc.accounts.find_by_id(account_id)
    txns = svc.transactions.all()
    cutoff = svc.opening_date(acct, txns)
    current = (
        svc.cleared_account_balance(acct, txns, cutoff)
        if against == "cleared"
        else svc.account_balance(acct, txns, cutoff)
    )
    delta = round(real_balance - current, 2)
    if dry_run:
        return {
            "dry_run": True,
            "against": against,
            "current_balance": current,
            "real_balance": real_balance,
            "delta": delta,
            "note": (
                "No changes written. "
                + (
                    f"An adjustment of {delta:+.2f} {acct.get('currency', '')} would be created."
                    if delta != 0
                    else "Balances match — no adjustment needed."
                )
            ),
        }
    adjustment = svc.reconcile(account_id, real_balance, date, against=against)
    acct2 = svc.accounts.find_by_id(account_id)
    txns2 = svc.transactions.all()
    cutoff2 = svc.opening_date(acct2, txns2)
    return {
        "adjustment": adjustment,
        "balance": svc.account_balance(acct2, txns2, cutoff2),
        "cleared_balance": svc.cleared_account_balance(acct2, txns2, cutoff2),
    }


@tool(
    "reconcile_accounts",
    description=(
        "Reconcile one or more accounts to their real-world balance (cleared/reconcile flow). "
        "By default the asserted balance is diffed against the account's CLEARED balance (the "
        "sum of transactions the user has matched against the bank); if it differs, an "
        "auto-cleared `adjustment` is created so the cleared balance snaps to truth, and the "
        "cleared transactions are LOCKED (marked reconciled). Pass against='working' to "
        "instead true up the full computed balance (legacy statement drift). Use when the "
        "user says 'my card actually shows X' or finishes ticking off cleared items.\n\n"
        "BATCH: pass `accounts` (a list of {account_id, real_balance[, against][, date]}) to "
        "reconcile several accounts in ONE call — each to its OWN asserted balance. Every "
        "account is resolved and reconciled independently; an unresolved/ambiguous name is "
        "reported per-item in `not_found`/`ambiguous` and never blocks the rest.\n\n"
        "ALWAYS call with dry_run=true first. Show the user the computed delta(s) and ask for "
        "explicit confirmation before calling again with dry_run=false (or omitting it) to "
        "apply. Never apply without user approval."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        ReconcileAccountsInput, array="accounts", tool_name="reconcile_accounts"
    ),
    mutates=("accounts",),
)
async def reconcile_accounts(input_data: dict):
    batch_items = input_data.get("accounts")
    is_batch = bool(batch_items)
    if not is_batch:
        account_id_in = input_data.get("account_id")
        if not account_id_in or input_data.get("real_balance") is None:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "account_id and real_balance are required for a single reconcile "
                        "— or pass a list via `accounts` to reconcile several at once."
                    ),
                }
            ), None
        batch_items = [
            {
                "account_id": account_id_in,
                "real_balance": input_data["real_balance"],
                "against": input_data.get("against"),
                "date": input_data.get("date"),
            }
        ]

    dry_run = bool(input_data.get("dry_run", False))
    default_against = input_data.get("against") or "cleared"
    default_date = input_data.get("date")

    rows = svc.accounts.all()
    results: list[dict] = []
    not_found: list[str] = []
    ambiguous: list[str] = []
    errors: list[dict] = []
    for item in batch_items:
        ref = str(item.get("account_id") or "").strip()
        account_id, amb = _resolve_account_ref_for_write(ref, rows)
        if amb:
            ambiguous.append(amb)
            continue
        if not account_id or not svc.accounts.find_by_id(account_id):
            not_found.append(ref)
            continue
        if item.get("real_balance") is None:
            errors.append({"account_id": account_id, "reason": "real_balance is required"})
            continue
        against = item.get("against") or default_against
        date = item.get("date") or default_date
        payload = _reconcile_one(account_id, float(item["real_balance"]), against, date, dry_run)
        results.append({"account_id": account_id, **payload})

    if not is_batch:
        if results:
            payload = dict(results[0])
            if not dry_run:
                payload.pop("account_id", None)
            return json.dumps({"ok": True, **payload}, ensure_ascii=False), None
        if errors:
            return json.dumps({"ok": False, "error": errors[0]["reason"]}, ensure_ascii=False), None
        error = (
            ambiguous[0] if ambiguous else "account not found — call list_accounts to get valid IDs"
        )
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False), None

    return json.dumps(
        {
            "ok": True,
            "dry_run": dry_run,
            "results": results,
            "count": len(results),
            "not_found": not_found,
            "ambiguous": ambiguous,
            "errors": errors,
        },
        ensure_ascii=False,
    ), None


class SetTransactionsClearedInput(ToolInput):
    """Schema-of-record for ``set_transactions_cleared`` (see ``schema_from_model``)."""

    txn_ids: List[str] = Field(
        min_length=1,
        description=(
            "Transaction ids (from get_transactions) to mark cleared/uncleared, all with the "
            "SAME flag — e.g. clearing every matched row after a statement reconciliation. "
            "Pass one id to set a single transaction. Each is set independently; one refusal "
            "(an account's Starting Balance row can't be uncleared) never blocks the rest."
        ),
    )
    cleared: bool = Field(description="True to mark cleared, False to mark uncleared.")


@tool(
    "set_transactions_cleared",
    description=(
        "Mark one or more transactions cleared or uncleared (cleared/reconcile flow) — i.e. "
        "whether the user has matched them against the bank. This NEVER changes the account's "
        "working balance; it only moves which legs the cleared balance folds. Use "
        "get_transactions to find the transaction id(s). This is an immediate write — confirm "
        "with the user first. An account's Starting Balance row (source='opening') cannot be "
        "UNcleared — it must stay cleared to reconcile.\n\n"
        "Pass several txn_ids to set the same cleared flag on many transactions in one call. "
        "Each is set independently — a Starting-Balance refusal is reported per item in "
        "`refused` and never blocks the rest."
    ),
    input_schema=schema_of(SetTransactionsClearedInput, tool_name="set_transactions_cleared"),
    mutates=("ledger",),
)
async def set_transactions_cleared(input_data: dict):
    from backend.core.errors import OpenNetWorthError

    ids = _ids_from(input_data, "txn_ids", "txn_id")
    if not ids:
        return json.dumps({"ok": False, "error": "txn_ids is required"}), None

    cleared = bool(input_data.get("cleared", False))
    updated: list[dict] = []
    not_found: list[str] = []
    refused: list[dict] = []
    for txn_id in ids:
        try:
            # ConflictError = the Starting Balance row refusing to be uncleared; surface it
            # as a readable per-item outcome rather than letting it escape the handler.
            result = svc.set_transaction_cleared(txn_id, cleared)
        except OpenNetWorthError as e:
            refused.append({"txn_id": txn_id, "reason": e.message or str(e)})
            continue
        if result is None:
            not_found.append(txn_id)
            continue
        updated.append(result)

    # A lone id takes the single-transaction path — the exact pre-rename response shape.
    if len(ids) == 1:
        if updated:
            return json.dumps({"ok": True, "transaction": updated[0]}, ensure_ascii=False), None
        if refused:
            return json.dumps(
                {"ok": False, "error": refused[0]["reason"]}, ensure_ascii=False
            ), None
        return json.dumps(
            {"ok": False, "error": "transaction not found — call get_transactions for valid ids"}
        ), None

    return json.dumps(
        {
            "ok": True,
            "updated": updated,
            "count": len(updated),
            "refused": refused,
            "not_found": not_found,
        },
        ensure_ascii=False,
    ), None
