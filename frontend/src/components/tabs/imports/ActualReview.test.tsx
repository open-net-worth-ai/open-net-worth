// @vitest-environment jsdom
//
// The review of an Actual budget: what only it asks for (a type for each account, a currency when
// the budget doesn't state one), through YNAB's window. Nothing here can be imported unsettled.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache, invalidateQueryCache } from '../../../state/queryCache'
import { ACTUAL_APP } from '../automations/datasync/budgetApps'
import { PREVIEW, run } from './importsFixtures'
import type { Preview, PreviewAccount, RunRecord } from './runs'
import { YnabReview } from './YnabReview'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

const ON = ['cash', 'credit']
const OFF = ['fixed_deposit', 'investment', 'retirement', 'receivable', 'loan']

function account(id: string, name: string, type: string, over: Partial<PreviewAccount> = {}) {
  return {
    id,
    name,
    ynab_type: 'other',
    type,
    on_budget: true,
    closed: false,
    opening_balance: 100,
    transactions: 4,
    link: null,
    chosen: false,
    candidates: [],
    match: null,
    type_options: ON,
    ...over,
  } as PreviewAccount
}

const ACCOUNTS = [
  account('a1', 'ACME Checking', 'cash'),
  account('a2', 'ACME Visa', 'credit'),
  account('a3', 'ACME Brokerage', 'investment', { on_budget: false, type_options: OFF }),
]

const actualPreview = (over: Partial<Preview> = {}): Preview => ({
  ...PREVIEW,
  plan_name: 'ACME Household',
  accounts: ACCOUNTS,
  warnings: [],
  prune: { categories: [], groups: [] },
  currency_change: false,
  base_currency: 'USD',
  ...over,
})

let preview: Preview
let runs: Map<string, RunRecord>
let settle: (id: string) => boolean
let requests: { url: string; body: Record<string, unknown> }[]
let blocked: string | null
let startRefusal: string | null
let refuseRecheck: string | null
let holdPost: Promise<void> | null
const onClose = vi.fn()
const onStarted = vi.fn()

const reply = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

