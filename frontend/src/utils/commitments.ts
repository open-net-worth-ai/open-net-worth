// Pure helpers for the Commitments tab — kept out of the component so they're unit-testable.
import type { Commitment, CommitmentAction, FundingSource, Installment } from '../types/commitments'
import {
  fCompactAmount,
  fCurrency,
  fDate,
  fMonth,
  getBaseCurrency,
  toBase as toBaseFx,
} from './format'

export const KIND_LABELS: Record<string, string> = {
  purchase: 'Purchase',
  loan_repayment: 'Loan repayment',
  auto_pay: 'Auto-pay',
  goal: 'Goal',
}

export const FUNDING_LABELS: Record<string, string> = {
  cash: 'Cash earmark',
  income: 'Recurring savings',
  asset_sale: 'Asset sale',
  loan: 'Loan',
  windfall: 'Windfall',
}

export function nativeTotal(c: Commitment): number {
  return c.installments.reduce((s, i) => s + (i.amount || 0), 0)
}

export function paidTotal(c: Commitment): number {
  return c.installments.filter((i) => i.status === 'paid').reduce((s, i) => s + (i.amount || 0), 0)
}

export function remainingTotal(c: Commitment): number {
  return c.installments.filter((i) => i.status !== 'paid').reduce((s, i) => s + (i.amount || 0), 0)
}

/** Paid share as an already-multiplied percent number (0–100); format with fPct for display. */
export function paidPct(c: Commitment): number {
  const total = nativeTotal(c)
  return total ? (paidTotal(c) / total) * 100 : 0
}

/** Σ base-currency value of unpaid installments — `toBase` from utils/format applied per row. */
export function remainingBase(c: Commitment, fx: Record<string, number>): number {
  return c.installments
    .filter((i) => i.status !== 'paid')
    .reduce((s, i) => s + toBaseFx(i.amount || 0, c.currency, fx), 0)
}

/** Σ base-currency value of PAID installments. The native-currency `paidTotal` must never be
 *  rendered with `fBase` — on a ₫ commitment that printed "S$1,922,829,852.00" beside a real
 *  base-currency "Remaining", i.e. two different currencies wearing the same symbol in one
 *  stat row. Same reasoning as the backend's historical-rate lock: a figure is only base
 *  once it has actually been converted. */
export function paidBase(c: Commitment, fx: Record<string, number>): number {
  return c.installments
    .filter((i) => i.status === 'paid')
    .reduce((s, i) => s + toBaseFx(i.amount || 0, c.currency, fx), 0)
}

/** Σ base-currency value of ALL installments (the contract total). */
export function nativeTotalBase(c: Commitment, fx: Record<string, number>): number {
  return c.installments.reduce((s, i) => s + toBaseFx(i.amount || 0, c.currency, fx), 0)
}

/** First not-yet-paid installment, earliest by due_date. */
export function nextInstallment(c: Commitment): Installment | null {
  const unpaid = c.installments.filter((i) => i.status !== 'paid')
  if (!unpaid.length) return null
  return [...unpaid].sort((a, b) => (a.due_date || '').localeCompare(b.due_date || ''))[0] ?? null
}

/** Whole days from `today` (local midnight) to an ISO date — negative if past.
 *  Parses the YYYY-MM-DD into LOCAL components so the countdown is correct in any
 *  timezone (a bare `new Date('2026-06-13')` is UTC midnight and drifts ±1 day). */
export function daysUntil(date: string, today: Date = new Date()): number {
  const [y, m, d] = (date || '').split('-').map(Number)
  if (!y) return 0
  const target = new Date(y, (m || 1) - 1, d || 1)
  const t = new Date(today)
  t.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - t.getTime()) / 86400000)
}

// ── date formatting (design handoff parity: fmtDate/fmtDay/monthShort/monthLong) ──

/** "13 Jun 2026" — due / maturity / shortfall dates always carry their year. */
export function fmtDate(iso: string): string {
  if (!iso) return '—'
  return fDate(iso, 'short', 'always')
}

/** "13 Jun" (year only when it isn't the current one) */
export function fmtDay(iso: string): string {
  if (!iso) return '—'
  return fDate(iso)
}

/** "Jun" from a "YYYY-MM" key */
export function monthShort(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  if (!y) return ym
  return new Date(y, (m || 1) - 1, 1).toLocaleDateString(undefined, { month: 'short' })
}

/** "Aug 2026" from a "YYYY-MM" key — always with the year, for an axis that spans more than
 *  one calendar year (a bare "Jan" is ambiguous once the timeline crosses into a second year). */
export function monthShortYear(ym: string): string {
  return fMonth(ym, 'short', 'always')
}

/** "June" / "June 2027" from a "YYYY-MM" key (year only when it isn't the current one) */
export function monthLong(ym: string): string {
  return fMonth(ym, 'long')
}

// ── dual/compact currency formatting (design handoff parity: fNativeCompact/fDual) ──

