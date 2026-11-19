// @vitest-environment jsdom
//
// Owner bug, 2026-07-29: a foreign-currency statement row showed BOTH its converted and its
// original amount, but the matched ledger entry beside it showed only the converted one —
// `06/22  $17.98  −S$21.67` against a bare `−S$21.21`. Both are the same ~US$18 charge; the
// S$0.46 gap is FX timing, not a discrepancy. Showing one side's original and not the other's
// made an identical charge read as a mismatch.
//
// The data was never missing: `_existing_summary` (backend/services/import_anomalies.py) has
// emitted `original_amount`/`original_currency` for every projected row all along. This was a
// pure rendering gap, and it spanned far more than the one card in the screenshot — hence the
// sweep below, one case per surface. The rule everywhere: show the original when the subject
// HAS one, render NOTHING at all when it doesn't (no dash, no empty parens).

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { originalCharge } from './importHelpers'
import type { Account } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn().mockResolvedValue({ ok: false }) }))

afterEach(cleanup)
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

const acct = {
  id: 'a1',
  name: 'Northgate Rewards Card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
} as unknown as Account
const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}
/** Every `.imp3-orig` inside a subtree — the ONE class the original renders through. */
const origTexts = (el: ParentNode) =>
  Array.from(el.querySelectorAll('.imp3-orig')).map((n) => n.textContent?.trim())

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
  const onPreview = vi.fn().mockResolvedValue(preview)
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={vi
        .fn()
        .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })}
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
}

/** A representative repro: a USD charge on an SGD card, matched to a ledger entry that
 *  carries its OWN original. `withOriginal=false` is the equally common domestic row that
 *  legitimately has none on either side. */
function matchedPreview(withOriginal = true) {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-22',
        payee: 'Anthropic',
        description: 'ANTHROPIC SAN FRANCISCO US',
        amount: -21.67,
        rowIndex: 0,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:anth' }],
        original_amount: withOriginal ? 17.98 : null,
        original_currency: withOriginal ? 'USD' : null,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'same account, same amount, same day — wording differs (review)',
            existing_id: 'e-anth',
            band: 'strong',
            existing: {
              note: 'Anthropic, PBC – Auto-recharge credits',
              amount: -21.21,
              currency: 'SGD',
              date: '2026-06-22',
              kind: 'expense',
              source: 'agent',
              original_amount: withOriginal ? 17.9 : null,
              original_currency: withOriginal ? 'USD' : null,
            },
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

/** A plain (unmatched) statement row, plus a ledger-only entry and a boundary entry — the
 *  three surfaces outside the matched-pair card that also render a transaction amount. */
function plainAndLedgerOnlyPreview(withOriginal = true) {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-23',
        payee: 'Vercel',
        description: 'VERCEL INC',
        amount: -20.1,
        rowIndex: 0,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:vercel' }],
        flags: [],
        original_amount: withOriginal ? 14.99 : null,
        original_currency: withOriginal ? 'USD' : null,
      },
    ],
    importable: 1,
    duplicates: 0,
    skipped: [],
    total: 1,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 0,
    },
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [
        {
          id: 'led-1',
          date: '2026-06-15',
          note: 'Figma seat',
          amount: -18.44,
          currency: 'SGD',
          kind: 'expense',
          source: 'manual',
          original_amount: withOriginal ? 13.5 : null,
          original_currency: withOriginal ? 'USD' : null,
        },
      ],
      boundary: [
        {
          id: 'bnd-1',
          date: '2026-07-02',
          note: 'Linear seat',
          amount: -11.02,
          currency: 'SGD',
          kind: 'expense',
          source: 'manual',
          original_amount: withOriginal ? 8.0 : null,
          original_currency: withOriginal ? 'USD' : null,
        },
      ],
    },
  }
}

// ── The pure guard ──────────────────────────────────────────────────────────────────

describe('originalCharge', () => {
  it('formats the pre-conversion charge when there is one', () => {
    expect(originalCharge({ original_amount: 10.9, original_currency: 'USD' }, 'SGD')).toBe(
      '$10.90',
    )
  })

  it('reads the client-mapped camelCase spelling too', () => {
    expect(originalCharge({ origAmount: 1201200, origCurrency: 'VND' }, 'SGD')).toBe('₫1,201,200')
  })

  it('returns null — never a placeholder — when there is nothing to show', () => {
    expect(originalCharge({}, 'SGD')).toBeNull()
    expect(originalCharge({ original_amount: null, original_currency: null }, 'SGD')).toBeNull()
    expect(originalCharge({ original_amount: 10.9, original_currency: null }, 'SGD')).toBeNull()
    expect(originalCharge({ original_amount: null, original_currency: 'USD' }, 'SGD')).toBeNull()
  })

  it('stays quiet when the "original" is already the displayed currency', () => {
    expect(originalCharge({ original_amount: 14.21, original_currency: 'SGD' }, 'SGD')).toBeNull()
  })
})

