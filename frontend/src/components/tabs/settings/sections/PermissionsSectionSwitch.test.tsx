// @vitest-environment jsdom
//
// Settings → Permissions' switch point for permissions_v2: the list of stored decisions (an
// Owner column, no search) with the flag off, the built-in tool catalogue (a search box, one
// value per tool) with it on.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache } from '../../../../state/queryCache'
import { PermissionsSectionSwitch } from './PermissionsSectionSwitch'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'

describe.each([false, true])('Settings → Permissions (permissions_v2=%s)', (on) => {
  let restore: () => void
  beforeEach(() => {
    clearQueryCache()
    restore = overrideFlagsForTests({ permissions_v2: on })
    vi.mocked(authFetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        permissions: [
          {
            tool: 'add_transactions',
            connector_id: 'core',
            level: 'always_allow',
            default_level: 'needs_approval',
            description: 'Record new transactions.',
            updated_at: '2026-09-21T10:00:00Z',
            tool_exists: true,
          },
        ],
        total: 1,
        yolo: false,
      }),
    } as Response)
  })
  afterEach(() => {
    restore()
    cleanup()
  })

  it(`renders the ${on ? 'one-value-per-tool' : 'stored-decisions'} section`, async () => {
    render(<PermissionsSectionSwitch toast={vi.fn()} />)
    expect(await screen.findByText(on ? 'Record new transactions.' : 'Owner')).toBeTruthy()
    expect(screen.queryByLabelText('Search tools') !== null).toBe(on)
    expect(screen.queryByText('Owner') !== null).toBe(!on)
  })
})
