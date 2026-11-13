// @vitest-environment jsdom
//
// "Link to a bank account…" from the account itself (Krishna, 2026-11-12: "tap the account and
// select the bank to link"). Every connected connector's accounts are offered, the closest
// match first; one already feeding another account is shown, not offered; linking writes the
// account's external_ids, keeping every link it already had.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AccountHeader } from './AccountHeader'
import { LinkBankAccountDialog } from './LinkBankAccountDialog'
import { matchScore } from './linkBankMatch'
import { fromApiAccount } from './data'
import type { ApiAccount } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
const emitDataChanged = vi.hoisted(() => vi.fn())
vi.mock('../../../lucius/bus', async (load) => ({
  ...(await load<typeof import('../../../lucius/bus')>()),
  emitDataChanged,
}))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  emitDataChanged.mockReset()
})

const CA_MONZO = fromApiAccount({
  id: 'acc-monzo',
  name: 'CA_Monzo',
  kind: 'asset',
  type: 'cash',
  currency: 'GBP',
  balance: 0,
  balance_base: 0,
  external_ids: [
    { source: 'plaid', ref: 'old-1', name: 'Old', mask: '0001', provider_label: 'Plaid' },
  ],
} as ApiAccount)

const GROUPS = {
  connectors: [
    {
      provider: 'gocardless',
      provider_label: 'GoCardless',
      status: 'ok',
      accounts: [
        {
          provider_account_id: 'gc-2',
          name: 'Savings Pot',
          mask: '5678',
          currency: 'GBP',
          account_type: 'cash',
          institution: 'Monzo',
          linked_account_id: null,
        },
        {
          provider_account_id: 'gc-1',
          name: 'Current Account',
          mask: '1234',
          currency: 'GBP',
          account_type: 'cash',
          institution: 'Monzo',
          linked_account_id: null,
        },
        {
          provider_account_id: 'gc-3',
          name: 'Joint',
          mask: '9999',
          currency: 'GBP',
          account_type: 'cash',
          institution: 'Monzo',
          linked_account_id: 'acc-other',
        },
      ],
    },
    {
      provider: 'plaid',
      provider_label: 'Plaid',
      status: 'ok',
      accounts: [
        {
          provider_account_id: 'pl-1',
          name: 'Checking',
          mask: '4321',
          currency: 'USD',
          account_type: 'cash',
          institution: 'Chase',
          linked_account_id: null,
        },
      ],
    },
  ],
}

function serve(groups: unknown = GROUPS) {
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    const body =
      url === '/api/linkable-bank-accounts'
        ? groups
        : url === '/api/accounts'
          ? [
              { id: 'acc-other', name: 'Joint Account' },
              { id: 'acc-monzo', name: 'CA_Monzo' },
            ]
          : init?.method === 'PUT'
            ? { id: 'acc-monzo' }
            : {}
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
  })
}

const noop = () => {}

describe('LinkBankAccountDialog', () => {
  it('offers every connected bank’s accounts, suggests the closest, and keeps taken ones out of reach', async () => {
    serve()
    render(<LinkBankAccountDialog account={CA_MONZO} onClose={noop} onConnectBank={noop} />)
    await screen.findByText('Current Account')
    expect(screen.getByRole('region', { name: 'GoCardless' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Plaid' })).toBeTruthy()
    // Same currency, type and bank words win over a USD account at another bank.
    expect(screen.getAllByText('Suggested')).toHaveLength(1)
    // A bank account already feeding another account says so and has no Link button.
    expect(await screen.findByText(/Linked to “Joint Account”/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Link CA_Monzo to Joint/ })).toBeNull()
  })

  it('links the picked account and keeps the links it already had', async () => {
    serve()
    render(<LinkBankAccountDialog account={CA_MONZO} onClose={noop} onConnectBank={noop} />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Link CA_Monzo to Current Account •1234' }),
    )
    await screen.findByText(/CA_Monzo is linked to Current Account through GoCardless/)
    const put = authFetch.mock.calls.find((c) => c[1]?.method === 'PUT')
    expect(put?.[0]).toBe('/api/accounts/acc-monzo')
    const sent = JSON.parse(put?.[1]?.body as string).external_ids
    expect(sent.map((e: { source: string; ref: string }) => `${e.source}:${e.ref}`)).toEqual([
      'plaid:old-1',
      'gocardless:gc-1',
    ])
    expect(emitDataChanged).toHaveBeenCalledWith(['accounts'])
    expect(screen.getByText(/bring in transactions/i)).toBeTruthy()
  })

  it('with nothing to link, sends the user to connect a bank', async () => {
    serve({ connectors: [] })
    const onConnectBank = vi.fn()
    render(
      <LinkBankAccountDialog account={CA_MONZO} onClose={noop} onConnectBank={onConnectBank} />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Connect a bank' }))
    expect(onConnectBank).toHaveBeenCalled()
    expect(screen.getByText('No bank or broker is connected yet.')).toBeTruthy()
  })

  it('scores the last four digits above everything else', () => {
    const withMask = { ...CA_MONZO, lastFour: '5678' }
    const row = (mask: string) => ({ provider_account_id: mask, name: 'X', mask, currency: 'GBP' })
    expect(matchScore(withMask, row('5678'), 'GoCardless')).toBeGreaterThan(
      matchScore(withMask, row('1234'), 'GoCardless'),
    )
  })
})

describe('the account menu', () => {
  it('opens the picker from “Link to a bank account…”', async () => {
    serve()
    render(
      <AccountHeader
        account={CA_MONZO}
        dueInfo={null}
        stats={null}
        menuOpen={true}
        setMenuOpen={noop}
        collapsed={false}
        onReopen={noop}
        onAddTxn={noop}
        onEdit={noop}
        onArchive={noop}
        onDelete={noop}
        onManagePayees={noop}
        onManageCategories={noop}
        onImport={noop}
        onExport={noop}
        onReconcile={noop}
        onReview={noop}
        onStartReview={noop}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Link to a bank account/ }))
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(within(dialog).getByText('Current Account')).toBeTruthy())
  })
})
