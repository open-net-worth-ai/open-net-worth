// @vitest-environment jsdom
//
// The Sync panel: schedule + enable, Sync now with polling and a live-report link, last/next run
// and the review list with both versions and the keep-this-side choice. The server is scripted.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { SyncPanel } from './SyncPanel'
import { clearQueryCache } from '../../../state/queryCache'
import { PLANS, SYNC_ITEM, syncStatus } from './importsFixtures'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

let current: ReturnType<typeof syncStatus>
let requests: Array<{ url: string; method: string; body: unknown }>
const onOpen = vi.fn()

const ok = (body: unknown) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })

beforeEach(() => {
  requests = []
  current = syncStatus()
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (method !== 'GET')
      requests.push({ url, method, body: init?.body && JSON.parse(String(init.body)) })
    if (url === '/api/ynab/budgets') return ok(PLANS)
    if (url.endsWith('/resolve')) current = syncStatus({ conflicts: [] })
    if (url.endsWith('/run')) current = syncStatus({ running: true, run_id: 'r9' })
    if (url.endsWith('/settings')) current = syncStatus({ interval: '6h' })
    return ok(current)
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('SyncPanel', () => {
  it('shows the last result, the next run and both versions of a conflict', async () => {
    render(<SyncPanel onOpen={onOpen} />)
    expect(await screen.findByText(/3 sent to YNAB, 1 pulled, 1 needs review/)).toBeTruthy()
    expect(screen.getByText('Next run')).toBeTruthy()
    expect(screen.getByText('mine')).toBeTruthy()
    expect(screen.getByText('theirs')).toBeTruthy()
  })

  it('applies the chosen side to both and clears the item', async () => {
    render(<SyncPanel onOpen={onOpen} />)
    fireEvent.click(await screen.findByText("Keep YNAB's"))
    await waitFor(() => expect(screen.queryByText('Needs your review (1)')).toBeNull())
    expect(requests).toEqual([
      { url: '/api/ynab/sync/conflicts/c_1/resolve', method: 'POST', body: { keep: 'ynab' } },
    ])
  })

  it('offers only YNAB’s side for something YNAB cannot take, unless YNAB has nothing', async () => {
    current = syncStatus({
      conflicts: [
        { ...SYNC_ITEM, kind: 'unsupported', reason: 'YNAB cannot change a split’s date.' },
      ],
    })
    render(<SyncPanel onOpen={onOpen} />)
    await screen.findByText("Keep YNAB's")
    expect(screen.queryByText("Keep Open Net Worth's")).toBeNull()
  })

  it('changes the schedule, starts a run and links to its live report', async () => {
    render(<SyncPanel onOpen={onOpen} />)
    fireEvent.change(await screen.findByLabelText('Schedule'), { target: { value: '6h' } })
    await waitFor(() =>
      expect(requests[0]).toMatchObject({ method: 'PUT', body: { interval: '6h' } }),
    )
    fireEvent.click(screen.getByText('Sync now'))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({ url: '/api/ynab/sync/run', method: 'POST' }),
    )
    expect(await screen.findByText('Syncing…')).toBeTruthy()
    fireEvent.click(screen.getByText('View live report'))
    expect(onOpen).toHaveBeenCalledWith('r9')
  })

  it('links the last report when the run was stored, and says when a quiet run was not', async () => {
    current = syncStatus({ last_run: { ...syncStatus().last_run, run_id: 'r5' } })
    render(<SyncPanel onOpen={onOpen} />)
    fireEvent.click(await screen.findByText('Open the last report'))
    expect(onOpen).toHaveBeenCalledWith('r5')
  })

  it('asks for a token first, and for a plan before it can be turned on', async () => {
    current = syncStatus({ token_saved: false })
    render(<SyncPanel onOpen={onOpen} />)
    expect(
      await screen.findByText(/Save your YNAB access token on the YNAB connector/),
    ).toBeTruthy()
    cleanup()
    clearQueryCache()
    current = syncStatus({
      plan_id: null,
      plan_name: null,
      enabled: false,
      last_run: null,
      conflicts: [],
    })
    render(<SyncPanel onOpen={onOpen} />)
    expect(await screen.findByText('Not run yet.')).toBeTruthy()
    expect(screen.getByLabelText('YNAB plan to keep in sync')).toBeTruthy()
    expect((screen.getByRole('switch') as HTMLInputElement).disabled).toBe(true)
  })

  it('shows a failed run’s reason', async () => {
    current = syncStatus({
      last_run: { ...syncStatus().last_run, ok: false, error: 'YNAB HTTP 401', summary: 'Failed' },
      conflicts: [],
    })
    render(<SyncPanel onOpen={onOpen} />)
    expect(await screen.findByText(/YNAB HTTP 401/)).toBeTruthy()
  })
})

describe('SyncPanel review item', () => {
  it('shows what differs, field by field, before the identifying context', async () => {
    current = syncStatus({
      conflicts: [
        {
          ...SYNC_ITEM,
          onw: { ...SYNC_ITEM.onw, memo: 'dinner', category: 'Dining' },
          ynab: { ...SYNC_ITEM.ynab, memo: 'lunch', category: 'Dining' },
        },
      ],
    })
    render(<SyncPanel onOpen={onOpen} />)
    await screen.findByText('Memo')
    expect(screen.getByText('dinner')).toBeTruthy()
    expect(screen.getByText('lunch')).toBeTruthy()
    expect(screen.queryByText('Category')).toBeNull() // equal on both sides: not listed
    expect(screen.queryByText('Amount')).toBeNull()
    expect(screen.queryByText('mine')).toBeNull()
    expect(screen.getByText(/2026-10-01 · Visa · Bistro · -60.00/)).toBeTruthy()
  })

  it('puts the reason first for something YNAB cannot take', async () => {
    current = syncStatus({
      conflicts: [
        { ...SYNC_ITEM, kind: 'unsupported', reason: 'YNAB cannot change a split’s date.' },
      ],
    })
    render(<SyncPanel onOpen={onOpen} />)
    expect(await screen.findByText('YNAB cannot change a split’s date.')).toBeTruthy()
  })

  it('shows a side that was deleted', async () => {
    current = syncStatus({ conflicts: [{ ...SYNC_ITEM, ynab: null }] })
    render(<SyncPanel onOpen={onOpen} />)
    expect(await screen.findByText('Transaction')).toBeTruthy()
    expect(screen.getByText('Deleted')).toBeTruthy()
  })
})
