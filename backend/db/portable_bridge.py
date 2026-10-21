"""Registry bridging DB-backed reference-data stores into the JSON-bundle portability
format (plan 2, task 7).

The migration (tasks 1/3/5) moved evidence/price_history/instruments off their JSON
files and onto real tables, renaming each source file to ``<name>.json.migrated-<ts>``.
``core.portable.PORTABLE_FILES`` still names them by their ORIGINAL filename — that
manifest is the shared contract with ``/api/data/export``, ``/api/data/import`` and
``/api/reset`` — and rewriting it to know about tables instead of files is the larger,
later, unified rewrite the storage-engine spec describes (D6: "The exporter... becomes
portable.py's new implementation — today 91 hardcoded file constants, tomorrow a
query"). Until that lands, this module is the narrow bridge: ``BRIDGED`` maps each
migrated store's ORIGINAL filename to the (already cutover-verified) io-module
functions that read/write its table, so ``services.portability`` and ``/api/reset``
keep working, unchanged in shape, regardless of which side of the migration a given
store is on. See docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md.

``BRIDGED`` has all three migrated REFERENCE-data stores, but only ``price_history.json`` and
``instruments.json`` are LIVE — present in ``core.portable.PORTABLE_FILES``, so
``build_export_bundle``/``apply_import`` actually reach them. ``evidence.json`` is
wired but DORMANT (task 7 review, 2026-08-07): a real evidence export measures
~108MB, over ``services.portability``'s 100MB per-entry import cap, so export would
succeed while every import of that same bundle 422s — a broken round trip, not a
theoretical one. See the entry's own comment below and the ADR's Decision section.
``/api/reset`` still wipes evidence's tables via ``wipe_all`` regardless of this
dormancy — reset-to-blank-slate and export-portability are different questions; a
derived cache belongs in "wipe everything" even though it doesn't belong in a
travel-with-you bundle.

PLAN 3 TASK 6 — the money core's six files/eight tables (``accounts``, ``transactions`` +
its two child tables, ``txn_audit``, ``import_batches``, ``statements_meta``,
``merge_journal``) join ``BRIDGED`` too, sourced from ``backend.db.money_io``. Two
differences from the three reference-data entries above, both load-bearing:

1. **Undoable.** The Plan-2 no-hook ruling on ``_apply_bridged_import`` ("a bundle restore
   is an admin operation ... outside the checkpoint/undo system entirely") is explicitly
   scoped to REFERENCE data — money is the OPPOSITE ruling: a self-hoster importing their
   real ledger over a wrong/test one must be able to undo it. ``money_io``'s six
   ``import_<store>`` functions fire ``core.io.fire_db_write_hook`` for every row they
   insert; ``apply_money_bundle`` (below) fires it again for every row its wipe phase
   is about to remove — together these give ``/api/data/import``'s checkpoint turn (see
   ``api/portability.py``'s ``begin_turn``/``end_turn`` wrap) a complete before-image of
   every touched row, table by table. ADR'd in T8.
2. **Cross-file FK coupling.** ``transactions.from_account_id``/``to_account_id`` are
   ``ON DELETE RESTRICT`` onto ``accounts.id`` — unlike evidence/price_history/instruments
   (three fully independent stores), the money six are ONE referential unit for import
   purposes and cannot use the generic per-file ``_apply_bridged_import`` loop
   (``services/portability.py``) the three reference-data entries share. ``apply_money_
   bundle`` is the dedicated orchestration; see its own docstring for the wipe/import
   ordering this coupling forces. ``MONEY_FILES``/``MONEY_TABLES`` name the group so
   ``services.portability`` can route these six around the generic loop.

PLAN 4A TASK 7 — the twelve planning stores (``envelope_groups`` (bundle file ``category_groups.json``), ``categories``,
``commitments``, ``envelopes``, ``income_events``, ``recurring_streams``, ``targets``,
``corrections``, ``match_decisions``, ``match_reviews``, ``budget_assignments``,
``plan_state``), sourced from ``backend.db.planning_io``. A THIRD combination, distinct from
both precedents above:

1. **Undoable, like the money six** (section 1 above) — the reference-data no-hook ruling
   never applied to planning. ``planning_io``'s ``import_*`` functions already fire
   ``fire_db_write_hook`` unconditionally (built in task 6); every planning ``_wipe_*``
   function below reuses ``_capture_then_delete`` — the SAME helper the money six's wipes
   use. The one documented exception is ``corrections``: its wipe fires the hook too
   (uniform with its eleven siblings — no special-casing in the wipe machinery itself), but
   ``"corrections"`` sits in ``ai.checkpoint._EXCLUDED_TABLES`` (a behavioral signal log,
   not user-authored data — a ``/rewind`` must never resurrect stale correction history),
   so the capture is a STRUCTURAL no-op there. ``wipe_all`` still wipes ``corrections``
   regardless (below) — reset and undo-ability are independent questions, matching
   ``txn_audit``'s own precedent.
2. **No cross-file FK, like the three reference-data stores** (section 2's OPPOSITE) —
   planning tables FK only ``books`` (never each other; per-store comments, ``tables.py``),
   so each of the twelve rides the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop, one file per call, exactly like
   evidence/price_history/instruments — NOT ``apply_money_bundle``'s dedicated group
   orchestration. The two dict-shaped stores (``budget_assignments``, ``plan_state``) still
   fit this generic loop: their ``import_fn``/``export_fn`` just pass a ``dict`` through
   instead of a ``list`` — the loop itself is shape-agnostic (plain ``json.loads``/
   ``json.dumps`` either way).

NINE of the twelve are LIVE (present in ``core.portable.PORTABLE_FILES``): ``envelope_groups``,
``categories``, ``commitments``, ``envelopes``, ``recurring_streams``, ``targets``,
``budget_assignments``, ``plan_state`` pre-date this task (tasks 3/4's own reports deferred
wiring them here); ``income_events`` joined in this task's OWN fix round 1 — its FIRST pass
left it bridged (so ``wipe_all`` already unconditionally wiped it on every ``/api/reset``) but
NOT in ``PORTABLE_FILES``, so the pre-wipe backup loop (which iterates that list) silently
never captured it: a self-hoster hitting reset lost real dividend/income history with no
backup and no undo path (reset opens no checkpoint turn) — the review caught this as a
Blocker, the exact bug class the ADR referenced above fixed once for price_history/
instruments and Plan 3 Task 6 fixed again for ``TXN_AUDIT_JSON``/``MERGE_JOURNAL_JSON``. The
remaining THREE stay DORMANT, mirroring ``evidence.json``'s own precedent exactly — bridged
(so a future flip is a manifest-only change, not a rebuild) but not reachable through the
export/import bundle pipeline, each with an affirmatively-checked reason their omission is
SAFE (core/portable.py's own docstring has the full reasoning): ``corrections.json``/
``match_reviews.json`` per ``core.portable``'s own pre-existing, explicit exclusion ("the
pending-review queue, the corrections log" — runtime/behavioral, not user-authored data, a
decision that predates this task entirely); ``match_decisions.json`` is the review queue's
verdict-cache sibling ("the match-verdict cache"), excluded for the same reason — every row
in either of the latter two is keyed on a transaction-id pair ``/api/reset`` ALSO wipes in
the same pass, so neither is independently meaningful data once a reset has run. ``wipe_all``
below reaches all twelve regardless of live/dormant status, same as it already does for
``evidence.json`` today — reset-to-blank-slate and export-portability remain independent
questions; that independence is exactly why a store can be safely wiped-without-backup
(match_reviews/match_decisions/corrections) while a DIFFERENT wiped-without-backup store
(income_events, before this fix) is a genuine bug — "dormant" alone proves nothing about
safety, only the accompanying reasoning does.

PLAN 4B TASK 6 — the eight investment stores (``positions``, ``lots``, ``grants``,
``vest_events``, ``option_contracts``, ``realized_events``, ``watchlists``, ``asset_brief``),
sourced from ``backend.db.investments_io`` (task 5). Closes the deferred ``/api/reset`` gap
tasks 2/3 flagged: reset used to write dead ``positions.json``/``lots.json``/... files
(nothing reads them post-cutover) and never cleared the DB rows for any of the 8 tables.
SEVEN join ``BRIDGED``; the eighth (``asset_brief``) deliberately does NOT — a third
combination, matching planning's shape but with one new wrinkle:

1. **Undoable, like money/planning** — ``investments_io``'s ``import_payload_list``/
   ``import_watchlist`` fire ``fire_db_write_hook`` unconditionally (task 5); the seven
   investment ``_wipe_*`` functions below reuse ``_capture_then_delete``, the SAME helper
   money/planning's wipes use.
2. **No cross-file FK, like planning** — investment tables FK only ``books`` (a lot's
   ``position_id``, a realized event's ``consumptions[].lot_id``, a vest event's
   ``grant_id`` are opaque, Python-filtered ids, never a SQL JOIN — ``tables.py``'s own
   investments-block comment), so all seven ride the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop — no ``apply_money_bundle``-style
   group orchestration.
3. **One store is wipe-only, not bridged at all — the new wrinkle.** ``asset_brief`` is a
   derived per-instrument LLM/search-synthesis CACHE (``asset_brief_store.py``'s own
   docstring: "rebuilt by ``POST /api/intel/assets/{id}/refresh``"), checkpoint-EXCLUDED
   (``ai.checkpoint._EXCLUDED_TABLES``) and absent from ``core.portable.PORTABLE_FILES`` —
   mirroring ``evidence.json``/``instruments.json``'s own cache precedent. Unlike THOSE
   three (which stay wired into ``BRIDGED`` even while dormant, so a future flip to
   portable is a manifest-only change), ``asset_brief`` has no export/import role to keep
   wired at all — a Tier-2 cache keyed by ``instrument_id`` has nothing coherent to
   "restore" independent of the instruments it describes — so it is deliberately OMITTED
   from ``BRIDGED`` entirely and reached by ``wipe_all`` (below) via one direct
   ``wipe(conn, "asset_briefs.json")`` call instead of the ``for filename in BRIDGED``
   loop every other wiped store uses. ``test_asset_brief_is_wiped_but_not_bridged``
   (``tests/db/test_investments_portable.py``) pins this shape.

ALL SEVEN bridged filenames were ALREADY LIVE (present in ``core.portable.PORTABLE_FILES``)
BEFORE this task touched anything — tasks 1-5 never needed to edit ``portable.py``,
verified in this task's own report. Every one is REAL, IRREPLACEABLE user portfolio data:
positions/lots/grants/vest_events/option_contracts/realized_events are the user's holdings
and disposal/vesting history; ``watchlist`` is durable monitoring config, matching
``WATCHLIST_JSON``'s own pre-existing portable status. This is the Wave-A-T7 lesson
(income_events.json's fix-round-1 Blocker) applied PROACTIVELY here, not caught by a review
round: a store ``wipe_all`` unconditionally reaches MUST be in ``PORTABLE_FILES`` (so the
pre-wipe backup loop, ``api/setup.py::reset_all_data``, actually captures it) unless its
loss is independently, affirmatively judged safe — ``asset_brief`` (point 3 above) is that
one affirmatively-safe exception, exactly like ``evidence.json``.

PLAN 4C TASK 3 — logs/telemetry: of the nine Tier-2 log/telemetry stores (plan 4c tasks
1-2: ``automation_runs``, ``automation_runtime``, ``batch_ledger``,
``harness_suggestions``, ``mcp_write_log``, ``model_telemetry``, ``pending_analysis``,
``run_history``, ``tool_call_log``), exactly ONE — ``automation_runs`` — joins ``BRIDGED``.
This is a BEHAVIOR-PRESERVING PORT, not a new policy (task-3-brief.md's own binding table):
``automation_runs`` was ALREADY a ``core.portable.PORTABLE_FILES`` member and ALREADY
checkpoint-INCLUDED before this task touched anything (``AUTOMATION_RUNS_JSON``'s own file
comment: "durable run history — portable") — only its ``BRIDGED`` wiring is new, the same
dead-file-path-to-DB fix every prior wave's first LIVE table needed. Its 8 siblings are
runtime scheduler state / re-fetchable / observability rings, checkpoint-EXCLUDED
(``ai.checkpoint._EXCLUDED_TABLES``, plan 4c task 1) — none of the 8 were EVER in
``PORTABLE_FILES`` or reachable by ``wipe_all`` before this task, and this task does NOT add
them: unlike ``asset_brief`` (plan 4b, wipe-only but still reset-reached) the 8 excluded
logs stores get NO ``_WIPE_FNS`` entry at all — there is no reset role for them to preserve.
Extending "reset means reset" to these 8 operational logs is a deferred, ADR-worthy
decision (task-3-brief.md flags it explicitly), not a silent side effect of this
storage-engine port. ``automation_runs`` is UNDOABLE (``logs_io.import_payload_list`` fires
``fire_db_write_hook`` unconditionally, like every other bridged store this wave) and rides
the GENERIC per-file ``_apply_bridged_import`` loop (no cross-file FK — ``tables.py``'s own
Tier-2 logs comment: no book_id, no FK anywhere in this wave), exactly like planning's/
investments' own bridged entries.

PLAN 4D TASK 6 — the twelve config stores (plan 4d tasks 1/2/5: ``payees``, ``automations``,
``alerts``, ``dashboard_layouts``, ``memory``, ``news_sources``, ``statement_formats``,
``tool_grants``, ``signals``, ``intel_signals``, plus ``memory_settings``/``preferred_models``
sharing the ONE ``settings`` table), sourced from ``backend.db.config_io``. A shape
planning/investments never needed — not one wave-wide undoable/no-FK combination, but a
per-store SPLIT along the portable axis:

1. **SEVEN join BRIDGED**, undoable, no cross-file FK — ``payees``/``automations``/``alerts``
   (Tier-1, ``book_id=DEFAULT_BOOK_ID``, ``config_io``'s own optional ``book_id`` parameter),
   ``dashboard_layout`` (dict-shaped per-book singleton, mirrors ``plan_state``/``watchlist``),
   ``memory``/``news_sources``/``intel_signals`` (Tier-2/3, no ``book_id``). All seven ride the
   GENERIC per-file ``services.portability._apply_bridged_import`` loop, exactly like
   planning/investments — ``config_io``'s ``import_payload_list``/``import_dashboard`` fire
   ``fire_db_write_hook`` unconditionally (task 5), so a restore through ``ai.checkpoint``
   reverts a config bundle import same as any other bridged store's. ALL SEVEN filenames were
   ALREADY LIVE (in ``core.portable.PORTABLE_FILES``) before this task touched anything —
   user-authored config from the JsonStore era, no ``income_events``-shaped backup gap existed
   here.
2. **FIVE are wipe-only, NOT bridged at all** — ``memory_settings``, ``preferred_models``,
   ``statement_formats``, ``tool_grants``, ``signals``. Unlike ``asset_brief`` (the one prior
   wipe-only precedent, a derived CACHE with nothing coherent to export), these five are
   excluded for a DIFFERENT reason: "reset means reset" for instance-local config that must
   never travel to another machine — a tool grant is instance-local trust
   (``tool_grant_store.py``'s own "NOT portable" section states the canonical reasoning this
   task extends to its four siblings), a signal is a disposable notification ring, a statement
   format / memory toggle / preferred-model shortlist is this install's own tuning, never a
   portable fact. TWO of the five (``tool_grants``, ``signals``) are ALSO checkpoint-EXCLUDED
   (``ai.checkpoint._EXCLUDED_TABLES``, added in plan 4d task 1 — BEFORE this task); their wipe
   fn still fires ``fire_db_write_hook`` unconditionally (uniform with every sibling wipe fn,
   never a special case in the wipe machinery itself) — the exclusion alone makes the capture a
   structural no-op, mirroring ``corrections``' own precedent exactly. The other THREE
   (``memory_settings``, ``preferred_models``, ``statement_formats``) stay checkpoint-INCLUDED
   despite being non-portable — portable-status and checkpoint-status are independent
   questions, the same precedent ``match_reviews``/``match_decisions`` already established for
   planning (``core/portable.py``'s own docstring).

``memory_settings``/``preferred_models`` are the ONE new wrinkle no prior wave needed: two
config STORES sharing one physical ``settings`` table, keyed by ``namespace``
(``config_io.import_namespaced``/``export_namespaced``). Each still gets its OWN
filename-keyed wipe fn (``_wipe_settings_namespace(conn, "memory_settings" | "preferred_
models")``) that touches ONLY its own row — never a whole-table truncate — mirroring
``config_io``'s own per-namespace import scoping exactly (``test_memory_settings_and_
preferred_models_wipe_is_scoped_by_namespace`` pins this). Neither is ``BRIDGED`` (no
export/import role — point 2 above).

``signals`` is the one config store this task deliberately does NOT build through
``_config_bridge`` below even though it is a flat list shaped exactly like its six bridged
siblings: it is wipe-only (point 2), so ``config_io``'s ``reverse=True`` ORDER FIX for it
(that module's own docstring) never actually runs here — recorded so a future flip to
portable does not silently import it in file order (oldest-first) instead of the newest-first
its real ``SignalsStore.query_signals`` read path requires.

ADR ``docs/adr/2026-08-08-config-stores-and-tenancy.md``: the ``settings`` namespaced-singleton
pattern, the tenancy ruling (``book_id`` vs no ``book_id`` today, pending a future ``user_id``
column for ``memory``/``memory_settings``), the ``tool_grants``/``signals`` checkpoint
exclusions, the ``signals`` prepend-order cutover decision. Connector secrets (D8) and the ~17
connector stores are a DEFERRED separate security track — none are ``JsonStore`` subclasses,
none block this module's own scope.

INTEL STORIES SPLIT (RFC ``docs/rfc/intel-signals-data-model.md`` §3.4, migration
``202608301500``) — ``"intel_signals.json"``'s SEVENTH config-bridge entry (above) was REMOVED
outright, not migrated: its table split into ``intel_stories`` + ``signal_timeline_entries`` +
``daily_digests``, and ``backend.db.tables.intel_signals`` no longer exists to import through
``_config_bridge``. This is that bespoke bridge, a THIRD combination distinct from every prior
parent+child pair in this file:

1. **One bridge entry composes two tables**, like the money six's ``transactions.json`` composes
   ``transactions``+``transaction_splits``+``transaction_external_refs`` into ONE bundle file —
   but WITHOUT the money six's cross-FILE FK coupling to a sibling bridge entry
   (``transactions`` -> ``accounts.id``). ``signal_timeline_entries.signal_id`` FKs
   ``intel_stories.id`` ONLY, entirely inside this one entry, so ``"intel_stories.json"`` rides
   the GENERIC per-file ``services.portability._apply_bridged_import`` loop like every
   reference-data/planning/investment entry — it needs no ``apply_money_bundle``-style group
   orchestration. Sourced from ``backend.db.intel_signals_io`` (mirrors ``money_io.py``'s
   ``export_transactions``/``import_transactions`` shape — see that module's own docstring).
2. **Undoable** — ``intel_signals_io.import_intel_stories`` fires ``fire_db_write_hook``
   unconditionally for both tables (like every other bridge this wave); ``_wipe_intel_stories``
   below reuses ``_capture_then_delete`` for each, children (``signal_timeline_entries``) first —
   releasing nothing FK-wise (CASCADE would handle it either way) but matching every other
   parent+child wipe's own "children before parent" convention (``_wipe_transactions``,
   ``_wipe_evidence``) for the same reason: correct regardless of whether SQLite's
   ``PRAGMA foreign_keys`` is actually enforcing the CASCADE. checkpoint-INCLUDED, unchanged
   from ``intel_signals``' own status (``ai.checkpoint._EXCLUDED_TABLES`` — verified, neither
   table is a member) — a ``/rewind`` reverts a signal write exactly as it always could.
3. **``daily_digests`` is wipe-only, deliberately NOT bridged** — a THIRD reason for that
   classification pattern, distinct from ``asset_brief``'s "derived cache" and
   ``memory_settings``'s "instance-local config": a delivered-digest record is regenerable
   notification history, not a book fact (mirrors ``notification_log``'s own "disposable
   notification ring" reasoning) — it only ever rode inside ``intel_signals.json`` because it
   was never given its own file. Reached by ``wipe_all`` below via one direct
   ``wipe(conn, "daily_digests.json")`` call, like ``asset_briefs.json``/``pending_pushes.json``
   — the key is purely symbolic (``daily_digests`` never had its own JSON source file either),
   not a real path anything reopens. Still checkpoint-INCLUDED (unchanged from its old home
   inside ``intel_signals``) — portable-status and checkpoint-status are independent questions,
   the same precedent ``match_reviews``/``match_decisions``/the three wipe-only config stores
   already established.
4. **``claims`` (migration ``202608301800``) is wipe-only, same reasoning as point 3** — a
   claim row is a derived cross-subject push-dedup fact (``services.claims.group_key_for``/
   ``is_recent_duplicate_push``), not a book fact. Security-review fix (2026-08-30): this
   table shipped in the same change as points 1-3 above but with NO ``_WIPE_FNS`` entry at
   all, so ``/api/reset`` silently left model-derived claim statements behind — closed the
   same way point 3 was, one direct ``wipe(conn, "claims.json")`` call in ``wipe_all`` below.
   Still checkpoint-INCLUDED (``ai.checkpoint._EXCLUDED_TABLES`` does not list it).

PLAN 4E TASK 1 — the 4 misc stores (``artifacts``, ``uploads``, ``email_snapshots``,
``pending_pushes``), sourced from ``backend.db.misc_io``. A per-store split shaped like
planning's/investments'/logs' own (undoable, no cross-file FK — no book_id anywhere in this
wave), NOT configs' five-way wipe-only spread:

1. **THREE join BRIDGED**, undoable, no cross-file FK — ``artifacts``/``uploads`` (HYBRID,
   spec D5: only the metadata catalog is DB-backed; the document/parsed-payload bytes stay
   Tier-4 files under ``ARTIFACTS_DIR``/``UPLOADS_DIR``, untouched by this bridge) and
   ``email_snapshots`` (FULL MIGRATE — every field, including ``body_text``, lives inline).
   All three ride the GENERIC per-file ``services.portability._apply_bridged_import`` loop
   (``misc_io.import_payload_list`` fires ``fire_db_write_hook`` unconditionally, like every
   other bridged store's import fn) and were ALREADY LIVE (in ``core.portable.PORTABLE_
   FILES``) before this task touched anything — the load-bearing safety check this task's
   own brief calls out explicitly (the ``income_events.json`` bug class Wave A caught).
2. **ONE is wipe-only, never bridged** — ``pending_pushes``, mirroring ``asset_briefs.json``'s
   own precedent exactly: a runtime retry queue, not user data, correctly ABSENT from
   ``PORTABLE_FILES`` — ``/api/reset`` still reaches it via one direct
   ``wipe(conn, "pending_pushes.json")`` call, since the ``for filename in BRIDGED`` loop
   never sees a key that was never added to ``BRIDGED``. Also checkpoint-EXCLUDED
   (``ai.checkpoint._EXCLUDED_TABLES``, plan 4e task 1) — its wipe fn still fires the hook
   unconditionally (uniform with every sibling wipe fn, never a special case in the wipe
   machinery itself); the exclusion alone makes the capture a structural no-op, mirroring
   ``corrections``'/``tool_grants``'/``signals``' own precedent.

PLAN 4E TASK 2 — ``checkpoints`` (the 5th Wave-E table, schema-only from revision 0017,
absorbed by revision 0019): wipe-only, NEVER bridged — mirrors ``pending_pushes.json``'s
own precedent exactly: undo history is not portable (a checkpoint from one machine has no
meaning replayed against another machine's data). ``/api/reset`` wipes it (below) so a
reset never leaves stale manifest rows pointing at data the reset just erased — a "Restore"
button offering to revert to a state that no longer has anything to revert FROM. Also
checkpoint-EXCLUDED (``ai.checkpoint._EXCLUDED_TABLES``, plan 4e task 2 — belt-and-braces
alongside the store's own ``checkpointed=False``) — its wipe fn still fires the hook
unconditionally, same uniform-no-special-case posture as every sibling wipe fn; the
exclusion makes the capture a structural no-op. The Tier-4 gzip blob pool
(``CHECKPOINTS_BLOBS``) this manifest references is UNRELATED and untouched by reset,
exactly as it was before this task.

WAVE 5C (JSON->DB) — of the five runtime-queues/state stores this wave migrates
(``dedup_config``, ``harness_state``, ``skills_state``, ``subscriptions``, ``actions``;
``connector_sessions`` deferred — see ``docs/adr/2026-08-10-connector-sessions-deferred-
to-secrets-wave.md``), only TWO join this module at all:

1. **``skills_state`` joins BRIDGED**, undoable, no cross-file FK — a keyed-dict store
   (id = skill id), sourced from ``wave5c_io.import_keyed``/``export_keyed`` (the SAME
   generic dict-shaped pair ``model_telemetry``/``cost_log_daily`` use elsewhere, but
   THIS is the first keyed-dict store to actually join ``BRIDGED`` — every prior
   dict-shaped bridge entry, plan_state/profile/watchlists/dashboard_layout, is a
   per-book SINGLETON, one row total, not a dict of many rows). Was ALREADY LIVE (in
   ``core.portable.PORTABLE_FILES``) before this task touched anything — the load-bearing
   safety check every wave's own docstring calls out (the ``income_events.json`` bug
   class): its wipe fn (``_wipe_skills_state``, below) reuses the ordinary
   ``_capture_then_delete`` (a plain id-pk flat table, despite the dict-shaped JSON
   source — the id lives in the row's ``id`` COLUMN either way).
2. **``actions`` ALSO joins BRIDGED**, same shape as ``skills_state`` — ordinary flat
   list, was ALREADY LIVE in ``PORTABLE_FILES`` pre-migration. Its bridge uses
   ``wave5c_io.import_actions``/``export_actions`` (NOT the generic
   ``import_payload_list``/``export_payload_list`` pair) — ``actions.json``'s natural id
   is a plain INTEGER (see ``db/action_store.py``'s own docstring), so the import side
   must ``str()``-coerce the id COLUMN the same way ``ActionStore._insert_row`` does on
   the live write path, or a row absorbed by the cutover and a row written afterward
   would carry differently-typed id columns.

``dedup_config``/``harness_state`` do NOT join this module — both are namespace-keyed
singleton rows (``settings``/``runtime_state`` respectively), and NEITHER was portable
pre-migration (``DEDUP_CONFIG_JSON``/``HARNESS_STATE_JSON`` were never in
``core.portable.PORTABLE_FILES``) — preserved as non-portable, not silently promoted.
``subscriptions`` (VESTIGIAL — see ``db/subscription_store.py``) also does NOT join this
module, for the same reason ``agent_findings`` (Wave 5B) doesn't: never portable
pre-migration, and a retired feature's absorbed-but-dead data earns no NEW reset/export
role it never had.

PLUGGABLE BUDGET ENGINES (.claude/rfcs/2026-08-22-pluggable-budget-engines.md, Decision E) —
``budget_engine_state`` (revision 0049): the default per-(book, engine) config store for a
lens that owns no bespoke store of its own (Phase 1's only consumer: 50/30/20). A NEW table,
not part of any prior wave's absorb set, sourced from its own small io module
(``budget_engine_state_io``) rather than folded into ``planning_io`` — mirrors ``profile.json``'s
own precedent (a table added independently of the original planning wave). Undoable
(``import_budget_engine_state`` fires ``fire_db_write_hook`` unconditionally) and no
cross-file FK (FKs only ``books``, like every other planning-adjacent store), so it rides the
GENERIC per-file ``services.portability._apply_bridged_import`` loop like ``budget_
assignments``/``plan_state`` — bespoke ``Bridge`` entry (dict-shaped, like those two), built
directly below rather than through ``_planning_bridge`` (which assumes the generic
book_id/seq/id/payload shape this table doesn't have).

Layering: this lives in ``backend/db/``, so ``services.portability`` importing it is a
normal services -> db edge (downward, allowed). ``core.portable`` (the manifest) does
NOT import this module — core is a leaf and stays one; the bridging decision lives
entirely in services/db.

Wiping is deliberately NOT part of ``Bridge`` (which stays exactly the two callables a
bundle entry needs) — ``wipe``/``wipe_all`` are the separate, explicit "make this
store empty" operation both a per-file bundle restore (wipe one store, then maybe
reinsert) and ``/api/reset`` (wipe every bridged store, always) need.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, NamedTuple

from sqlalchemy import Connection, Table, delete, select

from backend.core.errors import ValidationError
from backend.core.io import fire_db_write_hook
from backend.core.paths import statements_files_dir
from backend.db import (
    budget_engine_state_io,
    config_io,
    evidence_io,
    institution_io,
    instruments_io,
    intel_signals_io,
    investments_io,
    logs_io,
    misc_io,
    money_io,
    planning_io,
    price_history_io,
    profile_io,
    wave5c_io,
)
from backend.db.attachment_store import is_attachment_id
from backend.db.owned_assets_legacy import normalize_imported_asset
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    accounts,
    actions,
    alerts,
    artifacts,
    asset_brief,
    attachments,
    automation_runs,
    automations,
    budget_assignments,
    budget_engine_state,
    categories,
    checkpoints,
    claims,
    commitments,
    connector_snapshots,
    corrections,
    daily_digests,
    dashboard_layouts,
    email_snapshots,
    envelope_groups,
    envelopes,
    evidence,
    evidence_subjects,
    grants,
    import_batches,
    import_runs,
    income_events,
    institutions,
    instruments,
    intel_stories,
    lots,
    match_decisions,
    match_reviews,
    memory,
    merge_journal,
    news_sources,
    notification_log,
    option_contracts,
    owned_asset_categories,
    owned_assets,
    payees,
    pending_pushes,
    plan_state,
    positions,
    price_movement_notes,
    price_points,
    profile,
    realized_events,
    receipts,
    recurring_streams,
    rejected_identities,
    settings,
    signal_timeline_entries,
    skills_state,
    statement_formats,
    statements_meta,
    targets,
    tool_permissions,
    transaction_external_refs,
    transaction_receipts,
    transaction_splits,
    transactions,
    txn_audit,
    vest_events,
    watchlists,
)


class Bridge(NamedTuple):
    """One migrated store's portable-bundle seam: read/write its table, keyed by the
    JSON filename callers still know it by."""

    export_fn: Callable[[Connection], Any]
    import_fn: Callable[[Connection, Any], int]


_RETIRED_GROUP_KEYS = frozenset({"categories", "envelope_ids"})


def _planning_bridge(table: Table, *, id_field: str = "id") -> Bridge:
    """Nine of the twelve planning stores share ``planning_io``'s generic (book_id, seq,
    id, payload) import/export pair, parameterized only by ``table`` — mirrors revision
    0010's own ``_generic(table)`` closure
    (``db/alembic/versions/202608081826_planning_cutover.py``). ``match_decisions`` (bespoke
    import, generic export) and the two dict-shaped stores (``budget_assignments``,
    ``plan_state``) build their ``Bridge`` entries directly instead, below.

    ``id_field`` (mirrors ``_misc_bridge``'s own parameter): the payload key copied into
    the DB ``id`` column on import — default ``"id"`` for every planning store except
    ``owned_asset_categories``, whose store keys rows by ``"key"`` (there is no ``"id"``
    field in that payload shape at all) — see its own ``BRIDGED`` entry below."""
    return Bridge(
        export_fn=lambda conn: planning_io.export_payload_list(conn, table),
        import_fn=lambda conn, raw: planning_io.import_payload_list(
            conn, table, raw, id_field=id_field
        ),
    )


def _envelope_groups_bridge() -> Bridge:
    """The bundle keeps its original ``category_groups.json`` filename (the export contract —
    renaming it would orphan every existing backup); the rows are envelope groups. An older
    bundle's rows still carry the retired ``categories`` membership list (and the even older
    ``envelope_ids``) — dropped on import, never stored."""
    return Bridge(
        export_fn=lambda conn: planning_io.export_payload_list(conn, envelope_groups),
        import_fn=lambda conn, raw: planning_io.import_payload_list(
            conn,
            envelope_groups,
            [{k: v for k, v in row.items() if k not in _RETIRED_GROUP_KEYS} for row in raw],
        ),
    )


def _owned_assets_bridge() -> Bridge:
    """An older backup's asset rows are upgraded to today's shape on import
    (``owned_assets_legacy.normalize_imported_asset``): retired review fields dropped, legacy
    insurance/maturity facts typed, and the stored purchase copy removed exactly as revision
    ``202609261500`` does."""
    return Bridge(
        export_fn=lambda conn: planning_io.export_payload_list(conn, owned_assets),
        import_fn=lambda conn, raw: planning_io.import_payload_list(
            conn,
            owned_assets,
            [normalize_imported_asset(row) for row in raw],
        ),
    )


def _owned_asset_categories_bridge() -> Bridge:
    """Strip retired category cadence from older portable bundles."""
    return Bridge(
        export_fn=lambda conn: planning_io.export_payload_list(conn, owned_asset_categories),
        import_fn=lambda conn, raw: planning_io.import_payload_list(
            conn,
            owned_asset_categories,
            [{k: v for k, v in row.items() if k != "cadence_months"} for row in raw],
            id_field="key",
        ),
    )


def _automations_bridge() -> Bridge:
    """Legacy asset-review schedules are retired data and may not return on restore."""

    def import_without_asset_reviews(conn: Connection, raw: list[dict]) -> int:
        filtered = []
        for row in raw:
            provenance = row.get("provenance") if isinstance(row, dict) else None
            system_key = provenance.get("system_key") if isinstance(provenance, dict) else None
            if not (isinstance(system_key, str) and system_key.startswith("asset_remark:")):
                filtered.append(row)
        return config_io.import_payload_list(conn, automations, filtered, book_id=DEFAULT_BOOK_ID)

    return Bridge(
        export_fn=lambda conn: config_io.export_payload_list(conn, automations),
        import_fn=import_without_asset_reviews,
    )


def _investment_bridge(table: Table) -> Bridge:
    """The six Tier-1 investment flat-list stores share ``investments_io``'s generic
    (book_id, seq, id, payload) import/export pair, parameterized only by ``table`` —
    mirrors ``_planning_bridge`` exactly. ``store=table.name`` is passed explicitly so
    ``investments_io._guard_money`` (the FINITE-MONEY GUARD, task 5) always knows which
    per-store money-field list to check, matching every direct call site in
    ``tests/db/test_investments_cutover.py``; ``id_field`` stays at its default ("id"),
    which is every one of these six's real shape — only ``asset_brief`` uses
    ``id_field="instrument_id"``, and ``asset_brief`` is NOT bridged (module docstring,
    PLAN 4B TASK 6). ``watchlist`` builds its ``Bridge`` directly instead, below (bespoke
    dict-shaped import, like ``plan_state``)."""
    return Bridge(
        export_fn=lambda conn: investments_io.export_payload_list(conn, table),
        import_fn=lambda conn, raw: investments_io.import_payload_list(
            conn, table, raw, store=table.name
        ),
    )


def _logs_bridge(table: Table) -> Bridge:
    """automation_runs is the ONE logs/telemetry store that is portable+reset-scoped
    (plan 4c task 3) — mirrors ``_planning_bridge``'s shape exactly, parameterized by
    table (only one caller today, kept as a named helper for the same reason
    ``_planning_bridge`` is: a future logs store reclassified as portable is then a
    one-line addition, not a rebuild)."""
    return Bridge(
        export_fn=lambda conn: logs_io.export_payload_list(conn, table),
        import_fn=lambda conn, raw: logs_io.import_payload_list(conn, table, raw),
    )


def _config_bridge(table: Table, *, book_id: str | None = None) -> Bridge:
    """The six flat-list config bridges (``payees``/``automations``/``alerts`` — Tier-1,
    ``book_id=DEFAULT_BOOK_ID``; ``memory``/``news_sources``/``intel_signals`` — Tier-2/3, no
    ``book_id``) share ``config_io``'s generic (book_id, seq, id, payload) import/export pair,
    parameterized by ``table`` + ``book_id`` — mirrors ``_planning_bridge``/``_investment_
    bridge`` exactly. ``dashboard_layout`` (the seventh, dict-shaped) builds its ``Bridge``
    directly instead, below (bespoke, like ``plan_state``/``watchlist``). ``signals`` is NOT
    built through this helper — it is wipe-only (module docstring, PLAN 4D TASK 6 section)."""
    return Bridge(
        export_fn=lambda conn: config_io.export_payload_list(conn, table),
        import_fn=lambda conn, raw: config_io.import_payload_list(
            conn, table, raw, book_id=book_id
        ),
    )


def _misc_bridge(table: Table, *, id_field: str = "id") -> Bridge:
    """The three bridged misc stores (``artifacts``/``uploads`` — HYBRID, metadata only;
    ``email_snapshots`` — FULL MIGRATE, ``id_field="message_id"``) share ``misc_io``'s
    generic (seq, id, payload) import/export pair, parameterized by ``table`` +
    ``id_field`` — mirrors ``_logs_bridge`` exactly (no ``book_id`` anywhere in this wave).
    ``pending_pushes`` (the fourth misc store) is NOT built through this helper — it is
    wipe-only (module docstring, PLAN 4E TASK 1 section)."""
    return Bridge(
        export_fn=lambda conn: misc_io.export_payload_list(conn, table),
        import_fn=lambda conn, raw: misc_io.import_payload_list(
            conn, table, raw, id_field=id_field
        ),
    )


def attachment_id_errors(filename: str, parsed: object) -> list[str]:
    """Attachment ids a restore must refuse, before it writes anything
    (``services.portability.inspect_bundle``) — the bridges refuse them again at import. An
    attachment's files are named after its id, so a crafted ``../../*`` or ``*`` would become a
    path or a pattern the day the row, or an asset's photo or document, is deleted (security
    review 2026-09-26, S1). ``attachments.json`` rows' ids; ``assets.json`` photo/doc refs."""
    if not isinstance(parsed, list):
        return []
    if filename == "attachments.json":
        refs = [
            (f"{filename}[{i}]", row.get("id") if isinstance(row, dict) else row)
            for i, row in enumerate(parsed)
        ]
    elif filename == "assets.json":
        refs = []
        for i, row in enumerate(parsed):
            row = row if isinstance(row, dict) else {}
            if row.get("photo_ref") is not None:
                refs.append((f"{filename}[{i}].photo_ref", row["photo_ref"]))
            for doc in row.get("docs") or []:
                ref = doc.get("ref_id") if isinstance(doc, dict) else doc
                refs.append((f"{filename}[{i}].docs", ref))
    else:
        return []
    return [
        f"{where}: {ref!r} is not an attachment id"
        for where, ref in refs
        if not is_attachment_id(ref)
    ]


