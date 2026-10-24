"""Statement-import / batch / verification tools."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools._shared import svc
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of, schema_of_no_required

logger = logging.getLogger(__name__)

_24H_SECS = 86400


def _rows_left_out(
    batch_id: str, expected_net: float, actual_net: float, booked: int
) -> dict | None:
    """When the statement's OWN rows tie to its opening/closing and the booked rows differ
    from them only by rows deliberately not imported (a card payment left to the bank's
    side), say so — {rows, net} — instead of reporting a mismatch. None when that isn't the
    whole story (a genuinely missing or wrong row still reports a mismatch)."""
    from backend.db.statement_store import StatementStore

    try:
        record = next(
            (r for r in StatementStore().all() if batch_id in (r.get("batch_ids") or [])), None
        )
    except Exception:
        return None
    if not record:
        return None
    rows = [r for r in record.get("import_rows") or [] if not r.get("skip")]
    signed = [
        float(r["amount"]) * (1 if r.get("direction") == "IN" else -1)
        for r in rows
        if r.get("amount") is not None
    ]
    statement_net = round(sum(signed), 2)
    if abs(statement_net - expected_net) > 0.01:
        return None  # the statement itself doesn't tie: not a left-out-rows story
    gap = round(statement_net - actual_net, 2)
    if abs(gap) <= 0.01:
        return None
    left = len(signed) - booked
    if left <= 0:
        return None  # nothing was left out, so the gap is something else
    return {"net": gap, "rows": left}


def _resolve_account_or_error(account_id: str) -> tuple[str | None, dict | None]:
    """Resolve an account REF — an id or a NAME — to (id, None), or (None, error_dict).

    These two tools answer with an error DICT rather than raising, so the shared resolver's
    ambiguity ValidationError is caught and rendered in that shape. Resolution happens before
    anything downstream compares the value against a stored leg's ``from/to_account_id``,
    which are always ids."""
    from backend.core.errors import ValidationError, recovery_hint
    from backend.services import accounts as accounts_service

    if not (account_id or "").strip():
        return None, {
            "ok": False,
            "error": "account_id is required — pass the account's name or its id.",
        }
    if svc.accounts.find_by_id(account_id):  # id fast path through the shared store handle
        return account_id, None
    try:
        resolved = accounts_service.resolve_account_ref(account_id, rows=svc.accounts.all())
    except ValidationError as exc:
        return None, {"ok": False, "error": str(exc)}
    if resolved is None:
        names = [(a.get("name") or "").strip() for a in svc.accounts.all()]
        return None, {
            "ok": False,
            "error": f"No account matching {account_id!r} by id or name."
            + recovery_hint(account_id, names, tool_hint="list_accounts"),
        }
    return resolved, None


def _batch_summary(batch: dict, txns: list[dict]) -> dict:
    """Build the confirmation-preview summary for a batch."""
    dates = [t["date"] for t in txns if t.get("date")]
    sample = [
        {
            "date": t.get("date", ""),
            "description": (t.get("description") or "")[:60],
            "amount": t.get("from_amount") or t.get("to_amount"),
            "kind": t.get("kind", ""),
        }
        for t in txns[:5]
    ]
    return {
        "id": batch["id"],
        "description": batch.get("description", ""),
        "created_at": batch.get("created_at", ""),
        "status": batch.get("status", ""),
        "transaction_count": len(txns),
        "date_span": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
        "sample": sample,
    }


def _batch_age_secs(batch: dict) -> float:
    """Seconds since the batch was created (UTC). Returns 0 on parse error."""
    created_at = batch.get("created_at", "")
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - created).total_seconds()
    except ValueError:
        return 0.0


class ListImportBatchesInput(ToolInput):
    """Schema-of-record for ``list_import_batches`` (see ``schema_from_model``)."""

    status: Optional[Literal["active", "rolled_back"]] = Field(
        default=None, description="Filter by batch status. Omit to return all."
    )


@tool(
    "list_import_batches",
    description="List all statement-import batches. Each batch groups the transactions added in a single add_transactions call. Use this to find a batch_id before rolling back or forward.",
    input_schema=schema_of_no_required(ListImportBatchesInput, tool_name="list_import_batches"),
)
async def list_import_batches(input_data: dict):
    status_filter = input_data.get("status") or None
    batches = svc.import_batches.list_batches(status=status_filter)
    return json.dumps(batches, ensure_ascii=False), None


class RollbackImportBatchInput(ToolInput):
    """Schema-of-record for ``rollback_import_batch`` (see ``schema_from_model``)."""

    batch_id: str = Field(
        description="UUID of the batch to roll back. Get it from list_import_batches."
    )
    preview: Optional[bool] = Field(
        default=None,
        description=("Pass true to see what the batch contains without deleting anything."),
    )


@tool(
    "rollback_import_batch",
    description=(
        "Undo a statement import: soft-delete all transactions that belong to the batch. "
        "DESTRUCTIVE. Pass preview:true first if you are unsure what the batch contains. "
        "The batch is marked 'rolled_back' and can be restored with restore_import_batch."
    ),
    input_schema=schema_of(RollbackImportBatchInput, tool_name="rollback_import_batch"),
    mutates=("ledger",),
)
async def rollback_import_batch(input_data: dict):
    batch_id = input_data.get("batch_id", "")
    preview_only = bool(input_data.get("preview", False))
    batch = svc.import_batches.find_by_id(batch_id)
    if batch is None:
        return json.dumps({"ok": False, "error": "Batch not found"}), None
    if batch.get("status") == "rolled_back":
        return json.dumps({"ok": False, "error": "Batch is already rolled back"}), None
    txns = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    summary = _batch_summary(batch, txns)
    if preview_only:
        # A DRY RUN the caller opted into — not an approval step. Whether the owner is asked
        # is the harness gate's decision (`should_gate`), never a flag the model sets for
        # itself; the `confirm=true` this branch used to require was the second confirm
        # mechanism, removed 2026-08-27 (ADR 2026-08-25-one-confirm-mechanism-for-every-tool).
        resp: dict = {"ok": True, "preview": True, "batch": summary}
        if _batch_age_secs(batch) > _24H_SECS:
            resp["warning"] = "This batch was created by an earlier session/agent."
        return json.dumps(resp, ensure_ascii=False), None
    txn_ids = [t["id"] for t in txns]
    deleted, _ = svc.transactions.bulk_soft_delete(txn_ids)
    svc.import_batches.set_status(batch_id, "rolled_back")
    # Record the exact ids we soft-deleted so restore can un-delete only those rows,
    # not any transactions the user may have explicitly deleted independently.
    svc.import_batches.set_rolled_back_txn_ids(batch_id, list(deleted))
    # UN-MERGE (RFC statement-import-unified D7): a decision-commit records pre-merge
    # snapshots on the batch — restore each merged-into transaction to its exact prior
    # state, so rollback reverses the WHOLE import, not just the created rows.
    unmerged = 0
    for snap in batch.get("merges") or []:
        before = snap.get("before") or {}
        if before.get("id"):
            svc.transactions.upsert(dict(before))
            unmerged += 1
    logger.info(
        "tool rollback_import_batch: batch=%s deleted=%d unmerged=%d",
        batch_id,
        len(deleted),
        unmerged,
    )
    resp_ok: dict = {"ok": True, "batch": summary, "deleted": len(deleted)}
    if unmerged:
        resp_ok["unmerged"] = unmerged
        resp_ok["note"] = (
            "merged rows were restored to their pre-import state; restoring this batch "
            "later will NOT re-apply the merges"
        )
    return json.dumps(resp_ok, ensure_ascii=False), None


class RestoreImportBatchInput(ToolInput):
    """Schema-of-record for ``restore_import_batch`` (see ``schema_from_model``)."""

    batch_id: str = Field(
        description="UUID of the batch to restore. Must currently be in 'rolled_back' status."
    )


@tool(
    "restore_import_batch",
    description=(
        "Undo a rollback: restore all transactions that were soft-deleted by rollback_import_batch "
        "and set the batch status back to 'active'. Only valid when the batch status is 'rolled_back'."
    ),
    input_schema=schema_of(RestoreImportBatchInput, tool_name="restore_import_batch"),
    mutates=("ledger",),
)
async def restore_import_batch(input_data: dict):
    batch_id = input_data.get("batch_id", "")
    batch = svc.import_batches.find_by_id(batch_id)
    if batch is None:
        return json.dumps({"ok": False, "error": "Batch not found"}), None
    if batch.get("status") != "rolled_back":
        return json.dumps(
            {"ok": False, "error": f"Batch status is '{batch.get('status')}', not 'rolled_back'"}
        ), None
    # Use the stored rollback list when available so we don't resurrect transactions
    # the user explicitly deleted. Legacy batches without the field fall back to the
    # old behaviour (all soft-deleted rows in the batch).
    rolled_back_ids = batch.get("rolled_back_txn_ids")
    if rolled_back_ids is not None:
        txn_ids = rolled_back_ids
    else:
        txns = svc.transactions.query_by_batch(batch_id, include_deleted=True)
        txn_ids = [t["id"] for t in txns if t.get("deleted")]
    restored, _ = svc.transactions.bulk_set_deleted(txn_ids, deleted=False)
    svc.import_batches.set_status(batch_id, "active")
    logger.info("tool restore_import_batch: batch=%s restored=%d", batch_id, len(restored))
    return json.dumps({"ok": True, "batch_id": batch_id, "restored": len(restored)}), None


class VerifyStatementBalanceInput(ToolInput):
    """Schema-of-record for ``verify_statement_balance`` (see ``schema_from_model``)."""

    account_id: str = Field(description="Account the statement belongs to — its NAME or its id.")
    opening_balance: float = Field(
        description=(
            "Balance at the START of the statement period, as shown on the statement (a card "
            "or loan statement's amount owed is fine as printed)."
        )
    )
    closing_balance: float = Field(
        description="Balance at the END of the statement period, as shown on the statement."
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Batch ID returned by add_transactions. Use this to scope the check to "
            "exactly the transactions just imported."
        ),
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "First date of the statement period (YYYY-MM-DD). Used as fallback when "
            "batch_id is not available."
        ),
    )
    date_to: Optional[str] = Field(
        default=None,
        description=(
            "Last date of the statement period (YYYY-MM-DD). Used as fallback when "
            "batch_id is not available."
        ),
    )


@tool(
    "verify_statement_balance",
    description=(
        "Verify a statement import by checking that the net change of the imported "
        "transactions matches what the statement says.\n\n"
        "How it works:\n"
        "• Takes the opening and closing balances shown on the statement.\n"
        "• Sums the IN and OUT amounts from the imported transactions (filtered by "
        "  batch_id, or by account + date range if no batch ID).\n"
        "• Checks: opening_balance + total_in - total_out ≈ closing_balance.\n"
        "• A mismatch means some transactions were not imported, duplicated, or wrong.\n\n"
        "This checks statement net movement, not the displayed account balance. Imported rows "
        "dated before a cash/card account's Starting Balance are reported separately "
        "(excluded_transaction_count). Brokerage openings may summarize prior activity.\n\n"
        "Call this immediately after add_transactions whenever the statement shows "
        "opening and/or closing balances. Report the result to the user even when it passes."
    ),
    input_schema=schema_of(VerifyStatementBalanceInput, tool_name="verify_statement_balance"),
    # A read whose computed balance_check is remembered on the import batch.
    refreshes=("uploads",),
)
async def verify_statement_balance(input_data: dict):
    account_id = input_data.get("account_id", "")
    opening_balance = input_data.get("opening_balance")
    closing_balance = input_data.get("closing_balance")
    batch_id = input_data.get("batch_id", "")
    date_from = input_data.get("date_from", "")
    date_to = input_data.get("date_to", "")

    if opening_balance is None or closing_balance is None:
        return json.dumps(
            {"ok": False, "error": "opening_balance and closing_balance are required"}
        ), None

    account_id, err = _resolve_account_or_error(account_id)
    if err is not None:
        return json.dumps(err), None
    acct = svc.accounts.find_by_id(account_id) or {}

    from backend.services.statements.ledger_diff import is_reconcilable_leg

    all_txns = svc.transactions.all()

    # Scope to the transactions we just imported — and, like diff_statement's own
    # _ledger_net, never a kind="adjustment" row (Starting Balance / a manual reconcile
    # delta): those are book-keeping fixes the statement itself never printed, so counting
    # them here would make this tool disagree with diff_statement on the same window (MATH-46).
    if batch_id:
        scoped = [t for t in all_txns if t.get("batch_id") == batch_id and is_reconcilable_leg(t)]
    elif date_from and date_to:
        scoped = [
            t
            for t in all_txns
            if is_reconcilable_leg(t)
            and (t.get("from_account_id") == account_id or t.get("to_account_id") == account_id)
            and date_from <= t.get("date", "") <= date_to
        ]
    else:
        return json.dumps(
            {"ok": False, "error": "Provide batch_id or both date_from and date_to"}
        ), None

    # Sum the legs for this account
    total_in = sum(
        float(t.get("to_amount") or 0) for t in scoped if t.get("to_account_id") == account_id
    )
    total_out = sum(
        float(t.get("from_amount") or 0) for t in scoped if t.get("from_account_id") == account_id
    )

    # A card or loan statement prints what is OWED as positive numbers, and spending RAISES it —
    # the opposite of a bank statement. Taken "as shown on the statement" (what this tool asks
    # for), the owed figures must be read inverted, or every correct card import looks like a
    # mismatch (QA 2026-10-13: "a gap of 2,181.92" on a card that tied to the cent).
    owed_shown = (
        acct.get("type") in {"credit", "loan"}
        and float(opening_balance) >= 0
        and float(closing_balance) >= 0
    )
    expected_net = round(float(closing_balance) - float(opening_balance), 2)
    if owed_shown:
        expected_net = -expected_net
    actual_net = round(total_in - total_out, 2)
    delta = round(actual_net - expected_net, 2)
    matched = abs(delta) <= 0.01
    left_out = (
        None
        if matched or not batch_id
        else _rows_left_out(batch_id, expected_net, actual_net, len(scoped))
    )
    tracking_start = svc.opening_date(acct, all_txns) if acct else None
    # Brokerage/loan openings can deliberately summarize earlier activity. Only cash
    # and card accounts have actionable hidden legs (services.tracking_start).
    excluded = [
        t
        for t in scoped
        if acct.get("type") in {"cash", "credit"}
        and tracking_start
        and t.get("date", "") < tracking_start
        and (t.get("from_account_id") == account_id or t.get("to_account_id") == account_id)
    ]

    if left_out:
        matched = True
    result: dict = {
        "ok": True,
        "status": "mismatch"
        if not matched
        else (
            "tracking_start_conflict" if excluded else ("ok_rows_left_out" if left_out else "ok")
        ),
        "tracking_start_conflict": bool(excluded),
        "account_id": account_id,
        "account_currency": acct.get("currency", ""),
        "scoped_by": f"batch:{batch_id}" if batch_id else f"date:{date_from}..{date_to}",
        "transaction_count": len(scoped),
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "expected_net": expected_net,
        "total_in": round(total_in, 2),
        "total_out": round(total_out, 2),
        "actual_net": actual_net,
        "delta": delta,
    }
    if owed_shown:
        result["balances_read_as"] = "amount owed (card/loan statement)"
    if left_out:
        result["rows_left_out"] = left_out
        result["left_out_note"] = (
            f"The statement ties: the only difference is the {left_out['rows']} statement "
            f"row(s) left out of this import (net {left_out['net']:+,.2f}), e.g. a card payment "
            "that belongs on the bank's side. Nothing is missing."
        )
    if excluded:
        result["tracking_start"] = tracking_start
        result["excluded_transaction_count"] = len(excluded)
        result["note"] = (
            "These imported rows predate the account's Starting Balance, so they are history: "
            "they don't change its balance, and the statement's closing figure can't be "
            "compared with today's. Nothing to adjust unless the user says today's balance "
            "is wrong."
        )
    if not matched:
        mismatch_note = (
            "Net change from imported transactions doesn't match the statement. "
            "Some rows may be missing or wrong — re-run diff_statement to check. "
            f"Do NOT tell the user it reconciles: say it is close, with a gap of {abs(delta):,.2f}, "
            "and name a cause (timing, a missing row) only once you have traced it."
        )
        result["note"] = f"{result['note']} {mismatch_note}" if excluded else mismatch_note
        # Build largest_legs: the top-3 scoped legs for this account, by abs amount
        legs: list[dict] = []
        for t in scoped:
            if t.get("to_account_id") == account_id and t.get("to_amount"):
                legs.append(
                    {
                        "date": t.get("date", ""),
                        "direction": "IN",
                        "amount": float(t["to_amount"]),
                        "description": (t.get("description") or "")[:60],
                    }
                )
            if t.get("from_account_id") == account_id and t.get("from_amount"):
                legs.append(
                    {
                        "date": t.get("date", ""),
                        "direction": "OUT",
                        "amount": float(t["from_amount"]),
                        "description": (t.get("description") or "")[:60],
                    }
                )
        legs.sort(key=lambda x: x["amount"], reverse=True)
        result["largest_legs"] = legs[:3]
        if expected_net != 0 and legs and legs[0]["amount"] > 50 * abs(expected_net):
            result["hint"] = (
                "Leg amounts are wildly out of scale with the statement — the transaction "
                "currency was probably wrong at creation (legs are stored in the account "
                "currency; check what currency the rows were added with)."
            )

    # Persist balance_check to the import batch so it survives the session
    if batch_id:
        try:
            svc.import_batches.set_balance_check(batch_id, result)
        except Exception:
            pass

    return json.dumps(result, ensure_ascii=False), None


class DiffStatementInput(ToolInput):
    """Schema-of-record for ``diff_statement`` (see ``schema_from_model``)."""

    account_id: str = Field(
        description=(
            "Account to reconcile against — its NAME or its id. Call list_accounts "
            "only to discover what accounts exist, not to translate a name."
        )
    )
    ref_id: Optional[str] = Field(
        default=None,
        description=(
            "The ref_id of the uploaded bank statement file (from the attachment). Use this "
            "when the file was just uploaded."
        ),
    )
    statement_id: Optional[str] = Field(
        default=None,
        description=(
            "Statement ID (from list_statements or a prior diff_statement result), for a "
            "statement already on file — its content is read from storage."
        ),
    )


_DIFF_WITHHELD_NOTES = {
    "parse_incomplete": (
        "This statement's parse never finished reading every page — the missing/extra lists "
        "would be unreliable (an unread row looks identical to a genuinely missing one), so "
        "they are withheld. Do NOT delete or add anything based on this call; re-upload for a "
        "clean read, or ask the owner to confirm by hand."
    ),
    "reconcile_off": (
        "This statement's own printed balances don't reconcile, so comparing it against the "
        "ledger would compound one unproven claim onto another. Do NOT delete or add anything "
        "based on this call — ask the owner to confirm by hand or re-check the statement's "
        "own totals."
    ),
    "parse_unverified": (
        "This statement was read before completeness checks existed, so we can't tell what's "
        "missing from it yet. Do NOT delete or add anything based on this call; ask the owner "
        "to re-upload the file so it can be re-read and verified."
    ),
}


def _diff_withheld_result(reason: str, meta: dict, account_name: str, statement_id, bank_count):
    extra = meta.get("extra") or {}
    return {
        "ok": True,
        "withheld": reason,
        "account": account_name,
        "statement_id": statement_id,
        "bank_count": bank_count,
        "pages_incomplete": len(extra.get("incomplete_pages") or []),
        "note": _DIFF_WITHHELD_NOTES[reason],
        "say": (
            "Tell the user plainly that you couldn't read this statement in full (name its "
            "month); never refer to a comparison tool or a tool at all."
        ),
    }


def _import_rows_as_bank_txns(record: dict) -> list[dict]:
    """The decision-preview pipeline's ``import_rows`` (``CanonicalRow`` dicts), adapted to
    this module's flat ``{date, amount, direction, description}`` contract — skip rows
    excluded, same as ``reconcile.parse_bank_statement``'s own mapping."""
    return [
        {
            "date": r["date"],
            "amount": r["amount"],
            "direction": r["direction"],
            "description": r.get("description", ""),
        }
        for r in (record.get("import_rows") or [])
        if not r.get("skip")
    ]


