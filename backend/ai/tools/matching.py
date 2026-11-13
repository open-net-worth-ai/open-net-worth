"""Dedup / match engine tools for Lucius (RFC transaction-dedup-engine §API surface).

Read (headless-allowed):
  • ``check_duplicates``       — PRE-write: classify rows the caller has NOT booked yet.
  • ``find_duplicates``        — on-demand scan for duplicate / transfer-pair candidates.
  • ``list_match_reviews``     — the pending review queue the engine surfaced on import/sync.
  • ``list_match_decisions``   — the remembered verdict cache (for auditing / management).

``check_duplicates`` (2026-07-31) closes the one gap the rest of this surface structurally
could not: everything else here is POST-hoc. Rows are created first and flagged afterwards
(``match_review.review_created_rows`` — created-then-flagged is deliberate, so a flagged row is
never silently dropped), and ``find_duplicates`` scans the LEDGER by date range. Neither can
answer "here are rows I am about to write; are they already there?". Owner incident: the same
S$2,000.00 card payment was booked twice by the email agent (a two-leg transfer from the bank
alert, then a single-leg credit from the Giro alert two days later) and caught by eye. The
verdict comes from ``services.match_preflight``, which is ``matching.match_batch`` — the SAME
engine entry point and the SAME candidate pool ``add_transactions``' own duplicate pre-flight
uses — never a second matcher. See that module for why ``match_batch`` and not ``check()``.

Write (approve-tier; NOT headless — a merge mutates/tombstones rows):
  • ``merge_transactions``      — merge a pair (canonical absorbs absorbed); optional become_transfer.
  • ``resolve_match_reviews``   — accept (merge) or dismiss (keep both, remember different) one or
                                  more reviews with the SAME action (was ``resolve_match_review``).
  • ``swap_review_canonical``   — swap a review's canonical to a tied alternative (F20).
  • ``forget_match_decisions``  — remove one or more remembered verdicts so the engine re-evaluates
                                  those pairs (was ``forget_match_decision``).

Batch-aware (harness audit 2026-07-19, "make single-entity tools accept batches"):
``resolve_match_review``/``forget_match_decision`` accepted a scalar-or-array id (the SAME
action/removal applies to every id — the common "clear the queue" shape); ``merge_transactions``
gains an array ``pairs`` param (each pair's canonical_id/absorbed_id differ, so there is no
shared scalar field to list). Every batch item is resolved INDEPENDENTLY and reports its own
{ok, ...} or {ok: False, error} outcome — a batch never aborts wholesale on one bad item, and
resolving one review/pair that invalidates another (a shared transaction already merged/gone)
surfaces as THAT item's own error, never a crash. ``find_duplicates`` (range-scoped, not
id-scoped) and ``swap_review_canonical`` (both fields vary independently per swap, no shared
field to batch on) are left singular — see their own docstrings for why.

**Renamed 2026-07-19 (owner complaint — a singular param name that accepts a list is a
trap: the model reads the singular, wants several, and STRINGIFIES the list instead of
passing an array):** ``resolve_match_review`` → ``resolve_match_reviews`` (``review_id`` →
``review_ids``); ``forget_match_decision`` → ``forget_match_decisions`` (``pair_key`` →
``pair_keys``). Both still take a scalar OR an array under the new plural name — a single
id stays a trivial one-line call. The old names were migrated, not aliased (a tool NAME is
persisted data; the param name is not, so
no alias is needed for it — the model always sees the current schema on its next call).
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools import _txn_schema
from backend.ai.tools._shared import _txn_data_from_ai
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.services import labels, match_preflight, match_review
from backend.services.matching import SCORE_STRONG


def _slim(txn: dict | None) -> dict | None:
    """The full transaction row plus flattened amount/currency convenience fields — a prior
    curated subset hid from_account_id/to_account_id (the exact fields the matcher's own
    transfer-pair logic branches on), category, and payee_id from the model reviewing the
    match (root-caused 2026-07-09). Mirrors match_review._enrich_txn's pattern."""
    if not txn:
        return None
    amt = txn.get("from_amount") if txn.get("from_amount") is not None else txn.get("to_amount")
    return {
        **txn,
        "amount": amt,
        "currency": txn.get("from_currency") or txn.get("to_currency"),
    }