/** Compact currency amount WITH symbol — S$50k, ₫2.01B — for dense chips/chart labels. */
export function fCompactCurrency(amt: number, ccy?: string): string {
  const code = (ccy || getBaseCurrency()).toUpperCase()
  const value = amt === 0 ? 0 : amt
  return fCompactAmount(value, code, 1)
}

/** Native amount, plus " · base" when the commitment's currency differs from base
 *  (e.g. "₫938M · S$49,135"); bare base-currency format when it doesn't. */
export function fDual(amt: number, ccy: string, fx: Record<string, number>): string {
  const code = (ccy || getBaseCurrency()).toUpperCase()
  if (code === getBaseCurrency()) return fCurrency(amt, code)
  return `${fCompactCurrency(amt, code)} • ${fCurrency(toBaseFx(amt, code, fx), getBaseCurrency())}`
}

// ── tracker vs. assessed commitment (design handoff parity: isTracker/trackerBehind) ──

/** goal/auto_pay are TRACKERS — aspirational progress, never run through the cash-coverage
 *  verdict (see backend `_PROJECTED_KINDS`/`services/commitments.py`). */
export function isTracker(c: Commitment): boolean {
  return c.kind === 'goal' || c.kind === 'auto_pay'
}

/** A tracker is "behind" if a scheduled contribution's due date has passed unmade. */
export function trackerBehind(c: Commitment, today: Date = new Date()): boolean {
  if (!isTracker(c) || c.status !== 'active') return false
  return c.installments.some((i) => i.status !== 'paid' && daysUntil(i.due_date, today) < 0)
}

/** Row status chip vocabulary (design handoff `riskState(c)`): risk | ok | track | behind | off. */
export type RiskState = 'risk' | 'ok' | 'track' | 'behind' | 'off'
export function riskState(c: Commitment): RiskState {
  if (c.status !== 'active') return 'off'
  if (isTracker(c)) return trackerBehind(c) ? 'behind' : 'track'
  if (c.coverage?.at_risk) return 'risk'
  return 'ok'
}

// ── funding plan aggregates ──

/** Σ of every non-disabled funding source's base value (design handoff `fundedTotal`). */
export function fundedTotal(c: Commitment): number {
  return (c.funding_plan || []).reduce((s, f) => s + (f.disabled ? 0 : (f.value_base ?? 0)), 0)
}

/** Total recurring monthly set-aside across active commitments' income-type sources. */
export function monthlySetAside(list: Commitment[]): number {
  return list
    .filter((c) => c.status === 'active')
    .flatMap((c) => c.funding_plan || [])
    .filter((f) => !f.disabled && f.type === 'income' && f.cadence === 'monthly')
    .reduce((s, f) => s + (f.value_base ?? 0), 0)
}

const monthKey = (date: string): string => (date || '').slice(0, 7)

/** Whole months from today to a date, clamped >= 0 — for envelope accrual capping. */
export function monthsUntil(date: string, today: Date = new Date()): number {
  if (!date) return 0
  const [y, m] = date.split('-').map(Number)
  if (!y || !m) return 0
  return Math.max(0, (y - today.getFullYear()) * 12 + (m - 1 - today.getMonth()))
}

/** A funding source is the ONE recurring budget envelope this commitment relies on —
 *  distinguishes it from a one-time draw (asset_sale/loan/windfall/cash). */
export function isEnvelopeSource(f: FundingSource): boolean {
  return f.type === 'income' && f.cadence === 'monthly'
}

/** Live envelope accrual info for the ONE income-type source on a commitment — best-effort
 *  fetched from Planning (`envelope_id` -> this month's assigned/available), since the
 *  funding source itself carries no `target` sub-object in the real schema (design-only). */
export interface EnvelopeAccrual {
  envelope: string
  per_period: number
  accumulated: number
}

export interface FundingAllocation {
  id: string
  label: string
  type: FundingSource['type']
  base: number
  isEnvelope: boolean
}

export interface InstallmentFunding {
  alloc: FundingAllocation[]
  total: number
  covered: number
  shortfall: number
  envBase: number
}

/** The per-installment funding WATERFALL (design handoff `installmentFunding`): dated draws
 *  (loan/sale/windfall/cash) are earmarked to the earliest unpaid installment they're
 *  available for; the recurring set-aside envelope tops up the rest, capped at what it will
 *  have accrued by each due date — so it can never contribute money it hasn't saved yet.
 *  All amounts in base currency. `toBase` converts a native amount using the commitment's
 *  currency; `envelope` is the live accrual info for the one income-type source, if any. */
