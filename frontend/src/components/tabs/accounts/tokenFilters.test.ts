/**
 * REG1 regression coverage: the register used to translate every SearchFilter token
 * EXCEPT account/date into a CLIENT-SIDE match against only the currently loaded page
 * (AccountsTab.tsx's old txMatchesTokens), while the "N of TOTAL" footer kept showing
 * the server's UNFILTERED total — so a filter with real matches beyond the first page
 * silently under-reported ("1 of 1184" for a payee with 8 real matches). These tests
 * prove a committed token now becomes a real server-side TxnFilters field / query
 * param, not a client-only match.
 */
import { describe, it, expect } from 'vitest'
import {
  tokensToFilterPatch,
  dateRangeFromPrefix,
  combineDateRange,
  addFilterToken,
} from './tokenFilters'
import { buildTxnQueryString } from './useAccountsLedger'
import type { FilterToken } from './SearchFilter'
import type { TxnFilters } from './types'

const noResolve = { resolvePayeeId: () => null }

function baseFilters(patch: Partial<TxnFilters> = {}): TxnFilters {
  return {
    accountId: null,
    accountIds: [],
    search: '',
    qField: 'any',
    kind: [],
    category: [],
    payeeIds: [],
    tags: [],
    source: [],
    dateFrom: '',
    dateTo: '',
    cleared: null,
    needsReview: false,
    amounts: [],
    uncategorized: false,
    batchId: null,
    showHidden: false,
    hiddenOnly: false,
    ...patch,
  }
}

describe('addFilterToken — Cleared and Uncleared are one choice', () => {
  const cleared: FilterToken = { field: 'cleared', value: true, label: 'Cleared' }
  const uncleared: FilterToken = { field: 'cleared', value: false, label: 'Uncleared' }

  it('adding Uncleared replaces Cleared (both chips showed, only the last filtered)', () => {
    const tokens = addFilterToken(addFilterToken([], cleared), uncleared)
    expect(tokens).toEqual([uncleared])
    expect(tokensToFilterPatch(tokens, { resolvePayeeId: () => null }).cleared).toBe(false)
  })

  it('replacing keeps every other filter', () => {
    const review: FilterToken = { field: 'review', value: true, label: 'Needs review' }
    expect(addFilterToken([review, uncleared], cleared)).toEqual([review, cleared])
  })
})

describe('addFilterToken — at most one active text-family chip', () => {
  it('a second text-family token replaces the first, never stacks', () => {
    const t1: FilterToken = { field: 'textpayee', value: 'grab', label: 'Payee: "grab"' }
    const t2: FilterToken = { field: 'textnote', value: 'lunch', label: 'Note: "lunch"' }
    // The comma-shorthand parser makes "payee:grab, note:lunch" commit both of
    // these in sequence — only ONE may ever end up active.
    const tokens = addFilterToken(addFilterToken([], t1), t2)
    expect(tokens).toEqual([t2])
  })

  it('replacing works across every text-family field combination', () => {
    const families: FilterToken['field'][] = ['text', 'textpayee', 'textnote', 'textcategory']
    for (const first of families) {
      for (const second of families) {
        const t1: FilterToken = { field: first, value: 'a', label: '' }
        const t2: FilterToken = { field: second, value: 'b', label: '' }
        expect(addFilterToken([t1], t2)).toEqual([t2])
      }
    }
  })

  it('a non-text token never evicts an existing text-family chip', () => {
    const textTok: FilterToken = { field: 'text', value: 'grab', label: '"grab"' }
    const catTok: FilterToken = { field: 'category', value: 'food', label: 'Category: Food' }
    expect(addFilterToken([textTok], catTok)).toEqual([textTok, catTok])
  })

  it('non-text tokens keep accumulating normally (OR-multi, no dedup)', () => {
    const a: FilterToken = { field: 'category', value: 'food', label: '' }
    const b: FilterToken = { field: 'category', value: 'transport', label: '' }
    expect(addFilterToken([a], b)).toEqual([a, b])
  })

  it('an exact repeat of an already-active token is a no-op, not a second identical chip', () => {
    // Reported: clicking a payee that's already filtered (e.g. via viewPayee) rendered
    // two "Payee: MetaPay" chips side by side instead of leaving the one active filter.
    const a: FilterToken = { field: 'payee', value: 'MetaPay', label: 'Payee: MetaPay' }
    const b: FilterToken = { field: 'payee', value: 'MetaPay', label: 'Payee: MetaPay' }
    expect(addFilterToken([a], b)).toEqual([a])
  })

  it('an amount token only dedups when field, value, op AND dir all match', () => {
    const gte: FilterToken = { field: 'amount', value: 50, op: '>=', dir: 'any', label: '' }
    const lte: FilterToken = { field: 'amount', value: 50, op: '<=', dir: 'any', label: '' }
    expect(addFilterToken([gte], lte)).toEqual([gte, lte])
    expect(addFilterToken([gte], { ...gte })).toEqual([gte])
  })

  it('resulting token list only ever carries ONE text-family field into the translator', () => {
    // End-to-end: simulate the exact reported comma-shorthand scenario ("payee:grab,
    // note:lunch" committing a textpayee token then a textnote token) and confirm the
    // translated patch reflects only the winning (last) text-family token — never a
    // chip that renders as active but is silently dropped from the query.
    const afterPayee = addFilterToken([], {
      field: 'textpayee',
      value: 'grab',
      label: 'Payee: "grab"',
    })
    const afterNote = addFilterToken(afterPayee, {
      field: 'textnote',
      value: 'lunch',
      label: 'Note: "lunch"',
    })
    expect(afterNote).toHaveLength(1)
    const patch = tokensToFilterPatch(afterNote, noResolve)
    expect(patch.search).toBe('lunch')
    expect(patch.qField).toBe('note')
  })
})

