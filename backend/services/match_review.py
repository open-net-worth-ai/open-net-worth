"""match_review — orchestration over the Match engine: run it on freshly-created rows, then
COMBINE the deterministic verdict with the user's ``dedup_policy`` (+ the LLM verdict in Phase 2)
into a final action, performing auto-merges and enqueuing everything else for review.

This is the service-level brain that sits between the engine (pure decision) and persistence
(merge primitive + review queue). Every ingest surface (import commit, connector sync, email,
AI bulk-add) calls ``review_created_rows`` after it has written rows through the ledger choke —
the rows are created-then-flagged (RFC §user-controlled policy & review ergonomics), so import
semantics are unchanged and a flagged row is never silently dropped.

Phase 1 is deterministic-only: nothing auto-merges (the agreement gate needs L3); matches are
enqueued for the operator. Phase 2 wires the LLM verdict through ``combine`` so an agreed match
auto-merges per policy. Layer: ``services → db → core`` (+ sibling ``ledger``/``matching``).

Hardening fixes:
  F02 — verdicts are now keyed on the concrete (incoming_id, canonical_id) pair, not signature.
        The remembered-'different' skip also requires exact pair-id match.
  F04 — dismiss_review accepts source= so judge passes source='llm' preserving llm_reason;
        list_decisions/forget_decision service helpers for the REST + AI surface.
  F10 — known_transfer_routes and ledger window are computed ONCE per review_created_rows /
        scan_duplicates pass (not once per row).
  F11 — queue dedup on concrete (incoming_id, canonical_id) pair. Canonical-claim tracking
        (one canonical never claimed by two reviews in the same pass) is now internal to
        ``matching.match_batch``'s batch-level global 1:1 assignment (RFC batch-aware-dedup,
        Phase 4) — the old sequential first-come-wins claim loop is gone; a later, better-
        scoring row now wins a contested canonical over an earlier one.
  F17 — zombie review sweep in list_reviews and judge_reviews_async: dead items get status
        'superseded'; accept_review validates liveness of both rows first.
  F20 — tie alternatives stored on review items; auto-merge path refuses when tie exists;
        swap_review_canonical service function for the REST swap-canonical endpoint. Sourced
        straight from ``RowMatch.alternatives`` since Phase 4.
"""

from __future__ import annotations

import logging

from backend.core.write_receipt import record_write
from backend.db.match_decision_store import MatchDecisionStore
from backend.db.match_review_store import MatchReviewStore
from backend.services import audit as audit_service
from backend.services import ledger as ledger_service
from backend.services import matching

logger = logging.getLogger(__name__)

reviews = MatchReviewStore()
decisions = MatchDecisionStore()


def _policy() -> str:
    from backend.services import profile as profile_service

    return profile_service.normalize_dedup_policy(
        profile_service.read_profile().get("dedup_policy")
    )


# ── Zombie-review helpers ──────────────────────────────────────────────────────


def _is_live(txn: dict | None) -> bool:
    """A row is live if it exists, is not deleted, and has not been merged into another row."""
    return txn is not None and not txn.get("deleted") and not txn.get("merged_into")


def _enrich_txn(txn: dict | None) -> dict | None:
    """Add flat `amount` and `currency` fields expected by the frontend MatchTxn shape."""
    if not txn:
        return None
    amount = txn.get("from_amount") if txn.get("from_amount") is not None else txn.get("to_amount")
    currency = txn.get("from_currency") or txn.get("to_currency") or ""
    return {**txn, "amount": amount, "currency": currency}


def _sweep_zombies(
    pending_items: list[dict],
    txns_by_id: dict[str, dict] | None = None,
    *,
    persist: bool = True,
) -> tuple[list[dict], int]:
    """Supersede pending items whose incoming or canonical row is no longer live.

    Returns (live_items, superseded_count). Mutates the store for each zombie — sets status
    to 'superseded' WITHOUT writing a decision record (F17: zombie resolution is not a verdict).

    ``persist=False`` does the FILTERING without the write. The two halves are separable and
    only one of them is a mutation: a caller that merely wants to SHOW the pending items still
    needs zombies excluded (a review pointing at a deleted transaction is something a reader
    could act on), but it has no business stamping the store to do it. Used by
    ``list_reviews(persist_sweep=False)`` so a `list_` tool is not a writer — 2026-08-23.

    ``txns_by_id`` (optional): an already-loaded id->txn map — ``list_reviews`` builds ONE
    and shares it between this sweep and its own enrich loop (they each paid for a full
    ledger parse before, 2026-08-12)."""
    live: list[dict] = []
    superseded = 0
    if txns_by_id is None:
        txns_by_id = {t["id"]: t for t in ledger_service.transactions.all()}
    for item in pending_items:
        incoming = txns_by_id.get(item.get("incoming_id"))
        canonical = txns_by_id.get(item.get("canonical_id"))
        if not _is_live(incoming) or not _is_live(canonical):
            if persist:
                reviews.set_status(item["id"], "superseded")
            superseded += 1
        else:
            live.append(item)
    return live, superseded


# ── Main ingest path ───────────────────────────────────────────────────────────


def _result_reasons(cand: matching.Candidate) -> list[str]:
    """Mirror ``check()``'s own ``MatchResult.reasons`` shape — used to build the synthetic
    ``MatchResult`` that ``combine()`` and the review item's stored ``reasons`` field both
    need. No klass/score debug prefix (owner, 2026-07-27) — kept in lockstep with check()'s
    own reasons, which dropped it for the same reason: it leaked into user-facing text."""
    return list(cand.reasons)


