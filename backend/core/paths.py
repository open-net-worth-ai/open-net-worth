"""Central registry of all data and cache file paths.

All backend modules must import from here — never construct paths inline.
To relocate a file, change its entry here; nothing else needs updating.

The root locations (``DATA_DIR`` / ``CACHE_DIR``) come from ``core.config`` so
they can be overridden via the ``DATA_DIR`` / ``CACHE_DIR`` env vars. Their
defaults are ``~/.open-net-worth/data`` and ``~/.open-net-worth/cache`` — OUTSIDE
the git checkout, so a fresh clone runs with zero configuration and ``git clean`` /
``git pull`` can never touch a user's live database (2026-08-23 — see
``core/config.py`` and ``docs/adr/2026-08-23-zero-config-default-data-dir.md``).
"""

import os
from pathlib import Path

from backend.core import platform_fs
from backend.core.config import (
    assert_qa_cache_dir_safe,
    assert_qa_data_dir_safe,
    assert_qa_database_url_safe,
    assert_qa_log_dir_safe,
    get_settings,
)
from backend.core.shipped_assets import PRODUCT_DOCS_DIR as _SHIPPED_PRODUCT_DOCS_DIR

# core/paths.py lives one level below backend/, so BACKEND is the parent's parent.
BACKEND = Path(__file__).parent.parent

_settings = get_settings()

DESKTOP_MODE = _settings.desktop_mode
DATA_DIR = _settings.data_dir
CACHE_DIR = _settings.cache_dir

# Every blob store owns exactly ONE directory under this shared parent — one consistent,
# discoverable answer to "where do this app's files live" instead of four flat siblings of
# the DB file/backups/logs (2026-08-21 — see
# docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md), AMENDED 2026-08-22 (see
# that ADR's amendment + .claude/rfcs/2026-08-22-consolidate-derived-data-storage.md, Option
# A): everything a store keeps on disk for id X lives under FILES_DIR / "<store>" and starts
# with X — `X<ext>` is the original the user gave us, `X.parsed.json` is what we computed
# from it. STATEMENTS_PARSED_DIR (which used to hold that derived cache OUTSIDE this tree)
# is RETIRED — its content now lives at FILES_DIR / "statements" / "<id>.parsed.json",
# exactly like every other store's derived cache (`attachments`' own `<id>.parsed.json`
# sidecar, renamed the same day for the same rule).
FILES_DIR = DATA_DIR / "files"

# QA data-safety guard — fires immediately at import time when ONW_QA_MODE=1.
# Covers both `uvicorn backend.main:app` and bare `import backend.main` (TestClient).
# DATA_DIR and CACHE_DIR are guarded here; LOGS_DIR is guarded after it resolves
# below. Normal production boots (no ONW_QA_MODE) skip all of this entirely.
assert_qa_data_dir_safe(DATA_DIR)
assert_qa_cache_dir_safe(CACHE_DIR)
SESSIONS_DIR = DATA_DIR / "sessions"

# Ensure directories exist at import time. parents=True so a first boot on a fresh
# machine (where the ~/.open-net-worth root does not exist yet) creates the whole
# chain instead of crashing with FileNotFoundError on the missing parent.
#
# _home_root is the zero-config instance root (~/.open-net-worth) — the SAME literal
# path core.config's private _INSTANCE_ROOT names, recomputed here rather than
# imported since it's a private symbol of a sibling module. Checked BEFORE the mkdir
# calls below so we know whether WE are the one creating it: mkdir(parents=True)
# creates this root as a side effect whenever DATA_DIR/CACHE_DIR default to inside it,
# at the process umask (0755 on a typical single-user box) — group/other-readable on
# any machine with a shared group (e.g. macOS's "staff"), for a directory whose leaves
# already assume owner-only (onw.db 0600, files/ 0700, logs/ 0700). Owner-only from
# creation closes that gap without touching a directory an operator already has (a
# pre-2026-08-23 install, or one that deliberately customized permissions) — chmod
# fires only on the "we just made this" path, never on an existing root.
_home_root = Path.home() / ".open-net-worth"
_creating_home_root = not _home_root.exists()

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

if _creating_home_root and _home_root.is_dir():
    platform_fs.restrict_to_owner(_home_root)

DB_FILE = (
    DATA_DIR / "onw.db"
)  # the SQLite database (spec 2026-08-05); Postgres via ONW_DATABASE_URL

# Same QA data-safety guard as above, for the ONW_DATABASE_URL override: a QA
# boot that isolates DATA_DIR but sets ONW_DATABASE_URL to the live sqlite file
# would still write QA rows into the operator's real database. No-op for a
# non-sqlite URL (e.g. a deliberately provisioned QA Postgres server) and
# outside QA mode.
assert_qa_database_url_safe(os.environ.get("ONW_DATABASE_URL"))

