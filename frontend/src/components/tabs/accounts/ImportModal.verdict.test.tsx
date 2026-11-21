// @vitest-environment jsdom
//
// SI-64 — the two-question verdict (owner's words: "after importing, is everything ok?
// would all statement transactions be in my ledger, and would the amounts match"). The
// PRIMARY verdict is a projected post-commit block computed LIVE from the current row
// choices. The math waterfall that used to fold behind a "Show the math" expander is GONE
// (owner, 2026-07-31) — see the last describe in this file.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

vi.mock('../../../hooks/useModels', () => ({
  useModels: () => ({ models: [], defaultModel: '', taskModels: {}, loaded: true }),
}))
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
})

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account
// getAllByText, not getByText: once expanded, the comparison card repeats the row's own
// name/description (imp3-cmp-name) — the FIRST match is always the collapsed header, present
// regardless of expand state, so [0] is stable across repeated calls within one test.
const pairRowFor = (text: string | RegExp) =>
  openRow(screen.getAllByText(text)[0]!.closest('.imp-pair-row') as HTMLElement)

// The verdict panel collapses to one summary row by default; expand it once per test so the
// per-question detail lines are queryable.
const openVerdict = () => {
  const btn = document.querySelector('.imp3-vsummary') as HTMLElement | null
  if (btn && btn.getAttribute('aria-expanded') === 'false') fireEvent.click(btn)
}

async function openWith(preview: unknown) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
  const utils = render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  return utils
}

const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

// Fixture 1 — all-merge clean book: a strong match adopting settled facts, a plain new row,
// and an L0 dup. Everything covered; every projected figure equals the statement's own.
function cleanBookPreview() {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-05',
        payee: 'Grab',
        description: 'Grab ride',
        amount: -8.2,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e-strong',
            existing: {
              note: 'Grab ride',
              amount: -8.2,
              currency: 'SGD',
              date: '2026-06-06',
              kind: 'expense',
            },
            band: 'strong',
          },
        ],
      },
      {
        ...base,
        date: '2026-06-07',
        payee: 'Lazada',
        description: 'LAZADA SG',
        amount: -6.89,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v2' }],
        rowIndex: 1,
        flags: [],
      },
      {
        ...base,
        date: '2026-06-08',
        payee: 'Dup',
        description: 'Dup row',
        amount: -4,
        dup: true,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v3' }],
        rowIndex: 2,
        flags: [],
      },
    ],
    importable: 2,
    duplicates: 1,
    skipped: [],
    total: 3,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

// Fixture 2 — a needs-eyes row with no match, defaulting to Import (owner, 2026-07-28).
function skipNoMatchPreview() {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-02',
        payee: 'Blurry',
        description: 'Blurry row',
        amount: -2,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v4' }],
        rowIndex: 0,
        needsEyes: true,
        confidence: 0.4,
        flags: [],
      },
      {
        ...base,
        date: '2026-06-03',
        payee: 'Clear',
        description: 'Clear row',
        amount: -3,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v5' }],
        rowIndex: 1,
        flags: [],
      },
    ],
    importable: 2,
    duplicates: 0,
    skipped: [],
    total: 2,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 0,
    },
  }
}

// Fixture 3 — a review match whose ledger copy is an ESTIMATE (−11.00 vs settled −11.04).
function estimatePreview() {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-09',
        payee: 'Gopay',
        description: 'Gopay-Gojek',
        amount: -11.04,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:v6' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'possible match',
            existing_id: 'e-review',
            existing: {
              note: 'Gopay-Gojek',
              amount: -11.0,
              currency: 'SGD',
              date: '2026-06-09',
              kind: 'expense',
            },
            band: 'review',
          },
        ],
      },
    ],
    importable: 1,
    duplicates: 0,
    skipped: [],
    total: 1,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

