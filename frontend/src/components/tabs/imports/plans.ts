import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import { detailOf } from './runs'

export interface Plan {
  id: string
  name: string
  currency: string | null
  encrypted?: boolean
}

/** The plans a saved sign-in can see. */
export function usePlans(app: BudgetAppInfo, enabled: boolean) {
  return useQueryCache<Plan[]>(
    `${app.apiPrefix}/budgets`,
    async () => {
      const r = await authFetch(`${app.apiPrefix}/budgets`)
      if (!r.ok)
        throw new Error(await detailOf(r, `Could not list your ${app.name} ${app.planWord}s.`))
      return ((await r.json()) as { budgets: Plan[] }).budgets
    },
    { enabled },
  )
}