export function installmentFunding(
  c: Commitment,
  toBase: (amt: number) => number,
  envelope: EnvelopeAccrual | null,
  today: Date = new Date(),
): { env: FundingSource | null; byInst: Record<string, InstallmentFunding> } {
  const env = (c.funding_plan || []).find(isEnvelopeSource) || null
  const perPeriod = envelope?.per_period ?? 0
  const accumulated = envelope?.accumulated ?? 0
  const draws = (c.funding_plan || [])
    .filter((f) => !f.disabled && !isEnvelopeSource(f) && (f.value_base ?? 0) > 0)
    .map((f) => ({
      id: f.id,
      label: f.label,
      type: f.type,
      avail: f.available_date,
      remaining: f.value_base ?? 0,
    }))
    .sort((a, b) => (a.avail || '0000').localeCompare(b.avail || '0000'))
  const unpaid = [...c.installments]
    .filter((i) => i.status !== 'paid')
    .sort((a, b) => (a.due_date || '').localeCompare(b.due_date || ''))

  const pool: typeof draws = []
  let envUsed = 0
  let di = 0
  const byInst: Record<string, InstallmentFunding> = {}

  for (const inst of unpaid) {
    const dueYm = monthKey(inst.due_date)
    while (
      di < draws.length &&
      (draws[di]!.avail == null || monthKey(draws[di]!.avail!) <= dueYm)
    ) {
      pool.push(draws[di]!)
      di++
    }
    const total = toBase(inst.amount)
    let need = total
    const alloc: FundingAllocation[] = []
    for (const d of pool) {
      if (need <= 0.5) break
      if (d.remaining <= 0.5) continue
      const take = Math.min(need, d.remaining)
      d.remaining -= take
      need -= take
      alloc.push({ id: d.id, label: d.label, type: d.type, base: take, isEnvelope: false })
    }
    let envBase = 0
    if (env && need > 0.5) {
      const envAvail = accumulated + perPeriod * monthsUntil(inst.due_date, today) - envUsed
      if (envAvail > 0.5) {
        envBase = Math.min(need, envAvail)
        envUsed += envBase
        need -= envBase
        alloc.push({
          id: env.id,
          label: env.label,
          type: 'income',
          base: envBase,
          isEnvelope: true,
        })
      }
    }
    alloc.sort((a, b) => Number(b.isEnvelope) - Number(a.isEnvelope) || b.base - a.base)
    byInst[inst.id] = {
      alloc,
      total,
      covered: total - Math.max(0, need),
      shortfall: Math.max(0, need),
      envBase,
    }
  }
  return { env, byInst }
}

// ── "not happening" impact analysis (design handoff `actionRole`) ──

export type ActionRole = 'inflow' | 'outflow' | 'prep'

/** Classifies an action by its role in the funding plan so a "not happening" outcome can
 *  state the right consequence: inflow = money in (fails -> cushion drops), outflow = money
 *  out / obligation (fails -> arrears), prep = enabler. */
export function actionRole(a: CommitmentAction): ActionRole {
  if (!a) return 'prep'
  if (a.funding_id) return 'inflow'
  const cat = a.category || ''
  const lab = (a.label || '').trim()
  if (
    /^(confirm|set up|review|re-verify|sign)\b/i.test(lab) ||
    /review|handover|pre-arrangement|documentation/i.test(cat)
  ) {
    if (/\bSPA\b|installment|payment/i.test(lab)) return 'outflow'
    return 'prep'
  }
  if (/loan repay|apt payment/i.test(cat) || /auto-debit|installment/i.test(lab)) return 'outflow'
  if (
    a.amount != null &&
    (/receive|bonus|sell|repayment|sale|dividend|set aside/i.test(lab) ||
      /savings|stock sale|cash transfer/i.test(cat))
  ) {
    return 'inflow'
  }
  return 'prep'
}

// ── loan intelligence (design handoff `loanIntel` — pure amortization math) ──

export interface LoanIntel {
  paidCount: number
  balance: number
  apr: number
  term: number
  payment: number
  monthsLeft: number
  extra: number
  monthsSaved: number
  interestSaved: number
}

/** Current balance from payments made, remaining term, and an early-payoff scenario
 *  (pay `extra` more per month -> months saved + interest saved). Pure math — no
 *  server round trip; the `loan` terms are stored on the commitment at creation. */
export function loanIntel(c: Commitment, extra = 200): LoanIntel | null {
  if (!c.loan) return null
  const { principal, apr, term_months: term, payment } = c.loan
  const i = apr / 1200
  const k = c.installments.filter((x) => x.status === 'paid').length
  const bal =
    i === 0
      ? principal - payment * k
      : principal * (1 + i) ** k - payment * (((1 + i) ** k - 1) / i)
  if (payment <= 0) return null
  const nLeft = i === 0 ? bal / payment : (Math.log(1 - (bal * i) / payment) / Math.log(1 + i)) * -1
  const nAt = (pmt: number) =>
    i === 0 ? bal / pmt : (Math.log(1 - (bal * i) / pmt) / Math.log(1 + i)) * -1
  const nFast = nAt(payment + extra)
  const interestNow = payment * nLeft - bal
  const interestFast = (payment + extra) * nFast - bal
  return {
    paidCount: k,
    balance: bal,
    apr,
    term,
    payment,
    monthsLeft: Math.ceil(nLeft),
    extra,
    monthsSaved: Math.max(0, Math.round(nLeft - nFast)),
    interestSaved: Math.max(0, interestNow - interestFast),
  }
}
