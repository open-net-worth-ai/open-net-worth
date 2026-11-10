// @vitest-environment jsdom
//
// PR #93 user-acceptance scenarios through the whole Accounts screen (REC-01/15/17, HOLD-01/02/03/04,
// UI-08, UI-21): reconcile on a Brokerage total account sends `real_total` and `expected_balance`
// to the reconcile endpoint, Keep / Use call the confirm endpoint with accept false / true, and
// what the server then says reaches the screen. Synthetic figures only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

import { AccountsTab } from './AccountsTab'
import type { ApiAccount } from './types'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { setBaseCurrency } from '../../../utils/format'
import { onDataChanged } from '../../../lucius/bus'

let restoreFlag: () => void = () => {}

function res(body: unknown, status = 200): Response {
  const ok = status < 400
  return { ok, status, json: async () => body, text: async () => JSON.stringify(body) } as Response
}

const PLAID = {
  source: 'plaid',
  ref: 'pa-1',
  name: 'Brokerage',
  mask: '4321',
  linked_at: '2026-11-01T00:00:00Z',
  provider_label: 'Plaid',
  status: 'connected' as const,
}

const TYPED: ApiAccount = {
  id: 'acc-401k',
  name: 'Old 401(k)',
  kind: 'asset',
  type: 'retirement',
  institution: 'Acme Securities',
  currency: 'USD',
  initial_balance_date: '2026-01-01',
  external_ids: [],
  balance: 4980,
  balance_base: 4980,
  cleared_balance: 4980,
  cleared_balance_base: 4980,
  value_source: 'total',
  value_sync: null,
  value_siblings: [],
  value_breakdown: { tracked_holdings: 3020, untracked: 4980, total: 8000, currency: 'USD' },
}

const HELD: ApiAccount = {
  ...TYPED,
  id: 'acc-held',
  name: 'Brokerage X',
  type: 'investment',
  balance: 123456.78,
  balance_base: 123456.78,
  cleared_balance: 123456.78,
  cleared_balance_base: 123456.78,
  external_ids: [PLAID],
  value_sync: {
    status: 'held_back',
    source: 'plaid',
    provider_label: 'Plaid',
    total: 123456.78,
    held_total: 0,
    changed_at: '2026-11-10T10:00:00+00:00',
  },
  value_breakdown: { tracked_holdings: 0, untracked: 123456.78, total: 123456.78, currency: 'USD' },
}

interface Call {
  url: string
  method: string
  body: unknown
}
let calls: Call[] = []
let accounts: ApiAccount[] = []
let confirmReply: () => Response = () => res({})
let reconcileReply: () => Response = () =>
  res({ adjustment: 0, balance: 4980, cleared_balance: 4980 })

function routes() {
  calls = []
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    calls.push({ url, method, body })
    if (url.endsWith('/reconcile') && method === 'POST') return reconcileReply()
    if (url.endsWith('/value-sync/confirm') && method === 'POST') return confirmReply()
    const one = accounts.find((a) => url === `/api/accounts/${a.id}`)
    if (one) return res(one)
    if (url.startsWith('/api/accounts')) return res(accounts)
    if (url.startsWith('/api/categories')) return res([])
    if (url.startsWith('/api/payees')) return res({ payees: [] })
    if (url.startsWith('/api/portfolio')) return res({ holdings_by_account: { 'acc-401k': 3020 } })
    if (url.startsWith('/api/prices')) return res({})
    if (url.startsWith('/api/transactions'))
      return res({ transactions: [], total: 0, has_more: false })
    return res({})
  })
}

const writes = (suffix: string) =>
  calls.filter((c) => c.method === 'POST' && c.url.endsWith(suffix))

async function boot(id: string) {
  routes()
  render(<AccountsTab selectedAccountId={id} />)
  await waitFor(() => expect(screen.getByLabelText('More actions')).toBeTruthy())
}

beforeEach(() => {
  setBaseCurrency('USD')
  vi.clearAllMocks()
  restoreFlag = overrideFlagsForTests({ investment_value_source: true })
  accounts = [TYPED, HELD]
  confirmReply = () => res({})
  reconcileReply = () => res({ adjustment: 0, balance: 4980, cleared_balance: 4980 })
})
afterEach(() => {
  cleanup()
  restoreFlag()
})

