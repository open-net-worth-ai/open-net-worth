// @vitest-environment jsdom
//
// The YNAB card's status line: when it last imported, the sync schedule and what waits for review.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { YnabImportLink } from './YnabImportLink'
import { clearQueryCache } from '../../../state/queryCache'
import { importRun, syncStatus } from '../imports/importsFixtures'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

const ok = (body: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
let sync: ReturnType<typeof syncStatus>

beforeEach(() => {
  sync = syncStatus()
  authFetch.mockImplementation((url: string) =>
    url.startsWith('/api/imports/runs') ? ok({ runs: [importRun()] }) : ok(sync),
  )
})
afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('YnabImportLink', () => {
  it('reads import date, schedule and review count on one line', async () => {
    render(<YnabImportLink />)
    expect(await screen.findByText(/Imported .* · Sync every 1h · 1 to review/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Import & sync/ })).toBeTruthy()
  })

  it('leaves the sync parts out when it is off and nothing waits', async () => {
    sync = syncStatus({ enabled: false, conflicts: [] })
    render(<YnabImportLink />)
    const line = await screen.findByText(/Imported /)
    expect(line.textContent).not.toMatch(/Sync|review/)
  })
})