beforeEach(() => {
  preview = actualPreview()
  runs = new Map()
  settle = () => true
  requests = []
  blocked = null
  startRefusal = null
  refuseRecheck = null
  holdPost = null
  authFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const path = url.split('?')[0] ?? ''
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(init.body as string) : null
    if (method === 'POST') requests.push({ url, body })
    if (path === '/api/actual-budget/migration' && method === 'GET')
      return reply({ token_saved: true, job: null, blocked_reason: blocked })
    if (path === '/api/actual-budget/migration/preview') {
      if (refuseRecheck) return reply({ detail: refuseRecheck }, 422)
      if (holdPost) await holdPost
      const started = run({ provider: 'actual_budget', plan_id: 'b1', preview, status: 'running' })
      runs.set(started.id, started)
      return reply({ ...started, blocked_reason: blocked }, 202)
    }
    if (path === '/api/actual-budget/migration/start') {
      if (startRefusal) return reply({ detail: startRefusal }, 409)
      return reply(run({ provider: 'actual_budget', kind: 'import', status: 'running' }), 202)
    }
    const id = /\/api\/imports\/runs\/(\w+)$/.exec(path)?.[1]
    if (id) {
      const known = runs.get(id)!
      return reply(settle(id) ? { ...known, status: 'done' } : known)
    }
    return reply({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

async function open(props: { onConnect?: () => void } = {}) {
  const first = run({ provider: 'actual_budget', plan_id: 'b1', preview })
  runs.set(first.id, first)
  render(
    <YnabReview
      {...props}
      app={ACTUAL_APP}
      runId={first.id}
      planLabel="ACME Household"
      onClose={onClose}
      onStarted={onStarted}
    />,
  )
  const dialog = await screen.findByRole('dialog', { name: /Import ACME Household/ })
  const w = within(dialog)
  await w.findByRole('cell', { name: /ACME Checking/ })
  return w
}

const typeOf = (w: ReturnType<typeof within>, name: string) =>
  w.getByRole('button', { name: new RegExp(`^Type for ${name}`) }) as HTMLButtonElement
const importButton = (w: ReturnType<typeof within>) =>
  w.getByRole('button', { name: 'Import' }) as HTMLButtonElement
const pick = (w: ReturnType<typeof within>, name: string, option: RegExp | string) => {
  fireEvent.click(typeOf(w, name))
  fireEvent.mouseDown(screen.getByRole('option', { name: option }))
}
const previews = () => requests.filter((r) => r.url.endsWith('/preview'))

describe('the types', () => {
  it('offers only the types the server allows for each account’s band', async () => {
    const w = await open()
    fireEvent.click(typeOf(w, 'ACME Checking'))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Cash (suggested)',
      'Credit',
    ])
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Cash (suggested)' }))
    fireEvent.click(typeOf(w, 'ACME Brokerage'))
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Fixed deposit',
      'Investment (suggested)',
      'Retirement',
      'Receivable',
      'Loan',
    ])
  })

  it('groups accounts by band, and never says tracking', async () => {
    const w = await open()
    expect(w.getByRole('rowheader', { name: /On budget/ }).textContent).toMatch(/^On budget2/)
    expect(w.getByRole('rowheader', { name: /Off budget/ }).textContent).toMatch(/^Off budget1/)
    expect(w.getByText(/A card must be Credit/)).toBeTruthy()
    expect(w.queryByText(/\(tracking\)/)).toBeNull()
  })

  it('blocks the import until every new account is settled, and says so', async () => {
    const w = await open()
    expect(importButton(w).disabled).toBe(true)
    expect(importButton(w).title).toBe('Check the account types first')
    const note = w.getByText(/3 still need a type/)
    expect(importButton(w).getAttribute('aria-describedby')).toBe(note.closest('li')!.id)
    pick(w, 'ACME Checking', 'Cash (suggested)')
    pick(w, 'ACME Visa', 'Credit (suggested)')
    expect(w.getByText(/1 still needs a type/)).toBeTruthy()
    expect(importButton(w).disabled).toBe(true)
    pick(w, 'ACME Brokerage', 'Investment (suggested)')
    expect(w.queryByText(/still need/)).toBeNull()
    expect(importButton(w).disabled).toBe(false)
    expect(importButton(w).title).toBe('')
  })

  it('accepting the suggestions settles them all with no re-check, and focus stays in the window', async () => {
    const w = await open()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    expect(w.queryByRole('button', { name: 'Accept suggestions' })).toBeNull()
    expect(importButton(w).disabled).toBe(false)
    expect(typeOf(w, 'ACME Checking').textContent).toBe('Cash')
    expect(typeOf(w, 'ACME Checking').getAttribute('aria-label')).toBe(
      'Type for ACME Checking: Cash',
    )
    expect(previews()).toEqual([])
    expect(document.activeElement?.id).toBe('ui-page-tab-accounts')
  })

  it('imports with the type of every account that has no link, and the settled sync', async () => {
    preview = actualPreview({
      accounts: [
        ...ACCOUNTS,
        account('a4', 'ACME Saved', 'cash', { link: { id: 'x', name: 'Old savings' } }),
      ],
    })
    const w = await open()
    expect(
      within(w.getByRole('row', { name: /ACME Saved/ })).getByText('Keeps its own'),
    ).toBeTruthy()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    pick(w, 'ACME Checking', 'Credit')
    await waitFor(() => expect(previews().length).toBe(1))
    await waitFor(() => expect(importButton(w).disabled).toBe(false))
    fireEvent.click(importButton(w))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(requests.at(-1)?.body).toMatchObject({
      plan_id: 'b1',
      sync_direction: 'both',
      account_types: { a1: 'credit', a2: 'credit', a3: 'investment' },
    })
  })

  it('picking the suggestion fires nothing; a change fires one re-check with the types', async () => {
    const w = await open()
    pick(w, 'ACME Checking', 'Cash (suggested)')
    expect(previews()).toEqual([])
    pick(w, 'ACME Visa', 'Cash')
    await waitFor(() => expect(previews().length).toBe(1))
    expect(previews()[0]?.body.account_types).toEqual({
      a1: 'cash',
      a2: 'cash',
      a3: 'investment',
    })
    expect(previews()[0]?.body.recheck_of).toBeTruthy()
  })

  it('five quick changes during a re-check fire at most one more, and no control disables', async () => {
    let held = true
    settle = () => !held
    const w = await open()
    settle = (id) => {
      const first = [...runs.keys()][0]
      return id === first || !held
    }
    pick(w, 'ACME Checking', 'Credit')
    await waitFor(() => expect(previews().length).toBe(1))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    pick(w, 'ACME Visa', 'Cash')
    pick(w, 'ACME Checking', 'Cash')
    pick(w, 'ACME Visa', 'Credit')
    pick(w, 'ACME Checking', 'Credit')
    pick(w, 'ACME Visa', 'Cash')
    expect(previews().length).toBe(1)
    for (const name of ['ACME Checking', 'ACME Visa', 'ACME Brokerage'])
      expect(typeOf(w, name).disabled).toBe(false)
    expect(importButton(w).disabled).toBe(true)
    held = false
    await waitFor(() => expect(previews().length).toBe(2), { timeout: 5000 })
    expect(previews()[1]?.body.account_types).toEqual({
      a1: 'credit',
      a2: 'cash',
      a3: 'investment',
    })
    await waitFor(() => expect(importButton(w).disabled).toBe(true))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2200))
    })
    expect(previews().length).toBe(2)
  }, 10000)

  it('a linked account shows no type, and isn’t counted', async () => {
    preview = actualPreview({
      accounts: [account('a1', 'ACME Checking', 'cash', { link: { id: 'x', name: 'Mine' } })],
    })
    const w = await open()
    expect(w.getByText('Keeps its own')).toBeTruthy()
    expect(w.queryByRole('button', { name: /^Type for/ })).toBeNull()
    expect(w.queryByText(/still need/)).toBeNull()
    expect(importButton(w).disabled).toBe(false)
  })
})

