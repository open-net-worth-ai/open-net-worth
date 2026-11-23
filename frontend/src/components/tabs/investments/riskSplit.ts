/** How much of a name is held directly and inside which funds (flag `fund_lookthrough`).
 *  Every figure is the server's (`investments_book._with_fund_holdings`); this only words it. */
export interface RiskSplit {
  directPct: number
  funds: Array<{ symbol: string; pct: number }>
}

const pct1 = (n: number) => `${n.toFixed(1)}%`

/** "19.6% direct · 3.6% via ACMI · 2.6% via ACMQ", or "all via funds: 3.7% ACMI · 2.7% ACMQ". */
export function splitLine(s: RiskSplit): string {
  if (s.directPct >= 0.05) {
    return [
      `${pct1(s.directPct)} direct`,
      ...s.funds.map((f) => `${pct1(f.pct)} via ${f.symbol}`),
    ].join(' · ')
  }
  return `all via funds: ${s.funds.map((f) => `${pct1(f.pct)} ${f.symbol}`).join(' · ')}`
}
