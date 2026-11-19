/**
 * AddRow.tsx — Inline transaction add row (YNAB-style).
 * Rendered inside the Register when `active` is true.
 *
 * Presentational — onAdd(data) fires when the user commits; returns false to
 * leave the row open (e.g., on a validation error from the orchestrator).
 */

import { useState, useEffect, useRef } from 'react'
import type { Account, Category } from './types'
import { catOptionsFor } from './categories'
import {
  AddAccountSelect,
  AddDateField,
  AddPayeeField,
  AddCategoryField,
  IconPlus,
  type PayeeOption,
} from './pickers'
import { Button } from '../../ui/Button'
import { TagField } from './TagField'
import { localIso } from '../../../utils/format'

// ── AddData: what onAdd receives ──────────────────────────────────────────

export interface AddData {
  date: string
  payee: string // display name; orchestrator resolves to payeeId
  description: string // bank/matching string — the ONLY freeform field read by dedup/rules/payees
  memo: string // personal annotation — display-only
  tags: string[]
  category: string
  account: string // from-account id (income → to-account)
  amount: string // raw decimal string
  kind: 'income' | 'expense' | 'transfer' | 'investment'
  transferTo: string // target account id for transfers, '' otherwise
  /** The +/− toggle. `kind` already carries it for income/expense, but a TRANSFER needs it
   *  separately — money IN to the open account means the OTHER account is the from-leg. */
  inflow: boolean
}

// ── AddRow ─────────────────────────────────────────────────────────────────

export interface AddRowProps {
  active: boolean
  onClose: () => void
  accounts: Account[]
  showAccount: boolean
  selectedId: string | null
  payeeOptions: PayeeOption[]
  tmpl: string
  /** Visible column keys in REG_COLS order (from the Register's `cols`) — the add row renders
   *  the same cells as the header/rows so the shared grid template stays aligned. */
  colKeys: string[]
  onAdd: (data: AddData) => boolean | void
  onManagePayees?: () => void
  categories: Category[]
  onCreateCategory?: (prefill: string, kind: 'income' | 'expense', cb: (id: string) => void) => void
}

interface FormState {
  date: string
  payee: string
  description: string
  memo: string
  tags: string[]
  category: string
  account: string
  amount: string
  inflow: boolean
  transferTo: string
}

