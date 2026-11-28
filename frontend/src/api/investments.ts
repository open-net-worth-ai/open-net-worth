import { authFetch } from './client'
import type { ActivityFeed } from '../types/activity'
// Fetch helpers for the P2-INV5 fees-summary + portfolio X-ray (look-through) surfaces.
// Kept separate from api/portfolio.ts (owned elsewhere) so the X-ray panel + fees line have a
// dedicated, typed client. All math is server-side (services/lots, services/xray); these just
// marshal JSON.

export interface FeesSummary {
  base_currency: string
  acquisition_fees_base: number
  disposal_fees_base: number
  total_fees_base: number
  by_instrument: Record<
    string,
    { acquisition_fees_base: number; disposal_fees_base: number; total_fees_base: number }
  >
}

export interface XrayBucket {
  name: string
  value_base: number
  pct: number
}

export interface PortfolioXray {
  base_currency: string
  total_base: number
  by_sector: XrayBucket[]
  by_region: XrayBucket[]
  unmapped_pct: number
  sector_unmapped_pct: number
  region_unmapped_pct: number
  sector_unmapped_base: number
  region_unmapped_base: number
  looked_through_pct: number
  mapped_instruments: number
  total_instruments: number
  // Flag `fund_lookthrough` only: how each axis was placed, and the server's sector verdict.
  sector_coverage?: XrayCoverage
  region_coverage?: XrayCoverage
  sector_flag?: { name: string; pct: number; threshold_pct: number } | null
}

export interface XrayCoverage {
  looked_through: number
  classified: number
  unmapped: number
}

export interface Constituent {
  weight: number
  sector: string
  region: string
  label?: string
}

export async function fetchFeesSummary(): Promise<FeesSummary> {
  const r = await authFetch('/api/fees-summary')
  if (!r.ok) throw new Error('fees-summary failed')
  return r.json()
}

export async function fetchPortfolioXray(): Promise<PortfolioXray> {
  const r = await authFetch('/api/portfolio/xray')
  if (!r.ok) throw new Error('portfolio xray failed')
  return r.json()
}

// Set (or clear, with []) a fund's look-through map. Validated + normalized server-side; the
// write is checkpoint-covered. Returns the updated instrument or throws on 404/422.
export async function setInstrumentConstituents(
  instrumentId: string,
  constituents: Constituent[],
): Promise<unknown> {
  const r = await authFetch(`/api/instruments/${instrumentId}/constituents`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ constituents }),
  })
  if (!r.ok) {
    let detail = 'save failed'
    try {
      detail = (await r.json()).detail || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return r.json()
}

export interface ActivityParams {
  date_from?: string
  date_to?: string
  type?: string[]
  account_id?: string
  limit?: number
  offset?: number
}

export async function fetchInvestmentActivity(params: ActivityParams = {}): Promise<ActivityFeed> {
  const q = new URLSearchParams()
  if (params.date_from) q.set('date_from', params.date_from)
  if (params.date_to) q.set('date_to', params.date_to)
  if (params.type?.length) q.set('type', params.type.join(','))
  if (params.account_id) q.set('account_id', params.account_id)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  const r = await authFetch(`/api/investments/activity${qs ? '?' + qs : ''}`)
  if (!r.ok) throw new Error('investments/activity failed')
  return r.json()
}
