/**
 * tokenFilters.ts — pure translation of SearchFilter's FilterToken list into the
 * server-side TxnFilters shape (REG1: the register used to match every token
 * EXCEPT account/date CLIENT-SIDE against only the currently loaded page, so the
 * "N of TOTAL" footer and a filtered result set could silently disagree once the
 * ledger grew past one page. Every token is now a real query param.)
 *
 * Kept as pure functions (no React) so the token → filter mapping is unit-testable
 * without rendering the (large) AccountsTab orchestrator.
 */

import type { FilterToken } from './SearchFilter'
import type { TxnAmountFilter, TxnFilters, TxnQField } from './types'

// ── Token-list commit (the one place new tokens are appended) ────────────────

/** The free-text family — text/textpayee/textnote/textcategory all fold into the
 * SAME single `search`+`qField` pair server-side (see `tokensToFilterPatch` below),
 * so at most one of them may ever be active at once. Without this, committing a
 * second text-family chip (e.g. the comma-shorthand `payee:grab, note:lunch`) would
 * render TWO active chips while only the last one actually reached the query — a
 * chip that lies about filtering, the exact defect class REG1 exists to kill. */
const TEXT_FAMILY_FIELDS = new Set(['text', 'textpayee', 'textnote', 'textcategory'])

/** Single-valued fields: the server takes ONE `cleared` boolean, so a Cleared chip
 * beside an Uncleared one rendered both while only the last reached the query. A new
 * token on such a field replaces the old one instead of stacking beside it. */
const SINGLE_VALUE_FIELDS = new Set(['cleared'])

/** Two tokens are the SAME filter — dedup key, not display label (the label can
 * differ for an otherwise-identical amount token's rendering, but field+value+op+dir
 * is what actually reaches the query). */
const tokenKey = (t: FilterToken) => `${t.field}|${String(t.value)}|${t.op ?? ''}|${t.dir ?? ''}`

/** Append `next` to `tokens`, replacing any existing text-family token when `next`
 * is itself text-family (every other field just OR-combines at query time, so it's
 * safe to keep appending). An exact repeat of an already-active token (e.g. clicking
 * a payee that's already filtered) is a no-op rather than a second identical chip
 * that silently OR-combines with itself. Use this everywhere a token is committed. */
export function addFilterToken(tokens: FilterToken[], next: FilterToken): FilterToken[] {
  if (TEXT_FAMILY_FIELDS.has(next.field)) {
    return [...tokens.filter((t) => !TEXT_FAMILY_FIELDS.has(t.field)), next]
  }
  if (SINGLE_VALUE_FIELDS.has(next.field)) {
    return [...tokens.filter((t) => t.field !== next.field), next]
  }
  if (tokens.some((t) => tokenKey(t) === tokenKey(next))) return tokens
  return [...tokens, next]
}

// ── OR-field token → TxnFilters (category/payee/tag/account/source/type + text/status) ──

export interface TokenFilterCtx {
  /** Resolve a payee's display NAME (as carried on a 'payee' token) to its id. */
  resolvePayeeId: (name: string) => string | null
}

export type TokenFilterPatch = Pick<
  TxnFilters,
  | 'category'
  | 'payeeIds'
  | 'tags'
  | 'accountIds'
  | 'source'
  | 'kind'
  | 'amounts'
  | 'search'
  | 'qField'
  | 'cleared'
  | 'needsReview'
  | 'uncategorized'
  | 'batchId'
  | 'hiddenOnly'
>

/** Translate `filter.tokens` into the server-side multi-value filter fields.
 *
 * Date tokens (date_on/date_after/date_before) are handled separately by
 * `combineDateRange` — they intersect with the preset date range rather than
 * becoming their own filter field.
 *
 * Free-text tokens (text/textpayee/textnote/textcategory) map to a SINGLE
 * `search`+`qField` pair — the server contract is one text search, not a list.
 * In practice at most one is ever present (`addFilterToken` above replaces any
 * existing text-family token on commit); if this is ever called with more than
 * one anyway, the last one wins — never silently OR/AND two searches. */