describe('tokensToFilterPatch — OR-multi fields', () => {
  it('a category token becomes a server-side category filter (not a client match)', () => {
    const tokens: FilterToken[] = [{ field: 'category', value: 'food', label: 'Category: Food' }]
    expect(tokensToFilterPatch(tokens, noResolve).category).toEqual(['food'])
  })

  it('two category tokens OR together', () => {
    const tokens: FilterToken[] = [
      { field: 'category', value: 'food', label: 'Category: Food' },
      { field: 'category', value: 'transport', label: 'Category: Transport' },
    ]
    expect(tokensToFilterPatch(tokens, noResolve).category).toEqual(['food', 'transport'])
  })

  it('a payee token resolves the display NAME to an id before it is sent', () => {
    const resolve = { resolvePayeeId: (name: string) => (name === 'Onwai' ? 'pay_onwai' : null) }
    const tokens: FilterToken[] = [{ field: 'payee', value: 'Onwai', label: 'Payee: Onwai' }]
    expect(tokensToFilterPatch(tokens, resolve).payeeIds).toEqual(['pay_onwai'])
  })

  it('an unresolvable payee name falls back to the raw string (matches nothing server-side, never a no-op)', () => {
    const tokens: FilterToken[] = [
      { field: 'payee', value: 'Ghost Payee', label: 'Payee: Ghost Payee' },
    ]
    expect(tokensToFilterPatch(tokens, noResolve).payeeIds).toEqual(['Ghost Payee'])
  })

  it('a tag token becomes a server-side tag filter', () => {
    const tokens: FilterToken[] = [{ field: 'tag', value: 'reimbursable', label: '#reimbursable' }]
    expect(tokensToFilterPatch(tokens, noResolve).tags).toEqual(['reimbursable'])
  })

  it('account/source/type tokens map to their OR-multi fields', () => {
    const tokens: FilterToken[] = [
      { field: 'account', value: 'acc_meridian', label: 'Account: Meridian' },
      { field: 'source', value: 'import', label: 'Source: Imported (file)' },
      { field: 'type', value: 'expense', label: 'Type: Expense' },
    ]
    const patch = tokensToFilterPatch(tokens, noResolve)
    expect(patch.accountIds).toEqual(['acc_meridian'])
    expect(patch.source).toEqual(['import'])
    expect(patch.kind).toEqual(['expense'])
  })

  it('review/cleared tokens set the tri-state flags', () => {
    expect(
      tokensToFilterPatch([{ field: 'review', value: true, label: 'Needs review' }], noResolve)
        .needsReview,
    ).toBe(true)
    expect(
      tokensToFilterPatch([{ field: 'cleared', value: true, label: 'Cleared' }], noResolve).cleared,
    ).toBe(true)
    expect(
      tokensToFilterPatch([{ field: 'cleared', value: false, label: 'Uncleared' }], noResolve)
        .cleared,
    ).toBe(false)
  })

  // Statement Import's "View imported" deep-link.
  it('an importBatch token becomes the batch-id filter, and is null when absent', () => {
    expect(
      tokensToFilterPatch(
        [{ field: 'importBatch', value: 'imp-1', label: 'This import' }],
        noResolve,
      ).batchId,
    ).toBe('imp-1')
    expect(tokensToFilterPatch([], noResolve).batchId).toBeNull()
  })
})

