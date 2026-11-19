import type { Account, TransactionUpdate, Txn } from './types'
import { CURRENCY_OPTIONS } from '../../firstrun/currencies'
import { ZERO_DEC } from './format'
import { fDate } from '../../../utils/format'

const SUPPORTED_CURRENCIES = new Set(CURRENCY_OPTIONS.map((option) => option.value))

export interface ReviewDraft {
  kind: Txn['kind']
  accountId: string
  toAccountId: string
  investmentDirection: '' | 'buy' | 'sell'
  amount: number
  toAmount: number
  originalAmount: string
  originalCurrency: string
  date: string
  payee: string
  payeeEdited: boolean
  category: string | null
  memo: string
  tags: string[]
  cleared: boolean
}

function primaryAmount(txn: Txn): number {
  return Math.abs(txn.fromAmount ?? txn.toAmount ?? txn.baseAmount ?? 0)
}

export function startingReviewDraft(txn: Txn, payeeNames: Record<string, string>): ReviewDraft {
  return {
    kind: txn.kind,
    accountId: txn.fromAccountId ?? txn.toAccountId ?? '',
    toAccountId: txn.kind === 'transfer' ? (txn.toAccountId ?? '') : '',
    investmentDirection: txn.kind === 'investment' ? (txn.fromAccountId ? 'buy' : 'sell') : '',
    amount: primaryAmount(txn),
    toAmount: Math.abs(txn.toAmount ?? primaryAmount(txn)),
    originalAmount: txn.origAmount == null ? '' : String(Math.abs(txn.origAmount)),
    originalCurrency: txn.origCurrency ?? '',
    date: txn.date,
    // A LINKED payee always names the card. Only a row with no payee at all resolves to '',
    // which the card shows as "No payee yet" — a bank string must never pose as a payee.
    // A linked payee whose name is not in the map (hidden, or not loaded yet) falls back to
    // the description rather than claiming the row has no payee.
    payee: txn.payeeId ? (payeeNames[txn.payeeId] ?? txn.description) : '',
    payeeEdited: false,
    category: txn.category,
    memo: txn.memo,
    tags: txn.tags ?? [],
    cleared: txn.cleared,
  }
}

function account(accounts: Account[], id: string): Account | undefined {
  return accounts.find((item) => item.id === id)
}

function selectableAccount(txn: Txn, accounts: Account[], id: string): Account | undefined {
  const found = account(accounts, id)
  if (!found || (found.archived && id !== txn.fromAccountId && id !== txn.toAccountId))
    return undefined
  return found
}

export function validateReviewDraft(
  txn: Txn,
  draft: ReviewDraft,
  accounts: Account[],
): string | null {
  if (!draft.date) return 'Choose a date.'
  if (!(draft.amount > 0)) return 'Enter a valid amount.'
  if (!selectableAccount(txn, accounts, draft.accountId)) return 'Choose an active account.'
  if (
    txn.splits.length > 0 &&
    (draft.kind !== txn.kind || draft.accountId !== (txn.fromAccountId ?? txn.toAccountId))
  ) {
    return 'Use Edit all details to change type, account, or split lines.'
  }
  if (draft.kind === 'transfer') {
    if (!selectableAccount(txn, accounts, draft.toAccountId))
      return 'Choose an active destination account.'
    if (draft.accountId === draft.toAccountId) return 'Transfers need two different accounts.'
    if (!(draft.toAmount > 0)) return 'Enter the received amount.'
  }
  if (draft.kind === 'investment' && !draft.investmentDirection) {
    return 'Choose whether this investment is a buy or sell.'
  }
  const originalAmount = draft.originalAmount.trim()
  const originalCurrency = draft.originalCurrency.trim().toUpperCase()
  const hasOriginalAmount = originalAmount !== ''
  const hasOriginalCurrency = originalCurrency !== ''
  if (draft.kind !== 'transfer' && hasOriginalAmount !== hasOriginalCurrency) {
    return 'Original charge needs both an amount and currency.'
  }
  if (hasOriginalAmount && !(Number(originalAmount) > 0)) {
    return 'Enter a valid original charge amount.'
  }
  if (hasOriginalCurrency && !SUPPORTED_CURRENCIES.has(originalCurrency)) {
    return 'Choose a supported original currency.'
  }
  const ledgerCurrency = account(accounts, draft.accountId)?.currency
  if (hasOriginalCurrency && originalCurrency === ledgerCurrency) {
    return 'Original currency must differ from the account currency.'
  }
  return null
}