export function tokensToFilterPatch(tokens: FilterToken[], ctx: TokenFilterCtx): TokenFilterPatch {
  const category: string[] = []
  const payeeIds: string[] = []
  const tags: string[] = []
  const accountIds: string[] = []
  const source: string[] = []
  const kind: string[] = []
  const amounts: TxnAmountFilter[] = []
  let search = ''
  let qField: TxnQField = 'any'
  let cleared: boolean | null = null
  let needsReview = false
  let hiddenOnly = false
  let uncategorized = false
  let batchId: string | null = null

  const pushOnce = (arr: string[], v: string) => {
    if (!arr.includes(v)) arr.push(v)
  }

  for (const t of tokens) {
    switch (t.field) {
      case 'category':
        pushOnce(category, String(t.value))
        break
      case 'payee':
        pushOnce(payeeIds, ctx.resolvePayeeId(String(t.value)) ?? String(t.value))
        break
      case 'tag':
        pushOnce(tags, String(t.value))
        break
      case 'account':
        pushOnce(accountIds, String(t.value))
        break
      case 'source':
        pushOnce(source, String(t.value))
        break
      case 'type':
        pushOnce(kind, String(t.value))
        break
      case 'amount':
        amounts.push({
          op: (t.op as TxnAmountFilter['op']) ?? '>=',
          dir: (t.dir as TxnAmountFilter['dir']) ?? 'any',
          value: Number(t.value),
        })
        break
      case 'review':
        needsReview = true
        break
      case 'hidden':
        hiddenOnly = true
        break
      case 'cleared':
        cleared = !!t.value
        break
      case 'uncategorized':
        uncategorized = true
        break
      case 'importBatch':
        batchId = String(t.value)
        break
      case 'text':
        search = String(t.value)
        qField = 'any'
        break
      case 'textpayee':
        search = String(t.value)
        qField = 'payee'
        break
      case 'textnote':
        search = String(t.value)
        qField = 'note'
        break
      case 'textcategory':
        search = String(t.value)
        qField = 'category'
        break
      default:
        break // date_on/date_after/date_before — see combineDateRange
    }
  }
  return {
    category,
    payeeIds,
    tags,
    accountIds,
    source,
    kind,
    amounts,
    search,
    qField,
    cleared,
    needsReview,
    uncategorized,
    batchId,
    hiddenOnly,
  }
}

// ── Date tokens (AND-intersect with the preset range) ─────────────────────

const _pad = (n: number) => String(n).padStart(2, '0')

/** Derive the [start, end] ISO day range a date_on token's ISO PREFIX covers —
 * a day (YYYY-MM-DD), a month (YYYY-MM), or a year (YYYY) prefix. Returns null
 * for anything else (defensive; every token the picker emits matches one of these). */
export function dateRangeFromPrefix(prefix: string): { start: string; end: string } | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(prefix)) return { start: prefix, end: prefix }
  const monthMatch = prefix.match(/^(\d{4})-(\d{2})$/)
  if (monthMatch) {
    const y = Number(monthMatch[1] ?? 0),
      mo = Number(monthMatch[2] ?? 0)
    const lastDay = new Date(y, mo, 0).getDate()
    return { start: `${prefix}-01`, end: `${prefix}-${_pad(lastDay)}` }
  }
  if (/^\d{4}$/.test(prefix)) return { start: `${prefix}-01-01`, end: `${prefix}-12-31` }
  return null
}

function tightenLower(a: string, b: string): string {
  return !a ? b : !b ? a : a > b ? a : b
}
function tightenUpper(a: string, b: string): string {
  return !a ? b : !b ? a : a < b ? a : b
}

/** Intersect the preset-derived [presetFrom, presetTo] range (either may be ''
 * = unbounded) with every date_on/date_after/date_before token — AND semantics,
 * exactly how the register builds a custom range from chip tokens. */
export function combineDateRange(
  presetFrom: string,
  presetTo: string,
  tokens: FilterToken[],
): { dateFrom: string; dateTo: string } {
  let dateFrom = presetFrom
  let dateTo = presetTo
  for (const t of tokens) {
    if (t.field === 'date_on') {
      const r = dateRangeFromPrefix(String(t.value))
      if (r) {
        dateFrom = tightenLower(dateFrom, r.start)
        dateTo = tightenUpper(dateTo, r.end)
      }
    } else if (t.field === 'date_after') {
      dateFrom = tightenLower(dateFrom, String(t.value))
    } else if (t.field === 'date_before') {
      dateTo = tightenUpper(dateTo, String(t.value))
    }
  }
  return { dateFrom, dateTo }
}
