// Open Net Worth AI's status read (Settings → AI): the shapes, and how the credit line reads.

import { fShortDate } from '../../../../utils/format'

export const ONW_AI_KEY = 'onw_ai'
export const ONW_AI_STATUS_PATH = '/api/model-config/onw-ai'

export interface OnwAiCreditFigures {
  remaining_usd: number
  limit_usd: number
  resets_at: string | null
}

export interface OnwAiStatus {
  managed: boolean
  credit: OnwAiCreditFigures | null
}

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export function creditText(credit: OnwAiCreditFigures): string {
  const left = `${usd.format(credit.remaining_usd)} of ${usd.format(credit.limit_usd)} left`
  const resets = credit.resets_at ? new Date(credit.resets_at) : null
  if (!resets || Number.isNaN(resets.getTime())) return left
  // The portal resets at 00:00 UTC on the 1st; its UTC calendar day is shown so it never
  // reads as the 30th west of UTC.
  return `${left} · resets ${fShortDate(resets.toISOString().slice(0, 10))}`
}