describe('reconcile on a Brokerage total account', () => {
  it('REC-01: "Yes, it matches" posts real_total 8000 and the shown balance, never real_balance', async () => {
    await boot('acc-401k')
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile' }))
    expect(screen.getByRole('dialog').textContent).toContain(
      'Is the account’s real total $8,000.00 USD?',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it matches' }))
    await waitFor(() => expect(writes('/reconcile')).toHaveLength(1))
    const call = writes('/reconcile')[0]!
    expect(call.url).toBe('/api/accounts/acc-401k/reconcile')
    expect(call.body).toEqual({ real_total: 8000, expected_balance: 4980 })
    expect(call.body).not.toHaveProperty('real_balance')
    expect(call.body).not.toHaveProperty('against')
  })

  it('REC-01: typing 9,000.00 previews +$1,000.00 and posts real_total 9000', async () => {
    reconcileReply = () => res({ adjustment: 1000, balance: 5980, cleared_balance: 5980 })
    await boot('acc-401k')
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile' }))
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    fireEvent.change(screen.getByPlaceholderText('8000'), { target: { value: '9000' } })
    expect(screen.getByText('+$1,000.00')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add adjustment' }))
    await waitFor(() => expect(writes('/reconcile')).toHaveLength(1))
    expect(writes('/reconcile')[0]!.body).toEqual({ real_total: 9000, expected_balance: 4980 })
  })

  it('REC-15: a stale screen (409) shows the server’s message and re-reads the account', async () => {
    reconcileReply = () => res({ detail: 'The balance is now $5,000.00, not $4,980.00.' }, 409)
    await boot('acc-401k')
    const readsBefore = calls.filter((c) => c.url === '/api/accounts/acc-401k').length
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it matches' }))
    expect(await screen.findByText('The balance is now $5,000.00, not $4,980.00.')).toBeTruthy()
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/accounts/acc-401k').length).toBeGreaterThan(
        readsBefore,
      ),
    )
  })

  it('a successful reconcile reloads the register so the new row shows without a reload (UI-21)', async () => {
    reconcileReply = () => res({ adjustment: 1000, balance: 5980, cleared_balance: 5980 })
    await boot('acc-401k')
    const txnReads = () => calls.filter((c) => c.url.startsWith('/api/transactions')).length
    const before = txnReads()
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile' }))
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    fireEvent.change(screen.getByPlaceholderText('8000'), { target: { value: '9000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add adjustment' }))
    await waitFor(() => expect(txnReads()).toBeGreaterThan(before))
  })
})

describe('Keep / Use on a held-back total', () => {
  it('HOLD-01 / UI-08: shows the question with both figures', async () => {
    await boot('acc-held')
    expect(await screen.findByRole('button', { name: 'Keep $123,456.78' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Use $0.00' })).toBeTruthy()
    expect(screen.getByText(/That looks wrong, so it wasn’t used/)).toBeTruthy()
  })

  it('HOLD-02: Keep posts accept false to the confirm endpoint and does not reload the register', async () => {
    await boot('acc-held')
    const keep = await screen.findByRole('button', { name: 'Keep $123,456.78' })
    const txnReads = () => calls.filter((c) => c.url.startsWith('/api/transactions')).length
    const before = txnReads()
    fireEvent.click(keep)
    await waitFor(() => expect(writes('/value-sync/confirm')).toHaveLength(1))
    expect(writes('/value-sync/confirm')[0]).toMatchObject({
      url: '/api/accounts/acc-held/value-sync/confirm',
      method: 'POST',
      body: { accept: false },
    })
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/accounts/acc-held').length).toBeGreaterThan(0),
    )
    expect(txnReads()).toBe(before)
  })

  it('HOLD-03: Use posts accept true, re-reads the account and reloads the register', async () => {
    await boot('acc-held')
    const use = await screen.findByRole('button', { name: 'Use $0.00' })
    const txnReads = () => calls.filter((c) => c.url.startsWith('/api/transactions')).length
    const before = txnReads()
    fireEvent.click(use)
    await waitFor(() => expect(writes('/value-sync/confirm')).toHaveLength(1))
    expect(writes('/value-sync/confirm')[0]!.body).toEqual({ accept: true })
    await waitFor(() => expect(txnReads()).toBeGreaterThan(before))
  })

  it('Use tells every screen the ledger changed, so net worth refreshes (QA 2026-11-10)', async () => {
    const seen: string[][] = []
    const off = onDataChanged((domains) => seen.push(domains))
    try {
      await boot('acc-held')
      fireEvent.click(await screen.findByRole('button', { name: 'Use $0.00' }))
      await waitFor(() => expect(seen.some((d) => d.includes('ledger'))).toBe(true))
    } finally {
      off()
    }
  })

  it('Keep changes no figure, so it broadcasts no ledger change', async () => {
    const seen: string[][] = []
    const off = onDataChanged((domains) => seen.push(domains))
    try {
      await boot('acc-held')
      fireEvent.click(await screen.findByRole('button', { name: 'Keep $123,456.78' }))
      await waitFor(() => expect(writes('/value-sync/confirm')).toHaveLength(1))
      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/api/accounts/acc-held').length).toBeGreaterThan(0),
      )
      expect(seen.some((d) => d.includes('ledger'))).toBe(false)
    } finally {
      off()
    }
  })

  it('HOLD-04: a second Use that the server refuses (422) shows the server’s reason', async () => {
    confirmReply = () => res({ detail: 'no held-back total to confirm' }, 422)
    await boot('acc-held')
    fireEvent.click(await screen.findByRole('button', { name: 'Use $0.00' }))
    expect(await screen.findByText('no held-back total to confirm')).toBeTruthy()
  })

  it('HOLD-04: a refusal with no readable reason says "Failed (500)"', async () => {
    confirmReply = () =>
      ({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('x')
        },
      }) as unknown as Response
    await boot('acc-held')
    fireEvent.click(await screen.findByRole('button', { name: 'Keep $123,456.78' }))
    expect(await screen.findByText('Failed (500)')).toBeTruthy()
  })
})
