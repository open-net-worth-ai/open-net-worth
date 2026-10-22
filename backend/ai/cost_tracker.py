"""Persistent cost tracking for all AI calls across the app."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Raw per-turn records are kept for this many trailing days; anything older is
# folded into the daily rollup ("simplified").  Time-based, not count-based, so a
# heavy day can't prematurely evict recent turns — recent days must stay re-bucketable
# by the viewer's local timezone at read time.
_RAW_RETENTION_DAYS = 15

# Runaway backstop only.  Age (above) is the real retention policy; this caps the raw
# table size in a pathological single-window burst.  Set well above expected 15-day
# volume so it effectively never trips in normal use.
_COST_LOG_MAX = 20000

# Serialises concurrent record() calls — the hourly background agents and the user's
# interactive turns both call record() per LLM response.  The per-table DB lock each
# store call takes (PayloadTableStore._use_conn) already makes each INDIVIDUAL
# append/delete atomic, but the RETENTION DECISION here spans a read + two tables
# (cost_log, cost_log_daily); without this lock two threads could both read the same
# "aged out" snapshot and both roll the same rows into the daily aggregate, double
# counting them. Process-local only (like the file-era lock this replaces) — a
# multi-worker deployment can still race across processes; unchanged by this migration.
_record_lock: threading.Lock = threading.Lock()


def record(
    source: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    session_id: str = "",
    cost_usd: float | None = None,
    surface: str | None = None,
    batch: bool = False,
    turn_index: int | None = None,
    cost_basis: str | None = None,
    provider: str | None = None,
    peak_prompt_tokens: int | None = None,
    context_window: int | None = None,
) -> float:
    """Write a cost record to the DB. Returns cost_usd. Best-effort — never raises.

    When ``cost_usd`` is provided it is used directly (the provider already computed it
    with the correct ``base_url``).  When omitted the generic ``cost_usd_for_model``
    estimator is used as a fallback (base_url defaults to ``""`` — fine for Anthropic
    models; callers that set a non-default base_url SHOULD pass the pre-computed value
    to avoid the empty-base_url defeating the local-endpoint detection in the OpenAI
    pricing helper).

    ``surface`` is an optional attribution label set by the entrypoint (``'web'``,
    ``'connector:<name>'``, ``'scheduled'``).  When provided it is stored alongside
    ``source`` so the cost API can break down spend by originating interface.

    ``batch`` marks a provider batch API call (Anthropic/OpenAI Message Batches).
    A pre-computed ``cost_usd`` is used as-is (the caller already applied any batch
    discount); when the cost is estimated here instead, the estimate is halved to
    reflect the 50% batch discount both providers offer. The flag is persisted so
    the cost log can be filtered/audited by submission lane.

    ``cost_basis`` marks what the dollar figure MEANS, and is written only when a caller
    knows it is not an invoice: ``"plan_included"`` for a Claude Code CLI turn running on
    the user's Pro/Max login, where the reported figure is a locally-computed LIST PRICE
    for usage already covered by the subscription. Absent (the overwhelming default)
    means "metered spend", the pre-existing reading of every row. Recording the number
    but stamping it is deliberate: a reader can tell an estimate from a charge, while
    zeroing it would hide real consumption of a rate-limited quota.

    ``provider`` is the catalog display label of the endpoint/credential that actually
    served this call (``"Anthropic"``, ``"OpenRouter"``, ``"Claude Code CLI"`` …), supplied
    by the caller when it's known with certainty — every AI provider class resolves its own
    (transport, base_url) via ``model_config_store.provider_label_for`` before calling
    ``record``. When omitted, ``services/cost.py``'s ``by_provider`` breakdown falls back to
    guessing from the bare ``model`` id, which cannot distinguish an aggregator's namespaced
    upstream vendors (``openrouter.ai`` routes hundreds of ``vendor/model`` ids behind one
    endpoint) and mislabels or fails to label them "Unknown".

    ``turn_index`` is the EXACT join key back to a chat transcript (ADR
    2026-07-19-restore-cost-chip-on-rehydrate): ``assistant.run()`` passes the stored
    history's length at the moment this turn opened — an index that never moves
    (history is append-only) and is unique per turn within a session. Persisted only
    when given, so pre-migration rows are distinguishable from rows that legitimately
    have none (a non-chat call — memory/fold/title — never passes one).

    Records older than ``_RAW_RETENTION_DAYS`` are rolled up into the ``cost_log_daily``
    table (keyed by date|model|source) and dropped from the raw table, so per-turn detail
    is kept for the recent window while older history is preserved compactly
    ("simplified").  A high ``_COST_LOG_MAX`` backstop guards against a pathological
    burst.  Already-rolled-up records cannot be recovered from a live install (this is a
    forward-only fix).
    """
    try:
        from backend.db.cost_log_store import CostLogDailyStore, CostLogStore

        if cost_usd is None:
            from backend.ai.models.pricing import cost_usd_for_model

            cost_usd = cost_usd_for_model(
                model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
            )
            if batch:
                cost_usd *= 0.5
        cost = cost_usd

        # Per-operation attribution: feed the collector active on this context (if any) so
        # the operation that triggered this call (a statement parse…) can report ITS spend.
        from backend.core.cost_scope import add_llm_cost

        add_llm_cost(cost, input_tokens, output_tokens)

        rec: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "session_id": session_id,
            "model": model,
            "input_tokens": input_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
        }
        if surface is not None:
            rec["surface"] = surface
        if batch:
            rec["batch"] = True
        if turn_index is not None:
            rec["turn_index"] = turn_index
        if cost_basis:
            rec["cost_basis"] = cost_basis
        if provider:
            rec["provider"] = provider
        # Context-pressure inputs for the chat compaction check (ai/assistant.py
        # _token_pressure_over_fraction): the token buckets above SUM a turn's requests,
        # so the largest single request's prompt is stored on its own, beside the window
        # the provider reported serving it.
        if peak_prompt_tokens:
            rec["peak_prompt_tokens"] = peak_prompt_tokens
        if context_window:
            rec["context_window"] = context_window

        with _record_lock:
            store = CostLogStore()
            store.append(rec)

            # Charge this turn's tokens to whoever started the session, for a PUBLIC demo's
            # per-visitor quota (a no-op unless DEMO_MODE — see services/demo_quota). Hooked
            # HERE, right after the append, because that is the one point where a record is
            # definitely written; record() has several later exits through the retention
            # branch below. Cache READS are excluded: they are near-free and enormous in an
            # agentic loop, so counting them would exhaust a visitor's budget in one turn.
            # Best-effort — metering must never be able to fail a cost write.
            try:
                from backend.services import demo_quota

                demo_quota.note_usage(
                    session_id, int(input_tokens) + int(output_tokens) + int(cache_creation_tokens)
                )
            except Exception:  # noqa: BLE001
                logger.debug("demo quota metering skipped", exc_info=True)

            # Age-based retention: keep raw per-turn detail for the trailing
            # _RAW_RETENTION_DAYS and fold older records into the daily rollup.  Cutoff
            # is UTC — the write path has no viewer tz and a day-granular boundary
            # doesn't need one.  Fast path: only scan when the oldest record has aged
            # out (≈ once/day), so the common case stays a cheap append.
            #
            # The probe is what MAKES that fast path fast.  This block used to open with
            # `rows = store.all_with_ids()` and then read only `rows[0]` and `len(rows)`
            # off it, so the "cheap append" case materialised the entire cost log — a full
            # scan on the event loop once per LLM call (live 2026-08-11 18:47, named by
            # core.loop_watch).  Two indexed reads answer both questions; the log is only
            # loaded when there is actually something to evict.
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_RAW_RETENTION_DAYS)).strftime(
                "%Y-%m-%d"
            )
            total, oldest_ts = store.retention_probe()
            oldest_date = oldest_ts[:10]
            aged = bool(oldest_date and oldest_date < cutoff)
            if not aged and total <= _COST_LOG_MAX:
                return cost

            rows = store.all_with_ids()  # oldest-first (id, payload) pairs
            if aged:
                kept: list[tuple[str, dict]] = []
                aged_out: list[tuple[str, dict]] = []
                for rid, r in rows:
                    r_date = (r.get("ts") or "")[:10]
                    (aged_out if (r_date and r_date < cutoff) else kept).append((rid, r))
                if aged_out:
                    CostLogDailyStore().merge_records([r for _, r in aged_out])
                    store.delete_many([rid for rid, _ in aged_out])
                    rows = kept

            # Runaway backstop: roll up (never silently drop) the oldest overflow if the
            # raw table somehow exceeds the cap within the retention window.
            if len(rows) > _COST_LOG_MAX:
                overflow = rows[: len(rows) - _COST_LOG_MAX]
                CostLogDailyStore().merge_records([r for _, r in overflow])
                store.delete_many([rid for rid, _ in overflow])
                logger.warning(
                    "cost_tracker: raw log exceeded backstop %d within %dd retention; "
                    "rolled %d record(s) up early",
                    _COST_LOG_MAX,
                    _RAW_RETENTION_DAYS,
                    len(overflow),
                )
        return cost
    except Exception:
        logger.exception("cost_tracker.record failed (non-fatal)")
        return 0.0
