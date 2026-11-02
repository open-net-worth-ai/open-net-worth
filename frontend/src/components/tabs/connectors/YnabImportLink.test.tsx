// @vitest-environment jsdom
//
// The YNAB card's status line: when it last imported, the sync schedule and what waits for review.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { YnabImportLink } from './YnabImportLink'
import { clearQueryCache } from '../../../state/queryCache'
import { importRun, syncStatus } from '../imports/importsFixtures'
import { overrideFlagsForTests } from '../../../hooks/useFlag'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

const ok = (body: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
let sync: ReturnType<typeof syncStatus>
let restoreFlags: () => void

beforeEach(() => {
  restoreFlags = overrideFlagsForTests({ ynab_import_and_sync: true })
  sync = syncStatus()
  authFetch.mockImplementation((url: string) =>
    url.startsWith('/api/imports/runs') ? ok({ runs: [importRun()] }) : ok(sync),
  )
})
afterEach(() => {
  restoreFlags()
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('YnabImportLink', () => {
  it('reads import date, schedule and review count on one line', async () => {
    render(<YnabImportLink />)
    expect(await screen.findByText(/Imported .* · Sync every 1h · 1 to review/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Two-way sync/ })).toBeTruthy()
  })

  it('leaves the sync parts out when it is off and nothing waits', async () => {
    sync = syncStatus({ enabled: false, conflicts: [] })
    render(<YnabImportLink />)
    const line = await screen.findByText(/Imported /)
    expect(line.textContent).not.toMatch(/Sync|review/)
  })

  it('is not there, and reads nothing, while the ynab_import_and_sync flag is off', () => {
    restoreFlags()
    restoreFlags = overrideFlagsForTests({ ynab_import_and_sync: false })
    const { container } = render(<YnabImportLink />)
    expect(container.textContent).toBe('')
    expect(authFetch).not.toHaveBeenCalled()
  })
})
