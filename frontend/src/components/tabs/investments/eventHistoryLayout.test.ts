import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Whitespace-collapsed: Prettier owns how a long selector or track list is wrapped.
const css = readFileSync(resolve(__dirname, 'design.css'), 'utf8').replace(/\s+/g, ' ')

describe('investment event history desktop alignment', () => {
  it('right-aligns income headers above right-aligned values', () => {
    expect(css).toContain(
      '.inv-event-table--income .inv-event-head > :nth-child(4), .inv-event-table--income .inv-event-head > :nth-child(5) { text-align: right; }',
    )
  })

  it('gives the vest status column enough room for its chip and action to sit on one line, without hogging the row', () => {
    expect(css).toContain(
      '.inv-event-table--vest .inv-event-head, .inv-event-table--vest .inv-event-row { grid-template-columns: minmax(6.5rem, 1fr) minmax(4.5rem, 0.75fr) minmax(3.5rem, 0.65fr) minmax(6rem, 0.9fr) minmax(21rem, 0.5fr); }',
    )
  })

  it('hides the price-at-vest column on mobile, alongside units, like every other desktop-only column', () => {
    expect(css).toContain(
      '.inv-event-units, .inv-event-kind, .inv-event-name, .inv-event-price { display: none; }',
    )
  })
})
