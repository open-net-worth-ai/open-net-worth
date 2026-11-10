// data.detailPatchToUpdate.test.ts — the transaction detail drawer's patch → TransactionUpdate
// mapping (extracted from AccountsTab.tsx's saveDetailEdit so this money-routing logic is
// unit-testable without rendering the whole tab). Covers every kind × currency combination the
// drawer can produce — the exact matrix that let two real bugs ship silently:
//   1. A same-currency transfer edit left to_amount stale (TxnDetail.tsx never resynced the
//      hidden "Received" field to the freshly-typed Amount).
//   2. Saving ANY edit on an investment SELL (to-leg only) routed the amount to from_amount
//      because `wantsToLeg` never considered kind==='investment'.
//   3. A leg the edit emptied (account id → null) kept its stale amount: an income stored on
//      the from-leg (written before the ledger refused that shape) came out of the edit-form
//      repair with from_account_id=null beside an orphaned from_amount=20 — the backend's
//      `_normalise_leg` keeps a bare amount, and an omitted field means "unchanged".

import { describe, it, expect } from 'vitest'
import { detailPatchToUpdate, fromApiTxn, toApiTxnCreate } from './data'
import type { ApiTxn } from './types'

describe('detailPatchToUpdate — expense (from-leg)', () => {
  it('routes amount/currency to the from leg', () => {
    const u = detailPatchToUpdate({
      kind: 'expense',
      amount: 42.5,
      currency: 'SGD',
      fromAccountId: 'a1',
      toAccountId: null,
    })
    expect(u.from_amount).toBe(42.5)
    expect(u.from_currency).toBe('SGD')
    expect(u.to_amount).toBeNull()
  })
})

describe('detailPatchToUpdate — income (to-leg)', () => {
  it('routes amount/currency to the to leg', () => {
    const u = detailPatchToUpdate({
      kind: 'income',
      amount: 6800,
      currency: 'SGD',
      fromAccountId: null,
      toAccountId: 'a1',
    })
    expect(u.to_amount).toBe(6800)
    expect(u.to_currency).toBe('SGD')
    expect(u.from_amount).toBeNull()
  })
})

describe('detailPatchToUpdate — adjustment', () => {
  it('increase (to-leg only) routes amount to the to leg', () => {
    const u = detailPatchToUpdate({
      kind: 'adjustment',
      amount: 100,
      currency: 'SGD',
      fromAccountId: null,
      toAccountId: 'a1',
    })
    expect(u.to_amount).toBe(100)
    expect(u.from_amount).toBeNull()
  })

  it('decrease (from-leg only) routes amount to the from leg', () => {
    const u = detailPatchToUpdate({
      kind: 'adjustment',
      amount: 100,
      currency: 'SGD',
      fromAccountId: 'a1',
      toAccountId: null,
    })
    expect(u.from_amount).toBe(100)
    expect(u.to_amount).toBeNull()
  })
})

describe('detailPatchToUpdate — investment (regression: sell used to route to from_amount)', () => {
  it('a BUY (from-leg only) routes amount to the from leg', () => {
    const u = detailPatchToUpdate({
      kind: 'investment',
      amount: 5000,
      currency: 'USD',
      fromAccountId: 'a1',
      toAccountId: null,
    })
    expect(u.from_amount).toBe(5000)
    expect(u.to_amount).toBeNull()
  })

  it('a SELL (to-leg only) routes amount to the to leg, not the from leg', () => {
    const u = detailPatchToUpdate({
      kind: 'investment',
      amount: 8000.0,
      currency: 'USD',
      fromAccountId: null,
      toAccountId: 'a1',
    })
    expect(u.to_amount).toBe(8000.0)
    expect(u.to_currency).toBe('USD')
    expect(u.from_amount).toBeNull()
  })
})

describe('detailPatchToUpdate — transfer, same currency (regression: stale toAmount)', () => {
  it('mirrors from_amount into to_amount even if patch.toAmount carries a stale different number', () => {
    const u = detailPatchToUpdate({
      kind: 'transfer',
      amount: 7900.0,
      currency: 'EUR',
      fromAccountId: 'a1',
      toAccountId: 'a2',
      toAmount: 8000.0,
      toCurrency: 'EUR', // stale — the hidden Received field's old value
    })
    expect(u.from_amount).toBe(7900.0)
    expect(u.to_amount).toBe(7900.0)
    expect(u.to_currency).toBe('EUR')
  })

  it('same currency with no stale toAmount at all still mirrors correctly', () => {
    const u = detailPatchToUpdate({
      kind: 'transfer',
      amount: 250,
      currency: 'SGD',
      fromAccountId: 'a1',
      toAccountId: 'a2',
      toAmount: 250,
      toCurrency: 'SGD',
    })
    expect(u.from_amount).toBe(250)
    expect(u.to_amount).toBe(250)
  })
})

