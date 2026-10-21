// RouterShell — the full app shell (sidebar, topbar, tab content).
// Mounted as the root route component inside <RouterProvider>.
// Reads auth/logout/authChanged from router context via useRouteContext.

import { useGatewayIdentity } from './hooks/useGatewayIdentity'
import {
  Fragment,
  Suspense,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type ReactElement,
} from 'react'
import { useNavigate, useLocation, useRouteContext, useRouterState } from '@tanstack/react-router'
import {
  LayoutGrid,
  Box,
  Inbox as InboxIcon,
  TrendingUp,
  Wallet,
  Target,
  Receipt,
  Lightbulb,
  Zap,
  Workflow,
  FileCode,
  User,
  Share2,
  Download,
  Settings as SettingsIcon,
  ChevronLeft,
  ChevronRight,
  Menu,
  Search,
  Bug,
  Info,
  Eye,
  EyeOff,
  NAV_ICON_PROPS,
} from './ui/icons'
import { Spark } from './lucius/identity'
import { PortfolioProvider, usePortfolioContext } from './state/portfolio'
import { TickerStrip } from './components/TickerStrip'
import { Tooltip } from './components/ui/Tooltip'
import { DesktopNavControls } from './components/DesktopNavControls'
import { useHistoryNav } from './hooks/useHistoryNav'
import { useScrollMemory } from './hooks/useScrollMemory'
import { useSidebarPeek } from './hooks/useSidebarPeek'
import { isShortcutsKey, isSidebarToggleKey } from './utils/shortcuts'
import { ShortcutsDialog } from './components/ShortcutsDialog'
import { getTickerEnabled, subscribeTicker } from './tickerPref'
import { Overview } from './components/tabs/overview/Overview'
import { LuciusHome } from './components/tabs/lucius/LuciusHome'
import { LuciusMobile } from './components/tabs/lucius/LuciusMobile'
import { Inbox } from './components/tabs/Inbox'
import { Investments } from './components/tabs/Investments'
import AccountsTab from './components/tabs/accounts/AccountsTab'
import { ScreenIntro } from './components/intro/ScreenIntro'
import { LuciusRestoreTip, RequestedGuides } from './components/intro/LuciusTours'
import { sendNextPrefill } from './state/luciusSendIntent'
import { ACCOUNT_TYPES, type AccountType } from './components/tabs/accounts/categories'
import { TransactionSurfacesProvider } from './components/tabs/accounts/TransactionSurfaces'
import { fromApiTxn } from './components/tabs/accounts/data'
import { useTransactionSurfaces } from './components/tabs/accounts/transactionSurfacesContext'
import { Plan } from './components/tabs/plan/Plan'
import { TabSkeleton, type TabSkeletonVariant } from './components/ui/TabSkeleton'
import { budgetSkeletonVariant } from './utils/budgetMethodMemory'
import { reconcileBook } from './utils/bookIdentity'
import { Modal } from './components/ui/Modal'
import { ChatPanel } from './components/chat/ChatPanel'
import { ChatComposerProvider, useChatComposer } from './state/chatComposer'
import { openReportProblem } from './state/reportProblem'
import { ScreenSnapshotProvider } from './state/screenSnapshot'
import { AssetNavProvider } from './state/assetNav'
import { LuciusChatProvider, useLuciusChat } from './state/luciusChat'
import { useDataVersion } from './hooks/useDataVersion'
import { connectDataEvents } from './lucius/dataEventsStream'
import {
  useInboxCount,
  useIntelSignals,
  useMatchReviewsCount,
  useProfile,
  useSetupStatus,
} from './hooks/serverState'
import { VisibleIdsProvider } from './lucius/visibleIds'
import { LuciusDock } from './components/chat/lucius/LuciusDock'
import { ArtifactPanelProvider } from './state/artifactPanel'
import { DockArtifactPanel } from './components/tabs/lucius/DockArtifactPanel'
import type { ArtifactRef } from './types/chat'
import {
  MobileTabBar,
  PRICE_STATUS_LABEL,
  type PriceStatus,
} from './components/mobile/MobileTabBar'
import { useIsMobile } from './hooks/useIsMobile'
import { useTimezoneCapture } from './hooks/useTimezoneCapture'
import { useVisualViewportVars } from './hooks/useVisualViewportVars'
import { NewVersionBanner } from './NewVersionBanner'
import { isLuciusDockVisible, useLuciusTab } from './hooks/useLuciusTab'
// Static, not a lazy chunk (2026-09-05): a first open used to wait on a ~17 KB (gzipped)
// download with a `null` fallback — a blank beat after the tap, and one more chunk that can
// 404 in a tab that predates a rebuild. Served from the owner's own machine, that split saved
// nothing measurable. The SECTIONS inside stay lazy with their own prefetch + skeleton.
// docs/adr/2026-09-05-settings-ships-in-the-main-bundle.md
import { Settings } from './components/tabs/Settings'
import { DragRightClick } from './components/chat/lucius/dragRightClick'
import { CommandPalette } from './components/CommandPalette'
import { FirstRun, stageToStep } from './components/firstrun/FirstRun'
import { behindFirstRunProps } from './components/firstrun/behindFirstRun'
import { ErrorBoundary } from './components/ErrorBoundary'
import { BootUnreachable } from './components/BootUnreachable'
import { useBrand } from './brand-context'
import { fDateTime, setBaseCurrency, setDisplayFormats } from './utils/format'
import {
  TABS,
  type TabId,
  type MobileNavTarget,
  PRIMARY_TABS,
  CONFIG_TABS,
  TAB_LABELS,
  TAB_DISPLAY_NAMES,
  NARROW_TABS,
  SELF_HEADER_TABS,
  TAB_SUBTITLES,
} from './tabs'
import { lazyChunk } from './lazyChunk'
import { tabToSlug, slugToTab } from './routeSlug'
import { HelpDocsFrame } from './components/help/HelpDocsFrame'
import { isHelpPathname } from './components/help/docId'
import {
  desktopBridge,
  desktopClientBridge,
  enqueueNativeImports,
  macDesktopChrome,
  requestNativeTransaction,
  subscribeDesktopCommands,
  goCommandPath,
} from './platform/desktop'
import { DesktopStartupSplash } from './components/DesktopStartupSplash'
import { invalidateQueryCache } from './state/queryCache'

const Commitments = lazyChunk(() =>
  import('./components/tabs/commitments/Commitments').then((m) => ({
    default: m.Commitments,
  })),
)
const Assets = lazyChunk(() =>
  import('./components/tabs/assets/Assets').then((m) => ({ default: m.Assets })),
)
const Intel = lazyChunk(() => import('./components/tabs/Intel').then((m) => ({ default: m.Intel })))
const Skills = lazyChunk(() =>
  import('./components/tabs/Skills').then((m) => ({ default: m.Skills })),
)
const Automations = lazyChunk(() =>
  import('./components/tabs/Automations').then((m) => ({
    default: m.Automations,
  })),
)
const Builder = lazyChunk(() =>
  import('./components/tabs/Builder').then((m) => ({ default: m.Builder })),
)
const Connectors = lazyChunk(() =>
  import('./components/tabs/connectors/ConnectorsTab').then((m) => ({
    default: m.ConnectorsTab,
  })),
)

const Imports = lazyChunk(() =>
  import('./components/tabs/imports/ImportsTab').then((m) => ({ default: m.ImportsTab })),
)

const LAZY_TABS = [Commitments, Assets, Intel, Skills, Automations, Builder, Connectors, Imports]

/** Warms every lazy tab once the shell is idle, so a first visit skips the chunk wait. */
function useWarmLazyTabs() {
  useEffect(() => {
    const warm = () => LAZY_TABS.forEach((tab) => tab.preload())
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(warm, { timeout: 4000 })
      return () => window.cancelIdleCallback(id)
    }
    const id = window.setTimeout(warm, 1500)
    return () => window.clearTimeout(id)
  }, [])
}

// Every route gets an intentional first-paint shape. Keeping this as a total map makes a new
// tab choose its loading UI up front instead of quietly inheriting a generic card grid.
const TAB_SKELETON_VARIANTS: Record<TabId, TabSkeletonVariant> = {
  lucius: 'workspace',
  dashboard: 'dashboard',
  inbox: 'inbox',
  investments: 'holdings',
  accounts: 'accounts',
  assets: 'assets',
  commitments: 'commitments',
  plan: 'budget',
  intel: 'intel',
  skills: 'skills',
  automations: 'automations',
  build: 'builder',
  profile: 'workspace',
  connectors: 'connectors',
  imports: 'workspace',
  settings: 'workspace',
  help: 'help',
}

// Inner bridge: lives inside ChatComposerProvider so it can call useChatComposer.
// Exposes requestOpen via a stable ref so RouterShell's handlers (defined before
// the provider mounts) can fire the fresh-thread signal without prop-drilling.
function PaletteBridge({
  requestOpenRef,
  switchConversationRef,
}: {
  requestOpenRef: MutableRefObject<((fresh?: boolean) => void) | null>
  switchConversationRef: MutableRefObject<
    ((sessionId: string, selection?: { model: string; effort: string }) => void) | null
  >
}) {
  const { requestOpen } = useChatComposer()
  const { switchConversation } = useLuciusChat()
  requestOpenRef.current = requestOpen
  switchConversationRef.current = switchConversation
  return null
}

const ICON_PROPS = NAV_ICON_PROPS

// Nav auto-collapse geometry (mirrors index.css). The nav collapses when the
// content area left over after the expanded nav + the docked Lucius panel would
// drop below MIN_CONTENT_W.
const NAV_EXPANDED_W = 200 // .sidebar.expanded width
const CHAT_DOCK_W = 400 // --chat-width default the .app-shell reserves when docked
const NAV_DRAWER_BP = 768 // ≤ this the nav is an off-canvas drawer (no inline width)
const MIN_CONTENT_W = 1200 // min content width before collapsing; tuned so a
// laptop with the docked panel open trips it (e.g. 1512 − 400 − 200 = 912 < 1200)
// while the same laptop keeps the nav expanded when the panel is closed (1312 ≥ 1200).

