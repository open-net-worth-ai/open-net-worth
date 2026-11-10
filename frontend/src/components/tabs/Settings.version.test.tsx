// The version sits at the foot of Settings, read from the server this app is connected to.
// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../state/queryCache'
import { Settings } from './Settings'

const mocks = vi.hoisted(() => ({
  health: { ok: true, json: async () => ({ status: 'ok', version: '9.9.9' }) } as {
    ok: boolean
    json: () => Promise<unknown>
  },
  mobile: false,
}))

vi.mock('../../api/client', () => ({
  authFetch: vi.fn((url: string) =>
    Promise.resolve(
      url === '/api/health'
        ? { status: 200, ...mocks.health }
        : { ok: false, status: 500, json: async () => ({}) },
    ),
  ),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../hooks/useIsMobile', () => ({ useIsMobile: () => mocks.mobile }))

const version = () => document.body.querySelector('.rd-set-version')

beforeEach(() => {
  clearQueryCache()
  mocks.mobile = false
  mocks.health = { ok: true, json: async () => ({ status: 'ok', version: '9.9.9' }) }
})

afterEach(cleanup)

it('wide: the rail ends with the version the server reports', async () => {
  render(<Settings />)
  await waitFor(() => expect(version()?.textContent).toBe('Version 9.9.9'))
  expect(version()?.closest('.rd-set-nav')?.lastElementChild).toBe(version())
})

it('phone: the menu ends with the version', async () => {
  mocks.mobile = true
  render(<Settings />)
  await waitFor(() => expect(version()?.textContent).toBe('Version 9.9.9'))
  expect(version()?.closest('.rd-set-menu')?.lastElementChild).toBe(version())
})

it('shows nothing when the server does not answer', async () => {
  mocks.health = { ok: false, json: async () => ({}) }
  render(<Settings />)
  await waitFor(() => expect(document.body.querySelector('.rd-set-nav')).not.toBeNull())
  await new Promise((resolve) => setTimeout(resolve, 20))
  expect(version()).toBeNull()
})

it('shows nothing when the answer has no version', async () => {
  mocks.health = { ok: true, json: async () => ({ status: 'ok' }) }
  render(<Settings />)
  await new Promise((resolve) => setTimeout(resolve, 20))
  expect(version()).toBeNull()
})
