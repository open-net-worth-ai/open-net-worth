/**
 * SearchFilter.tsx — Token-based search/filter bar for the Accounts register.
 * Ported from prototype/accounts/app.jsx (SearchFilter, Filters, ActiveChips).
 *
 * Token model: PRESENTATIONAL — emits/removes tokens; orchestrator maps tokens
 * to TxnFilters for server-side filtering via useAccountsLedger.
 * No API calls here.
 */

import { useState, useMemo, useRef, useEffect } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useBreakpoint } from '../../../hooks/useBreakpoint'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import { FilterCombo, IconPlus, scrollHiIntoView } from './pickers'
import { ColumnsPicker, type ColumnVisibility } from './columns'

// ── Types ─────────────────────────────────────────────────────────────────

export interface FilterToken {
  field: string
  value: string | number | boolean
  op?: string // amount tokens: '>=' | '<=' | '='
  dir?: string // amount tokens: 'any' | 'in' | 'out'
  label: string
  dot?: string
}

export interface SuggestData {
  categories: { id: string; label: string; color: string }[]
  payees: { name: string }[]
  accounts: { id: string; name: string }[]
  tags: string[]
}

export interface FilterState {
  preset: string // 'all' | 'this_month' | 'last_3' | 'this_year' | 'last_3y' | 'custom'
  cfM: number // custom from month (1-12)
  cfY: number // custom from year
  ctM: number // custom to month (1-12)
  ctY: number // custom to year
  tokens: FilterToken[]
}

// ── Constants ─────────────────────────────────────────────────────────────

const FMONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DATE_PRESETS: [string, string][] = [
  ['this_month', 'This month'],
  ['last_3', 'Last 3 months'],
  ['this_year', 'This year'],
  ['last_3y', 'Last 3 years'],
  ['all', 'All time'],
]
const TYPE_OPTS: [string, string][] = [
  ['expense', 'Expense'],
  ['income', 'Income'],
  ['transfer', 'Transfer'],
  ['adjustment', 'Adjustment'],
  ['investment', 'Investment'],
]

export const SOURCE_OPTS = [
  { value: '', label: 'Any source' },
  { value: 'manual', label: 'Manual entry' },
  { value: 'bank', label: 'Bank sync' },
  { value: 'import', label: 'Imported (file)' },
  { value: 'ai', label: 'Lucius (AI)' },
  { value: 'agent', label: 'Agent' },
  { value: 'automation', label: 'Automation' },
  { value: 'reconcile', label: 'Reconcile adjustment' },
]

// ── Date parsing (smart / unambiguous — never MM/DD vs DD/MM) ───────────────
// Accepts ISO (2026-06-01, 2026-06), month names (Jun / June 2026 / Jun 6 / 6 Jun 2026),
// bare year (2026) and bare month number (1–12). DISPLAYS as "Jun 1, 2026" / "Jun 2026"
// so there is zero ordering ambiguity. Returns null for anything not date-like.
const MONTH_FULL = [
  'january',
  'february',
  'march',
  'april',
  'may',
  'june',
  'july',
  'august',
  'september',
  'october',
  'november',
  'december',
]
const MONTH_ABBR = [
  'jan',
  'feb',
  'mar',
  'apr',
  'may',
  'jun',
  'jul',
  'aug',
  'sep',
  'oct',
  'nov',
  'dec',
]

export interface ParsedDate {
  kind: 'day' | 'month' | 'year'
  prefix: string // ISO prefix for an "On" match: YYYY-MM-DD | YYYY-MM | YYYY
  start: string // first day in range (YYYY-MM-DD) — for "on or after"
  end: string // last day in range (YYYY-MM-DD)  — for "on or before"
  display: string // "Jun 1, 2026" | "Jun 2026" | "2026"
  displayStart: string // "Jun 1, 2026"
  displayEnd: string // "Jun 30, 2026"
}
const _pad = (n: number) => String(n).padStart(2, '0')
const _lastDay = (y: number, m1: number) => new Date(y, m1, 0).getDate() // m1 = 1-based month
const _fmtDay = (y: number, m0: number, d: number) => `${FMONTHS[m0]} ${d}, ${y}`

