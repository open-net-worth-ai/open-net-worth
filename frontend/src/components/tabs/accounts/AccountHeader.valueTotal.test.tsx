// @vitest-environment jsdom
//
// PR #93 user-acceptance scenarios for the account header (UI-01/02/05/07/08/23/24, REC-18/19,
// FLAG-12): which card an account gets, the reconcile popover on a Brokerage total account, the
// Keep / Use answers, Add holding handing the request to Lucius. Synthetic figures only.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { AccountHeader } from './AccountHeader'
import { fromApiAccount } from './data'
import type { Account, ApiAccount, ApiValueSync } from './types'
import { setBaseCurrency } from '../../../utils/format'

const composer = { setPendingText: vi.fn() }
vi.mock('../../../state/chatComposer', async (orig) => ({
  ...(await orig<typeof import('../../../state/chatComposer')>()),
  useChatComposerOptional: () => composer,
}))

beforeAll(() => {
  setBaseCurrency('USD')
})
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 12))
  composer.setPendingText.mockReset()
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

const PLAID = {
  source: 'plaid',
  ref: 'pa-1',
  name: 'Brokerage',
  mask: '4321',
  linked_at: '2026-11-01T00:00:00Z',
  provider_label: 'Plaid',
  status: 'connected' as const,
}

function api(over: Partial<ApiAccount> = {}): ApiAccount {
  return {
    id: 'acc_1',
    name: 'Old 401(k)',
    kind: 'asset',
    type: 'retirement',
    institution: 'Acme Securities',
    currency: 'USD',
    initial_balance_date: '2026-11-01',
    external_ids: [],
    balance: 4980,
    balance_base: 4980,
    cleared_balance: 4980,
    cleared_balance_base: 4980,
    value_source: 'total',
    value_sync: null,
    value_siblings: [],
    value_breakdown: { tracked_holdings: 3020, untracked: 4980, total: 8000, currency: 'USD' },
    ...over,
  } as ApiAccount
}

const acct = (over: Partial<ApiAccount> = {}, holdingsBase = 3020): Account =>
  fromApiAccount(api(over), holdingsBase)

function linked(sync: Partial<ApiValueSync> = {}, over: Partial<ApiAccount> = {}) {
  return api({
    external_ids: [PLAID],
    value_sync: {
      status: 'fresh',
      source: 'plaid',
      provider_label: 'Plaid',
      total: 8000,
      changed_at: '2026-11-10T10:00:00+00:00',
      ...sync,
    },
    ...over,
  })
}

const noop = () => {}

function renderHeader(account: Account, over: Partial<Parameters<typeof AccountHeader>[0]> = {}) {
  const handlers = {
    onEdit: vi.fn(),
    onReconcile: vi.fn(),
    onReconcileTotal: vi.fn(),
    onConfirmHeldTotal: vi.fn(),
  }
  const view = render(
    <AccountHeader
      account={account}
      dueInfo={null}
      stats={null}
      menuOpen={false}
      setMenuOpen={noop}
      collapsed={false}
      onReopen={noop}
      onAddTxn={noop}
      onArchive={noop}
      onDelete={noop}
      onManagePayees={noop}
      onManageCategories={noop}
      onImport={noop}
      onExport={noop}
      onReview={noop}
      onStartReview={noop}
      {...handlers}
      {...over}
    />,
  )
  return { ...handlers, view }
}

const reconcileTrigger = () => screen.getByRole('button', { name: 'Reconcile' })
const popover = () => screen.getByRole('dialog').textContent