def _attachments_bridge() -> Bridge:
    def _import(conn: Connection, raw: list[dict]) -> int:
        errors = attachment_id_errors("attachments.json", raw)
        if errors:
            raise ValidationError("; ".join(errors[:5]))
        return misc_io.import_payload_list(conn, attachments, raw)

    return Bridge(
        export_fn=lambda conn: misc_io.export_payload_list(conn, attachments), import_fn=_import
    )


BRIDGED: dict[str, Bridge] = {
    # wired but DORMANT — evidence is deliberately non-portable (rebuildable ~108MB
    # cache; would breach the 100MB import entry cap). Flipping it on requires raising
    # the caps + a real-scale test. Ruled 2026-08-07 (task 7 review). Not in
    # core.portable.PORTABLE_FILES, so build_export_bundle never reaches this entry —
    # it exists so a future deliberate flip is one manifest line, not a rebuild.
    "evidence.json": Bridge(
        export_fn=evidence_io.export_evidence,
        import_fn=evidence_io.import_evidence_json,
    ),
    "price_history.json": Bridge(
        export_fn=price_history_io.export_price_history,
        import_fn=price_history_io.import_price_history,
    ),
    "instruments.json": Bridge(
        export_fn=instruments_io.export_instruments,
        import_fn=instruments_io.import_instruments,
    ),
    # Money core (plan 3 task 6) — all six LIVE (in core.portable.PORTABLE_FILES), all six
    # UNDOABLE (see this module's docstring, section 1). Import for this group never goes
    # through the generic per-file _apply_bridged_import loop these six entries' export side
    # still shares with evidence/price_history/instruments — see apply_money_bundle below.
    #
    # SHARP EDGE (fix round 1, task 6 review): this dict — like the reference-data entries
    # above it — keys purely on ``Path.name``, globally, with no scoping to "only when the
    # full six-file group is present in core.portable.PORTABLE_FILES for this call". A Path
    # merely NAMED one of these six filenames, monkeypatched into ANY ``PORTABLE_FILES`` list
    # (e.g. an isolated test sandbox), routes through this DB bridge regardless of the
    # caller's intent — it already bit two pre-existing test fixtures (see the task-6 report's
    # "Collision found" section); both were fixed by renaming the colliding sandbox files, not
    # by changing this module. Flagging here for the next person who reuses one of these six
    # names as a generic fixture.
    "accounts.json": Bridge(
        export_fn=money_io.export_accounts,
        import_fn=money_io.import_accounts,
    ),
    "institutions.json": Bridge(
        export_fn=institution_io.export_institutions,
        import_fn=institution_io.import_institutions,
    ),
    "transactions.json": Bridge(
        export_fn=money_io.export_transactions,
        import_fn=money_io.import_transactions,
    ),
    "txn_audit.json": Bridge(
        export_fn=money_io.export_txn_audit,
        import_fn=money_io.import_txn_audit,
    ),
    "import_batches.json": Bridge(
        export_fn=money_io.export_import_batches,
        import_fn=money_io.import_import_batches,
    ),
    "statements.json": Bridge(
        export_fn=money_io.export_statements,
        import_fn=money_io.import_statements,
    ),
    "merge_journal.json": Bridge(
        export_fn=money_io.export_merge_journal,
        import_fn=money_io.import_merge_journal,
    ),
    # Planning (plan 4a task 7) — twelve total; NINE live (in core.portable.PORTABLE_FILES;
    # income_events.json joined in this task's OWN fix round 1 — see module docstring), three
    # dormant (mirrors evidence.json above). All twelve UNDOABLE (module docstring,
    # PLAN 4A TASK 7 section 1) and all ride the generic per-file _apply_bridged_import loop
    # (section 2) — no group orchestration needed, unlike the money six above.
    "category_groups.json": _envelope_groups_bridge(),
    "categories.json": _planning_bridge(categories),
    "commitments.json": _planning_bridge(commitments),
    # Owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port) — a sibling of
    # commitments.json: same generic (book_id, seq, id, payload) shape, LIVE user data.
    "assets.json": _owned_assets_bridge(),
    # id_field="key": OwnedAssetCategoriesStore keys its rows by "key", not "id" — see
    # _planning_bridge's own docstring.
    "asset_categories.json": _owned_asset_categories_bridge(),
    "envelopes.json": _planning_bridge(envelopes),
    "income_events.json": _planning_bridge(income_events),  # LIVE (fix round 1) — real income data
    "recurring_streams.json": _planning_bridge(recurring_streams),
    "targets.json": _planning_bridge(targets),
    "corrections.json": _planning_bridge(corrections),  # dormant; also checkpoint-excluded
    "match_reviews.json": _planning_bridge(match_reviews),  # dormant — the pending-review queue
    "rejected_identities.json": _planning_bridge(rejected_identities),  # LIVE — user deletes
    "match_decisions.json": Bridge(
        # Bespoke import (deterministic surrogate id when pair_key is absent) — see
        # planning_io.import_match_decisions's own docstring. Export stays the generic
        # payload-list read; the surrogate lives only in the id COLUMN, never the payload.
        export_fn=lambda conn: planning_io.export_payload_list(conn, match_decisions),
        import_fn=planning_io.import_match_decisions,
    ),  # dormant — the review queue's verdict-cache sibling
    "budget_assignments.json": Bridge(
        export_fn=planning_io.export_budget_assignments,
        import_fn=planning_io.import_budget_assignments,
    ),
    "plan_state.json": Bridge(
        export_fn=planning_io.export_plan_state,
        import_fn=planning_io.import_plan_state,
    ),
    # Pluggable budget engines (revision 0049) — the default per-engine config store; a NEW
    # addition, not part of the twelve above (module docstring's own section). Bespoke
    # dict-shaped Bridge, like budget_assignments/plan_state, sourced from its own io module.
    "budget_engine_state.json": Bridge(
        export_fn=budget_engine_state_io.export_budget_engine_state,
        import_fn=budget_engine_state_io.import_budget_engine_state,
    ),
    # Wave 5 (JSON->DB): the owner's financial-profile singleton, mirroring plan_state (a per-book
    # singleton keyed by book_id, payload verbatim). Checkpoint-INCLUDED + portable (user data).
    "profile.json": Bridge(
        export_fn=profile_io.export_profile,
        import_fn=profile_io.import_profile,
    ),
    # Investments (plan 4b task 6) — 7 of the 8 investment stores: every REAL-USER-DATA
    # one (portfolio/holdings, irreplaceable). asset_brief (the 8th) is deliberately
    # ABSENT here — see module docstring's PLAN 4B TASK 6 section, point 3: it is a
    # derived cache, wiped directly by wipe_all below but never exported/imported. All
    # seven here are LIVE (already in core.portable.PORTABLE_FILES — verified, see the
    # task's own report) and UNDOABLE (investments_io.import_payload_list/import_watchlist
    # fire fire_db_write_hook unconditionally, like money/planning).
    "positions.json": _investment_bridge(positions),
    "lots.json": _investment_bridge(lots),
    "grants.json": _investment_bridge(grants),
    "vest_events.json": _investment_bridge(vest_events),
    "option_contracts.json": _investment_bridge(option_contracts),
    "realized_events.json": _investment_bridge(realized_events),
    "watchlist.json": Bridge(
        export_fn=investments_io.export_watchlist,
        import_fn=investments_io.import_watchlist,
    ),
    # Logs/telemetry (plan 4c task 3) — ONE of the nine Tier-2 log stores:
    # automation_runs. Behavior-preserving port, not a new policy — already LIVE (in
    # core.portable.PORTABLE_FILES) and checkpoint-INCLUDED before this task touched
    # anything (module docstring, PLAN 4C TASK 3 section). Its 8 siblings
    # (automation_runtime/batch_ledger/harness_suggestions/mcp_write_log/
    # model_telemetry/pending_analysis/run_history/tool_call_log) are deliberately
    # ABSENT from BRIDGED — checkpoint-EXCLUDED runtime logs with no reset/export role
    # at all (unlike asset_brief, they get no _WIPE_FNS entry either — see that section).
    "automation_runs.json": _logs_bridge(automation_runs),
    # Config (plan 4d task 6) — SEVEN of the twelve config stores: payees/automations/alerts
    # (Tier-1, book_id=DEFAULT_BOOK_ID), dashboard_layout (dict-shaped per-book singleton),
    # memory/news_sources/intel_signals (Tier-2/3, no book_id). All seven were ALREADY LIVE
    # (in core.portable.PORTABLE_FILES) before this task touched anything. UNDOABLE
    # (config_io's import_* functions fire fire_db_write_hook unconditionally, like
    # planning/investments) and NO cross-file FK (module docstring, PLAN 4D TASK 6 section),
    # so all seven ride the GENERIC per-file services.portability._apply_bridged_import loop.
    # The remaining FOUR (memory_settings/preferred_models/statement_formats/signals) are
    # wipe-only — see _WIPE_FNS below; no BRIDGED entry for any of them. A fifth,
    # tool_grants, was absorbed into `tool_permissions` at revision 0040 and its key is gone.
    "payees.json": _config_bridge(payees, book_id=DEFAULT_BOOK_ID),
    "automations.json": _automations_bridge(),
    "alerts.json": _config_bridge(alerts, book_id=DEFAULT_BOOK_ID),
    "memory.json": _config_bridge(memory),
    "news_sources.json": _config_bridge(news_sources),
    # Intel stories + timeline entries (RFC intel-signals-data-model §3.4, migration
    # 202608301500) — the bespoke bridge that replaces the removed "intel_signals.json"
    # config-bridge entry above (module docstring, INTEL STORIES SPLIT section). Composes
    # TWO tables into one bundle file, mirroring the money six's transactions.json shape but
    # WITHOUT their cross-file FK coupling — signal_timeline_entries.signal_id FKs
    # intel_stories.id only, so this rides the generic per-file _apply_bridged_import loop,
    # not apply_money_bundle's group orchestration. daily_digests (the split's third table)
    # is deliberately absent from BRIDGED — wipe-only, see _WIPE_FNS below.
    "intel_stories.json": Bridge(
        export_fn=intel_signals_io.export_intel_stories,
        import_fn=intel_signals_io.import_intel_stories,
    ),
    "dashboard_layout.json": Bridge(
        export_fn=config_io.export_dashboard,
        import_fn=config_io.import_dashboard,
    ),
    # Misc stores (plan 4e task 1) — THREE of the four: artifacts/attachments (HYBRID —
    # metadata only, document/parsed-payload bytes stay Tier-4 files) and email_snapshots
    # (FULL MIGRATE, id_field="message_id"). All three were ALREADY LIVE (in
    # core.portable.PORTABLE_FILES) before this task touched anything. UNDOABLE
    # (misc_io.import_payload_list fires fire_db_write_hook unconditionally, like every
    # other bridged store) and NO cross-file FK, so all three ride the GENERIC per-file
    # services.portability._apply_bridged_import loop. The fourth (pending_pushes) is
    # wipe-only — see _WIPE_FNS below; no BRIDGED entry for it.
    #
    # "uploads.json" -> "attachments.json" (cross-channel-attachments RFC Phase 2,
    # revision 0041 / ADR 2026-08-16-attachment-store-replaces-upload-store): the bundle
    # KEY changes with the table rename. No dual-key import shim (CLAUDE.md: no
    # backward-compat shims) — a portable export produced before this change would not
    # restore its uploads.json member after this change; acceptable for pre-1.0 data.
    "artifacts.json": _misc_bridge(artifacts),
    "attachments.json": _attachments_bridge(),
    # Transaction receipts (cross-channel-attachments RFC Phase 4, revision 0042) — the
    # SAME bare (seq, id, payload) shape as artifacts/attachments above, so they ride the
    # identical generic `_misc_bridge`/`misc_io` pair with zero bespoke plumbing. Receipt
    # BLOB bytes (RECEIPTS_DIR) are not touched here either, mirroring artifacts'/
    # attachments' own "reset's file-level wipe owns the directory" split.
    "receipts.json": _misc_bridge(receipts),
    "transaction_receipts.json": _misc_bridge(transaction_receipts),
    "email_snapshots.json": _misc_bridge(email_snapshots, id_field="message_id"),
    # Connector (bank/broker) provenance snapshots (RFC connectors-as-data-sources §C) — a
    # deliberate SIBLING of email_snapshots above (owner decision: separate store, not a
    # shared one), same _misc_bridge shape, "key" ("<source>:<ref>") in place of
    # email_snapshots' "message_id".
    "connector_snapshots.json": _misc_bridge(connector_snapshots, id_field="key"),
    # Wave 5C (JSON->DB) — runtime queues/state. Two of the five stores that wave
    # migrates join BRIDGED (see this module's own WAVE 5C docstring section for why the
    # other three — dedup_config/harness_state/subscriptions — do not).
    "skills_state.json": Bridge(
        export_fn=lambda conn: wave5c_io.export_keyed(conn, skills_state),
        import_fn=lambda conn, raw: wave5c_io.import_keyed(conn, skills_state, raw),
    ),
    "actions.json": Bridge(
        export_fn=lambda conn: wave5c_io.export_actions(conn, actions),
        import_fn=lambda conn, raw: wave5c_io.import_actions(conn, actions, raw),
    ),
}