def _cached_diff_parse(record: dict) -> tuple[list[dict] | None, dict, dict | None]:
    """Cached parse for a re-diff — meta/reconcile PAIRED with whichever rows cache actually
    produced them, never cross-matched (security review: pairing this tool's own
    ``parsed_rows`` with the decision-preview pipeline's ``import_meta``/``reconcile`` — a
    possibly DIFFERENT, later, complete re-parse of the same file — could read an incomplete
    diff-tool parse as gated open). Prefers ``parsed_rows`` (this tool's own, more direct
    cache); falls back to ``import_rows`` only when this tool never cached its own rows.
    ``tier`` is the record's ONE shared field either way (see ``set_parsed_rows``)."""
    if record.get("parsed_rows"):
        meta = record.get("parsed_meta") or {}
        return record["parsed_rows"], meta, record.get("parsed_reconcile")
    if record.get("import_rows"):
        meta = record.get("import_meta") or {}
        return _import_rows_as_bank_txns(record), meta, record.get("reconcile")
    return None, {}, None


@tool(
    "diff_statement",
    description=(
        "Parse an uploaded bank statement file and diff it against system transactions "
        "for a given account. Works with any bank — no per-bank configuration needed.\n\n"
        "The file and its parsed rows are stored persistently so the AI can refer back "
        "without re-uploading or re-parsing.\n\n"
        "Returns:\n"
        "• missing — transactions in the bank statement but not in the system (need to be added)\n"
        "• extra   — transactions in the system but not in the bank statement (need to be deleted)\n"
        "• matched — count of transactions that agree\n"
        "• withheld — set instead of missing/extra when the parse never finished reading every "
        "page, or the statement's own balances don't reconcile; the comparison cannot be "
        "trusted, so NOTHING is added or deleted off the back of this call.\n\n"
        "Workflow:\n"
        "1. User uploads the bank statement file and mentions the account.\n"
        "2. Call this tool with the file's ref_id and the account_id.\n"
        "3. Show the user the summary and the mismatch lists.\n"
        "4. Ask for confirmation, then use add_transactions / delete_transactions to fix them.\n"
        "5. Call verify_statement_balance to confirm the closing balance matches."
    ),
    input_schema=schema_of(DiffStatementInput, tool_name="diff_statement"),
    # A read whose parsed rows are remembered on the statement record so a re-diff skips the
    # expensive LLM pass.
    refreshes=("uploads",),
)
async def diff_statement(input_data: dict):
    from backend.services.attachments import get_attachment_for_model, private_refusal
    from backend.services.reconcile import diff_against_system, parse_bank_statement
    from backend.services.statements.decisions import _parse_withheld_reason

    ref_id = input_data.get("ref_id", "")
    account_id = input_data.get("account_id", "")
    statement_id = input_data.get("statement_id") or None

    refusal = private_refusal(ref_id) if ref_id else None
    if refusal:
        return json.dumps({"ok": False, "private": True, "error": refusal}), None
    # Resolve upload entry: prefer live cache, fall back to persisted upload_entry
    upload = get_attachment_for_model(ref_id) if ref_id else None
    if upload:
        statement_id = upload.get("statement_id") or statement_id
    elif statement_id:
        # ref_id expired — reconstruct from the persisted statement record
        try:
            stored = svc.statements.find_by_id(statement_id)
            if stored and stored.get("upload_entry"):
                upload = stored["upload_entry"]
                logger.info(
                    "diff_statement: ref_id expired, restored upload_entry from statement %s",
                    statement_id,
                )
        except Exception:
            pass

    if not upload:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "File not found — the attachment was deleted (or the ref is wrong) and no "
                    "statement record is on file. Ask the user to attach it again."
                ),
            }
        ), None
    if upload.get("kind") not in ("text", "pdf_pages"):
        return json.dumps(
            {
                "ok": False,
                "error": "Unsupported file type. Please upload a CSV, Excel, or PDF bank statement.",
            }
        ), None

    account_id, err = _resolve_account_or_error(account_id)
    if err is not None:
        return json.dumps(err), None
    acct = svc.accounts.find_by_id(account_id) or {}

    # Use cached parsed rows if available (avoids re-running expensive LLM parse)
    bank_txns, parse_meta, parse_reconcile, parse_tier = None, {}, None, None
    if statement_id:
        try:
            record = svc.statements.find_by_id(statement_id)
            if record:
                bank_txns, parse_meta, parse_reconcile = _cached_diff_parse(record)
                parse_tier = record.get("tier")
                if bank_txns:
                    logger.info(
                        "diff_statement: using cached parsed rows for statement %s (%d rows)",
                        statement_id,
                        len(bank_txns),
                    )
        except Exception:
            pass

    if bank_txns is None:
        try:
            bank_txns, meta_obj, parse_reconcile, parse_tier = await parse_bank_statement(upload)
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}), None
        parse_meta = meta_obj.to_dict()

        # Persist parsed rows + the completeness gate's own inputs to the statement store
        if statement_id and bank_txns:
            try:
                svc.statements.set_parsed_rows(
                    statement_id,
                    bank_txns,
                    account_id=account_id,
                    meta=parse_meta,
                    reconcile=parse_reconcile,
                    tier=parse_tier,
                )
            except Exception:
                pass

    if not bank_txns:
        return json.dumps(
            {"ok": False, "error": "No transactions could be parsed from the file."}
        ), None

    withheld = _parse_withheld_reason(parse_meta, parse_reconcile, parse_tier)
    if withheld:
        result = _diff_withheld_result(
            withheld, parse_meta, acct.get("name", account_id), statement_id, len(bank_txns)
        )
        return json.dumps(result, ensure_ascii=False), None

    all_txns = svc.transactions.all()
    result = diff_against_system(bank_txns, all_txns, account_id)
    result["ok"] = True
    result["account"] = acct.get("name", account_id)
    result["statement_id"] = statement_id
    return json.dumps(result, ensure_ascii=False), None


