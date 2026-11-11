import type { StatCard } from '../../ui/SummaryBar'
import type { FollowModel, FollowPerformance } from '../../../api/followedPortfolios'
import { fCompactAmount } from '../../../utils/format'
import { COPY } from './following.copy'
import { signedPct } from './following.format'

const signedUsd = (n: number) => `${n >= 0 ? '+' : '−'}${fCompactAmount(Math.abs(n), 'USD')}`
const signedCount = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n)}`

/** The three figures above the chart: what the filing is worth, how many stocks it holds (each
 *  with its change since the previous filing) and the 1Y return against the benchmark. */
export function headlineCards(model: FollowModel, perf: FollowPerformance): StatCard[] {
  const cards: StatCard[] = []
  if (model.total_value != null)
    cards.push({
      label: COPY.kpiValue,
      value: fCompactAmount(model.total_value, 'USD'),
      caption:
        model.prev_total_value != null
          ? COPY.kpiSincePrevious(signedUsd(model.total_value - model.prev_total_value))
          : undefined,
    })
  cards.push({
    label: COPY.kpiCount,
    value: String(model.positions ?? model.holdings.length),
    caption:
      model.prev_positions != null && model.positions != null
        ? COPY.kpiSincePrevious(signedCount(model.positions - model.prev_positions))
        : undefined,
  })
  const year = perf.periods['12M']
  if (year?.status === 'ok' && year.return != null)
    cards.push({
      label: COPY.kpiReturn,
      value: signedPct(year.return),
      tone: year.return >= 0 ? 'pos' : 'neg',
      caption: `${perf.benchmark} ${signedPct(year.benchmark_return)}`,
    })
  return cards
}
