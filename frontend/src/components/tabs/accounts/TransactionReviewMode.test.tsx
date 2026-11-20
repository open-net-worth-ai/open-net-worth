// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { TransactionReviewMode, type ReviewDraft } from './TransactionReviewMode'
import { fromApiTxn } from './data'
import type { Account, ApiTxn, Category, Txn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

// Your tag list loads with the review (its own cached query); stubbed so the queued fetch
// responses below stay the review's own, in order.
const KNOWN_TAGS: string[] = []
vi.mock('./tagOptions', async (orig) => ({
  ...(await orig<typeof import('./tagOptions')>()),
  useTransactionTags: vi.fn(() => KNOWN_TAGS),
}))
import { useTransactionTags } from './tagOptions'

function okJson(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

const ACCOUNTS: [Account, Account] = [
  {
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
  },
  {
    id: 'brokerage-1',
    name: 'Brokerage USD',
    kind: 'asset',
    type: 'investment',
    institution: 'Meridian',
    currency: 'USD',
    initialBalance: 0,
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
    balance: 1000,
    balanceBase: 1350,
    clearedBalance: 1000,
    clearedBalanceBase: 1350,
    role: 'securities',
    holdingsBase: 0,
  },
]

const USD_SAVINGS: Account = {
  ...ACCOUNTS[1],
  id: 'savings-usd',
  name: 'Savings USD',
  type: 'cash',
  role: 'cash',
}

const CATEGORIES: Category[] = [
  { id: 'groceries', label: 'Groceries', color: '#34b08a', kind: 'expense' },
  { id: 'dining', label: 'Dining out', color: '#e0a032', kind: 'expense' },
]

function apiTxn(over: Partial<ApiTxn> = {}): ApiTxn {
  return {
    id: 'txn-new',
    date: '2026-09-17',
    kind: 'expense',
    category: 'groceries',
    payee_id: 'pay-kroger',
    description: 'Kroger #184',
    memo: 'Weekly shop',
    from_account_id: 'cash-1',
    from_amount: 113.29,
    from_currency: 'SGD',
    to_account_id: null,
    to_amount: null,
    to_currency: null,
    base_amount: 113.29,
    base_currency: 'SGD',
    cleared: false,
    needs_review: true,
    source: 'automation',
    created_at: '2026-09-18T03:00:00Z',
    external_refs: [{ source: 'csv', ref: 'row-1' }],
    ...over,
  } as ApiTxn
}

function renderMode(over: Partial<React.ComponentProps<typeof TransactionReviewMode>> = {}) {
  const props: React.ComponentProps<typeof TransactionReviewMode> = {
    open: true,
    scope: 'automation',
    refreshSignal: 0,
    accounts: ACCOUNTS,
    categories: CATEGORIES,
    payeeNames: {
      'pay-kroger': 'Kroger',
      'pay-old': 'Old Shop',
      'pay-second': 'Second Shop',
      'pay-next': 'Next Shop',
      'pay-vnd': 'Hoang Thi Thu Phuong',
    },
    onClose: vi.fn(),
    onApprove: vi.fn(async (txn: Txn) => ({ ...txn, needsReview: false })),
    onReject: vi.fn(async () => undefined),
    onAskLucius: vi.fn(async () => undefined),
    onAllDetails: vi.fn(),
    onEditAll: vi.fn(),
    ...over,
  }
  return { ...render(<TransactionReviewMode {...props} />), props }
}

it('changes category directly from the review card and includes it in approval', async () => {
  const onApprove = vi.fn(async (txn: Txn) => ({ ...txn, needsReview: false }))
  renderMode({ onApprove })
  await screen.findByRole('heading', { name: 'Kroger' })
  fireEvent.click(screen.getByRole('button', { name: 'Other' }))
  fireEvent.click(screen.getByRole('button', { name: 'Dining out' }))
  expect(screen.queryByLabelText('Amount')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() =>
    expect(onApprove).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'txn-new' }),
      expect.objectContaining({ category: 'dining' }),
    ),
  )
})

