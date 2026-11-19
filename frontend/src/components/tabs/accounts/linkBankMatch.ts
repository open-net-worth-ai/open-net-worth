// How alike a bank account (as a connector sees it) and one of the user's accounts look — the
// ordering of LinkBankAccountDialog's list. A suggestion only: nothing links without a pick.
import type { Account } from './types'

export interface BankAccount {
  provider_account_id: string
  name: string
  mask?: string | null
  currency?: string | null
  institution?: string | null
  account_type?: string | null
  linked_account_id?: string | null
}

const words = (s: string | null | undefined) =>
  (s ?? '')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3)

/** How alike a bank account and this account look: last four, currency, type, shared words. */
export function matchScore(account: Account, row: BankAccount, providerLabel: string): number {
  let score = 0
  if (account.lastFour && row.mask && account.lastFour === row.mask) score += 5
  if (row.currency && row.currency.toUpperCase() === account.currency?.toUpperCase()) score += 2
  if (row.account_type && row.account_type === account.type) score += 2
  const mine = new Set([...words(account.name), ...words(account.institution)])
  for (const w of new Set([
    ...words(row.name),
    ...words(row.institution),
    ...words(providerLabel),
  ])) {
    if (mine.has(w)) score += 1
  }
  return score
}
