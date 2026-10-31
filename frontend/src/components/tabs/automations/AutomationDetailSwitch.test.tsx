// @vitest-environment jsdom
//
// The Automation page's switch point for permissions_v2: main's "Always allowed" list with
// Revoke with the flag off, the Tool permissions section (Always allowed and Blocked) with it on.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { Automation } from '../../../api/automations'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { clearQueryCache } from '../../../state/queryCache'
import { AutomationDetailSwitch } from './AutomationDetailSwitch'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../api/client'

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
  run_count: 0,
  next_run_at: '2026-08-03T08:00:00+08:00',
  last_run_at: null,
  last_status: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  provenance: {},
  stats: { cost_this_month: 0 },
  always_allowed: [
    { src: 'Open Net Worth', desc: 'Allow update transactions', tools: ['update_transactions'] },
  ],
}

function mount() {
  const handlers = { onRevoke: vi.fn(), onSaveToolLists: vi.fn() }
  render(
    <AutomationDetailSwitch
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
      {...handlers}
    />,
  )
  return handlers
}

describe.each([false, true])('the Automation page (permissions_v2=%s)', (on) => {
  let restore: () => void
  beforeEach(() => {
    clearQueryCache()
    restore = overrideFlagsForTests({ permissions_v2: on })
    vi.mocked(authFetch).mockResolvedValue({
      ok: true,
      json: async () => ({ permissions: [], total: 0 }),
    } as Response)
  })
  afterEach(() => {
    restore()
    cleanup()
  })

  it(`renders ${on ? 'Tool permissions' : 'the Always allowed list'}`, () => {
    mount()
    expect(screen.queryByText('Tool permissions') !== null).toBe(on)
    expect(screen.queryByText(/^Always allowed/) !== null).toBe(!on)
  })

  it(`${on ? 'removes a tool through the lists handler' : 'revokes an entry through the old handler'}`, () => {
    const { onRevoke, onSaveToolLists } = mount()
    fireEvent.click(
      screen.getByRole('button', {
        name: on ? 'Remove Update transactions' : 'Revoke Open Net Worth: update_transactions',
      }),
    )
    expect(onRevoke).toHaveBeenCalledTimes(on ? 0 : 1)
    expect(onSaveToolLists).toHaveBeenCalledTimes(on ? 1 : 0)
  })
})
