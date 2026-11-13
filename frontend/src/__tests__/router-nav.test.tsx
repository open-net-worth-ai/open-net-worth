/**
 * @vitest-environment jsdom
 *
 * Unit tests for the TanStack Router migration's critical routing behaviours:
 *
 *  1. Index `/` redirect to the last-saved wealthTab, or `defaultLandingTab`'s
 *     viewport-aware default when there is no saved/valid tab (desktop → 'lucius',
 *     mobile → 'dashboard').
 *  2. ?from= param drives the sidebar activeTab on an asset route.
 *  3. Peer-drill inherits the source-tab's ?from= when drilling from an asset route.
 *  4. Drill-in routes (/cash/payees, /intel/monitors) map to the correct parent tab.
 *
 * These tests use TanStack Router's createRouter + createMemoryHistory so the
 * full route tree runs deterministically with no backend or DOM paint.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createRouter,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  redirect,
} from '@tanstack/react-router'
import { TABS } from '../tabs'
import { tabToSlug, slugToTab, SLUG_MAP, SLUG_ALIASES } from '../routeSlug'
import { defaultLandingTab, connectorCallbackSearch, validateAccountsSearch } from '../router'
import { enableBankingReturnOf } from '../components/tabs/connectors/bankPage'

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Replicate the activeTab derivation from RouterShell for the two single-asset
 * routes: /asset/$instrumentId (tickerless fallback) and /quote/$symbol (the
 * canonical ticker-backed URL) — both route identically for tab-highlight purposes.
 * Uses slugToTab() to translate URL slugs to tab ids (mirrors RouterShell).
 */
function deriveActiveTab(pathname: string, fromParam: string | undefined): string {
  const isSingleAssetRoute = pathname.startsWith('/asset/') || pathname.startsWith('/quote/')
  const isPayeesRoute = pathname === '/accounts/payees'
  const isMonitorsRoute = pathname === '/intel/monitors'

  if (isSingleAssetRoute) {
    return fromParam && (TABS as readonly string[]).includes(fromParam) ? fromParam : 'intel'
  }
  if (isPayeesRoute) return 'accounts'
  if (isMonitorsRoute) return 'intel'

  const tabId = slugToTab(pathname.slice(1))
  return (TABS as readonly string[]).includes(tabId) ? tabId : 'dashboard'
}

it('preserves the transaction-review route command and rejects unknown values', () => {
  expect(validateAccountsSearch({ review: 'transactions' }).review).toBe('transactions')
  expect(validateAccountsSearch({ review: 'anything-else' }).review).toBeUndefined()
})

it('hands a connector callback on to /connectors with the marker it needs', () => {
  const search = connectorCallbackSearch('enable_banking', { state: 's', code: 'c' })
  expect(search).toEqual({ state: 's', code: 'c', connector: 'enable_banking' })
  expect(enableBankingReturnOf(search)).toEqual({ state: 's', code: 'c', error: '' })
})

// ── Tab resolution (beforeLoad logic) ────────────────────────────────────────

describe('defaultLandingTab — index redirect logic', () => {
  function mockViewport(widthPx: number) {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('639.98') ? widthPx <= 639.98 : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  }

  beforeEach(() => {
    mockViewport(1400)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns lucius (desktop default) when wealthTab is null', () => {
    expect(defaultLandingTab(null)).toBe('lucius')
  })

  it('returns lucius (desktop default) when wealthTab is empty string (falsy)', () => {
    expect(defaultLandingTab('')).toBe('lucius')
  })

  it('returns a valid tab directly', () => {
    expect(defaultLandingTab('accounts')).toBe('accounts')
    expect(defaultLandingTab('intel')).toBe('intel')
    expect(defaultLandingTab('dashboard')).toBe('dashboard')
    expect(defaultLandingTab('investments')).toBe('investments')
  })

  it('falls back to lucius (desktop default) for an unknown tab value', () => {
    expect(defaultLandingTab('something-old')).toBe('lucius')
    expect(defaultLandingTab('wealth')).toBe('lucius')
    // No back-compat: former tab names ('equities' etc.) are not special-cased.
    expect(defaultLandingTab('equities')).toBe('lucius')
  })
})

// ── Sidebar activeTab derivation ──────────────────────────────────────────────

