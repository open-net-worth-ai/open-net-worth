// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import type { PortfolioXray } from '../../../api/investments'
import type { BookRow, DerivedBook } from './useInvestmentsBook'

const querySource = vi.fn()
vi.mock('../../../hooks/serverState/dataQuery', () => ({
  querySource: (...args: unknown[]) => querySource(...args),
}))

import { SectorGeo } from './SectorGeo'

afterEach(() => {
  cleanup()
  querySource.mockReset()
})

// One EUR-listed global fund — the browser's old currency guess read it as "Europe 100%".
const FUND = {
  sym: 'ACME-ETFP.MI',
  name: 'ACME FTSE Global All-Cap UCITS',
  ccy: 'EUR',
  cls: 'equity',
  sub: 'etf',
  value: 10_000,
  _raw: {},
} as unknown as BookRow
const D = { all: [FUND] } as unknown as DerivedBook

function xray(over: Partial<PortfolioXray> = {}): PortfolioXray {
  return {
    base_currency: 'EUR',
    total_base: 10_000,
    by_sector: [],
    by_region: [
      { name: 'North America', value_base: 6_000, pct: 60 },
      { name: 'Europe', value_base: 2_000, pct: 20 },
      { name: 'Pacific', value_base: 1_000, pct: 10 },
      { name: 'Emerging markets', value_base: 1_000, pct: 10 },
    ],
    unmapped_pct: 0,
    sector_unmapped_pct: 0,
    region_unmapped_pct: 0,
    sector_unmapped_base: 0,
    region_unmapped_base: 0,
    looked_through_pct: 100,
    mapped_instruments: 1,
    total_instruments: 1,
    ...over,
  }
}

function regionRows(container: HTMLElement): string[] {
  return [...container.querySelectorAll('.sec-row')].map((r) =>
    (r.textContent ?? '').replace(/\s+/g, ' ').trim(),
  )
}

describe('SectorGeo — Region reads the server look-through (#210)', () => {
  it('shows a fund’s saved constituents, not its listing currency', async () => {
    querySource.mockResolvedValue(xray())
    const { container } = render(<SectorGeo d={D} />)
    fireEvent.click(screen.getByRole('button', { name: 'Region' }))

    await screen.findByText('North America')
    expect(querySource).toHaveBeenCalledWith('portfolio_xray', {}, '/api/portfolio/xray')
    expect(regionRows(container)).toEqual([
      'North America60%',
      'Europe20%',
      'Pacific10%',
      'Emerging markets10%',
    ])
  })

  it('shows what nothing can place as Unmapped, last, and never flags it as concentration', async () => {
    querySource.mockResolvedValue(
      xray({
        by_region: [{ name: 'United States', value_base: 2_000, pct: 20 }],
        region_unmapped_pct: 80,
        region_unmapped_base: 8_000,
      }),
    )
    const { container } = render(<SectorGeo d={D} />)
    fireEvent.click(screen.getByRole('button', { name: 'Region' }))

    await screen.findByText('Unmapped')
    expect(regionRows(container)).toEqual(['United States20%', 'Unmapped80%'])
    expect(container.querySelector('.mk-flag')).toBeNull()
  })
})
