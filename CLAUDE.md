# Open Net Worth — Codebase Guide

FastAPI backend + React/TypeScript frontend. AI assistant = Lucius. `AGENTS.md` is a symlink to
this file — edit here only.

## What belongs in this file

**Orientation + gotchas only** — what an agent must know *before* reading any file. Not a
changelog, not an ADR mirror. A decision's reasoning → `docs/adr/`; a workflow's steps →
`.claude/skills/`; a subsystem's detail → `docs/<AREA>.md` (see [Docs map](#docs-map)). If an
agent can discover it on demand, link it instead of restating it.

## Active development — the Hardening Loop

Drive ONE core flow at a time to "I'd bet my real numbers on it": **① Foundation (ledger + net
worth) → ② Budgeting → ③ Investments**. Status is earned by evidence, never asserted. Live board:
`docs/launch/BOARD.md` (the maintainer's local copy, gitignored). Run a turn with the **`hardening-loop`** skill;
capture a bug with `make bug MSG="…"`.

## Running locally

Fresh clone: `make setup start` (venv, deps, DB migration, frontend build, editable install of
the `onw` CLI, then run). No `.env` needed — data defaults to `~/.open-net-worth/`, outside the
checkout. `AUTH_PASSWORD=… docker compose up` on Windows (the password is REQUIRED under Docker).
`onw autostart install` / `make autostart` installs a boot service (launchd/systemd).

Users don't clone: they `uv tool install open-net-worth` (wheel built by `make wheel`) or pull
`ghcr.io/open-net-worth-ai/open-net-worth`. A release is a manual, gated, signed act —
`make release-commit VERSION=x.y.z`, then the releases repo's `release.yml`; steps and owner setup in
[`RELEASING.md`](RELEASING.md), ADR `2026-10-05-releases-are-gated-signed-and-verified`.
The wheel carries `frontend/dist`, `docs-site/dist-embed` and `docs/product` under
`backend/_bundled/`; `core/shipped_assets.py` resolves them — REPO copy wins when present,
bundled only inside an installed wheel. `backend/cli/` is the top layer (`cli → api → …`). ADR
`2026-09-19-distribute-as-uv-tool-and-published-image`.

```bash
uvicorn backend.main:app --reload --port 8080
cd frontend && npm run dev   # → http://localhost:5174
```

## Windows

Windows 10/11 x64 is a first-class platform. POSIX-only primitives live behind `core/` seams — do
not call `fcntl`, `os.killpg`, `os.geteuid`, `os.fchmod`, `dir_fd=` or `preexec_fn` outside them:
`core/file_lock.py` (shared/exclusive locks), `core/process_tree.py` (spawn + kill a tree),
`core/anchored_dir.py` (no-follow, name-relative directory access), `core/platform_fs.py` (binary
fds, fsync, retrying replace, owner-only dirs), `sandbox/win_job.py` (Job Object caps). Every
`os.open` of file content goes through `platform_fs.open_flags` (adds `O_BINARY`). CI runs the
focused suite on `windows-latest`; what differs from POSIX is in [`docs/WINDOWS.md`](docs/WINDOWS.md).

## Dependency rule (5 import-linter contracts, CI-blocking)

`api → ai → services → db → core` — imports flow downward only. `db` must not import `ai`
(write-hook inverted via `core.io`); `services` must not import `api`; `core`/`models`/`sandbox`
are secret-free leaves. Run: `make lint` / `make imports`; full reference:
[BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md). Routers (`api/`) validate → call ONE service
→ shape response; services hold all logic; repos (`db/`) are data access only. **All monetary
math in `services/`** — a stored `base_amount` is trusted only while `base_currency` matches the
current base; otherwise recompute via `txn_base_amount()`.

## Write choke points (never bypass)

| Domain | Choke |
|---|---|
| Transactions | `ledger_service.create_transaction` / `bulk_create_transactions` (erase: `transaction_purge.purge_transactions`) |
| Positions / Lots | `positions_service.apply_position_change` / `lots_service.apply_lot_change` |
| Planning / Equity | `planning.apply_budget_change` / `equity_service.apply_equity_change` |
| Automations · Income · Receipts | `apply_automation_change` · `apply_income_change` · `receipts_service.apply_receipt_change` |
| Owned assets | `owned_assets_service.apply_owned_asset_change` |
| Intel stories / timeline | `intel_stories_service.apply_story_change` |
| Intel claims (cross-subject push dedup) | `claims_service.apply_claim_push` |

- `ledger.resolve_transaction_row(...)` is the resolution half, NO store write — previews render
  exactly what a write would store.
- Every store is DB-backed (Alembic, `db/alembic/`) — `JsonStore` is retired; a new revision is
  **TIMESTAMP-named**, never a counter. Every money/Planning/trade choke is ONE DB transaction
  (`db.engine.locked_unit_of_work`).
- Boot migrations are fail-closed (`db/migrate.ensure_migrated()`), gated by the working-folder
  claim (`core/instance_lock.py`) running first. A database AHEAD of this checkout's Alembic
  scripts, BEHIND but still open in another process, or populated with no `alembic_version`
  stamp is refused untouched — never backed up, never restored (ADR
  `2026-09-06-database-ahead-of-code-is-refused-untouched`).
- **A live instance is migrated only by a caller that opts in** (`allow_live_instance=True` /
  `alembic -x allow_live_instance=1`) — the boot, `make setup`, `make db-upgrade`, `make seed`.
  Any other migration on `~/.open-net-worth`, the pointer's root, or `backend/data` is REFUSED
  before it touches the file, whatever the mode. A scratch run isolates with `ONW_ROOT` (or
  `DATA_DIR`+`CACHE_DIR`+`LOGS_DIR`) **and** `ONW_QA_MODE=1` — `ONW_DATA_DIR` is not a variable.
- Full inventory + rationale: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) / [`docs/adr/`](docs/adr/).

