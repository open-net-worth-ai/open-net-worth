// @vitest-environment jsdom
// Brokerage total / Live holdings on the account screen (ADR 2026-11-10). Every figure comes
// from the server (`value_source`, `value_sync`, the account's holdings and balance); these
// tests pin what each state SAYS and which actions it offers. Synthetic figures only.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { AccountValueCard, AccountValueFooter, AccountValueNotice } from './AccountValuePanels'
import { needsReconciling } from './reconcileDue'
import { sidebarLine, valuationOf } from './valuation'
import type { Account, ApiAccount, ApiValueSync } from './types'
import { setBaseCurrency } from '../../../utils/format'

beforeAll(() => setBaseCurrency('USD'))
afterEach(cleanup)

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Individual Brokerage',
    kind: 'asset',
    type: 'investment',
    institution: 'Acme Securities',
    currency: 'USD',
    external_ids: [
      {
        source: 'plaid',
        ref: 'pa-1',
        name: 'Brokerage',
        mask: '4321',
        linked_at: '2026-11-01T00:00:00Z',
        provider_label: 'Plaid',
        status: 'connected',
      },
    ],
    balance: 123456.78,
    balance_base: 123456.78,
    cleared_balance: 123456.78,
    cleared_balance_base: 123456.78,
    value_source: 'total',
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 123456.78,
      changed_at: '2026-11-09T10:00:00+00:00',
    },
    value_siblings: [],
    value_breakdown: {
      tracked_holdings: 0,
      untracked: 123456.78,
      total: 123456.78,
      currency: 'USD',
    },
    ...over,
  }
}

function account(a: ApiAccount, holdingsBase = 0): Account {
  return {
    id: a.id,
    name: a.name,
    kind: 'asset',
    type: a.type,
    institution: a.institution ?? '',
    currency: a.currency,
    initialBalance: 0,
    initialBalanceDate: '2026-11-01',
    creditLimit: null,
    lastFour: null,
    note: '',
    statementCloseDay: null,
    paymentDueDay: null,
    reminderDays: null,
    lastReconciled: null,
    archived: false,
    connected: true,
    externalIds: (a.external_ids ?? []).map((e) => ({
      source: e.source,
      ref: e.ref,
      name: e.name,
      mask: e.mask,
      linkedAt: e.linked_at,
      providerLabel: e.provider_label,
      status: e.status ?? 'connected',
    })),
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: a.balance,
    balanceBase: a.balance_base,
    clearedBalance: a.cleared_balance,
    clearedBalanceBase: a.cleared_balance_base,
    role: holdingsBase > 0 ? 'securities' : 'cash',
    holdingsBase,
    valuation: valuationOf(a),
  }
}

function withSync(sync: Partial<ApiValueSync>, over: Partial<ApiAccount> = {}) {
  return api({
    value_sync: { status: 'fresh', source: 'plaid', provider_label: 'Plaid', ...sync },
    ...over,
  })
}

const noop = () => {}

describe('the Account value card', () => {
  it('shows a linked Brokerage total alone until something is tracked', () => {
    render(<AccountValueCard account={account(api())} onAddHolding={noop} onSwitch={noop} />)
    expect(screen.getByText('Total value')).toBeTruthy()
    expect(screen.getByText('$123,456.78')).toBeTruthy()
    expect(screen.getByText('Add holdings for live prices, gains and allocation')).toBeTruthy()
  })

  it('splits the total once holdings are tracked: tracked + untracked = total', () => {
    const a = account(api({ balance: 4980, balance_base: 4980 }), 3020)
    render(<AccountValueCard account={a} onAddHolding={noop} onSwitch={noop} />)
    expect(screen.getByText('Tracked holdings')).toBeTruthy()
    expect(screen.getByText('Cash and untracked holdings')).toBeTruthy()
    expect(screen.getByText('$3,020.00')).toBeTruthy()
    expect(screen.getByText('$4,980.00')).toBeTruthy()
    expect(screen.getByText('$8,000.00')).toBeTruthy()
  })

  it('offers Live holdings when only a sliver is untracked', () => {
    const onSwitch = vi.fn()
    const a = account(api({ balance: 60, balance_base: 60 }), 3020)
    render(<AccountValueCard account={a} onAddHolding={noop} onSwitch={onSwitch} />)
    expect(screen.getByText(/is untracked\. Switch to live/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }))
    expect(onSwitch).toHaveBeenCalled()
  })

  it('shows a dash, not $0.00, while the first total is on its way', () => {
    const a = account(withSync({ status: 'waiting' }, { balance: 0, balance_base: 0 }))
    render(<AccountValueCard account={a} onAddHolding={noop} onSwitch={noop} />)
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.queryByText('$0.00')).toBeNull()
  })

  it('labels a Live holdings account Holdings + Cash', () => {
    const a = account(api({ value_source: 'holdings', balance: 500, balance_base: 500 }), 3020)
    render(<AccountValueCard account={a} onAddHolding={noop} onSwitch={noop} />)
    expect(screen.getByText('Holdings')).toBeTruthy()
    expect(screen.getByText('Cash')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add holding' })).toBeNull()
  })
})

