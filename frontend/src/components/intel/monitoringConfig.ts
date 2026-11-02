// The Intel monitoring config (`global_config` on the watchlist store) — ONE type and ONE hook,
// shared by Settings › Intelligence (where every control lives) and the Intel tab (which reads
// the brief verbosity and the monitoring state). The hook writes through the same query-cache key
// the Intel tab polls, so a change in Settings shows on the tab without a refetch.
import { useCallback } from 'react'
import { authFetch } from '../../api/client'
import { setCachedQueryData, useQueryCache } from '../../state/queryCache'
import { fDuration } from '../../utils/format'

export interface CrashThresholds {
  equity: number
  commodity: number
  crypto: number
}

export type OpinionLevel = 'information' | 'lean' | 'suggest'
export type BriefVerbosity = 1 | 2 | 3 | 4 | 5

export interface MonitoringConfig {
  price_check_interval_seconds: number
  news_scan_interval_seconds: number
  auto_scan_holdings?: boolean
  enabled: boolean
  notify_routes?: Partial<NotifyRoutes>
  search_budget?: number
  last_scan_at?: string | null
  last_price_check_at?: string | null
  web_search_enabled?: boolean
  news_freshness?: 'off' | 'balanced' | 'fast'
  outlet_feeds_enabled?: boolean
  crash_thresholds?: CrashThresholds
  spike_min_abs?: number
  spike_factor?: number
  // AI analysis on watched tickers (ADR 2026-09-27-watch-story-reads-and-opinion-level)
  explained_alerts_enabled?: boolean
  watch_reads_enabled?: boolean
  watch_reads_per_day?: number
  ai_opinion_level?: OpinionLevel
  holdings_brief_verbosity?: BriefVerbosity
  watchlist_brief_verbosity?: BriefVerbosity
  // The user's own words on what Intel should tell them (experiment: intel_delivery_decider)
  delivery_instructions?: string
}

export interface AlertChannel {
  id: string
  label: string
  available: boolean
}

// Channels the backend can send to but the Connectors screen offers no way to add
// (connectors/catalog.ts: webhook is "intentionally not an addable card"). Offering one that is
// not already set up would point the user at a setup screen that does not exist.
const NOT_ADDABLE = new Set(['webhook'])

/** The channels worth showing: every one that works, plus an addable one to set up, plus any
 * `inUse` id (so a saved choice is never hidden) — never a dead end. */
export function visibleChannels(channels: AlertChannel[], inUse: string[] = []): AlertChannel[] {
  return channels.filter((c) => c.available || !NOT_ADDABLE.has(c.id) || inUse.includes(c.id))
}

// Where each kind of Intel update is ALSO sent (in-app is always on). `price_alerts` is only the
// default a NEW alert starts from — each alert saves its own channels. Mirrors
// backend/services/notify_routes.py.
export type NotifyKind = 'price_alerts' | 'watch_alerts' | 'news'
export type NotifyRoutes = Record<NotifyKind, string[]>

export const NOTIFY_KINDS: { id: NotifyKind; label: string; sub: string }[] = [
  {
    id: 'price_alerts',
    label: 'Price alerts',
    sub: 'Default for new alerts. Each alert can change its own.',
  },
  { id: 'watch_alerts', label: 'Watch alerts', sub: 'Big moves on what you hold or watch.' },
  { id: 'news', label: 'News analyses', sub: 'Lucius on news that moves your names.' },
]

export function notifyRoutes(cfg: Pick<MonitoringConfig, 'notify_routes'> | null): NotifyRoutes {
  const r = cfg?.notify_routes ?? {}
  return {
    price_alerts: r.price_alerts ?? [],
    watch_alerts: r.watch_alerts ?? [],
    news: r.news ?? [],
  }
}

