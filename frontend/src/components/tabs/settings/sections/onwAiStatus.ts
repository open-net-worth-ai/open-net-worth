// Open Net Worth AI's status read (Settings → AI): the shapes, and how the credit line reads.

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
  // The portal resets at 00:00 UTC on the 1st; shown in UTC so it never reads as the 30th.
  const day = resets.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
  return `${left} · resets ${day}`
}