// Fixture 4 — mixed: the estimate row (skipped), a needs-eyes skip, and a plain import.
function mixedPreview() {
  const p = estimatePreview()
  p.drafts = [
    ...p.drafts,
    {
      ...base,
      date: '2026-06-10',
      payee: 'Blurry',
      description: 'Blurry row',
      amount: -2,
      external_refs: [{ source: 'pdf', ref: 'ref:a1:v7' }],
      rowIndex: 1,
      needsEyes: true,
      confidence: 0.4,
      flags: [],
    } as never,
    {
      ...base,
      date: '2026-06-11',
      payee: 'Clear',
      description: 'Clear row',
      amount: -3,
      external_refs: [{ source: 'pdf', ref: 'ref:a1:v8' }],
      rowIndex: 2,
      flags: [],
    } as never,
  ]
  p.importable = 3
  p.total = 3
  return p
}

describe('ImportModal — the two-question verdict (SI-64)', () => {
  it('all-merge clean book: ✓ coverage with named buckets, ✓ amounts with the projected total', async () => {
    await openWith(cleanBookPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    openVerdict()
    const line1 = screen
      .getByText(/Every statement transaction will be in your ledger/)
      .closest('.imp-verdict-line') as HTMLElement
    expect(line1.className).toContain('imp3-vline ok')
    expect(line1.textContent).toContain('1 linked to entry you already had')
    expect(line1.textContent).toContain('1 imported new')
    expect(line1.textContent).toContain('1 already booked')
    const line2 = screen
      .getByText(/Amounts match the statement to the cent/)
      .closest('.imp-verdict-line') as HTMLElement
    expect(line2.className).toContain('imp3-vline ok')
    // −8.20 (merge adopts settled) − 6.89 (new) − 4.00 (dup) = −19.09.
    expect(line2.textContent).toContain('will total −S$19.09 in your ledger')
    expect(screen.queryByText(/won’t be anywhere after import/)).toBeNull()
  })

  it('one skip-no-match: defaults to ✓ imported; Leave out flips coverage to ⚠ and scopes amounts', async () => {
    await openWith(skipNoMatchPreview())
    await screen.findByText('Clear row')
    openVerdict()
    // A needs-eyes row with no match defaults to Import (owner, 2026-07-28: a guard-flagged
    // row with no ledger match should book by default, not vanish silently) — no queue
    // interaction needed to reach this state.
    const blurryRow = openRow(screen.getByText('Blurry row').closest('.imp3-arow') as HTMLElement)
    expect(blurryRow.querySelector('.imp3-plain-decision')?.textContent).toContain('Import')
    const clearRow = openRow(screen.getByText('Clear row').closest('.imp3-arow') as HTMLElement)
    expect(clearRow.querySelector('.imp3-plain-decision')?.textContent).toContain('Import')
    expect(screen.queryByText(/won’t be anywhere after import/)).toBeNull()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.getByText(/will total −S\$5\.00 in your ledger/)).toBeTruthy()
    // Switching its decision to Leave out flips question 1 to ⚠ — live, like the After column.
    fireEvent.click(within(blurryRow).getByRole('radio', { name: 'Leave out' }))
    const line1 = screen
      .getByText(/1 statement transaction won’t be anywhere after import/)
      .closest('.imp-verdict-line') as HTMLElement
    expect(line1.className).toContain('imp3-vline warn')
    expect(line1.textContent).toContain('Left out with no ledger match')
    // The uncovered row is EXCLUDED from the amounts question — only the clear row counts.
    const line2 = screen
      .getByText(/Amounts match the statement to the cent/)
      .closest('.imp-verdict-line') as HTMLElement
    expect(line2.textContent).toContain('will total −S$3.00 in your ledger')
  })

  it('skip-with-match keeping an estimate: ✓ coverage (the entry holds it), ⚠ amounts with the delta', async () => {
    await openWith(estimatePreview())
    await screen.findByText('Gopay')
    openVerdict()
    fireEvent.click(within(pairRowFor('Gopay')).getByRole('radio', { name: 'Leave out' }))
    // Covered — the matched entry represents the skipped statement row.
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.getByText(/1 linked to entry you already had/)).toBeTruthy()
    // …but the ledger keeps −11.00 where the statement settled −11.04. (The per-row Off-by
    // marker shares the "Off by" text now, so anchor on the verdict's own kept phrase.)
    const line2 = screen.getByText(/off the statement/).closest('.imp-verdict-line') as HTMLElement
    expect(line2.className).toContain('imp3-vline warn')
    expect(line2.textContent).toContain('S$0.04 off the statement')
    expect(line2.textContent).toContain('1 row keeps your older figure')
    // Choosing Merge adopts the settled figure — the warning clears instantly.
    fireEvent.click(within(pairRowFor('Gopay')).getByRole('radio', { name: 'Link' }))
    expect(screen.queryByText(/Off by/)).toBeNull()
    expect(screen.getByText(/will total −S\$11\.04 in your ledger/)).toBeTruthy()
  })

  it('mixed book: both warnings coexist, each scoped to its own question', async () => {
    await openWith(mixedPreview())
    await screen.findByText('Clear row')
    openVerdict()
    // Both rows start fully covered (owner, 2026-07-28: "everything should have a decided
    // default") — a confident review-band match defaults to Merge (adopts the settled figure,
    // no drift yet) and the needs-eyes row with no match now defaults to Import. Neither
    // warning shows until the user explicitly opts a row out.
    expect(screen.queryByText(/won’t be anywhere after import/)).toBeNull()
    expect(screen.queryByText(/off the statement/)).toBeNull()
    const blurryRow = openRow(screen.getByText('Blurry row').closest('.imp3-arow') as HTMLElement)
    fireEvent.click(within(blurryRow).getByRole('radio', { name: 'Leave out' }))
    expect(screen.getByText(/1 statement transaction won’t be anywhere after import/)).toBeTruthy()
    fireEvent.click(within(pairRowFor('Gopay')).getByRole('radio', { name: 'Leave out' }))
    expect(screen.getByText(/1 statement transaction won’t be anywhere after import/)).toBeTruthy()
    const line2 = screen.getByText(/off the statement/).closest('.imp-verdict-line') as HTMLElement
    // Represented = the skip-with-match (−11.00 kept) + the plain import (−3.00); the
    // uncovered needs-eyes row's −2.00 belongs to question 1 only.
    expect(line2.textContent).toContain('S$0.04 off the statement')
    expect(line2.textContent).toContain('1 row keeps your older figure')
  })
})

