// plan/useOutlookFire.ts — seeds FireConfig from GET /api/fire + real net worth.
// Re-fetches when total (net worth) or fireVersion changes. Falls back to the
// planData FIRE defaults seeded with the real net worth on error/loading.
//
// Scenarios + FIRE baseline (levers, streams, events) are server-persisted via
// usePlanState → /api/plan/scenarios + /api/plan/baseline. This hook only seeds
// the initial FIRE config (ages, spend, rates) from /api/fire.

import { useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { fetchCachedQuery, getCachedQueryData, useQueryCache } from '../../../state/queryCache'
import { makeFireConfig } from './planData'
import type { FireConfig } from './planData'

// Fields we consume from GET /api/fire. Matches the FireResult shape in useFire.ts
// but we re-declare it here so this file has no cross-layer dependency on hooks/.
interface FireApiRow {
  // null without a date of birth on file (MATH-28) — never a fabricated 0.
  current_age: number | null
  retirement_target_age: number
  monthly_retirement_spending: number
  withdrawal_rate_pct: number
  expected_return_pct: number
  volatility_pct: number
  monthly_savings: number
  life_expectancy: number
  fire_number: number
  progress_pct: number
  years_to_fire: number | null
  projected_fire_age: number | null
  monthly_savings_needed: number | null
  on_track: boolean | null
  retirement_duration_years: number
}

function configFromRow(d: FireApiRow, total: number): FireConfig {
  return makeFireConfig({
    netWorth: total,
    // FireConfig.currentAge (and the Monte-Carlo/trajectory math it feeds throughout
    // the Outlook tab) stays a definite number — `?? undefined` degrades an unknown age
    // to "field not provided", which makeFireConfig's own null-skip guard already
    // resolves to the persona default (38) rather than the fabricated 0 this used to be
    // (MATH-28). Same for the two fields below that are only ever derived FROM the age.
    currentAge: d.current_age ?? undefined,
    targetAge: d.retirement_target_age,
    monthlyRetirementSpend: d.monthly_retirement_spending,
    withdrawalRate: d.withdrawal_rate_pct,
    expectedReturn: d.expected_return_pct,
    volatility: d.volatility_pct,
    monthlySaving: d.monthly_savings,
    lifeExpectancy: d.life_expectancy,
    fireNumber: d.fire_number,
    progressPct: d.progress_pct,
    yearsToFi: d.years_to_fire ?? 0,
    projectedFireAge:
      d.projected_fire_age ??
      (d.current_age != null ? d.current_age + (d.years_to_fire ?? 0) : undefined),
    monthlySavingNeeded: d.monthly_savings_needed ?? 0,
    onTrack: d.on_track ?? undefined,
    retirementDuration: d.retirement_duration_years,
  })
}

/**
 * Fetch /api/fire?current_nw=<total> and build a FireConfig seeded from real data.
 * ALWAYS fetches (a $0 net worth is valid — the backend resolves the config from the
 * profile, with its own defaults), so the displayed plan is the user's real data, never
 * the planData persona constants. Exposes `loading` so the Outlook can hold its skeleton
 * until the real config arrives instead of flashing seed defaults. Re-fetches whenever
 * `total` or `fireVersion` changes; `loading` only gates the FIRST resolve.
 */
export function useOutlookFire(
  total: number,
  fireVersion: number,
  totalLoading = false,
): {
  config: FireConfig
  loading: boolean
  error: boolean
  retry: () => void
  configVersion: number
} {
  const requestUrl = `/api/fire?current_nw=${Math.max(0, Math.round(total))}`
  const cached = getCachedQueryData<FireApiRow>(requestUrl)
  const [config, setConfig] = useState<FireConfig>(() =>
    cached ? configFromRow(cached, total) : makeFireConfig({ netWorth: Math.max(0, total) }),
  )
  const [loading, setLoading] = useState(!cached || totalLoading)
  const [retryCount, setRetryCount] = useState(0)
  const prevVer = useRef(fireVersion)
  const pendingVersionRefresh = useRef<number | null>(null)
  // The fireVersion that `config` currently reflects — set ONLY inside the fetch's success
  // callback, alongside setConfig itself, so a consumer keying off this (instead of racing on
  // `loading`, which flips via a SEPARATE effect one render ahead of the fetch settling) can
  // never observe a still-stale `config` for the version it thinks it already applied.
  const [configVersion, setConfigVersion] = useState(cached && !totalLoading ? fireVersion : -1)
  // Disabled while the caller's net worth is still an unconfirmed 0 placeholder — otherwise
  // this fetches (and briefly settles on) /api/fire?current_nw=0 for every real user, and the
  // sim it unblocks runs a real Monte-Carlo pass against $0 net worth, flashing a wrong-but
  // real-looking odds % before the true net worth lands and corrects it.
  const fetchRow = async () => {
    const response = await authFetch(requestUrl)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return response.json() as Promise<FireApiRow>
  }
  // fireVersion is NOT this query's version: the effect below owns version refreshes, with a
  // forced fetch (a second, non-forced one here would only duplicate the request).
  const query = useQueryCache(requestUrl, fetchRow, {
    staleTime: 0,
    version: retryCount,
    enabled: !totalLoading,
  })

  useEffect(() => {
    // Re-gate (show the loading state, which lets the Outlook re-seed its levers) on the FIRST
    // load and whenever the user SAVES new FIRE settings (fireVersion bump) — but NOT on a plain
    // net-worth (total) refresh, which must not reset the user's in-progress lever tweaks.
    let cancelled = false
    const versionChanged = fireVersion !== prevVer.current
    if (versionChanged) {
      prevVer.current = fireVersion
      pendingVersionRefresh.current = fireVersion
    }
    void Promise.resolve().then(async () => {
      if (cancelled) return
      // Net worth itself hasn't resolved yet — `total` is a placeholder, so stay loading
      // rather than settle on a config/sim seeded from it.
      if (totalLoading) {
        setLoading(true)
        return
      }
      if (versionChanged) {
        // Only before the first config: a later version advance is usually a write elsewhere in
        // the app ('*'), and re-gating then paused the scenario odds and screen commands for
        // every one of them while showing nothing new.
        if (configVersion === -1) setLoading(true)
        try {
          // Forced: the advance means something was just written, and joining a fetch already
          // in flight could return the row from BEFORE that write — which, once handled, looks
          // like "nothing changed" and drops e.g. a Lucius profile edit.
          const row = await fetchCachedQuery(requestUrl, fetchRow, { force: true })
          if (cancelled) return
          setConfig(configFromRow(row, total))
          setLoading(false)
          setConfigVersion(fireVersion)
          pendingVersionRefresh.current = null
        } catch {
          if (!cancelled) setLoading(false)
        }
        return
      }
      if (pendingVersionRefresh.current === fireVersion) {
        if (query.refreshing || query.error) return
        pendingVersionRefresh.current = null
      }
      if (!query.data) {
        if (!query.initialLoading) setLoading(false)
        return
      }
      setConfig(configFromRow(query.data, total))
      setLoading(false)
      setConfigVersion(fireVersion)
    })
    return () => {
      cancelled = true
    }
    // configVersion is read only to gate `loading` before the first config; re-running when it
    // advances would just re-apply the same row.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    fireVersion,
    query.data,
    query.error,
    query.initialLoading,
    query.refreshing,
    total,
    totalLoading,
  ])

  return {
    config,
    loading,
    error: !!query.error,
    retry: () => {
      setRetryCount((c) => c + 1)
      void query.refresh()
    },
    configVersion,
  }
}
