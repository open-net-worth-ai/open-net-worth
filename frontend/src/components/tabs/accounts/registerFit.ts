/**
 * registerFit.ts — how the register's columns use the room the register actually has.
 *
 * The register is a fixed-pixel grid, so on a laptop (or with the Lucius panel open beside it)
 * its columns can outrun the container. First every column except Description is squeezed toward
 * its resize minimum, always leaving Description room to read. When even that does not fit, the
 * grid overflows (`overflow`): the rows scroll sideways and the Amount column pins to the right
 * edge. No column the user chose is ever dropped — Memo and Source used to be set aside here,
 * which left a scroll that ended at Description with no way to reach them (owner, 2026-10-25).
 * Nothing here is persisted: widening the window gives every column its own width back.
 */

import type { RegCol } from './Register'

/** The row grid's horizontal padding (18px a side) and column gap — keep in step with `.acct-tr`. */
const ROW_PADDING = 36
const COLUMN_GAP = 10
/** Description is the column worth defending: the bank string you came to read. */
export const DESCRIPTION_COMFORT = 140
export const DESCRIPTION_FLOOR = 72

export interface RegisterFit {
  widths: Record<string, number>
  /** True when even the squeezed grid is wider than the container. */
  overflow: boolean
}

const minOf = (c: RegCol): number => c.min ?? c.w

/** The widths that make `cols` fit `available` with the description at `descriptionPx`, squeezing
 *  every other column toward its minimum in proportion to its slack; null when they cannot. */
function squeeze(
  cols: RegCol[],
  widths: Record<string, number>,
  available: number,
  descriptionPx: number,
): Record<string, number> | null {
  const fixed = cols.filter((c) => c.key !== 'description')
  const wanted = fixed.reduce((sum, c) => sum + (widths[c.key] ?? c.w), 0)
  const hasDescription = fixed.length < cols.length
  const deficit = wanted + (hasDescription ? descriptionPx : 0) - available
  if (deficit <= 0) return widths
  const slack = (c: RegCol) => Math.max(0, (widths[c.key] ?? c.w) - minOf(c))
  const capacity = fixed.reduce((sum, c) => sum + slack(c), 0)
  if (capacity < deficit) return null
  const out = { ...widths }
  for (const c of fixed)
    out[c.key] = Math.round((widths[c.key] ?? c.w) - (slack(c) * deficit) / capacity)
  return out
}

export function fitRegisterColumns(
  cols: RegCol[],
  widths: Record<string, number>,
  containerWidth: number,
): RegisterFit {
  // Unmeasured (first paint, no layout engine): take the user's columns as they are.
  if (!(containerWidth > 0)) return { widths, overflow: false }
  const room = containerWidth - ROW_PADDING - COLUMN_GAP * (cols.length - 1)
  // The other columns give way first, keeping Description comfortable…
  const fitted = squeeze(cols, widths, room, DESCRIPTION_COMFORT)
  if (fitted) return { widths: fitted, overflow: false }
  // …then, every one at its minimum, Description (the 1fr track) narrows to its floor. Retrying the
  // squeeze with the floor instead handed the others width BACK as the room shrank.
  const floor = Object.fromEntries(cols.map((c) => [c.key, minOf(c)]))
  const atMinimum = cols.reduce((sum, c) => sum + (c.key === 'description' ? 0 : minOf(c)), 0)
  const hasDescription = cols.some((c) => c.key === 'description')
  const overflow = atMinimum + (hasDescription ? DESCRIPTION_FLOOR : 0) > room
  return { widths: floor, overflow }
}