# ── User data ─────────────────────────────────────────────────────────────────
INSTRUMENTS_JSON = DATA_DIR / "instruments.json"  # instrument master (the "what")
POSITIONS_JSON = (
    DATA_DIR / "positions.json"
)  # holdings: instrument × account × qty/cost (the "how much")
LOTS_JSON = DATA_DIR / "lots.json"  # acquisition lots backing positions (cost basis + FIFO order)
REALIZED_EVENTS_JSON = (
    DATA_DIR / "realized_events.json"
)  # append-only disposal events + realized P&L (P2-INV1)
INCOME_EVENTS_JSON = (
    DATA_DIR / "income_events.json"
)  # dividend / interest / distribution income events (P2-INV2)
GRANTS_JSON = (
    DATA_DIR / "grants.json"
)  # equity-compensation grants (RSU/LTIP) — checkpoint-INCLUDED (user money-facts)
OPTION_CONTRACTS_JSON = (
    DATA_DIR / "option_contracts.json"
)  # derivative overlay contracts — checkpoint-INCLUDED (portfolio truth, like lots/grants)
HOLDING_PLANS_JSON = (
    DATA_DIR / "holding_plans.json"
)  # why you own / would buy an instrument — checkpoint-INCLUDED (user intent)
VEST_EVENTS_JSON = (
    DATA_DIR / "vest_events.json"
)  # append-only vest-event ledger (income txn + lot + locked-flip per vest) — checkpoint-INCLUDED
PRICE_HISTORY_JSON = (
    DATA_DIR / "price_history.json"
)  # persisted daily price series per price_source_id (history + day P&L)
PROFILE_JSON = DATA_DIR / "profile.json"
ACCOUNTS_JSON = DATA_DIR / "accounts.json"
INSTITUTIONS_JSON = DATA_DIR / "institutions.json"
TRANSACTIONS_JSON = DATA_DIR / "transactions.json"
# Dedup / match engine (RFC transaction-dedup-engine): verdict cache, merge undo journal,
# the grouped review queue, and the engine/LLM settings.
MATCH_DECISIONS_JSON = DATA_DIR / "match_decisions.json"
MATCH_REVIEWS_JSON = DATA_DIR / "match_reviews.json"
MERGE_JOURNAL_JSON = DATA_DIR / "merge_journal.json"
REJECTED_IDENTITIES_JSON = DATA_DIR / "rejected_identities.json"
DEDUP_CONFIG_JSON = DATA_DIR / "dedup_config.json"
ACTIONS_JSON = DATA_DIR / "actions.json"
# VESTIGIAL (Wave 5C): the subscription-detection module that wrote this was dropped
# ("Drop subscriptions module (moved to payee/automation detection)") well before this
# constant's store (`backend/db/subscription_store.py`, a JsonStore subclass) was retired
# along with it — zero live readers/writers today. Kept only so the Wave-5C cutover can
# find and absorb its 5 real historical rows (user dismiss/confirm decisions) byte-
# identically; mirrors AGENT_FINDINGS_JSON's own post-retirement fate. Never excluded,
# never portable — preserved as-is, not a new classification.
SUBSCRIPTIONS_JSON = DATA_DIR / "subscriptions.json"
COMMITMENTS_JSON = (
    DATA_DIR / "commitments.json"
)  # obligations + installment schedules + funding plans
OWNED_ASSETS_JSON = (
    DATA_DIR / "assets.json"
)  # things you own valued by a dated manual mark (ADR 2026-09-06-assets-tab-faithful-port)
OWNED_ASSET_CATEGORIES_JSON = (
    DATA_DIR / "asset_categories.json"
)  # user-created owned-asset categories (builtins are a services.owned_assets constant)
DASHBOARD_LAYOUT_JSON = (
    DATA_DIR / "dashboard_layout.json"
)  # Overview widget-dashboard layout: named views, each a flat array of widget instances
# (position/size/config/title/limit/alert only — never a derived figure). User-authored
# planning config like PLAN_STATE_JSON — checkpoint-INCLUDED, portable.
WATCHLIST_JSON = DATA_DIR / "watchlist.json"
# Standalone ticker-keyed price alerts, decoupled from watchlist items (ADR 0014).
# Durable user money-config like watchlist.json: checkpoint-INCLUDED and portable.
ALERTS_JSON = DATA_DIR / "alerts.json"
# News source registry (INT9 Phase A, .claude/rfcs/news-source-management.md): builtin + user
# rows — {what to fetch, how often, how it maps to watched entities, policy}. Durable user
# config like watchlist.json/alerts.json: checkpoint-INCLUDED and portable.
NEWS_SOURCES_JSON = DATA_DIR / "news_sources.json"
# INTEL_SIGNALS_JSON is the FROZEN historical source-file name the plan-4d config cutover
# (revision 0016, via db/alembic/versions_evidence_source.py's own "never rename one" rule)
# still references — kept unchanged for that reason. `intel_signals` itself was split into
# intel_stories/signal_timeline_entries/daily_digests at revision 202608301500 (RFC
# intel-signals-data-model); INTEL_STORIES_JSON below is the CURRENT bundle filename for the
# story+timeline half (portable_bridge.py's bespoke Bridge). daily_digests never gets a
# PORTABLE_FILES constant — it is deliberately wipe-only, referenced only by a symbolic
# "daily_digests.json" string key in portable_bridge._WIPE_FNS.
INTEL_SIGNALS_JSON = DATA_DIR / "intel_signals.json"
INTEL_STORIES_JSON = DATA_DIR / "intel_stories.json"
ASSET_BRIEF_JSON = (
    DATA_DIR / "asset_briefs.json"
)  # per-instrument synthesized brief (news, key_issues, facts, peers, description) — checkpoint-EXCLUDED (derived search/LLM cache, like price_history/intel_signals)
EVIDENCE_JSON = (
    DATA_DIR / "evidence.json"
)  # accumulated news/web evidence cache behind Intel scans — checkpoint-EXCLUDED, instance-local (derived, rebuildable from sources, like price_history/asset_briefs)
SIGNALS_JSON = DATA_DIR / "signals.json"
CONNECTOR_SESSIONS_JSON = DATA_DIR / "connector_sessions.json"
MEMORY_JSON = DATA_DIR / "memory.json"
MEMORY_SETTINGS_JSON = DATA_DIR / "memory_settings.json"
AGENT_MEMORY_JSON = DATA_DIR / "agent_memory.json"
PENDING_TRANSACTIONS_JSON = DATA_DIR / "pending_transactions.json"
IMPORT_BATCHES_JSON = DATA_DIR / "import_batches.json"
STATEMENTS_JSON = DATA_DIR / "statements.json"
STATEMENTS_FILES_DIR = FILES_DIR / "statements"


