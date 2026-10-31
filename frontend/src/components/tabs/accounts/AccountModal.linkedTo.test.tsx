// @vitest-environment jsdom
//
// CDS-UI-13/14/15 (connectors-as-data-sources UI spec, Region 3): AccountModal's "Linked to"
// list replaces the dead `connected` toggle, plus the `prefill` step-skip for
// ConnectorAccountsList's "Create account…" flow.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

import AccountModal from './AccountModal'
import type { Account, AccountCreate, ExternalId } from './types'

afterEach(cleanup)

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function baseAccount(overrides: Partial<Account> = {}): Account {
  return {
    id: 'acc_1',
    name: 'Meridian Multiplier',
    kind: 'asset',
    type: 'cash',
    institution: 'Meridian',
    currency: 'SGD',
    initialBalance: 100,
    initialBalanceDate: null,
    creditLimit: null,
    lastFour: null,
    note: '',
    statementCloseDay: null,
    paymentDueDay: null,
    reminderDays: null,
    lastReconciled: null,
    archived: false,
    connected: false,
    externalIds: [],
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: 100,
    balanceBase: 100,
    clearedBalance: 100,
    clearedBalanceBase: 100,
    role: 'cash',
    holdingsBase: 0,
    ...overrides,
  }
}

const plaidLink: ExternalId = {
  source: 'plaid',
  ref: 'plaid_acct_1',
  name: 'Chase Checking',
  mask: '1234',
  linkedAt: '2026-08-01T00:00:00Z',
  providerLabel: 'Plaid',
  status: 'connected',
}

function renderEdit(account: Account, onSave = vi.fn()) {
  render(
    <AccountModal
      open
      mode="edit"
      account={account}
      institutions={[]}
      onClose={() => {}}
      onSave={onSave}
    />,
  )
  return onSave
}

describe('AccountModal "Linked to" list', () => {
  it('CDS-UI-13: renders one row per external_ids entry and no Connected control', () => {
    renderEdit(baseAccount({ externalIds: [plaidLink] }))

    expect(screen.getByText('Linked to')).toBeTruthy()
    expect(screen.getByText('Plaid · Chase Checking ••1234')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Unlink' })).toBeTruthy()
    expect(screen.queryByText(/^Connected$/i)).toBeNull()
    expect(screen.queryByLabelText(/connected/i)).toBeNull()
  })

  it('CDS-UI-14: shows the empty copy when there are no external_ids', () => {
    renderEdit(baseAccount({ externalIds: [] }))

    expect(screen.getByText('Not linked to a connector.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Unlink' })).toBeNull()
  })

  it('CDS-UI-15: Unlink PUTs the entry removed and the row disappears, modal stays open', async () => {
    mockAuthFetch.mockResolvedValue(okJson({}))
    const onSave = renderEdit(
      baseAccount({
        externalIds: [plaidLink, { ...plaidLink, ref: 'plaid_acct_2', name: 'Chase Savings' }],
      }),
    )

    fireEvent.click(screen.getAllByRole('button', { name: 'Unlink' })[0]!)

    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalledTimes(1))
    const [url, init] = mockAuthFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/accounts/acc_1')
    expect(init.method).toBe('PUT')
    const body = JSON.parse(init.body as string) as { external_ids: Array<{ ref: string }> }
    expect(body.external_ids).toHaveLength(1)
    expect(body.external_ids[0]?.ref).toBe('plaid_acct_2')

    await waitFor(() => expect(screen.getByText('Plaid · Chase Savings ••1234')).toBeTruthy())
    expect(screen.queryByText('Plaid · Chase Checking ••1234')).toBeNull()
    // No confirm dialog, no modal close, no save/toast side effect.
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})

describe('AccountModal prefill (ConnectorAccountsList "Create account…")', () => {
  it('opens the compact essentials view seeded from prefill', () => {
    render(
      <AccountModal
        open
        mode="add"
        institutions={[]}
        prefill={{
          name: 'Chase Checking',
          currency: 'USD',
          lastFour: '1234',
          institution: 'Chase',
        }}
        onClose={() => {}}
        onSave={vi.fn()}
      />,
    )

    expect(screen.queryByText('Add manually')).toBeNull()
    expect(screen.getByDisplayValue('Chase Checking')).toBeTruthy()
    expect(screen.queryByDisplayValue('1234')).toBeNull()
  })

  it('starts on the prefilled account type', () => {
    render(
      <AccountModal
        open
        mode="add"
        institutions={[]}
        prefill={{ type: 'retirement' }}
        onClose={() => {}}
        onSave={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /^Account type: Retirement/ })).toBeTruthy()
  })

  it('creates via a contract-valid payload from the prefilled essentials', () => {
    const onSave = vi.fn()
    render(
      <AccountModal
        open
        mode="add"
        institutions={[]}
        prefill={{ name: 'Chase Checking', currency: 'USD' }}
        onClose={() => {}}
        onSave={onSave}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const [payload] = onSave.mock.calls[0] as [AccountCreate]
    expect(payload.name).toBe('Chase Checking')
    expect(payload.currency).toBe('USD')
  })
})
