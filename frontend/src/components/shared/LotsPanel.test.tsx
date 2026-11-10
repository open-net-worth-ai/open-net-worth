// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, waitFor, fireEvent } from '@testing-library/react'
import { fDate } from '../../utils/format'
import { LotsPanel } from './LotsPanel'
import type { Lot, RealizedEvent, IncomeEvent } from '../../types/portfolio'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

const LOT: Lot = {
  id: 'lot-1',
  position_id: 'pos-1',
  instrument_id: 'inst-1',
  account_id: 'acct-1',
  acquired_at: '2024-01-15',
  original_qty: 100,
  remaining_qty: 100,
  unit_cost: 185.2,
  cost_currency: 'USD',
  fees: 1.5,
  kind: 'buy',
  effective_unit_cost: 185.2,
  remaining_basis: 18520,
  term_to_date: 'long',
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.includes('/lots'))
      return Promise.resolve({ ok: true, json: async () => ({ lots: [LOT] }) })
    if (url.startsWith('/api/realized-pnl'))
      return Promise.resolve({ ok: true, json: async () => ({ base_currency: 'SGD', events: [] }) })
    if (url.includes('/income'))
      return Promise.resolve({ ok: true, json: async () => ({ base_currency: 'SGD', events: [] }) })
    if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
      return Promise.resolve({ ok: true, json: async () => [] })
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
  })
})
afterEach(cleanup)

describe('LotsPanel — Lucius entity wiring', () => {
  it('renders each open lot row as a draggable lot mention entity', async () => {
    const { container } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )

    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="lot:lot-1"]')
      if (!el) throw new Error('lot row not yet rendered')
      return el
    })

    // The global dragRightClick delegation only fires on [data-entity-id][draggable].
    expect(row.getAttribute('draggable')).toBe('true')
    expect(row.getAttribute('data-entity-label')).toBe('AAPL')
    expect(row.getAttribute('data-ctx-type')).toBe('lot')

    // A sublabel disambiguates multiple lots of the same instrument in the picker/chip.
    expect(row.getAttribute('data-entity-sublabel')).toBe(
      `Buy • 100 left • ${fDate('2024-01-15', 'short', 'always')}`,
    )

    // Field projection carries the lot's descriptor keys for the chip popover / AI context.
    const fields = JSON.parse(row.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.symbol).toBe('AAPL')
    expect(fields.kind).toBe('buy')
    expect(fields.acquired_at).toBe('2024-01-15')
  })

  it('renders each realized-history row as a draggable disposal mention entity', async () => {
    const EVENT: RealizedEvent = {
      id: 'rlz-1',
      position_id: 'pos-1',
      instrument_id: 'inst-1',
      account_id: 'acct-1',
      disposed_at: '2024-02-20',
      quantity: 15,
      proceeds_per_unit: 688,
      proceeds_currency: 'USD',
      method: 'fifo',
      is_taxable: true,
      cost_basis_known: true,
      consumptions: [],
      realized_gain: 5000,
      realized_gain_base: 6750,
      base_currency: 'SGD',
    }
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [EVENT] }),
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })

    const { container } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )

    // Realized history is an always-visible card — no tab switch needed for it to mount.
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="disposal:rlz-1"]')
      if (!el) throw new Error('disposal row not yet rendered')
      return el
    })
    expect(row.getAttribute('draggable')).toBe('true')
    expect(row.getAttribute('data-entity-label')).toBe('AAPL')
    expect(row.getAttribute('data-ctx-type')).toBe('disposal')
    expect(row.getAttribute('data-entity-sublabel')).toBe(
      `15 units • ${fDate('2024-02-20', 'short', 'always')}`,
    )

    const fields = JSON.parse(row.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.symbol).toBe('AAPL')
    expect(fields.method).toBe('fifo')
    expect(fields.disposed_at).toBe('2024-02-20')
    // base snapshot must NOT leak into the chip fields (filtered out by the descriptor)
    expect(fields.realized_gain_base).toBeUndefined()
  })

  it('renders each dividend-history row as a draggable income mention entity', async () => {
    const EVENT: IncomeEvent = {
      id: 'inc-1',
      position_id: 'pos-1',
      instrument_id: 'inst-1',
      account_id: 'acct-1',
      paid_at: '2024-03-15',
      income_kind: 'dividend',
      gross_amount: 120,
      tax_withheld: 20,
      currency: 'USD',
      is_drp: false,
      txn_id: 'txn-1',
      base_amount: 135,
      base_amount_available: true,
    }
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      if (url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [EVENT] }),
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })

    const { container } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )

    // Dividends is an always-visible card — no tab switch needed for it to mount.
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="income:inc-1"]')
      if (!el) throw new Error('dividend row not yet rendered')
      return el
    })
    expect(row.getAttribute('draggable')).toBe('true')
    expect(row.getAttribute('data-entity-label')).toBe('AAPL')
    expect(row.getAttribute('data-ctx-type')).toBe('income')

    const fields = JSON.parse(row.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.symbol).toBe('AAPL')
    expect(fields.income_kind).toBe('dividend')
    expect(fields.paid_at).toBe('2024-03-15')
  })

  it('shows an empty message (no crash) when the position has no dividend history', async () => {
    const { findByText, queryByText } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    expect(await findByText('No dividends recorded for this holding yet.')).toBeTruthy()
    expect(queryByText('undefined')).toBeNull()
  })
})

