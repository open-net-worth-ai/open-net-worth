/**
 * data.ts — Adapters between API (snake_case) and view (camelCase) types,
 * plus re-exports of helpers so component lanes have a single import point.
 *
 * API → View:   fromApiAccount, fromApiTxn, fromApiSourceEntry
 * View → API:   toApiTxnCreate  (for POST /api/transactions)
 */

import { kindOf } from './categories'
import { localIso, toBase as appToBase } from '../../../utils/format'
import type {
  ApiAccount,
  ApiTxn,
  Account,
  Txn,
  AccountCreate,
  TransactionCreate,
  TransactionUpdate,
  FxRates,
  SourceEntry,
  StatementLink,
  TxnSplit,
} from './types'

// ── Account adapter ───────────────────────────────────────────────────────

/**
 * Map a raw API account to the camelCase view shape.
 *
 * `holdingsBase`: the total market value in base currency for this account's
 * positions (from GET /api/portfolio, matched by account_id). Pass 0 if the
 * account has no holdings.
 */
export function fromApiAccount(api: ApiAccount, holdingsBase = 0): Account {
  const kind: 'asset' | 'liability' = api.kind ?? kindOf(api.type)
  const role: 'securities' | 'cash' = holdingsBase > 0 ? 'securities' : 'cash'
  return {
    id: api.id,
    name: api.name,
    kind,
    type: api.type,
    institution: api.institution ?? '',
    currency: api.currency,
    initialBalance: api.initial_balance ?? 0,
    initialBalanceDate: api.initial_balance_date ?? null,
    creditLimit: api.credit_limit ?? null,
    lastFour: api.last_four ?? null,
    note: api.note ?? '',
    statementCloseDay: api.statement_close_day ?? null,
    paymentDueDay: api.payment_due_day ?? null,
    reminderDays: api.reminder_days_before ?? null,
    lastReconciled: api.last_reconciled_at ?? null,
    archived: api.archived ?? false,
    connected: api.connected ?? false,
    externalIds: (api.external_ids ?? []).map((e) => ({
      source: e.source,
      ref: e.ref,
      name: e.name,
      mask: e.mask,
      linkedAt: e.linked_at,
      providerLabel: e.provider_label,
      status: e.status ?? 'connected',
    })),
    apr: api.apr ?? null,
    principal: api.principal ?? null,
    payoffDate: api.payoff_date ?? null,
    minPayment: api.min_payment ?? null,
    apy: api.apy ?? null,
    // Default to 0 when a response omits the hydrated balance (e.g. a non-list endpoint) — an
    // undefined here renders as "NaN" in the balance card / CC due alert.
    balance: api.balance ?? 0,
    balanceBase: api.balance_base ?? 0,
    clearedBalance: api.cleared_balance ?? 0,
    clearedBalanceBase: api.cleared_balance_base ?? 0,
    role,
    holdingsBase,
  }
}

/**
 * What an account is WORTH, in base currency: its holdings PLUS its balance.
 *
 * An `investment` account's settlement cash is part of its value, and drawn margin is that
 * cash gone negative (ADR 2026-09-07-seven-account-types-one-axis) — so the two ADD. Every
 * surface that showed the holdings INSTEAD of the balance silently dropped the margin debit:
 * the owner's brokerage read S$242,279 against a true S$231,010. `holdingsBase` is 0 for an
 * account that holds no positions, so this is the correct number for every type, which is why
 * it replaces the `role === 'securities'` branch at all five call sites rather than joining it.
 */
export function accountValue(a: Pick<Account, 'holdingsBase' | 'balanceBase'>): number {
  return a.holdingsBase + a.balanceBase
}

/** The same definition against the CLEARED balance. Holdings are settled, so they always count. */
export function accountClearedValue(
  a: Pick<Account, 'holdingsBase' | 'clearedBalanceBase'>,
): number {
  return a.holdingsBase + a.clearedBalanceBase
}

/** Reverse: view Account → AccountCreate payload for POST /api/accounts. */
export function toApiAccountCreate(a: Partial<Account>): AccountCreate {
  return {
    name: a.name ?? '',
    type: a.type ?? 'cash',
    institution: a.institution ?? '',
    currency: a.currency ?? 'SGD',
    credit_limit: a.creditLimit ?? null,
    last_four: a.lastFour ?? null,
    note: a.note ?? '',
    statement_close_day: a.statementCloseDay ?? null,
    payment_due_day: a.paymentDueDay ?? null,
    apr: a.apr ?? null,
    principal: a.principal ?? null,
    payoff_date: a.payoffDate ?? null,
    min_payment: a.minPayment ?? null,
    apy: a.apy ?? null,
  }
}

