"""Canonical "portable files" manifest (P2-PT1, core leaf).

The single source of truth for *which* files constitute the user's portable
data — consumed by both the export/import service (``services.portability``)
and ``/api/reset`` (``api.setup``). Keeping the list here, built from
``backend.paths`` constants, stops the file set being copy-pasted (and silently
diverging) across features.

This module is a core leaf: it imports only stdlib + ``backend.paths`` (itself a
core-tier re-export). It contains NO logic — just the manifest constants.

Included = the user's own data (profile, ledger, holdings, history, memory,
chat sessions). Excluded = caches (regenerate locally), runtime/transient state
(cost_log, connector_sessions, checkpoints, the signals feed, the email-ingest
poll cursor + config, the pending-review queue, the match-verdict cache, the
corrections log) and instance-local mappings.

Why the runtime/transient files are deliberately NOT portable (P2-PT1 fix):
they are *also* in ``ai.checkpoint._EXCLUDED_PATHS`` (never checkpointed), so an
import that overwrote them could never be rolled back by ``restore()`` — silent,
non-undoable data loss against the API's "every import is one undoable event"
promise. They are also instance-local poll/runtime state (the email
``uidvalidity``/``last_uid`` cursor desyncs ingest if moved between instances),
which has no business travelling in a portability bundle anyway.

A second, narrower reason applies to two of the twelve planning stores plan 4a task 7 added
to ``backend/db/portable_bridge.py``'s ``BRIDGED`` (so ``/api/reset``'s ``wipe_all`` reaches
them unconditionally, regardless of this list): "the pending-review queue" (``match_reviews.
json`` — the dedup/match engine's grouped review queue) and "the match-verdict cache"
(``match_decisions.json``) are checkpoint-INCLUDED — unlike every other file named above —
yet still correctly excluded here. Every row in either store is keyed on a PAIR of
transaction ids that ``/api/reset`` ALSO wipes in the same pass (``accounts.json``/
``transactions.json`` are two of the LIVE money six, wiped by the same ``wipe_all`` call), so
neither is independently meaningful data once a reset has actually run: a pending review or
cached verdict about a since-deleted transaction pair has nothing left to be about, and
losing it costs at most a repeated dedup question, never a historical financial fact. "the
corrections log" (``corrections.json``) is the third of this family, already
checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``) for the same "behavioral signal, not
user-authored, a ``/rewind`` must not resurrect it" reason. Contrast ``INCOME_EVENTS_JSON``
below (dividend/interest/distribution income history): an independently meaningful historical
fact, not a workflow artifact coupled to data the same reset already destroys — checkpoint-
INCLUDED real user data with NO such safe-to-lose reasoning is portable, full stop; omitting
it from this list while ``wipe_all`` still reaches it is the data-loss bug fix round 1 of the
plan 4a task 7 review caught and closed (git history / that task's report has the incident).

A THIRD, plain reason (not the "coupled-to-already-destroyed-data" logic above) covers the
one Tier-2 investment store plan 4b task 6 wired into ``portable_bridge``'s wipe machinery:
``ASSET_BRIEF_JSON`` (the per-instrument synthesized-brief cache — news/key_issues/facts/
peers/description, rebuilt on demand by ``POST /api/intel/assets/{id}/refresh``) is
checkpoint-EXCLUDED and correctly absent from this list for the SAME "derive, don't travel"
reason ``PRICE_HISTORY_JSON``/``INSTRUMENTS_JSON`` already establish above — not a user-
authored fact, cheap to rebuild, never worth a backup slot. Unlike those two (and unlike
``EVIDENCE_JSON``), its ``portable_bridge.BRIDGED`` entry doesn't exist AT ALL — only a wipe
fn — since a cache keyed by ``instrument_id`` has nothing coherent to export/import
independent of the instruments it describes; see that module's own PLAN 4B TASK 6 docstring
section for the full reasoning. The other seven investment stores (positions/lots/grants/
vest_events/option_contracts/realized_events/watchlist — real, irreplaceable portfolio data)
were ALREADY present below before that task touched anything; only their DB-bridge wiring
was new.
"""

from __future__ import annotations

from pathlib import Path

