// @vitest-environment jsdom
//
// The opening balance has ONE editing surface: the Starting Balance transaction.
// ADR 2026-07-28-starting-balance-single-surface.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import AccountModal from './AccountModal'
import type { Account, AccountCreate, AccountUpdate } from './types'

afterEach(cleanup)

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

function renderAdd(onSave = vi.fn()) {
  render(<AccountModal open mode="add" institutions={[]} onClose={() => {}} onSave={onSave} />)
  return onSave
}

describe('create — current balance is part of the essential first view', () => {
  it('asks for the balance without asking for its date', () => {
    renderAdd()
    expect(screen.getByLabelText(/Current balance/)).toBeTruthy()
    expect(screen.queryByLabelText('Balance as of')).toBeNull()
    expect(screen.getByText('Currency')).toBeTruthy()
  })

  it('submits directly from the compact view', () => {
    const onSave = renderAdd()
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday account'), {
      target: { value: 'New checking' },
    })
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '250.50' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const [payload] = onSave.mock.calls[0] as [AccountCreate]
    expect(payload.initial_balance).toBe(250.5)
  })

  it('Add account is blocked until the account has a name', () => {
    renderAdd()
    expect(
      (screen.getByRole('button', { name: 'Add account' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('keeps the compact field rhythm stable when the name error appears', () => {
    renderAdd()

    // Name, type, and currency reserve the same message line; balance already has help text.
    expect(document.querySelectorAll('.ui-field__message-space')).toHaveLength(3)
  })
})

describe('liability sign — the form says "amount you owe", storage is raw-negative', () => {
  it('negates a positive number typed for a liability', () => {
    const onSave = renderAdd()
    fireEvent.click(screen.getByLabelText(/Account type: Cash/))
    fireEvent.click(screen.getByText('Credit'))
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday account'), {
      target: { value: 'Amex' },
    })
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '5000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const [payload] = onSave.mock.calls[0] as [AccountCreate]
    // Pre-fix this shipped +5000 — a card opening as a 5,000 ASSET, a 10,000 net-worth swing.
    expect(payload.initial_balance).toBe(-5000)
  })

  it('leaves an asset opening positive', () => {
    const onSave = renderAdd()
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday account'), {
      target: { value: 'Cash' },
    })
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '900' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const [payload] = onSave.mock.calls[0] as [AccountCreate]
    expect(payload.initial_balance).toBe(900)
  })
})

describe('edit — the opening is reported, not edited', () => {
  function renderEdit(account: Account, onEditOpening?: () => void) {
    const onSave = vi.fn()
    render(
      <AccountModal
        open
        mode="edit"
        account={account}
        institutions={[]}
        onClose={() => {}}
        onSave={onSave}
        onEditOpening={onEditOpening}
      />,
    )
    return onSave
  }

  it('offers no editable opening amount or as-of date', () => {
    renderEdit(baseAccount({ initialBalance: 4200, initialBalanceDate: '2026-01-01' }))
    expect(screen.queryByPlaceholderText('0')).toBeNull()
    expect(document.querySelector('#f-asof')).toBeNull()
  })

  it('shows the opening and its date read-only', () => {
    renderEdit(baseAccount({ initialBalance: 4200, initialBalanceDate: '2026-01-01' }))
    expect(screen.getByText(/SGD 4,200/)).toBeTruthy()
    expect(screen.getByText(/as of Jan 1, 2026/)).toBeTruthy()
  })

  it('shows a liability opening as a positive "owed" figure', () => {
    renderEdit(
      baseAccount({
        type: 'credit',
        kind: 'liability',
        initialBalance: -344.36,
      }),
    )
    expect(screen.getByText(/SGD 344\.36 owed/)).toBeTruthy()
  })

  it('hands off to the transaction that owns the number', () => {
    const onEditOpening = vi.fn()
    renderEdit(baseAccount({ initialBalance: 4200 }), onEditOpening)
    fireEvent.click(screen.getByText('Open'))
    expect(onEditOpening).toHaveBeenCalledTimes(1)
  })

  it('keeps the starting-balance explanation visible', () => {
    renderEdit(baseAccount())

    expect(
      screen.getByText(
        'Starting balance is what this account held when you began tracking it. Transactions before this date are excluded, so your ledger starts from this balance.',
      ),
    ).toBeTruthy()
  })

  it('shows account details as a standard visible section', () => {
    renderEdit(baseAccount())

    expect(document.querySelector('.acct-details-disclosure')).toBeNull()
    expect(screen.getByText('Account details').classList.contains('acct-section-label')).toBe(true)
    expect(screen.getByLabelText('Last 4 digits')).toBeTruthy()
  })

  it('shows the locked account type and currency as quiet text metadata', () => {
    renderEdit(baseAccount({ type: 'credit', kind: 'liability', currency: 'SGD' }))

    const meta = document.querySelector('.acct-identity-meta')
    expect(meta?.textContent).toBe('CreditSGD')
    expect(meta?.querySelector('svg')).toBeNull()
    expect(meta?.parentElement?.classList.contains('acct-avatar-row')).toBe(true)
  })

  it('explains the purpose of the institution field', () => {
    renderEdit(baseAccount())

    expect(screen.getByText('Groups accounts by bank or broker.')).toBeTruthy()
  })

  it('puts the annual percentage unit in the interest-rate label', () => {
    renderEdit(baseAccount())

    expect(screen.getByLabelText('Interest rate (APY, %)')).toBeTruthy()
    expect(screen.queryByText('Annual yield, %')).toBeNull()
  })

  it('places the starting balance after the editable account details', () => {
    renderEdit(baseAccount())

    const lastFour = screen.getByLabelText('Last 4 digits').closest('.ui-field')
    const opening = screen.getByText('Starting balance').closest('.acct-opening-compact')
    if (!lastFour || !opening) throw new Error('expected both fields to be rendered')
    expect(lastFour.compareDocumentPosition(opening)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('never sends the opening on an unrelated save', () => {
    // The whole point: renaming an account must not re-book its Starting Balance row, nor
    // drag the tracking-start cutoff along with it.
    const onSave = renderEdit(
      baseAccount({ initialBalance: 4200, initialBalanceDate: '2026-01-01' }),
    )
    fireEvent.change(screen.getByDisplayValue('Meridian Multiplier'), {
      target: { value: 'Renamed' },
    })
    fireEvent.click(screen.getByText('Save changes'))

    const [payload] = onSave.mock.calls[0] as [AccountUpdate]
    expect(payload.name).toBe('Renamed')
    expect('initial_balance' in payload).toBe(false)
    expect('initial_balance_date' in payload).toBe(false)
  })
})
