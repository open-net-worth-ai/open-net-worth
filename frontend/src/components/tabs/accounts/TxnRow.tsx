/**
 * TxnRow.tsx — Single register row. Factored out of Register.tsx for line-count.
 * Handles inline cell editing (date, payee, category, memo, amount, account).
 *
 * Sign-aware amount edit: positive = income (money in), negative = expense (money out).
 * Presentational — all mutations go via onUpdate callback.
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { EntityRow } from '../../../lucius/EntityRow'
import type { Txn, Account, Category, FxRates } from './types'
import { fCur, fBase } from './format'
import { fDate } from '../../../utils/format'
import { catOptionsFor } from './categories'
import { buildTxnView } from './txnView'
import {
  InlineField,
  InlinePayee,
  AddAccountSelect,
  AddDateField,
  AddCategoryField,
  type PayeeOption,
} from './pickers'
import { SourceIcon, IconCheck } from './pickers'
import { ChevronUp, ChevronDown, Check, Circle } from '../../../ui/icons'

export interface RowPatch {
  date?: string
  description?: string
  memo?: string
  category?: string | null
  payee?: string // payee display NAME — orchestrator resolves to payeeId
  cleared?: boolean
  kind?: 'expense' | 'income' | 'transfer' | 'adjustment' | 'investment'
  fromAccountId?: string | null
  toAccountId?: string | null
  fromAmount?: number | null
  fromCurrency?: string | null
  toAmount?: number | null
  toCurrency?: string | null
}

export function categoryPill(
  catKey: string | null,
  catMap: Record<string, Category> = {},
): React.ReactNode {
  const c = catKey ? catMap[catKey] : undefined
  const color = c?.color || '#7c8595'
  // A category id the live registry hasn't (yet, or ever) resolved must NEVER paint its raw
  // opaque id (`cat_xxx`) to the user — fall back to a friendly label instead.
  return (
    <span className="acct-pill" style={{ background: color + '26', color }}>
      {c?.label || 'Uncategorized'}
    </span>
  )
}

function accountPatch(t: Txn, id: string): RowPatch {
  // income = to-leg; investment sell (to-leg only) = to-leg; everything else = from-leg
  if (t.kind === 'income' || (t.kind === 'investment' && !t.fromAccountId && !!t.toAccountId))
    return { toAccountId: id }
  return { fromAccountId: id }
}

export interface TxnRowProps {
  t: Txn
  accounts: Account[]
  showAccount: boolean
  selectedId: string | null
  selected: boolean
  onToggle: (id: string, shift?: boolean) => void
  onEdit?: (t: Txn) => void
  onUpdate: (id: string, patch: RowPatch) => void
  onOpenDetail?: (t: Txn) => void
  onManagePayees?: () => void
  onCreateCategory?: (
    prefill: string,
    kind: 'income' | 'expense' | 'transfer' | 'investment',
    cb: (id: string) => void,
  ) => void
  splitOpen: boolean
  onSplit: (id: string) => void
  payeeOptions: PayeeOption[]
  payeeName: string | null // pre-resolved from t.payeeId by the orchestrator
  tmpl: string
  /** Visible column keys in REG_COLS order (from the Register's `cols`). Each toggleable cell
   *  renders iff its key is present, so header/row/add-row stay grid-aligned under the picker. */
  colKeys: string[]
  rates: FxRates
  categories: Category[]
  catMap?: Record<string, Category> // live server category map — for pill label/colour
}

