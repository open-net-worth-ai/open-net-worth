import { authFetch } from '../../../../api/client'
import { useFlag } from '../../../../hooks/useFlag'
import { useQueryCache } from '../../../../state/queryCache'
import { detailOf } from '../../imports/runs'
import { ACTUAL_APP, YNAB_APP, type BudgetAppInfo } from './budgetApps'
import { useBudgetSync, type BudgetSync } from './ynabSync'

export interface DataSyncEntry {
  app: BudgetAppInfo
  sync: BudgetSync
}

/** Whether Actual can run here at all. Its sync routes answer 409 behind the gateway, so the
 *  connector's status is what says so. */
function useActualAvailable(flagOn: boolean): boolean {
  const key = `${ACTUAL_APP.apiPrefix}/status`
  const status = useQueryCache<{ available?: boolean }>(
    key,
    async () => {
      // Every write revalidates every subscriber, so this is what keeps a flag that's off quiet.
      if (!flagOn) return undefined as unknown as { available?: boolean }
      const r = await authFetch(key)
      if (!r.ok) throw new Error(await detailOf(r, 'Could not read the Actual connector.'))
      return (await r.json()) as { available?: boolean }
    },
    { enabled: flagOn },
  )
  // Only the server saying no takes Actual away: a failed read shows as its sync failing to load.
  return flagOn && (status.data ? status.data.available !== false : !!status.error)
}

/** The data syncs this instance offers, in fixed order: an app whose flag is off, or which can't
 *  run here, isn't one of them. */
export function useDataSyncs(): DataSyncEntry[] {
  const ynabOn = useFlag(YNAB_APP.flag) === true
  const actualOn = useActualAvailable(useFlag(ACTUAL_APP.flag) === true)
  const ynab = useBudgetSync(YNAB_APP, false, ynabOn)
  const actual = useBudgetSync(ACTUAL_APP, false, actualOn)
  return [
    ...(ynabOn ? [{ app: YNAB_APP, sync: ynab }] : []),
    ...(actualOn ? [{ app: ACTUAL_APP, sync: actual }] : []),
  ]
}
