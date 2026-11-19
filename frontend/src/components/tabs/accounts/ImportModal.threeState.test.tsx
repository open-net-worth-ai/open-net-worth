// @vitest-environment jsdom
//
// Characterization coverage for the production reconciliation model retained under the
// Statement Import handoff: one statement-ordered audit list; matched rows preserve the live
// post-commit projection; ledger-only Delete / Show-match rows and the boundary bucket remain
// distinct at the bottom. Chips filter without changing structural bands or decisions.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account, Category } from './types'

// The ledger section's count lives in its own badge (the design's imp3-sec-badge); the title no
// longer repeats it, so read the number from the badge.
const ledgerSectionCount = () =>
  document.querySelector('.imp3-sec.ledger .imp3-sec-badge')?.textContent ?? null

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  // jsdom doesn't implement scrollIntoView — jump-nav / waterfall terms / flash-jump call it.
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
// A ledger-only row is the prototype's LedgerOnlyRow — its hint + one-vocabulary actions live
// in the disclosure, so the locator opens it. Boundary rows stay the flat
// acct-stmtview-check-row family (pure visibility, no actions).
// getAllByText[0] for the same reason pairRowTitled below uses it: an EXPANDED ledger-only row
// repeats its own description in the body's facts list (that is the one place a long bank
// descriptor is readable in full — the closed line ellipsizes it with no tooltip), so the name
// is no longer a unique text match. [0] is the collapsed header, present regardless of state.
const rowFor = (note: string) => {
  const el = screen.getAllByText(note)[0]!
  const arow = el.closest('.imp3-arow') as HTMLElement | null
  if (arow) return openRow(arow)
  return el.closest('.acct-stmtview-check-row') as HTMLElement
}
// getAllByText, not getByText: once expanded, the comparison card repeats the row's own
// name/description (imp3-cmp-name) — the FIRST match is always the collapsed header, present
// regardless of expand state, so [0] is stable across repeated calls within one test.
// Pair rows used to be addressed by their verdict headline ('✓ Will link', 'Already recorded
// — skipped', '≈ Possibly already recorded — …'). That whole sub-line is deleted (owner,
// 2026-07-29: it trailed raw matcher diagnostics). The `.imp3-dchip` decision chip — "Link" /
// "Import" / "Leave out" — is what a user reads now, so it is what these locators key on.
const pairRows = () => Array.from(document.querySelectorAll('.imp-pair-row')) as HTMLElement[]
const chipOf = (row: HTMLElement) => row.querySelector('.imp3-dchip')?.textContent ?? ''
const pairRowsFor = (decision: string) => pairRows().filter((r) => chipOf(r) === decision)
const pairRowFor = (decision: string) => openRow(pairRowsFor(decision)[0]!)
// A pure render barrier — it must NOT open the row, or a title that also appears inside the
// comparison card would start matching twice and break unrelated getByText assertions.
const findPairRowFor = (decision: string) =>
  waitFor(() => expect(pairRowsFor(decision).length).toBeGreaterThan(0))
// Some cases address a pair row by its own TITLE text rather than by its decision.
const pairRowTitled = (text: string | RegExp) =>
  openRow(screen.getAllByText(text)[0]!.closest('.imp-pair-row') as HTMLElement)
const weakRow = () => openRow(document.querySelector('.imp-pair-row.weak') as HTMLElement)
const findWeakRow = async () => {
  await waitFor(() => expect(document.querySelector('.imp-pair-row.weak')).toBeTruthy())
  return document.querySelector('.imp-pair-row.weak') as HTMLElement
}
const afterCol = (row: HTMLElement) => row.querySelector('.imp3-after') as HTMLElement
// The verdict panel collapses to one summary row by default; expand it once per test so the
// per-question detail lines are queryable.
const openVerdict = () => {
  const btn = document.querySelector('.imp3-vsummary') as HTMLElement | null
  if (btn && btn.getAttribute('aria-expanded') === 'false') fireEvent.click(btn)
}

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
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
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  return { ...utils, onDecisionCommit }
}

// ── Strong band: will-link pair row + after-import projection ───────────────────────