describe('deriveActiveTab — sidebar highlight logic', () => {
  it('highlights the matched tab on a plain tab route (new slugs)', () => {
    expect(deriveActiveTab('/accounts', undefined)).toBe('accounts')
    expect(deriveActiveTab('/plan', undefined)).toBe('plan')
    expect(deriveActiveTab('/investments', undefined)).toBe('investments')
    expect(deriveActiveTab('/intel', undefined)).toBe('intel')
  })

  it('highlights accounts on the /accounts/payees drill-in', () => {
    expect(deriveActiveTab('/accounts/payees', undefined)).toBe('accounts')
  })

  it('highlights intel on the /intel/monitors drill-in', () => {
    expect(deriveActiveTab('/intel/monitors', undefined)).toBe('intel')
  })

  it('defaults to intel on an asset route with no from param', () => {
    expect(deriveActiveTab('/asset/AAPL', undefined)).toBe('intel')
  })

  it('respects ?from=investments on an asset route', () => {
    expect(deriveActiveTab('/asset/AAPL', 'investments')).toBe('investments')
  })

  it('respects ?from=accounts (tab id) on an asset route', () => {
    expect(deriveActiveTab('/asset/AAPL', 'accounts')).toBe('accounts')
  })

  it('ignores an invalid ?from= value and falls back to intel', () => {
    expect(deriveActiveTab('/asset/AAPL', 'bogus-tab')).toBe('intel')
    expect(deriveActiveTab('/asset/AAPL', 'unknown')).toBe('intel')
  })

  it('peer-drill with ?from=investments keeps investments highlight', () => {
    // Simulates: Investments → AAPL → peer NVDA → peer MSFT
    // Each asset route carries from=investments; all should highlight investments.
    const from = 'investments'
    expect(deriveActiveTab('/asset/AAPL', from)).toBe('investments')
    expect(deriveActiveTab('/asset/NVDA', from)).toBe('investments')
    expect(deriveActiveTab('/asset/MSFT', from)).toBe('investments')
  })

  // /quote/$symbol — the canonical ticker-backed URL — routes identically to /asset/.
  it('defaults to intel on a quote route with no from param', () => {
    expect(deriveActiveTab('/quote/ONWAI.DE', undefined)).toBe('intel')
  })

  it('respects ?from=investments on a quote route', () => {
    expect(deriveActiveTab('/quote/ONWAI.DE', 'investments')).toBe('investments')
  })

  it('ignores an invalid ?from= value on a quote route and falls back to intel', () => {
    expect(deriveActiveTab('/quote/ONWAI.DE', 'bogus-tab')).toBe('intel')
  })

  it('peer-drill across quote routes keeps the source-tab highlight', () => {
    const from = 'investments'
    expect(deriveActiveTab('/quote/AAPL', from)).toBe('investments')
    expect(deriveActiveTab('/quote/NVDA', from)).toBe('investments')
    expect(deriveActiveTab('/quote/MSFT', from)).toBe('investments')
  })
})

// ── Settings-over-asset-detail selectedAsset fallback ─────────────────────────
//
// Regression (owner report): tapping Settings while on /quote/ONWAI.DE (or
// /asset/$instrumentId) briefly showed the plain Intel feed underneath the dialog
// instead of staying on the asset being viewed — /settings has no asset route params of
// its own, so the live-derived selectedAsset goes null the instant you navigate there.
// Mirrors RouterShell's `selectedAsset = settingsOpen && tabToRender === 'intel' ?
// lastSelectedAssetRef.current : currentSelectedAsset`.

function resolveSelectedAsset<T>(
  settingsOpen: boolean,
  tabToRender: string,
  currentSelectedAsset: T | null,
  lastSelectedAsset: T | null,
): T | null {
  return settingsOpen && tabToRender === 'intel' ? lastSelectedAsset : currentSelectedAsset
}

describe('resolveSelectedAsset — Settings floats over the asset you were viewing', () => {
  const kstl = { instrument_id: 'inst-onwai', ticker: 'ONWAI.DE', label: 'ONWAI Inc' }

  it('settings open over the intel tab: falls back to the last-known asset, not the live (null) one', () => {
    expect(resolveSelectedAsset(true, 'intel', null, kstl)).toBe(kstl)
  })

  it('settings open over a different tab: no asset to restore (never had one)', () => {
    expect(resolveSelectedAsset(true, 'accounts', null, null)).toBeNull()
  })

  it('settings closed: uses the live current asset, ignoring any remembered one', () => {
    const stale = { instrument_id: 'inst-old', ticker: 'OLD', label: 'Old Co' }
    expect(resolveSelectedAsset(false, 'intel', kstl, stale)).toBe(kstl)
  })

  it('settings closed and on the plain Intel feed: null even if an asset was remembered from before', () => {
    expect(resolveSelectedAsset(false, 'intel', null, kstl)).toBeNull()
  })
})

