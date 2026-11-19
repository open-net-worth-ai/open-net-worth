// @vitest-environment jsdom
//
// Reported 2026-11-13: renaming a bank's account in Add account (opened from a connector page)
// was overwritten mid-typing. Every write refreshes open screens; the connector page rebuilt
// the dialog's `prefill` on each render, and the dialog re-filled its whole form from it. The
// form fills when the dialog OPENS (or opens on another account), never on a re-render.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import AccountModal from './AccountModal'
import type { Account } from './types'

afterEach(cleanup)

const name = () => (screen.getByLabelText(/Name/) as HTMLInputElement).value

function account(over: Partial<Account> = {}): Account {
  return {
    id: 'acc_1',
    name: 'ACME Current',
    kind: 'asset',
    type: 'cash',
    institution: '',
    currency: 'EUR',
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
    connected: false,
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: 0,
    balanceBase: 0,
    clearedBalance: 0,
    clearedBalanceBase: 0,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

describe('AccountModal keeps what is being typed', () => {
  it('keeps a typed name when the caller re-renders with an equal, new prefill', () => {
    const props = { mode: 'add' as const, institutions: [], onClose: vi.fn(), onSave: vi.fn() }
    const { rerender } = render(
      <AccountModal open {...props} prefill={{ name: 'ACME Current', currency: 'EUR' }} />,
    )
    expect(name()).toBe('ACME Current')
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Holiday savings' } })

    rerender(<AccountModal open {...props} prefill={{ name: 'ACME Current', currency: 'EUR' }} />)

    expect(name()).toBe('Holiday savings')
  })

  it('keeps a typed name when the account being edited is re-read', () => {
    const props = { mode: 'edit' as const, institutions: [], onClose: vi.fn(), onSave: vi.fn() }
    const { rerender } = render(<AccountModal open {...props} account={account()} />)
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Holiday savings' } })

    rerender(<AccountModal open {...props} account={account({ balance: 45.2 })} />)

    expect(name()).toBe('Holiday savings')
  })

  it('fills afresh when opened again, or on another account', () => {
    const props = { mode: 'edit' as const, institutions: [], onClose: vi.fn(), onSave: vi.fn() }
    const { rerender } = render(<AccountModal open {...props} account={account()} />)
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Holiday savings' } })

    rerender(<AccountModal open {...props} account={account({ id: 'acc_2', name: 'Other' })} />)
    expect(name()).toBe('Other')

    rerender(<AccountModal open={false} {...props} account={account()} />)
    rerender(<AccountModal open {...props} account={account()} />)
    expect(name()).toBe('ACME Current')
  })
})
