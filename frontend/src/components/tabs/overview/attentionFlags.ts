// Needs attention — the ONE list of what wants the owner's eye. The Dashboard widget and the
// Lucius home both render it, so a count on one screen is the count on the other (ADR
// 2026-10-14-one-definition-per-fact-across-screens). Every figure and every threshold is the
// server's; this module decides which flags exist and how they read, never what the numbers are.

import { useDataVersion } from '../../../hooks/useDataVersion'
import { fetchJson } from '../../../hooks/serverState'
import { useInvestmentSignals } from '../../../hooks/useInvestmentSignals'
import { useQueryCache } from '../../../state/queryCache'
import type { SignalKind } from '../../../types/investmentSignals'
import { fBase, fBaseWhole, fMonth, fPct } from '../../../utils/format'
import { useAnalytics, useCommitments, useFundingCoverage } from './useWidgetData'

export type AttentionIcon = 'clock' | 'gap' | 'pie' | 'card' | 'cash'

export interface AttentionFlag {
  id: string
  sev: 'hi' | 'md'
  icon: AttentionIcon
  title: string
  desc: string
  nav?: { tab: string }
}

const REVIEW_COUNT_URL = '/api/transactions/recently-added?needs_review=true&limit=1'

const DECISION_TITLE: Partial<Record<SignalKind, string>> = {
  concentration: 'Position concentration',
  rebalance: 'Portfolio off target',
  vesting: 'Upcoming vest',
}

function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many
}

/** "due in 3 days", "due today", "overdue by 2 days". */
function dueIn(days: number): string {
  if (days < 0) return `overdue by ${-days} ${plural(-days, 'day', 'days')}`
  if (days === 0) return 'due today'
  if (days === 1) return 'due tomorrow'
  return `due in ${days} days`
}

/** How many ledger transactions are waiting for review (added while the owner was away). */
function useReviewCount(): { count: number; loading: boolean } {
  const version = useDataVersion('ledger')
  const query = useQueryCache<{ total?: number }>(
    REVIEW_COUNT_URL,
    () => fetchJson<{ total?: number }>(REVIEW_COUNT_URL),
    { version },
  )
  return { count: query.data?.total ?? 0, loading: query.initialLoading }
}

export function useAttentionFlags(): { flags: AttentionFlag[]; loading: boolean } {
  const { analytics: portfolio, loading } = useAnalytics()
  const { coverage, loading: coverageLoading } = useFundingCoverage()
  const { signals, loading: signalsLoading } = useInvestmentSignals()
  const { overdueCount, overdueTotal, cardsDueSoon, loading: commitmentsLoading } = useCommitments()
  const { count: reviewCount, loading: reviewLoading } = useReviewCount()
  const a = portfolio?.attention

  const flags: AttentionFlag[] = []
  if (coverage?.at_risk) {
    // The figure is the DEEPEST gap to the cash floor in the look-ahead window, so the copy
    // names the low point and the window — the first dip is usually much shallower, and
    // pairing the trough's dollars with the first dip's date read as a different number from
    // the Commitments tab's.
    const firstDip = coverage.earliest_shortfall_date
      ? fMonth(coverage.earliest_shortfall_date, 'short', 'always')
      : null
    const lowest = coverage.lowest_balance_month
      ? fMonth(coverage.lowest_balance_month, 'short', 'always')
      : null
    const timing = [
      firstDip && `cash first dips below the floor in ${firstDip}`,
      lowest && lowest !== firstDip && `bottoms out in ${lowest}`,
    ]
      .filter(Boolean)
      .join(' and ')
    flags.push({
      id: 'shortfall',
      sev: 'hi',
      icon: 'gap',
      title: 'Funding shortfall',
      desc: `Your commitments leave you ${fBaseWhole(coverage.total_shortfall_base)} short of your ${fBase(coverage.low_balance_threshold)} cash floor at the lowest point within the next ${coverage.horizon_months} months${coverage.spans_currencies ? ", at today's exchange rates" : ''}${timing ? `: ${timing}` : ''}.`,
      nav: { tab: 'commitments' },
    })
  }
  if (overdueCount > 0) {
    flags.push({
      id: 'overdue',
      sev: 'hi',
      icon: 'clock',
      title: `${overdueCount} overdue ${plural(overdueCount, 'payment', 'payments')}`,
      desc:
        overdueTotal > 0
          ? `${fBase(overdueTotal)} is past due in Commitments.`
          : 'Past their due date in Commitments.',
      nav: { tab: 'commitments' },
    })
  }
  // A card balance alone asks nothing of the owner; a statement about to fall due does. One
  // flag per card whose unpaid statement is due within a week or overdue (server-decided).
  for (const card of cardsDueSoon) {
    const days = card.days_until_due ?? 0
    flags.push({
      id: 'card-bill-' + card.account_id,
      sev: days < 0 ? 'hi' : 'md',
      icon: 'card',
      title: `${card.label}: ${fBase(card.amount_base)} ${dueIn(days)}`,
      desc: card.estimated
        ? "Estimated from the card's statement cycle."
        : 'Still owed on the last statement.',
      nav: { tab: 'accounts' },
    })
  }
  if (coverage?.over_committed) {
    flags.push({
      id: 'over-earmarked',
      sev: 'md',
      icon: 'cash',
      title: 'Cash over-earmarked',
      desc: `You've earmarked ${fBase(coverage.reconciliation.earmarked_base)} of cash but hold ${fBase(coverage.reconciliation.liquid_base)} liquid.`,
    })
  }
  // The open portfolio decisions are the Investments brief's own ("N to decide"): a concentration
  // call, a rebalance, a vest to plan for. This card used to compute a concentration flag of its
  // own and said "All clear" beside the brief's call.
  for (const sig of signals.filter((x) => x.actionable)) {
    flags.push({
      id: 'decision-' + sig.id,
      sev: sig.kind === 'concentration' ? 'hi' : 'md',
      icon: sig.kind === 'concentration' || sig.kind === 'rebalance' ? 'pie' : 'clock',
      title: DECISION_TITLE[sig.kind] ?? sig.tag,
      desc: sig.narrative,
      nav: { tab: 'investments' },
    })
  }
  if (a?.contingent_flagged) {
    flags.push({
      id: 'contingent',
      sev: 'md',
      icon: 'clock',
      title: `${fPct(a.contingent_pct)} contingent`,
      desc: 'Depends on unvested equity or money owed to you.',
    })
  }
  if (a?.thin_cash) {
    flags.push({
      id: 'thin-cash',
      sev: 'md',
      icon: 'cash',
      title: 'Thin cash buffer',
      desc:
        a.cash_months != null
          ? `${fBase(a.cash_total)} in cash covers about ${a.cash_months} months of your spending.`
          : `Only ${fBase(a.cash_total)} in liquid cash.`,
    })
  }
  if (reviewCount > 0) {
    flags.push({
      id: 'review',
      sev: 'md',
      icon: 'clock',
      title: `${reviewCount} ${plural(reviewCount, 'transaction', 'transactions')} to review`,
      desc: 'Imported or added by Lucius. Approve them when you have a minute.',
      nav: { tab: 'accounts' },
    })
  }

  return {
    flags,
    loading: loading || coverageLoading || signalsLoading || commitmentsLoading || reviewLoading,
  }
}
