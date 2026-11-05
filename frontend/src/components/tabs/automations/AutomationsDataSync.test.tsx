// @vitest-environment jsdom
//
// The Data sync tab follows either flag, and Actual only where it can run at all: behind the
// gateway it isn't listed, and a link to its page falls back to the list.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../state/queryCache'
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
let restore: () => void
const reply = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })

const tab = () => screen.queryByRole('tab', { name: /data sync/i })

beforeEach(() => {
  available = true
  route.search = {}
  api.listAutomations.mockResolvedValue({ automations: [], stats: {} })
  api.getAutomationOptions.mockResolvedValue({})
  authFetch.mockImplementation((url: string) => {
    const path = url.split('?')[0] ?? ''
    if (path === '/api/actual-budget/status') return reply({ available, configured: false })
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

describe('Data sync follows either flag', () => {
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
})
