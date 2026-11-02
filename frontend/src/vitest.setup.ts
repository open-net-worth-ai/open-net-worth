/**
 * Vitest global setup — runs before every test file.
 *
 * Problem (Node 26 + jsdom): Node 26 ships an experimental `localStorage` /
 * `sessionStorage` backed by `internal/webstorage`.  That experimental global
 * is already present on `globalThis` when vitest's `populateGlobal` runs, so
 * vitest's filter (`if (k in global) return keysArray.includes(k)`) skips
 * overriding them with jsdom's implementations — because neither key is in
 * vitest's explicit KEYS list.  The result: tests that run in the `jsdom`
 * environment still see `localStorage === undefined` (Node 26's experimental
 * value) instead of the jsdom-backed Storage object.
 *
 * Fix: when the test is running in the jsdom environment and jsdom has been
 * attached to `globalThis` (vitest puts it there as `globalThis.jsdom`), copy
 * the jsdom Storage instances over the Node experimental stubs.
 */
if (typeof (globalThis as { jsdom?: unknown }).jsdom !== 'undefined') {
  // jsdom environment: bridge its Storage objects to the global.
  const win = (globalThis as unknown as { jsdom: { window: Window } }).jsdom.window

  if (win.localStorage !== undefined) {
    Object.defineProperty(globalThis, 'localStorage', {
      get: () => win.localStorage,
      configurable: true,
    })
  }

  if (win.sessionStorage !== undefined) {
    Object.defineProperty(globalThis, 'sessionStorage', {
      get: () => win.sessionStorage,
      configurable: true,
    })
  }

  // jsdom does not implement ResizeObserver.  Provide a no-op stub so that
  // components which construct one at mount time don't crash.  Individual tests
  // that care about ResizeObserver behaviour replace this with their own mock.
  if (
    typeof (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver === 'undefined'
  ) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }

  // jsdom does not implement window.matchMedia. Default to "no match" (desktop /
  // no-motion-preference / etc.) so components that width-gate via useIsMobile /
  // useBreakpoint (mission-control task 94) don't crash mounting under jsdom.
  // Individual tests that care about a specific media query override this global.
  if (typeof win.matchMedia === 'undefined') {
    win.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
    Object.defineProperty(globalThis, 'matchMedia', {
      get: () => win.matchMedia,
      configurable: true,
    })
  }
}

// ── Shared query-cache isolation (2026-07-27 server-state consolidation) ──────
//
// `state/queryCache.ts` keeps its entries in a MODULE-LEVEL map that outlives an
// individual test. Once components read shared endpoints through it (see
// `hooks/serverState/`), one test's `/api/accounts` response stays warm into the next
// test's render — which silently defeats that test's own `authFetch` mock and makes
// failures depend on file order. Reset before every test so each starts cold.
//
// This is the frontend twin of the backend conftest.py autouse fixtures: isolation is
// structural here rather than something each test file has to remember.
import { afterEach, beforeEach } from 'vitest'
import { clearQueryCache } from './state/queryCache'

beforeEach(() => {
  clearQueryCache()
})

// ── Feature flags (docs/FEATURE_FLAGS.md) ─────────────────────────────────────
//
// `permissions_v2`'s switch points render a placeholder until the flag list has loaded, and a
// test has no server to load it from. Every test therefore starts on the flag's declared
// default (off = the old code, which is what the pre-existing tests prove); a test of the new
// path overrides it with `overrideFlagsForTests`, usually inside `describe.each([false, true])`.
//
// Imported inside the hook, not at the top: a static import here would load `useFlag` (and the
// `api/client` it pulls in) before a test file's `vi.mock('../api/client')` is registered, and
// `useFlag` would keep the real client for the whole file.
let restoreDefaultFlags: () => void
beforeEach(async () => {
  const { overrideFlagsForTests } = await import('./hooks/useFlag')
  restoreDefaultFlags = overrideFlagsForTests({ permissions_v2: false })
})
afterEach(() => restoreDefaultFlags())

// ── Entity-type registry seed ─────────────────────────────────────────────────
//
// In the app the registry is seeded before first render from the copy the server embeds
// in index.html. Tests have no server, so seed from the backend's exact payload —
// `lucius/entityTypes.fixture.json`, written by `python -m backend.scripts.entity_types_fixture`
// and pinned to the descriptors by tests/test_entity_types_fixture.py. Tests that need a
// synthetic type layer it on with `__test__setDescriptor`.
import { seedEntityRegistry, toClientDescriptors } from './lucius/entityRegistry'
import entityTypesFixture from './lucius/entityTypes.fixture.json'

seedEntityRegistry(toClientDescriptors(entityTypesFixture))
