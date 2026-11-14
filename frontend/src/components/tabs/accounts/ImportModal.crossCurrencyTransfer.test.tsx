// @vitest-environment jsdom
//
// A statement row can be a transfer with an account in another currency. The review says what
// the other side will book before anything does, and that it is an estimate; a pair with no
// exchange rate stays a plain row and says so rather than quietly booking as spending.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, within } from '@testing-library/react'
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

const account = (id: string, name: string, currency: string) =>
  ({ id, name, kind: 'asset', type: 'cash', currency, color: '#22d3ee' }) as unknown as Account

const accounts = [
  account('a1', 'Anchor', 'SGD'),
  account('a2', 'Anchor Savings', 'SGD'),
  account('a3', 'Harbor', 'USD'),
  { ...account('a4', 'Old Harbor', 'USD'), archived: true } as Account,
]
const catList: Category[] = [
  { id: 'cat_food', label: 'Food & Dining', color: '#e08a3c', kind: 'expense' } as Category,
]
const catMap = Object.fromEntries(catList.map((c) => [c.id, c]))

const draft = {
  date: '2026-06-20',
  payee: 'Top up',
  description: 'TOPUP HARBOR',
  amount: -135,
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
  external_refs: [{ source: 'pdf', ref: 'ref:a1:1' }],
  rowIndex: 0,
  flags: [] as { type: string; reason?: string }[],
}

const preview = (row: Record<string, unknown>) => ({
  statementId: 'st-1',
  drafts: [{ ...draft, ...row }],
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
})

const routed = (other_leg: unknown) => ({
  kind: 'transfer',
  routed: {
    rule_id: 'lucius_pass',
    from_account_id: 'a1',
    from_account_name: 'Anchor',
    to_account_id: 'a3',
    to_account_name: 'Harbor',
    other_leg,
  },
})

async function openWith(payload: unknown) {
  render(
    <ImportModal
      open
      accounts={accounts}
      defaultAccountId="a1"
      categories={catList}
      catMap={catMap}
      onClose={() => {}}
      onPreview={vi.fn().mockResolvedValue(payload) as never}
      onDecisionCommit={vi.fn()}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
}

function expandRow(inside: HTMLElement): HTMLElement {
  const row = inside.closest('.imp3-arow') as HTMLElement
  const line = row.querySelector('.imp3-arow-line') as HTMLElement
  if (line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

async function openEditWindow() {
  await screen.findByText('Top up')
  const row = screen.getByText('Top up').closest('.imp3-arow') as HTMLElement
  fireEvent.click(within(row).getByRole('button', { name: /Edit payee and category/ }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit payee & category' })
  fireEvent.click(within(dialog).getByRole('tab', { name: 'Transfer' }))
  return dialog
}

describe('ImportModal — a transfer with an account in another currency', () => {
  it('shows the converted side and that it is an estimate', async () => {
    await openWith(
      preview(routed({ account_id: 'a3', account_name: 'Harbor', amount: 100, currency: 'USD' })),
    )
    const row = expandRow(await screen.findByText('Transfer → Harbor'))
    const note = within(row).getByText(/Books as transfer: Anchor → Harbor/)
    expect(note.textContent).toContain('Anchor sends S$135.00 • Harbor receives about $100.00')
    expect(note.textContent).toContain('latest exchange rate')
  })

  it('on an incoming row, the other account is the one sending the estimate', async () => {
    await openWith(
      preview({
        ...routed({ account_id: 'a3', account_name: 'Harbor', amount: 200, currency: 'USD' }),
        amount: 270,
        routed: {
          rule_id: 'lucius_pass',
          from_account_id: 'a3',
          from_account_name: 'Harbor',
          to_account_id: 'a1',
          to_account_name: 'Anchor',
          other_leg: { account_id: 'a3', account_name: 'Harbor', amount: 200, currency: 'USD' },
        },
      }),
    )
    const row = expandRow(await screen.findByText('Transfer ← Harbor'))
    const note = within(row).getByText(/Books as transfer: Harbor → Anchor/)
    expect(note.textContent).toContain('Harbor sends about $200.00 • Anchor receives S$270.00')
    expect(within(row).getByText('≈$200.00 →')).toBeTruthy()
  })

  it('names the other currency on the row itself, without opening it', async () => {
    await openWith(
      preview(routed({ account_id: 'a3', account_name: 'Harbor', amount: 100, currency: 'USD' })),
    )
    const row = (await screen.findByText('Transfer → Harbor')).closest('.imp3-arow') as HTMLElement
    expect(row.querySelector('.imp3-arow-line')?.getAttribute('aria-expanded')).toBe('false')
    expect(within(row).getByText('→ ≈$100.00')).toBeTruthy()
    expect(within(row).getByText(/135\.00/)).toBeTruthy()
  })

  it('adds no second amount to a same-currency transfer', async () => {
    await openWith(preview(routed(null)))
    const row = (await screen.findByText('Transfer → Harbor')).closest('.imp3-arow') as HTMLElement
    expect(row.querySelector('.imp3-orig')).toBeNull()
  })

  it('shows no conversion on a same-currency transfer', async () => {
    await openWith(preview(routed(null)))
    const row = expandRow(await screen.findByText('Transfer → Harbor'))
    const note = within(row).getByText(/Books as transfer: Anchor → Harbor/)
    expect(note.textContent).not.toContain('exchange rate')
  })

  it('says why a row stayed plain when no rate exists', async () => {
    await openWith(preview({ flags: [{ type: 'transfer_no_rate', reason: 'No rate' }] }))
    const row = expandRow(await screen.findByText('Top up'))
    expect(within(row).getByText('Not a transfer — no exchange rate for that account')).toBeTruthy()
  })

  it('never reads "Looks good" while a transfer the owner asked for will book as spending', async () => {
    await openWith(preview({ flags: [{ type: 'transfer_no_rate', reason: 'No rate' }] }))
    expect(await screen.findByText('1 thing to check before importing')).toBeTruthy()
    expect(screen.queryByText('Looks good — ready to import')).toBeNull()
    fireEvent.click(screen.getByText('1 thing to check before importing'))
    expect(
      screen.getByText('1 row you marked as a transfer will book as spending or income'),
    ).toBeTruthy()
  })

  it('still reads "Looks good" with nothing flagged', async () => {
    await openWith(preview({}))
    expect(await screen.findByText('Looks good — ready to import')).toBeTruthy()
  })

  it('offers accounts in other currencies, marked, but never an archived one or the statement’s own', async () => {
    await openWith(preview({}))
    const dialog = await openEditWindow()
    const select = within(dialog).getByRole('combobox', { name: 'To account' })
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(labels).toContain('Anchor Savings')
    expect(labels).toContain('Harbor (USD)')
    expect(labels).not.toContain('Old Harbor (USD)')
    expect(labels).not.toContain('Anchor')
  })

  it('warns about the rate only once another currency is chosen', async () => {
    await openWith(preview({}))
    const dialog = await openEditWindow()
    const select = within(dialog).getByRole('combobox', { name: 'To account' })
    fireEvent.change(select, { target: { value: 'a2' } })
    expect(within(dialog).queryByText(/latest exchange rate/)).toBeNull()
    fireEvent.change(select, { target: { value: 'a3' } })
    expect(within(dialog).getByText(/latest exchange rate/)).toBeTruthy()
  })
})
