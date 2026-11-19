// @vitest-environment jsdom
//
// The Actual sync's page and its row in Data Sync: YNAB's components with Actual's words, and the
// little only Actual has (tracking, the server's warnings, the sentence a refusal comes back with).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache } from '../../../../state/queryCache'
import { PREVIEW, SYNC_ITEM, run, syncStatus } from '../../imports/importsFixtures'
import type { RunRecord } from '../../imports/runs'
import { ACTUAL_APP, YNAB_APP, type BudgetAppInfo } from './budgetApps'
import { DataSyncView } from './DataSyncView'
import { YnabSyncDetail } from './YnabSyncDetail'
import { useBudgetSync } from './ynabSync'

const authFetch = vi.hoisted(() => vi.fn())
const navigate = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const HOST = 'actual.acme.test'
const REFUSAL = 'The YNAB sync is on. Turn it off in Data Sync before turning on the Actual sync.'
const CONNECTOR = {
  id: 'actual_budget',
  name: 'Actual Budget',
  kind: 'actual_budget',
  enabled: true,
  config: {},
  secrets_public: {},
  status: 'connected',
  pausable: true,
  short_description: 'Import a budget from your Actual server and keep it in step.',
  tools: [],
}

type Status = ReturnType<typeof syncStatus>
let actual: Status
let ynab: Status
let runs: RunRecord[]
let config: Record<string, unknown>
let refuseSettings: string | null
let requests: { url: string; method: string; body: unknown }[]
const known = new Map<string, RunRecord>()
const NO_ACCOUNTS = {
  ...PREVIEW,
  plan_name: 'ACME Household',
  accounts: [],
  warnings: [],
  prune: { categories: [], groups: [] },
  currency_change: false,
}
const startBody = () =>
  requests.find((r) => r.url === '/api/actual-budget/migration/start')?.body as {
    plan_id: string
    sync_direction: string | null
  }
const IMPORT_FIRST = { kind: 'import_first', text: 'Import this budget first.' }
const DIFFERS = { kind: 'budget_differs', text: 'The connection is elsewhere.', plan_id: 'b2' }
const onBack = vi.fn()
const onOpenReport = vi.fn()

const reply = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

const actualStatus = (over: Record<string, unknown> = {}) =>
  syncStatus({
    plan_id: 'b1',
    plan_name: 'ACME Household',
    conflicts: [],
    budget_mode: 'envelope',
    warnings: [],
    last_run: null,
    ...over,
  })

const notSetUp = (over: Record<string, unknown> = {}) =>
  actualStatus({ enabled: false, plan_id: null, plan_name: null, ...over })

