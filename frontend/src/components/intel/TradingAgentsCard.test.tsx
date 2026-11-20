// @vitest-environment jsdom
//
// The TradingAgents card on the asset Analysis tab: gated by the flag, then one state at a time
// (spec docs/specs/trading-agents-ui.md §2.3). The data hooks are stubbed so every state is
// reachable without a server; ticker ACME, synthetic amounts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { overrideFlagsForTests } from '../../hooks/useFlag'
import { TaError } from '../../hooks/useTradingAgents'
import type { TaRun, TaRunSummary, TaStatus } from '../../types/tradingAgents'
import { CONTEXT, makeRun, makeStatus, makeSummary } from './tradingAgentsFixtures'
import { TradingAgentsCard } from './TradingAgentsCard'
import { _resetTaPanelMemoryForTests } from './useTaPanel'
import type { TaFocus } from './useTaPanel'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const api = vi.hoisted(() => ({
  startTaRun: vi.fn(),
  cancelTaRun: vi.fn(),
  deleteTaRun: vi.fn(),
}))

interface Query<T> {
  data: T | undefined
  error: unknown
  initialLoading: boolean
  refresh: () => Promise<T>
}

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
  deleteTaRun: api.deleteTaRun,
  useTaStatus: () => state.status,
  useTaRuns: () => state.runs,
  useTaRun: () => state.run,
  useTaEstimate: () => state.estimate,
  useTaContextPreview: () => state.preview,
}))

const query = <T,>(data: T | undefined, over: Partial<Query<T>> = {}): Query<T> => ({
  data,
  error: null,
  initialLoading: false,
  refresh: () => Promise.resolve(data as T),
  ...over,
})

function setup(opts: { status?: TaStatus; runs?: TaRunSummary[]; run?: TaRun; total?: number }) {
  state.status = query(opts.status ?? makeStatus())
  const runs = opts.runs ?? []
  state.runs = query({ runs, total: opts.total ?? runs.length })
  state.run = query(opts.run)
  state.estimate = query({
    estimate_usd: 0.42,
    low_usd: 0.3,
    high_usd: 0.6,
    calls: 38,
    basis: 'history' as const,
    sample_size: 10,
    plan_included: false,
    priced: true,
    unpriced_models: [],
  })
  state.preview = query(CONTEXT)
}

let restore: () => void
beforeEach(() => {
  restore = overrideFlagsForTests({ trading_agents: true })
  setup({})
})
afterEach(() => {
  cleanup()
  _resetTaPanelMemoryForTests()
  restore()
  vi.clearAllMocks()
})

const mount = (focus: TaFocus | null = null, onFocusHandled?: () => void) =>
  render(
    <TradingAgentsCard
      instrumentId="inst_acme"
      ticker="ACME"
      focus={focus}
      onFocusHandled={onFocusHandled}
    />,
  )
const openFull = () => fireEvent.click(screen.getByRole('button', { name: 'Show full analysis' }))

describe.each([false, true])('with the trading_agents flag %s', (on) => {
  it(on ? 'renders the card' : 'renders nothing at all', () => {
    restore()
    restore = overrideFlagsForTests({ trading_agents: on })
    const { container } = mount()
    if (on) expect(screen.getByText('Research view · TradingAgents')).toBeTruthy()
    else expect(container.innerHTML).toBe('')
  })
})

