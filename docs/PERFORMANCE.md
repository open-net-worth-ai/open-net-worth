# Performance — why the app was slow, what fixed it, how to measure it

**Status: current truth as of 2026-08-12.** Written at the close of the 2026-08-11→12
overnight investigation (owner + Claude). Read this BEFORE attempting any performance work —
especially before proposing a cache.

## The verdict in one paragraph

There was never one cause. Six independent layers stacked, so every earlier fix — all real —
was masked by the layers above it ("we fixed a lot and nothing got faster"). The app's numbers
after unstacking them, measured on real data with ALL derived-value caching disabled:
40–230ms per aggregate endpoint serial, **3.6s worst-case** for a fully-cold 18-endpoint
concurrent burst (down from 8–9s), and zero staleness by construction.

## The six layers (each one measured, not guessed)

### 1. Fan-out × recompute-the-world
A page load fires ~28 API requests; each endpoint independently rebuilt its aggregates from
the full ledger plus the same shared foundations (price map, FX, hydration context). Nothing
shared across the wave. *Still partially open — see backlog #2 (composite pages).*

### 2. The GIL multiplier (the "10s refresh" itself)
All heavy handlers ran concurrently in the threadpool. Measured: 18 endpoints **2.7s serial,
6.8s concurrent** — concurrency made the same work 2.5× slower, and every request's latency
inflated to the whole burst's wall (`net-worth/stats`: 172ms alone → 6.8s in the burst).
A client-side sweep found concurrency ~3 optimal (1.0s), 18 pathological (8–9s).
**Fixed:** `read_cache._gated_compute` — a `BoundedSemaphore(3)` on expensive computes
(nested-call bypass, 8s liveness valve). ADR
`2026-08-12-read-cache-leader-computes-gated-to-three`.

### 3. Caches that couldn't work — and one that lied
- Backend `read_cache`: its global epoch (`PRAGMA data_version`) was invalidated every ~4s by
  the automation event-outbox's stage/ack commits (`automation_runtime.__outbox__` —
  measured 21 invalidations / 90s), so it ran permanently cold; hits still paid an
  80–260ms `deepcopy`; per-arg keys recomputed variants (`build_projection` ×4/page).
- Frontend `queryCache`: a 30s default `staleTime` served **stale money numbers** across tab
  switches after an edit (the starting-balance incident). Every explicit `staleTime` in the
  codebase was already `0` — call sites had been hand-patching the default away one
  staleness bug at a time.

**Fixed (owner call):** memoisation OFF by default (`ONW_READ_CACHE=1` to re-enable), frontend
default `staleTime` 0 — cached data only ever bridges an in-flight refetch. ADR
`2026-08-12-no-derived-value-caching-by-default`.

### 4. Network calls inside the money path (the "fine at noon, awful at night" mechanism)
Live price fetches (Yahoo/CoinGecko) ran inside request handlers, holding the single-flight
lock every money read queues behind. Providers throttle progressively as the day's usage
accumulates; measured at 00:09: one **35.3s** fetch = a 35.7s `/api/portfolio` plus ~30s
stalls on unrelated endpoints (the read-cache waiter timeout signature). **Fixed:**
interactive callers pass `allow_stale=True` — last-known quotes immediately, refresh in a
background thread; scheduler/monitor/AI-tool keep blocking-fresh. ADR
`2026-08-12-provider-fetches-never-block-interactive-requests`.

### 5. Hot-path defects (each found by profiler/log, not intuition)
- `build_projection` read the ledger 18× (fixed, `b2e04d3e`); `net_worth_history` rescanned
  it 224× / 409k iterations (fixed, same lane).
- 8 event-loop blockers found by `core/loop_watch.py` (fixed, commits `3c348f6f`…`160e47a9`).
- **No HTTP compression at all** — 3.3MB transcripts, 409KB session lists, raw. Fixed:
  `_SelectiveGZip` in `main.py` (3.03MB→1.21MB, 409KB→44KB, 265KB→71KB; SSE excluded).
- Chat: whole-transcript rehydrate per open (0.8–1.9s, ×2 via load+reconcile), 154 markdown
  bubbles in one synchronous React commit (5–10s frozen tab switch on iOS). Fixed:
  `?tail=` fetching + last-id reconcile (`chatStore.shouldAdoptRemote`), windowed render +
  upward infinite scroll (`transcriptWindow.ts`), `content-visibility` lazy bubbles (last ~8
  eager so the bottom-jump lands on real heights).