_PARSED_ROWS_PAGE = 20


class GetStatementInput(ToolInput):
    """Schema-of-record for ``get_statement`` (see ``schema_from_model``)."""

    statement_id: str = Field(
        description="The statement_id returned when the file was uploaded, or found in list_import_batches."
    )
    offset: Optional[int] = Field(
        default=None, description="Row offset for paging parsed_rows (default 0)."
    )


@tool(
    "get_statement",
    description=(
        "Retrieve a previously uploaded bank statement by its statement_id. "
        "Returns statement metadata and the first 20 parsed rows (if parsed). "
        "Pass offset to page through further rows."
    ),
    input_schema=schema_of(GetStatementInput, tool_name="get_statement"),
)
async def get_statement(input_data: dict):
    statement_id = input_data.get("statement_id", "")
    row_offset = int(input_data.get("offset") or 0)
    record = svc.statements.find_by_id(statement_id)
    if record is None:
        # Maybe the caller passed an attachment ref_id instead of a statement_id: if that
        # attachment carries a statement_id, transparently resolve to that statement.
        from backend.services.attachments import get_attachment_for_model

        upload_entry = get_attachment_for_model(statement_id)
        if upload_entry and upload_entry.get("statement_id"):
            linked_sid = upload_entry["statement_id"]
            record = svc.statements.find_by_id(linked_sid)
            if record is not None:
                safe = _safe_statement(record, row_offset)
                safe["resolved_from_ref_id"] = True
                return json.dumps({"ok": True, "statement": safe}, ensure_ascii=False), None

        # An attachment ref with no statement behind it yet: say so, rather than "not found".
        if upload_entry is not None:
            return (
                json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"{statement_id!r} is a chat attachment ref_id, not a statement_id. "
                            "Use list_statements to find the persistent statement_id."
                        ),
                    }
                ),
                None,
            )
        # Unknown id — a deleted attachment or a bad id
        return (
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"Statement {statement_id!r} not found. Use list_statements to find "
                        "the statement_id."
                    ),
                }
            ),
            None,
        )
    safe = _safe_statement(record, row_offset)
    return json.dumps({"ok": True, "statement": safe}, ensure_ascii=False), None


