// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { tradingApi } from '../../../api/trading'
import type { TradingRun, TradingWorkspace, TradingOrder } from '../../../types/trading'
import { WorkspaceForm } from './WorkspaceForm'
import { RunForm } from './RunForm'
import { TradingOrders } from './TradingOrders'
import { RunTools } from './RunTools'
import { WorkspaceDetail } from './WorkspaceDetail'
import { useTradingRun, useTradingRuns } from '../../../hooks/useTrading'

vi.mock('../../../hooks/useTrading', () => ({ useTradingRun: vi.fn(), useTradingRuns: vi.fn() }))
vi.mock('./TradingPerformance', () => ({ TradingPerformance: () => null }))

vi.mock('../../../api/trading', () => ({
  tradingApi: {
    createWorkspace: vi.fn(),
    updateWorkspace: vi.fn(),
    createRun: vi.fn(),
    issueAccess: vi.fn(),
    revokeAccess: vi.fn(),
    script: vi.fn(),
    agent: vi.fn(),
    action: vi.fn(),
  },
}))
const workspace: TradingWorkspace = {
  id: 'tw_demo',
  name: 'Monthly account',
  instructions: 'Manage the allocation.',
  agent_kind: 'lucius',
  agent_label: 'Lucius',
  revision: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  mandate: {
    allowed_asset_classes: ['stock', 'crypto'],
    max_order_notional: null,
    max_model_cost_usd: '10',
    fee_bps: '0',
    slippage_bps: '0',
    max_participation: '1',
    max_quote_age_seconds: 300,
  },
}
const run: TradingRun = {
  id: 'trun_demo',
  workspace_id: workspace.id,
  workspace_snapshot: workspace,
  mode: 'paper',
  state: 'running',
  start_at: '2026-01-01T00:00:00Z',
  end_at: '2026-02-01T00:00:00Z',
  current_time: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  agent_state: 'idle',
  base_currency: 'USD',
  model_cost_usd: '0',
  data_version: 'synthetic',
  agent_version: 'v1',
  initial_capital: { USD: '1000' },
}
const order: TradingOrder = {
  id: 'order_demo',
  run_id: run.id,
  account_id: 'account_demo',
  instrument: { symbol: 'ACME', venue: 'simulation', asset_class: 'stock', currency: 'USD' },
  side: 'buy',
  quantity: '2',
  filled_quantity: '1',
  order_type: 'market',
  status: 'partially_filled',
  created_at: '2026-01-01T00:00:00Z',
  request_id: 'request_demo',
}

