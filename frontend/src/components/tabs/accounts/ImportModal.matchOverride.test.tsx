// @vitest-environment jsdom
//
// "Change match" on an already-matched row: the engine's pick is a suggestion, not the only
// option, so the owner can re-point the row via a top-priority override (`matchOverride`),
// distinct from the `manualLink` map (which stays scoped to plain unmatched rows).
//
// RFC 2026-07-29 §D8 rewired where the OPTIONS come from. They used to be the client's own
// eligibility pass over `ledgerCheck` (every uncovered entry, minus whatever the client's twin
// join had consumed). They are now `flags[].alternatives` — the SERVER's own per-row list,
// rendered verbatim, best-first, one summary per entry. That means:
//   * the control is band-agnostic (one picker, every band);
//   * an EMPTY `alternatives` renders no control at all (SIUM-ALT-NONE), instead of a live
//     control that opens onto every uncovered entry in the period;
//   * every listed option is pickable — the engine only offers entries no other row claimed,
//     so `decision_commit`'s uncovered-set path accepts each one.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

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
  Element.prototype.scrollIntoView = vi.fn()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
})

const acct = {
  id: 'v1',
  name: 'Harborline Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'VND',
  color: '#22d3ee',
} as unknown as Account
const base = {
  currency: 'VND',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

// Rows used to be located by their verdict headline ('✓ Will link', /Linked by you/). That
// whole sub-line is gone (owner, 2026-07-29 — it trailed raw matcher diagnostics), so these
// single-pair-row fixtures address THE pair row directly and assert the decision via the
// `.imp3-dchip` chip, which is what the user actually reads now.
const pairRowEl = () => document.querySelector('.imp-pair-row') as HTMLElement
const pairRow = () => openRow(pairRowEl())
const findPairRow = async () => {
  await waitFor(() => expect(pairRowEl()).toBeTruthy())
  return pairRow()
}
const decisionChip = (row: HTMLElement) => row.querySelector('.imp3-dchip')?.textContent
const ledgerCol = (row: HTMLElement) => {
  const sides = row.querySelectorAll('.imp3-cmp > .imp-pair-side')
  return sides[1] as HTMLElement
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
      defaultAccountId="v1"
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

const OTHER = {
  note: 'Different Grab charge',
  amount: -75000,
  currency: 'VND',
  date: '2026-06-19',
  kind: 'expense',
  source: 'manual',
}

// A strong-band engine match (Grab, matched to `e-grab`) whose flag ALSO carries one
// server-derived alternative (`led-other`) the user believes is the real match instead. The
// same entry stays in `ledgerCheck.rows`: an alternative is unclaimed by construction, so the
// server's set difference still lists it as uncovered.
function strongMatchPreview(extra: Record<string, unknown> = {}) {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-20',
        payee: 'Grab',
        description: 'GRAB',
        amount: -80000,
        external_refs: [{ source: 'pdf', ref: 'ref:v1:g1' }],
        rowIndex: 0,
        flags: [
          {
            type: 'near_duplicate',
            reason: 'sure',
            existing_id: 'e-grab',
            band: 'strong',
            existing: {
              note: 'Grab ride',
              amount: -80000,
              currency: 'VND',
              date: '2026-06-20',
              kind: 'expense',
            },
            alternatives: [{ id: 'led-other', existing: OTHER }],
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
      rows: [{ id: 'led-other', ...OTHER }],
      boundary: [],
    },
    ...extra,
  }
}

function transferPairOriginalPreview() {
  const preview = strongMatchPreview()
  preview.drafts[0]!.flags[0]!.type = 'transfer_pair'
  preview.drafts[0]!.flags[0]!.existing = {
    note: 'Opposite transfer leg',
    amount: 80000,
    currency: 'VND',
    date: '2026-06-20',
    kind: 'income',
  }
  return preview
}

describe('ImportModal — "Change match" on an already-matched row', () => {
  it('ports the handoff scan hierarchy: verdict, category, amount, and ledger evidence are visible before expansion', async () => {
    await openWith(strongMatchPreview())
    await waitFor(() => expect(pairRowEl()).toBeTruthy())
    // deliberately NOT the auto-opening locator: this case asserts what is visible CLOSED
    const row = pairRowEl()
    expect(row.querySelector('.imp3-arow-line')).toBeTruthy()
    expect(row.querySelector('.imp3-dchip')?.textContent).toBe('Link')
    expect(row.querySelector('.imp3-arow-catcol')?.textContent).toContain('Uncategorized')
    expect(row.querySelector('.imp3-arow-match')?.textContent).toMatch(/Ledger.*Grab ride/)
    expect(row.querySelector('.imp3-arow-match')?.textContent).toMatch(/₫80,000/)
    const disclosure = row.querySelector('.imp3-arow-line') as HTMLButtonElement
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(within(row).getByText('On the statement')).toBeTruthy()
    expect(within(row).getByText('In your ledger')).toBeTruthy()
    // Labeled by the actual decision (Link here), not a generic "After import".
    expect(within(row).getByText('After link')).toBeTruthy()
  })

  // Owner, 2026-07-29: "please stop showing these lucius thing/reason to users" — the engine's
  // own matcher diagnostics were leaking verbatim into the row. The decision chip stays.
  it('never renders the engine match reason, collapsed or expanded', async () => {
    await openWith(strongMatchPreview())
    const row = await findPairRow() // expanded — the comparison card is in scope too
    expect(decisionChip(row)).toBe('Link')
    expect(row.textContent).not.toContain('sure') // the fixture's flag.reason
    expect(row.textContent).not.toContain('✓ Will link') // the verdict sub-line itself
    expect(row.querySelector('.imp3-arow-match-src')).toBeNull()
    // The evidence a user CAN read is untouched.
    expect(within(row).getByText('In your ledger')).toBeTruthy()
    expect(row.textContent).toContain('Grab ride')
  })

  // SIUM-ALT-PICKER / SIUM-ALT-NONE: the control follows the SERVER's list, not the band.
  it('renders exactly the server\u2019s `alternatives`, in the order they arrived', async () => {
    await openWith(strongMatchPreview())
    await findPairRow()
    const row = pairRow()
    fireEvent.click(within(row).getByRole('button', { name: /Change match/ }))
    const list = screen.getByRole('listbox', { name: 'Match with a different ledger entry' })
    const options = within(list).getAllByRole('option')
    expect(options).toHaveLength(1) // === flags[0].alternatives.length, nothing added or dropped
    expect(options[0]!.textContent).toContain('Different Grab charge')
    // The engine only offers entries no other row claimed, so nothing is ever dimmed.
    expect(options.every((o) => !(o as HTMLButtonElement).disabled)).toBe(true)
  })

  it('renders NO `Change match` control when the flag carries no alternatives', async () => {
    const preview = strongMatchPreview()
    preview.drafts[0]!.flags[0]!.alternatives = []
    await openWith(preview)
    await findPairRow()
    const row = pairRow()
    expect(within(row).queryByRole('button', { name: /Change match/ })).toBeNull()
    // The row's own decision controls are untouched.
    expect(within(row).getByRole('radio', { name: 'Link' })).toBeTruthy()
    expect(within(row).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
  })

  it('re-points the row: ledger-now column, headline, and "not on statement" panel all follow', async () => {
    await openWith(strongMatchPreview({ statementId: 'st-grab' }))
    await findPairRow()
    // Before: the uncovered entry sits in the "not on statement" panel.
    expect(ledgerSectionCount()).toBe('1')
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    const list = screen.getByRole('listbox', { name: 'Match with a different ledger entry' })
    fireEvent.click(within(list).getByRole('option', { name: /Different Grab charge/ }))
    const row = pairRow()
    expect(within(ledgerCol(row)).getByText(/Different Grab charge/)).toBeTruthy()
    expect(within(ledgerCol(row)).queryByText(/Grab ride/)).toBeNull()
    // The re-pointed-to entry is now accounted for, and the engine's ORIGINAL pick is released
    // in its place (owner, 2026-10-26): the panel still lists exactly one entry — the one this
    // row no longer covers.
    expect(ledgerSectionCount()).toBe('1')
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByText('Grab ride')).toBeTruthy()
    expect(within(sect).queryByText(/Different Grab charge/)).toBeNull()
  })

  it("the commit payload carries the user-picked existing_id, not the engine's original match", async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(strongMatchPreview({ statementId: 'st-grab' }), { onDecisionCommit })
    await findPairRow()
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    fireEvent.click(screen.getByRole('option', { name: /Different Grab charge/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-other' },
    ])
  })

  it('"Back to Lucius\'s pick" reverts the row to the engine\'s original match', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(strongMatchPreview({ statementId: 'st-grab' }), { onDecisionCommit })
    await findPairRow()
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    fireEvent.click(screen.getByRole('option', { name: /Different Grab charge/ }))
    const overriddenRow = pairRow()
    fireEvent.click(within(overriddenRow).getByRole('button', { name: /Back to Lucius's pick/ }))
    // Reverted: headline + ledger column back to the engine's own match; the picker returns.
    const row = pairRow()
    expect(within(ledgerCol(row)).getByText(/Grab ride/)).toBeTruthy()
    expect(within(row).getByRole('button', { name: /Change match/ })).toBeTruthy()
    expect(ledgerSectionCount()).toBe('1')
    // Commit now carries the ENGINE's original existing_id again.
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'e-grab' },
    ])
  })

  // The one shape where "this entry is spoken for" (matchOverride) and "this row rejected it"
  // (Import on a match, ADR 2026-07-31) collide on the same entry. The row's live decision
  // wins: it books as new, so BOTH entries it points at — the engine's original pick and the
  // re-pointed one — are uncovered and must be listed.
  it('a WEAK row re-pointed and then set to Import releases the picked entry too', async () => {
    const p = strongMatchPreview()
    p.drafts[0]!.flags[0]!.band = 'weak'
    await openWith(p)
    await findPairRow()
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    fireEvent.click(screen.getByRole('option', { name: /Different Grab charge/ }))
    // Picking sets the row to Link, so the picked entry is claimed — only the engine's
    // original pick, which this row no longer covers, is listed.
    expect(ledgerSectionCount()).toBe('1')
    expect(
      within(document.querySelector('.imp3-sec.ledger') as HTMLElement).queryByText(
        /Different Grab charge/,
      ),
    ).toBeNull()
    fireEvent.click(within(pairRow()).getByRole('radio', { name: 'Import' }))
    expect(ledgerSectionCount()).toBe('2')
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByText(/Different Grab charge/)).toBeTruthy()
    expect(within(sect).getByText('Grab ride')).toBeTruthy()
  })

  it('a STRONG row re-pointed and then set to Import releases the picked entry as well (every band)', async () => {
    await openWith(strongMatchPreview())
    await findPairRow()
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    fireEvent.click(screen.getByRole('option', { name: /Different Grab charge/ }))
    fireEvent.click(within(pairRow()).getByRole('radio', { name: 'Import' }))
    expect(ledgerSectionCount()).toBe('2')
    const sect = document.querySelector('.imp3-sec.ledger') as HTMLElement
    expect(within(sect).getByText(/Different Grab charge/)).toBeTruthy()
    expect(within(sect).getByText('Grab ride')).toBeTruthy()
  })

  it('does not carry a transfer-only flag onto a normal replacement match', async () => {
    await openWith(transferPairOriginalPreview())
    await findPairRow()
    const verdictBtn = document.querySelector('.imp3-vsummary') as HTMLElement
    fireEvent.click(verdictBtn)
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Change match/ }))
    fireEvent.click(screen.getByRole('option', { name: /Different Grab charge/ }))

    const row = pairRow()
    const after = row.querySelector('.imp3-after') as HTMLElement
    expect(within(after).getByText('-₫75,000', { selector: 's.imp-after-old' })).toBeTruthy()
    expect(within(after).getByText('-₫80,000', { selector: '.imp-after-new' })).toBeTruthy()
    // the whole before→after move reads as one line in the After record (struck old → new)
    expect((after.querySelector('.imp3-after-rec') as HTMLElement).textContent).toContain(
      '-₫75,000→-₫80,000',
    )
    expect(screen.getByText(/Amounts match the statement to the cent/)).toBeTruthy()
    expect(screen.queryByText(/date, amount, category & notes stay as they are/)).toBeNull()
  })
})
