// @vitest-environment jsdom
// The Tags row is always in the View drawer, like Memo: an empty "—" row is how people learn
// a transaction can carry tags at all (owner, 2026-10-25). It used to vanish when untagged.
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

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

function renderDrawer(
  txn: Txn,
  opts: {
    mode?: 'view' | 'edit'
    onToggleCleared?: (t: Txn, cleared: boolean) => void
    onSave?: (id: string, patch: Record<string, unknown>) => void
  } = {},
) {
  render(
    <TxnDetail
      txn={txn}
      initialMode={opts.mode ?? 'view'}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="USD"
      rates={{}}
      onClose={() => {}}
      onSave={opts.onSave ?? (() => {})}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onToggleCleared={opts.onToggleCleared}
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
}

function tagsValue(): HTMLElement {
  const row = screen.getByText('Tags', { selector: '.acct-detail-k' }).closest('.acct-detail-row')
  return row!.querySelector('.acct-detail-v') as HTMLElement
}

describe('TxnDetail — Tags row', () => {
  it('shows an empty Tags row when the transaction has no tags', () => {
    renderDrawer(makeTxn({ tags: [] }))
    expect(tagsValue().textContent).toBe('—')
  })

  it('lists the tags when there are some', () => {
    renderDrawer(makeTxn({ tags: ['travel', 'reimbursable'] }))
    const chips = [...tagsValue().querySelectorAll('.acct-detail-tag')].map((c) => c.textContent)
    expect(chips).toEqual(['travel', 'reimbursable'])
  })
})
