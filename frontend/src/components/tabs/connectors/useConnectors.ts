// The Connectors read. Feature-local (the `/api/connectors` key is not in
// `SHARED_READ_ENDPOINTS`), cached through the remount-safe layer so the tab and its
// modals can mount independently without refetching — the convention the existing
// Connectors tab already follows.

import { useMemo } from 'react'
import { authFetch } from '../../../api/client'
import {
  type QueryCacheResult,
  useCachedFetchState,
  useQueryCache,
} from '../../../state/queryCache'
import { toEntries } from './adapter'
import type { ConnectorDetailEntry } from './model'
import type { Connector } from './types'

export const CONNECTORS_KEY = '/api/connectors'

async function fetchConnectors(): Promise<Connector[]> {
  const response = await authFetch(CONNECTORS_KEY)
  if (!response.ok) throw new Error(`${CONNECTORS_KEY} → HTTP ${response.status}`)
  return (await response.json()).connectors as Connector[]
}

export interface ConnectorsRead extends Omit<QueryCacheResult<Connector[]>, 'data'> {
  /** The raw wire rows — for a screen that needs a field the view model drops. */
  connectors: Connector[]
  /** The INSTALLED connectors, adapted to the screen shape. */
  entries: ConnectorDetailEntry[]
}

export function useConnectors(): ConnectorsRead {
  const query = useCachedFetchState<Connector[]>(CONNECTORS_KEY, fetchConnectors, { staleTime: 0 })
  const connectors = useMemo(() => query.data ?? [], [query.data])
  const entries = useMemo(() => toEntries(connectors), [connectors])
  return { ...query, connectors, entries }
}

/** How many installed connectors the Attention filter lists — the nav's red dot. Polled, because
 *  a background re-read can fail with no write to announce it. */
export function useConnectorAttentionCount(): number {
  const { data } = useQueryCache<Connector[]>(CONNECTORS_KEY, fetchConnectors, {
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  return useMemo(() => toEntries(data ?? []).filter((e) => e.attention).length, [data])
}