function strongRowPreview() {
  return {
    drafts: [
      {
        date: '2026-06-05',
        payee: 'Grab',
        description: 'Grab *Ride 8821',
        amount: -8.2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:strong1' }],
        flags: [
          {
            type: 'near_duplicate',
            reason: 'unique pairing in a complete statement window (books-close)',
            existing_id: 'existing-strong',
            existing: {
              note: 'Grab ride',
              amount: -8.2,
              currency: 'SGD',
              date: '2026-06-06',
              kind: 'expense',
            },
            band: 'strong',
            promoted: true,
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

describe('ImportModal — strong band pair row', () => {
  it('renders the workspace modal, all three handoff decisions, and the comparison body', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    expect(document.querySelector('.ui-modal-card--workspace')).toBeTruthy()
    const row = pairRowFor('Link')
    expect(within(row).getByRole('radio', { name: 'Link' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Import' })).toBeTruthy()
    // The handoff comparison names both sources, then lets the After panel span the full
    // width — labeled by the actual decision (Link here), not a generic "After import".
    expect(document.querySelector('.imp-pair-colhead')).toBeNull()
    const labels = Array.from(row.querySelectorAll('.imp3-cmp-h, .imp3-after-l')).map(
      (el) => el.textContent?.replace(/ • .*$/, '') ?? null,
    )
    expect(labels).toEqual(['On the statement', 'In your ledger', 'After link'])
    expect(row.querySelectorAll('.imp3-cmp > .imp-pair-side').length).toBe(2)
    expect(row.querySelector('.imp3-cmp')?.contains(row.querySelector('.imp3-after'))).toBe(false)
    // Footer describes the committed outcome, not a pending count. The commit button itself
    // just says "Confirm" — the breakdown lives in the footer note only.
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('AFTER IMPORT (merge): ONE record — existing labels, statement date as an old→new delta, cleared + reference badges', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    // The existing entry's identity (labels never change) with the statement's date.
    expect(within(after).getByText('Grab ride')).toBeTruthy()
    const struck = after.querySelector('s.imp-after-old') as HTMLElement
    expect(struck.textContent).toBe('06/06')
    expect(within(after).getByText('06/05').className).toContain('imp-after-new')
    // Same settled amount → no amount delta, the value stays quiet.
    expect(after.querySelectorAll('s.imp-after-old').length).toBe(1)
    expect(within(after).getByText('reference attached')).toBeTruthy()
    // The outcome line (zone 3) still reads the date re-stamp.
    expect(screen.getByText('06/05', { selector: '.imp-after-new' })).toBeTruthy()
  })

  it('flipping to Skip updates the decision chip AND the after column to the byte-unchanged record — live reactivity', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    fireEvent.click(within(pairRowFor('Link')).getByRole('radio', { name: 'Leave out' }))
    await findPairRowFor('Leave out')
    // The verdict sub-line that used to carry this class is gone entirely now.
    expect(document.querySelector('.imp3-arow-match-src')).toBeNull()
    const after = afterCol(pairRowFor('Leave out'))
    // ONE record, the existing entry as-is: its own date, no deltas, no cleared badge.
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    expect(within(after).getByText('06/06')).toBeTruthy()
    expect(after.querySelector('s.imp-after-old')).toBeNull()
    expect(within(after).getByText('yours stays as-is')).toBeTruthy()
    expect(screen.getByText('Nothing to book')).toBeTruthy()
  })

  it('flipping to Import new shows TWO stacked records with the honest "2 records" badge', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    fireEvent.click(within(pairRowFor('Link')).getByRole('radio', { name: 'Import' }))
    await findPairRowFor('Import')
    const after = afterCol(pairRowFor('Import'))
    expect(within(after).getByText(/After import • 2 records/)).toBeTruthy()
    const recs = after.querySelectorAll('.imp-after-rec')
    expect(recs.length).toBe(2)
    // Record 1: the existing entry unchanged; record 2: the statement row booked as new.
    expect(recs[0]!.textContent).toContain('Grab ride')
    expect(recs[1]!.textContent).toContain('Grab')
    expect(recs[1]!.className).toContain('new')
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('import 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })
})

// ── Review band: 3-chip pair row, profile-governed default (owner 2026-07-17:
// `import_review_default` — skip unless the profile opts into merge) ─────────────────

function reviewRowPreview() {
  return {
    drafts: [
      {
        date: '2026-06-09',
        payee: 'Gopay',
        description: 'Gopay-Gojek',
        amount: -11.0,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:review1' }],
        flags: [
          {
            type: 'near_duplicate',
            reason: 'possible match',
            existing_id: 'existing-review',
            existing: {
              note: 'Gopay-Gojek',
              amount: -11.0,
              currency: 'SGD',
              date: '2026-06-09',
              kind: 'expense',
              category: 'cat_transport',
              account_id: 'a1',
              source: 'agent',
              has_category: true,
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

describe('ImportModal — review band pair row', () => {
  it('a resolved match wins before needs-eyes in both the footer and commit payload', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false })
    const preview = reviewRowPreview()
    preview.drafts[0] = { ...preview.drafts[0]!, rowIndex: 0, needsEyes: true } as never
    const { onDecisionCommit } = await openWith({ ...preview, statementId: 'st-matched-eyes' })
    await screen.findByText('Gopay')

    // A resolved review-band match now counts as decided immediately (owner, 2026-07-27) —
    // wins so definitively over needsEyes that it isn't even "awaiting" anymore.
    const footer = document.querySelector('.imp3-foot-note') as HTMLElement
    expect(within(footer).getByText('Link 1')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'existing-review' },
    ])
  })

  it('defaults to Merge without the profile setting (factory default, owner 2026-07-17): question headline, Merge pressed', async () => {
    await openWith(reviewRowPreview())
    await screen.findByText('Gopay')
    const row = pairRowTitled('Gopay')
    expect(within(row).getByRole('radio', { name: 'Link' }).className).toContain('on')
    expect(within(row).getByRole('radio', { name: 'Leave out' }).className).not.toContain('on')
  })

  it("explicit importReviewDefault='skip' (the opt-out): Skip pressed, yours stays as-is", async () => {
    await openWith(reviewRowPreview(), { importReviewDefault: 'skip' })
    await screen.findByText('Gopay')
    const row = pairRowTitled('Gopay')
    expect(within(row).getByRole('radio', { name: 'Leave out' }).className).toContain('on')
    expect(within(row).getByRole('radio', { name: 'Link' }).className).not.toContain('on')
    expect(within(afterCol(row)).getByText('yours stays as-is')).toBeTruthy()
    expect(screen.getByText('Nothing to book')).toBeTruthy()
  })

  it('with import_review_default=merge (the owner opt-in): three chips, Merge pressed, after column = the merged record', async () => {
    await openWith(reviewRowPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('Gopay')
    const row = pairRowTitled('Gopay')
    // The band keeps its amber question treatment — only the pre-selected action changed.
    expect(within(row).getByRole('radio', { name: 'Link' }).className).toContain('on')
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Import' })).toBeTruthy()
    expect(within(afterCol(row)).getByText('reference attached')).toBeTruthy()
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('an explicit Skip overrides a merge default and parks the row: after column = yours as-is, nothing commits', async () => {
    await openWith(reviewRowPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('Gopay')
    const row = pairRowTitled('Gopay')
    fireEvent.click(within(row).getByRole('radio', { name: 'Leave out' }))
    expect(within(afterCol(row)).getByText('yours stays as-is')).toBeTruthy()
    expect(screen.getByText('Nothing to book')).toBeTruthy()
  })

  it('Merge recomputes the after column into the merged record (no deltas when date+amount already agree)', async () => {
    await openWith(reviewRowPreview())
    await screen.findByText('Gopay')
    fireEvent.click(within(pairRowTitled('Gopay')).getByRole('radio', { name: 'Link' }))
    const after = afterCol(pairRowTitled('Gopay'))
    expect(after.querySelector('s.imp-after-old')).toBeNull() // same date, same amount
    expect(within(after).getByText('reference attached')).toBeTruthy()
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })
})

// ── Weak band (RFC 2026-07-29 §D2): the ENGINE's own settled-drift tier ──────────────
// It used to be a client-side join over `ledgerCheck.rows[].statement_twin`. It is now an
// ordinary match flag carrying `band: 'weak'`, and the server has already removed the entry it
// claimed from `ledgerCheck.rows` (that list is a pure set difference).

const WEAK_TWIN = {
  note: 'Anthropic Claude API',
  amount: -14.16,
  currency: 'SGD',
  date: '2026-06-19',
  kind: 'expense',
  source: 'agent',
}

function weakPairPreview(extra: Record<string, unknown> = {}) {
  return {
    drafts: [
      {
        date: '2026-06-18',
        payee: 'ANTHROPIC',
        description: 'ANTHROPIC',
        amount: -14.63,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:weak1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'same account, settled 3.3% off — wording overlaps',
            existing_id: 'twin-ledger-1',
            band: 'weak',
            score: 0.31,
            existing: WEAK_TWIN,
            alternatives: [],
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
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [],
      boundary: [],
    },
    ...extra,
  }
}

describe('ImportModal — weak band (twin-sourced pair row)', () => {
  // SIUM-DRIFT-CARD / SIUM-WEAK-QUIET
  it('renders the SI-53 repro as a weak pair row: muted chip, Import default, all three decisions', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    const row = weakRow()
    expect(row.className).toContain('weak')
    // Muted "Possible match", never the amber "needs you" treatment, never green.
    const chip = row.querySelector('.imp3-qchip') as HTMLElement
    expect(chip.textContent).toBe('Possible match')
    expect(chip.className).toContain('maybe')
    expect(chip.className).not.toContain('open')
    expect(row.querySelector('.imp3-dchip')?.textContent).toBe('Import')
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Same one — link' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Import' })).toBeTruthy()
    // The standalone dismissal is GONE (owner, 2026-07-31) — `Import` carries it now.
    expect(within(row).queryByRole('button', { name: /Not the same/ })).toBeNull()
    // Ledger side shows the twin's own identity (the name link + the after-column echo).
    expect(within(row).getAllByText(/Anthropic Claude API/).length).toBeGreaterThan(0)
    // Default action new → after column shows the honest two-record projection.
    const after = afterCol(row)
    expect(within(after).getByText(/After import • 2 records/)).toBeTruthy()
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(2)
    // Weak defaults flow into imports with zero formula edits (§8).
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('import 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  // SIUM-WEAK-RELEASE (supersedes SIUM-WEAK-DISMISS): the weak band's default IS Import, and
  // Import is the assertion that the two are not the same thing — so the entry the server's
  // weak flag had claimed is uncovered, and the reconciliation panel must say so.
  it('the weak row is covered as an import, and its claimed entry is RELEASED to the ledger-only section (SI-64)', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    openVerdict()
    // Statement side is fully covered — the weak default is decisive, no queue interaction.
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.getByText(/1 imported new/)).toBeTruthy()
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()
    // …and the ledger side no longer under-reports: the twin is back, under the ORPHAN copy.
    expect(screen.getByText(/In your ledger, not on this statement/i)).toBeTruthy()
    expect(ledgerSectionCount()).toBe('1')
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByRole('button', { name: /Anthropic Claude API/ })).toBeTruthy()
    expect(screen.getByText(/1 ledger entry from this period isn’t on this statement/)).toBeTruthy()
  })

  // SIUM-WEAK-LINK
  it('weak Merge projects ONE record with date AND amount deltas (statement facts win, labels stay)', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    const row = weakRow()
    fireEvent.click(within(row).getByRole('radio', { name: 'Same one — link' }))
    const after = afterCol(row)
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    expect(within(after).getByText('Anthropic Claude API')).toBeTruthy() // label kept
    const olds = Array.from(after.querySelectorAll('s.imp-after-old')).map((el) => el.textContent)
    expect(olds).toEqual(['06/19', '-S$14.16'])
    expect(within(after).getByText('06/18').className).toContain('imp-after-new')
    expect(within(after).getByText('-S$14.63').className).toContain('imp-after-new')
    expect(within(after).getByText('reference attached')).toBeTruthy()
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('a weak Merge decision carries the TWIN’s id (never undefined) in the commit payload', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    mockAuthFetch.mockResolvedValue({ ok: false } as Response) // post-commit ledger-diff probe
    await openWith(weakPairPreview({ statementId: 'st-weak' }), { onDecisionCommit })
    await findWeakRow()
    fireEvent.click(within(weakRow()).getByRole('radio', { name: 'Same one — link' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'twin-ledger-1' },
    ])
  })

  // The engine's own diagnostic `reason` is never rendered (owner, 2026-07-29). What the
  // collapsed suggestion line DOES carry is client arithmetic over the two figures on screen.
  it('the collapsed suggestion line is a dashed `? Maybe` line with the drift, never the engine reason', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    const collapsed = document.querySelector('.imp-pair-row') as HTMLElement
    const maybe = collapsed.querySelector('.imp3-arow-maybe') as HTMLElement
    expect(maybe).toBeTruthy()
    expect(maybe.textContent).toContain('Maybe')
    expect(maybe.textContent).toContain('Anthropic Claude API')
    expect(maybe.textContent).toContain('3.3% off')
    // Never the strong band's solid ⇄ Ledger line, and never the matcher's prose.
    expect(collapsed.querySelector('.imp3-arow-match')).toBeNull()
    expect(collapsed.textContent).not.toContain('wording overlaps')
    expect(collapsed.textContent).not.toContain('same account, settled')
  })

  // SIUM-WEAK-RELINK (supersedes SIUM-WEAK-UNDO): the release is a pure function of the row's
  // live decision, so the segmented control IS the way back — no dismissal state to undo.
  it('the release is reversible: `Same one — link` re-claims the entry, `Import` releases it again', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    // Default (Import): released.
    expect(ledgerSectionCount()).toBe('1')

    fireEvent.click(within(weakRow()).getByRole('radio', { name: 'Same one — link' }))
    // Re-claimed — the entry is accounted for by this statement again.
    expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
    expect(screen.queryByText(/In your ledger, not on this statement/i)).toBeNull()
    // The row is still a weak pair card, still pointing at the same partner.
    const linked = document.querySelector('.imp-pair-row.weak') as HTMLElement
    expect(linked).toBeTruthy()
    expect(linked.querySelector('.imp3-qchip')?.textContent).toBe('Possible match')
    expect(linked.querySelector('.imp3-dchip')?.textContent).toBe('Link')
    expect(within(linked).getAllByText(/Anthropic Claude API/).length).toBeGreaterThan(0)

    fireEvent.click(within(weakRow()).getByRole('radio', { name: 'Import' }))
    expect(ledgerSectionCount()).toBe('1')
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByRole('button', { name: /Anthropic Claude API/ })).toBeTruthy()
  })

  // `Leave out` is NOT a rejection: nothing double-books precisely because the ledger entry
  // already covers the row, so its claim stands.
  it('`Leave out` keeps the entry claimed — only Import releases it', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    fireEvent.click(within(weakRow()).getByRole('radio', { name: 'Leave out' }))
    expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
  })

  // SIUM-CHIP-WEAK
  it('a `Possible matches` lens chip appears, filters to the weak rows, and vanishes once resolved', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    const lens = within(nav).getByRole('button', { name: /Possible matches\s*1/ })
    expect(lens.getAttribute('aria-pressed')).toBe('false')
    // It is NOT one of the partitioning chips — the row still belongs to Import.
    expect(within(nav).getByRole('button', { name: /Import\s*1/ })).toBeTruthy()
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()

    fireEvent.click(lens)
    expect(lens.getAttribute('aria-pressed')).toBe('true')
    expect(document.querySelectorAll('[data-draft-index]')).toHaveLength(1)

    // Linking resolves the possibility: the chip goes, and the filter falls back to All.
    fireEvent.click(within(weakRow()).getByRole('radio', { name: 'Same one — link' }))
    expect(within(nav).queryByRole('button', { name: /Possible matches/ })).toBeNull()
    expect(within(nav).getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe(
      'true',
    )
  })
})

// ── The release is WEAK-ONLY (SIUM-WEAK-RELEASE, band clause) ────────────────────────
// Same fixture, same partner, only the band differs. A strong/review row demands a deliberate
// answer, and its `Import` means "the engine was confident and I'm keeping both anyway" — not
// "these are different things" — so its entry stays claimed.

function bandedPairPreview(band: 'strong' | 'review') {
  const p = weakPairPreview()
  const flag = p.drafts[0]!.flags[0]! as Record<string, unknown>
  flag.band = band
  delete flag.score
  return p
}

describe('ImportModal — only the WEAK band releases its entry on Import', () => {
  for (const band of ['strong', 'review'] as const) {
    it(`a ${band} row explicitly set to Import leaves the matched entry claimed`, async () => {
      await openWith(bandedPairPreview(band), { importReviewDefault: 'merge' })
      await findPairRowFor('Link')
      const row = pairRowFor('Link')
      fireEvent.click(within(row).getByRole('radio', { name: 'Import' }))
      expect(document.querySelector('.imp-pair-row')?.className).not.toContain('weak')
      expect(document.querySelector('.imp3-dchip')?.textContent).toBe('Import')
      expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
      expect(screen.queryByText(/In your ledger, not on this statement/i)).toBeNull()
    })
  }
})

// ── Transfer-echo exception: the transfer's legs stay whole ──────────────────────────

function transferEchoPreview() {
  return {
    drafts: [
      {
        date: '2026-06-14',
        payee: 'FX',
        description: 'FX conversion leg',
        amount: -200,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:echo1' }],
        flags: [
          {
            type: 'transfer_leg_echo',
            reason: 'already booked as a transfer',
            existing_id: 'existing-transfer',
            existing: {
              note: 'EUR→SGD conversion',
              amount: -200,
              currency: 'SGD',
              date: '2026-06-13',
              kind: 'transfer',
            },
            band: 'strong',
          },
        ],
      },
    ],
    importable: 1,
    duplicates: 0,
    skipped: [],
    total: 1,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 0,
      transfer_leg_echo: 1,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

describe('ImportModal — after column transfer-echo exception', () => {
  it('merging into a transfer target shows the transfer UNCHANGED + "linked ✓" — no date/amount rewrite', async () => {
    await openWith(transferEchoPreview())
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    expect(within(after).getByText('EUR→SGD conversion')).toBeTruthy()
    expect(within(after).getByText('06/13')).toBeTruthy() // the transfer's OWN date, kept
    expect(after.querySelector('s.imp-after-old')).toBeNull() // no delta, nothing rewritten
    expect(within(after).getByText('linked ✓')).toBeTruthy()
    expect(screen.getByText(/linked ✓/)).toBeTruthy()
  })
})

// ── Counting invariants (§8): weak is reviewed in Needs you and remains filterable ────

function mixedBandPreview() {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'A',
        description: 'A',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:m1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e1',
            existing: { note: 'a', amount: -1, currency: 'SGD' },
            band: 'review',
          },
        ],
      },
      {
        date: '2026-06-02',
        payee: 'B',
        description: 'B',
        amount: -2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:m2' }],
        rowIndex: 1,
        needsEyes: true,
        confidence: 0.4,
        flags: [],
      },
      {
        date: '2026-06-03',
        payee: 'C',
        description: 'C',
        amount: -3,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:m3' }],
        rowIndex: 2,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'settled drift',
            existing_id: 'twin-w1',
            band: 'weak',
            score: 0.29,
            alternatives: [],
            existing: {
              note: 'C twin',
              amount: -3.09,
              currency: 'SGD',
              date: '2026-06-03',
              kind: 'expense',
              source: 'agent',
            },
          },
        ],
      },
      {
        date: '2026-06-04',
        payee: 'D',
        description: 'D',
        amount: -4,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:m4' }],
        rowIndex: 3,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 0,
    skipped: [],
    total: 4,
    anomaly: {
      near_duplicate: 2,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 2,
    },
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      // `twin-w1` is CLAIMED by row C's weak flag, so the server's set difference already
      // dropped it — the client never subtracts it itself.
      rows: [],
      boundary: [],
    },
  }
}

// The cross-walk fixture (owner 2026-07-16): one row per band — strong, review, weak, plain
// new — no needs-eyes/dups, the shape whose DEFAULT actions map bands onto outcomes 1:1
// (strong+review default merge → linked; weak+plain default new → imported). The review
// merge default now requires the profile opt-in (`importReviewDefault: 'merge'`) — the
// cross-walk identity Matched+Needs-you = linked only holds under that config.
function fourBandPreview() {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'S',
        description: 'S',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:x1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e-s',
            existing: {
              note: 's twin',
              amount: -1,
              currency: 'SGD',
              date: '2026-06-01',
              kind: 'expense',
            },
            band: 'strong',
          },
        ],
      },
      {
        date: '2026-06-02',
        payee: 'R',
        description: 'R',
        amount: -2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:x2' }],
        rowIndex: 1,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e-r',
            existing: {
              note: 'r twin',
              amount: -2,
              currency: 'SGD',
              date: '2026-06-02',
              kind: 'expense',
            },
            band: 'review',
          },
        ],
      },
      {
        date: '2026-06-03',
        payee: 'W',
        description: 'W',
        amount: -3,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:x3' }],
        rowIndex: 2,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'settled drift',
            existing_id: 'twin-x1',
            band: 'weak',
            score: 0.29,
            alternatives: [],
            existing: {
              note: 'W twin',
              amount: -3.09,
              currency: 'SGD',
              date: '2026-06-04',
              kind: 'expense',
              source: 'agent',
            },
          },
        ],
      },
      {
        date: '2026-06-04',
        payee: 'P',
        description: 'P',
        amount: -4,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:x4' }],
        rowIndex: 3,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 0,
    skipped: [],
    total: 4,
    anomaly: {
      near_duplicate: 3,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 3,
    },
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [],
      boundary: [],
    },
  }
}