beforeEach(() => {
  actual = actualStatus()
  ynab = syncStatus({ conflicts: [] })
  runs = []
  refuseSettings = null
  requests = []
  config = {
    configured: true,
    enabled: true,
    available: true,
    has_password: true,
    has_encryption_password: false,
    server_host: HOST,
    budget_id: 'b1',
    budget_name: 'ACME Household',
    last_synced_at: null,
    last_status: 'never_run',
    last_error: null,
  }
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const path = url.split('?')[0] ?? ''
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : null
    if (method !== 'GET') requests.push({ url, method, body })
    if (path === '/api/actual-budget/sync') return reply(actual)
    if (path === '/api/ynab/sync') return reply(ynab)
    if (path.startsWith('/api/actual-budget/sync/') || path.startsWith('/api/ynab/sync/')) {
      if (refuseSettings) return reply({ detail: refuseSettings }, 409)
      return reply(actual)
    }
    if (path === '/api/actual-budget/status') return reply(config)
    if (path === '/api/actual-budget/migration/preview') {
      const started = run({
        provider: 'actual_budget',
        plan_id: body.plan_id,
        preview: NO_ACCOUNTS,
      })
      known.set(started.id, started)
      return reply({ ...started, blocked_reason: null }, 202)
    }
    if (path === '/api/actual-budget/migration/start')
      return reply(run({ provider: 'actual_budget', kind: 'import', status: 'running' }), 202)
    if (path === '/api/actual-budget/migration') return reply({ blocked_reason: null })
    const one = /\/api\/imports\/runs\/(\w+)$/.exec(path)?.[1]
    if (one) return reply(known.get(one))
    if (path === '/api/actual-budget/budgets') {
      return reply({
        budgets: [
          { id: 'b1', name: 'ACME Household', currency: null, encrypted: false },
          { id: 'b2', name: 'ACME Vault', currency: null, encrypted: true },
        ],
      })
    }
    if (path === '/api/connectors') return reply({ connectors: [CONNECTOR] })
    if (path === '/api/imports/runs') {
      const provider = new URLSearchParams(url.split('?')[1]).get('provider')
      return reply({ runs: runs.filter((r) => r.provider === provider) })
    }
    if (path === '/api/accounts') return reply([])
    return reply({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

const page = () =>
  render(
    <YnabSyncDetail app={ACTUAL_APP} reportId={null} onBack={onBack} onOpenReport={onOpenReport} />,
  )

function List({ apps, onOpen }: { apps: BudgetAppInfo[]; onOpen: (key: string) => void }) {
  const ynabSync = useBudgetSync(YNAB_APP, false, apps.includes(YNAB_APP))
  const actualSync = useBudgetSync(ACTUAL_APP, false, apps.includes(ACTUAL_APP))
  const entries = apps.map((app) => ({ app, sync: app === YNAB_APP ? ynabSync : actualSync }))
  return <DataSyncView entries={entries} onOpen={onOpen} />
}

describe('the Actual sync, setting up', () => {
  it('starts at connecting Actual', async () => {
    actual = notSetUp({ token_saved: false })
    page()
    expect(await screen.findByRole('heading', { name: 'Actual' })).toBeTruthy()
    expect(screen.getByText('Connect Actual')).toBeTruthy()
    expect(screen.getByText('Add your Actual server address and sign-in.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Connect Actual…' })).toBeTruthy()
    expect(screen.getByText('After you connect Actual.')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/YNAB|plan\b/)
  })

  it('connected: says the sign-in is saved, never the address', async () => {
    actual = notSetUp()
    page()
    expect(await screen.findByText(/Sign-in saved/)).toBeTruthy()
    expect(document.body.textContent).not.toContain(HOST)
  })

  it('pre-selects the budget saved on the connector', async () => {
    actual = notSetUp()
    page()
    const picker = (await screen.findByLabelText(/Actual budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('b1'))
    expect(screen.getByText(/Bring your budget in and keep it in sync/)).toBeTruthy()
    expect(screen.getByText(/Review what your budget holds/)).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Review…' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('an encrypted budget with no saved password sends you to the connection', async () => {
    actual = notSetUp()
    config = { ...config, budget_id: 'b2', budget_name: 'ACME Vault' }
    page()
    expect(
      await screen.findByText('This budget is encrypted. Add its encryption password first.'),
    ).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Review…' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect(screen.getByRole('button', { name: 'Open the connection' })).toBeTruthy()
  })

  it('offers the import again once the sync has been turned on', async () => {
    actual = notSetUp()
    runs = [run({ provider: 'actual_budget', kind: 'import', plan_id: 'b1' })]
    page()
    expect(await screen.findByText(/Imported. Turn on the sync/)).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'From Actual' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'To Actual' })).toBeTruthy()
  })
})

describe('the Actual sync, set up', () => {
  it('reads as YNAB’s page in Actual’s words, with its own rule about the trail', async () => {
    page()
    expect(await screen.findByRole('heading', { name: 'Actual' })).toBeTruthy()
    expect(
      screen.getByText('Transactions and each month’s Assigned, in step with your Actual budget.'),
    ).toBeTruthy()
    expect(screen.getByText('Actual budget')).toBeTruthy()
    expect(screen.getByLabelText('Turn the Actual sync off')).toBeTruthy()
    expect(
      screen.getByText(
        'Each Assigned change it makes in Actual leaves a line in that category’s note, so you can spot it there.',
      ),
    ).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/YNAB|red flag/)
    expect(document.body.textContent).not.toContain(HOST)
  })

  it('from Actual writes nothing there, so the trail isn’t mentioned', async () => {
    actual = actualStatus({ direction: 'from_ynab' })
    page()
    expect(
      await screen.findByText(
        'Actual’s transactions, categories and Assigned come here. Nothing is written to Actual.',
      ),
    ).toBeTruthy()
    expect(screen.queryByText(/leaves a line in that category/)).toBeNull()
  })

  it('a tracking budget says so once, and nothing else promises Assigned', async () => {
    actual = actualStatus({ budget_mode: 'tracking' })
    page()
    const note = await screen.findByText(/This budget is in tracking mode in Actual/)
    expect(note.className).toContain('a3-last-sync')
    expect(document.body.textContent?.match(/tracking/g)).toHaveLength(1)
    const withoutNote = (document.body.textContent ?? '').replace(note.textContent ?? '', '')
    expect(withoutNote).not.toMatch(/Assigned/)
    expect(
      screen.getByText(
        'Transactions and categories changed on either side are copied to the other.',
      ),
    ).toBeTruthy()
    expect(
      screen.getByText('Transactions and categories, in step with your Actual budget.'),
    ).toBeTruthy()
  })

  it('shows every warning the server sends, in the one block', async () => {
    const warnings = [
      { kind: 'income_in_envelope', text: 'ACME Pay is an income category in Actual.' },
      { kind: 'not_in_this_budget', text: '7 transactions here are on other accounts.' },
      {
        kind: 'waiting_for_both_ways',
        text: '2 changes here wait until this sync goes both ways.',
      },
      { kind: 'import_first', text: 'Import this budget first.' },
    ]
    actual = actualStatus({ warnings })
    page()
    const heading = await screen.findByText('Worth checking')
    const block = heading.closest('section')!
    expect(block.className).toContain('a3-attention')
    for (const w of warnings) expect(within(block).getByText(w.text)).toBeTruthy()
  })

  it('shows no block with nothing to say', async () => {
    page()
    await screen.findByRole('heading', { name: 'Actual' })
    expect(screen.queryByText('Worth checking')).toBeNull()
  })

  it('import first: the import is right there, and its end reads the status again', async () => {
    actual = actualStatus({
      warnings: [{ kind: 'import_first', text: 'Import this budget first.' }],
    })
    const importing = run({
      provider: 'actual_budget',
      kind: 'import',
      plan_id: 'b1',
      status: 'running',
      finished_at: null,
      phase: 'booking',
    })
    runs = [importing]
    page()
    expect(await screen.findByText(/Working: booking/)).toBeTruthy()
    const reads = () =>
      authFetch.mock.calls.filter(([url]) => url === '/api/actual-budget/sync').length
    const before = reads()
    runs = [{ ...importing, status: 'done', finished_at: '2026-10-12T09:02:14+00:00' }]
    actual = actualStatus({ warnings: [] })
    await waitFor(() => expect(reads()).toBeGreaterThan(before), { timeout: 4000 })
    await waitFor(() => expect(screen.queryByText('Worth checking')).toBeNull())
  })

  it('a refused Turn on shows the server’s sentence and stays off', async () => {
    actual = actualStatus({ enabled: false })
    refuseSettings = REFUSAL
    page()
    fireEvent.click(await screen.findByLabelText('Turn the Actual sync on'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(REFUSAL))
    expect(screen.getByRole('alert').className).toContain('a3-action-error')
    expect(requests.at(-1)?.url).toBe('/api/actual-budget/sync/settings')
  })

  it('a review item names Actual, and answers on Actual’s route', async () => {
    actual = actualStatus({
      conflicts: [{ ...SYNC_ITEM, reason: 'Changed in both Open Net Worth and Actual.' }],
    })
    page()
    const button = await screen.findByRole('button', { name: "Keep Actual's" })
    expect(screen.getByText(/here and in Actual/)).toBeTruthy()
    fireEvent.click(button)
    await waitFor(() =>
      expect(requests.at(-1)?.url).toMatch(/^\/api\/actual-budget\/sync\/conflicts/),
    )
  })
})

describe('importing from Worth checking', () => {
  async function review() {
    fireEvent.click(await screen.findByRole('button', { name: 'Review…' }))
    return within(await screen.findByRole('dialog', { name: /Import/ }))
  }

  it('import first keeps the direction the sync is on', async () => {
    actual = actualStatus({ direction: 'from_ynab', warnings: [IMPORT_FIRST] })
    page()
    const w = await review()
    await w.findByText(/Sync from Actual only/)
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(startBody()?.sync_direction).toBe('from_ynab'))
  })

  it('import first on a paused sync leaves it paused', async () => {
    actual = actualStatus({ enabled: false, direction: 'to_ynab', warnings: [IMPORT_FIRST] })
    page()
    const w = await review()
    await w.findByText(/Don’t sync, import once/)
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(startBody()).toBeTruthy())
    expect(startBody().sync_direction).toBeNull()
  })

  it('the first setup still starts on both ways', async () => {
    actual = notSetUp()
    page()
    const w = await review()
    expect(await w.findByText(/Keep in sync both ways/)).toBeTruthy()
  })

  it('a budget that differs offers no import while the sync is on', async () => {
    actual = actualStatus({ warnings: [DIFFERS] })
    page()
    expect(await screen.findByText(DIFFERS.text)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review…' })).toBeNull()
  })

  it('a budget that differs, sync off: imports it in the sync’s direction', async () => {
    config = { ...config, has_encryption_password: true }
    actual = actualStatus({ enabled: false, direction: 'from_ynab', warnings: [DIFFERS] })
    page()
    const picker = (await screen.findByLabelText(/Actual budget/)) as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('b2'))
    const w = await review()
    await w.findByText(/Sync from Actual only/)
    fireEvent.click(w.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(startBody()).toBeTruthy())
    expect(startBody()).toMatchObject({ plan_id: 'b2', sync_direction: 'from_ynab' })
  })
})

