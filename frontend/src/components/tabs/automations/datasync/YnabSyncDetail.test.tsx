// @vitest-environment jsdom
//
// The two-way YNAB sync's page in Automations: the setup steps (connect -> import -> turn on),
// then the set-up page (on/off, Sync now, the review list, schedule, history, a run report). The
// server is a small stateful fake of /api/ynab/* and /api/imports/runs.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { YnabSyncDetail } from './YnabSyncDetail'
import { clearQueryCache } from '../../../../state/queryCache'
import {
  PLANS,
  PREVIEW,
  SYNC_ITEM,
  importRun,
  run,
  syncRun,
  syncStatus,
} from '../../imports/importsFixtures'
import type { RunRecord } from '../../imports/runs'

const authFetch = vi.hoisted(() => vi.fn())
const navigate = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

// The YNAB connector's wire row: the sync page opens the connector's OWN panel.
const YNAB_CONNECTOR = {
  id: 'ynab',
  name: 'YNAB',
  kind: 'ynab',
  enabled: false,
  config: {},
  secrets_public: {},
  status: 'not_configured',
  pausable: true,
  short_description: 'Transactions already tracked in your YNAB budget.',
  tools: [],
}

let runs: RunRecord[]
type Sync = Omit<ReturnType<typeof syncStatus>, 'run_id'> & { run_id: string | null }
let sync: Sync
let preview = PREVIEW
let requests: { url: string; method: string; body: unknown }[]
const onBack = vi.fn()
const onOpenReport = vi.fn()

const ok = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

