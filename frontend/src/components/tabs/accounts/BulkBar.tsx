/**
 * BulkBar.tsx — Bulk action bar shown when rows are selected in the register.
 * Ported from prototype/accounts/main.jsx (BulkBar).
 *
 * Presentational — each action fires a callback; orchestrator wires to bulk hook fns.
 * Keyboard shortcuts: C=mark cleared, Shift+D=duplicate, Delete/Backspace=delete.
 */

import { useState, useRef, useEffect, type CSSProperties } from 'react'
import type { Account, Category } from './types'
import type { SelectionTotal } from './data'
import { fBase, fCur } from './format'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { BottomSheet } from '../../ui/BottomSheet'
import {
  Tags,
  ChevronRight,
  Check,
  Circle,
  Copy,
  Pencil,
  ArrowLeftRight,
  Download,
  Trash2,
  ChevronLeft,
  MoreHorizontal,
} from '../../../ui/icons'

// Hoisted out of BulkBar — a component declared during render is re-created (and its state
// reset) on every render (react-hooks/static-components).
function Shortcut({ k }: { k: string }) {
  return <span className="acct-bm-kbd">{k}</span>
}

export interface BulkBarProps {
  count: number
  total: SelectionTotal // signed leg sum: exact native when one currency, ≈ base when mixed
  accounts: Account[]
  categories: Category[]
  railCollapsed: boolean
  railWidth: number
  onClear: () => void // deselect all
  onCategory: (key: string) => void
  // Mobile swaps the quick-categorize dropdown for a handoff to Lucius — the flyout's small
  // touch targets don't fit the phone bar, and Lucius can ask follow-up questions a fixed
  // category list can't.
  onAskLucius: () => void
  onMarkCleared: () => void
  onMarkUncleared: () => void
  onMarkReviewed: () => void
  /** How many of the selected rows each action would ACTUALLY change. An action that would
   *  change nothing is DISABLED rather than hidden — hiding it makes the menu change height
   *  between selections, and a greyed row with a reason explains itself where a missing one
   *  just looks broken. Omit to leave every action enabled. */
  actionable?: { review: number; clear: number; unclear: number }
  onDuplicate: () => void
  onMemo: (text: string) => void
  onMove: (accountId: string) => void
  onExport: () => void
  onDelete: () => void
}