// SUPERSEDES the SI-64 "waterfall folds behind Show the math" criterion (owner, 2026-07-31:
// "let's not show the math for now… everything looks always add up! doesn't make sense").
// The waterfall's drift term is a residual solved for server-side, so its "adds up" tick could
// never fail — the whole surface is gone. The verdict lines it sat under are NOT affected and
// stay pinned by the describes above.
describe('ImportModal — the math waterfall is gone (SI-64 superseded)', () => {
  function totalsPreview() {
    const p = cleanBookPreview() as ReturnType<typeof cleanBookPreview> & {
      ledgerCheck: Record<string, unknown>
    }
    p.ledgerCheck = {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [],
      boundary: [],
      totals: [
        {
          account_name: 'Anchor',
          currency: 'SGD',
          ledger_net: -19.09,
          statement_net: -19.09,
          statement_unbooked_net: -6.89,
          uncovered_net: 0,
          boundary_net: 0,
          drift: 0,
          tie: false,
        },
      ],
    }
    return p
  }

  it('renders no expander and no waterfall even when the wire carries totals', async () => {
    await openWith(totalsPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    openVerdict()
    expect(screen.queryByRole('button', { name: /Show the math/ })).toBeNull()
    expect(document.querySelector('.imp-math')).toBeNull()
    expect(document.querySelector('.imp-mathfold')).toBeNull()
    expect(screen.queryByText('= your ledger, this period')).toBeNull()
    expect(screen.queryByText(/adds up/)).toBeNull()
  })

  it('the verdict panel still answers both questions without it', async () => {
    await openWith(totalsPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    openVerdict()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(document.querySelectorAll('.imp-verdict-line').length).toBeGreaterThan(0)
  })
})
