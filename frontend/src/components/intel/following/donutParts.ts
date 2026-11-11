/** The ring's segments — the top holdings, then everything else as one "Others" slice — shared
 *  by the ring and its legend so a colour always means the same holding in both. */
export const PALETTE = [
  'var(--lucius)',
  'var(--good)',
  'var(--warn)',
  'var(--info, #4ea1f3)',
  'var(--bad)',
  'color-mix(in srgb, var(--lucius) 55%, var(--good))',
]
export const TOP_SEGMENTS = 6

export interface Segment {
  symbol: string
  label: string
  weight: number
  color: string
}

export function donutParts(
  weights: { symbol: string; weight: number }[],
  _count: number,
  top = TOP_SEGMENTS,
): Segment[] {
  const parts = weights.slice(0, top)
  const rest = Math.max(0, 1 - parts.reduce((s, w) => s + w.weight, 0))
  return [
    ...parts.map((w, i) => ({ ...w, label: w.symbol, color: PALETTE[i]! })),
    ...(rest > 0.005
      ? [{ symbol: '', label: 'Others', weight: rest, color: 'var(--line-strong)' }]
      : []),
  ]
}
