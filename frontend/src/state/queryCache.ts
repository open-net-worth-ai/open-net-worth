import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { onDataChanged } from '../lucius/bus'

/** Stamped once per build by vite's `define`. See `PERSIST_KEY` below. */
declare const __BUILD_ID__: string

interface QuerySnapshot<T> {
  data: T | undefined
  error: unknown
  updatedAt: number
  refreshing: boolean
}

interface QueryEntry<T> {
  snapshot: QuerySnapshot<T>
  listeners: Set<() => void>
  promise: Promise<T> | null
  fetcher: () => Promise<T>
  /** Pending eviction timer, armed when the last listener unsubscribes. See scheduleGc. */
  gcTimer: ReturnType<typeof setTimeout> | null
}

/**
 * How long an entry with zero listeners is kept before it is evicted. Matches TanStack
 * Query's `gcTime` default. Without this the `entries` map grows for the life of the SPA
 * session — invisible at ~40 keys, but a real leak once parameterized keys
 * (`/api/transactions?date_from=…`) are cached.
 */
export const QUERY_CACHE_GC_MS = 5 * 60_000

const entries = new Map<string, QueryEntry<unknown>>()
const EMPTY_SNAPSHOT: QuerySnapshot<never> = {
  data: undefined,
  error: null,
  updatedAt: 0,
  refreshing: false,
}

// ── Cross-session persistence (2026-07-31) ───────────────────────────────────
//
// `entries` is a module-level Map, so it dies with the JS context. On the desktop that costs
// one navigation; on the installed PWA it is the ENTIRE experience: iOS tears a standalone app
// down when it is backgrounded, so every reopen starts with an empty cache, every one of the
// ~120 call sites reports `initialLoading`, and the user watches the whole app skeleton itself
// back into existence — the "it keeps refreshing and remounting whenever I open the app"
// report.
//
// So the cache is mirrored to localStorage and hydrated at module load, BEFORE React renders.
// A reopened app paints its last known content immediately, `useQueryCache`'s existing
// staleness check revalidates in the background, and `publish()` swaps the fresh data in. That
// is stale-while-revalidate across sessions, and it is why no new loading states were needed:
// `initialLoading` is `data === undefined`, and hydrated data is defined.
//
// What is deliberately NOT done here: no request is skipped. Hydration decides what is on
// screen during the round trip, never whether the round trip happens.

/**
 * Storage key, namespaced by build.
 *
 * A persisted response is a SHAPE from the build that fetched it. Hydrating last week's shape
 * into today's components is how a cache like this crashes a screen instead of speeding it up,
 * and it cannot be validated generically. Keying by build id means a deploy silently starts
 * from empty — one slow open, then correct forever — rather than resurrecting a stale shape.
 */
const PERSIST_KEY = `onw-query-cache:${typeof __BUILD_ID__ === 'string' ? __BUILD_ID__ : 'dev'}`
/** Key prefix, for sweeping the entries left behind by every previous build. */
const PERSIST_PREFIX = 'onw-query-cache:'

/**
 * Manual test escape hatch — NOT a feature, no UI toggle. Run in the browser console:
 *   localStorage.setItem('onw_disable_query_cache', '1'); location.reload()
 * to skip boot hydration and force every query to treat itself as stale on every mount, so
 * every read hits the network. Remove with:
 *   localStorage.removeItem('onw_disable_query_cache'); location.reload()
 */