// ── Transaction adapter ───────────────────────────────────────────────────

/** Map a raw API transaction to the camelCase view shape. */
export function fromApiTxn(api: ApiTxn): Txn {
  return {
    id: api.id,
    date: api.date,
    kind: api.kind,
    category: api.category ?? null,
    payeeId: api.payee_id ?? null,
    payeeName: api.payee_name ?? null,
    description: api.description ?? '',
    memo: api.memo ?? '',
    tags: api.tags ?? [],
    splits: (api.splits ?? []).map((s) => ({
      category: s.category,
      amount: s.amount,
      note: s.memo ?? '',
    })),
    fromAccountId: api.from_account_id ?? null,
    fromAmount: api.from_amount ?? null,
    fromCurrency: api.from_currency ?? null,
    toAccountId: api.to_account_id ?? null,
    toAmount: api.to_amount ?? null,
    toCurrency: api.to_currency ?? null,
    fromAccountName: api.from_account_name ?? null,
    toAccountName: api.to_account_name ?? null,
    ticker: api.ticker ?? null,
    quantity: api.quantity ?? null,
    origAmount: api.original_amount ?? null,
    origCurrency: api.original_currency ?? null,
    baseAmount: api.base_amount,
    baseCurrency: api.base_currency ?? 'SGD',
    cleared: api.cleared,
    source: api.source ?? 'manual',
    createdAt: api.created_at ?? api.date,
    deleted: api.deleted ?? false,
    needsReview: api.needs_review ?? false,
    hidden: api.hidden ?? false,
    via: api.via ?? null,
    automationId: api.automation_id ?? null,
    externalRefs: (api.external_refs ?? []).map((e) => ({
      source: e.source ?? null,
      ref: e.ref,
    })),
  }
}

/** Map one GET /api/transactions/{id}/sources entry (snake_case, kind-tagged "email" |
 *  "connector") to the view shape. Was `fromApiSourceEmail` / GET .../source-emails — renamed
 *  with the connector union (connectors-as-data-sources UI spec §4); the email branch's own
 *  field mapping is unchanged. `source`/`ref` (CDS-6) are read straight off the payload, never
 *  re-derived from `provider`/`message_id` — they're the exact pair the drawer's "Unlink
 *  source" action posts to `POST /api/transactions/{id}/refs/detach`. */
export function fromApiSourceEntry(api: Record<string, unknown>): SourceEntry {
  const source = String(api.source ?? '')
  const ref = String(api.ref ?? '')
  if (api.kind === 'connector') {
    if (api.unavailable) {
      return {
        kind: 'connector',
        source,
        ref,
        provider: String(api.provider ?? ''),
        providerLabel: String(api.provider_label ?? ''),
        unavailable: true,
      }
    }
    return {
      kind: 'connector',
      source,
      ref,
      provider: String(api.provider ?? ''),
      providerLabel: String(api.provider_label ?? ''),
      providerAccountId: String(api.provider_account_id ?? ''),
      fetchedAt: String(api.fetched_at ?? ''),
      raw: (api.raw as Record<string, unknown>) ?? {},
      unavailable: false,
    }
  }
  if (api.unavailable) {
    return {
      kind: 'email',
      source,
      ref,
      messageId: String(api.message_id ?? ''),
      unavailable: true,
    }
  }
  return {
    kind: 'email',
    source,
    ref,
    messageId: String(api.message_id ?? ''),
    subject: String(api.subject ?? ''),
    from: String(api.from ?? ''),
    receivedAt: String(api.received_at ?? ''),
    bodyText: String(api.body_text ?? ''),
    unavailable: false,
  }
}

export function fromApiStatementLink(api: {
  statement_id?: string | null
  label?: string | null
  filename?: string | null
  reference?: string | null
  period_start?: string | null
  period_end?: string | null
}): StatementLink {
  return {
    statementId: api.statement_id ?? '',
    label: api.label ?? '',
    filename: api.filename ?? null,
    reference: api.reference ?? null,
    periodStart: api.period_start ?? null,
    periodEnd: api.period_end ?? null,
  }
}

/**
 * Canonical "is this row money IN?" rule for the two-leg model — the single source of truth
 * for signing an amount (+/−) or colouring it pos/neg. Money in = income OR any to-leg-only
 * row: an investment SELL (cash from a share sale) and a positive to-leg adjustment such as a
 * Starting-Balance deposit both live in the TO leg with no FROM leg. Signing by kind alone
 * (`income ? +1 : -1`) mis-signs those as negative — mirror this everywhere instead. Matches
 * the register row's own sign logic (TxnRow).
 */
