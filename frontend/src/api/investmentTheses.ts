import { authFetch } from './client'
import type { InvestmentThesis, InvestmentThesisFields } from '../types/assetDetail'

const SAVE_FAILED = "Couldn't save your thesis. Try again."

/** One stock's thesis plus what an editor off the asset page needs to show it. */
export interface ThesisContext {
  instrument_id: string
  currency: string
  held: boolean
  thesis: InvestmentThesis | null
}

/** GET /api/investment-theses/for — by instrument id, else ticker. Null when no instrument
 *  carries the ticker yet (404). */
export async function fetchThesisFor(target: {
  instrumentId?: string
  ticker?: string
}): Promise<ThesisContext | null> {
  const q = new URLSearchParams()
  if (target.instrumentId) q.set('instrument_id', target.instrumentId)
  if (target.ticker) q.set('ticker', target.ticker)
  const res = await authFetch(`/api/investment-theses/for?${q}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error("Couldn't load your thesis.")
  return (await res.json()) as ThesisContext
}

/** PUT /api/investment-theses/{id} — replaces the thesis with `fields` (anything omitted is blanked).
 *  Rejects with the server's reason on a 422 (e.g. a buy-zone floor above its top). */
export async function saveInvestmentThesis(
  instrumentId: string,
  fields: InvestmentThesisFields,
): Promise<InvestmentThesis | null> {
  const res = await authFetch(`/api/investment-theses/${encodeURIComponent(instrumentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  if (!res.ok) {
    let detail = SAVE_FAILED
    if (res.status === 422) {
      try {
        const body = (await res.json()) as { detail?: unknown }
        if (typeof body.detail === 'string' && body.detail) detail = body.detail
      } catch {
        // keep the generic message
      }
    }
    throw new Error(detail)
  }
  const body = (await res.json()) as { thesis: InvestmentThesis | null }
  return body.thesis
}
