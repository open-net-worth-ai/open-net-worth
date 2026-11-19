// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, AutomationListResponse, AutomationRun } from '../../../api/automations'

const api = vi.hoisted(() => ({
  listAutomations: vi.fn(),
  updateAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  runAutomation: vi.fn(),
  createAutomation: vi.fn(),
  listAutomationRuns: vi.fn(),
  getAutomationRun: vi.fn(),
  getAutomationOptions: vi.fn(),
}))
vi.mock('../../../api/automations', async (load) => ({ ...(await load()), ...api }))

const navigate = vi.fn()
const route = vi.hoisted(() => ({
  search: {} as {
    automation?: string
    run?: string
    template?: string
    for?: string
    view?: string
    sync?: string
  },
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useLocation: () => ({ search: route.search }),
}))

import { _resetBusForTests, dispatchScreenCommand } from '../../../lucius/bus'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { Automations } from '../Automations'

const transactionAutomation: Automation = {
  id: 'automation_txn',
  name: 'Large charge alert',
  description: 'Flags unusually large charges.',
  instructions: 'When a Meridian charge over SGD 500 posts, tell me.',
  status: 'active',
  trigger: {
    kind: 'event',
    event: 'txn',
    when: { account: 'account_meridian', direction: 'out', over: 500 },
    limit: 'hour',
  },
  execution: { kind: 'lucius' },
  model: 'haiku',
  effort: 'low',
  run_count: 1,
  next_run_at: null,
  last_run_at: '2026-08-01T08:00:00Z',
  last_status: 'completed',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-08-01T08:00:00Z',
  provenance: { source: 'rule' },
  stats: { cost_this_month: 0.04 },
}

const scheduleAutomation: Automation = {
  ...transactionAutomation,
  id: 'automation_schedule',
  name: 'Morning money brief',
  description: '',
  instructions: 'Each weekday at 8am, give me a concise money brief.',
  trigger: { kind: 'schedule', type: 'weekdays', time: '08:00', tzid: 'Asia/Singapore' },
  run_count: 0,
  next_run_at: '2026-08-03T08:00:00+08:00',
  last_run_at: null,
  provenance: { source: 'scheduled_task' },
  stats: { cost_this_month: 0 },
}

const list = (automations: Automation[]): AutomationListResponse => ({
  automations,
  showing: automations.length,
  total: automations.length,
})

const runningRun: AutomationRun = {
  id: 'run_new',
  automation_id: scheduleAutomation.id,
  status: 'running',
  session_id: null,
  trigger: { source: 'manual' },
  execution: { kind: 'lucius' },
  model: 'haiku',
  effort: 'low',
  started_at: '2026-08-02T00:00:00Z',
  finished_at: null,
  summary: '',
  error: null,
  steps: [],
  pending_tool_ids: [],
  context: {},
  files: [],
  working_dir: null,
  input_tokens: null,
  output_tokens: null,
  cache_creation_tokens: null,
  cache_read_tokens: null,
  cost_usd: null,
  stats: {},
}

beforeEach(() => {
  api.listAutomations
    .mockReset()
    .mockResolvedValue(list([transactionAutomation, scheduleAutomation]))
  api.listAutomationRuns.mockReset().mockResolvedValue({ runs: [], showing: 0, total: 0 })
  api.updateAutomation.mockReset()
  api.deleteAutomation.mockReset()
  api.runAutomation.mockReset()
  api.createAutomation.mockReset()
  api.getAutomationRun.mockReset()
  api.getAutomationOptions.mockReset().mockResolvedValue({
    accounts: [],
    commitments: [],
    connectors: [],
    instruments: [],
    models: [],
  })
  navigate.mockReset()
  route.search = {}
  _resetBusForTests()
})
afterEach(() => {
  cleanup()
  _resetBusForTests()
  vi.useRealTimers()
})