function monthIndexFromWord(w: string): number | null {
  const lw = w.toLowerCase()
  if (lw.length < 3) return null
  // Standard 3-letter abbreviations are all prefixes of the full names, so a prefix match
  // on the full name covers "jun"/"june"/"sept"; an exact abbr is a harmless fallback.
  let i = MONTH_FULL.findIndex((m) => m.startsWith(lw))
  if (i === -1) i = MONTH_ABBR.findIndex((m) => m === lw)
  return i === -1 ? null : i
}
function _monthParsed(y: number, m0: number): ParsedDate {
  const m1 = m0 + 1,
    ld = _lastDay(y, m1)
  return {
    kind: 'month',
    prefix: `${y}-${_pad(m1)}`,
    start: `${y}-${_pad(m1)}-01`,
    end: `${y}-${_pad(m1)}-${_pad(ld)}`,
    display: `${FMONTHS[m0]} ${y}`,
    displayStart: _fmtDay(y, m0, 1),
    displayEnd: _fmtDay(y, m0, ld),
  }
}
function _dayParsed(y: number, m0: number, d: number): ParsedDate {
  const iso = `${y}-${_pad(m0 + 1)}-${_pad(d)}`,
    disp = _fmtDay(y, m0, d)
  return {
    kind: 'day',
    prefix: iso,
    start: iso,
    end: iso,
    display: disp,
    displayStart: disp,
    displayEnd: disp,
  }
}

export function parseDateInput(raw: string): ParsedDate | null {
  const s = (raw || '').trim().toLowerCase()
  if (!s) return null
  const yr = new Date().getFullYear()
  let m: RegExpMatchArray | null
  if ((m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
    const y = +m[1]!,
      mo = +m[2]!,
      d = +m[3]!
    if (mo >= 1 && mo <= 12 && d >= 1 && d <= _lastDay(y, mo)) return _dayParsed(y, mo - 1, d)
    return null
  }
  if ((m = s.match(/^(\d{4})-(\d{1,2})$/))) {
    const y = +m[1]!,
      mo = +m[2]!
    return mo >= 1 && mo <= 12 ? _monthParsed(y, mo - 1) : null
  }
  // month name, optional day, optional year — "june", "jun 2026", "june 6", "jun 6, 2026"
  if ((m = s.match(/^([a-z]+)\.?\s*(\d{1,2})?(?:,?\s*(\d{4}))?$/))) {
    const mi = monthIndexFromWord(m[1]!)
    if (mi !== null) {
      const y = m[3] ? +m[3] : yr
      if (m[2]) {
        const d = +m[2]
        return d >= 1 && d <= _lastDay(y, mi + 1) ? _dayParsed(y, mi, d) : null
      }
      return _monthParsed(y, mi)
    }
  }
  // "<day> <month name> [year]" — "6 jun", "6 june 2026"
  if ((m = s.match(/^(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{4}))?$/))) {
    const mi = monthIndexFromWord(m[2]!)
    if (mi !== null) {
      const d = +m[1]!,
        y = m[3] ? +m[3] : yr
      return d >= 1 && d <= _lastDay(y, mi + 1) ? _dayParsed(y, mi, d) : null
    }
  }
  if ((m = s.match(/^(\d{4})$/))) {
    const y = +m[1]!
    if (y >= 1990 && y <= 2100)
      return {
        kind: 'year',
        prefix: String(y),
        start: `${y}-01-01`,
        end: `${y}-12-31`,
        display: String(y),
        displayStart: _fmtDay(y, 0, 1),
        displayEnd: _fmtDay(y, 11, 31),
      }
  }
  if ((m = s.match(/^(\d{1,2})$/))) {
    const mo = +m[1]!
    if (mo >= 1 && mo <= 12) return _monthParsed(yr, mo - 1)
  }
  return null
}

// The On / Before / After suggestion rows for a parsed date (mode 'any' = all three).
type DateRow = FilterToken & { pre?: string; disp?: React.ReactNode }
export function dateSuggestions(
  pd: ParsedDate,
  mode: 'any' | 'on' | 'before' | 'after',
): DateRow[] {
  const onLabel = (pd.kind === 'day' ? 'On ' : 'In ') + pd.display
  const rows: DateRow[] = []
  if (mode === 'any' || mode === 'on')
    rows.push({ field: 'date_on', value: pd.prefix, label: onLabel, pre: 'Date', disp: onLabel })
  if (mode === 'any' || mode === 'after')
    rows.push({
      field: 'date_after',
      value: pd.start,
      label: 'On or after ' + pd.displayStart,
      pre: 'Date',
      disp: 'On or after ' + pd.displayStart,
    })
  if (mode === 'any' || mode === 'before')
    rows.push({
      field: 'date_before',
      value: pd.end,
      label: 'On or before ' + pd.displayEnd,
      pre: 'Date',
      disp: 'On or before ' + pd.displayEnd,
    })
  return rows
}

