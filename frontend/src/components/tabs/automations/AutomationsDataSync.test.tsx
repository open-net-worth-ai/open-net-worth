// @vitest-environment jsdom
//
// The Data Sync tab follows either flag, and Actual only where it can run at all: behind the
// gateway it isn't listed, and a link to its page falls back to the list.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache, invalidateQueryCache } from '../../../state/queryCache'
import { overrideFlagsForTests } from '../../../hooks/useFlag'
import { syncStatus } from '../imports/importsFixtures'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const api = vi.hoisted(() => ({
  listAutomations: vi.fn(),
  getAutomationOptions: vi.fn(),
}))
vi.mock('../../../api/automations', async (load) => ({ ...(await load()), ...api }))

const route = vi.hoisted(() => ({ search: {} as Record<string, string> }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ search: route.search }),
}))

import { Automations } from '../Automations'

let available: boolean
let statusFails: boolean
let actualSyncFails: boolean
let restore: () => void
const reply = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })

const tab = () => screen.queryByRole('tab', { name: /data sync/i })

beforeEach(() => {
  available = true
  statusFails = false
  actualSyncFails = false
  route.search = {}
  api.listAutomations.mockResolvedValue({ automations: [], stats: {} })
  api.getAutomationOptions.mockResolvedValue({})
  authFetch.mockImplementation((url: string) => {
    const path = url.split('?')[0] ?? ''
    if (path === '/api/actual-budget/status')
      return statusFails
        ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
        : reply({ available, configured: false })
    if (path === '/api/actual-budget/sync' && actualSyncFails)
      return Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: 'Down' }),
      })
    if (path.endsWith('/sync'))
      return reply(
        syncStatus({
          enabled: false,
          token_saved: false,
          plan_id: null,
          plan_name: null,
          conflicts: [],
          last_run: null,
        }),
      )
    if (path === '/api/imports/runs') return reply({ runs: [] })
    if (path === '/api/connectors') return reply({ connectors: [] })
    return reply({})
  })
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  restore?.()
  vi.clearAllMocks()
})

const flags = (ynab_import_and_sync: boolean, actual_budget_sync: boolean) => {
  restore = overrideFlagsForTests({ ynab_import_and_sync, actual_budget_sync })
}

describe('Data Sync follows either flag', () => {
  it('neither flag: no tab', async () => {
    flags(false, false)
    render(<Automations />)
    await waitFor(() => expect(api.listAutomations).toHaveBeenCalled())
    expect(tab()).toBeNull()
  })

  it('only Actual’s flag: the tab, with Actual alone', async () => {
    flags(false, true)
    route.search = { view: 'sync' }
    render(<Automations />)
    expect(await screen.findByRole('button', { name: 'Set up Actual' })).toBeTruthy()
    expect(tab()).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set up YNAB' })).toBeNull()
  })

  it('only YNAB’s flag: the tab, with YNAB alone', async () => {
    flags(true, false)
    route.search = { view: 'sync' }
    render(<Automations />)
    expect(await screen.findByRole('button', { name: 'Set up YNAB' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set up Actual' })).toBeNull()
  })

  it('both flags: two tiles, YNAB first', async () => {
    flags(true, true)
    route.search = { view: 'sync' }
    render(<Automations />)
    await screen.findByRole('button', { name: 'Set up Actual' })
    const names = screen
      .getAllByRole('button', { name: /^Set up / })
      .map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Set up YNAB', 'Set up Actual'])
  })

  it('behind the gateway Actual isn’t listed, and its link falls back to the list', async () => {
    flags(true, true)
    available = false
    route.search = { view: 'sync', sync: 'actual_budget' }
    render(<Automations />)
    expect(await screen.findByRole('button', { name: 'Set up YNAB' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Set up Actual' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Actual' })).toBeNull()
  })

  it('a link to Actual’s page opens it', async () => {
    flags(false, true)
    route.search = { sync: 'actual_budget' }
    render(<Automations />)
    expect(await screen.findByRole('heading', { name: 'Actual' })).toBeTruthy()
  })

  it('a failed status read doesn’t hide Actual: it says it couldn’t load', async () => {
    flags(true, true)
    statusFails = true
    actualSyncFails = true
    route.search = { view: 'sync' }
    render(<Automations />)
    expect(await screen.findByText('The Actual sync couldn’t load')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Set up YNAB' })).toBeTruthy()
  })

  it('an app that’s off asks nothing, even when every read is refreshed', async () => {
    flags(true, false)
    route.search = { view: 'sync' }
    render(<Automations />)
    await screen.findByRole('button', { name: 'Set up YNAB' })
    invalidateQueryCache(() => true)
    await new Promise((r) => setTimeout(r, 20))
    const asked = authFetch.mock.calls.map(([url]) => String(url))
    expect(asked.filter((u) => u.includes('actual'))).toEqual([])
    expect(asked.filter((u) => u.includes('provider=actual_budget'))).toEqual([])
  })

  it('behind the gateway only the status is read', async () => {
    flags(false, true)
    available = false
    render(<Automations />)
    await waitFor(() =>
      expect(authFetch.mock.calls.some(([u]) => u === '/api/actual-budget/status')).toBe(true),
    )
    await new Promise((r) => setTimeout(r, 20))
    invalidateQueryCache(() => true)
    await new Promise((r) => setTimeout(r, 20))
    const asked = authFetch.mock.calls.map(([url]) => String(url))
    expect(asked.filter((u) => u.includes('/api/actual-budget/sync'))).toEqual([])
    expect(asked.filter((u) => u.includes('provider=actual_budget'))).toEqual([])
  })
})
