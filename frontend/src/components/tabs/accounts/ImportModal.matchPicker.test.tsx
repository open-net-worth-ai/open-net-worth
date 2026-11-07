// @vitest-environment jsdom
//
// The match picker, after RFC 2026-07-29 §D8 (acceptance: SIUM-ALT-*).
//
// This file used to characterize a CLIENT-SIDE twin matcher: the browser re-derived which
// ledger entry each weak row paired with, from `ledgerCheck.twin_eligible` + per-row
// `statement_twin` annotations, and then hand-rolled 1:1 exclusivity on top — a steal (take an
// entry another row holds by default and that row falls to its next free candidate) and a swap
// (take one another row holds by an explicit pick and the two trade partners). Every one of
// those behaviours is DELETED, not moved: there is one match engine now, it resolves 1:1
// exclusivity server-side, and the wire carries its result as `flags[].band` plus
// `flags[].alternatives`.
//
// So the questions worth asking changed shape:
//   * does the client render the server's option list VERBATIM (no add, drop, dim, re-order)?
//   * is a pick an ordinary `matchOverride` that re-points every surface at once?
//   * does an EMPTY list render no control at all, rather than a dead affordance?
// A crossed pair is no longer a client concern at all — the engine's global claim assignment
// resolves it before the preview is sent, and `_attach_alternatives` never offers an entry
// another row claimed, so a "steal" is unrepresentable on the wire.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

// The ledger-only rows follow the prototype's LedgerOnlyRow: the closed line collapses and the
// hint + actions live in the body, so open the row before reaching into it.
const openLedgerRow = (scope: HTMLElement, name: RegExp): HTMLElement => {
  const btn = within(scope).getByRole('button', { name })
  const row = btn.closest('.imp3-arow') as HTMLElement
  const line = row.querySelector('.imp3-arow-line') as HTMLElement
  if (line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

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
  Element.prototype.scrollIntoView = vi.fn()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
})

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account
const ledgerCol = (row: HTMLElement) => {
  const sides = row.querySelectorAll('.imp3-cmp > .imp-pair-side')
  return sides[1] as HTMLElement
}
const afterCol = (row: HTMLElement) => row.querySelector('.imp3-after') as HTMLElement
// Every row-level action shares one bordered-pill CTA family on the row's imp3-actrow line,
// so the picker is reached through the row rather than the ledger column.
const changeMatch = (row: HTMLElement) => within(row).getByRole('button', { name: /Change match/ })
const weakRow = (n = 0) => openRow(document.querySelectorAll('.imp-pair-row')[n] as HTMLElement)
const findPairRows = () =>
  waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())

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
  return utils
}

const A = {
  note: 'Anthropic A',
  amount: -32.4,
  currency: 'SGD',
  date: '2026-06-19',
  kind: 'expense',
  source: 'agent',
}
const B = {
  note: 'Anthropic B',
  amount: -32.41,
  currency: 'SGD',
  date: '2026-06-18',
  kind: 'expense',
  source: 'agent',
}