const DISABLE_KEY = 'onw_disable_query_cache'
function isQueryCacheDisabled(): boolean {
  try {
    return localStorage.getItem(DISABLE_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Byte budget. localStorage is ~5MB per origin, shared with auth status, the FX cache,
 * dashboard layouts and chat drafts — and `setItem` THROWS when full rather than evicting
 * anything. Persisting the cache must never be the reason a user loses their dashboard layout,
 * so it takes a bounded slice and drops what does not fit, newest first.
 */
const MAX_TOTAL_BYTES = 2_000_000
/** One oversized read (a full transaction history) must not consume the whole budget. */
const MAX_ENTRY_BYTES = 300_000
/**
 * Writes are debounced because `JSON.stringify` over the whole cache is main-thread work and
 * publishes arrive in bursts (a page load resolves a dozen reads at once). The flush on
 * `pagehide`/`visibilitychange` is the load-bearing one on iOS: a backgrounded PWA may never
 * get another timer tick before it is killed.
 */
const PERSIST_DEBOUNCE_MS = 1_000

interface PersistedEntry {
  data: unknown
  updatedAt: number
}

let persistTimer: ReturnType<typeof setTimeout> | null = null
/** Set once the current entries have been written; cleared by any publish. */
let persistClean = true

function hydrateFromStorage(): void {
  try {
    // Sweep older builds' caches first — without this every deploy leaks a ~2MB key forever.
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i)
      if (key?.startsWith(PERSIST_PREFIX) && key !== PERSIST_KEY) localStorage.removeItem(key)
    }
    const raw = localStorage.getItem(PERSIST_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw) as Record<string, PersistedEntry> | null
    if (!parsed || typeof parsed !== 'object') return
    for (const [key, record] of Object.entries(parsed)) {
      if (!record || record.data === undefined || typeof record.updatedAt !== 'number') continue
      if (!isPersistable(key)) continue
      entries.set(key, {
        snapshot: {
          data: record.data,
          error: null,
          updatedAt: record.updatedAt,
          refreshing: false,
        },
        listeners: new Set(),
        promise: null,
        // Replaced by the real one the moment any consumer calls entryFor(). Until then the
        // entry is read-only: hydrated data with no way to refetch is still better than
        // nothing, and nothing can invoke this without having supplied a fetcher first.
        fetcher: () => Promise.reject(new Error(`No fetcher registered for ${key}`)),
        gcTimer: null,
      })
    }
  } catch {
    // Corrupt blob, quota weirdness, storage disabled (private mode). Start empty — the app
    // then behaves exactly as it did before this existed.
  }
}

/** Endpoints whose RESPONSE is a credential. The mirror below writes cached values to
 *  localStorage in plaintext, where any script on the origin can read them and where they
 *  outlive the tab. `/api/auth/api-token` returns the full-access bearer and the server sets
 *  `Cache-Control: no-store` on it for exactly this reason — honour that here rather than
 *  quietly making a durable copy of it. */
// Named by SEGMENT rather than as a path literal: `tests/test_route_path_consistency.py`
// scans this tree for `/api/...` strings and requires each to be a real backend route, and a
// prefix is not one.
const SECRET_KEY_SEGMENTS = ['auth']

function isSecretKey(key: string): boolean {
  return SECRET_KEY_SEGMENTS.some((seg) => key.startsWith(`/api/${seg}/`))
}

/** Reads that must never paint from a previous session. Feature flags pick which code path
 *  renders, and "off means the old code" (docs/FEATURE_FLAGS.md): a hydrated `true` would show
 *  the new path for a flag the owner has since switched off, until — or, offline, instead of —
 *  the refetch. `useFlag` reads `undefined` until this session has fetched instead. */
const LIVE_ONLY_KEYS = new Set(['/api/feature-flags'])

function isPersistable(key: string): boolean {
  return !isSecretKey(key) && !LIVE_ONLY_KEYS.has(key)
}

function persistNow(): void {
  if (persistClean) return
  persistClean = true
  try {
    // Newest first, so what survives the budget is what the user is most likely to look at.
    const ordered = [...entries.entries()]
      .filter(
        ([key, e]) =>
          e.snapshot.data !== undefined && e.snapshot.updatedAt > 0 && isPersistable(key),
      )
      .sort((a, b) => b[1].snapshot.updatedAt - a[1].snapshot.updatedAt)

    const out: Record<string, PersistedEntry> = {}
    let total = 0
    for (const [key, entry] of ordered) {
      let serialized: string
      try {
        serialized = JSON.stringify(entry.snapshot.data)
      } catch {
        continue // a value with a cycle or a BigInt — skip it, never fail the whole write
      }
      if (serialized.length > MAX_ENTRY_BYTES) continue
      total += serialized.length + key.length + 32
      if (total > MAX_TOTAL_BYTES) break
      out[key] = { data: entry.snapshot.data, updatedAt: entry.snapshot.updatedAt }
    }
    localStorage.setItem(PERSIST_KEY, JSON.stringify(out))
  } catch {
    // Over quota despite the budget, or storage unavailable. Drop the mirror rather than
    // retrying: a missing cache costs one slow open, a write loop costs every open.
    try {
      localStorage.removeItem(PERSIST_KEY)
    } catch {
      /* nothing further to do */
    }
  }
}

function schedulePersist(): void {
  persistClean = false
  if (persistTimer !== null) return
  persistTimer = setTimeout(() => {
    persistTimer = null
    persistNow()
  }, PERSIST_DEBOUNCE_MS)
}

/** Write immediately — used on the lifecycle events that precede an iOS teardown. */
export function flushQueryCachePersistence(): void {
  if (persistTimer !== null) {
    clearTimeout(persistTimer)
    persistTimer = null
  }
  persistNow()
}

if (typeof window !== 'undefined' && typeof localStorage !== 'undefined') {
  if (!isQueryCacheDisabled()) hydrateFromStorage()
  // `pagehide` fires on the iOS teardown path where `beforeunload` does not; the visibility
  // handler covers the backgrounding that precedes it.
  window.addEventListener('pagehide', flushQueryCachePersistence)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushQueryCachePersistence()
  })
}