class FindDuplicatesInput(ToolInput):
    """Schema-of-record for ``find_duplicates`` (see ``schema_from_model``)."""

    date_from: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD lower bound (optional)."
    )
    date_to: Optional[str] = Field(
        default=None, description="ISO YYYY-MM-DD upper bound (optional)."
    )


@tool(
    "find_duplicates",
    description=(
        "Scan the ledger for likely DUPLICATE transactions and TRANSFER PAIRS (the same charge "
        "entered via two sources, or the two legs of one transfer like a bank→wallet top-up). "
        "Deterministic — uses the match engine's amount/date/description rules and never merges "
        "anything; it just reports candidate pairs for you to check. Each pair has a klass "
        "(near_duplicate | transfer_pair), a score, a become_transfer flag, the reasons, and both "
        "transactions. Each transaction is claimed by AT MOST ONE pair — the scan resolves a "
        "global best-scoring 1:1 assignment across the whole range first, so the returned set is "
        "safe to act on without double-counting a row or attempting two merges that contradict "
        "each other. Optionally bound by date range; a huge unbounded range is refused with an "
        "error naming the row cap — narrow date_from/date_to and re-scan. Call merge_transactions to act on a pair. This scans rows ALREADY booked — for rows you "
        "have not written yet, use check_duplicates instead."
    ),
    input_schema=schema_of(FindDuplicatesInput, tool_name="find_duplicates"),
)
async def find_duplicates(input_data: dict):
    try:
        pairs = match_review.scan_duplicates(input_data.get("date_from"), input_data.get("date_to"))
    except ValidationError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), None
    out = [
        {
            "signature": p["signature"],
            "klass": p["klass"],
            "score": p["score"],
            "become_transfer": p["become_transfer"],
            "reasons": p["reasons"],
            "incoming": _slim(p["incoming"]),
            "canonical": _slim(p["canonical"]),
        }
        for p in pairs
    ]
    return json.dumps({"pairs": out, "count": len(out)}, ensure_ascii=False), None


# ── check_duplicates — the PRE-write half of the surface ────────────────────────────────
#
# The item schema is a PROPER SUBSET of add_transactions' own (built from the same shared field
# table in _txn_schema), plus a caller-supplied `ref`. That subsetting is what makes "paste the
# rows you were about to write" literally true: every create field this tool does not advertise
# is one the match engine cannot read anyway, and _txn_data_from_ai ignores it.
_PREFLIGHT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        **_txn_schema.preflight_item_properties(),
        "ref": {
            "type": "string",
            "description": (
                "Your own label for this row (an email id, a statement line number) — echoed "
                "back on its result so you can map answers to rows without counting indexes."
            ),
        },
    },
    "required": ["account_id", "amount", "currency"],
}


def _preflight_existing(txn: dict | None, maps) -> dict | None:
    """The matched ledger row, full, with its opaque category/payee/account ids labelled.

    Same "return everything" contract as ``_slim``/``_project_txn`` — never a curated subset
    (2026-07-09 scar), and never a caller-chosen projection: this is a money-bearing read and a
    model that could select ``amount`` over ``base_amount`` would defeat the historical-rate lock.
    """
    return _slim(labels.label_txn(txn, maps)) if txn else None


