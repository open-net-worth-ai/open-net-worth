// @vitest-environment jsdom
//
// The in-app YNAB migration flow: token state -> pick a plan -> preview -> import with live
// progress -> verification report -> re-check. The server is a scripted fake.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { YnabMigration } from './YnabMigration'
import { clearQueryCache } from '../../../state/queryCache'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const PLANS = { budgets: [{ id: 'p1', name: 'Household', currency: 'USD' }] }
const PREVIEW = {
  plan_name: 'Household',
  currency: 'USD',
  accounts: [
    {
      name: 'Checking',
      ynab_type: 'checking',
      type: 'cash',
      closed: false,
      opening_balance: 100,
      transactions: 4,
    },
  ],
  categories: 3,
  groups: 2,
  transactions: 4,
  splits: 1,
  transfers: 1,
  budget_months: 2,
  warnings: ['1 transfer(s) have no matching row in the other account.'],
  book_state: 'fresh',
  base_currency: 'SGD',
  currency_change: true,
  currency_blocked: false,
  money_data: [],
  prune: { categories: ['Vacation'], groups: [] },
}
const ROW = {
  group: 'account',
  label: 'Checking: balance',
  expected: 90,
  actual: 90,
  ok: true,
  note: '',
}

function job(over: Record<string, unknown>) {
  return {
    mode: 'preview',
    plan_id: 'p1',
    status: 'done',
    phase: 'done',
    progress: { done: 0, total: 0 },
    log: [],
    counts: {},
    problems: [],
    warnings: [],
    backup: null,
    preview: null,
    verification: null,
    error: null,
    ...over,
  }
}

let status: { token_saved: boolean; job: unknown }
let statusReads: Array<unknown>
let posts: string[]

function ok(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}

beforeEach(() => {
  posts = []
  statusReads = []
  status = { token_saved: true, job: null }
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posts.push(url)
      if (url.endsWith('/preview')) status = { ...status, job: job({ preview: PREVIEW }) }
      if (url.endsWith('/start'))
        statusReads = [
          job({
            mode: 'import',
            status: 'running',
            phase: 'booking transactions',
            progress: { done: 2, total: 4 },
            preview: PREVIEW,
          }),
        ]
      if (url.endsWith('/verify'))
        status = {
          ...status,
          job: job({
            mode: 'verify',
            preview: PREVIEW,
            verification: { ok: true, mismatches: 0, rows: [ROW] },
          }),
        }
      return ok(status.job)
    }
    if (url === '/api/ynab/budgets') return ok(PLANS)
    if (url === '/api/ynab/migration') {
      if (statusReads.length) {
        status = { ...status, job: statusReads.shift() }
        if (!statusReads.length && (status.job as { status: string }).status === 'running')
          statusReads = [
            job({
              mode: 'import',
              preview: PREVIEW,
              log: ['Accounts: 1 created, 0 already there.'],
              verification: {
                ok: false,
                mismatches: 1,
                rows: [ROW, { ...ROW, label: 'x', ok: false, actual: 80, note: 'explained' }],
              },
            }),
          ]
      }
      return ok(status)
    }
    return ok({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('YnabMigration', () => {
  it('asks for the token first when none is saved', async () => {
    status = { token_saved: false, job: null }
    render(<YnabMigration />)
    expect(await screen.findByText(/Save your YNAB access token above first/)).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalledWith('/api/ynab/budgets')
  })

  it('shows an error with a retry when the plans cannot be listed', async () => {
    authFetch.mockImplementation((url: string) =>
      url === '/api/ynab/budgets'
        ? Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: 'YNAB HTTP 401' }) })
        : ok(status),
    )
    render(<YnabMigration />)
    expect(await screen.findByText('YNAB HTTP 401')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('says so when the token sees no plans', async () => {
    authFetch.mockImplementation((url: string) =>
      url === '/api/ynab/budgets' ? ok({ budgets: [] }) : ok(status),
    )
    render(<YnabMigration />)
    expect(await screen.findByText(/can't see any YNAB plans/)).toBeTruthy()
  })

  it('needs a plan before it will preview', async () => {
    render(<YnabMigration />)
    const preview = (await screen.findByText('Preview')) as HTMLButtonElement
    expect(preview.closest('button')?.disabled).toBe(true)
  })

  it('walks plan -> preview -> import with progress -> report -> re-check', async () => {
    render(<YnabMigration />)
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByText('Preview'))

    expect(await screen.findByText(/Household \(USD\) will bring across/)).toBeTruthy()
    expect(screen.getByText(/1 accounts · 4 transactions/)).toBeTruthy()
    expect(screen.getByText(/no matching row in the other account/)).toBeTruthy()
    expect(screen.getByText(/base currency will change from SGD to USD/)).toBeTruthy()
    expect(screen.getByText(/starter items that will be removed: Vacation/)).toBeTruthy()

    fireEvent.click(screen.getByText('Import into Open Net Worth'))
    expect(await screen.findByText(/booking transactions/)).toBeTruthy()
    await waitFor(() => expect(posts).toContain('/api/ynab/migration/start'))

    expect(await screen.findByText('Checked against YNAB', {}, { timeout: 4000 })).toBeTruthy()
    expect(screen.getByText('1 to look at')).toBeTruthy()
    expect(screen.getByText(/Accounts: 1 created/)).toBeTruthy()

    fireEvent.click(screen.getByText('Re-check'))
    expect(await screen.findByText('All match')).toBeTruthy()
    await waitFor(() => expect(posts).toContain('/api/ynab/migration/verify'))
  })

  it('makes the user confirm before importing into a book with other accounts', async () => {
    status = {
      token_saved: true,
      job: job({ preview: { ...PREVIEW, book_state: 'foreign' } }),
    }
    render(<YnabMigration />)
    const importBtn = (await screen.findByText('Import into Open Net Worth')).closest('button')!
    expect(importBtn.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText(/didn't come from YNAB/))
    expect(importBtn.disabled).toBe(false)
  })

  it('blocks the import when the currency cannot change', async () => {
    status = {
      token_saved: true,
      job: job({
        preview: {
          ...PREVIEW,
          currency_change: false,
          currency_blocked: true,
          money_data: ['owned assets'],
        },
      }),
    }
    render(<YnabMigration />)
    expect(await screen.findByText(/already holds owned assets/)).toBeTruthy()
    expect(
      (screen.getByText('Import into Open Net Worth').closest('button') as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })

  it('shows a failed job’s reason', async () => {
    status = { token_saved: true, job: job({ status: 'failed', error: 'YNAB HTTP 429' }) }
    render(<YnabMigration />)
    expect(await screen.findByText('YNAB HTTP 429')).toBeTruthy()
  })
})