describe('tokensToFilterPatch — free text (single search + qField)', () => {
  it('a text token searches "any" field', () => {
    const patch = tokensToFilterPatch(
      [{ field: 'text', value: 'grab', label: '"grab"' }],
      noResolve,
    )
    expect(patch.search).toBe('grab')
    expect(patch.qField).toBe('any')
  })
  it('textpayee/textnote/textcategory scope qField', () => {
    expect(
      tokensToFilterPatch([{ field: 'textpayee', value: 'grab', label: '' }], noResolve).qField,
    ).toBe('payee')
    expect(
      tokensToFilterPatch([{ field: 'textnote', value: 'lunch', label: '' }], noResolve).qField,
    ).toBe('note')
    expect(
      tokensToFilterPatch([{ field: 'textcategory', value: 'dining', label: '' }], noResolve)
        .qField,
    ).toBe('category')
  })
})

describe('tokensToFilterPatch — amount constraints', () => {
  it('an amount token becomes an AND-composable constraint with defaults', () => {
    const tokens: FilterToken[] = [
      { field: 'amount', value: 100, op: '>=', dir: 'out', label: 'Outflow ≥ $100' },
    ]
    expect(tokensToFilterPatch(tokens, noResolve).amounts).toEqual([
      { op: '>=', dir: 'out', value: 100 },
    ])
  })
  it('two amount tokens both survive (a range: >= X AND <= Y)', () => {
    const tokens: FilterToken[] = [
      { field: 'amount', value: 100, op: '>=', dir: 'any', label: '' },
      { field: 'amount', value: 500, op: '<=', dir: 'any', label: '' },
    ]
    expect(tokensToFilterPatch(tokens, noResolve).amounts).toEqual([
      { op: '>=', dir: 'any', value: 100 },
      { op: '<=', dir: 'any', value: 500 },
    ])
  })
  it('missing op/dir default to >= / any', () => {
    const patch = tokensToFilterPatch([{ field: 'amount', value: 42, label: '' }], noResolve)
    expect(patch.amounts).toEqual([{ op: '>=', dir: 'any', value: 42 }])
  })
})

describe('dateRangeFromPrefix', () => {
  it('a day prefix is a single-day range', () => {
    expect(dateRangeFromPrefix('2026-06-15')).toEqual({ start: '2026-06-15', end: '2026-06-15' })
  })
  it('a month prefix spans the whole month', () => {
    expect(dateRangeFromPrefix('2026-06')).toEqual({ start: '2026-06-01', end: '2026-06-30' })
  })
  it('a year prefix spans Jan 1 – Dec 31', () => {
    expect(dateRangeFromPrefix('2026')).toEqual({ start: '2026-01-01', end: '2026-12-31' })
  })
  it('junk returns null', () => {
    expect(dateRangeFromPrefix('not-a-date')).toBeNull()
  })
})

describe('combineDateRange — AND-intersect with the preset range', () => {
  it('no date tokens: the preset range passes through unchanged', () => {
    expect(combineDateRange('2026-01-01', '2026-12-31', [])).toEqual({
      dateFrom: '2026-01-01',
      dateTo: '2026-12-31',
    })
  })
  it('a date_on token narrows an unbounded (all-time) preset', () => {
    const tokens: FilterToken[] = [{ field: 'date_on', value: '2026-06', label: 'In Jun 2026' }]
    expect(combineDateRange('', '', tokens)).toEqual({
      dateFrom: '2026-06-01',
      dateTo: '2026-06-30',
    })
  })
  it('date_after / date_before tighten the bounds (AND, never widen)', () => {
    const tokens: FilterToken[] = [
      { field: 'date_after', value: '2026-03-01', label: '' },
      { field: 'date_before', value: '2026-03-15', label: '' },
    ]
    // Preset is the whole year; the two chip tokens narrow it to March 1–15.
    expect(combineDateRange('2026-01-01', '2026-12-31', tokens)).toEqual({
      dateFrom: '2026-03-01',
      dateTo: '2026-03-15',
    })
  })
  it('a date token narrower than the preset wins (intersection, not replacement)', () => {
    const tokens: FilterToken[] = [{ field: 'date_after', value: '2026-06-10', label: '' }]
    // Preset already starts 2026-06-01; the chip's 2026-06-10 is the tighter (later) lower bound.
    expect(combineDateRange('2026-06-01', '2026-06-30', tokens).dateFrom).toBe('2026-06-10')
  })
})

// ── End-to-end: token → patch → the exact query string sent to the server ────────