# The money six, named as a group — services.portability routes these filenames AROUND the
# generic per-file loop (both on export's — no, export is fine generic; see module docstring
# — and on import, where it matters: apply_money_bundle below, not _apply_bridged_import,
# handles them). MONEY_TABLES is every table the group touches, for services.portability's
# locked_unit_of_work(*MONEY_TABLES) call (the money-core choke-point seam, db/engine.py) —
# order here is cosmetic (locked_unit_of_work sorts alphabetically itself); MONEY_FILES' order
# is NOT cosmetic where it drives _MONEY_WIPE_ORDER/_MONEY_IMPORT_ORDER below.
MONEY_FILES: tuple[str, ...] = (
    "accounts.json",
    "institutions.json",
    "transactions.json",
    "txn_audit.json",
    "import_batches.json",
    "statements.json",
    "merge_journal.json",
)
MONEY_TABLES: tuple[Table, ...] = (
    accounts,
    institutions,
    transactions,
    transaction_splits,
    transaction_external_refs,
    txn_audit,
    import_batches,
    statements_meta,
    merge_journal,
)


def _wipe_evidence(conn: Connection) -> list[Path]:
    # Children first: correct under both dialects regardless of whether ON DELETE
    # CASCADE actually fires (SQLite only enforces FKs with PRAGMA foreign_keys=ON,
    # set at connect time by db/engine.py) — this does not depend on it either way.
    conn.execute(delete(evidence_subjects))
    conn.execute(delete(evidence))
    return []


