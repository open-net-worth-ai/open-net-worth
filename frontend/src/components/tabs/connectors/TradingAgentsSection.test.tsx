// @vitest-environment jsdom
//
// The TradingAgents setup form inside the connector detail: the research-only notice and the
// install are immediate writes, everything else arms the footer Save, which PATCHes only what
// changed. Hooks are stubbed; ticker ACME, synthetic amounts.
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TA_NEVER_SENT } from '../../../types/tradingAgentsConstants'
import type { TaConfig, TaStatus } from '../../../types/tradingAgents'
import { makeStatus } from '../../intel/tradingAgentsFixtures'
import { useConnectorSaveHost, type ConnectorSaveHost } from './formSave'
import { TradingAgentsSection } from './TradingAgentsSection'

const api = vi.hoisted(() => ({
  saveTaConfig: vi.fn(),
  acceptTaDisclaimer: vi.fn(),
  installTa: vi.fn(),
}))
const state = vi.hoisted(() => ({ config: null as unknown, status: null as unknown }))

vi.mock('../../../hooks/useTradingAgents', () => ({
  TaError: class TaError extends Error {
    field: string | null
    constructor(message: string, ...rest: (string | null)[]) {
      super(message)
      this.field = rest[1] ?? null
    }
  },
  saveTaConfig: api.saveTaConfig,
  acceptTaDisclaimer: api.acceptTaDisclaimer,
  installTa: api.installTa,
  useTaConfig: () => state.config,
  useTaStatus: () => state.status,
}))
vi.mock('../../../hooks/useModels', () => ({
  useModels: () => ({
    models: [
      { id: 'claude-opus-4', provider_key: 'anthropic', provider_label: 'Anthropic' },
      { id: 'claude-haiku-4', provider_key: 'anthropic', provider_label: 'Anthropic' },
    ],
    defaultModel: 'claude-opus-4',
    loaded: true,
  }),
}))
vi.mock('./secrets', () => ({ revealConnectorSecret: vi.fn() }))

const CONFIG: TaConfig = {
  deep_model: null,
  quick_model: null,
  analysts: ['market', 'social', 'news', 'fundamentals'],
  max_debate_rounds: 1,
  max_risk_rounds: 1,
  max_tool_rounds: 5,
  max_llm_calls: 150,
  bridge_concurrency: null,
  per_run_cap_usd: 2,
  monthly_cap_usd: 20,
  context_level: 'investments',
  output_language: 'English',
  sec_edgar_user_agent: '',
  has_alpha_vantage_api_key: true,
  has_fred_api_key: false,
}

const query = <T,>(data: T) => ({
  data,
  error: null,
  initialLoading: false,
  refresh: () => Promise.resolve(data),
})

function setup(config: TaConfig = CONFIG, status: TaStatus = makeStatus()) {
  state.config = query(config)
  state.status = query(status)
}

function Harness({ hostRef }: { hostRef: { current: ConnectorSaveHost | null } }) {
  const host = useConnectorSaveHost()
  useEffect(() => {
    hostRef.current = host
  })
  return (
    <host.Provider>
      <TradingAgentsSection />
    </host.Provider>
  )
}

function mount() {
  const hostRef: { current: ConnectorSaveHost | null } = { current: null }
  render(<Harness hostRef={hostRef} />)
  return hostRef
}

const save = (hostRef: { current: ConnectorSaveHost | null }) =>
  act(async () => {
    await hostRef.current!.run()
  })