describe('the currency', () => {
  it('asks when the budget doesn’t say, and sends it', async () => {
    preview = actualPreview({ currency_asked: true, currency: 'USD' })
    const w = await open()
    expect(w.getByText('Budget currency')).toBeTruthy()
    expect(w.getByText(/Actual doesn’t say, so this starts from this book’s currency/)).toBeTruthy()
    const select = w.getByRole('button', { name: /^Budget currency/ })
    fireEvent.click(select)
    fireEvent.change(screen.getByPlaceholderText('Search currencies…'), {
      target: { value: 'EUR' },
    })
    fireEvent.mouseDown(screen.getByRole('option', { name: /^EUR/ }))
    await waitFor(() => expect(previews().length).toBe(1))
    expect(previews()[0]?.body.currency).toBe('EUR')
    await waitFor(() => expect(w.getByRole('button', { name: /^Budget currency/ })).toBeTruthy())
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    await waitFor(() => expect(importButton(w).disabled).toBe(false))
    fireEvent.click(importButton(w))
    await waitFor(() => expect(requests.at(-1)?.url).toMatch(/\/start$/))
    expect(requests.at(-1)?.body.currency).toBe('EUR')
  })

  it('sends the starting currency too, if it is left alone', async () => {
    preview = actualPreview({ currency_asked: true, currency: 'USD' })
    const w = await open()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    fireEvent.click(importButton(w))
    await waitFor(() => expect(requests.at(-1)?.url).toMatch(/\/start$/))
    expect(requests.at(-1)?.body.currency).toBe('USD')
  })

  it('isn’t asked when the budget states one', async () => {
    const w = await open()
    expect(w.queryByText('Budget currency')).toBeNull()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    fireEvent.click(importButton(w))
    await waitFor(() => expect(requests.at(-1)?.url).toMatch(/\/start$/))
    expect(requests.at(-1)?.body).not.toHaveProperty('currency')
  })
})

