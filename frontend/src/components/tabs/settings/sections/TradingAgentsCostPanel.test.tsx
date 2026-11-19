// @vitest-environment jsdom
//
// Settings → Cost & Performance → TradingAgents panel, mounted under the section's one period
// picker. Rendered through AiUsageSection with a routed authFetch; ticker ACME, synthetic
// amounts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: '', loading: false }),
}))

import { authFetch } from '../../../../api/client'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { invalidateQueryCache } from '../../../../state/queryCache'
import type { TaCostSummary, TaStatus } from '../../../../types/tradingAgents'
import { makeStatus } from '../../../intel/tradingAgentsFixtures'
import { AiUsageSection } from './AiUsageSection'
import { costWindowQuery } from './costWindow'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const part = (calls: number, cost: number, plan = 0) => ({
  calls,
  input_tokens: 40_000,
  output_tokens: 6_000,
  cost_usd: cost,
  plan_included_calls: plan,
})

const SUMMARY: TaCostSummary = {
  window: { days: 30, start: '2026-11-01', end: '2026-11-19' },
  totals: {
    runs: 13,
    done: 9,
    failed: 2,
    stopped: 1,
    active: 1,
    metered_usd: 1.84,
    avg_cost_per_run_usd: 0.15,
    median_cost_per_run_usd: 0.12,
    calls: 93,
    input_tokens: 600_000,
    output_tokens: 90_000,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    plan_included_calls: 0,
    unpriced_calls: 3,
  },
  month: { spend_usd: 3.1, cap_usd: 20, pct_used: 0.155, per_run_cap_usd: 2, max_llm_calls: 150 },
  by_role: {
    analyst_market: { ...part(41, 0.61), runs: 9 },
    portfolio_manager: { ...part(12, 0.4), runs: 9 },
  },
  by_model: {
    'anthropic:claude-opus-4': { ...part(88, 1.6), runs: 9, transport: 'anthropic' },
    'codex_cli:gpt-5': { ...part(52, 0, 52), runs: 4, transport: 'codex_cli' },
  },
  by_ticker: [{ ...part(60, 0.92), ticker: 'ACME', runs: 3 }],
  by_day: [
    { ...part(10, 0.2), date: '2026-11-11', runs: 1 },
    { ...part(30, 0.74), date: '2026-11-12', runs: 2 },
    { ...part(0, 0), date: '2026-11-13', runs: 0 },
  ],
  recent: [
    {
      id: 'tar_000000000001',
      created_at: '2026-11-12T10:00:00Z',
      ticker: 'ACME',
      trade_date: '2026-11-12',
      signal: 'Buy',
      status: 'done',
      calls: 31,
      input_tokens: 40_000,
      output_tokens: 6_000,
      cost_usd: 0.38,
      estimate_usd: 0.42,
      plan_included_calls: 0,
      unpriced_calls: 2,
    },
    {
      id: 'tar_000000000002',
      created_at: '2026-11-11T10:00:00Z',
      ticker: 'ACME',
      trade_date: '2026-11-11',
      signal: null,
      status: 'stopped_budget',
      calls: 52,
      input_tokens: 30_000,
      output_tokens: 4_000,
      cost_usd: 0,
      estimate_usd: null,
      plan_included_calls: 52,
      unpriced_calls: 0,
    },
  ],
}

