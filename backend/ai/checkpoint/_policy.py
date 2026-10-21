"""Checkpoint policy — what gets captured, what never does.

``MAX_CHECKPOINTS`` (the active-checkpoint cap), the excluded-paths/prefixes/tables sets,
and ``_COMPANIONS`` (referential file-pair capture). Read this before changing any of them —
each exclusion below exists because *not* excluding it already broke something once; the
inline comments are the scar tissue.

Split out of the former flat ``backend/ai/checkpoint.py`` (plan 4e, task 2) — pure code
motion, no logic changed. See ``backend/ai/checkpoint/__init__.py`` for the package's own
mechanism docstring and the compatibility re-export shim.
"""

from __future__ import annotations

from backend.paths import (
    AGENT_FINDINGS_JSON,
    ALPACA_CONFIG_JSON,
    ARTIFACTS_DIR,
    ASSET_BRIEF_JSON,
    ATTACHMENTS_DIR,
    AUTH_JSON,
    AUTOMATION_RUNTIME_JSON,
    BATCH_LEDGER_JSON,
    BRIEFING_CACHE_JSON,
    CHANNELS_CONFIG_JSON,
    CHAT_PAIRING_JSON,
    CHECKPOINTS_INDEX,
    # files that must NEVER be captured (runtime state / caches / secrets)
    CONNECTOR_SESSIONS_JSON,
    CORRECTIONS_JSON,
    COST_LOG_DAILY_JSON,
    COST_LOG_JSON,
    DISCORD_CONFIG_JSON,
    EMAIL_INGEST_JSON,
    ENABLE_BANKING_CONFIG_JSON,
    EVIDENCE_JSON,
    FX_CACHE_JSON,
    GOCARDLESS_CONFIG_JSON,
    HARNESS_STATE_JSON,
    IBKR_CONFIG_JSON,
    MCP_CLIENTS_JSON,
    MCP_PENDING_CALLS_JSON,
    MCP_WRITE_LOG_JSON,
    MODEL_CONFIG_JSON,
    MODEL_TELEMETRY_JSON,
    MONITORING_PRICE_CACHE_JSON,
    NAMES_CACHE_JSON,
    PENDING_ANALYSIS_JSON,
    PENDING_PLANS_JSON,
    PENDING_TRANSACTIONS_JSON,
    PLAID_CONFIG_JSON,
    PLUGGY_CONFIG_JSON,
    RUN_HISTORY_JSON,
    SEARCH_CONFIG_JSON,
    SIGNALS_JSON,
    SIMPLEFIN_CONFIG_JSON,
    SKILL_BUNDLES_DIR,
    SLACK_CONFIG_JSON,
    TELEGRAM_CONFIG_JSON,
    TOOL_CALL_LOG_JSON,
    TOOL_DISCOVERY_LOG_JSON,
    TXN_AUDIT_JSON,
    WHATSAPP_INBOUND_CONFIG_JSON,
    YODLEE_CONFIG_JSON,
)

MAX_CHECKPOINTS = 50  # active checkpoints retained; oldest pruned (blobs GC'd)

