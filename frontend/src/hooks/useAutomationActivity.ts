import { useCallback } from 'react'
import { getAutomationActivity, type AutomationActivity } from '../api/automations'
import { invalidateQueryCache, useQueryCache, type QueryCacheResult } from '../state/queryCache'

const ACTIVITY_KEY = '/api/automations/activity'

/**
 * useAutomationActivity — the Lucius landing screen's Activity panel (Active runs +
 * Scheduled automations), backed by ONE aggregator read (GET /api/automations/activity →
 * services/automations/activity.py). Mirrors useInbox.ts's useQueryCache shape rather than
 * Automations.tsx's own local useState+useEffect fetch — this hook has exactly one consumer
 * today (ActivityPanel) but is small enough, and changes often enough while a run is live,
 * to earn the shared cache's poll-dedup for free. Automations has no `data_changed` domain
 * of its own (the full Automations tab reloads explicitly after each action instead), so
 * this polls on the same 30s cadence useIntelSignals/useInbox-adjacent widgets use rather
 * than wiring into that bus.
 */
export function useAutomationActivity(): QueryCacheResult<AutomationActivity> {
  const fetcher = useCallback(() => getAutomationActivity(), [])
  return useQueryCache<AutomationActivity>(ACTIVITY_KEY, fetcher, {
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
}

/** Re-read the panel now rather than at the next poll. A read stamp clears a failed run from
 *  Needs attention, and nothing else tells this cache that one landed. */
export function refreshAutomationActivity(): void {
  invalidateQueryCache(ACTIVITY_KEY)
}