// How far the AI's opinion goes (Settings › Intelligence) — the same three levels the backend
// checks (monitoring/ai_opinion.py).
export const OPINION_OPTIONS: { value: OpinionLevel; label: string; help: string }[] = [
  {
    value: 'information',
    label: 'Information only',
    help: 'Lucius explains what happened and what it could mean for you, and lists considerations. It never takes a side.',
  },
  {
    value: 'lean',
    label: 'Show a lean',
    help: 'Adds Lucius’s overall lean — bullish, bearish or neutral — with its reason. Still no instructions to buy or sell.',
  },
  {
    value: 'suggest',
    label: 'Allow suggestions',
    help: 'Adds the lean and at most one suggestion framed against your own thesis and buy zone. Never urgent, never a promise.',
  },
]

export const NOT_ADVICE = "Lucius's view, not financial advice. The decision is always yours."

export const DEFAULT_CRASH: CrashThresholds = { equity: 12, commodity: 15, crypto: 25 }
export const PRICE_OPTS = [60, 300, 900, 1800]
export const NEWS_OPTS = [14400, 28800, 43200, 86400]

export const FRESHNESS_OPTS: { value: 'off' | 'balanced' | 'fast'; label: string }[] = [
  { value: 'off', label: 'Off (no fast poll)' },
  { value: 'balanced', label: 'Balanced (15 min)' },
  { value: 'fast', label: 'Fast (5 min)' },
]

// A native <select> silently shows its first <option> when its value matches
// none — which would misrepresent a cadence set off-menu (e.g. 1h via Lucius).
// Surface the real value as an extra, sorted option so the control never lies.
export function intervalOptions(opts: number[], current: number) {
  const values = opts.includes(current) ? opts : [...opts, current].sort((a, b) => a - b)
  return values.map((v) => ({ value: String(v), label: fDuration(v) }))
}

/** The brief verbosity a config asks for, defaulting to 3 (the standard one-to-two lines). */
export function verbosityOf(
  cfg: Pick<MonitoringConfig, 'holdings_brief_verbosity' | 'watchlist_brief_verbosity'> | null,
  which: 'holdings' | 'watchlist',
): BriefVerbosity {
  const raw = which === 'holdings' ? cfg?.holdings_brief_verbosity : cfg?.watchlist_brief_verbosity
  return raw && raw >= 1 && raw <= 5 ? raw : 3
}

const WATCHLIST_KEY = '/api/watchlist'

interface WatchlistPayload {
  config: MonitoringConfig
  [key: string]: unknown
}

async function getJson<T>(url: string): Promise<T> {
  const response = await authFetch(url)
  if (!response.ok) throw new Error(String(response.status))
  return response.json()
}

function withConfig(changes: Partial<MonitoringConfig>) {
  return (prev: WatchlistPayload | undefined): WatchlistPayload =>
    ({ ...(prev ?? {}), config: { ...(prev?.config ?? {}), ...changes } }) as WatchlistPayload
}

/** The config plus the alert channels, and a `patch` that updates optimistically, saves, and
 *  rolls back (returning false) when the save fails. */
export function useMonitoringConfig() {
  const watchlist = useQueryCache<WatchlistPayload>(
    WATCHLIST_KEY,
    () => getJson<WatchlistPayload>(WATCHLIST_KEY),
    { staleTime: 0 },
  )
  const channels = useQueryCache<AlertChannel[]>(
    '/api/alert-channels',
    async () =>
      (await getJson<{ channels?: AlertChannel[] }>('/api/alert-channels')).channels ?? [],
    { staleTime: 60_000 },
  )
  const cfg = watchlist.data?.config ?? null

  const patch = useCallback(
    async (changes: Partial<MonitoringConfig>): Promise<boolean> => {
      const before = watchlist.data?.config
      setCachedQueryData<WatchlistPayload>(WATCHLIST_KEY, withConfig(changes))
      try {
        const res = await authFetch('/api/watchlist/config', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(changes),
        })
        if (!res.ok) throw new Error(String(res.status))
        const saved = (await res.json()) as MonitoringConfig
        setCachedQueryData<WatchlistPayload>(WATCHLIST_KEY, withConfig(saved))
        return true
      } catch {
        if (before) setCachedQueryData<WatchlistPayload>(WATCHLIST_KEY, withConfig(before))
        return false
      }
    },
    [watchlist.data?.config],
  )

  return {
    cfg,
    channels: channels.data ?? [],
    loading: watchlist.initialLoading && !cfg,
    patch,
  }
}