const TAB_ICONS: Record<TabId, ReactElement> = {
  lucius: <Spark size={20} />,
  dashboard: <LayoutGrid {...ICON_PROPS} />,
  inbox: <InboxIcon {...ICON_PROPS} />,
  investments: <TrendingUp {...ICON_PROPS} />,
  accounts: <Wallet {...ICON_PROPS} />,
  assets: <Box {...ICON_PROPS} />,
  commitments: <Target {...ICON_PROPS} />,
  plan: <Receipt {...ICON_PROPS} />,
  intel: <Lightbulb {...ICON_PROPS} />,
  skills: <Zap {...ICON_PROPS} />,
  automations: <Workflow {...ICON_PROPS} />,
  build: <FileCode {...ICON_PROPS} />,
  profile: <User {...ICON_PROPS} />,
  connectors: <Share2 {...ICON_PROPS} />,
  imports: <Download {...ICON_PROPS} />,
  settings: <SettingsIcon {...ICON_PROPS} />,
  // Same glyph as the topbar's own "Help" button below — one icon for Help everywhere.
  help: <Info {...ICON_PROPS} />,
}

/** The shell's one-shot commands for the app-level surfaces, applied inside their provider:
 *  `?review=transactions` (a notification, a copied URL) opens the review cards in place, on
 *  whatever tab is showing, and clears itself so back never reopens it; a transaction row
 *  handed in (Plan's category link, a `?txn=` deep link) opens the drawer in place. Lucius's
 *  landing and the receipt chips call the surfaces directly and never touch the URL. */
function SurfaceCommands({
  reviewRequested,
  onReviewApplied,
  openTxn,
  onOpenTxnApplied,
  openTxnId,
  onOpenTxnIdApplied,
}: {
  reviewRequested: boolean
  onReviewApplied: () => void
  openTxn: Record<string, unknown> | null
  onOpenTxnApplied: () => void
  openTxnId: string | null
  onOpenTxnIdApplied: () => void
}) {
  const { openReview, openTransaction, openTransactionById } = useTransactionSurfaces()
  useEffect(() => {
    if (!reviewRequested) return
    openReview('automation')
    onReviewApplied()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewRequested])
  useEffect(() => {
    if (!openTxn) return
    openTransaction(fromApiTxn(openTxn as never))
    onOpenTxnApplied()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openTxn])
  useEffect(() => {
    if (!openTxnId) return
    openTransactionById(openTxnId)
    onOpenTxnIdApplied()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openTxnId])
  return null
}

export function RouterShell() {
  return (
    <PortfolioProvider>
      <RouterShellInner />
    </PortfolioProvider>
  )
}

