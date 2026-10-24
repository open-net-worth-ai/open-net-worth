import {
  createRouter,
  createBrowserHistory,
  createRootRouteWithContext,
  createRoute,
  redirect,
} from '@tanstack/react-router'
import { RouterShell } from './RouterShell'
import { TABS } from './tabs'
import { SLUG_ALIASES, tabToSlug } from './routeSlug'
import { isStandalonePwa } from './utils/pwa'
import { createHelpRoutes } from './components/help/routes'
import type { SignOutScope } from './utils/gatewaySignOut'

// Router context — threaded from <RouterProvider context={...}> in App.tsx.
export interface RouterContext {
  auth: { required: boolean; authenticated: boolean; mode?: 'gateway' | 'demo' }
  onLogout: (scope?: SignOutScope) => Promise<void>
  onAuthChanged: () => void
}

// Root layout: component is the full app shell.
const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RouterShell,
})

// Help Center — registered via a factory (components/help/routes.ts) rather than a direct
// createRoute() call here, so that file doesn't need to import rootRoute/RouterShell back
// (RouterShell already imports the Help Center — a route file importing back would cycle).
const { helpIndexRoute, helpDocRoute } = createHelpRoutes(rootRoute)

// Mirrors useIsMobile's breakpoint (hooks/useIsMobile.ts) — inlined because beforeLoad runs
// outside React, before any hook could run.
const MOBILE_QUERY = '(max-width: 639.98px)'

/** Which tab a fresh visit (or an invalid saved tab) lands on.
 *
 * Desktop's new home is the Lucius tab. Mobile's landing experience is deliberately left
 * unchanged — the phone's Lucius tab is a bottom-bar destination, not "the whole app", so a
 * cold mobile visit still opens on Dashboard exactly as it did before this tab existed.
 *
 * The installed PWA is the one exception: it IS meant to read as "the whole app", opened with
 * assistant-first intent (owner, 2026-08-13) — so it always lands on Lucius, ignoring even a
 * saved tab from a previous session. An ordinary mobile browser tab keeps the existing
 * saved-tab/Dashboard behavior untouched. */
export function defaultLandingTab(saved: string | null): string {
  if (isStandalonePwa()) return 'lucius'
  if (saved && (TABS as readonly string[]).includes(saved)) return saved
  const isMobileViewport = typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches
  return isMobileViewport ? 'dashboard' : 'lucius'
}

// Index: redirect to last-saved tab (or the default landing tab).
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    const tabId = defaultLandingTab(localStorage.getItem('wealthTab'))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    throw redirect({ to: `/${tabToSlug(tabId as any)}` as any, replace: true })
  },
})

// FIRST RUN is a place, not a layer. It used to render as an overlay above whatever route
// happened to be active, so the URL read /dashboard while you were choosing a data folder —
// the address bar describing a screen nobody could see, and Back navigating the page beneath.
// Componentless like the tab routes: RouterShell renders from the pathname.
const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/setup',
})
// One url per screen — /setup/folder, /setup/model, /setup/password, /setup/restore. Setup is
// a sequence, and a sequence whose position lives only in component state cannot be reloaded,
// linked, or walked with the browser's own Back button.
const setupStageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/setup/$stage',
})

// Tab routes — one per tab id
const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/dashboard',
})
const luciusRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/lucius',
})
const inboxRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/inbox',
})
const investmentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/investments',
})
// Optional keys, so a navigate() that predates a newly added param still typechecks.
export function validateAccountsSearch(search: Record<string, unknown>): {
  account?: string
  txn?: string
  review?: 'transactions'
} {
  return {
    account: typeof search.account === 'string' ? search.account : undefined,
    txn: typeof search.txn === 'string' ? search.txn : undefined,
    review: search.review === 'transactions' ? search.review : undefined,
  }
}

const accountsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/accounts',
  // ?account=<id> keeps the selected account in the URL — refresh-safe + bookmarkable.
  // ?txn=<id> deep-links one transaction's detail drawer, so a Lucius write-receipt chip can
  // open the SAME drawer the register opens rather than a chat-only lookalike. Mirrors the
  // /commitments?open=<id> precedent (a Plan budget row deep-linking its commitment).
  // ?review=transactions is a one-shot command that opens the compact review queue.
  validateSearch: validateAccountsSearch,
})
// Assets tab — /assets. `assetRoute` (below) is already taken by the instrument-detail
// screen, hence the plural. ?id=<id> deep-links a specific asset — the Commitments ?open=
// precedent (push on open, replace on close; see RouterShell.tsx's selectAssetItem).
const assetsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assets',
  validateSearch: (search: Record<string, unknown>) => ({
    id: typeof search.id === 'string' ? search.id : undefined,
  }),
})
const commitmentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/commitments',
  // ?open=<id> deep-links a specific commitment (e.g. from its Plan budget row) — refresh-safe.
  validateSearch: (search: Record<string, unknown>) => ({
    open: typeof search.open === 'string' ? search.open : undefined,
  }),
})
const planRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/plan',
})
const intelRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/intel',
})
const skillsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/skills',
})
const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/agents',
})
const automationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/automations',
  validateSearch: (search: Record<string, unknown>) => ({
    automation: typeof search.automation === 'string' ? search.automation : undefined,
    run: typeof search.run === 'string' ? search.run : undefined,
    // Deep-link from a Connectors provider section's "Automate with a template" — opens the
    // editor prefilled with `sync_<provider>` and is cleared once consumed (Automations.tsx).
    template: typeof search.template === 'string' ? search.template : undefined,
  }),
})
const buildRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/build',
})
const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profile',
})
const connectorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  // SLUG_ALIASES generates the old /integrations → /connectors redirect (a frozen historical
  // URL, not the tab id — see SLUG_ALIASES's own comment).
  path: '/connectors',
})
const importsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/imports',
  // ?provider=ynab&run=<id> — the provider tab, and one run opened as a report.
  validateSearch: (search: Record<string, unknown>) => ({
    provider: typeof search.provider === 'string' ? search.provider : undefined,
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
})
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
})

