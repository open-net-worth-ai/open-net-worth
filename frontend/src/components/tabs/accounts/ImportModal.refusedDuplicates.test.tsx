// @vitest-environment jsdom
//
// A row the LEDGER refused as a duplicate must be NAMED on the Done screen.
//
// The write choke refuses any create that strongly matches something already booked
// (ADR 2026-08-01-refuse-strong-duplicates-at-the-write-choke). That is the right call — it is
// what stops the same charge landing twice from a statement, an agent and a manual entry. But a
// refusal changes what the user gets: they asked to import N rows and got fewer.
//
// If the Done screen stays silent, the feature built to end silent double-booking introduces a
// silent SHORTfall instead — "Booked." with no hint that anything was held back, and a count
// that quietly disagrees with the register. That is the same failure shape as a swallowed
// delete error, and it gets the same contract: reported, never dropped.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
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

function preview() {
  return {
    drafts: [
      {
        date: '2026-06-02',
        payee: 'Spotify',
        description: 'Spotify',
        amount: -11.98,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        external_refs: [{ source: 'statement', ref: 'ref:a1:sp' }],
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
    statementId: 'st-refused',
    ledgerCheck: { period: { start: '2026-06-01', end: '2026-06-30' }, rows: [], boundary: [] },
  }
}

async function commitWith(commitResult: Record<string, unknown>) {
  const onDecisionCommit = vi.fn().mockResolvedValue({
    created: 1,
    merged: 0,
    duplicates: 0,
    skipped: 0,
    deleted: 0,
    deleteErrors: [],
    refusedDuplicates: [],
    ...commitResult,
  })
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={vi.fn().mockResolvedValue(preview()) as never}
      onDecisionCommit={onDecisionCommit}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await screen.findByText('Spotify')

  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await screen.findByText(/^Imported /)
}

describe('Done screen — rows the ledger refused as duplicates', () => {
  it('names every refused row instead of quietly dropping it', async () => {
    await commitWith({
      created: 1,
      refusedDuplicates: [
        {
          date: '2026-06-05',
          description: 'ANTHROPIC SAN FRANCISCO USA',
          duplicate_of: 't_1',
          reason: 'exact match',
        },
        {
          date: '2026-06-06',
          description: 'Grab* A-9IOHTB',
          duplicate_of: 't_2',
          reason: 'near duplicate',
        },
      ],
    })

    expect(screen.getByText(/2 rows not imported — already in your ledger/)).toBeTruthy()
    // The row itself, not just a count — a bare number is not something the user can act on.
    expect(document.body.textContent).toContain('ANTHROPIC SAN FRANCISCO USA')
    expect(document.body.textContent).toContain('Grab* A-9IOHTB')
    expect(document.body.textContent).toContain('2026-06-05')
  })

  it('says nothing when the ledger refused nothing', async () => {
    await commitWith({ created: 1, refusedDuplicates: [] })

    expect(screen.queryByText(/not imported — already in your ledger/)).toBeNull()
  })

  it('reads as singular for one refusal', async () => {
    await commitWith({
      created: 1,
      refusedDuplicates: [
        { date: '2026-06-05', description: 'Spotify', duplicate_of: 't_1', reason: 'exact match' },
      ],
    })

    expect(screen.getByText(/1 row not imported — already in your ledger/)).toBeTruthy()
  })

  it('still names a refusal that carries no description', async () => {
    await commitWith({
      created: 1,
      refusedDuplicates: [
        { date: null, description: null, duplicate_of: 't_1', reason: 'exact match' },
      ],
    })

    // A blank string here would render "1 row not imported — ." and read as a rendering bug
    // rather than as the missing-data case it is.
    expect(screen.getByText(/1 row not imported — already in your ledger/)).toBeTruthy()
    expect(document.body.textContent).toContain('(unnamed row)')
  })
})