# Keys stripped from upload_entry before it reaches the AI — genuinely large binary blobs
# (base64 image/page data), not reconciliation-relevant text. Everything else (kind, text,
# summary, format) is the extracted statement content an agent needs to resolve an ambiguous
# parsed row against the source document, and was previously dropped wholesale even though
# it would have serialized fine (root-caused 2026-07-09 alongside the accounts.py last_four bug).
_UPLOAD_ENTRY_AI_STRIP = frozenset({"content", "data", "images"})


def _safe_statement(record: dict, row_offset: int = 0) -> dict:
    """Build the AI-facing statement dict: upload_entry minus large binary blobs, parsed_rows paged."""
    safe = {k: v for k, v in record.items() if k != "parsed_rows"}
    upload_entry = record.get("upload_entry")
    if upload_entry:
        safe["upload_entry"] = {
            k: v for k, v in upload_entry.items() if k not in _UPLOAD_ENTRY_AI_STRIP
        }
    safe["has_upload_entry"] = bool(record.get("upload_entry"))
    all_rows = record.get("parsed_rows") or []
    safe["parsed_row_count"] = len(all_rows)
    page = all_rows[row_offset : row_offset + _PARSED_ROWS_PAGE]
    safe["parsed_rows"] = page
    if row_offset + len(page) < len(all_rows):
        safe["parsed_rows_note"] = (
            f"Showing rows {row_offset + 1}–{row_offset + len(page)} of {len(all_rows)}. "
            "Pass offset to fetch more."
        )
    return safe


