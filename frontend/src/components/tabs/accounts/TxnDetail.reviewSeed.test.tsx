// @vitest-environment jsdom
//
// The drawer opened from a review card's "All details": it starts from the card's unsaved
// picks (EditSeed), and leaving the editor with unsaved edits asks before dropping them — a
// stray swipe or Escape on a phone used to lose them silently (2026-11-19).
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail, { type EditSeed } from './TxnDetail'
import { reviewDraftSeed, startingReviewDraft } from './transactionReview'
import type { Account, Txn } from './types'

vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async () => ({ ok: true, json: async () => ({ tags: [] }) })),
}))

afterEach(cleanup)

const ACCT = {
  id: 'a1',
  name: 'Harborview Checking',
  kind: 'asset',
  type: 'cash',
  institution: '',
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
  balance: 0,
  balanceBase: 0,
  clearedBalance: 0,
  clearedBalanceBase: 0,
} as unknown as Account

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-15',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'Corner Grocer',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 42,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 42,
    baseCurrency: 'USD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-15',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderEditor(txn: Txn, seed?: EditSeed) {
  const onClose = vi.fn()
  render(
    <TxnDetail
      txn={txn}
      initialMode="edit"
      editSeed={seed}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="USD"
      rates={{}}
      onClose={onClose}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'manual',
          addedBy: 'You',
          createdAt: '2026-06-15',
          updatedAt: '2026-06-15',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
  return { onClose }
}

const escape = () =>
  fireEvent.keyDown(document.querySelector('.acct-detail-wrap') as Element, { key: 'Escape' })

describe('TxnDetail — seeded from the review card', () => {
  it("opens the editor on the card's picks, not the saved row", () => {
    renderEditor(makeTxn({ memo: 'saved memo' }), {
      memo: 'from the card',
      date: '2026-06-20',
      tags: ['work-trip'],
    })
    expect(screen.getByDisplayValue('from the card')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove tag work-trip' })).toBeTruthy()
  })

  it('carries only what the card changed', () => {
    const txn = makeTxn({ memo: 'm', tags: ['a'] })
    const draft = startingReviewDraft(txn, {})
    expect(reviewDraftSeed(txn, draft)).toBeNull()
    expect(reviewDraftSeed(txn, { ...draft, memo: 'new', tags: ['a', 'b'] })).toEqual({
      memo: 'new',
      tags: ['a', 'b'],
    })
  })
})

describe('TxnDetail — leaving the editor with unsaved edits', () => {
  it('closes at once when nothing was changed', () => {
    const { onClose } = renderEditor(makeTxn())
    escape()
    // (Radix's own Escape and the drawer's both reach dismiss — at least once is the contract.)
    expect(onClose).toHaveBeenCalled()
    expect(screen.queryByText('Discard your changes?')).toBeNull()
  })

  it('asks first, keeps editing on Cancel, and closes on Discard', () => {
    const { onClose } = renderEditor(makeTxn())
    fireEvent.change(screen.getByPlaceholderText('e.g. 2 lattes + croissant'), {
      target: { value: 'typed' },
    })
    escape()
    expect(screen.getByText('Discard your changes?')).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByDisplayValue('typed')).toBeTruthy()
    escape()
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('treats the seeded picks as the starting point, not as unsaved edits', () => {
    const { onClose } = renderEditor(makeTxn(), { memo: 'from the card' })
    escape()
    expect(onClose).toHaveBeenCalled()
    expect(screen.queryByText('Discard your changes?')).toBeNull()
  })
})
