import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { SETUP_STATUS_KEY } from './keys'

export interface SetupStatus {
  is_fresh: boolean
  restore_pending?: boolean
  /** Opaque identity of the book on the server; it changes when the data is reset or replaced. */
  book_id?: string
  /** When first run finished; null for a book set up before the stamp existed. */
  onboarding_completed_at?: string | null
}

const fetchSetupStatus = () =>
  fetchJson<SetupStatus>(SETUP_STATUS_KEY, { signal: AbortSignal.timeout(15_000) })

/**
 * Whether machine setup is needed, including after restoring a book on a fresh instance.
 */
export function useSetupStatus(): QueryCacheResult<SetupStatus> {
  const version = useDataVersion('accounts')
  return useQueryCache<SetupStatus>(SETUP_STATUS_KEY, fetchSetupStatus, {
    staleTime: 0,
    version,
  })
}
