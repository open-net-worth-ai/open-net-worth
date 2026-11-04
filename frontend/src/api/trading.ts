import { authFetch } from './client'
import { invalidateQueryCache } from '../state/queryCache'
import type {
  Collection,
  TradingWorkspace,
  TradingRun,
  TradingAccount,
  TradingOrder,
  TradingFill,
  TradingEvent,
  Performance,
  TradingComparison,
  RunCreate,
} from '../types/trading'

export const tradingRoot = '/api/trading'
export async function tradingRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(`${tradingRoot}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const problem = detail?.detail
    const message = typeof problem === 'string' ? problem : problem?.message
    throw new Error(
      message || detail?.error || detail?.message || `Request failed (${response.status})`,
    )
  }
  const result = await response.json()
  if (result?.ok === false || result?.status === 'failed') {
    throw new Error(
      result.error || result.stderr || 'Execution failed. Inspect activity for details.',
    )
  }
  if (init?.method && init.method !== 'GET') invalidateQueryCache(tradingRoot)
  return result as T
}
const post = <T>(path: string, value: unknown) =>
  tradingRequest<T>(path, { method: 'POST', body: JSON.stringify(value) })
export const tradingApi = {
  workspaces: () => tradingRequest<Collection<TradingWorkspace>>('/workspaces'),
  workspace: (id: string) =>
    tradingRequest<TradingWorkspace>(`/workspaces/${encodeURIComponent(id)}`),
  createWorkspace: (data: Partial<TradingWorkspace>) => post<TradingWorkspace>('/workspaces', data),
  updateWorkspace: (id: string, data: Partial<TradingWorkspace>) =>
    tradingRequest<TradingWorkspace>(`/workspaces/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  runs: (id: string) =>
    tradingRequest<Collection<TradingRun>>(`/workspaces/${encodeURIComponent(id)}/runs`),
  allRuns: () => tradingRequest<Collection<TradingRun>>('/runs'),
  createRun: (id: string, data: RunCreate) =>
    post<TradingRun>(`/workspaces/${encodeURIComponent(id)}/runs`, data),
  run: (id: string) => tradingRequest<TradingRun>(`/runs/${encodeURIComponent(id)}`),
  accounts: (id: string) =>
    tradingRequest<Collection<TradingAccount>>(`/runs/${encodeURIComponent(id)}/accounts`),
  orders: (id: string) =>
    tradingRequest<Collection<TradingOrder>>(`/runs/${encodeURIComponent(id)}/orders`),
  fills: (id: string) =>
    tradingRequest<Collection<TradingFill>>(`/runs/${encodeURIComponent(id)}/fills`),
  events: (id: string, offset = 0) =>
    tradingRequest<Collection<TradingEvent>>(
      `/runs/${encodeURIComponent(id)}/events?offset=${offset}&limit=50`,
    ),
  performance: (id: string) =>
    tradingRequest<Performance>(`/runs/${encodeURIComponent(id)}/performance`),
  action: (id: string, action: string) =>
    post<TradingRun>(`/runs/${encodeURIComponent(id)}/actions`, { action }),
  cancelOrder: (id: string, orderId: string) =>
    tradingRequest<TradingOrder>(
      `/runs/${encodeURIComponent(id)}/orders/${encodeURIComponent(orderId)}`,
      { method: 'DELETE' },
    ),
  comparisons: () => tradingRequest<Collection<TradingComparison>>('/comparisons'),
  comparison: (id: string) =>
    tradingRequest<TradingComparison>(`/comparisons/${encodeURIComponent(id)}`),
  createComparison: (name: string, run_ids: string[]) =>
    post<TradingComparison>('/comparisons', { name, run_ids }),
  agent: (id: string, model: string) =>
    post<{ status: string }>(`/runs/${encodeURIComponent(id)}/agent`, model ? { model } : {}),
  script: (id: string, source: string) =>
    post<Record<string, unknown>>(`/runs/${encodeURIComponent(id)}/script`, { source }),
  issueAccess: (id: string) =>
    post<{ token: string; expires_at: string; endpoint: string }>(
      `/runs/${encodeURIComponent(id)}/access`,
      {},
    ),
  revokeAccess: (id: string) =>
    tradingRequest<{ ok: boolean }>(`/runs/${encodeURIComponent(id)}/access`, { method: 'DELETE' }),
}