def statements_files_dir() -> Path:
    """The ONE accessor every consumer of the statements blob directory must call — never
    import the ``STATEMENTS_FILES_DIR`` module constant directly (security-gate fix wave,
    2026-08-22 consolidate-derived-data-storage revision 0046 review, Fix 3).

    ``from backend.core.paths import STATEMENTS_FILES_DIR`` binds a MODULE-LOCAL name in the
    IMPORTING module at import time — a separate Python object reference from this module's
    own ``STATEMENTS_FILES_DIR``. Before this accessor existed, five different modules each
    held their own such binding (this module, the legacy ``backend/paths.py`` re-export shim,
    ``statement_store.py``, ``money_io.py``, ``portable_bridge.py``), so a test isolating the
    statements directory had to ``monkeypatch.setattr`` every one of them individually (the
    ``_isolate_statement_files`` fixture in ``tests/conftest.py`` grew to five call sites,
    and ~13 other test files each needed their own two-module "double-monkeypatch dance") —
    and a caller that forgot one binding got a SILENT split-brain: e.g.
    ``money_io._reinline_statement`` reading the wrong (unpatched) directory degrades to
    ``parsed_rows=None`` rather than raising, so a statement looks "never parsed" instead of
    surfacing the bug.

    A bare function that reads this module's OWN global on every call closes that class of
    bug structurally: every consumer calls ``statements_files_dir()`` instead of importing
    the constant, so there is exactly ONE binding left to patch (this module's
    ``STATEMENTS_FILES_DIR``) and every consumer sees a patch instantly, resolved fresh at
    call time — not frozen at whatever value was live the first time some OTHER module
    happened to import it."""
    return STATEMENTS_FILES_DIR


