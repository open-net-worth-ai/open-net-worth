// Settings → AI providers → Open Net Worth AI: the credit left, when it is known.
// Private Cloud asks its portal; a pasted key has no figure yet (the relay offers no key-info
// route), so nothing is shown rather than a guess. The key itself is never part of this read.
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { creditText, ONW_AI_STATUS_PATH } from './onwAiStatus'
import type { OnwAiStatus } from './onwAiStatus'

async function fetchStatus(): Promise<OnwAiStatus | null> {
  const r = await authFetch(ONW_AI_STATUS_PATH)
  if (!r.ok) return null
  return (await r.json()) as OnwAiStatus
}

export function OnwAiCredit() {
  const status = useCachedFetchState<OnwAiStatus | null>(ONW_AI_STATUS_PATH, fetchStatus, {
    staleTime: 60_000,
  })
  const credit = status.data?.credit
  if (!credit) return null
  return (
    <div className="rd-help" role="status">
      {creditText(credit)}
    </div>
  )
}