export function BulkBar({
  count,
  total,
  accounts,
  categories,
  railCollapsed,
  railWidth,
  onClear,
  onCategory,
  onAskLucius,
  onMarkCleared,
  onMarkUncleared,
  onMarkReviewed,
  actionable,
  onDuplicate,
  onMemo,
  onMove,
  onExport,
  onDelete,
}: BulkBarProps) {
  const [menu, setMenu] = useState<string | null>(null)
  const [moveOpen, setMoveOpen] = useState(false)
  const [catOpen, setCatOpen] = useState(false)
  const [memoMode, setMemoMode] = useState(false)
  const [memoText, setMemoText] = useState('')
  const ref = useRef<HTMLDivElement | null>(null)
  const isMobile = useIsMobile()
  // `undefined` (caller did not say) leaves everything enabled; 0 means "would change nothing".
  // Declared above the key handler so the `C` shortcut and the menu item read one condition.
  const nReview = actionable?.review
  const nCleared = actionable?.clear
  const nUncleared = actionable?.unclear
  const close = () => {
    setMenu(null)
    setMoveOpen(false)
    setCatOpen(false)
    setMemoMode(false)
    setMemoText('')
  }
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      // The mobile "more" sheet is portaled to <body>, outside `ref` — it owns its own
      // scrim-click/Escape dismissal (BottomSheet), so this outside-click check sits out
      // while it's open instead of closing it the instant a tap lands inside the portal.
      if (isMobile && menu === 'more') return
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    const isField = (el: Element | null) => !!el && /^(input|select|textarea)$/i.test(el.tagName)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close()
        return
      }
      if (
        isField(document.activeElement) ||
        (document.activeElement as HTMLElement)?.isContentEditable
      )
        return
      if (menu || memoMode) return
      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault()
        if (nCleared !== 0) onMarkCleared()
      } else if ((e.key === 'd' || e.key === 'D') && e.shiftKey) {
        e.preventDefault()
        onDuplicate()
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        onDelete()
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  })
  const spend = categories.filter((c) => c.kind === 'expense')
  const act = <A extends unknown[]>(fn: (...args: A) => void, ...args: A) => {
    close()
    fn(...args)
  }
  /** Tooltip explaining a greyed-out action, or nothing when the action is live. */
  const whyDisabled = (n: number | undefined, all: string) => (n === 0 ? all : undefined)
  // Shared between the desktop floating dropdown and the mobile bottom sheet — same actions,
  // different chrome around them (BulkBarProps' `menu`/`moveOpen`/`memoMode` state machine).
  const moreMainItems = (
    <>
      {isMobile && (
        <>
          <button className="acct-bm-item" onClick={() => setCatOpen(true)}>
            <Tags className="acct-bm-ic" size={14} aria-hidden="true" />
            Categorize
            <ChevronRight className="acct-bm-caret" size={14} aria-hidden="true" />
          </button>
          <div className="acct-bm-div" />
        </>
      )}
      <button
        className="acct-bm-item"
        onClick={() => act(onMarkCleared)}
        disabled={nCleared === 0}
        title={whyDisabled(nCleared, 'Every selected transaction is already cleared')}
      >
        <Check className="acct-bm-ic" size={14} aria-hidden="true" />
        Mark as Cleared
        {!isMobile && <Shortcut k="C" />}
      </button>
      <button
        className="acct-bm-item"
        onClick={() => act(onMarkUncleared)}
        disabled={nUncleared === 0}
        title={whyDisabled(nUncleared, 'No selected transaction is cleared')}
      >
        <Circle className="acct-bm-ic dim" size={14} aria-hidden="true" />
        Mark as Uncleared
        {!isMobile && <Shortcut k="C" />}
      </button>
      <button
        className="acct-bm-item"
        onClick={() => act(onMarkReviewed)}
        disabled={nReview === 0}
        title={whyDisabled(nReview, 'Every selected transaction is already reviewed')}
      >
        <Check className="acct-bm-ic" size={14} aria-hidden="true" />
        Mark as Reviewed
      </button>
      <div className="acct-bm-div" />
      <button className="acct-bm-item" onClick={() => act(onDuplicate)}>
        <Copy className="acct-bm-ic" size={14} aria-hidden="true" />
        Duplicate
        {!isMobile && <Shortcut k="⇧D" />}
      </button>
      <button className="acct-bm-item" onClick={() => setMemoMode(true)}>
        <Pencil className="acct-bm-ic" size={14} aria-hidden="true" />
        Description…
      </button>
      <button className="acct-bm-item" onClick={() => setMoveOpen(true)}>
        <ArrowLeftRight className="acct-bm-ic" size={14} aria-hidden="true" />
        Move to Account
        <ChevronRight className="acct-bm-caret" size={14} aria-hidden="true" />
      </button>
      <button className="acct-bm-item" onClick={() => act(onExport)}>
        <Download className="acct-bm-ic" size={14} aria-hidden="true" />
        Export {count} {count === 1 ? 'Transaction' : 'Transactions'}
      </button>
      <div className="acct-bm-div" />
      <button className="acct-bm-item danger" onClick={() => act(onDelete)}>
        <Trash2 className="acct-bm-ic" size={14} aria-hidden="true" />
        Delete
        {!isMobile && <Shortcut k="⌫" />}
      </button>
    </>
  )
  const catItems = (
    <>
      <button className="acct-bm-item" onClick={() => setCatOpen(false)}>
        <ChevronLeft className="acct-bm-ic" size={14} aria-hidden="true" />
        Back
      </button>
      <div className="acct-bm-div" />
      {spend.map((c) => (
        <button key={c.id} className="acct-bm-item" onClick={() => act(onCategory, c.id)}>
          <i className="acct-bm-dot" style={{ background: c.color }} />
          {c.label}
        </button>
      ))}
    </>
  )
  const moveItems = (
    <>
      {isMobile && (
        <>
          <button className="acct-bm-item" onClick={() => setMoveOpen(false)}>
            <ChevronLeft className="acct-bm-ic" size={14} aria-hidden="true" />
            Back
          </button>
          <div className="acct-bm-div" />
        </>
      )}
      {accounts.map((a) => (
        <button key={a.id} className="acct-bm-item" onClick={() => act(onMove, a.id)}>
          {a.name}
        </button>
      ))}
    </>
  )
  const memoFields = (
    <>
      {!isMobile && <div className="acct-bm-lbl">Set description on {count}</div>}
      <input
        className="acct-bm-input"
        autoFocus
        placeholder="Description"
        value={memoText}
        onChange={(e) => setMemoText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') act(onMemo, memoText)
        }}
      />
      <div className="acct-bm-memo-actions">
        <button className="acct-bm-textbtn" onClick={() => setMemoMode(false)}>
          Cancel
        </button>
        <button className="acct-bm-textbtn primary" onClick={() => act(onMemo, memoText)}>
          Apply
        </button>
      </div>
    </>
  )
  return (
    <div
      className={'acct-bulk mb-anchored' + (railCollapsed ? ' rail-collapsed' : '')}
      style={{ '--acct-bulk-rail-w': railCollapsed ? '0px' : `${railWidth}px` } as CSSProperties}
    >
      <div className="acct-bulk-inner" ref={ref}>
        <button
          className="acct-bulk-x"
          onClick={onClear}
          title="Deselect all"
          aria-label="Deselect all"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
        <span className="acct-bulk-count">
          {count}{' '}
          {isMobile ? (count === 1 ? 'txn' : 'txns') : count === 1 ? 'Transaction' : 'Transactions'}
        </span>
        {count >= 1 && (
          <span className={'acct-bulk-total mask ' + (total.amount < 0 ? 'neg' : 'pos')}>
            {total.approx ? '≈ ' : ''}
            {total.amount >= 0 ? '+' : ''}
            {total.currency
              ? fCur(total.amount, total.currency, { cents: true })
              : fBase(total.amount, { cents: true })}
          </span>
        )}
        <span className="acct-bulk-sep" />

        <div className="acct-bm-wrap">
          {isMobile ? (
            // Mobile: the categorize flyout's tap targets don't fit the phone bar, and Lucius
            // can ask a follow-up ("which category?") a fixed list can't — hand off instead.
            <button
              className="acct-bulk-btn acct-bulk-btn--lucius"
              onClick={() => act(onAskLucius)}
            >
              Ask Lucius
            </button>
          ) : (
            <>
              <button
                className={'acct-bulk-btn' + (menu === 'cat' ? ' on' : '')}
                onClick={() => setMenu((m) => (m === 'cat' ? null : 'cat'))}
              >
                <Tags className="acct-bm-ic" size={14} aria-hidden="true" />
                Categorize
              </button>
              {menu === 'cat' && (
                <div className="acct-bm-menu cats">
                  <div className="acct-bm-lbl">Set category</div>
                  {spend.map((c) => (
                    <button
                      key={c.id}
                      className="acct-bm-item"
                      onClick={() => act(onCategory, c.id)}
                    >
                      <i className="acct-bm-dot" style={{ background: c.color }} />
                      {c.label}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="acct-bm-wrap">
          <button
            className={'acct-bulk-btn' + (menu === 'more' ? ' on' : '')}
            onClick={() => setMenu((m) => (m === 'more' ? null : 'more'))}
            aria-label={isMobile ? 'More' : undefined}
          >
            <MoreHorizontal className="acct-bm-ic" size={14} aria-hidden="true" />
            {!isMobile && 'More'}
          </button>
          {menu === 'more' && !isMobile && !memoMode && !moveOpen && (
            <div className="acct-bm-menu wide">{moreMainItems}</div>
          )}
          {menu === 'more' && !isMobile && moveOpen && !memoMode && (
            <div className="acct-bm-menu wide">
              <button className="acct-bm-item" onClick={() => setMoveOpen(false)}>
                <ChevronLeft className="acct-bm-ic" size={14} aria-hidden="true" />
                Move to Account
              </button>
              <div className="acct-bm-div" />
              {moveItems}
            </div>
          )}
          {menu === 'more' && !isMobile && memoMode && (
            <div className="acct-bm-menu memo">{memoFields}</div>
          )}
        </div>
      </div>
      {menu === 'more' && isMobile && (
        <BottomSheet
          title={
            memoMode
              ? `Set description on ${count}`
              : moveOpen
                ? 'Move to account'
                : catOpen
                  ? 'Set category'
                  : 'More actions'
          }
          onClose={close}
        >
          {memoMode ? memoFields : moveOpen ? moveItems : catOpen ? catItems : moreMainItems}
        </BottomSheet>
      )}
    </div>
  )
}