ARTIFACTS_JSON = (
    DATA_DIR / "artifacts.json"
)  # chat-artifact metadata (id/title/filename/type/size); bytes live under ARTIFACTS_DIR — checkpoint-EXCLUDED (rendered byproduct of a chat turn), portable (transcripts reference artifact ids)
ARTIFACTS_DIR = FILES_DIR / "artifacts"  # artifact document bytes (<id>.html / <id>.svg)
# Chat attachments (cross-channel-attachments RFC, Phase 2): metadata rows in
# ATTACHMENTS_JSON, the parsed payload the model consumes (text/images/import bytes,
# base64-encoded JSON) under ATTACHMENTS_DIR — permanent until user-deleted (Settings →
# Uploaded files); replaced the 30-min in-memory cache that evaporated on every --reload
# restart, then the `uploads`/`UploadStore` table+store were renamed to `attachments`/
# `AttachmentStore` (ADR 2026-08-16-attachment-store-replaces-upload-store) to carry the
# durable cross-channel record (source_channel/content_hash/media_type/…) the RFC
# requires — Telegram photos now write here too instead of surviving only as inline
# transcript image blocks. Both checkpoint-EXCLUDED (a Lucius restore must never
# resurrect a deleted bank statement), both portable.
ATTACHMENTS_JSON = DATA_DIR / "attachments.json"
ATTACHMENTS_DIR = FILES_DIR / "attachments"
# Transaction receipts (cross-channel-attachments RFC Phase 4, items 23/24 — see
# docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md "Transaction
# receipts"): durable evidence a transaction cites, independent of AttachmentStore and
# StatementStore — metadata rows in RECEIPTS_JSON, original bytes under RECEIPTS_DIR,
# mirroring ATTACHMENTS_JSON/ATTACHMENTS_DIR's own HYBRID shape exactly. The
# transaction<->receipt many-to-many link table has its own file name/constant
# (TRANSACTION_RECEIPTS_JSON) but no directory of its own — a link row carries no bytes.
# Both tables checkpoint-EXCLUDED (a Lucius restore must never resurrect a user-deleted
# receipt or relink one it explicitly unlinked) and portable (the user's own evidence).
RECEIPTS_JSON = DATA_DIR / "receipts.json"
RECEIPTS_DIR = FILES_DIR / "receipts"
TRANSACTION_RECEIPTS_JSON = DATA_DIR / "transaction_receipts.json"
EMAIL_INGEST_JSON = DATA_DIR / "email_ingest.json"
# Email provenance snapshots (EM1): a copy of the email content the email-transaction-logger
# agent read at fetch time, so a booked transaction can be cross-checked against its source.
# Durable user evidence (not a disposable cache) — checkpoint-INCLUDED, portable.
EMAIL_SNAPSHOTS_JSON = DATA_DIR / "email_snapshots.json"
# Connector (bank/broker) provenance snapshots (RFC connectors-as-data-sources §C) — DB-native
# from birth (no JSON predecessor), like `claims`/`daily_digests`; this constant exists purely
# as the portable-bundle member name / registry key, mirroring EMAIL_SNAPSHOTS_JSON's own
# "durable user evidence" posture: checkpoint-INCLUDED, portable.
CONNECTOR_SNAPSHOTS_JSON = DATA_DIR / "connector_snapshots.json"
AUTH_JSON = (
    DATA_DIR / "auth.json"
)  # instance auth secrets (password hash, session key, API token) (P3-AUTH)
APP_CONFIG_JSON = (
    DATA_DIR / "app_config.json"
)  # instance branding / white-label (app_name, accent_color, logo_url) (P3-ON) — NOT portable, NOT checkpointed
MODEL_CONFIG_JSON = (
    DATA_DIR / "model_config.json"
)  # runtime AI provider/model config (provider, model, base_url, write-only api_key) — gitignored, chmod 600, checkpoint-EXCLUDED (holds a secret)
PREFERRED_MODELS_JSON = (
    DATA_DIR / "preferred_models.json"
)  # the owner's curated model shortlist ({"models": ["<provider_key>:<model_id>", …]}) — surfaced as a "Preferred" group in every model picker. User intent, portable, checkpoint-included (no secrets). Succeeds routing_config.json, dropped with auto model switching (ADR 2026-08-06-auto-model-switching-removed)
CHANNELS_CONFIG_JSON = (
    DATA_DIR / "channels_config.json"
)  # outbound notification-channel config (email-out + webhook; write-only secrets) — gitignored, chmod 600, checkpoint-EXCLUDED
# Connector config files below are LEGACY paths: every connector now lives in the `connectors` /
# `connector_secrets` tables, and connectors read no environment variables. The constants remain
# only so `core.sensitive` keeps classifying a leftover file (or its `.migrated-<ts>` rename) as a
# credential. The descriptions are of the retired file shape.
TELEGRAM_CONFIG_JSON = (
    DATA_DIR / "telegram_config.json"
)  # Telegram connector config (bot_token write-only + allowed_chat_ids + enabled); gitignored, checkpoint-EXCLUDED (holds a secret)
SLACK_CONFIG_JSON = (
    DATA_DIR / "slack_config.json"
)  # Slack two-way chat connector config (bot_token + signing_secret write-only secrets + allowed_sender_ids + default_channel_id + enabled); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
DISCORD_CONFIG_JSON = (
    DATA_DIR / "discord_config.json"
)  # Discord two-way chat connector config (bot_token write-only secret + public_key Ed25519 verify key + application_id + allowed_sender_ids + default_channel_id + enabled); gitignored, chmod 600, checkpoint-EXCLUDED (holds a secret)
WHATSAPP_INBOUND_CONFIG_JSON = (
    DATA_DIR / "whatsapp_inbound_config.json"
)  # WhatsApp two-way chat connector INBOUND config (app_secret + verify_token write-only secrets + allowed_sender_ids + enabled + operator_trust); gitignored, chmod 600, checkpoint-EXCLUDED (holds a secret). Outbound reply creds live in channels_config.json["whatsapp"]
SIMPLEFIN_CONFIG_JSON = (
    DATA_DIR / "simplefin_config.json"
)  # SimpleFIN bank-aggregation connector config (access_url write-only secret + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds a secret)
GOCARDLESS_CONFIG_JSON = (
    DATA_DIR / "gocardless_config.json"
)  # GoCardless (Bank Account Data) open-banking connector config (secret_id + secret_key/token write-only secrets + institution_id + requisition_id + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
PLAID_CONFIG_JSON = (
    DATA_DIR / "plaid_config.json"
)  # Plaid bank-aggregation connector config (client_id + secret/access_token write-only secrets + environment + item_id + /transactions/sync cursor + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
PLUGGY_CONFIG_JSON = (
    DATA_DIR / "pluggy_config.json"
)  # Pluggy.ai (Brazilian open-finance) bank-aggregation connector config (client_id + client_secret/api_key write-only secrets + item_ids[per-connection consent handles] + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
ALPACA_CONFIG_JSON = (
    DATA_DIR / "alpaca_config.json"
)  # Alpaca brokerage connector config (api_key_id + secret_key write-only secrets [bearer credential pair] + environment paper|live + enabled READ-sync switch + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds a SECRET key pair a restore must never resurrect) (alpaca-connector S1 read-sync; the S2 trading half was removed 2026-07-19)
IBKR_CONFIG_JSON = (
    DATA_DIR / "ibkr_config.json"
)  # IBKR Flex-Web-Service connector config (flex_token write-only secret [read-only Flex credential; cannot trade] + flex_query_id + enabled READ switch + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds a credential a restore must never resurrect) (ibkr-connector read-sync; the dormant trading flag was removed 2026-07-20)
SEARCH_CONFIG_JSON = (
    DATA_DIR / "search_config.json"
)  # Web Search connector config (selected provider tavily|perplexity|… + per-provider write-only api_key + enabled); gitignored, chmod 600, checkpoint-EXCLUDED (holds API keys)
ENABLE_BANKING_CONFIG_JSON = (
    DATA_DIR / "enable_banking_config.json"
)  # Enable Banking (EU PSD2 open banking) connector config (application_id + RSA private_key write-only secrets + cached JWT + aspsp/country + session_id[per-bank consent handle] + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
YODLEE_CONFIG_JSON = (
    DATA_DIR / "yodlee_config.json"
)  # Yodlee (Envestnet|Yodlee) bank-aggregation connector config (client_id + secret write-only + username/password user-creds write-only + transient session tokens + api_endpoint + enabled + poll state + account_map); gitignored, chmod 600, checkpoint-EXCLUDED (holds secrets)
SKILLS_STATE_JSON = (
    DATA_DIR / "skills_state.json"
)  # runtime state keyed by skill id: {enabled, author, created_at, updated_at}; non-content fields only — checkpoint-COVERED
SKILLS_DIR = (
    DATA_DIR / "skills"
)  # file-first skill folders: <id>/{SKILL.md,scripts/,references/,assets/}; content is truth; checkpoint-COVERED
SKILL_BUNDLES_DIR = (
    DATA_DIR / "skill_bundles"
)  # LEGACY: content-addressed bundle files (immutable per version). Left as inert history after migration; never written again. checkpoint-EXCLUDED
USER_PACKS_DIR = (
    DATA_DIR / "user_packs"
)  # user-installed community extension packs: <type>/<id>/<version>/{manifest.json,data/,scripts/,ui/} — immutable per version; gitignored; checkpoint-EXCLUDED (user-installed content, large, installs are explicit human actions) (P4-EXT phase-1)
SHIPPED_EXTENSIONS_DIR = (
    BACKEND / "extensions"
)  # shipped/built-in extension packs: <engine>/<id>/{manifest.json,data/,…} — read-only by the loader, user packs take precedence (P4-EXT phase-1). Moved off `backend/data/extensions` 2026-08-28: that path made `backend/data` do double duty as BOTH the legacy in-repo instance directory and a shipped-content directory, which is what forced the legacy detector to be non-recursive. Ships empty (the loader tolerates a missing dir); tests build their own pack in a tmp dir rather than depending on a fixture that exists only to be found.
MCP_CLIENTS_JSON = (
    DATA_DIR / "mcp_clients.json"
)  # outbound external-MCP server configs (url + write-only token + allowlist) (P3-MCP-CLIENT) — gitignored, chmod 600, checkpoint-EXCLUDED
AGENTS_JSON = (
    DATA_DIR / "agents.json"
)  # config-driven autonomous agents (definition + scoped tools + cadence) (P3-AG1)
AGENT_FINDINGS_JSON = (
    DATA_DIR / "agent_findings.json"
)  # append-only agent-run output/proposal review queue; runtime log → checkpoint-EXCLUDED (P3-AG1 S2)
AGENT_LESSONS_JSON = (
    DATA_DIR / "agent_lessons.json"
)  # per-agent distilled "lessons" from reflection over findings+outcomes; authored agent state → checkpoint-COVERED (P3-AG5 S1)
MCP_PENDING_CALLS_JSON = (
    DATA_DIR / "mcp_pending_calls.json"
)  # NONCE-BOUND pending external-MCP write calls: {nonce: {server_id, tool, args, created_at}} verbatim — the confirm card carries the nonce; apply_external_mcp_call(nonce) pops + fires the STORED args (model cannot substitute server/tool/args on apply) — single-use, capped, runtime queue → checkpoint-EXCLUDED, gitignored (external-mcp-write H1/M2)
MCP_WRITE_LOG_JSON = (
    DATA_DIR / "mcp_write_log.json"
)  # append-only audit log of MUTATING inbound-MCP calls ({ts, surface, tool, domains, args_summary, base_amount, checkpoint_id, result_ok}) — observability, not a gate; runtime/behavioral log → checkpoint-EXCLUDED, gitignored
TOOL_CALL_LOG_JSON = (
    DATA_DIR / "tool_call_log.json"
)  # PER-TOOL-CALL telemetry ring: one row per dispatch from EVERY surface ({ts, tool, alias_used, origin, session_id, turn_index, call_index, mutated, domains, ok, error_class, duration_ms, result_chars, input_chars, checkpoint_id}) — SIZES AND SHAPES ONLY, never raw args/results (real financial records). Answers the CORE_TOOL_NAMES-membership / result-cost / discovery-waste questions from evidence instead of judgement. Runtime/behavioral telemetry → checkpoint-EXCLUDED, NOT portable, gitignored (mirrors cost_log + tool_discovery_log)
CHAT_PAIRING_JSON = (
    DATA_DIR / "chat_pairing.json"
)  # pending chat-channel pairing requests {(connector,sender_id): {code, requested_at, sender_label}} for the `pairing` access policy — an unknown sender's pairing-CODE reply is recorded here; the operator approves → the sender id is appended to that channel's allowlist + the row cleared. Runtime queue (capped) → checkpoint-EXCLUDED, gitignored (chat-pairing)
RULES_JSON = (
    DATA_DIR / "rules.json"
)  # spend categorization rules (match → set category/tags) (P2-SP1)
RECURRING_STREAMS_JSON = (
    DATA_DIR / "recurring_streams.json"
)  # cash-flow recurring-stream USER STATE only — never the detection itself (recomputed every read): overlay rows {type:"overlay", id:<signature>, status:confirmed|dismissed} correcting a detected stream (retired `declared` rows may linger, unread; ADR 2026-10-16-declared-recurring-streams-are-retired). Drives services/cashflow's forward baseline (.claude/rfcs/2026-07-27-cashflow-stream-model.md); user-authored → checkpoint-INCLUDED, portable
STATEMENT_FORMATS_JSON = (
    DATA_DIR / "statement_formats.json"
)  # learned STATEMENT-IMPORT format memory ONLY (a source file's header fingerprint → its column schema/conventions) — a repeat import of a known source parses with ZERO LLM calls; user knowledge → checkpoint-INCLUDED, portable (RFC statement-import-unified D3)
PAYEES_JSON = (
    DATA_DIR / "payees.json"
)  # first-class payee entities (id/name/default_categories/aliases/usage); txns store payee_id (P4-PAYEE phase 1)
TARGETS_JSON = DATA_DIR / "targets.json"  # deadline savings targets ("save X by date Y") (P2-BU1)
PLAN_STATE_JSON = (
    DATA_DIR / "plan_state.json"
)  # Outlook saved scenarios + FIRE baseline override ({scenarios:[...], baseline:{...}|null}); user-authored config → checkpoint-INCLUDED, portable
BUDGET_ASSIGNMENTS_JSON = (
    DATA_DIR / "budget_assignments.json"
)  # YNAB envelope assignments: {YYYY-MM: {category: assigned_base_amount}} (PLAN)
BUDGET_ENGINE_STATE_JSON = (
    DATA_DIR / "budget_engine_state.json"
)  # default per-engine config for a pluggable budget engine with no bespoke store of its own: {engine_id: payload} — engine-agnostic, opaque payload (.claude/rfcs/2026-08-22-pluggable-budget-engines.md); user-authored config → checkpoint-INCLUDED, portable
CATEGORY_GROUPS_JSON = (
    DATA_DIR / "category_groups.json"
)  # envelope groups (PLAN) — the table is ``envelope_groups``; the filename stays: it is the pre-DB source + the portable bundle's entry name (existing exports)
ENVELOPES_JSON = (
    DATA_DIR / "envelopes.json"
)  # budget envelope entities — decoupled from category (.claude/rfcs/envelope-not-category.md P0); checkpoint-COVERED, portable
CATEGORIES_JSON = (
    DATA_DIR / "categories.json"
)  # category registry (store-seeded on first boot, user-owned thereafter) — checkpoint-COVERED, portable
SCHEDULED_TASKS_JSON = (
    DATA_DIR / "scheduled_tasks.json"
)  # scheduled/recurring tasks (reminders, recurring reviews) (P2-SK2)
AUTOMATIONS_JSON = DATA_DIR / "automations.json"  # canonical configuration — portable
AUTOMATION_RUNS_JSON = DATA_DIR / "automation_runs.json"  # durable run history — portable
AUTOMATION_RUNTIME_JSON = (
    DATA_DIR / "automation_runtime.json"
)  # durable Automation queue/outbox and run-ownership state — checkpoint-excluded
CORRECTIONS_JSON = (
    DATA_DIR / "corrections.json"
)  # category-correction signal log (learn-from-corrections → rule suggestions) (P2-SP2)
HARNESS_STATE_JSON = (
    DATA_DIR / "harness_state.json"
)  # self-harness watermark path — feature removed 2026-08-28 (see docs/launch/BOARD.md);
# constant kept ONLY because backend/db/alembic/versions_evidence_source.py's frozen
# re-import feeds revision 0025's historical cutover job, which must keep replaying on a
# fresh database. Not a live path — nothing reads or writes this file anymore.
BATCH_LEDGER_JSON = (
    DATA_DIR / "batch_ledger.json"
)  # pending/completed provider batch jobs (anthropic/openai Message Batch API rows) — runtime state → checkpoint-EXCLUDED (batch results are re-fetchable)

