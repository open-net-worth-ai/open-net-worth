// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Automation, AutomationRun } from '../../../api/automations'
import { clearQueryCache } from '../../../state/queryCache'
import { AutomationDetailV2 } from './AutomationDetailV2'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../api/client'

const tools = [
  {
    tool: 'update_transactions',
    connector_id: 'core',
    level: 'needs_approval',
    default_level: 'needs_approval',
    description: 'Edit existing transactions.',
    updated_at: '',
    tool_exists: true,
  },
  {
    tool: 'email_send_message',
    connector_id: 'email',
    level: 'needs_approval',
    default_level: 'needs_approval',
    description: 'Send an email.',
    updated_at: '',
    tool_exists: true,
  },
  {
    tool: 'get_net_worth',
    connector_id: 'core',
    level: 'always_allow',
    default_level: 'always_allow',
    description: 'Read net worth.',
    updated_at: '',
    tool_exists: true,
  },
  {
    tool: 'browser_click',
    connector_id: 'mcp:playwright',
    level: 'needs_approval',
    default_level: 'needs_approval',
    description: '',
    updated_at: '',
    tool_exists: true,
  },
]

beforeEach(() => {
  clearQueryCache()
  vi.mocked(authFetch).mockResolvedValue({
    ok: true,
    json: async () => ({ permissions: tools, total: tools.length }),
  } as Response)
})

const automation: Automation = {
  id: 'automation_a',
  name: 'Morning brief',
  description: 'What changed overnight.',
  instructions: 'Every weekday at 8am, give me a brief.',
  status: 'active',
  trigger: { kind: 'schedule', type: 'weekdays', time: '08:00', tzid: 'Asia/Singapore' },
  execution: { kind: 'lucius' },
  model: 'haiku',
  effort: 'low',
  run_count: 4,
  next_run_at: '2026-08-03T08:00:00+08:00',
  last_run_at: '2026-08-02T00:00:00Z',
  last_status: 'waiting',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  provenance: {},
  stats: { cost_this_month: 0.11 },
}

function run(
  status: AutomationRun['status'],
  id: string = status,
  sessionId: string | null = `session-${status}`,
): AutomationRun {
  return {
    id,
    automation_id: automation.id,
    status,
    session_id: sessionId,
    trigger: { kind: 'manual' },
    execution: { kind: 'lucius' },
    model: 'haiku',
    effort: 'low',
    started_at: '2026-08-02T08:00:00Z',
    finished_at: status === 'running' ? null : '2026-08-02T08:00:07Z',
    summary: `${status} summary`,
    error: status === 'failed' ? 'Connector unavailable' : null,
    steps: [{ label: 'Read accounts', done: status !== 'running' }],
    pending_tool_ids: status === 'waiting' ? ['tool_1'] : [],
    context: {},
    files: [],
    working_dir: null,
    input_tokens: status === 'running' ? null : 1200,
    output_tokens: status === 'running' ? null : 80,
    cache_creation_tokens: null,
    cache_read_tokens: null,
    cost_usd: status === 'running' ? null : 0.01,
    stats: {},
  }
}

afterEach(() => cleanup())

