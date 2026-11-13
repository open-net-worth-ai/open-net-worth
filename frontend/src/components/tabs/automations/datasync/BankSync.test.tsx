// @vitest-environment jsdom
//
// Bank sync without AI (ADR 2026-11-13): the connector panel (mode, Sync per account, what a mode
// change did), the review list, and the Data sync card — and nothing at all with the flag off.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache } from '../../../../state/queryCache'
import { BankSyncSection } from './BankSyncPanel'
import { BankSyncReviews } from './BankSyncReviews'
import { DataSyncView } from './DataSyncView'
import type { BankSyncStatus } from './bankSync'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const reply = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

function status(over: Partial<BankSyncStatus> = {}): BankSyncStatus {
  return {
    provider: 'gocardless',
    label: 'GoCardless',
    mode: 'off',
    ready: true,
    lucius_followup: false,
    interval_hours: 12,
    daily_reads: 4,
    last_run: null,
    next_run_at: null,
    last_status: null,
    last_error: null,
    running: false,
    to_review: 0,
    lucius_automations: [],
    accounts: [
      {
        account_id: 'acc_1',
        name: 'ACME Current',
        bank_name: 'Current Account',
        mask: '0001',
        sync: true,
        reads_left: 3,
        next_read_at: null,
      },
    ],
    ...over,
  }
}

let current: BankSyncStatus
const sent: { url: string; method?: string; body?: string }[] = []

beforeEach(() => {
  current = status()
  sent.length = 0
  authFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
    if (init?.method) sent.push({ url, method: init.method, body: init.body })
    if (url === '/api/bank-sync/gocardless/mode' && init?.method === 'PUT') {
      current = status({ mode: 'data' })
      return reply({
        ...current,
        paused_automations: ['Sync GoCardless'],
        resumed_automation: null,
        needs_template: false,
      })
    }
    if (url === '/api/bank-sync/gocardless') return reply(current)
    if (url.startsWith('/api/bank-sync/reviews?'))
      return reply({
        items: [
          {
            id: 'bsr_1',
            provider: 'gocardless',
            provider_label: 'GoCardless',
            kind: 'modified',
            ref: 'acc:h1',
            txn_id: 't1',
            created_at: '2026-11-13T10:00:00+00:00',
            updated_at: '2026-11-13T10:00:00+00:00',
            detail: {
              current: { date: '2026-11-08', amount: -25, currency: 'GBP', description: 'ACME' },
              after: { date: '2026-11-08', amount: -27.5, currency: 'GBP', description: 'ACME' },
            },
          },
        ],
      })
    if (url.startsWith('/api/bank-sync/reviews/')) return reply({ ok: true })
    return reply({}, 404)
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
  navigate.mockReset()
})

describe.each([false, true])('bank_data_sync=%s', (on) => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ bank_data_sync: on })))
  afterEach(() => restore())

  it(on ? 'shows the panel on the connector page' : 'shows nothing at all', async () => {
    render(<BankSyncSection provider="gocardless" />)
    if (!on) {
      await new Promise((r) => setTimeout(r, 20))
      expect(screen.queryByRole('region', { name: 'GoCardless sync' })).toBeNull()
      expect(authFetch).not.toHaveBeenCalled()
      return
    }
    expect(await screen.findByRole('region', { name: 'GoCardless sync' })).toBeTruthy()
    expect(screen.getByText(/Nothing books GoCardless transactions/)).toBeTruthy()
  })
})

describe('with bank_data_sync on', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ bank_data_sync: true })))
  afterEach(() => restore())

  it('choosing Sync without AI says which Automation it turned off', async () => {
    render(<BankSyncSection provider="gocardless" />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Sync without AI' }))
    expect(
      await screen.findByText(
        /Turned off the Automation “Sync GoCardless”, so rows aren’t booked twice/,
      ),
    ).toBeTruthy()
    expect(sent[0]).toMatchObject({ url: '/api/bank-sync/gocardless/mode', method: 'PUT' })
    expect(JSON.parse(sent[0]!.body!)).toEqual({ mode: 'data' })
  })

  it('lists each linked account with its own Sync box and reads left', async () => {
    current = status({ mode: 'data' })
    render(<BankSyncSection provider="gocardless" />)
    const box = await screen.findByRole('checkbox', { name: /ACME Current/ })
    expect((box as HTMLInputElement).checked).toBe(true)
    expect(screen.getByText(/3 of 4 bank reads left today/)).toBeTruthy()
    fireEvent.click(box)
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/bank-sync/gocardless/accounts/acc_1',
        method: 'PUT',
      }),
    )
    expect(JSON.parse(sent.at(-1)!.body!)).toEqual({ sync: false })
  })

  it('a review item changes nothing until you choose', async () => {
    render(<BankSyncReviews provider="gocardless" />)
    expect(
      await screen.findByText('GoCardless changed a transaction you have booked.'),
    ).toBeTruthy()
    expect(sent).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Use the bank’s version' }))
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({ url: '/api/bank-sync/reviews/bsr_1/accept' }),
    )
  })

  it('puts bank connectors in the Data sync view beside the budget apps', () => {
    const onOpen = vi.fn()
    render(
      <DataSyncView
        entries={[]}
        banks={[status({ mode: 'data', to_review: 2 })]}
        onOpen={onOpen}
      />,
    )
    const card = screen.getByRole('article', { name: 'GoCardless bank sync' })
    expect(card.textContent).toContain('2 to review')
    fireEvent.click(card)
    expect(onOpen).toHaveBeenCalledWith('bank:gocardless')
  })
})
