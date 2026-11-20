// @vitest-environment jsdom
//
// Statement-import category resolution. The row's `category` field already carries whatever
// the screen displays — a manual pick, a matched rule, a payee default, or the ✦ AI hint (all
// resolved server-side into a real category id during preview, see
// backend/services/statements/enrich.py) — so confirming without touching a row must send that
// id back as an ordinary explicit edit. No separate "hint" concept on the wire: whatever is
// shown on screen is what gets booked, via the SAME edits.category path a manual pick already
// used before this fix.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const base = { currency: 'SGD', kind: 'expense', dup: false, preRecon: false, autoCat: false }

function previewWithCategory(category: string | null) {
  return {
    statementId: 'st-cathint',
    drafts: [
      {
        ...base,
        rowIndex: 0,
        date: '2026-07-01',
        payee: 'Grab',
        description: 'Grab ride',
        amount: -12,
        category,
        autoCat: category != null,
        external_refs: [{ source: 'pdf', ref: 'grab-1' }],
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
  }
}

async function openAndConfirm(preview: unknown) {
  const onPreview = vi.fn().mockResolvedValue(preview)
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
  render(
    <ImportModal
      open
      accounts={[account]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
    />,
  )
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, {
    target: { files: [new File(['%PDF-1.4'], 'statement.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/i }))
  await waitFor(() => expect(screen.getByText('Grab ride')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
  return onDecisionCommit
}

describe('ImportModal — a shown category is booked without being touched', () => {
  it('sends the row category id as an explicit edit when the screen already resolved one', async () => {
    const onDecisionCommit = await openAndConfirm(previewWithCategory('cat_transport'))
    // 4th arg = the staged ledger deletions (none marked here) — see
    // ImportModal.stagedDelete.test.tsx for that contract; 5th = the uploaded source file.
    expect(onDecisionCommit).toHaveBeenCalledWith(
      'st-cathint',
      'a1',
      [{ row: 0, action: 'create', edits: { category: 'cat_transport' } }],
      [],
      expect.any(File),
    )
  })

  it('sends no category edit — and no deviation at all — when the row is genuinely Uncategorized', async () => {
    // No edits means nothing deviates from the server's own default (create) — the decision
    // flow omits the row entirely rather than spelling out a no-op (RFC D7: "send per-row
    // deviations from the defaults").
    const onDecisionCommit = await openAndConfirm(previewWithCategory(null))
    expect(onDecisionCommit).toHaveBeenCalledWith('st-cathint', 'a1', [], [], expect.any(File))
  })
})
