// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { IncomeEvent } from '../../types/portfolio'
import { DividendForm } from './DividendForm'

const ACCOUNTS = [
  { id: 'cash-1', name: 'Everyday Cash', kind: 'asset', currency: 'SGD' },
  { id: 'cash-2', name: 'Spare Cash', kind: 'asset', currency: 'SGD' },
]

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))
vi.mock('../../hooks/serverState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/serverState')>()
  return { ...actual, useAccounts: () => ({ data: ACCOUNTS }), notifyPortfolioChanged: vi.fn() }
})

const CASH: IncomeEvent = {
  id: 'inc-1',
  position_id: 'pos-1',
  instrument_id: 'inst-1',
  account_id: 'cash-1',
  paid_at: '2026-06-15',
  income_kind: 'dividend',
  gross_amount: 12,
  tax_withheld: 1.8,
  currency: 'SGD',
  is_drp: false,
  txn_id: 'txn-div',
  base_amount: 10.2,
}

function respond(url: string, init?: RequestInit) {
  if (url === '/api/transactions/txn-div')
    return Promise.resolve({
      ok: true,
      json: async () => ({
        kind: 'income',
        to_account_id: 'cash-1',
        to_amount: 10.2,
        to_currency: 'SGD',
      }),
    })
  if (url.startsWith('/api/income/'))
    return Promise.resolve({ ok: true, json: async () => ({ ok: true, init }) })
  return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation(respond)
})
afterEach(cleanup)

function renderForm(event: IncomeEvent = CASH) {
  const onDone = vi.fn()
  const view = render(
    <DividendForm event={event} symbol="ACME" onDone={onDone} onClose={() => {}} />,
  )
  return { ...view, onDone }
}

function calls(prefix: string) {
  return mockAuthFetch.mock.calls.filter(([url]) => String(url).startsWith(prefix))
}

// Dividends could only be corrected by asking Lucius (hand UAT, 2026-11-10).
describe('DividendForm', () => {
  it('saves a correction without touching the account the user never changed', async () => {
    const { findByLabelText, getByRole, onDone } = renderForm()
    fireEvent.change(await findByLabelText(/Tax withheld/), { target: { value: '1.2' } })

    fireEvent.click(getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const [url, init] = calls('/api/income/')[0]!
    expect(url).toBe('/api/income/inc-1')
    expect((init as RequestInit).method).toBe('PATCH')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.tax_withheld).toBe(1.2)
    expect('account_id' in body).toBe(false)
  })

  it('warns, then sends no account, when the user picks "Don\'t add"', async () => {
    const { findByText, getByRole, onDone, container } = renderForm()
    await findByText(/goes into Everyday Cash/)
    const select = container.querySelector('label.disposal-deposit select') as HTMLSelectElement

    fireEvent.change(select, { target: { value: '' } })
    expect(await findByText(/removes this dividend's cash transaction/)).toBeTruthy()
    fireEvent.click(getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const body = JSON.parse(String((calls('/api/income/')[0]![1] as RequestInit).body))
    expect(body.account_id).toBeNull()
  })

  it('deletes the dividend with its cash after a confirmation that says so', async () => {
    const { findByText, getByRole, onDone } = renderForm()
    await findByText(/goes into Everyday Cash/)

    fireEvent.click(getByRole('button', { name: 'Delete' }))
    expect(await findByText(/Its cash transaction is removed too/)).toBeTruthy()
    const buttons = document.querySelectorAll('button')
    const confirm = [...buttons].filter((b) => b.textContent === 'Delete').pop()!
    fireEvent.click(confirm)

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    const [url, init] = calls('/api/income/')[0]!
    expect(url).toBe('/api/income/inc-1?remove_cash=true')
    expect((init as RequestInit).method).toBe('DELETE')
  })

  it('offers no account for a reinvested dividend and says its shares stay', async () => {
    const { container, getByRole, findByText } = renderForm({
      ...CASH,
      id: 'inc-2',
      is_drp: true,
      txn_id: null,
    })
    expect(container.querySelector('label.disposal-deposit')).toBeNull()

    fireEvent.click(getByRole('button', { name: 'Delete' }))
    expect(await findByText(/reinvested shares stay as a lot/)).toBeTruthy()
  })
})