// ── Multi-account holding: pre-merged data (asset_detail.py's _position_block), no fetch ──
describe('LotsPanel — merged multi-account data', () => {
  const MERGED_LOT: Lot = {
    id: 'lot-vantage-1',
    position_id: 'pos-vantage',
    instrument_id: 'inst-1',
    account_id: 'acc-vantage',
    account_name: 'Vantage',
    acquired_at: '2024-01-15',
    original_qty: 80,
    remaining_qty: 80,
    unit_cost: 489.2,
    cost_currency: 'USD',
    fees: 0,
    kind: 'buy',
    effective_unit_cost: 489.2,
    remaining_basis: 39136,
    term_to_date: 'long',
  }
  const MERGED_REALIZED: RealizedEvent = {
    id: 'rlz-meridian-1',
    position_id: 'pos-meridian',
    instrument_id: 'inst-1',
    account_id: 'acc-meridian',
    account_name: 'Meridian',
    disposed_at: '2024-03-01',
    quantity: 5,
    proceeds_per_unit: 600,
    proceeds_currency: 'SGD',
    method: 'fifo',
    is_taxable: true,
    cost_basis_known: true,
    consumptions: [],
    realized_gain: 500,
    realized_gain_base: 675,
    base_currency: 'SGD',
  }
  const MERGED_INCOME: IncomeEvent = {
    id: 'inc-meridian-1',
    position_id: 'pos-meridian',
    instrument_id: 'inst-1',
    account_id: 'acc-meridian',
    account_name: 'Meridian',
    paid_at: '2024-04-01',
    income_kind: 'dividend',
    gross_amount: 40,
    tax_withheld: 4,
    currency: 'SGD',
    is_drp: false,
    txn_id: 'txn-meridian-1',
    base_amount: 36,
    base_amount_available: true,
  }

  it('renders merged data directly with no network fetch at all', () => {
    render(
      <LotsPanel
        positionId={null}
        symbol="AAPL"
        costCcy="USD"
        base={20000}
        costBase={18520}
        merged={{ lots: [MERGED_LOT], realized: [MERGED_REALIZED], income: [MERGED_INCOME] }}
      />,
    )
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })

  it('shows the merged counts in every card header', () => {
    const { getByText } = render(
      <LotsPanel
        positionId={null}
        symbol="AAPL"
        costCcy="USD"
        base={20000}
        costBase={18520}
        merged={{ lots: [MERGED_LOT], realized: [MERGED_REALIZED], income: [MERGED_INCOME] }}
      />,
    )
    expect(getByText(/Acquisition lots \(1\)/)).toBeTruthy()
    expect(getByText(/Realized history \(1\)/)).toBeTruthy()
    expect(getByText(/Dividends \(1\)/)).toBeTruthy()
  })

  it('adds an Account column to every table, showing each account name — all three cards visible at once', () => {
    const { getAllByText, getByText } = render(
      <LotsPanel
        positionId={null}
        symbol="AAPL"
        costCcy="USD"
        base={20000}
        costBase={18520}
        merged={{ lots: [MERGED_LOT], realized: [MERGED_REALIZED], income: [MERGED_INCOME] }}
      />,
    )
    // One "Account" header per card (lots, realized, dividends) — no click needed, all three
    // sections render simultaneously.
    expect(getAllByText('Account')).toHaveLength(3)
    expect(getByText('Vantage')).toBeTruthy() // acquisition-lots row
    expect(getAllByText('Meridian')).toHaveLength(2) // realized + dividends rows
  })

  // Regression: the realized/dividends totals used to render as a <tfoot> cell with a
  // colspan sized to land under a specific column (Realized / Net) — visibly misaligned in
  // practice. Both now render as a plain right-aligned line under their table instead.
  it('renders the realized and dividends totals as a plain line below each table, not a tfoot cell', () => {
    const { container, getByText } = render(
      <LotsPanel
        positionId={null}
        symbol="AAPL"
        costCcy="USD"
        base={20000}
        costBase={18520}
        merged={{ lots: [MERGED_LOT], realized: [MERGED_REALIZED], income: [MERGED_INCOME] }}
      />,
    )
    const realizedTotal = getByText(/Total realized/)
    const dividendsTotal = getByText(/Total received/)
    expect(realizedTotal.textContent).toBe('Total realized: +S$675.00')
    expect(dividendsTotal.textContent).toBe('Total received: S$36.00')
    expect(realizedTotal.closest('table')).toBeNull()
    expect(dividendsTotal.closest('table')).toBeNull()
    expect(container.querySelector('tfoot')).toBeNull()
  })

  it('handles empty merged data with the ordinary empty states, not the old placeholder', async () => {
    const { findByText, queryByText } = render(
      <LotsPanel
        positionId={null}
        symbol="AAPL"
        costCcy="USD"
        base={0}
        costBase={0}
        merged={{ lots: [], realized: [], income: [] }}
      />,
    )
    expect(await findByText(/No lots recorded for this holding yet/)).toBeTruthy()
    expect(await findByText('No disposals recorded for this holding yet.')).toBeTruthy()
    expect(await findByText('No dividends recorded for this holding yet.')).toBeTruthy()
    expect(queryByText(/spans more than one account/)).toBeNull()
  })
})

