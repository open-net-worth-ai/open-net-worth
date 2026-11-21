// "Create rule…" on a transaction: the editor's starting point, read off the row, and the
// one-shot hand-off from wherever the transaction is open (the drawer lives app-wide) to the
// Rules tab, which owns the editor. A module-level slot rather than the URL: a rule's lines
// are too long to read in an address bar, and a refresh should not reopen the editor.
import type { Txn } from '../../accounts/types'
import { txnIsInflow } from '../../accounts/data'
import { blankRuleForm, blankSplit, type RuleForm } from './ruleModel'
import type { TxnCondition } from '../txnConditions'

/** The figure the row moved, in its own currency, as a positive number. */
function rowAmount(txn: Txn): number | null {
  const inflow = txnIsInflow(txn)
  const leg = inflow ? (txn.toAmount ?? txn.fromAmount) : (txn.fromAmount ?? txn.toAmount)
  return leg == null ? null : Math.abs(leg)
}

function rowAccount(txn: Txn): string | null {
  return (
    (txnIsInflow(txn) ? txn.toAccountId : txn.fromAccountId) ?? txn.fromAccountId ?? txn.toAccountId
  )
}

/** payee is X, amount about Y, account is Z (only when no payee names it); the row's category
 *  and tags, or its split lines as fixed amounts, as the actions. */
export function ruleSeedFromTransaction(txn: Txn): RuleForm {
  const form = blankRuleForm()
  const conditions: TxnCondition[] = []
  if (txn.payeeId) conditions.push({ field: 'payee', op: 'is', value: txn.payeeId })
  const amount = rowAmount(txn)
  if (amount !== null && amount > 0) {
    conditions.push({ field: 'amount', op: 'approx', value: amount })
    form.knownAmount = amount
  }
  const account = rowAccount(txn)
  if (!txn.payeeId && account) conditions.push({ field: 'account', op: 'is', value: account })
  form.when = { conditions }
  if (txn.splits.length > 0) {
    form.splits = txn.splits.map((split) => ({
      ...blankSplit('fixed'),
      value: Math.abs(split.amount),
      category: split.category,
      memo: split.note,
    }))
  } else if (txn.category) {
    form.category = txn.category
  }
  // Tags belong to the whole transaction, split or not.
  form.tags = [...txn.tags]
  return form
}

let pending: RuleForm | null = null

export function stashRuleSeed(seed: RuleForm): void {
  pending = seed
}

/** Reads the waiting seed without consuming it (a render may run twice). */
export function peekRuleSeed(): RuleForm | null {
  return pending
}

/** The seed waiting for the Rules tab, once. */
export function takeRuleSeed(): RuleForm | null {
  const seed = pending
  pending = null
  return seed
}
