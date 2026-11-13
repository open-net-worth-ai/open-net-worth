// @vitest-environment jsdom
//
// Bank sync without AI (ADR 2026-11-13): the connector line, the bank's page (the on/off switch,
// Sync per account, the setup steps), the review list and the Data sync card — and nothing at all
// with the flag off. No Lucius option anywhere on it.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { overrideFlagsForTests } from '../../../../hooks/useFlag'
import { clearQueryCache } from '../../../../state/queryCache'
import { BankSyncHead, BankSyncSettings } from './BankSyncPanel'
import { BankSyncSetup } from './BankSyncSetup'
import { BankRunReport } from './BankRunReport'
import { BankSyncSection } from './BankSyncSummary'
import { BankSyncReviews } from './BankSyncReviews'
import { DataSyncView } from './DataSyncView'
import { ConnectorNextStep } from '../../connectors/ConnectorNextStep'
import type { BankSyncStatus } from './bankSync'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
const navigate = vi.hoisted(() => vi.fn())
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouter: () => undefined,
}))

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
    history_days: 90,
    stage: 'history',
    accounts: [
      {
        account_id: 'acc_1',
        name: 'ACME Current',
        bank_name: 'Current Account',
        mask: '0001',
        sync: true,
        reads_left: 3,
        next_read_at: null,
        imported: false,
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
    if (url === '/api/bank-sync') return reply({ connectors: [current] })
    if (url === '/api/bank-sync/gocardless/schedule') {
      current = status({ ...current, interval_hours: JSON.parse(init!.body!).interval_hours })
      return reply(current)
    }
    if (url === '/api/imports/runs/r1')
      return reply({
        id: 'r1',
        provider: 'gocardless',
        kind: 'sync',
        trigger: 'scheduled',
        status: 'done',
        started_at: '2026-11-13T10:00:00+00:00',
        finished_at: '2026-11-13T10:00:05+00:00',
        outcome: '1 booked',
        counts: { fetched: 3, booked: 1, pending_skipped: 1, already_booked: 1, errors: 0 },
        errors: [],
        warnings: [],
        log: [],
        problems: [],
        progress: { done: 0, total: 0 },
      })
    if (url.startsWith('/api/transactions?batch_id=bank_sync%3Ar1'))
      return reply([
        {
          id: 't1',
          date: '2026-11-12',
          description: 'ACME GROCERY',
          payee_name: 'ACME Grocery',
          category: null,
          from_account_id: 'acc_1',
          from_amount: 45.2,
          from_currency: 'EUR',
          from_account_name: 'ACME Current',
          to_amount: null,
          to_currency: null,
        },
      ])
    if (url === '/api/categories') return reply([])
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
    if (url === '/api/bank-sync/gocardless/import')
      return reply({ run_id: 'r1', accounts: 1, skipped_for_limit: 0 })
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

  it(
    on ? 'is the connector page’s one next step until set up' : 'shows nothing at all',
    async () => {
      render(<BankSyncSection provider="gocardless" />)
      if (!on) {
        await new Promise((r) => setTimeout(r, 20))
        expect(screen.queryByRole('region', { name: 'GoCardless sync' })).toBeNull()
        expect(authFetch).not.toHaveBeenCalled()
        return
      }
      expect(await screen.findByRole('region', { name: 'GoCardless sync' })).toBeTruthy()
      expect(screen.getByText('Next: bring in transactions')).toBeTruthy()
      expect(screen.getByText(/every 12 hours/)).toBeTruthy()
      expect(screen.queryByRole('checkbox')).toBeNull() // the details live on the sync's own page
      fireEvent.click(screen.getByRole('button', { name: 'Set up sync' }))
      expect(navigate).toHaveBeenCalledWith(
        expect.objectContaining({
          to: '/automations',
          search: { view: 'sync', sync: 'bank:gocardless' },
        }),
      )
    },
  )
})