_STATEMENTS_LIST_LIMIT = 20


def _statement_effective_date(r: dict) -> str:
    """The date a cycle/date-range filter matches against: the statement's OWN printed
    date when known (statement_date, else period_end), falling back to the upload date
    for a record with no extracted metadata at all. Never the account's current balance
    date — a statement's identity is when it covers, not when it was uploaded."""
    meta = r.get("import_meta") or {}
    return meta.get("statement_date") or meta.get("period_end") or (r.get("created_at") or "")[:10]


def _statement_meta_status(r: dict) -> str:
    """'available' — metadata was extracted; 'unavailable' — extraction was attempted and
    failed (import_meta.meta_unavailable, ADR 2026-08-01); 'unknown' — never attempted."""
    meta = r.get("import_meta")
    if meta is None:
        return "unknown"
    return "unavailable" if meta.get("meta_unavailable") else "available"


def _resolve_statement_account_ids(refs: list[str]) -> tuple[set[str], list[str]]:
    """Resolve each ref (id or NAME) the same way the ledger choke does. A ref matching
    nothing is reported back, never silently dropped — dropping it would look identical
    to 'that account has no statements'."""
    from backend.core.errors import ValidationError
    from backend.services import accounts as accounts_service

    resolved: set[str] = set()
    unmatched: list[str] = []
    scope = svc.accounts.all()
    for ref in refs:
        if svc.accounts.find_by_id(ref):
            resolved.add(ref)
            continue
        try:
            hit = accounts_service.resolve_account_ref(ref, rows=scope)
        except ValidationError:
            hit = None
        if hit:
            resolved.add(hit)
        else:
            unmatched.append(ref)
    return resolved, unmatched