- The N+1 batch is CLOSED (2026-08-12, commits `dc3348d2`/`530a3499`/`8acc0efb` — see the
  progress note under the benchmark table): `as_of_maps` (was 39–75k row-by-row `fetchone`),
  `net-worth/stats` (241→65 statements), commitments/coverage (84→29), the reviews
  sweep/enrich double parse; `alert-channels` attributed and demoted (247 statements = 15ms).
- Transcript payload elision + backward paging: CLOSED (2026-08-12 ~06:00). Measured first:
  tool results were **85–95% of every heavy tail=30 payload** (worst: 2.27MB of 2.4MB; single
  `fetch_page` results to 767KB) — NOT the digest text bodies the earlier theory blamed
  (biggest text: 132KB, one-off). The list endpoint now strips any tool result/input over
  4KB (`api/chat._elide_tool_payloads` → `resultElided`/`inputElided` markers); the pill
  fetches `GET .../tool-results/{tool_use_id}` on first tap (~10ms for the 767KB block —
  the UI only ever showed results inside that tap-to-open pane, so nothing visible changed).
  `before=<counter>` pages history backwards from the earliest loaded message ("Show
  earlier" used to dead-end at the fetched tail; positional ids make the counter the exact
  server offset). Eight heaviest conversations: **7.7MB → 352KB total (22×)**; worst single
  open 386KB → 18KB on the wire, 0.19s → 0.06s.
- `/api/uploads/{id}/image`: shipped WITH `Cache-Control: private, max-age=1y, immutable`
  the day the thumbnail feature landed (`e411d1e9`) — the pre-ship warning above did its job.