def review_created_rows(
    created: list[dict],
    *,
    batch_id: str | None = None,
    profile: frozenset = matching.SCAN,
) -> dict:
    """Run the engine over ``created`` as ONE BATCH and resolve each row per ``dedup_policy``.

    Returns ``{auto_merged, queued, already_pending, reviews: [ids], pending_reviews: [ids]}``.
    Best-effort: a per-row failure is logged and skipped — dedup review must never break the
    write that already happened.

    ``already_pending``/``pending_reviews`` (owner incident 2026-07-31) report the pairs the F11
    dedup below declined to enqueue a SECOND time because a live review item already covers them.
    Without it, ``queued == 0`` means BOTH "the engine found nothing" and "the engine already
    flagged this, go look at the queue" — the same conflation the bare ``except: pass`` at the
    AI-add call site had. That is not academic: it is how a re-run of this pass over an
    already-flagged duplicate reads as an all-clear, which is exactly how a net that HAD fired
    was mistaken for one that never evaluated the row at all.

    Rebuilt on ``matching.match_batch`` (RFC batch-aware-dedup, Phase 4 — post-commit
    unification): the old sequential first-come-wins F11 claim loop is replaced by
    ``match_batch``'s own batch-level global 1:1 assignment — a contested canonical goes to the
    best-scoring row, not merely the first one processed. Context is always
    ``BatchContext(complete=False)``: a created batch (import/AI-add/connector-sync/email) is
    not a bank statement's completeness claim, so books-close promotion never fires here.

    Self-match (Phase 4 specific): ``created``'s rows are ALREADY-PERSISTED ledger transactions
    (the write choke ran before this is ever called), so ``match_batch``'s own ledger-pool read
    would otherwise read each row back out of the ledger as a distinct, perfect-score
    "different" candidate for itself. ``matching.match_batch``/``_batch_ledger_pool`` gained an
    additive ``exclude_ids`` parameter for exactly this — dropping the whole batch's own ids
    from the ledger half of the pool. H01 sibling matching (batch rows as candidates for each
    other — opposite-direction intra-batch transfer legs still pair; the _same_feed invariant
    still vetoes same-direction same-batch near-dups) is unaffected, since it comes from
    match_batch's own ``rows`` handling, never the ledger read.

    F10: uses SCAN profile (not HEADLESS) — the rows just passed the L0 floor at the choke point
    and L0 is redundant here. known_transfer_routes() and rates are computed ONCE for the full
    pass and threaded through every match_batch call below (including the degraded per-row
    fallback), so neither is ever recomputed mid-pass.
    F11: queue dedup on concrete (incoming_id, canonical_id) pair, sorted so an intra-batch
    transfer pair's two legs (A→B and B→A) collapse to one review.
    F02b: combine() is never called with a cached verdict at ingest — the remembered-'different'
    check uses get_for_pair (exact pair ids), and combine() is always called with llm_verdict=None.
    F20: alternatives are read straight off ``RowMatch.alternatives`` (match_batch's own tie
    computation), not re-derived here.
    """
    policy = _policy()
    # F10: compute these ONCE for the full pass (not per-row, not re-derived by match_batch).
    rates = matching._load_rates()
    known_routes = matching.known_transfer_routes()
    auto_merged = 0
    queued: list[str] = []
    already_pending: list[str] = []

    # F11: dedup pending enqueues on a SORTED (id_a, id_b) pair so that neither direction
    # (A→B or B→A) creates a duplicate when both legs of an intra-batch transfer are processed.
    # Mapped to the item id (not a bare set) so a skip can NAME the review already covering the
    # pair instead of vanishing into a bare zero.
    enqueued_pairs: dict[tuple[str, str], str] = {
        tuple(sorted([r["incoming_id"], r["canonical_id"]])): r["id"]  # type: ignore[misc]
        for r in reviews.pending()
        if r.get("incoming_id") and r.get("canonical_id")
    }

    # A created row may already be dead (no id, or merged away by an earlier step in this same
    # request) — drop it before it ever reaches the engine, exactly like the old per-row guard.
    valid_rows = [r for r in created if r.get("id") and not r.get("merged_into")]
    row_ids: frozenset[str] = frozenset(r["id"] for r in valid_rows)
    ctx = matching.BatchContext(complete=False)

    try:
        row_matches = matching.match_batch(
            valid_rows,
            context=ctx,
            profile=profile,
            rates=rates,
            known_routes=known_routes,
            exclude_ids=row_ids,
        )
        row_results = list(zip(valid_rows, row_matches))
    except Exception:  # noqa: BLE001 — one poisoned batch must degrade, never lose every row
        logger.exception(
            "match_review: match_batch failed for the full %d-row pass; retrying row-by-row",
            len(valid_rows),
        )
        # Degrade to isolated singleton batches so one bad row can't sink its batch-mates. This
        # loses cross-row (H01/sibling) matching for the remainder of THIS pass only — an
        # acceptable trade-off for an exceptional path; the common case never reaches here.
        row_results = []
        for row in valid_rows:
            try:
                singleton = matching.match_batch(
                    [row],
                    context=ctx,
                    profile=profile,
                    rates=rates,
                    known_routes=known_routes,
                    exclude_ids=row_ids,
                )
                row_results.append((row, singleton[0]))
            except Exception:  # noqa: BLE001 — review must never break a completed write
                logger.exception(
                    "match_review: engine.match_batch failed for txn %s", row.get("id")
                )

    for row, rm in row_results:
        rid = row.get("id")
        if rm.assigned is None or rm.band == "none":
            continue
        try:
            canonical = ledger_service.transactions.find_by_id(rm.assigned.id)
            if canonical is None:
                continue

            # F02b: use get_for_pair (exact pair ids) for the remembered-'different' check.
            remembered = decisions.get_for_pair(rid, rm.assigned.id)
            if remembered and remembered.get("verdict") == "different":
                continue

            signature = matching.pair_signature(row, canonical, rates)

            # F11/H01: skip if this pair is already in the pending queue. Sorted pair key so
            # A→B and B→A both hit the same dedup entry (both legs of an intra-batch transfer).
            pair_key_tuple: tuple[str, str] = tuple(sorted([rid, rm.assigned.id]))  # type: ignore[assignment]
            covered_by = enqueued_pairs.get(pair_key_tuple)
            if covered_by is not None:
                already_pending.append(covered_by)
                continue

            synthetic = matching.MatchResult(
                action="review",
                candidates=[rm.assigned],
                canonical_id=rm.assigned.id,
                become_transfer=rm.assigned.become_transfer,
                reasons=_result_reasons(rm.assigned),
            )

            # F02b: NEVER pass a cached verdict into combine() at ingest.
            # combine() with llm_verdict=None always returns "review" (Phase 1 floor).
            final = matching.combine(synthetic, policy, llm_verdict=None)
            if final == "auto_merge":
                # Without LLM agreement this never triggers (combine returns "review" when
                # llm_verdict is None), but guard here in case of future policy changes.
                try:
                    merge_transactions_preserving_fields(
                        rm.assigned.id,
                        rid,
                        reason="; ".join(synthetic.reasons) or "auto-merge",
                        become_transfer=synthetic.become_transfer,
                    )
                    decisions.remember(
                        {
                            "incoming_id": rid,
                            "canonical_id": rm.assigned.id,
                            "signature": signature,
                            "verdict": "pair" if synthetic.become_transfer else "same",
                            "become_transfer": synthetic.become_transfer,
                            "source": "rule",
                        }
                    )
                    auto_merged += 1
                    continue
                except Exception:  # noqa: BLE001 — fall through to review on a merge failure
                    logger.exception(
                        "match_review: auto-merge failed for %s→%s", rid, rm.assigned.id
                    )

            item = reviews.enqueue(
                {
                    "incoming_id": rid,
                    "canonical_id": rm.assigned.id,
                    "klass": rm.assigned.klass.value,
                    "score": rm.assigned.score,
                    "become_transfer": synthetic.become_transfer,
                    "signature": signature,
                    "reasons": synthetic.reasons,
                    "batch_id": batch_id,
                    "alternatives": rm.alternatives,
                    # The engine's OWN confidence band ("strong"|"review") — exposed so
                    # _recommended_action (ai/tools/matching.py) reads the SAME grounded
                    # SCORE_STRONG/SCORE_FENCE boundary match_batch already computed instead of
                    # recomputing a second, disconnected threshold (harness audit 2026-07-19).
                    "band": rm.band,
                }
            )
            enqueued_pairs[pair_key_tuple] = item["id"]
            queued.append(item["id"])
        except Exception:  # noqa: BLE001 — review must never break a completed write
            logger.exception("match_review: failed to resolve match for txn %s", rid)
            continue

    if already_pending:
        logger.info(
            "match_review: %d row(s) already covered by a live review item (%s)",
            len(already_pending),
            ", ".join(already_pending),
        )
    return {
        "auto_merged": auto_merged,
        "queued": len(queued),
        "already_pending": len(already_pending),
        "reviews": queued,
        "pending_reviews": already_pending,
    }


