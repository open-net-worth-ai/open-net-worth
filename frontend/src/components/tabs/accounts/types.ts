/**
 * types.ts — Accounts tab type contracts.
 *
 * Convention: API types use snake_case (mirrors backend JSON).
 * View types use camelCase (used internally by AccountsTab components).
 * Adapters `fromApiAccount` / `fromApiTxn` (in data.ts) bridge the two.
 */

import type { AccountType } from './categories'

// ── API types (snake_case — mirrors backend JSON) ─────────────────────────

/** One connector this account is linked to — `provider_label` is server-derived (never a
 *  frontend copy of the provider-name table), present on read only. */
export interface ApiExternalId {
  source: string
  ref: string
  name: string
  mask: string
  linked_at: string
  provider_label: string
}

export interface ApiAccount {
  id: string
  name: string
  kind: 'asset' | 'liability'
  type: string
  institution?: string | null
  currency: string
  initial_balance?: number | null
  initial_balance_date?: string | null
  credit_limit?: number | null
  last_four?: string | null
  note?: string | null
  statement_close_day?: number | null
  payment_due_day?: number | null
  reminder_days_before?: number | null
  last_reconciled_at?: string | null
  archived?: boolean
  connected?: boolean
  /** One row per connector this account is linked to (source, ref) — `connected` above is
   *  derived from this list server-side and is no longer independently writable. */
  external_ids?: ApiExternalId[]
  apr?: number | null
  principal?: number | null
  payoff_date?: string | null
  min_payment?: number | null
  apy?: number | null
  // server-computed, read-only
  balance: number
  balance_base: number
  cleared_balance: number
  cleared_balance_base: number
}

export interface ApiTxnSplit {
  category: string
  amount: number
  note?: string | null
}

export interface ApiTxn {
  id: string
  date: string
  kind: 'expense' | 'income' | 'transfer' | 'adjustment' | 'investment'
  category?: string | null
  payee_id?: string | null
  /** Server-attached display name for `payee_id` (absent when there is no payee). */
  payee_name?: string | null
  /** Bank/matching string — the ONLY freeform field read by dedup, rules, and payee
   *  resolution (ADR 2026-07-17). */
  description?: string | null
  /** Personal annotation — display-only, never read by matching/rules/payees. */
  memo?: string | null
  tags?: string[] | null
  splits?: ApiTxnSplit[] | null
  from_account_id?: string | null
  from_amount?: number | null
  from_currency?: string | null
  to_account_id?: string | null
  to_amount?: number | null
  to_currency?: string | null
  /** Server-attached account names for the two legs (absent for a missing leg). */
  from_account_name?: string | null
  to_account_name?: string | null
  /** Investment annotation on the cash leg of a buy/sell. */
  ticker?: string | null
  quantity?: number | null
  original_amount?: number | null
  original_currency?: string | null
  base_amount: number
  base_currency?: string | null
  cleared: boolean
  source?: string | null
  created_at?: string | null
  /** Review queue only: the automation that fired the create ("via …") and its stable id. */
  via?: string | null
  automation_id?: string | null
  deleted?: boolean
  needs_review?: boolean
  /** Non-blocking write-time flags (tracking-start honesty): a leg landed before its
   *  account's start, or a transfer straddles one. The row is still written. */
  warnings?: TxnWarning[] | null
  /** Provenance/dedup pairs — ONE list (ADR 2026-07-21); [0] = the creation-time origin.
   *  Display/dedup passthrough; the commit re-derives it server-side. Empty/omitted on a
   *  manually-entered row. */
  external_refs?: Array<{ source: string | null; ref: string }>
}

export interface TxnWarning {
  type: 'before_tracking_start' | 'straddle'
  account_id: string
  message: string
}

export interface ApiPayee {
  id: string
  name: string
  aliases?: string[]
  default_categories?: Record<string, string>
  autocategorize?: boolean
  hidden?: boolean
  txn_count?: number
  /** ISO date of most-recent use; live-derived server-side (see docs/DATA_MODEL.md). */
  last_used?: string | null
  rules?: Array<{ match_type: string; value: string }>
}

// ── Camelcase view types (internal, used by components) ───────────────────

