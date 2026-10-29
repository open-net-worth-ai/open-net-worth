// @vitest-environment jsdom
//
// Plaid Hosted Link ends on Plaid's own "you can close this window" page and never shows the
// user a public token. After "Link bank" the form must finish the link ON ITS OWN — polling
// POST /api/plaid/link/complete — and re-fetch the Accounts list once the bank is linked.

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
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
  expect(await screen.findByText(/Waiting for you to finish at Plaid/)).toBeTruthy()
  expect(window.open).toHaveBeenCalledWith(
    'https://hosted.plaid.com/link/1',
    '_blank',
    'noopener,noreferrer',
  )
  expect(screen.queryByLabelText('Public token')).toBeNull()

  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(calls('/api/plaid/link/complete')).toBe(1)
  expect(screen.getByText(/Waiting for you to finish at Plaid/)).toBeTruthy()

  const accountsBefore = calls('/api/connectors/plaid/accounts')
  completeStatus = 'linked'
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(await screen.findByText(/Bank linked/)).toBeTruthy()
  expect(screen.queryByText(/Waiting for you to finish at Plaid/)).toBeNull()
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

it('Link bank saves unsaved keys first, then opens Plaid — no "Save or Link first?"', async () => {
  const unsaved = { ...STATUS, has_secret: false, client_id: '' }
  const base = mockAuthFetch.getMockImplementation()!
  const order: string[] = []
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/plaid/config' || url === '/api/plaid/link') order.push(url)
    if (url === '/api/plaid/status') return okJson(unsaved)
    return base(url, init)
  })
  render(<PlaidSection />)
  const linkBtn = await screen.findByRole('button', { name: 'Link bank' })
  expect(screen.getByText('Enter your Client ID and Secret first.')).toBeTruthy()
  fireEvent.change(screen.getByLabelText(/^Client ID/), { target: { value: 'cid' } })
  fireEvent.change(screen.getByLabelText(/^Secret/), { target: { value: 'sec' } })
  fireEvent.click(linkBtn)
  await screen.findByText(/Waiting for you to finish at Plaid/)
  expect(order).toEqual(['/api/plaid/config', '/api/plaid/link'])
})

function failComplete(status: number) {
  const base = mockAuthFetch.getMockImplementation()!
  mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) =>
    url === '/api/plaid/link/complete'
      ? ({ ok: false, status, json: async () => ({ detail: 'Method Not Allowed' }) } as Response)
      : base(url, init),
  )
}

it('an out-of-date server says reload instead of a raw "Method Not Allowed"', async () => {
  failComplete(405)
  render(<PlaidSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Link bank' }))
  await screen.findByText(/Waiting for you to finish at Plaid/)
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(await screen.findByText(/was updated while this page was open\. Reload/)).toBeTruthy()
  expect(screen.queryByText('Method Not Allowed')).toBeNull()
  expect(screen.queryByText(/Waiting for you to finish at Plaid/)).toBeNull()
})

it('a passing server error keeps checking, and the link still completes', async () => {
  failComplete(502)
  render(<PlaidSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Link bank' }))
  await screen.findByText(/Waiting for you to finish at Plaid/)
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(screen.getByText(/Waiting for you to finish at Plaid/)).toBeTruthy()

  mockAuthFetch.mockImplementation(async (url: string) => {
    if (url === '/api/plaid/link/complete') return okJson({ status: 'linked' })
    if (url === '/api/connectors/plaid/accounts')
      return okJson({ status: 'ok', provider_label: 'Plaid', accounts: [] })
    return okJson(url === '/api/plaid/status' ? STATUS : [])
  })
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(await screen.findByText(/Bank linked/)).toBeTruthy()
  expect(screen.queryByText('Method Not Allowed')).toBeNull()
})

it('step ④ waits for a linked account, then offers the sync automation', async () => {
  const linkedStatus = { ...STATUS, configured: true, has_access_token: true }
  const row = (linked: string | null) => ({
    provider_account_id: 'p1',
    name: 'Gold Checking',
    mask: '0000',
    currency: 'USD',
    balance: 110,
    account_type: 'cash',
    linked_account_id: linked,
  })
  const serve = (linked: string | null) =>
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url === '/api/plaid/status') return okJson(linkedStatus)
      if (url === '/api/connectors/plaid/accounts')
        return okJson({ status: 'ok', provider_label: 'Plaid', accounts: [row(linked)] })
      return okJson([{ id: 'acc_1', name: 'Everyday Checking', type: 'cash', external_ids: [] }])
    })

  serve(null)
  const { unmount } = render(<PlaidSection />)
  const step4 = await screen.findByRole('region', { name: 'Step 4: Bring in transactions' })
  expect(await within(step4).findByText(/Link an account above first/)).toBeTruthy()
  expect(within(step4).queryByRole('button', { name: 'Set up automation' })).toBeNull()
  unmount()

  serve('acc_1')
  render(<PlaidSection />)
  const ready = await screen.findByRole('region', { name: 'Step 4: Bring in transactions' })
  expect(await within(ready).findByRole('button', { name: 'Set up automation' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Step 3: Choose and link accounts' })).toBeTruthy()
})
