// @vitest-environment jsdom
//
// The Imports & sync page: connection state, plan -> preview -> import with the live strip, the
// foreign-book confirmation, and the run history (kind filter, archive, show archived). The
// server is a small stateful fake of /api/ynab/* and /api/imports/runs.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportsTab } from './ImportsTab'
import { clearQueryCache } from '../../../state/queryCache'
import { PLANS, PREVIEW, importRun, run, syncRun, syncStatus } from './importsFixtures'
import type { RunRecord } from './runs'

const authFetch = vi.hoisted(() => vi.fn())
const navigate = vi.hoisted(() => vi.fn())
let search: Record<string, string> = {}
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { search } }),
}))

let runs: RunRecord[]
let hasToken: boolean
let preview = PREVIEW
let posts: { url: string; body: unknown }[]

const ok = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

function summaries(archived: boolean) {
  return runs.filter((r) => archived || !r.archived_at).reverse()
}

beforeEach(() => {
  search = {}
  runs = []
  hasToken = true
  preview = PREVIEW
  posts = []
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const path = url.split('?')[0] ?? ''
    if (init?.method === 'POST') {
      const body = init.body ? JSON.parse(init.body as string) : null
      posts.push({ url, body })
      const archive = /runs\/(\w+)\/(un)?archive$/.exec(url)
      if (archive) {
        const r = runs.find((x) => x.id === archive[1])!
        r.archived_at = archive[2] ? null : '2026-10-13T00:00:00+00:00'
        return ok(r)
      }
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
          : run({
              kind,
              preview,
              status: 'running',
              finished_at: null,
              phase: 'booking transactions',
              progress: { done: 2, total: 4 },
            })
      runs.push(started)
      return ok(started, 202)
    }
    if (path === '/api/ynab/status')
      return ok({ has_access_token: hasToken, budget_name: 'Household' })
    if (path === '/api/ynab/budgets') return ok(PLANS)
    if (path === '/api/ynab/sync') return ok(syncStatus({ conflicts: [], token_saved: hasToken }))
    if (path === '/api/imports/runs') return ok({ runs: summaries(url.includes('archived=true')) })
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

async function pickPlan() {
  const select = (await screen.findByLabelText(/YNAB plan/)) as HTMLSelectElement
  await waitFor(() => expect(select.options.length).toBeGreaterThan(1))
  fireEvent.change(select, { target: { value: 'p1' } })
}

describe('ImportsTab', () => {
  it('asks for the token on the connector, and never for the token itself', async () => {
    hasToken = false
    render(<ImportsTab />)
    expect(await screen.findByText('Save your YNAB access token first')).toBeTruthy()
    expect(screen.getByText('No token')).toBeTruthy()
    expect(screen.queryByLabelText(/access token/i)).toBeNull()
    expect(authFetch).not.toHaveBeenCalledWith('/api/ynab/budgets')
    fireEvent.click(screen.getAllByText('Open connector')[0]!)
    expect(navigate).toHaveBeenCalledWith({ to: '/connectors' })
  })

  it('shows the saved token and the plan name', async () => {
    render(<ImportsTab />)
    expect(await screen.findByText('Token saved')).toBeTruthy()
    expect(screen.getByText('Uses the token saved on the YNAB connector.')).toBeTruthy()
  })

  it('needs a plan before it will preview, and a preview before it will import', async () => {
    render(<ImportsTab />)
    const previewBtn = (await screen.findByRole('button', { name: 'Preview' })) as HTMLButtonElement
    expect(previewBtn.disabled).toBe(true)
    await pickPlan()
    expect(previewBtn.disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('previews a plan, then imports it with a live strip', async () => {
    render(<ImportsTab />)
    await pickPlan()
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))

    expect(await screen.findByText(/Household \(USD\) will bring across/)).toBeTruthy()
    expect(screen.getByText(/no matching row in the other account/)).toBeTruthy()
    expect(screen.getByText(/base currency will change from SGD to USD/)).toBeTruthy()
    expect(screen.getByText(/starter items that will be removed: Vacation/)).toBeTruthy()
    expect(posts[0]).toEqual({
      url: '/api/ynab/migration/preview',
      body: { plan_id: 'p1', confirm_nonempty: false },
    })

    const importBtn = screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement
    await waitFor(() => expect(importBtn.disabled).toBe(false))
    fireEvent.click(importBtn)
    expect(await screen.findByText(/Working: booking transactions/)).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Run progress' })).toBeTruthy()
    fireEvent.click(screen.getByText('View live report'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/imports',
      search: { provider: 'ynab', run: runs.at(-1)?.id },
    })
  })

  it('confirms before importing into a book that holds other accounts', async () => {
    preview = { ...PREVIEW, book_state: 'foreign' }
    runs = [run({ preview })]
    render(<ImportsTab />)
    await pickPlan()
    const importBtn = screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement
    await waitFor(() => expect(importBtn.disabled).toBe(false))
    fireEvent.click(importBtn)
    expect(await screen.findByText('Import alongside your existing accounts?')).toBeTruthy()
    expect(posts).toEqual([])
    fireEvent.click(screen.getByText('Import anyway'))
    await waitFor(() =>
      expect(posts[0]).toEqual({
        url: '/api/ynab/migration/start',
        body: { plan_id: 'p1', confirm_nonempty: true },
      }),
    )
  })

  it('blocks the import when the currency cannot change', async () => {
    preview = { ...PREVIEW, currency_change: false, currency_blocked: true, money_data: ['assets'] }
    runs = [run({ preview })]
    render(<ImportsTab />)
    await pickPlan()
    expect(await screen.findByText(/already holds assets/)).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('archiving from the report then going back shows the updated list', async () => {
    runs = [importRun({ outcome: 'finished' })]
    search = { provider: 'ynab', run: runs[0]?.id ?? '' }
    const { rerender } = render(<ImportsTab />)
    fireEvent.click(await screen.findByText('Archive'))
    await waitFor(() => expect(runs[0]?.archived_at).toBeTruthy())

    search = { provider: 'ynab' }
    rerender(<ImportsTab />)
    expect(await screen.findByText('No runs yet')).toBeTruthy()
    expect(screen.queryByText('finished')).toBeNull()
  })

  it('opens the whole report from a history row', async () => {
    runs = [importRun()]
    render(<ImportsTab />)
    fireEvent.click(await screen.findByText('Open'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/imports',
      search: { provider: 'ynab', run: runs[0]?.id },
    })
  })

  it('shows the report when the URL names a run', async () => {
    runs = [importRun()]
    search = { provider: 'ynab', run: runs[0]?.id ?? '' }
    render(<ImportsTab />)
    expect(await screen.findByText('Verification')).toBeTruthy()
    fireEvent.click(screen.getByText('‹ Runs'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/imports',
      search: { provider: 'ynab', run: undefined },
    })
  })
})

describe('Run history', () => {
  it('says what to do when there are no runs', async () => {
    render(<ImportsTab />)
    expect(await screen.findByText('No runs yet')).toBeTruthy()
    expect(screen.getByText('Preview a plan to see what would change.')).toBeTruthy()
  })

  it('lists newest first and filters by kind', async () => {
    runs = [run({ outcome: 'a preview' }), importRun({ outcome: 'an import' })]
    render(<ImportsTab />)
    await screen.findByText('an import')
    const rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('an import')).toBeTruthy()
    expect(within(rows[1]!).getByText('a preview')).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: 'Preview' }))
    expect(screen.queryByText('an import')).toBeNull()
    expect(screen.getByText('a preview')).toBeTruthy()
  })

  it('lists a scheduled sync run and filters to sync runs', async () => {
    runs = [
      importRun({ outcome: 'an import' }),
      syncRun({ trigger: 'scheduled', outcome: 'a sync' }),
    ]
    render(<ImportsTab />)
    await screen.findByText('a sync')
    expect(screen.getByText('Scheduled')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Sync' }))
    expect(screen.queryByText('an import')).toBeNull()
    expect(screen.getByText('a sync')).toBeTruthy()
  })

  it('shows the Sync panel on the page', async () => {
    render(<ImportsTab />)
    expect(await screen.findByLabelText('Schedule')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeTruthy()
  })

  it('hides archived runs until asked, then mutes them with a tag', async () => {
    runs = [importRun({ outcome: 'kept' }), importRun({ outcome: 'hidden', archived_at: 'x' })]
    render(<ImportsTab />)
    await screen.findByText('kept')
    expect(screen.queryByText('hidden')).toBeNull()

    fireEvent.click(screen.getByLabelText('Show archived'))
    const row = (await screen.findByText('hidden')).closest('tr') as HTMLElement
    expect(within(row).getByText('Archived')).toBeTruthy()
    expect(row.className).toContain('imp-table__row--archived')
  })

  it('archives a finished run from its menu and can bring it back', async () => {
    runs = [importRun({ outcome: 'finished' })]
    render(<ImportsTab />)
    await screen.findByText('finished')
    fireEvent.click(screen.getByRole('button', { name: /More actions/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Archive' }))
    await waitFor(() => expect(posts[0]?.url).toBe(`/api/imports/runs/${runs[0]?.id}/archive`))
    await waitFor(() => expect(screen.queryByText('finished')).toBeNull())

    fireEvent.click(screen.getByLabelText('Show archived'))
    await screen.findByText('finished')
    fireEvent.click(screen.getByRole('button', { name: /More actions/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Unarchive' }))
    await waitFor(() => expect(posts[1]?.url).toContain('/unarchive'))
  })

  it('a running run cannot be archived and shows its phase', async () => {
    runs = [importRun({ status: 'running', finished_at: null, phase: 'booking transactions' })]
    render(<ImportsTab />)
    fireEvent.click(await screen.findByRole('button', { name: /More actions/ }))
    expect((screen.getByRole('menuitem', { name: 'Archive' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    expect(screen.getAllByText(/booking transactions/).length).toBeGreaterThan(0)
  })
})