describe('token → TxnFilters → query string (server-side filtering wire format)', () => {
  it('a payee token results in a payee_id query param, not a client-only match', () => {
    const resolve = { resolvePayeeId: () => 'pay_onwai' }
    const patch = tokensToFilterPatch([{ field: 'payee', value: 'Onwai', label: '' }], resolve)
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('payee_id=pay_onwai')
  })

  it('a category token results in a category query param', () => {
    const patch = tokensToFilterPatch([{ field: 'category', value: 'food', label: '' }], noResolve)
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('category=food')
  })

  // REG-repeated-params: kind/category/account_ids/tag/payee_id/source are Annotated
  // `List[str]` Query params server-side (backend/api/ledger.py) — a single comma-joined
  // value arrives as ONE literal string (never split), so a multi-select filter silently
  // matched nothing past the first pick. Every OR-multi field must emit REPEATED params.
  it('two OR-combined tag tokens serialize as REPEATED tag params, never comma-joined', () => {
    const patch = tokensToFilterPatch(
      [
        { field: 'tag', value: 'reimbursable', label: '' },
        { field: 'tag', value: 'payroll', label: '' },
      ],
      noResolve,
    )
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    const params = new URLSearchParams(qs)
    expect(params.getAll('tag')).toEqual(['reimbursable', 'payroll'])
    expect(qs).not.toContain(encodeURIComponent('reimbursable,payroll'))
  })

  it('two OR-combined category tokens also serialize as repeated params', () => {
    const patch = tokensToFilterPatch(
      [
        { field: 'category', value: 'food', label: '' },
        { field: 'category', value: 'transport', label: '' },
      ],
      noResolve,
    )
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(new URLSearchParams(qs).getAll('category')).toEqual(['food', 'transport'])
  })

  it('an uncategorized token results in uncategorized=true (Plan tab "Review in Accounts")', () => {
    const patch = tokensToFilterPatch(
      [{ field: 'uncategorized', value: true, label: 'Uncategorized' }],
      noResolve,
    )
    expect(patch.uncategorized).toBe(true)
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('uncategorized=true')
  })

  it('a free-text token sends both q and q_field', () => {
    const patch = tokensToFilterPatch([{ field: 'textpayee', value: 'grab', label: '' }], noResolve)
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('q=grab')
    expect(qs).toContain('q_field=payee')
  })

  it('an amount range (two tokens) serializes as one comma-joined op:dir:value list', () => {
    const patch = tokensToFilterPatch(
      [
        { field: 'amount', value: 100, op: '>=', dir: 'any', label: '' },
        { field: 'amount', value: 500, op: '<=', dir: 'out', label: '' },
      ],
      noResolve,
    )
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain(`amount=${encodeURIComponent('>=:any:100,<=:out:500')}`)
  })

  it('an importBatch token results in an batch_id query param (Statement Import "View imported")', () => {
    const patch = tokensToFilterPatch(
      [{ field: 'importBatch', value: 'imp-42', label: 'This import' }],
      noResolve,
    )
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('batch_id=imp-42')
  })

  it('cleared/needs_review tokens serialize as their own params', () => {
    const patch = tokensToFilterPatch(
      [
        { field: 'cleared', value: false, label: '' },
        { field: 'review', value: true, label: '' },
      ],
      noResolve,
    )
    const qs = buildTxnQueryString(baseFilters(patch), 0)
    expect(qs).toContain('cleared=false')
    expect(qs).toContain('needs_review=true')
  })

  it('an unfiltered TxnFilters sends no token-derived params (just pagination)', () => {
    const qs = buildTxnQueryString(baseFilters(), 0)
    expect(qs).toBe('hidden=false&limit=75&offset=0')
  })

  it('hidden rows are left out unless Show hidden is on', () => {
    expect(buildTxnQueryString(baseFilters(), 0)).toContain('hidden=false')
    expect(buildTxnQueryString(baseFilters({ showHidden: true }), 0)).not.toContain('hidden')
  })

  it('a Status: Hidden token lists ONLY hidden rows, whatever Show hidden says', () => {
    const tokens: FilterToken[] = [{ field: 'hidden', value: true, label: 'Hidden' }]
    const patch = tokensToFilterPatch(tokens, noResolve)
    expect(patch.hiddenOnly).toBe(true)
    expect(buildTxnQueryString(baseFilters(patch), 0)).toContain('hidden=true')
    expect(buildTxnQueryString(baseFilters({ ...patch, showHidden: true }), 0)).toContain(
      'hidden=true',
    )
  })
})
