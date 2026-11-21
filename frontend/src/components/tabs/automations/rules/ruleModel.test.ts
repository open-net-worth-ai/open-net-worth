import { describe, expect, it } from 'vitest'
import type { Txn } from '../../accounts/types'
import { blankRuleForm, formToBody, ruleIssues, type RuleForm } from './ruleModel'
import { ruleSeedFromTransaction } from './ruleSeed'

const txn = (over: Partial<Txn>): Txn => ({
  id: 'txn_1',
  date: '2026-11-01',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'ACME GYM',
  memo: '',
  tags: [],
  splits: [],
  fromAccountId: 'acc_card',
  fromAmount: 100,
  fromCurrency: 'USD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 100,
  baseCurrency: 'USD',
  cleared: true,
  source: 'manual',
  createdAt: '',
  deleted: false,
  needsReview: false,
  externalRefs: [],
  ...over,
})

describe('formToBody', () => {
  it('builds the contract shape, with splits and match any', () => {
    const form: RuleForm = {
      ...blankRuleForm(),
      match: 'any',
      when: {
        conditions: [
          { field: 'payee', op: 'is', value: 'pay_acme' },
          { field: 'amount', op: 'between', value: [10, 30] },
        ],
      },
      tags: ['gym'],
      override: true,
      splits: [
        { kind: 'fixed', value: 30, category: 'cat_a', tags: ['t1'], memo: '' },
        { kind: 'remainder', value: '', category: 'cat_b', tags: [], memo: 'rest' },
      ],
    }
    expect(formToBody(form, 4)).toEqual({
      trigger: {
        kind: 'event',
        event: 'txn',
        when: {
          match: 'any',
          conditions: [
            { field: 'payee', op: 'is', value: 'pay_acme' },
            { field: 'amount', op: 'between', value: [10, 30] },
          ],
        },
      },
      execution: {
        kind: 'transaction_transform',
        position: 4,
        stop: false,
        override_existing: true,
        set: { tags: ['gym'] },
        splits: [
          { amount: { type: 'fixed', value: 30 }, category: 'cat_a', tags: ['t1'] },
          { amount: { type: 'remainder' }, category: 'cat_b', memo: 'rest' },
        ],
      },
    })
  })
})

describe('ruleIssues', () => {
  it('blocks a rule with no condition or action', () => {
    expect(ruleIssues({ ...blankRuleForm(), when: {} }).blocking).toEqual([
      'Add at least one condition.',
      'Add at least one action.',
    ])
  })

  it('opens a new rule empty: nothing chosen for the owner', () => {
    expect(blankRuleForm().when).toEqual({})
    expect(ruleIssues(blankRuleForm()).blocking).toEqual([
      'Add at least one condition.',
      'Add at least one action.',
    ])
  })

  it('hints when fixed lines do not add up to the known amount and no line takes the rest', () => {
    const form: RuleForm = {
      ...blankRuleForm(),
      when: { conditions: [{ field: 'payee', op: 'is', value: 'pay_acme' }] },
      knownAmount: 100,
      splits: [{ kind: 'fixed', value: 30, category: 'cat_a', tags: [], memo: '' }],
    }
    const issues = ruleIssues(form)
    expect(issues.blocking).toEqual([])
    expect(issues.hints[0]).toMatch(/add up to 30 but the amount is 100/)
  })

  it('does not flag opposing amounts when any one may match', () => {
    const when = {
      conditions: [
        { field: 'amount', op: 'over', value: 100 },
        { field: 'amount', op: 'under', value: 10 },
      ],
    }
    const base = { ...blankRuleForm(), when, category: 'cat_a' }
    expect(ruleIssues({ ...base, match: 'all' }).blocking).toHaveLength(1)
    expect(ruleIssues({ ...base, match: 'any' }).blocking).toEqual([])
  })
})

describe('ruleSeedFromTransaction', () => {
  it('seeds payee, approximate amount and the row category and tags', () => {
    const form = ruleSeedFromTransaction(
      txn({ payeeId: 'pay_acme', category: 'cat_fit', tags: ['gym'] }),
    )
    expect(form.when).toEqual({
      conditions: [
        { field: 'payee', op: 'is', value: 'pay_acme' },
        { field: 'amount', op: 'approx', value: 100 },
      ],
    })
    expect(form.category).toBe('cat_fit')
    expect(form.tags).toEqual(['gym'])
    expect(form.splits).toEqual([])
  })

  it('names the account only when there is no payee, and turns splits into fixed lines', () => {
    const form = ruleSeedFromTransaction(
      txn({
        splits: [
          { category: 'cat_a', amount: 30, note: 'one' },
          { category: 'cat_b', amount: 70, note: '' },
        ],
        tags: ['T1', 'T3'],
      }),
    )
    expect(form.tags).toEqual(['T1', 'T3'])
    expect(form.when).toEqual({
      conditions: [
        { field: 'amount', op: 'approx', value: 100 },
        { field: 'account', op: 'is', value: 'acc_card' },
      ],
    })
    expect(form.category).toBe('')
    expect(form.splits.map((s) => [s.kind, s.value, s.category, s.memo])).toEqual([
      ['fixed', 30, 'cat_a', 'one'],
      ['fixed', 70, 'cat_b', ''],
    ])
  })
})