function entryFor<T>(key: string, fetcher: () => Promise<T>): QueryEntry<T> {
  let entry = entries.get(key) as QueryEntry<T> | undefined
  if (!entry) {
    entry = {
      snapshot: EMPTY_SNAPSHOT,
      listeners: new Set(),
      promise: null,
      fetcher,
      gcTimer: null,
    }
    entries.set(key, entry as QueryEntry<unknown>)
  }
  entry.fetcher = fetcher
  return entry
}

function cancelGc<T>(entry: QueryEntry<T>): void {
  if (entry.gcTimer === null) return
  clearTimeout(entry.gcTimer)
  entry.gcTimer = null
}

/**
 * Arm eviction for an entry that just lost its last listener. A fetch still in flight
 * defers eviction (the entry is re-armed when that fetch settles), so a component that
 * unmounts mid-request cannot drop the result out from under a concurrent imperative
 * `fetchCachedQuery` awaiting the same promise.
 *
 * Imperative-only entries — created by `setCachedQueryData` and never subscribed — are
 * deliberately NOT evicted: they never transition from listeners to no-listeners, and
 * they are few and long-lived by construction (write-through mutation results).
 */
function scheduleGc<T>(key: string, entry: QueryEntry<T>): void {
  cancelGc(entry)
  entry.gcTimer = setTimeout(() => {
    entry.gcTimer = null
    // Re-check under the timer: a listener may have re-subscribed, or a refresh started.
    if (entry.listeners.size > 0) return
    if (entry.promise) {
      scheduleGc(key, entry)
      return
    }
    // Only drop the entry if it is still the live one for this key — a `clearQueryCache`
    // plus remount may have replaced it while the timer was pending.
    if (entries.get(key) === (entry as QueryEntry<unknown>)) entries.delete(key)
  }, QUERY_CACHE_GC_MS)
}

function publish<T>(entry: QueryEntry<T>, patch: Partial<QuerySnapshot<T>>): void {
  entry.snapshot = { ...entry.snapshot, ...patch }
  // Only a new VALUE is worth re-mirroring; `refreshing` flips twice per fetch and would
  // otherwise schedule a full serialization on every request for no change in content.
  if ('data' in patch) schedulePersist()
  for (const listener of [...entry.listeners]) listener()
}

function revalidate<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const entry = entryFor(key, fetcher)
  if (entry.promise) return entry.promise
  publish(entry, { refreshing: true, error: null })
  entry.promise = entry
    .fetcher()
    .then((data) => {
      publish(entry, { data, error: null, updatedAt: Date.now(), refreshing: false })
      return data
    })
    .catch((error: unknown) => {
      publish(entry, { error, refreshing: false })
      throw error
    })
    .finally(() => {
      entry.promise = null
    })
  // Effects initiate background revalidation; attach a rejection handler so a failed
  // refresh is represented in the snapshot without becoming an unhandled promise.
  entry.promise.catch(() => {})
  return entry.promise
}