beforeEach(() => {
  vi.clearAllMocks()
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

describe('trading workspace configuration', () => {
  it.each(['workspace', 'run'] as const)(
    'preserves Escape dismissal after a refused %s save',
    async (kind) => {
      let reject!: (error: Error) => void
      const request = new Promise<never>((_, deny) => {
        reject = deny
      })
      const close = vi.fn()
      if (kind === 'workspace') {
        vi.mocked(tradingApi.createWorkspace).mockReturnValue(request)
        render(<WorkspaceForm onClose={close} onSaved={vi.fn()} />)
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Monthly account' } })
      } else {
        vi.mocked(tradingApi.createRun).mockReturnValue(request)
        render(<RunForm workspace={workspace} onClose={close} onSaved={vi.fn()} />)
      }
      const submit = screen.getByRole('button', {
        name: kind === 'workspace' ? 'Create workspace' : 'Create run',
      })
      submit.focus()
      fireEvent.click(submit)
      expect(document.activeElement).not.toBe(submit)
      reject(new Error('Server refused save'))
      await screen.findByText('Server refused save')
      fireEvent.keyDown(document.activeElement!, { key: 'Escape', code: 'Escape' })
      await waitFor(() => expect(close).toHaveBeenCalled())
    },
  )

  it('creates an agent-independent workspace with a server-enforced mandate', async () => {
    vi.mocked(tradingApi.createWorkspace).mockResolvedValue(workspace)
    const saved = vi.fn()
    render(<WorkspaceForm onClose={vi.fn()} onSaved={saved} />)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Monthly account' } })
    fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'external' } })
    fireEvent.change(screen.getByLabelText('Maximum order value'), { target: { value: '250' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }))
    await waitFor(() => expect(tradingApi.createWorkspace).toHaveBeenCalled())
    expect(vi.mocked(tradingApi.createWorkspace).mock.calls[0]?.[0]).toMatchObject({
      name: 'Monthly account',
      agent_kind: 'external',
      mandate: { max_order_notional: '250', max_model_cost_usd: '10' },
    })
    expect(saved).toHaveBeenCalledWith(workspace)
  })

  it('retains the workspace draft after a refused save', async () => {
    vi.mocked(tradingApi.createWorkspace).mockRejectedValue(new Error('Invalid mandate'))
    render(<WorkspaceForm onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Keep this name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }))
    expect(await screen.findByText('Invalid mandate')).toBeTruthy()
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Keep this name')
  })

  it('creates a backtest with an explicit synthetic dataset and multiple accounts', async () => {
    vi.mocked(tradingApi.createRun).mockResolvedValue(run)
    render(<RunForm workspace={workspace} onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Environment'), { target: { value: 'backtest' } })
    fireEvent.click(screen.getByRole('button', { name: 'Load synthetic example' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create run' }))
    await waitFor(() => expect(tradingApi.createRun).toHaveBeenCalled())
    const input = vi.mocked(tradingApi.createRun).mock.calls[0]?.[1]
    expect(input?.mode).toBe('backtest')
    expect(input?.data_version).toBe('synthetic-example-v1')
    expect(input?.accounts).toHaveLength(2)
    expect(input?.observations).toHaveLength(4)
    expect(input?.live_authorized).toBe(false)
  })

  it('requires explicit live authorization and presents backend refusal without losing the form', async () => {
    vi.mocked(tradingApi.createRun).mockRejectedValue(new Error('Broker account is not verified'))
    render(<RunForm workspace={workspace} onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Environment'), { target: { value: 'live' } })
    expect((screen.getByRole('button', { name: 'Create run' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    fireEvent.change(screen.getByLabelText('Broker connector ID'), {
      target: { value: 'mcp:example' },
    })
    fireEvent.change(screen.getByLabelText('Dedicated broker account ID'), {
      target: { value: 'dedicated_demo' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: /I authorize this run/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Create run' }))
    expect(await screen.findByText('Broker account is not verified')).toBeTruthy()
    expect((screen.getByLabelText('Environment') as HTMLSelectElement).value).toBe('live')
  })
})

describe('execution visibility and controls', () => {
  it('exposes a rejected live start accessibly after dismissing its confirmation', async () => {
    const ready = { ...run, mode: 'live' as const, state: 'ready' as const }
    const query = <T,>(data: T) => ({
      data,
      error: null,
      initialLoading: false,
      refreshing: false,
      refresh: vi.fn().mockResolvedValue(data),
    })
    vi.mocked(useTradingRuns).mockReturnValue(query({ items: [ready], total: 1 }))
    vi.mocked(useTradingRun).mockReturnValue({
      run: query(ready),
      accounts: query({ items: [], total: 0 }),
      orders: query({ items: [], total: 0 }),
      fills: query({ items: [], total: 0 }),
      performance: query(undefined),
    })
    vi.mocked(tradingApi.action).mockRejectedValue(
      new Error('No verified live trading adapter is available'),
    )
    render(
      <WorkspaceDetail
        workspace={workspace}
        runId={ready.id}
        section="overview"
        onRun={vi.fn()}
        onSection={vi.fn()}
        onBack={vi.fn()}
        onEdit={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
    const confirm = screen.getByRole('dialog')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Start run' }))
    const error = await screen.findByRole('alert')
    expect(error.textContent).toContain('No verified live trading adapter is available')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(error.closest('[aria-hidden="true"]')).toBeNull()
    expect(screen.getAllByText('ready').length).toBeGreaterThan(0)
  })

  it('shows partial fills and requests cancellation only after confirmation', async () => {
    const cancel = vi.fn().mockResolvedValue(undefined)
    render(<TradingOrders orders={[order]} fills={[]} onCancel={cancel} busy={false} />)
    expect(screen.getByText('partially filled')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel order ACME' }))
    expect(cancel).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Request cancellation' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(order))
  })

  it('never offers a retry or cancellation for an unknown submission outcome', () => {
    render(
      <TradingOrders
        orders={[{ ...order, status: 'submission_unknown' }]}
        fills={[]}
        onCancel={vi.fn()}
        busy={false}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Cancel order ACME' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'View order ACME' }))
    expect(screen.getByText(/broker outcome is unknown/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /retry order/i })).toBeNull()
  })

  it('refreshes an open order detail when a later fill changes its state', () => {
    const { rerender } = render(
      <TradingOrders orders={[order]} fills={[]} onCancel={vi.fn()} busy={false} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'View order ACME' }))
    rerender(
      <TradingOrders
        orders={[{ ...order, status: 'filled', filled_quantity: '2' }]}
        fills={[]}
        onCancel={vi.fn()}
        busy={false}
      />,
    )
    expect(within(screen.getByRole('dialog')).getByText('filled')).toBeTruthy()
    expect(within(screen.getByRole('dialog')).queryByText('partially filled')).toBeNull()
  })

  it('renders a broker fill using its canonical timestamp field', () => {
    const timestamp = '2026-01-04T12:30:00Z'
    render(
      <TradingOrders
        orders={[order]}
        fills={[
          {
            id: 'fill-demo',
            order_id: order.id,
            account_id: order.account_id,
            quantity: '1',
            price: '100',
            fees: '0.5',
            timestamp,
          },
        ]}
        onCancel={vi.fn()}
        busy={false}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'View order ACME' }))
    const expected = new Date(timestamp).toLocaleString()
    expect(
      within(screen.getByRole('dialog')).getByText(
        (text) => text.includes(expected) && text.includes('Fees'),
      ),
    ).toBeTruthy()
  })

  it('keeps external credentials in the one-time dialog and discards them on close', async () => {
    vi.mocked(tradingApi.issueAccess).mockResolvedValue({
      token: 'synthetic-scoped-token',
      endpoint: '/api/trading/gateway/trun_demo',
      expires_at: '2026-02-01T00:00:00Z',
    })
    render(<RunTools run={run} />)
    fireEvent.click(screen.getByRole('button', { name: 'External agent access' }))
    fireEvent.click(screen.getByRole('button', { name: 'Issue run credential' }))
    expect(await screen.findByDisplayValue('synthetic-scoped-token')).toBeTruthy()
    expect(JSON.stringify(localStorage)).not.toContain('synthetic-scoped-token')
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
    fireEvent.click(screen.getByRole('button', { name: 'External agent access' }))
    expect(screen.queryByDisplayValue('synthetic-scoped-token')).toBeNull()
  })

  it('surfaces script isolation failures instead of reporting a successful run', async () => {
    vi.mocked(tradingApi.script).mockRejectedValue(new Error('Docker isolation is unavailable'))
    render(<RunTools run={run} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run strategy script' }))
    fireEvent.click(screen.getByRole('button', { name: 'Execute strategy' }))
    expect(await screen.findByText('Docker isolation is unavailable')).toBeTruthy()
    expect(screen.queryByText(/Script finished/)).toBeNull()
  })

  it('discards a credential that arrives after its dialog has already closed', async () => {
    let resolve!: (value: { token: string; endpoint: string; expires_at: string }) => void
    vi.mocked(tradingApi.issueAccess).mockReturnValue(
      new Promise((value) => {
        resolve = value
      }),
    )
    render(<RunTools run={run} />)
    fireEvent.click(screen.getByRole('button', { name: 'External agent access' }))
    fireEvent.click(screen.getByRole('button', { name: 'Issue run credential' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }))
    resolve({
      token: 'late-synthetic-token',
      endpoint: '/api/trading/gateway/trun_demo',
      expires_at: '2026-02-01T00:00:00Z',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'External agent access' }))
    expect(screen.queryByDisplayValue('late-synthetic-token')).toBeNull()
  })

  it('keeps Escape dismissal working after a successful asynchronous strategy execution', async () => {
    let resolve!: (value: Record<string, unknown>) => void
    vi.mocked(tradingApi.script).mockReturnValue(
      new Promise((value) => {
        resolve = value
      }),
    )
    render(<RunTools run={run} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run strategy script' }))
    const execute = screen.getByRole('button', { name: 'Execute strategy' })
    execute.focus()
    fireEvent.click(execute)
    // jsdom retains focus on disabled buttons; browsers can instead fall back to body.
    // Neither should remain the dialog's keyboard target while execution is in flight.
    expect(document.activeElement).not.toBe(execute)
    resolve({ ok: true })
    await screen.findByText(/Script finished/)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
    fireEvent.keyDown(document.activeElement!, { key: 'Escape', code: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})
