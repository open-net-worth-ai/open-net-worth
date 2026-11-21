---
title: A screen's heavy reads are one query, answered from one read of the book
status: Accepted
date: 2026-11-21
deciders: repo owner ("redesign how the dashboard loads… one endpoint… like Grafana") + Claude agent
---

# A screen's heavy reads are one query, answered from one read of the book

## Context

A Dashboard load fired ~39 requests at once. The heavy ones (net worth, its history stats,
portfolio analytics, commitments summary + coverage, the planning month, the investments brief)
each rebuilt the same foundation from scratch: the whole transaction table JSON-decoded 16 times
per load, the holdings context 19 times, the price maps ~20 times. Measured 2026-11-21 on the
owner's 2-vCPU server: ~7 s to settle, the slowest four at 3–7 s each — none slow on its own;
every request inflated to the whole burst because they shared two vCPUs and the GIL.
`docs/PERFORMANCE.md` already named the fix as backlog #2 (compute a page's foundation once).

Constraints: no derived money number may be served from a cache
(`2026-08-12-no-derived-value-caching-by-default`); every fact has ONE authority
(`2026-10-14-one-definition-per-fact-across-screens`); Dashboard widgets must stay standalone
(`2026-07-31-overview-aggregates-are-server-computed`).

## Decision

1. **One query endpoint, Grafana-style.** `GET /api/query?q={"queries":[{refId, source,
   params}]}` answers named SOURCES (`services/data_query.SOURCES`). Each source is the exact
   service call its own GET route makes — the batch is a cheaper way to ask, never a second
   computation (a test pins batch == route for every source). It answers in one thread, in
   request order, identical queries once, and STREAMS NDJSON so each widget fills as soon as its
   answer exists. A bad batch is refused whole (422); a failing source fails alone.
2. **One read of the book per request: `core/request_memo`.** Inside the batch, the raw store
   reads (`transactions.all` / `query_txns`, `accounts.all`, every `PayloadTableStore.all`,
   `statements.all`, the price maps, `positions.hydration_context`) happen once. Nothing outlives
   the request, so this is not a cache in the 2026-08-12 sense. Callers get their own copies of
   row lists. Any commit in the same context clears the memo (an engine `commit` listener), and so
   does a fresh live price. Outside an open memo every call computes exactly as before — the
   scheduler, Lucius's tools and every other route are untouched.
3. **The client batches by tick, not by screen.** Each heavy hook keeps its cache key (its GET
   URL) and only its fetcher changes: it queues `{source, params}` in
   `hooks/serverState/dataQuery.ts`. Everything queued in one tick goes out as one request — a
   Dashboard mount, and equally the post-write revalidation that refreshes every mounted read at
   once. Widgets stay standalone; every screen using these hooks shares the batch. If the batch
   fails, each query falls back to its own GET.

## Consequences

- Same seven answers, measured on a copy of the owner's book (M-series Mac, warm): **4.1 s CPU
  as separate GETs → 1.5 s as one query** — before the `query_txns` scoping landed.
- The live portfolio (`GET /api/portfolio`) stays its own request: it waits on Yahoo/CoinGecko,
  and one network wait in an in-order batch would hold up every widget after it.
- Adding a heavy read: add a `SOURCES` entry (same service call as its route) and its client name
  in `DataSource`, then point the hook's fetcher at `querySource`.
- Sharing raw reads ACROSS requests stays deferred — the owner's call (PERFORMANCE.md #5).

## Alternatives considered

- **A Dashboard-shaped composite DTO** — one payload holding every widget's fields. Rejected: it
  is a second definition of each fact, and it couples widgets to a screen.
- **Request-scoped memo only, keeping 15 GETs** — saves duplicate work inside one request, but a
  Dashboard's duplicates are ACROSS requests; the GIL contention stays.
- **Cross-request cache with per-table epochs** — the biggest win, but it is the deferred caching
  decision; not taken without the owner.