describe('AutomationDetailV2', () => {
  it('renders all durable run states and hands history selection to the global conversation owner', () => {
    const onSelectRun = vi.fn()
    const runs = [run('completed'), run('running'), run('waiting'), run('failed')]
    render(
      <AutomationDetailV2
        automation={automation}
        runs={runs}
        selectedRunId="waiting"
        onSelectRun={onSelectRun}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )

    expect(document.querySelectorAll('.rd-dot.good')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.run')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.warn')).toHaveLength(1)
    expect(document.querySelectorAll('.rd-dot.bad')).toHaveLength(1)
    fireEvent.click(screen.getAllByRole('listitem')[0]!)
    expect(onSelectRun).toHaveBeenCalledWith(runs[0])
    expect(screen.queryByRole('button', { name: /^(approve|skip)$/i })).toBeNull()
  })

  it('renders an otherwise sparse durable conversation without inventing context or cost', () => {
    const sparse = {
      ...run('completed', 'sparse'),
      context: {},
      cost_usd: null,
      input_tokens: null,
      output_tokens: null,
    }
    render(
      <AutomationDetailV2
        automation={automation}
        runs={[sparse]}
        selectedRunId="sparse"
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )
    expect(screen.getByText('No context recorded.')).toBeTruthy()
    expect(screen.getAllByText('No cost data yet.').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /conversation/i })).toBeNull()
  })

  it('shows the approved empty-history guidance', () => {
    render(
      <AutomationDetailV2
        automation={automation}
        runs={[]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )
    expect(screen.getByText(/Hasn’t run yet\. It first runs/)).toBeTruthy()
  })

  it('opens long instructions in the wide detail modal', () => {
    render(
      <AutomationDetailV2
        automation={{ ...automation, instructions: 'Review every relevant detail. '.repeat(16) }}
        runs={[]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(document.querySelector('.ui-modal-card--xl')).toBeTruthy()
  })

  // Until 2026-08-06 a run could not be stopped from anywhere in the product: chat's Stop
  // 409'd an Automation-owned session on the reasoning that this tab owned the control, and
  // this tab had no such control. A scheduled run that looped could not be interrupted.
  it('offers Stop in place of Run now while a run is in flight, and names the run it stops', () => {
    const onStopRun = vi.fn()
    const onRunNow = vi.fn()
    const running = run('running')
    render(
      <AutomationDetailV2
        automation={automation}
        runs={[run('completed'), running]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={onRunNow}
        onStopRun={onStopRun}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: /run now/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /stop/i }))
    expect(onStopRun).toHaveBeenCalledWith(running)
    expect(onRunNow).not.toHaveBeenCalled()
  })

  it('offers Run now, not Stop, when nothing is in flight', () => {
    render(
      <AutomationDetailV2
        automation={automation}
        runs={[run('completed'), run('waiting')]}
        selectedRunId={null}
        onSelectRun={vi.fn()}
        onCloseRun={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRunNow={vi.fn()}
        onStopRun={vi.fn()}
        onStatusChange={vi.fn()}
        onSaveToolLists={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^stop$/i })).toBeNull()
  })

  describe('Tool permissions', () => {
    const withLists: Automation = {
      ...automation,
      permission_mode: 'auto',
      always_allowed: [
        {
          src: 'Open Net Worth',
          desc: 'Allow update transactions',
          tools: ['update_transactions'],
        },
      ],
      blocked: ['email_send_message'],
    }

    function mount(onSaveToolLists = vi.fn(), value: Automation = withLists) {
      render(
        <AutomationDetailV2
          automation={value}
          runs={[]}
          selectedRunId={null}
          onSelectRun={vi.fn()}
          onCloseRun={vi.fn()}
          onEdit={vi.fn()}
          onDelete={vi.fn()}
          onRunNow={vi.fn()}
          onStopRun={vi.fn()}
          onStatusChange={vi.fn()}
          onSaveToolLists={onSaveToolLists}
        />,
      )
      return onSaveToolLists
    }

    it('lists what this Automation always allows and blocks, and says what the rest follows', () => {
      mount()
      const allowed = screen.getByLabelText('Permission for Update transactions in this Automation')
      expect((allowed as HTMLSelectElement).value).toBe('always_allow')
      const blocked = screen.getByLabelText('Permission for Email send message in this Automation')
      expect((blocked as HTMLSelectElement).value).toBe('blocked')
      expect(screen.getByText('Everything else: global setting · Auto mode')).toBeTruthy()
    })

    it('moves a tool between the lists when its value changes', () => {
      const onSave = mount()
      fireEvent.change(
        screen.getByLabelText('Permission for Update transactions in this Automation'),
        {
          target: { value: 'blocked' },
        },
      )
      expect(onSave).toHaveBeenCalledWith({
        always_allowed: [],
        blocked: ['email_send_message', 'update_transactions'],
      })
    })

    it('lists an external MCP tool by its namespaced name', async () => {
      const onSave = mount(vi.fn(), { ...withLists, always_allowed: [], blocked: [] })
      fireEvent.click(screen.getByRole('button', { name: /add tool/i }))
      const picker = await screen.findByRole('dialog', { name: 'Add a tool' })
      fireEvent.click(
        await within(picker).findByRole('button', {
          name: 'Block Browser click · MCP playwright in this Automation',
        }),
      )
      expect(onSave).toHaveBeenCalledWith({
        always_allowed: [],
        blocked: ['mcp__playwright__browser_click'],
      })
    })

    it('removes a tool from this Automation without touching the other list', () => {
      const onSave = mount()
      fireEvent.click(screen.getByRole('button', { name: 'Remove Email send message' }))
      expect(onSave).toHaveBeenCalledWith({
        always_allowed: withLists.always_allowed,
        blocked: [],
      })
    })

    it('adds a tool from the searchable picker as Always allow or Block', async () => {
      const onSave = mount(vi.fn(), { ...withLists, always_allowed: [], blocked: [] })
      fireEvent.click(screen.getByRole('button', { name: /add tool/i }))
      const picker = await screen.findByRole('dialog', { name: 'Add a tool' })
      await within(picker).findByText('Get net worth')
      // An external MCP tool is offered too, under the name its calls carry.
      expect(within(picker).getByText('Browser click · MCP playwright')).toBeTruthy()

      fireEvent.change(within(picker).getByLabelText('Search tools'), {
        target: { value: 'email' },
      })
      expect(within(picker).queryByText('Get net worth')).toBeNull()
      fireEvent.click(
        within(picker).getByRole('button', { name: 'Block Email send message in this Automation' }),
      )
      expect(onSave).toHaveBeenCalledWith({
        always_allowed: [],
        blocked: ['email_send_message'],
      })

      fireEvent.click(screen.getByRole('button', { name: /add tool/i }))
      const again = await screen.findByRole('dialog', { name: 'Add a tool' })
      fireEvent.click(
        await within(again).findByRole('button', {
          name: 'Always allow Update transactions in this Automation',
        }),
      )
      expect(onSave).toHaveBeenLastCalledWith({
        always_allowed: [
          {
            src: 'Open Net Worth',
            desc: 'Allow update transactions',
            tools: ['update_transactions'],
          },
        ],
        blocked: [],
      })
    })

    it('keeps an entry that names no tool, and removes it by position', () => {
      const onSave = mount(vi.fn(), {
        ...automation,
        always_allowed: [{ src: 'Telegram', desc: 'Send the daily digest' }],
      })
      fireEvent.click(screen.getByRole('button', { name: 'Remove Send the daily digest' }))
      expect(onSave).toHaveBeenCalledWith({ always_allowed: [], blocked: [] })
    })
  })
})