describe('ImportModal — counting invariants (§8)', () => {
  it('every structural population defaults to its own decided outcome — none owned by Needs-you', async () => {
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    // The SummaryBar card row is GONE (owner 2026-07-16: one counting surface per meaning —
    // outcomes live in the verdict + footer, bands in the chips, statement-side facts on
    // the metaline).
    expect(document.querySelector('.sumbar')).toBeNull()
    // "A" (review, confident match) defaults to Will link, "B" (needs-eyes, no match)
    // defaults to Import (owner, 2026-07-28), and "C" (weak twin) defaults to its decisive
    // New — every structural population is pre-decided at load, so Needs-you owns nothing
    // and doesn't even render as a chip, and nothing is Left out by default.
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).getByRole('button', { name: 'All' })).toBeTruthy()
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(within(nav).getByRole('button', { name: /Will link\s*1/ })).toBeTruthy()
    // The weak row "C" partitions under Import like any other row; its only extra surface is
    // the transient `Possible matches` LENS, which owns nothing (SIUM-CHIP-WEAK).
    expect(within(nav).getByRole('button', { name: /Import\s*3/ })).toBeTruthy()
    expect(within(nav).getByRole('button', { name: /Possible matches\s*1/ })).toBeTruthy()
    expect(within(nav).queryByRole('button', { name: /Left out/ })).toBeNull()
  })

  it('cross-walk: the handoff filter populations partition All while outcomes stay truthful', async () => {
    await openWith(fourBandPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('P')
    openVerdict()
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    const chipN = (re: RegExp) =>
      Number(
        (within(nav).getByRole('button', { name: re }).querySelector('i') as HTMLElement)
          .textContent,
      )
    // "All" carries no count badge (handoff: review3.jsx:597 renders it as bare text) — its
    // total comes independently from the rendered row count, not the chip itself.
    const all = document.querySelectorAll('[data-draft-index]').length
    const matched = chipN(/^Will link/),
      fresh = chipN(/^Import/)
    // "R" (review) and "W" (weak) both default to their own decided outcome at load (owner,
    // 2026-07-28) — "R" moves straight to Will link, "W" to its decisive New — so Needs-you
    // owns nothing and doesn't even render.
    expect(within(nav).queryByRole('button', { name: /^Needs you/ })).toBeNull()
    const line1 = screen
      .getByText(/Every statement transaction will be in your ledger/)
      .closest('.imp-verdict-line') as HTMLElement
    const linked = Number(/(\d+) linked to/.exec(line1.textContent || '')?.[1])
    const imported = Number(/(\d+) imported new/.exec(line1.textContent || '')?.[1])
    expect(matched + fresh).toBe(all)
    expect([all, matched, fresh, linked, imported]).toEqual([4, 2, 2, 2, 2])
    // …and the footer note echoes the same outcome counts (the button itself just says
    // "Confirm").
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 2 • import 2')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('an explicit strong-row Leave out moves ownership instead of double-counting filters', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).getByRole('button', { name: 'All' })).toBeTruthy()
    expect(within(nav).getByRole('button', { name: /Will link\s*1/ })).toBeTruthy()

    fireEvent.click(within(pairRowFor('Link')).getByRole('radio', { name: 'Leave out' }))
    expect(within(nav).queryByRole('button', { name: /Will link/ })).toBeNull()
    expect(within(nav).getByRole('button', { name: /Left out\s*1/ })).toBeTruthy()
    const owned = Array.from(within(nav).getAllByRole('button'))
      .filter((button) => !button.textContent?.startsWith('All'))
      .reduce((sum, button) => sum + Number(button.querySelector('i')?.textContent || 0), 0)
    expect(owned).toBe(1)
  })

  it('metaline statement-side facts: rows read always; already-imported + skipped only when nonzero, skipped toggles the detail', async () => {
    const p = {
      ...mixedBandPreview(),
      duplicates: 2,
      skipped: [{ description: 'garbled', reason: 'unreadable amount' }],
    }
    await openWith(p)
    await screen.findByText('D')
    expect(screen.getByText('4 rows read')).toBeTruthy()
    expect(screen.getByText('2 already imported')).toBeTruthy()
    const tog = screen.getByRole('button', { name: 'view' })
    expect(tog.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('1 skipped row — not imported')).toBeNull()
    fireEvent.click(tog)
    expect(screen.getByText('1 skipped row — not imported')).toBeTruthy()
    expect(screen.getByText('garbled')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'hide' }))
    expect(screen.queryByText('1 skipped row — not imported')).toBeNull()
  })

  it('zero already-imported / zero skipped render NO metaline fact (quiet)', async () => {
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    const metaline = document.querySelector('.imp3-facts') as HTMLElement
    expect(within(metaline).getByText('4 rows read')).toBeTruthy()
    expect(metaline.textContent).not.toContain('already imported')
    expect(metaline.textContent).not.toContain('skipped')
  })
})

// ── Filter chips (owner directive 2026-07-16, supersedes the jump-nav): single-select ─

