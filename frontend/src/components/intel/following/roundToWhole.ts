/** Rounds shares of one whole (0–1 each, summing to 1) to `decimals` places of a percent so the
 *  rounded figures still add up to exactly 100: each is rounded down, and the units left over go
 *  to the ones that lost the most (the largest-remainder method). Rounding each on its own made
 *  a portfolio's sectors read 100.2% (owner, 2026-11-13). Returns percents. */
export function roundToWhole(shares: number[], decimals = 1): number[] {
  const unit = 10 ** decimals
  const total = shares.reduce((a, b) => a + b, 0)
  if (total <= 0) return shares.map(() => 0)
  // Scaled to the whole they are part of, in units of the last decimal shown.
  const exact = shares.map((s) => (s / total) * 100 * unit)
  const floors = exact.map(Math.floor)
  let left = 100 * unit - floors.reduce((a, b) => a + b, 0)
  const byRemainder = exact
    .map((v, i) => ({ i, r: v - floors[i]! }))
    .sort((a, b) => b.r - a.r || a.i - b.i)
  for (const { i } of byRemainder) {
    if (left <= 0) break
    floors[i]! += 1
    left -= 1
  }
  return floors.map((f) => f / unit)
}