describe('the connection links', () => {
  it('a paused connection says so, leads to it, and Sync now waits', async () => {
    config = { ...config, enabled: false }
    actual = actualStatus({
      connector_paused: true,
      warnings: [
        { kind: 'connector_paused', text: 'The Actual connection is paused.' },
        IMPORT_FIRST,
      ],
    })
    page()
    const heading = await screen.findByText('Worth checking')
    const block = within(heading.closest('section')!)
    expect(block.getByText(/The Actual connection is paused\./)).toBeTruthy()
    const syncNow = screen.getByRole('button', { name: 'Sync now' }) as HTMLButtonElement
    expect(syncNow.disabled).toBe(true)
    expect(screen.getByText('Waiting: the Actual connection is paused.')).toBeTruthy()
    expect(block.queryByRole('button', { name: /Review/ })).toBeNull()
    fireEvent.click(block.getByRole('button', { name: 'Open the connection' }))
    expect(await screen.findByLabelText(/Server address/)).toBeTruthy()
    expect(screen.getByText(/Resume the connector/)).toBeTruthy()
  })

  it('open the form even when the connector is healthy', async () => {
    actual = notSetUp()
    page()
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }))
    expect(await screen.findByLabelText(/Server address/)).toBeTruthy()
  })

  it('YNAB’s still open the overview', async () => {
    authFetch.mockImplementation((url: string) => {
      const path = url.split('?')[0] ?? ''
      if (path === '/api/ynab/sync') return reply(syncStatus({ enabled: false, plan_id: null }))
      if (path === '/api/ynab/status')
        return reply({ configured: true, enabled: true, has_access_token: true })
      if (path === '/api/ynab/budgets') return reply({ budgets: [] })
      if (path === '/api/connectors')
        return reply({
          connectors: [{ ...CONNECTOR, id: 'ynab', name: 'YNAB', kind: 'ynab' }],
        })
      if (path === '/api/imports/runs') return reply({ runs: [] })
      return reply({})
    })
    render(<YnabSyncDetail reportId={null} onBack={onBack} onOpenReport={onOpenReport} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }))
    await waitFor(() => expect(document.querySelector('.rd-modal')).toBeTruthy())
    expect(screen.queryByLabelText(/Access token/)).toBeNull()
  })
})

