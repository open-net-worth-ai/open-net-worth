import { describe, expect, it } from 'vitest'
import { fallbackPricedNote } from './usePerformanceView'

describe('fallbackPricedNote', () => {
  it('is null when every holding had a price', () => {
    expect(fallbackPricedNote([])).toBeNull()
  })

  it('names holdings valued at cost and at a manual price', () => {
    const note = fallbackPricedNote([
      {
        instrument_id: 'i1',
        symbol: 'ACME.DE',
        name: 'Acme fund',
        source: 'cost',
        from: '2023-03-22',
        to: '2024-01-18',
      },
      {
        instrument_id: 'i2',
        symbol: '',
        name: 'Example bond 2035',
        source: 'manual',
        from: '2024-02-01',
        to: '2026-11-26',
      },
    ])
    expect(note).toBe(
      'Some holdings have no price history for part of this range: ACME.DE is valued at what you paid from 2023-03-22 to 2024-01-18; Example bond 2035 is valued at its manual price.',
    )
  })
})
