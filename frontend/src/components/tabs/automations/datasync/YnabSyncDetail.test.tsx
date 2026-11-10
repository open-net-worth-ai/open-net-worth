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
import type { Preview, PreviewCategory, RunRecord } from '../../imports/runs'

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
// What a preview answers once the review links an account (null: the same as unlinked).
let linked: Preview | null
// What a preview answers once the review changes where a category goes (null: unchanged).
let categorized: Preview | null
let requests: { url: string; method: string; body: unknown }[]
const onBack = vi.fn()
const onOpenReport = vi.fn()

const ok = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

beforeEach(() => {
  runs = []
  sync = syncStatus()
  preview = PREVIEW
  linked = null
  categorized = null
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
      const shown =
        categorized && Object.keys(body.category_links ?? {}).length
          ? categorized
          : linked && Object.keys(body.account_links ?? {}).length
            ? linked
            : preview
      const started =
        kind === 'preview'
          ? run({ preview: shown })
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

/** The Envelope column's text on the row of a YNAB category, found by its own cell. */
const envelopeCell = (w: ReturnType<typeof within>, ynabName: string) =>
  w.getAllByRole('cell', { name: ynabName })[0]!.closest('tr')!.querySelectorAll('td')[2]!
    .textContent

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

  it('reviews the plan in one window, then imports it and turns the sync on', async () => {
    sync = notSetUp()
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    expect(screen.queryByText('Turn on the sync')).toBeNull() // one step, not two
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const review = await screen.findByRole('dialog', { name: /Import Household/ })
    const w = within(review)
    expect(await w.findByText(/USD, 1 account, 4 transactions, 3 categories/)).toBeTruthy()
    // Three tabs over one pane: accounts first, then each category under its group, then rows.
    expect(w.getByRole('cell', { name: 'Checking' })).toBeTruthy()
    expect(w.getByRole('cell', { name: 'Cash' })).toBeTruthy() // the app's own type label
    fireEvent.click(w.getByRole('tab', { name: /Categories/ }))
    expect(w.getByRole('rowheader', { name: /Bills/ })).toBeTruthy()
    expect(w.getAllByRole('cell', { name: 'Utilities' }).length).toBeGreaterThan(0)
    fireEvent.click(w.getByRole('tab', { name: /Transactions/ }))
    expect(w.getByRole('cell', { name: 'Landlord' })).toBeTruthy()
    expect(w.getByRole('cell', { name: 'Ready to Assign' })).toBeTruthy() // a blank inflow
    expect(requests).toEqual([
      { url: '/api/ynab/migration/preview', method: 'POST', body: { plan_id: 'p1' } },
    ])
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() =>
      expect(requests.at(-1)).toEqual({
        url: '/api/ynab/migration/start',
        method: 'POST',
        body: { plan_id: 'p1', confirm_nonempty: false, sync_direction: 'both' },
      }),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(await screen.findByText('View live report'))
    expect(onOpenReport).toHaveBeenCalledWith(runs.at(-1)?.id)
  })

  it('opens at its final shape while the plan is read, with nothing to import yet', async () => {
    // The step used to fall back to a bare "Preview" between a run finishing and its details
    // arriving, so it looked reset and was clicked again. The window now waits on its own run.
    sync = notSetUp()
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    const base = authFetch.getMockImplementation()!
    authFetch.mockImplementation((url: string, init?: RequestInit) =>
      /\/api\/imports\/runs\/\w+$/.test(url.split('?')[0] ?? '')
        ? ok({ ...runs.at(-1), status: 'running', phase: 'reading YNAB', preview: null })
        : base(url, init),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household \(USD\)/ }))
    expect(w.getByText('Reading it from YNAB…')).toBeTruthy()
    expect(w.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Accounts',
      'Categories',
      'Transactions',
    ])
    expect((w.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('says a book with other accounts keeps them, and importing then confirms it', async () => {
    sync = notSetUp()
    preview = { ...PREVIEW, book_state: 'foreign' }
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household/ }))
    expect(
      await w.findByText(/Each YNAB account comes in as a new one unless you pick/),
    ).toBeTruthy()
    fireEvent.click(w.getByRole('button', { name: 'After import' }))
    fireEvent.mouseDown(screen.getByRole('option', { name: /Don’t sync, import once/ }))
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        url: '/api/ynab/migration/start',
        body: { plan_id: 'p1', confirm_nonempty: true, sync_direction: null },
      }),
    )
  })

  it('imports a YNAB account into one already here, matching the rows both hold', async () => {
    sync = notSetUp()
    const checking = {
      ...PREVIEW.accounts[0]!,
      id: 'y1',
      link: null,
      chosen: false,
      candidates: [{ id: 'a1', name: 'Everyday', type: 'cash' }],
      match: null,
    }
    preview = { ...PREVIEW, book_state: 'foreign', accounts: [checking] }
    linked = {
      ...preview,
      accounts: [
        {
          ...checking,
          link: { id: 'a1', name: 'Everyday' },
          chosen: true,
          match: {
            matched: 2,
            to_check: 1,
            added: 1,
            own_only: 1,
            pairs: [
              {
                ref: 't9',
                date: '2026-10-02',
                payee: 'Landlord',
                amount: -1200,
                existing: { id: 'x1', date: '2026-10-02', payee: 'Rent payment', amount: -1200 },
              },
            ],
          },
        },
      ],
    }
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household/ }))
    fireEvent.click(await w.findByRole('button', { name: 'Where Checking goes' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'New account',
      'Use Everyday',
    ])
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Use Everyday' }))
    expect(
      await w.findByText('2 transactions already in Everyday. 1 added. 1 to check.'),
    ).toBeTruthy()
    expect(w.getByText(/1 transaction of Everyday’s own isn’t in YNAB/)).toBeTruthy()
    expect(requests.at(-1)).toMatchObject({
      url: '/api/ynab/migration/preview',
      body: { plan_id: 'p1', account_links: { y1: 'a1' }, recheck_of: runs[0]!.id },
    })
    fireEvent.click(w.getByLabelText(/Same as Rent payment/))
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() =>
      expect(requests.at(-1)).toEqual({
        url: '/api/ynab/migration/start',
        method: 'POST',
        body: {
          plan_id: 'p1',
          confirm_nonempty: true,
          sync_direction: 'both',
          account_links: { y1: 'a1' },
          same_as: { t9: 'x1' },
        },
      }),
    )
  })

  it('says where each YNAB category goes, lets it be changed, and holds the import for a pick', async () => {
    sync = notSetUp()
    const row = (
      id: string,
      name: string,
      over: Partial<PreviewCategory> = {},
    ): PreviewCategory => ({
      id,
      label: name,
      name,
      hidden: false,
      link: null,
      chosen: false,
      ask: false,
      options: [],
      note: 'Budget syncs with a new envelope of its own',
      ...over,
    })
    const matched = row('c2', 'Utilities', {
      link: { ref: 'cat:u1', name: 'Utilities', kind: 'category' },
      note: 'Budget syncs with envelope Utilities',
    })
    const ask = row('c3', 'Dining out', {
      ask: true,
      options: [
        { ref: 'cat:g9', name: 'Food shop' },
        { ref: 'cat:u1', name: 'Utilities' },
      ],
      note: 'Matches more than one of your categories. Pick one.',
    })
    const groups = (rows: PreviewCategory[]) => [
      { id: 'yg1', name: 'Bills', group: null, categories: rows },
    ]
    preview = { ...PREVIEW, category_groups: groups([row('c1', 'Rent'), matched, ask]) }
    categorized = {
      ...preview,
      category_groups: groups([
        row('c1', 'Rent', {
          chosen: true,
          link: { ref: 'cat:g9', name: 'Food shop', kind: 'category' },
          note: 'Not budgeted here',
        }),
        {
          ...matched,
          link: null,
          note: 'Budget syncs with a new envelope of its own',
          chosen: true,
        },
        {
          ...ask,
          ask: false,
          chosen: true,
          link: { ref: 'env:e1', name: 'Gifts', kind: 'envelope' },
          note: "Envelope Gifts holds other categories too — its Assigned won't sync",
        },
      ]),
    }
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household/ }))
    fireEvent.click(await w.findByRole('tab', { name: /Categories/ }))
    // one of yours keeps the envelope it is in, shown by name; no line under any row
    expect(envelopeCell(w, 'Utilities')).toBe('Bills pot')
    expect(w.queryByText(/Budget syncs|Pick one\.$|Similar name/)).toBeNull()
    expect(w.getByText(/Dining out matches more than one of your categories/)).toBeTruthy()
    expect((w.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(w.getByRole('button', { name: 'Category here for Utilities' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'New category',
      'Utilities',
      'Food shop',
    ])
    // each category wears its own colour, as everywhere else in the app
    const tag = screen.getByRole('option', { name: 'Food shop' }).querySelector('svg')
    expect(tag?.getAttribute('stroke')).toBe('#e9c46a')
    fireEvent.mouseDown(screen.getByRole('option', { name: 'New category' }))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        url: '/api/ynab/migration/preview',
        body: { plan_id: 'p1', category_links: { c2: '' }, recheck_of: runs[0]!.id },
      }),
    )
    // Gifts holds other categories, so its Assigned won't sync: said by a mark, not a line
    // the bubble is portaled to <body>, outside the dialog's accessibility tree; focus (or a
    // hover, or a tap) opens it, and the recheck's re-render can come first
    await waitFor(() => {
      fireEvent.focus(w.getByRole('button', { name: 'Check Dining out' }))
      expect(screen.getByRole('tooltip', { hidden: true }).textContent).toMatch(
        /Gifts holds other categories/,
      )
    })
    // a new category picks its envelope in its own column; one of yours keeps the one it is in
    expect(w.getByRole('button', { name: 'Envelope for Dining out' }).textContent).toBe('Gifts')
    expect(w.queryByRole('button', { name: 'Envelope for Rent' })).toBeNull()
    fireEvent.click(w.getByRole('button', { name: 'Envelope for Utilities' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Utilities (new)'])
    expect(envelopeCell(w, 'Rent')).toBe('Not budgeted')
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() =>
      expect(requests.at(-1)).toMatchObject({
        url: '/api/ynab/migration/start',
        body: { category_links: { c2: '' } },
      }),
    )
  })

  it('searches a long list of categories', async () => {
    sync = notSetUp()
    const many = Array.from({ length: 12 }, (_, i) => ({ ref: `cat:k${i}`, name: `Shop ${i}` }))
    preview = {
      ...PREVIEW,
      category_choices: { categories: many, envelopes: [] },
      category_groups: [
        {
          id: 'yg1',
          name: 'Bills',
          group: null,
          categories: [
            {
              id: 'c1',
              label: 'Old Cat',
              name: 'Old Cat',
              hidden: true,
              link: null,
              chosen: false,
              ask: false,
              options: [],
              note: 'Budget syncs with a new envelope of its own.',
            },
            {
              id: 'c2',
              label: 'Cold Cat',
              name: 'Cold Cat',
              hidden: true,
              link: { ref: 'cat:k1', name: 'Shop 1', kind: 'category' },
              chosen: false,
              ask: false,
              options: [],
              note: 'Budget syncs with envelope Shop 1',
            },
          ],
        },
      ],
    }
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household/ }))
    fireEvent.click(await w.findByRole('tab', { name: /Categories/ }))
    expect(envelopeCell(w, 'Cold Cat')).toBe('Not budgeted')
    fireEvent.click(w.getByRole('button', { name: 'Category here for Old Cat' }))
    const box = screen.getByPlaceholderText('Search your categories…')
    fireEvent.change(box, { target: { value: 'shop 11' } })
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Shop 11'])
    fireEvent.change(box, { target: { value: 'zzz' } })
    expect(screen.getByText('No category matches.')).toBeTruthy()
  })

  it('a plan in another currency than a book with data cannot be imported, and says why', async () => {
    sync = notSetUp()
    preview = { ...PREVIEW, currency_blocked: true, currency_change: false }
    page()
    const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'p1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review…' }))
    const w = within(await screen.findByRole('dialog', { name: /Import Household/ }))
    expect(await w.findByText(/Can’t import: this book is in SGD/)).toBeTruthy()
    expect((w.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('an import that left the sync off offers it in the same step', async () => {
    sync = notSetUp()
    runs = [importRun()]
    page()
    expect(await screen.findByText(/Imported\. Turn on the sync/)).toBeTruthy()
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
  it('shows the next sync and the last result', async () => {
    page()
    expect(await screen.findByText(/Next sync:/)).toBeTruthy()
    expect(screen.getByText(/3 sent to YNAB, 1 pulled, 1 needs review/)).toBeTruthy()
    expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true)
  })

  it('says in one line per direction that categories travel too', async () => {
    page()
    expect(
      await screen.findByText(/Transactions, categories and Assigned changed on either side/),
    ).toBeTruthy()
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
    // read like a Trigger: the schedule is its value, and clicking it opens the choices
    fireEvent.click(await screen.findByRole('button', { name: 'How often to sync' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Every 6 hours' }))
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

  it('opens a run as a modal over the page, and closing it returns to the page', async () => {
    runs = [importRun()]
    page(runs[0]?.id ?? null)
    expect(await screen.findByText('Verification')).toBeTruthy()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('Schedule')).toBeTruthy() // the page is still underneath
    fireEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(onOpenReport).toHaveBeenCalledWith(null)
  })
})
