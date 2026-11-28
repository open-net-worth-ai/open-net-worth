// Tooltip formatting and pointer snapping for PerfChart.

export function fmtReturn(v: number): string {
  return (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
}

/** A gap between two returns, in percentage points. */
export function fmtGap(v: number): string {
  return (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + ' pts'
}

/** The index of the point nearest a horizontal position `t` (0 = left edge, 1 = right). */
export function nearestIndex(t: number, N: number): number {
  if (N < 2 || !Number.isFinite(t)) return 0
  return Math.round(Math.min(1, Math.max(0, t)) * (N - 1))
}
