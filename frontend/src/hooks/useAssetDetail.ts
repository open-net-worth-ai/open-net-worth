import { useCallback, useEffect, useRef, useState } from 'react'
import { useDataVersion } from './useDataVersion'
import { authFetch } from '../api/client'
import { keyIssuesPassActive, type AssetDetail } from '../types/assetDetail'

// How old a last_refreshed_at can be before we auto-trigger a background refresh.
const STALE_MS = 60 * 60 * 1000 // 1 hour

function isStale(lastRefreshedAt: string | null): boolean {
  if (!lastRefreshedAt) return true
  try {
    return Date.now() - new Date(lastRefreshedAt).getTime() > STALE_MS
  } catch {
    return true
  }
}

// Session cache of the last-seen detail per instrument. Lets a reopen render the
// last-known view INSTANTLY (stale-while-revalidate) instead of blanking to the
// skeleton for ~10s while the backend re-fetches cold live prices. The skeleton is
// reserved for the genuine first load (nothing cached yet).
const detailCache = new Map<string, AssetDetail>()

/** Test-only: reset the module cache between cases. */
export function _clearDetailCache(): void {
  detailCache.clear()
}

/** Seeds the cache for an instrument this hook hasn't fetched by id yet — used by
 *  useResolveTicker so the /quote/$symbol route's one-hop by-ticker fetch renders
 *  instantly instead of a second, redundant GET-by-id before first paint. The mount
 *  effect below still revalidates in the background (the existing stale-while-
 *  revalidate path), matching how a reopen behaves. */
export function seedDetailCache(instrumentId: string, data: AssetDetail): void {
  detailCache.set(instrumentId, data)
}

interface UseAssetDetailResult {
  data: AssetDetail | null
  loading: boolean
  refreshing: boolean
  /** True while a backend-initiated background pass (the on-demand key_issues top-up — no
   *  POST from this hook) is in flight, per its own key_issues_refresh_stage — the quiet
   *  counterpart to `refreshing` for the SAME `RefreshingBanner`, never the full-skeleton
   *  takeover (this hook already has content to show whenever this fires). See
   *  BACKGROUND_REFRESH_WATCH_MS. */
  backgroundRefreshing: boolean
  error: boolean
  /** POST refresh + refetch (shows refreshing state, makes LLM/search call). */
  refresh: () => Promise<void>
  /** GET-only refetch — updates data without triggering a search+LLM refresh. */
  refetch: () => Promise<void>
  /** Set briefly after an explicit Refresh tap the backend declined (rate-limited, already
   *  fresh, etc.) — the backend returns 200 with `{refreshed: false, reason}` almost
   *  instantly in that case, so the button re-enables right away with nothing visibly
   *  different, inviting a confused user to tap it again and again (owner report
   *  2026-08-11: "i have to tap multiple times ... ah, it hit rate limit"). Auto-clears. */
  refreshNotice: string | null
}

// Backend `reason` (asset_brief.refresh_asset_brief) -> a short human explanation for why an
// explicit Refresh tap didn't actually trigger a new search+LLM pass.
const REFRESH_DECLINED_MESSAGES: Record<string, string> = {
  rate_limited: 'Just refreshed — try again shortly',
  fresh: 'Already up to date',
  search_unconfigured: "Search isn't configured",
  no_ticker: 'No ticker to research',
  refresh_failed: 'Refresh failed — try again',
}
const REFRESH_NOTICE_MS = 4000

