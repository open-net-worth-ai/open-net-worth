// @vitest-environment jsdom
//
// Owner, 2026-10-26: "even the statement viewer is not supporting full files like we have in
// that documents tab". The viewer shows the statement through the Documents previewer — a CSV
// is a spreadsheet grid, not raw text.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import { StatementViewer } from './StatementsSection'
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

afterEach(cleanup)

describe('StatementViewer', () => {
  it('shows a CSV statement as the Documents spreadsheet grid', async () => {
    authFetch.mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', rows: [] }) })
    vault.fetchVaultItem.mockResolvedValue({
      id: 'statement-stmt_v',
      kind: 'statement',
      source_id: 'stmt_v',
      title: 'Riverside Bank — Aug 2026',
      filename: 'aug.csv',
      media_type: 'text/csv',
      private: false,
      file_url: '/api/statements/stmt_v/file',
    } as VaultItem)
    vault.fetchVaultTable.mockResolvedValue({
      sheets: [],
      sheet: '',
      rows: [
        ['Date', 'Description', 'Amount'],
        ['2026-08-01', 'COFFEE QA', '-10'],
      ],
      columns: 3,
      offset: 0,
      has_more: false,
      total_rows: 2,
      more_columns: false,
    })
    render(<StatementViewer stmt={{ id: 'stmt_v', filename: 'aug.csv' }} onClose={() => {}} />)
    expect(await screen.findByRole('cell', { name: 'COFFEE QA' })).toBeTruthy()
    expect(vault.fetchVaultItem).toHaveBeenCalledWith('statement-stmt_v')
    expect(document.querySelector('pre.acct-stmtview-text')).toBeNull()
  })
})
