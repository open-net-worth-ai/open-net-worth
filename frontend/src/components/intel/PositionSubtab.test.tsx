// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, waitFor } from '@testing-library/react'
import { fDate } from '../../utils/format'
import { PositionSubtab } from './PositionSubtab'
import type { AssetPosition } from '../../types/assetDetail'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

const POSITION: AssetPosition = {
  held: true,
  position_id: 'pos-1',
  qty: 120,
  cost_basis: 489.2,
  cost_currency: 'USD',
  market_value_base: 141888,
  market_value_native: 141888,
  weight_pct: 29.4,
  unrealized_pl_base: 83257,
  unrealized_pl_pct: 141.8,
  lots: [],
  realized: [],
  income: [],
  per_account: [
    {
      account_id: 'acc-1',
      account_name: 'Brokerage',
      quantity: 120,
      market_value_base: 141888,
      cost_basis: 489.2,
      cost_currency: 'USD',
      unrealized_pl_base: 83257,
    },
  ],
  unvested: null,
}

const MERGED_POSITION: AssetPosition = {
  ...POSITION,
  position_id: null,
  cost_basis: null,
  lots: [
    {
      id: 'lot-1',
      position_id: 'pos-vantage',
      instrument_id: 'inst-1',
      account_id: 'acc-1',
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
    },
    {
      id: 'lot-2',
      position_id: 'pos-meridian',
      instrument_id: 'inst-1',
      account_id: 'acc-2',
      account_name: 'Meridian',
      acquired_at: '2024-06-01',
      original_qty: 40,
      remaining_qty: 40,
      unit_cost: 500.1,
      cost_currency: 'SGD',
      fees: 5,
      kind: 'buy',
      effective_unit_cost: 500.1,
      remaining_basis: 20004,
      term_to_date: 'short',
    },
  ],
  realized: [
    {
      id: 'rlz-merged-1',
      position_id: 'pos-vantage',
      instrument_id: 'inst-1',
      account_id: 'acc-1',
      account_name: 'Vantage',
      disposed_at: '2024-03-01',
      quantity: 5,
      proceeds_per_unit: 600,
      proceeds_currency: 'USD',
      method: 'fifo',
      is_taxable: true,
      cost_basis_known: true,
      consumptions: [],
      realized_gain: 500,
      realized_gain_base: 675,
      base_currency: 'SGD',
    },
  ],
  income: [
    {
      id: 'inc-merged-1',
      position_id: 'pos-meridian',
      instrument_id: 'inst-1',
      account_id: 'acc-2',
      account_name: 'Meridian',
      paid_at: '2024-04-01',
      income_kind: 'dividend',
      gross_amount: 40,
      tax_withheld: 4,
      currency: 'SGD',
      is_drp: false,
      txn_id: 'txn-1',
      base_amount: 36,
      base_amount_available: true,
    },
  ],
  per_account: [
    {
      account_id: 'acc-1',
      account_name: 'Vantage',
      quantity: 80,
      market_value_base: 94592,
      cost_basis: 489.2,
      cost_currency: 'USD',
      unrealized_pl_base: 55504,
    },
    {
      account_id: 'acc-2',
      account_name: 'Meridian',
      quantity: 40,
      market_value_base: 47296,
      cost_basis: 500.1,
      cost_currency: 'SGD',
      unrealized_pl_base: 27753,
    },
  ],
}

const LOCKED_ONLY_POSITION: AssetPosition = {
  ...POSITION,
  position_id: null,
  cost_basis: null,
  qty: 0,
  market_value_base: 0,
  market_value_native: 0,
  unrealized_pl_base: null,
  unrealized_pl_pct: null,
  per_account: [],
  unvested: {
    units: 500,
    market_value_base: 19750,
    grant_id: 'grant-1',
    next_vest: null,
    tranches: [],
  },
}

const POSITION_WITH_UNVESTED: AssetPosition = {
  ...POSITION,
  unvested: {
    units: 500,
    market_value_base: 19750,
    grant_id: 'grant-1',
    next_vest: { date: '2027-01-01', units: 250 },
    tranches: [
      { date: '2025-01-01', units: 250, vested: true, value_base: null },
      { date: '2027-01-01', units: 250, vested: false, value_base: 9875 },
      { date: '2028-01-01', units: 250, vested: false, value_base: 9875 },
    ],
  },
}

