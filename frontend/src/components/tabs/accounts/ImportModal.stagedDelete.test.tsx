// @vitest-environment jsdom
//
// The ledger-only "Delete" is STAGED, not written through (owner, 2026-08-01: "i have to delete
// every row by myself, can we also mark them as delete and only do in the confirm button
// instead of delete one by one?").
//
// Before this, each row's Delete fired `DELETE /api/transactions/<id>` on click — one
// irreversible round-trip per row, before the user had committed anything, under a footer that
// promises "nothing is written until you confirm". Every other decision on this screen is
// staged and applied at Confirm; Delete was the one that wasn't.
//
// Four things are pinned here, and they are the four that make a destructive staged action safe:
//
//   1. staged-but-not-confirmed writes NOTHING
//   2. Confirm deletes EXACTLY the marked rows (and the footer said so first)
//   3. unmark restores — a mis-click costs nothing
//   4. a failed delete SURFACES rather than being silently dropped
//
// A staged delete that silently fires, fires twice, or fires on the wrong row is worse than the
// write-through behaviour it replaced.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
  vi.spyOn(window, 'confirm').mockReturnValue(true)
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

const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}
const rowFor = (note: string) =>
  openRow(screen.getAllByText(note)[0]!.closest('.imp3-arow') as HTMLElement)
const footerText = () => document.querySelector('.imp3-foot-note')?.textContent ?? ''

// Three uncovered ledger entries + one plain statement row to import — the owner's shape
// (several duplicate ride charges sitting under one statement being reviewed).
function preview() {
  return {
    drafts: [
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
        rowIndex: 0,
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
    statementId: 'st-staged',
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [
        {
          id: 'ride-1',
          date: '2026-06-05',
          note: 'Grab ride A',
          amount: -13.71,
          currency: 'SGD',
          source: 'agent',
        },
        {
          id: 'ride-2',
          date: '2026-06-06',
          note: 'Grab ride B',
          amount: -9.4,
          currency: 'SGD',
          source: 'agent',
        },
        {
          id: 'keep-1',
          date: '2026-06-07',
          note: 'Groceries',
          amount: -52.1,
          currency: 'SGD',
          source: 'ai',
        },
      ],
      boundary: [],
    },
  }
}

async function openWith(p: unknown = preview(), commitResult: Record<string, unknown> = {}) {
  const onDecisionCommit = vi.fn().mockResolvedValue({
    created: 1,
    merged: 0,
    duplicates: 0,
    skipped: 0,
    deleted: 0,
    deleteErrors: [],
    ...commitResult,
  })
  const onPreview = vi.fn().mockResolvedValue(p)
  render(
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
  await screen.findByText('Grab ride A')
  return { onDecisionCommit }
}

const mark = (note: string) =>
  fireEvent.click(within(rowFor(note)).getByRole('button', { name: 'Delete' }))

// ── 1. staged, not fired ─────────────────────────────────────────────────────────────────

describe('staged delete — nothing is written until you confirm', () => {
  it('marking a row makes NO network call at all', async () => {
    await openWith()
    mockAuthFetch.mockClear()

    mark('Grab ride A')
    await screen.findByText('Will delete')

    expect(mockAuthFetch).not.toHaveBeenCalled()
  })

  it('marking every row still writes nothing, and no row leaves the panel', async () => {
    await openWith()
    mockAuthFetch.mockClear()

    mark('Grab ride A')
    mark('Grab ride B')
    mark('Groceries')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(3))

    expect(mockAuthFetch).not.toHaveBeenCalled()
    // the entries are still real and still on screen — they are marked, not removed
    expect(document.querySelector('.imp3-sec.ledger .imp3-sec-badge')?.textContent).toBe('3')
  })

  it('abandoning the import without confirming writes nothing', async () => {
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    await screen.findByText('Will delete')
    mockAuthFetch.mockClear()

    expect(onDecisionCommit).not.toHaveBeenCalled()
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })
})

// ── 2. the footer says so, then Confirm deletes exactly those ────────────────────────────