// How long to keep the quiet "background refresh" indicator (+ its fast poll) alive while
// waiting for the on-demand key_issues top-up (backend.monitoring.analyst.
// trigger_on_demand_brief_refresh) to actually land real content — this hook did NOT itself
// request that pass via triggerRefresh, so the usual `refreshing` flag (bounded to an
// in-flight POST) never covers it. "Landed" is `data.has_narrative`, the backend's OWN bar for
// "this pass produced real content" (analyst._key_issues_force_stale's `has_content` check) —
// NOT key_issues_refresh_stage === 'ready': the backend stamps "ready" the instant its pass
// finishes even when that pass only filled key_issues and left portfolio_narrative empty (a
// live, documented model-turn gap), and in exactly that case it retries again in the
// background every analyst._KEY_ISSUES_NEVER_PRODUCED_RETRY_MINUTES (5 minutes) until a fuller
// pass lands. This window must comfortably outlast that 5-minute retry cadence plus one more
// LLM call's latency, or the fast poll gives up (and the "generating" indicator disappears)
// long before the backend even tries again — leaving a half-filled result on screen looking
// finished (owner-reproduced, UBER open-then-watch, 2026-09-03; the previous 60s value
// predates that retry cadence and was never long enough to see it). Still bounded rather than
// trusting the backend indefinitely: a wedged process (a hard crash mid-pass) could in
// principle never land has_narrative at all — this timeout self-heals that by quietly
// dropping the generating indicator rather than pinning it on the page forever.
const BACKGROUND_REFRESH_WATCH_MS = 8 * 60_000