beforeEach(() => {
  runs = []
  sync = syncStatus()
  preview = PREVIEW
  requests = []
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const path = url.split('?')[0] ?? ''
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : null
    if (method !== 'GET') requests.push({ url, method, body })
    if (path.startsWith('/api/ynab/sync/')) {
      if (url.endsWith('/resolve')) sync = { ...sync, conflicts: [] }
      if (url.endsWith('/run')) sync = { ...sync, running: true, run_id: 'r9' }
      if (url.endsWith('/settings')) sync = { ...sync, ...body }
      return ok(sync)
    }
    if (method === 'POST' && path.startsWith('/api/ynab/migration/')) {
      if (url.endsWith('/start') && preview.book_state === 'foreign' && !body.confirm_nonempty)
        return ok({ detail: { error: 'confirm_nonempty', message: 'confirm' } }, 409)
      const kind = url.endsWith('/start')
        ? 'import'
        : url.endsWith('/verify')
          ? 'verify'
          : 'preview'
      const started =
        kind === 'preview'
          ? run({ preview })
          : run({ kind, preview, status: 'running', finished_at: null, phase: 'booking' })
      runs.push(started)
      return ok(started, 202)
    }
    if (path === '/api/connectors') return ok({ connectors: [YNAB_CONNECTOR] })
    if (path === '/api/accounts') return ok([])
    if (path === '/api/connectors/ynab/accounts')
      return ok({ status: 'not_configured', provider_label: 'YNAB', accounts: [] })
    if (path === '/api/ynab/status')
      return ok({
        configured: sync.token_saved,
        enabled: sync.token_saved,
        has_access_token: sync.token_saved,
        budget_id: null,
        last_status: 'never_run',
      })
    if (path === '/api/ynab/budgets') return ok(PLANS)
    if (path === '/api/ynab/sync') return ok(sync)
    if (path === '/api/imports/runs') return ok({ runs: [...runs].reverse() })
    const one = /\/api\/imports\/runs\/(\w+)$/.exec(path)?.[1]
    if (one) return ok(runs.find((r) => r.id === one))
    return ok({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

const page = (reportId: string | null = null) =>
  render(<YnabSyncDetail reportId={reportId} onBack={onBack} onOpenReport={onOpenReport} />)

const notSetUp = (over: Record<string, unknown> = {}) =>
  syncStatus({
    enabled: false,
    plan_id: null,
    plan_name: null,
    last_run: null,
    conflicts: [],
    ...over,
  })

describe('YnabSyncDetail setup', () => {
  it('starts at connecting YNAB, and connects right here without leaving the page', async () => {
    sync = notSetUp({ token_saved: false })
    page()
    expect(await screen.findByText('Connect YNAB')).toBeTruthy()
    expect(screen.queryByLabelText(/access token/i)).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Connect YNAB…' }))
    const token = await screen.findByLabelText(/access token/i)
    fireEvent.change(token, { target: { value: 'tok-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(requests).toContainEqual({
        url: '/api/ynab/config',
        method: 'PUT',
        body: expect.objectContaining({ access_token: 'tok-123' }),
      }),
    )
    expect(navigate).not.toHaveBeenCalled()
  })

  it('once a token is saved, step 1 can still change it in place', async () => {
    sync = notSetUp()
    page()
    expect(await screen.findByText(/Token saved/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Change' }))
    const panel = await screen.findByRole('dialog')
    expect(panel.textContent).toContain('YNAB')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('imports as step 2: a plan, a preview, then the import', async () => {
    sync = notSetUp()
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    const importBtn = screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement
    expect(importBtn.disabled).toBe(true)
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    expect(await screen.findByText(/Household \(USD\) will bring across/)).toBeTruthy()
    await waitFor(() => expect(importBtn.disabled).toBe(false))
    fireEvent.click(importBtn)
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        url: '/api/ynab/migration/start',
        body: { plan_id: 'p1', confirm_nonempty: false },
      }),
    )
    fireEvent.click(await screen.findByText('View live report'))
    expect(onOpenReport).toHaveBeenCalledWith(runs.at(-1)?.id)
  })

  it('confirms before importing into a book that holds other accounts', async () => {
    sync = notSetUp()
    preview = { ...PREVIEW, book_state: 'foreign' }
    runs = [run({ preview })]
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    const importBtn = screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement
    await waitFor(() => expect(importBtn.disabled).toBe(false))
    fireEvent.click(importBtn)
    expect(await screen.findByText('Import alongside your existing accounts?')).toBeTruthy()
    expect(requests).toEqual([])
    fireEvent.click(screen.getByText('Import anyway'))
    await waitFor(() =>
      expect(requests[0]).toMatchObject({ body: { plan_id: 'p1', confirm_nonempty: true } }),
    )
  })

  it('after the import, picks a direction and turns the sync on for the imported plan', async () => {
    sync = notSetUp()
    runs = [importRun()]
    page()
    expect(await screen.findByText('Turn on the sync')).toBeTruthy()
    expect(screen.getByText(/Runs every hour/)).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'From YNAB' }))
    await waitFor(() =>
      expect(requests[0]).toEqual({
        url: '/api/ynab/sync/settings',
        method: 'PUT',
        body: { direction: 'from_ynab' },
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Turn on' }))
    await waitFor(() =>
      expect(requests[1]).toEqual({
        url: '/api/ynab/sync/settings',
        method: 'PUT',
        body: { enabled: true, plan_id: runs[0]?.plan_id },
      }),
    )
  })
})

describe('YnabSyncDetail once set up', () => {
  it('says it is a data sync and shows the next sync and the last result', async () => {
    page()
    expect(await screen.findByText('Data sync')).toBeTruthy()
    expect(screen.getByText('Fixed rules, no AI.')).toBeTruthy()
    expect(screen.getByText(/Next sync:/)).toBeTruthy()
    expect(screen.getByText(/3 sent to YNAB, 1 pulled, 1 needs review/)).toBeTruthy()
    expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true)
  })

  it('puts the review list first and applies the chosen side to both', async () => {
    page()
    expect(await screen.findByText('Needs your review')).toBeTruthy()
    expect(screen.getByText('mine')).toBeTruthy()
    expect(screen.getByText('theirs')).toBeTruthy()
    fireEvent.click(screen.getByText("Keep YNAB's"))
    await waitFor(() => expect(screen.queryByText('Needs your review')).toBeNull())
    expect(requests).toEqual([
      { url: '/api/ynab/sync/conflicts/c_1/resolve', method: 'POST', body: { keep: 'ynab' } },
    ])
  })

  it('offers only YNAB’s side for something YNAB cannot take', async () => {
    sync = syncStatus({
      conflicts: [
        { ...SYNC_ITEM, kind: 'unsupported', reason: 'YNAB cannot change a split’s date.' },
      ],
    })
    page()
    expect(await screen.findByText('YNAB cannot change a split’s date.')).toBeTruthy()
    expect(screen.queryByText("Keep Open Net Worth's")).toBeNull()
  })

  it('shows what differs, field by field, and a side that was deleted', async () => {
    sync = syncStatus({
      conflicts: [
        {
          ...SYNC_ITEM,
          onw: { ...SYNC_ITEM.onw, memo: 'dinner', category: 'Dining' },
          ynab: { ...SYNC_ITEM.ynab, memo: 'lunch', category: 'Dining' },
        },
        { ...SYNC_ITEM, id: 'c_2', ynab: null },
      ],
    })
    page()
    await screen.findByText('Memo')
    expect(screen.getByText('dinner')).toBeTruthy()
    expect(screen.getByText('lunch')).toBeTruthy()
    expect(screen.queryByText('Category')).toBeNull() // equal on both sides: not listed
    expect(screen.getByText('Deleted')).toBeTruthy()
  })

  it('changes the schedule, turns it off, and starts a sync', async () => {
    page()
    fireEvent.change(await screen.findByLabelText('How often to sync'), {
      target: { value: '6h' },
    })
    await waitFor(() =>
      expect(requests[0]).toMatchObject({ method: 'PUT', body: { interval: '6h' } }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({ url: '/api/ynab/sync/run', method: 'POST' }),
    )
    expect(await screen.findByText('Syncing now…')).toBeTruthy()
  })

  it('turning it off keeps the plan', async () => {
    page()
    fireEvent.click(await screen.findByRole('switch'))
    await waitFor(() =>
      expect(requests[0]).toEqual({
        url: '/api/ynab/sync/settings',
        method: 'PUT',
        body: { enabled: false, plan_id: 'p1' },
      }),
    )
  })

  it('shows a failed sync’s reason', async () => {
    sync = syncStatus({
      last_run: { ...syncStatus().last_run, ok: false, error: 'YNAB HTTP 401', summary: 'Failed' },
      conflicts: [],
    })
    page()
    expect(await screen.findByText(/failed\. YNAB HTTP 401/)).toBeTruthy()
  })

  it('lists every run newest first, filters to the ones needing attention, and opens one', async () => {
    runs = [
      importRun({ outcome: 'an import' }),
      syncRun({ trigger: 'scheduled', outcome: 'a sync' }),
      syncRun({ status: 'failed', outcome: '', error: 'YNAB HTTP 500' }),
    ]
    page()
    const history = await screen.findByRole('list', { name: 'YNAB sync history' })
    const rows = within(history).getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]?.textContent).toContain('YNAB HTTP 500')
    expect(rows[2]?.textContent).toContain('an import')
    fireEvent.click(screen.getByRole('tab', { name: 'Needs attention' }))
    expect(within(history).getAllByRole('listitem')).toHaveLength(1)
    fireEvent.click(within(history).getByRole('listitem'))
    expect(onOpenReport).toHaveBeenCalledWith(runs[2]?.id)
  })

  it('re-checks the plan against YNAB from the menu', async () => {
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Re-check against YNAB/ }))
    await waitFor(() =>
      expect(requests[0]).toMatchObject({
        url: '/api/ynab/migration/verify',
        body: { plan_id: 'p1' },
      }),
    )
  })

  it('opens a run as a report under its own breadcrumb', async () => {
    runs = [importRun()]
    page(runs[0]?.id ?? null)
    expect(await screen.findByText('Verification')).toBeTruthy()
    expect(screen.getByText('Run report')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'YNAB' }))
    expect(onOpenReport).toHaveBeenCalledWith(null)
    fireEvent.click(screen.getByRole('button', { name: 'Automations' }))
    expect(onBack).toHaveBeenCalled()
  })
})