export interface TxnSplit {
  category: string
  amount: number
  note: string
}

export interface Txn {
  id: string
  date: string
  kind: 'expense' | 'income' | 'transfer' | 'adjustment' | 'investment'
  category: string | null
  payeeId: string | null
  /** The payee's display name as the server sent it with the row. */
  payeeName: string | null
  /** Bank/matching string — the ONLY freeform field read by dedup, rules, and payee
   *  resolution (ADR 2026-07-17). */
  description: string
  /** Personal annotation — display-only, never read by matching/rules/payees. */
  memo: string
  tags: string[]
  splits: TxnSplit[]
  fromAccountId: string | null
  fromAmount: number | null
  fromCurrency: string | null
  toAccountId: string | null
  toAmount: number | null
  toCurrency: string | null
  /** The legs' account names, as the server sent them with the row. */
  fromAccountName?: string | null
  toAccountName?: string | null
  /** Investment annotation (cash leg of a buy/sell): what was traded, and how much. */
  ticker?: string | null
  quantity?: number | null
  origAmount: number | null
  origCurrency: string | null
  baseAmount: number
  baseCurrency: string
  cleared: boolean
  source: string
  createdAt: string
  deleted: boolean
  needsReview: boolean
  /** Review queue only: the automation that fired the create ("via …") and its stable id. */
  via?: string | null
  automationId?: string | null
  /** Provenance/dedup pairs — ONE list (ADR 2026-07-21); [0] = the creation-time origin.
   *  Any entry with source `"email"` drives the drawer's "Source emails" section (EM1);
   *  empty on a manually-entered row. */
  externalRefs: Array<{ source: string | null; ref: string }>
}

/** View shape of `ApiExternalId` — one connector this account is linked to. */
export interface ExternalId {
  source: string
  ref: string
  name: string
  mask: string
  linkedAt: string
  providerLabel: string
}

export interface Account {
  id: string
  name: string
  kind: 'asset' | 'liability'
  type: string
  /** Shared institution display name. */
  institution: string
  currency: string
  /** The account's stored starting balance + its "as of" date (edit-form source of truth —
   *  distinct from `balance`, the server-computed CURRENT balance). */
  initialBalance: number
  initialBalanceDate: string | null
  creditLimit: number | null
  lastFour: string | null
  note: string
  statementCloseDay: number | null
  paymentDueDay: number | null
  reminderDays: number | null
  lastReconciled: string | null
  archived: boolean
  connected: boolean
  /** Optional (rather than defaulting to `[]` in the type) so the many hand-built `Account`
   *  fixtures across the test suite that predate connector linking don't all need updating —
   *  every read site treats an absent array the same as an empty one. */
  externalIds?: ExternalId[]
  apr: number | null
  principal: number | null
  payoffDate: string | null
  minPayment: number | null
  apy: number | null
  // server-computed (never store)
  balance: number
  balanceBase: number
  clearedBalance: number
  clearedBalanceBase: number
  // client-derived
  role: 'securities' | 'cash'
  /** Market value of this account's holdings in base currency (positions, from /api/portfolio). */
  holdingsBase: number
}

export interface Category {
  /** Opaque, immutable id (`cat_<12hex>`) — see docs/adr/2026-07-05-category-opaque-ids.md.
   *  `label` is the only human-readable field; there is no more built-in/custom distinction. */
  id: string
  label: string
  color: string
  /** Category kinds are scoped like transactions (backend `_VALID_KINDS`): a category belongs to
   *  ONE kind and is only offered on transactions of that kind — see the Manage-categories modal
   *  (Expense / Inflow / Transfer / Investment). ('adjustment' rows never carry a category.) */
  kind: 'income' | 'expense' | 'transfer' | 'investment'
  /** Budget group name — not returned by GET /api/categories; may be absent. */
  group?: string
}

export interface Payee {
  id: string
  name: string
  aliases: string[]
  /** Per-kind auto-applied default category, keyed by transaction kind
   *  (expense|income|transfer|investment → category id). The create choke applies the
   *  default for the transaction's OWN kind, never cross-kind. */
  defaultCategories: Record<string, string>
  autocategorize: boolean
  hidden: boolean
  txnCount: number
  /** ISO date of most-recent use, or null if never used. */
  lastUsed: string | null
  /** Raw-match renaming rules (auto-categorisation), as stored server-side. */
  rules: Array<{ match_type: string; value: string }>
}

