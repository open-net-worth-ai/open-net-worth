import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { querySource } from './dataQuery'
import { NET_WORTH_KEY } from './keys'

export interface NetWorthBreakdown {
  accounts_asset: number
  accounts_liability: number
  market_positions: number
  options_mark: number
  /** The `retirement`-band total (CPF, SRS, pension — locked until retirement). */
  retirement_base: number
}

export interface NetWorthData {
  base_currency: string
  net_worth: number
  /** `net_worth` minus `breakdown.retirement_base` — what's actually spendable today, since
   *  retirement money is locked. */
  net_worth_excl_retirement: number
  /** Unvested equity (bucket="locked"), reported alongside but EXCLUDED from `net_worth`
   *  (ADR 2026-07-31-unvested-equity-excluded-from-net-worth). */
  unvested_base: number
  breakdown: NetWorthBreakdown
}

const fetchNetWorth = () => querySource<NetWorthData>('net_worth', {}, NET_WORTH_KEY)

/**
 * The ONE net worth — GET /api/net-worth (backend/services/ledger.py::net_worth()). Every
 * "net worth" surface in the app (the Overview headline, the FIRE projection's `current_nw`,
 * …) must read this, never re-sum usePortfolio() rows client-side — that duplicate computation
 * drifted from this one (unvested equity double-counted) and shipped a S$120,000 headline
 * against a true S$98,000 (ADR 2026-07-31-single-source-net-worth).
 *
 * Prices in the backend figure refresh on the scheduler's own ~2min cadence, not live on every
 * keystroke like usePortfolio()'s client-side price fetch did — a deliberate trade-off (see the
 * ADR): a number that is briefly stale beats a number that is silently wrong. Refetches on
 * portfolio/ledger/account writes via `useDataVersion`.
 */
export function useNetWorth(): QueryCacheResult<NetWorthData> {
  const version = useDataVersion(['portfolio', 'ledger', 'accounts', 'assets'])
  return useQueryCache<NetWorthData>(NET_WORTH_KEY, fetchNetWorth, { version })
}