// The 32.40/32.41 shape, as the SERVER now sends it: one weak-band row assigned to A,
// with B carried on the same flag as an alternative. B is unclaimed by construction, so the
// ledger check still lists it as uncovered; A is claimed, so the server already removed it.
function twoCandidatePreview(extra: Record<string, unknown> = {}) {
  return {
    drafts: [
      {
        date: '2026-06-18',
        payee: 'ANTHROPIC',
        description: 'ANTHROPIC',
        amount: -32.41,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:pick1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'same account, settled 0.03% off',
            existing_id: 'led-a',
            band: 'weak',
            score: 0.31,
            existing: A,
            alternatives: [{ id: 'led-b', existing: B }],
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
      rows: [{ id: 'led-b', ...B }],
      boundary: [],
    },
    ...extra,
  }
}

// The engine derived no alternative for this row — its assignment is the only option.
function soleCandidatePreview() {
  const p = twoCandidatePreview()
  p.drafts[0]!.flags[0]!.alternatives = []
  p.ledgerCheck.rows = []
  return p
}

// A long list, to exercise the picker's own search field (it appears past 4 options).
function manyCandidatePreview() {
  const p = twoCandidatePreview()
  p.drafts[0]!.flags[0]!.alternatives = [
    { id: 'led-b', existing: B },
    { id: 'led-c', existing: { ...B, note: 'Cursor subscription' } },
    { id: 'led-d', existing: { ...B, note: 'Dropbox renewal' } },
    { id: 'led-e', existing: { ...B, note: 'Extra charge' } },
    { id: 'led-f', existing: { ...B, note: 'Figma seat' } },
  ]
  return p
}

describe('ImportModal — the picker is the server’s `alternatives`, verbatim (SIUM-ALT-PICKER)', () => {
  it('an alternative on the wire → the Change match affordance, wired as a listbox', async () => {
    await openWith(twoCandidatePreview())
    await findPairRows()
    const btn = changeMatch(weakRow())
    expect(btn.getAttribute('aria-haspopup')).toBe('listbox')
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(btn)
    expect(btn.getAttribute('aria-expanded')).toBe('true')
  })

  // SIUM-ALT-NONE: never a dead control that opens onto an empty list.
  it('an EMPTY alternatives array → no affordance at all, other controls unaffected', async () => {
    await openWith(soleCandidatePreview())
    await findPairRows()
    expect(screen.queryByRole('button', { name: /Change match/ })).toBeNull()
    const row = weakRow()
    expect(within(row).getByRole('radio', { name: 'Same one — link' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Import' })).toBeTruthy()
  })

  it('a flag with no alternatives key at all (older preview) → no affordance', async () => {
    const p = twoCandidatePreview()
    delete (p.drafts[0]!.flags[0] as Record<string, unknown>).alternatives
    await openWith(p)
    await findPairRows()
    expect(screen.queryByRole('button', { name: /Change match/ })).toBeNull()
  })

  // The option count equals the response's own array length: no option added, dropped, dimmed
  // or re-ordered by the client. The engine's list excludes the row's own assignment, so the
  // starting state has no `· current` entry — "Back to Lucius's pick" is the way back.
  it('names every entry the server sent, in server order, none of them dimmed', async () => {
    await openWith(twoCandidatePreview())
    await findPairRows()
    fireEvent.click(changeMatch(weakRow()))
    const list = screen.getByRole('listbox', { name: 'Match with a different ledger entry' })
    const opts = within(list).getAllByRole('option')
    expect(opts.length).toBe(1)
    expect(opts[0]!.textContent).toContain('06/18 • Anthropic B • -S$32.41 (same amount)')
    expect(opts[0]!.getAttribute('aria-selected')).toBe('false')
    expect((opts[0] as HTMLButtonElement).disabled).toBe(false)
  })

  it('the picked option becomes the marked `• current` one', async () => {
    await openWith(twoCandidatePreview())
    await findPairRows()
    fireEvent.click(changeMatch(weakRow()))
    fireEvent.click(screen.getByRole('option', { name: /Anthropic B/ }))
    fireEvent.click(changeMatch(weakRow()))
    const opt = screen.getByRole('option', { name: /Anthropic B/ })
    expect(opt.getAttribute('aria-selected')).toBe('true')
    expect(opt.textContent).toContain('• current')
  })

  // SIUM-ALT-SEARCH
  it('past four options a search field filters the list; Escape closes only the popover', async () => {
    await openWith(manyCandidatePreview())
    await findPairRows()
    const btn = changeMatch(weakRow())
    fireEvent.click(btn)
    const list = screen.getByRole('listbox', { name: 'Match with a different ledger entry' })
    expect(within(list).getAllByRole('option').length).toBe(5)
    fireEvent.change(screen.getByLabelText('Search entries'), { target: { value: 'Figma' } })
    const shown = within(screen.getByRole('listbox')).getAllByRole('option')
    expect(shown.length).toBe(1)
    expect(shown[0]!.textContent).toContain('Figma seat')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    // The modal stayed open and nothing re-pointed.
    expect(within(ledgerCol(weakRow())).getByText(/Anthropic A/)).toBeTruthy()
  })
})

describe('ImportModal — a pick re-points every surface (SIUM-ALT-PICK-REPOINTS)', () => {
  async function pickB() {
    await openWith(twoCandidatePreview({ statementId: 'st-pick' }), {
      onDecisionCommit: vi
        .fn()
        .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 }),
    })
    await findPairRows()
    fireEvent.click(changeMatch(weakRow()))
    fireEvent.click(screen.getByRole('option', { name: /Anthropic B/ }))
  }

  it('ledger column, decision and After projection all follow the pick', async () => {
    await pickB()
    const row = weakRow()
    const col = ledgerCol(row)
    expect(within(col).getByText(/Anthropic B/)).toBeTruthy()
    expect(within(col).queryByText(/Anthropic A/)).toBeNull()
    // An explicit partner choice is affirmative intent — the decision flips to Link, visibly.
    expect(
      within(row).getByRole('radio', { name: 'Same one — link' }).getAttribute('aria-checked'),
    ).toBe('true')
    expect(row.querySelector('.imp3-dchip')?.textContent).toBe('Link')
    const after = afterCol(row)
    expect(after.querySelectorAll('.imp-after-rec').length).toBe(1)
    expect(within(after).getByText('Anthropic B')).toBeTruthy()
    // Each badge is its own chip (the design's imp3-after-rec <em>s), not one joined string.
    expect(within(after).getByText('cleared ✓')).toBeTruthy()
    expect(within(after).getByText('reference attached')).toBeTruthy()
  })

  it('the re-pointed-to entry leaves "In your ledger, not on this statement"', async () => {
    await pickB()
    expect(document.querySelector('.imp3-sec.ledger')).toBeNull()
  })

  it('the commit payload carries the PICKED existing_id on the merge decision', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(twoCandidatePreview({ statementId: 'st-pick' }), { onDecisionCommit })
    await findPairRows()
    fireEvent.click(changeMatch(weakRow()))
    fireEvent.click(screen.getByRole('option', { name: /Anthropic B/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-b' },
    ])
  })

  // SIUM-ALT-BACK
  it('"Back to Lucius’s pick" restores the engine’s own partner everywhere and disappears', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(twoCandidatePreview({ statementId: 'st-pick' }), { onDecisionCommit })
    await findPairRows()
    fireEvent.click(changeMatch(weakRow()))
    fireEvent.click(screen.getByRole('option', { name: /Anthropic B/ }))
    fireEvent.click(within(weakRow()).getByRole('button', { name: /Back to Lucius's pick/ }))
    const row = weakRow()
    expect(within(ledgerCol(row)).getByText(/Anthropic A/)).toBeTruthy()
    expect(within(afterCol(row)).getByText('Anthropic A')).toBeTruthy()
    expect(within(row).queryByRole('button', { name: /Back to Lucius's pick/ })).toBeNull()
    // B is uncovered again, so it returns to the ledger-only block.
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByRole('button', { name: /Anthropic B/ })).toBeTruthy()
    // …and the entry there reads as an ORPHAN, never "resolve it above first" (SIUM-NO-DEADEND).
    const entry = openLedgerRow(sect, /Anthropic B/)
    expect(entry.textContent).toContain('A real card charge should appear on its own statement')
    expect(within(entry).queryByRole('button', { name: /Show match/ })).toBeNull()
  })
})