from backend.core.paths import statements_files_dir
from backend.paths import (
    ACCOUNTS_JSON,
    ACTIONS_JSON,
    AGENTS_JSON,
    ALERTS_JSON,
    ARTIFACTS_DIR,
    ARTIFACTS_JSON,
    ATTACHMENTS_DIR,
    ATTACHMENTS_JSON,
    AUTOMATION_RUNS_JSON,
    AUTOMATIONS_JSON,
    BUDGET_ASSIGNMENTS_JSON,
    BUDGET_ENGINE_STATE_JSON,
    CATEGORIES_JSON,
    CATEGORY_GROUPS_JSON,
    COMMITMENTS_JSON,
    CONNECTOR_SNAPSHOTS_JSON,
    DASHBOARD_LAYOUT_JSON,
    EMAIL_SNAPSHOTS_JSON,
    ENVELOPES_JSON,
    GRANTS_JSON,
    HOLDING_PLANS_JSON,
    IMPORT_BATCHES_JSON,
    INCOME_EVENTS_JSON,
    INSTITUTIONS_JSON,
    INSTRUMENTS_JSON,
    INTEL_STORIES_JSON,
    LOTS_JSON,
    MEMORY_JSON,
    MERGE_JOURNAL_JSON,
    NEWS_SOURCES_JSON,
    OPTION_CONTRACTS_JSON,
    OWNED_ASSET_CATEGORIES_JSON,
    OWNED_ASSETS_JSON,
    PAYEES_JSON,
    PLAN_STATE_JSON,
    POSITIONS_JSON,
    PRICE_HISTORY_JSON,
    PROFILE_JSON,
    REALIZED_EVENTS_JSON,
    RECEIPTS_DIR,
    RECEIPTS_JSON,
    RECURRING_STREAMS_JSON,
    REJECTED_IDENTITIES_JSON,
    RULES_JSON,
    SCHEDULED_TASKS_JSON,
    SKILLS_DIR,
    SKILLS_STATE_JSON,
    STATEMENTS_JSON,
    TARGETS_JSON,
    TRANSACTION_RECEIPTS_JSON,
    TRANSACTIONS_JSON,
    TXN_AUDIT_JSON,
    VEST_EVENTS_JSON,
    WATCHLIST_JSON,
)