## Key invariants

One line each — full reasoning and the scar behind each is in
[`docs/INVARIANTS.md`](docs/INVARIANTS.md).

**Ledger**
- `category` is optional (`None` → "Uncategorized"); an unknown category RAISES, never coerces.
  Precedence at create: explicit > payee-default > `None`.
- Category ids are opaque (`cat_<12hex>`); `resolve_category_ref` accepts id or label.
- Account refs resolve by name too; ambiguity RAISES, naming every candidate.
- `kind=transfer` requires both legs, distinct and existing. Its figure and unit belong to the
  SAME leg — a missing destination amount is FX-derived, never the raw number relabelled.
- An impossible transfer rate (>10x `core.fx.known_rate`) is REFUSED at the choke. So are equal
  amounts across a KNOWN non-1:1 pair (`force_transfer_rate` bypasses; an unknown pair only warns).
- An account's opening is ONE fact, `opening_txn_id` — no `initial_balance` field. Every create
  requires an `initial_balance`; zero is valid and still mints today's Starting Balance row in
  the same DB transaction as the account and pointer.
- `delete_account` is a three-way choke — no LIVE refs deletes outright; the account's OWN opening
  (Starting Balance) txn as the sole live ref deletes both, atomically, no `force` needed; other
  live refs need `force=true`, cascading a soft+hard delete of every referencing txn. EVERY path
  (including "no refs") hard-deletes any STALE already-soft-deleted row first — no early return
  skips it — so a fully soft-deleted account never 500s on the accounts table's RESTRICT FK.
  Refused OUTRIGHT (any path, its own `account_net_worth_linked` 409 shape — never the
  transaction-count one, since `force=true` can't fix it) if the account is linked via
  `owned_assets.loan_account_id`, a live position, a live lot, or an open option contract — see
  `docs/INVARIANTS.md` and ADR `2026-09-06-force-delete-account-hard-removes-its-cascaded-transactions`.
  Outside that one exception, the Starting Balance transaction refuses delete/uncleared/edit/merge
  from either side.
- **Delete permanently** (`transaction_purge.purge_transactions`, `POST /api/transactions/purge`)
  is the one ERASE: hard delete + tombstones, audit cut to stubs, re-import guard keeps only the
  ref, `write_log` images nulled so no restore resurrects it. Refused while a lot/vest/income/
  owned-asset/commitment fact names the row. An account force-delete erases its cascade the same
  way (`erase_traces`). A plain delete stays soft (ADR
  `2026-10-17-delete-permanently-erases-a-transactions-contents`).
- Dedup identity tests the EMITTER, not the namespace — `email` has hundreds of senders, so two msgids are two payments only from ONE sender. Unresolved fails CLOSED.
- A DELETED identity is remembered (`rejected_identities`, movement-scoped, written at the delete choke) and
  EVERY create choke refuses it as `previously_deleted` regardless of `force` — only `resurrect` / a `create`
  decision books it back. Rollback ≠ reject: `batch_id` rollback, trade undo and merge tombstones write nothing.
- Payee normalization: `services/payees._normalize_payee()`, one source of truth.
- Seven account types are the ONLY classification axis (`cash, fixed_deposit, investment,
  retirement, receivable, credit, loan`); `kind`, band, spendability and budget membership
  derive from the type. Cash on hand = Σ `cash` — one function, one number everywhere;
  settlement cash (margin included) belongs to `investment`, never netted into cash. Dashboard
  net worth includes retirement accounts; the Assets breakdown groups their cash and vested
  holdings together, and `net_worth_excl_retirement` subtracts both from the full total.
- Unvested equity is EXCLUDED from net worth, reported separately as `unvested_base`.
- A vest books NO ledger transaction, whatever the grant's `tax_event` — it is paid in SHARES,
  so only the FMV lot is written; an income leg into the custodian account counted the vest
  twice (cash that never arrived + the shares). Tax at vest is information on the vest EVENT,
  read by `equity_report`. Pre-2026-09-22 events keep their `txn_id` and every reader honours it.
  A two-step vest (`release_required`) parks its units in the pending position with NO lot but
  WITH a basis: `cost_basis` is the quantity-weighted vest-date FMV of every still-parked vest
  (`equity_release.pending_basis`), re-derived at park, release and undo — a null basis blanked
  the P&L of the whole merged Holdings row, delivered shares included (2026-09-23).
- An owned asset's value is its newest valuation; its purchase point is derived from cost/cost_date —
  never stored, never the current value, none for policies; identity edits never write marks; a
  loan secures at most one live asset; equity is derived from the linked liability, never stored.
- Concentration, today's move and the invested denominator have ONE authority
  (`investments_book`: `project_invested` / `concentration_by_name` / `today_move`), and the
  benchmark is `profile.preferred_benchmark` — Intel, the risk engine and the pulse read them,
  never recompute (ADR `2026-09-25-concentration-today-and-benchmark-have-one-authority`).
  A diversified equity/bond fund is not a "name" for single-name concentration (ADR
  `2026-10-14-today-is-the-regular-session-on-every-surface`, whose "today" decision the owner
  reverted: the price shown follows the pre/post print, ADR `2026-09-01-extended-price-consistency`).

**Cross-screen facts**
- Overdue and due-in-30-days are ONE function (`commitments.due_summary`), the runway is ONE
  (`cashflow.runway_from_projection`), and the funding verdict is ONE client cache entry — the
  Dashboard, Commitments, Cash Flow and the daily briefing read them, never re-derive (ADR
  `2026-10-14-one-definition-per-fact-across-screens`; long form in `docs/INVARIANTS.md`).

**Retirement**
- ONE simulation: `services/montecarlo.py`, fed by `services/retirement_plan.py` (saved plan = profile
  FIRE settings + Outlook baseline; callers pass only the levers they change). The Outlook screen
  (`/api/plan/outlook`) and `simulate_retirement` read it — there is no engine in the browser, and
  no second one in the backend (`get_fire_projection` returns only the FIRE number and progress — no on-track verdict or projected age). Deterministic: same inputs ⇒ same numbers; `/api/plan/outlook` with
  no levers IS what Lucius's tool returns with no arguments.
- A monthly saving is its own stored fact (`profile.monthly_saving`), never income. Read it ONLY
  through `services.fire.monthly_saving()` (stated wins, else income − expenses). ADR
  `2026-10-14-one-retirement-engine-saving-is-a-fact`. What the LEDGER says is saved a month is
  `reports_rollup.trailing_saving()` (last 3 complete months) for the Outlook and the Dashboard
  rate — never a window's `net` divided by its months.

**Budgeting**
- Ready to Assign = cash − baseline − Σ envelope available (SIGNED, held_in_envelopes) − held
  outside envelopes − card spend not yet covered (ADR
  `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category`, restoring ADR
  `2026-07-02-balance-based-rta` + `2026-07-09`, one day after
  `2026-09-08-ready-to-assign-is-unassigned-cash` shipped). An envelope holds its own overspend
  until month-end — RTA does not move when a category goes red, cash or card; the carry clamps
  at 0 at month-end, so a still-negative envelope is charged to NEXT month's RTA instead of
  carrying as debt. Uncategorized/no-envelope CASH spend HOLDS THE SAME WAY, in a pseudo-envelope
  of its own with `assigned ≡ 0` (`held_outside_envelopes`, one per unlinked category + one for
  Uncategorized): RTA does not move at the spend, the hole resets at the roll and is charged to next
  month exactly once, and labelling the transaction before then makes the hold evaporate (ADR
  `2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end`, amending 2026-09-09's
  drain-immediately "one fix" after direct YNAB observation). Its ONE asymmetry with `_available`:
  an upper `min(0, …)` clamp — a bucket nothing can be assigned to holds HOLES, never surpluses,
  so an inflow under an unlinked label still raises RTA. "No bucket" is a CASH-only rule — an
  unfunded CARD spend is RTA-neutral whether or not its category is linked (a card never had cash
  to lose), surfacing only via the payment row's `underfunded` and touching RTA only when funded;
  the hold reads a CASH-ONLY activity fold (`_cash_activity_map` — the same PASS as `_activity_map`,
  but gated on the moving leg's account being in `ctx["cash_ids"]`, the exact set `_cash_as_of`
  sums; NOT the card coverage cap's accumulator, which admits `credit` legs). It is valued in
  CASH's terms too — the moving leg's own native amount at CURRENT rates, never a transfer's
  stamped `base_amount` (taken from whichever leg was already in base currency, so routinely the
  other leg at the bank's rate) and never a stamp whose `base_currency` no longer matches the
  base. The cash fold is
  NOT a subset of the full one: an INFLOW on an unlinked category is in it and not in the full
  map (`fold_no_bucket_only`), so a refund cancels the hold its own spend opened without becoming
  Activity on an envelope that doesn't exist — while a BLANK-category inflow is inflow-to-assign
  (`_INFLOW_CATEGORIES`) and folds nowhere. Which keys are even EVALUATED (`_unbudgeted_keys`)
  is the UNION of both folds, never the full one alone: a categorized transfer or investment sell
  arriving at a `credit` account folds POSITIVE and can net a real cash hole to ~0 on the full
  map (review, 2026-09-10). Covering an overspent envelope now COSTS
  Ready to Assign, dollar for dollar up to the hole's size — the genuine YNAB mechanic, opposite
  of what shipped for one day. `RTA ≤ cash − baseline` is RETIRED; the identity instead closes
  exactly (`RTA + Σ available(signed) + held_outside_envelopes + card_spend_not_covered +
  baseline == cash_as_of`).
  `cover_overspending` never proposes a card's payment envelope (coverage funds that row; an
  assignment double-funds it) and `_auto_cover_overspending` is capped at displayed RTA.
  `_op_auto_assign` re-derives each step from the live book for the COUPLED modes only
  (`_COUPLED_AUTO_ASSIGN_MODES` — a read/write-set proof about `month`, not a guess), so
  preview == end state.
- Deleting a category with `reassign=True` and an envelope-backed `reassign_to` CARRIES its
  assigned money there (`categories._carry_assigned_to_target` → `planning.add_to_assigned`,
  the `assign` choke): THIS month's figure lands on the target's envelope instead of releasing
  to Ready to Assign, so RTA does not move while the activity changes house. Any OTHER month —
  earlier or later alike — always releases; a delete is a forward-state edit, never a rewrite.
  The carry may never fail the delete: six cases release exactly as before — no `reassign`
  (a bare `reassign_to` is a defensive argument, not an instruction), no target, an unlinked
  (unbudgeted) target, a BOT-MANAGED destination (`envelopes.add_category` refuses that link at
  the root, but the write does not rest on it), an envelope that SURVIVES because other
  categories still reference it, nothing assigned, and a destination already more negative than
  the carry is big (`assign` refuses a negative figure; lifting it to zero would invent money).
  `carried_assigned` can never be non-zero beside `reassigned_to: None`.
- Deleting an envelope never LOWERS Ready to Assign, as a property over random books, with NO
  exception (`ΔRTA(m) = max(0, prev_carry) + assigned(m,E) + Σ_c max(0, cash_c) + Σ_c
  non_cash_c`, each term ≥ 0 by construction). `Σ_c non_cash_c` USED TO go negative when a
  `credit`-account leg (an archived card's spend, or a card-funded categorized
  transfer/investment buy) drained a LINKED envelope with nothing to offset it — fixed by ADR
  `2026-09-28-iterative-available-walk-and-uniform-card-coverage`: every such outflow is now a
  card-spend candidate whether or not a LIVE payment envelope resolves for it, so
  `_CARD_UNCOVERED_KEY` cancels the drain the same way a live card's does and `non_cash_c` is 0
  for it, unconditionally
  (`tests/test_delete_envelope_never_lowers_rta.py::test_archived_card_spend_is_no_longer_an_exception_to_the_property`).
  The formula's per-category term is `Σ_c max(0, cash_c)` over MEMBER categories held
  SEPARATELY, not `max(0, Σ_c cash_c)` over the envelope's summed activity — a MERGED envelope's
  members hold separately once detached, so spend 100 on one member + refund 100 on another
  moves RTA by +100 where the summed form wrongly says 0 (corrected in `services/envelopes.py`'s
  comment, review 2026-09-10).
- `sync_card_payment_envelopes()` is a GLOBAL, idempotent reconciliation over the WHOLE book, not
  scoped to one account — a caller that threads `side_effects` (`accounts.create_account`/
  `update_account`) gets each retirement's owning `account_id` (stamped from the accounts
  snapshot BEFORE the sync's own FK-clear step) and must filter by it: `retired_payment_envelopes`
  is reserved for a retirement THIS write's own account caused, `unrelated_retired_payment_envelopes`
  for one incidentally swept up (e.g. an archived card's stale envelope, never retired because
  archiving alone doesn't trigger a sync, caught by a LATER unrelated edit or account creation).
  Both are disclosed — never silently dropped, never falsely attributed to the wrong account
  (review 2026-09-10, follow-up fix to `47a6ebfd0`).
- A budget fresh start is ONE transaction through `apply_budget_change`: it zeroes the month's
  zero-based assignments and moves `budget_config.rollover_start`; the before-image is held
  server-side and Undo replays exactly it — never a client-supplied map. Its preview overrides
  `_build_context`'s floor and zeroes the same month (`floor_override=`,
  `zero_month_assignments=`), never writes-then-reverts (ADR
  `2026-09-08-budget-fresh-start-moves-the-floor-not-the-data` + its amendment).
- The RTA breakdown popover presents the SAME identity reframed as YNAB's literal flow terms
  (carried-in / inflow / assigned / cash overspending charged now — real month names
  interpolated, matching YNAB's own wording exactly) — a pure algebraic rearrangement, never a
  second computation (ADR `2026-09-13-rta-popover-flow-framing`); `cash_overspending(m) ≥ 0` is
  a proven theorem (`docs/INVARIANTS.md`), so the charge never contains card-caused overspend.
  The desktop rail and the fresh-start dialog keep the original stock framing unchanged.

**AI harness**
- Tool results are bounded AT THE TOOL — filter before you cap.
- Codex gets our tools as real (dynamic) tool calls that the HOST captures and runs — Codex never
  executes one; text `<tool_call>` is the fallback (ADR `2026-10-03-codex-tools-are-native-calls-the-host-runs`).
  The Codex turn stays OPEN across tool rounds — the call parks and the next round answers it
  in-thread; never re-paste the transcript into a fresh thread per round (that looped a report
  67× — ADR `2026-10-05-codex-turns-stay-open-across-tool-rounds`).
- NEVER add a caller-chosen `fields` param to a money-bearing read (defeats the rate lock).
  `run_sql_query` is the one deliberately-scoped exception.
- `fetch_page` (and every `<provider>_fetch_page`) runs unasked only for a URL the user typed
  or a web search returned (`ai/tools/_fetch_gate`); anything else is a card, or a refusal
  where no card can show. An Automation widens it only by its owner-set `fetch_hosts`, which
  Lucius can never write (ADR `2026-10-17-a-fetch-runs-unasked-only-for-a-url-the-user-or-a-search-supplied`).
- First run configures the machine; Lucius onboards the book (two separate predicates).
- Lucius reads the DB directly, read-only, via `run_sql_query` — the boundary is the connection
  (an allowlist authorizer), never a SQL parser. Denied = `SECRET_TABLES ∪ {sessions}`.
- One confirm mechanism for every tool: a gated tool stages its OWN call arguments under the
  `tool_use_id`; approval resolves server-side and re-runs the same handler.
- Every turn goes through `ai/turns.py`, never a bare `asyncio.create_task` (confirm gate fails
  open without it) — server-owned, not request-owned; a second call mid-turn enqueues.
- A transcript is a table of messages (`session_messages`), not a JSON column. NEVER
  `batch_alter_table("sessions")` — the CASCADE deletes every message.
- "Unread" is a finished turn after the session meta's `last_read_at` (`POST /api/sessions/read`),
  never browser storage — a read on any client clears every client (ADR
  `2026-10-03-conversation-read-state-lives-on-the-server`).
- A self-updating CLI (Claude Code / Codex) can silently change how tools reach the model — `make cli-smoke` is the opt-in LIVE regression of both transports (never in `make check`); the app also probes the configured CLI once per version at boot — [`docs/ai/CLI_REGRESSION.md`](docs/ai/CLI_REGRESSION.md).
- A file marked Private in Documents never reaches a model: every model-facing attachment read
  goes through `attachments.get_attachment_for_model`/`private_refusal` (fail closed); only the
  user switches Private off. An asset file with "Lucius can read" off IS Private — the same guard
  (ADR `2026-09-26-vault-is-a-view-over-existing-stores`).

## Connectors

- A connector is a row of `connectors` (settings + app-written state); its credentials are a row of
  `connector_secrets` — the only SECRET table of the pair. `connectors` is kept in a sanitised
  backup, so **nothing that can carry a credential** (an MCP `url`/`args`, an OAuth token, Telegram's
  chat map) may land in `config`/`state`; it is also SQL-denied to Lucius (raw server-chosen MCP
  text, personal settings), and every connector facade store is in `core/mention_guard`'s
  forbidden set. A sanitised backup and a QA clone also strip `RETIRED_SECRET_TABLES` (`mcp_clients`),
  and a restore refuses a backup from another Alembic revision. Price-source telemetry stays in
  `runtime_state` (`market_data_observability`) — only quota and circuit are connector `state`.
  What a kind IS — secret fields, pausable, notify channel, copy, tools, remove hook — lives in
  `services/connector_catalog.py`; never add a connector list anywhere else. Data access is
  `db/connector_store.ConnectorStore`. Connectors read no environment variables.
  ADR `2026-10-10-connectors-live-in-a-connectors-table-and-secrets-in-connector-secrets`.

## AI tools

One `@tool`-decorated handler in `ai/tools/<group>.py` — the registry auto-collects. Use the
**`add-tool`** skill. Naming is enforced and **FINAL — no aliases**; a rename is a data migration
(`backend/scripts/tool_renames.py`). Every tool is explicitly write-classified (`mutates=`) — a
`get_`/`list_` prefix proves nothing. A connector-scoped tool carries `connector="<id>"`, gating
discovery and resolving its `tool_permissions` owner; untagged = the `core` bucket. Conventions:
[`docs/ai/TOOL_CONVENTIONS.md`](docs/ai/TOOL_CONVENTIONS.md).

## Coding standards

Functions ≤ ~60 lines, files ≤ ~400 lines. Typed exceptions from `core/errors.py`; structured
`logging`, never `print`. No comments unless the WHY is non-obvious. CSS: never introduce a
`className` without its definition in `frontend/src/index.css` (`npm run lint:classes`).
Screen data: read through `useQueryCache` — it revalidates on every tool write, from any surface
(`dispatch` → `GET /api/events`, ADR `2026-09-29-every-write-refreshes-every-open-screen`); a
screen holding fetched data in local state must add `useDataVersion('*')` to its fetch deps.

**Clock:** "today" and time of day for the user come from `core.user_time` (`user_today()` / `user_now()`, the profile's IANA `timezone`) — never `date.today()` / `datetime.now()`: a hosted container runs UTC. Market sessions use their exchange zone; stored timestamps stay UTC/server clock.

## Proportionate workflow

Use the smallest workflow that gives credible evidence for the requested change.

- Docs/non-behavioral changes: relevant formatting or docs checks only.
- Behavior/bugs: focused tests; add a regression test when practical.
- UI/E2E QA: only for material visual, interaction, accessibility, responsive, or user-flow
  changes—never for backend-only work. When warranted, use isolated ports 8090–8099 via `make
  qa-e2e`/`make qa-cu`; never 8080.
- Broad suites/`make check`: only for cross-cutting, high-risk, release, or merge-ready changes.
  Use **`write-test`** and **`run-app`** for test conventions and commands.
- Throwaway probes/stress scripts: the timeout must kill the PROCESS itself
  (`subprocess.run(..., timeout=N)`), never `( cmd | tail ) & kill $!` — `$!` is the subshell,
  so a hung child is orphaned (three PDF probes once ran 31h at load 37, starving :8080).
- Reviews: security for auth/secrets/money movement/untrusted I/O; code for medium/high risk,
  large diffs, or shared invariants; UI design for new screens, major redesigns, or IA changes.
- Agents: no minimum count. Add agents only for useful independent work or justified specialist
  review; one agent is enough for a small change.
- Bounded work needs no PRD, RFC, formal plan, worktree, or approval ceremony unless requested.

**User-visible change ⇒ docs in the same commit.** Update `docs/product/` (and README /
desktop/README when install or desktop behaviour changes); `docs/docs-map.json` maps code areas to
pages. The `docs-impact` commit-msg hook (`make install-hooks`) blocks a user-facing commit without a
doc change unless the message carries `Docs: none — <reason>`.

Done means the requested behavior works and risk-proportionate checks pass. Report performed and
unperformed verification exactly. Update docs/ADRs only for changed contracts, workflows, or
durable decisions. Use synthetic data, never a real row. Report unrelated failures; do not fix
them without scope.

## Personal and financial data never enters the repo

This repo is built mostly by AI agents next to a real book, so the guard is mechanical, not
a matter of care: `backend/scripts/privacy_guard.py` refuses databases, bank exports, statement
PDFs/CSVs and screenshots outside the fixture/product-art folders, real home paths, and the
owner's private denylist (kept outside the repo: `~/.config/open-net-worth/privacy-denylist.txt`
locally, the `PRIVACY_DENYLIST` secret in CI). It runs in the pre-commit hook, as a Claude Code
hook on every agent `git commit` / `git push` / `gh pr create` (`.claude/settings.json`), and as
the CI `privacy` job on every PR and push — with `pii_lint` (ID/phone/account shapes) and gitleaks.
Never bypass it (`--no-verify`, editing the allowlists to get green) and never "fix" a finding by
renaming the value: replace real data with synthetic data. QA evidence (screenshots, statements,
recordings) goes in gitignored scratch folders, never the tree.

## Docs map

| Area | Doc |
|---|---|
| Stores, fields, money math | [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) |
| Feature docs | [`docs/product/`](docs/product/) (served in-app at `/docs`) |
| Budgeting model | [`docs/PLANNING_MODEL.md`](docs/PLANNING_MODEL.md) |
| Invariants, long form | [`docs/INVARIANTS.md`](docs/INVARIANTS.md) |
| AI tools | [`docs/ai/`](docs/ai/) |
| Backend layers | [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) |
| Decisions | [`docs/adr/`](docs/adr/) |
| Windows | [`docs/WINDOWS.md`](docs/WINDOWS.md) |

## Project structure

```
backend/
├── core/          # Leaf — stdlib only. Paths, FX math, security, IO seams, errors
├── models/        # Pydantic domain schemas — leaf beside core/
├── db/            # DB-backed stores — all data access; never raw json.load
├── services/      # All business logic and monetary math
├── api/           # Thin routers — validate → one service → response
├── ai/            # Assistant, @tool registry, memory, checkpoints
├── mcp/           # MCP server (inbound) + external MCP client manager
└── connectors/, sandbox/, ingest/  # chat connectors, code sandbox (leaf), IMAP client

frontend/src/
├── components/    # Tab UIs, chat panel, design system (ui/), help/
├── hooks/         # Data hooks (usePortfolio, useBudget, useChat)
└── state/, types/, utils/
```

## Security-critical areas

`/security-review` before shipping anything in this list.

- Terminal (`/api/terminal`, a shell): every gate lives in `services/terminal.py` and is checked on the
  WebSocket itself — gateway mode or explicit opt-in, same-origin, a session not an API token, fresh sign-in,
  one at a time; the child env is `core.child_env.scrubbed_environ` (shared with the CLI providers). ADR
  `2026-10-03-terminal-is-a-gated-shell-with-the-providers-env-filter`.
- MCP inbound: Bearer-only auth. Nonce staging fires server-STORED args — approve-then-substitute
  is structurally impossible.
- External MCP: `tool_permissions` is the SINGLE authority on whether a call fires — no
  unconfigurable floor, for every tool in the app. `discovered_tools` is what WE observed in a
  handshake, never a client-supplied value.
- A stdio MCP connection persists for the app's LIFETIME, one child per server, serialized by a
  per-server lock; its stderr is a pipe that MUST stay drained (`StderrTail`).
- `always_allow` means allow everywhere, including unattended — never silently degraded.
- A connector's `endpoint` is a credential — model-facing reads project `redact_endpoint`, never
  the raw field; every server-chosen string is sanitized.
- The operator may read a connector secret back; the model never may
  (`services/connector_secrets.py`, not imported under `ai/`/`mcp/`).
- Chat channels: HMAC/Ed25519 first, `operator_trust` default false, IDOR guard on confirm, 10
  turns/60s rate limit per sender.
- Sandbox: 3 kill switches, off by default — NOT a filesystem jail (no chroot/seccomp/uid-drop).
  Auth: PBKDF2 + HMAC, 5 failures/15min → 429.
- Gateway mode (`ONW_GATEWAY_TOKEN`, `api/gateway.py`): an outermost ASGI middleware 403s every request
  lacking `X-Onw-Gateway-Token` (constant-time) except `GET /api/health`; a valid token plus exactly
  one `X-Onw-Gateway-Auth: session` sets `request.state.gateway_authenticated`, which `require_auth`
  and `/api/auth/status` honour. Any other value (`none`, missing, garbage) FAILS CLOSED to
  `gateway_passthrough`: the app authenticates it itself (Bearer/cookie), `_is_loopback` is False
  for it and the stored `auth_disabled` never opens it. The token must be >=32 chars or boot
  refuses. `X-Onw-User-Email` is display-only.
- Password: a set password is changed only with the correct `old_password` — loopback never waives it. Two
  ways around a forgotten one, no others: `onw reset-password` (`AuthStore.replace_password`, hash + session
  key in one write) and the desktop app's `desktop_authenticated` session header (ADR
  `2026-10-04-a-forgotten-password-resets-only-from-the-desktop-app-or-the-terminal`).
- Public demo (`DEMO_MODE`): every visitor is the operator, and needs NO password — only
  `demo_open_access_active()` (`DEMO_MODE` AND `DEMO_OPEN_ACCESS`, env-only, never a stored setting
  or header) opens `require_auth`; either alone stays gated. `api/demo_lockdown.py` makes WRITES an
  allowlist (a route nobody classified is refused; `tests/test_demo_route_classification.py` fails on
  an unreviewed mutating route) and locks secret reveal, AI-credential writes, the Builder, MCP,
  extensions, skills, Automations, backups/reset/whole-book import, the Terminal, `/api/move/*`,
  auth state and the API token. Every model call is token-checked where its provider is built
  (`ai.assistant._provider_from_resolved` → `demo_quota`) and pinned there to the instance model at
  ≤ medium effort, the tools in `ai.tools.registry._DEMO_REFUSED_TOOLS` are refused at dispatch, the visitor key is the address the
  `DEMO_TRUSTED_PROXY_HOPS`th proxy appended (never the left of `X-Forwarded-For`), conversations
  are owner-scoped (`services/demo_visitors`); seeded Automations still run, charged to the instance ceiling. A new route or
  store that does any of those joins the list (ADRs `2026-10-05-a-public-demo-visitor-is-an-operator-who-cannot-reach-past-the-book`,
  `2026-10-07-the-public-demo-needs-no-password`, `2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard`).
- SSRF guard (`assert_public_url`) + DNS-rebind-pinned fetch (`pinned_urlopen`).
- Problem reports ("Report a problem") are the only thing the app ever sends about itself, so they are
  strictly user-initiated: every surface only OPENS the previewed dialog, Send posts exactly the edited
  preview, nothing is queued or retried. Desktop builds its own report in the main process
  (`problem-report.ts`); the browser uses `api/problem_reports.py`. Redaction exists twice
  (`core/report_redact.py`, `desktop/src/main/report-redact.ts`) and BOTH run
  `tests/fixtures/problem_report_redaction.json` — change a rule in all three. ADR
  `2026-10-04-problem-reports-are-user-initiated-previewed-and-redacted-twice`.
- Outbound TLS: the trust store is decided ONCE at boot (`core.pinned_http.ensure_default_trust_store`,
  certifi fallback for an interpreter with no CA bundle); every stdlib `urlopen`/`build_opener`
  outside `core/pinned_http.py` passes `context=https_context()` — a repo guard test fails otherwise
  (ADR `2026-09-21-outbound-https-trust-store-is-one-boot-decision`).

## Core seams (inversion points)

| Seam | File | Purpose |
|---|---|---|
| Write hook | `core.io` | `db` triggers checkpoint without importing `ai` |
| Desktop alert feed | `services/alert_feed.py` | Intel-story alerts (price/crash/watch/analysis) reach the desktop banners by READ — never as a notify channel (its delivery result would defeat `push_retry`'s zero-delivery retry) and never as notification-log rows (Intel is their one in-app surface) |
| Headless turn / Automation runner | `core.headless_turn` / `core.automation_runner` | Run a turn / Lucius without importing `ai` |