describe('which card an account gets (UI-01 / UI-02 / FLAG-12)', () => {
  it('a total account with holdings: tracked + untracked = total, and the "Value you entered" footer', () => {
    const { view } = renderHeader(acct())
    expect(
      [...view.container.querySelectorAll('.acct-bal-part')].map((p) => p.textContent),
    ).toEqual([
      '$3,020.00Tracked holdings',
      '$4,980.00Cash and untracked holdings',
      '$8,000.00Total value',
    ])
    expect(screen.getByRole('status').textContent).toBe('Value you entered · Nov 1')
    expect(screen.queryByText('Cleared')).toBeNull()
    expect(screen.queryByText('Uncleared')).toBeNull()
  })

  it('a linked total with no holdings: Total value $123,456.78 alone and "Value synced via Plaid"', () => {
    const { view } = renderHeader(
      acct(
        linked(
          { total: 123456.78 },
          {
            balance: 123456.78,
            balance_base: 123456.78,
            value_breakdown: {
              tracked_holdings: 0,
              untracked: 123456.78,
              total: 123456.78,
              currency: 'USD',
            },
          },
        ),
        0,
      ),
    )
    expect(view.container.querySelector('.acct-src-one')?.textContent).toBe(
      '$123,456.78Total value',
    )
    expect(
      [...screen.getAllByRole('status')].some(
        (s) => s.textContent === 'Value synced via Plaid · last changed Nov 10',
      ),
    ).toBe(true)
  })

  it('an unlinked total with no holdings keeps today’s Cleared + Uncleared card', () => {
    renderHeader(acct({}, 0))
    expect(screen.getByText('Cleared')).toBeTruthy()
    expect(screen.getByText('Uncleared')).toBeTruthy()
    expect(screen.getByText('Working balance')).toBeTruthy()
    expect(screen.queryByText('Total value')).toBeNull()
    expect(screen.getByText('Never reconciled')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add holding' })).toBeNull()
  })

  it('FLAG-12: flag off (no valuation from the server) is the old card, even with holdings and a link', () => {
    renderHeader(
      acct(
        {
          value_source: undefined,
          value_sync: undefined,
          value_breakdown: undefined,
          external_ids: [PLAID],
        },
        3020,
      ),
    )
    expect(screen.getByText('Cleared')).toBeTruthy()
    expect(screen.getByText('Uncleared')).toBeTruthy()
    expect(screen.getByText('Working balance')).toBeTruthy()
    for (const gone of ['Total value', 'Tracked holdings', 'Account value', 'Add holding'])
      expect(screen.queryByText(gone)).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByText(/Value synced|Value you entered/)).toBeNull()
  })

  it('a Live holdings account is Holdings + Cash and keeps the old reconcile line', () => {
    const { view } = renderHeader(
      acct({ value_source: 'holdings', balance: 500, balance_base: 500 }, 3020),
    )
    expect(
      [...view.container.querySelectorAll('.acct-bal-part')].map((p) => p.textContent),
    ).toEqual(['$3,020.00Holdings', '$500.00Cash', '$3,520.00Total value'])
    expect(screen.getByText('Never reconciled')).toBeTruthy()
    fireEvent.click(reconcileTrigger())
    expect(popover()).toContain('Is the real balance')
  })
})

