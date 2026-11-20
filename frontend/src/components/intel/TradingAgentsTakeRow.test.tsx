// @vitest-environment jsdom
//
// The TradingAgents row under Lucius's take (docs/specs/trading-agents-ui.md §11): one state at
// a time, a to e. The data hooks are stubbed; ticker ACME, synthetic amounts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../hooks/useFlag'
import type { TradingAgentsInput } from '../../types/assetDetail'
import type { TaRun, TaRunSummary, TaStatus } from '../../types/tradingAgents'
import { CONTEXT, makeRun, makeStatus, makeSummary } from './tradingAgentsFixtures'
import { TradingAgentsTakeRow } from './TradingAgentsTakeRow'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const api = vi.hoisted(() => ({ startTaRun: vi.fn(), cancelTaRun: vi.fn() }))
const state = vi.hoisted(() => ({
  status: null as unknown,
  runs: null as unknown,
  run: null as unknown,
  estimate: null as unknown,
  preview: null as unknown,
}))

vi.mock('../../hooks/useTradingAgents', () => ({
  TaError: class TaError extends Error {},
  startTaRun: api.startTaRun,
  cancelTaRun: api.cancelTaRun,
  useTaStatus: () => state.status,
  useTaRuns: () => state.runs,
  useTaRun: () => state.run,
  useTaEstimate: () => state.estimate,
  useTaContextPreview: () => state.preview,
}))

const query = <T,>(data: T | undefined) => ({
  data,
  error: null,
  initialLoading: false,
  refresh: () => Promise.resolve(data as T),
})

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10)
const done = (over: Partial<TaRunSummary> = {}) => makeSummary({ trade_date: daysAgo(0), ...over })
const INPUT: TradingAgentsInput = {
  run_id: 'tar_000000000001',
  signal: 'Sell',
  trade_date: daysAgo(0),
  age_days: 0,
}
const ESTIMATE = {
  estimate_usd: 0.42,
  low_usd: 0.3,
  high_usd: 0.6,
  calls: 38,
  basis: 'history' as const,
  sample_size: 10,
  plan_included: false,
  priced: true,
  unpriced_models: [],
}

function setup(opts: {
  status?: TaStatus
  runs?: TaRunSummary[]
  run?: TaRun
  estimate?: unknown
}) {
  state.status = query(opts.status ?? makeStatus())
  state.runs = query({ runs: opts.runs ?? [], total: (opts.runs ?? []).length })
  state.run = query(opts.run)
  state.estimate = query(opts.estimate ?? ESTIMATE)
  state.preview = query(CONTEXT)
}

const onOpen = vi.fn()
const onRefresh = vi.fn()
const mount = (input: TradingAgentsInput | null = null) =>
  render(<TradingAgentsTakeRow ticker="ACME" input={input} onOpen={onOpen} onRefresh={onRefresh} />)