describe('LotsPanel — write controls', () => {
  it('opens the add-lot form from the panel header', async () => {
    const { findByText, findAllByText, findAllByDisplayValue } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    fireEvent.click(await findByText('Add lot'))
    expect((await findAllByText('Add lot')).length).toBeGreaterThan(0)
    expect(await findByText('Symbol')).toBeTruthy()
    expect((await findAllByDisplayValue('AAPL')).length).toBeGreaterThan(0)
  })

  it('opens the edit-lot form when a buy row is clicked', async () => {
    const { container, findByText, findAllByDisplayValue } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="lot:lot-1"]')
      if (!el) throw new Error('lot row not yet rendered')
      return el
    })
    fireEvent.click(row)
    expect(await findByText('Edit lot')).toBeTruthy()
    expect((await findAllByDisplayValue('AAPL')).length).toBeGreaterThan(0)
  })

  it('a partly sold lot opens editable, guards quantity, and reports the sales it updated', async () => {
    // 2026-10-13: a sold lot used to open note-only, so its date couldn't be fixed.
    const partial: Lot = { ...LOT, remaining_qty: 40 } // 60 of 100 sold
    let patched: Record<string, unknown> | null = null
    mockAuthFetch.mockImplementation((url: string, init?: { method?: string; body?: string }) => {
      if (url === '/api/lots/lot-1' && init?.method === 'PATCH') {
        patched = JSON.parse(String(init.body))
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ok: true,
            sales_updated: [
              {
                event_id: 'rlz-1',
                disposed_at: '2026-10-12',
                realized_gain_before: 1000,
                realized_gain_after: 1600,
                currency: 'USD',
              },
            ],
          }),
        })
      }
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [partial] }) })
      if (url.startsWith('/api/realized-pnl') || url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      if (url === '/api/accounts') return Promise.resolve({ ok: true, json: async () => [] })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { container, findByText, findByLabelText, getByRole, queryByRole } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="lot:lot-1"]')
      if (!el) throw new Error('lot row not yet rendered')
      return el
    })
    fireEvent.click(row)

    expect(await findByText(/60 of 100 have been sold/)).toBeTruthy()
    expect(queryByRole('button', { name: 'Remove lot' })).toBeNull()
    const qty = (await findByLabelText(/Quantity/)) as HTMLInputElement
    expect(qty.disabled).toBe(false)

    fireEvent.change(qty, { target: { value: '50' } })
    expect(await findByText(/Can't be less than the 60 already sold/)).toBeTruthy()
    expect((getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(qty, { target: { value: '120' } })
    fireEvent.change(await findByLabelText(/Unit cost/), { target: { value: '170' } })
    fireEvent.click(getByRole('button', { name: 'Save' }))

    expect(await findByText(/Sale on .*: gain \+.*1,000\.00 USD → \+.*1,600\.00 USD/)).toBeTruthy()
    expect(patched).toMatchObject({ quantity: 120, unit_cost: 170 })
  })

  it('shows an Add lot CTA on the no-lots empty state', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      if (url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      if (url === '/api/accounts') return Promise.resolve({ ok: true, json: async () => [] })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByText, findAllByText, findAllByDisplayValue } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={0} costBase={null} />,
    )
    expect(await findByText(/No lots recorded for this holding yet/)).toBeTruthy()
    const addLot = (await findAllByText('Add lot'))[0]
    if (!addLot) throw new Error('expected Add lot')
    fireEvent.click(addLot)
    expect(await findByText('Symbol')).toBeTruthy()
    expect((await findAllByDisplayValue('AAPL')).length).toBeGreaterThan(0)
  })

  it('opens add lot with an inline account field instead of Which account?', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/accounts' || url.startsWith('/api/accounts?'))
        return Promise.resolve({
          ok: true,
          json: async () => [
            { id: 'acc-vantage', name: 'Vantage', kind: 'asset', type: 'investment' },
            { id: 'acc-meridian', name: 'Meridian', kind: 'asset', type: 'investment' },
          ],
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { findByText, queryByText } = render(
      <LotsPanel
        positionId={null}
        instrumentId="inst-1"
        symbol="AAPL"
        costCcy="USD"
        base={20000}
        costBase={18520}
        writeTargets={[
          { positionId: 'pos-vantage', accountId: 'acc-vantage', accountName: 'Vantage' },
          { positionId: 'pos-meridian', accountId: 'acc-meridian', accountName: 'Meridian' },
        ]}
      />,
    )
    fireEvent.click(await findByText('Add lot'))
    expect(queryByText('Which account?')).toBeNull()
    expect(await findByText('Account')).toBeTruthy()
    expect(await findByText('+ New account…')).toBeTruthy()
  })

  it('opens the sale form when a realized-history row is clicked', async () => {
    const EVENT: RealizedEvent = {
      id: 'rlz-1',
      position_id: 'pos-1',
      instrument_id: 'inst-1',
      account_id: 'acct-1',
      disposed_at: '2024-02-20',
      quantity: 15,
      proceeds_per_unit: 688,
      proceeds_currency: 'USD',
      method: 'fifo',
      is_taxable: true,
      cost_basis_known: true,
      consumptions: [],
      realized_gain: 5000,
      realized_gain_base: 6750,
      base_currency: 'SGD',
    }
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [LOT] }) })
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [EVENT] }),
        })
      if (url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      if (url === '/api/accounts') return Promise.resolve({ ok: true, json: async () => [] })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { container, findByRole } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-entity-id="disposal:rlz-1"]')
      if (!el) throw new Error('disposal row not yet rendered')
      return el
    })
    fireEvent.click(row)
    const dialog = await findByRole('dialog')
    expect(dialog.textContent).toMatch(/Edit sale/)
  })
})