def _list_statement_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "created_at": r.get("created_at"),
        "filename": r.get("filename"),
        "size_bytes": r.get("size_bytes"),
        "kind": r.get("kind"),
        "account_id": r.get("account_id"),
        "parsed": r.get("parsed_rows") is not None,
        "parsed_row_count": len(r["parsed_rows"]) if r.get("parsed_rows") else None,
        "batch_ids": r.get("batch_ids", []),
        "imported": bool(r.get("batch_ids")),
        "meta_status": _statement_meta_status(r),
        "statement_date": (r.get("import_meta") or {}).get("statement_date"),
    }


class ListStatementsInput(ToolInput):
    """Schema-of-record for ``list_statements`` (see ``schema_from_model``)."""

    account_ids: Optional[List[str]] = Field(
        default=None,
        description=(
            "Only statements linked to these accounts, by id OR exact NAME. A name matching "
            "nothing is reported back, never silently dropped."
        ),
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "Lower bound (YYYY-MM-DD, inclusive) on the statement's OWN cycle date — its "
            "printed statement_date/period_end, falling back to the upload date when no "
            "metadata was extracted."
        ),
    )
    date_to: Optional[str] = Field(
        default=None, description="Upper bound (YYYY-MM-DD, inclusive), same date basis."
    )
    parsed: Optional[bool] = Field(
        default=None,
        description="true = only statements diff_statement has already parsed; false = only unparsed ones.",
    )
    imported: Optional[bool] = Field(
        default=None,
        description="true = only statements that produced at least one import batch; false = only ones that didn't.",
    )
    meta_status: Optional[Literal["available", "unavailable", "unknown"]] = Field(
        default=None,
        description=(
            "Extracted-metadata status: 'available' = due date/amount were read from the "
            "statement, 'unavailable' = extraction was attempted and failed, 'unknown' = "
            "extraction was never attempted."
        ),
    )
    kind: Optional[Literal["text", "pdf_pages", "image"]] = Field(
        default=None, description="Filter by the uploaded file's parsed kind."
    )
    limit: Optional[int] = Field(
        default=None,
        description="Max rows to return (default 20). Pass a higher value to see more.",
    )
    offset: Optional[int] = Field(default=None, description="Row offset for paging (default 0).")


@tool(
    "list_statements",
    description=(
        "List previously uploaded bank statement files — filtered and paged, never an "
        "unbounded dump. Returns metadata for each statement: filename, upload date, "
        "account, whether it has been parsed and imported, and metadata-extraction status.\n\n"
        "FILTER SERVER-SIDE: pass account_ids / date_from & date_to / parsed / imported / "
        "meta_status / kind rather than fetching every statement and scanning it yourself. "
        "Returns 20 rows by default (limit/offset page through the rest) and always reports "
        "`showing` and `total` so you can tell a subset from the whole store.\n\n"
        "Use get_statement for a single statement's full detail (parsed rows included)."
    ),
    input_schema=schema_of(ListStatementsInput, tool_name="list_statements"),
)
async def list_statements(input_data: dict):
    records = svc.statements.list_statements()
    grand_total = len(records)

    account_filter_note = None
    resolved_ids: Optional[set] = None
    raw_ids = input_data.get("account_ids")
    if raw_ids:
        refs = [str(r).strip() for r in raw_ids if str(r).strip()]
        resolved_ids, unmatched = _resolve_statement_account_ids(refs)
        if unmatched:
            account_filter_note = (
                f"No account matches {', '.join(repr(u) for u in unmatched)} — check the "
                "name or call list_accounts to see what exists."
            )

    date_from = input_data.get("date_from") or None
    date_to = input_data.get("date_to") or None
    parsed_filter = input_data.get("parsed")
    imported_filter = input_data.get("imported")
    meta_status_filter = input_data.get("meta_status")
    kind_filter = input_data.get("kind")

    def _matches(r: dict) -> bool:
        if resolved_ids is not None and r.get("account_id") not in resolved_ids:
            return False
        eff_date = _statement_effective_date(r)
        if date_from and eff_date < date_from:
            return False
        if date_to and eff_date > date_to:
            return False
        if parsed_filter is not None and (r.get("parsed_rows") is not None) != bool(parsed_filter):
            return False
        if imported_filter is not None and bool(r.get("batch_ids")) != bool(imported_filter):
            return False
        if meta_status_filter is not None and _statement_meta_status(r) != meta_status_filter:
            return False
        return not (kind_filter is not None and r.get("kind") != kind_filter)

    matched = [r for r in records if _matches(r)]
    matched_total = len(matched)

    raw_limit = input_data.get("limit")
    limit = int(raw_limit) if raw_limit is not None else _STATEMENTS_LIST_LIMIT
    offset = int(input_data.get("offset") or 0)
    page = matched[offset : offset + limit] if limit > 0 else matched[offset:]
    summary = [_list_statement_row(r) for r in page]

    out: dict = {
        "ok": True,
        "statements": summary,
        "showing": len(summary),
        "total": matched_total,
    }
    notes: list[str] = []
    if account_filter_note:
        notes.append(account_filter_note)
    if matched_total == 0:
        # A zero-match must never read as an empty statement store — say what DOES exist
        # (or that nothing does) so the model never reports "no statements" for a filter
        # that just excluded them all.
        if grand_total == 0:
            notes.append("No statements have been uploaded yet.")
        else:
            notes.append(
                f"0 of {grand_total} statements match these filters — statements DO exist, "
                "your filter excluded them. Loosen or drop a filter and retry."
            )
    else:
        end = offset + len(summary)
        if end < matched_total:
            notes.append(
                f"Showing {offset + 1}-{end} of {matched_total} matching statements"
                + (f" (of {grand_total} total)" if grand_total != matched_total else "")
                + f". Pass offset={end} to see more."
            )
    if notes:
        out["note"] = " ".join(notes)
    return json.dumps(out, ensure_ascii=False), None