export function reviewDraftToUpdate(
  txn: Txn,
  draft: ReviewDraft,
  accounts: Account[],
): TransactionUpdate {
  const source = account(accounts, draft.accountId)
  const destination = account(accounts, draft.toAccountId)
  const originalAccountId = txn.fromAccountId ?? txn.toAccountId ?? ''
  const originalDirection = txn.kind === 'investment' ? (txn.fromAccountId ? 'buy' : 'sell') : ''
  const structureChanged =
    draft.kind !== txn.kind ||
    draft.accountId !== originalAccountId ||
    draft.toAccountId !== (txn.kind === 'transfer' ? (txn.toAccountId ?? '') : '') ||
    draft.investmentDirection !== originalDirection ||
    draft.amount !== primaryAmount(txn) ||
    (draft.kind === 'transfer' && draft.toAmount !== Math.abs(txn.toAmount ?? primaryAmount(txn)))
  const update: TransactionUpdate = { needs_review: false }

  if (draft.kind !== txn.kind) update.kind = draft.kind
  if (draft.date !== txn.date) update.date = draft.date
  if (draft.memo !== txn.memo) update.memo = draft.memo
  if (draft.tags.join('\u0000') !== (txn.tags ?? []).join('\u0000')) update.tags = draft.tags
  if (draft.cleared !== txn.cleared) update.cleared = draft.cleared
  if (draft.category !== txn.category) update.category = draft.category

  if (structureChanged) {
    update.from_account_id = null
    update.from_amount = null
    update.from_currency = null
    update.to_account_id = null
    update.to_amount = null
    update.to_currency = null
  }
  if (
    structureChanged &&
    (draft.kind === 'transfer' || draft.kind === 'investment' || draft.kind === 'adjustment')
  ) {
    update.payee_id = null
  }
  if (
    structureChanged &&
    (draft.kind === 'income' ||
      (draft.kind === 'adjustment' && txn.toAccountId != null) ||
      (draft.kind === 'investment' && draft.investmentDirection === 'sell'))
  ) {
    update.to_account_id = draft.accountId
    update.to_amount = draft.amount
    update.to_currency = source?.currency ?? txn.toCurrency ?? txn.baseCurrency
  } else if (structureChanged) {
    update.from_account_id = draft.accountId
    update.from_amount = draft.amount
    update.from_currency = source?.currency ?? txn.fromCurrency ?? txn.baseCurrency
  }
  if (structureChanged && draft.kind === 'transfer') {
    update.to_account_id = draft.toAccountId
    update.to_amount = draft.toAmount
    update.to_currency = destination?.currency ?? txn.toCurrency ?? txn.baseCurrency
  }

  const originalAmount = draft.originalAmount.trim()
  const originalCurrency = draft.originalCurrency.trim().toUpperCase()
  const originalChanged =
    (originalAmount ? Number(originalAmount) : null) !==
      (txn.origAmount == null ? null : Math.abs(txn.origAmount)) ||
    (originalCurrency || null) !== (txn.origCurrency?.toUpperCase() ?? null)
  if (draft.kind === 'transfer' && txn.kind !== 'transfer') {
    update.original_amount = null
    update.original_currency = null
  } else if (draft.kind !== 'transfer' && originalChanged) {
    update.original_amount = originalAmount ? Number(originalAmount) : null
    update.original_currency = originalCurrency || null
  }
  return update
}

/** The card's unsaved picks the drawer's editor can take, or null when the card has changed
 *  nothing it carries. Only the fields both surfaces edit the same way: the editor's own form
 *  covers the rest (kind, account, amounts) from the saved row. */