describe('ImportModal — filter chips', () => {
  it('defaults to All (pressed), omits zero-count chips, keeps no tab semantics, hides nothing', async () => {
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByRole('tablist')).toBeNull()
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    const all = within(nav).getByRole('button', { name: 'All' })
    expect(all.className).toContain('on')
    expect(all.getAttribute('aria-pressed')).toBe('true')
    // No strong rows → that chip doesn't render. "Not on statement" DOES: row "C" is a weak
    // row sitting at its Import default, which releases `twin-w1` back onto the panel.
    expect(within(nav).queryByRole('button', { name: /Matched/ })).toBeNull()
    expect(within(nav).getByRole('button', { name: /Not on statement\s*1/ })).toBeTruthy()
    // Under the All default every draft is on screen (the weak row's identity also echoes
    // in its after column, so use getAllByText).
    for (const t of ['A', 'B', 'C', 'D']) expect(screen.getAllByText(t).length).toBeGreaterThan(0)
  })

  it('a band chip shows only its band; a decision made under a filter survives switching back to All', async () => {
    // Every row (A, B, C) is already auto-decided with its own default (owner, 2026-07-27/28)
    // — "A" is a regular matched pair row throughout, never a Needs-you queue card here.
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    // Decide "A" (still under All) — it's already pre-selected to Link by default.
    expect(
      within(pairRowTitled('A')).getByRole('radio', { name: 'Link' }).getAttribute('aria-checked'),
    ).toBe('true')

    fireEvent.click(within(nav).getByRole('button', { name: /Import\s*3/ }))
    // The Import band-chip shows "B", "C", and "D" — "A" (Will link) is hidden.
    expect(screen.getAllByText('B').length).toBeGreaterThan(0)
    expect(screen.getAllByText('C').length).toBeGreaterThan(0)
    expect(screen.getByText('D')).toBeTruthy()
    expect(screen.queryByText('A')).toBeNull()
    // …switch back to All: everything is back and "A"'s earlier default survived.
    fireEvent.click(within(nav).getByRole('button', { name: 'All' }))
    expect(screen.getByText('D')).toBeTruthy()
    expect(within(pairRowTitled('A')).getByRole('radio', { name: 'Link' }).className).toContain(
      'on',
    )
  })

  it("an All-view choice resolves that row without disturbing another row's independent default", async () => {
    // REWORDED 2026-07-27/28 (owner: "remove this and relevant code" — the 1/2/3 keyboard
    // shortcut queue is gone; "when i tap on any decision, they should move to their
    // respective list" — every row is already auto-decided and is a regular row throughout,
    // never a Needs-you queue card here). What survives: a decision made on one row doesn't
    // disturb another row's own independent default.
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    // "A" (review, confident match) defaults to Link; "B" (needs-eyes, no match)
    // defaults to Import (owner, 2026-07-28) — both already decided, independently.
    expect(
      within(pairRowTitled('A')).getByRole('radio', { name: 'Link' }).getAttribute('aria-checked'),
    ).toBe('true')
    const bRow = () =>
      openRow(
        screen.getByRole('button', { name: /^B, Import/ }).closest('.imp3-arow') as HTMLElement,
      )
    expect(within(bRow()).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked')).toBe(
      'true',
    )

    fireEvent.click(within(pairRowTitled('A')).getByRole('radio', { name: 'Import' }))
    // "B"'s independent default is untouched by "A"'s decision.
    expect(within(bRow()).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked')).toBe(
      'true',
    )

    fireEvent.click(within(bRow()).getByRole('radio', { name: 'Leave out' }))
    // Deciding "B" doesn't disturb "A"'s own choice (Import, made earlier) either.
    expect(
      within(pairRowTitled('A'))
        .getByRole('radio', { name: 'Import' })
        .getAttribute('aria-checked'),
    ).toBe('true')
  })

  it('coverage stays amber until a left-out row is given a receipt; the amounts question is independent', async () => {
    await openWith(mixedBandPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('D')
    openVerdict()
    // "A" already links and "C" already imports as new; "B" (needs-eyes, no match) now
    // defaults to Import too (owner, 2026-07-28) — coverage starts clean. Leaving "B" out is
    // what puts question 1 back into its amber, needs-a-receipt state; the amounts question
    // only scopes to represented rows, so it stays settled throughout.
    expect(screen.queryByText(/still need your call/)).toBeNull()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()

    const bRow = openRow(
      screen.getByRole('button', { name: /^B, Import/ }).closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(bRow).getByRole('radio', { name: 'Leave out' }))

    expect(screen.getByText(/1 statement transaction won’t be anywhere after import/)).toBeTruthy()
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()

    const bRowAgain = openRow(
      screen.getByRole('button', { name: /^B, Left out/ }).closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(bRowAgain).getByRole('button', { name: 'Import' }))

    expect(screen.queryByText(/won’t be anywhere after import/)).toBeNull()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()
  })

  it('unrouted and pre-reconciliation rows never enter the actionable queue', async () => {
    await openWith({
      drafts: [
        {
          date: '2026-06-01',
          payee: 'Foreign',
          description: 'Foreign',
          amount: -1,
          currency: 'USD',
          kind: 'expense',
          category: null,
          dup: false,
          rowIndex: 0,
          needsEyes: true,
          flags: [{ type: 'no_account_for_currency', reason: 'Pick a USD account first.' }],
        },
        {
          date: '2026-05-01',
          payee: 'Old',
          description: 'Old',
          amount: -2,
          currency: 'SGD',
          kind: 'expense',
          category: null,
          dup: false,
          rowIndex: 1,
          needsEyes: true,
          preRecon: true,
          flags: [{ type: 'low_confidence', reason: 'Before the reconciled boundary.' }],
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
        flagged_rows: 2,
      },
    })
    await screen.findByText('Foreign')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(within(nav).getByRole('button', { name: /Left out\s*2/ })).toBeTruthy()
    expect(screen.queryByText(/still need your call/)).toBeNull()
    expect(document.querySelector('.imp3-queue')).toBeNull()
    for (const index of [0, 1]) {
      const row = document.querySelector(`[data-draft-index="${index}"]`) as HTMLElement
      expect(within(row).queryByRole('button', { name: 'Import' })).toBeNull()
    }
  })

  it('a Leave-out decision on a plain needs-eyes row can be switched back to Import before commit', async () => {
    // REWORDED 2026-07-28 (owner: "when i tap on any decision, they should move to their
    // respective list") — "B" (a plain needs-eyes row, no match) defaults to Import; deciding
    // it as Leave out moves it straight into Left out, and it's re-decided the same way it
    // was first decided, as a plain row.
    mockAuthFetch.mockResolvedValue({ ok: false })
    const { onDecisionCommit } = await openWith({ ...mixedBandPreview(), statementId: 'st-queue' })
    await screen.findByText('D')
    const eyesRow = openRow(
      screen.getByRole('button', { name: /^B, Import/ }).closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(eyesRow).getByRole('radio', { name: 'Leave out' }))

    const row = openRow(
      screen.getByRole('button', { name: /^B, Left out/ }).closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(row).getByRole('button', { name: 'Import' }))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    expect(decisions).toContainEqual({ row: 1, action: 'create' })
  })

  it('Fix the amount records a corrected read and commits it through the existing edits seam', async () => {
    // REWORDED 2026-07-28: "B" (needs-eyes, no match) auto-decides at load, so there's no
    // Needs-you queue card to open — Fix the amount now lives on the plain row itself (owner
    // call: a misread amount must stay correctable regardless of the row's decided state).
    mockAuthFetch.mockResolvedValue({ ok: false })
    const { onDecisionCommit } = await openWith({
      ...mixedBandPreview(),
      statementId: 'st-fix-amount',
    })
    await screen.findByText('D')
    const bRow = openRow(
      screen.getByRole('button', { name: /^B, Import/ }).closest('.imp3-arow') as HTMLElement,
    )

    fireEvent.click(within(bRow).getByRole('button', { name: 'Fix the amount' }))
    const input = within(bRow).getByRole('spinbutton', { name: 'Correct amount for B' })
    fireEvent.change(input, { target: { value: '2.75' } })
    fireEvent.click(within(bRow).getByRole('button', { name: 'Use this' }))

    // Confirming the fix includes the row (Fix-and-confirm implies Import) with the
    // corrected figure.
    expect(within(bRow).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    fireEvent.click(document.querySelector('.imp3-commit') as HTMLElement)
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toContainEqual({
      row: 1,
      action: 'create',
      edits: { amount: -2.75 },
    })
  })

  it('Fix amount via Enter records the corrected figure the same way', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false })
    const { onDecisionCommit } = await openWith({
      ...mixedBandPreview(),
      statementId: 'st-fix-undo',
    })
    await screen.findByText('D')
    const bRow = openRow(
      screen.getByRole('button', { name: /^B, Import/ }).closest('.imp3-arow') as HTMLElement,
    )

    fireEvent.click(within(bRow).getByRole('button', { name: 'Fix the amount' }))
    const input = within(bRow).getByRole('spinbutton', { name: 'Correct amount for B' })
    fireEvent.change(input, { target: { value: '2.75' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(within(bRow).queryByRole('spinbutton', { name: 'Correct amount for B' })).toBeNull()

    fireEvent.click(document.querySelector('.imp3-commit') as HTMLElement)
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toContainEqual({
      row: 1,
      action: 'create',
      edits: { amount: -2.75 },
    })
  })

  it('the Not-on-statement chip isolates the ledger-only block; band chips hide it', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('True orphan')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    fireEvent.click(within(nav).getByRole('button', { name: /Not on statement\s*2/ }))
    expect(ledgerSectionCount()).toBe('2')
    expect(screen.queryByText('Y')).toBeNull() // statement rows hidden
    // "X" (review band, confident match) is already auto-decided with the default merge
    // action (owner, 2026-07-27) and moved straight to Will link — nothing left in Needs-you,
    // so that chip doesn't render at all.
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    fireEvent.click(within(nav).getByRole('button', { name: /Will link\s*1/ }))
    expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
    expect(screen.queryByText('True orphan')).toBeNull()
  })
})

// ── Flat list order (owner directive 2026-07-16): statement order, never regrouped ───

function outOfBandOrderPreview() {
  // Statement order deliberately interleaves bands: strong, weak, strong, plain.
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'S1',
        description: 'S1',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:o1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e-o1',
            existing: { note: 's1 twin', amount: -1, currency: 'SGD' },
            band: 'strong',
          },
        ],
      },
      {
        date: '2026-06-02',
        payee: 'WK',
        description: 'WK',
        amount: -2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:o2' }],
        rowIndex: 1,
        flags: [],
      },
      {
        date: '2026-06-03',
        payee: 'S2',
        description: 'S2',
        amount: -3,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:o3' }],
        rowIndex: 2,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e-o2',
            existing: { note: 's2 twin', amount: -3, currency: 'SGD' },
            band: 'strong',
          },
        ],
      },
      {
        date: '2026-06-04',
        payee: 'PL',
        description: 'PL',
        amount: -4,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:o4' }],
        rowIndex: 3,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 0,
    skipped: [],
    total: 4,
    anomaly: {
      near_duplicate: 2,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 2,
    },
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [
        {
          id: 'twin-o1',
          date: '2026-06-02',
          note: 'WK twin',
          amount: -2.06,
          currency: 'SGD',
          kind: 'expense',
          source: 'agent',
          statement_twin: {
            description: 'WK',
            amount: 2,
            date: '2026-06-02',
            row_index: 1,
            booked: false,
            exact: false,
          },
        },
      ],
      boundary: [],
    },
  }
}