const json = (body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

let summary: TaCostSummary
let status: TaStatus
let restore: () => void

function route(url: string): Response {
  if (url.startsWith('/api/cost/trading-agents')) return json(summary)
  if (url.startsWith('/api/trading-agents/status')) return json(status)
  if (url.startsWith('/api/cost/summary'))
    return json({
      total: { turns: 1, input_tokens: 1, cache_read_tokens: 0, output_tokens: 1, cost_usd: 0.1 },
      by_model: {},
      by_provider: {},
      by_search_fetch: {},
      by_surface: {
        trading_agents: {
          turns: 4,
          input_tokens: 1,
          cache_read_tokens: 0,
          output_tokens: 1,
          cost_usd: 0.1,
        },
        chat: { turns: 1, input_tokens: 1, cache_read_tokens: 0, output_tokens: 1, cost_usd: 0.05 },
      },
      by_day: [],
      by_hour: [],
      recent: [],
      window_days: 1,
    })
  return json({})
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
  summary = SUMMARY
  status = makeStatus()
  restore = overrideFlagsForTests({ trading_agents: true })
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => {
  cleanup()
  restore()
})

const panel = async () =>
  (await screen.findByText('Multi-agent stock research · runs, spend and limits')).closest(
    '.rd-panel',
  ) as HTMLElement

describe.each([false, true])('with the trading_agents flag %s', (on) => {
  it(on ? 'shows the panel' : 'shows no panel and never asks for its spend', async () => {
    restore()
    restore = overrideFlagsForTests({ trading_agents: on })
    render(<AiUsageSection toast={vi.fn()} />)
    await screen.findByText('Search & fetch')
    if (on) {
      expect(await panel()).toBeTruthy()
    } else {
      expect(screen.queryByText('Multi-agent stock research · runs, spend and limits')).toBeNull()
      expect(
        mockAuthFetch.mock.calls.some(([u]) => String(u).startsWith('/api/cost/trading-agents')),
      ).toBe(false)
    }
  })
})

describe('the panel', () => {
  it('sits between Search & fetch and Model telemetry', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const mine = await panel()
    const search = screen.getByText('Search & fetch').closest('.rd-panel')!
    expect(search.compareDocumentPosition(mine) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('asks for the same calendar days as the rest of the section, never days=N', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    await panel()
    fireEvent.click(screen.getByRole('tab', { name: '7 days' }))
    await waitFor(() =>
      expect(
        mockAuthFetch.mock.calls.some(
          ([u]) =>
            String(u).startsWith('/api/cost/trading-agents?start=') && String(u).includes('&end='),
        ),
      ).toBe(true),
    )
    const urls = mockAuthFetch.mock.calls
      .map(([u]) => String(u))
      .filter((u) => u.includes('trading-agents?'))
    expect(urls.some((u) => /days=\d/.test(u) && !u.includes('days=0'))).toBe(false)
  })

  it('shows the totals, the month against its cap, and the unpriced warning', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    expect(p.getByText('Metered spend · Today')).toBeTruthy()
    for (const label of [
      'Metered spend',
      'Runs',
      'Average per run',
      'Median per run',
      'Model calls',
      'Tokens in / out',
    ]) {
      expect(p.getByText(label)).toBeTruthy()
    }
    expect(p.getByText(/This month \$3\.10 of \$20\.00/)).toBeTruthy()
    expect(p.getByRole('progressbar', { name: 'TradingAgents spend this month' })).toBeTruthy()
    expect(p.getByText('9 done · 2 failed · 1 stopped · 1 running')).toBeTruthy()
    expect(p.getByText('3 calls had no price; real spend is higher.')).toBeTruthy()
    expect(p.queryByText('Plan-included calls')).toBeNull()
  })

  it('shows the plan tile and plan wording for subscription runs', async () => {
    summary = {
      ...SUMMARY,
      totals: { ...SUMMARY.totals, metered_usd: 0, plan_included_calls: 52, unpriced_calls: 0 },
    }
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    expect(p.getByText('Plan-included calls')).toBeTruthy()
    expect(
      p.getByText(/in \/ .* out tokens on a subscription\. Counted, not billed\./),
    ).toBeTruthy()
    expect(p.getByText('By role (share of calls)')).toBeTruthy()
    expect(p.queryByText('3 calls had no price; real spend is higher.')).toBeNull()
  })

  it('breaks spend down by role, model (with its plan), ticker and day', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    expect(p.getByText('Market analyst')).toBeTruthy()
    expect(p.getByText('claude-opus-4 · API')).toBeTruthy()
    expect(p.getByText('gpt-5 · Codex plan')).toBeTruthy()
    expect(p.getByText(/plan-included/)).toBeTruthy()
    expect(p.getByText('ACME', { selector: '.rd-bar-k' })).toBeTruthy()
    expect(p.getByRole('img', { name: /Spend by day\. Peak .* on /i })).toBeTruthy()
  })

  it('lists recent runs with the shared rating pill, plan and unpriced markers', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    const rows = p.getAllByRole('row')
    expect(rows[1]!.textContent).toContain('Buy')
    expect(rows[1]!.textContent).toContain('!')
    expect(rows[2]!.textContent).toContain('Stopped: budget')
    expect(rows[2]!.textContent).toContain('Plan')
  })

  it('says so when the period has no runs, keeping the month line', async () => {
    summary = { ...SUMMARY, totals: { ...SUMMARY.totals, runs: 0, calls: 0 } }
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    expect(p.getByText('No TradingAgents runs in this period.')).toBeTruthy()
    expect(p.queryByText('Runs')).toBeNull()
  })

  it('turns the month meter off when metered runs are off', async () => {
    summary = { ...SUMMARY, month: { ...SUMMARY.month, cap_usd: 0, pct_used: null } }
    render(<AiUsageSection toast={vi.fn()} />)
    const p = within(await panel())
    expect(p.getByText('Metered runs are off (monthly cap is $0).')).toBeTruthy()
    expect(p.queryByRole('progressbar')).toBeNull()
  })

  it('stays hidden for an owner who never enabled the connector and has no runs', async () => {
    status = makeStatus({ availability: { status: 'connector_off', message: '' } })
    summary = { ...SUMMARY, totals: { ...SUMMARY.totals, runs: 0, calls: 0 } }
    render(<AiUsageSection toast={vi.fn()} />)
    await screen.findByText('Search & fetch')
    await waitFor(() =>
      expect(
        mockAuthFetch.mock.calls.some(([u]) => String(u).startsWith('/api/cost/trading-agents')),
      ).toBe(true),
    )
    expect(screen.queryByText('Multi-agent stock research · runs, spend and limits')).toBeNull()
  })

  it('labels the TradingAgents surface bucket, and only that one', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const surface = (await screen.findByText('By surface')).closest('.rd-bars')!
    const labels = [...surface.querySelectorAll('.rd-bar-k')].map((n) => n.textContent)
    expect(labels).toEqual(['TradingAgents', 'chat'])
  })
})

describe('costWindowQuery', () => {
  it('uses a preset range, an inclusive start/end for 7 and 30 days, or all time', () => {
    expect(costWindowQuery('today', 'UTC')).toBe('range=today&tz=UTC')
    expect(costWindowQuery('yesterday', 'UTC')).toBe('range=yesterday&tz=UTC')
    expect(costWindowQuery('0', 'UTC')).toBe('days=0&tz=UTC')
    for (const days of ['7', '30'] as const) {
      const [start, end] = /start=([\d-]+)&end=([\d-]+)/
        .exec(costWindowQuery(days, 'UTC'))!
        .slice(1)
      const span = (Date.parse(end!) - Date.parse(start!)) / 86_400_000
      expect(span).toBe(Number(days) - 1)
    }
  })
})
