// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AccountModal from './AccountModal'
import type { Account, AccountCreate, AccountUpdate } from './types'
import { setBaseCurrency } from '../../../utils/format'

afterEach(() => {
  cleanup()
  setBaseCurrency('SGD')
})

function createdAccount(): Account {
  return {
    id: 'acc_new',
    name: 'Everyday account',
    kind: 'asset',
    type: 'cash',
    institution: '',
    currency: 'SGD',
    initialBalance: 125.5,
    initialBalanceDate: '2026-09-10',
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
    balance: 125.5,
    balanceBase: 125.5,
    clearedBalance: 125.5,
    clearedBalanceBase: 125.5,
    role: 'cash',
    holdingsBase: 0,
  }
}

function renderAdd(
  onSave: (form: AccountCreate | AccountUpdate, id?: string) => Promise<Account> | void = vi.fn(),
  onClose: () => void = vi.fn(),
) {
  render(<AccountModal open mode="add" institutions={[]} onClose={onClose} onSave={onSave} />)
}

describe('AccountModal quick create', () => {
  it('opens directly on the four essentials without advanced fields or a date', () => {
    renderAdd()

    expect(screen.getByLabelText(/Name/)).toBeTruthy()
    expect(screen.getByText('Account type')).toBeTruthy()
    expect(screen.getByText('Currency')).toBeTruthy()
    expect(screen.getByLabelText(/Current balance/)).toBeTruthy()
    expect(document.querySelector('.acct-balance-inline > span')).toBeNull()
    expect(screen.queryByText('Add manually')).toBeNull()
    expect(screen.queryByText('Institution')).toBeNull()
    expect(screen.queryByText('Last 4 digits')).toBeNull()
    expect(screen.queryByText('Note')).toBeNull()
    expect(screen.queryByText('Colour')).toBeNull()
    expect(screen.queryByLabelText('Balance as of')).toBeNull()
  })

  it('submits a null-free minimal payload and prevents a duplicate while pending', async () => {
    let resolveSave: (value: Account) => void = () => {}
    const onSave = vi.fn<(form: AccountCreate | AccountUpdate, id?: string) => Promise<Account>>(
      () =>
        new Promise<Account>((resolve) => {
          resolveSave = resolve
        }),
    )
    renderAdd(onSave)

    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Everyday account' } })
    fireEvent.change(screen.getByLabelText(/Current balance/), { target: { value: '125.50' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const payload = onSave.mock.calls[0]?.[0] as AccountCreate
    expect(payload).toEqual({
      name: 'Everyday account',
      type: 'cash',
      currency: 'SGD',
      initial_balance: 125.5,
    })
    expect((screen.getByRole('button', { name: 'Adding…' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Adding…' }))
    expect(onSave).toHaveBeenCalledTimes(1)

    resolveSave(createdAccount())
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Adding…' })).toBeNull())
  })

  it('defaults the currency to the base currency, not SGD', () => {
    setBaseCurrency('USD')
    const onSave = vi.fn()
    renderAdd(onSave)

    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Checking' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    expect((onSave.mock.calls[0]?.[0] as AccountCreate).currency).toBe('USD')
  })

  it('falls back to the base currency when a prefill carries none', () => {
    setBaseCurrency('USD')
    const onSave = vi.fn()
    render(
      <AccountModal
        open
        mode="add"
        institutions={[]}
        prefill={{ type: 'retirement', name: '401k' }}
        onClose={vi.fn()}
        onSave={onSave}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    expect((onSave.mock.calls[0]?.[0] as AccountCreate).currency).toBe('USD')
  })

  it('cannot be dismissed while creation is pending', () => {
    const onClose = vi.fn()
    renderAdd(
      vi.fn(() => new Promise<Account>(() => {})),
      onClose,
    )
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Everyday account' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('keeps entered values and shows a useful error when creation fails', async () => {
    renderAdd(vi.fn().mockRejectedValue(new Error('422')))
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'Everyday account' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    expect((await screen.findByRole('alert')).textContent).toMatch(/couldn’t add this account/i)
    expect(screen.getByDisplayValue('Everyday account')).toBeTruthy()
  })

  it('keeps the old wide edit layout with metadata above the type-specific details', () => {
    const props = {
      mode: 'edit' as const,
      account: { ...createdAccount(), type: 'credit', kind: 'liability' as const },
      institutions: [] as string[],
      onClose: () => {},
      onSave: vi.fn(),
    }
    const { rerender } = render(<AccountModal open {...props} />)

    expect(document.querySelector('.ui-modal-card--xl')).toBeTruthy()
    expect(screen.getByDisplayValue('Everyday account')).toBeTruthy()
    expect(screen.getByText('Institution')).toBeTruthy()
    expect(screen.getByText('Last 4 digits')).toBeTruthy()
    expect(screen.getByLabelText('Note')).toBeTruthy()
    expect(screen.queryByText('Colour')).toBeNull()
    expect(screen.getByText('Credit details')).toBeTruthy()
    expect(screen.queryByLabelText(/Remind me/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Advanced details' })).toBeNull()
    expect(screen.queryByText('Not linked to a connector.')).toBeNull()

    expect(document.querySelector('#f-l4')).toBeTruthy()

    const scroller = document.querySelector('.acct-wizard--edit')
    expect(scroller?.classList.contains('is-scrolling')).toBe(false)
    fireEvent.scroll(scroller as Element)
    expect(scroller?.classList.contains('is-scrolling')).toBe(true)

    rerender(<AccountModal open={false} {...props} />)
    rerender(<AccountModal open {...props} />)
    expect(document.querySelector('.acct-wizard--edit')?.classList.contains('is-scrolling')).toBe(
      false,
    )
  })

  it('saves an institution on the account and restores it when reopened', async () => {
    const saved = { ...createdAccount(), institution: 'UOB' }
    const onSave = vi.fn().mockResolvedValue(saved)
    const props = {
      mode: 'edit' as const,
      account: createdAccount(),
      institutions: ['DBS'],
      onClose: () => {},
      onSave,
    }
    const { rerender } = render(<AccountModal open {...props} />)

    const institution = screen.getByRole('combobox', { name: 'Institution' })
    fireEvent.change(institution, { target: { value: 'UOB' } })
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Use “UOB”' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0]?.[0]).toMatchObject({ institution: 'UOB' })
    expect(onSave.mock.calls[0]?.[0]).not.toHaveProperty('reminder_days_before')

    rerender(<AccountModal open={false} {...props} />)
    rerender(<AccountModal open {...props} account={saved} institutions={['DBS', 'UOB']} />)
    expect((screen.getByRole('combobox', { name: 'Institution' }) as HTMLInputElement).value).toBe(
      'UOB',
    )
    fireEvent.focus(screen.getByRole('combobox', { name: 'Institution' }))
    expect(screen.getByRole('option', { name: 'UOB' })).toBeTruthy()
  })
})
