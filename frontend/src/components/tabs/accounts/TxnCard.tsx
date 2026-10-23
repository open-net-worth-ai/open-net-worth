/**
 * TxnCard.tsx — mobile register row, rendered as a card instead of a dense grid row.
 *
 * The desktop `.acct-reg` grid (10 columns, drag-resizable, inline-editable) is
 * unreadable below ~640px — see mission-control task 94. Below that width, Register
 * swaps TxnRow for TxnCard per transaction: no inline cell editing (tap opens the
 * full TxnDetail drawer, which already edits every field), no column picker/resize —
 * just the read layout, ranked by what a user scans a transaction list for:
 *   1. Payee/description + amount (the "who / how much") — the title row.
 *   2. Date + category (or the Split chip) — the "when / what for" meta row.
 *   3. Account (only in the all-accounts view) and a needs-review flag, same row.
 *   4. Memo — personal annotation, shown only when present.
 * Selection (checkbox) and the cleared toggle stay one-tap actions, same as the
 * desktop row; everything else (rename payee, edit category, split) happens in the
 * detail drawer, which is already the mobile-appropriate surface for editing.
 */

import { EntityRow } from '../../../lucius/EntityRow'
import type { Txn, Account, Category, FxRates } from './types'
import { fCur, fBase } from './format'
import { fDate } from '../../../utils/format'
import { buildTxnView } from './txnView'
import { categoryPill } from './TxnRow'
import { SourceIcon, IconCheck } from './pickers'
import { ChevronUp, ChevronDown, Check, Circle } from '../../../ui/icons'

export interface TxnCardProps {
  t: Txn
  accounts: Account[]
  showAccount: boolean
  selectedId: string | null
  selected: boolean
  onToggle: (id: string, shift?: boolean) => void
  onUpdate: (id: string, patch: { cleared?: boolean }) => void
  onOpenDetail?: (t: Txn) => void
  payeeName: string | null
  rates: FxRates
  catMap?: Record<string, Category>
  splitOpen: boolean
  onSplit: (id: string) => void
  /** False when a date-group header (Register's YNAB-style grouping) already shows this
   *  card's date, so repeating it per-card would be redundant. Defaults to true. */
  showDate?: boolean
}

export function TxnCard({
  t,
  accounts,
  showAccount,
  selectedId,
  selected,
  onToggle,
  onUpdate,
  onOpenDetail,
  payeeName,
  rates,
  catMap,
  splitOpen,
  onSplit,
  showDate = true,
}: TxnCardProps) {
  const {
    ccy,
    pos,
    baseVal,
    amtTitle,
    nativeMain,
    baseMain,
    primaryAcct,
    isTransfer,
    isAdjustment,
    isSplit,
    amtSub,
    payeeText,
    payeeFromName,
    payeeToName,
    mentionSub,
  } = buildTxnView(t, accounts, selectedId, payeeName, rates, showAccount)

  const payeeMain: React.ReactNode = isTransfer ? (
    isAdjustment ? (
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
  const askLabel = payeeName || t.description || '(no description)'
  const askSearch = [t.description, t.memo, payeeName, primaryAcct?.name, t.category]
    .filter(Boolean)
    .join(' ')

  return (
    <>
      <EntityRow
        as="article"
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
          amount_base: fBase((pos ? 1 : -1) * Math.abs(baseVal), { cents: true }),
          account: it.kind !== 'transfer' ? (primaryAcct?.name ?? null) : null,
          from_account: it.kind === 'transfer' ? (payeeFromName ?? null) : null,
          to_account: it.kind === 'transfer' ? (payeeToName ?? null) : null,
          description: it.description || null,
          status: it.cleared ? 'Cleared' : 'Uncleared',
          kind: it.kind,
        })}
        className={'acct-card' + (selected ? ' sel' : '') + (t.needsReview ? ' needs-review' : '')}
        onClick={() => onOpenDetail?.(t)}
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
        <span className="acct-card-body">
          <span className="acct-card-top">
            <span className={'acct-payee-nm' + (isTransfer ? ' transfer' : '')}>{payeeMain}</span>
            <span
              className={
                'acct-card-amt ' +
                (pos ? 'pos' : 'neg') +
                (t.kind === 'investment' ? ' invest' : '')
              }
              title={amtTitle}
            >
              <span className="acct-amt-main">{showAccount ? baseMain : nativeMain}</span>
              {amtSub && <span className="acct-amt-orig">{amtSub}</span>}
            </span>
          </span>
          <span className="acct-card-meta">
            {showDate && <span className="acct-card-date">{fDate(t.date, 'numeric')}</span>}
            {isSplit ? (
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
            ) : t.category && !isAdjustment ? (
              categoryPill(t.category, catMap)
            ) : !isTransfer && (t.kind === 'expense' || t.kind === 'income') ? (
              <span
                className="acct-unsorted"
                title="No category — this won't count toward any budget envelope"
              >
                Uncategorized
              </span>
            ) : null}
            {showAccount && primaryAcct && !isTransfer && (
              <span className="acct-card-acct">{primaryAcct.name}</span>
            )}
            {t.needsReview && <span className="acct-review-pill">Review</span>}
            <span
              className="acct-clr"
              title={t.cleared ? 'Cleared — tap to unclear' : 'Uncleared — tap to clear'}
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
            {!t.needsReview && <SourceIcon source={t.source} />}
          </span>
          {t.memo && (
            <span
              className="acct-card-memo"
              style={{
                display: '-webkit-box',
                overflow: 'hidden',
                WebkitBoxOrient: 'vertical',
                WebkitLineClamp: 3,
              }}
            >
              {t.memo}
            </span>
          )}
        </span>
      </EntityRow>
      {isSplit && splitOpen && (
        <div className="acct-card-split-detail">
          {t.splits.map((s, i) => (
            <div className="acct-card-split-line" key={i}>
              {categoryPill(s.category, catMap)}
              {s.note && <span className="acct-split-note">{s.note}</span>}
              <span className="acct-split-amt">
                {fCur(s.amount, t.fromCurrency ?? ccy, { cents: true })}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