let restore: () => void
beforeEach(() => {
  restore = overrideFlagsForTests({ trading_agents: true })
  setup({})
})
afterEach(() => {
  cleanup()
  restore()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe.each([false, true])('with the trading_agents flag %s', (on) => {
  it(on ? 'shows the row' : 'renders nothing at all', () => {
    restore()
    restore = overrideFlagsForTests({ trading_agents: on })
    const { container } = mount()
    if (on) expect(screen.getByRole('button', { name: 'Add TradingAgents research' })).toBeTruthy()
    else expect(container.innerHTML).toBe('')
  })
})

describe('(a) a run is moving', () => {
  const startedAt = () => new Date(Date.now() - 72_000).toISOString()
  const running = makeSummary({
    id: 'tar_000000000009',
    status: 'running',
    signal: null,
    started_at: startedAt(),
  })

  it('shows the stage, the clock, Cancel and a link to the card', () => {
    setup({
      runs: [running],
      run: makeRun({
        id: running.id,
        status: 'running',
        signal: null,
        progress: [{ stage: 'Market Analyst', status: 'started', at: new Date().toISOString() }],
      }),
    })
    mount(INPUT)
    expect(screen.getByRole('status').textContent).toMatch(
      /TradingAgents research · Analysts · 1m 1\ds/,
    )
    expect(screen.queryByRole('button', { name: 'Add TradingAgents research' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    expect(onOpen).toHaveBeenCalledWith('tar_000000000009')
  })

  it('says Queued for a run still waiting', () => {
    setup({ runs: [{ ...running, status: 'queued' }] })
    mount()
    expect(screen.getByRole('status').textContent).toContain('Queued')
  })

  it('cancels the run, and shows the server sentence when that fails', async () => {
    setup({ runs: [running] })
    api.cancelTaRun.mockRejectedValueOnce(new Error('x'))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.cancelTaRun).toHaveBeenCalledWith('tar_000000000009')
    expect((await screen.findByRole('alert')).textContent).toBe('Could not cancel the analysis.')
  })
})

describe('(b) the take used a usable run', () => {
  it('shows the research view line, not a second verdict', () => {
    setup({ runs: [done()] })
    mount({ ...INPUT, view: 'negative outlook, not a buy now' })
    const line = screen.getByRole('button', { name: /Research view: Sell/ })
    expect(line.textContent).toBe('Research view: Sell · today — negative outlook, not a buy now')
    expect(line.querySelector('.ta-rating')).toBeNull()
    fireEvent.click(line)
    expect(onOpen).toHaveBeenCalledWith('tar_000000000001')
  })

  it('leaves the gloss out when the server sent no view', () => {
    setup({ runs: [done()] })
    mount(INPUT)
    expect(screen.getByRole('button', { name: /Research view/ }).textContent).toBe(
      'Research view: Sell · today',
    )
  })

  it('says the run may be outdated past three days, and not before', () => {
    setup({ runs: [done({ trade_date: daysAgo(4) })] })
    mount({ ...INPUT, age_days: 4 })
    expect(screen.getByText('may be outdated')).toBeTruthy()
    cleanup()
    mount({ ...INPUT, age_days: 3 })
    expect(screen.queryByText('may be outdated')).toBeNull()
  })
})

describe('(c) a usable run the take has not used yet', () => {
  it('says the take is updating, with no button', () => {
    setup({ runs: [done()] })
    mount(null)
    expect(screen.getByRole('status').textContent).toBe(
      'TradingAgents result ready — updating this take…',
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('offers a plain refresh once it has waited two minutes', () => {
    vi.useFakeTimers()
    setup({ runs: [done()] })
    mount(null)
    act(() => {
      vi.advanceTimersByTime(119_000)
    })
    expect(screen.queryByRole('button', { name: 'Refresh this take' })).toBeNull()
    act(() => {
      vi.advanceTimersByTime(2_000)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh this take' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('treats a newer run than the one the take used as not yet used', () => {
    setup({ runs: [done({ id: 'tar_000000000002' })] })
    mount(INPUT)
    expect(screen.getByRole('status').textContent).toContain('updating this take')
  })
})

describe('(d) no usable run, ready to run', () => {
  it('offers the button with the metered cost, and starts only on the click', async () => {
    api.startTaRun.mockResolvedValue('tar_000000000003')
    mount()
    expect(screen.getByText('about $0.42')).toBeTruthy()
    expect(api.startTaRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Add TradingAgents research' }))
    await waitFor(() => expect(api.startTaRun).toHaveBeenCalledTimes(1))
    const status = state.status as { data: TaStatus }
    expect(api.startTaRun).toHaveBeenCalledWith(
      'ACME',
      status.data.analysts,
      status.data.context_level,
    )
  })

  it('names the plan when the run is included in it', () => {
    setup({
      status: makeStatus({
        models: {
          deep: 'claude_cli:opus',
          quick: 'claude_cli:haiku',
          transports: { deep: 'claude_cli', quick: 'claude_cli' },
        },
      }),
      estimate: { ...ESTIMATE, plan_included: true },
    })
    mount()
    expect(screen.getByText(/^uses your .* plan$/)).toBeTruthy()
  })

  it('does not treat a stale run (over 14 days) as research', () => {
    setup({ runs: [done({ trade_date: daysAgo(20) })] })
    mount()
    expect(screen.getByRole('button', { name: 'Add TradingAgents research' })).toBeTruthy()
  })

  it('discloses what is shared on request', () => {
    mount()
    const summary = screen.getByText("What's shared")
    fireEvent.click(summary)
    expect(screen.getByText('Investable cash')).toBeTruthy()
  })

  it('shows the server refusal inline', async () => {
    const { TaError } = await import('../../hooks/useTradingAgents')
    api.startTaRun.mockRejectedValueOnce(new TaError('The monthly cap is reached.'))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Add TradingAgents research' }))
    expect((await screen.findByRole('alert')).textContent).toBe('The monthly cap is reached.')
  })

  it('stays one row: the button, a hint and a disclosure, nothing else', () => {
    const { container } = mount()
    expect(container.children).toHaveLength(1)
    expect(container.querySelectorAll('.ta-take-row')).toHaveLength(1)
    expect(container.querySelectorAll('p, h1, h2, h3, ul')).toHaveLength(0)
  })
})

describe('(e) TradingAgents cannot run yet', () => {
  it.each([
    ['connector_off', "TradingAgents isn't set up yet."],
    ['connector_paused', 'The TradingAgents connector is paused.'],
    ['not_installed', "TradingAgents isn't installed."],
    ['needs_reinstall', 'TradingAgents needs reinstalling after an update.'],
    ['model_unavailable', 'Pick a model for TradingAgents.'],
  ] as const)('%s: one quiet line and a link to the connector page', (status, text) => {
    setup({
      status: makeStatus({ availability: { status, message: 'Pick a model for TradingAgents.' } }),
    })
    mount()
    expect(screen.getByText(text)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add TradingAgents research' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Set up TradingAgents' }))
    expect(navigate).toHaveBeenCalledWith({
      to: '/connectors',
      search: { connector: 'trading_agents' },
    })
  })

  it('an unaccepted notice is a set-up step', () => {
    setup({ status: makeStatus({ disclaimer_accepted: false }) })
    mount()
    expect(screen.getByRole('button', { name: 'Set up TradingAgents' })).toBeTruthy()
  })

  it('an install in progress has no link', () => {
    setup({
      status: makeStatus({ availability: { status: 'installing', message: '' } }),
    })
    mount()
    expect(screen.getByText('Installing TradingAgents…')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