export function reviewDraftSeed(
  txn: Txn,
  draft: ReviewDraft,
): {
  category?: string | null
  payee?: string
  date?: string
  memo?: string
  tags?: string[]
} | null {
  const seed: NonNullable<ReturnType<typeof reviewDraftSeed>> = {}
  if (draft.category !== txn.category) seed.category = draft.category
  if (draft.payeeEdited) seed.payee = draft.payee
  if (draft.date !== txn.date) seed.date = draft.date
  if (draft.memo !== txn.memo) seed.memo = draft.memo
  if (draft.tags.join('\u0000') !== (txn.tags ?? []).join('\u0000')) seed.tags = draft.tags
  return Object.keys(seed).length ? seed : null
}

/** The card's source line, in the drawer's own words. `via` is the audit's qualifier: a named
 *  automation is the actor itself ("Added by Weekly Fraud Checking"), while a channel — an
 *  iOS Shortcut, Telegram — qualifies the writer ("Added by Lucius via iOS Shortcut"); the
 *  automation id tells the two apart. No `via`: the writer's label. */
export function sourceLine(txn: Pick<Txn, 'source' | 'via' | 'automationId'>): string {
  const label = SOURCE_LABEL[txn.source] ?? 'New transaction'
  if (!txn.via) return label
  return txn.automationId ? `Added by ${txn.via}` : `${label} via ${txn.via}`
}

/** How a row entered the ledger, as the review surfaces word it. */
export const SOURCE_LABEL: Record<string, string> = {
  ai: 'Added by Lucius',
  agent: 'Added by an agent',
  automation: 'Added by automation',
  bank: 'Bank sync',
  email: 'Added from email',
  import: 'Imported',
  manual: 'Added manually',
  opening: 'Starting balance',
  reconcile: 'Reconciliation',
}

export function isInflow(draft: ReviewDraft): boolean {
  return (
    draft.kind === 'income' || (draft.kind === 'investment' && draft.investmentDirection === 'sell')
  )
}

export function transactionCurrency(
  draft: ReviewDraft,
  accounts: Account[],
  fallback: string,
): string {
  return accounts.find((item) => item.id === draft.accountId)?.currency ?? fallback
}

export function reviewDate(value: string): string {
  return fDate(value, 'short', 'always')
}

/** Amount in the currency's own minor units: two decimals for most, none for VND/JPY/KRW… */
export function reviewAmount(value: number, currency: string): string {
  const digits = ZERO_DEC.has((currency || '').toUpperCase()) ? 0 : 2
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

/** How each stored kind is named to the reader. ONE vocabulary for the whole tab — the review
 *  card used to invent "Investment purchase"/"Investment sale" from a direction it inferred,
 *  which no other surface says. The amount's sign already tells a buy from a sell. */
export const KIND_LABEL: Record<string, string> = {
  expense: 'Expense',
  income: 'Income',
  transfer: 'Transfer',
  investment: 'Investment',
  adjustment: 'Balance adjustment',
}

export function typeLabel(draft: ReviewDraft): string {
  return KIND_LABEL[draft.kind] ?? draft.kind.charAt(0).toUpperCase() + draft.kind.slice(1)
}

/** The card's heading: a transfer names its destination, anything else names its payee.
 *  Empty means no payee is linked yet — the card shows that as a placeholder, never the
 *  bank description standing in for one. An investment row with no payee is the exception:
 *  its description is the trade's own ("Sold 60 KSTL @ 150.00 USD"), not a bank
 *  string, so it names the card. */
export function summaryTitle(draft: ReviewDraft, accounts: Account[], description = ''): string {
  if (draft.kind === 'transfer') {
    const destination = accounts.find((item) => item.id === draft.toAccountId)
    return `Transfer to ${destination?.name ?? 'another account'}`
  }
  if (draft.kind === 'investment' && !draft.payee) return description.trim()
  return draft.payee
}
