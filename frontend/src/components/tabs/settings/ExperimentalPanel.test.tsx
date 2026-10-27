// @vitest-environment jsdom
// Settings → Developer → Experimental: generated from GET /api/feature-flags — a row per
// flag, its state note, toggle → PUT, Reset → PUT null; env/demo make it read-only; empty
// and error states.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import type { FeatureFlag } from '../../../hooks/useFlag'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../api/client'
import { ExperimentalPanel } from './ExperimentalPanel'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const toast = vi.fn()

function flag(over: Partial<FeatureFlag> = {}): FeatureFlag {
  return {
    name: 'new_budget_rollover',
    description: 'Carries overspending into next month the new way.',
    kind: 'release',
    default: false,
    enabled: false,
    source: 'default',
    stored: null,
    env_var: 'ONW_FLAG_NEW_BUDGET_ROLLOVER',
    owner: 'owner',
    added: '2026-10-25',
    remove_by: '2026-12-31',
    ...over,
  }
}

function json(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
}

function serve(flags: FeatureFlag[], locked = false) {
  mockAuthFetch.mockImplementation((path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const enabled = JSON.parse(String(init.body)).enabled as boolean | null
      return json(flag({ enabled: enabled ?? false, stored: enabled, source: 'stored' }))
    }
    if (path === '/api/feature-flags') return json({ flags, locked })
    return json({}, 404)
  })
}

beforeEach(() => {
  clearQueryCache()
  mockAuthFetch.mockReset()
  toast.mockReset()
})
afterEach(cleanup)

it('renders one row per declared flag with its description and default state', async () => {
  serve([flag(), flag({ name: 'safe_mode', kind: 'kill_switch', default: true, enabled: true })])
  render(<ExperimentalPanel toast={toast} />)
  expect(await screen.findByText('New budget rollover')).toBeTruthy()
  expect(screen.getAllByText(/Carries overspending/)).toHaveLength(2)
  expect(screen.getByText(/Default \(off\)\./)).toBeTruthy()
  expect(screen.getByText('Kill switch')).toBeTruthy()
  expect(screen.queryByText('Reset')).toBeNull()
})

it('a toggle PUTs the new value', async () => {
  serve([flag()])
  render(<ExperimentalPanel toast={toast} />)
  fireEvent.click(await screen.findByRole('switch', { name: 'New budget rollover' }))
  await waitFor(() =>
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/feature-flags/new_budget_rollover',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }),
    ),
  )
  expect(toast).toHaveBeenCalledWith('New budget rollover turned on')
})

it('a changed flag shows Changed and a Reset that PUTs null', async () => {
  serve([flag({ enabled: true, stored: true, source: 'stored' })])
  render(<ExperimentalPanel toast={toast} />)
  expect(await screen.findByText('Changed')).toBeTruthy()
  expect(screen.getByText(/the default is off/)).toBeTruthy()
  fireEvent.click(screen.getByText('Reset'))
  await waitFor(() =>
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/feature-flags/new_budget_rollover',
      expect.objectContaining({ body: JSON.stringify({ enabled: null }) }),
    ),
  )
})

it('an environment override makes the switch read-only and says why', async () => {
  serve([flag({ enabled: true, source: 'env' })])
  render(<ExperimentalPanel toast={toast} />)
  const sw = await screen.findByRole('switch', { name: 'New budget rollover' })
  expect((sw as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(/ONW_FLAG_NEW_BUDGET_ROLLOVER/)).toBeTruthy()
})

it('the public demo is read-only at the defaults', async () => {
  serve([flag({ source: 'demo' })], true)
  render(<ExperimentalPanel toast={toast} />)
  const sw = await screen.findByRole('switch', { name: 'New budget rollover' })
  expect((sw as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(/public demo/)).toBeTruthy()
})

it('no flags → an empty state, not a blank panel', async () => {
  serve([])
  render(<ExperimentalPanel toast={toast} />)
  expect(await screen.findByText('No experimental features right now')).toBeTruthy()
})

it('a failed load offers a retry', async () => {
  mockAuthFetch.mockImplementation(() => json({}, 500))
  render(<ExperimentalPanel toast={toast} />)
  expect(await screen.findByText('Could not load experimental features')).toBeTruthy()
})