export function AddRow({
  active,
  onClose,
  accounts,
  selectedId,
  payeeOptions,
  tmpl,
  colKeys,
  onAdd,
  onManagePayees,
  categories,
  onCreateCategory,
}: AddRowProps) {
  const today = localIso()
  const defAcct = () => selectedId || accounts[0]?.id || ''
  const blank = (keep?: FormState): FormState => ({
    date: keep ? keep.date : today,
    payee: '',
    description: '',
    memo: '',
    tags: [],
    category: '',
    account: keep ? keep.account : defAcct(),
    amount: '',
    inflow: false,
    transferTo: '',
  })
  const [f, setF] = useState<FormState>(blank)
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }))
  const valid =
    parseFloat(f.amount) > 0 && !!f.account && (!f.transferTo || f.transferTo !== f.account)
  const gridRef = useRef<HTMLDivElement | null>(null)
  const fields = () =>
    gridRef.current
      ? Array.from(gridRef.current.querySelectorAll<HTMLElement>('[data-addfield]'))
      : []
  function advance(dir: number, fromEl?: HTMLElement | null) {
    const list = fields()
    const from = fromEl || (document.activeElement as HTMLElement)
    const i = list.indexOf(from)
    if (i === -1) {
      if (dir > 0 && list[0]) list[0].focus()
      return
    }
    const target = list[i + dir]
    if (target) {
      target.focus()
      try {
        ;(target as HTMLInputElement).select?.()
      } catch (_) {}
    } else if (dir > 0) submit(true)
  }
  const keyAdvance = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      advance(1)
    }
  }

  function submit(again: boolean) {
    if (!valid) return
    const ok = onAdd({
      date: f.date,
      payee: f.payee.trim(),
      description: f.description.trim(),
      memo: f.memo.trim(),
      tags: f.tags,
      category: f.category,
      account: f.account,
      amount: f.amount,
      kind: f.transferTo ? 'transfer' : f.inflow ? 'income' : 'expense',
      transferTo: f.transferTo,
      inflow: f.inflow,
    })
    if (ok === false) return
    if (again) {
      setF(blank(f))
      requestAnimationFrame(() => {
        const list = fields()
        if (list[0]) list[0].focus()
      })
    } else {
      onClose()
      setF(blank())
    }
  }
  function cancel() {
    onClose()
    setF(blank())
  }
  useEffect(() => {
    if (active) setF(blank())
  }, [active]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!active) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        cancel()
      }
    }
    document.addEventListener('keydown', onEsc, true)
    return () => document.removeEventListener('keydown', onEsc, true)
  }, [active]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!active) return null

  const catKind: 'income' | 'expense' = f.inflow ? 'income' : 'expense'
  const catOpts = catOptionsFor(catKind, categories)
  const xferAcct = f.transferTo ? accounts.find((a) => a.id === f.transferTo) : null
  // Render the same visible columns (in REG_COLS order) as the header/rows so the shared grid
  // template lines up; cb/clr/amount are structural (always rendered), the rest are toggleable.
  const has = (key: string) => colKeys.includes(key)

  return (
    <div className="acct-addrow">
      <div className="acct-addrow-grid" style={{ gridTemplateColumns: tmpl }} ref={gridRef}>
        <span className="acct-addrow-plus on">
          <IconPlus size={11} />
        </span>
        <span></span>
        {has('account') && (
          <span className="acct-addrow-cell">
            <AddAccountSelect
              accounts={accounts}
              value={f.account}
              onChange={(id) => set('account', id)}
              advance={advance}
              autoFocus
            />
          </span>
        )}
        {has('date') && (
          <span className="acct-addrow-cell">
            <AddDateField
              value={f.date}
              onChange={(v) => set('date', v)}
              advance={advance}
              autoFocus={!has('account')}
            />
          </span>
        )}
        {has('payee') && (
          <span className="acct-addrow-cell">
            <AddPayeeField
              value={f.transferTo ? 'To/From ' + (xferAcct?.name || '') : f.payee}
              onChange={(v, defCat) =>
                setF((s) => {
                  const next = { ...s, payee: v, transferTo: '' }
                  if (defCat) {
                    const c = catOpts.find((x) => x.id === defCat)
                    next.category = defCat
                    if (c) next.inflow = c.kind === 'income'
                  }
                  return next
                })
              }
              onSelectTransfer={(id) =>
                setF((s) => ({ ...s, transferTo: id, payee: '', category: '' }))
              }
              options={payeeOptions}
              accounts={accounts}
              fromAccountId={f.account}
              advance={advance}
              onManagePayees={onManagePayees}
            />
          </span>
        )}
        {has('category') && (
          <span className="acct-addrow-cell">
            {/* Transfers may carry an optional label (ADR 2026-07-03-categorized-transfers):
                the budget only counts it when exactly one side is off-budget. */}
            <AddCategoryField
              value={f.category}
              onChange={(k) => set('category', k)}
              options={catOpts}
              advance={advance}
              clearable
              onCreate={
                onCreateCategory
                  ? (prefill, cb) => onCreateCategory(prefill, catKind, cb)
                  : undefined
              }
            />
          </span>
        )}
        {has('description') && (
          <span className="acct-addrow-cell">
            <input
              className="acct-addin"
              data-addfield
              placeholder="Description"
              value={f.description}
              onChange={(e) => set('description', e.target.value)}
              onKeyDown={keyAdvance}
            />
          </span>
        )}
        {has('memo') && (
          <span className="acct-addrow-cell">
            <input
              className="acct-addin"
              data-addfield
              placeholder="Memo"
              value={f.memo}
              onChange={(e) => set('memo', e.target.value)}
              onKeyDown={keyAdvance}
            />
          </span>
        )}
        {has('tags') && (
          <span className="acct-addrow-cell">
            <TagField
              compact
              addField
              value={f.tags}
              onChange={(tags) => set('tags', tags)}
              placeholder="Tags"
              onAdvance={(dir, el) => advance(dir, el)}
            />
          </span>
        )}
        {has('source') && <span></span>}
        <span className="acct-addrow-cell amt">
          <button
            type="button"
            className={'acct-addrow-sign ' + (f.inflow ? 'in' : 'out')}
            title={f.inflow ? 'Inflow — click for outflow' : 'Outflow — click for inflow'}
            onClick={() =>
              setF((s) => {
                const ni = !s.inflow
                const c = catOpts.find((x) => x.id === s.category)
                return {
                  ...s,
                  inflow: ni,
                  category: !ni && c && c.kind === 'income' ? '' : s.category,
                }
              })
            }
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
            >
              {f.inflow ? <path d="M12 5v14M5 12h14" /> : <path d="M5 12h14" />}
            </svg>
          </button>
          <input
            className={'acct-addin r ' + (f.inflow ? 'in' : 'out')}
            data-addfield
            inputMode="decimal"
            placeholder="0.00"
            value={f.amount}
            onChange={(e) => set('amount', e.target.value.replace(/[^0-9.]/g, ''))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit(true)
              else if (e.key === 'Escape') cancel()
            }}
          />
        </span>
      </div>
      <div className="acct-addrow-actions">
        <div className="acct-addrow-actions-inner">
          <Button size="sm" variant="ghost" onClick={cancel}>
            Cancel
          </Button>
          <Button size="sm" variant="secondary" disabled={!valid} onClick={() => submit(false)}>
            Save
          </Button>
          <Button size="sm" disabled={!valid} onClick={() => submit(true)}>
            Save and add another
          </Button>
        </div>
      </div>
    </div>
  )
}