PENDING_ANALYSIS_JSON = (
    CACHE_DIR / "pending_analysis.json"
)  # durable ledger of crash events awaiting LLM analysis — runtime scheduler state → checkpoint-EXCLUDED

PENDING_PUSHES_JSON = (
    CACHE_DIR / "pending_pushes.json"
)  # durable retry queue for external notify() pushes that delivered to zero channels (INT6) — runtime scheduler state → checkpoint-EXCLUDED, drained by the analysis_sweep tick

# WAVE 5F (JSON->DB): the three CACHE_DIR watermarks below are now `runtime_state`
# namespace rows (backend/db/digest_state_store.py / news_screen_state_store.py /
# memory_consolidate_state_store.py) — same "genuinely state, not a rebuildable cache"
# reasoning as HARNESS_STATE_JSON/BATCH_LEDGER_JSON above. Kept as constants (source
# anchors for the 0037 cutover, never read at runtime again) — mirrors every other
# migrated store's own `*_JSON` constant staying in this registry forever.
DIGEST_STATE_JSON = (
    CACHE_DIR / "digest_state.json"
)  # daily-digest once-per-day delivery watermark (last_eod_date/last_premarket_date) — runtime scheduler state → checkpoint-EXCLUDED
NEWS_SCREEN_STATE_JSON = (
    CACHE_DIR / "news_screen_state.json"
)  # news-significance-screen rolling daily budget counter (day/screened) — runtime scheduler state → checkpoint-EXCLUDED
MEMORY_CONSOLIDATE_STATE_JSON = (
    CACHE_DIR / "memory_consolidate_state.json"
)  # memory-consolidation cadence watermark (last_count/ts) — runtime state → checkpoint-EXCLUDED