@tool(
    "check_duplicates",
    description=(
        "Checks transactions you have NOT written yet against the ledger — the pre-write twin "
        "of find_duplicates (which scans rows already booked). Writes NOTHING: no transactions, "
        "no review items, no decisions.\n\n"
        "Use it when a row came from a source that can repeat itself: a card alert and the "
        "later statement line, a bank alert and a Giro alert for one payment, a re-forwarded "
        "receipt, an entry the user may already have logged.\n\n"
        "Pass the same items you would pass to add_transactions (extra fields are ignored). "
        "Each result carries your `ref` and `index`, the engine's band (strong | review | weak "
        "| none) and, per candidate, klass (exact | near_duplicate | transfer_pair | "
        "transfer_leg_echo), score, reasons and the matched transaction — plus "
        "recommended_action: skip (already recorded — booking it duplicates real money), "
        "confirm (ambiguous — ask the user), create (no duplicate evidence). Rows are checked "
        "against each other too: duplicate_of_index names another row in this list."
    ),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "transactions": {
                "type": "array",
                "minItems": 1,
                "description": "The rows you are considering booking. Nothing here is written.",
                "items": _PREFLIGHT_ITEM_SCHEMA,
            },
            "max_candidates": {
                "type": "integer",
                "description": (
                    f"Candidates to return per row (1-{match_preflight.MAX_CANDIDATES_PER_ROW}, "
                    f"default {match_preflight.DEFAULT_CANDIDATES_PER_ROW}). Raise it only when "
                    "a result reports candidates_total above what it showed."
                ),
            },
        },
        "required": ["transactions"],
    },
)
async def check_duplicates(input_data: dict):
    """Read-only: classify unwritten rows against the ledger and each other.

    Per-row normalisation failures are reported PER ROW rather than aborting the call (unlike
    ``add_transactions``, where atomicity of the WRITE is the point) — this call writes nothing,
    so there is no partial-batch hazard, and a caller checking 20 rows should not lose 19 answers
    to one malformed date. The rows that did normalise are still classified as ONE batch, so
    cross-row exclusivity and sibling matching are unaffected.
    """

    raw = input_data.get("transactions")
    if isinstance(raw, dict):  # a single item passed unwrapped
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return json.dumps(
            {"error": "transactions must be a non-empty array of rows to check"},
            ensure_ascii=False,
        ), None
    if len(raw) > match_preflight.MAX_PROPOSED_ROWS:
        return json.dumps(
            {
                "error": (
                    f"{len(raw)} rows exceeds the {match_preflight.MAX_PROPOSED_ROWS}-row cap for "
                    "one check — split into smaller calls (20-25 rows each, matching the "
                    "guidance for the write itself). Nothing was checked."
                )
            },
            ensure_ascii=False,
        ), None

    today_iso = user_today().isoformat()
    refs = [(item.get("ref") if isinstance(item, dict) else None) for item in raw]
    normalised: list[dict] = []
    submitted_at: list[int] = []  # engine index -> submitted index
    failed: dict[int, str] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            failed[i] = f"expected an object, got {item!r}"
            continue
        try:
            normalised.append(_txn_data_from_ai(item, default_date=today_iso))
            submitted_at.append(i)
        except (ValueError, KeyError, ValidationError) as e:
            failed[i] = str(e)

    # Handler leniency, per the group convention: a stringified integer is coerced, anything
    # unusable falls back to the default rather than failing a call that writes nothing.
    try:
        want = int(input_data.get("max_candidates") or 0)
    except (TypeError, ValueError):
        want = 0
    want = want or match_preflight.DEFAULT_CANDIDATES_PER_ROW
    try:
        checked = match_preflight.check_proposed_rows(normalised, max_candidates=want)
    except ValidationError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), None

    maps = labels.label_maps(categories=True, payees=True, accounts=True)
    by_submitted: dict[int, dict] = {}
    for res in checked:
        sub = submitted_at[res["index"]]
        dup = res["duplicate_of_index"]
        by_submitted[sub] = {
            **res,
            "index": sub,
            "duplicate_of_index": None if dup is None else submitted_at[dup],
            "candidates": [
                {
                    **c,
                    "existing": _preflight_existing(c["existing"], maps),
                    "proposed_index": (
                        None if c["proposed_index"] is None else submitted_at[c["proposed_index"]]
                    ),
                }
                for c in res["candidates"]
            ],
        }

    results = []
    for i in range(len(raw)):
        row = by_submitted.get(i) or {"index": i, "error": failed.get(i, "not checked")}
        if refs[i]:
            row = {"ref": refs[i], **row}
        results.append(row)

    flagged = sum(
        1
        for r in results
        if (r.get("recommended_action") or {}).get("action") in ("skip", "confirm")
    )
    payload = {
        "results": results,
        "showing": len(results),
        "total": len(raw),
        "flagged": flagged,
        "errors": len(failed),
    }
    withheld = sum(
        1 for r in results if r.get("candidates_total", 0) > r.get("candidates_showing", 0)
    )
    if withheld:
        payload["note"] = (
            f"{withheld} row(s) matched more candidates than shown (highest-scoring first) — "
            "re-check those rows with a higher max_candidates to see the rest."
        )
    return json.dumps(payload, ensure_ascii=False), None


