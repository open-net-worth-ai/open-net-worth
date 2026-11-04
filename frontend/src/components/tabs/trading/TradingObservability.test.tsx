// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type {
  TradingEvidence,
  TradingEvent,
  TradingObservation,
  TradingOrder,
  TradingRun,
} from '../../../types/trading'
import { TradingMarketChart } from './TradingMarketChart'
import { TradingEvidenceInspector } from './TradingEvidenceInspector'
import { EventPayload, TradingAgentWork } from './TradingAgentWork'

const fixture = vi.hoisted(() => ({
  main: vi.fn(),
  volume: vi.fn(),
  markers: vi.fn(),
  evidence: undefined as TradingEvidence | undefined,
  events: [] as TradingEvent[],
}))
vi.mock('../../intel/assetPriceChartLwc', () => ({
  toTime: (value: string) => Math.floor(new Date(value).getTime() / 1000),
  buildChart: () => ({
    mainSeries: { setData: fixture.main },
    volumeSeries: { setData: fixture.volume },
    chart: { remove: vi.fn(), subscribeClick: vi.fn(), timeScale: () => ({ fitContent: vi.fn() }) },
  }),
}))
vi.mock('lightweight-charts', () => ({ createSeriesMarkers: fixture.markers }))
vi.mock('../../../theme', () => ({ useTheme: () => 'dark' }))
vi.mock('../../../state/queryCache', () => ({
  useQueryCache: (key: string, _load: unknown, options?: { enabled: boolean }) => ({
    data:
      options?.enabled === false
        ? undefined
        : key.includes('/evidence/')
          ? fixture.evidence
          : key.includes('/events?')
            ? { items: fixture.events, total: fixture.events.length }
            : key.includes('/work/')
              ? { payload: { content: 'Full native record restored' } }
              : { items: [], total: 0 },
    initialLoading: false,
    error: null,
    refresh: vi.fn(),
  }),
}))
const instrument = {
  symbol: 'ACME',
  venue: 'simulation',
  asset_class: 'stock' as const,
  currency: 'USD',
}
const order: TradingOrder = {
  id: 'ord_first',
  run_id: 'run_sample',
  account_id: 'acc_sample',
  instrument,
  side: 'buy',
  quantity: '1',
  order_type: 'market',
  status: 'filled',
  created_at: '2026-01-01T10:00:00Z',
  request_id: 'request_first',
}

