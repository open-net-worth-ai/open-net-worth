import type { FollowModel } from '../../../api/followedPortfolios'

/** One tile: a sector (or a stock, when sectors are unknown), sized by its weight. */
export interface Tile {
  key: string
  label: string
  weight: number
  /** The quarter's change in what they hold of it, in shares valued at the quarter-end price
   *  (0.45 = 45% more). Null when the source gives no share counts (a member's estimate). */
  change: number | null
}

/** A change under 1% is no change: the holding did not move. */
export const UNCHANGED = 0.01

/** The tiles for the map: by sector when the sectors are known for most of the weight (as the
 *  ring did), else by stock. ``sliceOf`` says which tile a holding belongs to. */
export function tilesOf(
  model: FollowModel,
  bySector: boolean,
  sliceOf: (symbol: string) => string,
): Tile[] {
  const acc = new Map<string, { weight: number; moved: number; before: number; known: boolean }>()
  for (const h of model.holdings) {
    const key = bySector ? sliceOf(h.symbol) : h.symbol
    const t = acc.get(key) ?? { weight: 0, moved: 0, before: 0, known: false }
    t.weight += h.weight
    if (h.price != null && h.prev_shares != null && h.shares_change != null) {
      t.moved += h.shares_change * h.price
      t.before += h.prev_shares * h.price
      t.known = true
    }
    acc.set(key, t)
  }
  return [...acc.entries()]
    .map(([key, t]) => ({
      key,
      label: key,
      weight: t.weight,
      change: t.known ? (t.before > 0 ? t.moved / t.before : t.moved > 0 ? 1 : 0) : null,
    }))
    .sort((a, b) => b.weight - a.weight)
}