def _recommended_action(
    klass: str | None, score: float | None, become_transfer: bool, band: str | None = None
) -> dict:
    """Derive the recommended action for a review item so the model knows what to do by default.

    The confidence cut was an ad hoc ``score >= 0.9`` (harness audit 2026-07-19 — not grounded
    in anything the match engine itself measures). Grounded fix: reuse ``SCORE_STRONG`` (0.80),
    the SAME boundary ``matching._band``/``match_batch`` already use to call a match "strong"
    (confident enough to be auto-merge-eligible with L3 agreement) vs. merely "review" (every
    item reaching this queue already cleared ``SCORE_FENCE``, the review floor, by construction —
    the only question left is strong vs. ambiguous). Prefer the engine's OWN precomputed ``band``
    when the review item carries one (stamped by ``review_created_rows`` off ``RowMatch.band`` —
    single source of truth); fall back to recomputing from ``score`` for review items enqueued
    before that field existed.

    Rules (in priority order):
    - transfer_pair + become_transfer => accept; the pair becomes a single transfer entry.
    - near_duplicate at the engine's "strong" band (score >= SCORE_STRONG) => accept.
    - otherwise => inspect; dates/notes may differ enough to keep both.
    """
    if klass == "transfer_pair" and become_transfer:
        return {
            "action": "accept",
            "note": "converts the pair into a single transfer between the two accounts",
        }
    is_strong = band == "strong" if band is not None else (score or 0) >= SCORE_STRONG
    if klass == "near_duplicate" and is_strong:
        return {
            "action": "accept",
            "note": "true duplicate — keep one; accepting merges the incoming into the canonical",
        }
    return {
        "action": "inspect",
        "note": "inspect dates/notes before deciding",
    }


class ListMatchReviewsInput(ToolInput):
    """Schema-of-record for ``list_match_reviews`` (see ``schema_from_model``)."""

    batch_id: Optional[str] = Field(
        default=None, description="Only items from this import batch (optional)."
    )
    review_ids: Optional[List[str]] = Field(
        default=None, description="Only these ids; omit for every pending review."
    )


