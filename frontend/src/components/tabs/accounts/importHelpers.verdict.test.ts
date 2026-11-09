// The two-question verdict's pure computation (SI-64) + the match picker's pure display
// helper. No React, no wire — plain fixtures against importHelpers exports.

import { describe, it, expect } from 'vitest'
import {
  coverageVerdict,
  amountsVerdict,
  mergeAdoptsStatementFacts,
  matchOptionLine,
  sameFigure,
  linkedAmountDrift,
  offByTitle,
  anchorPerspectiveAmount,
  anchorPerspectiveCurrency,
} from './importHelpers'
import type { VerdictRowInput, ExistingSummaryLike } from './importHelpers'

const row = (over: Partial<VerdictRowInput>): VerdictRowInput => ({
  effect: 'new',
  amount: -10,
  currency: 'SGD',
  ...over,
})

describe('coverageVerdict (question 1 — would everything be in my ledger?)', () => {
  it('all-merge clean book: every row covered, buckets named', () => {
    const v = coverageVerdict([
      row({ effect: 'merge', existing: { amount: -10 } }),
      row({ effect: 'merge', existing: { amount: -5 }, amount: -5 }),
      row({ effect: 'new' }),
    ])
    expect(v).toEqual({
      ok: true,
      total: 3,
      uncovered: 0,
      linked: 2,
      imported: 1,
      dups: 0,
      absorbed: 0,
    })
  })

  it('a skip WITHOUT a resolved match is covered by nothing', () => {
    const v = coverageVerdict([row({ effect: 'skip' }), row({ effect: 'new' })])
    expect(v.ok).toBe(false)
    expect(v.uncovered).toBe(1)
    expect(v.imported).toBe(1)
  })

  it('a skip WITH a resolved match counts as linked — the matched entry represents it', () => {
    const v = coverageVerdict([row({ effect: 'skip', existing: { amount: -9.9 } })])
    expect(v).toEqual({
      ok: true,
      total: 1,
      uncovered: 0,
      linked: 1,
      imported: 0,
      dups: 0,
      absorbed: 0,
    })
  })

  it('dups and absorbed FX legs count as covered, in their own buckets', () => {
    const v = coverageVerdict([row({ effect: 'dup' }), row({ effect: 'absorbed' })])
    expect(v).toEqual({
      ok: true,
      total: 2,
      uncovered: 0,
      linked: 0,
      imported: 0,
      dups: 1,
      absorbed: 1,
    })
  })
})

describe('amountsVerdict (question 2 — would the amounts match?)', () => {
  it('all-merge adopting settled figures: totals match the statement exactly', () => {
    const v = amountsVerdict([
      row({ effect: 'merge', amount: -18.8, existing: { amount: -18.2 }, mergeAdopts: true }),
      row({ effect: 'new', amount: -6.89 }),
    ])
    expect(v.ok).toBe(true)
    expect(v.kept).toBe(0)
    expect(v.totals).toEqual({ SGD: -25.69 })
    expect(v.deltas).toEqual({})
    expect(v.represented).toBe(2)
  })

  it('skip-with-match keeps the EXISTING figure: kept counted, delta = projected − statement', () => {
    const v = amountsVerdict([row({ effect: 'skip', amount: -11.04, existing: { amount: -11.0 } })])
    expect(v.ok).toBe(false)
    expect(v.kept).toBe(1)
    expect(v.totals).toEqual({ SGD: -11.0 })
    expect(v.deltas).toEqual({ SGD: 0.04 })
  })

  it('a non-adopting merge (kind gate) keeps the existing figure too', () => {
    const v = amountsVerdict([
      row({ effect: 'merge', amount: -200, existing: { amount: -200.5 }, mergeAdopts: false }),
    ])
    expect(v.kept).toBe(1)
    expect(v.totals).toEqual({ SGD: -200.5 })
    expect(v.deltas).toEqual({ SGD: -0.5 })
  })

  it('an uncovered skip is EXCLUDED from both sides — question 1 territory, never double-reported', () => {
    const v = amountsVerdict([
      row({ effect: 'skip', amount: -500 }),
      row({ effect: 'new', amount: -3 }),
    ])
    expect(v.ok).toBe(true)
    expect(v.represented).toBe(1)
    expect(v.totals).toEqual({ SGD: -3 })
  })

  it('an existing summary with no readable amount falls back to the statement figure — no fabricated delta', () => {
    const v = amountsVerdict([row({ effect: 'skip', amount: -7, existing: {} })])
    expect(v.ok).toBe(true)
    expect(v.kept).toBe(0)
    expect(v.totals).toEqual({ SGD: -7 })
  })

  it('buckets per currency and prunes zero deltas', () => {
    const v = amountsVerdict([
      row({ effect: 'new', amount: -10, currency: 'SGD' }),
      row({ effect: 'skip', amount: -100000, currency: 'VND', existing: { amount: -99000 } }),
    ])
    expect(v.totals).toEqual({ SGD: -10, VND: -99000 })
    expect(v.deltas).toEqual({ VND: 1000 })
  })

  it('two kept figures that cancel out still fail — kept > 0, deltas empty', () => {
    const v = amountsVerdict([
      row({ effect: 'skip', amount: -10, existing: { amount: -10.5 } }),
      row({ effect: 'skip', amount: -20, existing: { amount: -19.5 } }),
    ])
    expect(v.ok).toBe(false)
    expect(v.kept).toBe(2)
    expect(v.deltas).toEqual({})
  })

  it('dups and absorbed legs ride at their statement figures', () => {
    const v = amountsVerdict([
      row({ effect: 'dup', amount: -4 }),
      row({ effect: 'absorbed', amount: -6 }),
    ])
    expect(v.ok).toBe(true)
    expect(v.totals).toEqual({ SGD: -10 })
  })
})