describe('Automations', () => {
  it('collapses finished automations and clears them after a confirm', async () => {
    const finished: Automation = {
      ...scheduleAutomation,
      id: 'automation_done',
      name: 'Pay the tax bill',
      status: 'completed',
      trigger: { kind: 'schedule', type: 'once', at: '2026-08-01T09:00:00+08:00' },
      next_run_at: null,
      last_run_at: '2026-08-01T09:00:05+08:00',
    }
    api.listAutomations.mockResolvedValue(list([transactionAutomation, finished]))
    api.deleteAutomation.mockResolvedValue(undefined)
    render(<Automations onOpenChat={vi.fn()} onOpenConversation={vi.fn()} />)

    const toggle = await screen.findByRole('button', { name: 'Finished · 1' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('article', { name: 'Pay the tax bill' })).toBeNull()

    fireEvent.click(toggle)
    expect(screen.getByRole('article', { name: 'Pay the tax bill' })).toBeTruthy()
    expect(screen.getByText('Finished')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Clear finished' }))
    expect(api.deleteAutomation).not.toHaveBeenCalled()
    expect(await screen.findByText('Clear 1 finished automation?')).toBeTruthy()
    const confirm = screen
      .getAllByRole('button', { name: 'Clear finished' })
      .find((button) => button.closest('[role="dialog"]'))
    fireEvent.click(confirm!)

    await waitFor(() => expect(api.deleteAutomation).toHaveBeenCalledWith('automation_done'))
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Finished/ })).toBeNull())
    expect(screen.getByRole('article', { name: 'Large charge alert' })).toBeTruthy()
    expect(api.deleteAutomation).toHaveBeenCalledTimes(1)
  })

  it('opens the finished section while searching so a match is never hidden', async () => {
    api.listAutomations.mockResolvedValue(
      list([
        transactionAutomation,
        { ...scheduleAutomation, name: 'Pay the tax bill', status: 'completed', next_run_at: null },
      ]),
    )
    render(<Automations onOpenChat={vi.fn()} onOpenConversation={vi.fn()} />)
    await screen.findByRole('button', { name: 'Finished · 1' })

    fireEvent.change(screen.getByRole('searchbox', { name: /search automations/i }), {
      target: { value: 'tax' },
    })
    expect(screen.getByRole('article', { name: 'Pay the tax bill' })).toBeTruthy()
  })

  it('renders migrated origins as one searchable, ungrouped Automation list', async () => {
    render(<Automations onOpenChat={vi.fn()} onOpenConversation={vi.fn()} />)

    expect(await screen.findAllByRole('article')).toHaveLength(2)
    expect(screen.queryByText(/run by the app/i)).toBeNull()
    expect(screen.queryByRole('tab', { name: /^rules$/i })).toBeNull()
    expect(screen.queryByRole('tab', { name: /scheduling/i })).toBeNull()

    fireEvent.change(screen.getByRole('searchbox', { name: /search automations/i }), {
      target: { value: '500' },
    })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.getByRole('article', { name: 'Large charge alert' })).toBeTruthy()
  })

  it('distinguishes loading, load failure, true empty and filtered empty', async () => {
    let resolve!: (value: AutomationListResponse) => void
    api.listAutomations.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      }),
    )
    const first = render(<Automations />)
    expect(screen.getByText('Loading automations…')).toBeTruthy()
    resolve(list([]))
    expect(await screen.findByText('No automations yet')).toBeTruthy()
    expect(screen.getAllByText(/start from a template/i).length).toBeGreaterThan(0)
    first.unmount()

    api.listAutomations.mockRejectedValueOnce(new Error('offline'))
    render(<Automations />)
    expect(await screen.findByText('Automations couldn’t load')).toBeTruthy()
    cleanup()

    api.listAutomations.mockResolvedValueOnce(list([transactionAutomation]))
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'not here' } })
    expect(screen.getByText('Nothing matches “not here”')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /clear the search/i }))
    expect(screen.getByRole('article', { name: 'Large charge alert' })).toBeTruthy()
  })

  it('opens templates and duplicates as unsaved drafts', async () => {
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    fireEvent.click(screen.getByRole('button', { name: 'Subscription creep template' }))
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(api.createAutomation).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    const card = screen.getByRole('article', { name: /large charge alert/i })
    fireEvent.click(card.querySelector('button[aria-label="More actions"]')!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Duplicate' }))
    expect(screen.getByDisplayValue('Large charge alert (copy)')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.createAutomation).not.toHaveBeenCalled()
  })

  it('opens the global Lucius composer with the exact review-first prompt', async () => {
    const onOpenChat = vi.fn()
    render(<Automations onOpenChat={onOpenChat} />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    fireEvent.click(screen.getByRole('button', { name: 'New automation' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Create with Lucius' }))

    expect(onOpenChat).toHaveBeenCalledWith(
      'Draft a new automation for me. Ask what it should do and when it should run, then show me the completed draft before creating it.',
    )
    expect(api.createAutomation).not.toHaveBeenCalled()
  })

  it('opens manual setup as on-demand instead of silently scheduling it', async () => {
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    fireEvent.click(screen.getByRole('button', { name: 'New automation' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Set up manually' }))

    expect(screen.getByRole('dialog', { name: 'New automation' })).toBeTruthy()
    expect(screen.getByText('Only when you ask')).toBeTruthy()
  })

  it('rolls a failed pause back to active and surfaces the error', async () => {
    api.updateAutomation.mockRejectedValueOnce(new Error('write failed'))
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })
    const card = screen.getByRole('article', { name: /large charge alert/i })
    fireEvent.click(card.querySelector('button[aria-label="More actions"]')!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pause' }))
    expect((await screen.findByRole('alert')).textContent).toContain('write failed')
    expect(screen.queryByText('Paused')).toBeNull()
    await waitFor(() => expect(api.updateAutomation).toHaveBeenCalled())
  })

  it('opens a Lucius screen-command draft in the same unsaved review editor', async () => {
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    act(() => {
      dispatchScreenCommand({
        name: 'automations.review_draft',
        args: {
          automation: {
            name: 'Friday review',
            description: 'Review the week.',
            instructions: 'Every Friday, review my spending.',
            trigger: { kind: 'schedule', type: 'weekly', day: 'fri', time: '17:00' },
            approval: 'manual',
            model: '',
            effort: 'medium',
          },
        },
      })
    })

    expect(screen.getByRole('dialog', { name: 'Confirm Lucius’s draft' })).toBeTruthy()
    expect(screen.getByText(/Drafted from what you typed/)).toBeTruthy()
    expect(screen.getByDisplayValue('Friday review')).toBeTruthy()
    expect(api.createAutomation).not.toHaveBeenCalled()
  })

  it('opens a Lucius once draft when the command omits the schedule kind', async () => {
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    act(() => {
      dispatchScreenCommand({
        name: 'automations.review_draft',
        args: {
          automation: {
            name: 'Pay Beacon Rewards Card bill',
            instructions:
              'Remind me to pay the Beacon Rewards Card bill — S$618.45 due 2026-08-22, minimum payment S$45.',
            description: 'One-time reminder to pay the Beacon Rewards Card bill.',
            trigger: { type: 'once', date: '2026-08-10', time: '09:00:00' },
            approval: 'notify_only',
            model: 'sonnet',
          },
        },
      })
    })

    expect(screen.getByRole('dialog', { name: 'Confirm Lucius’s draft' })).toBeTruthy()
    expect(screen.getByDisplayValue('Pay Beacon Rewards Card bill')).toBeTruthy()
    expect(api.createAutomation).not.toHaveBeenCalled()
  })

  it('opens a Lucius on-demand handoff that uses the compact manual trigger shape', async () => {
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    act(() => {
      dispatchScreenCommand({
        name: 'automations.review_draft',
        args: {
          automation: {
            name: 'QA Lucius Review',
            description: 'One-sentence cash summary.',
            instructions: 'Give me a one-sentence cash summary only when I ask.',
            trigger: { type: 'manual' },
            approval: 'none',
            model: '',
            effort: 'medium',
          },
        },
      })
    })

    expect(screen.getByRole('dialog', { name: 'Confirm Lucius’s draft' })).toBeTruthy()
    expect(screen.getByText('Only when you ask')).toBeTruthy()
    // Pins the DEFAULT permission mode, not this draft's: the seed's 'none' is not a valid
    // policy and is dropped upstream, so the editor falls through to its default — changed
    // from 'manual' to 'auto' on 2026-08-09 at the owner's request.
    expect(screen.getByRole('button', { name: 'Permission mode: Manually approve' })).toBeTruthy()
    expect(api.createAutomation).not.toHaveBeenCalled()
  })

  it('polls a live Run now result until its durable conversation exists, then stops', async () => {
    vi.useFakeTimers()
    route.search = { automation: scheduleAutomation.id }
    const durable = {
      ...runningRun,
      status: 'completed' as const,
      session_id: 'session_durable',
      finished_at: '2026-08-02T00:00:02Z',
    }
    api.listAutomationRuns
      .mockResolvedValueOnce({ runs: [], showing: 0, total: 0 })
      .mockResolvedValue({ runs: [durable], showing: 1, total: 1 })
    const refreshedSchedule = {
      ...scheduleAutomation,
      description: 'Updated after the terminal run.',
      model: 'openai:gpt-current',
      effort: 'high',
    }
    api.listAutomations
      .mockResolvedValueOnce(list([transactionAutomation, scheduleAutomation]))
      .mockResolvedValueOnce(list([transactionAutomation, refreshedSchedule]))
    api.runAutomation.mockResolvedValue(runningRun)
    const onOpenConversation = vi.fn()

    render(<Automations onOpenConversation={onOpenConversation} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await act(async () => {})
    expect(onOpenConversation).not.toHaveBeenCalled()

    await act(async () => {
      vi.advanceTimersByTime(1_000)
      await Promise.resolve()
    })
    expect(onOpenConversation).toHaveBeenCalledOnce()
    expect(onOpenConversation).toHaveBeenCalledWith('session_durable', {
      model: refreshedSchedule.model,
      effort: refreshedSchedule.effort,
    })
    expect(api.listAutomations).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Updated after the terminal run.')).toBeTruthy()
    const callsAtTerminal = api.listAutomationRuns.mock.calls.length

    await act(async () => {
      vi.advanceTimersByTime(2_000)
    })
    expect(api.listAutomationRuns).toHaveBeenCalledTimes(callsAtTerminal)
  })

  it('fetches a deep-linked run outside the first history page by id', async () => {
    route.search = { automation: scheduleAutomation.id, run: 'run_older' }
    const firstPage: AutomationRun[] = Array.from({ length: 50 }, (_, index) => ({
      ...runningRun,
      id: `run_${index}`,
      status: 'completed',
      session_id: `session_${index}`,
      finished_at: '2026-08-02T00:00:02Z',
      summary: `First-page run ${index}`,
    }))
    const deepLinkedRun: AutomationRun = {
      ...runningRun,
      id: 'run_older',
      status: 'completed',
      session_id: 'session_older',
      started_at: '2026-05-01T00:00:00Z',
      finished_at: '2026-05-01T00:00:02Z',
      summary: 'Deep-linked older run',
    }
    api.listAutomationRuns.mockResolvedValueOnce({ runs: firstPage, showing: 50, total: 51 })
    api.getAutomationRun.mockResolvedValueOnce(deepLinkedRun)

    render(<Automations />)

    // The row shows time/status/cost only — no summary — so the adopted run is evidenced by
    // the fetch, the row count, and the card opening on it, all asserted below.
    expect(await screen.findByRole('button', { name: 'Close this run' })).toBeTruthy()
    expect(api.getAutomationRun).toHaveBeenCalledWith(scheduleAutomation.id, 'run_older')
    expect(screen.getAllByRole('listitem')).toHaveLength(51)
    expect(screen.getByRole('button', { name: 'Close this run' })).toBeTruthy()
    expect(screen.queryByText('Run not found')).toBeNull()
  })

  // CDS-UI-11: a direct visit to /automations?template=sync_plaid opens the editor prefilled
  // with no click required, and clears the param afterward.
  it('opens the editor prefilled from a ?template= deep link, then clears the param', async () => {
    route.search = { template: 'sync_plaid' }
    render(<Automations />)
    // hidden: the deep link opens a modal, which aria-hides the gallery behind it.
    await screen.findByRole('article', { name: 'Large charge alert', hidden: true })

    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByDisplayValue('Sync Plaid')).toBeTruthy()

    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/automations', replace: true }),
    )
    const call = navigate.mock.calls.find((c) => c[0]?.to === '/automations')!
    expect(call[0].search({ template: 'sync_plaid', automation: 'x' })).toEqual({
      template: undefined,
      automation: 'x',
    })
  })

  // Auto-copy on a followed portfolio: the template opens already naming it, so the
  // owner is not asked again what they just clicked.
  it("fills a template's [name] from ?for=, and clears both params", async () => {
    route.search = { template: 'copy-followed-portfolio', for: 'Acme Capital' }
    render(<Automations />)
    await screen.findByRole('dialog', { hidden: true })
    expect(screen.getByDisplayValue('Copy Acme Capital')).toBeTruthy()
    expect(
      screen.getByDisplayValue(/^Copy the followed portfolio Acme Capital in my broker account/),
    ).toBeTruthy()
    const call = navigate.mock.calls.find((c) => c[0]?.to === '/automations')!
    expect(call[0].search({ template: 'x', for: 'y' })).toEqual({})
  })

  // CDS-UI-12: an unknown template key is a silent no-op — gallery renders, editor stays
  // closed, no error/console warning.
  it('silently ignores an unknown ?template= key', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    route.search = { template: 'unknown_key' }
    render(<Automations />)
    await screen.findByRole('article', { name: 'Large charge alert' })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(warn).not.toHaveBeenCalled()
    expect(error).not.toHaveBeenCalled()
    warn.mockRestore()
    error.mockRestore()
  })

  it("opens a detail at the top of a phone's .app-layout scroller and Back returns to the list's offset", async () => {
    // jsdom has no Element.scrollTo; on a phone it moves .app-layout, not the window.
    Element.prototype.scrollTo = function (this: Element, opts?: ScrollToOptions | number) {
      this.scrollTop = typeof opts === 'object' ? (opts.top ?? 0) : 0
    }
    const windowScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    // A fresh element each time — re-rendering the identical element lets React skip the tab.
    const shell = () => (
      <div className="app-layout">
        <Automations />
      </div>
    )
    try {
      const { container, rerender } = render(shell())
      const layout = container.querySelector('.app-layout') as HTMLElement
      const card = await screen.findByRole('article', { name: 'Morning money brief' })
      layout.scrollTop = 640
      fireEvent.click(card)

      route.search = { automation: scheduleAutomation.id }
      rerender(shell())
      expect(layout.scrollTop).toBe(0)

      route.search = {}
      rerender(shell())
      expect(layout.scrollTop).toBe(640)
    } finally {
      delete (Element.prototype as { scrollTo?: unknown }).scrollTo
      windowScroll.mockRestore()
    }
  })
})

// The Data Sync view is behind the ynab_import_and_sync flag: off, the screen is main's.
describe('Automations — the ynab_import_and_sync flag', () => {
  let restoreFlags: () => void = () => {}
  afterEach(() => restoreFlags())

  it('off: no Data Sync tab, and a sync deep link still opens the Lucius list', async () => {
    restoreFlags = overrideFlagsForTests({ ynab_import_and_sync: false })
    route.search = { view: 'sync', sync: 'ynab' }
    render(<Automations onOpenChat={vi.fn()} onOpenConversation={vi.fn()} />)

    expect(await screen.findAllByRole('article')).toHaveLength(2)
    expect(screen.queryByRole('tab', { name: /data sync/i })).toBeNull()
    expect(screen.getByText(/^Run on a schedule or when something happens\./)).toBeTruthy()
  })

  it('on: Lucius and Data Sync are two tabs', async () => {
    restoreFlags = overrideFlagsForTests({ ynab_import_and_sync: true })
    render(<Automations onOpenChat={vi.fn()} onOpenConversation={vi.fn()} />)

    expect(await screen.findByRole('tab', { name: /data sync/i })).toBeTruthy()
    expect(screen.getByRole('tab', { name: /lucius/i })).toBeTruthy()
  })
})
