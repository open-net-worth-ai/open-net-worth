/**
 * categories.ts — Account-type vocabulary, tag helpers, and provenance metadata.
 *
 * Category data is server-driven (GET /api/categories). The helpers below operate
 * on the server-returned Category[] list and group by kind (Income / Expense) rather
 * than any hardcoded group vocabulary.
 */

import type { Category } from './types'

// ── Category picker helpers ────────────────────────────────────────────────

/**
 * Category options shaped for the AddCategoryField combobox, grouped by kind.
 *
 * A category is KIND-SCOPED (expense | income | transfer | investment — mirrors the backend's
 * `_VALID_KINDS`), so the picker offers categories of the SAME kind as the transaction:
 *   - expense txn → expense categories (group "Expenses")
 *   - transfer txn → transfer categories (group "Transfer")
 *   - investment txn → investment categories (group "Investment")
 *   - income txn → income categories (group "Income") PLUS expense categories as
 *     "Refund to a budget envelope" (an inflow carrying a spending category is a refund).
 */
export function catOptionsFor(
  kind: 'income' | 'expense' | 'transfer' | 'investment',
  catsIn: Category[] = [],
  currentId?: string | null,
): (Category & { group: string })[] {
  // The `split` sentinel is a structural marker a split txn carries, never a pickable category
  // (mirror of STRUCTURAL_CATEGORY_KEYS) — filter it out of every picker so it can't be chosen
  // or shown as a current value.
  const cats = catsIn.filter((c) => c.id !== 'split')
  const opts = kindOptions(kind, cats)
  // The row's OWN category always stays pickable, whatever its kind: the backend accepts e.g. an
  // income "Stock sales" on an investment sale, and a picker that dropped it showed the row as
  // uncategorised (owner report 2026-09-28).
  const current = currentId ? cats.find((c) => c.id === currentId) : undefined
  if (current && !opts.some((c) => c.id === current.id)) {
    opts.push({ ...current, group: KIND_GROUP[current.kind ?? 'expense'] ?? 'Other' })
  }
  return opts
}

const KIND_GROUP: Record<string, string> = {
  expense: 'Expenses',
  income: 'Income',
  transfer: 'Transfer',
  investment: 'Investment',
}

function kindOptions(
  kind: 'income' | 'expense' | 'transfer' | 'investment',
  cats: Category[],
): (Category & { group: string })[] {
  if (kind === 'income') {
    return cats.map((c) => ({
      ...c,
      group: c.kind === 'income' ? 'Income' : 'Refund to a budget envelope',
    }))
  }
  return cats
    .filter((c) => c.kind === kind)
    .map((c) => ({ ...c, group: KIND_GROUP[kind] ?? 'Other' }))
}

/** Same model shaped for a <select> (flat for expense; optgroup for income). */
export function catSelectGroups(
  kind: 'income' | 'expense',
  cats: Category[] = [],
):
  | { value: string; label: string }[]
  | { group: string; options: { value: string; label: string }[] }[] {
  const opts = catOptionsFor(kind, cats)
  if (kind !== 'income') {
    return opts.map((c) => ({ value: c.id, label: c.label }))
  }
  const incomeOpts = opts.filter((c) => c.kind === 'income')
  const refundOpts = opts.filter((c) => c.kind === 'expense')
  const result: { group: string; options: { value: string; label: string }[] }[] = []
  if (incomeOpts.length)
    result.push({
      group: 'Income',
      options: incomeOpts.map((c) => ({ value: c.id, label: c.label })),
    })
  if (refundOpts.length)
    result.push({
      group: 'Refund to a budget envelope',
      options: refundOpts.map((c) => ({ value: c.id, label: c.label })),
    })
  return result
}

/**
 * An inflow carrying a spending category is a refund (contra-expense), not income.
 * Requires the live category map so the lookup is server-authoritative.
 */
export function isRefundTxn(
  kind: string,
  categoryId: string | null | undefined,
  catMap: Record<string, { kind?: string }>,
): boolean {
  // The `split` sentinel is stored as an expense-kind row, but a split inflow lands on its
  // lines' envelopes, never on a "Split" one (it once read "Returned to Split envelope").
  return (
    kind === 'income' &&
    !!categoryId &&
    categoryId !== 'split' &&
    catMap[categoryId]?.kind === 'expense'
  )
}

// ── Account types ─────────────────────────────────────────────────────────
//
// Seven types on one axis (ADR 2026-09-07-seven-account-types-one-axis) — a sidebar "group" IS
// the type; there is no separate group vocabulary. Liquidity order: cash is the most liquid,
// loan the least.

export const ACCOUNT_TYPES = [
  'cash',
  'fixed_deposit',
  'investment',
  'retirement',
  'receivable',
  'credit',
  'loan',
] as const

export type AccountType = (typeof ACCOUNT_TYPES)[number]

export const TYPE_LABELS: Record<AccountType, string> = {
  cash: 'Cash',
  fixed_deposit: 'Fixed deposit',
  investment: 'Investment',
  retirement: 'Retirement',
  receivable: 'Receivable',
  credit: 'Credit',
  loan: 'Loan',
}