describe('the history', () => {
  it('Actual lists no previews unless they failed, and counts what it lists', async () => {
    runs = [
      run({ provider: 'actual_budget', kind: 'sync', outcome: 'Nothing changed' }),
      run({ provider: 'actual_budget', kind: 'preview', outcome: 'Would bring across' }),
      run({
        provider: 'actual_budget',
        kind: 'preview',
        status: 'failed',
        outcome: '',
        error: 'Down',
      }),
    ]
    page()
    const history = (await screen.findByRole('list', {
      name: 'Actual sync history',
    })) as HTMLElement
    expect(within(history).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText('2 runs')).toBeTruthy()
    expect(screen.queryByText(/Would bring across/)).toBeNull()
  })
})

describe('the Data Sync list', () => {
  it('only Actual on: one tile, set up by name', async () => {
    actual = notSetUp()
    const onOpen = vi.fn()
    render(<List apps={[ACTUAL_APP]} onOpen={onOpen} />)
    const tile = await screen.findByRole('button', { name: 'Set up Actual' })
    expect(tile.querySelector('.a3-mark-logo')?.getAttribute('src')).toBe(
      '/connector-logos/actual_budget.png',
    )
    fireEvent.click(tile)
    expect(onOpen).toHaveBeenCalledWith('actual_budget')
    expect(screen.queryByRole('button', { name: 'Set up YNAB' })).toBeNull()
  })

  it('both set up: two rows, YNAB first, each opening its own page', async () => {
    const onOpen = vi.fn()
    render(<List apps={[YNAB_APP, ACTUAL_APP]} onOpen={onOpen} />)
    const rows = await screen.findAllByRole('article')
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual(['YNAB', 'Actual'])
    expect(rows[1]!.textContent).toContain('ACME Household: Transactions')
    fireEvent.click(rows[1]!)
    expect(onOpen).toHaveBeenCalledWith('actual_budget')
  })

  it('one set up: its row, then the other as a tile', async () => {
    actual = notSetUp()
    render(<List apps={[YNAB_APP, ACTUAL_APP]} onOpen={vi.fn()} />)
    expect((await screen.findAllByRole('article')).length).toBe(1)
    expect(screen.getByRole('button', { name: 'Set up Actual' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set up YNAB' })).toBeNull()
  })

  it('a refused action from a card’s menu says why, above the list', async () => {
    actual = actualStatus({ enabled: false })
    refuseSettings = REFUSAL
    render(<List apps={[ACTUAL_APP]} onOpen={vi.fn()} />)
    await screen.findByRole('article')
    fireEvent.click(screen.getByRole('button', { name: /actions|more/i }))
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Turn on' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(REFUSAL))
    expect(screen.getByRole('alert').className).toContain('a3-action-error')
  })

  it('one app failing to load doesn’t hide the other', async () => {
    function Failing() {
      const ynabSync = useBudgetSync(YNAB_APP, false, true)
      const entries = [
        { app: YNAB_APP, sync: ynabSync },
        {
          app: ACTUAL_APP,
          sync: { ...ynabSync, error: new Error('Down'), loading: false, refresh: vi.fn() },
        },
      ]
      return <DataSyncView entries={entries} onOpen={vi.fn()} />
    }
    render(<Failing />)
    expect(await screen.findByText('The Actual sync couldn’t load')).toBeTruthy()
    expect(screen.getByRole('article', { name: 'YNAB' })).toBeTruthy()
  })
})
