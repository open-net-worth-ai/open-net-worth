import type { Follow } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { shortDate } from './following.format'
import { periodRows, periods, type ActivityRow, type Names } from './activityRows'

const TOP = 3

export interface Group {
  key: string
  title: string
  periodKey: string
  rows: ActivityRow[]
}

/** The groups the strip shows, newest first: the dated trades since the last quarterly report,
 *  then that quarter's biggest buys and biggest sales. Empty groups are left out. */
export function latestGroups(follow: Follow, names: Names): Group[] {
  const spans = periods(follow)
  const quarter = spans.find((p) => p.shows === 'changes')
  const since = spans.find((p) => p.key === 'since')
  const out: Group[] = []
  // Newest first: the dated trades after the last report, then that report's quarter.
  if (since)
    out.push({
      key: 'since',
      title: COPY.recentTrades,
      periodKey: since.key,
      rows: periodRows(follow, names, since).slice(0, TOP),
    })
  if (quarter) {
    const rows = periodRows(follow, names, quarter)
    const top = (up: boolean) =>
      rows
        .filter((r) => r.up === up)
        .sort((a, b) => b.magnitude - a.magnitude)
        .slice(0, TOP)
    // Named by the day it was reported, not the quarter it covers: in November an "Apr – Jun"
    // title reads as old news, though it is the newest report there is.
    const filed = (follow.history ?? []).find((q) => q.as_of === quarter.to)?.filed_at
    const day = filed ? shortDate(filed).replace(/, \d{4}$/, '') : quarter.label
    out.push(
      { key: 'buys', title: COPY.boughtReported(day), periodKey: quarter.key, rows: top(true) },
      { key: 'sells', title: COPY.soldReported(day), periodKey: quarter.key, rows: top(false) },
    )
  }
  return out.filter((g) => g.rows.length > 0)
}
