import { describe, expect, it } from 'vitest'
import { transferPair } from './makeTransfer'
import type { Txn } from './types'

function txn(over: Partial<Txn>): Txn {
  return {
    id: 't',
    date: '2026-11-10',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'ACME',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: null,
    fromAmount: null,
    fromCurrency: null,
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    ...over,
  } as Txn
}

const out = txn({
  id: 'o',
  kind: 'expense',
  fromAccountId: 'chk',
  fromAmount: 312.4,
  fromCurrency: 'GBP',
})
const into = txn({
  id: 'i',
  kind: 'income',
  toAccountId: 'card',
  toAmount: 312.4,
  toCurrency: 'GBP',
})

describe('transferPair', () => {
  it('pairs money out of one account with the same money into another', () => {
    const r = transferPair([into, out])
    expect('pay' in r && r.pay.id === 'o' && r.receive.id === 'i').toBe(true)
  })

  it('needs exactly two rows, one out and one in', () => {
    expect(transferPair([out])).toHaveProperty('why')
    expect(transferPair([out, { ...out, id: 'o2' }])).toHaveProperty('why')
    expect(transferPair([out, into, { ...into, id: 'i2' }])).toHaveProperty('why')
  })

  it('refuses two rows in the same account', () => {
    const r = transferPair([out, { ...into, toAccountId: 'chk' }])
    expect(r).toEqual({ why: 'Both transactions are in the same account' })
  })

  it('refuses different amounts in one currency', () => {
    const r = transferPair([out, { ...into, toAmount: 500 }])
    expect(r).toEqual({ why: 'The two amounts are different' })
  })

  it('leaves amounts in two currencies to the server', () => {
    const r = transferPair([out, { ...into, toAmount: 650, toCurrency: 'EUR' }])
    expect('pay' in r).toBe(true)
  })

  it('refuses a split row', () => {
    const split = { ...out, splits: [{ category: 'c', amount: 1, note: '' }] } as unknown as Txn
    expect(transferPair([split, into])).toHaveProperty('why')
  })
})