const domDraftOrder = () =>
  Array.from(document.querySelectorAll('[data-draft-index]')).map((el) =>
    el.getAttribute('data-draft-index'),
  )

describe('ImportModal — flat list in statement order', () => {
  it('renders rows in rowIndex order under All — a weak row between two strong rows stays between them', async () => {
    await openWith(outOfBandOrderPreview())
    await screen.findByText('PL')
    expect(domDraftOrder()).toEqual(['0', '1', '2', '3'])
    // …and no grouped-section scaffolding remains.
    expect(screen.queryByText(/Matched with your ledger/)).toBeNull()
    expect(screen.queryByText(/New on the statement/)).toBeNull()
    expect(document.querySelector('.imp-pair-colhead')).toBeNull()
  })

  it('each handoff chip filters to its population and preserves statement order within it', async () => {
    // "WK" (weak twin) defaults to its own decisive New at load — Needs you owns nothing
    // here and doesn't even render as a chip.
    await openWith(outOfBandOrderPreview())
    await screen.findByText('PL')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    fireEvent.click(within(nav).getByRole('button', { name: /Will link\s*2/ }))
    expect(domDraftOrder()).toEqual(['0', '2'])
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    fireEvent.click(within(nav).getByRole('button', { name: /Import\s*2/ }))
    expect(domDraftOrder()).toEqual(['1', '3'])
  })
})

// ── Section subtotals + the verdict's own jump (SI-58; waterfall removed 2026-07-31) ─────

function totalsPreview() {
  const base = mixedBandPreview()
  const p = {
    ...base,
    ledgerCheck: {
      ...base.ledgerCheck,
      rows: [
        ...base.ledgerCheck.rows,
        {
          id: 'lo-1',
          date: '2026-06-10',
          note: 'Uncovered row',
          amount: -6,
          currency: 'SGD',
          kind: 'expense',
          source: 'ai',
        },
      ],
      totals: [] as unknown[],
    },
  }
  p.ledgerCheck.totals = [
    {
      account_name: 'Anchor',
      currency: 'SGD',
      ledger_net: -16,
      statement_net: -10,
      statement_unbooked_net: -4,
      uncovered_net: -6,
      boundary_net: 0,
      drift: 0,
      tie: false,
    },
  ]
  return p
}

describe('ImportModal — section subtotals + the verdict jump (SI-58)', () => {
  it('the verdict residue line activates the Not-on-statement chip; no waterfall term survives', async () => {
    await openWith(totalsPreview())
    await screen.findByText('D')
    openVerdict()
    const spy = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>
    spy.mockClear()
    // The waterfall and every jump-term inside it are gone (owner 2026-07-31) — the verdict's
    // own residue line is the surviving route into the ledger-only section.
    expect(screen.queryByRole('button', { name: /Show the math/ })).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'not booked yet (rows you haven’t imported)' }),
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'ledger rows flagged above' })).toBeNull()
    expect(document.querySelector('.imp-math')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /ledger entr(y|ies) from this period/ }))
    expect(spy).toHaveBeenCalled()
    // 2, not 1: `lo-1` from the server plus row "C"'s released weak twin.
    expect(
      screen.getByRole('button', { name: /Not on statement\s*2/ }).getAttribute('aria-pressed'),
    ).toBe('true')
    // The block's header still carries the SAME totals[0] figure the waterfall used to show —
    // the SERVER's `uncovered_net`, which by construction predates any client-side decision
    // (the same snapshot semantics a Delete / manual link / Change match already leave behind).
    const ledgerHead = screen
      .getByText('In your ledger, not on this statement')
      .closest('.imp3-sec-h') as HTMLElement
    expect(within(ledgerHead).getByText('−S$6.00')).toBeTruthy()
    expect(screen.queryByText('D')).toBeNull() // the statement list is hidden under this chip
  })

  it('the verdict issues sentence names the ledger-only count and points below — no "duplicates to clean up" grouping', async () => {
    await openWith(totalsPreview())
    await screen.findByText('D')
    openVerdict()
    expect(
      screen.getByText(/2 ledger entries from this period aren’t on this statement/),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: /ledger entr(y|ies) from this period/ })).toBeTruthy()
    expect(screen.queryByText(/duplicates to clean up/i)).toBeNull()
  })
})

// ── LEDGER-ONLY section (§6.5): one action vocabulary — Delete / Show match ↑ ────────

function ledgerOnlyPreview() {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'X',
        description: 'X',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:lx' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'claimed',
            existing_id: 'e-claimed',
            existing: { note: 'X twin', amount: -1, currency: 'SGD', kind: 'expense' },
            band: 'review',
          },
        ],
      },
      {
        date: '2026-06-02',
        payee: 'Y',
        description: 'Y',
        amount: -2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:ly' }],
        rowIndex: 1,
        flags: [],
      },
      {
        date: '2026-06-03',
        payee: 'Z',
        description: 'Z',
        amount: -3,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:lz' }],
        rowIndex: 2,
        flags: [],
      },
      {
        date: '2026-06-04',
        payee: 'W',
        description: 'W',
        amount: -4,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:lw' }],
        rowIndex: 3,
        flags: [],
      },
      // The L0 duplicate that makes `booked-1` a BOOKED case: id equality on `dupOf`, no
      // matching involved (RFC 2026-07-29 §D3 amendment (d)).
      {
        date: '2026-06-05',
        payee: 'stmt row',
        description: 'stmt row',
        amount: -5,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: true,
        dupOf: 'booked-1',
        external_refs: [{ source: 'pdf', ref: 'ref:a1:lb' }],
        rowIndex: 4,
        flags: [],
      },
    ],
    importable: 4,
    duplicates: 1,
    skipped: [],
    total: 5,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [
        {
          id: 'booked-1',
          date: '2026-06-05',
          note: 'Booked twin row',
          amount: -5,
          currency: 'SGD',
          source: 'ai',
        },
        {
          id: 'orphan-1',
          date: '2026-06-08',
          note: 'True orphan',
          amount: -8,
          currency: 'SGD',
          source: 'ai',
        },
      ],
      boundary: [],
    },
  }
}

describe('ImportModal — LEDGER-ONLY rows (one action vocabulary)', () => {
  // SIUM-LEDGERONLY-BOOKED — the case is decided by `dup && dupOf === entry.id`, id equality,
  // no scoring and no tolerance.
  it('a booked entry reads "Probably a duplicate…" and offers the same four actions', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('Booked twin row')
    const row = rowFor('Booked twin row')
    expect(
      within(row).getByText(
        'Probably a duplicate — your ledger already has this from an earlier import.',
      ),
    ).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Open' })).toBeTruthy()
    expect(within(row).getByRole('button', { name: /Link to a new row/ })).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Leave it' })).toBeTruthy()
    expect(within(row).queryByRole('button', { name: 'Remove' })).toBeNull()
  })

  // SIUM-NO-DEADEND: the third "claimed" case and its dead end are gone by construction — a
  // claimed entry is no longer in `ledger_check.rows` at all.
  it('never renders the "resolve it there first" dead end or a "Show match ↑" button', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('Booked twin row')
    for (const note of ['Booked twin row', 'True orphan']) rowFor(note)
    expect(document.body.textContent).not.toContain('A possible match is already linked above')
    expect(screen.queryByRole('button', { name: /Show match/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Show statement row/ })).toBeNull()
    expect(document.body.textContent).not.toContain('Statement row:')
  })

  it('a true orphan keeps the mid-window copy and a Delete button', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('True orphan')
    const row = rowFor('True orphan')
    expect(
      within(row).getByText(/A real card charge should appear on its own statement/),
    ).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeTruthy()
  })

  // Delete is STAGED (owner, 2026-08-01) — it no longer writes through on click. The row stays
  // put, marked and undoable, and the deletion rides the Confirm commit like every other
  // decision on this screen. The full contract (footer, undo, commit payload, failure) lives in
  // ImportModal.stagedDelete.test.tsx; what this file keeps is the action-vocabulary half.
  it('Delete marks the row instead of writing through — no DELETE call, the row stays', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('True orphan')
    fireEvent.click(within(rowFor('True orphan')).getByRole('button', { name: 'Delete' }))
    await screen.findByText('Will delete')
    expect(screen.getAllByText('True orphan').length).toBeGreaterThan(0)
    expect(mockAuthFetch).not.toHaveBeenCalledWith('/api/transactions/orphan-1', {
      method: 'DELETE',
    })
  })

  it('a marked row offers Undo in place of Delete', async () => {
    await openWith(ledgerOnlyPreview())
    await screen.findByText('True orphan')
    fireEvent.click(within(rowFor('True orphan')).getByRole('button', { name: 'Delete' }))
    await screen.findByText('Will delete')
    const row = rowFor('True orphan')
    expect(within(row).queryByRole('button', { name: 'Delete' })).toBeNull()
    fireEvent.click(within(row).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.queryByText('Will delete')).toBeNull())
  })

  // A released weak partner enters this section as an ORDINARY ledger-only row — same copy,
  // same one action vocabulary. It must never grow a "claimed above" special case: the third
  // dead-end case stays gone (SIUM-NO-DEADEND).
  it('a released weak partner renders as a plain orphan row, with no claimed-above dead end', async () => {
    await openWith(weakPairPreview())
    await findWeakRow()
    // The name also appears on the statement row's own `? Maybe` suggestion line, so address
    // the ledger-only row through its section rather than by text alone.
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    const row = openRow(
      within(sect).getByText('Anthropic Claude API').closest('.imp3-arow') as HTMLElement,
    )
    expect(
      within(row).getByText(/A real card charge should appear on its own statement/),
    ).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeTruthy()
    expect(screen.queryByText(/That row books when you import/)).toBeNull()
    expect(document.body.textContent).not.toContain('A possible match is already linked above')
    expect(screen.queryByRole('button', { name: /Show match/ })).toBeNull()
  })
})

// ── SI-50: ledger column identity line + drawer plumbing (unchanged behavior) ────────

