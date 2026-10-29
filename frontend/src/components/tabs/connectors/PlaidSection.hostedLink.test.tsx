// @vitest-environment jsdom
//
// Plaid Hosted Link ends on Plaid's own "you can close this window" page and never shows the
// user a public token. After "Link bank" the form must finish the link ON ITS OWN — polling
// POST /api/plaid/link/complete — and re-fetch the Accounts list once the bank is linked.

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PlaidSection } from './PlaidSection'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const STATUS = {
  configured: false,
  enabled: true,
  environment: 'production',
  has_secret: true,
  client_id: 'cid',
  has_access_token: false,
  last_synced_at: null,
  last_status: 'never_run',
  last_error: null,
}

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

let completeStatus = 'pending'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.spyOn(window, 'open').mockReturnValue(null)
  completeStatus = 'pending'
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url === '/api/plaid/status') return okJson(STATUS)
    if (url === '/api/plaid/link') return okJson({ link: 'https://hosted.plaid.com/link/1' })
    if (url === '/api/plaid/link/complete') return okJson({ status: completeStatus })
    if (url === '/api/connectors/plaid/accounts')
      return okJson({ status: 'ok', provider_label: 'Plaid', accounts: [] })
    return okJson([])
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  mockAuthFetch.mockReset()
})

const calls = (url: string) => mockAuthFetch.mock.calls.filter(([u]) => u === url).length

it('completes the link by itself once Plaid says the session finished', async () => {
  render(<PlaidSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Link bank' }))
  expect(await screen.findByText('Waiting for you to finish at Plaid…')).toBeTruthy()
  expect(window.open).toHaveBeenCalledWith(
    'https://hosted.plaid.com/link/1',
    '_blank',
    'noopener,noreferrer',
  )
  expect(screen.queryByLabelText('Public token')).toBeNull()

  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(calls('/api/plaid/link/complete')).toBe(1)
  expect(screen.getByText('Waiting for you to finish at Plaid…')).toBeTruthy()

  const accountsBefore = calls('/api/connectors/plaid/accounts')
  completeStatus = 'linked'
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(await screen.findByText(/Bank linked/)).toBeTruthy()
  expect(screen.queryByText('Waiting for you to finish at Plaid…')).toBeNull()
  expect(calls('/api/connectors/plaid/accounts')).toBeGreaterThan(accountsBefore)

  await act(() => vi.advanceTimersByTimeAsync(9000))
  expect(calls('/api/plaid/link/complete')).toBe(2)
})

it('Check now reports a session that has not finished yet', async () => {
  render(<PlaidSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Link bank' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Check now' }))
  expect(await screen.findByText(/Plaid has not finished yet/)).toBeTruthy()
})