@tool(
    "list_match_reviews",
    description=(
        "List the pending duplicate/transfer-pair review items the match engine surfaced during "
        "imports and connector syncs (rows it created but flagged as a likely duplicate). Each has "
        "the candidate klass, score, reasons, both transactions, any tied alternatives, and a "
        "recommended_action field that tells you what to do: 'accept' for high-confidence matches "
        "(transfer pairs with become_transfer, or near_duplicates at the engine's own 'strong' "
        "confidence band), 'inspect' for lower-confidence cases (every queued item already "
        "cleared the engine's review floor — 'inspect' means ambiguous, not weak). "
        "superseded_count shows how many stale items were filtered out of this listing. "
        "Use resolve_match_reviews "
        "to accept (merge) or dismiss (keep both) each, or swap_review_canonical to prefer a "
        "tied alternative first."
    ),
    input_schema=schema_of(ListMatchReviewsInput, tool_name="list_match_reviews"),
    # A genuine read since 2026-08-23. The zombie sweep does two things — it FILTERS stale
    # items out of the result, and it STAMPS them 'superseded' — and only the second is a
    # write. This passes `persist_sweep=False`, so the model still never sees a review
    # pointing at a deleted transaction (which it could act on) while the tool writes
    # nothing. The ingest/judging path and the REST endpoint still persist.
)
async def list_match_reviews(input_data: dict):
    items, superseded_count = match_review.list_reviews(
        input_data.get("batch_id"), persist_sweep=False
    )
    review_ids = input_data.get("review_ids")
    if review_ids:
        wanted = {str(r) for r in review_ids if r}
        items = [it for it in items if it.get("id") in wanted]
    out = [
        {
            "id": it["id"],
            "klass": it.get("klass"),
            "score": it.get("score"),
            "become_transfer": it.get("become_transfer"),
            "reasons": it.get("reasons"),
            "alternatives": [_slim(a) for a in (it.get("alt_txns") or [])],
            "incoming": _slim(it.get("incoming")),
            "canonical": _slim(it.get("canonical")),
            "recommended_action": _recommended_action(
                it.get("klass"),
                it.get("score"),
                bool(it.get("become_transfer")),
                it.get("band"),
            ),
        }
        for it in items
    ]
    return (
        json.dumps(
            {"reviews": out, "count": len(out), "superseded_count": superseded_count},
            ensure_ascii=False,
        ),
        None,
    )


class ListMatchDecisionsInput(ToolInput):
    """Schema-of-record for ``list_match_decisions`` (see ``schema_from_model``)."""

    limit: Optional[int] = Field(default=None, description="Max results (1–50, default 50).")
    offset: Optional[int] = Field(
        default=None,
        description="Zero-based row offset (default 0) — page through older decisions.",
    )


@tool(
    "list_match_decisions",
    description=(
        "List the most recent remembered dedup verdicts (the engine's verdict cache). Each entry "
        "shows what the engine or a user decided about a specific transaction pair: 'same' (merged), "
        "'different' (dismissed, will not be re-surfaced), or 'pair' (transfer pair). Use "
        "forget_match_decisions to clear a verdict so the engine re-evaluates that pair. "
        "Returns {decisions, showing, total, offset}, newest first; pass offset to page "
        "through older decisions rather than only ever seeing the newest 50."
    ),
    input_schema=schema_of(ListMatchDecisionsInput, tool_name="list_match_decisions"),
)
async def list_match_decisions(input_data: dict):
    limit = min(max(1, int(input_data.get("limit") or 50)), 50)
    offset = max(0, int(input_data.get("offset") or 0))
    result = match_review.list_decisions(limit, offset=offset)
    result["showing"] = len(result["decisions"])
    end = offset + result["showing"]
    if end < result["total"]:
        result["note"] = (
            f"Showing decisions {offset + 1}–{end} of {result['total']}. Page with offset={end}."
        )
    return json.dumps(result, ensure_ascii=False), None


class MergePairItem(NestedInput):
    """Schema-of-record for a ``merge_transactions.pairs`` batch item (see
    ``schema_from_model``). BARE-typed on purpose — the field docs live once, on the
    top-level singular fields below; repeating them per-item would double the cost of
    every description char for a call that never uses them (see the analogous rules.py
    ``_RULE_BATCH_ITEM_SCHEMA`` comment)."""

    canonical_id: str = Field()
    absorbed_id: str = Field()
    become_transfer: Optional[bool] = Field(default=None)
    reason: Optional[str] = Field(default=None)


class MergeTransactionsInput(ToolInput):
    """Schema-of-record for ``merge_transactions`` (see ``schema_from_model``)."""

    canonical_id: Optional[str] = Field(default=None, description="The transaction id to KEEP.")
    absorbed_id: Optional[str] = Field(
        default=None, description="The transaction id to fold in (tombstoned)."
    )
    become_transfer: Optional[bool] = Field(
        default=None,
        description="Rebuild the canonical as a transfer across both accounts. Default false.",
    )
    reason: Optional[str] = Field(default=None, description="Why these are the same (audited).")
    pairs: List[MergePairItem] = Field(
        default_factory=list,
        description="Merge several at once, each independently.",
    )


