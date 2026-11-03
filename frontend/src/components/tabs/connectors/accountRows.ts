// Pure helpers behind the connector "Accounts" list: the row shape the server sends, how rows
// group by their suggested account type, and what "Add" seeds the account form with.
import { LIABILITY_TYPES, TYPE_LABELS, TYPE_ORDER, type AccountType } from '../accounts/categories'
import type { AccountPrefill } from '../accounts/types'

export interface ProviderAccountRow {
  provider_account_id: string
  name: string
  mask: string
  currency: string
  balance: number | null
  // The provider's suggested app account type (one of the seven), or null.
  account_type: AccountType | null
  // The bank the account sits at, when one connector links several (Plaid); else null.
  institution?: string | null
  linked_account_id: string | null
}

export interface RowGroup {
  key: string
  label: string
  rows: ProviderAccountRow[]
}

const GROUP_LABELS: Record<AccountType, string> = {
  ...TYPE_LABELS,
  fixed_deposit: 'Fixed deposits',
  investment: 'Investments',
  receivable: 'Receivables',
  credit: 'Credit cards',
  loan: 'Loans',
}

/** Rows grouped by suggested type, in the app's type order; untyped rows last as "Other".
 *  A provider that suggests no types at all gets ONE unlabelled group — headings that all
 *  say "Other" would only add noise. */
export function groupRows(rows: ProviderAccountRow[]): RowGroup[] {
  if (rows.every((r) => r.account_type == null)) return [{ key: 'all', label: '', rows }]
  const groups: RowGroup[] = TYPE_ORDER.map((t) => ({
    key: t,
    label: GROUP_LABELS[t],
    rows: rows.filter((r) => r.account_type === t),
  }))
  groups.push({ key: 'other', label: 'Other', rows: rows.filter((r) => r.account_type == null) })
  return groups.filter((g) => g.rows.length > 0)
}

export function isOwed(row: ProviderAccountRow): boolean {
  return row.account_type != null && LIABILITY_TYPES.has(row.account_type)
}

/** The create form's seed for a provider account: its suggested type, and its balance in the
 *  form's terms — a card or loan's amount OWED is entered positive (the form signs it), and a
 *  provider may report it either way round. The balance comes ONLY with a known type: the form
 *  otherwise starts on Cash, where a card's prefilled debt would book as an asset. */
export function prefillFor(row: ProviderAccountRow, institution?: string): AccountPrefill {
  return {
    type: row.account_type ?? undefined,
    name: row.name,
    currency: row.currency,
    lastFour: row.mask,
    // The bank — never the connector: "Plaid" is how the data arrives, not where the money is.
    // Unknown → left blank for the user to fill, rather than filing the account under "Plaid".
    institution: institution || row.institution || undefined,
    initial:
      row.balance == null || row.account_type == null
        ? undefined
        : String(isOwed(row) ? Math.abs(row.balance) : row.balance),
  }
}

// A linked account filed under the provider placeholder (or nothing) while its bank has a name —
// see FileUnderBankFix.
export interface MisfileAccount {
  id: string
  name: string
  institution?: string | null
}

/** Linked accounts sitting under the placeholder, grouped by the bank they belong to. */
export function misfiled(
  rows: ProviderAccountRow[],
  accounts: MisfileAccount[],
  providerLabel: string,
): Map<string, MisfileAccount[]> {
  const byBank = new Map<string, MisfileAccount[]>()
  for (const row of rows) {
    const bank = (row.institution || '').trim()
    if (!bank || !row.linked_account_id) continue
    const acct = accounts.find((a) => a.id === row.linked_account_id)
    const current = (acct?.institution || '').trim()
    if (!acct || (current && current !== providerLabel) || current === bank) continue
    const list = byBank.get(bank) ?? []
    if (!list.some((a) => a.id === acct.id)) list.push(acct)
    byBank.set(bank, list)
  }
  return byBank
}

/** The institution a NEW account from this bank should go under: the one the bank's other
 *  linked accounts already use — the most common, if they differ — so a renamed institution
 *  ("Joint Chase") keeps every account at the bank in one group instead of a fresh "Chase"
 *  being minted beside it. The provider placeholder never counts. ``undefined`` when none of
 *  the bank's accounts is linked yet (the caller falls back to the bank's own name). */
export function bankInstitution(
  row: ProviderAccountRow,
  rows: ProviderAccountRow[],
  accounts: MisfileAccount[],
  providerLabel: string,
): string | undefined {
  const bank = (row.institution || '').trim()
  if (!bank) return undefined
  const counts = new Map<string, number>()
  for (const other of rows) {
    if ((other.institution || '').trim() !== bank || !other.linked_account_id) continue
    const inst = (accounts.find((a) => a.id === other.linked_account_id)?.institution || '').trim()
    if (!inst || inst === providerLabel) continue
    counts.set(inst, (counts.get(inst) ?? 0) + 1)
  }
  let best: string | undefined
  for (const [inst, n] of counts) if (best === undefined || n > (counts.get(best) ?? 0)) best = inst
  return best
}
