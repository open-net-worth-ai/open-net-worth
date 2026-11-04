import type { Amount } from '../../../types/trading'
import type { BadgeTone } from '../../ui'

export function tradingMoney(value: Amount | null | undefined, currency = 'USD') {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value))
  } catch {
    return `${Number(value).toLocaleString()} ${currency}`
  }
}
export function tradingPercent(value: Amount | null | undefined) {
  return value == null
    ? '—'
    : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`
}
export function tradingTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}
export function statusTone(value: string): BadgeTone {
  if (['running', 'completed', 'filled'].includes(value)) return 'pos'
  if (['failed', 'rejected'].includes(value)) return 'neg'
  if (
    ['paused', 'needs_reconciliation', 'submission_unknown', 'cancel_pending', 'stopping'].includes(
      value,
    )
  )
    return 'warn'
  return 'neutral'
}
export const readable = (value: string) =>
  value === 'submission_unknown'
    ? 'Outcome unknown'
    : value === 'cancel_pending'
      ? 'Cancellation requested'
      : value.replaceAll('_', ' ')
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error)