def _wipe_price_history(conn: Connection) -> list[Path]:
    conn.execute(delete(price_points))
    return []


def _wipe_instruments(conn: Connection) -> list[Path]:
    conn.execute(delete(instruments))
    return []


def _capture_then_delete(conn: Connection, table: Table, pk_name: str) -> list[Path]:
    """The undoable-bridge counterpart to the three reference-data ``_wipe_*`` functions
    above: fire the checkpoint before-image hook for every row this wipe is about to
    remove, THEN delete them. The three functions above never do this (the Plan-2
    no-hook ruling); every money wipe below goes through this helper instead (this
    module's docstring, section 1) — and so does every planning wipe (PLAN 4A TASK 7
    section, including ``corrections``, whose capture is a structural no-op via
    ``ai.checkpoint._EXCLUDED_TABLES`` rather than a different code path here).

    Fired UNCONDITIONALLY, matching ``money_io``'s six ``import_<store>`` functions and
    every other DB store write in this codebase — a no-op when no checkpoint turn is
    active (``/api/reset`` never wraps itself in ``begin_turn``, so this capture is inert
    there; only the DELETE itself matters for reset), and captured into the active
    turn's write_log when one is (``/api/data/import``'s ``replace=true`` path).

    Returns ``[]`` always — every ``_wipe_*`` function has a uniform ``Connection ->
    list[Path]`` signature (fix round 1, task 6 review) so ``wipe``/``wipe_all``/
    ``apply_money_bundle`` can accumulate filesystem paths to unlink generically; only
    ``_wipe_statements_meta`` below ever returns a non-empty list."""
    existing = list(conn.execute(select(table)))
    if existing:
        fire_db_write_hook(
            table.name, [(getattr(r, pk_name), dict(r._mapping)) for r in existing], conn=conn
        )
    conn.execute(delete(table))
    return []


