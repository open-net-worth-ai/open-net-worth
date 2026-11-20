import type { ProposedChange } from '../../../../types/chat'
import type { TaDisclosure, TaRunPreviewData } from '../../../../types/tradingAgents'
import type { CardFixture } from './types'

// Shapes mirror `ai/tools/trading_agents._StartPreview.build`: a generic tool_permission card
// whose display carries the `trading_agents_run` preview. Synthetic amounts, ticker ACME.

const NEVER_SENT = [
  'account names',
  'institutions and account numbers',
  'transactions and payees',
  'income and budgets',
  'net worth and liabilities',
  'retirement balances',
  'owned assets',
  'investment theses, memory and profile',
]

const DISCLOSURE: TaDisclosure = {
  level: 'investments',
  includes: ['cash', 'every investment position'],
  never_sent: NEVER_SENT,
  currency: 'USD',
  currency_source: 'instrument',
  cash_basis:
    'Cash in your cash accounts plus the cash inside your investment accounts, before any money set aside in budget envelopes.',
  fx_converted: [
    { ticker: null, from: 'EUR', to: 'USD', rate: 1.08, what: 'cash' },
    { ticker: 'GLOBEX', from: 'GBP', to: 'USD', rate: 1.27 },
  ],
  average_price_omitted: ['INITECH'],
  partial_cost: [],
  cash_omitted: null,
  cash_floored: null,
}

function startCard(
  toolUseId: string,
  data: TaRunPreviewData,
  args: Record<string, unknown>,
): ProposedChange {
  return {
    type: 'tool_permission',
    id: toolUseId,
    grant_tool: 'start_trading_agents_analysis',
    session_id: 'sess_demo',
    reason: 'start_trading_agents_analysis needs your approval before it runs.',
    display: {
      tool: 'start_trading_agents_analysis',
      arguments: args,
      preview: { kind: 'trading_agents_run', data: { ...data }, raw_arguments: args },
    },
  }
}

const BASE: Omit<
  TaRunPreviewData,
  'deep_model' | 'quick_model' | 'estimated_cost' | 'shared_with_the_ai_provider' | 'disclosure'
> = {
  ticker: 'ACME',
  trade_date: '2026-11-19',
  analysts: ['market', 'social', 'news', 'fundamentals'],
  per_run_cap: '$2.00, at most 150 model calls',
  this_month: '$3.10 spent of a $20.00 cap',
  context_level: 'investments',
  data_vendors: 'Receive the ticker and date only, never your amounts.',
}

export const TRADING_AGENTS_FIXTURES: CardFixture[] = [
  {
    name: 'start_trading_agents_analysis · investments level, FX and omitted price',
    change: startCard(
      'toolu_ta_investments',
      {
        ...BASE,
        deep_model: 'anthropic:claude-opus-4 via anthropic',
        quick_model: 'anthropic:claude-haiku-4 via anthropic',
        estimated_cost: 'about $0.42 ($0.30 to $0.60)',
        shared_with_the_ai_provider: {
          cash: 9400,
          currency: 'USD',
          positions: [
            { ticker: 'ACME', quantity: 120, average_price: 41.5 },
            { ticker: 'GLOBEX', quantity: 30, average_price: 88.25 },
            { ticker: 'INITECH', quantity: 12.5, average_price: null },
            { ticker: 'UMBRELLA', quantity: 200, average_price: 12.1 },
          ],
        },
        disclosure: DISCLOSURE,
      },
      { ticker: 'ACME' },
    ),
  },
  {
    name: 'start_trading_agents_analysis · position level, ticker not held',
    change: startCard(
      'toolu_ta_position',
      {
        ...BASE,
        context_level: 'position',
        analysts: ['market', 'news'],
        deep_model: 'openai:gpt-5 via openai',
        quick_model: 'openai:gpt-5 via openai',
        estimated_cost:
          'about $0.31 ($0.22 to $0.45), a model has no known price so this understates',
        shared_with_the_ai_provider: { cash: 9400, currency: 'USD', positions: [] },
        disclosure: {
          ...DISCLOSURE,
          level: 'position',
          fx_converted: [],
          average_price_omitted: [],
        },
      },
      { ticker: 'ACME', context_level: 'position', analysts: ['market', 'news'] },
    ),
  },
  {
    name: 'start_trading_agents_analysis · ticker only on a subscription CLI',
    change: startCard(
      'toolu_ta_ticker',
      {
        ...BASE,
        context_level: 'ticker',
        deep_model: 'claude_cli:sonnet via claude_cli',
        quick_model: 'claude_cli:haiku via claude_cli',
        estimated_cost: 'plan-included: no dollars, it uses your subscription quota',
        shared_with_the_ai_provider: 'nothing: ticker and date only',
        disclosure: {
          ...DISCLOSURE,
          level: 'ticker',
          includes: [],
          currency: null,
          currency_source: null,
          cash_basis: null,
          fx_converted: [],
          average_price_omitted: [],
        },
      },
      { ticker: 'ACME', context_level: 'ticker' },
    ),
  },
]