# ── Flat data stores (single JSON file each) ──────────────────────────────────
# Order is the canonical bundle order. The bundle stores each by ``Path.name``,
# which is the allowlist used to reject path-traversal / unknown entries on import.
PORTABLE_FILES: list[Path] = [
    PROFILE_JSON,
    ACCOUNTS_JSON,
    INSTITUTIONS_JSON,
    TRANSACTIONS_JSON,
    # Transaction provenance/audit trail (plan 3 task 6) — the money six's remaining two
    # money-core files (accounts/transactions/statements/import_batches were already here;
    # this and merge_journal below were never added). Historically NEVER wiped by /api/reset
    # (never in this manifest at all — see the task-6 report for the git-history proof) —
    # joining PORTABLE_FILES here is a deliberate behavior change: reset means reset, the same
    # "portable-status and reset-status are independent questions" precedent evidence.json
    # already established (portable_bridge.py's own docstring) applies here too, just via the
    # opposite membership (txn_audit stays out of ai.checkpoint's per-turn undo — a restore must
    # not erase the audit trail — but that is a SEPARATE exclusion list, _EXCLUDED_TABLES, from
    # this one).
    TXN_AUDIT_JSON,
    # Category registry — user-owned data: seed once, then user can add/delete/rename freely.
    # Must travel with the bundle so a restored instance sees the same category set.
    CATEGORIES_JSON,
    INSTRUMENTS_JSON,
    POSITIONS_JSON,
    LOTS_JSON,
    REALIZED_EVENTS_JSON,
    # Dividend/interest/distribution income history (plan 4a task 7, fix round 1) — a sibling
    # investment-money-fact store to REALIZED_EVENTS_JSON above (income_event_store.py's own
    # docstring), checkpoint-INCLUDED real user data ("NOT a cache"). ABSENT from this manifest
    # in this task's first pass despite `income_events` joining `portable_bridge.BRIDGED` (so
    # `wipe_all` already reached it): the same silent-backup-omission + no-recovery-path gap
    # `docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md` closed once for
    # price_history/instruments, and Plan 3 Task 6 closed again for TXN_AUDIT_JSON/
    # MERGE_JOURNAL_JSON — any bridged store `/api/reset` unconditionally wipes MUST be
    # portable (so the pre-wipe backup loop, which iterates this exact list, actually captures
    # it) unless its loss is independently judged inconsequential (see match_reviews/
    # match_decisions below, which stay deliberately excluded, for the reasoning that makes
    # THEIR omission safe and this one wasn't).
    INCOME_EVENTS_JSON,
    PRICE_HISTORY_JSON,
    # Option contracts — portfolio truth, like lots/grants — checkpoint-INCLUDED and portable.
    OPTION_CONTRACTS_JSON,
    HOLDING_PLANS_JSON,
    ACTIONS_JSON,
    COMMITMENTS_JSON,
    # Owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port) — a sibling of
    # COMMITMENTS_JSON above: user-owned book facts, checkpoint-INCLUDED and portable.
    OWNED_ASSETS_JSON,
    OWNED_ASSET_CATEGORIES_JSON,
    WATCHLIST_JSON,
    ALERTS_JSON,
    # Story-arc history (RFC intel-signals-data-model, revision 202608301500) — renamed from
    # INTEL_SIGNALS_JSON when intel_signals split into three tables. Bespoke bridge
    # (portable_bridge.py's own "intel_stories.json" entry): each story's
    # signal_timeline_entries children re-nest into a `timeline[]` list on export, split back
    # out on import — mirrors money_io.py's transactions+splits shape. daily_digests is
    # deliberately ABSENT from this manifest: wipe-only, not portable (a delivered-digest
    # record is regenerable notification history, not a book fact — tables.py's own comment
    # on that table has the full reasoning); checkpoint inclusion is unchanged either way.
    INTEL_STORIES_JSON,
    # News source registry (INT9 Phase A) — builtin + user rows, like watchlist/alerts above.
    NEWS_SOURCES_JSON,
    MEMORY_JSON,
    RULES_JSON,
    # Recurring-stream user state (overlay confirm/dismiss corrections) — the
    # forward cash-flow baseline reads it, so a restored instance must project the same way.
    RECURRING_STREAMS_JSON,
    PAYEES_JSON,
    TARGETS_JSON,
    SCHEDULED_TASKS_JSON,
    AUTOMATIONS_JSON,
    AUTOMATION_RUNS_JSON,
    STATEMENTS_JSON,
    IMPORT_BATCHES_JSON,
    # Transaction-merge undo journal (plan 3 task 6) — the last of the money six; same
    # never-portable-before history as TXN_AUDIT_JSON above (joining here is a deliberate
    # behavior change, not a mirror of a considered past decision — there was none).
    MERGE_JOURNAL_JSON,
    REJECTED_IDENTITIES_JSON,
    # Chat-artifact metadata. Like statements, only the metadata JSON travels —
    # the document bytes under artifacts/ stay instance-local (statement-files
    # precedent); a transcript referencing a missing artifact renders the chat
    # card's "could not load" state, never an error page.
    ARTIFACTS_JSON,
    # Outlook saved scenarios + FIRE baseline override — user-authored planning config.
    PLAN_STATE_JSON,
    # Overview widget-dashboard layout (named views, each a flat widget-instance array) —
    # user-authored layout config, like PLAN_STATE_JSON above.
    DASHBOARD_LAYOUT_JSON,
    # Envelope decoupling (envelope-not-category.md decision 8): the budget-axis entity +
    # its two adjacent stores were checkpoint-covered but NOT portable — a latent export gap
    # this RFC fixes. All three are user-authored planning config, like PLAN_STATE_JSON above.
    ENVELOPES_JSON,
    BUDGET_ASSIGNMENTS_JSON,
    CATEGORY_GROUPS_JSON,
    # Pluggable budget engines (.claude/rfcs/2026-08-22-pluggable-budget-engines.md) — the
    # default per-engine config store for any lens that owns no bespoke store of its own
    # (Phase 1's only consumer is 50/30/20). User-authored planning config, like the three
    # above; envelope's/caps' OWN state stays in the existing stores untouched.
    BUDGET_ENGINE_STATE_JSON,
    # Equity comp — user money-facts: RSU/LTIP grants + append-only vest history.
    # checkpoint-INCLUDED and portable like lots/options above, but ABSENT from this
    # manifest before P0-7 — an export→fresh-instance move silently dropped a user's
    # entire equity/vest history despite the "single .zip of every store" promise.
    GRANTS_JSON,
    VEST_EVENTS_JSON,
    # User-authored AI config: custom Agents, and the skills runtime state (per-skill
    # enabled/author — the skill CONTENT itself travels via SKILLS_DIR below). Both were
    # checkpoint-included but not portable before P0-7 — the same silent export gap.
    AGENTS_JSON,
    SKILLS_STATE_JSON,
    # Email provenance snapshots (EM1) — a copy of the email content behind an
    # email-booked transaction's external_ref. User-owned evidence, like statements/artifacts
    # above: export-more-of-the-user's-own-data direction (P0-7 precedent).
    EMAIL_SNAPSHOTS_JSON,
    # Connector (bank/broker) provenance snapshots (RFC connectors-as-data-sources §C) — the
    # raw row a provider fetch returned, behind a connector-booked transaction's external_ref.
    # A deliberate SIBLING of EMAIL_SNAPSHOTS_JSON above, not a shared file — same
    # export-more-of-the-user's-own-data direction.
    CONNECTOR_SNAPSHOTS_JSON,
    # Chat attachments (metadata): the user's own documents — export-more-of-the-
    # user's-own-data direction. Unlike artifacts, the payload DIR travels too (below):
    # owner decision 2026-07-17, a migrated install keeps its attachment history.
    # Renamed from UPLOADS_JSON (cross-channel-attachments RFC Phase 2).
    ATTACHMENTS_JSON,
    # Transaction receipts (cross-channel-attachments RFC Phase 4) — the user's own
    # evidence, like attachments above. The blob dir travels too (below), same reasoning:
    # a migrated install keeps its receipt history, not just the metadata pointing at bytes
    # that no longer exist.
    RECEIPTS_JSON,
    TRANSACTION_RECEIPTS_JSON,
]

