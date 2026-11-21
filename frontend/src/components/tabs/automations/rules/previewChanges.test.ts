import { describe, expect, it } from 'vitest'
import type { RuleMatch } from '../../../../api/automations'
import { describeChanges } from './previewChanges'

const labels = {
  categories: [
    { id: 'cat_a', label: 'Groceries' },
    { id: 'cat_b', label: 'Household' },
  ],
}

// The server's real shape: a row that isn't split carries `splits: null`, not [].
const match = (over: Partial<RuleMatch> = {}): RuleMatch => ({
  transaction_id: 't1',
  date: '2026-11-02',
  description: 'ACME',
  memo: '',
  payee_id: 'pay_acme',
  payee_name: 'Acme',
  account_id: 'acc_card',
  account_name: 'Card',
  amount: 100,
  currency: 'USD',
  kind: 'expense',
  category: 'cat_a',
  tags: [],
  splits: null,
  after: null,
  skipped_reason: null,
  ...over,
})

describe('describeChanges', () => {
  it('reads a plain row whose splits are null before and after', () => {
    const row = match({
      after: {
        category: 'cat_b',
        tags: ['T1'],
        description: 'ACME',
        memo: '',
        payee_id: 'pay_acme',
        splits: null,
      },
    })
    expect(describeChanges(row, labels)).toEqual(['Category: Groceries → Household', 'Tags: + T1'])
  })

  it('says a row is split when the Rule splits it', () => {
    const row = match({
      after: {
        category: 'split',
        tags: null,
        description: 'ACME',
        memo: '',
        payee_id: 'pay_acme',
        splits: [{}, {}],
      },
    })
    expect(describeChanges(row, labels)).toEqual(['Split into 2 lines'])
  })
})