describe('linkedAmountDrift (the ONE Off-by predicate — owner motivation 2026-07-17)', () => {
  it('a skip leaving a drifted existing standing: {statement, kept, diff = kept − statement}', () => {
    const d = linkedAmountDrift(
      row({ effect: 'skip', amount: -11.04, existing: { amount: -11.0 } }),
    )!
    expect(d.statement).toBe(-11.04)
    expect(d.kept).toBe(-11.0)
    expect(d.diff).toBeCloseTo(0.04)
  })

  it('a non-adopting merge (kind gate) drifts; an adopting merge never does', () => {
    expect(
      linkedAmountDrift(
        row({ effect: 'merge', amount: -200, existing: { amount: -200.5 }, mergeAdopts: false }),
      ),
    ).toEqual({ statement: -200, kept: -200.5, diff: -0.5 })
    expect(
      linkedAmountDrift(
        row({ effect: 'merge', amount: -200, existing: { amount: -200.5 }, mergeAdopts: true }),
      ),
    ).toBeNull()
  })

  it('null for new/dup/absorbed, an uncovered skip, a no-amount existing, and sub-tolerance drift', () => {
    expect(linkedAmountDrift(row({ effect: 'new' }))).toBeNull()
    expect(linkedAmountDrift(row({ effect: 'dup' }))).toBeNull()
    expect(linkedAmountDrift(row({ effect: 'absorbed' }))).toBeNull()
    expect(linkedAmountDrift(row({ effect: 'skip' }))).toBeNull()
    expect(linkedAmountDrift(row({ effect: 'skip', existing: {} }))).toBeNull()
    expect(
      linkedAmountDrift(row({ effect: 'skip', amount: -10, existing: { amount: -10.004 } })),
    ).toBeNull()
  })

  it('anchorPerspectiveAmount picks the statement-account leg — no false transfer drift (two illustrative cases)', () => {
    // Case 1: Lighthouse Bank→QuickPay −200,000 top-up. Statement leg −200,000 (out of
    // Lighthouse Bank). Anchor = Lighthouse Bank (the from side) → the from-leg, signed
    // negative. Raw summary amount was +200,000 (unsigned magnitude) → a false
    // "Off by ₫400,000". Now it matches: drift 0.
    const lhbQuickpay = {
      kind: 'transfer',
      amount: 200000,
      account_id: 'lhb',
      to_account_id: 'quickpay',
      from_amount: 200000,
      from_currency: 'VND',
      to_amount: 200000,
      to_currency: 'VND',
    }
    const kept1 = anchorPerspectiveAmount(lhbQuickpay, 'lhb', -200000)
    const keptCcy1 = anchorPerspectiveCurrency(lhbQuickpay, 'lhb', 'VND')
    expect(kept1).toBe(-200000)
    expect(
      linkedAmountDrift(
        row({
          effect: 'merge',
          amount: -200000,
          currency: 'VND',
          existing: { amount: 200000, currency: 'VND' },
          existingKept: kept1,
          existingKeptCurrency: keptCcy1,
          mergeAdopts: false,
        }),
      ),
    ).toBeNull()

    // Case 2: +2,000,000 VND credit into Lighthouse Bank linked to a Zephyr SGD→Lighthouse
    // Bank transfer. Anchor = Lighthouse Bank (the to side) → the to-leg ₫2,000,000, not the
    // from-leg "S$99.51" (a false "Off by ₫1,999,900"). Now: drift 0, in the statement's own
    // currency.
    const zephyrLhb = {
      kind: 'transfer',
      amount: 99.51,
      account_id: 'zephyr_sgd',
      to_account_id: 'lhb',
      from_amount: 99.51,
      from_currency: 'SGD',
      to_amount: 2000000,
      to_currency: 'VND',
    }
    const kept2 = anchorPerspectiveAmount(zephyrLhb, 'lhb', 2000000)
    const keptCcy2 = anchorPerspectiveCurrency(zephyrLhb, 'lhb', 'VND')
    expect(kept2).toBe(2000000)
    expect(keptCcy2).toBe('VND')
    expect(
      linkedAmountDrift(
        row({
          effect: 'skip',
          amount: 2000000,
          currency: 'VND',
          existing: { amount: 99.51, currency: 'SGD' },
          existingKept: kept2,
          existingKeptCurrency: keptCcy2,
        }),
      ),
    ).toBeNull()
  })

  it('anchorPerspectiveAmount passes non-transfers through; falls back to statement-signed magnitude when legs/anchor missing', () => {
    // Non-transfer: unchanged.
    expect(anchorPerspectiveAmount({ kind: 'expense', amount: -11.04 }, 'lhb', -11.04)).toBe(-11.04)
    // Transfer with no legs on the wire (old cache) + anchor matches neither → sign from
    // the statement figure (the link already guarantees direction agreement).
    expect(
      anchorPerspectiveAmount(
        { kind: 'transfer', amount: 200000, account_id: 'x', to_account_id: 'y' },
        'lhb',
        -200000,
      ),
    ).toBe(-200000)
    // No existing → null.
    expect(anchorPerspectiveAmount(null, 'lhb', -1)).toBeNull()
  })

  it('a still-genuine transfer drift survives the perspective fix', () => {
    // Anchor-side leg (Lighthouse Bank to-leg) is 2,001,700 but the statement says
    // 2,000,000 → a real ₫1,700 drift on the very leg the user cares about. Must NOT be
    // masked.
    const drifted = {
      kind: 'transfer',
      amount: 99.51,
      account_id: 'zephyr_sgd',
      to_account_id: 'lhb',
      from_amount: 99.51,
      from_currency: 'SGD',
      to_amount: 2001700,
      to_currency: 'VND',
    }
    const kept = anchorPerspectiveAmount(drifted, 'lhb', 2000000)
    const keptCcy = anchorPerspectiveCurrency(drifted, 'lhb', 'VND')
    expect(kept).toBe(2001700)
    expect(keptCcy).toBe('VND')
    const d = linkedAmountDrift(
      row({
        effect: 'merge',
        amount: 2000000,
        currency: 'VND',
        existing: { amount: 99.51, currency: 'SGD' },
        existingKept: kept,
        existingKeptCurrency: keptCcy,
        mergeAdopts: false,
      }),
    )!
    expect(d.diff).toBe(1700)
  })

  it('PARITY: rows flagged by linkedAmountDrift ≡ amountsVerdict.kept — same fixtures through both', () => {
    const books: VerdictRowInput[][] = [
      [
        row({ effect: 'merge', amount: -18.8, existing: { amount: -18.2 }, mergeAdopts: true }),
        row({ effect: 'new', amount: -6.89 }),
      ],
      [row({ effect: 'skip', amount: -11.04, existing: { amount: -11.0 } })],
      [row({ effect: 'merge', amount: -200, existing: { amount: -200.5 }, mergeAdopts: false })],
      [row({ effect: 'skip', amount: -500 }), row({ effect: 'new', amount: -3 })],
      [row({ effect: 'skip', amount: -7, existing: {} })],
      [
        row({ effect: 'skip', amount: -10, existing: { amount: -10.5 } }),
        row({ effect: 'skip', amount: -20, existing: { amount: -19.5 } }),
      ],
      [row({ effect: 'dup', amount: -4 }), row({ effect: 'absorbed', amount: -6 })],
      // Sign-flipped transfer_pair merge + a sub-tolerance skip — the gnarly edges tie too.
      [
        row({ effect: 'merge', amount: 100, existing: { amount: -100 }, mergeAdopts: false }),
        row({ effect: 'skip', amount: -10, existing: { amount: -10.004 } }),
      ],
    ]
    for (const book of books) {
      expect(book.filter((r) => linkedAmountDrift(r) !== null).length).toBe(
        amountsVerdict(book).kept,
      )
    }
  })
})

