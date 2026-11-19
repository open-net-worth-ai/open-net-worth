import type { TaContext, TaRun, TaRunSummary, TaStatus } from '../../types/tradingAgents'

// Synthetic TradingAgents payloads for the card's tests: ticker ACME, made-up amounts.

export const CONTEXT: TaContext = {
  ticker: 'ACME',
  portfolio: {
    cash: 9400,
    currency: 'USD',
    positions: [
      { ticker: 'ACME', quantity: 120, average_price: 41.5 },
      { ticker: 'GLOBEX', quantity: 30, average_price: null },
    ],
  },
  disclosure: {
    level: 'investments',
    includes: ['cash', 'every investment position'],
    never_sent: ['account names', 'transactions and payees'],
    currency: 'USD',
    currency_source: 'instrument',
    cash_basis: 'Cash in your cash accounts plus the cash inside your investment accounts.',
    fx_converted: [],
    average_price_omitted: ['GLOBEX'],
    partial_cost: [],
    cash_omitted: null,
    cash_floored: null,
  },
}

export function makeStatus(over: Partial<TaStatus> = {}): TaStatus {
  return {
    availability: { status: 'ready', message: 'TradingAgents is ready.' },
    install: { status: 'ready', version: '9fa1196fffbcfd9de5146d2dd2869df84ab99838', error: null },
    models: {
      deep: 'anthropic:claude-opus-4',
      quick: 'anthropic:claude-haiku-4',
      transports: { deep: 'anthropic', quick: 'anthropic' },
    },
    caps: { per_run_cap_usd: 2, monthly_cap_usd: 20, max_llm_calls: 150 },
    context_level: 'investments',
    analysts: ['market', 'social', 'news', 'fundamentals'],
    month_spend_usd: 3.1,
    queue_length: 0,
    disclaimer_accepted: true,
    ...over,
  }
}

export function makeSummary(over: Partial<TaRunSummary> = {}): TaRunSummary {
  return {
    id: 'tar_000000000001',
    ticker: 'ACME',
    instrument_id: 'inst_acme',
    trade_date: '2026-11-19',
    status: 'done',
    requested_by: 'user',
    session_id: null,
    analysts: ['market', 'social', 'news', 'fundamentals'],
    config_snapshot: {
      deep: 'anthropic:claude-opus-4',
      quick: 'anthropic:claude-haiku-4',
      transports: { deep: 'anthropic', quick: 'anthropic' },
    },
    context_level: 'investments',
    signal: 'Overweight',
    decision_text: '**Overweight** ACME: the thesis holds.',
    llm_calls: 31,
    input_tokens: 212_000,
    output_tokens: 31_000,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    cost_usd: 0.38,
    plan_included_calls: 0,
    unpriced_calls: 0,
    cost_by_role: {},
    cost_by_model: {},
    estimate_usd: 0.42,
    cap_usd: 2,
    error: null,
    created_at: '2026-11-19T10:00:00Z',
    started_at: '2026-11-19T10:00:05Z',
    finished_at: '2026-11-19T10:06:00Z',
    ...over,
  }
}

export function makeRun(over: Partial<TaRun> = {}): TaRun {
  return {
    ...makeSummary(),
    context_sent: CONTEXT,
    progress: [],
    reports: {
      fields: {
        market_report: 'Momentum is **steady**.',
        bull_history: 'Bull: margins expand.',
        bear_history: 'Bear: valuation is full.',
      },
      truncated: [],
    },
    ...over,
  }
}