def _wipe_accounts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, accounts, "id")


def _wipe_institutions(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, institutions, "id")


def _wipe_transactions(conn: Connection) -> list[Path]:
    # Children first — correct regardless of whether ON DELETE CASCADE actually fires
    # (mirrors _wipe_evidence's own reasoning above); also releases the RESTRICT FK
    # transactions.from/to_account_id holds onto accounts (see _MONEY_WIPE_ORDER below —
    # this function must run before _wipe_accounts, every caller respects that).
    _capture_then_delete(conn, transaction_splits, "split_id")
    _capture_then_delete(conn, transaction_external_refs, "ref_id")
    _capture_then_delete(conn, transactions, "id")
    return []


def _wipe_txn_audit(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, txn_audit, "id")


def _wipe_import_batches(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, import_batches, "id")


def _wipe_statements_meta(conn: Connection) -> list[Path]:
    """Like ``_capture_then_delete``, plus COLLECTING (never deleting — see below) each
    wiped row's extracted parsed-rows file path (``STATEMENTS_FILES_DIR/<id>.parsed.json``,
    deterministic from the row's own id since revision 0046 — ``money_io``'s
    payload/parsed_rows split); a parsed-rows file has no DB row of its own to cascade from,
    so nothing else notices it needs to go.

    ROLLBACK SAFETY (fix round 1, task 6 review — Important finding, empirically
    reproduced): this function used to ``unlink()`` each file INSIDE this DB transaction,
    immediately. A filesystem delete is NOT part of the SQL transaction and is NEVER
    undone by a ROLLBACK — so a LATER step in the same money-group transaction failing
    (e.g. a corrupt transactions.json entry hitting an FK violation, the exact scenario
    ``test_corrupt_money_bundle_import_rolls_back_atomically`` already covers) left the DB
    row for a statement fully restored (rollback correctly undid the DELETE) while its
    parsed-rows file was already gone from disk — a dangling ``has_parsed_rows=1`` pointing
    at nothing. ``_reinline_statement``'s unconditional read then raised
    ``FileNotFoundError`` for that ONE row, and since every ``StatementStore`` read
    (``all``/``find_by_id``/``query``) reinlines EVERY row it touches, that one poisoned
    row silently bricked every future read of the whole store — permanently, until someone
    noticed and hand-repaired it.

    The fix: this function only ever COLLECTS paths, never deletes. Every caller
    (``wipe``/``wipe_all``/``apply_money_bundle`` below) must delete the returned paths
    ONLY AFTER its own enclosing transaction has committed — ``services.portability``'s
    ``_apply_money_bridged_import``/``reset_bridged_stores`` do this via
    ``_unlink_best_effort``, called strictly outside the ``with unit_of_work(...)``/
    ``with locked_unit_of_work(...)`` block. On any rollback, the exception propagates
    before that post-commit step ever runs, so ZERO files are deleted — matching the DB's
    own all-or-nothing guarantee instead of racing ahead of it.

    NOTE for a future refactor: this function has no import phase of its own, so today's
    only caller (``wipe_all`` below, via ``/api/reset``) never needs the live-`has_parsed_
    rows`-set re-query ``apply_money_bundle`` does after ITS import phase (fix round 2, task
    6 review) — a wipe-only transaction can never have a row rewrite a path this function
    already collected. If a future change ever wipes AND reseeds/reimports in the SAME
    transaction as this function's caller, it needs that same live-set pruning or it
    reintroduces the exact bug ``apply_money_bundle``'s fix round 2 closed."""
    existing = list(conn.execute(select(statements_meta)))
    paths_to_unlink: list[Path] = []
    if existing:
        fire_db_write_hook(
            statements_meta.name, [(r.id, dict(r._mapping)) for r in existing], conn=conn
        )
        for r in existing:
            if r.has_parsed_rows:
                paths_to_unlink.append(statements_files_dir() / f"{r.id}.parsed.json")
    conn.execute(delete(statements_meta))
    return paths_to_unlink


