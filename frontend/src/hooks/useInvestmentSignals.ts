// Cross-tab Portfolio Brief signal aggregator (Wave 2, RFC 09-cross-tab).
//
// Thin fetch wrapper — all signal computation has moved to the backend
// (GET /api/investments/brief). This hook fetches and maps the response.
//
// The Investments brief and the Dashboard's Needs attention both read it, so an open decision
// is the same decision on both.
//
// On any error the hook degrades silently to the on-track fallback.

import { querySource } from './serverState/dataQuery'
import { useQueryCache } from '../state/queryCache'
import type { InvestmentSignal } from '../types/investmentSignals'

export interface SignalsResult {
  signals: InvestmentSignal[]
  decisionsCount: number
  loading: boolean
}

// On-track fallback — emitted when fetch fails or backend returns empty.
const ONTRACK: InvestmentSignal = {
  id: 'ontrack',
  kind: 'ontrack',
  tone: 'good',
  actionable: false,
  tag: 'On track',
  narrative: 'Your portfolio is within guidelines. No rebalancing or concentration alerts.',
}

export function useInvestmentSignals(): SignalsResult {
  const query = useQueryCache<Omit<SignalsResult, 'loading'>>(
    '/api/investments/brief',
    async () => {
      const data = await querySource<{ signals?: unknown; decisions_count?: number }>(
        'investments_brief',
        {},
        '/api/investments/brief',
      ).catch((err: unknown) => {
        // An HTTP error answer means "no brief" → the fallback. A network failure stays an
        // error, so the cache keeps the last real signals instead of replacing them.
        if (err instanceof Error && /→ HTTP \d+$/.test(err.message)) return null
        throw err
      })
      if (!data || !Array.isArray(data.signals)) {
        return { signals: [ONTRACK], decisionsCount: 0 }
      }
      return {
        signals: data.signals as InvestmentSignal[],
        decisionsCount: data.decisions_count ?? 0,
      }
    },
    { staleTime: 0 },
  )
  if (query.data) return { ...query.data, loading: false }
  if (query.error) return { signals: [ONTRACK], decisionsCount: 0, loading: false }
  return { signals: [], decisionsCount: 0, loading: true }
}
