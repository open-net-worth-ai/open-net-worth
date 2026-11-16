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
from backend.services.bank_data_sync import card_payments, pending, relink, reviews

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
    # Rows of an account linked again under a new id, matched to the bookings they already are.
    relinked: int = 0
    # Pending rows: booked uncleared, settled (now cleared), dropped by the bank (removed).
    pending: int = 0
    settled: int = 0
    pending_removed: int = 0
    # Card payments booked as ONE transfer (card_payments.py): new rows, and rows already in the
    # book promoted to it (``promoted`` holds their before-images, for Undo).
    card_payments: int = 0
    promoted: list[dict] = field(default_factory=list)
    # Ids of the pending bookings this run took off the book, and the bank's own view of every
    # row it booked or folded into a booking, by ref: what Undo needs (undo.py).
    removed_ids: list[str] = field(default_factory=list)
    removed_entries: dict[str, dict] = field(default_factory=dict)
    booked_views: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _ref(row: dict) -> str:
    refs = row.get("external_refs") or []
    return str(refs[0].get("ref") or "") if refs and isinstance(refs[0], dict) else ""


def _refs_of(txn: dict) -> list[str]:
    return [str((e or {}).get("ref") or "") for e in txn.get("external_refs") or []]


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
        # Cleared means settled at the bank: a posted row is, a pending one isn't yet
        # (pending.py clears it when the bank settles it).
        "cleared": not row.get("pending"),
        # Booked for review, as YNAB books a bank's rows unapproved (owner, 2026-11-14): a feed
        # is otherwise a trusted writer (core.txn_sources), and nothing asked the user to look.
        # Settling a pending row later only clears it and moves its date — an approval stands.
        "needs_review": True,
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


def _adopt_ref(provider: str, row: dict, ref: str, known: dict) -> None:
    """A row found booked only under an alias (an earlier id: GoCardless's bank id before its
    own came first) gets its current ref on that booking, so later reads key on the stable id
    and a bank id that changes afterwards can't book it again."""
    txn_id = row.get("booked_txn_id")
    if not txn_id or (provider, ref) in known or row.get("_relinked"):
        return
    try:
        ledger_service.attach_external_refs(
            txn_id, row.get("external_refs") or [], reason="bank_data_sync: stable id"
        )
        known[(provider, ref)] = txn_id
    except Exception:  # noqa: BLE001 — the row is booked either way; the next run tries again
        logger.exception("bank_data_sync: %s could not file %s under its stable id", provider, ref)


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


def book(
    provider: str,
    rows: list[dict],
    *,
    run_id: Optional[str],
    batch_id: str,
    read: Optional[dict[str, str]] = None,
    relinked: Optional[set[str]] = None,
) -> Booked:
    """Book ``rows`` (the opted-in accounts' window: posted and pending, booked or not) in one
    ledger write. Every write here — the batch, a pending row settled or dropped, a
    pending→posted link — runs under the audit surface ``bank_data_sync``. ``read``: account id
    → the window start it was read from, for dropping a pending row the bank no longer lists.
    ``relinked``: accounts read from a new bank id this run, whose rows are first matched to the
    bookings they already are (``relink.carry``); a row the bank re-keyed on an account read
    whole is matched the same way (``relink.reissued``). Raises only when that write failed
    (then nothing was booked)."""
    from backend.core import audit_context

    token = audit_context.set_audit_scope("system", SURFACE, note=f"run:{run_id}")
    try:
        matched = relink.carry(provider, rows, relinked or set())
        matched += relink.reissued(provider, rows, read or {}, relinked or set())
        out = _book(provider, rows, run_id=run_id, batch_id=batch_id)
        out.relinked = matched
        if read:
            entries = pending.tracked(provider)
            seen: dict[str, set[str]] = {a: set() for a in read}
            for row in rows:
                if row.get("pending") and row.get("linked_account_id") in seen:
                    seen[row["linked_account_id"]].add(_ref(row))
            gone = pending.drop_vanished(entries, seen, read)
            out.pending_removed = len(gone)
            out.removed_ids.extend(g["entry"]["txn_id"] for g in gone)
            out.removed_entries.update({g["ref"]: g["entry"] for g in gone})
            pending.save(provider, entries)
        return out
    finally:
        audit_context.clear_audit_scope(token)


