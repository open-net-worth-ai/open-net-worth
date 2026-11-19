import type { UntrackedExcluded } from '../../../types/aggregates'

/** Where the untracked money is: "Old 401(k)" for one account, "2 accounts" for several. */
export function untrackedWhere(u: UntrackedExcluded): string {
  if (u.accounts.length === 1) return u.accounts[0]!.name || 'a Brokerage total account'
  return `${u.accounts.length} accounts`
}