// ── TanStack Router memory integration ───────────────────────────────────────
// These tests spin up a minimal router (no components) with the same index
// beforeLoad redirect logic to confirm the redirect fires correctly.

describe('index route redirect (router integration)', () => {
  let localStorageMock: Record<string, string>

  beforeEach(() => {
    localStorageMock = {}
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => localStorageMock[k] ?? null,
      setItem: (k: string, v: string) => {
        localStorageMock[k] = v
      },
      removeItem: (k: string) => {
        delete localStorageMock[k]
      },
      clear: () => {
        localStorageMock = {}
      },
    })
    // Desktop viewport by default — defaultLandingTab's mobile carve-out is not
    // what this block exercises (that's covered by defaultLandingTab's own unit
    // tests above and by router.test.ts).
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function buildRouter(initialPath: string) {
    const rootRoute = createRootRoute()
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: '/',
      beforeLoad: () => {
        const saved = localStorage.getItem('wealthTab')
        const tabId = defaultLandingTab(saved)
        // Mirror real router.tsx: translate tab id → URL slug before redirecting
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        throw redirect({ to: `/${tabToSlug(tabId as any)}` as any, replace: true })
      },
    })
    const dashboardRoute = createRoute({ getParentRoute: () => rootRoute, path: '/dashboard' })
    const investmentsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/investments' })
    const accountsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/accounts' })
    const intelRoute = createRoute({ getParentRoute: () => rootRoute, path: '/intel' })
    const luciusRoute = createRoute({ getParentRoute: () => rootRoute, path: '/lucius' })

    const routeTree = rootRoute.addChildren([
      indexRoute,
      dashboardRoute,
      investmentsRoute,
      accountsRoute,
      intelRoute,
      luciusRoute,
    ])
    const history = createMemoryHistory({ initialEntries: [initialPath] })
    const router = createRouter({ routeTree, history })
    await router.load()
    return router
  }

  it('redirects / to /lucius (desktop default) when no wealthTab is saved', async () => {
    const router = await buildRouter('/')
    expect(router.state.location.pathname).toBe('/lucius')
  })

  it('redirects / to /accounts when wealthTab=accounts', async () => {
    localStorageMock['wealthTab'] = 'accounts'
    const router = await buildRouter('/')
    expect(router.state.location.pathname).toBe('/accounts')
  })

  it('redirects / to /lucius (desktop default) when wealthTab is an unrecognized value', async () => {
    localStorageMock['wealthTab'] = 'old-tab-name'
    const router = await buildRouter('/')
    expect(router.state.location.pathname).toBe('/lucius')
  })

  it('does not redirect when navigating directly to /intel', async () => {
    const router = await buildRouter('/intel')
    expect(router.state.location.pathname).toBe('/intel')
  })
})

// ── Slug mapping (tabToSlug / slugToTab) ─────────────────────────────────────

describe('slug mapping — every tab id now equals its own URL slug', () => {
  // 2026-09-06 id/label cleanup: 'cash', 'overview', 'transactions', 'integrations' and
  // 'builder' were renamed to what they'd always displayed as ('accounts', 'dashboard',
  // 'plan', 'connectors', 'build'), so SLUG_MAP's remaps became identity and were removed.
  // SLUG_ALIASES is unrelated — it redirects OLD, already-bookmarked URLs, independent of
  // whatever the internal id is called today.

  it('SLUG_MAP has no remaps left — every tab id is its own slug', () => {
    expect(SLUG_MAP).toEqual({})
  })

  it('tabToSlug and slugToTab are the identity for every tab', () => {
    for (const tabId of TABS) {
      expect(tabToSlug(tabId)).toBe(tabId)
      expect(slugToTab(tabId)).toBe(tabId)
    }
  })

  it('SLUG_ALIASES maps old, historical URL segments to a live tab, distinct from itself', () => {
    for (const [oldSlug, newSlug] of Object.entries(SLUG_ALIASES)) {
      const tabId = slugToTab(newSlug)
      expect((TABS as readonly string[]).includes(tabId)).toBe(true)
      expect(oldSlug).not.toBe(newSlug)
    }
  })
})