describe('offByTitle (the marker tooltip — honest per effect)', () => {
  it('skip: yours stays, the statement figure is not adopted', () => {
    expect(offByTitle(row({ effect: 'skip', amount: -148.3, existing: { amount: -150 } }))).toBe(
      'Skipped — yours stays -S$150.00; the statement’s -S$148.30 is not adopted.',
    )
  })

  it('kind-gated merge: links them but keeps your figure — never claims Merge adopts', () => {
    expect(
      offByTitle(
        row({ effect: 'merge', amount: -200, existing: { amount: -200.5 }, mergeAdopts: false }),
      ),
    ).toBe(
      'Statement settled -S$200.00 • yours says -S$200.50 — this merge links them but keeps your figure (its legs/kind stay as booked).',
    )
  })

  it('null when there is no drift, OR the kept figure is in a different currency than the statement row', () => {
    expect(offByTitle(row({ effect: 'new' }))).toBeNull()
    // A near-duplicate match normally shares the statement row's own currency (the same
    // real transaction, read twice) — a MISMATCHED currency means the match itself is
    // wrong/coincidental, not a genuine drift. Subtracting across currencies would
    // fabricate a number and mislabel it (regression: a ₫382,450 statement row matched
    // to an unrelated S$18.90 ledger entry showed "Off by ₫382,470" — not a real amount
    // in any currency). No honest single number exists here, so none is shown.
    expect(
      offByTitle(
        row({
          effect: 'skip',
          amount: -10,
          currency: 'SGD',
          existing: { amount: -160000, currency: 'VND' },
        }),
      ),
    ).toBeNull()
  })
})

