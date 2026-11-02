// @vitest-environment jsdom
//
// Settings → Developer under permissions_v2: the run_code "Always allow" switch and the
// "Unattended script runs" switch exist only while the flag is off — a tool's own value in
// Settings → Permissions replaced both. The feature switches themselves stay either way.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache } from '../../../../state/queryCache'
import { DeveloperSectionSwitch } from './DeveloperSectionSwitch'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'

describe.each([false, true])('Settings → Developer (permissions_v2=%s)', (on) => {
  let restore: () => void
  beforeEach(() => {
    clearQueryCache()
    restore = overrideFlagsForTests({ permissions_v2: on })
    vi.mocked(authFetch).mockResolvedValue({
      ok: true,
      json: async () => ({ enabled: true, auto_run: false }),
    } as Response)
  })
  afterEach(() => {
    restore()
    cleanup()
  })

  it('shows the two permission switches only while the flag is off', async () => {
    render(<DeveloperSectionSwitch toast={vi.fn()} />)
    // Code execution is loaded as enabled: only then does the old section offer its Always allow.
    await screen.findByText(
      on
        ? /Run code asks you first by default/
        : /You'll be asked for permission the first time Lucius runs code/,
    )
    expect(screen.queryByText('Always allow (skip the per-run prompt)') !== null).toBe(!on)
    expect(screen.queryByText('Unattended script runs') !== null).toBe(!on)
  })

  it('keeps the feature switches either way', async () => {
    render(<DeveloperSectionSwitch toast={vi.fn()} />)
    expect((await screen.findAllByText('Bundled script skills')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Code execution').length).toBeGreaterThan(0)
  })
})