describe('the notes', () => {
  const FOUR = [
    'This Actual server is reached over plain http.',
    "Ready to Assign here won't match Actual's To Budget.",
    'Carryover is on for ACME Rent in Actual.',
    'Actual is holding 5.00 for later in 2026-11.',
    'A split on 2026-10-01 says 5.00.',
  ]

  it('shows three notes, says how many more, and blockers don’t count', async () => {
    preview = actualPreview({ warnings: FOUR })
    const w = await open()
    expect(w.getByText(/3 still need a type/)).toBeTruthy()
    for (const line of FOUR.slice(0, 3)) expect(w.getByText(line)).toBeTruthy()
    expect(w.queryByText(FOUR[3]!)).toBeNull()
    fireEvent.click(w.getByRole('button', { name: 'Show 2 more notes' }))
    expect(w.getByText(FOUR[4]!)).toBeTruthy()
    fireEvent.click(w.getByRole('button', { name: 'Show fewer notes' }))
    expect(w.queryByText(FOUR[4]!)).toBeNull()
  })

  it('one more note reads as one', async () => {
    preview = actualPreview({ warnings: FOUR.slice(0, 4) })
    const w = await open()
    expect(w.getByRole('button', { name: 'Show 1 more note' })).toBeTruthy()
  })

  it('shows no such link for three or fewer', async () => {
    preview = actualPreview({ warnings: FOUR.slice(0, 3) })
    const w = await open()
    expect(w.queryByRole('button', { name: /more note/ })).toBeNull()
  })

  it('collapses many asks to one line', async () => {
    const asks = ['Rent', 'Food', 'Fuel', 'Gym'].map((name, i) => ({
      id: `c${i}`,
      label: name,
      name,
      hidden: false,
      link: null,
      chosen: false,
      ask: true,
      options: [],
      note: '',
    }))
    preview = actualPreview({
      category_groups: [{ id: 'g', name: 'Bills', group: null, categories: asks }],
    })
    const w = await open()
    expect(
      w.getByText(
        '4 Actual categories match more than one of yours. Pick one for each in Categories.',
      ),
    ).toBeTruthy()
    expect(w.queryByText(/Actual’s Rent matches/)).toBeNull()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    expect(importButton(w).disabled).toBe(true)
  })

  it('says an import would be refused before anything is settled, and holds it', async () => {
    blocked = 'The YNAB sync is on. Turn it off in Data sync before importing from Actual.'
    const w = await open()
    const note = await w.findByText(blocked)
    expect(note.closest('li')?.className).toContain('ynr-note--bad')
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    expect(importButton(w).disabled).toBe(true)
    expect(importButton(w).getAttribute('aria-describedby')).toBe(note.closest('li')!.id)
  })

  it('a refusal at the start stays in the window, as an alert, with the choices kept', async () => {
    startRefusal = 'An import or sync with YNAB is running; start this import when it finishes.'
    const w = await open()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    fireEvent.click(importButton(w))
    await waitFor(() => expect(w.getByRole('alert').textContent).toBe(startRefusal))
    expect(onClose).not.toHaveBeenCalled()
    expect(typeOf(w, 'ACME Checking').textContent).toBe('Cash')
  })

  it('says a budget that could not be read, and holds the import', async () => {
    const failed = run({
      provider: 'actual_budget',
      plan_id: 'b1',
      status: 'failed',
      preview: null,
      error: 'The Actual server refused the encryption password.',
    })
    runs.set(failed.id, failed)
    settle = () => false
    render(
      <YnabReview
        app={ACTUAL_APP}
        runId={failed.id}
        planLabel="ACME Household"
        onClose={onClose}
        onStarted={onStarted}
      />,
    )
    expect(
      await screen.findByText('The Actual server refused the encryption password.'),
    ).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })
})

