// @vitest-environment jsdom
//
// Settings → AI usage → AI spend panel.
//
// BUG regression: asset_brief.py's non-LLM search/fetch API costs (logged through the
// cost_log table as model="fetch:<id>"/"search:<id>") were mixed into the "By model"/
// "By provider" bars alongside real AI models/providers — e.g. "search:tavily" sitting next
// to "sonnet" in By model, "Tavily" sitting next to "Claude Code CLI" in By provider. The
// backend now routes those rows to a separate `by_search_fetch` breakdown instead
// (services/cost.py); this file verifies the panel renders that split, and that a genuine
// sub-cent cost (e.g. $0.002 for a couple of tiny turns) doesn't round to the misleading
// "$0.00" a plain 2-decimal format would show.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'claude_cli:sonnet', loading: false }),
}))

import { AiUsageSection } from './AiUsageSection'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const PROVIDERS = {
  catalog: [
    {
      key: 'claude_cli',
      label: 'Claude Code CLI',
      transport: 'claude_cli',
      base_url: '',
      needs_api_key: false,
      editable_base_url: false,
      model_hints: [],
      cli_binary: 'claude',
    },
  ],
  configured_providers: {},
  detected_clis: { claude_cli: true },
  detected_cli_paths: {},
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const COST_SUMMARY = {
  total: { turns: 1, input_tokens: 100, cache_read_tokens: 0, output_tokens: 50, cost_usd: 0.1 },
  by_model: {
    sonnet: { turns: 1, input_tokens: 100, cache_read_tokens: 0, output_tokens: 50, cost_usd: 0.1 },
  },
  by_provider: {
    'Claude Code CLI': {
      turns: 1,
      input_tokens: 100,
      cache_read_tokens: 0,
      output_tokens: 50,
      cost_usd: 0.1,
    },
  },
  by_search_fetch: {
    Tavily: { turns: 2, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0, cost_usd: 0.01 },
    Serper: { turns: 1, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0, cost_usd: 0.002 },
  },
  by_surface: {
    chat: { turns: 1, input_tokens: 100, cache_read_tokens: 0, output_tokens: 50, cost_usd: 0.1 },
  },
  by_day: [],
  by_hour: [
    {
      hour: '2026-09-21T10',
      turns: 1,
      input_tokens: 100,
      cache_read_tokens: 0,
      output_tokens: 50,
      cost_usd: 0.1,
    },
  ],
  recent: [
    {
      ts: '2026-09-21T10:30:00+07:00',
      model: 'sonnet',
      provider: 'Claude Code CLI',
      cost_usd: 0.012,
    },
    {
      ts: '2026-09-21T10:00:00+07:00',
      model: 'haiku',
      provider: 'Anthropic',
      cost_usd: 0.004,
    },
  ],
  window_days: 1,
}

function route(url: string): Response {
  if (url.startsWith('/api/model-config/providers')) return json(PROVIDERS)
  if (url.startsWith('/api/model-config'))
    return json({
      default_model: 'claude_cli:sonnet',
      provider: 'claude_cli',
      model: 'sonnet',
      base_url: '',
      task_models: {},
    })
  if (url.startsWith('/api/preferred-models')) return json({ models: [] })
  if (url.startsWith('/api/memory/settings')) return json({ enabled: true })
  if (url.startsWith('/api/memory/facts')) return json({ total: 0 })
  if (url.startsWith('/api/cost/summary')) return json(COST_SUMMARY)
  return json({})
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => cleanup())

describe('AiUsageSection — AI spend: search/fetch split from models/providers', () => {
  it('offers Today, Yesterday, 7 days, 30 days, and All time windows', async () => {
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByRole('tab', { name: 'Today' })).toBeTruthy())
    expect(screen.getByRole('tab', { name: 'Yesterday' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '7 days' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '30 days' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'All time' })).toBeTruthy()
  })

  it('places the period control above both cost panels', async () => {
    render(<AiUsageSection toast={vi.fn()} />)

    const period = await screen.findByLabelText('Cost period')
    const aiPanel = screen.getByText('AI spend').closest('.rd-panel')
    const servicePanel = screen.getByText('Search & fetch').closest('.rd-panel')

    expect(period.closest('.rd-panel')).toBeNull()
    expect(period.compareDocumentPosition(aiPanel as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(
      period.compareDocumentPosition(servicePanel as Node) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('selects a cost window from the compact mobile filter menu', async () => {
    render(<AiUsageSection toast={vi.fn()} />)

    const filter = await screen.findByRole('button', { name: 'Cost window: Today' })
    fireEvent.click(filter)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Yesterday' }))

    await waitFor(() => {
      expect(
        mockAuthFetch.mock.calls.some(([url]) => String(url).includes('range=yesterday')),
      ).toBe(true)
    })
    expect(screen.getByRole('button', { name: 'Cost window: Yesterday' })).toBeTruthy()
    expect(screen.getByText('Estimated total · Yesterday')).toBeTruthy()
  })

  it('shows a compact cost timeline with visible dollar context', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 21, 14, 2))
    try {
      render(<AiUsageSection toast={vi.fn()} />)

      const chart = await screen.findByLabelText('Cost trend')
      // Today stops at the current hour (2 PM) instead of drawing empty future hours.
      expect(chart.querySelectorAll('.rd-cost-trend-bar')).toHaveLength(15)
      expect(screen.getByText('US$0.10 avg / turn')).toBeTruthy()
      expect(screen.getByText('US$0.10 peak')).toBeTruthy()
      expect(screen.getByText('12 AM')).toBeTruthy()
      expect(screen.getByText('2 PM')).toBeTruthy()
      expect(screen.queryByText('11 PM')).toBeNull()
      expect(screen.queryByText('Each bar is one AI turn')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('never trims Today before the last hour that has spend', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 21, 3, 0))
    try {
      render(<AiUsageSection toast={vi.fn()} />)
      const chart = await screen.findByLabelText('Cost trend')
      expect(chart.querySelectorAll('.rd-cost-trend-bar')).toHaveLength(11)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps all 24 hours for Yesterday', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Yesterday' }))
    await waitFor(() =>
      expect(
        screen.getByLabelText('Cost trend').querySelectorAll('.rd-cost-trend-bar'),
      ).toHaveLength(24),
    )
    expect(screen.getByText('11 PM')).toBeTruthy()
  })

  it('separates the summary metrics and cost trend into distinct panel rows', async () => {
    render(<AiUsageSection toast={vi.fn()} />)

    const cacheRow = (await screen.findByText('Cache hits')).closest('.rd-stat')
    const summaryRow = cacheRow?.closest('.rd-stats')
    const trendRow = screen.getByLabelText('Cost trend')

    expect(summaryRow?.classList.contains('rd-cost-stats')).toBe(true)
    expect(summaryRow?.nextElementSibling).toBe(trendRow)
  })

  it('shows bar details immediately on hover and keyboard focus without a native tooltip delay', async () => {
    render(<AiUsageSection toast={vi.fn()} />)

    const bar = await screen.findByLabelText('10 AM · US$0.10 · 1 turn')
    expect(bar.getAttribute('title')).toBeNull()
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.mouseEnter(bar)
    expect(screen.getByRole('tooltip').textContent).toBe('10 AM · US$0.10 · 1 turn')

    fireEvent.mouseLeave(bar)
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.focus(bar)
    expect(screen.getByRole('tooltip')).toBeTruthy()
  })

  it('renders Search & fetch as a separate estimated-cost table with call counts', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    const heading = await screen.findByText('Search & fetch')
    const servicePanel = heading.closest('.rd-panel')
    const aiPanel = screen.getByText('AI spend').closest('.rd-panel')

    expect(servicePanel).toBeTruthy()
    expect(servicePanel).not.toBe(aiPanel)
    expect(servicePanel?.textContent).toContain('Estimated third-party API charges')
    expect(servicePanel?.textContent).toContain('Estimated total · Today')
    expect(servicePanel?.querySelector('table')).toBeTruthy()
    expect(servicePanel?.textContent).toContain('Provider')
    expect(servicePanel?.textContent).toContain('Estimated cost')
    expect(servicePanel?.textContent).toContain('Calls')
    expect(servicePanel?.textContent).toContain('Tavily')
    expect(servicePanel?.textContent).toContain('Serper')
    expect(servicePanel?.textContent).toContain('2 calls')
    expect(servicePanel?.textContent).toContain('US$0.0120')
    expect(aiPanel?.textContent).not.toContain('Tavily')
    expect(aiPanel?.textContent).toContain('Total spendUS$0.10')
  })

  it('does not list the search-provider labels under By model or By provider', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Search & fetch')).toBeTruthy())
    const byModelHeading = screen.getByText('By model')
    const byModelSection = byModelHeading.closest('.rd-bars')
    expect(byModelSection?.textContent).not.toContain('Tavily')

    const byProviderHeading = screen.getByText('By provider')
    const byProviderSection = byProviderHeading.closest('.rd-bars')
    expect(byProviderSection?.textContent).not.toContain('Tavily')
    expect(byProviderSection?.textContent).not.toContain('Serper')
  })

  it('shows a genuine sub-cent cost with enough precision to look nonzero', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    // Serper's $0.002 must not render as the misleading "US$0.00".
    await waitFor(() => expect(screen.getByText('US$0.0020')).toBeTruthy())
  })

  it('still shows a cent-or-more cost at the familiar 2 decimal places', async () => {
    render(<AiUsageSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByText('US$0.10').length).toBeGreaterThan(0))
  })
})

describe('AiUsageSection — AI spend: no search/fetch activity', () => {
  it('keeps the separate Search & fetch panel with an empty state', async () => {
    const empty = { ...COST_SUMMARY, by_search_fetch: {} }
    mockAuthFetch.mockImplementation(async (url: string) => {
      const u = String(url)
      if (u.startsWith('/api/cost/summary')) return json(empty)
      return route(u)
    })
    render(<AiUsageSection toast={vi.fn()} />)
    const heading = await screen.findByText('Search & fetch')
    const servicePanel = heading.closest('.rd-panel')
    expect(servicePanel?.textContent).toContain('No search or fetch calls in this window.')
  })
})