describe('loading and error', () => {
  it('shows a busy skeleton until the status loads', () => {
    state.status = query<TaStatus>(undefined, { initialLoading: true })
    const { container } = mount()
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
  })

  it('offers a retry when the status cannot load', () => {
    state.status = query<TaStatus>(undefined, { error: new Error('x') })
    mount()
    expect(screen.getByText("Couldn't load TradingAgents")).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('not available', () => {
  const cases: [string, Partial<TaStatus>, string, string | null][] = [
    [
      'connector off',
      { availability: { status: 'connector_off', message: '' } },
      "TradingAgents isn't set up yet.",
      'Set up',
    ],
    [
      'paused',
      { availability: { status: 'connector_paused', message: '' } },
      'The TradingAgents connector is paused.',
      'Resume',
    ],
    [
      'not installed',
      { availability: { status: 'not_installed', message: 'Install TradingAgents first.' } },
      'Install TradingAgents first.',
      'Open setup',
    ],
    [
      'needs reinstall',
      { availability: { status: 'needs_reinstall', message: 'Reinstall.' } },
      'TradingAgents needs reinstalling after an update.',
      'Open setup',
    ],
    [
      'installing',
      { availability: { status: 'installing', message: '' } },
      'Installing TradingAgents…',
      null,
    ],
    [
      'no model',
      { availability: { status: 'model_unavailable', message: 'No model.' } },
      'No model.',
      'Choose models',
    ],
    [
      'notice not accepted',
      { disclaimer_accepted: false },
      'Read and accept the research-only notice to run your first analysis.',
      'Open setup',
    ],
  ]

  it.each(cases)('%s shows one line and no form', (_name, over, text, button) => {
    setup({ status: makeStatus(over) })
    mount()
    expect(screen.getByText(text)).toBeTruthy()
    expect(screen.queryByText('Run multi-agent analysis')).toBeNull()
    if (button) {
      fireEvent.click(screen.getByRole('button', { name: button }))
      expect(navigate).toHaveBeenCalledWith({
        to: '/connectors',
        search: { connector: 'trading_agents' },
      })
    } else {
      expect(screen.queryByRole('button')).toBeNull()
    }
  })

  it('keeps the last report readable with the notice and the disclaimer', () => {
    const summary = makeSummary()
    setup({
      status: makeStatus({ availability: { status: 'connector_paused', message: '' } }),
      runs: [summary],
      run: makeRun(),
    })
    mount()
    expect(screen.getByRole('img', { name: 'Rating: Overweight, 4 of 5' })).toBeTruthy()
    expect(screen.getByText(/Research only\. Ratings come from AI models/)).toBeTruthy()
    expect(screen.queryByText('Run new analysis')).toBeNull()
  })
})

describe('ready, no runs (the form)', () => {
  it('shows the notice, the form and the estimate, with one primary action', () => {
    mount()
    expect(screen.getByText('Run multi-agent analysis')).toBeTruthy()
    expect(
      screen.getByText(/About \$0\.42 \(range \$0\.30 – \$0\.60\) · about 38 model calls/),
    ).toBeTruthy()
    expect(screen.getByText('Based on your last 10 runs.')).toBeTruthy()
    expect(screen.getByText(/Stops at \$2\.00 or 150 calls per run\./)).toBeTruthy()
    expect(screen.getByText(/Research only\. Ratings come from AI models/)).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'Sentiment' })).toBeTruthy()
  })

  it('starts the run with the chosen analysts and context level', async () => {
    api.startTaRun.mockResolvedValue('tar_000000000002')
    mount()
    fireEvent.click(screen.getByRole('checkbox', { name: 'News' }))
    fireEvent.click(screen.getByRole('tab', { name: 'This position' }))
    fireEvent.click(screen.getByRole('button', { name: 'Run multi-agent analysis' }))
    await waitFor(() =>
      expect(api.startTaRun).toHaveBeenCalledWith(
        'ACME',
        ['market', 'social', 'fundamentals'],
        'position',
      ),
    )
  })

  it('refuses to start with no analyst', () => {
    mount()
    for (const name of ['Market', 'Sentiment', 'News', 'Fundamentals']) {
      fireEvent.click(screen.getByRole('checkbox', { name }))
    }
    expect(screen.getByText('Pick at least one analyst.')).toBeTruthy()
    const start = screen.getByRole('button', {
      name: 'Run multi-agent analysis',
    }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
  })

  it('shows the server refusal under the button, with the way to the setup page', async () => {
    api.startTaRun.mockRejectedValue(new TaError('Your monthly cap would be passed.'))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Run multi-agent analysis' }))
    expect(await screen.findByText('Your monthly cap would be passed.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open setup' })).toBeTruthy()
  })

  it('names the plan, not dollars, for a subscription estimate', () => {
    setup({
      status: makeStatus({
        models: {
          deep: 'claude_cli:sonnet',
          quick: 'claude_cli:haiku',
          transports: { deep: 'claude_cli', quick: 'claude_cli' },
        },
      }),
    })
    state.estimate = query({
      estimate_usd: 0,
      low_usd: 0,
      high_usd: 0,
      calls: 40,
      basis: 'static' as const,
      sample_size: 0,
      plan_included: true,
      priced: true,
      unpriced_models: [],
    })
    mount()
    expect(
      screen.getByText('Uses your Claude Code plan · about 40 model calls · no dollars'),
    ).toBeTruthy()
  })

  it('names the models with no known price', () => {
    state.estimate = query({
      estimate_usd: 0.42,
      low_usd: 0.3,
      high_usd: 0.6,
      calls: 38,
      basis: 'static' as const,
      sample_size: 0,
      plan_included: false,
      priced: false,
      unpriced_models: ['acme-1', 'acme-2'],
    })
    mount()
    expect(
      screen.getByText('No price known for acme-1, acme-2 — a run on it is refused.'),
    ).toBeTruthy()
  })

  it('disables Start when the shared list cannot load', () => {
    state.preview = query(undefined, { error: new Error('x') })
    mount()
    fireEvent.click(screen.getByText('What will be shared'))
    const details = screen.getByText('What will be shared').closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    expect(screen.getByText(/Couldn't load what would be shared/)).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Run multi-agent analysis' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })
})

describe('running', () => {
  const progress = [
    { stage: 'Market Analyst', status: 'started' as const, at: '2026-11-19T10:00:05Z' },
    { stage: 'Market Analyst', status: 'finished' as const, at: '2026-11-19T10:01:00Z' },
    { stage: 'Bull Researcher', status: 'started' as const, at: '2026-11-19T10:01:05Z' },
  ]

  it('shows the stage list with the active stage, the live line and Cancel', async () => {
    const running = makeRun({
      status: 'running',
      signal: null,
      progress,
      llm_calls: 14,
      cost_usd: 0.11,
    })
    setup({ runs: [makeSummary({ status: 'running', signal: null })], run: running })
    mount()
    const stages = screen.getAllByRole('listitem')
    expect(stages).toHaveLength(5)
    expect(within(stages[0]!).getByText(/Done:/)).toBeTruthy()
    expect(within(stages[1]!).getByText(/In progress:/)).toBeTruthy()
    expect(within(stages[1]!).getByText('Debating')).toBeTruthy()
    expect(within(stages[4]!).getByText(/Waiting:/)).toBeTruthy()
    expect(screen.getByText(/14 model calls · \$0\.11 so far \(of \$2\.00\)/)).toBeTruthy()
    api.cancelTaRun.mockResolvedValue(undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel analysis of ACME' }))
    await waitFor(() => expect(api.cancelTaRun).toHaveBeenCalledWith(running.id))
    expect(screen.getByRole('button', { name: 'Cancel analysis of ACME' }).textContent).toBe(
      'Cancelling…',
    )
  })

  it('shows the server message and re-enables Cancel when cancelling fails', async () => {
    const running = makeRun({ status: 'running', signal: null, progress })
    setup({ runs: [makeSummary({ status: 'running', signal: null })], run: running })
    api.cancelTaRun.mockRejectedValue(new TaError('The run already finished.'))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel analysis of ACME' }))
    expect((await screen.findByRole('alert')).textContent).toBe('The run already finished.')
    expect(screen.getByRole('button', { name: 'Cancel analysis of ACME' }).textContent).toBe(
      'Cancel',
    )
  })

  it('says it is queued behind the others, and shows no stage list', () => {
    const queued = makeRun({ status: 'queued', signal: null })
    setup({
      status: makeStatus({ queue_length: 3 }),
      runs: [makeSummary({ status: 'queued', signal: null })],
      run: queued,
    })
    mount()
    expect(screen.getByText('Queued behind 2 other runs')).toBeTruthy()
    expect(screen.queryByRole('list', { name: 'Analysis stages' })).toBeNull()
  })

  it('counts plan calls instead of dollars on a subscription, and flags unpriced calls', () => {
    const running = makeRun({
      status: 'running',
      signal: null,
      cost_usd: 0,
      plan_included_calls: 14,
      llm_calls: 14,
      unpriced_calls: 2,
      config_snapshot: { transports: { deep: 'codex_cli', quick: 'codex_cli' } },
    })
    setup({ runs: [makeSummary({ status: 'running', signal: null })], run: running })
    mount()
    expect(screen.getByText(/14 model calls on your Codex plan · 2 calls unpriced/)).toBeTruthy()
  })

  it('ignores a graph node that belongs to no stage', () => {
    const odd = makeRun({
      status: 'running',
      signal: null,
      progress: [{ stage: 'Some New Node', status: 'started', at: '2026-11-19T10:00:05Z' }],
    })
    setup({ runs: [makeSummary({ status: 'running', signal: null })], run: odd })
    mount()
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByText('Starting…')).toBeTruthy()
  })
})

describe('done', () => {
  it("shows the server's reading of the rating under it when the take used this run", () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    render(
      <TradingAgentsCard
        instrumentId="inst_acme"
        ticker="ACME"
        takeView={{ runId: 'tar_000000000001', view: 'negative outlook, not a buy now' }}
      />,
    )
    expect(screen.getByText('negative outlook, not a buy now')).toBeTruthy()
  })

  it('shows no reading for a run the take did not use, or when the server sent none', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    const { unmount } = render(
      <TradingAgentsCard
        instrumentId="inst_acme"
        ticker="ACME"
        takeView={{ runId: 'tar_other', view: 'steady outlook' }}
      />,
    )
    expect(screen.queryByText('steady outlook')).toBeNull()
    unmount()
    mount()
    expect(screen.queryByText('negative outlook, not a buy now')).toBeNull()
  })

  it('shows only the rating, the decision and the disclaimer until the full analysis is opened', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    const { container } = mount()
    expect(screen.getByRole('img', { name: 'Rating: Overweight, 4 of 5' })).toBeTruthy()
    expect(container.querySelector('.ta-decision')?.textContent).toBe(
      'Overweight ACME: the thesis holds.',
    )
    expect(screen.getByText(/Research only\. Ratings come from AI models/)).toBeTruthy()
    expect(screen.queryByText('Market', { selector: 'summary' })).toBeNull()
    expect(screen.queryByText('What was shared', { selector: 'summary' })).toBeNull()
    expect(screen.queryByText(/\$0\.38 metered/)).toBeNull()
    expect(screen.queryByRole('list', { name: 'Earlier analyses' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()
  })

  it('shows the report rows that exist, the history and the footer once opened', () => {
    const older = makeSummary({ id: 'tar_000000000000', signal: 'Hold', trade_date: '2026-10-30' })
    setup({ runs: [makeSummary(), older], run: makeRun(), total: 2 })
    mount()
    openFull()
    expect(screen.getAllByRole('img', { name: 'Rating: Overweight, 4 of 5' })).toHaveLength(2)
    for (const row of ['Market', 'Bull vs Bear', 'Portfolio manager', 'What was shared']) {
      expect(screen.getByText(row, { selector: 'summary' })).toBeTruthy()
    }
    for (const row of ['Sentiment', 'News', 'Fundamentals', 'Trader plan', 'Risk debate']) {
      expect(screen.queryByText(row, { selector: 'summary' })).toBeNull()
    }
    expect(screen.getByText(/\$0\.38 metered/)).toBeTruthy()
    expect(screen.getByRole('list', { name: 'Earlier analyses' })).toBeTruthy()
    expect(screen.getByText(/Research only\. Ratings come from AI models/)).toBeTruthy()
  })

  it('cuts a long decision at a sentence boundary and expands it in place', () => {
    const long = `${'First point stands. '.repeat(2)}Third point holds. Fourth point is held back. Fifth too.`
    setup({ runs: [makeSummary()], run: makeRun({ decision_text: long }) })
    const { container } = mount()
    const decision = () => container.querySelector('.ta-decision')?.textContent
    expect(decision()).toBe('First point stands. First point stands. Third point holds.')
    const more = screen.getByRole('button', { name: 'Show more' })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(more)
    expect(decision()).toBe(long)
    const less = screen.getByRole('button', { name: 'Show less' })
    expect(less.getAttribute('aria-expanded')).toBe('true')
    expect(less.getAttribute('aria-controls')).toBe(
      container.querySelector('.ta-decision')?.getAttribute('id'),
    )
    fireEvent.click(less)
    expect(decision()).toBe('First point stands. First point stands. Third point holds.')
  })

  it('opens and closes the full analysis with a labelled, controlled region', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    const toggle = screen.getByRole('button', { name: 'Show full analysis' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('region', { name: 'Full analysis' })).toBeNull()
    fireEvent.click(toggle)
    const hide = screen.getByRole('button', { name: 'Hide full analysis' })
    expect(hide.getAttribute('aria-expanded')).toBe('true')
    const region = screen.getByRole('region', { name: 'Full analysis' })
    expect(hide.getAttribute('aria-controls')).toBe(region.id)
    fireEvent.click(hide)
    expect(screen.queryByText('Market', { selector: 'summary' })).toBeNull()
  })

  it('remembers the open state for the ticker while the page lives', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    openFull()
    cleanup()
    mount()
    expect(screen.getByRole('button', { name: 'Hide full analysis' })).toBeTruthy()
  })

  it('opens the requested run in full, scrolls to it and reports the request handled', () => {
    const older = makeSummary({ id: 'tar_000000000000', signal: 'Hold', trade_date: '2026-10-30' })
    setup({ runs: [makeSummary(), older], run: makeRun({ id: older.id }), total: 2 })
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const handled = vi.fn()
    mount({ runId: 'tar_000000000000', seq: 1 }, handled)
    expect(screen.getByRole('button', { name: 'Hide full analysis' })).toBeTruthy()
    expect(scroll).toHaveBeenCalledTimes(1)
    expect(handled).toHaveBeenCalledTimes(1)
  })

  it('shows the latest run when the requested one is gone', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    mount({ runId: 'tar_deleted', seq: 1 })
    expect(screen.getByRole('img', { name: 'Rating: Overweight, 4 of 5' })).toBeTruthy()
  })

  it('shows the thesis chip from the server stance and never computes one', () => {
    const judged = makeRun({
      thesis_judgment: {
        role: 'core',
        reason: 'Compounder',
        holding_period: '10 years',
        breaks_if: 'Margins collapse',
        levels: {},
        stance: 'intact',
        position: 'Price sits between your levels',
        read: 'between your levels — the thesis is intact on price',
        cite: 'Your thesis (core): "Compounder".',
      },
    })
    setup({ runs: [makeSummary()], run: judged })
    mount()
    expect(
      screen.getByText('Your thesis: between your levels — the thesis is intact on price'),
    ).toBeTruthy()
  })

  it('leaves the chip out when the owner has no thesis', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    expect(screen.queryByText(/Your thesis:/)).toBeNull()
  })

  it('shows Unclear as a neutral pill with no scale cell lit', () => {
    setup({
      runs: [makeSummary({ signal: 'REVIEW' })],
      run: makeRun({ signal: 'REVIEW' }),
    })
    const { container } = mount()
    expect(screen.getByRole('img', { name: /Rating: Unclear/ })).toBeTruthy()
    expect(container.querySelector('.ta-rating--unclear')).not.toBeNull()
    expect(container.querySelector('.ta-scale__cell--on')).toBeNull()
  })

  it('notes a shortened report and lists the recipient of what was shared', () => {
    setup({
      runs: [makeSummary()],
      run: makeRun({
        reports: { fields: { market_report: 'Long.' }, truncated: ['market_report'] },
      }),
    })
    mount()
    openFull()
    expect(screen.getByText('Shortened at 40,000 characters.')).toBeTruthy()
    expect(screen.getByText(/receives the ticker and date, and:/)).toBeTruthy()
  })

  it('confirms before deleting a run', async () => {
    api.deleteTaRun.mockResolvedValue(undefined)
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    openFull()
    fireEvent.click(screen.getByRole('button', { name: 'Delete run' }))
    expect(screen.getByText('Delete this run?')).toBeTruthy()
    const buttons = screen.getAllByRole('button', { name: 'Delete run' })
    fireEvent.click(buttons[buttons.length - 1]!)
    await waitFor(() => expect(api.deleteTaRun).toHaveBeenCalledWith('tar_000000000001'))
  })

  it('keeps the dialog open and shows the server message when the delete fails', async () => {
    api.deleteTaRun.mockRejectedValue(new TaError('The run is locked.'))
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    openFull()
    fireEvent.click(screen.getByRole('button', { name: 'Delete run' }))
    const buttons = screen.getAllByRole('button', { name: 'Delete run' })
    fireEvent.click(buttons[buttons.length - 1]!)
    expect((await screen.findByRole('alert')).textContent).toBe('The run is locked.')
    expect(screen.getByText('Delete this run?')).toBeTruthy()
  })

  it('closes the dialog once the delete succeeds', async () => {
    api.deleteTaRun.mockResolvedValue(undefined)
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    openFull()
    fireEvent.click(screen.getByRole('button', { name: 'Delete run' }))
    const buttons = screen.getAllByRole('button', { name: 'Delete run' })
    fireEvent.click(buttons[buttons.length - 1]!)
    await waitFor(() => expect(screen.queryByText('Delete this run?')).toBeNull())
  })

  it('swaps to the form for a new run and back', () => {
    setup({ runs: [makeSummary()], run: makeRun() })
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Run new analysis' }))
    expect(screen.getByText('Run multi-agent analysis')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('img', { name: 'Rating: Overweight, 4 of 5' })).toBeTruthy()
  })

  it('lists earlier runs and makes the picked one the featured run', () => {
    const older = makeSummary({ id: 'tar_000000000000', signal: 'Hold', trade_date: '2026-10-30' })
    setup({ runs: [makeSummary(), older], run: makeRun(), total: 2 })
    mount()
    openFull()
    const list = screen.getByRole('list', { name: 'Earlier analyses' })
    expect(within(list).getAllByRole('button')).toHaveLength(2)
    expect(within(list).getAllByRole('button')[0]!.getAttribute('aria-current')).toBe('true')
  })
})