- **Day-after log audit (2026-08-12, 10.8k real requests):** the fixes held (stats at 67
  statements, not 241), and the tripwires caught the next layer —
  `/api/planning/summary` at **727 statements/request** (661 from `_assigned_amt`:
  `_available`'s carry walk did one `month_map()` SELECT per month × envelope node; fixed
  `18e54a7d`, one `all_map()` per context → **66 statements**, pinned by
  `tests/test_planning_query_efficiency.py`), and the scheduler price check's sync
  preamble (`read_rows()` = a full hydrated-portfolio build) hard-BLOCKING the event loop
  >500ms per tick (fixed `143d713f`, one `to_thread` hop). The day's dominant slowness was
  environmental and now MEASURED: **140 `--reload` restarts** (one loop_watch boot line
  each; each also empties the in-memory quote cache — why `/api/prices` showed p95 5.4s
  despite stale-serving's p50 121ms) plus **120 loop_watch "STARVED, not blocked — GIL
  contention from concurrent CPU" diagnoses** (agent test suites beside the live server).
  Still open, named: `/api/briefing` occasionally runs a 146s generation inside a request;
  `/api/returns` ~2.2s p50 of honest compute; `enrich-sectors` still on the page-load path
  (individual 30s computes); and one live wave ran `build_projection` FOUR times — the
  composite-pages case (backlog #2), observed in production.

### 6. Environmental amplifiers (not the root cause, but real)
Agents editing `backend/` under the owner's `--reload` server: **65 cold restarts in 2h**
(22:00–24:00 on 2026-08-11) — first requests after each restart measured 4–15s. Agent
sessions compete for CPU (a full pytest run beside the live server, twice, by two different
agents — stop doing this). The phone loads the app as 350+ unminified dev modules through a
days-old vite. Rules: agents work in worktrees; the owner's daily server runs without
`--reload`; ship the phone a production build.

## The backlog, in order

1. **Endpoint hygiene** — the N+1s above. Small, testable, each shaves honest compute.
2. **Composite per-page endpoints** — compute a page's shared foundation once per wave
   (extend the `hydration_context` pattern). Overview first. This is the biggest remaining
   UX lever now that caching is off.
   **Shipped 2026-11-21 for the Dashboard's heavy reads** as a generic query endpoint, not a
   page DTO: `GET /api/query` (named sources, streamed) + `core/request_memo` (one raw read of
   the book per request). Same seven answers: 4.1 s CPU as separate GETs → 1.5 s as one query.
   ADR `2026-11-21-a-screens-heavy-reads-are-one-query-from-one-read-of-the-book`.
3. **Worker-process split** — move price polling/RSS ingest out of the serving process
   (cheap half). Moving LLM turns is expensive surgery; only on fresh loop-watch evidence.
4. ~~**Transcript elision + real pagination**~~ — DONE 2026-08-12 (see layer 5: 22× on the
   heaviest conversations, fetch-on-tap tool details, `before=` paging).
5. **Caching, redesigned deliberately** — only after 1–2, if still wanted: per-table epochs
   (so outbox chatter can't invalidate money reads), no deepcopy on hit, dedup across arg
   variants. Re-enable per-surface with evidence, behind `ONW_READ_CACHE`.
6. **Incremental aggregates at the write chokes** — the eventual architecture, WHEN the
   ledger is ~10× today's size. Demoted from "core fix" by measurement: recompute-on-read
   is 40–230ms at 2k transactions once the scan bugs are gone.

## Benchmark baseline — `backend/scripts/perf_bench.py`, warm server, 2026-08-12 ~03:30

| Wave | Wall | Tail |
|---|---|---|
| overview | 2.23s | `net-worth/stats` 2.22s |
| plan | 4.50s | `forecast-scorecard` 2.71s (238q), `net-worth/stats` 2.11s |
| investments | 0.99s | `portfolio/analytics` 0.98s |
| chat-open | 0.19s | messages tail=30, 291KB |

Same waves fired seconds after a `--reload` restart: overview **20.4s**, plan **13.8s** — the
measured cost of editing `backend/` under the owner's live server. Owner-experienced starting
points before this investigation (browser, 2026-08-11): Accounts 13.6s, Commitments 37.5s,
Plan/Outlook 12.0s, Lucius tab-switch 5–10s frozen. Beat the table above or it didn't happen.

Progress against baseline: **plan 4.50s → 2.57s** (2026-08-12 ~04:15, scorecard
single-ledger-read fix — scorecard serial 463ms → 130–290ms). Then the full N+1 batch
(2026-08-12 ~05:30, rates/ledger-load threading through stats, commitments/coverage and the
reviews sweep): **overview 2.23s → 1.27s, plan → 1.78s**; serial: stats 241→65 statements
(215ms), commitments 84→29 (69ms), reviews one ledger parse (27ms). `alert-channels` was
attributed and DEMOTED: 247 statements but only **15ms** wall (config-store accessor
chattiness — each field accessor re-reads its namespace row; fix opportunistically when
touching those 17 stores, it is not a wall-time problem).

## How to measure (reuse this, don't re-derive it)

- Per-request server truth: `PERF-REQ` log lines (`main.py` middleware) — total/db/compute
  per request. Loop stalls: `core/loop_watch.py` warnings with sampled frames.
- The decisive reproduction: fire the page-load endpoint set concurrently with plain curl —
  no browser needed. 18-endpoint list + harness pattern: session scratchpad `burst.sh` /
  `eps.txt` (recreate trivially: `xargs -P 18 -I{} curl -s --compressed -o /dev/null
  "http://127.0.0.1:8080{}" < eps.txt` and time the wall).
- Serial-vs-concurrent comparison is the GIL-thrash detector: concurrent wall ≫ serial sum
  ⇒ execution-model problem, NOT endpoint problem.
- Single-flight/cache accounting: instrument `read_cache.cached` before importing the app
  (pattern: scratchpad `instrumented_burst.py` — per-key leader ms / hits / waits).
- Epoch churn: poll `PRAGMA data_version` on a read-only connection for 90s.
- Naive before/after diffs of money endpoints are WORTHLESS while prices/FX move — compare
  under frozen inputs (see the proj_compare/nwh_golden harness pattern) or byte-identical
  output proofs.

## Wrong theories, so nobody chases them again

- "SQLite is slow / the DB is too big" — reads are single-digit ms; the 307MB is
  evidence/sessions, not the ledger (2k rows, 2.3MB).
- "It's the Vite dev server" (for the desktop wall) — the curl burst reproduces the wall
  with no browser. (Dev serving IS a real tax on the phone.)
- "GIL contention alone" — categories at concurrency 30 held 82ms; the killer was
  concurrent *heavy* computes + waits-behind-leaders, which the gate + cache-off resolved.
- "SQLAlchemy pool exhaustion" — checkout measured 0.04ms.
- "It got slow because the sessions moved to the DB" — the migrated load path measures
  0.1s warm; the chat slowness was payload size + render, both predating the migration.