def _wipe_merge_journal(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, merge_journal, "absorbed_id")


# Planning (plan 4a task 7) — all twelve reuse ``_capture_then_delete`` directly (module
# docstring, PLAN 4A TASK 7 section 1): no children to wipe first (no cross-file FK between
# planning tables — each FKs only ``books``, which this module never touches), so there is
# nothing here for a per-store wipe function to do beyond naming its table + pk column.
# ``corrections`` is wired identically to its eleven siblings — the structural no-op comes
# from ``ai.checkpoint._EXCLUDED_TABLES``, not from any special-casing in this function.
def _wipe_envelope_groups(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, envelope_groups, "id")


def _wipe_categories(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, categories, "id")


def _wipe_commitments(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, commitments, "id")


def _wipe_owned_assets(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, owned_assets, "id")


def _wipe_owned_asset_categories(conn: Connection) -> list[Path]:
    # DB column is always "id" (book_payload_table's fixed PK) even though the store's
    # id_field="key" (owned_asset_categories.OwnedAssetCategoriesStore) — the store's
    # id_field only names which payload key is copied into that column at insert.
    return _capture_then_delete(conn, owned_asset_categories, "id")


def _wipe_envelopes(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, envelopes, "id")


def _wipe_income_events(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, income_events, "id")


def _wipe_recurring_streams(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, recurring_streams, "id")


def _wipe_targets(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, targets, "id")


def _wipe_corrections(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, corrections, "id")


def _wipe_match_decisions(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, match_decisions, "id")


def _wipe_match_reviews(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, match_reviews, "id")


def _wipe_rejected_identities(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, rejected_identities, "id")


def _wipe_budget_assignments(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, budget_assignments, "id")


def _wipe_plan_state(conn: Connection) -> list[Path]:
    # plan_state's primary key column is book_id, not id (per-book singleton — see
    # backend/db/tables.py) — the one planning wipe whose pk_name differs from "id".
    return _capture_then_delete(conn, plan_state, "book_id")


def _wipe_budget_engine_state(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, budget_engine_state, "id")


def _wipe_profile(conn: Connection) -> list[Path]:
    # profile's primary key column is book_id (per-book singleton, like plan_state). Wave 5.
    return _capture_then_delete(conn, profile, "book_id")


# Investments (plan 4b task 6) — the seven BRIDGED stores reuse _capture_then_delete
# directly, like money/planning (undoable). watchlists' pk column is book_id (a per-book
# singleton, like plan_state — NOT the generic flat-list "id"); the other six are ordinary
# id-pk flat lists. asset_brief (the 8th investment table, wipe-only — NOT in BRIDGED, see
# module docstring's PLAN 4B TASK 6 section) also gets a wipe fn here: its pk column is
# still the generic payload_table "id" (holding the instrument_id VALUE — id_field is a
# store-level read/write alias, never a real column name).
def _wipe_positions(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, positions, "id")


def _wipe_lots(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, lots, "id")


def _wipe_grants(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, grants, "id")


def _wipe_vest_events(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, vest_events, "id")


def _wipe_option_contracts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, option_contracts, "id")


def _wipe_realized_events(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, realized_events, "id")


def _wipe_watchlists(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, watchlists, "book_id")


def _wipe_asset_brief(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, asset_brief, "id")


# price_movement_notes (2026-09-20, Notable Price Movement) — wipe-only, never bridged: a derived
# per-session cache (bars + evidence + one LLM paragraph), same class as asset_brief above.
def _wipe_price_movement_notes(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, price_movement_notes, "id")


# Logs/telemetry (plan 4c task 3) — the ONE bridged log store, same undoable
# _capture_then_delete shape as every other bridged store's wipe fn. Its 8 excluded
# siblings get NO wipe fn at all (module docstring, PLAN 4C TASK 3 section) — there is
# nothing here for wipe_all to reach for them, by design.
def _wipe_automation_runs(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, automation_runs, "id")


# Config (plan 4d task 6) — the seven BRIDGED config stores reuse _capture_then_delete
# directly, like every other undoable bridge (module docstring, PLAN 4D TASK 6 section).
# dashboard_layout's pk column is book_id (a per-book singleton, like plan_state/
# watchlists — NOT the generic flat-list "id"); the other six are ordinary id-pk flat
# lists. The wipe-only siblings below ALSO reuse _capture_then_delete (statement_formats/
# signals/tool_permissions — flat lists) or the namespace-scoped settings helper
# (memory_settings/preferred_models) — signals/tool_permissions fire the hook too, uniform
# with every sibling wipe fn; their ai.checkpoint._EXCLUDED_TABLES membership is what makes
# THEIR capture a structural no-op, mirroring corrections' own precedent exactly.
def _wipe_payees(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, payees, "id")


def _wipe_automations(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, automations, "id")


def _wipe_alerts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, alerts, "id")


def _wipe_dashboard_layouts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, dashboard_layouts, "book_id")


def _wipe_memory(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, memory, "id")


def _wipe_news_sources(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, news_sources, "id")


# Intel stories (RFC intel-signals-data-model, migration 202608301500) — children first,
# mirroring _wipe_transactions'/_wipe_evidence's own "correct regardless of whether ON
# DELETE CASCADE actually fires" reasoning. signal_timeline_entries' real pk column is
# `seq` (DB-assigned autoincrement), not `id` — see intel_signals_io.py's own docstring
# for why that matters on the IMPORT side too.
def _wipe_intel_stories(conn: Connection) -> list[Path]:
    _capture_then_delete(conn, signal_timeline_entries, "seq")
    _capture_then_delete(conn, intel_stories, "id")
    return []


# daily_digests — wipe-only, deliberately NOT a BRIDGED key (module docstring, INTEL
# STORIES SPLIT section, point 3: a delivered-digest record is regenerable notification
# history, not a book fact). `date` is its literal primary key (tables.py's own comment).
def _wipe_daily_digests(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, daily_digests, "date")


# claims (RFC intel-signals-data-model, migration 202608301800) — wipe-only, same
# reasoning as daily_digests above: a claim row is a derived cross-subject push-dedup
# fact (services.claims.group_key_for/is_recent_duplicate_push), not a book fact, so it
# has no BRIDGED key either. Security-review fix (2026-08-30): this table shipped with
# NO _WIPE_FNS entry at all, so `/api/reset` silently left model-derived claim
# statements about specific instruments behind — the same "reset means reset" bug class
# income_events.json/asset_brief were deliberately fixed for; see this module's other
# wipe-only stores for the established pattern this closes the gap on.
def _wipe_claims(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, claims, "id")


def _wipe_statement_formats(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, statement_formats, "id")


# Renamed from `signals`/`SignalsStore` at revision 0044 (2026-08-20) — see that migration's
# docstring. Same wipe-only classification as before the rename; the table object is the
# new `notification_log`, but the `_WIPE_FNS` key below stays "signals.json" — UNLIKE
# `tool_permissions.json` above (a purely symbolic key, never a real JSON source), this
# key is the historical JsonStore-era source filename, cross-checked against
# `config_io.CONFIG_FILES` (which revision 0016's frozen migration still reads by that
# name) — it does not follow the table rename.
def _wipe_notification_log(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, notification_log, "id")


# THE permission table — one level per (owner, tool), for every tool in the app; it absorbed
# `tool_grants` at revision 0040 and inherits its classification whole. Wipe-only, NEVER
# BRIDGED: a permission level is instance-local trust ("I trust THIS install to let Lucius
# place orders unattended"), never a fact that should travel to another machine inside an
# exported bundle. `/api/reset` still reaches it — "reset means reset", and the direction a
# reset moves this table is back to the cautious default, since an absent row is UNDECIDED.
# Fires the hook uniformly with every sibling wipe fn; its ai.checkpoint._EXCLUDED_TABLES
# membership is what makes the capture a no-op.
def _wipe_tool_permissions(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, tool_permissions, "id")


def _wipe_settings_namespace(conn: Connection, namespace: str) -> list[Path]:
    """The namespace-scoped counterpart to ``_capture_then_delete``: ``memory_settings``
    and ``preferred_models`` share ONE physical ``settings`` table (keyed by
    ``namespace``), so each gets its own filename-keyed wipe fn that only ever touches ITS
    OWN row — never a whole-table truncate that would also destroy the sibling namespace's
    row — mirroring ``config_io.import_namespaced``'s identical per-namespace scoping.
    Fires the hook unconditionally when the row exists; an absent namespace is a true
    no-op (nothing to capture, nothing to delete), matching every other wipe fn's "no
    existing rows -> no hook" contract."""
    existing = conn.execute(select(settings).where(settings.c.namespace == namespace)).first()
    if existing is not None:
        fire_db_write_hook(settings.name, [(namespace, dict(existing._mapping))], conn=conn)
        conn.execute(delete(settings).where(settings.c.namespace == namespace))
    return []


def _wipe_memory_settings(conn: Connection) -> list[Path]:
    return _wipe_settings_namespace(conn, "memory_settings")


def _wipe_preferred_models(conn: Connection) -> list[Path]:
    return _wipe_settings_namespace(conn, "preferred_models")


# Misc stores (plan 4e task 1) — all four reuse _capture_then_delete directly, like every
# other undoable bridge. artifacts/attachments/email_snapshots (the three BRIDGED) and
# pending_pushes (wipe-only, below) are all ordinary generic-"id"-pk flat lists — no
# per-book singleton shape in this wave. artifacts'/attachments' BYTE files (ARTIFACTS_DIR/
# ATTACHMENTS_DIR) are NOT touched here — reset's file-level wipe (api/setup.py) owns
# clearing those directories, exactly as it already did before this task (the metadata row
# is the only thing that moved to the DB).
def _wipe_artifacts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, artifacts, "id")


def _wipe_attachments(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, attachments, "id")


def _wipe_receipts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, receipts, "id")


def _wipe_transaction_receipts(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, transaction_receipts, "id")


def _wipe_email_snapshots(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, email_snapshots, "id")


def _wipe_connector_snapshots(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, connector_snapshots, "id")


def _wipe_pending_pushes(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, pending_pushes, "id")


def _wipe_import_runs(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, import_runs, "id")


# Checkpoints (plan 4e task 2) — wipe-only, like pending_pushes above; reuses
# _capture_then_delete directly, uniform with every other wipe fn (the actual no-op comes
# from ai.checkpoint._EXCLUDED_TABLES + the store's own checkpointed=False, not a special
# case here).
def _wipe_checkpoints(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, checkpoints, "id")


# Wave 5C (JSON->DB) — the two BRIDGED runtime-state stores, reached via the ordinary
# `for filename in BRIDGED` loop below like every other non-money entry. Both are
# ordinary generic-"id"-pk flat tables despite skills_state's dict-shaped JSON source —
# the skill id lives in the row's id COLUMN either way, so _capture_then_delete (which
# only ever touches that column) needs no special casing for the shape difference.
def _wipe_skills_state(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, skills_state, "id")


def _wipe_actions(conn: Connection) -> list[Path]:
    return _capture_then_delete(conn, actions, "id")