// ── Helpers ───────────────────────────────────────────────────────────────

const UNCATEGORIZED_TOKEN: FilterToken = {
  field: 'uncategorized',
  value: true,
  label: 'Uncategorized',
}

/** A blank category — a category condition, not a status. "unsorted" is the label it
 * carried before 2026-10-06; "none" only under `category:`, where it can't be mistaken
 * for a search. */
function isUncategorizedWord(q: string, inCategoryScope: boolean): boolean {
  const ql = q.trim().toLowerCase()
  if (!ql) return false
  const words = ['uncategorized', 'uncategorised', 'unsorted', 'no category']
  if (inCategoryScope) words.push('none')
  return words.some((w) => w.startsWith(ql))
}

/** Offered the moment the empty search box is focused — one click, no syntax. */
const QUICK_PICKS: (FilterToken & { pre: string; disp: string })[] = [
  { ...UNCATEGORIZED_TOKEN, pre: 'Category', disp: 'Uncategorized' },
  { field: 'cleared', value: false, label: 'Uncleared', pre: 'Status', disp: 'Uncleared' },
  { field: 'review', value: true, label: 'Needs review', pre: 'Status', disp: 'Needs review' },
]

export function amountLabel(t: FilterToken, symStr: string): string {
  const dirWord = t.dir === 'out' ? 'Outflow' : t.dir === 'in' ? 'Inflow' : 'Amount'
  const opWord = t.op === '=' ? '=' : t.op === '<=' ? '≤' : '≥'
  return dirWord + ' ' + opWord + ' ' + symStr + t.value
}