PENDING_PLANS_JSON = (
    DATA_DIR / "pending_plans.json"
)  # staged write calls awaiting the user's ✓ (the confirm/permission gate): {tool_use_id or token: {tool, args, change_type, created_at}} verbatim — approval re-calls the tool against the STORED args, so the model cannot substitute amount/category/account on confirm — single-use, capped, chmod 600, checkpoint-EXCLUDED and NOT portable (mirrors MCP_PENDING_CALLS_JSON). An awaiting-approval row NEVER expires (ADR 2026-07-30-approvals-never-expire); only the legacy minted-token preview rows keep the 15-min TTL. Moved out of CACHE_DIR on 2026-07-30 (adopted in place by db.pending_plan_store._migrate_from_cache_dir): a durable pause is state a live transcript depends on, so it belongs beside the sessions it pauses, not in a disposable cache

TXN_AUDIT_JSON = (
    DATA_DIR / "txn_audit.json"
)  # transaction provenance / audit trail (who/what-surface/session/intent + before-after diff per create|update|delete|restore); forensic record → checkpoint-EXCLUDED so a data restore never erases "how it got here" (transaction-provenance RFC)

RUN_HISTORY_JSON = (
    DATA_DIR / "run_history.json"
)  # append-only agent + task run history (status, error, streak-dedup companion); behavioral log → checkpoint-EXCLUDED (a checkpoint restore must never erase a failure record the user is relying on — same rationale as agent_findings.json/signals.json)

FILES_DIR.mkdir(exist_ok=True)
STATEMENTS_FILES_DIR.mkdir(exist_ok=True)
ARTIFACTS_DIR.mkdir(exist_ok=True)
ATTACHMENTS_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR.mkdir(exist_ok=True)
# Owner-only (2026-08-22 consolidate-derived-data-storage RFC §4.4): FILES_DIR now mixes
# raw originals with derived caches for every store — statements' parsed-rows sidecar
# (financial data at rest) used to be the one thing hardened to 0700, under the now-retired
# STATEMENTS_PARSED_DIR; that hardening is leveled up to the whole files/ tree instead of
# staying asymmetric with receipts/attachments (whose sidecars carry equally sensitive
# extracted text). Directory-level only, no per-file mode change.
for _files_leaf in (FILES_DIR, STATEMENTS_FILES_DIR, ARTIFACTS_DIR, ATTACHMENTS_DIR, RECEIPTS_DIR):
    os.chmod(_files_leaf, 0o700)

COST_LOG_JSON = DATA_DIR / "cost_log.json"
# Daily-aggregate rollup written when cost_log.json evicts old records.
# Keys: "YYYY-MM-DD|model|source" → {turns, input_tokens, cache_creation_tokens,
# cache_read_tokens, output_tokens, cost_usd}. session_id detail is intentionally
# lost for rolled-up records — daily aggregates are compact summaries only.
# Retention: unbounded (entries are tiny; ~50 bytes/key; ~400 days ≈ <1 MB typical).
COST_LOG_DAILY_JSON = DATA_DIR / "cost_log_daily.json"