def _cleanup_staged_renames(staged_renames: list[tuple[Path, Path]]) -> None:
    """Best-effort remove every STAGED TEMP file (never the final path — nothing ever
    renamed a temp onto it) — called by ``apply_money_bundle`` when its import phase is
    about to propagate an exception (the enclosing transaction is rolling back), so a
    money-bundle import that fails partway through never leaves orphaned
    ``<final>.import-tmp-<hex>`` files behind. Final review fix, plan 3 task 6 — the
    WRITE-side counterpart to ``_capture_then_delete``'s DELETE-side rollback safety:
    nothing here needs a transaction at all, since a staged temp was never referenced by
    any DB row in the first place."""
    for tmp_path, _final_path in staged_renames:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


_WIPE_FNS: dict[str, Callable[[Connection], list[Path]]] = {
    "evidence.json": _wipe_evidence,
    "price_history.json": _wipe_price_history,
    "instruments.json": _wipe_instruments,
    "accounts.json": _wipe_accounts,
    "institutions.json": _wipe_institutions,
    "transactions.json": _wipe_transactions,
    "txn_audit.json": _wipe_txn_audit,
    "import_batches.json": _wipe_import_batches,
    "statements.json": _wipe_statements_meta,
    "merge_journal.json": _wipe_merge_journal,
    "category_groups.json": _wipe_envelope_groups,
    "categories.json": _wipe_categories,
    "commitments.json": _wipe_commitments,
    "assets.json": _wipe_owned_assets,
    "asset_categories.json": _wipe_owned_asset_categories,
    "envelopes.json": _wipe_envelopes,
    "income_events.json": _wipe_income_events,
    "recurring_streams.json": _wipe_recurring_streams,
    "targets.json": _wipe_targets,
    "corrections.json": _wipe_corrections,
    "match_decisions.json": _wipe_match_decisions,
    "match_reviews.json": _wipe_match_reviews,
    "rejected_identities.json": _wipe_rejected_identities,
    "budget_assignments.json": _wipe_budget_assignments,
    "plan_state.json": _wipe_plan_state,
    "budget_engine_state.json": _wipe_budget_engine_state,
    "profile.json": _wipe_profile,
    "positions.json": _wipe_positions,
    "lots.json": _wipe_lots,
    "grants.json": _wipe_grants,
    "vest_events.json": _wipe_vest_events,
    "option_contracts.json": _wipe_option_contracts,
    "realized_events.json": _wipe_realized_events,
    "watchlist.json": _wipe_watchlists,
    # wipe-only — deliberately NOT a BRIDGED key (a cache; module docstring, PLAN 4B TASK
    # 6 section, point 3). Reached explicitly by wipe_all below, not via the `for filename
    # in BRIDGED` loop every other entry here rides.
    "asset_briefs.json": _wipe_asset_brief,
    "price_movement_notes.json": _wipe_price_movement_notes,
    # Logs/telemetry (plan 4c task 3) — the one bridged log store; reached via the
    # ordinary `for filename in BRIDGED` loop below, like every other non-money entry.
    "automation_runs.json": _wipe_automation_runs,
    # Config (plan 4d task 6) — the seven BRIDGED config stores, reached via the ordinary
    # `for filename in BRIDGED` loop below like every other non-money entry.
    "payees.json": _wipe_payees,
    "automations.json": _wipe_automations,
    "alerts.json": _wipe_alerts,
    "dashboard_layout.json": _wipe_dashboard_layouts,
    "memory.json": _wipe_memory,
    "news_sources.json": _wipe_news_sources,
    # Intel stories (RFC intel-signals-data-model, migration 202608301500) — reached via
    # the ordinary `for filename in BRIDGED` loop below, like every other non-money entry.
    "intel_stories.json": _wipe_intel_stories,
    # daily_digests — wipe-only, deliberately NOT a BRIDGED key (module docstring, INTEL
    # STORIES SPLIT section, point 3). Reached explicitly by wipe_all below, not via the
    # `for filename in BRIDGED` loop — mirrors asset_briefs.json's own wipe-only precedent.
    "daily_digests.json": _wipe_daily_digests,
    # claims — wipe-only, same reasoning as daily_digests immediately above (derived
    # push-dedup fact, not a book fact). Reached explicitly by wipe_all below. Security-
    # review fix (2026-08-30) — see _wipe_claims' own docstring for why this entry exists.
    "claims.json": _wipe_claims,
    # wipe-only — deliberately NOT BRIDGED keys (config/cache, not user-authored financial
    # fact; module docstring, PLAN 4D TASK 6 section, point 2). Reached explicitly by
    # wipe_all below, not via the `for filename in BRIDGED` loop — mirrors asset_briefs.
    # json's own wipe-only precedent.
    "statement_formats.json": _wipe_statement_formats,
    # Key stays "signals.json" — the historical JsonStore-era source filename (matches
    # `config_io.CONFIG_FILES`, which revision 0016's frozen migration still reads by this
    # name), even though the table itself was renamed to `notification_log` at revision
    # 0044. UNLIKE `tool_permissions.json` just below, this key is NOT purely symbolic —
    # it is cross-checked against the real historical migration source name, so it does
    # not follow the table rename.
    "signals.json": _wipe_notification_log,
    # THE permission table — wipe-only, never BRIDGED (see the wipe fn's own comment).
    # Keyed "<table_name>.json" like `checkpoints.json`: this table never had a JSON source,
    # so the key is a purely symbolic registry identifier, not a path anything reopens. The
    # absorbed `tool_grants.json` key is GONE with its table (revision 0040) — a reset that
    # still named it would be reaching for a table that no longer exists.
    "tool_permissions.json": _wipe_tool_permissions,
    "memory_settings.json": _wipe_memory_settings,
    "preferred_models.json": _wipe_preferred_models,
    # Misc stores (plan 4e task 1) — the three BRIDGED misc stores, reached via the
    # ordinary `for filename in BRIDGED` loop below like every other non-money entry.
    "artifacts.json": _wipe_artifacts,
    "attachments.json": _wipe_attachments,
    # Transaction receipts (cross-channel-attachments RFC Phase 4, revision 0042) — BRIDGED
    # like artifacts/attachments above, reached via the same `for filename in BRIDGED` loop.
    "receipts.json": _wipe_receipts,
    "transaction_receipts.json": _wipe_transaction_receipts,
    "email_snapshots.json": _wipe_email_snapshots,
    "connector_snapshots.json": _wipe_connector_snapshots,
    # wipe-only — deliberately NOT a BRIDGED key (a runtime retry queue, not user data;
    # module docstring, PLAN 4E TASK 1 section, point 2). Reached explicitly by wipe_all
    # below, not via the `for filename in BRIDGED` loop — mirrors asset_briefs.json's own
    # wipe-only precedent.
    "pending_pushes.json": _wipe_pending_pushes,
    # wipe-only — deliberately NOT a BRIDGED key (undo history is not portable; module
    # docstring, PLAN 4E TASK 2 section). Keyed "checkpoints.json" (the table name), NOT
    # the source file's real basename ("index.json" — CHECKPOINTS_INDEX lived under
    # CHECKPOINTS_DIR, not DATA_DIR) — this key is a purely symbolic registry identifier,
    # consistent with every sibling entry's <table_name>.json shape, never an actual path
    # anything reopens by this name. Reached explicitly by wipe_all below, not via the
    # `for filename in BRIDGED` loop — mirrors pending_pushes.json's own wipe-only
    # precedent.
    "checkpoints.json": _wipe_checkpoints,
    # wipe-only — an import report describes the book Reset just emptied; not portable.
    "import_runs.json": _wipe_import_runs,
    # Wave 5C (JSON->DB) — the two BRIDGED runtime-state stores, reached via the ordinary
    # `for filename in BRIDGED` loop below like every other non-money entry.
    "skills_state.json": _wipe_skills_state,
    "actions.json": _wipe_actions,
}

# Explicit FK-safe orderings for the money group — see this module's docstring, section 2.
# transactions.from_account_id/to_account_id are ON DELETE RESTRICT onto accounts.id, so the
# two orders are OPPOSITES: wiping must clear transactions (+children) before accounts
# (otherwise deleting a still-referenced account raises); importing must insert accounts
# before transactions (otherwise a transaction referencing a not-yet-inserted account raises
# the same FK the other way). The four remaining money files carry no FK to accounts/
# transactions (tables.py's own comments on each), so their relative order is cosmetic —
# listed after the accounts/transactions pair in both tuples for readability only.
_MONEY_WIPE_ORDER: tuple[str, ...] = (
    "transactions.json",
    "accounts.json",
    "institutions.json",
    "txn_audit.json",
    "import_batches.json",
    "statements.json",
    "merge_journal.json",
)
_MONEY_IMPORT_ORDER: tuple[str, ...] = (
    "institutions.json",
    "accounts.json",
    "transactions.json",
    "txn_audit.json",
    "import_batches.json",
    "statements.json",
    "merge_journal.json",
)


def wipe(conn: Connection, filename: str) -> list[Path]:
    """Delete one bridged store's rows, inside the CALLER's transaction. Raises
    KeyError for a filename that isn't bridged — callers only invoke this after
    checking ``filename in BRIDGED``.

    Returns any filesystem paths this wipe made obsolete (currently only ever
    ``statements.json``'s parsed-rows files — every other store's wipe function returns
    ``[]``). ROLLBACK SAFETY (fix round 1, task 6 review): these paths are NOT deleted
    here — see ``_wipe_statements_meta``'s docstring for why a filesystem delete must
    never happen before the caller's enclosing transaction has committed. The caller
    (``wipe_all``/``apply_money_bundle`` below, and transitively
    ``services.portability``) owns deleting them, strictly after commit.

    A money filename's wipe function is NOT safe to call in isolation out of
    ``_MONEY_WIPE_ORDER`` — see that tuple's comment. The only callers in this codebase
    (``wipe_all`` below and ``apply_money_bundle``) both respect the order; a caller
    outside this module that needs to wipe a single money store should use ``wipe_all``
    or ``apply_money_bundle`` instead of calling this directly with one money filename.
    """
    return _WIPE_FNS[filename](conn)


