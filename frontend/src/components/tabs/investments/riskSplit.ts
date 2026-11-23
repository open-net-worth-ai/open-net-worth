/** How much of a name is held directly and inside which funds (flag `fund_lookthrough`).
 *  Every figure is the server's (`investments_book._with_fund_holdings`, funds largest first);
 *  this only words it. */
export interface RiskSplit {
  directPct: number
  funds: Array<{ symbol: string; pct: number }>
}

/** Funds named on the line; the rest fold into "+N more". The tooltip lists every one. */
const SHOWN_FUNDS = 2

const pct1 = (n: number) => `${n.toFixed(1)}%`

/** "19.6% direct · 3.6% via ACMI · 2.6% via ACMQ · +3 more 5.2%", or for a name held only
 *  through funds "all via funds: 3.7% ACMI · 2.7% ACMQ". */
export function splitLine(s: RiskSplit): string {
  const shown = s.funds.slice(0, SHOWN_FUNDS)
  const rest = s.funds.slice(SHOWN_FUNDS)
  const direct = s.directPct >= 0.05
  const parts = [
    ...(direct ? [`${pct1(s.directPct)} direct`] : []),
    ...shown.map((f) => (direct ? `${pct1(f.pct)} via ${f.symbol}` : `${pct1(f.pct)} ${f.symbol}`)),
  ]
  if (rest.length) parts.push(`+${rest.length} more ${pct1(rest.reduce((t, f) => t + f.pct, 0))}`)
  return direct ? parts.join(' · ') : `all via funds: ${parts.join(' · ')}`
}

/** Every fund, one per line — the tooltip behind a capped line. */
export function splitDetail(s: RiskSplit): string {
  return [
    ...(s.directPct >= 0.05 ? [`${pct1(s.directPct)} held directly`] : []),
    ...s.funds.map((f) => `${pct1(f.pct)} inside ${f.symbol}`),
    "Counted from each fund's top 10 holdings — at least this much.",
  ].join('\n')
}