beforeEach(() => {
  vi.clearAllMocks()
  fixture.events = []
  fixture.evidence = undefined
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((callback: FrameRequestCallback) => {
      callback(0)
      return 1
    }),
  )
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('actual trading observability', () => {
  it('keeps rejected current quotes as recorded history without a current numeric headline', () => {
    render(
      <TradingMarketChart
        observations={[
          {
            instrument,
            timestamp: '2026-01-01T10:00:00Z',
            price: '999.99',
            open: '998',
            high: '1001',
            low: '997',
            close: '999.99',
            volume: '100',
            source: 'yahoo',
            current_quote_authorized: false,
          },
        ]}
        orders={[]}
        fills={[]}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText('Current quote unverified')).toBeTruthy()
    expect(screen.queryByText('$999.99')).toBeNull()
    expect(screen.getByText(/Recorded market history is retained below/)).toBeTruthy()
    expect(fixture.main.mock.calls[0]?.[0][0]).toMatchObject({ close: 999.99 })
    expect(screen.getByText('999.99')).toBeTruthy()
  })

  it('keeps real candles when quote updates share the stream, without inventing OHLCV for quotes', () => {
    const rows: TradingObservation[] = [
      {
        instrument,
        timestamp: '2026-01-01T10:00:00Z',
        price: '75',
        open: '74',
        high: '76',
        low: '73',
        close: '75',
        volume: '100',
      },
      {
        instrument,
        timestamp: '2026-01-01T11:00:00Z',
        price: '77',
        open: '75',
        high: '78',
        low: '74',
        close: '77',
        volume: '150',
      },
      { instrument, timestamp: '2026-01-01T11:01:00Z', price: '77.5' },
    ]
    render(<TradingMarketChart observations={rows} orders={[]} fills={[]} onSelect={vi.fn()} />)
    expect(screen.getByText(/OHLCV candles · 2 recorded bars/)).toBeTruthy()
    expect(fixture.main.mock.calls[0]?.[0]).toHaveLength(2)
    expect(fixture.main.mock.calls[0]?.[0][1]).toMatchObject({
      open: 75,
      high: 78,
      low: 74,
      close: 77,
    })
    expect(screen.getByText(/1 quote updates are retained/)).toBeTruthy()
    expect(fixture.volume.mock.calls[0]?.[0]).toHaveLength(2)
  })

  it('offers the actual fill as a selectable evidence record even without a decision link', () => {
    const select = vi.fn()
    render(
      <TradingMarketChart
        observations={[{ instrument, timestamp: '2026-01-01T10:00:00Z', price: '75' }]}
        orders={[order]}
        fills={[
          {
            id: 'fill_first',
            order_id: order.id,
            account_id: order.account_id,
            quantity: '1',
            price: '75',
            fees: '0.1',
            timestamp: '2026-01-01T10:00:00Z',
          },
        ]}
        onSelect={select}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Buy 1 ACME/ }))
    expect(select).toHaveBeenCalledWith({ kind: 'fill', id: 'fill_first' })
    expect(screen.getByText(/Quote series · OHLCV unavailable/)).toBeTruthy()
  })

  it('navigates every explicitly linked order in the same inspector', () => {
    fixture.evidence = {
      selection: { kind: 'event', id: 'event_decision' },
      order,
      orders: [order, { ...order, id: 'ord_second', side: 'sell' }],
      fills: [],
      decision: null,
      events: [],
      observations: [],
      files: [],
      missing_links: ['Decision link not recorded'],
      data_context: {
        label: 'Data link not recorded',
        cutoff: '2026-01-01T10:00:00Z',
        data_version: 'synthetic',
      },
    }
    const select = vi.fn()
    render(
      <TradingEvidenceInspector
        runId="run_sample"
        selection={{ kind: 'event', id: 'event_decision' }}
        onClose={vi.fn()}
        onSelect={select}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Inspect order ACME · sell/ }))
    expect(select).toHaveBeenCalledWith({ kind: 'order', id: 'ord_second' })
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
  })

  it('hides storage fragments from both work views while preserving the complete logical record', () => {
    fixture.events = [
      {
        id: 'part_first',
        sequence: 1,
        kind: 'agent_record_part',
        payload: { content: 'INTERNAL_STORAGE_FRAGMENT_ONLY' },
      },
      {
        id: 'logical_result',
        sequence: 2,
        kind: 'agent_tool_result',
        payload: { availability: 'chunked', record_ids: ['part_first'] },
      },
    ]
    const time = '2026-01-01T10:00:00Z'
    const run: TradingRun = {
      id: 'run_sample',
      workspace_id: 'workspace_sample',
      mode: 'paper',
      state: 'running',
      start_at: time,
      end_at: null,
      current_time: time,
      created_at: time,
      agent_state: 'idle',
      base_currency: 'USD',
      model_cost_usd: '0',
      data_version: 'synthetic',
      agent_version: 'v1',
      initial_capital: { USD: '1000' },
      workspace_snapshot: {
        id: 'workspace_sample',
        name: 'Synthetic workspace',
        instructions: 'Observe this test allocation.',
        agent_kind: 'external',
        agent_label: 'Synthetic executor',
        revision: 1,
        created_at: time,
        updated_at: time,
        mandate: {
          allowed_asset_classes: ['stock'],
          max_model_cost_usd: '1',
          fee_bps: '0',
          slippage_bps: '0',
          max_participation: '1',
          max_quote_age_seconds: 300,
        },
      },
    }
    render(<TradingAgentWork run={run} selection={null} onSelect={vi.fn()} />)
    expect(screen.queryByText(/INTERNAL_STORAGE_FRAGMENT_ONLY/)).toBeNull()
    expect(screen.getByText('agent tool result')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Execution' }))
    expect(screen.queryByText(/INTERNAL_STORAGE_FRAGMENT_ONLY/)).toBeNull()
    fireEvent.click(screen.getByText('Inputs, outputs and recorded details'))
    fireEvent.click(screen.getByRole('button', { name: 'Load complete record' }))
    expect(screen.getByText(/Full native record restored/)).toBeTruthy()
  })

  it('retrieves a chunked native record only when the user opens it', () => {
    render(
      <EventPayload
        runId="run_sample"
        event={{
          id: 'event_large',
          kind: 'agent_tool_result',
          payload: { availability: 'chunked', record_ids: ['part_first'] },
        }}
      />,
    )
    expect(screen.queryByText(/Full native record restored/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Load complete record' }))
    expect(screen.getByText(/Full native record restored/)).toBeTruthy()
  })
})