describe('with bank_data_sync on', () => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ bank_data_sync: true })))
  afterEach(() => restore())

  it('the page is one on/off switch, with no Lucius options', () => {
    render(
      <>
        <BankSyncHead status={status({ mode: 'data', stage: 'done' })} />
        <BankSyncSettings status={status({ mode: 'data', stage: 'done' })} />
      </>,
    )
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByText(/Lucius/)).toBeNull()
    const sw = screen.getByRole('switch', { name: 'Turn the GoCardless sync off' })
    expect((sw as HTMLInputElement).checked).toBe(true)
  })

  it('switching it on says which Automation it turned off', async () => {
    render(<BankSyncHead status={status({ stage: 'done' })} />)
    fireEvent.click(screen.getByRole('switch', { name: 'Turn the GoCardless sync on' }))
    expect(
      await screen.findByText(
        /Turned off the Automation “Sync GoCardless”, so rows aren’t booked twice/,
      ),
    ).toBeTruthy()
    expect(sent[0]).toMatchObject({ url: '/api/bank-sync/gocardless/mode', method: 'PUT' })
    expect(JSON.parse(sent[0]!.body!)).toEqual({ mode: 'data' })
  })

  it('lists each linked account with its own Sync box and reads left', async () => {
    render(<BankSyncSettings status={status({ mode: 'data', stage: 'done' })} />)
    const box = await screen.findByRole('checkbox', { name: /ACME Current/ })
    expect((box as HTMLInputElement).checked).toBe(true)
    expect(screen.getByText(/^From Current Account ··0001 · 3 of 4 reads left today$/)).toBeTruthy()
    fireEvent.click(box)
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/bank-sync/gocardless/accounts/acc_1',
        method: 'PUT',
      }),
    )
    expect(JSON.parse(sent.at(-1)!.body!)).toEqual({ sync: false })
  })

  it('setup brings the history in first, then turns the sync on, with no AI choice', async () => {
    const { rerender } = render(<BankSyncSetup status={status()} />)
    expect(screen.getByText(/Up to 90 days/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Import history' }))
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/bank-sync/gocardless/import',
        method: 'POST',
      }),
    )
    const imported = status({ stage: 'sync' })
    imported.accounts![0]!.imported = true
    rerender(<BankSyncSetup status={imported} />)
    expect(screen.getByText(/^Imported/)).toBeTruthy()
    expect(screen.queryByText(/Lucius/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Turn on' }))
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({ url: '/api/bank-sync/gocardless/mode', method: 'PUT' }),
    )
    expect(JSON.parse(sent.at(-1)!.body!)).toEqual({ mode: 'data' })
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

  it('the schedule is a menu of what the bank allows, and picking one saves it', async () => {
    render(
      <BankSyncSettings status={status({ mode: 'data', stage: 'done', intervals: [6, 12, 24] })} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'How often to sync' }))
    expect(screen.queryByRole('menuitem', { name: 'Every hour' })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Once a day' }))
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/bank-sync/gocardless/schedule',
        method: 'PUT',
      }),
    )
    expect(JSON.parse(sent.at(-1)!.body!)).toEqual({ interval_hours: 24 })
  })

  it('accounts can be linked without leaving the page', async () => {
    render(<BankSyncSetup status={status({ stage: 'accounts', accounts: [] })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Link accounts' }))
    expect(await screen.findByRole('dialog', { name: /Link GoCardless accounts/ })).toBeTruthy()
  })

  it('a run report says what it did and lists what it booked', async () => {
    render(<BankRunReport runId="r1" onClose={() => {}} />)
    expect(
      await screen.findByText('1 booked · 1 already in your book · 1 still pending at the bank'),
    ).toBeTruthy()
    expect(screen.getByText('Scheduled · took 5s · read 3 from the bank')).toBeTruthy()
    expect(await screen.findByText('ACME Grocery')).toBeTruthy()
    expect(screen.getByText('ACME Current · Uncategorized')).toBeTruthy()
  })

  it('step 2 can be skipped: Skip for now moves on to Turn on, importing nothing', () => {
    render(<BankSyncSetup status={status()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }))
    expect(screen.getByText('Skipped. The first sync brings it in.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Skip for now' })).toBeNull()
    expect(sent).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'Turn on' })).toBeTruthy()
  })

  it('once set up, the connector page shows one line and Manage', async () => {
    current = status({ mode: 'off', stage: 'done' })
    render(<BankSyncSection provider="gocardless" />)
    expect(await screen.findByText('Off')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Manage' })).toBeTruthy()
    expect(screen.queryByText('Next: bring in transactions')).toBeNull()
  })

  it.each(['off', 'data'] as const)(
    'with the sync %s, the Lucius next-step card never shows beside it',
    async (mode) => {
      current = status({ mode })
      render(
        <ConnectorNextStep
          provider="gocardless"
          providerLabel="GoCardless"
          linkedCount={1}
          unlinkedCount={0}
        />,
      )
      await waitFor(() => expect(authFetch).toHaveBeenCalledWith('/api/bank-sync'))
      await new Promise((r) => setTimeout(r, 20))
      expect(screen.queryByText(/Set up an automation and Lucius/)).toBeNull()
    },
  )

  it('a connector with no bank sync keeps the Lucius next step', async () => {
    authFetch.mockImplementation(() => reply({ connectors: [] }))
    render(
      <ConnectorNextStep
        provider="simplefin"
        providerLabel="SimpleFIN"
        linkedCount={1}
        unlinkedCount={0}
      />,
    )
    expect(await screen.findByText(/Set up an automation and Lucius/)).toBeTruthy()
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