beforeEach(() => setup())
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('after an update', () => {
  it('shows the reinstall message in Install and offers the reinstall button', () => {
    const message = 'TradingAgents needs reinstalling after an update.'
    setup(CONFIG, makeStatus({ availability: { status: 'needs_reinstall', message } }))
    mount()
    const install = document.querySelectorAll('section.ta-section')[1]!
    expect(install.textContent).toContain(message)
    expect(screen.getByText('Needs reinstall')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reinstall' }))
    expect(api.installTa).toHaveBeenCalled()
  })
})

describe('the form', () => {
  it('shows the eight sections in order, and no AI key field', () => {
    mount()
    const titles = [...document.querySelectorAll('section.ta-section h3')].map((h) => h.textContent)
    expect(titles).toEqual([
      'Research-only notice',
      'Install',
      'Models',
      'Analysts and depth',
      'Budget',
      'Data sources',
      'Portfolio context',
      'Permissions',
    ])
    expect(screen.getByLabelText('Alpha Vantage API key (optional)')).toBeTruthy()
    expect(screen.getByLabelText('FRED API key (optional)')).toBeTruthy()
    expect(screen.queryByLabelText(/anthropic|openai/i)).toBeNull()
  })

  it('arms the footer Save only on a change, then PATCHes just that field', async () => {
    api.saveTaConfig.mockResolvedValue({ ...CONFIG, monthly_cap_usd: 30 })
    const host = mount()
    expect(host.current?.state?.dirty).toBeFalsy()
    fireEvent.change(screen.getByLabelText('Monthly cap (USD)'), { target: { value: '30' } })
    await waitFor(() => expect(host.current?.state?.dirty).toBe(true))
    await save(host)
    expect(api.saveTaConfig).toHaveBeenCalledWith({ monthly_cap_usd: 30 })
  })

  it('refuses an out-of-range value on the field and writes nothing', async () => {
    const host = mount()
    fireEvent.change(screen.getByLabelText('Debate rounds (1–3)'), { target: { value: '9' } })
    await waitFor(() => expect(host.current?.state?.dirty).toBe(true))
    await save(host)
    expect(api.saveTaConfig).not.toHaveBeenCalled()
    expect(screen.getByText('A whole number from 1 to 3.')).toBeTruthy()
  })

  it("puts the server's refusal on the field it names", async () => {
    const { TaError } = await import('../../../hooks/useTradingAgents')
    api.saveTaConfig.mockRejectedValue(
      new (TaError as unknown as new (m: string, c: string, f: string) => Error)(
        'per_run_cap_usd must be at most 50.',
        'validation_error',
        'per_run_cap_usd',
      ),
    )
    const host = mount()
    fireEvent.change(screen.getByLabelText('Per-run cap (USD)'), { target: { value: '5' } })
    await waitFor(() => expect(host.current?.state?.dirty).toBe(true))
    await save(host)
    expect(screen.getByText('per_run_cap_usd must be at most 50.')).toBeTruthy()
  })

  it('sends an explicit null to clear a saved key and omits a blank one', async () => {
    api.saveTaConfig.mockResolvedValue({ ...CONFIG, has_alpha_vantage_api_key: false })
    const host = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Clear saved key' }))
    await waitFor(() => expect(host.current?.state?.dirty).toBe(true))
    await save(host)
    expect(api.saveTaConfig).toHaveBeenCalledWith({ alpha_vantage_api_key: null })
  })

  it('resets blank parallel calls to automatic with a null', async () => {
    setup({ ...CONFIG, bridge_concurrency: 3 })
    api.saveTaConfig.mockResolvedValue(CONFIG)
    const host = mount()
    fireEvent.change(screen.getByLabelText('Parallel model calls (1–8)'), { target: { value: '' } })
    await waitFor(() => expect(host.current?.state?.dirty).toBe(true))
    await save(host)
    expect(api.saveTaConfig).toHaveBeenCalledWith({ bridge_concurrency: null })
  })

  it('warns when a saved model belongs to a provider that is gone', () => {
    setup({ ...CONFIG, deep_model: 'gone:model-x' })
    mount()
    expect(screen.getByText(/gone:model-x: this provider is turned off/)).toBeTruthy()
  })

  it('says a subscription model counts against the plan, not dollars', () => {
    setup({ ...CONFIG, quick_model: 'claude_cli:haiku' })
    mount()
    expect(screen.getByText(/Uses your Claude Code subscription/)).toBeTruthy()
  })
})

describe('the notice and the install are immediate writes', () => {
  it('keeps Accept disabled until the box is ticked, then records the notice at once', async () => {
    setup(CONFIG, makeStatus({ disclaimer_accepted: false }))
    api.acceptTaDisclaimer.mockResolvedValue(undefined)
    const host = mount()
    const accept = screen.getByRole('button', { name: 'Accept and continue' }) as HTMLButtonElement
    expect(accept.disabled).toBe(true)
    expect(screen.getByText('Tick the box to continue')).toBeTruthy()
    fireEvent.click(
      screen.getByRole('checkbox', {
        name: 'I understand this is research only, not financial advice.',
      }),
    )
    fireEvent.click(accept)
    await waitFor(() => expect(api.acceptTaDisclaimer).toHaveBeenCalledTimes(1))
    expect(host.current?.state?.dirty).toBeFalsy()
  })

  it('shows an accepted notice as done', () => {
    mount()
    expect(screen.getByText('Notice accepted.')).toBeTruthy()
  })

  it('shows the install error as the log and offers Try again', () => {
    setup(
      CONFIG,
      makeStatus({
        install: { status: 'failed', version: null, error: 'pip exited with status 1' },
      }),
    )
    mount()
    expect(screen.getByRole('alert').textContent).toBe('pip exited with status 1')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })

  it('starts the install from Install', async () => {
    api.installTa.mockResolvedValue(undefined)
    setup(CONFIG, makeStatus({ install: { status: 'absent', version: null, error: null } }))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Install' }))
    await waitFor(() => expect(api.installTa).toHaveBeenCalledTimes(1))
  })

  it('says plainly that the installed code can read everything the app stores here', () => {
    mount()
    const note = screen.getByRole('note', { name: 'Third-party software' }).textContent ?? ''
    expect(note).toContain("run with your computer user's permissions")
    expect(note).toContain(
      'read everything Open Net Worth stores on this machine, including the database and your saved keys',
    )
    expect(note).toContain(
      'local analysis logs inside your data folder, which are deleted when you remove the connector or the run',
    )
  })

  it('shows the installed commit shortened', () => {
    mount()
    expect(screen.getByText('commit 9fa1196')).toBeTruthy()
  })
})

describe('who receives what', () => {
  it('names the AI provider and the data vendors, and adds a vendor once its key is saved', () => {
    mount()
    const text = document.body.textContent ?? ''
    expect(text).toContain('Your AI provider')
    expect(text).toContain(
      'Yahoo Finance, SEC EDGAR, StockTwits, Reddit, Polymarket, Alpha Vantage.',
    )
    expect(text).not.toContain('Polymarket, Alpha Vantage, FRED')
  })

  it('keeps the never-sent list at the eight entries the backend ships', () => {
    expect(TA_NEVER_SENT).toHaveLength(8)
    mount()
    expect(document.body.textContent).toContain(TA_NEVER_SENT.join(', '))
  })
})

describe('loading', () => {
  it('shows a loading state until both reads land', () => {
    state.config = { ...query(undefined), data: undefined, initialLoading: true }
    mount()
    expect(screen.getByText('Loading TradingAgents settings…')).toBeTruthy()
  })
})