const catList: Category[] = [
  { id: 'cat_transport', label: 'Transport', color: '#3b86dd', kind: 'expense' },
]
const catMap = { cat_transport: catList[0]! }

async function openWithCats(preview: unknown) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
  const utils = render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      categories={catList}
      catMap={catMap}
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

function reviewWithDetail(existingOver: Record<string, unknown> = {}) {
  const p = reviewRowPreview()
  p.drafts[0]!.flags[0]!.existing = { ...p.drafts[0]!.flags[0]!.existing, ...existingOver } as never
  return p
}

describe('ImportModal — ledger column identity line (SI-50 plumbing kept)', () => {
  it('renders account • category • source under the ledger name on a pair row', async () => {
    await openWithCats(reviewWithDetail())
    await screen.findByText('Gopay')
    // Date + amount dropped from this sentence (owner, 2026-07-27: duplicated the
    // .imp3-cmp-facts footer right below it, which already carries both).
    expect(
      within(pairRowTitled('Gopay')).getByText('— Anchor • Transport • via agent'),
    ).toBeTruthy()
  })

  it('source phrase map: ai → via Lucius', async () => {
    await openWithCats(reviewWithDetail({ source: 'ai' }))
    await screen.findByText('Gopay')
    // The source phrase now lives only in the expanded comparison's identity line
    // (.imp3-cmp-src) — the collapsed summary's own copy of it went with the rest of the
    // review-band flag headline (owner, 2026-07-28: "isn't that we decided to not show
    // anything like this?").
    expect(within(pairRowTitled('Gopay')).getAllByText(/via Lucius/).length).toBeGreaterThan(0)
  })

  it('an unresolvable account is OMITTED and an unresolvable category falls back to its raw value — never "undefined"', async () => {
    await openWithCats(reviewWithDetail({ account_id: 'ghost-account', category: 'ghost-cat' }))
    await screen.findByText('Gopay')
    const line = within(pairRowTitled('Gopay')).getByText(/^— /)
    expect(line.textContent).not.toContain('undefined')
    expect(line.textContent).not.toContain('Anchor')
    expect(line.textContent).toContain('ghost-cat')
  })

  it('a blank category reads "Uncategorized", never "undefined"', async () => {
    await openWithCats(reviewWithDetail({ category: null, has_category: false }))
    await screen.findByText('Gopay')
    const line = within(pairRowTitled('Gopay')).getByText(/^— /)
    expect(line.textContent).toContain('Uncategorized')
    expect(line.textContent).not.toContain('undefined')
  })

  it('clicking the quoted ledger name fetches and opens the real transaction drawer', async () => {
    await openWithCats(reviewWithDetail())
    await screen.findByText('Gopay')
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 'existing-review',
        date: '2026-06-09',
        kind: 'expense',
        category: 'cat_transport',
        description: 'Gopay-Gojek',
        memo: '',
        from_account_id: 'a1',
        from_amount: 11.0,
        from_currency: 'SGD',
        base_amount: 11.0,
        base_currency: 'SGD',
        cleared: true,
        source: 'agent',
        tags: [],
        splits: [],
      }),
    } as unknown as Response)
    fireEvent.click(screen.getAllByRole('button', { name: /Gopay-Gojek/ })[0]!)
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/transactions/existing-review'),
    )
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeTruthy())
    const drawer = within(document.querySelector('.acct-detail-wrap') as HTMLElement)
    expect(drawer.getAllByText('Gopay-Gojek').length).toBeGreaterThan(0)
  })

  it('a failed fetch shows inline error feedback instead of failing silently', async () => {
    await openWithCats(reviewWithDetail())
    await screen.findByText('Gopay')
    mockAuthFetch.mockResolvedValue({ ok: false, status: 404 } as Response)
    const row = pairRowTitled('Gopay')
    fireEvent.click(within(row).getAllByRole('button', { name: /Gopay-Gojek/ })[0]!)
    await waitFor(() => expect(row.textContent).toContain('Couldn’t open — try again.'))
    expect(document.querySelector('.acct-detail-wrap')).toBeNull()
  })

  it('closing the drawer returns to the import modal with the selected per-row action intact', async () => {
    const onParentClose = vi.fn()
    await openWith(strongRowPreview(), { onClose: onParentClose })
    await findPairRowFor('Link')
    fireEvent.click(within(pairRowFor('Link')).getByRole('radio', { name: 'Leave out' }))
    await findPairRowFor('Leave out')
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 'existing-strong',
        date: '2026-06-09',
        kind: 'expense',
        category: 'cat_transport',
        description: 'Grab ride',
        memo: '',
        from_account_id: 'a1',
        from_amount: 11.0,
        from_currency: 'SGD',
        base_amount: 11.0,
        base_currency: 'SGD',
        cleared: true,
        source: 'agent',
        tags: [],
        splits: [],
      }),
    } as unknown as Response)
    fireEvent.click(screen.getAllByRole('button', { name: /Grab ride/ })[0]!)
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeTruthy())
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Transaction details' }), {
      key: 'Escape',
    })
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeNull())
    expect(onParentClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Import a statement' })).toBeTruthy()
    expect(pairRowsFor('Leave out').length).toBe(1)
    expect(
      within(pairRowFor('Leave out')).getByRole('radio', { name: 'Leave out' }).className,
    ).toContain('on')
    // Escape on the Decide step now confirms first — a real row decision (the Leave-out
    // override above) is in progress, not yet booked.
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Import a statement' }), { key: 'Escape' })
    expect(onParentClose).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Discard review' }))
    expect(onParentClose).toHaveBeenCalledTimes(1)
  })

  // Regression (code review on merged e9a43f118): the nested drawer's own `drawerTxn` state
  // never refreshed after a detach, so unlinking the LAST source entry left the "Source"
  // heading orphaned (hasSourceRef still true off the stale externalRefs) until the drawer
  // was closed and reopened. Fixed by treating detach like save/mark-reviewed — await the
  // caller's write, then re-fetch via openExistingTxn.
  it("unlinking the drawer's LAST source entry re-fetches the row and drops the Source section", async () => {
    const onDetachTxnSourceRef = vi.fn().mockResolvedValue(undefined)
    const onLoadTxnSourceEntries = vi.fn().mockResolvedValue([
      {
        kind: 'connector',
        source: 'plaid',
        ref: 't1',
        provider: 'plaid',
        providerLabel: 'Plaid',
        providerAccountId: 'linked-acct',
        fetchedAt: '2026-06-09T00:00:00Z',
        raw: {},
        unavailable: false,
      },
    ])
    await openWith(strongRowPreview(), { onDetachTxnSourceRef, onLoadTxnSourceEntries })
    await findPairRowFor('Link')

    let fetchCount = 0
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (url !== '/api/transactions/existing-strong') throw new Error(`unexpected fetch ${url}`)
      fetchCount += 1
      return {
        ok: true,
        json: async () => ({
          id: 'existing-strong',
          date: '2026-06-09',
          kind: 'expense',
          category: 'cat_transport',
          description: 'Grab ride',
          memo: '',
          from_account_id: 'a1',
          from_amount: 11.0,
          from_currency: 'SGD',
          base_amount: 11.0,
          base_currency: 'SGD',
          cleared: true,
          source: 'agent',
          tags: [],
          splits: [],
          // The LAST entry — gone by the second (post-detach) fetch.
          external_refs: fetchCount === 1 ? [{ source: 'plaid', ref: 't1' }] : [],
        }),
      } as unknown as Response
    })
    fireEvent.click(screen.getAllByRole('button', { name: /Grab ride/ })[0]!)
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeTruthy())
    const drawer = document.querySelector('.acct-detail-wrap') as HTMLElement

    await waitFor(() => expect(within(drawer).getByText('Unlink source')).toBeTruthy())
    fireEvent.click(within(drawer).getByText('Unlink source'))
    fireEvent.click(screen.getByRole('button', { name: 'Unlink source' }))

    expect(onDetachTxnSourceRef).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'existing-strong' }),
      'plaid',
      't1',
    )
    // The detach write re-triggers openExistingTxn (a second GET of the same row) — proof the
    // drawer refreshed from the server rather than trusting the write's own promise.
    await waitFor(() => expect(fetchCount).toBe(2))
    // With externalRefs now empty, hasSourceRef flips false and the WHOLE section disappears —
    // the pre-existing "no provenance" state, not an orphaned heading over an empty list.
    await waitFor(() =>
      expect(within(drawer).queryByText('Source', { selector: '.acct-detail-sec' })).toBeNull(),
    )
    expect(within(drawer).queryByText('Unlink source')).toBeNull()
  })
})

// ── Outcome line: original-charge gap-fill (unchanged copy) + after-column mirror ────