def scan_duplicates(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """On-demand housekeeping scan: run the engine over the ledger in a date range and return
    candidate duplicate / transfer-pair pairs WITHOUT enqueuing or merging, safe for an agent/cron
    caller via the ``find_duplicates`` tool.

    Gained GLOBAL 1:1 exclusivity (harness audit 2026-07-19 — the old implementation called
    ``matching.check()`` once PER ROW, independently, so it had no cross-row coordination: the
    same transaction could come back as the CANONICAL of one pair and ALSO the INCOMING of a
    different pair, so a caller acting on the returned set could double-count that row's amount
    or attempt a second merge that fails once the first commits (the write choke itself refuses
    a merge whose canonical/absorbed is already merged — that failure is safe, but the returned
    set could no longer all be actioned as given).

    Classification is still per-row via ``matching.check()`` — NOT ``matching.match_batch``.
    ``match_batch``'s ``_classify_batch_pairs`` stamps every row in its ``rows`` argument with
    ONE shared synthetic ``batch_id`` so the same-feed invariant (F23a) applies across
    siblings — correct for its actual callers (an incoming batch that genuinely all just arrived
    together: one statement, one bulk-add), but WRONG here: a scan's rows can come from
    completely unrelated imports spread over months, and stamping the whole scanned set as "one
    feed" would blanket-veto every legitimate cross-source duplicate pair (verified — this was
    tried first and returned zero pairs for the exact 2x2 scenario ``review_created_rows``
    handles correctly). ``check()`` classifies each row against the ledger pool with its own
    REAL ``batch_id``, so the same-feed veto still fires correctly PER PAIR.

    The 1:1 EXCLUSIVITY itself is layered on top via ``matching._global_claim_assignment`` — the
    SAME shared primitive ``match_batch``/``import_anomalies._enforce_exclusive_matches`` use
    (ADR #30) — applied over these per-row candidate lists instead of match_batch's own
    classification. Every row whose top candidate reached the review floor contributes claims
    (its primary + same-class alternatives at/above SCORE_FENCE, exactly like
    ``_assign_batch_matches``); each existing ledger row is claimed by at most one scanned row,
    and a losing claim falls through to its next-ranked free candidate.

    F10: rates and known_routes computed ONCE for the entire pass.
    F02b: get_for_pair used for the remembered-'different' skip (exact pair-id match).
    F11: a sibling pair can still win in BOTH directions (A's best claim targets B AND B's best
    claim independently targets A — claims are per-identity, not per-pair), deduped on the
    sorted (id_a, id_b) pair exactly like ``review_created_rows``.

    Raises ``ValidationError`` when the (date-bounded) row count exceeds
    ``matching.MAX_BATCH_ROWS`` (the same O(rows × pool) cost backstop ``match_batch`` enforces)
    — narrow date_from/date_to and scan in smaller windows.
    """
    from backend.core.errors import ValidationError

    # F10: compute these ONCE for the full pass.
    rates = matching._load_rates()
    known_routes = matching.known_transfer_routes()
    rows = [
        r
        for r in ledger_service.transactions.query_txns(date_from=date_from, date_to=date_to)
        if not r.get("deleted") and not r.get("merged_into")
    ]
    if len(rows) > matching.MAX_BATCH_ROWS:
        raise ValidationError(
            f"{len(rows)} live transactions in range exceeds the {matching.MAX_BATCH_ROWS}-row "
            "scan cap — narrow date_from/date_to and scan in smaller windows"
        )
    results = [
        matching.check(
            row,
            profile=matching.SCAN,
            rates=rates,
            exclude_ids={row.get("id")},
            known_routes=known_routes,
        )
        for row in rows
    ]

    # Global 1:1 claim assignment (ADR #30 primitive, reused — not re-derived).
    claims: list[tuple] = []
    for idx, (row, result) in enumerate(zip(rows, results)):
        if result.action != "review" or not result.candidates:
            continue
        klass = result.candidates[0].klass
        rank = 0
        for cand in result.candidates:
            if cand.klass != klass or cand.score < matching.SCORE_FENCE:
                continue
            # No sibling batch here (every candidate is a real persisted ledger row) — an
            # empty sibling map makes _claim_target resolve every target by ledger id
            # (still echo-side-qualified for transfer_leg_echo).
            target = matching._claim_target({}, cand)
            if target is None:
                continue
            key = (
                -cand.score,
                cand.fidelity,
                matching._date_gap(row.get("date"), cand),
                idx,
                rank,
            )
            claims.append((key, row.get("id"), target, cand))
            rank += 1
    won = matching._global_claim_assignment(claims, fidelity_at=1)

    # F11: dedup by concrete pair, not just signature.
    seen_pairs: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        rid = row.get("id") or ""
        cand = won.get(rid)
        if cand is None:
            continue
        canonical = cand.txn
        canonical_id = canonical.get("id") or ""
        # F02b: use exact pair-id check for remembered-'different'.
        remembered = decisions.get_for_pair(rid, canonical_id)
        if remembered and remembered.get("verdict") == "different":
            continue
        pair_tuple = tuple(sorted([rid, canonical_id]))
        if pair_tuple in seen_pairs:
            continue
        seen_pairs.add(pair_tuple)
        sig = matching.pair_signature(row, canonical, rates)
        out.append(
            {
                "signature": sig,
                "klass": cand.klass.value,
                "score": cand.score,
                "become_transfer": cand.become_transfer,
                "reasons": _result_reasons(cand),
                "incoming_id": rid,
                "canonical_id": canonical_id,
                "incoming": row,
                "canonical": canonical,
            }
        )
    return out


async def judge_reviews_async(batch_id: str | None = None) -> dict:
    """Phase 2 — the LLM (L3) judgment pass over the pending review queue.

    F17: sweeps zombies BEFORE consulting the cache or the LLM (no spend on dead pairs).
    F02c: reuses a cached verdict ONLY when the cached pair ids equal the item's pair ids.
    F04a: passes source='llm' to dismiss_review so the decision source is preserved.
    F20: skips auto-merge when the review item has alternatives (tie → keep for human).

    Returns ``{auto_merged, kept_for_review, judged, cost_capped, superseded_count}``.
    """
    from backend.db.dedup_config_store import DedupConfigStore

    cfg = DedupConfigStore().get()
    policy = _policy()
    auto_merged = 0
    kept = 0
    judged = 0
    cost_capped = False

    if not cfg.get("l3_enabled", True):
        pending_items = reviews.pending(batch_id)
        live_items, superseded_count = _sweep_zombies(pending_items)
        return {
            "auto_merged": 0,
            "kept_for_review": len(live_items),
            "judged": 0,
            "cost_capped": False,
            "superseded_count": superseded_count,
        }

    max_per_run = int(cfg.get("l3_max_per_run") or 0)
    model = cfg.get("l3_model") or ""

    # F17: sweep zombies BEFORE any LLM spend.
    pending_items = reviews.pending(batch_id)
    live_items, superseded_count = _sweep_zombies(pending_items)

    for item in live_items:
        incoming_id = item.get("incoming_id") or ""
        canonical_id = item.get("canonical_id") or ""
        incoming = ledger_service.transactions.find_by_id(incoming_id)
        canonical = ledger_service.transactions.find_by_id(canonical_id)
        # Liveness was already checked by _sweep_zombies, but a row could be deleted mid-run.
        if not _is_live(incoming) or not _is_live(canonical):
            reviews.set_status(item["id"], "superseded")
            superseded_count += 1
            continue

        # F02c: only reuse a cached verdict when the stored pair ids match THIS item's pair ids.
        cached = decisions.get_for_pair(incoming_id, canonical_id)
        if cached and cached.get("verdict"):
            verdict = cached["verdict"]
        else:
            if max_per_run and judged >= max_per_run:
                cost_capped = True
                kept += 1
                continue
            verdict, reason = await matching.judge_pair(incoming, canonical, model=model)
            judged += 1
            if verdict is None:
                kept += 1
                continue
            decisions.remember(
                {
                    "incoming_id": incoming_id,
                    "canonical_id": canonical_id,
                    "signature": item.get("signature") or "",
                    "verdict": verdict,
                    "become_transfer": item.get("become_transfer"),
                    "llm_reason": reason,
                    "source": "llm",
                }
            )

        if verdict == "different":
            # F04a: pass source='llm' so the decision record keeps the correct source.
            # Guard: if the item was resolved mid-run (e.g. zombie sweep), skip gracefully.
            try:
                dismiss_review(item["id"], source="llm")
            except Exception:  # noqa: BLE001
                pass
            kept += 1
            continue

        # Re-check the deterministic invariants AFTER the LLM (the LLM never overrides them).
        if matching._same_source_event_veto(incoming, canonical) or matching._same_feed(
            incoming, canonical
        ):
            kept += 1
            continue

        # F20: refuse auto-merge while a live tie exists — keep for human to resolve.
        # Filter to alternatives whose row is still live; stale alts don't constitute a tie.
        live_alts = [
            alt_id
            for alt_id in (item.get("alternatives") or [])
            if _is_live(ledger_service.transactions.find_by_id(alt_id))
        ]
        if live_alts:
            kept += 1
            continue

        klass = (
            matching.MatchClass.TRANSFER_PAIR
            if item.get("become_transfer")
            else matching.MatchClass.NEAR_DUPLICATE
        )
        synthetic = matching.MatchResult(
            action="review",
            candidates=[matching.Candidate(canonical, klass, item.get("score") or 0.0)],
            canonical_id=canonical_id,
            become_transfer=bool(item.get("become_transfer")),
        )
        if matching.combine(synthetic, policy, llm_verdict=verdict) == "auto_merge":
            try:
                accept_review(item["id"])
                auto_merged += 1
            except Exception:  # noqa: BLE001 — fall back to leaving it queued
                logger.exception("judge: auto-merge failed for review %s", item["id"])
                kept += 1
        else:
            kept += 1

    return {
        "auto_merged": auto_merged,
        "kept_for_review": kept,
        "judged": judged,
        "cost_capped": cost_capped,
        "superseded_count": superseded_count,
    }


# ── Merge field preservation (harness audit 2026-07-19) ─────────────────────────
#
# ledger_service.merge_transactions only carries external_refs/merged_from forward, plus a
# narrowly-scoped cleared/reconciled_at/settled-amount inheritance (F16b/F16c) — everything
# else distinct on the absorbed row (memo, tags, an unlinked category/payee) was SILENTLY
# dropped with no trace. Losing a memo is a real loss even though nothing downstream reads it
# (matching/rules/payees all key on `description`, ADR 2026-07-17) — it's the user's own note.
#
# Two policies, chosen per field by how safe an automatic decision is:
#   ADOPT-IF-BLANK (extends the merge choke's own cleared/reconciled_at idiom): memo, tags
#   (union), and — for a non-transfer merge only, since become_transfer wipes both regardless —
#   category/payee_id. Applied via a direct pre-merge store patch (never `update_transaction`,
#   which would re-run rules/corrections for a change the user never made) so it rides through
#   the merge journal and stays undo-safe, and audited as its own 'update' event (the merge's
#   own before/after diff can't show it, since by the time merge_transactions reads canonical it
#   already carries the adopted value).
#   SURFACE, NEVER AUTO-PICK: when canonical and absorbed carry a DIFFERENT non-null memo/
#   category/payee_id, or the absorbed row carries splits canonical will end up without, a real
#   judgment call exists — reported back as ``field_conflicts`` for the caller to act on, never
#   silently resolved either direction.


def _merge_field_preservation(
    canonical: dict, absorbed: dict, *, become_transfer: bool
) -> tuple[dict, dict]:
    """Compute the pre-merge ADOPT-IF-BLANK patch + a SURFACE-conflicts report.

    Returns ``(patch, info)``: ``patch`` is applied to canonical BEFORE the merge call;
    ``info`` is ``{"preserved_fields": [...], "field_conflicts": {field: {kept, dropped}}}``.
    """
    patch: dict = {}
    preserved: list[str] = []
    conflicts: dict[str, dict] = {}

    # tags: additive union, regardless of become_transfer (a transfer still carries tags).
    canon_tags = list(canonical.get("tags") or [])
    absorbed_tags = list(absorbed.get("tags") or [])
    merged_tags = canon_tags + [t for t in absorbed_tags if t not in canon_tags]
    if merged_tags != canon_tags:
        patch["tags"] = merged_tags
        preserved.append("tags")

    # memo: adopt only when canonical has none — never overwrite the user's own annotation.
    canon_memo = (canonical.get("memo") or "").strip()
    absorbed_memo = (absorbed.get("memo") or "").strip()
    if not canon_memo and absorbed_memo:
        patch["memo"] = absorbed.get("memo")
        preserved.append("memo")
    elif canon_memo and absorbed_memo and canon_memo != absorbed_memo:
        conflicts["memo"] = {"kept": canonical.get("memo"), "dropped": absorbed.get("memo")}

    if not become_transfer:
        # become_transfer wipes category/splits/payee_id on the rebuilt canonical regardless
        # (a transfer carries neither) — adopting or reporting them there would be reporting a
        # non-event, so this whole block is a no-op for a become_transfer merge.
        for field in ("category", "payee_id"):
            canon_val = canonical.get(field)
            absorbed_val = absorbed.get(field)
            if not canon_val and absorbed_val:
                patch[field] = absorbed_val
                preserved.append(field)
            elif canon_val and absorbed_val and canon_val != absorbed_val:
                conflicts[field] = {"kept": canon_val, "dropped": absorbed_val}

        absorbed_splits = absorbed.get("splits")
        if absorbed_splits and not canonical.get("splits"):
            conflicts["splits"] = {
                "kept": None,
                "dropped": f"{len(absorbed_splits)} split line(s) on the absorbed row",
            }

    return patch, {"preserved_fields": preserved, "field_conflicts": conflicts}


def merge_transactions_preserving_fields(
    canonical_id: str,
    absorbed_id: str,
    *,
    reason: str = "",
    become_transfer: bool = False,
) -> tuple[dict, dict]:
    """Wraps ``ledger_service.merge_transactions`` with the pre-merge preservation pass above.
    Every AI-tool / REST merge entry point routes through this, never the raw ledger primitive
    directly, so no merge path can silently reproduce the data-loss this closes.

    Returns ``(merged_canonical_row, info)`` — see the section docstring above for ``info``'s
    shape. A lookup failure on either id is left for ``ledger_service.merge_transactions``
    itself to raise (unchanged error behavior); this wrapper just skips preservation in that case.

    Raises, writing nothing, whatever ``ledger_service.assert_mergeable`` refuses —
    ConflictError for a Starting Balance, ValidationError for the rest.
    """
    canonical = ledger_service.transactions.find_by_id(canonical_id)
    absorbed = ledger_service.transactions.find_by_id(absorbed_id)
    info: dict = {"preserved_fields": [], "field_conflicts": {}}
    if canonical is not None and absorbed is not None:
        # Every refusal the merge choke can raise, asserted HERE: the preservation pass WRITES
        # to the canonical first, so leaving any of them to merge_transactions would refuse the
        # merge while still having mutated a row.
        ledger_service.assert_mergeable(canonical, absorbed, become_transfer=become_transfer)
        patch, info = _merge_field_preservation(
            canonical, absorbed, become_transfer=become_transfer
        )
        if patch:
            patched = ledger_service.transactions.update_by_id(canonical_id, patch)
            if patched is not None:
                before_diff, after_diff = audit_service.changed_fields(canonical, patched)
                audit_service.record_txn_event(
                    "update",
                    canonical_id,
                    source=canonical.get("source"),
                    before=before_diff,
                    after=after_diff,
                    note=(
                        f"preserved {', '.join(info['preserved_fields'])} from absorbed "
                        f"{absorbed_id} before merge"
                    ),
                )
    merged = ledger_service.merge_transactions(
        canonical_id, absorbed_id, reason=reason, become_transfer=become_transfer
    )
    # The canonical's update and the absorbed row's delete both already reached the turn
    # receipt through the ledger's audit choke — this adds only the PROVENANCE that choke
    # cannot know: that the update was a MERGE, and what it absorbed. Same (type, op, id),
    # so the receipt's de-dup folds it into that existing record rather than counting twice.
    try:
        record_write(
            "update",
            canonical_id,
            entity_type="transaction",
            meta={"verb": "merge", "absorbed_id": absorbed_id},
        )
    except Exception:  # noqa: BLE001 - describing a write must never be able to fail it
        pass
    return merged, info


# ── Review-queue resolution (REST / AI tools call these) ───────────────────────────


def list_reviews(
    batch_id: str | None = None, *, persist_sweep: bool = True
) -> tuple[list[dict], int]:
    """Pending review items, enriched with both rows.

    F17: sweeps zombie items (missing/deleted/merged-elsewhere rows) before returning.
    Returns (enriched_items, superseded_count).

    ``persist_sweep=False`` filters zombies out of the RESULT without stamping the store —
    see ``_sweep_zombies``. ``superseded_count`` then reports what was filtered rather than
    what was resolved, which is the honest reading for a caller that wrote nothing.
    """
    import time as _time

    _log = __import__("logging").getLogger(__name__)
    _t0 = _time.perf_counter()
    raw_pending = reviews.pending(batch_id)
    if not raw_pending:
        return [], 0
    _t1 = _time.perf_counter()
    # One load, indexed by id, shared by the sweep AND the enrich loop below — each used to
    # build its own full-ledger map (2 parses/request; before that, find_by_id per lookup
    # made it 191). 2026-08-12: one parse.
    txns_by_id = {t["id"]: t for t in ledger_service.transactions.all()}
    live_items, superseded_count = _sweep_zombies(raw_pending, txns_by_id, persist=persist_sweep)
    _t2 = _time.perf_counter()

    out = []
    _lookup_count = 0
    for r in live_items:
        incoming = txns_by_id.get(r.get("incoming_id"))
        canonical = txns_by_id.get(r.get("canonical_id"))
        _lookup_count += 2
        # Enrich alternatives: convert ids to full txn dicts.
        alt_txns = []
        for alt_id in r.get("alternatives") or []:
            alt = txns_by_id.get(alt_id)
            _lookup_count += 1
            if alt and _is_live(alt):
                alt_txns.append(alt)
        out.append(
            {
                **r,
                "incoming": _enrich_txn(incoming),
                "canonical": _enrich_txn(canonical),
                "alt_txns": [_enrich_txn(a) for a in alt_txns],
            }
        )
    _t3 = _time.perf_counter()
    _log.info(
        "PERF list_reviews pending=%.1fms sweep=%.1fms enrich=%.1fms "
        "(items=%d, find_by_id_calls=%d)",
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        (_t3 - _t2) * 1000,
        len(live_items),
        _lookup_count,
    )
    return out, superseded_count


def accept_review(review_id: str) -> tuple[dict, dict]:
    """Merge the pair behind a review item (canonical absorbs incoming) and mark it merged.

    F17: re-validates liveness of both rows before merging.

    Returns ``(merged_canonical_row, info)`` — ``info`` is
    ``merge_transactions_preserving_fields``'s preservation report (harness audit 2026-07-19).
    """
    from backend.core.errors import ValidationError

    r = reviews.find_by_id(review_id)
    if r is None or r.get("status") != "pending":
        raise ValidationError("Review item not found or already resolved")

    # F17: validate liveness.
    incoming = ledger_service.transactions.find_by_id(r.get("incoming_id"))
    canonical = ledger_service.transactions.find_by_id(r.get("canonical_id"))
    if not _is_live(incoming) or not _is_live(canonical):
        reviews.set_status(review_id, "superseded")
        raise ValidationError(
            "One or both transactions no longer exist — review superseded before it could be accepted"
        )

    merged, info = merge_transactions_preserving_fields(
        r["canonical_id"],
        r["incoming_id"],
        reason="; ".join(r.get("reasons") or []) or "user-accepted merge",
        become_transfer=bool(r.get("become_transfer")),
    )
    reviews.set_status(review_id, "merged")
    if r.get("incoming_id") and r.get("canonical_id"):
        decisions.remember(
            {
                "incoming_id": r["incoming_id"],
                "canonical_id": r["canonical_id"],
                "signature": r.get("signature") or "",
                "verdict": "pair" if r.get("become_transfer") else "same",
                "become_transfer": bool(r.get("become_transfer")),
                "source": "user",
            }
        )
    return merged, info


def dismiss_review(review_id: str, *, source: str = "user") -> dict | None:
    """Keep both rows and REMEMBER the pair as 'different' so it is never re-surfaced.

    F04a: accepts source= so the judge path can pass source='llm' preserving the true origin.
    The decision record retains the llm_reason from the review item when source='llm'.

    Security: mirrors accept_review — raises ValidationError if the item is not pending
    (prevents overwriting a 'same' cache decision with 'different' on an already-merged pair).
    """
    from backend.core.errors import ValidationError

    r = reviews.find_by_id(review_id)
    if r is None or r.get("status") != "pending":
        raise ValidationError("Review item not found or already resolved")
    reviews.set_status(review_id, "dismissed")
    incoming_id = r.get("incoming_id") or ""
    canonical_id = r.get("canonical_id") or ""
    if incoming_id and canonical_id:
        entry: dict = {
            "incoming_id": incoming_id,
            "canonical_id": canonical_id,
            "signature": r.get("signature") or "",
            "verdict": "different",
            "source": source,
        }
        # F04a: preserve llm_reason when dismissing via the judge path.
        if r.get("llm_reason"):
            entry["llm_reason"] = r["llm_reason"]
        decisions.remember(entry)
    return r


def swap_review_canonical(review_id: str, new_canonical_id: str) -> dict:
    """Swap the canonical of a review item to one of its tied alternatives (F20).

    Validates that new_canonical_id is either the current canonical or in the item's alternatives.
    Recomputes pair_key / signature. Returns the enriched updated item.
    """
    from backend.core.errors import ValidationError

    r = reviews.find_by_id(review_id)
    if r is None or r.get("status") != "pending":
        raise ValidationError("Review item not found or already resolved")

    current_canonical_id = r.get("canonical_id")
    alternatives = r.get("alternatives") or []
    allowed = {current_canonical_id} | set(alternatives)
    if new_canonical_id not in allowed:
        raise ValidationError(
            f"new_canonical_id {new_canonical_id!r} is not the current canonical or a tied alternative"
        )

    new_canonical = ledger_service.transactions.find_by_id(new_canonical_id)
    if not _is_live(new_canonical):
        raise ValidationError("Target canonical transaction no longer exists")

    incoming = ledger_service.transactions.find_by_id(r.get("incoming_id"))
    if not _is_live(incoming):
        reviews.set_status(review_id, "superseded")
        raise ValidationError("Incoming transaction no longer exists — review superseded")

    rates = matching._load_rates()
    new_signature = matching.pair_signature(incoming, new_canonical, rates)
    # Update alternatives: put the old canonical back, remove the new one.
    new_alts = [a for a in alternatives if a != new_canonical_id]
    if current_canonical_id and current_canonical_id != new_canonical_id:
        new_alts.insert(0, current_canonical_id)

    reviews.update_by_id(
        review_id,
        {
            "canonical_id": new_canonical_id,
            "signature": new_signature,
            "alternatives": new_alts,
        },
    )
    updated = reviews.find_by_id(review_id)
    return {
        **(updated or {}),
        "incoming": _enrich_txn(incoming),
        "canonical": _enrich_txn(new_canonical),
        "alt_txns": [
            _enrich_txn(ledger_service.transactions.find_by_id(a))
            for a in new_alts
            if ledger_service.transactions.find_by_id(a)
        ],
    }


# ── Decisions management (F04b REST surface) ────────────────────────────────────


def list_decisions(limit: int = 50, offset: int = 0) -> dict:
    """Return the most recent decisions for the management UI. ``offset`` pages through
    older ones — the verdict cache has no natural ceiling, so every decision must be
    reachable, not just the newest page.

    Derives payee_label and amount_bucket from the stored row when resolvable.
    """
    items = decisions.recent(limit, offset=offset)
    total = decisions.total_count()
    out = []
    for item in items:
        entry = {
            "signature": item.get("signature"),
            "pair_key": item.get("pair_key"),
            # incoming_id (security review L3a): a `detach_external_ref`-recorded decision
            # keys `incoming_id` on the EXTERNAL identity (`services.ledger._external_
            # identity`), not a second transaction id — omitting it left that decision
            # showing in Settings with no visible counterpart at all.
            "incoming_id": item.get("incoming_id"),
            "verdict": item.get("verdict"),
            "canonical_id": item.get("canonical_id"),
            "become_transfer": item.get("become_transfer"),
            "llm_reason": item.get("llm_reason"),
            "source": item.get("source"),
            "created_at": item.get("created_at"),
            "payee_label": _derive_payee_label(item),
            "amount_bucket": _derive_amount_bucket(item),
        }
        out.append(entry)
    return {"decisions": out, "total": total, "limit": limit, "offset": offset}


def forget_decision(pair_key_or_signature: str) -> bool:
    """Delete a decision by its pair_key (or legacy signature for backward compat)."""
    return decisions.forget(pair_key_or_signature)


def recent_merges(days: int = 7, limit: int = 20) -> list[dict]:
    """UX-3: most recent merge-journal entries, newest first.

    Returns up to ``limit`` entries from the last ``days`` days. Each entry carries:
    absorbed_id, canonical_id, canonical_description, absorbed_description,
    canonical_amount, absorbed_amount, canonical_currency, merged_at, source.

    ``source`` is inferred from the ``reason`` field because the journal does not record
    the originating surface separately. "auto-merge" or "rule" in the reason → "rule";
    "llm" or "AI" → "ai"; otherwise → "user".

    Uses ledger_service.merge_journal (the module-level instance) so tests that
    monkeypatch that attribute will see the isolated store.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = sorted(
        ledger_service.merge_journal.all(),
        key=lambda e: e.get("created_at", ""),
        reverse=True,
    )
    out: list[dict] = []
    for e in entries:
        if (e.get("created_at") or "") < cutoff:
            continue
        reason = (e.get("reason") or "").lower()
        if "auto-merge" in reason or "rule" in reason:
            source = "rule"
        elif "llm" in reason or "ai" in reason:
            source = "ai"
        else:
            source = "user"
        cb = e.get("canonical_before") or {}
        ab = e.get("absorbed_before") or {}
        out.append(
            {
                "absorbed_id": e.get("absorbed_id"),
                "canonical_id": e.get("canonical_id"),
                "canonical_description": cb.get("description") or "",
                "absorbed_description": ab.get("description") or "",
                "canonical_amount": cb.get("base_amount"),
                "absorbed_amount": ab.get("base_amount"),
                "canonical_currency": cb.get("from_currency") or cb.get("to_currency") or "",
                "merged_at": e.get("created_at"),
                "source": source,
            }
        )
        if len(out) >= limit:
            break
    return out


def _derive_payee_label(decision: dict) -> str | None:
    """Best-effort: pull the payee-like token from one of the stored rows."""
    for id_field in ("incoming_id", "canonical_id"):
        row_id = decision.get(id_field)
        if not row_id:
            continue
        row = ledger_service.transactions.find_by_id(row_id)
        if row and row.get("description"):
            tok = matching.payee_key(row["description"])
            if tok:
                return tok[:40]
    return None


def _derive_amount_bucket(decision: dict) -> str | None:
    """Best-effort: pull the amount bucket from one of the stored rows."""
    for id_field in ("incoming_id", "canonical_id"):
        row_id = decision.get(id_field)
        if not row_id:
            continue
        row = ledger_service.transactions.find_by_id(row_id)
        if row:
            amt = (
                row.get("from_amount")
                if row.get("from_amount") is not None
                else row.get("to_amount")
            )
            if amt is not None:
                try:
                    return f"~{int(round(abs(float(amt))))}"
                except (TypeError, ValueError):
                    pass
    return None