export const TYPE_MEANING: Record<AccountType, string> = {
  cash: 'Bank, e-wallet, physical cash · spendable · in budget',
  fixed_deposit: 'Time-locked cash · counts to net worth, not spendable',
  investment: 'Brokerage or crypto exchange · holdings + settlement cash',
  retirement: 'CPF, SRS, pension · locked until retirement',
  receivable: 'Money someone owes you',
  credit: 'Credit card, BNPL, line of credit · paid from cash · in budget',
  loan: 'Mortgage, car, personal, student, margin loan',
}

export const TYPE_COLOR: Record<AccountType, string> = {
  cash: '#34b08a',
  fixed_deposit: '#2ba39a',
  investment: '#3b86dd',
  retirement: '#7c8595',
  receivable: '#d4537e',
  credit: '#e0a032',
  loan: '#e2554f',
}

/** The `AccIcon` glyph for each type — the add-account picker and the connector account list. */
export const TYPE_ICON: Record<AccountType, string> = {
  cash: 'banknote',
  fixed_deposit: 'coins',
  investment: 'trending',
  retirement: 'umbrella',
  receivable: 'incoming',
  credit: 'card',
  loan: 'home',
}

export const LIABILITY_TYPES: ReadonlySet<AccountType> = new Set(['credit', 'loan'])

export const TYPE_ORDER = ACCOUNT_TYPES

/** Client-side derivation of an account's kind from its type — for places that need it before a
 *  server round-trip (the server's own `ApiAccount.kind` is otherwise the source of truth). */
export function kindOf(t: string): 'asset' | 'liability' {
  return LIABILITY_TYPES.has(t as AccountType) ? 'liability' : 'asset'
}

/** `TYPE_LABELS` lookup for a loosely-typed `Account.type` (a plain `string` in the view model,
 *  since the server — not the client — validates the vocabulary) — falls back to the raw value
 *  for anything not in the seven-type vocab. */
export function typeLabel(t: string): string {
  return TYPE_LABELS[t as AccountType] ?? t
}

// ── Tag helpers ───────────────────────────────────────────────────────────

/** Normalize a single tag token: lowercase, hyphenated, trimmed. */
export function slugTag(s: unknown): string {
  return String(s == null ? '' : s)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** Parse a comma-separated tag string into a clean, deduplicated slug array. */
export function normalizeTags(str: string | null | undefined): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  String(str ?? '')
    .split(',')
    .forEach((seg) => {
      const t = slugTag(seg)
      if (t && !seen.has(t)) {
        seen.add(t)
        out.push(t)
      }
    })
  return out
}

/**
 * Live format while typing: lowercase + spaces→hyphen per comma-segment.
 * Leaves a trailing hyphen in place so the next word attaches;
 * edge hyphens are trimmed on blur via normalizeTags.
 */
export function liveTagFormat(str: string | null | undefined): string {
  return String(str ?? '')
    .split(',')
    .map((seg, i) => {
      let s = seg
        .toLowerCase()
        .replace(/[^a-z0-9-]+/g, '-')
        .replace(/-{2,}/g, '-')
      if (i > 0) s = s.replace(/^-+/, '')
      return s
    })
    .join(', ')
}

// ── Provenance vocabulary ─────────────────────────────────────────────────

export interface SourceMeta {
  key: string
  label: string
  addedBy: string
  icon: string
}

export const SOURCE_META: Record<string, SourceMeta> = {
  manual: { key: 'manual', label: 'Manual entry', addedBy: 'You', icon: 'manual' },
  bank: { key: 'bank', label: 'Bank sync', addedBy: 'Bank feed', icon: 'sync' },
  import: { key: 'import', label: 'Imported (file)', addedBy: 'File import', icon: 'import' },
  reconcile: { key: 'reconcile', label: 'Reconcile', addedBy: 'System', icon: 'reconcile' },
  ai: { key: 'ai', label: 'Lucius (AI)', addedBy: 'Lucius', icon: 'lucius' },
  automation: { key: 'automation', label: 'Automation', addedBy: 'Automation', icon: 'automation' },
  agent: { key: 'agent', label: 'Agent', addedBy: 'Agent', icon: 'agent' },
}

export interface ActorTypeMeta {
  icon: string
  trusted: boolean
}

export const ACTOR_TYPE: Record<string, ActorTypeMeta> = {
  you: { icon: 'manual', trusted: true },
  lucius: { icon: 'lucius', trusted: false },
  bank: { icon: 'sync', trusted: true },
  import: { icon: 'import', trusted: false },
  system: { icon: 'reconcile', trusted: true },
  automation: { icon: 'automation', trusted: true },
  agent: { icon: 'agent', trusted: false },
}

export const ACTION_VERB: Record<string, string> = {
  created: 'Created',
  imported: 'Imported',
  reconciled: 'Reconciled',
  updated: 'Edited',
  recategorised: 'Recategorised',
  flagged: 'Flagged for review',
  reviewed: 'Marked reviewed',
  approved: 'Approved',
  rejected: 'Rejected',
  split: 'Split',
  merged: 'Merged',
  reverted: 'Reverted',
  deleted: 'Deleted',
}
