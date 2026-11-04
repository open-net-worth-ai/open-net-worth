// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { tradingApi } from '../../../api/trading'
import type {
  DelegationCreate,
  DelegationReview,
  TradingRun,
  TradingWorkspace,
} from '../../../types/trading'
import { defaultDelegation } from './delegationDraft'
import { WorkspaceForm } from './WorkspaceForm'

vi.mock('../../../api/trading', () => ({
  tradingApi: { reviewDelegation: vi.fn(), createDelegation: vi.fn(), activateWorkspace: vi.fn() },
}))
const draft = (): DelegationCreate => {
  const value = defaultDelegation()
  value.workspace.name = 'Monthly delegation'
  value.workspace.instructions = 'Research and manage this allocation for one month.'
  value.workspace.agent_kind = 'external'
  value.workspace.agent_label = 'Research team'
  return value
}
const reviewed = (value = draft()): DelegationReview => ({ ...value, ready: true, issues: [] })
const saved = () => ({
  workspace: {
    ...draft().workspace,
    id: 'tw_saved',
    revision: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  } as TradingWorkspace,
  run: { id: 'trun_saved' } as TradingRun,
  started: true,
})

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
})
afterEach(cleanup)

describe('one reviewed delegation for expert and conversational setup', () => {
  it('reviews before creation and preserves original instructions, allocation and executor', async () => {
    const value = draft()
    vi.mocked(tradingApi.reviewDelegation).mockResolvedValue(reviewed(value))
    vi.mocked(tradingApi.createDelegation).mockResolvedValue(saved())
    const onSaved = vi.fn()
    render(<WorkspaceForm draft={value} onClose={vi.fn()} onSaved={onSaved} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review workspace' }))
    await screen.findByText(
      'Unsaved draft. Review the exact delegation and authority before starting.',
    )
    expect(tradingApi.createDelegation).not.toHaveBeenCalled()
    expect(vi.mocked(tradingApi.reviewDelegation).mock.calls[0]?.[0]).toMatchObject(value)
    fireEvent.click(screen.getByRole('button', { name: 'Create and start workspace' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved().workspace, 'trun_saved'))
    expect(vi.mocked(tradingApi.createDelegation).mock.calls[0]?.[0]).toMatchObject({
      workspace: {
        instructions: value.workspace.instructions,
        agent_kind: 'external',
        agent_label: 'Research team',
      },
      environment: { accounts: [{ capital: '1000' }], live_authorized: false },
      start: true,
    })
  })

  it('keeps the draft and Escape dismissal after a refused review', async () => {
    let reject!: (error: Error) => void
    vi.mocked(tradingApi.reviewDelegation).mockReturnValue(
      new Promise<never>((_, deny) => {
        reject = deny
      }),
    )
    const close = vi.fn()
    render(<WorkspaceForm draft={draft()} onClose={close} onSaved={vi.fn()} />)
    const submit = screen.getByRole('button', { name: 'Review workspace' })
    submit.focus()
    fireEvent.click(submit)
    expect(document.activeElement).not.toBe(submit)
    reject(new Error('Invalid mandate'))
    await screen.findByText('Invalid mandate')
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Monthly delegation')
    fireEvent.keyDown(document.activeElement!, { key: 'Escape', code: 'Escape' })
    await waitFor(() => expect(close).toHaveBeenCalled())
  })

  it('requires a human live authorization and rechecks the draft before enabling activation', async () => {
    const value = draft()
    value.environment.mode = 'live'
    value.environment.accounts[0] = {
      ...value.environment.accounts[0]!,
      connector_id: 'mcp:synthetic',
      broker_account_id: 'isolated_synthetic',
    }
    vi.mocked(tradingApi.reviewDelegation).mockImplementation(async (input) => ({
      ...input,
      ready: input.environment.live_authorized,
      issues: input.environment.live_authorized ? [] : ['Human authorization required'],
    }))
    vi.mocked(tradingApi.createDelegation).mockResolvedValue(saved())
    render(<WorkspaceForm draft={value} onClose={vi.fn()} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review workspace' }))
    const start = await screen.findByRole('button', { name: 'Create and start live trading' })
    expect((start as HTMLButtonElement).disabled).toBe(true)
    expect(tradingApi.createDelegation).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: /I authorize live trading/ }))
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
    expect(
      vi.mocked(tradingApi.reviewDelegation).mock.calls[1]?.[0].environment.live_authorized,
    ).toBe(true)
    fireEvent.click(start)
    await waitFor(() => expect(tradingApi.createDelegation).toHaveBeenCalled())
    expect(
      vi.mocked(tradingApi.createDelegation).mock.calls[0]?.[0].environment.live_authorized,
    ).toBe(true)
  })

  it('opens a saved workspace after activation refusal instead of offering duplicate creation', async () => {
    vi.mocked(tradingApi.reviewDelegation).mockResolvedValue(reviewed())
    vi.mocked(tradingApi.createDelegation).mockResolvedValue({
      ...saved(),
      started: false,
      activation_error: { code: 'conflict', message: 'Missing permission' },
    })
    const onSaved = vi.fn()
    render(<WorkspaceForm draft={draft()} onClose={vi.fn()} onSaved={onSaved} />)
    fireEvent.click(screen.getByRole('button', { name: 'Review workspace' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create and start workspace' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved().workspace, 'trun_saved'))
    expect(tradingApi.createDelegation).toHaveBeenCalledTimes(1)
  })

  it('attaches an environment to an existing workspace without copying its identity metadata', async () => {
    const existing = saved().workspace
    vi.mocked(tradingApi.reviewDelegation).mockImplementation(async (input) => reviewed(input))
    vi.mocked(tradingApi.activateWorkspace).mockResolvedValue(saved())
    render(<WorkspaceForm workspace={existing} onClose={vi.fn()} onSaved={vi.fn()} />)
    expect((screen.getByLabelText('Name') as HTMLInputElement).closest('fieldset')?.disabled).toBe(
      true,
    )
    fireEvent.change(screen.getByLabelText('Maximum order value'), { target: { value: '200' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review workspace' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create and start workspace' }))
    await waitFor(() =>
      expect(tradingApi.activateWorkspace).toHaveBeenCalledWith(
        existing.id,
        expect.anything(),
        existing.revision,
      ),
    )
    expect(tradingApi.createDelegation).not.toHaveBeenCalled()
    expect(vi.mocked(tradingApi.reviewDelegation).mock.calls[0]?.[0].workspace.mandate.max_order_notional).toBe(existing.mandate.max_order_notional)
    expect(vi.mocked(tradingApi.reviewDelegation).mock.calls[0]?.[0].workspace).not.toHaveProperty(
      'id',
    )
  })
})
