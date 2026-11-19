import type { Txn } from './types'

// "Make transfer" on two selected rows (ADR 2026-11-13, Amendment 19): money out of one of your
// accounts and the same money into another — a card payment, a move to savings — that arrived as
// two rows. The server merges them (POST /api/matches/merge, become_transfer); this only says
// whether the selection is such a pair, and if not, why.

export type TransferPair = { pay: Txn; receive: Txn } | { why: string }

const SELECT_TWO = 'Select two transactions: money out of one account and into another'

function single(t: Txn): boolean {
  return (t.kind === 'expense' || t.kind === 'income') && t.splits.length === 0
}

export function transferPair(selected: Txn[]): TransferPair {
  if (selected.length !== 2) return { why: SELECT_TWO }
  const pay = selected.find((t) => t.kind === 'expense')
  const receive = selected.find((t) => t.kind === 'income')
  if (!pay || !receive || !selected.every(single)) return { why: SELECT_TWO }
  if (!pay.fromAccountId || pay.fromAccountId === receive.toAccountId)
    return { why: 'Both transactions are in the same account' }
  if (
    pay.fromCurrency === receive.toCurrency &&
    Math.abs((pay.fromAmount ?? 0) - (receive.toAmount ?? 0)) > 0.005
  )
    return { why: 'The two amounts are different' }
  return { pay, receive }
}
