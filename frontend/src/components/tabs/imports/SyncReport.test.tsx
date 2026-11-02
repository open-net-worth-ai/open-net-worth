// @vitest-environment jsdom
//
// A sync run opened as a report: Sent / Pulled with field-by-field changes, Assigned, conflicts
// (Keep buttons only while still open), deferred, errors, "Nothing changed", paging by 50.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { RunReport } from './RunReport'
import { clearQueryCache } from '../../../state/queryCache'
import { syncRun, syncStatus } from './importsFixtures'
import type { RunRecord, TxnChange } from './runs'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

let current: RunRecord
let live: ReturnType<typeof syncStatus>
let posts: string[]

beforeEach(() => {
  posts = []
  live = syncStatus()
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') posts.push(url)
    const body = url === '/api/ynab/sync' ? live : current
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

const open = () => render(<RunReport runId={current.id} onBack={() => {}} />)

describe('Sync run report', () => {
  it('shows the counts and the sent transactions with each field before and after', async () => {
    current = syncRun()
    open()
    expect(await screen.findByRole('tab', { name: 'Sent (2)' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Pulled (1)' })).toBeTruthy()
    expect(screen.getByText('YNAB requests')).toBeTruthy()
    expect(screen.getByText('Amount -60.00 → -62.50')).toBeTruthy()
    expect(screen.getByText('Cleared no → yes')).toBeTruthy()
    expect(screen.getByText('Created')).toBeTruthy()
    expect(screen.queryByText('Grocer')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Pulled (1)' }))
    expect(screen.getByText('Grocer')).toBeTruthy()
    expect(screen.getByText('Deleted')).toBeTruthy()
  })

  it('lists Assigned moves, deferred items and errors', async () => {
    current = syncRun()
    open()
    const row = (await screen.findByText('Rent')).closest('tr') as HTMLElement
    expect(within(row).getByText('1,200.00 → 1,300.00')).toBeTruthy()
    expect(within(row).getByText('Sent to YNAB')).toBeTruthy()
    expect(screen.getByText('Lunch')).toBeTruthy()
    expect(screen.getByText(/YNAB did not accept it: bad date/)).toBeTruthy()
  })

  it('offers Keep buttons on a conflict that is still open, and settles it', async () => {
    current = syncRun()
    open()
    fireEvent.click(await screen.findByText("Keep YNAB's"))
    await waitFor(() => expect(posts).toEqual(['/api/ynab/sync/conflicts/c_1/resolve']))
  })

  it('shows a conflict that is no longer open as resolved, without buttons', async () => {
    current = syncRun()
    live = syncStatus({ conflicts: [] })
    open()
    expect(await screen.findByText('Resolved')).toBeTruthy()
    expect(screen.queryByText("Keep YNAB's")).toBeNull()
  })

  it('says Nothing changed for a run with nothing in it and hides the empty sections', async () => {
    current = syncRun({
      outcome: 'Nothing changed',
      totals: { sent: 0, pulled: 0, assigned: 0, conflicts: 0, deferred: 0, errors: 0 },
      txn_changes: [],
      assigned_changes: [],
      conflicts: [],
      deferred: [],
      errors: [],
    })
    open()
    expect(await screen.findByText('Nothing changed.')).toBeTruthy()
    expect(screen.queryByText('Transactions')).toBeNull()
    expect(screen.queryByText('Assigned')).toBeNull()
    expect(screen.queryByText('Deferred')).toBeNull()
  })

  it('pages a long list by 50 and says how many were not stored', async () => {
    const many: TxnChange[] = Array.from({ length: 120 }, (_, i) => ({
      direction: 'pulled',
      txn_id: `t${i}`,
      date: '2026-10-02',
      payee: `Payee ${i}`,
      account: 'Checking',
      amount: -1,
      currency: 'USD',
      action: 'created',
      fields: [],
    }))
    current = syncRun({
      txn_changes: many,
      totals: { sent: 0, pulled: 130, assigned: 0, conflicts: 0, deferred: 0, errors: 0 },
      not_stored: { txn_changes: 10 },
      assigned_changes: [],
      conflicts: [],
      deferred: [],
      errors: [],
    })
    open()
    expect(await screen.findByText('Payee 49')).toBeTruthy()
    expect(screen.queryByText('Payee 50')).toBeNull()
    fireEvent.click(screen.getByText('Show 50 more'))
    expect(screen.getByText('Payee 99')).toBeTruthy()
    fireEvent.click(screen.getByText('Show 20 more'))
    expect(screen.getByText('Payee 119')).toBeTruthy()
    expect(screen.queryByText(/Show \d+ more/)).toBeNull()
    expect(screen.getByText('+10 more not stored')).toBeTruthy()
  })

  it('a failed sync shows why above what it captured', async () => {
    current = syncRun({ status: 'failed', error: 'YNAB HTTP 401' })
    open()
    expect(await screen.findByText('YNAB HTTP 401')).toBeTruthy()
    expect(screen.getByText('This run failed')).toBeTruthy()
  })
})
