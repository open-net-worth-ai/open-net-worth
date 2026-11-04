// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { tradingApi } from '../../../api/trading'
import { TradingComparisons } from './TradingComparisons'

const fixture = vi.hoisted(() => ({ comparison: undefined as unknown, chart: vi.fn() }))
vi.mock('../overview/charts', () => ({
  OvChart: (props: { config: unknown }) => {
    fixture.chart(props.config)
    return <div data-testid="comparison-chart" />
  },
}))

vi.mock('../../../api/trading', () => ({
  tradingRoot: '/api/trading',
  tradingApi: {
    comparisons: vi.fn(),
    allRuns: vi.fn(),
    comparison: vi.fn(),
    createComparison: vi.fn(),
  },
}))
vi.mock('../../../state/queryCache', () => ({
  useQueryCache: (key: string) => ({
    data: key.includes('/comparisons/')
      ? fixture.comparison
      : key.endsWith('/runs')
        ? {
            items: [
              {
                id: 'run-a',
                workspace_snapshot: { agent_label: 'Agent A' },
                mode: 'paper',
                created_at: '2026-01-01T00:00:00Z',
                data_version: 'synthetic',
                agent_version: 'v1',
              },
              {
                id: 'run-b',
                workspace_snapshot: { agent_label: 'Agent B' },
                mode: 'paper',
                created_at: '2026-01-01T00:00:00Z',
                data_version: 'synthetic',
                agent_version: 'v1',
              },
            ],
            total: 2,
          }
        : { items: [], total: 0 },
    initialLoading: false,
    error: null,
    refresh: vi.fn(),
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  fixture.comparison = undefined
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
})
afterEach(cleanup)

it('keeps comparison rejection accessible and Escape functional after its submit is disabled', async () => {
  let reject!: (error: Error) => void
  vi.mocked(tradingApi.createComparison).mockReturnValue(
    new Promise((_, deny) => {
      reject = deny
    }),
  )
  render(<TradingComparisons comparisonId="" onSelect={vi.fn()} onRun={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'New comparison' }))
  fireEvent.change(screen.getByLabelText('Comparison name'), {
    target: { value: 'Different accounts' },
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /Agent A/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: /Agent B/ }))
  const submit = screen.getByRole('button', { name: 'Create comparison' })
  submit.focus()
  fireEvent.click(submit)
  expect(document.activeElement).not.toBe(submit)
  reject(new Error('Runs have different capital'))
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Runs have different capital')
  expect(alert.closest('[aria-hidden="true"]')).toBeNull()
  expect((screen.getByLabelText('Comparison name') as HTMLInputElement).value).toBe(
    'Different accounts',
  )
  fireEvent.keyDown(document.activeElement!, { key: 'Escape', code: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

it('distinguishes identical agent labels and renders recorded scenario facts and multiple equity series', () => {
  const period = { start_at: '2026-01-01T00:00:00Z', end_at: '2026-02-01T00:00:00Z' }
  fixture.comparison = {
    id: 'comparison-a',
    name: 'Same mandate',
    run_ids: ['run-left', 'run-right'],
    comparable: true,
    scenario: {
      period,
      capital_by_currency: { USD: '1000' },
      base_currency: 'USD',
      data_version: 'frozen-market-v2',
      instruments: [{ symbol: 'ACME', asset_class: 'stock', venue: 'simulation', currency: 'USD' }],
      fee_bps: '5',
      slippage_bps: '2',
      max_participation: '0.25',
    },
    results: [
      {
        run_id: 'run-left',
        workspace_id: 'workspace-left',
        agent_label: 'Lucius',
        agent_version: 'strategy-left',
        mode: 'backtest',
        ...period,
        current_time: period.start_at,
        base_currency: 'USD',
        equity: '1010',
        valuation_complete: true,
        history: [
          { timestamp: period.start_at, equity: '1000' },
          { timestamp: period.end_at, equity: '1010' },
        ],
      },
      {
        run_id: 'run-right',
        workspace_id: 'workspace-right',
        agent_label: 'Lucius',
        agent_version: 'strategy-right',
        mode: 'backtest',
        ...period,
        current_time: period.start_at,
        base_currency: 'USD',
        equity: '990',
        valuation_complete: true,
        history: [
          { timestamp: period.start_at, equity: '1000' },
          { timestamp: period.end_at, equity: '990' },
        ],
      },
    ],
  }
  const onRun = vi.fn()
  render(<TradingComparisons comparisonId="comparison-a" onSelect={vi.fn()} onRun={onRun} />)
  expect(screen.getByRole('button', { name: 'Lucius · run-left' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Lucius · run-right' })).toBeTruthy()
  expect(screen.getByText('Dataset: frozen-market-v2')).toBeTruthy()
  expect(screen.getByText(/ACME \(stock, simulation, USD\)/)).toBeTruthy()
  expect(screen.getByText(/Fee: 5 bps · Slippage: 2 bps/)).toBeTruthy()
  expect(screen.getByText(/Capital: \$1,000.00/)).toBeTruthy()
  expect(screen.getByTestId('comparison-chart')).toBeTruthy()
  expect(fixture.chart.mock.calls.at(-1)?.[0]).toMatchObject({
    data: {
      datasets: [
        { label: 'Lucius · backtest · run-left', data: [1000, 1010] },
        { label: 'Lucius · backtest · run-right', data: [1000, 990] },
      ],
    },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Lucius · run-right' }))
  expect(onRun).toHaveBeenCalledWith('run-right', 'workspace-right')
})
