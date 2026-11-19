// Shared server-state hooks — one cache key AND one module-level fetcher per shared read.
//
// RFC: .claude/rfcs/2026-07-27-frontend-server-state-consolidation.md
//
// Why this layer exists: before it, 153 files called `authFetch` directly and every component
// that wanted accounts/profile/categories fetched them itself. A single page load issued the
// same GET 2-6x concurrently (measured 2026-07-27), which on a single-process GIL-bound
// backend cost N round trips of real CPU — /api/accounts profiled at 0.057s but was observed
// at 7.0s live, purely from queueing behind duplicates.
//
// Import from here, not from the individual files.

export { fetchJson } from './fetchJson'
export * from './keys'
export { notifyPortfolioChanged } from './notifyPortfolioChanged'

export { useProfile, type ProfileData } from './useProfile'
export { useAccounts, type AccountLike } from './useAccounts'
export { useCategories, type CategoryLike } from './useCategories'
export { useSetupStatus, type SetupStatus } from './useSetupStatus'
export { useIntelSignals, type IntelSignal } from './useIntelSignals'
export { useInboxCount, type InboxCount } from './useInboxCount'
export { useMatchReviewsCount, type MatchReviewsResponse } from './useMatchReviewsCount'
export { useNetWorth, type NetWorthData, type NetWorthBreakdown } from './useNetWorth'
export {
  useCashflowProjections,
  useCommitmentsCoverageRead,
  useCommitmentsSummary,
  useDividendSummary,
  useNetWorthStats,
  usePlanningSummary,
  usePortfolioAnalytics,
  useReportsRollup,
  useCashFlowGraph,
  useSpendByWeek,
} from './aggregates'
export type { ReportScope } from './aggregates'
