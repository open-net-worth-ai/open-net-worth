// matchEntity.ts — type-synonym search for the @ mention picker.
//
// Implements the algorithm from design_handoffs/handoff_mention_type_search/.
// Single source of truth for TYPE_SYNONYMS, norm, searchHay, typeMatch, matchEntity.
// Import and use in the picker — do NOT scatter type checks elsewhere.

import type { MentionRow } from './mentionRow'
import { descriptorFor, labelFor } from './entityRegistry'
import { pluralize } from '../utils/pluralize'

// Maps each real mention type to its natural-language synonyms (singular, plural, informal).
// Keys are the real backend `type` strings from MentionRow.
// Prototype types mapped to real types: budget→budget_month, scheduled→automation,
// time→returns (closest; period/date synonyms apply).
export const TYPE_SYNONYMS: Record<string, string[]> = {
  account: ['account', 'accounts'],
  holding: [
    'holding',
    'holdings',
    'stock',
    'stocks',
    'position',
    'positions',
    'investment',
    'investments',
    'equity',
    'equities',
    'asset',
    'assets',
  ],
  instrument: ['instrument', 'instruments', 'security', 'securities', 'ticker', 'tickers'],
  transaction: [
    'transaction',
    'transactions',
    'txn',
    'txns',
    'payment',
    'payments',
    'spending',
    'spend',
    'expense',
    'expenses',
    'charge',
    'charges',
    'activity',
  ],
  category: ['category', 'categories'],
  budget_month: ['budget', 'budgets', 'budgetmonth', 'budgetmonths'],
  returns: ['return', 'returns', 'period', 'periods', 'time', 'timerange', 'daterange', 'dates'],
  target: ['target', 'targets'],
  allocation_slice: ['allocation', 'allocations', 'slice', 'slices'],
  net_worth: ['networth', 'networthsummary'],
  payee: ['payee', 'payees', 'merchant', 'merchants', 'vendor', 'vendors'],
  automation: [
    'automation',
    'automations',
    'rule',
    'rules',
    'scheduled',
    'scheduledtask',
    'scheduledtasks',
    'recurring',
    'upcoming',
    'subscription',
    'subscriptions',
    'trigger',
    'triggers',
  ],
  watchlist: ['watchlist', 'watchlists', 'watch'],
  skill: ['skill', 'skills', 'command', 'commands', 'action', 'actions'],
}

/** Normalize a string for comparison: lowercase, strip non-alphanumeric. */
export function norm(s: unknown): string {
  return (s == null ? '' : String(s)).toLowerCase().replace(/[^a-z0-9]+/g, '')
}

// Cache key for precomputed hay — stored as a WeakMap-like property via symbol
// so we never mutate the shared MentionRow object's declared fields.
const _hayKey = Symbol('hay')

interface WithHay {
  [_hayKey]?: string
}

/**
 * Build (and memoize) the multi-field search string for a row.
 * Includes: label, sublabel, value, type, and the numeric amount if parseable from sublabel.
 */
export function searchHay(row: MentionRow): string {
  const r = row as MentionRow & WithHay
  if (r[_hayKey] !== undefined) return r[_hayKey]!

  // Note: row.type is intentionally NOT included — it's structural metadata, not a name
  // field. Type matching is handled separately by typeMatch() after the name-hit check.
  // Including type here would make "ac" hit every account row (type "account" contains "ac").
  const parts: string[] = [row.label, row.sublabel, row.value]
  // Extract amount-like numbers from sublabel (e.g. "S$1,480" → "1480", "1480.00").
  if (row.sublabel) {
    const digits = row.sublabel.replace(/[^0-9.]/g, '')
    if (digits) {
      parts.push(digits)
      // Also push the integer part without decimals for partial matches.
      const intPart = digits.replace(/\..*/, '')
      if (intPart && intPart !== digits) parts.push(intPart)
    }
  }
  const hay = norm(parts.filter(Boolean).join(' '))
  r[_hayKey] = hay
  return hay
}

/**
 * Every word a type answers to: its own name and registry label, singular and plural, then
 * the hand-kept TYPE_SYNONYMS aliases. Derived so a newly registered type is searchable by
 * name without being listed ("@connector" found nothing while it was missing from the table).
 */
export function typeNames(type: string): string[] {
  // Only a registered type answers to its own name — a retired one ("agent") stays silent.
  const d = descriptorFor(type)
  const own = d ? [...new Set([norm(type), norm(d.label)].filter(Boolean))] : []
  const words = new Set<string>()
  for (const w of own) {
    words.add(w)
    words.add(norm(pluralize(w, 2)))
  }
  return [...words, ...(TYPE_SYNONYMS[type] ?? [])]
}

/**
 * True when the normalized query prefixes (or exactly matches) one of the type's names.
 * Requires nq.length >= 3 to prevent short-query flooding.
 * The NAME startsWith the QUERY (so "acc" → "account"), NOT the other way around.
 */
export function typeMatch(type: string, nq: string): boolean {
  if (nq.length < 3) return false
  return typeNames(type).some((s) => s === nq || s.startsWith(nq))
}

/**
 * "payee vietnam airlines" → a payee whose name holds "vietnam airlines": a leading word that
 * names the row's type, then a name. The server's `split_type_qualifier` is the twin rule.
 * A single word is never a qualifier — that is a name, or a whole-type listing ("@payees").
 */
export function qualifiedMatch(row: MentionRow, query: string): boolean {
  const m = /^(\S+)\s+(.*\S)\s*$/.exec(query.trim())
  if (!m) return false
  const rest = norm(m[2])
  return !!rest && typeMatch(row.type, norm(m[1])) && searchHay(row).includes(rest)
}

export interface MatchResult {
  ok: boolean
  hint?: string
}

/**
 * Decide whether a row matches the query, and why.
 * Empty query → everything passes ({ok:true}).
 * Name/field hit (via searchHay) → {ok:true} (with optional hint for amount/category hits).
 * No name hit but type match → {ok:true, hint: "Accounts"} (the type plural label).
 * No match → {ok:false}.
 */
export function matchEntity(row: MentionRow, query: string): MatchResult {
  const nq = norm(query)
  if (!nq) return { ok: true }

  const nameHit = searchHay(row).includes(nq)
  if (!nameHit) {
    if (typeMatch(row.type, nq)) {
      const label = labelFor(row.type)
      return { ok: true, hint: pluralize(label, 2) }
    }
    if (qualifiedMatch(row, query)) return { ok: true }
    return { ok: false }
  }

  // Name hit: check for richer hints (amount or category match on transactions).
  if (row.type === 'transaction') {
    // Amount match: query contains digits and the sublabel has a matching number.
    if (/[0-9]/.test(query)) {
      const numQuery = query.replace(/[^0-9.]/g, '')
      if (numQuery && row.sublabel) {
        const numInSub = row.sublabel.replace(/[^0-9.,]/g, '').replace(/,/g, '')
        if (numInSub.includes(numQuery)) {
          return { ok: true, hint: 'amount • ' + row.sublabel }
        }
      }
    }
  }

  return { ok: true }
}
