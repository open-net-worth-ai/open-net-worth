// @vitest-environment jsdom
//
// The two-way YNAB sync card: schedule + enable, Sync now with polling, last/next run, and the
// review list with both versions and the keep-this-side choice. The server is a scripted fake.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { YnabSync } from './YnabSync'
import { clearQueryCache } from '../../../state/queryCache'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const ITEM = {
  id: 'c_1',
  kind: 'conflict',
  entity: 'txn',
  title: 'Bistro',
  reason: 'Changed in both Open Net Worth and YNAB since the last sync.',
  onw: { date: '2026-10-01', account: 'Visa', payee: 'Bistro', memo: 'mine', amount: -60 },
  ynab: { date: '2026-10-01', account: 'Visa', payee: 'Bistro', memo: 'theirs', amount: -60 },
}

function base(over: Record<string, unknown> = {}) {
  return {
    token_saved: true,
    enabled: true,
    interval: '1h',
    intervals: ['15m', '30m', '1h', '6h', 'daily', 'manual'],
    plan_id: 'p1',
    plan_name: 'Household',
    running: false,
    last_run: {
      trigger: 'schedule',
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      ok: true,
      error: null,
      requests: 2,
      summary: '3 sent to YNAB, 1 pulled, 1 needs review',
    },
    next_run_at: new Date(Date.now() + 3600_000).toISOString(),
    conflicts: [ITEM],
    ...over,
  }
}

let current: ReturnType<typeof base>
let requests: Array<{ url: string; method: string; body: unknown }>

function ok(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

beforeEach(() => {
  requests = []
  current = base()
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (method !== 'GET')
      requests.push({ url, method, body: init?.body && JSON.parse(String(init.body)) })
    if (url === '/api/ynab/budgets')
      return ok({ budgets: [{ id: 'p1', name: 'Household', currency: 'USD' }] })
    if (url.endsWith('/resolve')) current = base({ conflicts: [] })
    if (url.endsWith('/run')) current = base({ running: true })
    if (url.endsWith('/settings')) current = base({ interval: '6h' })
    return ok(current)
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('YnabSync', () => {
  it('shows the last run, the next run and both versions of a conflict', async () => {
    render(<YnabSync />)
    expect(await screen.findByText(/3 sent to YNAB, 1 pulled, 1 needs review/)).toBeTruthy()
    expect(screen.getByText(/Next run:/)).toBeTruthy()
    expect(screen.getByText('mine')).toBeTruthy()
    expect(screen.getByText('theirs')).toBeTruthy()
  })

  it('applies the chosen side to both and clears the item', async () => {
    render(<YnabSync />)
    fireEvent.click(await screen.findByText("Keep YNAB's"))
    await waitFor(() => expect(screen.queryByText('Needs your review (1)')).toBeNull())
    expect(requests).toEqual([
      { url: '/api/ynab/sync/conflicts/c_1/resolve', method: 'POST', body: { keep: 'ynab' } },
    ])
  })

  it('offers only YNAB’s side for something YNAB cannot take, unless YNAB has nothing', async () => {
    current = base({
      conflicts: [{ ...ITEM, kind: 'unsupported', reason: 'YNAB cannot change a split’s date.' }],
    })
    render(<YnabSync />)
    await screen.findByText("Keep YNAB's")
    expect(screen.queryByText("Keep Open Net Worth's")).toBeNull()
  })

  it('changes the schedule and starts a run on request', async () => {
    render(<YnabSync />)
    const select = (await screen.findByLabelText('Schedule')) as HTMLSelectElement
    fireEvent.change(select, { target: { value: '6h' } })
    await waitFor(() =>
      expect(requests[0]).toMatchObject({ method: 'PUT', body: { interval: '6h' } }),
    )
    fireEvent.click(screen.getByText('Sync now'))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({ url: '/api/ynab/sync/run', method: 'POST' }),
    )
    expect(await screen.findByText('Syncing…')).toBeTruthy()
  })

  it('asks for a token first, and for a plan before it can be turned on', async () => {
    current = base({ token_saved: false })
    render(<YnabSync />)
    expect(await screen.findByText(/Save your YNAB access token above first/)).toBeTruthy()
    cleanup()
    clearQueryCache()
    current = base({
      plan_id: null,
      plan_name: null,
      enabled: false,
      last_run: null,
      conflicts: [],
    })
    render(<YnabSync />)
    expect(await screen.findByText('Not run yet.')).toBeTruthy()
    expect(screen.getByLabelText('YNAB plan to keep in sync')).toBeTruthy()
    expect((screen.getByRole('switch') as HTMLInputElement).disabled).toBe(true)
  })

  it('shows a failed run’s reason', async () => {
    current = base({
      last_run: { ...base().last_run, ok: false, error: 'YNAB HTTP 401', summary: 'Failed' },
      conflicts: [],
    })
    render(<YnabSync />)
    expect(await screen.findByText(/YNAB HTTP 401/)).toBeTruthy()
  })
})