describe('the footer says where the total comes from', () => {
  it.each([
    [withSync({ status: 'waiting' }), /Waiting for Plaid’s first total/],
    [
      withSync({ status: 'fresh', total: 123456.78, changed_at: '2026-11-09T10:00:00+00:00' }),
      /Value synced via Plaid · last changed/,
    ],
    [withSync({ status: 'kept' }), /Value you kept/],
    [api({ external_ids: [], value_sync: null }), /Value you entered/],
    // QA 2026-11-10: the first total was held back, so nothing has synced yet — never claim it.
    [withSync({ status: 'held_back', held_total: 0 }), /^Value you entered/],
  ])('%#', (a, text) => {
    render(<AccountValueFooter account={account(a)} />)
    expect(screen.getByRole('status').textContent).toMatch(text)
  })
})

describe('notices', () => {
  function notice(a: Account, over: Partial<Parameters<typeof AccountValueNotice>[0]> = {}) {
    const props = {
      account: a,
      onOpenConnector: vi.fn(),
      onEdit: vi.fn(),
      onReviewHoldings: vi.fn(),
      onConfirmHeld: vi.fn(),
      ...over,
    }
    render(<AccountValueNotice {...props} />)
    return props
  }

  it('asks about a held-back total and answers both ways', () => {
    const p = notice(
      account(
        withSync(
          { status: 'held_back', total: 5000, held_total: 0 },
          {
            balance: 5000,
            balance_base: 5000,
            value_breakdown: { tracked_holdings: 0, untracked: 5000, total: 5000, currency: 'USD' },
          },
        ),
      ),
    )
    expect(screen.getByText(/That looks wrong, so it wasn’t used/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Keep $5,000.00' }))
    expect(p.onConfirmHeld).toHaveBeenCalledWith(false)
    fireEvent.click(screen.getByRole('button', { name: 'Use $0.00' }))
    expect(p.onConfirmHeld).toHaveBeenCalledWith(true)
  })

  it('sends an expired sign-in to Reconnect', () => {
    const p = notice(account(withSync({ status: 'reconnect' })))
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(p.onOpenConnector).toHaveBeenCalled()
  })

  it('names the currency it refused', () => {
    notice(account(withSync({ status: 'currency_mismatch', reported_currency: 'EUR' })))
    expect(screen.getByText(/reports this account in EUR, but it’s set up in USD/)).toBeTruthy()
  })

  it('says a paused connector keeps nothing up to date', () => {
    const paused = api()
    paused.external_ids = paused.external_ids!.map((e) => ({ ...e, status: 'paused' as const }))
    notice(account(paused))
    expect(screen.getByText(/Plaid is paused, so this account isn’t updating/)).toBeTruthy()
  })

  it('flags a Live holdings account far from the reported total, once', () => {
    const a = account(
      api({
        value_source: 'holdings',
        balance: 0,
        balance_base: 0,
        value_sync: {
          status: 'compare',
          source: 'plaid',
          provider_label: 'Plaid',
          reported_total: 10000,
        },
      }),
      3020,
    )
    notice(a)
    expect(screen.getByText(/You may be missing a holding/)).toBeTruthy()
  })

  it('stays quiet when all is well', () => {
    notice(account(api()))
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('sidebar and reconcile cadence', () => {
  it('never asks to reconcile a total its connector keeps', () => {
    const a = account(api())
    expect(needsReconciling({ ...a, initialBalanceDate: '2025-01-01' })).toBe(false)
    expect(sidebarLine(a)?.text).toMatch(/^Updated/)
  })

  it('still asks for an unlinked total on the usual cadence', () => {
    const a = account(api({ external_ids: [], value_sync: null }))
    expect(needsReconciling({ ...a, initialBalanceDate: '2025-01-01' })).toBe(true)
    expect(sidebarLine(a)?.text).toMatch(/^Entered/)
  })

  it.each([
    ['waiting', 'Waiting for total', false],
    ['held_back', 'Needs your OK', true],
    ['reconnect', 'Reconnect', true],
    ['error', 'Not updating', true],
  ] as const)('a %s total reads "%s"', (status, text, warn) => {
    expect(sidebarLine(account(withSync({ status })))).toEqual({ text, warn })
  })

  it('reads nothing new for an account on the old valuation', () => {
    expect(valuationOf(api({ value_source: undefined }))).toBeNull()
  })
})