class CheckStatementDriftInput(ToolInput):
    """Schema-of-record for ``check_statement_drift`` (see ``schema_from_model``)."""


@tool(
    "check_statement_drift",
    description=(
        "Check every imported statement's printed closing balance against what the ledger "
        "says that account's balance was on the same day.\n\n"
        "Different from verify_statement_balance, and complementary: that one checks whether "
        "an import's NET CHANGE matches the statement's own opening-to-closing delta, which "
        "stays correct even when the absolute balance is wrong. This one checks the ABSOLUTE "
        "balance, so it catches a wrong opening, a charge deleted long ago, or a "
        "double-booked payment — none of which change an import's net.\n\n"
        "Reading the result, per account:\n"
        "• clean=true — nothing unreconciled. Say so; it is the common case.\n"
        "• timing_only — a close drifted and the NEXT one cleared it. Normal posting lag "
        "(charged before the cutoff, posted after it), not an error. Do not report as a problem.\n"
        "• pending=true — the drift is on the NEWEST statement, so lag and a missing row look "
        "identical until the next statement arrives. Say it is undecided; do not guess.\n"
        "• constant_drift set — the same drift at every unreconciled close INCLUDING the "
        "earliest: the opening balance is wrong. Fix it with set_account_opening.\n"
        "• otherwise — use each row's window_from and drift_change to name the date range that "
        "introduced it, then diff_statement or get_transactions over that window.\n\n"
        "Never edit anything off the back of this — report what disagrees and let the owner "
        "decide which side is right."
    ),
    input_schema=schema_of(CheckStatementDriftInput, tool_name="check_statement_drift"),
)
async def check_statement_drift(input_data: dict):
    from backend.services.statement_drift import statement_drift

    return json.dumps({"ok": True, **statement_drift()}, ensure_ascii=False), None


# ── Card statement status (RFC cross-channel-attachments §8.1) ──────────────────────────────
#
# Backed directly by services.card_statements.statement_summary — the deterministic cycle
# math (paid/partial/unpaid + due date + amount). This tool exists so the model has a bounded,
# grounded way to answer "which card statements have I not paid" WITHOUT inferring payment
# state from an account's current balance or from a transaction that merely looks like a
# payment — current balance answers "what do I owe right now", statement settlement answers
# "did the printed statement get settled", and those are different questions (a balance can
# read zero mid-cycle while last month's statement is still unpaid).


def _statement_payment_since(summary) -> date:
    """The date FROM which a settling payment is looked for, re-derived from the PUBLIC
    StatementSummary contract rather than re-implementing card_statements' internal
    recency ladder: ``statement_id``/``statement_date`` are populated ONLY when a fresh
    statement's printed facts are driving the summary (see the dataclass's own docstring),
    in which case the service anchors payment-detection at the statement's OWN close date;
    otherwise it anchors at the derived ``last_statement_close``. Both fields are already
    on the object the service handed back — this reads them, it does not recompute them."""
    if summary.statement_id:
        return date.fromisoformat(summary.statement_date)
    return date.fromisoformat(summary.last_statement_close)


def _matched_card_payments(account_id: str, since: date) -> list[dict]:
    """Transactions that satisfy ``statement_summary``'s own payment detection — same
    filter as ``card_statements._find_payment`` (transfer, to this card, on/after ``since``,
    not deleted) — but returning the actual rows as evidence instead of just a total."""
    since_iso = since.isoformat()
    matched = [
        {"transaction_id": t["id"], "date": t.get("date"), "amount": float(t["to_amount"])}
        for t in svc.transactions.all()
        if not t.get("deleted")
        and t.get("kind") == "transfer"
        and t.get("to_account_id") == account_id
        and t.get("date", "") >= since_iso
        and t.get("to_amount") is not None
    ]
    matched.sort(key=lambda m: m["date"])
    return matched


def _unreconciled_statement_row(account: dict) -> dict:
    """Row shape for a `credit` account statement_summary refused to compute at all —
    no cycle configured AND nothing uploaded, so there is genuinely nothing to report."""
    return {
        "account_id": account["id"],
        "account_label": account.get("name") or account["id"],
        "status": "unreconciled",
        "currency": account.get("currency", ""),
        "statement_period_start": None,
        "statement_period_end": None,
        "due_date": None,
        "days_until_due": None,
        "overdue": False,
        "statement_amount": None,
        "matched_payments": [],
        "remaining_amount": None,
        "evidence_ids": [],
        "warnings": [
            "No statement cycle is configured for this account (set statement_close_day "
            "and/or payment_due_day) and no statement has ever been uploaded for it — "
            "payment status cannot be determined."
        ],
    }