describe('Actual’s words', () => {
  it('in the reading state, the options and the tabs', async () => {
    const w = await open()
    expect(w.getByRole('tablist', { name: 'What the budget holds' })).toBeTruthy()
    fireEvent.click(w.getByRole('button', { name: 'After import' }))
    expect(screen.getByRole('option', { name: 'Sync from Actual only' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Sync to Actual only' })).toBeTruthy()
    fireEvent.click(w.getByRole('button', { name: 'After import' }))
    fireEvent.click(w.getByRole('tab', { name: /Categories/ }))
    expect(w.getByRole('columnheader', { name: 'Category in Actual' })).toBeTruthy()
    expect(document.body.textContent).not.toContain('YNAB')
  })
})

describe('while a re-check runs', () => {
  it('Import shows it is busy, and other choices wait for the POST too', async () => {
    let release = () => undefined as void
    holdPost = new Promise((r) => {
      release = r
    })
    preview = actualPreview({
      accounts: [
        account('a1', 'ACME Checking', 'cash', {
          candidates: [{ id: 'x', name: 'Mine', type: 'cash' }],
        }),
      ],
    })
    const w = await open()
    pick(w, 'ACME Checking', 'Credit')
    await waitFor(() => expect(previews().length).toBe(1), { timeout: 2000 })
    const where = w.getByRole('button', { name: 'Where ACME Checking goes' }) as HTMLButtonElement
    await waitFor(() => expect(where.disabled).toBe(true))
    expect(importButton(w).getAttribute('aria-busy')).toBe('true')
    expect(typeOf(w, 'ACME Checking').disabled).toBe(false)
    release()
    await waitFor(() => expect(where.disabled).toBe(false), { timeout: 4000 })
  })

  it('a refused type re-check says so, doesn’t loop, and leaves Import usable', async () => {
    refuseRecheck = 'ACME Checking can’t be credit.'
    const w = await open()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    pick(w, 'ACME Checking', 'Credit')
    expect(await w.findByText(refuseRecheck, undefined, { timeout: 2000 })).toBeTruthy()
    await act(async () => {
      await new Promise((r) => setTimeout(r, 900))
    })
    expect(previews().length).toBe(1)
    expect(importButton(w).disabled).toBe(false)
  })
})

describe('when it can’t go on', () => {
  it('a failed read links to the connection, closing the window', async () => {
    const onConnect = vi.fn()
    const failed = run({
      provider: 'actual_budget',
      plan_id: 'b1',
      status: 'failed',
      preview: null,
      error: 'This budget is encrypted, and its encryption password is missing or wrong.',
    })
    runs.set(failed.id, failed)
    render(
      <YnabReview
        app={ACTUAL_APP}
        runId={failed.id}
        planLabel="ACME Vault"
        onClose={onClose}
        onStarted={onStarted}
        onConnect={onConnect}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Open the connection' }))
    expect(onConnect).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('a refusal the block now states says it once, and focus isn’t left on a dead button', async () => {
    const sentence = 'The YNAB sync is on. Turn it off in Data sync before importing from Actual.'
    startRefusal = sentence
    const w = await open()
    fireEvent.click(w.getByRole('button', { name: 'Accept suggestions' }))
    blocked = sentence
    importButton(w).focus()
    fireEvent.click(importButton(w))
    await waitFor(() => expect(importButton(w).disabled).toBe(true))
    expect(w.getAllByText(sentence)).toHaveLength(1)
    expect(w.queryByRole('alert')).toBeNull()
    await waitFor(() => expect(document.activeElement?.id).toBe('ui-page-tab-accounts'))
  })
})

describe('on a phone', () => {
  const asPhone = () =>
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }))

  it('shows one note before folding, and blockers stay', async () => {
    asPhone()
    preview = actualPreview({ warnings: ['Note one.', 'Note two.', 'Note three.'] })
    const w = await open()
    expect(w.getByText(/3 still need a type/)).toBeTruthy()
    expect(w.getByText('Note one.')).toBeTruthy()
    expect(w.queryByText('Note two.')).toBeNull()
    expect(w.getByRole('button', { name: 'Show 2 more notes' })).toBeTruthy()
  })

  it('lets the list flow into the sheet', async () => {
    const w = await open()
    expect(w.getByRole('tabpanel').className).toContain('ynr-pane--flow')
  })
})

describe('YNAB’s review', () => {
  it('reads no migration status, even when every read is refreshed, and keeps its pane', async () => {
    const first = run({ preview: { ...PREVIEW } })
    runs.set(first.id, first)
    render(
      <YnabReview runId={first.id} planLabel="Household" onClose={onClose} onStarted={onStarted} />,
    )
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('cell', { name: 'Checking' })
    expect(within(dialog).getByRole('tabpanel').className).toBe('ynr-pane')
    invalidateQueryCache(() => true)
    await new Promise((r) => setTimeout(r, 20))
    const asked = authFetch.mock.calls.map(([url]) => String(url))
    expect(asked.filter((u) => /\/migration(\?|$)/.test(u))).toEqual([])
  })
})
