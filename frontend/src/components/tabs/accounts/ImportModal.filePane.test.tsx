// @vitest-environment jsdom
//
// Owner, 2026-10-26: "i do need a preview feature like this, where i can scroll or review the
// files on the left/or right … before making decisions". The Decide step shows the statement's
// own file beside the review, through the Documents previewer; a click on a review row points
// the file at the row it came from; "Hide file" collapses it and is remembered.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'
import type { VaultItem, VaultTable } from '../../../api/vault'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
const vault = vi.hoisted(() => ({
  fetchVaultItem: vi.fn<(id: string) => Promise<VaultItem>>(),
  fetchVaultTable: vi.fn<(id: string, sheet?: string, offset?: number) => Promise<VaultTable>>(),
}))
vi.mock('../../../api/vault', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/vault')>()),
  fetchVaultItem: (id: string) => vault.fetchVaultItem(id),
  fetchVaultTable: (id: string, sheet?: string, offset?: number) =>
    vault.fetchVaultTable(id, sheet, offset),
}))

const ACCOUNTS = [
  { id: 'rvb', name: 'Riverside Bank', currency: 'USD', kind: 'asset', type: 'cash' },
] as unknown as Account[]

const draft = (rowIndex: number, description: string, sourceRow: number) => ({
  date: '2026-08-0' + (rowIndex + 1),
  payee: null,
  description,
  amount: -10 - rowIndex,
  currency: 'USD',
  kind: 'expense',
  category: null,
  dup: false,
  rowIndex,
  sourceRow,
  external_refs: [],
  flags: [],
})

const PREVIEW = {
  statementId: 'stmt_fp',
  resolvedAccountId: 'rvb',
  drafts: [draft(0, 'COFFEE QA', 4), draft(1, 'TAXI QA', 6)],
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

const ITEM = {
  id: 'statement-stmt_fp',
  kind: 'statement',
  source_id: 'stmt_fp',
  title: 'Riverside Bank — Aug 2026',
  filename: 'aug.csv',
  media_type: 'text/csv',
  private: false,
  file_url: '/api/statements/stmt_fp/file',
} as VaultItem

beforeEach(() => {
  localStorage.clear()
  authFetch.mockImplementation(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({ parse_status: 'ready', progress: null, error: null }),
    }),
  )
  vault.fetchVaultItem.mockResolvedValue(ITEM)
  vault.fetchVaultTable.mockResolvedValue({
    sheets: [],
    sheet: '',
    rows: [
      ['Aug history'],
      [''],
      ['Date', 'Description', 'Amount'],
      ['2026-08-01', 'COFFEE QA', '-10'],
      [''],
      ['2026-08-02', 'TAXI QA', '-11'],
    ],
    columns: 3,
    offset: 0,
    has_more: false,
    total_rows: 6,
    more_columns: false,
  })
})
afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function open() {
  return render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      resumeStatementId="stmt_fp"
      resumeFilename="aug.csv"
      onReopenPreview={vi.fn().mockResolvedValue(PREVIEW) as never}
      onClose={() => {}}
      onPreview={vi.fn() as never}
      onDecisionCommit={vi.fn() as never}
    />,
  )
}

const fileRegion = () => screen.getByRole('complementary', { name: 'Statement file' })

describe('ImportModal — the statement file beside the review', () => {
  it('shows the statement file in the Documents grid, and a review row points at its row', async () => {
    open()
    await screen.findByText('COFFEE QA', { selector: '.imp3-arow-name' })
    expect(vault.fetchVaultItem).toHaveBeenCalledWith('statement-stmt_fp')
    await waitFor(() => expect(fileRegion().textContent).toContain('aug.csv'))
    await waitFor(() => expect(fileRegion().querySelector('table.vault-grid')).toBeTruthy())

    fireEvent.click(screen.getByText('TAXI QA', { selector: '.imp3-arow-name' }))
    await waitFor(() =>
      expect(fileRegion().querySelector('tr[aria-current="true"]')?.textContent).toContain(
        'TAXI QA',
      ),
    )
    expect(fileRegion().textContent).toContain('Row 6')
  })

  it('Hide file collapses the pane and is remembered for next time', async () => {
    const { unmount } = open()
    await screen.findByText('COFFEE QA', { selector: '.imp3-arow-name' })
    await waitFor(() => expect(fileRegion()).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Hide file' }))
    expect(screen.queryByRole('complementary', { name: 'Statement file' })).toBeNull()
    unmount()

    open()
    await screen.findByText('COFFEE QA', { selector: '.imp3-arow-name' })
    expect(screen.getByRole('button', { name: 'Show file' })).toBeTruthy()
    expect(screen.queryByRole('complementary', { name: 'Statement file' })).toBeNull()
  })

  // QA 2026-10-26: a row tapped while the file is hidden (a phone's Review tab, or Hide file)
  // was never remembered — showing the file then pointed nowhere.
  it('a row clicked while the file is hidden is the one shown when it comes back', async () => {
    open()
    await screen.findByText('COFFEE QA', { selector: '.imp3-arow-name' })
    fireEvent.click(screen.getByRole('button', { name: 'Hide file' }))
    fireEvent.click(screen.getByText('TAXI QA', { selector: '.imp3-arow-name' }))
    fireEvent.click(screen.getByRole('button', { name: 'Show file' }))
    await waitFor(() =>
      expect(fileRegion().querySelector('tr[aria-current="true"]')?.textContent).toContain(
        'TAXI QA',
      ),
    )
  })
})