describe('LotsPanel — sold lots stay in the history (found 2026-10-12)', () => {
  // A holding sold out and bought back: two closed lots, one open. The table used to list
  // only the open lot, so the earlier buys looked deleted.
  const closedA: Lot = {
    ...LOT,
    id: 'lot-a',
    acquired_at: '2024-01-15',
    remaining_qty: 0,
    remaining_basis: 0,
  }
  const closedB: Lot = {
    ...LOT,
    id: 'lot-b',
    acquired_at: '2024-06-01',
    original_qty: 40,
    remaining_qty: 0,
    remaining_basis: 0,
  }
  const open: Lot = {
    ...LOT,
    id: 'lot-c',
    acquired_at: '2026-10-12',
    original_qty: 50,
    remaining_qty: 50,
    remaining_basis: 9260,
  }

  function withLots(lots: Lot[]) {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots')) return Promise.resolve({ ok: true, json: async () => ({ lots }) })
      if (url.startsWith('/api/realized-pnl') || url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
  }

  it('lists every lot, marks the sold ones, and counts both', async () => {
    withLots([closedA, closedB, open])
    const { container, findByText, getAllByText } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={10000} costBase={9260} />,
    )

    expect(await findByText('Acquisition lots (1 open · 2 closed)')).toBeTruthy()
    const rows = container.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(container.querySelectorAll('tr.lots-row--closed')).toHaveLength(2)
    expect(getAllByText('Sold')).toHaveLength(2)
    // The open lot alone carries the whole unrealized gain: 10000 − 9260.
    const openRow = container.querySelector('[data-entity-id="lot:lot-c"]') as HTMLElement
    expect(openRow.className).not.toContain('lots-row--closed')
    expect(openRow.textContent).toContain('740')
  })

  it('a fully sold holding still shows its lots, not the empty state', async () => {
    withLots([closedA, closedB])
    const { findByText, queryByText, container } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={0} costBase={null} />,
    )

    expect(await findByText('Acquisition lots (0 open · 2 closed)')).toBeTruthy()
    expect(container.querySelectorAll('tr.lots-row--closed')).toHaveLength(2)
    expect(queryByText(/No lots recorded/)).toBeNull()
  })
})

