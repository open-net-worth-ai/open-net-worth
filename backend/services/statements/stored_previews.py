"""The finished review of a statement, kept with the statement so opening it is instant.

``parse_status = ready`` means every step of the read is done — the parse AND the review built on
it (routing, the matcher, anomaly detection, enrichment, the ledger check). ``decision_preview``
persists that final response here before the record can read ``ready``, and a reopen returns it
as-is: no fingerprint, no recompute, across restarts (ADR
``2026-10-25-ready-means-the-review-is-persisted``).

Only an explicit user action asks for a new one: ``force_reparse`` / ``force_relearn``, a
different account, changed instructions, or a column mapping. A ledger that moved on since is
the COMMIT's concern, not the open's: it re-derives the match pass against the live ledger
(``decisions.rederive_match_pass``) and refuses what it now finds duplicated. A statement read
before this existed has no row and recomputes once, as it always did.
"""

from __future__ import annotations

import copy
import json

from backend.db.statement_preview_store import StatementPreviewStore


def keep(
    statement_id: str,
    *,
    fmt: str,
    instr_hash: str | None,
    account_arg: str | None,
    preview: dict,
) -> None:
    """Persist ``preview`` as this statement's review. Raises on a storage failure: a read that
    could not keep its review must not go on to report ``ready``."""
    StatementPreviewStore().put(
        statement_id,
        fmt=fmt,
        instr_hash=instr_hash,
        account_arg=account_arg,
        resolved_account_id=preview.get("resolved_account_id"),
        preview=json.loads(json.dumps(preview, default=str)),
    )


def drop(statement_id: str) -> None:
    StatementPreviewStore().delete(statement_id)


def recall(
    statement_id: str,
    *,
    fmt: str,
    instr_hash: str | None,
    account_id: str | None,
    record: dict,
    one_off: bool,
) -> dict | None:
    """The stored review if it answers THIS request, else ``None`` (the caller computes). A
    caller naming no account takes the one the review was computed for; naming the one it
    resolved to is the same answer minus the "looks like X" banner an explicit choice never
    gets."""
    row = StatementPreviewStore().get(statement_id)
    if row is None or row["fmt"] != fmt or row["instr_hash"] != instr_hash:
        return None
    # Kept before the rule above existed: an "ask for an account" payload is no review.
    if row["preview"].get("needs_account"):
        return None
    if account_id not in (None, row["account_arg"], row["resolved_account_id"]):
        return None
    preview = _as_reopened(copy.deepcopy(row["preview"]), record, one_off)
    if account_id:
        preview["account_suggestion"] = None
    return preview


def flagged_duplicates(statement_id: str) -> set[int] | None:
    """The row indexes the stored review flagged as already booked, or ``None`` when there is no
    stored review (nothing to compare a commit against)."""
    row = StatementPreviewStore().get(statement_id)
    if row is None:
        return None
    return {e["index"] for e in row["preview"].get("rows") or [] if e.get("duplicate")}


def _as_reopened(preview: dict, record: dict, one_off: bool) -> dict:
    """The preview as a recompute over the cached parse would report it: the spend fields say
    what THIS call billed — nothing — and the file's cost stays what its read cost; whether
    the typed instructions were remembered is this request's own choice."""
    preview["llm_calls"] = 0
    preview["llm_cost_usd"] = round(float(record.get("llm_cost_usd") or 0.0), 6)
    if preview.get("instructions"):
        preview["instructions"]["used_in_parse"] = False
        preview["instructions"]["remembered"] = not one_off
    policy = preview.get("match_policy")
    if policy:
        if policy["status"] == "derived":
            policy["status"] = "cached"
        policy["llm_calls"], policy["cost_usd"] = 0, 0.0
    lucius = preview.get("lucius_pass")
    if lucius:
        lucius["fresh"], lucius["llm_calls"], lucius["cost_usd"] = False, 0, 0.0
    return preview
