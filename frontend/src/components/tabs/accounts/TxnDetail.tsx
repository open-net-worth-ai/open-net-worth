// TxnDetail.tsx — transaction detail drawer: View ⇄ Edit modes
import * as Dialog from '@radix-ui/react-dialog'
import { Fragment, useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { StatementViewer } from './StatementsSection'
import { ReceiptsSection } from './ReceiptsSection'
import { CcyPicker } from './ds'
import {
  SOURCE_META,
  ACTION_VERB,
  catOptionsFor,
  normalizeTags,
  liveTagFormat,
  isRefundTxn,
  fCur,
  fxInfo,
} from './data'
import { toBase, fBase } from './format'
import { txnMentionSublabel, fDate } from '../../../utils/format'
import { EntityRow } from '../../../lucius/EntityRow'
import { Minus, Plus, X } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePointerResize } from '../../../hooks/usePointerResize'
import { DRAG_SURFACE_PROPS, useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import type {
  Txn,
  Account,
  Category,
  TxnActivity,
  FxRates,
  SourceEntry,
  StatementLink,
} from './types'
import { AddAccountSelect, AddDateField, AddPayeeField, AddCategoryField } from './pickers'
import { SplitEditor, type EditSplit } from './SplitEditor'
import { ConfirmModal } from './ConfirmModal'
import { Checkbox } from '../../ui/Checkbox'
import { InfoPopover } from '../../ui/InfoPopover'
import { KIND_LABEL } from './transactionReview'

// ── Activity field-diff formatting (what changed per edit) ─────────────────────
const CHANGE_FIELD_LABEL: Record<string, string> = {
  from_amount: 'Amount',
  to_amount: 'Amount',
  base_amount: 'Base amount',
  category: 'Category',
  payee_id: 'Payee',
  note: 'Note',
  description: 'Description',
  memo: 'Memo',
  date: 'Date',
  cleared: 'Cleared',
  kind: 'Type',
  tags: 'Tags',
  from_account_id: 'From account',
  to_account_id: 'To account',
  from_currency: 'Currency',
  to_currency: 'Currency',
  original_amount: 'Original amount',
  original_currency: 'Original currency',
}
// Internal bookkeeping fields that are never meaningful in a user-facing diff.
// needs_review renders as its own verb (Approved / Flagged for review), never as a field edit.
const CHANGE_FIELD_HIDDEN = new Set(['base_amount', 'merged_from', 'external_refs', 'needs_review'])
interface ChangeCtx {
  acctMap: Record<string, Account>
  catMap: Record<string, Category>
  payeeMap: Record<string, string>
}
function fmtChangeVal(field: string, v: unknown, ctx: ChangeCtx): string {
  if (v == null || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  // Resolve ids to human labels; fall back to the raw id when unresolvable (e.g. deleted) —
  // EXCEPT category, whose id is an opaque `cat_xxx` that must never paint to the user.
  if (field.endsWith('account_id')) return ctx.acctMap[String(v)]?.name ?? String(v)
  if (field === 'payee_id') return ctx.payeeMap[String(v)] ?? String(v)
  if (field === 'category') return ctx.catMap[String(v)]?.label ?? 'Uncategorized'
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
  if (typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(v)
}

export interface TxnDetailProps {
  txn: Txn | null
  accounts: Account[]
  categories: Category[]
  // Name-based options for the payee picker (value = display name; the save resolves name→id).
  payeeOptions: Array<{ value: string; label: string; defaultCategory?: string | null }>
  // id → display name — the picker is name-keyed, so resolving a stored payee_id needs this.
  payeeById?: Record<string, string>
  actors?: Record<string, { name: string; type: string }>
  baseCurrency: string
  rates: FxRates
  onClose: () => void
  /** Open already editing (the review card's Edit); re-applied whenever the txn changes. */
  initialMode?: 'view' | 'edit'
  onSave: (id: string, patch: Record<string, unknown>) => Promise<void> | void
  onDelete: (txn: Txn) => void
  /** Erase it: the row, its history's contents, everything kept only about it — no undo.
   *  Omitted (the import review's drafts), the action is not offered. */
  onPurge?: (txn: Txn) => void
  onMarkReviewed: (txn: Txn) => void
  /** Flip cleared ⇄ uncleared from View, at once — like the register's cleared column. Omitted
   *  (the import review's drafts), the status shows but can't be flipped there. */
  onToggleCleared?: (txn: Txn, cleared: boolean) => void
  onManagePayees: () => void
  onCreateCategory: (
    prefill: string,
    kind: 'income' | 'expense' | 'transfer' | 'investment',
    cb: (id: string) => void,
  ) => void
  onLoadActivity: (id: string) => Promise<TxnActivity>
  /** Every source snapshot behind a booked txn — email (EM1) or connector (connectors-as-
   *  data-sources) — oldest-received-first for email, then connector entries in the txn's own
   *  external_refs order; pruned snapshots arrive as explicit `unavailable` entries. null = 404
   *  (no provenance of either kind). */
  onLoadSourceEntries: (id: string) => Promise<SourceEntry[] | null>
  /** Detach one provenance pair from this row (CDS-6, ADR 2026-09-07's "Unlink source") — the
   *  one deliberate removal path for an otherwise append-only `external_refs`. Rejects on
   *  failure (the drawer shows the section's inline error + Retry); resolves once the
   *  server's write has landed so the caller can refresh the row's `externalRefs` for its
   *  badges elsewhere in the app. */
  onDetachSourceRef: (txn: Txn, source: string, ref: string) => Promise<void>
  /** The statement(s) this row is ON. OPTIONAL: the panel is additive enrichment, so a
   *  caller that omits it simply renders no statement row — never a broken drawer. */
  onLoadStatementLinks?: (id: string) => Promise<StatementLink[]>
  /** True when this drawer is opened from WITHIN another modal (the import review's matched-
   *  ledger-entry link) — stacks it above that modal instead of the normal register case's
   *  z:120 (deliberately below --z-modal there, so a ConfirmModal from a plain edit still
   *  stacks over it). Both this drawer and the modal it's nested in render through a Radix
   *  `Dialog.Portal` straight to `document.body`, so an ancestor wrapper's stacking context
   *  can't reach the portalled content at all — the z-index has to live on the drawer's own
   *  root instead. */
  liftAboveModal?: boolean
  /** The row is still on its way (opened by id — a receipt chip, a deep link): `txn` is a
   *  placeholder carrying the id, so the sections that load by id start at once, while the
   *  hero and the fact rows show the drawer's own skeleton and the actions wait. */
  loading?: boolean
  /** A write the HOST made for this drawer failed — an approve, a delete, a new category — or
   *  the row never loaded. Said here, in the footer (in place of the skeleton for a row that
   *  never came), because these surfaces show no toast bar; the host clears it on its next
   *  write. */
  actionError?: string | null
}

// ── Source (email — EM1 — or connector) ─────────────────────────────────────
type SourceState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; data: SourceEntry[] }
  | { status: 'unavailable' }
  | { status: 'error' }

// ── Helpers ──────────────────────────────────────────────────────────────────
// Exported for ReceiptsSection.tsx (self-fetching, per the receipts UI spec — never
// prop-threaded), so the receipt row's date matches every other date in this drawer.
// The drawer is the audit view of a ledger row, so the year is always shown.
export function fmtDay(iso: string | null | undefined): string {
  return iso ? fDate(iso.split('T')[0]!, 'short', 'always') : '—'
}
// Date-only values stay verbatim (no timezone shift). A full timestamp is stored in UTC
// (…Z) — render it in the viewer's LOCAL time (HH:MM, 24h, no seconds), like the rest of the drawer.
function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = iso.includes('T') ? new Date(iso) : null
  if (!d || isNaN(d.getTime())) return fmtDay(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${fDate(d, 'short', 'always')}  •  ${hh}:${mm}`
}
/** "provider_account_id" → "Provider Account Id" — the raw-fields toggle's row labels
 *  (connectors-as-data-sources UI spec §4.4). */
// Namespaces a statement FILE mints refs under (mirrors backend `FILE_IMPORT_NAMESPACES`).
// They are dedup fingerprints, not evidence: no snapshot is ever stored for them.
const FILE_IMPORT_SOURCES = new Set([
  'statement',
  'ofx',
  'csv',
  'qif',
  'xlsx',
  'mt940',
  'camt',
  'pdf',
])

function hasProvenanceRef(refs: Array<{ source: string | null }> | undefined): boolean {
  return !!refs?.some((r) => !r.source || !FILE_IMPORT_SOURCES.has(r.source))
}

function titleCaseRawKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
// `opening` is its own actor — the Starting Balance row is neither a hand entry nor a
// reconcile, and labelling it either has always been a lie (ADR 2026-07-28).
const SRC_TO_TYPE: Record<string, string> = {
  manual: 'you',
  ai: 'lucius',
  bank: 'bank',
  import: 'import',
  reconcile: 'system',
  automation: 'automation',
  agent: 'agent',
  opening: 'opening',
}
const TYPE_TO_SRC: Record<string, string> = {
  you: 'manual',
  lucius: 'ai',
  bank: 'bank',
  import: 'import',
  system: 'reconcile',
  automation: 'automation',
  agent: 'agent',
  opening: 'opening',
}
const SVG_D: Record<string, string> = {
  bank: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>',
  import: '<path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M5 21h14"/>',
  reconcile: '<path d="M4 8h13l-3-3"/><path d="M20 16H7l3 3"/>',
  automation:
    '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  agent:
    '<rect x="4" y="8" width="16" height="11" rx="2.5"/><path d="M12 8V4.5"/><circle cx="12" cy="3.5" r="1"/><path d="M9 13v1.5M15 13v1.5"/>',
  manual: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  opening: '<path d="M5 21V4"/><path d="M5 4h11l-2 3.5L16 11H5"/>',
}
function DSourceIcon({ source }: { source: string }) {
  if (source === 'ai') return <span className="acct-src-ic lucius">✦</span>
  return (
    <span className={`acct-src-ic${source === 'agent' ? ' agent' : ''}`}>
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        dangerouslySetInnerHTML={{ __html: SVG_D[source] ?? SVG_D.manual ?? '' }}
      />
    </span>
  )
}
function ActorIcon({ type }: { type: string }) {
  return <DSourceIcon source={TYPE_TO_SRC[type] ?? 'manual'} />
}
// The badge classifies a NAMED actor ("Transaction & Trade Logger" → Agent). When the name
// already is the type ("Automation", "System"), it would only repeat itself — so it stays out.
function ActorBadge({ type, name }: { type: string; name?: string }) {
  const L: Record<string, string> = {
    lucius: 'AI',
    automation: 'Automation',
    agent: 'Agent',
    bank: 'Sync',
    import: 'Import',
    system: 'System',
    opening: 'Starting balance',
  }
  const label = L[type]
  if (!label || (name && name.trim().toLowerCase() === label.toLowerCase())) return null
  return <span className={`acct-detail-badge ${type}`}>{label}</span>
}
function DRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="acct-detail-row">
      <span className="acct-detail-k">{label}</span>
      <span className="acct-detail-v">{children}</span>
    </div>
  )
}
function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="acct-edit-field">
      <div className="acct-edit-label">{label}</div>
      {children}
      {hint && <div className="acct-edit-hint">{hint}</div>}
    </div>
  )
}

// ── Edit mode ─────────────────────────────────────────────────────────────────
interface EditBodyProps {
  txn: Txn
  accounts: Account[]
  acctMap: Record<string, Account>
  categories: Category[]
  catMap: Record<string, Category>
  payeeOptions: TxnDetailProps['payeeOptions']
  payeeName: string
  baseCurrency: string
  rates: FxRates
  onManagePayees: () => void
  onCreateCategory: TxnDetailProps['onCreateCategory']
  onSave: (id: string, patch: Record<string, unknown>) => Promise<void> | void
  onCancel: () => void
  actionError?: string | null
}
function EditBody({
  txn,
  accounts,
  acctMap,
  categories,
  catMap,
  payeeOptions,
  payeeName,
  baseCurrency,
  rates,
  onManagePayees,
  onCreateCategory,
  onSave,
  onCancel,
  actionError,
}: EditBodyProps) {
  const isInc0 = txn.kind === 'income'
  // The kind's own leg first, but whichever leg HAS the money if not: an income stored on the
  // from-leg (written before the ledger refused that shape) opened as amount 0 with no
  // account, and its save was refused for exactly that (owner report 2026-09-20). Seeded from
  // the leg that exists, the save below writes it back on the right leg.
  const mainAmt = isInc0
    ? (txn.toAmount ?? txn.fromAmount ?? 0)
    : (txn.fromAmount ?? txn.toAmount ?? 0)
  const mainAcct = isInc0
    ? (txn.toAccountId ?? txn.fromAccountId ?? '')
    : (txn.fromAccountId ?? txn.toAccountId ?? '')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [f, setF] = useState({
    kind: txn.kind as string,
    amount: String(mainAmt),
    date: txn.date,
    account: mainAcct,
    toAccount:
      txn.toAccountId && txn.toAccountId !== txn.fromAccountId ? (txn.toAccountId ?? '') : '',
    category: txn.category ?? '',
    payee: payeeName,
    description: txn.description ?? '',
    memo: txn.memo ?? '',
    tags: (txn.tags ?? []).join(', '),
    origAmount: txn.origAmount != null ? String(txn.origAmount) : '',
    origCurrency: txn.origCurrency ?? '',
    toAmount: txn.toAmount != null ? String(txn.toAmount) : '',
    // Foreign-currency base-value PIN (ADR 2026-07-09): the base-ccy (e.g. SGD) value the user
    // actually transacted at — pre-filled with the stored value, editable, locked on save.
    baseAmount: txn.baseAmount != null ? String(Math.abs(txn.baseAmount)) : '',
  })
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }))
  const [cleared, setCleared] = useState(txn.cleared)
  // Split allocation (P3-SP). Amount kept as a string per line for mid-typing; parsed on save.
  // Split mode is active when there are lines; only expense/income can split (backend gate).
  const [splits, setSplits] = useState<EditSplit[]>(() =>
    (txn.splits ?? []).map((s) => ({
      category: s.category,
      amount: String(Math.abs(s.amount)),
      note: s.note ?? '',
    })),
  )
  const [dir, setDir] = useState<'increase' | 'decrease'>(
    txn.kind === 'adjustment' && txn.fromAccountId ? 'decrease' : 'increase',
  )
  // An investment SELL is to-leg only (no fromAccountId — cash in from a share sale). The form
  // STARTS from the original direction, never an assumed buy, anchored to the immutable `txn`
  // prop; the Amount sign then flips it buy ⇄ sell, the way adjustment's sign flips raise/lower.
  const wasInvestmentSell = txn.kind === 'investment' && !txn.fromAccountId && !!txn.toAccountId
  const [investSell, setInvestSell] = useState(wasInvestmentSell)
  const switchKind = (k: string) => {
    // Leaving expense/income drops any split allocation (a transfer/adjustment/investment leg
    // can't carry splits — the backend would 422). switching between expense↔income keeps them.
    if (k !== 'expense' && k !== 'income') setSplits([])
    // Switching INTO Invest keeps the sign on screen: income (+) becomes a sell, expense (−) a
    // buy, an adjustment follows its raise/lower. A transfer shows no direction of its own, so
    // the investment's own direction stands.
    if (k === 'investment' && f.kind !== 'investment') {
      if (f.kind === 'income') setInvestSell(true)
      else if (f.kind === 'expense') setInvestSell(false)
      else if (f.kind === 'adjustment') setInvestSell(dir === 'increase')
    }
    setF((s) =>
      k === 'expense' && s.category && catMap[s.category]?.kind === 'income'
        ? { ...s, kind: k, category: '' }
        : { ...s, kind: k },
    )
  }
  const enterSplit = () =>
    setSplits([
      { category: f.category && f.category !== 'split' ? f.category : '', amount: '', note: '' },
      { category: '', amount: '', note: '' },
    ])
  const exitSplit = () => {
    setSplits([])
    if (f.category === 'split') set('category', '')
  }
  const isTransfer = f.kind === 'transfer',
    isAdjustment = f.kind === 'adjustment',
    isInvestment = f.kind === 'investment'
  // The Amount badge shows an inflow sign (+) for income AND an investment SELL (cash in from a
  // share sale) — mirrors the View header's heroPos. Without it a positive sale reads as "−".
  const inflow = f.kind === 'income' || (isInvestment && investSell)
  const fallbackCcy = isInc0 ? (txn.toCurrency ?? baseCurrency) : (txn.fromCurrency ?? baseCurrency)
  const ccy = acctMap[f.account]?.currency ?? fallbackCcy
  const toCcy = acctMap[f.toAccount]?.currency ?? ccy

  function save() {
    const amt = parseFloat(f.amount) || 0
    const hasOrig = !!f.origCurrency && f.origCurrency !== ccy && parseFloat(f.origAmount) > 0
    // Foreign-currency base value (ADR 2026-07-09): only send "Value in SGD" when the user
    // actually CHANGED it from the stored value. Otherwise leave it out — the write choke
    // preserves the stamped value on an unrelated edit and recomputes it when the amount changes,
    // both automatically (no flag). Sending the pre-filled value on every save would wrongly
    // override a recompute when the amount changed.
    const baseInit = txn.baseAmount != null ? Math.abs(txn.baseAmount) : NaN
    const baseNow = parseFloat(f.baseAmount)
    const baseEdited =
      ccy !== baseCurrency &&
      f.baseAmount.trim() !== '' &&
      baseNow > 0 &&
      Math.abs(baseNow - baseInit) > 0.005
    const patch: Record<string, unknown> = {
      amount: amt,
      baseAmount: toBase(amt, ccy, rates),
      currency: ccy,
      ...(baseEdited ? { pinnedBase: baseNow } : {}),
      origAmount: hasOrig ? parseFloat(f.origAmount) : null,
      origCurrency: hasOrig ? f.origCurrency : null,
      date: f.date,
      payeeId: isTransfer || isAdjustment ? null : f.payee || null,
      description: f.description,
      memo: f.memo,
      tags: normalizeTags(f.tags),
      kind: f.kind,
      // Sent only when changed: the register's cleared column may have moved it meanwhile.
      ...(cleared !== txn.cleared ? { cleared } : {}),
    }
    if (isAdjustment) {
      patch.accountId = f.account
      patch.category = null
      if (dir === 'increase') {
        patch.toAccountId = f.account
        patch.fromAccountId = null
      } else {
        patch.fromAccountId = f.account
        patch.toAccountId = null
      }
    } else if (isTransfer) {
      patch.fromAccountId = f.account
      patch.toAccountId = f.toAccount || f.account
      patch.accountId = f.account
      patch.category = f.category || null
      patch.origAmount = null
      patch.origCurrency = null
      // The "Received" field (f.toAmount) only renders — and only gets kept in sync with the
      // user's edits — when the two legs' currencies differ (see the Field below). When they
      // match, f.toAmount still holds whatever to_amount the transaction loaded with, so
      // trusting it here would silently overwrite to_amount with a stale number every time the
      // main Amount field is edited (same-currency transfer's two legs must be equal anyway).
      patch.toAmount = toCcy !== ccy ? parseFloat(f.toAmount) || amt : amt
      patch.toCurrency = acctMap[f.toAccount]?.currency ?? ccy
    } else if (f.kind === 'investment') {
      // Buy = from-leg (cash out), sell = to-leg (cash in) — see investSell. Category is
      // optional — keep whatever the user set.
      if (investSell) {
        patch.toAccountId = f.account
        patch.fromAccountId = null
        patch.accountId = f.account
        patch.category = f.category || null
      } else {
        patch.fromAccountId = f.account
        patch.toAccountId = null
        patch.accountId = f.account
        patch.category = f.category || null
      }
    } else if (f.kind === 'income') {
      patch.toAccountId = f.account
      patch.fromAccountId = null
      patch.accountId = f.account
      patch.category = f.category || null
    } else {
      patch.fromAccountId = f.account
      patch.toAccountId = null
      patch.accountId = f.account
      patch.category = f.category || null
    }
    // Split allocation (P3-SP): only expense/income may split. In split mode send the lines and
    // DROP the top-level category — the backend derives the `split` sentinel (≥2 lines) or
    // collapses a single line to a plain category. If this row WAS a split and the user unsplit,
    // send [] to clear it back to the picked (or blank) category.
    if ((f.kind === 'expense' || f.kind === 'income') && splits.length > 0) {
      patch.splits = splits.map((l) => ({
        category: l.category,
        amount: Math.abs(parseFloat(l.amount) || 0),
        note: l.note || '',
      }))
      delete patch.category
    } else if (txn.category === 'split') {
      patch.splits = []
    }
    // The failure lands HERE, beside the button: the tab's toast sits under the phone sheet,
    // so a refused save read as a tap that did nothing.
    setSaveError(null)
    void Promise.resolve(onSave(txn.id, patch)).catch((error: unknown) =>
      setSaveError(error instanceof Error ? error.message : 'Couldn’t save the transaction'),
    )
  }

  // The picker offers categories of the SAME kind as the transaction — a transfer shows
  // transfer-kind categories, an investment investment-kind (categories are kind-scoped; owner
  // 2026-07-10). catPickKind drives both the options list and a "New category" quick-create.
  const catPickKind = isTransfer
    ? 'transfer'
    : isInvestment
      ? 'investment'
      : f.kind === 'income'
        ? 'income'
        : 'expense'
  const catOpts = catOptionsFor(catPickKind, categories, f.category)
  // Split editor gating (expense/income only). splitError, when set, blocks Save and tells the
  // user why — mirroring the backend's _validate_splits rules so a save can never 422.
  const canSplit = f.kind === 'expense' || f.kind === 'income'
  const splitMode = canSplit && splits.length > 0
  const splitTotal = parseFloat(f.amount) || 0
  const splitRemaining =
    Math.round((splitTotal - splits.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0)) * 100) /
    100
  const splitError = !splitMode
    ? ''
    : splits.some((l) => !l.category)
      ? 'Each split line needs a category'
      : splits.some((l) => (parseFloat(l.amount) || 0) <= 0)
        ? 'Each split line needs an amount above 0'
        : Math.abs(splitRemaining) > 0.01
          ? `Lines must sum to ${fCur(splitTotal, ccy, { cents: true })}`
          : ''
  return (
    <>
      <div className="acct-edit-body">
        {isAdjustment ? (
          <div className="acct-edit-adjbadge">
            Balance adjustment<span>corrects this account's balance</span>
          </div>
        ) : (
          <div className="acct-edit-seg">
            {(['expense', 'income', 'transfer', 'investment'] as const).map((k) => (
              <button
                key={k}
                className={`acct-edit-seg-btn${f.kind === k ? ` on ${k}` : ''}`}
                onClick={() => switchKind(k)}
              >
                {k === 'investment' ? 'Invest' : k.charAt(0).toUpperCase() + k.slice(1)}
              </button>
            ))}
          </div>
        )}
        <Field label="Amount">
          <div className="acct-edit-amt">
            {isAdjustment ? (
              <button
                type="button"
                className={`acct-edit-sign tog ${dir === 'increase' ? 'pos' : 'neg'}`}
                onClick={() => setDir((d) => (d === 'increase' ? 'decrease' : 'increase'))}
                title="Switch raise / lower"
              >
                <SignGlyph positive={dir === 'increase'} />
              </button>
            ) : isInvestment ? (
              <button
                type="button"
                className={`acct-edit-sign tog ${investSell ? 'pos' : 'neg'}`}
                onClick={() => setInvestSell((v) => !v)}
                title={investSell ? 'Sell — switch to buy' : 'Buy — switch to sell'}
                aria-label="Switch buy / sell"
              >
                <SignGlyph positive={investSell} />
              </button>
            ) : (
              <span className={`acct-edit-sign ${inflow ? 'pos' : 'neg'}`}>
                <SignGlyph positive={inflow} />
              </span>
            )}
            <span className="acct-edit-ccy">{ccy}</span>
            <input
              className="acct-edit-amt-in"
              type="number"
              inputMode="decimal"
              value={f.amount}
              onChange={(e) => set('amount', e.target.value)}
              placeholder="0.00"
            />
          </div>
        </Field>
        {(ccy !== baseCurrency || (isTransfer && toCcy !== baseCurrency)) && !isAdjustment && (
          <Field
            label={`Value in ${baseCurrency}`}
            hint="The value you actually transacted at — locked, not auto-converted at today's rate"
          >
            <div className="acct-edit-amt">
              <span className="acct-edit-ccy">{baseCurrency}</span>
              <input
                className="acct-edit-amt-in"
                type="number"
                inputMode="decimal"
                value={f.baseAmount}
                onChange={(e) => set('baseAmount', e.target.value)}
                placeholder="0.00"
              />
            </div>
          </Field>
        )}
        {!isTransfer && !isAdjustment && (
          <Field label="Original charge" hint="If you were billed in another currency">
            <div className="acct-edit-orig">
              <input
                className="acct-edit-in"
                type="number"
                inputMode="decimal"
                value={f.origAmount}
                onChange={(e) => set('origAmount', e.target.value)}
                placeholder="0.00"
              />
              <CcyPicker value={f.origCurrency} onChange={(c) => set('origCurrency', c)} />
            </div>
          </Field>
        )}
        {isTransfer && f.toAccount && toCcy !== ccy && (
          <Field
            label={`Received (${toCcy})`}
            hint="Amount that arrived, in the destination currency"
          >
            <input
              className="acct-edit-in"
              type="number"
              inputMode="decimal"
              value={f.toAmount}
              onChange={(e) => set('toAmount', e.target.value)}
              placeholder="0.00"
            />
          </Field>
        )}
        <Field label="Date">
          <AddDateField
            value={f.date}
            onChange={(v: string) => set('date', v)}
            advance={() => {}}
          />
        </Field>
        {isTransfer ? (
          <>
            <div className="acct-edit-grid2">
              {/* Switching either leg's account can change whether the two currencies match —
                  resetting toAmount to mirror the current Amount keeps the (possibly
                  newly-revealed) Received field from silently carrying over a number from the
                  OLD currency pairing under a new currency label. */}
              <Field label="From">
                <AddAccountSelect
                  accounts={accounts}
                  value={f.account}
                  onChange={(v: string) => setF((s) => ({ ...s, account: v, toAmount: s.amount }))}
                  advance={() => {}}
                />
              </Field>
              <Field label="To">
                <AddAccountSelect
                  accounts={accounts}
                  value={f.toAccount}
                  onChange={(v: string) =>
                    setF((s) => ({ ...s, toAccount: v, toAmount: s.amount }))
                  }
                  advance={() => {}}
                />
              </Field>
            </div>
            <Field
              label="Category (optional)"
              hint="Label — affects the budget only when one side is off-budget"
            >
              <AddCategoryField
                value={f.category}
                options={catOpts}
                onChange={(k: string) => set('category', k)}
                clearable
                onCreate={(prefill, cb) => onCreateCategory(prefill, catPickKind, cb)}
                advance={() => {}}
              />
            </Field>
          </>
        ) : isAdjustment ? (
          <Field label="Account">
            <AddAccountSelect
              accounts={accounts}
              value={f.account}
              onChange={(v: string) => set('account', v)}
              advance={() => {}}
            />
          </Field>
        ) : splitMode ? (
          <>
            <Field label="Account">
              <AddAccountSelect
                accounts={accounts}
                value={f.account}
                onChange={(v: string) => set('account', v)}
                advance={() => {}}
              />
            </Field>
            <SplitEditor
              lines={splits}
              onChange={setSplits}
              total={splitTotal}
              currency={ccy}
              options={catOpts}
              onCreateCategory={(prefill, cb) => onCreateCategory(prefill, catPickKind, cb)}
              onUnsplit={exitSplit}
            />
          </>
        ) : (
          <div className="acct-edit-grid2">
            <Field label="Account">
              <AddAccountSelect
                accounts={accounts}
                value={f.account}
                onChange={(v: string) => set('account', v)}
                advance={() => {}}
              />
            </Field>
            <Field label="Category (optional)">
              <AddCategoryField
                value={f.category}
                options={catOpts}
                onChange={(k: string) => set('category', k)}
                clearable
                onCreate={(prefill, cb) => onCreateCategory(prefill, catPickKind, cb)}
                advance={() => {}}
              />
              {canSplit && (
                <button type="button" className="acct-edit-splitlink" onClick={enterSplit}>
                  Split into multiple categories
                </button>
              )}
            </Field>
          </div>
        )}
        {!isTransfer && !isAdjustment && (
          <Field
            label="Payee"
            hint={isInvestment ? 'The broker or platform' : 'Who you paid / who paid you'}
          >
            <AddPayeeField
              value={f.payee}
              onChange={(v: string, defCat?: string | null) =>
                // The offered default is the payee's EXPENSE category — never valid on an
                // investment row (categories are kind-scoped).
                setF((s) => ({
                  ...s,
                  payee: v,
                  category: s.category || (isInvestment ? '' : (defCat ?? '')),
                }))
              }
              options={payeeOptions}
              accounts={[]}
              fromAccountId={null}
              onSelectTransfer={() => {}}
              onManagePayees={onManagePayees}
              advance={() => {}}
            />
          </Field>
        )}
        <hr className="acct-edit-divider" />
        <Field
          label="Description"
          hint="The merchant / bank text. Used for matching, rules & payees — keep it the stable merchant name."
        >
          <textarea
            className="acct-edit-in acct-note-area"
            value={f.description}
            onChange={(e) => set('description', e.target.value)}
            placeholder="Merchant or bank description"
            rows={2}
          />
        </Field>
        <Field
          label="Memo"
          hint="A personal note for this transaction — what you ordered, who you were with. Never affects matching."
        >
          <textarea
            className="acct-edit-in acct-note-area"
            value={f.memo}
            onChange={(e) => set('memo', e.target.value)}
            placeholder="e.g. 2 lattes + croissant"
            rows={2}
          />
        </Field>
        <Field
          label="Tags"
          hint="lowercase-with-hyphens • comma-separated (japan-trip, reimbursable…)"
        >
          <input
            className="acct-edit-in"
            value={f.tags}
            onChange={(e) => set('tags', liveTagFormat(e.target.value))}
            onBlur={() => set('tags', normalizeTags(f.tags).join(', '))}
            placeholder="japan-trip, reimbursable"
          />
        </Field>
        <Field label="Cleared">
          <ClearedCheck cleared={cleared} locked={txn.source === 'opening'} onChange={setCleared} />
        </Field>
      </div>
      <div className="acct-detail-foot">
        {splitError && <span className="acct-edit-spliterr">{splitError}</span>}
        {(saveError || actionError) && (
          <span className="acct-edit-spliterr" role="alert">
            {saveError || actionError}
          </span>
        )}
        <button className="acct-detail-edit" onClick={save} disabled={!!splitError}>
          Save changes
        </button>
        <button className="acct-detail-del ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </>
  )
}

function sourceEntryKey(entry: SourceEntry, index: number): string {
  return entry.kind === 'email'
    ? entry.messageId || String(index)
    : `${entry.provider}:${entry.unavailable ? index : entry.providerAccountId}`
}

function unlinkLabel(entry: SourceEntry, count: number): string {
  if (count === 1) return 'Unlink source'
  return entry.kind === 'email' ? 'Unlink email' : `Unlink ${entry.providerLabel}`
}

// ── View mode ─────────────────────────────────────────────────────────────────
interface ViewBodyProps {
  txn: Txn
  acctMap: Record<string, Account>
  catMap: Record<string, Category>
  payeeMap: Record<string, string>
  payeeName: string
  activity: TxnActivity | null
  actors?: Record<string, { name: string; type: string }>
  baseCurrency: string
  rates: FxRates
  source: SourceState
  onRetrySource: () => void
  /** Detach one selected provenance entry after the user confirms the destructive action. */
  onUnlinkSource: (entry: SourceEntry, key: string) => void
  unlinkingKey: string | null
  unlinkErrorKey: string | null
  /** The statement(s) this row is ON — empty for a hand entry, an agent booking or a
   *  bank-feed sync. Distinct from the batch-derived 'From' row above it, which is
   *  blind to anything a statement LINKED rather than CREATED. */
  statementLinks: StatementLink[]
  onEdit: () => void
  onDelete: (txn: Txn) => void
  onPurge?: (txn: Txn) => void
  onMarkReviewed: (txn: Txn) => void
  onToggleCleared?: (txn: Txn, cleared: boolean) => void
  /** The row itself has not arrived yet — see TxnDetail's `loading`. */
  pending?: boolean
  actionError?: string | null
}
function ViewBody({
  txn,
  acctMap,
  catMap,
  payeeMap,
  payeeName,
  activity,
  actors,
  baseCurrency,
  rates,
  source,
  onRetrySource,
  onUnlinkSource,
  unlinkingKey,
  unlinkErrorKey,
  statementLinks,
  onEdit,
  onDelete,
  onPurge,
  onMarkReviewed,
  onToggleCleared,
  pending = false,
  actionError,
}: ViewBodyProps) {
  const changeCtx: ChangeCtx = { acctMap, catMap, payeeMap }
  // Bound once: noUncheckedIndexedAccess makes [0] possibly-undefined, and the resolver
  // already sorts the covering statement first (the cycle this row genuinely belongs to).
  const primaryStatement = statementLinks[0]
  const meta = SOURCE_META[txn.source] ?? SOURCE_META.manual!
  const creatorType = SRC_TO_TYPE[txn.source] ?? 'you'
  const creator = { name: activity?.addedBy ?? meta.addedBy, type: creatorType }
  const [viewStatementId, setViewStatementId] = useState<string | null>(null)
  const [idCopied, setIdCopied] = useState(false)
  // Per-email full-body toggles, keyed by message id — each email expands independently.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())
  const [pendingUnlink, setPendingUnlink] = useState<{ entry: SourceEntry; key: string } | null>(
    null,
  )
  useEffect(() => {
    setExpandedIds(new Set())
    setIdCopied(false)
    setPendingUnlink(null)
  }, [txn.id])
  const copyTransactionId = async () => {
    try {
      await navigator.clipboard.writeText(txn.id)
      setIdCopied(true)
      window.setTimeout(() => setIdCopied(false), 1600)
    } catch {
      // Clipboard access can be denied by the browser; leave the visible ID in place.
    }
  }
  // Every provider's booked row carries external_refs keyed by its own provider name — but a
  // statement file's refs are row-identity fingerprints with no stored evidence, so only a ref
  // outside FILE_IMPORT_SOURCES opens the Source section (the statements panel's own gate,
  // hasAnyRef, still counts every ref).
  const hasSourceRef = hasProvenanceRef(txn.externalRefs)
  const unlinkableSources =
    source.status === 'ok'
      ? source.data
          .map((entry, index) => ({ entry, key: sourceEntryKey(entry, index) }))
          .filter(({ entry }) => !entry.unavailable)
      : []
  const events = activity?.activity ?? []
  const flagEvent = [...events].reverse().find((e) => e.action === 'flagged')
  let reviewReason: string
  if (flagEvent) {
    const faName = flagEvent.actorName || (actors ?? {})[flagEvent.actor]?.name || flagEvent.actor
    reviewReason = faName + ' flagged this' + (flagEvent.note ? ' — ' + flagEvent.note : '')
  } else if (creatorType === 'lucius') reviewReason = "Lucius suggested this — confirm it's right."
  else if (creatorType === 'agent')
    reviewReason = creator.name + " added this automatically — confirm it's right."
  else if (creatorType === 'import') reviewReason = "Imported automatically — confirm it's right."
  else reviewReason = 'Confirm the details are right.'

  const isIncome = txn.kind === 'income',
    isTransfer = txn.kind === 'transfer' || txn.kind === 'adjustment'
  const isStartingBalance = txn.source === 'opening'
  // investment sell = to-leg only (no fromAccountId); treated as inflow for sign/color.
  const isInvestmentSell = txn.kind === 'investment' && !txn.fromAccountId && !!txn.toAccountId
  const heroPos = isIncome || isInvestmentSell
  const amount = heroPos ? (txn.toAmount ?? 0) : (txn.fromAmount ?? txn.toAmount ?? 0)
  const currency = heroPos
    ? (txn.toCurrency ?? baseCurrency)
    : (txn.fromCurrency ?? txn.toCurrency ?? baseCurrency)
  const from = acctMap[txn.fromAccountId ?? ''],
    to = acctMap[txn.toAccountId ?? '']
  const acct =
    acctMap[heroPos ? (txn.toAccountId ?? '') : (txn.fromAccountId ?? txn.toAccountId ?? '')]
  const cat = txn.category ? (catMap[txn.category] ?? null) : null
  const isSplit = txn.category === 'split' && txn.splits && txn.splits.length > 0
  const amtStr = fCur(amount, currency, { cents: true }).replace('-', '')
  // A transfer has two legs, so one sign says nothing — but an adjustment has ONE, and its
  // direction is the whole point: a Starting Balance of -1,000.00 rendered identically to
  // +1,000.00 here while the register showed the sign (owner repro 2026-08-09). Same rule the
  // edit form uses for its increase/decrease toggle.
  const heroSign =
    txn.kind === 'transfer'
      ? ''
      : txn.kind === 'adjustment'
        ? txn.fromAccountId
          ? '−'
          : '+'
        : heroPos
          ? '+'
          : '−'
  const fx = fxInfo(amount, currency, txn.origAmount, txn.origCurrency, rates)
  // The bank/matching description + the personal memo, shown as-is — no payee de-dup
  // (matches the register column).
  const description = txn.description
  const memo = txn.memo
  const edited = activity && activity.updatedAt && activity.updatedAt !== activity.createdAt
  const chip = (a?: Account) =>
    a ? (
      <span className="acct-detail-chip">{a.name}</span>
    ) : (
      <span className="acct-payee-sub">—</span>
    )
  const splitGlyph = (
    <svg
      className="acct-detail-splitglyph"
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6 3v6a4 4 0 0 0 4 4h8" />
      <path d="M6 21v-6a4 4 0 0 1 4-4h8" />
      <path d="M15 6l3 3-3 3" />
      <path d="M15 12l3 3-3 3" />
    </svg>
  )

  // The provenance + activity load from /activity (async); null = still loading.
  const loading = activity === null
  // Show the LOCKED base value (the rate on the day / the value you pinned) — not a live
  // re-conversion at today's rate (ADR 2026-07-09). Only fall back to "today" for a
  // legacy row with no stored base or one written under a different base currency.
  // Computed ONCE here so the visible "≈ base" figure below and the Lucius
  // screen-context field (amount_base, via txnFields) can never disagree (MATH-42).
  const locked = txn.baseCurrency === baseCurrency && txn.baseAmount != null
  const shown = locked ? (txn.baseAmount ?? 0) : toBase(amount, currency, rates)
  // Mention + screen-context wiring: the open transaction must be @-mentionable AND
  // readable by Lucius's screen awareness (same EntityRow contract the register rows use).
  const signedAmt = (heroPos ? 1 : -1) * Math.abs(amount)
  const signedBase = (heroPos ? 1 : -1) * Math.abs(shown)
  const mLabel = payeeName || txn.description || '(no description)'
  const mSearch = [txn.description, txn.memo, payeeName, acct?.name, txn.category]
    .filter(Boolean)
    .join(' ')
  const mSub = txnMentionSublabel({
    kind: txn.kind,
    payeeName,
    accountName: acct?.name,
    date: txn.date,
    amount: Math.abs(amount),
    currency,
    signed: signedAmt,
  })
  const txnFields = () => ({
    date: txn.date,
    payee: payeeName || null,
    category: txn.category && txn.kind !== 'adjustment' ? txn.category : null,
    amount_base: fBase(signedBase, { cents: true }),
    account: txn.kind !== 'transfer' ? (acct?.name ?? null) : null,
    from_account: txn.kind === 'transfer' ? (from?.name ?? null) : null,
    to_account: txn.kind === 'transfer' ? (to?.name ?? null) : null,
    description: txn.description || null,
    status: txn.cleared ? 'Cleared' : 'Uncleared',
    kind: txn.kind,
  })

  if (pending && actionError) {
    return (
      <>
        <div className="acct-detail-hero">
          <span className="acct-edit-spliterr" role="alert">
            {actionError}
          </span>
        </div>
        <div className="acct-detail-foot">
          <button className="acct-detail-edit" disabled>
            Edit transaction
          </button>
          <button className="acct-detail-del" disabled>
            Delete
          </button>
        </div>
      </>
    )
  }
  if (pending) {
    return (
      <>
        <div className="acct-detail-hero acct-detail-hero--pending" aria-busy="true">
          <span className="acct-detail-skel acct-detail-skel--amount" />
          <span className="acct-detail-skel acct-detail-skel--payee" />
          <span className="acct-detail-skel acct-detail-skel--date" />
        </div>
        <div className="acct-detail-rows">
          {['Type', 'Account', 'Category', 'Source'].map((label) => (
            <DRow key={label} label={label}>
              <span className="acct-detail-skel" />
            </DRow>
          ))}
        </div>
        <div className="acct-detail-sec">Activity</div>
        <div className="acct-detail-timeline-skel">
          {[0, 1].map((i) => (
            <div key={i} className="acct-detail-skel-row">
              <span className="acct-detail-skel-dot" />
              <span className="acct-detail-skel" />
            </div>
          ))}
        </div>
        <div className="acct-detail-foot">
          <button className="acct-detail-edit" disabled>
            Edit transaction
          </button>
          <button className="acct-detail-del" disabled>
            Delete
          </button>
        </div>
      </>
    )
  }

  return (
    <>
      <EntityRow
        as="div"
        type="transaction"
        id={txn.id}
        label={mLabel}
        search={mSearch}
        sublabel={mSub}
        item={txn}
        toFields={txnFields}
        className="acct-detail-hero"
      >
        <div className={`acct-detail-amt ${heroPos ? 'pos' : 'neg'}`}>
          {heroSign}
          {amtStr}
          <span className="acct-detail-ccy">{currency}</span>
        </div>
        {currency !== baseCurrency &&
          (() => {
            const todayStr = fCur(toBase(amount, currency, rates), baseCurrency, { cents: true })
            return (
              <div
                className="acct-detail-base"
                title={
                  locked
                    ? `Locked at the transaction-date rate • ${todayStr} at today's rate`
                    : (fx.est ?? '') + ' • reporting rate, ≈ today'
                }
              >
                ≈ {fCur(shown, baseCurrency, { cents: true })}
                <span className="acct-detail-base-sub">
                  {' '}
                  in {baseCurrency}
                  {locked ? '' : ' • today'}
                </span>
              </div>
            )
          })()}
        {txn.kind === 'transfer' &&
          txn.toCurrency &&
          txn.toCurrency !== currency &&
          txn.toAmount != null && (
            <div
              className="acct-detail-base"
              title="Amount that arrived, in the destination currency"
            >
              → {fCur(txn.toAmount, txn.toCurrency, { cents: true })}
              <span className="acct-detail-base-sub"> received in {txn.toCurrency}</span>
            </div>
          )}
        <div className="acct-detail-payee">
          {isTransfer ? (
            txn.kind === 'adjustment' ? (
              'Balance adjustment'
            ) : (
              <span>
                {from ? from.name : '—'}
                <span className="acct-detail-arrow">→</span>
                {to ? to.name : '—'}
              </span>
            )
          ) : (
            payeeName || txn.description || '—'
          )}
        </div>
        <div className="acct-detail-date">{fmtDay(txn.date)}</div>
      </EntityRow>
      {txn.needsReview && (
        <div className="acct-detail-review">
          <div className="acct-detail-review-t">
            <b>Needs your review</b>
            <span>{reviewReason}</span>
          </div>
          <div className="acct-detail-review-actions">
            <button className="acct-detail-review-btn reject" onClick={() => onDelete(txn)}>
              Reject
            </button>
            <button className="acct-detail-review-btn approve" onClick={() => onMarkReviewed(txn)}>
              Approve
            </button>
          </div>
        </div>
      )}
      <div className="acct-detail-rows">
        <DRow label="Type">
          <span className={`acct-detail-kind ${txn.kind}`}>{KIND_LABEL[txn.kind] ?? txn.kind}</span>
        </DRow>
        <DRow label="Account">{chip(acct)}</DRow>
        <DRow label="Cleared">
          <ClearedCheck
            cleared={txn.cleared}
            locked={isStartingBalance || !onToggleCleared || pending}
            onChange={(next) => onToggleCleared?.(txn, next)}
          />
        </DRow>
        {txn.kind !== 'adjustment' && (
          <DRow label="Category">
            {isSplit ? (
              <span className="acct-detail-chip acct-detail-splitcat">
                {splitGlyph}Split
                <span className="acct-detail-splitdots">
                  {txn.splits.map((s, i) => (
                    <i
                      key={i}
                      style={{ background: catMap[s.category]?.color }}
                      title={catMap[s.category]?.label ?? 'Uncategorized'}
                    />
                  ))}
                </span>
              </span>
            ) : cat ? (
              <span className="acct-detail-chip">
                <i style={{ background: cat.color }} />
                {cat.label ?? txn.category}
              </span>
            ) : txn.kind === 'investment' || txn.kind === 'transfer' ? (
              <span className="acct-payee-sub">None</span>
            ) : (
              <span
                className="acct-unsorted"
                title="No category — this won't count toward any budget envelope"
              >
                Uncategorized
              </span>
            )}
          </DRow>
        )}
        <DRow label="Description">
          {description ? (
            <span style={{ whiteSpace: 'pre-wrap' }}>{description}</span>
          ) : (
            <span className="acct-payee-sub">—</span>
          )}
        </DRow>
        <DRow label="Memo">
          {memo ? (
            <span style={{ whiteSpace: 'pre-wrap' }}>{memo}</span>
          ) : (
            <span className="acct-payee-sub">—</span>
          )}
        </DRow>
        {txn.tags.length > 0 && (
          <DRow label="Tags">
            <span className="acct-detail-tags">
              {txn.tags.map((tg) => (
                <span key={tg} className="acct-detail-tag">
                  {tg}
                </span>
              ))}
            </span>
          </DRow>
        )}
        {txn.origCurrency && txn.origCurrency !== currency && (
          <DRow label="Original charge">
            {fCur(txn.origAmount ?? 0, txn.origCurrency)}
            {fx.fact && (
              <span className="acct-detail-fxfact" title="Your card's actual settled rate">
                {' '}
                • {fx.fact}
              </span>
            )}
          </DRow>
        )}
        {txn.kind === 'income' && isRefundTxn(txn.kind, txn.category, catMap) && (
          <DRow label="Refund">Returned to {cat?.label ?? 'budget'} envelope</DRow>
        )}
      </div>
      {isSplit && (
        <>
          <div className="acct-detail-sec">Split into {txn.splits.length}</div>
          <div className="acct-detail-splits">
            {txn.splits.map((s, i) => {
              const sc = catMap[s.category]
              return (
                <div className="acct-detail-split" key={i}>
                  <span className="acct-detail-chip">
                    <i style={{ background: sc?.color }} />
                    {sc?.label ?? 'Uncategorized'}
                  </span>
                  {s.note && <span className="acct-detail-split-note">{s.note}</span>}
                  <span className="acct-detail-split-amt">
                    {fCur(s.amount, currency, { cents: true })}
                  </span>
                </div>
              )
            })}
          </div>
        </>
      )}
      {/* key={txn.id}: the drawer never unmounts on a row switch (this component's own
          docstring above), so without a key ReceiptsSection's local UI state — armed row,
          open viewer, in-flight attach — would leak from the previous transaction onto the
          next one instead of resetting. The hook's own list re-fetches on txnId regardless;
          keying just makes the WHOLE section (local state included) start fresh per row. */}
      <ReceiptsSection key={txn.id} txnId={txn.id} />
      <div className="acct-detail-sec">Provenance</div>
      <div className="acct-detail-rows">
        <DRow label="ID">
          <button
            type="button"
            className={`acct-detail-id${idCopied ? ' copied' : ''}`}
            aria-label={`Copy transaction ID ${txn.id}`}
            title="Copy transaction ID"
            onClick={copyTransactionId}
          >
            {idCopied ? 'Copied' : txn.id}
          </button>
        </DRow>
        <DRow label="Source">
          <span className="acct-detail-src">
            <DSourceIcon source={txn.source} />
            {meta.label}
          </span>
        </DRow>
        <DRow label="Added by">
          {loading ? (
            <span className="acct-detail-skel" />
          ) : (
            <span className="acct-detail-src">
              <span>
                {creator.name}
                {activity?.via && <span className="acct-detail-via"> • via {activity.via}</span>}
              </span>
              <ActorBadge type={creator.type} name={creator.name} />
            </span>
          )}
        </DRow>
        {activity?.import?.filename && (
          <DRow label="From">
            <span className="acct-detail-src">
              <button
                type="button"
                className="acct-detail-stmt"
                title="View the statement"
                onClick={() => setViewStatementId(activity.import!.statementId!)}
              >
                {activity.import.filename}
              </button>
              <span className="acct-payee-sub">
                {' '}
                • batch {activity.import.batchId.slice(0, 12)}…
              </span>
            </span>
          </DRow>
        )}
        {primaryStatement && (
          <DRow
            label={
              statementLinks.length > 1
                ? `On statements (${statementLinks.length})`
                : 'On statement'
            }
          >
            <span className="acct-detail-src">
              <button
                type="button"
                className="acct-detail-stmt"
                title="View the statement"
                onClick={() => setViewStatementId(primaryStatement.statementId)}
              >
                {primaryStatement.label}
              </button>
              {primaryStatement.reference && (
                <span className="acct-payee-sub"> • ref {primaryStatement.reference}</span>
              )}
            </span>
          </DRow>
        )}
        <DRow label="Created">{fmtDateTime(txn.createdAt)}</DRow>
        <DRow label="Last edited">
          {loading ? (
            <span className="acct-detail-skel" />
          ) : edited ? (
            fmtDateTime(activity?.updatedAt)
          ) : (
            <span className="acct-payee-sub">Never edited</span>
          )}
        </DRow>
      </div>
      {hasSourceRef && (
        <>
          <div className="acct-detail-sec">
            {source.status === 'ok' && source.data.length > 1
              ? `Source (${source.data.length})`
              : 'Source'}
          </div>
          {source.status === 'loading' && (
            <div className="acct-detail-rows">
              <DRow label="Provider">
                <span className="acct-detail-skel" />
              </DRow>
              <DRow label="Fetched">
                <span className="acct-detail-skel" />
              </DRow>
            </div>
          )}
          {source.status === 'ok' &&
            source.data.map((entry, i) => {
              const key = sourceEntryKey(entry, i)
              const open = expandedIds.has(key)
              const toggle = () =>
                setExpandedIds((prev) => {
                  const next = new Set(prev)
                  if (next.has(key)) next.delete(key)
                  else next.add(key)
                  return next
                })
              // kind: 'email' — BYTE-IDENTICAL to the pre-generalization "Source email"
              // section (CDS-UI-17's regression gate); only `em` → `entry` changed.
              if (entry.kind === 'email') {
                return (
                  <Fragment key={key}>
                    {i > 0 && <div className="acct-detail-email-div" />}
                    <div className="acct-detail-source-block">
                      {entry.unavailable ? (
                        <div className="acct-detail-email-empty">
                          Snapshot unavailable — this transaction was booked from email before
                          snapshots were kept (or the original message is no longer stored).
                        </div>
                      ) : (
                        <>
                          <div className="acct-detail-rows">
                            <DRow label="Subject">
                              {entry.subject || (
                                <span className="acct-payee-sub">(no subject)</span>
                              )}
                            </DRow>
                            <DRow label="From">{entry.from}</DRow>
                            <DRow label="Received">{fmtDateTime(entry.receivedAt)}</DRow>
                          </div>
                          <button
                            type="button"
                            className="acct-detail-email-toggle"
                            aria-expanded={open}
                            aria-controls={`src-email-body-${key}`}
                            onClick={toggle}
                          >
                            {open ? 'Hide full email ▴' : 'Show full email ▾'}
                          </button>
                          {open && (
                            <pre id={`src-email-body-${key}`} className="acct-detail-email-body">
                              {entry.bodyText}
                            </pre>
                          )}
                          <div className="acct-detail-source-actions">
                            <button
                              className="acct-detail-source-unlink"
                              disabled={unlinkingKey === key}
                              onClick={() => setPendingUnlink({ entry, key })}
                            >
                              {unlinkingKey === key
                                ? 'Unlinking…'
                                : unlinkLabel(entry, unlinkableSources.length)}
                            </button>
                            {unlinkErrorKey === key && (
                              <span className="acct-detail-unlink-error">
                                Couldn't unlink this source.
                              </span>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  </Fragment>
                )
              }
              // kind: 'connector' (new) — the linked app account's name, resolved from the
              // txn's own leg, NEVER the raw provider_account_id (UI spec §4.4).
              const acctName =
                acctMap[txn.fromAccountId ?? '']?.name ??
                acctMap[txn.toAccountId ?? '']?.name ??
                `${entry.providerLabel} account`
              return (
                <Fragment key={key}>
                  {i > 0 && <div className="acct-detail-email-div" />}
                  <div className="acct-detail-source-block">
                    {entry.unavailable ? (
                      <div className="acct-detail-email-empty">
                        Snapshot unavailable — this transaction was booked before evidence was kept
                        (or the original record is no longer stored).
                      </div>
                    ) : (
                      <>
                        <div className="acct-detail-rows">
                          <DRow label="Provider">{entry.providerLabel}</DRow>
                          <DRow label="Provider account">{acctName}</DRow>
                          <DRow label="Fetched">{fmtDateTime(entry.fetchedAt)}</DRow>
                        </div>
                        <button
                          type="button"
                          className="acct-detail-raw-toggle"
                          aria-expanded={open}
                          aria-controls={`src-raw-${key}`}
                          onClick={toggle}
                        >
                          {open ? 'Hide raw fields ▴' : 'Show raw fields ▾'}
                        </button>
                        {open && (
                          <div id={`src-raw-${key}`} className="acct-detail-raw-body">
                            {Object.entries(entry.raw).map(([k, v]) => (
                              <DRow key={k} label={titleCaseRawKey(k)}>
                                {v !== null && typeof v === 'object'
                                  ? JSON.stringify(v)
                                  : String(v)}
                              </DRow>
                            ))}
                          </div>
                        )}
                        <div className="acct-detail-source-actions">
                          <button
                            className="acct-detail-source-unlink"
                            disabled={unlinkingKey === key}
                            onClick={() => setPendingUnlink({ entry, key })}
                          >
                            {unlinkingKey === key
                              ? 'Unlinking…'
                              : unlinkLabel(entry, unlinkableSources.length)}
                          </button>
                          {unlinkErrorKey === key && (
                            <span className="acct-detail-unlink-error">
                              Couldn't unlink this source.
                            </span>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </Fragment>
              )
            })}
          {source.status === 'unavailable' &&
            (txn.externalRefs.every((r) => r.source === 'email') ? (
              <div className="acct-detail-email-empty">
                Snapshot unavailable — this transaction was booked from email before snapshots were
                kept (or the original message is no longer stored).
              </div>
            ) : (
              <div className="acct-detail-email-empty">
                Snapshot unavailable — this transaction was booked before evidence was kept (or the
                original record is no longer stored).
              </div>
            ))}
          {source.status === 'error' && (
            <div className="acct-detail-email-empty">
              Couldn't load the source details.{' '}
              <button type="button" className="acct-detail-stmt" onClick={onRetrySource}>
                Retry
              </button>
            </div>
          )}
        </>
      )}
      {loading && (
        <>
          <div className="acct-detail-sec">Activity</div>
          <div className="acct-detail-timeline-skel">
            {[0, 1].map((i) => (
              <div key={i} className="acct-detail-skel-row">
                <span className="acct-detail-skel-dot" />
                <span className="acct-detail-skel" />
              </div>
            ))}
          </div>
        </>
      )}
      {!loading && events.length > 0 && (
        <>
          <div className="acct-detail-sec">Activity</div>
          <ol className="acct-detail-timeline">
            {events.map((e, i) => {
              // Server resolves actor TYPE (icon/badge) + display name; `actors` is a legacy fallback.
              const actorType = e.actor || (actors ?? {})[e.actor]?.type || 'you'
              const actorName = e.actorName || (actors ?? {})[e.actor]?.name || e.actor
              return (
                <li className="acct-detail-tl" key={i}>
                  <span className={`acct-detail-tl-ic ${actorType}`}>
                    <ActorIcon type={actorType} />
                  </span>
                  <div className="acct-detail-tl-body">
                    <div className="acct-detail-tl-head">
                      <span className="acct-detail-tl-verb">
                        {ACTION_VERB[e.action] ?? e.action}
                      </span>
                      <span className="acct-detail-tl-actor">
                        {actorName}
                        {e.via ? ' • via ' + e.via : ''}
                      </span>
                      <ActorBadge type={actorType} name={actorName} />
                    </div>
                    {e.import?.filename ? (
                      <div className="acct-detail-tl-note">
                        from{' '}
                        <button
                          type="button"
                          className="acct-detail-stmt"
                          title="View the statement"
                          onClick={() => setViewStatementId(e.import!.statementId!)}
                        >
                          {e.import.filename}
                        </button>
                      </div>
                    ) : e.import?.description ? (
                      <div className="acct-detail-tl-note">{e.import.description}</div>
                    ) : e.note ? (
                      <div className="acct-detail-tl-note">{e.note}</div>
                    ) : null}
                    {(() => {
                      const chs = (e.changes ?? []).filter((c) => !CHANGE_FIELD_HIDDEN.has(c.field))
                      return (
                        chs.length > 0 && (
                          <ul className="acct-detail-tl-changes">
                            {chs.map((c, j) => (
                              <li key={j}>
                                <span className="f">{CHANGE_FIELD_LABEL[c.field] ?? c.field}</span>{' '}
                                <span className="from">
                                  {fmtChangeVal(c.field, c.from, changeCtx)}
                                </span>
                                {' → '}
                                <span className="to">{fmtChangeVal(c.field, c.to, changeCtx)}</span>
                              </li>
                            ))}
                          </ul>
                        )
                      )
                    })()}
                    <div className="acct-detail-tl-time">{fmtDateTime(e.at)}</div>
                  </div>
                </li>
              )
            })}
          </ol>
        </>
      )}
      <div className="acct-detail-foot">
        {actionError && (
          <span className="acct-edit-spliterr" role="alert">
            {actionError}
          </span>
        )}
        <button className="acct-detail-edit" onClick={onEdit}>
          Edit transaction
        </button>
        <button
          className="acct-detail-del"
          disabled={isStartingBalance}
          onClick={() => onDelete(txn)}
        >
          Delete
        </button>
        {onPurge && !isStartingBalance && (
          <button className="acct-detail-del" onClick={() => onPurge(txn)}>
            Delete permanently
          </button>
        )}
      </div>
      {viewStatementId && (
        <StatementViewer stmt={{ id: viewStatementId }} onClose={() => setViewStatementId(null)} />
      )}
      <ConfirmModal
        open={!!pendingUnlink}
        title="Unlink source?"
        body="This removes the source link from this transaction. The transaction itself will not be deleted."
        confirmLabel="Unlink source"
        danger
        onClose={() => setPendingUnlink(null)}
        onConfirm={() => {
          if (!pendingUnlink) return
          onUnlinkSource(pendingUnlink.entry, pendingUnlink.key)
          setPendingUnlink(null)
        }}
      />
    </>
  )
}

/** A dialog opened ON the drawer takes focus as it opens — the host's delete confirm renders
 *  beside the drawer, not inside it — and Radix's non-modal default reads that as leaving: the
 *  drawer closed under the confirm, so Cancel had nothing to return to (owner report
 *  2026-09-26). A dialog portaled AFTER the drawer is stacked on it, the same opened-later rule
 *  its tier follows (ADR 2026-09-03); the page, Lucius and a modal it was opened over are not. */
function isStackedOnDrawer(drawer: Element | null, target: EventTarget | null): boolean {
  if (!drawer || !(target instanceof Element)) return false
  const dialog = target.closest('[role="dialog"], [role="alertdialog"]')
  return !!dialog && !!(drawer.compareDocumentPosition(dialog) & Node.DOCUMENT_POSITION_FOLLOWING)
}

// ── Main component ────────────────────────────────────────────────────────────
// What "cleared" means — asked often enough that the checkbox carries its own explanation.
const CLEARED_EXPLAINER =
  'Tick this once your bank shows the transaction as posted, not pending. It helps you check that your balance matches the bank’s.'

// Cleared checkbox. The Starting Balance is always cleared (the ledger refuses unclearing it).
function ClearedCheck({
  cleared,
  locked,
  onChange,
}: {
  cleared: boolean
  locked: boolean
  onChange: (cleared: boolean) => void
}) {
  return (
    <span className="acct-detail-cleared">
      <Checkbox
        checked={cleared}
        disabled={locked}
        label={<span className="sr-only">Cleared</span>}
        onChange={(e) => onChange(e.target.checked)}
      />
      {/* The bank's own words for the two states. */}
      <span className={cleared ? undefined : 'acct-detail-pending'}>
        {cleared ? 'Posted' : 'Pending'}
      </span>
      <InfoPopover text={CLEARED_EXPLAINER} width={210} />
    </span>
  )
}

// The + / − beside an amount, drawn as an icon: a text glyph is centred by its line box, and
// Plus Jakarta Sans's tall ascender sat the math axis ~3px below the middle of its box. The
// text stays for screen readers.
function SignGlyph({ positive }: { positive: boolean }) {
  const Icon = positive ? Plus : Minus
  return (
    <>
      <Icon size={16} strokeWidth={2.75} aria-hidden />
      <span className="sr-only">{positive ? '+' : '−'}</span>
    </>
  )
}

export default function TxnDetail({
  txn,
  accounts,
  categories,
  payeeOptions,
  payeeById,
  actors,
  baseCurrency,
  rates,
  onClose,
  initialMode,
  onSave,
  onDelete,
  onPurge,
  onMarkReviewed,
  onToggleCleared,
  onManagePayees,
  onCreateCategory,
  onLoadActivity,
  onLoadSourceEntries,
  onDetachSourceRef,
  onLoadStatementLinks,
  liftAboveModal,
  loading = false,
  actionError = null,
}: TxnDetailProps) {
  // Opened by id: the phone sheet sizes to its content, and a skeleton is shorter than the
  // filled drawer, so the sheet grew — its top edge jumping up — when the row landed. A
  // drawer that opened pending keeps the sheet's full height for its whole life instead
  // (a filled drawer reaches that ceiling on a phone anyway). Read once: the flag must not
  // flip off when the row arrives.
  const [openedPending] = useState(loading)
  // Category map built from server data — labels/colors for pills and diffs.
  const catMap = useMemo(
    () => Object.fromEntries(categories.map((c) => [c.id, c])) as Record<string, Category>,
    [categories],
  )
  // payee id → display name, for resolving the activity diff (falls back to the raw id if deleted).
  // id → name (the picker is name-keyed; resolving a stored payee_id needs the id map).
  const payeeMap = useMemo(() => payeeById ?? {}, [payeeById])
  const [mode, setMode] = useState<'view' | 'edit'>(initialMode ?? 'view')
  // Opened straight into edit (the review card's Edit): edit IS the drawer here, so leaving it
  // — save, cancel, ✕ or a drag — closes rather than dropping the reviewer on a detail view
  // they never asked for and have to dismiss a second time.
  const openedForEdit = initialMode === 'edit'
  // Below the phone breakpoint the drawer renders as a BOTTOM SHEET, not a right-docked
  // drawer (task 176): a 384px-wide panel on a 390px screen is a hairline off the viewport
  // edge with no reachable close target, and every touch target sits in the far corners.
  // Same width-only gate as the rest of the mobile shell (never pointer-gated).
  const isMobile = useIsMobile()
  const [width, setWidth] = useState<number>(() => {
    const v = parseInt(localStorage.getItem('acctDetailW') ?? '', 10)
    return v >= 340 && v <= 760 ? v : 384
  })
  const [activity, setActivity] = useState<TxnActivity | null>(null)
  const [source, setSource] = useState<SourceState>({ status: 'idle' })
  // The drawer never unmounts on a row switch (AccountsTab just swaps the `txn` prop in
  // place) — tracks the CURRENT txn id so an in-flight fetch from a txn the user has since
  // switched AWAY from can detect it's stale before touching state. Updated synchronously in
  // the same commit as every txn switch (the effect below), which always runs strictly
  // BEFORE a network response's .then/.catch can fire.
  const currentTxnIdRef = useRef<string | undefined>(txn?.id)
  // Set for one tick after a resize-drag ends — see startResize/handleBackdropClick below.
  const justResizedRef = useRef(false)
  const sheetRef = useRef<HTMLElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  // Drag-to-dismiss mirrors the ✕ exactly: from Edit it returns to View rather than
  // discarding the edit, same as `dismiss` below (which the backdrop click also uses).
  const dragHandlers = useSheetDragToDismiss({
    sheetRef,
    overlayRef,
    onDismiss: () => (mode === 'edit' && !openedForEdit ? setMode('view') : onClose()),
    enabled: isMobile,
  })

  useEffect(() => {
    localStorage.setItem('acctDetailW', String(width))
  }, [width])
  useEffect(() => {
    setMode(initialMode ?? 'view')
  }, [txn?.id, initialMode])
  // Clear the timeline only when SWITCHING transactions (id change) — avoids a flash on an edit.
  useEffect(() => {
    setActivity(null)
  }, [txn?.id])
  useEffect(() => {
    currentTxnIdRef.current = txn?.id
  }, [txn?.id])
  // (Re)load the activity whenever the txn changes — including a save, which replaces the txn
  // with a fresh object (same id) carrying the new fields + a new "Edited" event to show.
  useEffect(() => {
    if (!txn) return
    let cancelled = false
    onLoadActivity(txn.id)
      .then((a) => {
        if (!cancelled) setActivity(a)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [txn]) // eslint-disable-line react-hooks/exhaustive-deps
  // Source-entries fetch (email — EM1 — or connector): eager on drawer open, keyed on txn id
  // (not the whole txn object — an edit save never changes external_refs, so there's no need
  // to re-fetch on every save like activity does). Gated on carrying ANY external ref, not an
  // `email` one specifically — every provider's booked row carries its own-namespaced
  // external_refs, so a txn with no refs at all resets straight to idle with no request ever
  // made. Stale-response guard (also covers a manual Retry click that outlives a subsequent
  // row switch, not just the automatic effect-driven fetch): each call captures its OWN txn id
  // and re-checks it against currentTxnIdRef right before every setState, so a response for a
  // row the user has since navigated away from can never clobber the newer row's displayed data.
  const [statementLinks, setStatementLinks] = useState<StatementLink[]>([])
  const hasAnyRef = !!txn?.externalRefs?.length
  const hasSourceProvenance = hasProvenanceRef(txn?.externalRefs)
  const loadSourceEntries = useCallback(() => {
    if (!txn || !hasSourceProvenance) {
      setSource({ status: 'idle' })
      return
    }
    const id = txn.id
    setSource({ status: 'loading' })
    onLoadSourceEntries(id)
      .then((data) => {
        if (currentTxnIdRef.current !== id) return
        setSource(data && data.length ? { status: 'ok', data } : { status: 'unavailable' })
      })
      .catch(() => {
        if (currentTxnIdRef.current !== id) return
        setSource({ status: 'error' })
      })
  }, [txn?.id, hasSourceProvenance, onLoadSourceEntries])
  useEffect(() => {
    loadSourceEntries()
  }, [txn?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // "Unlink source" (CDS-6): per-entry busy/error state, keyed the same way the toggle/
  // disclosure state above is — one entry's failure never blocks or mislabels another's
  // button. On success the entry is filtered out of `source.data` locally (no refetch of the
  // whole list); the caller's own state (the txn's `externalRefs`) is refreshed by whatever
  // it does inside `onDetachSourceRef`, which flips `hasAnyRef`/`hasSourceRef` false — and the
  // whole section away — once the LAST entry is gone (the existing "no provenance" state).
  const [unlinkingKey, setUnlinkingKey] = useState<string | null>(null)
  const [unlinkErrorKey, setUnlinkErrorKey] = useState<string | null>(null)
  const handleUnlinkSource = useCallback(
    (entry: SourceEntry, key: string) => {
      if (!txn || entry.unavailable) return
      const id = txn.id
      setUnlinkingKey(key)
      setUnlinkErrorKey(null)
      onDetachSourceRef(txn, entry.source, entry.ref)
        .then(() => {
          if (currentTxnIdRef.current !== id) return
          setSource((prev) =>
            prev.status === 'ok'
              ? {
                  status: 'ok',
                  data: prev.data.filter(
                    (e) => !(e.source === entry.source && e.ref === entry.ref),
                  ),
                }
              : prev,
          )
        })
        .catch(() => {
          if (currentTxnIdRef.current !== id) return
          setUnlinkErrorKey(key)
        })
        .finally(() => {
          if (currentTxnIdRef.current !== id) return
          setUnlinkingKey(null)
        })
    },
    [txn, onDetachSourceRef],
  )
  // Which statement(s) this row is ON. Gated on the same "any ref" predicate as above: a row
  // Lucius logged from an email and a statement later confirmed is on that statement, and its
  // refs list leads with `email` — gating on the namespace would hide exactly the merge-linked
  // rows this panel exists to show.
  useEffect(() => {
    if (!txn || !hasAnyRef || !onLoadStatementLinks) {
      setStatementLinks([])
      return
    }
    const id = txn.id
    onLoadStatementLinks(id)
      .then((data) => {
        if (currentTxnIdRef.current === id) setStatementLinks(data)
      })
      .catch(() => {
        if (currentTxnIdRef.current === id) setStatementLinks([])
      })
  }, [txn?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // Publish the open drawer's width so the floating Lucius dock can shift left to clear it
  // (Lucius must stay reachable while the drawer is open). Tracks resize; cleared on close.
  // Skipped on phones: the sheet is full-width, and `.lx-dock` is display:none there anyway
  // (Lucius is a tab-bar item), so a published width would shift nothing and only mean
  // "there is a right-docked drawer", which is false.
  useEffect(() => {
    if (isMobile) return
    document.documentElement.style.setProperty('--detail-drawer-w', width + 'px')
    return () => {
      document.documentElement.style.removeProperty('--detail-drawer-w')
    }
  }, [width, isMobile])
  // The page behind a bottom sheet must not scroll (MB2.5, the More sheet's contract) —
  // on a phone the sheet covers the viewport and a background scroll under it reads as the
  // sheet itself sliding. Captures the previous value, so nesting under an already-locked
  // modal (the import review opens this drawer over itself) restores correctly.
  useEffect(() => {
    if (!isMobile || !txn) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [isMobile, !!txn]) // eslint-disable-line react-hooks/exhaustive-deps
  const drawerResize = useRef<{ startX: number; startWidth: number; handle: HTMLElement } | null>(
    null,
  )
  const onResizePointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      event.stopPropagation()
      const handle = event.target as HTMLElement
      handle.classList.add('drag')
      drawerResize.current = { startX: event.clientX, startWidth: width, handle }
    },
    onMove: (event) => {
      const drag = drawerResize.current
      if (!drag) return
      setWidth(Math.min(760, Math.max(340, drag.startWidth + (drag.startX - event.clientX))))
    },
    onEnd: () => {
      const drag = drawerResize.current
      if (!drag) return
      drag.handle.classList.remove('drag')
      drawerResize.current = null
      // A drag that ends with the pointer over the backdrop (dragging the handle left,
      // widening the drawer, commonly lands here) makes the browser synthesize a `click`
      // on the nearest common ancestor of the mousedown/mouseup targets — the backdrop
      // itself — even though mousedown's stopPropagation() above ran. That backdrop click
      // would otherwise be read as "click outside" and close the drawer right after the
      // release. Swallow exactly the one click this drag produces; a genuine later
      // outside-click still closes normally.
      justResizedRef.current = true
      setTimeout(() => {
        justResizedRef.current = false
      }, 0)
    },
  })

  if (!txn) return null
  const acctMap = Object.fromEntries(accounts.map((a) => [a.id, a]))
  const payeeName = txn.payeeId ? (payeeById?.[txn.payeeId] ?? '') : ''
  const editing = mode === 'edit'
  const handleBackdropClick = () => {
    if (justResizedRef.current) {
      justResizedRef.current = false
      return
    }
    onClose()
  }
  // Drag the sheet's grab handle down to dismiss (the `.mb-sheet` gesture + threshold).
  // Mirrors the ✕ exactly: from Edit it returns to View rather than discarding the edit,
  // the same protection the backdrop click already has.
  const dismiss = editing && !openedForEdit ? () => setMode('view') : onClose
  return (
    <Dialog.Root
      open
      modal={false}
      onOpenChange={(next) => {
        if (!next) dismiss()
      }}
    >
      <Dialog.Portal>
        <Dialog.Content
          asChild
          aria-describedby={undefined}
          onInteractOutside={(event) => {
            if (isStackedOnDrawer(overlayRef.current, event.target)) event.preventDefault()
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Escape') return
            event.preventDefault()
            event.stopPropagation()
            dismiss()
          }}
        >
          <div
            ref={overlayRef}
            className={`acct-detail-wrap${isMobile ? ' sheet' : ''}${liftAboveModal ? ' above-modal' : ''}`}
            onClick={editing ? undefined : handleBackdropClick}
          >
            <Dialog.Title className="ui-visually-hidden">Transaction details</Dialog.Title>
            {!isMobile && (
              <div
                className="acct-detail-resize"
                style={{ right: `calc(var(--chat-width, 0px) + ${width}px)` }}
                onPointerDown={onResizePointerDown}
                title="Drag to resize"
              />
            )}
            <aside
              ref={sheetRef}
              className={`acct-detail${editing ? ' editing' : ''}${isMobile ? ' sheet' : ''}${openedPending ? ' acct-detail--full' : ''}`}
              style={isMobile ? undefined : { width: width + 'px' }}
              onClick={(e) => e.stopPropagation()}
              {...dragHandlers}
            >
              {/* The sheet root is its own scroll container, so its sticky grab bar and title row
            opt in as drag surfaces — otherwise a handle drag while scrolled would read as a
            scroll (see useSheetDragToDismiss's top-of-file note, point 4). */}
              {isMobile && (
                <div className="acct-detail-grab" {...DRAG_SURFACE_PROPS}>
                  <span className="acct-detail-grabbar" />
                </div>
              )}
              <div className="acct-detail-top" {...DRAG_SURFACE_PROPS}>
                <span className="acct-detail-kicker">
                  {editing ? 'Edit transaction' : 'Transaction'}
                </span>
                <button className="acct-detail-x" onClick={dismiss} aria-label="Close">
                  <X size={18} strokeWidth={1.6} aria-hidden="true" />
                </button>
              </div>
              {editing ? (
                <EditBody
                  txn={txn}
                  accounts={accounts}
                  acctMap={acctMap}
                  categories={categories}
                  catMap={catMap}
                  payeeOptions={payeeOptions}
                  payeeName={payeeName}
                  baseCurrency={baseCurrency}
                  rates={rates}
                  onManagePayees={onManagePayees}
                  onCreateCategory={onCreateCategory}
                  // Wait for the save request to resolve before flipping back to View — flipping
                  // eagerly made a save look like it did nothing (or silently discarded a failure)
                  // while the request was still in flight. On failure, stay in Edit (the caller
                  // already surfaced an error toast) so the user's in-progress edit isn't lost.
                  onSave={(id, patch) =>
                    Promise.resolve(onSave(id, patch)).then(() =>
                      openedForEdit ? onClose() : setMode('view'),
                    )
                  }
                  onCancel={() => (openedForEdit ? onClose() : setMode('view'))}
                  actionError={actionError}
                />
              ) : (
                <ViewBody
                  pending={loading}
                  txn={txn}
                  acctMap={acctMap}
                  catMap={catMap}
                  payeeMap={payeeMap}
                  payeeName={payeeName}
                  activity={activity}
                  actors={actors}
                  baseCurrency={baseCurrency}
                  rates={rates}
                  source={source}
                  onRetrySource={loadSourceEntries}
                  onUnlinkSource={handleUnlinkSource}
                  unlinkingKey={unlinkingKey}
                  unlinkErrorKey={unlinkErrorKey}
                  statementLinks={statementLinks}
                  onEdit={() => setMode('edit')}
                  onDelete={onDelete}
                  onPurge={onPurge}
                  onMarkReviewed={onMarkReviewed}
                  onToggleCleared={onToggleCleared}
                  actionError={actionError}
                />
              )}
            </aside>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