export function useAssetDetail(instrumentId: string): UseAssetDetailResult {
  const [data, setData] = useState<AssetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(false)
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null)
  // Prevent double-refresh on concurrent effects
  const refreshingRef = useRef(false)
  // See BACKGROUND_REFRESH_WATCH_MS above — true only while this hook is WATCHING a
  // key_issues_refresh_stage it didn't itself trigger via a POST.
  const [backgroundRefreshing, setBackgroundRefreshing] = useState(false)
  // Bumped when a refresh settles with the narrative still missing, restarting the watch.
  const [watchEpoch, setWatchEpoch] = useState(0)

  useEffect(() => {
    if (!refreshNotice) return
    const timer = setTimeout(() => setRefreshNotice(null), REFRESH_NOTICE_MS)
    return () => clearTimeout(timer)
  }, [refreshNotice])

  // The asset this hook is CURRENTLY mounted for. A slow async op (a refresh's refetch, the 60s
  // poll, a late initial GET) captures the id it began with and commits to the live view ONLY if
  // the hook is still on that id — so a refresh that finishes after the user opened another ticker
  // can't clobber the new view (the "it jumps back to KSTL" race). Cache writes stay unconditional:
  // the result is valid data for its own id, and is correct to cache for a later reopen.
  const activeKeyRef = useRef(instrumentId)
  useEffect(() => {
    activeKeyRef.current = instrumentId
  }, [instrumentId])

  // `quiet` (used by the 60s background poll) keeps the last-good view on a transient
  // failure instead of flipping the whole screen to the error state.
  const fetchData = useCallback(
    async (opts?: { quiet?: boolean }): Promise<AssetDetail | null> => {
      const myKey = instrumentId
      const isStale = () => myKey !== activeKeyRef.current
      try {
        const res = await authFetch(`/api/intel/assets/${instrumentId}`)
        if (!res.ok) {
          if (!isStale() && !opts?.quiet) setError(true)
          return null
        }
        const d: AssetDetail = await res.json()
        detailCache.set(myKey, d)
        if (isStale()) return d // hook moved to another asset — don't touch the live view
        setData(d)
        setError(false)
        return d
      } catch {
        if (!isStale() && !opts?.quiet) setError(true)
        return null
      }
    },
    [instrumentId],
  )

  // force=true bypasses the 15-min freshness window (used by the explicit Refresh button,
  // where the user is asking for fresh data NOW). The 60s anti-spam floor still applies.
  // The on-open auto-refresh passes force=false so it respects the freshness window.
  const triggerRefresh = useCallback(
    async (force = false): Promise<void> => {
      if (refreshingRef.current) return
      refreshingRef.current = true
      setRefreshing(true)
      try {
        const qs = force ? '?force=true' : ''
        const res = await authFetch(`/api/intel/assets/${instrumentId}/refresh${qs}`, {
          method: 'POST',
        })
        // The backend answers a declined refresh (rate-limited / already fresh / etc.) with
        // 200 {refreshed: false, reason} almost instantly — surface it on an EXPLICIT tap
        // (force) so the user knows why nothing changed instead of tapping again. The silent
        // on-open auto-refresh (force=false) stays silent — the user never asked for it.
        if (force && res.ok) {
          const body: { refreshed?: boolean; reason?: string; take_refreshing?: boolean } =
            await res.json().catch(() => ({}))
          // The news half can decline (refreshed recently) while Lucius's take is still being
          // rewritten — that is progress, not "try again shortly".
          if (body.refreshed === false && !body.take_refreshing) {
            setRefreshNotice(
              (body.reason && REFRESH_DECLINED_MESSAGES[body.reason]) || 'Refresh unavailable',
            )
          }
        }
        const fresh = await fetchData()
        // The POST covers news/facts/peers only; the bull/bear pass the GET kicked off is still
        // running, and by now the watch may have lapsed — keep watching for it.
        if (fresh?.has_ticker && !fresh.has_narrative && instrumentId === activeKeyRef.current) {
          setWatchEpoch((n) => n + 1)
        }
      } finally {
        refreshingRef.current = false
        setRefreshing(false)
      }
    },
    [instrumentId, fetchData],
  )

  // On mount: show the last-known view immediately if we have it (skeleton only on a
  // true first load), then revalidate in the background and auto-refresh if stale/empty.
  useEffect(() => {
    let alive = true
    const cached = detailCache.get(instrumentId)
    if (cached) {
      setData(cached)
      setError(false)
      setLoading(false)
    } else {
      setData(null)
      setError(false)
      setLoading(true)
    }

    // quiet when we already showed cached data — a failed revalidate must not blank
    // the view to an error state; the stale data stays put.
    fetchData({ quiet: !!cached }).then((fresh) => {
      if (!alive) return
      setLoading(false)
      const eff = fresh ?? cached ?? null
      // A HELD instrument, or a WATCHED (following + monitor_news) instrument, is kept warm by
      // the Analyst pipeline's own background precompute (event-triggered + steady-cadence —
      // see backend.monitoring.analyst's _enqueue_stale_briefs / ADR
      // 2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation's Correction section),
      // so opening either never needs an on-open POST refresh (the ~1-minute wait this fix
      // removes) — that background pass is what keeps Lucius's Take fresh instead. Matches the
      // backend gate exactly (_is_held_instrument OR _is_watched_instrument): key_issues is no
      // longer held-only, since a bull/bear debate about the stock doesn't require owning it.
      // monitor_news defaults true when absent (same convention as the backend's own
      // `item.get("monitor_news", True)`) — only an EXPLICITLY muted watch item skips this.
      // The fallback below stays for everything else — an ad-hoc search
      // (/quote/$symbol on something never added to the book), or a muted watch item — exactly
      // as before this change.
      const isHeld = !!eff?.position
      const isWatched = !!eff?.watchlist?.following && eff.watchlist.monitor_news !== false
      // Only auto-refresh assets that actually have a news source. A no-ticker manual
      // holding can never fetch news/analysis, so kicking off a refresh just flashes the
      // "researching…" skeleton before landing on the "news unavailable" empty state — a
      // skeleton for data that can't arrive (and a wasted POST). Gate it on has_ticker.
      const needsResearch =
        !isHeld &&
        !isWatched &&
        !!eff?.has_ticker &&
        ((eff.news.length === 0 && eff.key_issues.length === 0) || isStale(eff.last_refreshed_at))
      if (needsResearch && !refreshingRef.current) {
        void triggerRefresh()
      }
    })

    return () => {
      alive = false
    }
  }, [instrumentId, fetchData, triggerRefresh])

  // Detects the backend's on-demand key_issues top-up (backend.monitoring.analyst.
  // trigger_on_demand_brief_refresh, fired from the GET route itself for a resolvable-ticker
  // instrument) — this hook never issued a POST for it, so the usual `refreshing` local flag
  // (bounded to an in-flight POST) never covers it. Keyed on `data.has_narrative` — see
  // BACKGROUND_REFRESH_WATCH_MS above for why that, not `key_issues_refresh_stage === 'ready'`,
  // is the real "landed" signal.
  //
  // Deliberately NOT gated on `refreshingRef.current` (the prior version skipped this effect
  // entirely whenever an explicit POST was in flight) — that gate meant a transition landing
  // WHILE `refreshing` was true got silently missed: `refreshing` flips back to false without
  // re-running this effect (it's a ref, not a dependency), so by the time this effect could
  // next act, a later poll may have already carried has_narrative true with nobody ever having
  // armed the watch. Watching unconditionally is safe — the render layer already ORs
  // `refreshing || backgroundRefreshing`, so both being true at once (the concurrent cold-open
  // case) shows one banner, not two.
  //
  // Keyed on `instrumentId` and `watchEpoch` too, with the timer owned by the effect's cleanup:
  // this hook instance is reused across navigation, and keying on has_narrative alone meant a
  // cached reopen of a second not-yet-narrated asset changed no dependency, so the mount
  // effect's clear of the PREVIOUS asset's watch left this one with none at all.
  const needsWatch = !!data?.has_ticker && !data?.has_narrative
  useEffect(() => {
    let live = true
    // Deferred to a microtask (react-hooks/set-state-in-effect — same pattern as
    // useMatchReviews.ts's own load() defer); `live` drops it if a re-run superseded this one.
    void Promise.resolve().then(() => {
      if (live) setBackgroundRefreshing(needsWatch)
    })
    if (!needsWatch) {
      return () => {
        live = false
      }
    }
    const timer = setTimeout(() => setBackgroundRefreshing(false), BACKGROUND_REFRESH_WATCH_MS)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [instrumentId, needsWatch, watchEpoch])

  // Keep the headline price / "As of" (and the position rail's market value) current
  // while the view stays open: poll the cheap GET every 60s, matching the backend's
  // 60s live-price cache. GET-only — never the expensive POST refresh. Paused while
  // the tab is hidden, and skipped while a manual refresh is already in flight; a
  // visibilitychange tick catches up the moment the tab is shown again.
  const anyWriteV = useDataVersion('*')
  useEffect(() => {
    if (anyWriteV > 0 && !refreshingRef.current) void fetchData({ quiet: true })
  }, [anyWriteV, fetchData])

  useEffect(() => {
    const tick = () => {
      if (!document.hidden && !refreshingRef.current) void fetchData({ quiet: true })
    }
    const timer = setInterval(tick, 60_000)
    document.addEventListener('visibilitychange', tick)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [fetchData])

  // While a refresh (explicit POST, OR the backend's own on-demand background top-up) is in
  // flight, poll much faster so the progressive refresh_stage/facts/peers writes (see
  // asset_brief._land_analysis) become visible on screen as they land, instead of the page
  // sitting on the "Refreshing…" banner until the whole thing resolves. news[] itself is no
  // longer part of this landing sequence — it's served live from the evidence table on every
  // read (asset_detail._live_news_for_instrument), not written by a refresh at all. Also while
  // the backend stamps its key_issues pass as running, which can outlast both client flags.
  const keyIssuesPassRunning = keyIssuesPassActive(data)
  useEffect(() => {
    if (!refreshing && !backgroundRefreshing && !keyIssuesPassRunning) return
    const timer = setInterval(() => {
      if (!document.hidden) void fetchData({ quiet: true })
    }, 2000)
    return () => clearInterval(timer)
  }, [refreshing, backgroundRefreshing, keyIssuesPassRunning, fetchData])

  const manualRefresh = useCallback(async () => {
    await triggerRefresh(true)
  }, [triggerRefresh])

  const refetch = useCallback(async () => {
    await fetchData()
  }, [fetchData])

  return {
    data,
    loading,
    refreshing,
    backgroundRefreshing,
    error,
    refresh: manualRefresh,
    refetch,
    refreshNotice,
  }
}