def wipe_all(conn: Connection) -> list[Path]:
    """Delete every bridged store's rows, inside the caller's transaction — the
    DB-backed half of ``/api/reset``. Never touches ``write_log``/``json_cutovers``
    (migration/checkpoint machinery, not user data) or the identity tables
    (users/books/book_members): this only ever operates on the tables named in
    ``_WIPE_FNS`` above.

    The three reference-data stores, all twelve planning stores (plan 4a task 7), the
    seven BRIDGED investment stores (plan 4b task 6), the one BRIDGED logs store
    (``automation_runs.json``, plan 4c task 3), the seven BRIDGED config stores (plan 4d
    task 6) AND the three BRIDGED misc stores (plan 4e task 1) wipe in BRIDGED's own
    (unordered — every one of the thirty-three is mutually independent, no cross-store
    FK among them) key order; the money six wipe in ``_MONEY_WIPE_ORDER`` afterward, for
    the FK-coupling reason that order exists (this module's docstring, section 2).
    Reaching a new BRIDGED store here needs no code change beyond extending
    ``BRIDGED``/``_WIPE_FNS`` above — this loop already iterates every non-money
    ``BRIDGED`` key unconditionally, live or dormant (evidence.json's own precedent).
    ``asset_briefs.json`` (plan 4b task 6) is reached via one direct
    ``wipe(conn, "asset_briefs.json")`` call below, since the ``for filename in BRIDGED``
    loop cannot see a key that was never added to ``BRIDGED``; the config wipe-only stores
    (``memory_settings``/``preferred_models``/``statement_formats``/``signals``, plan 4d
    task 6 — module docstring, PLAN 4D TASK 6 section, point 2) are reached the same way,
    one explicit ``wipe(conn, ...)`` call each — as is ``tool_permissions`` (THE permission
    table, which absorbed ``tool_grants``' own wipe-only entry at revision 0040 along with
    its instance-local-trust reasoning) — as is
    ``pending_pushes.json`` (plan 4e task 1, module docstring PLAN 4E TASK 1 section,
    point 2), ``checkpoints.json`` (plan 4e task 2, module docstring PLAN 4E TASK 2
    section), ``daily_digests.json`` (RFC intel-signals-data-model, module docstring's
    INTEL STORIES SPLIT section, point 3), and ``claims.json`` (same RFC, security-review
    fix 2026-08-30 — see ``_wipe_claims``' own docstring). The 8 logs stores excluded from Wave C's own
    BRIDGED addition get NO ``_WIPE_FNS`` entry at all (module docstring, PLAN 4C TASK 3
    section) — unlike
    ``asset_brief``/the five config wipe-only stores/``pending_pushes`` there is no reset
    role to reach for them, by design. One transaction end to end (the caller's), so a
    mid-wipe failure leaves nothing partially reset.

    Returns every filesystem path any wiped store's rows referenced — ROLLBACK SAFETY
    (fix round 1, task 6 review): this function NEVER deletes them itself (see
    ``_wipe_statements_meta``'s docstring). The caller (``services.portability.
    reset_bridged_stores``) MUST delete them only after ITS OWN transaction — the one this
    function ran inside of — has already committed. See ``_wipe_statements_meta``'s own
    docstring for why this function does NOT need ``apply_money_bundle``'s live-set pruning
    (fix round 2, task 6 review) today — only relevant if a future change ever composes a
    wipe and a reimport into one transaction."""
    paths_to_unlink: list[Path] = []
    for filename in BRIDGED:
        if filename in _MONEY_WIPE_ORDER:
            continue
        paths_to_unlink.extend(wipe(conn, filename))
    paths_to_unlink.extend(wipe(conn, "asset_briefs.json"))
    paths_to_unlink.extend(wipe(conn, "price_movement_notes.json"))
    for filename in (
        "memory_settings.json",
        "preferred_models.json",
        "statement_formats.json",
        "signals.json",
        "tool_permissions.json",
    ):
        paths_to_unlink.extend(wipe(conn, filename))
    paths_to_unlink.extend(wipe(conn, "pending_pushes.json"))
    paths_to_unlink.extend(wipe(conn, "checkpoints.json"))
    paths_to_unlink.extend(wipe(conn, "import_runs.json"))
    paths_to_unlink.extend(wipe(conn, "daily_digests.json"))
    paths_to_unlink.extend(wipe(conn, "claims.json"))
    for filename in _MONEY_WIPE_ORDER:
        paths_to_unlink.extend(wipe(conn, filename))
    return paths_to_unlink


def apply_money_bundle(
    conn: Connection, entries: dict[str, Any]
) -> tuple[dict[str, int], list[Path], list[tuple[Path, Path]]]:
    """Wipe + (if present) restore all SIX money-bridged stores as ONE referential unit,
    inside the CALLER's transaction (open it over ``MONEY_TABLES`` via
    ``db.engine.locked_unit_of_work`` — the money-core choke-point seam every other
    multi-table money write already composes through, e.g.
    ``services.ledger.set_account_opening``'s accounts+transactions pair).

    ``entries`` maps each of ``MONEY_FILES`` to its parsed bundle JSON (a list of dicts)
    when the bundle carried that file, or ``None`` when it didn't (a portable file absent
    from the bundle resets to empty — the same contract every other portable file gets in
    ``services.portability.apply_import``). Returns ``({filename: row_count}, paths_to_
    unlink, staged_renames)``: the row-count dict is for the filenames that WERE present —
    the shape ``services.portability.apply_import`` needs to fold into its own
    ``row_counts``/``restored_files``; ``paths_to_unlink`` is every filesystem path the wipe
    phase made obsolete (currently only ever statements' parsed-rows files);
    ``staged_renames`` is every ``(tmp_path, final_path)`` pair a present ``statements.json``
    entry staged (see the WRITE-SIDE ROLLBACK SAFETY section below) — the caller must
    ``os.replace``/``durable_replace`` each pair into place, ALSO strictly post-commit, and
    BEFORE applying ``paths_to_unlink`` (see that section for why the order is renames-then-
    unlinks, not that it is load-bearing given fix round 2's own pruning).

    ROLLBACK SAFETY (fix round 1, task 6 review): ``paths_to_unlink`` is a RETURN VALUE,
    never deleted by this function. The caller MUST delete those paths only AFTER its own
    ``with locked_unit_of_work(...)`` block has exited WITHOUT an exception (i.e. after
    the transaction committed) — see ``_wipe_statements_meta``'s docstring for the
    incident this guards against. Deleting a file before that point means a later step in
    THIS SAME function raising (a corrupt entry, an FK violation) would roll back every DB
    row but leave the file gone regardless — exactly the split-world state that used to
    brick ``StatementStore``'s reads.

    WHY NOT THE GENERIC ``_apply_bridged_import`` LOOP (services/portability.py): that
    function wipes-then-imports ONE file, atomically, per call — correct for evidence/
    price_history/instruments because they share no foreign keys. Money does:
    ``transactions.from_account_id``/``to_account_id`` are ``ON DELETE RESTRICT`` onto
    ``accounts.id``. Processing accounts.json and transactions.json as two independent
    wipe-then-import steps has NO safe order: wipe-accounts-first hits RESTRICT (old
    transactions still reference the old accounts being deleted); wipe-transactions-first-
    then-import-transactions-first hits the INSERT-time FK the other way (the new
    transactions reference NEW account ids that don't exist yet, since accounts.json
    hasn't been imported). The two phases must be FULLY separated across the whole group
    instead: wipe every touched table first (``_MONEY_WIPE_ORDER`` — transactions-family
    before accounts, releasing the RESTRICT), then import every present file
    (``_MONEY_IMPORT_ORDER`` — accounts before transactions, satisfying the INSERT-time
    FK). All eight tables share ONE transaction (the caller's) — a raise anywhere (a
    corrupt entry, a ``verify_cross_file_consistency`` violation inside an ``import_fn``)
    rolls back the ENTIRE group, not just one file: a partial replace across FK-coupled
    tables cannot be expressed as "roll back just the one file" without either an FK
    violation or a silently mixed old/new state, so the six are one atomic unit by
    construction, not by choice.

    UNDOABLE (plan 3 task 6; see this module's docstring, section 1): every row this
    wipes fires ``fire_db_write_hook`` with its full pre-wipe content (``_capture_then_
    delete``/``_wipe_statements_meta``, called via the ``wipe()`` dispatch below) BEFORE
    the delete; every row a present file's ``import_fn`` then inserts fires the hook
    again, ``(pk, None)`` (``money_io``'s own six functions). The active turn's
    ``CheckpointContext._db_seen`` dedup (first touch per (table, pk) wins) makes the
    combination correct with no extra bookkeeping here: a pk that existed pre-wipe keeps
    its wipe-phase image (the true "before" — a later ``(pk, None)`` from the import
    phase, if the bundle happens to reuse that same id, is a dedup no-op); a pk that is
    genuinely new to this bundle has no wipe-phase touch, so the import phase's
    ``(pk, None)`` is its only (and correct) image, meaning restore deletes it. Cross-
    table replay ordering (parents before children on insert, children before parents on
    delete) is handled entirely by ``ai.checkpoint._replay_write_log``'s existing
    ``metadata.sorted_tables`` topological sort — already proven against this EXACT FK
    (``tests/db/test_write_log_checkpoints.py::test_a_transactions_write_also_captures_
    the_opening_pointer_row_no_companions_map_needed``) — nothing here needs to know
    about replay order, only write order.

    DELETE-SET PRUNING (fix round 2, task 6 review — Blocker, empirically reproduced): a
    path the wipe phase collects can be RE-WRITTEN by the import phase that runs right
    after it, in this SAME function call — the deterministic ``<id>.parsed.json`` path
    (``money_io._statement_row``) means a bundle reimporting the SAME statement id with NEW
    ``parsed_rows`` content lands its fresh file at the EXACT path the wipe phase already
    queued for deletion. Fix round 1's naive "wipe collects, caller deletes post-commit"
    would have deleted that freshly-written file right after committing the row that
    correctly points at it — reimporting the same backup twice (the primary real-world use
    of ``/api/data/import``) silently lost the second import's parsed data, masked by fix round
    1's own tolerant-reinline (FIX 2) as ``parsed_rows=None`` with only a log line. The
    delete-set semantics must be "orphaned by the wipe AND not referenced by any surviving
    row at commit time" — re-querying the LIVE ``statements_meta.has_parsed_rows`` set HERE,
    inside this same transaction (so it sees this call's own uncommitted import writes),
    deriving each live row's path from its own id, and subtracting that set from
    ``paths_to_unlink`` is robust against ANY writer landing a file at a collected path, not
    just a same-id reimport specifically — deliberately more general than "if the reimported
    entries include this id" would be. The subtraction happens before this function returns,
    i.e. before the caller's transaction ever exits, so the list that escapes to
    ``services.portability``'s post-commit unlink step is already clean.

    WRITE-SIDE ROLLBACK SAFETY (final review fix — the mirror image of the delete-side
    fixes above): ``money_io.import_statements`` STAGES its sidecar writes to a temp path
    (passed ``staged_renames=`` below) rather than touching the live final path directly.
    Before this fix, a statement's sidecar file was written straight to its live path
    DURING this transaction — so a file ordered AFTER ``statements.json`` in
    ``_MONEY_IMPORT_ORDER`` (``merge_journal.json`` is last) failing correctly rolled the
    DB row back to its pre-import content, but the FILE it pointed at had already been
    overwritten with the failed import's content and was never rolled back — every
    subsequent read silently served the WRONG (failed-import) financial data under the
    restored row's original identity, with no warning (the file exists and parses fine).
    Staging closes this the same way the delete side already closed its own two scars:
    nothing touches a LIVE path until the transaction has actually committed. On a raise
    from the import loop below, this function cleans up whatever temps it had ALREADY
    staged (``_cleanup_staged_renames``) before re-raising — the final paths are never
    touched by that cleanup, since nothing ever renamed a temp onto them, so they still
    hold their correct pre-import content, matching the rolled-back DB rows exactly.
    """
    accounts_payload, institutions_payload = institution_io.normalize_account_institutions(
        entries.get("accounts.json"), entries.get("institutions.json")
    )
    entries = {
        **entries,
        "accounts.json": accounts_payload,
        "institutions.json": institutions_payload,
    }
    paths_to_unlink: list[Path] = []
    for filename in _MONEY_WIPE_ORDER:
        paths_to_unlink.extend(wipe(conn, filename))
    row_counts: dict[str, int] = {}
    staged_renames: list[tuple[Path, Path]] = []
    try:
        for filename in _MONEY_IMPORT_ORDER:
            parsed = entries.get(filename)
            if parsed is None:
                continue
            if filename == "statements.json":
                row_counts[filename] = money_io.import_statements(
                    conn, parsed, staged_renames=staged_renames
                )
            else:
                row_counts[filename] = BRIDGED[filename].import_fn(conn, parsed)
    except BaseException:
        _cleanup_staged_renames(staged_renames)
        raise
    if paths_to_unlink:
        files_dir = statements_files_dir()
        live_paths = {
            files_dir / f"{row.id}.parsed.json"
            for row in conn.execute(select(statements_meta.c.id, statements_meta.c.has_parsed_rows))
            if row.has_parsed_rows
        }
        paths_to_unlink = [p for p in paths_to_unlink if p not in live_paths]
    return row_counts, paths_to_unlink, staged_renames
