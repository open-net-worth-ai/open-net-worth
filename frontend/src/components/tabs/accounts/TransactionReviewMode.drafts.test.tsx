// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { TransactionReviewMode } from './TransactionReviewMode'
import type { Account, ApiTxn, Category } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const CASH: Account = {
  id: 'cash-1',
  name: 'Everyday Checking',
  kind: 'asset',
  type: 'cash',
  institution: 'Meridian',
  currency: 'SGD',
  initialBalance: 0,
  initialBalanceDate: null,
  creditLimit: null,
  lastFour: '1234',
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
  balance: 1000,
  balanceBase: 1000,
  clearedBalance: 1000,
  clearedBalanceBase: 1000,
  role: 'cash',
  holdingsBase: 0,
}

const CATEGORIES: Category[] = [
  { id: 'rent', label: 'Rent', color: '#e2554f', kind: 'expense' },
  { id: 'fees', label: 'Bank Fees', color: '#e2554f', kind: 'expense' },
]

function incomeRow(over: Partial<ApiTxn>): ApiTxn {
  return {
    id: 'txn-a',
    date: '2026-08-25',
    kind: 'income',
    category: 'rent',
    payee_id: 'pay-a',
    description: 'PayNow received',
    memo: '',
    from_account_id: null,
    from_amount: null,
    from_currency: null,
    to_account_id: 'cash-1',
    to_amount: 131.58,
    to_currency: 'SGD',
    base_amount: 131.58,
    base_currency: 'SGD',
    cleared: false,
    needs_review: true,
    source: 'automation',
    created_at: '2026-08-25T06:29:03Z',
    external_refs: [],
    ...over,
  } as ApiTxn
}

function renderMode(over: Partial<React.ComponentProps<typeof TransactionReviewMode>> = {}) {
  const props: React.ComponentProps<typeof TransactionReviewMode> = {
    open: true,
    scope: 'automation',
    accounts: [CASH],
    categories: CATEGORIES,
    payeeNames: { 'pay-a': 'Tenant A', 'pay-b': 'Tenant B' },
    onClose: vi.fn(),
    onApprove: vi.fn(),
    onReject: vi.fn(),
    onAllDetails: vi.fn(),
    onEditAll: vi.fn(),
    ...over,
  }
  return { ...render(<TransactionReviewMode {...props} />), props }
}

function pressedChip(root: HTMLElement): string | null {
  return root.querySelector('[aria-pressed="true"]')?.textContent ?? null
}

afterEach(cleanup)

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      transactions: [
        incomeRow({ id: 'txn-a', payee_id: 'pay-a' }),
        incomeRow({ id: 'txn-b', payee_id: 'pay-b', created_at: '2026-08-24T06:29:03Z' }),
      ],
      total: 2,
      has_more: false,
    }),
  } as Response)
})

it('an unsaved pick does not come back when the review is closed and reopened', async () => {
  const view = renderMode()
  await screen.findByRole('heading', { name: 'Tenant A' })
  fireEvent.click(screen.getByRole('button', { name: 'Other' }))
  fireEvent.click(
    within(screen.getByTestId('review-category-results')).getByRole('button', {
      name: 'Bank Fees',
    }),
  )
  expect(pressedChip(screen.getByTestId('transaction-review-card'))).toBe('Bank Fees')

  // Closed without approving: nothing was saved, the row is still Rent on the server.
  view.rerender(<TransactionReviewMode {...view.props} open={false} />)
  view.rerender(<TransactionReviewMode {...view.props} open />)
  await screen.findByRole('heading', { name: 'Tenant A' })
  expect(pressedChip(screen.getByTestId('transaction-review-card'))).toBe('Rent')
})

it('the card behind shows the category the card will have when it comes forward', async () => {
  let failApproval: (error: Error) => void = () => {}
  const onApprove = vi.fn(
    () =>
      new Promise<never>((_, reject) => {
        failApproval = reject
      }),
  )
  renderMode({ onApprove })
  await screen.findByRole('heading', { name: 'Tenant A' })
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await screen.findByRole('heading', { name: 'Tenant B' })
  fireEvent.click(screen.getByRole('button', { name: 'Other' }))
  fireEvent.click(
    within(screen.getByTestId('review-category-results')).getByRole('button', {
      name: 'Bank Fees',
    }),
  )

  // A failed save puts its row back in FRONT, so the edited card is now the one behind it.
  failApproval(new Error('The transaction changed'))
  await screen.findByRole('heading', { name: 'Tenant A' })
  const behind = pressedChip(screen.getByTestId('transaction-review-card-rear'))
  fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
  await screen.findByRole('heading', { name: 'Tenant B' })
  expect(pressedChip(screen.getByTestId('transaction-review-card'))).toBe('Bank Fees')
  expect(behind).toBe('Bank Fees')
})
