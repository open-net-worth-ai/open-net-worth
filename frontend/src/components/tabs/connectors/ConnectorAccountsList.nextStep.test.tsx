// @vitest-environment jsdom
//
// The redesigned Accounts list: provider accounts group by their suggested type, and once one is
// linked the list says what happens next — linking books nothing; transactions come in through
// the connector's sync Automation (or a one-off ask to Lucius).

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ConnectorAccountsList } from './ConnectorAccountsList'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
  vi.unstubAllGlobals()
})

const acct = (id: string, name: string, type: string | null, linked: string | null = null) => ({
  provider_account_id: id,
  name,
  mask: id,
  currency: 'USD',
  balance: 100,
  account_type: type,
  linked_account_id: linked,
})

function serve(accounts: unknown[]) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    const body =
      url === '/api/connectors/plaid/accounts'
        ? { status: 'ok', provider_label: 'Plaid', accounts }
        : [{ id: 'acc_1', name: 'My Checking', external_ids: [] }]
    return { ok: true, json: async () => body } as Response
  })
}

it('groups accounts by type and shows nothing next until one is linked', async () => {
  serve([acct('1', 'Gold Checking', 'cash'), acct('2', 'Diamond Card', 'credit')])
  render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
  const cash = await screen.findByRole('region', { name: 'Cash' })
  expect(within(cash).getByText('Gold Checking')).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Credit cards' })).getByText('Diamond Card'))
  expect(screen.getByText('0 of 2 added')).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Next step' })).toBeNull()
})

it('once an account is linked, says how transactions come in and opens the sync draft', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  serve([acct('1', 'Gold Checking', 'cash', 'acc_1'), acct('2', 'Diamond Card', 'credit')])
  render(<ConnectorAccountsList provider="plaid" providerLabel="Plaid" />)
  const next = await screen.findByRole('region', { name: 'Next step' })
  expect(within(next).getByText(/Linking doesn't import anything yet/)).toBeTruthy()
  expect(within(next).getByText(/1 account isn't linked, so it'll be skipped/)).toBeTruthy()
  expect(within(next).getByText(/to your linked account every 6 hours/)).toBeTruthy()
  expect(within(next).getByText(/Fetch my Plaid transactions/)).toBeTruthy()
  expect(screen.getByText('1 of 2 added')).toBeTruthy()
  fireEvent.click(within(next).getByRole('button', { name: 'Set up automation' }))
  expect(assign).toHaveBeenCalledWith('/automations?template=sync_plaid')
})
