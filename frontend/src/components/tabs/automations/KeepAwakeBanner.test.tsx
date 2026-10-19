// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { DesktopBridge, DesktopSettings } from '../../../../../desktop/src/contracts'
import { clearQueryCache } from '../../../state/queryCache'
import { KeepAwakeBanner } from './KeepAwakeBanner'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

beforeEach(() => {
  clearQueryCache()
  authFetch.mockReset()
  delete window.onwDesktop
})
afterEach(cleanup)

it('in the browser, shows the server switch only where the server can hold the machine awake', async () => {
  authFetch.mockResolvedValue(json({ supported: false, enabled: false, active: false }))
  const { container } = render(<KeepAwakeBanner />)
  await waitFor(() => expect(authFetch).toHaveBeenCalled())
  expect(container.querySelector('.a3-awake')).toBeNull()
})

it('saves the server switch through PUT /api/config/keep-awake', async () => {
  authFetch.mockImplementation(async (_url: string, init?: RequestInit) =>
    init?.method === 'PUT'
      ? json({ supported: true, enabled: true, active: true })
      : json({ supported: true, enabled: false, active: false }),
  )
  render(<KeepAwakeBanner />)
  const toggle = await screen.findByRole('switch', { name: 'Keep computer awake' })
  expect(screen.getByText('Automations only run while the server is awake.')).toBeTruthy()
  fireEvent.click(toggle)
  await waitFor(() => expect((toggle as HTMLInputElement).checked).toBe(true))
  expect(authFetch).toHaveBeenCalledWith(
    '/api/config/keep-awake',
    expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true }) }),
  )
})

it('inside the desktop app, uses the app switch and never asks the server', async () => {
  const settings = { keepAwake: true } as DesktopSettings
  window.onwDesktop = { version: 1, getSettings: async () => settings } as unknown as DesktopBridge
  render(<KeepAwakeBanner />)
  const toggle = await screen.findByRole('switch', { name: 'Keep computer awake' })
  expect((toggle as HTMLInputElement).checked).toBe(true)
  expect(screen.getByText('Automations only run while this computer is awake.')).toBeTruthy()
  expect(authFetch).not.toHaveBeenCalled()
})
