import { authFetch } from './client'
import { invalidateQueryCache } from '../state/queryCache'

export interface BinanceStatus {
  connector_id: 'binance'
  configured: boolean
  enabled: boolean
  environment: 'testnet' | 'live'
  quote_currency: string
  account_scope: 'dedicated_account' | 'subaccount'
  isolated_account: boolean
  has_api_key: boolean
  has_api_secret: boolean
  broker_account_id?: string
  venue: string
  verification: 'private_account_checked' | 'unverified'
  verified_at: string | null
  available_cash: string | number | null
  total_cash: string | number | null
  non_quote_balances: number | null
  open_orders: number | null
}
export interface BinanceSettings {
  environment: BinanceStatus['environment']
  quote_currency: string
  account_scope: BinanceStatus['account_scope']
  isolated_account: boolean
  enabled: boolean
  api_key?: string
  api_secret?: string
}
async function request(path: string, init?: RequestInit): Promise<BinanceStatus> {
  const response = await authFetch(`/api/binance/${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Could not check Binance settings.')
  }
  if (init?.method) invalidateQueryCache('/api/binance/status')
  return data
}
export const binanceApi = {
  status: () => request('status'),
  configure: (settings: BinanceSettings) => request('config', { method: 'PUT', body: JSON.stringify(settings) }),
  test: () => request('test', { method: 'POST' }),
}