describe('stopped or failed', () => {
  const cases: [TaRun['status'], Partial<TaRun>, string, string][] = [
    ['failed', { error: 'The model refused.' }, 'Failed', 'The model refused.'],
    [
      'stopped_budget',
      { llm_calls: 88 },
      'Stopped: budget',
      'It reached its $2.00 per-run cap after 88 calls.',
    ],
    ['stopped_calls', {}, 'Stopped: call limit', 'It reached its model-call limit.'],
    ['timed_out', {}, 'Timed out', 'It ran out of time before it finished.'],
    ['cancelled', {}, 'Cancelled', 'You cancelled this run.'],
    ['interrupted', {}, 'Interrupted', 'The app restarted while it was running.'],
  ]

  it.each(cases)(
    '%s names its reason and offers Retry, with no partial report',
    (status, over, label, why) => {
      setup({
        runs: [makeSummary({ status, signal: null })],
        run: makeRun({ status, signal: null, decision_text: null, ...over }),
      })
      mount()
      expect(screen.getByText(label)).toBeTruthy()
      expect(screen.getByText(why)).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
      expect(screen.queryByText('Market', { selector: 'summary' })).toBeNull()
      expect(screen.getByText(/Research only\. Ratings come from AI models/)).toBeTruthy()
    },
  )

  it('retries with the same analysts and context level', async () => {
    api.startTaRun.mockResolvedValue('tar_000000000009')
    setup({
      runs: [makeSummary({ status: 'failed', signal: null })],
      run: makeRun({
        status: 'failed',
        signal: null,
        analysts: ['market', 'news'],
        context_level: 'position',
      }),
    })
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() =>
      expect(api.startTaRun).toHaveBeenCalledWith('ACME', ['market', 'news'], 'position'),
    )
  })
})
