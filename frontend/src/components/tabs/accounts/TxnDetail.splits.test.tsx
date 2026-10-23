// @vitest-environment jsdom
//
// Split editing (P3-SP UI gap): before this, EditBody had NO split editor — a split
// transaction opened for editing showed only a lone "Split" sentinel in the category picker,
// its per-line allocation vanished, editing the amount 422'd (the stale lines no longer summed
// to the new leg), and picking a single category was silently ignored (the backend re-stamped
// the SPLIT sentinel because the patch never cleared `splits`). These tests pin the fix:
// the drawer renders/edits the lines, save maps them correctly, and Unsplit clears them.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Northgate Rewards Card',
    kind: 'asset',
    type: 'cash',
    institution: '',
    currency: 'SGD',
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
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}
const ACCT = makeAccount()

const CATS: Category[] = [
  { id: 'cat_food', label: 'Groceries', color: '#34b08a', kind: 'expense' },
  { id: 'cat_home', label: 'Household', color: '#e0a032', kind: 'expense' },
]

function makeSplit(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-10',
    kind: 'expense',
    category: 'split',
    payeeId: null,
    payeeName: null,
    description: 'Test split transaction',
    memo: '',
    tags: [],
    splits: [
      { category: 'cat_food', amount: 30, note: '' },
      { category: 'cat_home', amount: 20, note: '' },
    ],
    fromAccountId: 'a1',
    fromAmount: 50,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 50,
    baseCurrency: 'SGD',
    cleared: false,
    source: 'manual',
    createdAt: '2026-07-10',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderDetail(t: Txn) {
  let savedPatch: Record<string, unknown> | null = null
  render(
    <TxnDetail
      txn={t}
      accounts={[ACCT]}
      categories={CATS}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{}}
      onClose={() => {}}
      onSave={(_id, patch) => {
        savedPatch = patch
      }}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'manual',
          addedBy: 'You',
          createdAt: '2026-07-10',
          updatedAt: '2026-07-10',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
  return () => savedPatch
}

describe('TxnDetail — editing a split transaction', () => {
  it('renders the split lines (category + amount) in edit mode instead of a bare "Split" chip', () => {
    renderDetail(makeSplit())
    fireEvent.click(screen.getByText('Edit transaction'))
    // The editor is present (Unsplit + Add line controls), not the single category picker.
    expect(screen.getByText('Unsplit')).toBeTruthy()
    expect(screen.getByText('Add line')).toBeTruthy()
    // Each line shows its category label.
    expect(screen.getByText('Groceries')).toBeTruthy()
    expect(screen.getByText('Household')).toBeTruthy()
    // Amounts are populated and, summing to the 50 leg, the editor reads Balanced.
    const amts = screen.getAllByLabelText('Split amount') as HTMLInputElement[]
    expect(amts.map((a) => a.value)).toEqual(['30', '20'])
    expect(screen.getByText('Balanced')).toBeTruthy()
  })

  it('save sends the lines as `splits` and DROPS the top-level category (backend derives the sentinel)', () => {
    const getPatch = renderDetail(makeSplit())
    fireEvent.click(screen.getByText('Edit transaction'))
    fireEvent.click(screen.getByText('Save changes'))
    const patch = getPatch()!
    expect(patch.splits).toEqual([
      { category: 'cat_food', amount: 30, note: '' },
      { category: 'cat_home', amount: 20, note: '' },
    ])
    // Never leak the 'split' sentinel through the category field.
    expect('category' in patch).toBe(false)
  })

  it('an edited amount re-balances against the lines — save blocks while unbalanced, then maps the new total', () => {
    const getPatch = renderDetail(makeSplit())
    fireEvent.click(screen.getByText('Edit transaction'))
    // Bump the leg to 60 — now 30+20 ≠ 60, so the editor is unbalanced and Save is blocked.
    // The main Amount input is the unique `.acct-edit-amt-in` (the split-line inputs share the
    // "0.00" placeholder, so query by class instead).
    const amount = document.querySelector('.acct-edit-amt-in') as HTMLInputElement
    fireEvent.change(amount, { target: { value: '60' } })
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(true)
    // Allocate the extra 10 to the second line → balanced → Save enabled and the lines map.
    const amts = screen.getAllByLabelText('Split amount') as HTMLInputElement[]
    fireEvent.change(amts[1]!, { target: { value: '30' } })
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByText('Save changes'))
    expect(getPatch()!.splits).toEqual([
      { category: 'cat_food', amount: 30, note: '' },
      { category: 'cat_home', amount: 30, note: '' },
    ])
  })

  it('Unsplit clears the allocation: save sends splits:[] and a null (no longer "split") category', () => {
    const getPatch = renderDetail(makeSplit())
    fireEvent.click(screen.getByText('Edit transaction'))
    fireEvent.click(screen.getByText('Unsplit'))
    // Back to the single category picker with the sentinel cleared.
    expect(screen.getByText('Split into multiple categories')).toBeTruthy()
    fireEvent.click(screen.getByText('Save changes'))
    const patch = getPatch()!
    expect(patch.splits).toEqual([])
    expect(patch.category).toBeNull()
  })
})

describe('TxnDetail — splitting a plain expense', () => {
  it('offers a Split affordance and, once entered, blocks Save until the lines balance', () => {
    const getPatch = renderDetail(makeSplit({ category: 'cat_food', splits: [] }))
    fireEvent.click(screen.getByText('Edit transaction'))
    // A plain expense shows the single picker + the split affordance.
    const enter = screen.getByText('Split into multiple categories')
    fireEvent.click(enter)
    // The editor appears with two seeded lines; nothing allocated yet → unbalanced → Save blocked.
    expect(screen.getByText('Unsplit')).toBeTruthy()
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(true)
    // Seed line 1 already carries the original category (Groceries); give line 2 the remainder
    // but no category → still blocked with the category guard.
    const amts = screen.getAllByLabelText('Split amount') as HTMLInputElement[]
    fireEvent.change(amts[0]!, { target: { value: '30' } })
    fireEvent.change(amts[1]!, { target: { value: '20' } })
    expect(screen.getByText('Each split line needs a category')).toBeTruthy()
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(true)
    // Sanity: no save fired.
    expect(getPatch()).toBeNull()
  })

  it('switching the kind away from expense/income drops any split allocation', () => {
    renderDetail(makeSplit())
    fireEvent.click(screen.getByText('Edit transaction'))
    expect(screen.getByText('Unsplit')).toBeTruthy()
    // Transfer can't carry splits — the editor must disappear.
    fireEvent.click(screen.getByText('Transfer'))
    expect(screen.queryByText('Unsplit')).toBeNull()
  })
})
