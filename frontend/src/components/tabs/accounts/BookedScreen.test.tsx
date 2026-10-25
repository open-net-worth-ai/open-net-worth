// @vitest-environment jsdom
//
// The Booked screen and its Undo import: the confirm panel says what undo does, the undo goes
// through the caller's batch callback, and "Review again" hands back to the same review.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BookedScreen, type BookedScreenProps } from './BookedScreen'

afterEach(cleanup)

function renderBooked(over: Partial<BookedScreenProps> = {}) {
  const props: BookedScreenProps = {
    result: {
      created: 21,
      merged: 37,
      duplicates: 0,
      skipped: 2,
      accounts: 1,
      transfers: 3,
      batchId: 'b1',
    },
    filename: 'momo.pdf',
    context: 'MoMo · 1 Oct – 25 Oct · 60 rows',
    deleteErrors: [],
    refusedDuplicates: [],
    doneDiff: { core: 0, boundary: 0 },
    taught: false,
    reviewMarkState: 'idle',
    reviewMarkError: '',
    onMarkAllReviewed: vi.fn(),
    onViewInRegister: vi.fn(),
    onUndo: vi.fn().mockResolvedValue({ deleted: 21, unmerged: 37 }),
    onReviewAgain: vi.fn(),
    onImportAnother: vi.fn(),
    onDone: vi.fn(),
    ...over,
  }
  render(<BookedScreen {...props} />)
  return props
}

describe('BookedScreen', () => {
  it('summarises the import as the mock: title, context, three tiles, one balance line', () => {
    renderBooked()
    expect(screen.getByText('Imported momo.pdf')).toBeTruthy()
    expect(screen.getByText('MoMo · 1 Oct – 25 Oct · 60 rows')).toBeTruthy()
    expect(
      Array.from(document.querySelectorAll('.imp3-booked-stat')).map((n) => n.textContent),
    ).toEqual(['21added3 as transfers', '37linkedto entries you had', '2left outby you'])
    expect(screen.getByText('Statement and ledger agree for this period')).toBeTruthy()
    expect(screen.getByText('21 new rows are marked to review in the register')).toBeTruthy()
  })

  it('asks before undoing, and Keep it changes nothing', () => {
    const p = renderBooked()
    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }))
    const panel = screen.getByRole('alertdialog', { name: 'Undo this import?' })
    expect(panel.textContent).toContain(
      'The 21 added rows are removed and the 37 linked entries go back to how they were. The statement returns to Ready to review.',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(p.onUndo).not.toHaveBeenCalled()
  })

  it('undoes through the batch callback, then offers Review again', async () => {
    const p = renderBooked()
    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }))
    fireEvent.click(
      screen
        .getAllByRole('button', { name: 'Undo import' })
        .find((b) => b.className === 'imp3-undo-go')!,
    )
    expect(p.onUndo).toHaveBeenCalledWith('b1')
    expect(await screen.findByText('Import undone')).toBeTruthy()
    expect(screen.getByText(/21 rows removed · 37 entries restored/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Review again' }))
    expect(p.onReviewAgain).toHaveBeenCalled()
  })

  it('keeps the confirm panel open with the reason when the undo fails', async () => {
    renderBooked({ onUndo: vi.fn().mockRejectedValue(new Error('This import is already undone')) })
    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }))
    fireEvent.click(
      screen
        .getAllByRole('button', { name: 'Undo import' })
        .find((b) => b.className === 'imp3-undo-go')!,
    )
    expect(await screen.findByText('This import is already undone')).toBeTruthy()
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    expect(screen.queryByText('Import undone')).toBeNull()
  })

  it('offers no Undo when the commit returned no batch or no undo is wired', () => {
    renderBooked({ result: { created: 0, duplicates: 0, skipped: 5, accounts: 1, batchId: null } })
    expect(screen.queryByRole('button', { name: 'Undo import' })).toBeNull()
    cleanup()
    renderBooked({ onUndo: undefined })
    expect(screen.queryByRole('button', { name: 'Undo import' })).toBeNull()
  })

  it('says rows the user deleted stay deleted after an undo', () => {
    renderBooked({
      result: {
        created: 2,
        merged: 0,
        duplicates: 0,
        skipped: 0,
        accounts: 1,
        deleted: 1,
        batchId: 'b1',
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }))
    expect(screen.getByRole('alertdialog').textContent).toContain(
      'The 1 row you deleted stays deleted.',
    )
  })
})