export function TxnRow({
  t,
  accounts,
  showAccount,
  selectedId,
  selected,
  onToggle,
  onUpdate,
  onOpenDetail,
  onManagePayees,
  onCreateCategory,
  splitOpen,
  onSplit,
  payeeOptions,
  payeeName,
  tmpl,
  colKeys,
  rates,
  categories,
  catMap,
}: TxnRowProps) {
  const [edit, setEdit] = useState<string | null>(null)
  const close = () => setEdit(null)
  const commit = (patch: RowPatch) => {
    onUpdate(t.id, patch)
    setEdit(null)
  }
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelOpen = () => {
    if (openTimer.current) {
      clearTimeout(openTimer.current)
      openTimer.current = null
    }
  }
  const scheduleOpen = () => {
    cancelOpen()
    openTimer.current = setTimeout(() => {
      openTimer.current = null
      onOpenDetail?.(t)
    }, 200)
  }
  useEffect(() => cancelOpen, [])
  useEffect(() => {
    if (!edit) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close()
      }
    }
    document.addEventListener('keydown', onEsc, true)
    return () => document.removeEventListener('keydown', onEsc, true)
  }, [edit])
  const editCell = (field: string) => (e: React.MouseEvent) => {
    e.stopPropagation()
    cancelOpen()
    setEdit(field)
    try {
      window.getSelection()?.removeAllRanges()
    } catch (_) {}
  }
  const acctMap = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts])

  // Column visibility (from the Register's derived `cols`) — guard each hardcoded cell so the row
  // renders exactly the visible columns in REG_COLS order, keeping the CSS grid aligned.
  const has = (key: string) => colKeys.includes(key)
  // 1-based grid-column of the first visible key in the list — for the split-detail overlay, whose
  // cells are absolutely placed. Falls back to the last track (amount, always visible) so an index
  // is never emitted beyond the template's tracks.
  const gcol = (...keys: string[]): number => {
    for (const k of keys) {
      const i = colKeys.indexOf(k)
      if (i >= 0) return i + 1
    }
    return colKeys.length
  }

  const v = buildTxnView(t, accounts, selectedId, payeeName, rates, showAccount)
  const {
    amount,
    ccy,
    pos,
    baseVal,
    amtTitle,
    nativeMain,
    baseMain,
    primaryAcctId,
    primaryAcct,
    isTransfer,
    isSplit,
    amtSub,
    payeeText,
    payeeFromName,
    payeeToName,
    mentionSub,
  } = v
  // The bank/matching description, shown as-is — no payee de-dup (predictable; matches the drawer).
  const descText = t.description
  const descTitle = t.description || undefined
  // The personal memo (display-only; ADR 2026-07-17) — rendered in its own optional column.
  const memoText = t.memo
  const memoTitle = t.memo || undefined
  // The picker offers categories of the SAME kind as the transaction — a transfer shows
  // transfer-kind categories, an investment investment-kind (categories are kind-scoped; owner
  // 2026-07-10). Adjustments never reach the picker (their category cell stays locked).
  const catPickKind = t.kind === 'adjustment' ? 'expense' : t.kind
  const spendCats = catOptionsFor(catPickKind, categories, t.category)

  const payeeTransfer = isTransfer
  const payeeMain: React.ReactNode = isTransfer ? (
    t.kind === 'adjustment' ? (
      'Balance adjustment'
    ) : (
      <span>
        {payeeFromName || '—'}
        <span className="arrow">→</span>
        {payeeToName || '—'}
      </span>
    )
  ) : (
    payeeText
  )

  // ── Lucius mention wiring ─────────────────────────────────────────────────
  // Make every register row @-mentionable, draggable into the dock, and
  // right-click "Ask Lucius"-able — the same contract the prior Cash tab had.
  const signedBase = (pos ? 1 : -1) * Math.abs(baseVal)
  const fromAcct = t.fromAccountId ? acctMap[t.fromAccountId] : null
  const toAcct = t.toAccountId ? acctMap[t.toAccountId] : null
  const askLabel = payeeName || t.description || '(no description)'
  const askSearch = [t.description, t.memo, payeeName, primaryAcct?.name, t.category]
    .filter(Boolean)
    .join(' ')

  return (
    <>
      <EntityRow
        as="div"
        type="transaction"
        id={t.id}
        selected={selected}
        label={askLabel}
        search={askSearch}
        sublabel={mentionSub}
        item={t}
        toFields={(it) => ({
          date: it.date,
          payee: payeeName || null,
          category: it.category && !isTransfer ? it.category : null,
          amount_base: fBase(signedBase, { cents: true }),
          account: it.kind !== 'transfer' ? (primaryAcct?.name ?? null) : null,
          from_account: it.kind === 'transfer' ? (fromAcct?.name ?? null) : null,
          to_account: it.kind === 'transfer' ? (toAcct?.name ?? null) : null,
          description: it.description || null,
          status: it.cleared ? 'Cleared' : 'Uncleared',
          kind: it.kind,
        })}
        className={'acct-tr' + (selected ? ' sel' : '') + (t.needsReview ? ' needs-review' : '')}
        data-opening={t.source === 'opening' ? '' : undefined}
        style={{ gridTemplateColumns: tmpl, cursor: 'pointer' }}
        onClick={scheduleOpen}
      >
        <span
          className={'acct-cb' + (selected ? ' on' : '')}
          onClick={(e) => {
            e.stopPropagation()
            onToggle(t.id, e.shiftKey)
          }}
        >
          <IconCheck />
        </span>
        <span
          className="acct-clr"
          title={t.cleared ? 'Cleared — click to unclear' : 'Uncleared — click to clear'}
          onClick={(e) => {
            e.stopPropagation()
            onUpdate(t.id, { cleared: !t.cleared })
          }}
        >
          {t.cleared ? (
            <Check className="c" size={12} aria-hidden="true" />
          ) : (
            <Circle className="o" size={12} aria-hidden="true" />
          )}
        </span>
        {has('account') && (
          <span className="acct-td-acct" data-edit="" onDoubleClick={editCell('account')}>
            {edit === 'account' ? (
              <InlineField onCancel={close}>
                <AddAccountSelect
                  accounts={accounts}
                  value={primaryAcctId ?? ''}
                  onChange={(v) => commit(accountPatch(t, v))}
                  advance={() => {}}
                  autoFocus
                  autoOpen
                />
              </InlineField>
            ) : primaryAcct ? (
              <span>{primaryAcct.name}</span>
            ) : (
              <span>—</span>
            )}
          </span>
        )}
        {has('date') && (
          <span className="acct-td-date" data-edit="" onDoubleClick={editCell('date')}>
            {edit === 'date' ? (
              <InlineField onCancel={close}>
                <AddDateField
                  value={t.date}
                  onChange={(v) => commit({ date: v })}
                  advance={() => {}}
                  autoFocus
                />
              </InlineField>
            ) : (
              fDate(t.date, 'numeric')
            )}
          </span>
        )}
        {has('payee') && (
          <span
            className="acct-td-payee"
            data-edit={isTransfer ? undefined : ''}
            onDoubleClick={isTransfer ? undefined : editCell('payee')}
          >
            {edit === 'payee' ? (
              <InlineField onCancel={close}>
                <InlinePayee
                  value={payeeName}
                  payeeOptions={payeeOptions}
                  onManagePayees={onManagePayees}
                  commit={commit}
                />
              </InlineField>
            ) : (
              <div className={'acct-payee-nm' + (payeeTransfer ? ' transfer' : '')}>
                {payeeMain}
              </div>
            )}
          </span>
        )}
        {has('category') && (
          <span
            data-edit={t.kind === 'adjustment' || isSplit ? undefined : ''}
            onDoubleClick={t.kind === 'adjustment' || isSplit ? undefined : editCell('category')}
          >
            {edit === 'category' ? (
              <InlineField onCancel={close}>
                <AddCategoryField
                  value={t.category ?? ''}
                  options={spendCats}
                  onChange={(k) => commit({ category: k })}
                  onCreate={
                    onCreateCategory
                      ? (prefill, cb) => onCreateCategory(prefill, catPickKind, cb)
                      : undefined
                  }
                  advance={() => {}}
                  autoFocus
                  clearable
                />
              </InlineField>
            ) : isSplit ? (
              <button
                className="acct-split-chip"
                onClick={(e) => {
                  e.stopPropagation()
                  onSplit(t.id)
                }}
              >
                Split • {t.splits.length}{' '}
                {splitOpen ? (
                  <ChevronUp size={12} aria-hidden="true" />
                ) : (
                  <ChevronDown size={12} aria-hidden="true" />
                )}
              </button>
            ) : t.category && t.kind !== 'adjustment' ? (
              categoryPill(t.category, catMap)
            ) : !isTransfer && (t.kind === 'expense' || t.kind === 'income') ? (
              <span
                className="acct-unsorted"
                title="No category — this won't count toward any budget envelope"
              >
                Uncategorized
              </span>
            ) : (
              <span className="acct-payee-sub">—</span>
            )}
          </span>
        )}
        {has('description') && (
          <span
            className="acct-td-memo"
            data-edit=""
            onDoubleClick={editCell('description')}
            title={edit === 'description' ? undefined : descTitle}
          >
            {edit === 'description' ? (
              <>
                {/* EditText must import from pickers */}
                <input
                  className="acct-editin"
                  autoFocus
                  defaultValue={t.description}
                  onClick={(e) => e.stopPropagation()}
                  onBlur={(e) => commit({ description: e.target.value })}
                  onKeyDown={(e) => {
                    e.stopPropagation()
                    if (e.key === 'Enter') e.currentTarget.blur()
                    else if (e.key === 'Escape') {
                      ;(e as unknown as { _cancel: boolean })._cancel = true
                      close()
                    }
                  }}
                />
              </>
            ) : descText ? (
              descText
            ) : (
              <span className="acct-payee-sub">—</span>
            )}
          </span>
        )}
        {has('memo') && (
          <span
            className="acct-td-memo2"
            data-edit=""
            onDoubleClick={editCell('memo')}
            title={edit === 'memo' ? undefined : memoTitle}
          >
            {edit === 'memo' ? (
              <input
                className="acct-editin"
                autoFocus
                defaultValue={t.memo}
                onClick={(e) => e.stopPropagation()}
                onBlur={(e) => commit({ memo: e.target.value })}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') e.currentTarget.blur()
                  else if (e.key === 'Escape') {
                    close()
                  }
                }}
              />
            ) : memoText ? (
              memoText
            ) : (
              <span className="acct-payee-sub">—</span>
            )}
          </span>
        )}
        {has('source') && (
          <span className="acct-src">
            {t.needsReview ? (
              <span
                className="acct-review-pill"
                title="Added by an import, Lucius or an automation, and not confirmed by you yet. Approving only clears this flag."
              >
                Review
              </span>
            ) : (
              <SourceIcon source={t.source} />
            )}
          </span>
        )}
        {edit === 'amount' && !isTransfer ? (
          <span className="acct-td-amt">
            <input
              className="acct-editin r"
              type="number"
              autoFocus
              defaultValue={pos ? Math.abs(amount) : -Math.abs(amount)}
              onClick={(e) => e.stopPropagation()}
              onBlur={(e) => {
                const num = parseFloat(e.target.value)
                if (isNaN(num)) {
                  close()
                  return
                }
                const mag = Math.abs(num)
                const patch: RowPatch = {}
                if (t.kind === 'investment') {
                  // Keep kind=investment; positive = sell (to-leg), negative = buy (from-leg).
                  if (num > 0) {
                    patch.toAccountId = primaryAcctId
                    patch.fromAccountId = null
                    patch.toAmount = mag
                    patch.toCurrency = ccy
                    patch.fromAmount = null
                  } else {
                    patch.fromAccountId = primaryAcctId
                    patch.toAccountId = null
                    patch.fromAmount = mag
                    patch.fromCurrency = ccy
                    patch.toAmount = null
                  }
                } else if (num > 0 && t.kind !== 'income') {
                  patch.kind = 'income'
                  patch.toAccountId = primaryAcctId
                  patch.fromAccountId = null
                  patch.toAmount = mag
                  patch.toCurrency = ccy
                  patch.fromAmount = null
                  patch.category = null
                } else if (num < 0 && t.kind !== 'expense') {
                  patch.kind = 'expense'
                  patch.fromAccountId = primaryAcctId
                  patch.toAccountId = null
                  patch.fromAmount = mag
                  patch.fromCurrency = ccy
                  patch.toAmount = null
                  patch.category = null
                } else if (t.kind === 'income') {
                  patch.toAmount = mag
                } else {
                  patch.fromAmount = mag
                }
                commit(patch)
              }}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Enter') e.currentTarget.blur()
                else if (e.key === 'Escape') {
                  close()
                }
              }}
            />
          </span>
        ) : (
          <span
            className={
              'acct-td-amt mask ' +
              (pos ? 'pos' : 'neg') +
              (t.kind === 'investment' ? ' invest' : '')
            }
            data-edit={isTransfer ? undefined : ''}
            onDoubleClick={isTransfer ? undefined : editCell('amount')}
            title={amtTitle}
          >
            <span className="acct-amt-main">{showAccount ? baseMain : nativeMain}</span>
            {amtSub && <span className="acct-amt-orig">{amtSub}</span>}
          </span>
        )}
      </EntityRow>
      {isSplit && splitOpen && (
        <div className="acct-split-detail">
          {t.splits.map((s, i) => (
            // Overlay cells are absolutely placed onto the row's grid tracks; resolve each to the
            // live visible-column index (category/description may be hidden, and `memo` shifts
            // everything right of it) so the split lines track the columns actually on screen.
            <div className="acct-split-line" key={i} style={{ gridTemplateColumns: tmpl }}>
              <span
                className="acct-split-cat"
                style={{ gridColumn: gcol('category', 'payee', 'date') }}
              >
                {categoryPill(s.category, catMap)}
              </span>
              {s.note && (has('description') || has('memo')) && (
                <span
                  className="acct-split-note"
                  style={{ gridColumn: gcol('description', 'memo') }}
                >
                  {s.note}
                </span>
              )}
              <span className="acct-split-amt" style={{ gridColumn: gcol('amount') }}>
                {fCur(s.amount, t.fromCurrency ?? ccy, { cents: true })}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