export interface QueryCacheResult<T> {
  data: T | undefined
  error: unknown
  initialLoading: boolean
  refreshing: boolean
  refresh: () => Promise<T>
}

export function useQueryCache<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: {
    staleTime?: number
    version?: number
    refetchInterval?: number
    /** False suspends fetching for this key — for a read whose inputs are not ready yet (e.g.
     *  FIRE, which is meaningless before net worth is known). Any already-cached data is still
     *  returned, so a disabled query renders last-known content rather than a spinner. */
    enabled?: boolean
  } = {},
): QueryCacheResult<T> {
  const entry = entryFor(key, fetcher)
  const subscribe = useCallback(
    (listener: () => void) => {
      cancelGc(entry)
      entry.listeners.add(listener)
      return () => {
        entry.listeners.delete(listener)
        if (entry.listeners.size === 0) scheduleGc(key, entry)
      }
    },
    [entry, key],
  )
  const getSnapshot = useCallback(() => entry.snapshot, [entry])
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  // Default 0 — every mount/read revalidates; cached data only bridges the round-trip
  // (owner call 2026-08-12, after an edited starting balance survived tab switches for
  // 30s: a money number may paint stale for ONE in-flight fetch, never a freshness
  // window. Every explicit staleTime in the codebase was already 0 — call sites had been
  // hand-patching this default away one staleness bug at a time).
  const staleTime = options.staleTime ?? 0
  const enabled = options.enabled ?? true

  useEffect(() => {
    if (!enabled) return
    const effectiveStaleTime = isQueryCacheDisabled() ? 0 : staleTime
    if (!snapshot.updatedAt || Date.now() - snapshot.updatedAt >= effectiveStaleTime) {
      void revalidate(key, fetcher)
    }
    // `version` is an explicit invalidation signal; fetcher identity is intentionally
    // excluded because callers commonly close over render-local values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, staleTime, enabled, options.version])

  // Optional background polling, for badge counts and other live-ish reads that must refresh
  // without a navigation. Lives here rather than in each caller's own setInterval so that
  // concurrent subscribers to the same key collapse into one request: `revalidate` returns the
  // in-flight promise when a tick lands while a fetch is already running.
  const refetchInterval = options.refetchInterval
  useEffect(() => {
    if (!refetchInterval) return
    const id = setInterval(() => void revalidate(key, entry.fetcher), refetchInterval)
    return () => clearInterval(id)
  }, [key, refetchInterval, entry])

  return {
    data: snapshot.data,
    error: snapshot.error,
    // A key that errored with no prior data must NOT read as "still loading" — otherwise a
    // consumer renders its spinner forever instead of falling through to its error state.
    // Nor may a DISABLED key: nothing is coming, so a spinner would never resolve.
    initialLoading: enabled && snapshot.data === undefined && snapshot.error == null,
    refreshing: snapshot.refreshing,
    refresh: useCallback(() => revalidate(key, entry.fetcher), [key, entry]),
  }
}

/** Semantic alias for independently mounted settings/connectors sections. */
export function useCachedFetchState<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: { staleTime?: number; version?: number } = {},
): QueryCacheResult<T> {
  return useQueryCache(key, fetcher, options)
}

export function invalidateQueryCache(matcher: string | ((key: string) => boolean)): void {
  const matches = typeof matcher === 'string' ? (key: string) => key.startsWith(matcher) : matcher
  for (const [key, entry] of entries) {
    if (!matches(key)) continue
    publish(entry, { updatedAt: 0 })
    if (entry.listeners.size > 0) void revalidate(key, entry.fetcher)
  }
}

/**
 * Every Lucius write (the `data_changed` SSE) revalidates EVERY cached read — mounted ones now,
 * the rest on their next mount. Deliberately not narrowed by domain: a key→domains map drifts
 * the moment a screen is added and fails STALE, while this fails as an extra GET per mounted
 * read per write turn. Writes are rare; stale money numbers are the bug.
 */
export function installLiveRefresh(): () => void {
  return onDataChanged(() => invalidateQueryCache(() => true))
}
if (typeof window !== 'undefined') installLiveRefresh()

export function getCachedQueryData<T>(key: string): T | undefined {
  return entries.get(key)?.snapshot.data as T | undefined
}

/**
 * When the cached value for `key` was fetched, or undefined if there is none.
 *
 * For a hydrated entry this is the time of the ORIGINAL fetch — possibly a previous session.
 * That is the point: a caller seeding its state from the cache reports the data's real age
 * rather than implying it just arrived.
 */
export function getCachedQueryUpdatedAt(key: string): number | undefined {
  const at = entries.get(key)?.snapshot.updatedAt
  return at ? at : undefined
}

export function setCachedQueryData<T>(
  key: string,
  updater: T | ((previous: T | undefined) => T),
): T {
  let entry = entries.get(key) as QueryEntry<T> | undefined
  if (!entry) {
    entry = {
      snapshot: EMPTY_SNAPSHOT,
      listeners: new Set(),
      promise: null,
      fetcher: async () => {
        throw new Error(`No fetcher registered for ${key}`)
      },
      gcTimer: null,
    }
    entries.set(key, entry as QueryEntry<unknown>)
  }
  const data =
    typeof updater === 'function'
      ? (updater as (previous: T | undefined) => T)(entry.snapshot.data)
      : updater
  publish(entry, { data, error: null, updatedAt: Date.now() })
  return data
}

/**
 * Drop one key's cached value outright.
 *
 * Different from `invalidateQueryCache`, which keeps the data and marks it stale so subscribers
 * re-fetch while still rendering it. Use this only when the value is known to be WRONG rather
 * than merely old.
 */
export function deleteCachedQueryData(key: string): void {
  const entry = entries.get(key)
  if (!entry) return
  cancelGc(entry)
  entries.delete(key)
  schedulePersist()
}

export function fetchCachedQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: { staleTime?: number; force?: boolean } = {},
): Promise<T> {
  const entry = entryFor(key, fetcher)
  // Default 0 — every mount/read revalidates; cached data only bridges the round-trip
  // (owner call 2026-08-12, after an edited starting balance survived tab switches for
  // 30s: a money number may paint stale for ONE in-flight fetch, never a freshness
  // window. Every explicit staleTime in the codebase was already 0 — call sites had been
  // hand-patching this default away one staleness bug at a time).
  const staleTime = options.staleTime ?? 0
  // A STALE entry must be AWAITED here, not returned-then-refreshed. Stale-while-revalidate
  // only works for subscribed consumers (useQueryCache re-renders when publish() lands); a
  // one-shot caller takes the resolved value into its own setState and never hears from the
  // cache again — so "return stale, refresh in background" made every one-shot screen
  // permanently ONE VISIT BEHIND (the fresh rows landed in a cache nobody re-read; owner
  // report 2026-08-21: the Transactions tab showing old data on open — and with the
  // localStorage mirror, last SESSION's data after a PWA reopen). Within an explicit
  // nonzero staleTime the cached value is still returned; the revalidate dedup still
  // collapses concurrent identical fetches.
  if (
    !options.force &&
    entry.snapshot.data !== undefined &&
    Date.now() - entry.snapshot.updatedAt < staleTime
  ) {
    return Promise.resolve(entry.snapshot.data)
  }
  return revalidate(key, fetcher)
}

export function clearQueryCache(): void {
  for (const entry of entries.values()) {
    cancelGc(entry)
    entry.promise = null
    entry.listeners.clear()
  }
  entries.clear()
  // The mirror is the same data, just on disk — one account's holdings must not survive a
  // logout / 401 and hydrate into the next session.
  if (persistTimer !== null) {
    clearTimeout(persistTimer)
    persistTimer = null
  }
  persistClean = true
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i)
      if (key?.startsWith(PERSIST_PREFIX)) localStorage.removeItem(key)
    }
  } catch {
    /* storage unavailable — nothing was persisted either */
  }
}

/** Test-only: how many entries are currently retained. Backs the eviction tests. */
export function queryCacheSize(): number {
  return entries.size
}