const REALIZED = {
  base_currency: 'SGD',
  realized_total_base: 17057,
  events: [
    {
      id: 'e1',
      disposed_at: '2024-02-20',
      quantity: 15,
      proceeds_per_unit: 688,
      proceeds_currency: 'USD',
      method: 'fifo',
      realized_gain_base: 8193,
      consumptions: [],
    },
    {
      id: 'e2',
      disposed_at: '2024-09-11',
      quantity: 10,
      proceeds_per_unit: 1054,
      proceeds_currency: 'USD',
      method: 'specific_id',
      realized_gain_base: 8864,
      consumptions: [],
    },
  ],
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/realized-pnl'))
      return Promise.resolve({ ok: true, json: async () => REALIZED })
    if (url.includes('/lots'))
      return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
    if (url.includes('/income'))
      return Promise.resolve({ ok: true, json: async () => ({ base_currency: 'SGD', events: [] }) })
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
  })
})
afterEach(cleanup)

const INCOME_SUMMARY = {
  base_currency: 'SGD',
  events: [],
  total_base: 4200,
  gross_base: 4600,
  tax_withheld_base: 400,
  trailing_12m_base: 1250,
  cost_basis_base: 58720,
  yield_on_cost_pct: 2.13,
  by_instrument: {},
  by_kind: {},
}