describe('staged delete — the footer summary', () => {
  it('names the destructive count, distinctly from "leave out"', async () => {
    await openWith()
    expect(footerText()).not.toContain('delete')

    mark('Grab ride A')
    mark('Grab ride B')

    await waitFor(() => expect(footerText()).toContain('delete 2 from your ledger'))
    // it is emphasised in its own right, not folded into the neutral outcome run
    expect(document.querySelector('.imp3-foot-del')?.textContent).toBe('delete 2 from your ledger')
    expect(footerText()).toContain('nothing is written until you confirm')
  })

  it('drops back out of the summary when the marks are cleared', async () => {
    await openWith()
    mark('Grab ride A')
    await waitFor(() => expect(footerText()).toContain('delete 1 from your ledger'))

    fireEvent.click(within(rowFor('Grab ride A')).getByRole('button', { name: 'Undo' }))

    await waitFor(() => expect(footerText()).not.toContain('from your ledger'))
  })

  it('a statement with nothing to book but rows marked is still confirmable', async () => {
    // Deletions ARE an outcome — the CTA must not read "Nothing to book" and sit disabled.
    // The statement's one row is an already-imported duplicate, so nothing links or imports.
    const p = preview()
    await openWith({
      ...p,
      drafts: [{ ...p.drafts[0]!, dup: true, dupOf: 'seen-before' }],
      importable: 0,
      duplicates: 1,
    })
    mark('Grab ride A')

    await waitFor(() => expect(footerText()).toContain('delete 1 from your ledger'))
    const confirm = screen.getByRole('button', { name: 'Confirm' })
    expect((confirm as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('staged delete — confirm deletes exactly the marked rows', () => {
  it('sends precisely the marked ids on the commit, and spares the rest', async () => {
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    mark('Grab ride B')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual(['ride-1', 'ride-2'])
    // 'keep-1' was never marked and must not be in the payload
    expect(onDecisionCommit.mock.calls[0]![3]).not.toContain('keep-1')
  })

  it('sends an EMPTY list when nothing is marked — never a stray id', async () => {
    const { onDecisionCommit } = await openWith()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual([])
  })

  it('marking the same row twice queues it ONCE, not twice', async () => {
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    await screen.findByText('Will delete')
    // second tap is Undo, third re-marks — the id must appear exactly once either way
    fireEvent.click(within(rowFor('Grab ride A')).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.queryByText('Will delete')).toBeNull())
    mark('Grab ride A')
    await screen.findByText('Will delete')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual(['ride-1'])
  })

  it('asks for ONE confirmation naming the count, not one per row', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    mark('Grab ride B')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(2))
    confirmSpy.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0]![0]).toContain('Delete 2 transactions from your ledger?')
  })

  it('declining that confirmation commits NOTHING — not even the import', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    await screen.findByText('Will delete')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy())
    expect(onDecisionCommit).not.toHaveBeenCalled()
  })

  it('does not prompt at all when nothing is marked', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { onDecisionCommit } = await openWith()
    confirmSpy.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

// ── 3. unmark restores ───────────────────────────────────────────────────────────────────

describe('staged delete — unmark restores the row completely', () => {
  it('restores the chip, the hint and the full action vocabulary', async () => {
    await openWith()
    mark('Grab ride A')
    await screen.findByText('Will delete')

    fireEvent.click(within(rowFor('Grab ride A')).getByRole('button', { name: 'Undo' }))

    await waitFor(() => expect(screen.queryByText('Will delete')).toBeNull())
    const row = rowFor('Grab ride A')
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Leave it' })).toBeTruthy()
    expect(within(row).queryByRole('button', { name: 'Undo' })).toBeNull()
  })

  it('unmarking one row leaves the others marked', async () => {
    const { onDecisionCommit } = await openWith()
    mark('Grab ride A')
    mark('Grab ride B')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(2))

    fireEvent.click(within(rowFor('Grab ride A')).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(1))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual(['ride-2'])
  })
})

// ── the bulk affordance (existing buttons-only bar shape, no selection paradigm) ─────────

describe('staged delete — bulk marking', () => {
  it('marks every entry in the section in one tap, and clears them in one', async () => {
    const { onDecisionCommit } = await openWith()

    fireEvent.click(screen.getByRole('button', { name: /Mark all 3 for deletion/ }))
    await waitFor(() => expect(footerText()).toContain('delete 3 from your ledger'))

    fireEvent.click(screen.getByRole('button', { name: 'Clear marks' }))
    await waitFor(() => expect(footerText()).not.toContain('from your ledger'))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual([])
  })

  it('bulk-marking then confirming sends every id exactly once', async () => {
    const { onDecisionCommit } = await openWith()

    fireEvent.click(screen.getByRole('button', { name: /Mark all 3 for deletion/ }))
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(3))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![3]).toEqual(['ride-1', 'ride-2', 'keep-1'])
  })

  it('offers no Clear control until something is actually marked', async () => {
    await openWith()
    expect(screen.queryByRole('button', { name: 'Clear marks' })).toBeNull()
  })
})

// ── 4. a failed delete surfaces ──────────────────────────────────────────────────────────

describe('staged delete — failures surface', () => {
  it('reports a delete the server refused, on the Done screen', async () => {
    await openWith(preview(), {
      created: 1,
      deleted: 1,
      deleteErrors: [{ id: 'ride-2', error: 'the Starting Balance transaction can’t be deleted' }],
    })
    mark('Grab ride A')
    mark('Grab ride B')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await screen.findByText(/^Imported /)
    expect(screen.getByText(/1 marked transaction could not be deleted/)).toBeTruthy()
    expect(document.body.textContent).toContain('Starting Balance')
    // and it says what DID happen, so the user is not left guessing
    expect(document.body.textContent).toContain('1 row deleted from your ledger')
  })

  it('does not claim a deletion the server never made', async () => {
    await openWith(preview(), {
      created: 1,
      deleted: 0,
      deleteErrors: [{ id: 'ride-1', error: 'transaction not found' }],
    })
    mark('Grab ride A')
    await screen.findByText('Will delete')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await screen.findByText(/^Imported /)
    expect(document.body.textContent).not.toContain('1 row deleted from your ledger')
    expect(screen.getByText(/1 marked transaction could not be deleted/)).toBeTruthy()
  })

  it('a clean commit reports the deletions and no failure banner', async () => {
    await openWith(preview(), { created: 1, deleted: 2, deleteErrors: [] })
    mark('Grab ride A')
    mark('Grab ride B')
    await waitFor(() => expect(screen.getAllByText('Will delete')).toHaveLength(2))

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await screen.findByText(/^Imported /)
    expect(document.body.textContent).toContain('2 rows deleted from your ledger')
    expect(screen.queryByText(/could not be deleted/)).toBeNull()
  })
})
