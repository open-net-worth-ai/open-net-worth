---
title: Bank and broker connectors are data sources; Lucius books, the scheduler forgets
status: Accepted (amended by 2026-11-13-a-bank-connector-can-book-its-rows-without-ai)
date: 2026-09-05
deciders: repo owner (decision) + orchestrating Claude agent (design)
---

# 2026-09-05. Bank and broker connectors are data sources; Lucius books, the scheduler forgets

## Context

Eight aggregation providers reached the ledger through a hidden pipeline: a hardcoded
`_INGEST_SOURCES` cadence table in `backend/scheduler.py`, one APScheduler job per provider, and
`services.ingestion.orchestrator.run_ingestion`, which fetched, mapped, deduplicated by provider
ref, and committed through `ledger.bulk_create_transactions`. The user could not see when a sync
ran (no UI rendered the stored interval), could not read or change what it did, and never learned
when a provider corrected or withdrew a row (Plaid `modified` rows deduplicated away; `removed` was
never read). The provider-account to app-account mapping lived in nine config blobs.

The email connector had already left this shape (`docs/rfc/email-as-generic-source.md`): two
generic read tools plus a seeded, plain-English Automation that books through the ordinary
`add_transactions` choke, idempotent by `external_refs`. ADR
`2026-08-02-replace-rules-and-scheduled-tasks-with-automations` rejected "convert every row into a
Lucius prompt" for Rules and Scheduled Tasks because model-selected writes would weaken
deterministic ledger guarantees. That objection does not bind here: the ledger choke, not the
prompt, enforces idempotency (L0 ref floor inside the write transaction), refuses a previously
deleted identity, and refuses a strong duplicate naming the collision. What a Lucius turn decides
is only whether to call the tool and which rows to pass.

`.claude/rfcs/2026-08-10-connectors-architecture.md` §B7 confirmed polling through the scheduler
as the primary driver. This decision reverses that.

## Decision

We will make every bank and broker connector a read-only data source and delete the scheduled
sync pipeline:

- One read tool per offered provider (`<provider>_fetch_transactions`, `alpaca_fetch_positions`,
  `ibkr_fetch_activity`) returns the provider's accounts and normalized rows for a window, each row
  carrying `external_refs=[{source, ref}]`. Default window is a per-provider lookback (14 days for
  banks and IBKR) so late-posted rows and corrections are seen; "new" means "no live ledger row
  carries the ref", never "dated since the last run". `include_booked=true` returns booked rows
  with the transaction they landed on.
- Booking happens only in a Lucius turn through the existing `add_transactions` and position/lot
  tools. Templates in the Automations gallery, one per provider, five plain-English lines with no
  mechanism in them, are how a user turns sync on; cadence and wording are theirs. No seeding, no
  coupling to the connector toggle, no cadence floor.
- Every fetched row is kept in a connector snapshot store separate from email's, keyed by
  `source:ref`, hashed on normalized fields, not first-seen-wins. The tool reports `modified` and
  `vanished` (two consecutive complete misses) rows; this cut only notifies, never edits or deletes.
- The provider-account link moves onto the account as `external_ids`, unique per `(source, ref)`;
  `connected` derives from it; the config-store `account_map`s are backfilled then removed.
- `_INGEST_SOURCES`, the `ingest:*` jobs, the nine `reschedule_*` shims, the orchestrator, the
  adapters' commit halves, `connector_sync`, the nine `<provider>_sync_now` tools, and the nine
  sync-now routes are deleted. No aliases (ADR 2026-08-25).

## Consequences

### Positive

- One scheduling fact in the product: every recurring job the user can see is an Automation.
- The user reads and edits what sync does, sees every run, and can ask Lucius to fetch any window.
- Corrections and withdrawals become visible instead of silently dropped; raw provider evidence
  backs every booked row in the drawer, as email already does.
- Rows with no stable provider id get a derived ref instead of being dropped.
- Roughly 250 lines of scheduler machinery, 37 call sites, ten tools, nine routes, and the
  orchestrator go away; the data path (fetch + normalize) stays and is testable in isolation.

### Negative

- Every fetch is a Lucius turn, scheduled or in chat, so a self-hoster with no model configured
  cannot book provider rows at all; linking accounts and the Connectors account list still work.
- Existing enabled connectors stop auto-syncing on upgrade until the user picks the template.
- A first-run account mapping is a model decision (the template says ask when unsure); after
  linking it is deterministic.
- A refused strong duplicate of a hand-typed row is re-reported on every run inside the lookback
  until the parked "attach" review lands (owner deferred it).
- Reliability rests on the model calling two tools per run; run history exposes a bad run and the
  lookback makes it recoverable, but a deterministic pipeline had neither failure mode.

### Follow-ups

- The "attach" review: a refused strong duplicate becomes a pending review pairing the incoming
  ref with the hand-typed row; accepting appends the ref via `resolve_external_refs_patch`.
- The apply-changes write tool with a fixed policy (provider-owned fields only; tag, never delete).
- Product doc rewrite (`docs/product/connectors/bank-brokerage.md`), `CLAUDE.md` invariant line,
  `docs/INVARIANTS.md`, `docs/DATA_MODEL.md`, tool docs; board row with evidence.

## Alternatives considered

- **Keep the scheduler, add a deterministic sync executor to Automations** — rejected by owner: the
  clock moves, the pipeline stays hidden and uneditable.
- **Seed one active automation per enabled connector, tied to the toggle, with cadence floors** —
  rejected by owner as over-engineering; templates and visible errors are enough.
- **Return every row and let the choke dedup** — superseded by the `include_booked` switch, which
  keeps the listing use case without re-emitting booked rows through the model every run.
- **One shared snapshot store for email and connectors** — rejected by owner; email keeps its
  legacy-key and first-seen-wins semantics.
- **Book with `allow_duplicate` and merge after** — rejected; a real duplicate skews balances
  until resolved.

## Agent provenance

- **Decided by:** repo owner, in a design conversation with the orchestrating Claude agent
- **Lane / branch:** `feature/connectors-as-data-sources` (worktree `.claude/worktrees/connectors-as-data-sources`)
- **Alternatives weighed:** deterministic executor · seeded-per-connector with floors · no booked
  filter · shared snapshot store · force-then-merge
- **Driving artifacts:** `docs/prd/connectors-as-data-sources.md`,
  `.claude/rfcs/connectors-as-data-sources.md`, `docs/rfc/email-as-generic-source.md`,
  `.claude/rfcs/2026-08-10-connectors-architecture.md` §B7, Actual Budget `sync.ts`
- **Why this way (one line):** the ledger choke already guarantees idempotency and refuses
  duplicates, so the only thing a hidden pipeline added was a clock nobody could see.