it('shows queue category chips and a rear card only while another transaction is available', async () => {
  mockAuthFetch.mockResolvedValue(
    okJson({ transactions: [apiTxn(), apiTxn({ id: 'second', category: 'dining' })], total: 2 }),
  )
  const { container } = renderMode()
  await screen.findByRole('heading', { name: 'Kroger' })
  expect(container.ownerDocument.querySelector('.acct-review-deck.has-next')).not.toBeNull()
  const choice = screen.getByRole('button', { name: 'Dining out' })
  fireEvent.click(choice)
  expect(choice.getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await screen.findByText('1 remaining')
  expect(container.ownerDocument.querySelector('.acct-review-deck.has-next')).toBeNull()
})

beforeEach(() => {
  vi.clearAllMocks()
  mockAuthFetch.mockReset()
  mockAuthFetch.mockResolvedValue(
    okJson({
      transactions: [
        apiTxn(),
        apiTxn({
          id: 'txn-old',
          description: 'Old row',
          memo: '',
          payee_id: 'pay-old',
          created_at: '2026-09-18T01:00:00Z',
        }),
      ],
      total: 2,
      has_more: false,
    }),
  )
})

afterEach(cleanup)

describe('TransactionReviewMode — compact queue', () => {
  it('loads the global queue by review state and shows the newest card with important facts first', async () => {
    renderMode()

    expect(screen.getByRole('status').textContent).toContain('Loading transactions')
    await screen.findByRole('heading', { name: 'Kroger' })

    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/transactions/recently-added?needs_review=true&automation_only=true&limit=500',
    )
    expect(screen.getByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
    // The rear card previews the NEXT row (same fixture facts), so read the front card only.
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.getByText('Everyday Checking')).toBeTruthy()
    expect(front.getByText('Added by automation')).toBeTruthy()
    expect(front.getByText('113.29')).toBeTruthy()
    expect(front.getByText('Sep 17, 2026')).toBeTruthy()
    expect(front.getByText('Groceries')).toBeTruthy()
    // The memo wins the one detail line; the bank's string steps aside.
    expect(front.getByText('Weekly shop')).toBeTruthy()
    expect(front.getByLabelText('Memo')).toBeTruthy()
    expect(front.queryByText('Kroger #184')).toBeNull()
    expect(screen.queryByLabelText('Amount')).toBeNull()
    expect(screen.queryByLabelText('Cleared')).toBeNull()
    expect(screen.queryByText('Not cleared')).toBeNull()
    expect(screen.getByText('2 remaining')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Old Shop' })).toBeNull()
    expect(screen.queryByText('Activity')).toBeNull()
    expect(screen.queryByText('Receipts')).toBeNull()
    expect(screen.queryByText('Source emails')).toBeNull()
  })

  it('names the automation that fired the row and formats the amount in its currency', async () => {
    const vndWallet: Account = {
      ...ACCOUNTS[0],
      id: 'cash-vnd',
      name: 'VND Wallet',
      currency: 'VND',
    }
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-vnd',
            description: 'Hoang Thi Thu Phuong',
            payee_id: 'pay-vnd',
            from_account_id: 'cash-vnd',
            from_amount: 410008,
            from_currency: 'VND',
            base_amount: 21.36,
            base_currency: 'SGD',
            via: 'Bank SMS matcher',
            automation_id: 'auto_sms',
          }),
          apiTxn({ id: 'txn-plain', description: 'Plain row', payee_id: null }),
        ],
        total: 2,
        has_more: false,
      }),
    )
    renderMode({ accounts: [...ACCOUNTS, vndWallet] })
    await screen.findByRole('heading', { name: 'Hoang Thi Thu Phuong' })
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.getByText('410,008')).toBeTruthy()
    expect(front.queryByText('410,008.00')).toBeNull()
    expect(front.getByText(/≈ .*21\.36 in SGD/)).toBeTruthy()
    expect(within(screen.getByTestId('transaction-review-card-rear')).queryByText(/≈/)).toBeNull()
    expect(front.getByText('Added by Bank SMS matcher')).toBeTruthy()
    expect(front.queryByText(/Added by automation/)).toBeNull()
    const rear = within(screen.getByTestId('transaction-review-card-rear'))
    expect(rear.getByText('113.29')).toBeTruthy()
    expect(rear.getByText('Added by automation')).toBeTruthy()
  })

  it('searches categories as chips, not a native select, and hides non-matches', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    // The picker REPLACES the chips: one list, the chosen category at its top, no caption.
    // (Scoped to the front card — the rear preview still shows its own chip row.)
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.queryByText('Choose a category')).toBeNull()
    expect(front.queryByRole('button', { name: 'Other' })).toBeNull()
    const results = within(screen.getByTestId('review-category-results'))
    expect(results.queryByRole('button', { name: 'Uncategorized' })).toBeNull()
    expect(results.getAllByRole('button').map((chip) => chip.textContent)).toEqual([
      'Groceries',
      'Dining out',
    ])
    expect(results.getByRole('button', { name: 'Groceries' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    fireEvent.change(screen.getByLabelText('Search categories'), { target: { value: 'din' } })
    expect(results.queryByRole('button', { name: 'Groceries' })).toBeNull()
    fireEvent.click(results.getByRole('button', { name: 'Dining out' }))
    expect(screen.queryByTestId('review-category-results')).toBeNull()
    expect(front.getByText('Choose a category')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Dining out' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('says a row has no payee instead of dressing the bank description as one', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({ id: 'txn-raw', payee_id: null, description: 'RAW BANK MERCHANT 184', memo: '' }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })

    expect(await screen.findByRole('heading', { name: 'No payee yet' })).toBeTruthy()
    const front = within(screen.getByTestId('transaction-review-card'))
    // The bank string still identifies the row — in the details, not posing as the payee.
    expect(front.getByText('RAW BANK MERCHANT 184')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[1].payee).toBe('')
    expect(onApprove.mock.calls[0]?.[1].payeeEdited).toBe(false)
  })

  it('falls back to the bank description when the row has no memo', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({ transactions: [apiTxn({ memo: '' })], total: 1, has_more: false }),
    )
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.getByText('Kroger #184')).toBeTruthy()
    expect(front.getByLabelText('Bank description')).toBeTruthy()
    expect(front.queryByLabelText('Memo')).toBeNull()
  })

  it("formats a foreign charge's original amount in its currency's own digits", async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-orig',
            description: 'Hotel charge',
            original_amount: 963900,
            original_currency: 'VND',
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    renderMode()
    const front = within(await screen.findByTestId('transaction-review-card'))
    expect(await front.findByText('Originally 963,900 VND')).toBeTruthy()
  })

  it('shows a transfer its received leg, never a base-currency value', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-fx',
            kind: 'transfer',
            description: 'Transfer to Brokerage',
            category: null,
            payee_id: null,
            from_amount: 100,
            from_currency: 'SGD',
            to_account_id: 'brokerage-1',
            to_amount: 74,
            to_currency: 'USD',
            base_amount: 128.4,
            base_currency: 'EUR',
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    renderMode()
    await screen.findByRole('heading', { name: /Transfer to Brokerage USD/ })
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.getByText('74.00 USD')).toBeTruthy()
    expect(front.queryByText(/≈/)).toBeNull()
    expect(front.queryByText('−')).toBeNull()
  })

  it('previews the next card with ITS OWN chip suggestions, not the front card’s', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({ id: 'exp-1', kind: 'expense', category: 'dining', payee_id: 'pay-kroger' }),
          apiTxn({
            id: 'inc-1',
            kind: 'income',
            category: 'salary',
            payee_id: 'pay-old',
            from_account_id: null,
            from_amount: null,
            from_currency: null,
            to_account_id: 'cash-1',
            to_amount: 50,
            to_currency: 'SGD',
          }),
          apiTxn({
            id: 'inc-2',
            kind: 'income',
            category: 'interest',
            payee_id: 'pay-second',
            from_account_id: null,
            from_amount: null,
            from_currency: null,
            to_account_id: 'cash-1',
            to_amount: 5,
            to_currency: 'SGD',
          }),
        ],
        total: 3,
        has_more: false,
      }),
    )
    renderMode({
      categories: [
        ...CATEGORIES,
        { id: 'salary', label: 'Salary', color: '#34b08a', kind: 'income' },
        { id: 'interest', label: 'Interest', color: '#4488bb', kind: 'income' },
      ],
    })
    await screen.findByRole('heading', { name: 'Kroger' })

    // `hidden`: the rear card is aria-hidden and inert by design.
    const chips = (card: HTMLElement) =>
      within(card)
        .getAllByRole('button', { hidden: true })
        .map((chip) => chip.textContent)
        .filter(
          (label) =>
            label !== 'Details' && label !== 'Edit' && label !== 'Other' && label !== 'Tag',
        )
    const rear = screen.getByTestId('transaction-review-card-rear')
    // The rear card is the income: its own selection first, then the income ranking.
    expect(chips(rear)).toEqual(['Salary', 'Interest'])
    const previewed = chips(rear)

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('heading', { name: 'Old Shop' })
    // In front, the same card shows exactly what it previewed — nothing changed hands.
    expect(chips(screen.getByTestId('transaction-review-card'))).toEqual(previewed)
  })

  it('keeps a card’s category chips fixed while the queue around it shrinks', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({ id: 'txn-a', payee_id: 'pay-old', category: 'groceries' }),
          apiTxn({ id: 'txn-b', payee_id: 'pay-second', category: 'dining' }),
          apiTxn({ id: 'txn-c', payee_id: 'pay-next', category: 'dining' }),
        ],
        total: 3,
        has_more: false,
      }),
    )
    renderMode()
    await screen.findByRole('heading', { name: 'Old Shop' })
    const chipsOf = () =>
      within(screen.getByTestId('transaction-review-card'))
        .getAllByRole('button')
        .map((chip) => chip.textContent)
        .filter((label) => label !== 'Edit')
        .sort()
    const before = chipsOf()
    expect(before).toContain('Dining out')

    // Approving removes a row that the suggestion ranking used to count.
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('heading', { name: 'Second Shop' })
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('heading', { name: 'Next Shop' })
    // The same chips, so no row appears or disappears under the card being reviewed. Their
    // ORDER still follows each card's own selection, which leads.
    expect(chipsOf()).toEqual(before)
  })

  it('lets an income row be categorised as a refund against an expense category', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [apiTxn({ id: 'txn-refund', kind: 'income', category: null })],
        total: 1,
        has_more: false,
      }),
    )
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })

    // Money back on something you spent belongs against that spending, so the EXPENSE
    // categories are offered here too — the same rule the transaction drawer uses.
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    const results = within(screen.getByTestId('review-category-results'))
    fireEvent.click(results.getByRole('button', { name: 'Groceries' }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[1].category).toBe('groceries')
    expect(onApprove.mock.calls[0]?.[1].kind).toBe('income')
  })

  it('keeps a bounded-backlog notice instead of claiming the queue is complete', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({ transactions: [apiTxn()], total: 724, has_more: true }),
    )
    renderMode()

    await screen.findByRole('heading', { name: 'Kroger' })
    expect(screen.getByText('Showing the newest 500. More still need review.')).toBeTruthy()
  })

  it('loads another batch after the visible queue drains even when a row was skipped', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(
        okJson({
          transactions: [
            apiTxn(),
            apiTxn({ id: 'txn-second', description: 'Second row', payee_id: 'pay-second' }),
          ],
          total: 502,
          has_more: true,
        }),
      )
      .mockResolvedValueOnce(
        okJson({
          transactions: [
            apiTxn({ id: 'txn-next-batch', description: 'Next batch row', payee_id: 'pay-next' }),
          ],
          total: 1,
          has_more: false,
        }),
      )
    renderMode()

    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    await screen.findByRole('heading', { name: 'Second Shop' })
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    expect(await screen.findByRole('heading', { name: 'Next Shop' })).toBeTruthy()
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/transactions/recently-added?needs_review=true&automation_only=true&limit=500&offset=1',
    )
    expect(screen.queryByText('You’re all caught up')).toBeNull()
  })

  it('reviews everything flagged when opened on the wide scope, and says so', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [apiTxn({ id: 'txn-import', source: 'csv_import', via: undefined })],
        total: 1,
        has_more: false,
      }),
    )
    renderMode({ scope: 'all' })
    await screen.findByRole('heading', { name: 'Kroger' })
    // No automation_only: imports and chat-added rows are in this queue too.
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/transactions/recently-added?needs_review=true&limit=500',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText('You’re all caught up')).toBeTruthy()
    expect(screen.getByText('No transactions are waiting for review.')).toBeTruthy()
  })

  it('throws confetti when the reviewer clears the deck, never on an already-empty queue', async () => {
    mockAuthFetch.mockResolvedValue(okJson({ transactions: [apiTxn()], total: 1, has_more: false }))
    const { unmount } = renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    expect(screen.queryByTestId('review-confetti')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText('You’re all caught up')).toBeTruthy()
    expect(screen.getByTestId('review-confetti')).toBeTruthy()
    unmount()

    mockAuthFetch.mockResolvedValue(okJson({ transactions: [], total: 0, has_more: false }))
    renderMode()
    expect(await screen.findByText('You’re all caught up')).toBeTruthy()
    expect(screen.queryByTestId('review-confetti')).toBeNull()
  })

  it('throws no confetti when a pass ends with skipped cards still waiting', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [apiTxn(), apiTxn({ id: 'txn-second', payee_id: 'pay-second' })],
        total: 2,
        has_more: false,
      }),
    )
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('heading', { name: 'Second Shop' })
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(await screen.findByText('That’s everything for now')).toBeTruthy()
    expect(screen.queryByTestId('review-confetti')).toBeNull()
  })

  it('closes the picker without changing the pick, from its ✕ and from Escape', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Search categories'), { target: { value: 'din' } })
    fireEvent.click(screen.getByRole('button', { name: 'Close search' }))
    expect(screen.queryByTestId('review-category-results')).toBeNull()
    expect(screen.getByRole('button', { name: 'Groceries' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    // A reopened picker starts clean, not on the last query.
    expect((screen.getByLabelText('Search categories') as HTMLInputElement).value).toBe('')
    fireEvent.keyDown(screen.getByLabelText('Search categories'), { key: 'Escape' })
    expect(screen.queryByTestId('review-category-results')).toBeNull()
  })

  it('shows both transfer legs on the card and hands Edit to the full editor', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-transfer',
            kind: 'transfer',
            description: 'Transfer to Brokerage',
            category: null,
            payee_id: null,
            to_account_id: 'brokerage-1',
            to_amount: 110,
            to_currency: 'USD',
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    const onEditAll = vi.fn()
    renderMode({ onEditAll })

    await screen.findByRole('heading', { name: /Transfer to Brokerage USD/ })
    expect(screen.getByText('110.00 USD')).toBeTruthy()
    expect(screen.queryByLabelText('Amount')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEditAll).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'txn-transfer' }),
      expect.objectContaining({ kind: 'transfer' }),
    )
  })
  it('drops a card the drawer marked reviewed, and patches one the drawer edited, in place', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({ id: 'txn-a', payee_id: 'pay-kroger' }),
          apiTxn({ id: 'txn-b', payee_id: 'pay-old' }),
        ],
        total: 2,
        has_more: false,
      }),
    )
    const { rerender, props } = renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const fetches = mockAuthFetch.mock.calls.length

    // The drawer edited the front card's description: the card shows it, no reload.
    const edited = fromApiTxn(
      apiTxn({ id: 'txn-a', payee_id: 'pay-kroger', description: 'Kroger #999', memo: '' }),
    )
    rerender(<TransactionReviewMode {...props} rowChanged={{ txn: edited, seq: 1 }} />)
    expect(
      within(screen.getByTestId('transaction-review-card')).getByText('Kroger #999'),
    ).toBeTruthy()
    expect(screen.getByText('2 remaining')).toBeTruthy()

    // The drawer marked it reviewed: the next card comes to the front, the count drops.
    const reviewed = { ...edited, needsReview: false }
    rerender(<TransactionReviewMode {...props} rowChanged={{ txn: reviewed, seq: 2 }} />)
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
    expect(screen.getByText('1 remaining')).toBeTruthy()
    expect(mockAuthFetch.mock.calls.length).toBe(fetches)
  })

  it('closes on a tap on the stage’s empty area, never on a tap on the card', async () => {
    const onClose = vi.fn()
    renderMode({ onClose })
    await screen.findByRole('heading', { name: 'Kroger' })
    // The dialog says it may be dismissed from outside (the scrim, on desktop) …
    expect(
      screen
        .getByRole('dialog', { name: 'Review transactions' })
        .getAttribute('data-onw-dismissable'),
    ).toBe('true')
    // … a tap on the card itself does nothing …
    fireEvent.click(screen.getByTestId('transaction-review-card'))
    expect(onClose).not.toHaveBeenCalled()
    // … and a tap on the stage's own empty area (the phone, where the stage is the screen) closes.
    fireEvent.click(screen.getByLabelText('Transaction review keyboard shortcuts'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Details opens the drawer carrying the card picks', async () => {
    const onAllDetails = vi.fn()
    renderMode({ onAllDetails })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dining out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(onAllDetails).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'txn-new' }),
      expect.objectContaining({ category: 'dining' }),
    )
  })

  it('creates a category from the search when nothing matches, and picks it', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    const onCreateCategory = vi.fn(async (name: string, kind: string) => ({
      id: 'cat_new',
      label: name,
      kind,
      color: '#888',
    }))
    renderMode({ onApprove, onCreateCategory } as never)
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Search categories'), { target: { value: 'Pet care' } })
    fireEvent.click(screen.getByRole('button', { name: /Create “Pet care”/ }))
    await waitFor(() => expect(onCreateCategory).toHaveBeenCalledWith('Pet care', 'expense'))
    // An exact name match offers no Create.
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Search categories'), { target: { value: 'groceries' } })
    expect(screen.queryByRole('button', { name: /Create “groceries”/ })).toBeNull()
    fireEvent.click(screen.getByLabelText('Close search'))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ category: 'cat_new' }))
  })

  it('Edit sits beside Details and hands the drawer editor the card draft', async () => {
    const onEditAll = vi.fn()
    renderMode({ onEditAll })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dining out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEditAll).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'txn-new' }),
      expect.objectContaining({ category: 'dining' }),
    )
    // Edit never opens the card in place — that is Details.
    expect(screen.queryByTestId('review-details')).toBeNull()
  })

  it('a tap on the facts opens the drawer — the card edits only category and tags', async () => {
    const onAllDetails = vi.fn()
    renderMode({ onAllDetails })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('heading', { name: 'Kroger' }))
    expect(onAllDetails).toHaveBeenCalledTimes(1)
    // No payee, date or memo field anywhere on the card.
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('lets a transfer card change where the money went, in the source currency only', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-transfer-dest',
            kind: 'transfer',
            description: 'Transfer to savings',
            category: null,
            payee_id: null,
            from_account_id: 'brokerage-1',
            from_amount: 100,
            from_currency: 'USD',
            to_account_id: 'savings-usd',
            to_amount: 100,
            to_currency: 'USD',
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    const USD_CARD: Account = {
      ...USD_SAVINGS,
      id: 'card-usd',
      name: 'Travel Card',
      type: 'credit',
    }
    renderMode({ accounts: [...ACCOUNTS, USD_SAVINGS, USD_CARD], onApprove })

    const front = within(await screen.findByTestId('transaction-review-card'))
    expect(front.getByText('Going to')).toBeTruthy()
    expect(front.queryByText('Choose a category')).toBeNull()
    expect(front.getByRole('button', { name: 'Savings USD', pressed: true })).toBeTruthy()
    // The SGD checking account is not offered, here or behind Other: a cross-currency leg
    // needs its own received amount, which is the editor's job (owner, 2026-09-20).
    expect(front.queryByRole('button', { name: 'Everyday Checking' })).toBeNull()
    fireEvent.click(front.getByRole('button', { name: 'Other' }))
    expect(
      within(screen.getByTestId('review-account-results')).queryByRole('button', {
        name: 'Everyday Checking',
      }),
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Close search' }))

    fireEvent.click(front.getByRole('button', { name: 'Travel Card' }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[1].toAccountId).toBe('card-usd')
    expect(onApprove.mock.calls[0]?.[1].toAmount).toBe(100)
  })

  it('shows an unequal same-currency received leg before approval', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-same-currency-transfer',
            kind: 'transfer',
            description: 'Transfer to savings',
            category: null,
            payee_id: null,
            from_account_id: 'brokerage-1',
            from_amount: 100,
            from_currency: 'USD',
            to_account_id: 'savings-usd',
            to_amount: 98,
            to_currency: 'USD',
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    renderMode({ accounts: [...ACCOUNTS, USD_SAVINGS] })

    expect(await screen.findByText('98.00 USD')).toBeTruthy()
  })

  it('shows split transactions honestly and keeps total/category edits in the full editor', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson({
        transactions: [
          apiTxn({
            id: 'txn-split',
            category: 'split',
            splits: [
              { category: 'groceries', amount: 60 },
              { category: 'dining', amount: 53.29 },
            ],
          }),
        ],
        total: 1,
        has_more: false,
      }),
    )
    const onEditAll = vi.fn()
    renderMode({ onEditAll })

    // The split's own lines, read-only, with their figures — not a lone pill.
    await screen.findByText('Split across 2 categories')
    const front = within(screen.getByTestId('transaction-review-card'))
    expect(front.getByText('Groceries').textContent).toBe('Groceries60.00 SGD')
    expect(front.getByText('Dining out').textContent).toBe('Dining out53.29 SGD')
    expect(screen.queryByRole('button', { name: 'Other' })).toBeNull()
    expect(screen.queryByText('Choose a category')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(onEditAll).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'txn-split' }),
      expect.anything(),
    )
  })
})