// ── The matched-pair card: the surface the owner screenshotted ───────────────────────

describe('ImportModal — the matched ledger entry shows its own original', () => {
  it('collapsed LEDGER match line carries it, beside the statement row that already did', async () => {
    await openWith(matchedPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = document.querySelector('.imp-pair-row') as HTMLElement
    // Statement side (unchanged behaviour) …
    expect(origTexts(row.querySelector('.imp3-arow-amtwrap')!)).toEqual(['$17.98'])
    // … and the ledger side, which used to render only its converted −S$21.21.
    const match = row.querySelector('.imp3-arow-match') as HTMLElement
    expect(match.textContent).toContain('-S$21.21')
    expect(origTexts(match)).toEqual(['$17.90'])
  })

  it('expanded comparison card shows an original on BOTH sides', async () => {
    await openWith(matchedPreview())
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    const [stmt, ledger] = Array.from(row.querySelectorAll('.imp3-cmp > .imp-pair-side'))
    expect(origTexts((stmt as HTMLElement).querySelector('.imp3-cmp-facts')!)).toEqual(['$17.98'])
    expect(origTexts((ledger as HTMLElement).querySelector('.imp3-cmp-facts')!)).toEqual(['$17.90'])
  })

  it('renders NOTHING on either side when neither row has an original', async () => {
    await openWith(matchedPreview(false))
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    expect(origTexts(row)).toEqual([])
    // and no placeholder crept in where the figure would have been
    expect(row.textContent).not.toContain('()')
    expect(row.textContent).not.toContain('—S$')
    const ledger = row.querySelectorAll('.imp3-cmp > .imp-pair-side')[1] as HTMLElement
    expect(ledger.querySelector('.imp3-cmp-facts')!.textContent).toBe('06/22-S$21.21')
  })
})

// ── Every other surface that prints a per-transaction amount ─────────────────────────

describe('ImportModal — the other import surfaces', () => {
  it('a plain unmatched statement row shows its original', async () => {
    await openWith(plainAndLedgerOnlyPreview())
    const row = (await screen.findByText('Vercel')).closest('.imp3-arow') as HTMLElement
    expect(origTexts(row.querySelector('.imp3-arow-amtwrap')!)).toEqual(['$14.99'])
  })

  it('"In your ledger, not on this statement" rows show theirs', async () => {
    await openWith(plainAndLedgerOnlyPreview())
    const row = (await screen.findByText('Figma seat')).closest('.imp3-arow') as HTMLElement
    expect(row.textContent).toContain('-S$18.44')
    expect(origTexts(row)).toEqual(['$13.50'])
  })

  it('the boundary bucket shows theirs', async () => {
    await openWith(plainAndLedgerOnlyPreview())
    fireEvent.click(
      await screen.findByRole('button', { name: /sit near the cut-off|sits near the cut-off/ }),
    )
    const row = (await screen.findByText('Linear seat')).closest(
      '.acct-stmtview-check-row',
    ) as HTMLElement
    expect(origTexts(row)).toEqual(['$8.00'])
  })

  it('none of those three render anything when the rows carry no original', async () => {
    await openWith(plainAndLedgerOnlyPreview(false))
    await screen.findByText('Vercel')
    await screen.findByText('Figma seat')
    expect(origTexts(document.body)).toEqual([])
  })
})

// ── The "Needs you" queue card ───────────────────────────────────────────────────────

describe('ImportModal — the Needs-you queue card', () => {
  const needsEyesPreview = (withOriginal = true) => {
    const p = matchedPreview(withOriginal)
    p.drafts[0]!.flags[0]!.band = 'review'
    return p
  }

  /** Everything auto-decides at load now, so the only route back to a queue card is
   *  re-tapping a row's own already-selected decision twice (see threeState's own case). */
  const openQueueCard = async (withOriginal: boolean) => {
    await openWith(needsEyesPreview(withOriginal))
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    const row = openRow(document.querySelector('.imp-pair-row') as HTMLElement)
    fireEvent.click(within(row).getByRole('radio', { name: 'Link' }))
    fireEvent.click(within(row).getByRole('radio', { name: 'Link' }))
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    fireEvent.click(within(nav).getByRole('button', { name: /Needs you/ }))
    return document.querySelector('.imp3-qcard') as HTMLElement
  }

  it('shows the original on both sides of its comparison card', async () => {
    const card = await openQueueCard(true)
    const [stmt, ledger] = Array.from(card.querySelectorAll('.imp3-cmp > .imp-pair-side'))
    expect(origTexts((stmt as HTMLElement).querySelector('.imp3-cmp-facts')!)).toEqual(['$17.98'])
    expect(origTexts((ledger as HTMLElement).querySelector('.imp3-cmp-facts')!)).toEqual(['$17.90'])
  })

  it('renders nothing in the card when there is no original', async () => {
    const card = await openQueueCard(false)
    expect(origTexts(card)).toEqual([])
  })
})
