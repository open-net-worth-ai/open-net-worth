import { useQueryCache } from '../state/queryCache'
import { tradingApi, tradingRoot } from '../api/trading'

export function useTradingWorkspaces() {
  return useQueryCache(`${tradingRoot}/workspaces`, tradingApi.workspaces)
}
export function useTradingRuns(workspaceId: string) {
  return useQueryCache(
    `${tradingRoot}/workspaces/${workspaceId}/runs`,
    () => tradingApi.runs(workspaceId),
    { enabled: !!workspaceId },
  )
}
export function useTradingOverview(workspaceId: string) {
  return useQueryCache(
    `${tradingRoot}/workspaces/${workspaceId}/overview`,
    () => tradingApi.overview(workspaceId),
    { enabled: !!workspaceId },
  )
}
export function useTradingRun(runId: string) {
  const enabled = !!runId
  const root = `${tradingRoot}/runs/${runId}`
  const run = useQueryCache(root, () => tradingApi.run(runId), { enabled })
  const accounts = useQueryCache(`${root}/accounts`, () => tradingApi.accounts(runId), { enabled })
  const orders = useQueryCache(`${root}/orders`, () => tradingApi.orders(runId), { enabled })
  const fills = useQueryCache(`${root}/fills`, () => tradingApi.fills(runId), { enabled })
  const performance = useQueryCache(`${root}/performance`, () => tradingApi.performance(runId), {
    enabled,
  })
  return { run, accounts, orders, fills, performance }
}
