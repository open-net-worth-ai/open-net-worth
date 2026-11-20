// @vitest-environment jsdom
//
// Payee/category edit (owner: "i can't seem to tap on category to change anything at all,
// there is no way to change category before import atm?" / "let's support inline edit? to
// change payee, to change category"). A compact pencil icon sits in the row's OWN header —
// the same horizontal space the category pill/amount already share, not a new standing row
// (owner, rejecting an earlier attempt: "we have plenty of fucking horizontal space, why do we
// keep add new rows?") — and opens a real modal to edit both fields (owner: "let's have a
// pencil button to show and we open a modal to change the detail... do not create new fields").

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account, Category } from './types'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response)
})

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account
const catList: Category[] = [
  { id: 'cat_transport', label: 'Transport', color: '#3b86dd', kind: 'expense' } as Category,
  { id: 'cat_food', label: 'Food & Dining', color: '#e08a3c', kind: 'expense' } as Category,
]
const catMap = Object.fromEntries(catList.map((c) => [c.id, c]))

function plainRowPreview() {
  return {
    statementId: 'st-1',
    drafts: [
      {
        date: '2026-06-20',
        payee: 'Grab',
        description: 'GRAB RIDE',
        amount: -12.5,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        preRecon: false,
        autoCat: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:1' }],
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
  }
}

function enrichedRowPreview() {
  return {
    statementId: 'st-2',
    drafts: [
      {
        date: '2026-06-28',
        payee: 'Grab',
        description: 'Hoàn tiền giao dịch từ Đối tác PocketPay',
        amount: 36000,
        currency: 'VND',
        kind: 'income',
        category: null,
        dup: false,
        preRecon: false,
        autoCat: false,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:2' }],
        rowIndex: 0,
        flags: [],
        enrich: { payee_name: 'Đối tác PocketPay' },
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

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
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
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  return { onDecisionCommit }
}

describe('ImportModal — payee/category edit pencil', () => {
  it('sits in the row header — reachable without expanding, never nested inside the disclosure button', async () => {
    await openWith(plainRowPreview())
    await screen.findByText('Grab')
    const row = screen.getByText('Grab').closest('.imp3-arow') as HTMLElement
    const pencil = within(row).getByRole('button', { name: /Edit payee and category/ })
    expect(pencil).toBeTruthy()
    // Clicking it must NOT also toggle the row's own disclosure (they're not the same control).
    const line = row.querySelector('.imp3-arow-line') as HTMLElement
    expect(line.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(pencil)
    expect(line.getAttribute('aria-expanded')).toBe('false')
  })

  it('opens a modal; saving updates the row and rides the commit as edits.category/edits.payee', async () => {
    const { onDecisionCommit } = await openWith(plainRowPreview())
    await screen.findByText('Grab')
    const row = screen.getByText('Grab').closest('.imp3-arow') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /Edit payee and category/ }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit payee & category' })
    const payeeInput = within(dialog).getByPlaceholderText('Payee')
    fireEvent.change(payeeInput, { target: { value: 'Grab Rides' } })
    fireEvent.click(within(dialog).getByRole('button', { name: /Category…/ }))
    fireEvent.mouseDown(within(dialog).getByRole('button', { name: 'Transport' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Edit payee & category' })).toBeNull(),
    )
    expect(within(row).getByText('Grab Rides')).toBeTruthy()
    expect(within(row).getByText('Transport')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    const edits = decisions[0]!.edits as Record<string, unknown>
    expect(edits.payee).toBe('Grab Rides')
    expect(edits.category).toBe('cat_transport')
  })

  it('an edited payee becomes the row TITLE, not just the subtitle — overrides the stale AI-resolved name (owner repro 2026-07-28)', async () => {
    await openWith(enrichedRowPreview())
    const row = (await screen.findByText('Đối tác PocketPay')).closest('.imp3-arow') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /Edit payee and category/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit payee & category' })
    fireEvent.change(within(dialog).getByPlaceholderText('Payee'), { target: { value: 'Grab' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Edit payee & category' })).toBeNull(),
    )
    expect(within(row).getByText('Grab', { selector: '.imp3-arow-name' })).toBeTruthy()
    expect(within(row).queryByText('Đối tác PocketPay')).toBeNull()
  })

  // Owner, 2026-07-29: on a routed-transfer row the pencil collided with the wider
  // "Transfer ← <account>" chip. Since 2026-10-26 the modal it opens has a Type switch
  // (Expense or Income / Transfer) that turns a row into a transfer and back, so a ROUTED row
  // keeps the pencil — it is the only way to switch it back. Asserted beside the ordinary
  // expense row: both carry it.
  it('is present on a transfer-routed row (the Type switch lives there) and on an ordinary expense row', async () => {
    await openWith({
      ...plainRowPreview(),
      drafts: [
        {
          date: '2026-06-21',
          payee: null,
          description: 'TFR TO MERIDIAN',
          amount: -400,
          currency: 'SGD',
          kind: 'expense',
          category: null,
          dup: false,
          preRecon: false,
          autoCat: false,
          external_refs: [{ source: 'pdf', ref: 'ref:a1:2' }],
          rowIndex: 1,
          flags: [],
          routed: {
            rule_id: 'r1',
            from_account_id: 'a1',
            from_account_name: 'Anchor',
            to_account_id: 'a2',
            to_account_name: 'Meridian Multiplier (SGD)',
          },
        },
        plainRowPreview().drafts[0]!,
      ],
      importable: 2,
      total: 2,
    })
    const transferRow = (await screen.findByText('Transfer → Meridian Multiplier (SGD)')).closest(
      '.imp3-arow',
    ) as HTMLElement
    expect(
      within(transferRow).getByRole('button', { name: /Edit payee and category/ }),
    ).toBeTruthy()

    const expenseRow = screen.getByText('Grab').closest('.imp3-arow') as HTMLElement
    expect(within(expenseRow).getByRole('button', { name: /Edit payee and category/ })).toBeTruthy()
  })

  it('Cancel discards the edit', async () => {
    await openWith(plainRowPreview())
    await screen.findByText('Grab')
    const row = screen.getByText('Grab').closest('.imp3-arow') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /Edit payee and category/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit payee & category' })
    fireEvent.change(within(dialog).getByPlaceholderText('Payee'), {
      target: { value: 'Something else' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Edit payee & category' })).toBeNull(),
    )
    expect(within(row).getByText('Grab')).toBeTruthy()
  })
})