describe('TransactionReviewMode — edits and actions', () => {
  it('submits the card draft and review decision together, then advances', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dining out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[0].id).toBe('txn-new')
    expect(onApprove.mock.calls[0]?.[1]).toEqual<ReviewDraft>({
      kind: 'expense',
      accountId: 'cash-1',
      toAccountId: '',
      investmentDirection: '',
      amount: 113.29,
      toAmount: 113.29,
      originalAmount: '',
      originalCurrency: '',
      date: '2026-09-17',
      payee: 'Kroger',
      payeeEdited: false,
      category: 'dining',
      memo: 'Weekly shop',
      tags: [],
      cleared: false,
    })
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
    expect(screen.getByText('1 remaining')).toBeTruthy()
  })

  it('advances at once and brings the row back with its error when the save fails', async () => {
    const onApprove = vi
      .fn()
      .mockRejectedValueOnce(new Error('The transaction changed — reload and try again'))
      .mockImplementation(async (txn: Txn) => ({ ...txn, needsReview: false }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    // The next card is shown immediately — the save runs behind it.
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()

    // …and the row that failed comes back to the front, carrying the reason.
    expect(await screen.findByRole('heading', { name: 'Kroger' })).toBeTruthy()
    expect((await screen.findByRole('alert')).textContent).toContain(
      'The transaction changed — reload and try again',
    )
    expect(screen.getByText('2 remaining')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Retry approval' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
  })

  it('skips without writing and keeps the skipped row pending on a fresh open', async () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    const view = renderMode({ onApprove, onReject })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
    expect(screen.getByText('2 remaining')).toBeTruthy()
    expect(onApprove).not.toHaveBeenCalled()
    expect(onReject).not.toHaveBeenCalled()

    view.rerender(<TransactionReviewMode {...view.props} open={false} />)
    view.rerender(<TransactionReviewMode {...view.props} open />)
    expect(await screen.findByRole('heading', { name: 'Kroger' })).toBeTruthy()
  })

  it('requires confirmation before rejecting and advances only after deletion resolves', async () => {
    const onReject = vi.fn(async () => undefined)
    renderMode({ onReject })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))

    const confirm = screen.getByRole('dialog', { name: 'Reject transaction?' })
    expect(within(confirm).getByText(/Kroger/)).toBeTruthy()
    expect(onReject).not.toHaveBeenCalled()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Keep transaction' }))
    expect(screen.getByRole('heading', { name: 'Kroger' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Reject transaction?' })).getByRole('button', {
        name: 'Reject transaction',
      }),
    )
    await waitFor(() =>
      expect(onReject).toHaveBeenCalledWith(expect.objectContaining({ id: 'txn-new' })),
    )
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
  })

  it('keeps a failed rejection in the reject flow and retries deletion, never approval', async () => {
    const onApprove = vi.fn()
    const onReject = vi
      .fn()
      .mockRejectedValueOnce(new Error('Deletion failed'))
      .mockResolvedValueOnce(undefined)
    renderMode({ onApprove, onReject })
    await screen.findByRole('heading', { name: 'Kroger' })

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    let confirm = screen.getByRole('dialog', { name: 'Reject transaction?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Reject transaction' }))
    expect((await within(confirm).findByRole('alert')).textContent).toContain('Deletion failed')
    expect(screen.queryByRole('button', { name: 'Retry approval' })).toBeNull()

    confirm = screen.getByRole('dialog', { name: 'Reject transaction?' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Reject transaction' }))
    await waitFor(() => expect(onReject).toHaveBeenCalledTimes(2))
    expect(onApprove).not.toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Old Shop' })).toBeTruthy()
  })

  it('flies out before approving on a right swipe, skips on a left swipe, and ignores short drags', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    const card = screen.getByTestId('transaction-review-card')

    fireEvent.pointerDown(card, { pointerId: 1, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(card, { pointerId: 1, clientX: 145, clientY: 102 })
    fireEvent.pointerUp(card, { pointerId: 1, clientX: 145, clientY: 102 })
    expect(onApprove).not.toHaveBeenCalled()

    fireEvent.pointerDown(card, { pointerId: 2, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(card, { pointerId: 2, clientX: 245, clientY: 102 })
    fireEvent.pointerUp(card, { pointerId: 2, clientX: 245, clientY: 102 })
    expect(onApprove).not.toHaveBeenCalled()
    expect(card.classList.contains('is-exiting-right')).toBe(true)
    // The rear card rises into the vacated place while the approval is in flight.
    expect(card.closest('.acct-review-deck')?.classList.contains('is-advancing')).toBe(true)
    fireEvent.transitionEnd(card, { propertyName: 'transform' })
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))

    const nextCard = screen.getByTestId('transaction-review-card')
    fireEvent.pointerDown(nextCard, { pointerId: 3, clientX: 200, clientY: 100 })
    fireEvent.pointerMove(nextCard, { pointerId: 3, clientX: 55, clientY: 102 })
    fireEvent.pointerUp(nextCard, { pointerId: 3, clientX: 55, clientY: 102 })
    expect(screen.queryByText('That’s everything for now')).toBeNull()
    fireEvent.transitionEnd(nextCard, { propertyName: 'transform' })
    expect(await screen.findByText('That’s everything for now')).toBeTruthy()
    expect(screen.getByText('1 skipped transaction still needs review.')).toBeTruthy()
  })

  it('previews the next transaction on a rear card that shares the front card surface and is hidden from assistive tech', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const rear = screen.getByTestId('transaction-review-card-rear')
    expect(rear.classList.contains('acct-review-card')).toBe(true)
    expect(rear.getAttribute('aria-hidden')).toBe('true')
    expect(within(rear).getByText('Old row')).toBeTruthy()
    expect(within(rear).getByText('Everyday Checking')).toBeTruthy()
    expect(within(rear).getByText('Groceries')).toBeTruthy()
    // The SAME body as the front card — chips and the Details pill included — so a swipe
    // reveals a whole card, not a reduced one. None of it is reachable or announced.
    expect(within(rear).getByText('Choose a category')).toBeTruthy()
    expect(within(rear).getByText('Details')).toBeTruthy()
    expect(within(rear).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Old Shop' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await screen.findByRole('heading', { name: 'Old Shop' })
    expect(screen.queryByTestId('transaction-review-card-rear')).toBeNull()
  })

  it('names the outcome on the card while dragging: Approve to the right, Skip for now to the left', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const card = screen.getByTestId('transaction-review-card')
    expect(screen.queryByTestId('review-swipe-badge')).toBeNull()

    fireEvent.pointerDown(card, { pointerId: 7, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(card, { pointerId: 7, clientX: 120, clientY: 101 })
    expect(screen.queryByTestId('review-swipe-badge')).toBeNull()
    const wash = screen.getByTestId('review-swipe-wash')
    expect(wash.classList.contains('is-approve')).toBe(true)
    expect(Number.parseFloat(wash.style.opacity)).toBeGreaterThan(0)
    fireEvent.pointerMove(card, { pointerId: 7, clientX: 180, clientY: 101 })
    let badge = screen.getByTestId('review-swipe-badge')
    expect(badge.textContent).toBe('Approve')
    expect(badge.classList.contains('is-approve')).toBe(true)

    fireEvent.pointerMove(card, { pointerId: 7, clientX: 20, clientY: 101 })
    badge = screen.getByTestId('review-swipe-badge')
    expect(badge.textContent).toBe('Skip for now')
    expect(badge.classList.contains('is-skip')).toBe(true)

    fireEvent.pointerUp(card, { pointerId: 7, clientX: 100, clientY: 101 })
    expect(screen.queryByTestId('review-swipe-badge')).toBeNull()
    expect(screen.queryByTestId('review-swipe-wash')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Kroger' })).toBeTruthy()
  })

  it('records pointer versus keyboard input on the shortcut surface so a swipe never leaves a focus ring', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const stage = screen.getByLabelText('Transaction review keyboard shortcuts')
    expect(stage.getAttribute('data-input')).toBeNull()
    fireEvent.pointerDown(screen.getByTestId('transaction-review-card'), {
      pointerId: 9,
      clientX: 100,
      clientY: 100,
    })
    expect(stage.getAttribute('data-input')).toBe('pointer')
    fireEvent.keyDown(stage, { key: 'Shift' })
    expect(stage.getAttribute('data-input')).toBe('keyboard')
  })

  it('no longer offers Ask Lucius on the card', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    expect(screen.queryByRole('button', { name: 'Ask Lucius' })).toBeNull()
  })

  it('an untagged card offers only “+ Tag” in its bottom row, and the Tags row once tagged', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const card = within(screen.getByTestId('transaction-review-card'))
    expect(card.queryByText('Tags')).toBeNull()
    fireEvent.click(card.getByRole('button', { name: 'Tag' }))
    fireEvent.change(card.getByLabelText('Find or create a tag'), { target: { value: 'gift' } })
    fireEvent.click(card.getByRole('button', { name: /Create “gift”/ }))
    fireEvent.click(card.getByRole('button', { name: 'Done' }))
    // Tagged: the row stays, with its own "+ Tag"; the bottom row's is gone.
    expect(card.getByText('Tags')).toBeTruthy()
    expect(card.getByRole('button', { name: 'Remove tag gift' })).toBeTruthy()
    expect(card.getAllByRole('button', { name: 'Tag' })).toHaveLength(1)
  })

  it('loads your tags with the review, before the first "+ Tag"', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    expect(screen.queryByLabelText('Find or create a tag')).toBeNull()
    expect(useTransactionTags).toHaveBeenCalled()
  })

  it('tags picked on the card are approved with the category', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    fireEvent.click(screen.getByRole('button', { name: 'Tag' }))
    fireEvent.change(screen.getByLabelText('Find or create a tag'), {
      target: { value: 'Work Trip' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create “work-trip”/ }))
    expect(screen.getByRole('button', { name: 'Remove tag work-trip' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
    expect(onApprove.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ tags: ['work-trip'] }))
  })

  it('swipes from a category chip, and a tap on one still selects it', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    const chip = screen.getByRole('button', { name: 'Groceries' })

    // A tap: no travel, so the chip takes it.
    fireEvent.pointerDown(chip, { pointerId: 10, clientX: 100, clientY: 100 })
    fireEvent.pointerUp(chip, { pointerId: 10, clientX: 100, clientY: 100 })
    fireEvent.click(chip)
    expect(chip.getAttribute('aria-pressed')).toBe('true')
    expect(onApprove).not.toHaveBeenCalled()

    // A drag that starts on the same chip swipes the card instead.
    const card = screen.getByTestId('transaction-review-card')
    fireEvent.pointerDown(chip, { pointerId: 11, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(chip, { pointerId: 11, clientX: 245, clientY: 102 })
    fireEvent.pointerUp(chip, { pointerId: 11, clientX: 245, clientY: 102 })
    expect(card.classList.contains('is-exiting-right')).toBe(true)
    fireEvent.transitionEnd(card, { propertyName: 'transform' })
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))
  })

  it('swipes from an untouched field, but not from the one being typed in', async () => {
    renderMode()
    await screen.findByRole('heading', { name: 'Kroger' })
    const card = screen.getByTestId('transaction-review-card')
    fireEvent.click(screen.getByRole('button', { name: 'Tag' }))
    const search = screen.getByLabelText('Find or create a tag')

    // Being typed in: the drag is the field's (caret, selection) — the card stays.
    search.focus()
    fireEvent.pointerDown(search, { pointerId: 20, clientX: 300, clientY: 100 })
    fireEvent.pointerMove(search, { pointerId: 20, clientX: 150, clientY: 102 })
    fireEvent.pointerUp(search, { pointerId: 20, clientX: 150, clientY: 102 })
    expect(card.classList.contains('is-exiting-left')).toBe(false)

    // Untouched: just part of the card — a left drag skips it.
    search.blur()
    fireEvent.pointerDown(search, { pointerId: 21, clientX: 300, clientY: 100 })
    fireEvent.pointerMove(search, { pointerId: 21, clientX: 150, clientY: 102 })
    fireEvent.pointerUp(search, { pointerId: 21, clientX: 150, clientY: 102 })
    expect(card.classList.contains('is-exiting-left')).toBe(true)
  })

  it('maps ArrowRight and ArrowLeft outside fields while leaving field arrows alone', async () => {
    const onApprove = vi.fn<(txn: Txn, draft: ReviewDraft) => Promise<Txn>>(async (txn) => ({
      ...txn,
      needsReview: false,
    }))
    renderMode({ onApprove })
    await screen.findByRole('heading', { name: 'Kroger' })
    const shortcutSurface = screen.getByLabelText('Transaction review keyboard shortcuts')
    await waitFor(() => expect(document.activeElement).toBe(shortcutSurface))
    fireEvent.keyDown(shortcutSurface, { key: 'ArrowRight' })
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1))

    await screen.findByRole('heading', { name: 'Old Shop' })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.keyDown(screen.getByLabelText('Search categories'), { key: 'ArrowLeft' })
    expect(screen.queryByText('That’s everything for now')).toBeNull()
    fireEvent.keyDown(screen.getByLabelText('Transaction review keyboard shortcuts'), {
      key: 'ArrowLeft',
    })
    expect(await screen.findByText('That’s everything for now')).toBeTruthy()
  })

  it('shows a recoverable load error and retries the same request', async () => {
    mockAuthFetch
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(okJson({ transactions: [apiTxn()], total: 1, has_more: false }))
    renderMode()
    expect(await screen.findByText(/Couldn.t load transactions for review/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { name: 'Kroger' })).toBeTruthy()
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
  })

  it('shows the honest empty completion state', async () => {
    mockAuthFetch.mockResolvedValue(okJson({ transactions: [], total: 0, has_more: false }))
    const onClose = vi.fn()
    renderMode({ onClose })
    expect(await screen.findByText('You’re all caught up')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