describe('detailPatchToUpdate — transfer, different currencies', () => {
  it('honors an explicit Received amount in the destination currency', () => {
    const u = detailPatchToUpdate({
      kind: 'transfer',
      amount: 90,
      currency: 'EUR',
      fromAccountId: 'a1',
      toAccountId: 'a2',
      toAmount: 100,
      toCurrency: 'USD',
    })
    expect(u.from_amount).toBe(90)
    expect(u.from_currency).toBe('EUR')
    expect(u.to_amount).toBe(100)
    expect(u.to_currency).toBe('USD')
  })
})

describe('detailPatchToUpdate — an emptied leg clears its stale amount (regression: orphaned from_amount)', () => {
  it('income moved onto the to-leg sends from_amount/from_currency as null, not omitted', () => {
    // The live shape: kind=income, money stored on the from-leg (pre-2026-09-13 write). The
    // form seeds from that leg and saves it back on the to-leg with fromAccountId: null; the
    // PUT must clear the from leg's amount too, or the row keeps from_amount=20 with no account.
    const u = detailPatchToUpdate({
      kind: 'income',
      amount: 20,
      currency: 'SGD',
      fromAccountId: null,
      toAccountId: 'acc_dbs',
    })
    expect(u.to_account_id).toBe('acc_dbs')
    expect(u.to_amount).toBe(20)
    expect(u.from_account_id).toBeNull()
    expect(u.from_amount).toBeNull()
    expect(u.from_currency).toBeNull()
  })

  it('expense moved onto the from-leg sends to_amount/to_currency as null', () => {
    const u = detailPatchToUpdate({
      kind: 'expense',
      amount: 20,
      currency: 'SGD',
      fromAccountId: 'acc_dbs',
      toAccountId: null,
    })
    expect(u.from_amount).toBe(20)
    expect(u.to_account_id).toBeNull()
    expect(u.to_amount).toBeNull()
    expect(u.to_currency).toBeNull()
  })

  it('a transfer (both accounts set) clears neither leg', () => {
    const u = detailPatchToUpdate({
      kind: 'transfer',
      amount: 50,
      currency: 'SGD',
      fromAccountId: 'a1',
      toAccountId: 'a2',
      toAmount: 50,
      toCurrency: 'SGD',
    })
    expect(u.from_amount).toBe(50)
    expect(u.to_amount).toBe(50)
    expect(u.from_currency).toBe('SGD')
    expect(u.to_currency).toBe('SGD')
  })

  it('a patch that never mentions an account id clears nothing', () => {
    const u = detailPatchToUpdate({ kind: 'income', amount: 20, currency: 'SGD' })
    expect('from_amount' in u).toBe(false)
    expect('from_currency' in u).toBe(false)
    expect(u.to_amount).toBe(20)
  })
})

describe('detailPatchToUpdate — fields untouched by the patch stay absent', () => {
  it('a description-only patch does not synthesize any amount/account fields', () => {
    const u = detailPatchToUpdate({ description: 'Fixed a typo' })
    expect(u).toEqual({ description: 'Fixed a typo' })
  })
})

describe('detailPatchToUpdate — split allocation (P3-SP)', () => {
  it('passes every split line through to the update, its note on the wire as `memo`', () => {
    const splits = [
      { category: 'cat_food', amount: 30, note: '' },
      { category: 'cat_home', amount: 20, note: 'Mop' },
    ]
    const u = detailPatchToUpdate({ splits })
    expect(u.splits).toEqual([
      { category: 'cat_food', amount: 30, memo: '' },
      { category: 'cat_home', amount: 20, memo: 'Mop' },
    ])
  })

  it('passes an empty splits array (the explicit "unsplit" signal), distinct from omitting it', () => {
    expect(detailPatchToUpdate({ splits: [] }).splits).toEqual([])
    expect('splits' in detailPatchToUpdate({ description: 'x' })).toBe(false)
  })
})

describe('split line memo — the wire field is `memo`', () => {
  // The backend renamed a split line's `note` to `memo` (2026-08-02); the app kept reading and
  // writing `note`, so a typed note was dropped on save and every save of a split wiped the
  // line memos Lucius had written.
  const line = { category: 'cat_a', amount: 30, note: 'Weekly shop' }

  it('sends an edited split line note as `memo`', () => {
    const u = detailPatchToUpdate({ kind: 'expense', splits: [line] })
    expect(u.splits).toEqual([{ category: 'cat_a', amount: 30, memo: 'Weekly shop' }])
  })

  it('sends a created split line note as `memo`', () => {
    expect(toApiTxnCreate({ kind: 'expense', splits: [line] }).splits).toEqual([
      { category: 'cat_a', amount: 30, memo: 'Weekly shop' },
    ])
  })

  it('reads a stored line memo back', () => {
    const t = fromApiTxn({
      id: 't1',
      date: '2026-10-29',
      kind: 'expense',
      category: 'split',
      splits: [{ category: 'cat_a', amount: 30, memo: 'Weekly shop' }],
    } as ApiTxn)
    expect(t.splits[0]!.note).toBe('Weekly shop')
  })
})
