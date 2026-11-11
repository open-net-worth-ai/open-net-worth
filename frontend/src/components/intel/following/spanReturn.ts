/** The return over part of a cumulative series (each point is the return since the series'
 *  start): from the last point on or before ``from`` to the last on or before ``to``. The
 *  whole series when no span is given. */
export function spanReturn(
  series: { date: string; portfolio: number; benchmark: number | null }[],
  span?: { from: string; to: string },
): { portfolio: number; benchmark: number | null } | null {
  if (series.length < 2) return null
  const at = (d: string) => [...series].reverse().find((p) => p.date <= d) ?? series[0]!
  const a = span ? at(span.from) : series[0]!
  const b = span ? at(span.to) : series[series.length - 1]!
  return {
    portfolio: (1 + b.portfolio) / (1 + a.portfolio) - 1,
    // A day the benchmark has no price for leaves its return unknown, never 0%.
    benchmark:
      a.benchmark == null || b.benchmark == null ? null : (1 + b.benchmark) / (1 + a.benchmark) - 1,
  }
}