function RouterShellInner() {
  useWarmLazyTabs()
  const { auth, onLogout, onAuthChanged } = useRouteContext({ from: '__root__' })
  const gatewayIdentity = useGatewayIdentity(auth.mode === 'gateway')
  const navigate = useNavigate()
  const macChrome = macDesktopChrome()
  const historyNav = useHistoryNav()
  const { back: goBack, forward: goForward } = historyNav
  const location = useLocation()
  const pathname = location.pathname

  // Read asset params/search from the router state when on /asset/$instrumentId
  // (tickerless fallback) or /quote/$symbol (the canonical ticker-backed URL).
  // useRouterState gives us the current matches, from which we can pull params
  // and search safely without strict-mode type conflicts.
  const routerState = useRouterState()
  const assetMatch = routerState.matches.find((m) => m.routeId === '/asset/$instrumentId')
  const quoteMatch = routerState.matches.find((m) => m.routeId === '/quote/$symbol')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const assetParams = (assetMatch?.params ?? {}) as any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const assetSearch = (assetMatch?.search ?? {}) as any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const quoteParams = (quoteMatch?.params ?? {}) as any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const quoteSearch = (quoteMatch?.search ?? {}) as any

  const isAssetRoute = !!assetParams?.instrumentId
  const isQuoteRoute = !!quoteParams?.symbol
  // Either shape of the single-asset detail screen — used wherever the two routes
  // are routed identically (tab highlight, sidebar, localStorage persistence).
  const isSingleAssetRoute = isAssetRoute || isQuoteRoute

  // The asset selected by the CURRENT route, if any — null once navigated away (e.g. to
  // /settings, which has no asset params of its own). See lastSelectedAssetRef below for
  // why Settings needs the LAST one instead of this live value.
  const currentSelectedAsset = assetParams?.instrumentId
    ? {
        instrument_id: assetParams.instrumentId as string,
        label: assetSearch?.label as string | undefined,
        ticker: assetSearch?.ticker as string | undefined,
      }
    : quoteParams?.symbol
      ? { ticker: quoteParams.symbol as string, label: quoteSearch?.label as string | undefined }
      : null

  const isPayeesRoute = pathname === '/accounts/payees'
  const isMonitorsRoute = pathname === '/intel/monitors'
  // /help and every /help/* doc path — the in-app docs reader (HelpDocsFrame, which frames
  // the published site). 'help' IS a real TabId
  // (tabs.ts) with its two routes registered in router.tsx, but doc sub-paths
  // (/help/$category/$slug) are multi-segment, and slugToTab only translates a single slug —
  // same reason isPayeesRoute/isMonitorsRoute exist above for their own drill-in sub-routes.
  // Feeds routeTab/activeTab below so the rest of the shell (header, sidebar highlight, which
  // pane renders) treats /help like any other tab rather than a special case.
  const isHelpRoute = isHelpPathname(pathname)

  // Selected account in the URL (?account=<id>) — refresh-safe + bookmarkable.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const accountParam = (location.search as any)?.account
  const selectedAccountId = typeof accountParam === 'string' ? accountParam : null
  // One grouped review job may represent many imported/Lucius-added transactions. The URL
  // carries only the intent to open the queue; AccountsTab owns the queue and consumes it.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const reviewTransactionsParam = (location.search as any)?.review
  const openTransactionReview = reviewTransactionsParam === 'transactions'
  // ?add=<account type> opens the add-account form on that type (the Assets page's tiles).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const addAccountParam = (location.search as any)?.add
  const addAccountType = (ACCOUNT_TYPES as readonly string[]).includes(addAccountParam)
    ? (addAccountParam as AccountType)
    : null
  const clearAddAccount = () =>
    navigate({
      to: '/accounts',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, add: undefined })) as any,
      replace: true,
    })
  const selectAccount = (id: string | null) =>
    navigate({
      to: '/accounts',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, account: id || undefined })) as any,
      replace: true,
    })

  // Deep-linked commitment in the URL (?open=<id>) — e.g. from its Plan budget-row chip.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const openCommitmentParam = (location.search as any)?.open
  const selectedCommitmentId = typeof openCommitmentParam === 'string' ? openCommitmentParam : null

  // Sub-tab deep-link (?sub=<value>) — read once per route, since each pathname has its own
  // scoped search params; e.g. /plan?sub=outlook (a parked screen-command chip) or
  // /investments?sub=equity (the asset-detail "Unvested" card's link). Shared across every
  // tab with its own sub-tab nav — each tab component validates the value it understands.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const subTabParam = (location.search as any)?.sub
  const subTabRequest = typeof subTabParam === 'string' ? subTabParam : undefined
  const selectCommitment = (id: string | null) =>
    navigate({
      to: '/commitments',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, open: id || undefined })) as any,
      // Opening a commitment PUSHES a history entry so the browser/OS back gesture returns
      // to the list — it used to replace, which erased the list state from history entirely
      // (owner: tapping back from a commitment detail didn't return to "All commitments").
      // Closing back to the list still replaces, so it doesn't itself add a redundant stop.
      replace: id === null,
    })

  // Deep-linked asset in the URL (?id=<id>) — same precedent, its own route/key.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const openAssetItemParam = (location.search as any)?.id
  const selectedAssetItemId = typeof openAssetItemParam === 'string' ? openAssetItemParam : null
  const selectAssetItem = (id: string | null) =>
    navigate({
      to: '/assets',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, id: id || undefined })) as any,
      replace: id === null,
    })
  // The register's category (?cat=<key>) — opening one PUSHES, so back returns to All; going
  // back to All replaces, like closing an asset.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const assetCatParam = (location.search as any)?.cat
  const selectedAssetCat = typeof assetCatParam === 'string' ? assetCatParam : null
  const selectAssetCat = (cat: string | null) =>
    navigate({
      to: '/assets',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      search: ((prev: any) => ({ ...prev, cat: cat || undefined })) as any,
      replace: cat === null,
    })

  // Which tab's route we are on.
  const routeTab: TabId = (() => {
    if (isPayeesRoute) return 'accounts'
    if (isMonitorsRoute) return 'intel'
    if (isHelpRoute) return 'help'
    if (isSingleAssetRoute) return 'intel'
    const tabId = slugToTab(pathname.slice(1)) as TabId
    return (TABS as readonly string[]).includes(tabId) ? tabId : 'lucius'
  })()

  // Settings is an OVERLAY, not a destination: it opens over whatever you were doing and
  // closing puts you back (Profile is now its first section, so /profile lands here too).
  // The route is kept so it stays deep-linkable, but the tab rendered UNDERNEATH is the last
  // real one — otherwise the dialog would float over an empty shell.
  const settingsOpen = routeTab === 'settings' || routeTab === 'profile'
  // Help is an overlay on exactly Settings' terms (owner, 2026-08-24): a reference surface you
  // open OVER your work, not a destination — which is the same reasoning tabs.ts already gives
  // for keeping it out of the sidebar. Both /help routes stay real, so a doc is still
  // deep-linkable and doc->doc navigation still drives the URL and the back button; only the
  // presentation changed, from a full tab pane to the workspace-tier modal.
  const helpOpen = isHelpRoute
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const settingsSearch = location.search as any
  // ?from= keeps ONE meaning everywhere, never overwritten: which sidebar tab to
  // highlight/restore. It is NOT how RouterShell decides which tab COMPONENT renders
  // underneath Settings — that's a separate, structural fact (below), because an
  // asset-detail page isn't its own tab, it's a sub-view of Intel. Conflating the two
  // was the bug: opening Settings from /quote/KSTL.DE?from=overview used to overwrite
  // ?from= to "intel" so the render logic worked, which silently discarded "overview"
  // (owner: "I want that value to stay where it was, not switch to intel").
  const fromParam = settingsSearch?.from

  // ?assetSymbol=/?assetId= — set by goTo() below whenever the route being left is an
  // asset route. routeTab collapses BOTH asset-detail routes to 'intel' (see routeTab
  // above), so without these, /settings has no way to know WHICH asset (or that there
  // was one at all) once you've navigated away from it — this is what makes that survive
  // a refresh or a shared link, not just an in-session navigation.
  const assetSymbolParam =
    typeof settingsSearch?.assetSymbol === 'string' ? settingsSearch.assetSymbol : undefined
  const assetIdParam =
    typeof settingsSearch?.assetId === 'string' ? settingsSearch.assetId : undefined
  // label survives via the plain ?label= key (already carried over from the asset
  // route's own search by goTo()'s `{...prev, ...extra}` merge) — no separate name needed.
  const assetLabelParam =
    typeof settingsSearch?.label === 'string' ? settingsSearch.label : undefined

  // Which tab COMPONENT renders underneath Settings — 'intel' whenever an asset was open
  // (structural: that's where AssetDetailView/QuoteDetailView actually live), otherwise
  // whatever ?from= names. Never derived by rewriting ?from= itself.
  const fromTab =
    assetSymbolParam || assetIdParam
      ? 'intel'
      : typeof fromParam === 'string'
        ? (slugToTab(fromParam) as TabId)
        : undefined

  const lastAppTabRef = useRef<TabId>('lucius')
  if (!settingsOpen && !helpOpen) lastAppTabRef.current = routeTab
  else if (
    fromTab &&
    fromTab !== 'settings' &&
    fromTab !== 'profile' &&
    (TABS as readonly string[]).includes(fromTab)
  ) {
    lastAppTabRef.current = fromTab
  }
  const tabToRender: TabId = settingsOpen || helpOpen ? lastAppTabRef.current : routeTab
  const settingsSection = routeTab === 'profile' ? 'profile' : subTabRequest

  // Mirrors lastAppTabRef, one level more specific: /settings has no route params of its
  // own, so opening it from /quote/KSTL.DE (or /asset/$instrumentId) makes
  // currentSelectedAsset null — without this, the Intel tab underneath the dialog would
  // silently fall back to its plain feed instead of staying on the asset you were looking
  // at (owner report: "tap settings, it nav back to intel before opening the settings").
  // Prefers the URL-encoded asset (survives a refresh); falls back to the in-memory ref
  // for a caller that opened Settings without going through goTo() (shouldn't happen, but
  // cheap insurance).
  const lastSelectedAssetRef = useRef<typeof currentSelectedAsset>(null)
  if (!settingsOpen) {
    lastSelectedAssetRef.current = currentSelectedAsset
  } else if (assetSymbolParam) {
    lastSelectedAssetRef.current = { ticker: assetSymbolParam, label: assetLabelParam }
  } else if (assetIdParam) {
    lastSelectedAssetRef.current = {
      instrument_id: assetIdParam,
      label: assetLabelParam,
      ticker: undefined,
    }
  }

  // The full previous location — closing Settings must return to the EXACT page you were
  // on (e.g. /quote/KSTL.DE?from=overview), not just the tab it belongs to. Navigating to
  // `/${tabToSlug(lastAppTabRef.current)}` alone collapses any sub-route — an asset detail
  // page, /accounts/payees, /intel/monitors — back to that tab's plain landing page (owner
  // report: closing Settings from an asset page redirects to the generic Intel feed
  // instead of back to the asset). `to`/`params` (never a manually-interpolated path
  // string) so a parameterized route navigates the same proven way openAssetDetail does —
  // TanStack's `navigate({to: literalResolvedPath})` does NOT reliably re-match a
  // parameterized route the way `navigate({to: template, params})` does (owner report: the
  // dialog's own close-on-backdrop-click stopped working once this used a raw path).
  // Restores the ORIGINAL ?from= verbatim (fromParam, never fromTab — fromTab is 'intel'
  // for rendering purposes, but the asset page's own highlight must come back unchanged).
  const lastLocationRef = useRef<{
    to: string
    params?: Record<string, string>
    search: Record<string, unknown>
  }>({ to: '/dashboard', search: {} })
  // Frozen while EITHER overlay is up. Help must be skipped as well as Settings: opening Help
  // FROM Settings leaves settingsOpen false for as long as the Help modal is up, so without
  // this the ref would record /help as "the page Settings was opened from" — and closing
  // Settings afterwards would reopen Help, forever.
  if (!settingsOpen && !helpOpen) {
    lastLocationRef.current = isQuoteRoute
      ? { to: '/quote/$symbol', params: { symbol: quoteParams.symbol }, search: quoteSearch }
      : isAssetRoute
        ? {
            to: '/asset/$instrumentId',
            params: { instrumentId: assetParams.instrumentId },
            search: assetSearch,
          }
        : { to: pathname, search: settingsSearch }
  } else if (assetSymbolParam) {
    lastLocationRef.current = {
      to: '/quote/$symbol',
      params: { symbol: assetSymbolParam },
      search: { from: fromParam, label: assetLabelParam },
    }
  } else if (assetIdParam) {
    lastLocationRef.current = {
      to: '/asset/$instrumentId',
      params: { instrumentId: assetIdParam },
      search: { from: fromParam, label: assetLabelParam },
    }
  }

  // Closing Help returns to the EXACT page it was opened from — /settings included, so
  // Settings -> Help -> close lands back in Settings rather than past it. Its own ref rather
  // than a second reader of lastLocationRef above, precisely because that one deliberately
  // freezes while Settings is open: the two overlays have to remember different pages, and
  // Help is the only one of the pair that can be opened from inside the other.
  const helpReturnRef = useRef<{
    to: string
    params?: Record<string, string>
    search: Record<string, unknown>
  }>({ to: '/dashboard', search: {} })
  if (!helpOpen) {
    helpReturnRef.current = isQuoteRoute
      ? { to: '/quote/$symbol', params: { symbol: quoteParams.symbol }, search: quoteSearch }
      : isAssetRoute
        ? {
            to: '/asset/$instrumentId',
            params: { instrumentId: assetParams.instrumentId },
            search: assetSearch,
          }
        : { to: pathname, search: settingsSearch }
  }

  // On route/tab change, re-scan the screen so "Lucius can see this screen" reflects the new
  // content immediately (otherwise it stays on the old screen's context until the user scrolls).
  useEffect(() => {
    const r = requestAnimationFrame(() => window.dispatchEvent(new Event('lx-rescan')))
    const t = setTimeout(() => window.dispatchEvent(new Event('lx-rescan')), 260)
    return () => {
      cancelAnimationFrame(r)
      clearTimeout(t)
    }
  }, [pathname, tabToRender])

  // Which tab to highlight in the sidebar.
  // For asset detail, use the ?from= param; for drill-ins, the parent tab.
  const activeTab: TabId = (() => {
    if (isSingleAssetRoute) {
      const from = isAssetRoute ? assetSearch?.from : quoteSearch?.from
      return from && (TABS as readonly string[]).includes(from) ? (from as TabId) : 'intel'
    }
    if (isPayeesRoute) return 'accounts'
    if (isMonitorsRoute) return 'intel'
    // Help is an overlay with no nav row of its own, so returning 'help' here would light
    // nothing while a fully-rendered tab sits visible behind the modal. Highlight THAT tab.
    if (isHelpRoute) return lastAppTabRef.current
    const tabId = slugToTab(pathname.slice(1)) as TabId
    // Profile is a SECTION of Settings now, so /profile highlights the Settings entry.
    if (tabId === 'profile') return 'settings'
    return (TABS as readonly string[]).includes(tabId) ? tabId : 'lucius'
  })()

  // Persist active tab id to localStorage so the index redirect works on next visit.
  // Skip sub-routes so we don't overwrite with 'accounts' when on /accounts/payees etc.
  useEffect(() => {
    // isHelpRoute joins the drill-in exclusions for a different reason than they have: 'help'
    // is a real TabId, so it WOULD persist — and next launch would restore straight into the
    // Help overlay floating over whatever tab the fallback picked. An overlay is never the
    // page you were last on.
    if (!isSingleAssetRoute && !isPayeesRoute && !isMonitorsRoute && !isHelpRoute) {
      const tabId = slugToTab(pathname.slice(1))
      if ((TABS as readonly string[]).includes(tabId)) {
        localStorage.setItem('wealthTab', tabId)
      }
    }
  }, [pathname, isSingleAssetRoute, isPayeesRoute, isMonitorsRoute, isHelpRoute])

  // Default is VISIBLE — a new user with no stored preference should see their real numbers,
  // not a screen of dots. The stored value only ever means "hide" (`'1'`) once someone has
  // actually turned it on; `=== '1'` reads that correctly, where the old `!== '0'` hid
  // amounts for anyone who had never touched the toggle at all.
  const [hidden, setHidden] = useState(() => localStorage.getItem('wealthHide') === '1')
  // Mobile shell (<640px): the bottom tab bar + More sheet replace the drawer, and
  // Lucius renders as an ordinary in-flow tab inside .app-layout instead of the floating
  // pill. The keyboard needs no JS at all — see the `interactive-widget` note in index.css.
  const isMobile = useIsMobile()
  // Publishes the visible viewport for full-screen fixed overlays (the command palette).
  // A measurement only — see useVisualViewportVars for why that is not the old hook.
  useVisualViewportVars()
  // Owns the open state AND its persistence, and — on a phone, where Lucius is a tab
  // rather than a side panel — leaves it whenever the route changes. See useLuciusTab.
  // The desktop dock's open flag. On a phone Lucius is the /lucius route and this is never
  // read for rendering — see `mobileNav` and `openLucius` below.
  const [chatOpen, setChatOpen] = useLuciusTab()
  // Persistence records the user's dock preference, not whether the dock is painted on this
  // route. In particular, `/lucius` renders the in-flow main tab and must never reserve the
  // hidden dock's width during the first frame of a restored session.
  const luciusDockVisible = isLuciusDockVisible(chatOpen, isMobile, tabToRender)
  // The docked chat's OWN artifact side panel (owner, 2026-08-13: "when user use lucius side
  // panel! the artifact must open as the modal on the side, not fullscreen") — mirrors
  // LuciusHome's ArtifactPanelProvider, scoped to this one docked instance. Closes whenever
  // the docked ChatPanel itself stops being VISIBLE, same as it belongs to that chat session,
  // not to whatever tab happens to be showing behind it.
  const [dockOpenArtifact, setDockOpenArtifact] = useState<ArtifactRef | null>(null)
  // The EXACT same expression the docked ChatPanel's own `open` prop uses below — watching
  // bare `chatOpen` alone missed the case where tapping the Lucius NAV TAB itself navigates
  // tabToRender to 'lucius' without ever flipping chatOpen to false (owner report, real
  // device: the artifact panel stayed open, uncovered by the panel-close effect, and then
  // rendered edge-to-edge — misread as "now fullscreen" — once --chat-width read back as 0
  // from the now-hidden dock).
  useEffect(() => {
    if (!luciusDockVisible) setDockOpenArtifact(null)
  }, [luciusDockVisible])
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [onboarding, setOnboarding] = useState(false)
  // True for exactly one render pass after first run finishes — it arms ChatPanel's existing
  // (until now never-armed) __ONBOARDING_INIT__ trigger so Lucius speaks first.
  const [justFinishedFirstRun, setJustFinishedFirstRun] = useState(false)
  // One welcome per finished first run, however many chat panels are mounted when it arms.
  const onboardingClaimed = useRef(false)
  const claimOnboarding = () => {
    if (onboardingClaimed.current) return false
    onboardingClaimed.current = true
    return true
  }
  // Session-only guard against re-opening first run on someone who just completed it. The
  // completion stamp is a server write and can fail (offline, backend restarting); without this
  // a failed stamp means checkFreshSetup() re-reports is_fresh and the effect below puts the
  // user straight back into first run with no way out — the localStorage flag used to be that
  // escape hatch by accident. Deliberately NOT persisted: the server stamp remains the only
  // durable answer.
  const firstRunDoneThisSession = useRef(false)
  const [chatPrefill, setChatPrefill] = useState('')
  const [cashFilter, setCashFilter] = useState<{
    // FEB-07 — a merged envelope row's deep-link carries every member category, not just one.
    category?: string | string[]
    month?: string
    /** Handoff item 7 — the Outside-envelopes "Review in Accounts" / "Label it" deep-links:
     *  a blank-category register view, `kind` isolating uncategorized income from Uncategorized
     *  expense (both share the same blank-category bucket server-side). */
    uncategorized?: boolean
    kind?: string
    /** Dashboard Calendar widget deep-link: one exact day (ISO `YYYY-MM-DD`), applied as a
     *  `date_on` token — the same mechanism the register's own search bar produces. */
    dateOn?: string
    /** Dashboard Top merchants deep-link: one payee by name (the register's `payee` token). */
    payee?: string
  } | null>(null)
  // Plan → Accounts deep-link: open the transaction detail drawer for one row.
  const [cashOpenTxn, setCashOpenTxn] = useState<Record<string, unknown> | null>(null)

  // Deep-linked transaction in the URL (?txn=<id>): the surface commands below open the
  // drawer for it in place; the param is cleared once consumed so back never reopens it.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const openTxnParam = (location.search as any)?.txn
  const openTxnId = typeof openTxnParam === 'string' && openTxnParam ? openTxnParam : null

  const [cashRefreshSignal, setCashRefreshSignal] = useState(0)
  const [profileVersion, setProfileVersion] = useState(0)
  const [sidebarExpanded, setSidebarExpanded] = useState(
    () => localStorage.getItem('sidebar-expanded') !== 'false',
  )
  const peek = useSidebarPeek(!sidebarExpanded && !isMobile)
  // Live viewport width — drives the width-based nav auto-collapse below.
  const [viewportW, setViewportW] = useState(() => window.innerWidth)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false)
  const [intelUnread, setIntelUnread] = useState(0)
  const [inboxUnread, setInboxUnread] = useState(0)
  const [dedupCount, setDedupCount] = useState(0)
  const [targetAllocation, setTargetAllocation] = useState<Record<string, number>>({})
  const [riskConcentrationGuideline, setRiskConcentrationGuideline] = useState(20)
  const [riskStressDropPct, setRiskStressDropPct] = useState(30)
  const [importReviewDefault, setImportReviewDefault] = useState<'skip' | 'merge'>('merge')
  const [, setCurTick] = useState(0)

  // Bridge ref for calling requestOpen (fresh-thread signal) from handlers defined
  // before ChatComposerProvider mounts. PaletteBridge populates this once mounted.
  const chatRequestOpenRef = useRef<((fresh?: boolean) => void) | null>(null)
  const chatSwitchConversationRef = useRef<
    ((sessionId: string, selection?: { model: string; effort: string }) => void) | null
  >(null)
  // Tracks the previous "room for the expanded nav" state so the auto-collapse
  // is edge-triggered (fires once when the content area gets too narrow).
  const prevRoomRef = useRef(true)

  const { rows, positionRows, fx, isLive, loading, hasLoaded, loadFailed, refresh } =
    usePortfolioContext()
  // Per-device ticker preference. Subscribed rather than read once so the Settings toggle
  // takes effect immediately, exactly like the theme switch.
  const [tickerOn, setTickerOn] = useState(getTickerEnabled)
  useEffect(() => subscribeTicker(() => setTickerOn(getTickerEnabled())), [])
  const initialLoading = loading && !hasLoaded
  const brand = useBrand()

  // Writes made OUTSIDE this tab's chat (MCP, Telegram, Automations, another tab) arrive on
  // the app-wide GET /api/events stream and join the same bus.
  useEffect(() => connectDataEvents(), [])

  // Lucius wrote data this turn (data_changed SSE → bus) — refetch the shared views.
  // Domains cover the retired frontend MUTATING_TOOLS list (pinned by
  // tests/test_tool_domains.py::test_old_frontend_mutating_set_still_covered).
  const coreDataV = useDataVersion([
    'portfolio',
    'ledger',
    'accounts',
    'payees',
    'categories',
    'budget',
  ])
  useEffect(() => {
    if (coreDataV > 0) {
      refresh()
      setCashRefreshSignal((s) => s + 1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coreDataV])
  const profileDataV = useDataVersion('profile')
  useEffect(() => {
    if (profileDataV > 0) setProfileVersion((v) => v + 1)
  }, [profileDataV])

  const setupStatus = useSetupStatus()
  // Latches once the FIRST answer arrives. `initialLoading` goes true again whenever the
  // cache entry is re-keyed — and it is, on every save, because the query is versioned by the
  // accounts data version. Gating the whole app on it blanked the screen mid-flow.
  const setupAnswered = useRef(false)
  if (setupStatus.data !== undefined) setupAnswered.current = true
  const isFreshSetup = setupStatus.data?.is_fresh
  const restorePending = setupStatus.data?.restore_pending ?? false
  const checkFreshSetup = setupStatus.refresh
  const bookId = setupStatus.data?.book_id
  useEffect(() => {
    // A reset or restore on this address leaves the tab holding the OLD book's caches; the
    // server's book id is what tells them apart. On a swap the page reloads onto the new book.
    if (bookId) void reconcileBook(bookId)
  }, [bookId])

  useEffect(() => {
    // The SERVER stamp is the only signal now — the old localStorage['onboarding-dismissed']
    // check was per-browser, so first run reappeared in a private window and on every other
    // device (design spec §5.5).
    //
    // Setup gets its OWN url. It used to navigate straight to /dashboard and render first run
    // over the top, so the address bar named a screen nobody could see. First run hands off
    // to the LUCIUS TAB now, not Dashboard behind a docked panel — the conversation that just
    // onboarded the book should be the screen you land on, not a panel over an empty chart —
    // and that navigate belongs at the END of the flow, which is where it now happens.
    if (isFreshSetup && !firstRunDoneThisSession.current) {
      setOnboarding(true)
      // ONLY on entry. This effect re-runs whenever setup-status is refetched, and every
      // step of first run refetches it (saving the currency bumps the profile; saving a
      // provider bumps model config). Navigating unconditionally sent you back to /setup —
      // the greeting — the moment you pressed Continue on any step.
      if (
        restorePending &&
        !['/setup/model', '/setup/model-pick', '/setup/password'].includes(location.pathname)
      ) {
        navigate({ to: '/setup/$stage', params: { stage: 'model' }, replace: true })
      } else if (!location.pathname.startsWith('/setup')) navigate({ to: '/setup' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFreshSetup, restorePending, location.pathname])

  // Profile now comes from the shared cache (12 call sites read /api/profile app-wide). The
  // derived local state is kept as-is so every downstream consumer of baseCurrency /
  // targetAllocation / risk knobs is untouched — only the fetch is consolidated.
  const profileQuery = useProfile()
  const profileData = profileQuery.data
  useTimezoneCapture(profileData)
  useEffect(() => {
    if (!profileData) return
    setBaseCurrency(profileData.base_currency || 'SGD')
    setDisplayFormats({
      date_format: profileData.date_format as string | null | undefined,
      number_format: profileData.number_format as string | null | undefined,
    })
    setTargetAllocation(profileData.target_allocation || {})
    setRiskConcentrationGuideline(profileData.risk_concentration_guideline ?? 20)
    setRiskStressDropPct(profileData.risk_stress_drop_pct ?? 30)
    setImportReviewDefault(profileData.import_review_default === 'skip' ? 'skip' : 'merge')
    setCurTick((t) => t + 1)
  }, [profileData])

  // `profileVersion` is the app's manual "profile changed" nudge (settings saves, onboarding).
  // useProfile already self-invalidates on the `profile` data_changed domain; this covers the
  // paths that mutate the profile without emitting one.
  useEffect(() => {
    if (profileVersion > 0) void profileQuery.refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileVersion])

  // A new page opens at its top; Back/Forward return to where you were on that entry. On a
  // phone the scroller is .app-layout, not the window (useScrollMemory handles both).
  const appLayoutRef = useRef<HTMLDivElement>(null)
  useScrollMemory(appLayoutRef)

  useEffect(() => {
    const onResize = () => setViewportW(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Is there room to show the EXPANDED nav (200px) without crushing the content
  // below a comfortable minimum? The docked Lucius panel reserves --chat-width
  // (≈400px; a phone shows it as a full overlay, so it costs no inline width).
  // Below the drawer breakpoint the nav is an off-canvas drawer — inline width
  // doesn't apply, so it never auto-collapses there.
  const chatMargin = luciusDockVisible && viewportW > 640 ? CHAT_DOCK_W : 0
  const roomForExpandedNav =
    viewportW <= NAV_DRAWER_BP || viewportW - chatMargin - NAV_EXPANDED_W >= MIN_CONTENT_W

  // Auto-collapse the nav the instant the content area gets too narrow for it
  // (opening the panel, shrinking the window). Edge-triggered + sticky: once it
  // collapses, the user's toggle wins and persists — we NEVER auto-restore.
  useEffect(() => {
    if (!roomForExpandedNav && prevRoomRef.current) {
      setSidebarExpanded((cur) => {
        if (cur) localStorage.setItem('sidebar-expanded', 'false')
        return false
      })
    }
    prevRoomRef.current = roomForExpandedNav
  }, [roomForExpandedNav])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setPaletteOpen((o) => !o)
        return
      }
      if (isShortcutsKey(e)) {
        e.preventDefault()
        setShortcutsOpen((o) => !o)
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '/') {
        e.preventDefault()
        setChatOpen((o) => !o)
        return
      }
      const tag = (e.target as HTMLElement).tagName
      const isEditable =
        tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement).isContentEditable
      if (isSidebarToggleKey(e)) {
        e.preventDefault()
        pinSidebar()
        return
      }
      if (e.key === 'h' && !e.metaKey && !e.ctrlKey && !isEditable) {
        togglePrivacy()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [setChatOpen])

  function togglePrivacy() {
    setHidden((h) => {
      const next = !h
      localStorage.setItem('wealthHide', next ? '1' : '0')
      document.body.classList.toggle('hide-amounts', next)
      return next
    })
  }

  // The toggle is always authoritative and sticky: it sets and persists the
  // nav state, and nothing auto-restores it. (The width watcher above only
  // collapses — never expands — so a deliberate open survives a panel close.)
  function pinSidebar() {
    peek.close()
    toggleSidebar()
  }

  function toggleSidebar() {
    setSidebarExpanded((e) => {
      const next = !e
      localStorage.setItem('sidebar-expanded', String(next))
      return next
    })
  }

  useEffect(() => {
    document.body.classList.toggle('hide-amounts', hidden)
  }, [hidden])

  // On a phone there is no dock to catch 'lx-dock-open' (the long-press / right-click
  // "Ask Lucius about this" signal), so going to the Lucius tab answers it instead —
  // MB5.2: the entity is already staged as a chip by the time this fires.
  useEffect(() => {
    if (!isMobile) return
    const onDockOpen = () => {
      void navigate({ to: '/lucius' })
    }
    window.addEventListener('lx-dock-open', onDockOpen)
    return () => window.removeEventListener('lx-dock-open', onDockOpen)
  }, [isMobile, navigate])

  // The three sidebar badge counts. Each was its own fetch + (for two of them) its own 30s
  // setInterval; the inbox count additionally re-fetched on EVERY navigation. All three now
  // read shared cache entries — the 30s cadence is preserved via `refetchInterval` inside the
  // cache layer, where concurrent subscribers collapse into one request.
  const intelSignals = useIntelSignals()
  useEffect(() => {
    const stories = intelSignals.data
    if (!stories) return
    setIntelUnread(stories.filter((s) => s.status === 'action_needed').length)
  }, [intelSignals.data])

  const inboxCount = useInboxCount()
  useEffect(() => {
    if (!inboxCount.data) return
    setInboxUnread(inboxCount.data.unread_count ?? 0)
  }, [inboxCount.data])

  const matchReviews = useMatchReviewsCount()
  useEffect(() => {
    if (!matchReviews.data) return
    setDedupCount(matchReviews.data.count ?? 0)
  }, [matchReviews.data])

  const now = fDateTime(new Date(), 'long', 'always')
  // Clock-only form for the ticker head — see TickerStripProps.asOf for why.
  const nowShort = new Date().toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' })
  const priceStatus: PriceStatus = isLive ? 'live' : loading ? 'loading' : 'cached'

  // Navigate to a tab by string id. Translates the tab id to its URL slug. An optional
  // `search` lets a caller deep-link straight into a specific sub-tab (e.g. `{ sub: 'data' }`)
  // instead of the tab's default — mirrors the ?sub= convention every tab with its own
  // sub-tab nav now honors, so a caller never has to reach for localStorage to pre-select one.
  const goTo = (t: string, search?: Record<string, string>) => {
    if ((TABS as readonly string[]).includes(t)) {
      // Opening the overlay stamps the backdrop into the URL, so a refresh restores it —
      // including WHICH asset, when opened from an asset-detail screen. See
      // assetSymbolParam/assetIdParam above for the read side of this.
      const assetExtra = isQuoteRoute
        ? { assetSymbol: quoteParams.symbol }
        : isAssetRoute
          ? { assetId: assetParams.instrumentId }
          : {}
      // ?from= is ONLY overwritten for a plain-tab origin. Leaving an asset route keeps
      // ?from= exactly as it already was (that route's own sidebar highlight, e.g.
      // "overview") — it must never become "intel" just because that's what renders
      // underneath the dialog; see fromTab above for that separate, structural decision.
      const extra =
        t === 'settings' || t === 'profile'
          ? {
              ...search,
              ...(isQuoteRoute || isAssetRoute ? {} : { from: tabToSlug(lastAppTabRef.current) }),
              ...assetExtra,
            }
          : search
      navigate({
        to: `/${tabToSlug(t as any)}` as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        search: (extra ? (prev: any) => ({ ...prev, ...extra }) : undefined) as any,
      })
    }
  }

  // ── the phone's navigation selection ──
  // ONE value, from the route alone. On a phone Lucius is the /lucius route, a tab like any
  // other, so "am I looking at Lucius" and "which tab" are the same question — not a route
  // plus a parallel open-flag every consumer had to reconcile (owner, 2026-07-30: two lit
  // tabs / a route changing underneath an open Lucius; owner, 2026-09-05: Settings dismissed
  // over Lucius returned to the route underneath, because Lucius was not one).
  const mobileNav: MobileNavTarget = activeTab
  const selectMobileNav = (target: MobileNavTarget) => goTo(target)
  // What RENDERS is the tab under any overlay (tabToRender), not what the bar has lit: with
  // Settings open over /lucius the bar rightly lights More, but the 92dvh sheet leaves the top
  // of the viewport showing what is behind it — and that must still be Lucius, mounted, or
  // the dismiss lands on a surface that has to cold-mount in front of you.
  const phoneLucius = isMobile && tabToRender === 'lucius'
  // Every "Ask Lucius" / "open the chat" affordance in the tabs comes through here: the desktop
  // opens its side dock, the phone goes to the Lucius tab.
  const openLucius = () => {
    if (isMobile) goTo('lucius')
    else setChatOpen(true)
  }

  useEffect(() => {
    return subscribeDesktopCommands((command) => {
      if (command.type === 'import-files') {
        enqueueNativeImports(command.files)
        void navigate({
          to: '/accounts',
          search: { account: undefined, txn: undefined, review: undefined },
        })
      } else if (command.type === 'open-settings' || command.type === 'open-inbox') {
        void navigate({ to: '/settings', search: { sub: 'desktop' } } as never)
      } else if (command.type === 'open-activity') {
        void navigate({ to: '/accounts', search: { review: 'transactions' } } as never)
      } else if (command.type === 'open-intel') {
        void navigate({ to: '/intel' } as never)
      } else if (command.type === 'search') {
        setPaletteOpen(true)
      } else if (command.type === 'new-transaction') {
        requestNativeTransaction()
        void navigate({
          to: '/accounts',
          search: { account: undefined, txn: undefined, review: undefined },
        })
      } else if (command.type === 'open-lucius') {
        void navigate({ to: '/lucius' })
      } else if (command.type === 'resume') {
        invalidateQueryCache(() => true)
      } else if (command.type === 'go') {
        const path = goCommandPath(command.tab)
        if (path) void navigate({ to: path } as never)
      } else if (command.type === 'toggle-sidebar') {
        pinSidebar()
      } else if (command.type === 'open-help') {
        void navigate({ to: '/help' } as never)
      } else if (command.type === 'show-shortcuts') {
        setShortcutsOpen(true)
      } else if (command.type === 'history-back') {
        goBack()
      } else if (command.type === 'history-forward') {
        goForward()
      }
    })
  }, [navigate, goBack, goForward])

  // The native History menu mirrors the toolbar arrows, so main needs their state.
  const { canGoBack, canGoForward } = historyNav
  useEffect(() => {
    desktopClientBridge()?.reportHistory?.({ canGoBack, canGoForward })
  }, [canGoBack, canGoForward])

  // Open asset detail — navigates IMMEDIATELY, synchronously, no network call first.
  // A ticker (preferring the source-qualified priceSourceId when given — the bare
  // `ticker`/symbol can collide, e.g. two distinct instruments both called "OKLO";
  // priceSourceId is the pricing identity and doesn't) goes to /quote/$symbol, which
  // resolves itself once mounted. Only a tickerless instrument (manual/private
  // holdings) falls back to /asset/$instrumentId.
  const openAssetDetail = (row: {
    instrument_id?: string
    label?: string
    ticker?: string
    assetClass?: string
    priceSource?: string
    priceSourceId?: string
  }) => {
    const symbol = row.priceSourceId || row.ticker
    const sub = (quoteSearch?.sub ?? assetSearch?.sub) as string | undefined
    if (symbol) {
      navigate({
        to: '/quote/$symbol',
        params: { symbol },
        search: { from: activeTab, label: row.label, sub },
      })
      return
    }

    if (row.instrument_id) {
      navigate({
        to: '/asset/$instrumentId',
        params: { instrumentId: row.instrument_id },
        search: {
          from: activeTab,
          label: row.label,
          ticker: row.ticker,
          sub,
        },
      })
    }
  }

  // While Settings is open over the Intel tab, there are no live route params to derive
  // from (see lastSelectedAssetRef above) — fall back to the asset that was selected right
  // before it opened, so the dialog floats over the same screen instead of the plain feed.
  const selectedAsset =
    settingsOpen && tabToRender === 'intel' ? lastSelectedAssetRef.current : currentSelectedAsset

  // Sidebar tooltips: only the icon-only collapsed rail needs them (expanded and peek rails show
  // labels and shortcuts), shown to the right like a native sidebar.
  const railTip = (el: ReactElement, label: string, shortcut?: readonly string[]) =>
    sidebarExpanded || peek.open ? (
      el
    ) : (
      <Tooltip label={label} shortcut={shortcut} side="right">
        {el}
      </Tooltip>
    )

  const renderNavItem = (t: TabId) => (
    <Fragment key={t}>{railTip(navItemButton(t), TAB_LABELS[t])}</Fragment>
  )

  const navItemButton = (t: TabId) => (
    <button
      key={t}
      type="button"
      className={`sidebar-item${activeTab === t ? ' active' : ''}`}
      onClick={() => {
        goTo(t)
        setDrawerOpen(false)
        peek.close()
      }}
      aria-current={activeTab === t ? 'page' : undefined}
    >
      <span className="sidebar-icon" style={{ position: 'relative' }}>
        {TAB_ICONS[t]}
        {t === 'intel' && intelUnread > 0 && (
          <span className="sidebar-intel-badge">{intelUnread > 9 ? '9+' : intelUnread}</span>
        )}
        {t === 'inbox' && inboxUnread + dedupCount > 0 && (
          <span className="sidebar-intel-badge">
            {inboxUnread + dedupCount > 9 ? '9+' : inboxUnread + dedupCount}
          </span>
        )}
      </span>
      <span className="sidebar-label">{TAB_LABELS[t]}</span>
    </button>
  )

  // Hold the first paint until we know whether this is a fresh instance. `onboarding` starts
  // false, so the app rendered fully and first run dropped over it a moment later — you saw
  // the real UI flash past on every reload. Only the FIRST read blocks (initialLoading); once
  // the answer is cached, later renders never wait, and a non-fresh instance pays a few
  // milliseconds of empty ground rather than showing a screen it is about to cover.
  if (!setupAnswered.current && setupStatus.initialLoading && !firstRunDoneThisSession.current) {
    return desktopBridge() ? <DesktopStartupSplash /> : <div className="app-boot" />
  }
  // The first read failed (a hung or erroring server): whether this is a fresh instance is
  // unknown, and guessing "not fresh" would show an empty app where the wizard belongs.
  if (!setupAnswered.current && setupStatus.error != null && !firstRunDoneThisSession.current) {
    return <BootUnreachable busy={setupStatus.refreshing} onRetry={() => void checkFreshSetup()} />
  }

  // The rail's contents, rendered by the real rail AND by the hover-peek overlay.
  const sidebarBody = (
    <>
      <div className="sidebar-header">
        {/* Mark AND wordmark, not one or the other. A white-label logo_url swaps the
            mark; the app's own logo is the fallback. */}
        {/* Collapsed or peeking: the brand row pins the sidebar open (owner, 2026-10-03) — a
            big target beside the small chevron. Once expanded it is plain branding. */}
        <div
          className="sidebar-brandrow"
          {...(!sidebarExpanded && {
            role: 'button',
            tabIndex: 0,
            'aria-label': 'Expand sidebar',
            style: { cursor: 'pointer' },
            onClick: pinSidebar,
            onKeyDown: (e: React.KeyboardEvent) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                pinSidebar()
              }
            },
          })}
        >
          <img
            className="sidebar-logo"
            src={brand.logo_url || '/logo-mark.svg?v=facet-solid'}
            data-builtin-logo={!brand.logo_url || undefined}
            alt=""
            aria-hidden="true"
          />
          <span className="sidebar-brand">{brand.app_name}</span>
        </div>
        {!macChrome && (
          <button
            className="sidebar-toggle"
            onClick={pinSidebar}
            onMouseEnter={peek.enter}
            onMouseLeave={peek.leave}
            title={sidebarExpanded ? 'Collapse' : 'Expand'}
          >
            {sidebarExpanded ? (
              <ChevronLeft size={16} strokeWidth={1.5} aria-hidden="true" />
            ) : (
              <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      <div className="sidebar-nav">
        {/* Zone 1 — your money */}
        {PRIMARY_TABS.map(renderNavItem)}

        <div className="sidebar-divider" />

        {/* Zone 2 — you + your assistant */}
        {CONFIG_TABS.map(renderNavItem)}
      </div>
      {/* Utilities, pinned to the bottom. Moved off the topbar so the price ticker
          gets the whole row (owner, 2026-08-24). They are app-wide actions, not
          per-tab ones, so the nav rail is where they belong anyway. */}
      {!isMobile && (
        <div className="sidebar-util">
          <div className="sidebar-divider" />
          {railTip(
            <button
              className="sidebar-item"
              onClick={() => setPaletteOpen(true)}
              aria-label="Open command palette"
            >
              <span className="sidebar-icon">
                <Search size={16} strokeWidth={1.6} aria-hidden="true" />
              </span>
              <span className="sidebar-label">Search</span>
              {/* The shortcut hint used to live on the topbar Search button; it moved
                here with the control so the binding stays discoverable. */}
              <kbd className="sidebar-kbd">⌘K</kbd>
            </button>,
            'Search',
            ['Mod', 'K'],
          )}
          {railTip(
            <button
              className="sidebar-item"
              onClick={togglePrivacy}
              aria-label={hidden ? 'Show amounts' : 'Hide amounts'}
            >
              <span className="sidebar-icon">
                {hidden ? (
                  <Eye size={16} strokeWidth={1.6} aria-hidden="true" />
                ) : (
                  <EyeOff size={16} strokeWidth={1.6} aria-hidden="true" />
                )}
              </span>
              <span className="sidebar-label">{hidden ? 'Show amounts' : 'Hide amounts'}</span>
              {/* Bare `h` toggles privacy (see the keydown handler above) — advertise
                it the same way Search advertises ⌘K. */}
              <kbd className="sidebar-kbd">H</kbd>
            </button>,
            hidden ? 'Show amounts' : 'Hide amounts',
            ['H'],
          )}
          {railTip(
            <button
              className="sidebar-item"
              onClick={() => openReportProblem()}
              aria-label="Send feedback"
            >
              <span className="sidebar-icon">
                <Bug size={16} strokeWidth={1.6} aria-hidden="true" />
              </span>
              <span className="sidebar-label">Send feedback</span>
            </button>,
            'Send feedback',
          )}
        </div>
      )}
    </>
  )

  const behindFirstRun = behindFirstRunProps(onboarding)
  const behindFirstRunWrap = { style: { display: 'contents' }, ...behindFirstRun } as const

  return (
    <ChatComposerProvider>
      <ScreenSnapshotProvider>
        <LuciusChatProvider>
          <TransactionSurfacesProvider
            onManagePayees={() => navigate({ to: '/accounts/payees' as never })}
            onOpenAccounts={() =>
              void navigate({
                to: '/accounts',
                search: { account: undefined, txn: undefined, review: undefined },
              })
            }
          >
            <SurfaceCommands
              reviewRequested={openTransactionReview}
              onReviewApplied={() =>
                navigate({
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  search: ((prev: any) => ({ ...prev, review: undefined })) as any,
                  replace: true,
                })
              }
              openTxn={cashOpenTxn}
              onOpenTxnApplied={() => setCashOpenTxn(null)}
              openTxnId={openTxnId}
              onOpenTxnIdApplied={() =>
                navigate({
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  search: ((prev: any) => ({ ...prev, txn: undefined })) as any,
                  replace: true,
                })
              }
            />
            <VisibleIdsProvider>
              <PaletteBridge
                requestOpenRef={chatRequestOpenRef}
                switchConversationRef={chatSwitchConversationRef}
              />
              <div
                className={`app-shell${luciusDockVisible ? ' chat-open' : ''}${drawerOpen ? ' drawer-open' : ''}${isMobile ? ' mb-shell' : ''}`}
              >
                {/* Mobile drawer scrim — tap-outside closes the off-canvas sidebar */}
                <div
                  className="sidebar-scrim"
                  onClick={() => setDrawerOpen(false)}
                  aria-hidden="true"
                />
                {/* Inside the shell for its tab-bar clearance contract (--mobile-tabbar-clearance). */}
                <div {...behindFirstRunWrap}>
                  <NewVersionBanner />
                </div>
                <nav
                  className={`sidebar${sidebarExpanded ? ' expanded' : ''}${drawerOpen ? ' drawer-open' : ''}`}
                  {...behindFirstRun}
                  data-ctx-occluder
                  data-ctx-occluder-edge="left"
                >
                  {sidebarBody}
                </nav>

                {/* .app-main is the full-width column beside the sidebar: the topbar spans it,
                  and .app-layout beneath is the CAPPED reading band (and, on mobile, the
                  scroller). The cap sits on .app-layout itself so no per-tab stylesheet can
                  escape it — a child-based cap only reached direct children and lost to any
                  UNLAYERED tab CSS, which is how Automations ran edge-to-edge. */}
                <div
                  {...behindFirstRun}
                  className="app-main"
                  data-ctx-screen={phoneLucius ? 'Lucius' : TAB_LABELS[tabToRender]}
                >
                  <div
                    className="topbar"
                    data-ctx-occluder
                    data-ctx-occluder-edge="top"
                    data-ctx-ignore
                  >
                    <button
                      className="topbar-hamburger"
                      onClick={() => setDrawerOpen(true)}
                      title="Menu"
                      aria-label="Open navigation menu"
                    >
                      <Menu size={18} strokeWidth={1.6} aria-hidden="true" />
                    </button>
                    {!isMobile &&
                      (tickerOn ? (
                        <TickerStrip
                          rows={positionRows}
                          priceStatus={priceStatus}
                          asOf={nowShort}
                          asOfLong={`As of ${now} • ${PRICE_STATUS_LABEL[priceStatus]}`}
                        />
                      ) : (
                        <div className="stamp">
                          As of {now} •{' '}
                          <span
                            style={{
                              color: priceStatus === 'live' ? 'var(--good)' : 'var(--faint)',
                            }}
                          >
                            {PRICE_STATUS_LABEL[priceStatus]}
                          </span>
                        </div>
                      ))}
                    <div className="topbar-actions">
                      {!chatOpen && !isMobile && tabToRender !== 'lucius' && (
                        <button
                          id="chat-toggle-btn"
                          onClick={() => {
                            openLucius()
                          }}
                          title="Lucius — ⌘/"
                        >
                          Lucius ✦<kbd className="cmdk-btn-kbd">⌘/</kbd>
                        </button>
                      )}
                    </div>
                  </div>
                  <div
                    ref={appLayoutRef}
                    className={`app-layout${
                      tabToRender === 'accounts' ||
                      tabToRender === 'skills' ||
                      (tabToRender === 'lucius' && !isMobile)
                        ? ' app-layout--panel'
                        : ''
                    }${
                      // Plan sets its own `.pl-page--wide` (every sub-tab: the budget table +
                      // rail, Cash Flow, Outlook are all built to grow), but that only lifts the
                      // page's INNER cap — the shell's reading band on .app-layout still boxed it
                      // to 1400px, leaving margins on a big screen that Investments doesn't have
                      // (owner-caught, 2026-09-15).
                      tabToRender === 'investments' || tabToRender === 'plan'
                        ? ' app-layout--wide'
                        : ''
                    }${NARROW_TABS.has(tabToRender) ? ' app-layout--narrow' : ''}${phoneLucius ? ' app-layout--lucius' : ''}`}
                  >
                    {!SELF_HEADER_TABS.has(tabToRender) && !isSingleAssetRoute && !phoneLucius && (
                      <div className="tab-header">
                        <h1 className="tab-title">{TAB_DISPLAY_NAMES[tabToRender]}</h1>
                        <div className="tab-subtitle">{TAB_SUBTITLES[tabToRender]}</div>
                      </div>
                    )}

                    {phoneLucius ? (
                      <ErrorBoundary key="lucius" label="Lucius">
                        <LuciusMobile
                          prefill={chatPrefill}
                          onPrefillConsumed={() => setChatPrefill('')}
                          onboarding={justFinishedFirstRun}
                          onOnboardingDone={() => setJustFinishedFirstRun(false)}
                          claimOnboarding={claimOnboarding}
                        />
                      </ErrorBoundary>
                    ) : (
                      <>
                        {/* No whole-tree loading gate. It used to hold "Loading portfolio…" here until
                  the live-price fetch resolved, so every cold start (i.e. every reopen of the
                  installed PWA) mounted the entire tab tree at once, seconds late — the
                  "it reloads and remounts every time I open it" report of 2026-07-31. Tabs now
                  mount immediately and own their own skeletons; `initialLoading` is passed down
                  purely so a tab can tell "nothing yet" from "nothing at all" and skeleton
                  instead of claiming the portfolio is empty. */}
                        <Suspense
                          fallback={
                            <TabSkeleton
                              variant={
                                tabToRender === 'plan'
                                  ? budgetSkeletonVariant()
                                  : TAB_SKELETON_VARIANTS[tabToRender]
                              }
                              label={`Loading ${TAB_LABELS[tabToRender]}…`}
                              // Investments keeps the shell-owned title on screen, so its first
                              // paint begins directly with the portfolio masthead rather than a
                              // second title-shaped placeholder.
                              header={tabToRender !== 'investments'}
                            />
                          }
                        >
                          <ErrorBoundary key={tabToRender} label={TAB_LABELS[tabToRender]}>
                            {tabToRender === 'lucius' && !isMobile && (
                              <LuciusHome
                                prefill={chatPrefill}
                                onPrefillConsumed={() => setChatPrefill('')}
                                onOpenChange={setChatOpen}
                                onboarding={justFinishedFirstRun}
                                onOnboardingDone={() => setJustFinishedFirstRun(false)}
                                claimOnboarding={claimOnboarding}
                              />
                            )}
                            {tabToRender === 'dashboard' && (
                              <Overview
                                rows={rows}
                                loading={initialLoading}
                                loadFailed={loadFailed}
                                onRetry={refresh}
                                onChatSuggest={(text) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                                onNavigate={goTo}
                                onOpenAsset={openAssetDetail}
                                onOpenRegister={(filter) => {
                                  setCashFilter(filter)
                                  goTo('accounts')
                                }}
                              />
                            )}
                            {tabToRender === 'inbox' && <Inbox onNavigate={goTo} />}
                            {tabToRender === 'investments' && (
                              <Investments
                                rows={rows}
                                positionRows={positionRows}
                                loading={initialLoading}
                                loadFailed={loadFailed}
                                targets={targetAllocation}
                                riskConcentrationGuideline={riskConcentrationGuideline}
                                riskStressDropPct={riskStressDropPct}
                                refresh={() => {
                                  refresh()
                                  setProfileVersion((v) => v + 1)
                                }}
                                onAskLucius={(text) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                                onOpenAssetDetail={openAssetDetail}
                                onNavigate={goTo}
                                subTabRequest={subTabRequest}
                              />
                            )}
                            {tabToRender === 'accounts' && (
                              <AccountsTab
                                refresh={refresh}
                                refreshSignal={cashRefreshSignal}
                                registerFilter={cashFilter}
                                onRegisterFilterApplied={() => setCashFilter(null)}
                                showPayeesRoute={isPayeesRoute}
                                selectedAccountId={selectedAccountId}
                                onSelectAccount={selectAccount}
                                importReviewDefault={importReviewDefault}
                                onNavigate={goTo}
                                addAccountType={addAccountType}
                                onAddAccountConsumed={clearAddAccount}
                                onAskLucius={(text) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                              />
                            )}
                            {tabToRender === 'commitments' && (
                              <AssetNavProvider>
                                <Commitments
                                  fx={fx}
                                  onAskLucius={(text) => {
                                    setChatPrefill(text)
                                    openLucius()
                                  }}
                                  selectedId={selectedCommitmentId}
                                  onSelect={selectCommitment}
                                />
                              </AssetNavProvider>
                            )}
                            {tabToRender === 'assets' && (
                              <Assets
                                luciusOpen={luciusDockVisible}
                                onAskLucius={(text: string) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                                onNavigate={goTo}
                                selectedId={selectedAssetItemId}
                                onSelect={selectAssetItem}
                                selectedCat={selectedAssetCat}
                                onSelectCat={selectAssetCat}
                              />
                            )}
                            {tabToRender === 'plan' && (
                              <Plan
                                subTabRequest={subTabRequest}
                                onAskLucius={(text) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                                onOpenTxn={(raw) => setCashOpenTxn(raw)}
                                onOpenRegister={(filter) => {
                                  setCashFilter(filter)
                                  goTo('accounts')
                                }}
                              />
                            )}
                            {tabToRender === 'intel' && (
                              <AssetNavProvider>
                                <Intel
                                  onChatSuggest={(text) => {
                                    setChatPrefill(text)
                                    openLucius()
                                  }}
                                  selectedAsset={selectedAsset}
                                  showMonitorsRoute={isMonitorsRoute}
                                  subTabRequest={subTabRequest}
                                  backdrop={settingsOpen || helpOpen}
                                />
                              </AssetNavProvider>
                            )}
                            {tabToRender === 'skills' && (
                              <Skills
                                onRunSkill={(text) => {
                                  setChatPrefill(text)
                                  openLucius()
                                }}
                              />
                            )}
                            {tabToRender === 'automations' && (
                              <Automations
                                onOpenChat={(prefill) => {
                                  setChatPrefill(prefill)
                                  openLucius()
                                }}
                                onOpenConversation={(sessionId, selection) => {
                                  chatSwitchConversationRef.current?.(sessionId, selection)
                                  openLucius()
                                }}
                              />
                            )}
                            {tabToRender === 'build' && <Builder />}
                            {tabToRender === 'connectors' && <Connectors />}
                            {tabToRender === 'imports' && <Imports />}
                          </ErrorBoundary>
                        </Suspense>
                        {/* Outside the tab's Suspense: a lazy chart suspending re-ran the open
                            dialog's layout effects on every reveal, an update loop that took
                            Plan down. */}
                        <ErrorBoundary>
                          <ScreenIntro
                            screen={tabToRender}
                            luciusBeside={luciusDockVisible}
                            onAskLucius={(text) => {
                              sendNextPrefill()
                              setChatPrefill(text)
                              openLucius()
                            }}
                          />
                        </ErrorBoundary>
                      </>
                    )}
                    {/* Guides Lucius asks for (show_guide) — beside the phone's Lucius tab too. */}
                    <ErrorBoundary>
                      <RequestedGuides />
                      {/* Once, after Lucius's first change: where to undo it. Mounted here, once,
                          not per chat panel — two copies would each remember it separately. */}
                      <LuciusRestoreTip />
                    </ErrorBoundary>
                  </div>
                </div>
                {/* Hover peek: a SEPARATE fixed layer over the page. The real rail, topbar and main
                  column are untouched while it is open — no class, state or spacer they read
                  changes (a peek that reused the rail's own state shoved the page around). */}
                {peek.open && (
                  <nav
                    className="sidebar peek"
                    {...behindFirstRun}
                    aria-label="Sidebar preview"
                    onMouseEnter={peek.enter}
                    onMouseLeave={peek.leave}
                  >
                    {sidebarBody}
                  </nav>
                )}
                {/* LAST in the shell, deliberately: Chromium resolves overlapping drag regions in DOM
                  order, so a cluster rendered before .sidebar-header / .topbar (both drag) loses
                  its no-drag to them and its clicks start a window drag instead. */}
                {macChrome && (
                  <div {...behindFirstRunWrap}>
                    <DesktopNavControls
                      sidebarExpanded={sidebarExpanded}
                      onToggleSidebar={pinSidebar}
                      onToggleHover={peek.enter}
                      onToggleLeave={peek.leave}
                      canGoBack={historyNav.canGoBack}
                      canGoForward={historyNav.canGoForward}
                      onBack={goBack}
                      onForward={goForward}
                    />
                  </div>
                )}

                {helpOpen && (
                  <ErrorBoundary key="help" label="Help">
                    <Modal
                      open
                      onClose={() =>
                        void navigate({
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          to: helpReturnRef.current.to as any,
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          params: helpReturnRef.current.params as any,
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          search: helpReturnRef.current.search as any,
                        })
                      }
                      title="Help"
                      size="workspace"
                      className="onw-help"
                      overlayClassName="onw-help-overlay"
                      // A bottom sheet on a phone like every other modal — the Settings and
                      // price-chart precedent: index.css docks it, this opts into the drag.
                      mobileDockedSheet
                    >
                      <HelpDocsFrame
                        pathname={pathname}
                        hash={location.hash}
                        onNavigate={(to) => {
                          // `to` may carry a trailing "#fragment" (a cross-doc link that also points
                          // at a section) — TanStack wants the hash as its own navigate() option, not
                          // embedded in `to`. `replace` so reading through the docs doesn't stack one
                          // history entry per page: the frame keeps its OWN back/forward, and the
                          // app's Back should leave Help, not walk back through it.
                          const [path, hash] = to.split('#')
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          navigate({ to: path as any, hash: hash || undefined, replace: true })
                        }}
                        onOpenApp={(url) => {
                          // Leaving /help closes Help; the screen the guide pointed at opens.
                          const u = new URL(url, window.location.origin)
                          void navigate({
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            to: u.pathname as any,
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            search: Object.fromEntries(u.searchParams) as any,
                            hash: u.hash.replace(/^#/, '') || undefined,
                          })
                        }}
                      />
                    </Modal>
                  </ErrorBoundary>
                )}

                {settingsOpen && (
                  <ErrorBoundary key="settings" label={TAB_LABELS.settings}>
                    {/* Deliberately NOT keyed on profileVersion. Settings WRITES the profile, and a
                  remount would (a) throw away the open section, the search box and any unsaved
                  edit, and (b) loop: ProfileSection re-reads the profile on mount, which is
                  itself one of the nudges that bumps profileVersion. Its sections subscribe to
                  the query cache, so they re-render on new data without remounting. */}
                    <Settings
                      gatewayMode={auth.mode === 'gateway'}
                      subTabRequest={settingsSection}
                      onReviewDefaultChange={setImportReviewDefault}
                      onClose={() =>
                        void navigate({
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          to: lastLocationRef.current.to as any,
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          params: lastLocationRef.current.params as any,
                          // eslint-disable-next-line @typescript-eslint/no-explicit-any
                          search: lastLocationRef.current.search as any,
                        })
                      }
                    />
                  </ErrorBoundary>
                )}

                {onboarding && (
                  <FirstRun
                    gatewayMode={auth.mode === 'gateway'}
                    // Which screen, from the url — so a reload lands where you were instead of
                    // bouncing back to the greeting, and Back walks the sequence.
                    initialStep={stageToStep(pathname.split('/')[2])}
                    // Step 2 has two halves and both are urls, so the segment itself must come down —
                    // deriving the half from the step alone loses which one you are on.
                    currentStage={pathname.split('/')[2]}
                    restorePending={restorePending}
                    onStepChange={(stage, opts) =>
                      // eslint-disable-next-line @typescript-eslint/no-explicit-any
                      navigate({
                        to: (stage ? `/setup/${stage}` : '/setup') as any,
                        replace: opts?.replace,
                      })
                    }
                    onAuthChanged={onAuthChanged}
                    onDone={() => {
                      firstRunDoneThisSession.current = true
                      setOnboarding(false)
                      // A restored (or moved) book already has its history: Lucius's new-user
                      // welcome would run onboarding over a full book.
                      setJustFinishedFirstRun(!restorePending)
                      // Lucius is the main tab, not a floating dock over Dashboard — the
                      // conversation that just onboarded the book should BE the screen you land
                      // on, not a panel over an empty net-worth chart with nothing in it yet.
                      navigate({ to: '/lucius' })
                      setChatOpen(true)
                      setProfileVersion((v) => v + 1)
                      checkFreshSetup()
                      refresh()
                    }}
                  />
                )}

                <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

                <CommandPalette
                  open={paletteOpen}
                  onClose={() => setPaletteOpen(false)}
                  onNavigate={goTo}
                  onOpenChat={(query, opts) => {
                    if (query) setChatPrefill(query)
                    openLucius()
                    if (opts?.fresh) chatRequestOpenRef.current?.(true)
                  }}
                  rows={rows}
                  onCategoryFilter={(category, month) => {
                    setCashFilter({ category, month })
                    navigate({ to: '/accounts', search: { account: undefined, txn: undefined } })
                  }}
                  onOpenAsset={openAssetDetail}
                />

                {/* Desktop side panel. Below the phone breakpoint Lucius is rendered as an
            ordinary tab inside .app-layout instead (same component, mounted there
            only while its tab is active) — never this fixed-position dock. */}
                {!isMobile && (
                  <div {...behindFirstRunWrap}>
                    <ArtifactPanelProvider onOpen={setDockOpenArtifact}>
                      <ChatPanel
                        open={luciusDockVisible}
                        respondToGlobalOpen={tabToRender !== 'lucius'}
                        onOpenChange={(v) => {
                          setChatOpen(v)
                        }}
                        prefill={chatPrefill}
                        onPrefillConsumed={() => setChatPrefill('')}
                        onboarding={justFinishedFirstRun}
                        onOnboardingDone={() => setJustFinishedFirstRun(false)}
                        claimOnboarding={claimOnboarding}
                      />
                    </ArtifactPanelProvider>
                  </div>
                )}
                {!isMobile && dockOpenArtifact && (
                  <DockArtifactPanel
                    artifact={dockOpenArtifact}
                    onClose={() => setDockOpenArtifact(null)}
                  />
                )}

                {/* Global floating dock — desktop only, and not on the Lucius tab itself: it would
            float a "chat with Lucius" pill on top of the page that already IS the chat. The
            dock's own code and behavior on every OTHER tab are unchanged. */}
                {!chatOpen && !isMobile && !onboarding && tabToRender !== 'lucius' && (
                  <LuciusDock
                    onExpand={() => {
                      openLucius()
                    }}
                    onSetUpAi={() => {
                      void navigate({ to: '/settings', search: { sub: 'model' } })
                    }}
                  />
                )}

                {isMobile && (
                  <div {...behindFirstRunWrap}>
                    <MobileTabBar
                      selected={mobileNav}
                      onSelect={selectMobileNav}
                      icons={TAB_ICONS}
                      badges={{
                        inbox: inboxUnread + dedupCount,
                        intel: intelUnread,
                      }}
                      moreOpen={mobileMoreOpen}
                      onMoreOpenChange={setMobileMoreOpen}
                      stamp={{ asOf: now, prices: priceStatus }}
                      onOpenSearch={() => setPaletteOpen(true)}
                      onTogglePrivacy={togglePrivacy}
                      amountsHidden={hidden}
                      authRequired={auth.required || auth.mode === 'gateway'}
                      onLogout={onLogout}
                      gateway={gatewayIdentity}
                    />
                  </div>
                )}

                {/* Global delegated right-click + drag handlers — always mounted */}
                <DragRightClick />
              </div>
            </VisibleIdsProvider>
          </TransactionSurfacesProvider>
        </LuciusChatProvider>
      </ScreenSnapshotProvider>
    </ChatComposerProvider>
  )
}
