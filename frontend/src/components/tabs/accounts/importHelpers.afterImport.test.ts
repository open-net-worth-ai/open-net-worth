// The AFTER IMPORT projection (three-column owner amendment, 2026-07-16) must mirror
// `apply_import_merge` (facts="statement", backend/services/ledger.py) EXACTLY — these pure
// tests pin the per-action outcomes, including the kind-gate cases the backend enforces:
// a transfer target keeps its legs whole; a cross-kind merge (transfer_pair by construction,
// or a cross-kind weak twin) never rewrites date/amount; the original charge is gap-filled
// only when the existing row LACKS one.

import { describe, it, expect } from 'vitest'
import { afterImportProjection, driftPhrase, driftPct } from './importHelpers'
import type { ImportPreviewDraft } from './importHelpers'

const catMap = { cat_subs: { label: 'Subscriptions' } }

const draft = {
  date: '2026-06-18',
  payee: 'ANTHROPIC',
  description: 'ANTHROPIC',
  amount: -18.8,
  currency: 'SGD',
  category: null,
  kind: 'expense',
} as unknown as ImportPreviewDraft

const existing = {
  note: 'Anthropic Claude API',
  date: '2026-06-19',
  amount: -18.2,
  currency: 'SGD',
  kind: 'expense',
  category: 'cat_subs',
}