# ── Portable directories (whole-tree copy) ────────────────────────────────────
# Chat sessions (SESSIONS_DIR) is deliberately ABSENT here as of Wave 5E (JSON->DB):
# sessions are `sessions` table rows now (db/session_store.py), not a directory to
# whole-tree copy — still portable, via services.portability's own bespoke
# session_bundle_entries()/_replace_sessions() pair (which reconstructs the exact
# pre-migration per-file bundle shape from the DB), not this list.
PORTABLE_DIRS: list[Path] = [
    # Skill CONTENT (SKILL.md + scripts/references/assets per skill folder). The per-skill
    # enabled/author state travels separately as SKILLS_STATE_JSON above; together they make
    # a user's custom skills survive an export→restore (P0-7).
    SKILLS_DIR,
    # Attachment payloads (parsed attachment content, base64 JSON per ref) — pairs with
    # ATTACHMENTS_JSON above so attachment history survives an export→restore.
    ATTACHMENTS_DIR,
    # Receipt blob bytes — pairs with RECEIPTS_JSON above so receipt history (the actual
    # photographed/scanned artifact, not just its metadata row) survives an export→restore.
    RECEIPTS_DIR,
]


# Statement source files and generated documents travel too, but are NOT in PORTABLE_DIRS: nothing
# else treats them as a plain whole-tree copy (a statement's parsed-rows sidecar is inlined into
# statements.json by the money bridge; its resolver confines every read to the directory).
def portable_trees() -> dict[str, Path]:
    """Bundle tree name -> live directory, for every directory of files the bundle carries
    (``blobs/<name>/<relative path>``). Resolved per call so a test that redirects a directory
    sees the redirect."""
    trees = {directory.name: directory for directory in PORTABLE_DIRS}
    trees["statements"] = statements_files_dir()
    trees["artifacts"] = ARTIFACTS_DIR
    return trees


# Default empty contents per data file, used by ``/api/reset`` and by a
# full-replace import when a portable file is absent from the bundle. A keyed
# store (dict root) resets to ``{}``; a list store to ``[]``.
_DICT_ROOT_FILENAMES: set[str] = {
    PROFILE_JSON.name,
    PRICE_HISTORY_JSON.name,
    WATCHLIST_JSON.name,
    PLAN_STATE_JSON.name,
    DASHBOARD_LAYOUT_JSON.name,
    # {YYYY-MM: {envelope_id: amount}} — a bare dict, not a list (db/budget_assignments_store.py).
    BUDGET_ASSIGNMENTS_JSON.name,
    # {engine_id: payload} — a bare dict, not a list (db/budget_engine_state_store.py).
    BUDGET_ENGINE_STATE_JSON.name,
    # {skill_id: {enabled, author, created_at, updated_at}} — keyed dict (db/skill_store.py).
    SKILLS_STATE_JSON.name,
}


def empty_contents_for(filename: str) -> str:
    """Return the empty-state JSON text for a portable file (``{}`` vs ``[]``)."""
    return "{}" if filename in _DICT_ROOT_FILENAMES else "[]"