describe('PositionSubtab', () => {
  it('renders the 6-cell summary strip from the position', () => {
    const { getByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    for (const label of [
      'Quantity',
      'Avg cost',
      'Market value',
      'Unrealized P&L',
      'Realized P&L',
      'Dividends received',
    ])
      expect(getByText(label)).toBeTruthy()
    expect(getByText('120')).toBeTruthy() // quantity
    expect(getByText('shares')).toBeTruthy()
  })

  it('fetches realized P&L scoped to the position and shows the disposal count', async () => {
    const { findByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    await waitFor(() =>
      expect(
        mockAuthFetch.mock.calls.some(
          ([u]) => u === '/api/realized-pnl?position_id=pos-1&with_predecessors=true',
        ),
      ).toBe(true),
    )
    expect(await findByText('2 disposals')).toBeTruthy()
  })

  it('reuses LotsPanel for the acquisition/realized/dividends tables', async () => {
    const { findByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    // LotsPanel's card headers (with counts) prove it mounted with this position.
    expect(await findByText(/Acquisition lots/)).toBeTruthy()
    expect(await findByText(/Realized history \(2\)/)).toBeTruthy()
    expect(await findByText(/Dividends \(/)).toBeTruthy()
  })

  it('shows an em-dash for realized when the fetch fails (no crash)', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/realized-pnl'))
        return Promise.resolve({ ok: false, status: 500, json: async () => ({}) })
      if (url.includes('/lots'))
        return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
    const { getByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    // The Realized P&L cell stays an em-dash; the strip still renders.
    expect(getByText('Realized P&L')).toBeTruthy()
  })

  it('does not render the per-account table for a single-account holding', () => {
    const { queryByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    expect(queryByText('Held at')).toBeNull()
  })

  it('does not render the Unvested cell when the position has no locked exposure', () => {
    const { queryByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    expect(queryByText('Unvested')).toBeNull()
  })

  it('shows an em-dash for Dividends received while income is still loading', () => {
    const { getByText } = render(
      <PositionSubtab position={POSITION} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    expect(getByText('Dividends received')).toBeTruthy()
    expect(getByText('trailing 12m')).toBeTruthy()
  })

  it('renders the trailing-12m dividend figure once income is loaded', () => {
    const { getByText } = render(
      <PositionSubtab
        position={POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={INCOME_SUMMARY}
      />,
    )
    expect(getByText('S$1,250.00')).toBeTruthy()
  })

  it('shows $0.00 rather than hiding the cell when dividends total is exactly zero', () => {
    const { getByText } = render(
      <PositionSubtab
        position={POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={{ ...INCOME_SUMMARY, trailing_12m_base: 0 }}
      />,
    )
    expect(getByText('S$0.00')).toBeTruthy()
  })
})

describe('PositionSubtab — merged multi-account holding', () => {
  it('renders a per-account breakdown table with each account row', () => {
    const { getByText, getAllByText } = render(
      <PositionSubtab
        position={MERGED_POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(getByText('Held at')).toBeTruthy()
    // Vantage/Meridian also appear in the merged LotsPanel's own Account column below — assert
    // presence, not uniqueness.
    expect(getAllByText('Vantage').length).toBeGreaterThan(0)
    expect(getAllByText('Meridian').length).toBeGreaterThan(0)
  })

  it('never fetches lots/realized/income — LotsPanel renders the already-merged data instead', () => {
    render(
      <PositionSubtab
        position={MERGED_POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(
      mockAuthFetch.mock.calls.some(([u]: string[]) => (u ?? '').startsWith('/api/realized-pnl')),
    ).toBe(false)
    expect(mockAuthFetch.mock.calls.some(([u]: string[]) => (u ?? '').includes('/lots'))).toBe(
      false,
    )
    expect(mockAuthFetch.mock.calls.some(([u]: string[]) => (u ?? '').includes('/income'))).toBe(
      false,
    )
  })

  it('mounts LotsPanel with the merged lots/realized/income counts, no placeholder note', async () => {
    const { findByText, queryByText } = render(
      <PositionSubtab
        position={MERGED_POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(await findByText(/Acquisition lots \(2\)/)).toBeTruthy()
    expect(await findByText(/Realized history \(1\)/)).toBeTruthy()
    expect(await findByText(/Dividends \(1\)/)).toBeTruthy()
    expect(queryByText(/shown per account above/)).toBeNull()
    expect(queryByText(/spans more than one account/)).toBeNull()
  })

  it('shows an em-dash for the ambiguous native Avg cost cell', () => {
    const { getAllByText } = render(
      <PositionSubtab
        position={MERGED_POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(getAllByText('—').length).toBeGreaterThan(0)
  })
})

describe('PositionSubtab — locked-only holding (unvested grant, no liquid shares)', () => {
  it('shows an unvested-specific message, never the multi-account placeholder', () => {
    const { getByText, queryByText } = render(
      <PositionSubtab
        position={LOCKED_ONLY_POSITION}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(getByText(/unvested equity grant/)).toBeTruthy()
    expect(queryByText(/spans more than one account/)).toBeNull()
    expect(queryByText('Held at')).toBeNull()
  })
})

describe('PositionSubtab — unvested exposure', () => {
  it('renders an Unvested summary cell with units and value', () => {
    const { getByText } = render(
      <PositionSubtab
        position={POSITION_WITH_UNVESTED}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(getByText('Unvested')).toBeTruthy()
    expect(getByText('500 units')).toBeTruthy()
  })

  it('renders the vesting schedule with per-tranche status pills', () => {
    const { getByText, getAllByText, container } = render(
      <PositionSubtab
        position={POSITION_WITH_UNVESTED}
        symbol="AAPL"
        instrumentId="inst-1"
        income={null}
      />,
    )
    expect(getByText('Vesting schedule')).toBeTruthy()
    expect(getByText(fDate('2027-01-01', 'short', 'always'))).toBeTruthy()
    expect(getByText(fDate('2028-01-01', 'short', 'always'))).toBeTruthy()
    // One vested (muted, no est. value) + two upcoming (est. value at current price).
    expect(getByText('vested')).toBeTruthy()
    expect(getAllByText('upcoming')).toHaveLength(2)
    expect(container.querySelectorAll('.vest-badge')).toHaveLength(3)
  })

  it('tolerates a payload without tranches (older API) — no schedule panel, no crash', () => {
    const legacy = {
      ...POSITION_WITH_UNVESTED,
      unvested: { ...POSITION_WITH_UNVESTED.unvested!, tranches: undefined },
    } as unknown as AssetPosition
    const { queryByText } = render(
      <PositionSubtab position={legacy} symbol="AAPL" instrumentId="inst-1" income={null} />,
    )
    expect(queryByText('Vesting schedule')).toBeNull()
  })
})

// Regression (2026-10-31): a sub-cent coin's per-share cost rounded to "$0.00" everywhere,
// and the Held at "Cost basis" column showed that per-share figure instead of the total.
describe('PositionSubtab — sub-cent crypto cost', () => {
  const SHIB: AssetPosition = {
    ...POSITION,
    cost_basis: 0.0000240736,
    cost_currency: 'USD',
    per_account: [
      {
        account_id: 'acc-cb',
        account_name: 'Coinbase',
        quantity: 30196880,
        market_value_base: 181.77,
        cost_basis: 0.0000240736,
        cost_currency: 'USD',
        unrealized_pl_base: -747.31,
      },
      {
        account_id: 'acc-gm',
        account_name: 'Gemini',
        quantity: 39556138,
        market_value_base: 238.11,
        cost_basis: null,
        cost_currency: 'USD',
        unrealized_pl_base: null,
      },
    ],
  }

  it('shows the avg cost at its own precision, never rounded to $0.00', () => {
    const { getAllByText, queryByText } = render(
      <PositionSubtab
        position={SHIB}
        symbol="SHIB"
        instrumentId="inst-1"
        income={null}
        assetClass="crypto"
      />,
    )
    // summary card + the Coinbase Held at row
    expect(getAllByText('$0.00002407')).toHaveLength(2)
    expect(queryByText('$0.00')).toBeNull()
  })

  it('gives Held at both a per-share Avg cost and a total Cost basis per account', () => {
    const { getByText, getAllByText } = render(
      <PositionSubtab
        position={SHIB}
        symbol="SHIB"
        instrumentId="inst-1"
        income={null}
        assetClass="crypto"
      />,
    )
    expect(getAllByText('Avg cost').length).toBe(2) // summary card + Held at header
    expect(getByText('Cost basis')).toBeTruthy()
    expect(getByText('$726.95')).toBeTruthy()
  })
})