describe('reconcile on a Brokerage total account (REC-01 / REC-18)', () => {
  it('asks about the account’s real TOTAL, not the cash balance', () => {
    renderHeader(acct())
    fireEvent.click(reconcileTrigger())
    expect(popover()).toContain('Is the account’s real total $8,000.00 USD?')
    expect(popover()).not.toContain('4,980')
  })

  it('"Yes, it matches" sends the total (8,000.00) to onReconcileTotal, not onReconcile', () => {
    const h = renderHeader(acct())
    fireEvent.click(reconcileTrigger())
    fireEvent.click(screen.getByRole('button', { name: 'Yes, it matches' }))
    expect(h.onReconcileTotal).toHaveBeenCalledWith(expect.objectContaining({ id: 'acc_1' }), 8000)
    expect(h.onReconcile).not.toHaveBeenCalled()
  })

  it('"No": previews the adjustment against the total and sends the real total typed', () => {
    const h = renderHeader(acct())
    fireEvent.click(reconcileTrigger())
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    const input = screen.getByPlaceholderText('8000')
    expect(screen.getByText('Real total (USD)')).toBeTruthy()
    fireEvent.change(input, { target: { value: '9000' } })
    expect(screen.getByText('+$1,000.00')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add adjustment' }))
    expect(h.onReconcileTotal).toHaveBeenCalledWith(expect.objectContaining({ id: 'acc_1' }), 9000)
    expect(h.onReconcile).not.toHaveBeenCalled()
  })

  it('"No" with a lower total previews a negative adjustment, and an equal one says In sync', () => {
    renderHeader(acct())
    fireEvent.click(reconcileTrigger())
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    const input = screen.getByPlaceholderText('8000')
    fireEvent.change(input, { target: { value: '3020' } })
    expect(screen.getByText('-$4,980.00')).toBeTruthy()
    fireEvent.change(input, { target: { value: '8000' } })
    expect(screen.getByText('In sync')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('a linked total names the broker in both steps of the popover', () => {
    renderHeader(acct(linked()))
    fireEvent.click(reconcileTrigger())
    expect(popover()).toContain('Acme Securities’s latest total.')
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    expect(popover()).toContain('Replaced at Acme Securities’s next update.')
  })

  it('an unlinked total has no broker notes', () => {
    renderHeader(acct())
    fireEvent.click(reconcileTrigger())
    expect(popover()).not.toContain('latest total')
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    expect(popover()).not.toContain('Replaced at')
  })

  it('a waiting total has nothing to reconcile yet', () => {
    renderHeader(
      acct(
        linked(
          { status: 'waiting', total: undefined, changed_at: undefined },
          { balance: 0, balance_base: 0 },
        ),
        0,
      ),
    )
    expect(screen.queryByRole('button', { name: 'Reconcile' })).toBeNull()
    expect(screen.getByText('Waiting for Plaid’s first total')).toBeTruthy()
  })

  it('a paused connector can be reconciled again (no broker notes)', () => {
    renderHeader(acct(linked({}, { external_ids: [{ ...PLAID, status: 'paused' }] })))
    fireEvent.click(reconcileTrigger())
    expect(popover()).not.toContain('latest total')
  })
})

describe('the held-back notice through the header (UI-08)', () => {
  it('Keep answers false, Use answers true, each with the account', () => {
    const h = renderHeader(
      acct(
        linked(
          { status: 'held_back', total: 123456.78, held_total: 0 },
          {
            balance: 123456.78,
            balance_base: 123456.78,
            value_breakdown: {
              tracked_holdings: 0,
              untracked: 123456.78,
              total: 123456.78,
              currency: 'USD',
            },
          },
        ),
        0,
      ),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Keep $123,456.78' }))
    expect(h.onConfirmHeldTotal).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'acc_1' }),
      false,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Use $0.00' }))
    expect(h.onConfirmHeldTotal).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'acc_1' }),
      true,
    )
  })

  it('no notice and no valuation chrome for an account on the old valuation', () => {
    renderHeader(
      acct({ value_source: undefined, value_sync: undefined, value_breakdown: undefined }, 0),
    )
    expect(screen.queryByText(/That looks wrong/)).toBeNull()
  })
})

describe('Add holding, Edit (UI-23 / UI-24)', () => {
  it('Add holding pre-fills Lucius and opens the dock, sending nothing', () => {
    const opened = vi.fn()
    window.addEventListener('lx-dock-open', opened)
    renderHeader(acct(linked(), 0))
    fireEvent.click(screen.getByRole('button', { name: 'Add holding' }))
    expect(composer.setPendingText).toHaveBeenCalledTimes(1)
    expect(composer.setPendingText).toHaveBeenCalledWith(
      'Help me add the holdings in my Old 401(k) account at Acme Securities.',
    )
    expect(opened).toHaveBeenCalledTimes(1)
    window.removeEventListener('lx-dock-open', opened)
  })

  it('Add holding leaves out the institution when there is none', () => {
    renderHeader(acct(linked({}, { institution: '' }), 0))
    fireEvent.click(screen.getByRole('button', { name: 'Add holding' }))
    expect(composer.setPendingText).toHaveBeenCalledWith(
      'Help me add the holdings in my Old 401(k) account.',
    )
  })

  it('the pencil after the name opens the editor', () => {
    const h = renderHeader(acct())
    fireEvent.click(screen.getByRole('button', { name: 'Edit Old 401(k)' }))
    expect(h.onEdit).toHaveBeenCalledTimes(1)
  })

  it('the "Switch" prompt on a ≤2% untracked part opens the editor', () => {
    const h = renderHeader(acct({ balance: 60, balance_base: 60 }, 3020))
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }))
    expect(h.onEdit).toHaveBeenCalledTimes(1)
  })

  it('"Edit account" in a currency-mismatch notice opens the editor', () => {
    const h = renderHeader(acct(linked({ status: 'currency_mismatch', reported_currency: 'EUR' })))
    fireEvent.click(screen.getByRole('button', { name: 'Edit account' }))
    expect(h.onEdit).toHaveBeenCalledTimes(1)
  })
})