@tool(
    "merge_transactions",
    description=(
        "Merge two transactions that are the SAME real-world charge (or the two legs of one "
        "transfer): the canonical row survives and absorbs the other's source id (so a re-import "
        "stays idempotent), and the absorbed row is tombstoned (recoverable). Set become_transfer "
        "to rebuild the canonical as a two-leg transfer between the two accounts (keeping the "
        "earlier date) — use this for a bank→wallet top-up or a card payment on both statements. "
        "Identify the specific pair first (use find_duplicates). Reversible: the pre-merge rows are journaled and restorable from the UI (no "
        "undo TOOL), but the restore refuses once the surviving row is edited. A field distinct "
        "on the absorbed row is NEVER silently dropped: memo/tags/an unlinked category or payee "
        "are adopted onto the canonical when it lacked one (reported as preserved_fields), and a "
        "genuine conflict (both rows set, and different) is reported as field_conflicts instead "
        "of silently picked either way — re-apply it yourself (e.g. update_transactions) if it "
        "matters."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        MergeTransactionsInput, array="pairs", tool_name="merge_transactions"
    ),
    mutates=("ledger",),
)
async def merge_transactions(input_data: dict):
    raw_pairs = input_data.get("pairs")
    if raw_pairs is not None:
        if not isinstance(raw_pairs, list) or not raw_pairs:
            return json.dumps(
                {"error": "pairs must be a non-empty array"}, ensure_ascii=False
            ), None
        results = []
        merged_count = 0
        for i, p in enumerate(raw_pairs):
            if not isinstance(p, dict) or not p.get("canonical_id") or not p.get("absorbed_id"):
                results.append(
                    {
                        "index": i,
                        "ok": False,
                        "error": "each pair needs canonical_id and absorbed_id",
                    }
                )
                continue
            try:
                merged, info = match_review.merge_transactions_preserving_fields(
                    p["canonical_id"],
                    p["absorbed_id"],
                    reason=p.get("reason") or "merged via assistant",
                    become_transfer=bool(p.get("become_transfer", False)),
                )
                results.append({"index": i, "ok": True, "merged": _slim(merged), **info})
                merged_count += 1
            except (ValidationError, NotFoundError, ConflictError) as e:
                results.append({"index": i, "ok": False, "error": str(e)})
        return json.dumps(
            {
                "ok": True,
                "results": results,
                "merged_count": merged_count,
                "failed": len(raw_pairs) - merged_count,
                "showing": len(results),
                "total": len(raw_pairs),
            },
            ensure_ascii=False,
        ), None

    if not input_data.get("canonical_id") or not input_data.get("absorbed_id"):
        return json.dumps(
            {"error": "canonical_id and absorbed_id are required (or pass `pairs` for several)"},
            ensure_ascii=False,
        ), None
    try:
        merged, info = match_review.merge_transactions_preserving_fields(
            input_data["canonical_id"],
            input_data["absorbed_id"],
            reason=input_data.get("reason") or "merged via assistant",
            become_transfer=bool(input_data.get("become_transfer", False)),
        )
        return (
            json.dumps({"merged": _slim(merged), "ok": True, **info}, ensure_ascii=False),
            None,
        )
    except (ValidationError, NotFoundError, ConflictError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), None


def _as_id_list(raw) -> list[str]:
    """Normalize a scalar-or-array id field to a clean list of non-empty string ids."""
    ids = raw if isinstance(raw, list) else [raw] if raw else []
    return [str(i) for i in ids if i]


class ResolveMatchReviewsInput(ToolInput):
    """Schema-of-record for ``resolve_match_reviews`` (see ``schema_from_model``)."""

    review_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="The review id, or array of ids to resolve with the SAME action.",
    )
    action: Literal["accept", "dismiss"] = Field(
        description=(
            "accept = merge (canonical absorbs incoming; transfer_pair+become_transfer "
            "rebuilds as a unified transfer). dismiss = keep both rows permanently."
        )
    )