export interface ActivityEntry {
  action: string
  /** Actor TYPE id — drives the timeline icon + badge (you|lucius|agent|automation|system). */
  actor: string
  /** Resolved actor display name (a named agent shows its own name; else You/Lucius/System…). */
  actorName?: string | null
  /** The automation/channel that initiated this event — shown as "via <X>". */
  via?: string | null
  /** Stable id of the firing automation behind `via` (for future click-through). */
  automationId?: string | null
  at: string
  note?: string | null
  /** Statement-import provenance: which file/batch produced this row (links to the record). */
  import?: { batchId: string; statementId?: string; filename?: string; description?: string } | null
  /** Per-edit field diff (what changed): field key + before/after values. */
  changes?: Array<{ field: string; from: unknown; to: unknown }>
}

export interface TxnActivity {
  source: string
  addedBy: string
  /** The automation/channel that initiated the create — drives "Added by … · via X". */
  via?: string | null
  automationId?: string | null
  createdAt: string
  updatedAt: string
  /** Statement-import provenance for the row itself (batch → statement file). */
  import?: { batchId: string; statementId?: string; filename?: string; description?: string } | null
  activity: ActivityEntry[]
}

/** One source snapshot behind a booked transaction — the evidence the drawer's "Source"
 *  section shows, whether the row came from an email (EM1, unchanged) or a bank/broker
 *  connector fetch (connectors-as-data-sources RFC §B/UI spec §4). `unavailable: true` marks
 *  a snapshot the row provably came from whose evidence was never stashed / has since been
 *  pruned — shown as that entry's "unavailable" slot, never silently dropped. The loader
 *  returns the full list (email oldest-received-first, then connector entries in the txn's
 *  own `external_refs` order) or `null` on a 404 (no provenance of either kind at all).
 *
 *  `source`/`ref` are the exact external-ref pair this entry was matched against (CDS-6, ADR
 *  2026-09-07) — carried explicitly by the backend payload, never re-derived from `messageId`/
 *  `provider` here, so the drawer's "Unlink source" action posts the SAME pair the server
 *  used to find this entry. */
export type SourceEntry =
  | {
      kind: 'email'
      source: string
      ref: string
      messageId: string
      subject: string
      from: string
      receivedAt: string
      bodyText: string
      unavailable: false
    }
  | { kind: 'email'; source: string; ref: string; messageId: string; unavailable: true }
  | {
      kind: 'connector'
      source: string
      ref: string
      provider: string
      providerLabel: string
      providerAccountId: string
      fetchedAt: string
      raw: Record<string, unknown>
      unavailable: false
    }
  | {
      kind: 'connector'
      source: string
      ref: string
      provider: string
      providerLabel: string
      unavailable: true
    }

/** One statement a transaction is ON — NOT where the row came from. A row Lucius logged from
 *  an email alert and a statement later confirmed is on that statement and says so, while its
 *  `source` still correctly reads `ai`. Conflating "on" with "from" was the 2026-07-31 bug.
 *  An empty list is the honest answer for a hand entry, an agent booking or a bank-feed sync. */
export interface StatementLink {
  statementId: string
  label: string
  filename: string | null
  /** The bank's own printed reference for this row, when the statement carried one. */
  reference: string | null
  periodStart: string | null
  periodEnd: string | null
}

export type FxRates = Record<string, number>

// ── Mutation payload shapes ───────────────────────────────────────────────

export interface TransactionCreate {
  date: string
  kind: 'expense' | 'income' | 'transfer' | 'adjustment' | 'investment'
  category?: string | null
  payee_id?: string | null
  description?: string | null
  memo?: string | null
  tags?: string[]
  splits?: ApiTxnSplit[]
  from_account_id?: string | null
  from_amount?: number | null
  from_currency?: string | null
  to_account_id?: string | null
  to_amount?: number | null
  to_currency?: string | null
  original_amount?: number | null
  original_currency?: string | null
  cleared?: boolean
  source?: string
  needs_review?: boolean
  // Foreign-currency base value (ADR 2026-07-09): stamped once, preserved on edit; sending it is
  // the explicit "Value in SGD" override.
  base_amount?: number | null
}

