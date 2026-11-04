import type { DelegationCreate, RunCreate } from '../../../types/trading'

export function defaultEnvironment(): RunCreate {
  return {
    mode: 'paper',
    start_at: new Date().toISOString(),
    end_at: new Date(Date.now() + 30 * 86400000).toISOString(),
    accounts: [
      { name: 'Trading allocation', currency: 'USD', capital: '1000', venue: 'simulation' },
    ],
    base_currency: 'USD',
    fx_rates: { USD: '1' },
    observations: [],
    data_version: 'supplied',
    agent_version: 'configured executor',
    live_authorized: false,
  }
}

export const defaultDelegation = (): DelegationCreate => ({
  workspace: {
    name: '',
    instructions: '',
    agent_kind: 'lucius',
    agent_label: 'Lucius',
    mandate: {
      allowed_asset_classes: ['stock', 'crypto'],
      max_order_notional: null,
      max_model_cost_usd: '10',
      fee_bps: '0',
      slippage_bps: '0',
      max_participation: '1',
      max_quote_age_seconds: 300,
      market_data_source: 'yahoo',
      instruments: [],
    },
  },
  environment: defaultEnvironment(),
  start: true,
})