function willLinkPreviewWithOriginal(
  draftOrig: Record<string, unknown>,
  existingOrig: Record<string, unknown> = {},
) {
  return {
    drafts: [
      {
        date: '2026-06-09',
        payee: 'Anthropic',
        description: 'Anthropic',
        amount: -14.66,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:anthropic' }],
        ...draftOrig,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'books-close',
            existing_id: 'existing-anthropic',
            existing: {
              note: 'Anthropic',
              amount: -14.66,
              currency: 'SGD',
              date: '2026-06-09',
              kind: 'expense',
              ...existingOrig,
            },
            band: 'strong',
            promoted: true,
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

describe('ImportModal — original-charge gap-fill', () => {
  it('outcome line reads "adds the original charge" and the after column marks it "+ original charge"', async () => {
    await openWith(
      willLinkPreviewWithOriginal({ original_amount: 11.09, original_currency: 'USD' }),
    )
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(within(after).getByText(/adds the original charge|\+ original charge/)).toBeTruthy()
    expect(within(after).getByText('+ original charge $11.09')).toBeTruthy()
  })

  it('renders no gap-fill when the existing row already has an original amount (merge never overwrites it)', async () => {
    await openWith(
      willLinkPreviewWithOriginal(
        { original_amount: 11.09, original_currency: 'USD' },
        { original_amount: 10.5, original_currency: 'USD' },
      ),
    )
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(within(after).getByText(/cleared ✓/)).toBeTruthy()
    expect(within(after).queryByText(/\+ original charge/)).toBeNull()
    expect(within(after).getByText('original $10.50')).toBeTruthy() // the existing one, kept
  })
})

// ── Statement meta line: minimum_payment (untouched region) ──────────────────────────

function previewWithMeta(meta: Record<string, unknown>) {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'X',
        description: 'X',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:metax' }],
        flags: [],
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
    meta,
  }
}

describe('ImportModal — statement meta line minimum payment', () => {
  it('shows "min …" immediately after the amount-due fact when minimum_payment is present', async () => {
    await openWith(
      previewWithMeta({
        payment_due_date: '2026-06-30',
        amount_due: 3000.0,
        minimum_payment: 386.02,
        currency: 'SGD',
      }),
    )
    await screen.findByText('X')
    expect(screen.getByText('Due Jun 30, 2026 • S$3,000.00 • min S$386.02')).toBeTruthy()
  })

  it('renders no min fragment when minimum_payment is absent or zero', async () => {
    await openWith(
      previewWithMeta({
        payment_due_date: '2026-06-30',
        amount_due: 3000.0,
        minimum_payment: 0,
        currency: 'SGD',
      }),
    )
    await screen.findByText('X')
    expect(screen.getByText('Due Jun 30, 2026 • S$3,000.00')).toBeTruthy()
  })
})

// ── §2b transfer_pair formation honesty (headline now carries the label) ─────────────

function transferPairPreview(band: 'strong' | 'review') {
  return {
    drafts: [
      {
        date: '2026-06-05',
        payee: 'ATM',
        description: 'ATM withdrawal',
        amount: -200,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:tp1' }],
        flags: [
          {
            type: 'transfer_pair',
            reason: 'opposite-direction legs across two accounts, same base amount & near date',
            existing_id: 'existing-tp',
            existing: {
              note: 'Top-up received',
              amount: 200,
              currency: 'SGD',
              date: '2026-06-04',
              kind: 'income',
            },
            band,
          },
        ],
      },
    ],
    importable: 1,
    duplicates: 0,
    skipped: [],
    total: 1,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 1,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

describe('ImportModal — transfer_pair formation copy (§2b)', () => {
  it('a review-band transfer_pair row is TEMPORARILY quiet about "Opposite legs — one transfer?", never the old existing-transfer claim', async () => {
    // TEMPORARILY HIDDEN — the FLAG_LABEL headline + engine-reason bits are suppressed for
    // transfer_pair/transfer_leg_echo specifically (see `reviewFlagHeadline` in ImportModal.tsx);
    // the row still reads via its Ledger name/amount and decision controls. Revert this test
    // alongside that guard.
    await openWith(transferPairPreview('review'))
    await screen.findByText('Top-up received')
    expect(screen.queryByText('Opposite legs — one transfer?')).toBeNull()
    expect(screen.queryByText('Matches an existing transfer')).toBeNull()
  })

  it('a will-link transfer_pair merge becomes a real transfer — its own leg wins date + amount, not the existing figure', async () => {
    // Neither side is a transfer yet — Link builds one (apply_import_transfer_pair_merge,
    // backend/services/ledger.py) instead of the same-kind-only fact merge near_duplicate/
    // transfer_leg_echo use. owner repro 2026-07-28: this used to silently keep the row as a
    // plain income/expense and never establish the transfer relationship.
    await openWith(transferPairPreview('strong'))
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(within(after).getByText(/cleared ✓/)).toBeTruthy()
    expect(within(after).getByText('becomes a transfer')).toBeTruthy()
    // The statement's own date wins (the existing income's 06/04 is struck through).
    expect(after.querySelector('s.imp-after-old')?.textContent).toBe('06/04')
    expect(within(after).getByText('06/05')).toBeTruthy()
    // The draft is the debit leg — its own -$200 magnitude is the projected leg amount,
    // never averaged/replaced by the existing income's figure.
    expect(within(after).getByText('S$200.00')).toBeTruthy()
  })

  it('offers a Transfer kind-filter option once a transfer_pair row will actually book as one', async () => {
    // owner repro 2026-07-28: the kind filter only ever checked `d.routed` (a Lucius-routed
    // transfer op), so a transfer_pair match that becomes a real transfer via Link never
    // showed a Transfer option at all — effectiveKind must mirror actionFor exactly, the
    // same lockstep discipline verdictInput/confirmImport already follow.
    const fixture = transferPairPreview('strong')
    fixture.drafts.push({
      date: '2026-06-06',
      payee: 'Grocer',
      description: 'Grocer run',
      amount: -12,
      currency: 'SGD',
      kind: 'expense',
      category: null,
      dup: false,
      external_refs: [{ source: 'pdf', ref: 'ref:a1:tp2' }],
      flags: [],
    } as never)
    fixture.importable = 2
    fixture.total = 2
    await openWith(fixture)
    await findPairRowFor('Link')
    fireEvent.click(screen.getByRole('button', { name: 'Filter by kind' }))
    const pop = screen.getByRole('listbox')
    expect(within(pop).getByRole('option', { name: /Transfer\s*1/ })).toBeTruthy()
    fireEvent.click(within(pop).getByRole('option', { name: /Transfer\s*1/ }))
    expect(screen.getByText('Top-up received')).toBeTruthy()
    expect(screen.queryByText('Grocer run')).toBeNull()
  })
})

// ── SI-51: boundary bucket — unchanged, re-parented under LEDGER-ONLY ────────────────

function boundaryPreview(boundary?: Array<Record<string, unknown>>) {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'X',
        description: 'X',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:bx' }],
        flags: [],
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
      rows: [],
      // `on_statements` and `near_cutoff` are SERVER fields (ledger_statement_links.py /
      // ledger_diff.py `_near_cutoff`) — the fixture carries them exactly as the wire does,
      // because the UI must not be able to reconstruct either from `period` + `date` on its own.
      // `bnd-open` carries NO statement: the honest wire shape for a row booked by an agent and
      // never seen on a statement, which must render as an absent term, not a substitution.
      boundary: boundary ?? [
        {
          id: 'bnd-close',
          date: '2026-06-29',
          note: 'Coffee after close',
          amount: -5.5,
          currency: 'SGD',
          kind: 'expense',
          source: 'ai',
          account_id: 'a1',
          category: 'cat_transport',
          on_statements: {
            label: '1–30 Jun 2026 statement',
            reference: null,
            statements: [
              {
                statement_id: 'stmt_jun',
                label: '1–30 Jun 2026 statement',
                filename: 'jun.pdf',
                period_start: '2026-06-01',
                period_end: '2026-06-30',
              },
            ],
          },
          near_cutoff: { edge: 'end', days: 1, label: '1 day before closing' },
        },
        {
          id: 'bnd-open',
          date: '2026-06-01',
          note: 'Grocery near open',
          amount: -40,
          currency: 'SGD',
          kind: 'expense',
          source: 'agent',
          account_id: 'a1',
          category: null,
          on_statements: null,
          near_cutoff: { edge: 'start', days: 1, label: '1 day after opening' },
        },
      ],
    },
  }
}

describe('ImportModal — boundary bucket (SI-51)', () => {
  it('is collapsed by default and the verdict stays clean (boundary is not "missing")', async () => {
    await openWithCats(boundaryPreview())
    await screen.findByText(/2 entries sit near the cut-off/)
    openVerdict()
    expect(screen.queryByText('Coffee after close')).toBeNull()
    expect(screen.getByText(/Every statement transaction will be in your ledger/)).toBeTruthy()
    expect(screen.queryByText(/from this period/)).toBeNull() // no residue line — boundary is not "missing"
  })

  // The grouping key is the SERVER's `near_cutoff.edge`. It replaced a client `date >
  // period.end` test that was dead code: `period` on the wire is the already-widened window
  // and no emitted row can fall outside it, so the "dated after the close" group never
  // rendered once. Opening edge first — it is the likelier surprise (last cycle's tail).
  it('expands into two sub-groups keyed by the server edge, opening then closing', async () => {
    await openWithCats(boundaryPreview())
    fireEvent.click(await screen.findByRole('button', { name: /2 entries sit near the cut-off/ }))
    const section = document.querySelector('.acct-stmtview-check-boundary') as HTMLElement
    const order = Array.from(
      section.querySelectorAll(
        '.acct-stmtview-check-group, .acct-stmtview-check-row .imp-txn-link',
      ),
    ).map((el) => el.textContent)
    expect(order).toEqual([
      'Near the opening cut-off — likely the previous statement',
      'Grocery near open',
      'Near the closing cut-off — likely the next statement',
      'Coffee after close',
    ])
  })

  it('each row renders account • category • origin • cut-off, and no Delete/Show-match action', async () => {
    await openWithCats(boundaryPreview())
    fireEvent.click(await screen.findByRole('button', { name: /2 entries sit near the cut-off/ }))
    const closeRow = screen
      .getByRole('button', { name: 'Coffee after close' })
      .closest('.acct-stmtview-check-row') as HTMLElement
    expect(within(closeRow).getByText('2026-06-29')).toBeTruthy()
    // The last two terms are SERVER strings rendered verbatim — the old third term was the
    // client's `via Lucius` provenance phrase, which never answered "which statement".
    expect(
      within(closeRow).getByText(
        'Anchor • Transport • 1–30 Jun 2026 statement • 1 day before closing',
      ),
    ).toBeTruthy()
    expect(within(closeRow).queryByRole('button', { name: 'Delete' })).toBeNull()
    expect(within(closeRow).queryByRole('button', { name: 'Show match ↑' })).toBeNull()
    // On no statement we hold: the term drops out entirely rather than degrading to where the
    // row came FROM (the 2026-07-31 "Already on: <import batch>" bug).
    const openRow2 = screen
      .getByRole('button', { name: 'Grocery near open' })
      .closest('.acct-stmtview-check-row') as HTMLElement
    expect(within(openRow2).getByText('Anchor • Uncategorized • 1 day after opening')).toBeTruthy()
  })

  it('clicking a boundary row name opens the real transaction drawer', async () => {
    await openWithCats(boundaryPreview())
    fireEvent.click(await screen.findByRole('button', { name: /2 entries sit near the cut-off/ }))
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 'bnd-close',
        date: '2026-06-29',
        kind: 'expense',
        category: 'cat_transport',
        description: 'Coffee after close',
        memo: '',
        from_account_id: 'a1',
        from_amount: 5.5,
        from_currency: 'SGD',
        base_amount: 5.5,
        base_currency: 'SGD',
        cleared: true,
        source: 'ai',
        tags: [],
        splits: [],
      }),
    } as unknown as Response)
    fireEvent.click(screen.getByRole('button', { name: 'Coffee after close' }))
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalledWith('/api/transactions/bnd-close'))
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeTruthy())
  })

  it('renders nothing when boundary is empty (no dead toggle)', async () => {
    await openWithCats(boundaryPreview([]))
    await screen.findByText('X')
    expect(screen.queryByText(/near the cut-off/)).toBeNull()
    expect(document.querySelector('.acct-stmtview-check-boundary')).toBeNull()
  })

  // SUPERSEDES SI-56's "the waterfall's near-the-cut-off term force-expands the bucket": the
  // waterfall is gone (owner 2026-07-31), so the bucket's own toggle is the only way in —
  // pinned by "renders a collapsed boundary bucket…" above.
  it('no waterfall term offers a second way into the bucket', async () => {
    const p = boundaryPreview() as ReturnType<typeof boundaryPreview> & {
      ledgerCheck: { totals?: unknown[] }
    }
    p.ledgerCheck.totals = [
      {
        account_name: 'Anchor',
        currency: 'SGD',
        ledger_net: -46.5,
        statement_net: -1,
        statement_unbooked_net: -1,
        uncovered_net: 0,
        boundary_net: -45.5,
        drift: 0,
        tie: false,
      },
    ]
    await openWithCats(p)
    await screen.findByText('X')
    openVerdict()
    expect(screen.queryByRole('button', { name: /Show the math/ })).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'near the cut-off (adjacent statement)' }),
    ).toBeNull()
    expect(document.querySelector('.imp-math')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /2 entries sit near the cut-off/ }))
    await screen.findByText('Coffee after close')
  })
})