def _book(provider: str, rows: list[dict], *, run_id: Optional[str], batch_id: str) -> Booked:
    out = Booked()
    held = reviews.held_refs(provider)
    known = ledger_service.known_external_refs(provider)
    entries = pending.tracked(provider)
    pending_rows: dict[str, dict] = {}
    candidates: list[tuple[dict, dict]] = []
    for row in rows:
        ref = _ref(row)
        if not ref:
            continue
        _adopt_ref(provider, row, ref, known)
        if row.get("pending"):
            # Booked once, uncleared; the bank's pending list is read again every run.
            if row.get("booked_txn_id") or (provider, ref) in known or ref in held:
                continue
            pending_rows[ref] = row
        else:
            if row.get("_relinked"):
                continue  # the booking it already is carries its new ref (relink.carry)
            settled = pending.settle(provider, ref, row, entries)
            if settled == "settled":
                out.settled += 1
                continue
            if row.get("booked_txn_id") and settled == "none":
                continue  # already booked (the window is read whole, booked rows included)
            if ref in held or row.get("supersedes_ref") in held:
                out.held += 1
                continue
            if _settle_superseding(provider, row, known, run_id, out):
                continue
        draft = draft_for(row)
        if draft is None:
            out.errors.append(f"{row.get('date')}: a row with no account or amount was skipped")
            continue
        draft.update(row.pop("_carry", None) or {})
        candidates.append((row, draft))
    pending.save(provider, entries)  # what settled above is settled, whatever follows
    candidates, errors = _with_categories(candidates)
    out.errors.extend(errors)
    views = {_ref(row): _row_view(row) for row, _ in candidates}
    types = {a["id"]: a.get("type") for a in ledger_service.accounts.all()}
    candidates = card_payments.pair_within(candidates, types)
    candidates, out.promoted = card_payments.promote_into_book(provider, candidates, types)
    out.card_payments = len(out.promoted)
    out.booked_views = {r: views[r] for s in out.promoted for r in s["refs"] if r in views}
    if not candidates:
        return out
    created, skipped = ledger_service.bulk_create_transactions(
        [d for _, d in candidates], batch_id=batch_id, dedup_by_external_ref=True
    )
    out.created = created
    out.booked_views |= {r: views[r] for t in created for r in _refs_of(t) if r in views}
    out.card_payments += sum(1 for t in created if t.get("kind") == "transfer")
    pending.remember(entries, created, pending_rows)
    out.pending = sum(
        1
        for t in created
        if any(
            str((r or {}).get("ref") or "") in pending_rows for r in t.get("external_refs") or []
        )
    )
    pending.save(provider, entries)
    try:  # booked and committed: nothing after this may report it as unbooked
        _classify_skipped(provider, skipped, candidates, run_id, out)
    except Exception:  # noqa: BLE001
        logger.exception("bank_data_sync: %s skipped rows could not be sorted", provider)
        out.errors.append("Some rows the ledger refused couldn't be added to the review list.")
    return out


def align_first_imports(provider: str, account_ids: set[str]) -> tuple[set[str], list[str]]:
    """Move each first-imported cash/card account's start back to its oldest row from
    ``provider``, rewinding the opening by what that exposes (``tracking_start.compute_align``,
    the Preserve rewind), so the history counts and today's balance doesn't move. ONE write
    (``set_account_opening``) per account. Read from the ledger, so a run that stopped after
    booking aligns on the next one. Returns ``(settled account ids, warnings)``; an account that
    can't be aligned (its type) is settled with a warning, never retried forever. One that
    failed is left out, so only IT is retried.

    Rows dated the start day itself are left as they are: whether the opening already holds
    them depends on WHEN that balance was read, which no date can tell. An account that held
    nothing but its Starting Balance is then set to the bank's own settled figure from this same
    read (``balances.record``); any other keeps its opening and offers "Use the bank's balance"
    (ADR 2026-11-13, Amendment 11)."""
    txns = ledger_service.transactions.all()
    settled: set[str] = set()
    warnings: list[str] = []
    for account_id in sorted(account_ids):
        try:
            _align_one(provider, account_id, txns, warnings)
        except Exception:  # noqa: BLE001 — this account is retried next run, the others stand
            logger.exception("bank_data_sync: %s alignment of %s failed", provider, account_id)
            continue
        settled.add(account_id)
    return settled, warnings


def _align_one(provider: str, account_id: str, txns: list[dict], warnings: list[str]) -> None:
    """One account of ``align_first_imports``: at most one opening write."""
    from backend.core.errors import ValidationError
    from backend.core.txn_sources import ext_ref_pairs
    from backend.services import tracking_start

    account = ledger_service.accounts.find_by_id(account_id)
    if account is None:
        return
    mine = [
        t
        for t in txns
        if not t.get("deleted")
        and account_id in (t.get("from_account_id"), t.get("to_account_id"))
        and any(pair[0] == provider for pair in ext_ref_pairs(t))
    ]
    start = ledger_service.opening_date(account, txns)
    if not mine or not start:
        return
    oldest = min(str(t.get("date"))[:10] for t in mine)
    if oldest >= start:
        return
    try:
        tracking_start.assert_alignable(account)
    except ValidationError as exc:
        name = account.get("name") or "An account"
        warnings.append(f"{name}: history before {start} is booked but not counted — {exc}")
        return
    plan = tracking_start.compute_align(
        account, oldest, txns, start, ledger_service.opening_amount(account, txns)
    )
    ledger_service.set_account_opening(account_id, round(plan["new_opening"], 2), oldest)