def _card_statement_status(summary) -> str:
    if summary.statement_missing_for_cycle:
        return "missing-cycle"
    if not summary.paid:
        return "unpaid"
    if summary.partial:
        return "partial"
    return "paid"


def _card_statement_warnings(
    status: str, summary, *, has_statement_evidence: bool, overdue: bool, remaining: float
) -> list[str]:
    warnings: list[str] = []
    if status == "missing-cycle":
        warnings.append(
            "A newer statement cycle has closed without an uploaded statement — these "
            "figures are the ledger's own ESTIMATE for the latest cycle, not printed "
            "statement facts. Upload the newest statement for exact numbers."
        )
    elif not has_statement_evidence:
        warnings.append(
            "No statement has been uploaded for this cycle — these figures come from the "
            "account's configured cycle dates, not a printed statement."
        )
    if overdue:
        warnings.append(f"Payment is {abs(summary.days_until_due)} day(s) overdue.")
    if status == "partial":
        warnings.append(f"Partial payment — {remaining} {summary.currency} still due.")
    elif status == "unpaid":
        warnings.append("No payment has been detected against this statement.")
    return warnings


def _card_statement_row(account: dict) -> dict:
    from backend.services.card_statements import statement_summary

    account_id = account["id"]
    summary = statement_summary(account_id)
    if summary is None:
        return _unreconciled_statement_row(account)

    has_statement_evidence = bool(summary.statement_id)
    matched = _matched_card_payments(account_id, _statement_payment_since(summary))
    status = _card_statement_status(summary)
    # A fully paid statement can never be "overdue" — once settled, the due date lapsing
    # afterward is irrelevant (owner repro 2026-08-27: a payment posted well before the due
    # date was reported as "N days late" because days_until_due is today-relative and was
    # read as a lateness signal instead of a due-date-staleness one).
    overdue = bool(
        status != "paid" and summary.days_until_due is not None and summary.days_until_due < 0
    )
    remaining = summary.remaining_amount

    evidence_ids: list[str] = []
    if summary.statement_id:
        evidence_ids.append(summary.statement_id)
    evidence_ids.extend(m["transaction_id"] for m in matched)

    return {
        "account_id": account_id,
        "account_label": account.get("name") or account_id,
        "status": status,
        "currency": summary.currency,
        "base_currency": summary.base_currency,
        "statement_period_start": summary.statement_period_start,
        "statement_period_end": summary.statement_period_end or summary.last_statement_close,
        "due_date": summary.next_due_date,
        "days_until_due": summary.days_until_due,
        "overdue": overdue,
        "statement_amount": summary.last_statement_balance,
        "statement_amount_base": summary.last_statement_base,
        "matched_payments": matched,
        "remaining_amount": remaining,
        "due_source": summary.due_source,
        "evidence_ids": evidence_ids,
        "warnings": _card_statement_warnings(
            status,
            summary,
            has_statement_evidence=has_statement_evidence,
            overdue=overdue,
            remaining=remaining,
        ),
    }


class GetCardStatementStatusInput(ToolInput):
    """Schema-of-record for ``get_card_statement_status`` (see ``schema_from_model``)."""

    account_id: Optional[str] = Field(
        default=None,
        description=(
            "One credit-card account's NAME or id. Omit to report on every `credit` account."
        ),
    )


@tool(
    "get_card_statement_status",
    description=(
        "Deterministic per-statement-cycle payment status for credit card accounts, computed "
        "by services.card_statements.statement_summary — NEVER from the account's current "
        "balance or a transaction that merely looks like a payment.\n\n"
        "USE THIS for 'which card statements have I not paid', 'am I behind on my card "
        "bill', 'is my <card> statement settled'. A balance can read near-zero mid-cycle "
        "while last month's statement is still unpaid — do not eyeball get_transactions or "
        "a balance for this; call this tool.\n\n"
        "Pass account_id (name or id) for one card, or omit it for every `credit` "
        "account. Each row reports the statement period, due date, statement amount, matched "
        "payment(s), remaining amount owed, and one status:\n"
        "• paid — fully settled.\n"
        "• partial — a payment was made but less than the statement amount.\n"
        "• unpaid — no payment detected.\n"
        "• missing-cycle — a newer cycle closed with no statement uploaded; figures are a "
        "ledger ESTIMATE, not printed-statement fact — say so, suggest uploading it.\n"
        "• unreconciled — no cycle configured and none ever uploaded; status unknown.\n\n"
        "overdue / days_until_due compare the due date to TODAY, not to a payment date — "
        "overdue is always false once status is 'paid', so never describe a paid statement "
        "as paid 'N days late'; compare matched_payments[].date to due_date yourself for "
        "that.\n\n"
        "evidence_ids names the statement/payment record(s) behind the status — cite those "
        "ids rather than re-deriving the answer."
    ),
    input_schema=schema_of(GetCardStatementStatusInput, tool_name="get_card_statement_status"),
)
async def get_card_statement_status(input_data: dict):
    account_ref = (input_data.get("account_id") or "").strip()
    if account_ref:
        resolved, err = _resolve_account_or_error(account_ref)
        if err is not None:
            return json.dumps(err), None
        acct = svc.accounts.find_by_id(resolved) or {}
        if acct.get("type") != "credit":
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"{acct.get('name', resolved)!r} is not a `credit` account — "
                        "statement status only applies to credit cards. Use list_accounts to "
                        "find your card accounts."
                    ),
                }
            ), None
        scope = [acct]
    else:
        scope = [
            a for a in svc.accounts.all() if a.get("type") == "credit" and not a.get("archived")
        ]

    total = len(scope)
    statements = [_card_statement_row(a) for a in scope]
    out: dict = {"ok": True, "statements": statements, "showing": len(statements), "total": total}
    if total == 0:
        out["note"] = "No credit-card accounts exist yet."
    return json.dumps(out, ensure_ascii=False), None