export function txnIsInflow(t: Pick<Txn, 'kind' | 'fromAccountId' | 'toAccountId'>): boolean {
  return t.kind === 'income' || (!t.fromAccountId && !!t.toAccountId)
}

// ── Selection total ─────────────────────────────────────────────────────────

export interface SelectionTotal {
  amount: number
  /** Every selected leg shares this one currency → `amount` is the EXACT native sum. */
  currency: string | null
  /** Currencies mixed → `amount` is a base-currency approximation (marked ≈ in the UI). */
  approx: boolean
}

/**
 * Sum a register selection BY ITS LEGS — the figures the rows themselves display.
 * Account-scoped, a transfer counts by its leg on the viewed account (+ in / − out);
 * in the all-accounts view a transfer nets zero between own accounts and is skipped.
 * One shared currency → the exact native sum: a completed row's figure must never
 * drift with the market (owner, 2026-07-16). Mixed currencies → ≈ base: the trusted
 * historical stamp where present (its base_currency must match the current base —
 * the historical-rate lock), else the leg at the current cached rate.
 */
export function selectionTotal(
  rows: Txn[],
  selectedId: string | null,
  selAcctCurrency: string | null,
  baseCurrency: string,
  fx: FxRates,
): SelectionTotal {
  const parts: { native: number; ccy: string; base: number }[] = []
  for (const t of rows) {
    let sign: 1 | -1
    let leg: number | null
    let ccy: string
    if (t.kind === 'transfer') {
      if (!selectedId) continue
      const inflow = t.toAccountId === selectedId
      if (!inflow && t.fromAccountId !== selectedId) continue
      sign = inflow ? 1 : -1
      leg = inflow ? t.toAmount : t.fromAmount
      ccy = (inflow ? t.toCurrency : t.fromCurrency) || selAcctCurrency || baseCurrency
    } else {
      const inflow = txnIsInflow(t)
      sign = inflow ? 1 : -1
      leg = (inflow ? (t.toAmount ?? t.fromAmount) : (t.fromAmount ?? t.toAmount)) ?? null
      ccy =
        (inflow ? t.toCurrency || t.fromCurrency : t.fromCurrency || t.toCurrency) || baseCurrency
    }
    if (leg == null) continue
    const mag = Math.abs(leg)
    const stamp =
      t.kind !== 'transfer' && t.baseAmount && t.baseCurrency === baseCurrency ? t.baseAmount : null
    const baseMag = stamp != null ? Math.abs(stamp) : appToBase(mag, ccy, fx, baseCurrency)
    parts.push({ native: sign * mag, ccy, base: sign * baseMag })
  }
  const one = parts.length > 0 && parts.every((p) => p.ccy === parts[0]!.ccy) ? parts[0]!.ccy : null
  if (one != null)
    return { amount: parts.reduce((s, p) => s + p.native, 0), currency: one, approx: false }
  return { amount: parts.reduce((s, p) => s + p.base, 0), currency: null, approx: parts.length > 0 }
}

/**
 * Reverse: view Txn partial → TransactionCreate payload for POST /api/transactions.
 * Only the fields the API writes are included; balance fields are stripped.
 */
export function toApiTxnCreate(t: Partial<Txn>): TransactionCreate {
  return {
    date: t.date ?? localIso(),
    kind: t.kind ?? 'expense',
    category: t.category ?? null,
    payee_id: t.payeeId ?? null,
    description: t.description ?? null,
    memo: t.memo ?? null,
    tags: t.tags ?? [],
    splits:
      t.splits?.map((s) => ({ category: s.category, amount: s.amount, memo: s.note || '' })) ?? [],
    from_account_id: t.fromAccountId ?? null,
    from_amount: t.fromAmount ?? null,
    from_currency: t.fromCurrency ?? null,
    to_account_id: t.toAccountId ?? null,
    to_amount: t.toAmount ?? null,
    to_currency: t.toCurrency ?? null,
    original_amount: t.origAmount ?? null,
    original_currency: t.origCurrency ?? null,
    cleared: t.cleared ?? false,
    source: t.source ?? 'manual',
    needs_review: t.needsReview ?? false,
  }
}

