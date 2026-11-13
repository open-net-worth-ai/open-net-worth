/** The categorical colours of the Holdings sector ring and its legend. */
export const PALETTE = [
  'var(--lucius)',
  'var(--good)',
  'var(--warn)',
  'var(--info, #4ea1f3)',
  'var(--bad)',
  'color-mix(in srgb, var(--lucius) 55%, var(--good))',
]

/** Past the palette's six colours, the same six again, paler: a portfolio has up to eleven
 *  sectors, and a slice is always matched to its legend row by colour. */
export const sliceColor = (i: number) =>
  i < PALETTE.length
    ? PALETTE[i]!
    : `color-mix(in srgb, ${PALETTE[i % PALETTE.length]} 45%, var(--bg))`

/** The deep navy a card's ring darkens toward, in light and dark themes alike: mixing toward
 *  the text colour turned the ring pale in dark mode (owner, 2026-11-12). */
export const RING_NAVY = '#0b1430'
/** The ring's blue: vivid, as Moomoo's largest slice is (the app accent read too soft). */
export const RING_BLUE = '#3d6cf0'

export interface Segment {
  symbol: string
  /** Its real share of the whole portfolio (what pointing at it says). */
  weight: number
  /** Its share of the ring: the slices are sized against each other, so the ring is full. */
  share: number
  color: string
}

/** A card's ring, as Moomoo draws one: a fund's largest holdings filling the circle, sized
 *  against each other, in shades of one colour (the largest the brightest). A ring of shares of
 *  the whole was mostly a grey "Others" for any fund of hundreds of stocks (owner, 2026-11-12). */
export function donutParts(weights: { symbol: string; weight: number }[]): Segment[] {
  const parts = weights.filter((w) => w.weight > 0)
  const sum = parts.reduce((n, w) => n + w.weight, 0) || 1
  const last = Math.max(1, parts.length - 1)
  return parts.map((w, i) => ({
    ...w,
    share: w.weight / sum,
    // Moomoo's ring: a vivid royal blue for the largest, the middle slices staying blue, only the
    // smallest few going dark (a curve, not an even ramp).
    color:
      i === 0
        ? RING_BLUE
        : `color-mix(in srgb, ${RING_BLUE} ${Math.round(88 - 66 * (i / last) ** 1.7)}%, ${RING_NAVY})`,
  }))
}