export function parseSegment(
  seg: string,
  suggest: SuggestData,
  symStr: string,
): FilterToken | null {
  const s = (seg || '').trim()
  if (!s) return null
  const m = s.match(/^([a-zA-Z][a-zA-Z ]*?):\s*(.*)$/)
  if (m) {
    const f = m[1]!.trim().toLowerCase(),
      val = m[2]!.trim()
    if (val) {
      if (f === 'on' || f === 'date' || f === 'before' || f === 'after') {
        const pd = parseDateInput(val)
        if (pd)
          return (
            dateSuggestions(pd, f === 'date' ? 'on' : (f as 'on' | 'before' | 'after'))[0] ?? null
          )
      }
      if (f === 'category') {
        const c = suggest.categories.find((c) => c.label.toLowerCase() === val.toLowerCase())
        if (c)
          return { field: 'category', value: c.id, label: 'Category: ' + c.label, dot: c.color }
        if (isUncategorizedWord(val, true)) return UNCATEGORIZED_TOKEN
        return { field: 'textcategory', value: val, label: 'Category: "' + val + '"' }
      }
      if (f === 'payee') {
        const p = suggest.payees.find((p) => p.name.toLowerCase() === val.toLowerCase())
        return { field: 'payee', value: p ? p.name : val, label: 'Payee: ' + (p ? p.name : val) }
      }
      if (f === 'account') {
        const a = suggest.accounts.find((a) => a.name.toLowerCase() === val.toLowerCase())
        if (a) return { field: 'account', value: a.id, label: 'Account: ' + a.name }
      }
      if (f === 'tag') {
        const tg = val.replace(/^#/, '')
        return { field: 'tag', value: tg, label: '#' + tg }
      }
      if (f === 'source') {
        const o = SOURCE_OPTS.find((o) => o.value && o.label.toLowerCase() === val.toLowerCase())
        if (o) return { field: 'source', value: o.value, label: 'Source: ' + o.label }
      }
      if (f === 'type') {
        const o = TYPE_OPTS.find(([, l]) => l.toLowerCase() === val.toLowerCase())
        if (o) return { field: 'type', value: o[0], label: 'Type: ' + o[1] }
      }
      if (f === 'note' || f === 'memo')
        return { field: 'textnote', value: val, label: 'Note: "' + val + '"' }
      if (f === 'status' || f === 'cleared') {
        const lv = val.toLowerCase()
        if (lv.startsWith('unclear')) return { field: 'cleared', value: false, label: 'Uncleared' }
        if (lv.startsWith('clear')) return { field: 'cleared', value: true, label: 'Cleared' }
        if (lv.startsWith('need') || lv.startsWith('review'))
          return { field: 'review', value: true, label: 'Needs review' }
        if (lv.startsWith('hid')) return { field: 'hidden', value: true, label: 'Hidden' }
      }
      if (f === 'amount') {
        const n = parseFloat(val.replace(/[^0-9.]/g, ''))
        if (!isNaN(n)) {
          const op = /</.test(val) ? '<=' : /=/.test(val) ? '=' : '>='
          const t = { field: 'amount', value: n, op, dir: 'any', label: '' }
          return { ...t, label: amountLabel(t, symStr) }
        }
      }
    }
  }
  // Bare unambiguous date (a month name or ISO date) commits as an "On" token on Enter.
  // Pure digits stay text/amount (the picker still offers date suggestions for them).
  if (/[a-z]/i.test(s) || /^\d{4}-\d/.test(s)) {
    const pd = parseDateInput(s)
    if (pd) return dateSuggestions(pd, 'on')[0] ?? null
  }
  return { field: 'text', value: s, label: '"' + s + '"' }
}

export function buildSuggestions(
  draft: string,
  suggest: SuggestData,
  symStr: string,
): (FilterToken & { pre?: string; disp?: React.ReactNode })[] {
  const raw = (draft || '').trim()
  if (!raw) return QUICK_PICKS
  let scope: string | null = null,
    q = raw
  let dateMode: 'any' | 'on' | 'before' | 'after' = 'any'
  const m = raw.match(/^([a-zA-Z][a-zA-Z ]*?):\s*(.*)$/)
  if (m) {
    const f = m[1]!.trim().toLowerCase()
    const dateScopes: Record<string, 'on' | 'before' | 'after'> = {
      on: 'on',
      date: 'on',
      before: 'before',
      after: 'after',
    }
    const map: Record<string, string> = {
      category: 'category',
      payee: 'payee',
      tag: 'tag',
      account: 'account',
      source: 'source',
      type: 'type',
      amount: 'amount',
      note: 'note',
      memo: 'note',
      status: 'status',
    }
    if (dateScopes[f] !== undefined) {
      scope = 'date'
      dateMode = dateScopes[f]
      q = m[2]!
    } else if (map[f] !== undefined) {
      scope = map[f]
      q = m[2]!
    }
  }
  const ql = q.trim().toLowerCase()
  const out: (FilterToken & { pre?: string; disp?: React.ReactNode })[] = []
  const want = (f: string) => !scope || scope === f
  if (!scope && q.trim()) {
    const qt = q.trim()
    out.push({
      field: 'text',
      value: qt,
      label: '"' + qt + '"',
      pre: '',
      disp: (
        <>
          Find "<b>{qt}</b>" in any field
        </>
      ),
    })
    out.push({
      field: 'textpayee',
      value: qt,
      label: 'Payee: "' + qt + '"',
      pre: 'Payee',
      disp: (
        <>
          Find "<b>{qt}</b>" in the Payee
        </>
      ),
    })
    out.push({
      field: 'textcategory',
      value: qt,
      label: 'Category: "' + qt + '"',
      pre: 'Category',
      disp: (
        <>
          Find "<b>{qt}</b>" in the Category
        </>
      ),
    })
    out.push({
      field: 'textnote',
      value: qt,
      label: 'Note: "' + qt + '"',
      pre: 'Note',
      disp: (
        <>
          Find "<b>{qt}</b>" in the Note
        </>
      ),
    })
  }
  if (want('date')) {
    const pd = parseDateInput(q.trim())
    if (pd) dateSuggestions(pd, scope === 'date' ? dateMode : 'any').forEach((r) => out.push(r))
  }
  if (want('type'))
    TYPE_OPTS.filter(([, l]) => l.toLowerCase().includes(ql)).forEach(([v, l]) =>
      out.push({ field: 'type', value: v, label: 'Type: ' + l, pre: 'Type', disp: l }),
    )
  if (want('status')) {
    // A bare `status:` lists every status; `status:cleared` narrows like any other scope —
    // listing them all put Needs review first, so Enter on `status:cleared` added it.
    const all = scope === 'status' && !ql
    const reviewHit =
      scope === 'status'
        ? 'needs review'.startsWith(ql) || 'review'.startsWith(ql)
        : 'needs review'.includes(ql)
    if (all || reviewHit)
      out.push({
        field: 'review',
        value: true,
        label: 'Needs review',
        pre: 'Status',
        disp: 'Needs review',
      })
    if (all || (ql && 'cleared'.startsWith(ql)))
      out.push({ field: 'cleared', value: true, label: 'Cleared', pre: 'Status', disp: 'Cleared' })
    if (all || (ql && 'uncleared'.startsWith(ql)))
      out.push({
        field: 'cleared',
        value: false,
        label: 'Uncleared',
        pre: 'Status',
        disp: 'Uncleared',
      })
    if (all || (ql && (scope === 'status' ? 'hidden'.startsWith(ql) : 'hidden'.includes(ql))))
      out.push({ field: 'hidden', value: true, label: 'Hidden', pre: 'Status', disp: 'Hidden' })
  }
  const uncatHit =
    scope === 'category'
      ? !ql || isUncategorizedWord(ql, true)
      : !scope && isUncategorizedWord(ql, false)
  if (uncatHit) out.push({ ...UNCATEGORIZED_TOKEN, pre: 'Category', disp: 'Uncategorized' })
  if (want('amount')) {
    const n = parseFloat(ql.replace(/[^0-9.]/g, ''))
    if (!isNaN(n) && /[0-9]/.test(ql)) {
      const mk = (dir: string, op: string, pre: string) => {
        const opW = op === '=' ? '=' : op === '<=' ? '≤' : '≥'
        out.push({
          field: 'amount',
          value: n,
          op,
          dir,
          pre,
          disp: opW + ' ' + symStr + n,
          label: amountLabel({ field: 'amount', value: n, op, dir, label: '' }, symStr),
        })
      }
      mk('any', '>=', 'Amount')
      mk('any', '<=', 'Amount')
      mk('out', '=', 'Outflow')
      mk('out', '>=', 'Outflow')
      mk('out', '<=', 'Outflow')
      mk('in', '=', 'Inflow')
      mk('in', '>=', 'Inflow')
      mk('in', '<=', 'Inflow')
    }
  }
  if (want('account'))
    suggest.accounts
      .filter((a) => a.name.toLowerCase().includes(ql))
      .slice(0, 6)
      .forEach((a) =>
        out.push({
          field: 'account',
          value: a.id,
          label: 'Account: ' + a.name,
          pre: 'Account',
          disp: a.name,
        }),
      )
  if (want('category'))
    suggest.categories
      .filter((c) => c.label.toLowerCase().includes(ql))
      .slice(0, 8)
      .forEach((c) =>
        out.push({
          field: 'category',
          value: c.id,
          label: 'Category: ' + c.label,
          pre: 'Category',
          disp: c.label,
          dot: c.color,
        }),
      )
  if (want('payee'))
    suggest.payees
      .filter((p) => p.name.toLowerCase().includes(ql))
      .slice(0, 6)
      .forEach((p) =>
        out.push({
          field: 'payee',
          value: p.name,
          label: 'Payee: ' + p.name,
          pre: 'Payee',
          disp: p.name,
        }),
      )
  if (want('tag'))
    suggest.tags
      .filter((t) => t.toLowerCase().includes(ql))
      .slice(0, 6)
      .forEach((t) =>
        out.push({ field: 'tag', value: t, label: '#' + t, pre: 'Tag', disp: '#' + t }),
      )
  if (want('source'))
    SOURCE_OPTS.filter((o) => o.value && o.label.toLowerCase().includes(ql))
      .slice(0, 6)
      .forEach((o) =>
        out.push({
          field: 'source',
          value: o.value,
          label: 'Source: ' + o.label,
          pre: 'Source',
          disp: o.label,
        }),
      )
  return out.slice(0, 22)
}

// ── SearchFilter ──────────────────────────────────────────────────────────

export interface SearchFilterProps {
  tokens: FilterToken[]
  addToken: (t: FilterToken) => void
  removeToken: (i: number) => void
  clearTokens: () => void
  suggest: SuggestData
  sym: string
}

export function SearchFilter({
  tokens,
  addToken,
  removeToken,
  clearTokens,
  suggest,
  sym,
}: SearchFilterProps) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)
  const ref = useRef<HTMLDivElement | null>(null)
  const inpRef = useRef<HTMLInputElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  })
  const sugs = useMemo(() => {
    const all = buildSuggestions(draft, suggest, sym)
    if (draft.trim()) return all
    return all.filter((s) => !tokens.some((t) => t.field === s.field && t.value === s.value))
  }, [draft, suggest, sym, tokens])
  // Nothing is pre-highlighted on an empty box, so a stray Enter adds no filter.
  useEffect(() => {
    setHi(draft.trim() ? 0 : -1)
  }, [draft])
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])
  const commit = (s: (typeof sugs)[number]) => {
    addToken({ field: s.field, value: s.value, op: s.op, dir: s.dir, label: s.label, dot: s.dot })
    setDraft('')
    setHi(-1)
    setOpen(true)
    requestAnimationFrame(() => inpRef.current?.focus())
  }
  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value
    if (v.includes(',')) {
      const parts = v.split(',')
      const last = parts.pop()!
      parts.forEach((seg) => {
        const t = parseSegment(seg, suggest, sym)
        if (t) addToken(t)
      })
      setDraft(last)
    } else setDraft(v)
    setOpen(true)
  }
  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (sugs[hi]) commit(sugs[hi])
      else if (draft.trim()) {
        const t = parseSegment(draft, suggest, sym)
        if (t) addToken(t)
        setDraft('')
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setHi((h) => Math.min(h + 1, sugs.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Escape') setOpen(false)
    else if (e.key === 'Backspace' && draft === '' && tokens.length) {
      e.preventDefault()
      removeToken(tokens.length - 1)
    }
  }
  return (
    <div className="acct-searchwrap" ref={ref}>
      <svg
        className="acct-search-ic"
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="M21 21l-4.3-4.3" />
      </svg>
      <input
        ref={inpRef}
        className="acct-search"
        type="text"
        placeholder={
          tokens.length
            ? 'Add another filter…'
            : 'Search or filter — payee, category, amount, #tag…'
        }
        value={draft}
        onChange={onChange}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
      />
      {(draft || tokens.length > 0) && (
        <button
          className="acct-search-clear"
          aria-label="Clear"
          onMouseDown={(e) => {
            e.preventDefault()
            setDraft('')
            clearTokens()
          }}
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
      {open && sugs.length > 0 && (
        <div className="acct-sug" role="listbox" ref={popRef}>
          {sugs.map((s, i) => (
            <button
              key={s.field + '|' + String(s.value) + '|' + (s.op || '') + '|' + i}
              className={'acct-sug-item' + (i === hi ? ' hi' : '')}
              onMouseEnter={() => setHi(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                commit(s)
              }}
            >
              <span className="acct-sug-lbl">{s.pre}</span>
              <span className="acct-sug-txt">
                {s.dot && <i className="acct-sug-dot" style={{ background: s.dot }} />}
                {s.disp}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Filters (full panel: search + date range) ─────────────────────────────

export interface FiltersProps {
  /** The filtered set's real earliest date (server-computed) — DESCRIBES the 'all' range, never stored. */
  dataStart?: Date | null
  filter: FilterState
  suggest: SuggestData
  sym: string
  addToken: (t: FilterToken) => void
  removeToken: (i: number) => void
  clearTokens: () => void
  setPreset: (v: string) => void
  setCustom: (key: 'cfM' | 'cfY' | 'ctM' | 'ctY', value: number) => void
  onAddTxn: () => void
  /** All-accounts view — decides whether the `account` column is offered in the picker. */
  showAccount: boolean
  /** Register column-visibility model (see columns.tsx). */
  columnVis: ColumnVisibility
  /** Whether hidden transactions are listed — a row toggle in the same picker. */
  showHidden: boolean
  onToggleShowHidden: () => void
}

export function Filters({
  filter,
  dataStart,
  suggest,
  sym,
  addToken,
  removeToken,
  clearTokens,
  setPreset,
  setCustom,
  onAddTxn,
  showAccount,
  columnVis,
  showHidden,
  onToggleShowHidden,
}: FiltersProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const isMobileBp = useBreakpoint() === 'sm'
  const dragHandlers = useSheetDragToDismiss({
    sheetRef,
    overlayRef,
    onDismiss: () => setOpen(false),
    enabled: open && isMobileBp,
  })
  useEffect(() => {
    // NOT on mobile: there the panel is a Radix sheet PORTALLED to <body>, so it is not a DOM
    // descendant of `ref` (.acct-advwrap) and every click inside it — ticking "Custom month
    // range", picking a month — counted as an outside click and dismissed the sheet (owner,
    // 2026-08-24). Radix already owns dismissal there: overlay click, Escape, and the Done
    // button. This handler exists only for the desktop anchored popover.
    if (!open || isMobileBp) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, isMobileBp])
  const monthOpts = FMONTHS.map((m, i) => ({ value: String(i + 1), label: m }))
  const yearOpts = [2026, 2025, 2024, 2023, 2022, 2021].map((y) => ({
    value: String(y),
    label: String(y),
  }))
  const dateActive = filter.preset !== 'all'
  const dateLabel =
    filter.preset === 'custom'
      ? `${FMONTHS[filter.cfM - 1]} ${filter.cfY} – ${FMONTHS[filter.ctM - 1]} ${filter.ctY}`
      : (DATE_PRESETS.find(([v]) => v === filter.preset) ||
          (['all', 'All time'] as [string, string]))[1]
  // MOBILE: a native <select>, not FilterCombo. The combo opens a floating listbox, which
  // inside a bottom sheet covered the whole sheet and spilled above it (owner, 2026-08-24).
  // A native control hands the job to the platform picker — no overlay to position, and it
  // is the interaction people already know on a phone.
  const monthYear = (
    val: number,
    opts: { value: string; label: string }[],
    key: 'cfM' | 'cfY' | 'ctM' | 'ctY',
    anyLabel: string,
    popW: number,
  ) =>
    isMobileBp ? (
      <select
        className="acct-advselect"
        aria-label={anyLabel}
        value={String(val)}
        onChange={(e) => setCustom(key, +e.target.value)}
      >
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    ) : (
      <FilterCombo
        value={String(val)}
        options={opts}
        anyLabel={anyLabel}
        clearable={false}
        popW={popW}
        onChange={(v) => setCustom(key, +v)}
      />
    )

  // While the preset is 'all', the From/To fields DESCRIBE that range rather than drive it:
  // earliest data → today. Derived for display only — writing it into filter state on load
  // counted as a filter change and reset the register's scroll position (REG2). Touching a
  // field still calls setCustom, which flips the preset to 'custom' with that value.
  //
  // "To" is always TODAY, never the real last transaction date. "All time" means "through
  // now" — if the latest transaction happens to be a few days old, showing that stale date
  // as the upper bound reads as if the range stopped, when it hasn't. (The API's `last_date`
  // stays the true last-transaction date — this is a display choice, not a data one.)
  const showAll = filter.preset === 'all'
  const today = new Date()
  const vFromM = showAll && dataStart ? dataStart.getMonth() + 1 : filter.cfM
  const vFromY = showAll && dataStart ? dataStart.getFullYear() : filter.cfY
  const vToM = showAll ? today.getMonth() + 1 : filter.ctM
  const vToY = showAll ? today.getFullYear() : filter.ctY

  const dateBody = (
    <>
      <div className="acct-advbody">
        <div className="acct-advgroup">
          <div className="acct-advsec">Date range</div>
          <div className="acct-advpresets">
            {DATE_PRESETS.map(([v, l]) => (
              <button
                key={v}
                type="button"
                className={'acct-preset' + (filter.preset === v ? ' on' : '')}
                onClick={() => setPreset(v)}
              >
                {l}
              </button>
            ))}
            <button
              type="button"
              className={'acct-preset' + (filter.preset === 'custom' ? ' on' : '')}
              onClick={() => setPreset('custom')}
            >
              Custom
            </button>
          </div>
          {/* Always visible, not gated on preset === 'custom' — picking a month/year here IS
            what makes it custom (setCustom already flips filter.preset itself), so hiding the
            row behind its own result read backwards: you'd have to select "Custom" before you
            could even see what it meant (owner, 2026-08-24). */}
          <div className="acct-advrow">
            <span className="acct-advlbl">From</span>
            {monthYear(vFromM, monthOpts, 'cfM', 'Month', 148)}
            {monthYear(vFromY, yearOpts, 'cfY', 'Year', 120)}
          </div>
          <div className="acct-advrow">
            <span className="acct-advlbl">To</span>
            {monthYear(vToM, monthOpts, 'ctM', 'Month', 148)}
            {monthYear(vToY, yearOpts, 'ctY', 'Year', 120)}
          </div>
        </div>
      </div>
      <div className="acct-advfoot">
        <button className="acct-advclear" onClick={() => setPreset('all')}>
          Reset dates
        </button>
        <button className="acct-advdone" onClick={() => setOpen(false)}>
          Done
        </button>
      </div>
    </>
  )

  return (
    <div className="acct-filters">
      <SearchFilter
        tokens={filter.tokens}
        addToken={addToken}
        removeToken={removeToken}
        clearTokens={clearTokens}
        suggest={suggest}
        sym={sym}
      />
      <div className="acct-advwrap" ref={ref}>
        <button
          className={'acct-advbtn acct-viewbtn' + (dateActive ? ' on' : '')}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`Date range: ${dateLabel}`}
          title={`Date range: ${dateLabel}`}
        >
          <svg
            className="acct-viewcal"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="4.5" width="18" height="17" rx="2.5" />
            <path d="M3 9h18M8 2.5v4M16 2.5v4" />
          </svg>
          <span className="acct-viewlbl">{dateLabel}</span>
          <svg
            className="acct-viewcaret"
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {/* MOBILE: the app's OWN bottom sheet (Radix Dialog.Portal + .mb-sheet-scrim +
            .mb-sheet), the same primitive MobileTabBar and TxnDetail use. It portals to
            <body>, which is what actually matters here: .acct-sticky is `position: sticky;
            z-index: 5` and therefore a STACKING CONTEXT, so a sheet left inside it had its
            z-index scoped within that context and the tab bar painted over its lower half.
            Desktop keeps the anchored popover. */}
        {open &&
          (isMobileBp ? (
            <Dialog.Root
              open
              onOpenChange={(next) => {
                if (!next) setOpen(false)
              }}
            >
              <Dialog.Portal>
                <Dialog.Overlay ref={overlayRef} className="mb-sheet-scrim on" />
                <Dialog.Content
                  ref={sheetRef}
                  className="mb-sheet on acct-viewsheet"
                  aria-describedby={undefined}
                  data-ctx-ignore
                  {...dragHandlers}
                >
                  <div className="mb-sheet-handle-wrap">
                    <span className="mb-sheet-handle" />
                  </div>
                  <div className="mb-sheet-head">
                    <Dialog.Title className="mb-sheet-title">Date range</Dialog.Title>
                  </div>
                  {dateBody}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          ) : (
            <div className="acct-advpop acct-viewpop" role="dialog" aria-label="View options">
              {dateBody}
            </div>
          ))}
      </div>
      <ColumnsPicker
        showAccount={showAccount}
        hidden={columnVis.hidden}
        onToggle={columnVis.toggle}
        onReset={columnVis.reset}
        showHidden={showHidden}
        onToggleShowHidden={onToggleShowHidden}
      />
      <button className="acct-filter-add" onClick={onAddTxn} aria-label="Add transaction">
        <IconPlus size={12} />
        <span className="acct-filter-add-full">Add transaction</span>
        <span className="acct-filter-add-short">Add</span>
      </button>
    </div>
  )
}

// ── ActiveChips ───────────────────────────────────────────────────────────

export interface ActiveChipsProps {
  filter: FilterState
  removeToken: (i: number) => void
  clearAll: () => void
  setPreset: (v: string) => void
  sym: string
}

export function ActiveChips({ filter, removeToken, clearAll }: ActiveChipsProps) {
  const chips = filter.tokens.map((t, i) => ({
    label: t.label,
    dot: t.dot,
    clear: () => removeToken(i),
  }))
  if (!chips.length) return null
  return (
    <div className="acct-chip-row">
      {chips.map((c, i) => (
        <span className="acct-chip" key={i}>
          {c.dot && <i className="acct-chip-dot" style={{ background: c.dot }} />}
          <span className="acct-chip-label">{c.label}</span>
          <button className="acct-chip-x" onClick={c.clear} aria-label={`Remove ${c.label} filter`}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </span>
      ))}
      <button className="acct-chip-clear" onClick={clearAll}>
        Clear all
      </button>
    </div>
  )
}