# Files excluded from checkpointing — runtime state and derived caches whose
# revert would do more harm than good. memory.json is intentionally NOT here:
# we want "Lucius now thinks X" to be undoable.
_EXCLUDED_PATHS = {
    p.resolve()
    for p in (
        ASSET_BRIEF_JSON,  # per-instrument synthesized brief — derived search/LLM cache, like price_history/intel_signals
        EVIDENCE_JSON,  # accumulated news/web evidence behind Intel scans — derived cache, rebuildable from sources; a turn rollback must not delete/resurrect news (same rationale as ASSET_BRIEF_JSON)
        AGENT_FINDINGS_JSON,
        AUTH_JSON,
        AUTOMATION_RUNTIME_JSON,  # durable scheduler queue/outbox; restoring an older snapshot would replay or lose events
        BATCH_LEDGER_JSON,  # pending/completed provider batch jobs — runtime scheduler state, re-fetchable from provider, never snapshotted
        PENDING_ANALYSIS_JSON,  # crash-event analysis queue — runtime scheduler state (drain/fill cycle), never snapshotted
        PENDING_PLANS_JSON,  # token-bound staged ledger write calls (the confirm gate) — a single-use runtime queue written INSIDE an interactive turn's CheckpointContext (preview stages, confirm pops), so it must be explicitly excluded (same rationale as MCP_PENDING_CALLS_JSON)
        MODEL_CONFIG_JSON,  # runtime AI provider/model config — holds a write-only API key, never snapshotted
        CHANNELS_CONFIG_JSON,  # outbound channel secrets (SMTP pw / webhook secret) — never snapshotted
        MCP_CLIENTS_JSON,  # external-MCP connection secrets — never snapshotted (P3-MCP-CLIENT §3)
        MCP_WRITE_LOG_JSON,  # append-only audit of mutating inbound-MCP calls — runtime/behavioral log, written INSIDE the per-call CheckpointContext the adapter opens, so it must be explicitly excluded
        TOOL_CALL_LOG_JSON,  # per-tool-call telemetry ring — runtime/behavioral log written INSIDE every turn's CheckpointContext (it fires on EVERY dispatch), so it must be explicitly excluded; a rollback must never rewrite the record of what was called (same rationale as MCP_WRITE_LOG_JSON)
        CHAT_PAIRING_JSON,  # pending chat-channel pairing requests — a runtime queue (code + sender id, no secret), checkpoint-EXCLUDED (chat-pairing)
        MCP_PENDING_CALLS_JSON,  # nonce-bound pending external-MCP write calls — a single-use runtime queue written INSIDE an interactive turn's CheckpointContext (propose persists, apply pops), so it must be explicitly excluded (external-mcp-write H1/M2)
        TELEGRAM_CONFIG_JSON,  # Telegram connector config — holds a write-only bot token, never snapshotted
        SLACK_CONFIG_JSON,  # Slack connector config — holds a write-only bot token + signing secret, never snapshotted
        DISCORD_CONFIG_JSON,  # Discord connector config — holds a write-only bot token, never snapshotted
        WHATSAPP_INBOUND_CONFIG_JSON,  # WhatsApp inbound connector config — holds a write-only app secret + verify token, never snapshotted
        SIMPLEFIN_CONFIG_JSON,  # SimpleFIN connector config — holds a write-only access URL (a bearer credential), never snapshotted
        ALPACA_CONFIG_JSON,  # Alpaca brokerage connector config — holds a write-only API key/secret PAIR; a Lucius restore must never resurrect a superseded or revoked credential (alpaca-connector S1 read-sync)
        IBKR_CONFIG_JSON,  # IBKR Flex Web Service connector config — holds a write-only Flex token; a Lucius restore must never resurrect a superseded or revoked credential (ibkr-connector read-sync)
        GOCARDLESS_CONFIG_JSON,  # GoCardless connector config — holds write-only secret_key + cached tokens (bearer credentials), never snapshotted
        PLAID_CONFIG_JSON,  # Plaid connector config — holds write-only secret + access token (bearer credentials), never snapshotted
        PLUGGY_CONFIG_JSON,  # Pluggy.ai connector config — holds write-only client_secret + cached apiKey (bearer credentials), never snapshotted
        SEARCH_CONFIG_JSON,  # Web Search connector config — holds write-only search-provider API keys, never snapshotted
        ENABLE_BANKING_CONFIG_JSON,  # Enable Banking connector config — holds a write-only RSA private key + cached JWT/session (bearer credentials), never snapshotted
        YODLEE_CONFIG_JSON,  # Yodlee connector config — holds write-only secret + user password + session tokens (bearer credentials), never snapshotted
        CONNECTOR_SESSIONS_JSON,
        CORRECTIONS_JSON,
        TXN_AUDIT_JSON,  # transaction provenance/audit trail — a forensic record of how the data got here; a data restore must NOT erase it (transaction-provenance RFC)
        HARNESS_STATE_JSON,  # self-harness cooldown watermark path — feature removed 2026-08-28; kept only because the constant still exists for migration replay (see core/paths.py)
        PENDING_TRANSACTIONS_JSON,
        EMAIL_INGEST_JSON,
        COST_LOG_JSON,
        COST_LOG_DAILY_JSON,  # date|model|source cost rollup — belongs beside COST_LOG_JSON; never snapshotted (Wave 5B fix: missing from this set even though its raw-log sibling was already excluded)
        TOOL_DISCOVERY_LOG_JSON,  # find_tools search-quality telemetry — runtime behavioral log, never snapshotted (mirrors cost_log)
        MODEL_TELEMETRY_JSON,  # per-model latency measurement — written on EVERY model turn; without this exclusion every Q&A turn minted a checkpoint and the chat showed Restore on turns that changed no data (2026-07-11 regression)
        RUN_HISTORY_JSON,  # agent/task run ledger — runtime bookkeeping already described as checkpoint-EXCLUDED in its store doc; was only ever referenced in a comment here, never actually excluded
        SIGNALS_JSON,
        FX_CACHE_JSON,
        BRIEFING_CACHE_JSON,
        MONITORING_PRICE_CACHE_JSON,
        NAMES_CACHE_JSON,
        CHECKPOINTS_INDEX,  # the index manifest's own legacy file — never self-captured (would recurse). Vestigial post-DB-cutover (nothing writes this path anymore, see _index.py), kept for the SAME reason every other migrated store's file-path exclusion stays here too (plan 4e task 2)
    )
}

