// @vitest-environment jsdom
//
// Characterization boundary for the Statement Import handoff port. These assertions pin the
// production decision/write seam before the review DOM is restructured: the port may change
// hierarchy, labels, and controls, but not which deviations reach the cached decision commit or
// how the real batch-reviewed callback behaves.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

afterEach(cleanup)

const account = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account

const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

function mixedProductionPreview() {
  return {
    statementId: 'st-characterization',
    drafts: [
      {
        ...base,
        rowIndex: 0,
        date: '2026-06-01',
        payee: 'Strong',
        description: 'Strong match',
        amount: -10,
        external_refs: [{ source: 'csv', ref: 'strong' }],
        flags: [
          {
            type: 'near_duplicate',
            band: 'strong',
            reason: 'exact books-close match',
            existing_id: 'ledger-strong',
            existing: {
              note: 'Strong ledger',
              date: '2026-06-01',
              amount: -10,
              currency: 'SGD',
              kind: 'expense',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 1,
        date: '2026-06-02',
        payee: 'Review',
        description: 'Review match',
        amount: -20,
        external_refs: [{ source: 'csv', ref: 'review' }],
        flags: [
          {
            type: 'near_duplicate',
            band: 'review',
            reason: 'possible match',
            existing_id: 'ledger-review',
            existing: {
              note: 'Review ledger',
              date: '2026-06-02',
              amount: -20,
              currency: 'SGD',
              kind: 'expense',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 2,
        date: '2026-06-03',
        payee: 'Weak',
        description: 'Weak match',
        amount: -30,
        external_refs: [{ source: 'csv', ref: 'weak' }],
        // The settled-drift tier is an ENGINE band now (RFC 2026-07-29 §D2), not a client-side
        // join over `ledgerCheck.rows[].statement_twin`.
        flags: [
          {
            type: 'near_duplicate',
            band: 'weak',
            reason: 'settled 3.3% off',
            existing_id: 'ledger-weak',
            score: 0.28,
            alternatives: [],
            existing: {
              note: 'Weak ledger',
              date: '2026-06-03',
              amount: -29,
              currency: 'SGD',
              kind: 'expense',
              source: 'manual',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 3,
        date: '2026-06-04',
        payee: 'Clean',
        description: 'Clean import',
        amount: -40,
        external_refs: [{ source: 'csv', ref: 'clean' }],
        flags: [],
      },
      {
        ...base,
        rowIndex: 4,
        date: '2026-06-05',
        payee: 'Needs eyes',
        description: 'Low confidence',
        amount: -50,
        needsEyes: true,
        confidence: 0.4,
        notes: ['Amount could not be read confidently'],
        external_refs: [{ source: 'csv', ref: 'eyes' }],
        flags: [],
      },
      {
        ...base,
        rowIndex: 5,
        date: '2026-06-06',
        payee: 'Lucius skip',
        description: 'Pending row',
        amount: -60,
        lucius: { op: 'skip', reason: 'Pending rows do not book' },
        external_refs: [{ source: 'csv', ref: 'lucius-skip' }],
        flags: [],
      },
      {
        ...base,
        rowIndex: 6,
        date: '2026-05-01',
        payee: 'Before reconcile',
        description: 'Older than reconciliation',
        amount: -70,
        preRecon: true,
        external_refs: [{ source: 'csv', ref: 'pre-recon' }],
        flags: [],
      },
    ],
    // `ledger-weak` is claimed by row 2's weak flag, so the server's set difference already
    // dropped it from the ledger check.
    ledgerCheck: { rows: [], boundary: [] },
    importable: 7,
    duplicates: 0,
    skipped: [],
    total: 7,
    anomaly: {
      near_duplicate: 3,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 2,
    },
  }
}

async function openPreview(extra: Record<string, unknown> = {}) {
  const onPreview = vi.fn().mockResolvedValue(mixedProductionPreview())
  const onDecisionCommit = vi.fn().mockResolvedValue({
    created: 2,
    merged: 1,
    duplicates: 0,
    skipped: 4,
    batchId: 'imp-characterization',
  })
  const view = render(
    <ImportModal
      open
      accounts={[account]}
      defaultAccountId="a1"
      importReviewDefault="skip"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
      {...extra}
    />,
  )
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, {
    target: {
      files: [new File(['Date,Description,Amount'], 'statement.csv', { type: 'text/csv' })],
    },
  })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/i }))
  await waitFor(() => {
    expect(document.body.querySelector('.imp3-arow-match')?.textContent).toContain('Strong ledger')
  })
  return { ...view, onDecisionCommit }
}

describe('ImportModal handoff port — production behavior characterization', () => {
  it('commits the same mixed-state deviations through the one cached decision seam', async () => {
    const { onDecisionCommit } = await openPreview()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit).toHaveBeenCalledWith(
      'st-characterization',
      'a1',
      [
        { row: 0, action: 'merge', existing_id: 'ledger-strong' },
        { row: 1, action: 'skip' },
        { row: 2, action: 'create' },
        // Row 4 ("Needs eyes", no ledger match) now defaults to Import, not skip
        // (owner, 2026-07-28).
        { row: 4, action: 'create' },
        { row: 6, action: 'skip' },
      ],
      [],
    )
  })

  it('keeps Mark all reviewed on the real retryable batch callback', async () => {
    const onMarkImportReviewed = vi.fn().mockResolvedValue({ updated: 2 })
    await openPreview({ onMarkImportReviewed })

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText(/^Imported /)).toBeTruthy()
    expect(document.querySelector('.ui-modal-card--md')).toBeTruthy()
    expect(document.querySelector('.ui-modal-card--workspace')).toBeNull()
    expect(
      Array.from(document.querySelectorAll('.imp3-crumb')).map((node) => node.textContent),
    ).toEqual(['Choose', 'Read', 'Decide'])
    expect(
      Array.from(document.querySelectorAll('.imp3-booked-stat')).map((n) => n.textContent),
    ).toEqual(['2added ', '1linkedto entries you had', '3left outby you'])
    expect(screen.getByText('2 new rows are marked to review in the register')).toBeTruthy()
    const mark = await screen.findByRole('button', { name: 'Mark all reviewed' })
    fireEvent.click(mark)

    await waitFor(() => expect(onMarkImportReviewed).toHaveBeenCalledWith('imp-characterization'))
    expect(await screen.findByText('✓ All 2 new rows marked reviewed')).toBeTruthy()
  })

  it('offers "View in register" on the Booked screen, wired to the real batch id', async () => {
    // The register filter behind it unions the batch's created rows with the ones it linked.
    const onViewImportBatch = vi.fn()
    await openPreview({ onViewImportBatch })

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText(/^Imported /)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'View in register' }))
    expect(onViewImportBatch).toHaveBeenCalledWith('imp-characterization', 'Import: statement.csv')
  })

  it('omits "View in register" when no batch id came back, or no callback was wired', async () => {
    await openPreview() // no onViewImportBatch at all
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText(/^Imported /)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'View in register' })).toBeNull()
  })

  it('Undo import on the Booked screen undoes the batch and Review again reopens the same review', async () => {
    const onUndoImport = vi.fn().mockResolvedValue({ deleted: 2, unmerged: 1 })
    const { onDecisionCommit } = await openPreview({ onUndoImport })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('Imported statement.csv')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Undo import' }))
    fireEvent.click(document.querySelector('.imp3-undo-go') as HTMLButtonElement)
    await waitFor(() => expect(onUndoImport).toHaveBeenCalledWith('imp-characterization'))
    expect(await screen.findByText('Import undone')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Review again' }))
    // The review itself, not a re-read: the rows are back and Confirm books again.
    expect(document.body.querySelector('.imp3-arow-match')?.textContent).toContain('Strong ledger')
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy()
    expect(onDecisionCommit).toHaveBeenCalledTimes(1)
  })

  it('shows a loading indicator while the commit round-trip is in flight', async () => {
    // The commit can take a few seconds ("no load indicator is showing up") — the button
    // itself must read as busy for as long as onDecisionCommit's promise is unresolved.
    let resolveCommit!: (v: {
      created: number
      merged: number
      duplicates: number
      skipped: number
    }) => void
    const onDecisionCommit = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveCommit = resolve
        }),
    )
    await openPreview({ onDecisionCommit })

    const confirmBtn = screen.getByRole('button', { name: 'Confirm' })
    fireEvent.click(confirmBtn)

    const busyBtn = await screen.findByRole('button', { name: /Booking…/ })
    expect(busyBtn.getAttribute('aria-busy')).toBe('true')
    expect((busyBtn as HTMLButtonElement).disabled).toBe(true)
    expect(document.body.querySelector('.imp3-commit-spin')).toBeTruthy()
    // Clicking again while in flight must not fire a second commit.
    fireEvent.click(busyBtn)
    expect(onDecisionCommit).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveCommit({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
    })
    expect(await screen.findByText(/^Imported /)).toBeTruthy()
  })
})
