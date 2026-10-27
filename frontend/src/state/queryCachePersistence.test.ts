// @vitest-environment jsdom
//
// Regression (2026-07-31): "I don't want any tab to show like it's reloading at all — it should
// retain, show the old data."
//
// The cache is a module-level Map, so it dies with the JS context. On the installed PWA — which
// iOS tears down whenever it is backgrounded — that made every reopen a full skeleton sweep:
// ~120 call sites all reporting `initialLoading`, because `initialLoading` is
// `data === undefined`. Mirroring the cache to localStorage and hydrating it at module load is
// what makes a reopen show last session's content instantly and refresh underneath it.
//
// Hydration happens at import time, so each test drives it with `vi.resetModules()` +
// `await import(...)` — a fresh module registry standing in for a fresh JS context.

import { describe, it, expect, beforeEach, vi } from 'vitest'

type CacheModule = typeof import('./queryCache')

/** Import the module fresh, so its module-level hydration runs against current localStorage. */
async function reopenApp(): Promise<CacheModule> {
  vi.resetModules()
  return import('./queryCache')
}

/** The build-namespaced key the mirror is stored under. */
function storageKey(): string {
  const key = Object.keys(localStorage).find((k) => k.startsWith('onw-query-cache:'))
  return key ?? ''
}

beforeEach(() => {
  localStorage.clear()
})

describe('queryCache — survives a cold start', () => {
  it('hydrates a previous session’s data, so nothing reports initialLoading', async () => {
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'a1' }])
    first.flushQueryCachePersistence()

    // The app is killed and reopened: new module registry, same localStorage.
    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/accounts')).toEqual([{ id: 'a1' }])
    // The age is the ORIGINAL fetch's, not the hydration's — a caller must be able to say how
    // old what it is showing is.
    expect(second.getCachedQueryUpdatedAt('/api/accounts')).toBeGreaterThan(0)
  })

  it('still refetches — hydration decides what is ON SCREEN, never whether the request happens', async () => {
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'stale' }])
    first.flushQueryCachePersistence()

    const second = await reopenApp()
    const fetcher = vi.fn(async () => [{ id: 'fresh' }])
    // `force` is what an explicit refresh passes; the point is that a hydrated entry is a
    // starting picture, not an answer that suppresses the network.
    await second.fetchCachedQuery('/api/accounts', fetcher, { force: true })

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(second.getCachedQueryData('/api/accounts')).toEqual([{ id: 'fresh' }])
  })

  it('drops the mirror on clearQueryCache — i.e. on logout and on a 401', async () => {
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'a1' }])
    first.flushQueryCachePersistence()
    expect(storageKey()).not.toBe('')

    first.clearQueryCache()
    expect(storageKey()).toBe('')

    // And the next open starts genuinely empty — never the previous account's data.
    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/accounts')).toBeUndefined()
  })

  it('starts empty rather than crashing on a corrupt mirror', async () => {
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'a1' }])
    first.flushQueryCachePersistence()
    localStorage.setItem(storageKey(), '{not json')

    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/accounts')).toBeUndefined()
  })

  it('never persists or hydrates the feature-flag list — a flag must not paint from last session', async () => {
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/feature-flags', async () => ({ flags: [], locked: false }))
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'a1' }])
    first.flushQueryCachePersistence()
    expect(localStorage.getItem(storageKey())).not.toContain('/api/feature-flags')

    // Even a mirror written by some other build is not trusted for it.
    const blob = JSON.parse(localStorage.getItem(storageKey()) ?? '{}')
    blob['/api/feature-flags'] = { data: { flags: [], locked: false }, updatedAt: Date.now() }
    localStorage.setItem(storageKey(), JSON.stringify(blob))

    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/feature-flags')).toBeUndefined()
    expect(second.getCachedQueryData('/api/accounts')).toEqual([{ id: 'a1' }])
  })

  it('refuses to persist an oversized entry, and keeps the rest', async () => {
    // localStorage is a ~5MB budget shared with the dashboard layout, auth status and chat
    // drafts, and setItem throws rather than evicting. One huge read must not cost the user
    // their layout.
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/small', async () => ({ ok: true }))
    await first.fetchCachedQuery('/api/huge', async () => ({ blob: 'x'.repeat(400_000) }))
    first.flushQueryCachePersistence()

    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/small')).toEqual({ ok: true })
    expect(second.getCachedQueryData('/api/huge')).toBeUndefined()
  })

  it('does not hydrate a mirror written by a different build', async () => {
    // A persisted response is a SHAPE from the build that fetched it; no generic check can
    // catch a shape change, so a deploy must start from empty instead of feeding yesterday's
    // fields to today's components.
    const first = await reopenApp()
    await first.fetchCachedQuery('/api/accounts', async () => [{ id: 'a1' }])
    first.flushQueryCachePersistence()

    const written = storageKey()
    const payload = localStorage.getItem(written)!
    localStorage.removeItem(written)
    localStorage.setItem('onw-query-cache:some-older-build', payload)

    const second = await reopenApp()
    expect(second.getCachedQueryData('/api/accounts')).toBeUndefined()
    // …and the superseded key is swept, not left to leak a couple of MB per deploy.
    expect(localStorage.getItem('onw-query-cache:some-older-build')).toBeNull()
  })
})