describe('afterImportProjection', () => {
  it('skip → ONE record, byte-unchanged, labeled "yours stays as-is"', () => {
    const p = afterImportProjection({ action: 'skip', draft, existing, catMap })
    expect(p.count).toBeNull()
    expect(p.records).toHaveLength(1)
    const r = p.records[0]!
    expect(r.tag).toBe('kept')
    expect(r.date).toEqual({ value: '06/19' })
    expect(r.amount).toEqual({ value: '-S$18.20' })
    expect(r.category).toBe('Subscriptions')
    expect(r.badges).toEqual(['yours stays as-is'])
  })

  it('new → TWO records with the honest "2 records" count: existing unchanged + the statement row', () => {
    const p = afterImportProjection({ action: 'new', draft, existing, catMap })
    expect(p.count).toBe('2 records')
    expect(p.records).toHaveLength(2)
    expect(p.records[0]!.tag).toBe('kept')
    expect(p.records[0]!.title).toBe('Anthropic Claude API')
    expect(p.records[1]!.tag).toBe('new')
    expect(p.records[1]!.title).toBe('ANTHROPIC')
    expect(p.records[1]!.date).toEqual({ value: '06/18' })
    expect(p.records[1]!.category).toBe('Uncategorized')
  })

  it('merge (same kind) → ONE record: labels kept, statement date+amount win as old→new deltas, cleared + reference', () => {
    const p = afterImportProjection({
      action: 'merge',
      draft,
      existing,
      flagType: 'near_duplicate',
      catMap,
    })
    expect(p.records).toHaveLength(1)
    const r = p.records[0]!
    expect(r.tag).toBe('updated')
    expect(r.title).toBe('Anthropic Claude API') // label never changes
    expect(r.category).toBe('Subscriptions') // label never changes
    expect(r.date).toEqual({ value: '06/18', old: '06/19' })
    expect(r.amount).toEqual({ value: '-S$18.80', old: '-S$18.20' })
    expect(r.badges).toEqual(['cleared ✓', 'reference attached'])
  })

  it('merge with identical date+amount shows NO deltas (unchanged fields stay quiet)', () => {
    const same = { ...existing, date: '2026-06-18', amount: -18.8 }
    const r = afterImportProjection({
      action: 'merge',
      draft,
      existing: same,
      flagType: 'near_duplicate',
      catMap,
    }).records[0]!
    expect(r.date).toEqual({ value: '06/18' })
    expect(r.amount).toEqual({ value: '-S$18.80' })
  })

  it('merge into a transfer target (echo) → the transfer unchanged + linked ✓, never a rewrite', () => {
    const transfer = {
      note: 'EUR→SGD',
      date: '2026-06-13',
      amount: -200,
      currency: 'SGD',
      kind: 'transfer',
    }
    const p = afterImportProjection({
      action: 'merge',
      draft,
      existing: transfer,
      flagType: 'transfer_leg_echo',
      catMap,
    })
    const r = p.records[0]!
    expect(r.tag).toBe('kept')
    expect(r.date).toEqual({ value: '06/13' })
    expect(r.badges).toEqual(['linked ✓'])
  })

  it('merge on a transfer_pair (opposite kinds by construction) → becomes a real transfer, statement date/amount win its own leg', () => {
    // Neither side is a transfer yet — apply_import_transfer_pair_merge (backend) BUILDS one
    // from the two lone legs; the projection mirrors that instead of the generic "kept at
    // your figures" merge (regression: Link on a transfer_pair silently kept the
    // row as a plain income/expense and never established the transfer relationship).
    const income = {
      note: 'Top-up received',
      date: '2026-06-04',
      amount: 200,
      currency: 'SGD',
      kind: 'income',
    }
    const r = afterImportProjection({
      action: 'merge',
      draft,
      existing: income,
      flagType: 'transfer_pair',
      catMap,
    }).records[0]!
    expect(r.tag).toBe('updated')
    // The two legs genuinely differ (18.80 vs 200) — both show, never one figure standing
    // in for both (a same-currency fee-bearing pair needs this exactly like a cross-currency
    // one does — owner: "if it's cross currency transfer, you need to show two amounts").
    expect(r.amount).toEqual({ value: 'S$18.80 → S$200.00' })
    expect(r.date).toEqual({ value: '06/18', old: '06/04' })
    expect(r.badges).toEqual(['becomes a transfer', 'cleared ✓'])
    // An internal transfer between the owner's own accounts is never a spending category —
    // the old single-leg category (assigned back when this looked like real income) doesn't
    // carry over (owner: "an internal transfer can't be category").
    expect(r.category).toBe('')
  })

  it('merge on a CROSS-CURRENCY transfer_pair shows BOTH legs in their own currency, never one figure', () => {
    const income = {
      note: 'Top-up received',
      date: '2026-06-04',
      amount: 18.9,
      currency: 'SGD',
      kind: 'income',
    }
    const vndDraft = { ...draft, amount: -382450, currency: 'VND' } as ImportPreviewDraft
    const r = afterImportProjection({
      action: 'merge',
      draft: vndDraft,
      existing: income,
      flagType: 'transfer_pair',
      catMap,
    }).records[0]!
    expect(r.amount).toEqual({ value: '₫382,450 → S$18.90' })
  })

  it('merge onto a CROSS-KIND weak twin (no flag type) also never rewrites date/amount — the backend kind gate mirrored', () => {
    const income = {
      note: 'Refund',
      date: '2026-06-04',
      amount: 18.8,
      currency: 'SGD',
      kind: 'income',
    }
    const r = afterImportProjection({ action: 'merge', draft, existing: income, catMap })
      .records[0]!
    expect(r.tag).toBe('kept')
    expect(r.date).toEqual({ value: '06/04' })
    expect(r.badges).toEqual(['cleared ✓', 'reference linked'])
  })

  it('gap-fills the original charge ONLY when the existing row lacks one', () => {
    const d = { ...draft, original_amount: 10.95, original_currency: 'USD' } as ImportPreviewDraft
    const filled = afterImportProjection({
      action: 'merge',
      draft: d,
      existing,
      flagType: 'near_duplicate',
      catMap,
    }).records[0]!
    expect(filled.original).toEqual({ value: '$10.95', added: true })
    const has = { ...existing, original_amount: 10.5, original_currency: 'USD' }
    const kept = afterImportProjection({
      action: 'merge',
      draft: d,
      existing: has,
      flagType: 'near_duplicate',
      catMap,
    }).records[0]!
    expect(kept.original).toEqual({ value: '$10.50', added: false })
  })
})

// `weakEvidence`/`daysApart` retired with the client-side twin matcher (RFC 2026-07-29 §D8):
// the client no longer OWNS the "are these two the same" verdict, so there is nothing left for
// it to phrase. What survives is the drift arithmetic over two figures already on screen —
// shared by the weak suggestion line and the match picker's option lines, so they can never
// quote different numbers for the same pair.
describe('driftPct / driftPhrase', () => {
  it('settled % off the ledger figure', () => {
    expect(driftPct(18.8, -18.2)!.toFixed(1)).toBe('3.3')
    expect(driftPhrase(-18.8, -18.2)).toBe('3.3% off')
  })

  it('an in-tolerance pair reads "same amount"', () => {
    expect(driftPhrase(-18.8, -18.8)).toBe('same amount')
    expect(driftPhrase(-18.29, -18.3)).toBe('same amount')
  })

  it('degrades without usable amounts — never NaN, never "undefined"', () => {
    expect(driftPct(18.8, null)).toBeNull()
    expect(driftPhrase(-18.8, null)).toBeNull()
    expect(driftPhrase(null, -18.2)).toBeNull()
    expect(driftPhrase(-18.8, 0)).toBeNull()
  })
})