describe('LotsPanel — a sale shows the lots it used', () => {
  // One sale across an undated opening lot and a dated buy: the row used to show only a
  // weighted "Buy price" and an "Unknown" term, hiding which lots were sold.
  const SALE: RealizedEvent = {
    id: 'rlz-mixed',
    position_id: 'pos-1',
    instrument_id: 'inst-1',
    account_id: 'acct-1',
    disposed_at: '2026-10-12',
    quantity: 30,
    proceeds_per_unit: 210,
    proceeds_currency: 'USD',
    method: 'fifo',
    is_taxable: true,
    cost_basis_known: true,
    consumptions: [
      {
        lot_id: 'lot-open',
        qty: 20,
        unit_cost: 130,
        cost_currency: 'USD',
        acquired_at: '',
        term: 'unknown',
        realized_gain: 1600,
      },
      {
        lot_id: 'lot-buy',
        qty: 10,
        unit_cost: 120,
        cost_currency: 'USD',
        acquired_at: '2026-06-23',
        term: 'short',
        realized_gain: 900,
      },
    ],
    realized_gain: 2500,
    realized_gain_base: 3200,
    base_currency: 'SGD',
  }

  beforeEach(() => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [SALE] }),
        })
      if (url.includes('/income'))
        return Promise.resolve({
          ok: true,
          json: async () => ({ base_currency: 'SGD', events: [] }),
        })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
  })

  it('reads "mixed" when the lots it used have different holding periods', async () => {
    const { findByText } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={0} costBase={null} />,
    )
    expect(await findByText('mixed')).toBeTruthy()
  })

  it('expands to each consumed lot without opening the edit-sale form', async () => {
    const { findByRole, getByText, queryByText, container } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={0} costBase={null} />,
    )
    const toggle = await findByRole('button', { name: 'Show the lots this sale used' })
    expect(container.querySelector('.lots-consumption-table')).toBeNull()

    fireEvent.click(toggle)

    const table = container.querySelector('.lots-consumption-table') as HTMLElement
    expect(table.querySelectorAll(':scope > tbody > tr')).toHaveLength(2)
    expect(getByText('Date unknown')).toBeTruthy()
    expect(table.textContent).toContain('+1,600.00')
    expect(table.textContent).toContain('+900.00')
    expect(queryByText(/Edit sale/)).toBeNull()
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })
})

// Regression (2026-10-31): the Unit cost column used the currency's 2 decimals, so a
// sub-cent coin's lot (SHIB at $0.0000241) read "0.00" — as if it had been free.
describe('LotsPanel — sub-cent unit cost', () => {
  const SHIB_LOT: Lot = {
    ...LOT,
    id: 'lot-shib',
    original_qty: 30196880,
    remaining_qty: 30196880,
    unit_cost: 0.00002393,
    fees: 4.34,
    effective_unit_cost: 0.0000240737,
    remaining_basis: 726.95,
  }

  it('shows the unit cost at its own precision', () => {
    const { getByText } = render(
      <LotsPanel
        positionId={null}
        symbol="SHIB"
        costCcy="USD"
        base={181.77}
        costBase={929.08}
        merged={{ lots: [SHIB_LOT], realized: [], income: [] }}
      />,
    )
    expect(getByText('0.00002407')).toBeTruthy()
  })

  it('keeps the currency precision for an ordinary unit cost', async () => {
    const { findByText } = render(
      <LotsPanel positionId="pos-1" symbol="AAPL" costCcy="USD" base={20000} costBase={18520} />,
    )
    expect(await findByText('185.20')).toBeTruthy()
  })
})