# find_tools search-quality telemetry: every find_tools(query=…) call records one
# event {ts, query, kind, hits, n_groups} so search recall can be audited over time
# (app.log rotates; this capped ring does not). kind ∈ {ok, core_only, miss}.
# Checkpoint-EXCLUDED runtime telemetry — see ai/tools/discovery_log.py.
TOOL_DISCOVERY_LOG_JSON = DATA_DIR / "tool_discovery_log.json"

# ── Turn checkpoints (rollback) ───────────────────────────────────────────────
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
CHECKPOINTS_BLOBS = CHECKPOINTS_DIR / "blobs"
CHECKPOINTS_INDEX = CHECKPOINTS_DIR / "index.json"

CHECKPOINTS_BLOBS.mkdir(parents=True, exist_ok=True)

# ── Full-data disaster-recovery backups ───────────────────────────────────────
# A whole-DATA_DIR point-in-time copy on a daily wall-clock schedule (services.backup +
# backend.scheduler), independent of the AI-turn checkpoint system above: that one only
# captures AI-driven writes and is hard-capped at 50 turns, so a plain REST edit (e.g. the
# account Edit modal) or an incident older than ~50 turns has no rollback trail at all.
# A SIBLING of data/ under the working folder, not a child of it (2026-08-23): a snapshot
# stored inside the directory it snapshots dies with that directory. Still excluded from its
# own copy, which now also covers a legacy data/backups/ left behind by the move.
BACKUPS_DIR = _settings.backups_dir
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# ── Cache (ephemeral, safe to delete) ─────────────────────────────────────────
FX_CACHE_JSON = CACHE_DIR / "fx_cache.json"
BRIEFING_CACHE_JSON = CACHE_DIR / "briefing_cache.json"
MONITORING_PRICE_CACHE_JSON = CACHE_DIR / "monitoring_price_cache.json"
NAMES_CACHE_JSON = CACHE_DIR / "names_cache.json"
SECTORS_CACHE_JSON = CACHE_DIR / "sectors_cache.json"
MODELS_CACHE_JSON = CACHE_DIR / "models_cache.json"
# Rolling per-model latency/health stats measured from real turns (ttft, tok/s, errors).
# Runtime measurement, checkpoint-EXCLUDED — mirrors RUN_HISTORY_JSON.
MODEL_TELEMETRY_JSON = CACHE_DIR / "model_telemetry.json"
# Provider-result cache for automated sector/region classification (sys-classification RFC).
# Keyed by price_source_id → {sector, region, fund_lookthrough?, fetched_at}.
# CACHE-dir, checkpoint-EXCLUDED — mirrors SECTORS_CACHE_JSON.
CLASSIFICATION_CACHE_JSON = CACHE_DIR / "classification_cache.json"
# Per-domain favicon proxy cache (services/favicons.py): index.json (URL → resolved-icon
# metadata, TTL 30d positive / 1d negative) PLUS the fetched icon bytes themselves
# (<domain>.ico/.png), all under this one directory — a genuine rebuildable provider-
# response cache, like the six above. A DIRECTORY constant (not a single JSON file)
# because its content is per-domain, dynamically named, and partly binary.
FAVICONS_DIR = CACHE_DIR / "favicons"
# Isolated $CODEX_HOME for spawned Codex CLI child processes (ai/models/codex_home.py) —
# NOT app-owned data at all: a third-party tool's own config/cache home (config.toml, its
# own sqlite dbs, its own internally-shaped cache json). Must stay a real filesystem tree
# for that external CLI to function; "a deleted home is re-seeded on the next spawn" per
# that module's own docstring, so it is disposable like every cache above even though its
# contents are opaque to us.
CODEX_HOME_DIR = CACHE_DIR / "codex-home"

# ── Product documentation corpus (tracked source, NOT user data) ──────────────
# The one-corpus rule (docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md):
# docs/product/**/*.md is the single source of truth read by services/product_docs.py,
# which feeds both the Lucius docs tool (ai/tools/docs.py) and the in-app Help route
# (api/product_docs.py). Anchored on the repo root in a checkout and on backend/_bundled/
# inside an installed wheel (core/shipped_assets.py), never on DATA_DIR — this ships with
# the code, is read-only at runtime, and must not move when DATA_DIR is overridden.
PRODUCT_DOCS_DIR = _SHIPPED_PRODUCT_DOCS_DIR

# ── Application log file ──────────────────────────────────────────────────────
# Written by core.logging.add_file_handler(); gitignored; never in checkpoints.
# Honors the LOGS_DIR env var (via core.config) so logs can live outside the repo
# alongside DATA_DIR/CACHE_DIR; defaults to the historical backend/logs.
LOGS_DIR = _settings.log_dir
assert_qa_log_dir_safe(LOGS_DIR)  # QA must not write logs into the live instance tree
LOG_FILE = LOGS_DIR / "app.log"

LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ── Intentionally file-backed stores (2026-08-10, Wave 5F) ────────────────────────────
# The JSON->DB migration's own exit criterion, stated by the owner: "everything must be
# in database, there should be no .json file at all, except the ones that really deserve
# it." This frozenset is the WHOLE of "deserve it" — every runtime path allowed to still
# be a file after Wave 5F, and why. A new runtime `.json`/blob file that appears at a
# path NOT covered here is a bug, not a judgment call: new stores default to the
# database (`JsonStore` is RETIRED — see CLAUDE.md); a genuine new exception is added
# HERE, with its own reasoning, not invented ad hoc at the call site. See
# `docs/adr/2026-08-10-intentionally-file-backed-stores.md` for the full rule and
# rationale; `tests/db/test_no_stray_json_store.py` enforces it going forward.
#
# Three shapes, mixed in one frozenset — `is_intentionally_file_backed` tells them apart
# by whether a candidate path IS a member outright (an exact file) or is nested anywhere
# under one that is itself a directory:
#
#   * Exact files — genuine REBUILDABLE PROVIDER/RESPONSE CACHES. Each is disposable (an
#     operator can delete it any time; the app just re-fetches/re-derives it on the next
#     read) and none is user-authored data — losing one costs a slower next read, never a
#     fact. Indexing one into the database would buy nothing (nothing ever queries INTO
#     it — each is read/written whole, like the JSON stores this migration retired) while
#     adding write-hook/checkpoint overhead to a high-churn path with no user-facing
#     upside.
#   * Directories — BLOB BYTE-POOLS, or a third-party tool's own home. A large
#     binary/HTML/gzip blob or per-item parsed-statement document living IN the database
#     would bloat `onw.db` and slow every turn-checkpoint snapshot of it (spec D5's own
#     "Tier-4 files stay files" ruling — see docs/DATA_MODEL.md); a third-party tool's own
#     config/cache tree (CODEX_HOME_DIR) cannot move into our database at all, since that
#     EXTERNAL process reads it directly off disk, not through any store this app owns.
#   * Directories — HISTORICAL/BACKUP RECORD, frozen at capture time and never rewritten,
#     restore-only (a caller reads the whole snapshot as one opaque unit, never an
#     individual store inside it): `BACKUPS_DIR` (services/backup.py's daily
#     disaster-recovery snapshots — mostly `.zip` today, but includes at least one
#     surviving pre-zip DIRECTORY-format snapshot whose loose `.json` files this wave's
#     own whole-tree audit found, exactly the kind of legacy shape this category exists
#     to cover) and `SKILL_BUNDLES_DIR` (paths.py's own pre-existing comment already
#     called this "LEGACY... left as inert history after migration; never written again"
#     — this frozenset is the first place that was ever machine-CHECKED, not just stated).
#     Not a cache (nothing rebuilds a specific day's backup) and not an ordinary blob pool
#     (it holds the SAME shape the JSON stores this migration retired had, just frozen) —
#     genuinely a third reason, not a mis-filed instance of the first two.


def _resolve_ancestors_only(path: Path) -> Path:
    """Absolute + every ANCESTOR symlink resolved (`..`/`.` collapsed, `/var` ->
    `/private/var` on macOS, an overridden `DATA_DIR` that is itself a symlink, …) —
    but, unlike a plain `path.resolve()`, never follows a symlink that IS the final path
    component itself.

    Two real, DIFFERENT symlink hazards, found empirically by this wave's own final
    whole-tree audit re-run (neither anticipated up front) motivate the split:

    1. `CODEX_HOME_DIR/auth.json` is a DELIBERATE symlink to the operator's own
       `~/.codex/auth.json` (`ai/models/codex_home.py`'s own docstring — "the whole
       point of this transport is spending the user's existing codex login"). A plain
       `path.resolve()` follows it to a target entirely OUTSIDE `CACHE_DIR` and wrongly
       reports it as a stray — what this check actually cares about is where the
       DIRECTORY ENTRY lives (inside a documented exception, placed there by our own
       code), never what a symlink happens to point at.
    2. Conversely, a bare `os.path.abspath()` (no resolution at all) failed on macOS
       test runs: `/tmp`/`/var` are themselves symlinks to `/private/tmp`/`/private/var`,
       so `UPLOADS_DIR` built from an un-resolved `/var/folders/...` env-var override and
       a freshly-globbed candidate path that happened to walk through the ALREADY-
       resolved `/private/var/folders/...` spelling compared unequal despite naming the
       identical file — a real file was wrongly flagged as a stray (the file involved was
       ATTACHMENTS_DIR, née UPLOADS_DIR).

    Resolving every ancestor while leaving the final component alone satisfies both: a
    root-level infrastructure symlink normalizes away (hazard 2), while a symlink WE
    placed as the final component is compared by its own location, not its target
    (hazard 1)."""
    resolved_parent = path.parent.resolve()
    return resolved_parent / path.name


INTENTIONALLY_FILE_BACKED: frozenset[Path] = frozenset(
    _resolve_ancestors_only(p)
    for p in (
        # Provider/response caches — exact files (Task 2 of Wave 5F judged all four of
        # its own named CACHE_DIR watermark candidates to be genuine STATE, not cache —
        # none of them joins this list; they moved to `runtime_state` instead, see
        # backend/db/tables.py's own Wave 5F comment).
        FX_CACHE_JSON,
        MODELS_CACHE_JSON,
        SECTORS_CACHE_JSON,
        NAMES_CACHE_JSON,
        MONITORING_PRICE_CACHE_JSON,
        BRIEFING_CACHE_JSON,
        CLASSIFICATION_CACHE_JSON,
        # Blob byte-pools + a third-party tool's home — directories; every file anywhere
        # under one is exempt, however it's named.
        ARTIFACTS_DIR,
        ATTACHMENTS_DIR,
        RECEIPTS_DIR,
        STATEMENTS_FILES_DIR,
        CHECKPOINTS_BLOBS,
        FAVICONS_DIR,
        CODEX_HOME_DIR,
        # Historical/backup record — directories; see the category comment above.
        BACKUPS_DIR,
        SKILL_BUNDLES_DIR,
    )
)


def is_intentionally_file_backed(path: Path) -> bool:
    """True when `path` is a documented exception to "everything is in the database" —
    an exact cache file in INTENTIONALLY_FILE_BACKED, or nested anywhere under one of its
    directory members. Does NOT special-case a cutover's `.migrated-<ts>` / a Wave 5F
    cleanup's `.removed-<ts>` rename — those are inert historical relics, not a live
    store, and a caller auditing "does anything still WRITE outside the allowed set"
    checks for that suffix itself (mirrors `core.sensitive._MIGRATED_SUFFIX`'s own
    "renamed sources are a separate question from current secrecy" split)."""
    candidate = _resolve_ancestors_only(path)
    return any(
        candidate == member or member in candidate.parents for member in INTENTIONALLY_FILE_BACKED
    )