export type TransactionUpdate = Partial<TransactionCreate>

export interface BulkUpdateData {
  category?: string | null
  tags?: string[]
  cleared?: boolean
  payee_id?: string | null
  needs_review?: boolean
  to_account_id?: string | null
  description?: string | null
  memo?: string | null
}

export interface AccountCreate {
  name: string
  type: string
  institution?: string
  currency: string
  initial_balance?: number | null
  initial_balance_date?: string | null
  credit_limit?: number | null
  last_four?: string | null
  note?: string
  statement_close_day?: number | null
  payment_due_day?: number | null
  /** Full replacement array — append-only on link, filtered on unlink (never a partial
   *  patch); `provider_label` is read-only and omitted on write. */
  external_ids?: Array<Omit<ApiExternalId, 'provider_label'>>
  apr?: number | null
  principal?: number | null
  payoff_date?: string | null
  min_payment?: number | null
  apy?: number | null
}

export type AccountUpdate = Partial<Omit<AccountCreate, 'type' | 'currency'>>

export interface ReconcileData {
  real_balance: number
  date?: string | null
  against?: 'cleared' | 'working'
  /** The balance the user was asked about — the server refuses (409) if the book has moved. */
  expected_balance?: number
}

export interface ReconcileResult {
  ok: boolean
  /** The booked `adjustment` txn, or null when the balance already matched. */
  adjustment: { to_amount?: number | null; from_amount?: number | null } | null
  balance: number
  cleared_balance: number
}

/** Narrow, connector-specific seed for `AccountModal`'s "Create account…" flow. */
export interface AccountPrefill {
  /** Starts the form on this type (a deep link like the Assets page's "Retirement" tile). */
  type?: AccountType
  name?: string
  currency?: string
  lastFour?: string
  institution?: string
}

// ── Derived / selector types ──────────────────────────────────────────────

export interface AccountGroup {
  key: string
  label: string
  color: string
  accounts: Account[]
  totalBase: number
}

/** One "Amount >= X" / "Amount <= X" / "Amount = X" register token. Multiple
 * constraints AND together (this is how the register builds a range). `dir`
 * scopes the comparison to the in/out leg relative to the current `accountId`
 * VIEW scope (or falls back to kind — income='in', else 'out' — with none set). */
export interface TxnAmountFilter {
  op: '=' | '<=' | '>='
  dir: 'any' | 'in' | 'out'
  value: number
}

export type TxnQField = 'any' | 'payee' | 'note' | 'category'

export interface TxnFilters {
  /** The sidebar's single selected account (the VIEW scope) — ANDs with accountIds. */
  accountId: string | null
  /** OR'd multi-select from 'account' register tokens (either leg). */
  accountIds: string[]
  search: string
  qField: TxnQField
  /** OR'd multi-select from 'type' register tokens. */
  kind: string[]
  /** OR'd multi-select from 'category' register tokens. */
  category: string[]
  /** OR'd multi-select from 'payee' register tokens, resolved to payee ids. */
  payeeIds: string[]
  /** OR'd multi-select from 'tag' register tokens. */
  tags: string[]
  /** OR'd multi-select from 'source' register tokens (the projected display vocab). */
  source: string[]
  dateFrom: string
  dateTo: string
  /** Tri-state: null = any, true = cleared only, false = uncleared only. */
  cleared: boolean | null
  needsReview: boolean
  /** AND'd amount constraints. */
  amounts: TxnAmountFilter[]
  /** Blank-category transactions only (Plan tab's Outside-envelopes deep-links — "Review in
   *  Accounts" for Uncategorized expense, "Label it" composed with `kind: ['income']` for
   *  uncategorized income; mirrors GET /api/transactions?uncategorized=true). */
  uncategorized: boolean
  /** Exactly the rows created/merged by ONE statement import (Statement Import's "View
   *  imported" deep-link — mirrors GET /api/transactions?batch_id=X). Null = no filter. */
  batchId: string | null
}

export type TxnSortKey = 'date' | 'amount' | 'description' | 'category'
export type SortDir = 'asc' | 'desc'