# Excluded *trees* — anything UNDER one of these dirs is never captured (matched by
# prefix, not exact path). The skill-bundle store is content-addressed + immutable +
# can be large/binary, so snapshotting it into the gzip blob pool is wrong (a checkpoint
# restore reverts the skill record's bundle_version POINTER, not the version dirs, which
# are append-only on disk — script-bundling-skills S1 §1.4).
_EXCLUDED_PREFIXES = {p.resolve() for p in (SKILL_BUNDLES_DIR, ARTIFACTS_DIR, ATTACHMENTS_DIR)}

# DB tables excluded from checkpointing — the DB mirror of _EXCLUDED_PATHS (spec D7).
# Checkpoint policy is centralized here, not in the core.io seam: a DB store fires
# core.io.fire_db_write_hook unconditionally, and record_db_before (_context.py) decides
# captured-vs-excluded, exactly like _capture does for files.
#
# PLAN 3 TASK 4 — money core: `txn_audit` joins this set (below), mirroring TXN_AUDIT_JSON's
# existing file-level exclusion above — a forensic record of how the data got here, which a
# data restore must not erase. `merge_journal` was investigated and DELIBERATELY left OUT —
# `MERGE_JOURNAL_JSON` was never in `_EXCLUDED_PATHS` (checked: it is not one of the constants
# imported into this module, and grepping this file for "MERGE" finds nothing before this
# change), so the merge undo-journal has always been an ordinary, checkpoint-INCLUDED file —
# restoring a turn that recorded/popped a merge-journal entry correctly reverts that entry
# too. The DB table mirrors that today-true behavior exactly, not the brief's own
# illustrative "txn_audit + merge_journal" phrasing — see the plan-3 task-4 report for the
# evidence trail.
_EXCLUDED_TABLES = frozenset(
    {
        "connectors",  # connector settings + sync cursors/OAuth metadata — undoing a ledger write must never rewind a mailbox cursor or a token (connectors-table RFC)
        "connector_secrets",  # connector credentials — SECRET + NON_RESTORABLE, never snapshotted
        "desktop_access_grants",  # device decisions: rewinding must never restore revoked file access
        "desktop_ingest_jobs",  # intake identities: rewinding must never replay already delivered files
        "import_runs",  # operational record of a provider import/verify run, written outside any turn — a rewind must not erase or resurrect what an import report said
        "evidence",  # accumulated news/web evidence — derived cache, rebuildable from sources; a turn rollback must not delete/resurrect news (same rationale as EVIDENCE_JSON above)
        "evidence_subjects",  # evidence's derived subject index — goes with its parent row, not independently meaningful to restore
        "price_points",  # append-only price cache (PriceHistoryStore parity) — excluded by design, like the JSON store it will replace
        "write_log",  # checkpoint machinery itself — never self-captured (would recurse)
        "json_cutovers",  # permanent absorption ledger, not per-turn AI state — same rationale as its exemption from tier-2 test clearing
        "ynab_sync_state",  # YNAB two-way sync bookkeeping (cursors, mirror, baselines, review list) — a turn rollback must never rewind a cursor and replay pushes already made to YNAB
        "ynab_sync_mirror",
        "ynab_sync_baselines",
        "ynab_sync_conflicts",
        "txn_audit",  # transaction provenance/audit trail — a forensic record of how the data got here; a data restore must NOT erase it (mirrors TXN_AUDIT_JSON above; transaction-provenance RFC)
        "corrections",  # category-correction signal log (P2-SP2) — a runtime/behavioral signal, not user-authored data; a /rewind must not resurrect stale correction history. Replaces the file-level CORRECTIONS_JSON exclusion in _EXCLUDED_PATHS above (kept there too, for the legacy file) now that CorrectionStore is DB-backed (plan 4a task 2)
        "asset_brief",  # per-instrument synthesized brief — derived search/LLM cache, like price_history/intel_signals; a turn rollback must not delete/resurrect a refreshed brief (same rationale as ASSET_BRIEF_JSON above). Replaces the file-level ASSET_BRIEF_JSON exclusion in _EXCLUDED_PATHS above (kept there too, for the legacy file) now that AssetBriefStore is DB-backed (plan 4b task 3)
        # PLAN 4C (logs/telemetry, task 1) — 8 of the 9 Tier-2 logs/telemetry tables. Each
        # replaces its own file-level exclusion above (kept there too, for the legacy file) now
        # that the store is DB-backed. `automation_runs` is the ONE table in this wave that is
        # DELIBERATELY left OUT of this set — checkpoint-INCLUDED, mirroring its existing
        # AUTOMATION_RUNS_JSON behavior (already portable/captured today, "durable run history").
        "automation_runtime",  # durable scheduler queue/outbox; restoring an older snapshot would replay or lose events (mirrors AUTOMATION_RUNTIME_JSON above)
        "batch_ledger",  # pending/completed provider batch jobs — runtime scheduler state, re-fetchable from provider, never snapshotted (mirrors BATCH_LEDGER_JSON above)
        "mcp_write_log",  # append-only audit of mutating inbound-MCP calls — runtime/behavioral log written INSIDE the per-call CheckpointContext, so it must be explicitly excluded (mirrors MCP_WRITE_LOG_JSON above)
        "model_telemetry",  # per-model latency measurement — written on EVERY model turn; without this exclusion every Q&A turn would mint a checkpoint (mirrors MODEL_TELEMETRY_JSON above, 2026-07-11 regression)
        "pending_analysis",  # crash/news event analysis queue — runtime scheduler state (drain/fill cycle), never snapshotted (mirrors PENDING_ANALYSIS_JSON above)
        "run_history",  # agent/task run ledger — runtime bookkeeping (mirrors RUN_HISTORY_JSON above)
        "tool_call_log",  # per-tool-call telemetry ring — written INSIDE every turn's CheckpointContext (fires on EVERY dispatch); a rollback must never rewrite the record of what was called (mirrors TOOL_CALL_LOG_JSON above)
        # PLAN 4D (config stores, task 1) — the 2 of 11 new config tables that stay excluded;
        # the other 9 (settings, payees, automations, alerts, dashboard_layouts, memory,
        # news_sources, statement_formats, intel_signals — renamed intel_stories at revision
        # 202608301500, RFC intel-signals-data-model; its two new sibling tables,
        # signal_timeline_entries/daily_digests, join it here too) are ordinary
        # checkpoint-INCLUDED user data/config, unchanged from their JsonStore-era behavior.
        "notification_log",  # runtime notification cache (renamed 2026-08-20 from `signals`, SIGNALS_JSON parity) — self-heals on corruption, disposable like price_points/asset_brief; a /rewind must not resurrect or erase transient in-app notifications
        # PLAN 4E (misc stores, task 1) — 3 of the 4 misc tables stay excluded, replacing
        # their own file-level exclusion above (kept there too, for the legacy file, until
        # each is absorbed) now that each store is DB-backed. email_snapshots is the ONE
        # sibling left OUT of this set — checkpoint-INCLUDED, durable user evidence a
        # /rewind should correctly cover (never was in _EXCLUDED_PATHS as a file either).
        "artifacts",  # chat-artifact metadata — a rendered byproduct of a chat turn; a data restore must not delete or resurrect visualizations the transcript still references (mirrors the retired ARTIFACTS_JSON exclusion above)
        "attachments",  # chat-attachment metadata (renamed from "uploads" — cross-channel-attachments RFC Phase 2, ADR 2026-08-16-attachment-store-replaces-upload-store) — a Lucius restore must never resurrect a user-DELETED bank statement (payload dir still excluded via _EXCLUDED_PREFIXES); portable via export instead (mirrors the retired UPLOADS_JSON exclusion above)
        # NOTE: "receipts"/"transaction_receipts" (revision 0042) are DELIBERATELY NOT
        # here — reversed 2026-08-21 (ADR 2026-08-15-separate-attachments-receipts-and-
        # statements.md's amendment). Both were excluded at ship for the same stated
        # reason as "attachments" above, but that reasoning doesn't hold for a DB table:
        # write_log checkpointing is ROW-LEVEL and first-touch-per-turn (ADR
        # 2026-08-07-write-log-row-level-checkpoints), so a restore only ever replays the
        # exact (table, pk) rows THAT turn wrote — it cannot resurrect a receipt or
        # relink deleted/unlinked by a LATER, different write. Every other user-data
        # table in this app (transactions, accounts, categories, every money-core and
        # planning table) stays checkpoint-INCLUDED under the identical theoretical risk;
        # receipts got a strictly more conservative treatment than everything else for a
        # risk row-level tracking already contains. Now undoable via /rewind like every
        # other Lucius write choke.
        "pending_pushes",  # zero-delivery push retry queue (INT6) — runtime scheduler state (drain/fill cycle), never snapshotted (mirrors the retired PENDING_PUSHES_JSON exclusion above)
        # PLAN 4E TASK 2 — the checkpoint index itself. checkpointed=False at the store
        # (CheckpointIndexStore) already makes the capture a structural no-op — this entry
        # is defense in depth, mirroring pending_pushes'/tool_grants'/signals' own
        # belt-and-braces precedent (both layers say "never"). A checkpoint table must
        # never checkpoint itself — that would recurse. Replaces the file-level
        # CHECKPOINTS_INDEX exclusion in _EXCLUDED_PATHS above (kept there too, for the
        # legacy file) now that the index is DB-backed.
        "checkpoints",
        # WAVE 5B (JSON->DB) — 4 more Tier-2 logs/telemetry stores, mirroring PLAN 4C's own
        # nine exactly (same shape, same checkpoint-EXCLUDED reasoning). Each replaces its
        # own file-level exclusion above (kept there too, for the legacy renamed file) now
        # that the store is DB-backed.
        "tool_discovery_log",  # find_tools search-quality telemetry — runtime behavioral log (mirrors TOOL_DISCOVERY_LOG_JSON above)
        "cost_log",  # per-turn AI cost record — runtime telemetry, never snapshotted (mirrors COST_LOG_JSON above)
        "cost_log_daily",  # date|model|source cost rollup — same reasoning as its cost_log sibling (mirrors COST_LOG_DAILY_JSON above)
        "agent_findings",  # agent-run output/proposal review queue — VESTIGIAL: the Agent product (and AgentFindingStore) was retired in d0da1a09 ("feat!: retire the Agent product"); nothing reads or writes this table anymore, but its rows are real history absorbed by the cutover, not discarded (mirrors AGENT_FINDINGS_JSON above)
        # WAVE 5C (JSON->DB) — runtime queues/state. subscriptions/skills_state/actions are
        # NOT here: subscriptions is checkpoint-INCLUDED (VESTIGIAL but genuinely
        # user-authored — see db/subscription_store.py), skills_state and actions are
        # ordinary checkpoint-INCLUDED user data/config (unchanged from their JsonStore-era
        # behavior). runtime_state is the ONE new excluded table this wave adds:
        "runtime_state",  # checkpoint-EXCLUDED namespaced-singleton table (settings' excluded twin — db/tables.py's own docstring); holds harness_state today, mirrors HARNESS_STATE_JSON's own pre-existing file-level exclusion above now that HarnessStateStore is DB-backed
        # WAVE 5D1 (JSON->DB, secrets/config) — the two new tables this wave adds. Both
        # replace their own file-level exclusion above (kept there too, for the legacy
        # renamed file) now that each store is DB-backed. Also SECRET (core.sensitive.
        # SECRET_TABLES) and NON_RESTORABLE (core.sensitive.NON_RESTORABLE_TABLES) — a
        # credential must never be resurrected by a /rewind any more than by a
        # restore_backup or handed out in a sanitised download; all three policies are
        # independent axes that happen to agree on these two tables, not one implying
        # the others.
        "secret_config",  # namespace-keyed singleton (settings'/runtime_state's shape) holding auth + model_config
        # WAVE 5D2b (JSON->DB, permission/nonce machinery) — originally 3 new tables this
        # wave added; `mcp_pending_calls` was absorbed into `pending_plans` at revision 0046
        # (2026-08-22, external-MCP's confirm→apply migrated onto the SAME tool_use_id-keyed
        # mechanism every other gated tool already used) and no longer exists as its own
        # table. Each surviving one replaces its own file-level exclusion above (kept there
        # too, for the legacy renamed file) now that the store is DB-backed. Also
        # NON_RESTORABLE (core.sensitive.NON_RESTORABLE_TABLES), NEITHER SECRET —
        # restore_checkpoint is a MODEL-CALLABLE tool (mutates=("*",)), so an INCLUDED
        # confirm-gate table would mean "the model pops a token via a normal call, then
        # restores a checkpoint from before the pop -> the token is back and replayable" —
        # the exact same privilege-escalation shape the permission table's own comment
        # (below) describes for a revoked grant, here for a single-use approval instead of a
        # standing one.
        "pending_plans",  # token-bound staged write calls (the confirm gate, external-MCP's own staged calls included since revision 0046) — mirrors PENDING_PLANS_JSON's own pre-existing exclusion above
        "chat_pairing",  # pending chat-channel pairing requests — mirrors CHAT_PAIRING_JSON's own pre-existing exclusion above
        # WAVE 5E (JSON->DB, chat sessions) — the one new table this wave adds. A chat
        # transcript must survive a /rewind (a checkpoint restore must not delete chat
        # history) — pre-migration this was already true STRUCTURALLY, not via a listed
        # exclusion: neither the transcript write (`save_session_durable`) nor the
        # index/fold/proactive writes ever called `fire_write_hook`, so no session file
        # was ever captured despite none of them being named in `_EXCLUDED_PATHS` above.
        # `db/session_store.py`'s writes fire NO hook either (belt-and-braces, like
        # `checkpoints`' own `checkpointed=False` + this same set) — this entry is the
        # DB-era restatement of that always-true behavior, not a new policy.
        "sessions",
        # THE permission table — one level per (owner, tool), for every tool in the app; it
        # absorbed `tool_grants` at revision 0040 and inherits that entry's reasoning
        # unchanged, for the identical mechanism: restore_checkpoint is a MODEL-CALLABLE tool with
        # mutates=("*",), so an INCLUDED permission table would mean "the user BLOCKS a tool
        # (or revokes an always_allow) -> the model restores any earlier checkpoint -> the
        # decision is gone", privilege escalation through an unrelated tool with no malice
        # required. A checkpoint undoes what Lucius did to your DATA; a permission level is
        # not something Lucius did, it is something the owner decided — and Settings >
        # Permissions / the connector's own page are the only places it changes. The store
        # still fires the write hook unconditionally (uniform with every sibling); this
        # membership is what makes the capture a structural no-op, mirroring
        # signals'/corrections' own precedent.
        "tool_permissions",
        # SQL ACCESS (revision 0050, docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md)
        # — the audit ring for Lucius's read-only SQL tool. Same reasoning as `tool_call_log`
        # above, which it is modelled on: rows are written INSIDE every turn's
        # CheckpointContext (one per `run_sql_query` dispatch), and a rollback must never
        # rewrite the record of what was ASKED of the database. The record of a read is
        # forensic, not user data — a /rewind undoes what Lucius did to your data, and a
        # read did nothing to it.
        "sql_query_log",
        # B20 phase 2, L5 (statement-parse-status ADR) — a narrow, ever-overwritten cache of
        # ONE in-flight (or just-finished) statement's stage/page progress, mirroring
        # price_points'/evidence's/asset_brief's own "derived, rebuildable, never snapshotted"
        # reasoning: a rewind must not resurrect stale read-progress for a parse that has
        # since moved on (or been deleted, via this table's own ON DELETE CASCADE).
        "statement_parse_progress",
        # Notable Price Movement per-session cache (2026-09-20) — deterministic session facts +
        # a cited LLM paragraph, rebuildable from the price provider's bars and `evidence`;
        # same "derived, never snapshotted" class as asset_brief.
        "price_movement_notes",
    }
)

# COMPANIONS — files that are ONE referential unit, captured and restored together.
#
# A checkpoint captures only the files a turn actually wrote, and `restore` writes back
# exactly those. That is correct for independent stores and WRONG for a pair that points at
# each other.
#
# PLAN 3 TASK 4 — STRUCTURALLY OBSOLETE for accounts.json <-> transactions.json, removed:
# an account's opening pointer (`opening_txn_id`) into a transaction row is exactly the same
# referential-unit hazard as before, but the file-era mechanism that protected it — "capture
# the WHOLE partner FILE alongside this one" — only ever existed because a file-level
# checkpoint had no finer unit to work with. The DB half (spec D7, `record_db_before` in
# _context.py) captures a before-image per ROW, per TABLE, for every row a turn actually
# touches — an opening edit that changes one `accounts` row and one `transactions` row
# images BOTH rows, in the SAME write_log, replayed together by the SAME restore. Row-level
# images already ARE the atomic pair capture; a table-name companions map has nothing left
# to do for them. Keep the dict (and `snapshot_before_write`'s lookup) for any FUTURE
# file-pair — this is an emptied instance of a still-real mechanism, not a retired one.
_COMPANIONS: dict[str, tuple[str, ...]] = {}
