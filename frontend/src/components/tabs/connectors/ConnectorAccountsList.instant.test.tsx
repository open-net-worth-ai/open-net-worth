// @vitest-environment jsdom
//
// Link / Unlink show on the row the moment the write succeeds. The list's re-fetch asks the
// provider for live balances (a couple of seconds against real Plaid) — the row must not wait on
// it. Here that re-fetch never answers at all.

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ConnectorAccountsList } from './ConnectorAccountsList'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
})

const ok = (body: unknown) => ({ ok: true, json: async () => body }) as Response

function serve(linked: string | null) {
  let accountListCalls = 0
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/connectors/plaid/accounts') {
      accountListCalls += 1
      if (accountListCalls > 1) return new Promise<Response>(() => {}) // the slow provider
      return Promise.resolve(
        ok({
          status: 'ok',
          provider_label: 'Plaid',
          accounts: [
            {
              provider_account_id: 'p1',
              name: 'Gold Checking',
              mask: '0000',
              currency: 'USD',
              balance: 110,
              account_type: 'cash',
              linked_account_id: linked,
            },
          ],
        }),
      )
    }
    if (init?.method === 'PUT') return Promise.resolve(ok({}))
    return Promise.resolve(
      ok([{ id: 'acc_1', name: 'Everyday Checking', type: 'cash', external_ids: [] }]),
    )
  })
}

it('Unlink flips the row without waiting for the provider re-fetch', async () => {
  serve('acc_1')
  render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
  await screen.findByText('Linked to “Everyday Checking”')
  fireEvent.click(screen.getByRole('button', { name: 'Unlink' }))
  expect(await screen.findByRole('button', { name: 'Add' })).toBeTruthy()
  expect(screen.queryByText('Linked to “Everyday Checking”')).toBeNull()
})

it('Link shows the linked account without waiting for the provider re-fetch', async () => {
  serve(null)
  render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
  fireEvent.click(
    await screen.findByRole('button', { name: 'Link Gold Checking to an existing account' }),
  )
  const picker = await screen.findByRole('group', { name: 'Link Gold Checking to an account' })
  await waitFor(() => within(picker).getByRole('button', { name: 'Everyday Checking' }))
  fireEvent.mouseDown(within(picker).getByRole('button', { name: 'Everyday Checking' }))
  expect(await screen.findByText('Linked to “Everyday Checking”')).toBeTruthy()
})