// ── SI-49: fully-reconciled statement ────────────────────────────────────────────────

function cleanStatementPreview() {
  return {
    drafts: [
      {
        date: '2026-06-01',
        payee: 'New row',
        description: 'New row',
        amount: -1,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:clean1' }],
        flags: [],
      },
      {
        date: '2026-06-02',
        payee: 'Linked row',
        description: 'Linked row',
        amount: -2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:clean2' }],
        flags: [
          {
            type: 'near_duplicate',
            reason: 'x',
            existing_id: 'e2',
            existing: { note: 'linked', amount: -2, currency: 'SGD' },
            band: 'strong',
          },
        ],
      },
    ],
    importable: 2,
    duplicates: 0,
    skipped: [],
    total: 2,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

describe('ImportModal — a fully-reconciled statement (SI-49)', () => {
  it('renders the strong pair row inline with the new row — no band/section headers, no question rows, honest footer', async () => {
    await openWith(cleanStatementPreview())
    await screen.findAllByText('Linked row')
    expect(pairRowsFor('Link').length).toBe(1)
    // Contextual bulk bars are chip-gated — under the default All nothing renders.
    expect(document.querySelector('.imp-pair-bandhead')).toBeNull()
    expect(screen.queryByText(/Needs your decision/)).toBeNull()
    expect(screen.queryByText(/Weak match — treated as new/)).toBeNull()
    expect(screen.queryByText('Already in your account?')).toBeNull()
    expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
    const footer = document.querySelector('.imp3-foot-note') as HTMLElement
    expect(within(footer).getByText('Link 1 • import 1')).toBeTruthy()
  })
})

// ── Contextual band bulk bars: only with their chip active, still band-wide ──────────

describe('ImportModal — contextual band bulk bars', () => {
  it('Link all in Will link preserves an explicit decision now owned by Left out', async () => {
    await openWith(outOfBandOrderPreview())
    await screen.findByText('PL')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    const firstStrong = openRow(document.querySelector('[data-draft-index=\"0\"]') as HTMLElement)
    fireEvent.click(within(firstStrong).getByRole('radio', { name: 'Leave out' }))

    fireEvent.click(within(nav).getByRole('button', { name: /Will link\s*1/ }))
    const head = document.querySelector('.imp-pair-bandhead') as HTMLElement
    fireEvent.click(within(head).getByRole('button', { name: 'Link all' }))

    fireEvent.click(within(nav).getByRole('button', { name: /Left out\s*1/ }))
    const preserved = openRow(document.querySelector('[data-draft-index=\"0\"]') as HTMLElement)
    expect(
      within(preserved).getByRole('radio', { name: 'Leave out' }).getAttribute('aria-checked'),
    ).toBe('true')
  })

  it('the obsolete weak bulk path is absent, and Needs-you owns nothing once everything auto-decides', async () => {
    await openWith(mixedBandPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('D')
    expect(screen.queryByRole('button', { name: 'Leave all out' })).toBeNull()
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).queryByRole('button', { name: /Weak match/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Link all/ })).toBeNull()
    // "A", "B", and "C" all default to their own decided outcome at load — Needs-you owns
    // nothing and doesn't even render as a chip.
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(document.querySelectorAll('.imp3-qcard')).toHaveLength(0)
  })

  // SIUM-REVIEW-QUEUE. The old behaviour under test here was: re-tapping a weak row's
  // already-selected default twice cleared the override and dropped the row BACK into the
  // Needs-you queue as a card. A weak row must never enter that queue at all now — it has a
  // safe default, so demanding attention for it is exactly the lie this change removes. The
  // toggle itself is unchanged; it just no longer has a queue to fall into.
  it('a weak row never enters the Needs-you queue, however its decision is toggled', async () => {
    await openWith(mixedBandPreview())
    await screen.findByText('D')
    const cRow = () => weakRow()
    fireEvent.click(within(cRow()).getByRole('radio', { name: 'Import' })) // explicit, no-op value
    fireEvent.click(within(cRow()).getByRole('radio', { name: 'Import' })) // clears the override

    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(within(nav).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(document.querySelectorAll('.imp3-qcard')).toHaveLength(0)
    // It is still reachable — through its own lens, which owns no partition.
    fireEvent.click(within(nav).getByRole('button', { name: /Possible matches\s*1/ }))
    expect(document.querySelectorAll('[data-draft-index]')).toHaveLength(1)
  })
})

// ── Toggle semantics (owner amendment 2026-07-16): re-tap clears the override ─────────

describe('ImportModal — action chips toggle back to the band default', () => {
  it('strong row: tap Skip → skipped; tap Skip again → the merge default returns (no dead end)', async () => {
    await openWith(strongRowPreview())
    await findPairRowFor('Link')
    fireEvent.click(within(pairRowFor('Link')).getByRole('radio', { name: 'Leave out' }))
    await findPairRowFor('Leave out')
    expect(
      within(pairRowFor('Leave out'))
        .getByRole('radio', { name: 'Leave out' })
        .getAttribute('aria-checked'),
    ).toBe('true')
    fireEvent.click(within(pairRowFor('Leave out')).getByRole('radio', { name: 'Leave out' }))
    // The headline and the single-record merge projection return.
    await findPairRowFor('Link')
    const after = afterCol(pairRowFor('Link'))
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    expect(within(after).getByText('reference attached')).toBeTruthy()
    expect(document.querySelector('.imp3-foot-note')?.textContent).toContain('Link 1')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })

  it('review row: tap Import new twice → back to the merge default (profile opt-in ladder)', async () => {
    await openWith(reviewRowPreview(), { importReviewDefault: 'merge' })
    await screen.findByText('Gopay')
    const row = () => pairRowTitled('Gopay')
    fireEvent.click(within(row()).getByRole('radio', { name: 'Import' }))
    expect(within(row()).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    expect(within(afterCol(row())).getByText(/After import • 2 records/)).toBeTruthy()
    fireEvent.click(within(row()).getByRole('radio', { name: 'Import' }))
    expect(within(row()).getByRole('radio', { name: 'Link' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    expect(within(afterCol(row())).getByText('reference attached')).toBeTruthy()
  })

  it('bulk-set then re-tap one row: that row toggles off while the rest keep the bulk value', async () => {
    await openWith(outOfBandOrderPreview())
    await screen.findByText('PL')
    const nav = screen.getByRole('navigation', { name: 'Filter statement rows' })
    fireEvent.click(within(nav).getByRole('button', { name: /Will link\s*2/ }))
    const head = document.querySelector('.imp-pair-bandhead') as HTMLElement
    fireEvent.click(within(head).getByRole('button', { name: 'Leave all out' }))
    expect(pairRowsFor('Leave out').length).toBe(2)
    const first = openRow(document.querySelector('[data-draft-index="0"]') as HTMLElement)
    fireEvent.click(within(first).getByRole('radio', { name: 'Leave out' }))
    expect(chipOf(first)).toBe('Link')
    const second = document.querySelector('[data-draft-index="2"]') as HTMLElement
    expect(chipOf(second)).toBe('Leave out')
  })
})

// ── The ✦ "Categorised by Lucius" category-chip marker is REMOVED ────

describe('ImportModal — the ✦ category-chip marker is gone', () => {
  it('never renders on a plain row, auto-categorized or not', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
    const onPreview = vi.fn().mockResolvedValue({
      drafts: [
        {
          date: '2026-06-05',
          payee: 'Grab',
          description: 'Grab *Ride 8821',
          amount: -8.2,
          currency: 'SGD',
          kind: 'expense',
          category: 'cat_transport',
          autoCat: true,
          dup: false,
          external_refs: [{ source: 'pdf', ref: 'ref:a1:auto1' }],
          flags: [],
        },
        {
          date: '2026-06-06',
          payee: 'Cafe',
          description: 'Cafe purchase',
          amount: -5,
          currency: 'SGD',
          kind: 'expense',
          category: 'cat_transport',
          autoCat: false,
          dup: false,
          external_refs: [{ source: 'pdf', ref: 'ref:a1:manual1' }],
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
    })
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        categories={catList}
        catMap={catMap}
        onClose={() => {}}
        onPreview={onPreview as never}
        onDecisionCommit={onDecisionCommit}
      />,
    )
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, {
      target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
    })
    fireEvent.click(await screen.findByText(/Read the statement/i))
    await screen.findByText('Grab')

    const autoRow = screen.getByText('Grab').closest('.imp3-arow') as HTMLElement
    expect(autoRow.querySelector('.imp3-arow-catcol .imp3-cat em')).toBeNull()
    const manualRow = screen.getByText('Cafe').closest('.imp3-arow') as HTMLElement
    expect(manualRow.querySelector('.imp3-arow-catcol .imp3-cat em')).toBeNull()
  })

  it('never renders on a matched row either, LINK or not', async () => {
    const preview = strongRowPreview()
    ;(preview.drafts[0] as unknown as { category: string; autoCat: boolean }).category =
      'cat_transport'
    ;(preview.drafts[0] as unknown as { autoCat: boolean }).autoCat = true
    await openWithCats(preview)
    await findPairRowFor('Link')
    const row = pairRowFor('Link')
    expect(row.querySelector('.imp3-cat em')).toBeNull()
  })
})