@tool(
    "resolve_match_reviews",
    description=(
        "Resolve one or more pending match-review items with the SAME action. "
        "accept — merges the pair (canonical survives; incoming tombstoned); for "
        "transfer_pair+become_transfer rebuilds as a single two-leg transfer. Journaled and "
        "restorable from the UI (no undo TOOL); the restore refuses once the surviving row "
        "is edited. A field distinct on the incoming row is never silently dropped — see "
        "merge_transactions' own note on preserved_fields/field_conflicts, both returned here too. "
        "dismiss — asserts the rows are NOT duplicates; both rows stay permanently and the pair "
        "is remembered as 'different'. WARNING: do NOT dismiss an item at the engine's own "
        "'strong' confidence band unless you can explain why they differ — a strong-band "
        "dismissal leaves a visible duplicate. When in doubt, accept. "
        "Get ids from list_match_reviews. If a review "
        "has tied alternatives and a different one is the true duplicate, call "
        "swap_review_canonical first.\n\n"
        "Pass review_ids as a LIST to clear a whole queue at once with the SAME action."
    ),
    input_schema=schema_of(ResolveMatchReviewsInput, tool_name="resolve_match_reviews"),
    mutates=("ledger",),
)
async def resolve_match_reviews(input_data: dict):
    action = input_data.get("action")
    if action not in ("accept", "dismiss"):
        return json.dumps(
            {"error": "action must be 'accept' or 'dismiss'"}, ensure_ascii=False
        ), None

    ids = _as_id_list(input_data.get("review_ids"))
    if not ids:
        return json.dumps({"error": "review_ids is required"}, ensure_ascii=False), None

    results = []
    ok_count = 0
    for rid in ids:
        try:
            if action == "accept":
                merged, info = match_review.accept_review(rid)
                results.append({"review_id": rid, "ok": True, "merged": _slim(merged), **info})
            else:
                r = match_review.dismiss_review(rid)
                if r is None:
                    results.append(
                        {"review_id": rid, "ok": False, "error": "Review item not found"}
                    )
                    continue
                results.append({"review_id": rid, "ok": True, "dismissed": True})
            ok_count += 1
        except (ValidationError, NotFoundError, ConflictError) as e:
            results.append({"review_id": rid, "ok": False, "error": str(e)})

    if len(ids) == 1:
        only = results[0]
        if only["ok"]:
            payload = {k: v for k, v in only.items() if k not in ("review_id", "ok")}
            return json.dumps({"ok": True, **payload}, ensure_ascii=False), None
        return json.dumps({"error": only["error"]}, ensure_ascii=False), None

    return json.dumps(
        {
            "ok": True,
            "results": results,
            "resolved": ok_count,
            "failed": len(ids) - ok_count,
            "showing": len(results),
            "total": len(ids),
        },
        ensure_ascii=False,
    ), None


class SwapReviewCanonicalInput(ToolInput):
    """Schema-of-record for ``swap_review_canonical`` (see ``schema_from_model``)."""

    review_id: str = Field(description="The review item id (from list_match_reviews).")
    new_canonical_id: str = Field(
        description="The transaction id to make canonical — current canonical or a tied alternative."
    )


