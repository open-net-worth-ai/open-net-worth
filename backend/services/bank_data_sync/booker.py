"""Book a provider's fetched rows into the ledger with no model in the loop (ADR 2026-11-13).

Provider-agnostic: every row is already in the common shape ``connector_fetch.fetch_all``
returns (``external_refs``, ``date``, signed ``amount``, ``currency``, ``description``, ``payee``,
``memo``, ``original_amount``/``original_currency``, ``category_hint``, ``linked_account_id``,
``supersedes_ref``). Nothing here knows a provider.

All new rows of a run go through ONE ``ledger.bulk_create_transactions`` call — one DB
transaction, so a failure books nothing — with ``dedup_by_external_ref`` (the ref floor inside
the write). A row the user deleted comes back ``previously_deleted`` and is never forced. A row the
duplicate guard matches to one already in the book becomes a ``duplicate`` review item.

Categories, without AI: the user's Automations and payee defaults (the choke's own precedence,
read through the same resolver a preview uses); then the bank's MCC hint only when it names a
category that exists. A category is never created; anything else books Uncategorized."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.core.errors import OpenNetWorthError
from backend.services import ledger as ledger_service
from backend.services.bank_data_sync import reviews

logger = logging.getLogger(__name__)

SURFACE = "bank_data_sync"
_AMOUNT_TOL = 0.005


@dataclass
class Booked:
    created: list[dict] = field(default_factory=list)
    already_booked: int = 0
    deleted_before: int = 0
    to_review: int = 0
    held: int = 0
    linked_refs: int = 0
    errors: list[str] = field(default_factory=list)


def _ref(row: dict) -> str:
    refs = row.get("external_refs") or []
    return str(refs[0].get("ref") or "") if refs and isinstance(refs[0], dict) else ""


def draft_for(row: dict) -> Optional[dict]:
    """One fetched row → a ``bulk_create_transactions`` draft (the same fields the
    ``book-transactions`` skill passes to ``add_transactions``). None for a row with no linked
    account or no amount."""
    account_id, amount = row.get("linked_account_id"), row.get("amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    if not account_id or not amount or not row.get("date"):
        return None
    leg = "to" if amount > 0 else "from"
    draft = {
        "kind": "income" if amount > 0 else "expense",
        "date": row["date"],
        "description": row.get("description") or "",
        "external_refs": [dict(e) for e in row.get("external_refs") or []],
        "source": "feed",
        f"{leg}_account_id": account_id,
        f"{leg}_amount": abs(amount),
        f"{leg}_currency": row.get("currency"),
    }
    for key in ("payee", "memo", "original_amount", "original_currency"):
        if row.get(key) not in (None, ""):
            draft[key] = row[key]
    return draft


def _with_categories(drafts: list[tuple[dict, dict]]) -> tuple[list[tuple[dict, dict]], list[str]]:
    """Resolve each draft as the write would (Automations, payee default) WITHOUT writing; a row
    still blank gets its MCC hint if, and only if, that names an existing category. A draft the
    resolver refuses is dropped with its reason, so one bad row never sinks the batch."""
    from backend.services import categories as categories_service
    from backend.services.automations.transaction_events import active_transaction_transforms
    from backend.services.fx_cache import load_fx_cache

    rules = active_transaction_transforms()
    category_rows = categories_service.all_category_rows()
    account_rows = ledger_service.accounts.all()
    base = ledger_service.base_currency()
    rates = load_fx_cache()
    visible = [c for c in category_rows if not c.get("hidden")]
    kept, errors = [], []
    for row, draft in drafts:
        try:
            resolved, _meta = ledger_service.resolve_transaction_row(
                draft,
                mint_payee=False,
                automations=rules,
                category_rows=category_rows,
                rates=rates,
                account_rows=account_rows,
                base=base,
            )
        except OpenNetWorthError as exc:
            errors.append(f"{row.get('date')} {row.get('description') or ''}: {exc}"[:300])
            continue
        hint = row.get("category_hint")
        if not resolved.get("category") and isinstance(hint, str) and hint.strip():
            category = categories_service.resolve_category_ref(hint.strip(), rows=visible)
            if category:
                draft["category"] = category
        kept.append((row, draft))
    return kept, errors


def _settle_superseding(provider: str, row: dict, known: dict, run_id, out: Booked) -> bool:
    """A posted row that replaces a pending one already booked (Plaid's
    ``pending_transaction_id``). Same amount: link the new ref to the booked row (no money
    moves). Different: a ``posted`` review item. Either way it is not booked again. False when
    the row supersedes nothing booked."""
    old_ref = row.get("supersedes_ref")
    txn_id = known.get((provider, old_ref)) if old_ref else None
    if not txn_id:
        return False
    booked = ledger_service.transactions.find_by_id(txn_id) or {}
    if row.get("linked_account_id") not in (
        booked.get("from_account_id"),
        booked.get("to_account_id"),
    ):
        return False  # a pending ref on another account is not this row's — book it normally
    current = ledger_service.booked_row_snapshot(
        txn_id, prefer_account_id=row.get("linked_account_id")
    )
    booked_amount = abs(float((current or {}).get("amount") or 0))
    if abs(booked_amount - abs(float(row.get("amount") or 0))) <= _AMOUNT_TOL:
        ledger_service.attach_external_refs(
            txn_id, row.get("external_refs") or [], reason="bank_data_sync: pending posted"
        )
        out.linked_refs += 1
        return True
    reviews.record(
        provider,
        "posted",
        _ref(row),
        run_id=run_id,
        txn_id=txn_id,
        account_id=row.get("linked_account_id"),
        detail={"current": current, "after": _row_view(row), "row": _row_view(row)},
    )
    out.to_review += 1
    return True


def _row_view(row: dict) -> dict:
    keys = ("date", "amount", "currency", "description", "payee", "linked_account_id")
    view = {k: row.get(k) for k in keys}
    view["external_refs"] = row.get("external_refs") or []
    view["original_amount"] = row.get("original_amount")
    view["original_currency"] = row.get("original_currency")
    view["memo"] = row.get("memo")
    return view


def _classify_skipped(provider: str, skipped: list[dict], by_index: list, run_id, out: Booked):
    for item in skipped:
        if item.get("_previously_deleted"):
            out.deleted_before += 1
        elif item.get("_duplicate_of"):
            row = by_index[item["_batch_index"]][0]
            reviews.record(
                provider,
                "duplicate",
                _ref(row),
                run_id=run_id,
                txn_id=item["_duplicate_of"],
                account_id=row.get("linked_account_id"),
                detail={
                    "row": _row_view(row),
                    "reason": str(item.get("_duplicate_reason") or "")[:300],
                    "current": ledger_service.booked_row_snapshot(
                        item["_duplicate_of"], prefer_account_id=row.get("linked_account_id")
                    ),
                },
            )
            out.to_review += 1
        else:
            out.already_booked += 1


def book(provider: str, rows: list[dict], *, run_id: Optional[str], batch_id: str) -> Booked:
    """Book ``rows`` (unbooked, posted, opted-in — the runner's job) in one ledger write. Every
    write here — the one batch and any pending→posted link — runs under the audit surface
    ``bank_data_sync``. Raises only when that write failed (then nothing was booked)."""
    from backend.core import audit_context

    token = audit_context.set_audit_scope("system", SURFACE, note=f"run:{run_id}")
    try:
        return _book(provider, rows, run_id=run_id, batch_id=batch_id)
    finally:
        audit_context.clear_audit_scope(token)


def _book(provider: str, rows: list[dict], *, run_id: Optional[str], batch_id: str) -> Booked:
    out = Booked()
    held = reviews.held_refs(provider)
    known = ledger_service.known_external_refs(provider)
    candidates: list[tuple[dict, dict]] = []
    for row in rows:
        ref = _ref(row)
        if not ref or row.get("pending"):
            continue
        if ref in held:
            out.held += 1
            continue
        if _settle_superseding(provider, row, known, run_id, out):
            continue
        draft = draft_for(row)
        if draft is None:
            out.errors.append(f"{row.get('date')}: a row with no account or amount was skipped")
            continue
        candidates.append((row, draft))
    candidates, errors = _with_categories(candidates)
    out.errors.extend(errors)
    if not candidates:
        return out
    created, skipped = ledger_service.bulk_create_transactions(
        [d for _, d in candidates], batch_id=batch_id, dedup_by_external_ref=True
    )
    out.created = created
    try:  # booked and committed: nothing after this may report it as unbooked
        _classify_skipped(provider, skipped, candidates, run_id, out)
    except Exception:  # noqa: BLE001
        logger.exception("bank_data_sync: %s skipped rows could not be sorted", provider)
        out.errors.append("Some rows the ledger refused couldn't be added to the review list.")
    return out


def align_first_imports(provider: str, account_ids: set[str]) -> tuple[set[str], list[str]]:
    """Move each first-imported cash/card account's start back to its oldest row from
    ``provider``, so the history counts and today's balance doesn't move
    (``tracking_start.align_tracking_start``, which refuses anything that would move it). Read
    from the ledger, so a run that stopped after booking aligns on the next one. Returns
    ``(settled account ids, warnings)``; an account that can't be aligned (its type, a balance
    that wouldn't hold) is settled with a warning, never retried forever."""
    from backend.core.errors import ValidationError
    from backend.core.txn_sources import ext_ref_pairs
    from backend.services import tracking_start

    txns = ledger_service.transactions.all()
    settled: set[str] = set()
    warnings: list[str] = []
    for account_id in sorted(account_ids):
        account = ledger_service.accounts.find_by_id(account_id)
        if account is None:
            settled.add(account_id)
            continue
        dates = [
            str(t.get("date"))[:10]
            for t in txns
            if not t.get("deleted")
            and account_id in (t.get("from_account_id"), t.get("to_account_id"))
            and any(pair[0] == provider for pair in ext_ref_pairs(t))
        ]
        start = ledger_service.opening_date(account, txns)
        settled.add(account_id)
        if not dates or not start or min(dates) >= start:
            continue
        try:
            tracking_start.align_tracking_start(account_id, min(dates))
        except ValidationError as exc:
            name = account.get("name") or "An account"
            warnings.append(f"{name}: history before {start} is booked but not counted — {exc}")
    return settled, warnings