describe('mergeAdoptsStatementFacts (the kind-gate mirror, one implementation)', () => {
  it('transfer targets and transfer_pair merges never adopt', () => {
    expect(mergeAdoptsStatementFacts('expense', 'transfer', undefined)).toBe(false)
    expect(mergeAdoptsStatementFacts('expense', 'expense', 'transfer_leg_echo')).toBe(false)
    expect(mergeAdoptsStatementFacts('expense', 'income', 'transfer_pair')).toBe(false)
  })

  it('same-kind expense/income merges adopt; cross-kind never; missing kind is same-kind by construction', () => {
    expect(mergeAdoptsStatementFacts('expense', 'expense', 'near_duplicate')).toBe(true)
    expect(mergeAdoptsStatementFacts('income', 'income', undefined)).toBe(true)
    expect(mergeAdoptsStatementFacts('expense', 'income', 'near_duplicate')).toBe(false)
    expect(mergeAdoptsStatementFacts('expense', undefined, 'near_duplicate')).toBe(true)
  })
})

describe('sameFigure (client mirror of the server exact verdict)', () => {
  it('±0.01 absolute floor and 0.1% relative band; null never matches', () => {
    expect(sameFigure(-18.29, -18.3)).toBe(true) // an exact-tier tie under the ±0.01 absolute floor
    expect(sameFigure(14.22, 14.25)).toBe(false)
    expect(sameFigure(100000, 100050)).toBe(true) // 0.05% < 0.1%
    expect(sameFigure(null, 5)).toBe(false)
    expect(sameFigure(5, undefined)).toBe(false)
  })
})

// The picker's option line. Its subject is now a `flags[].alternatives[].existing` summary
// (`ExistingSummaryLike`) rather than a ledger-check row — same projection either way, but the
// server now sends each option's OWN summary so the client never has to look an id up.
// `weakPairEvidence` is gone with the client twin matcher: the client no longer decides which
// entry a row pairs with, so it has no pairing verdict left to phrase.
describe('matchOptionLine (picker option line)', () => {
  const cand: Pick<ExistingSummaryLike, 'date' | 'note' | 'amount' | 'currency'> = {
    date: '2026-06-19',
    note: 'Anthropic Claude API',
    amount: -18.2,
    currency: 'SGD',
  }

  it('renders date • note • amount with the drift suffix vs the statement figure', () => {
    expect(matchOptionLine(cand, -18.8, 'SGD')).toBe(
      '06/19 • Anthropic Claude API • -S$18.20 (3.3% off)',
    )
  })

  it('an in-tolerance figure reads "same amount"', () => {
    expect(matchOptionLine({ ...cand, amount: -18.8 }, -18.8, 'SGD')).toBe(
      '06/19 • Anthropic Claude API • -S$18.80 (same amount)',
    )
  })

  it('carries the option\u2019s own pre-conversion charge — often the only thing telling two near-identical figures apart', () => {
    expect(
      matchOptionLine({ ...cand, original_amount: 10.9, original_currency: 'USD' }, -18.8, 'SGD'),
    ).toBe('06/19 • Anthropic Claude API • -S$18.20 ($10.90) (3.3% off)')
  })

  it('degrades field-by-field — never "undefined", no suffix without a usable amount', () => {
    expect(matchOptionLine({ note: '' }, -18.8, 'SGD')).toBe('—')
    expect(matchOptionLine({ date: '2026-06-19', note: 'x' }, -18.8, 'SGD')).toBe('06/19 • x')
  })
})
