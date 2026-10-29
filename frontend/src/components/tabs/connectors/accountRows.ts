// Pure helpers behind the connector "Accounts" list: the row shape the server sends, how rows
// group by their suggested account type, and what "Add" seeds the account form with.
import { LIABILITY_TYPES, TYPE_LABELS, TYPE_ORDER } from '../accounts/categories'
import type { AccountPrefill, AccountType } from '../accounts/types'

export interface ProviderAccountRow {
  provider_account_id: string
  name: string
  mask: string
  currency: string
  balance: number | null
  // The provider's suggested app account type (one of the seven), or null.
  account_type: AccountType | null
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
export function prefillFor(row: ProviderAccountRow, providerLabel: string): AccountPrefill {
  return {
    type: row.account_type ?? undefined,
    name: row.name,
    currency: row.currency,
    lastFour: row.mask,
    institution: providerLabel,
    initial:
      row.balance == null || row.account_type == null
        ? undefined
        : String(isOwed(row) ? Math.abs(row.balance) : row.balance),
  }
}