// Asset detail — /asset/$instrumentId with optional search params for metadata.
// validateSearch keeps the params typed (no external zod dep needed).
// Fallback route: only reached when an asset has no market ticker (a manual/private
// holding — price_source="manual"), so there is nothing to build a /quote/$symbol
// URL from. Everything ticker-backed navigates to quoteRoute instead (see quoteRoute
// below) — this route never rewrites itself into that one.
export const assetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/asset/$instrumentId',
  validateSearch: (
    search: Record<string, unknown>,
  ): { from?: string; label?: string; ticker?: string; sub?: string } => ({
    from: typeof search.from === 'string' ? search.from : undefined,
    label: typeof search.label === 'string' ? search.label : undefined,
    ticker: typeof search.ticker === 'string' ? search.ticker : undefined,
    // Overview | Analysis | Position — refresh-safe + bookmarkable (?sub=, same as Plan/Investments/Settings).
    sub: typeof search.sub === 'string' ? search.sub : undefined,
  }),
})

// Quote detail — /quote/$symbol, the canonical shareable/bookmarkable asset URL
// (mirrors Yahoo Finance's /quote/AAPL). $symbol is the source-qualified ticker
// already carried as assetRoute's `ticker` search param (e.g. "KSTL.DE"), matched
// case-insensitively server-side. Navigating here never waits on a resolve call —
// the destination screen resolves the ticker itself (GET /api/intel/assets/by-ticker/
// {ticker}) and renders its own loading state.
export const quoteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/quote/$symbol',
  validateSearch: (
    search: Record<string, unknown>,
  ): { from?: string; label?: string; sub?: string } => ({
    from: typeof search.from === 'string' ? search.from : undefined,
    label: typeof search.label === 'string' ? search.label : undefined,
    // Overview | Analysis | Position — refresh-safe + bookmarkable (?sub=, same as Plan/Investments/Settings).
    sub: typeof search.sub === 'string' ? search.sub : undefined,
  }),
})

// Drill-in sub-routes — registered as flat siblings so specificity
// ensures /accounts/payees matches before /accounts.
export const accountsPayeesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/accounts/payees',
})

export const intelMonitorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/intel/monitors',
})

// Backward-compat redirect routes — old slugs redirect to canonical slugs so
// existing bookmarks (e.g. #/cash, #/cash/payees, #/transactions) keep working.
const legacyRedirectRoutes = Object.entries(SLUG_ALIASES).flatMap(([oldSlug, newSlug]) => {
  const base = createRoute({
    getParentRoute: () => rootRoute,
    path: `/${oldSlug}`,
    beforeLoad: () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      throw redirect({ to: `/${newSlug}` as any, replace: true })
    },
  })
  // Also redirect known sub-routes (only /cash/payees → /accounts/payees for now).
  if (oldSlug === 'cash') {
    const payees = createRoute({
      getParentRoute: () => rootRoute,
      path: `/${oldSlug}/payees`,
      beforeLoad: () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        throw redirect({ to: `/${newSlug}/payees` as any, replace: true })
      },
    })
    return [payees, base]
  }
  return [base]
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  setupStageRoute,
  setupRoute,
  luciusRoute,
  dashboardRoute,
  inboxRoute,
  investmentsRoute,
  // Drill-in before parent so specificity matches first
  accountsPayeesRoute,
  accountsRoute,
  assetsRoute,
  commitmentsRoute,
  planRoute,
  // Drill-in before parent
  intelMonitorsRoute,
  intelRoute,
  skillsRoute,
  agentsRoute,
  automationsRoute,
  buildRoute,
  profileRoute,
  connectorsRoute,
  importsRoute,
  settingsRoute,
  helpIndexRoute,
  helpDocRoute,
  assetRoute,
  quoteRoute,
  // Backward-compat: old slugs redirect to canonical slugs (sub-routes first)
  ...legacyRedirectRoutes,
])

// One-time migration for bookmarks/history entries from the retired hash router
// (#/overview, #/asset/...?from=...). Rewrite to the equivalent path+search BEFORE
// the browser-history router reads the URL, so an old bookmark still lands on the
// right screen instead of blank '/'. The old hash fragment already carries the
// full path+query (hash routing never sends anything before '#' to the server),
// so its content becomes the new URL verbatim.
if (window.location.hash.startsWith('#/')) {
  window.history.replaceState(null, '', window.location.hash.slice(1))
}

const history = createBrowserHistory()

export const router = createRouter({
  routeTree,
  history,
  context: {} as RouterContext,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