@tool(
    "swap_review_canonical",
    description=(
        "Swap a pending match-review's canonical to one of its tied alternatives (F20). Use when "
        "list_match_reviews shows more than one existing transaction plausibly matching the "
        "incoming row and the currently-picked canonical isn't the right one — e.g. two "
        "same-day, same-amount charges to the same merchant, and the OTHER one is the true "
        "duplicate. new_canonical_id must be the review's current canonical_id or one of its "
        "alternatives (from list_match_reviews). Returns the updated, re-enriched review item; "
        "call resolve_match_reviews next to actually accept or dismiss it."
    ),
    input_schema=schema_of(SwapReviewCanonicalInput, tool_name="swap_review_canonical"),
    mutates=("ledger",),
)
async def swap_review_canonical(input_data: dict):
    # Left singular (harness audit 2026-07-19 batching pass): both review_id AND
    # new_canonical_id vary independently per swap — there is no shared field a batch could
    # apply "the same value" across, unlike resolve_match_reviews' shared `action`. A genuine
    # batch would need an array of (review_id, new_canonical_id) pairs with no ergonomic win
    # over calling this once per swap, and F20 (just shipped) has no measured multi-swap usage
    # to justify the added schema complexity.
    try:
        updated = match_review.swap_review_canonical(
            input_data["review_id"], input_data["new_canonical_id"]
        )
        return json.dumps({"ok": True, "review": updated}, ensure_ascii=False), None
    except (ValidationError, NotFoundError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False), None


class ForgetMatchDecisionsInput(ToolInput):
    """Schema-of-record for ``forget_match_decisions`` (see ``schema_from_model``)."""

    pair_keys: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="The pair_key, or array of pair_keys to forget.",
    )


def _is_user_recorded_external_identity(decision: dict) -> bool:
    """True for a ``detach_external_ref``-recorded decision (security review L3b): keyed
    ``source="user"`` with ``incoming_id`` set to an EXTERNAL identity
    (``services.ledger._external_identity``) rather than a second transaction id (every
    ``txn_``-prefixed id). The model may forget its OWN verdicts (llm/rule-sourced same/
    different calls between two ledger rows); it may never unilaterally forget a decision
    the USER made by detaching a source in the drawer — that decision is what stops the
    detached pair from re-colliding on the next fetch, and forgetting it unattended would
    silently re-arm a refusal the user deliberately turned off."""
    if decision.get("source") != "user":
        return False
    incoming_id = str(decision.get("incoming_id") or "")
    return not incoming_id.startswith("txn_")


@tool(
    "forget_match_decisions",
    description=(
        "Remove one or more remembered dedup verdicts so the engine re-evaluates those pairs on "
        "the next import or scan. Useful when a 'different' verdict was wrong (the pair IS a "
        "duplicate) or when a 'same' verdict applies to rows that have since been split. Get the "
        "pair_key(s) or signature from list_match_decisions. Not reversible — each pair will be "
        "re-surfaced to the review queue on next import. Pass pair_keys as a LIST to forget "
        "several at once. Refuses a decision the USER recorded by detaching a source link in "
        "the drawer — you may forget your own verdicts, never the user's."
    ),
    input_schema=schema_of(ForgetMatchDecisionsInput, tool_name="forget_match_decisions"),
    mutates=("ledger",),
)
async def forget_match_decisions(input_data: dict):
    ids = _as_id_list(input_data.get("pair_keys"))
    if not ids:
        return json.dumps({"error": "Decision not found", "ok": False}, ensure_ascii=False), None

    forgotten: list[str] = []
    not_found: list[str] = []
    protected: list[str] = []
    for pk in ids:
        decision = match_review.decisions.find_by_id(pk)
        if decision is None:
            not_found.append(pk)
            continue
        if _is_user_recorded_external_identity(decision):
            protected.append(pk)
            continue
        (forgotten if match_review.forget_decision(pk) else not_found).append(pk)

    if len(ids) == 1:
        if forgotten:
            return json.dumps({"ok": True}, ensure_ascii=False), None
        if protected:
            return json.dumps(
                {
                    "error": (
                        "This decision was recorded by the USER (a detached source link), "
                        "not by you — you may forget your own verdicts, never the user's. "
                        "If they want the link restored, re-attach it with "
                        "add_transaction_refs instead."
                    ),
                    "ok": False,
                },
                ensure_ascii=False,
            ), None
        return json.dumps({"error": "Decision not found", "ok": False}, ensure_ascii=False), None

    return json.dumps(
        {
            "ok": True,
            "forgotten": forgotten,
            "not_found": not_found,
            "protected": protected,
            "showing": len(ids),
            "total": len(ids),
        },
        ensure_ascii=False,
    ), None