/**
 * Map the transaction detail drawer's richer edit patch (amount/currency/toAmount/orig* —
 * TxnDetail.tsx's EditBody.save()) onto a TransactionUpdate PUT body. Pure/testable so this
 * money-routing logic doesn't need a full component render to verify.
 *
 * `amount`/`currency` land on the leg that carries the primary `account`: income,
 * increase-adjustment, and an investment SELL (to-leg only — no fromAccountId) credit the TO
 * leg; everything else (expense, decrease-adjustment, investment BUY, a transfer's source)
 * debits the FROM leg.
 *
 * `toAmount`/`toCurrency` are a transfer's destination leg. Backstop: when the transfer's two
 * legs share a currency, `to_amount` mirrors `from_amount` rather than trusting `patch.toAmount`
 * — a same-currency transfer's legs must be equal, and TxnDetail's own "Received" field is
 * hidden (and can go stale) whenever the currencies match, so this is a second line of defense
 * against any caller that ever re-sends that stale value.
 */
export function detailPatchToUpdate(patch: Record<string, unknown>): TransactionUpdate {
  const u: TransactionUpdate = {}
  const kind = patch.kind as TransactionUpdate['kind'] | undefined
  if (patch.date != null) u.date = patch.date as string
  if (patch.description != null) u.description = patch.description as string
  if (patch.memo != null) u.memo = patch.memo as string
  if (kind != null) u.kind = kind
  if (patch.category !== undefined) u.category = patch.category as string | null
  // Split allocation (P3-SP): an array re-validates against the leg (backend derives the
  // `split` sentinel from ≥2 lines); [] clears it back to a plain category. Omitted = unchanged.
  // The wire field is `memo` (renamed from `note` 2026-08-02); sending `note` was silently
  // ignored, so every save of a split wiped its line memos.
  if (patch.splits !== undefined)
    u.splits = (patch.splits as TxnSplit[]).map((s) => ({
      category: s.category,
      amount: s.amount,
      memo: s.note || '',
    }))
  if (patch.tags !== undefined) u.tags = patch.tags as string[]
  if (patch.cleared !== undefined) u.cleared = patch.cleared as boolean
  if (patch.fromAccountId !== undefined) u.from_account_id = patch.fromAccountId as string | null
  if (patch.toAccountId !== undefined) u.to_account_id = patch.toAccountId as string | null
  // A leg with no account has no amount. An omitted field means "unchanged" to the PUT, and
  // the backend's `_normalise_leg` keeps a bare amount beside a null account id — so an income
  // stored on the from-leg (written before the ledger refused that shape) came out of the
  // edit-form repair with from_account_id=null and an orphaned from_amount (owner report
  // 2026-09-20). Clear the emptied leg explicitly; the PUT's clearable list honours the null.
  if (patch.fromAccountId === null) {
    u.from_amount = null
    u.from_currency = null
  }
  if (patch.toAccountId === null) {
    u.to_amount = null
    u.to_currency = null
  }
  const wantsToLeg =
    kind === 'income' ||
    ((kind === 'adjustment' || kind === 'investment') &&
      patch.toAccountId != null &&
      patch.fromAccountId == null)
  if (patch.amount !== undefined) {
    if (wantsToLeg) u.to_amount = patch.amount as number
    else u.from_amount = patch.amount as number
  }
  if (patch.currency !== undefined) {
    if (wantsToLeg) u.to_currency = patch.currency as string
    else u.from_currency = patch.currency as string
  }
  if (patch.toAmount !== undefined) {
    u.to_amount =
      kind === 'transfer' && patch.currency === patch.toCurrency
        ? (u.from_amount ?? (patch.toAmount as number))
        : (patch.toAmount as number)
  }
  if (patch.toCurrency !== undefined) u.to_currency = patch.toCurrency as string
  if (patch.origAmount !== undefined) u.original_amount = patch.origAmount as number | null
  if (patch.origCurrency !== undefined) u.original_currency = patch.origCurrency as string | null
  // Foreign-currency base value (ADR 2026-07-09) — only sent when the user set "Value in SGD".
  // base_amount is stamped once and preserved on edits that don't touch the leg; sending it is
  // the explicit override.
  if (patch.pinnedBase !== undefined) u.base_amount = patch.pinnedBase as number
  return u
}

// ── Re-exports for component lanes ────────────────────────────────────────

export {
  // format
  ZERO_DEC,
  sym,
  grp,
  fCur,
  fBase,
  fxInfo,
  toBase,
} from './format'

export {
  // category picker helpers (server-driven — no hardcoded list)
  catOptionsFor,
  catSelectGroups,
  isRefundTxn,
  // account type vocab (a sidebar "group" IS the type — no separate group vocabulary)
  ACCOUNT_TYPES,
  TYPE_LABELS,
  TYPE_MEANING,
  TYPE_COLOR,
  LIABILITY_TYPES,
  TYPE_ORDER,
  kindOf,
  // tags
  slugTag,
  normalizeTags,
  liveTagFormat,
  // provenance
  SOURCE_META,
  ACTOR_TYPE,
  ACTION_VERB,
} from './categories'
