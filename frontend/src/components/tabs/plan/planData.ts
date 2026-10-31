// plan/planData.ts — formatters, budget helpers and FIRE types/defaults for the Plan redesign.
// Verbatim typed port of design_handoffs/plan_handoff/prototype/plan/data.jsx.
// Mock month/income data (MONTHNUM, INCOMENUM, BUDGETS, CATDEF, GROUP_DEFS, INCOME_*)
// is DROPPED — real data replaces them in the sub-tab lanes.
// The pure functions (formatters, statusOf, cat, subtotal) and the FIRE defaults are kept and
// re-exported as named exports. The retirement simulation is NOT here: it runs on the server.

import { fBaseCompact, getCurrencySymbol, getNumberLocale } from '../../../utils/format'

// ── Types ───────────────────────────────────────────────────────────────────

export interface BudgetCategory {
  key: string
  label: string
  color: string
  assigned: number
  activity: number
  available: number
  target: number
  /** target flavor: 'set_aside' | 'refill' | 'balance' | null.
   *  'balance' = the by_date savings goal ("Have a balance of $X by [date]") — measures
   *  Available toward a dated target, never resets. */
  tt: 'set_aside' | 'refill' | 'balance' | null
  cad: 'weekly' | 'monthly' | 'yearly' | null
  status: 'funded' | 'underfunded' | 'overspent' | 'overfunded' | 'none'
  needed: number
  hidden?: boolean
  // optional target-editor fields
  tamt?: number
  due?: string
  rep?: boolean
  every?: number
  everyUnit?: 'weekly' | 'monthly' | 'yearly'
}

export interface BudgetGroup {
  id: string
  name: string
  collapsed: boolean
  cats: BudgetCategory[]
  subtotal: { assigned: number; activity: number; available: number }
}

export interface BudgetMonth {
  month: string
  readyToAssign: number
  inflows: number
  carried: number
  groups: BudgetGroup[]
  totals: { assigned: number; activity: number; available: number }
  ageOfMoney: number
  totalAvailable: number
  income: {
    groups: Array<{
      id: string
      name: string
      kind: string
      collapsed: boolean
      cats: Array<{
        key: string
        label: string
        color: string
        group: string
        received: number
        ytd: number
      }>
      subtotal: { received: number; ytd: number }
    }>
    totalReceived: number
    totalYtd: number
  }
  underfundedCount: number
  underfundedAmount: number
  overspentCount: number
  overspentAmount: number
  overfundedCount: number
  overfundedAmount: number
}

export interface FireConfig {
  currentAge: number
  targetAge: number
  netWorth: number
  monthlyRetirementSpend: number
  withdrawalRate: number
  readonly recommendedSwr: number
  expectedReturn: number
  volatility: number
  monthlySaving: number
  lifeExpectancy: number
  fireNumber: number
  progressPct: number
  yearsToFi: number
  projectedFireAge: number
  monthlySavingNeeded: number
  onTrack: boolean
  retirementDuration: number
}

export interface BandRow {
  year: number
  age: number
  p10: number
  p25: number
  p50: number
  p75: number
  p90: number
}

export interface LifeResult {
  bands: BandRow[]
  retireYear: number
  horizonYears: number
  nPaths: number
  successProbability: number
  depletionProbability: number
  nestEggAtRetire: number
  nestEggBand: BandRow
  medianEnding: number
  p10Ending: number
  p90Ending: number
}

export interface SimBands {
  bands: BandRow[]
  successProbability: number
  medianEnding: number
  p10Ending: number
  p90Ending: number
  nPaths: number
  horizonYears: number
  hasWithdrawalPhase?: boolean
  depletionProbability?: number
}

// ── money formatting (SGD base) ──────────────────────────────────────────────

export function grp(n: number, cents: boolean): string {
  // Follows the number_format preference; falls back to en-US (the ported default) when the
  // user has not set one. Without this the whole Plan tab ignored the setting that Settings
  // → Currency & format promises applies "throughout the app".
  return Math.abs(n).toLocaleString(getNumberLocale() ?? 'en-US', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })
}
// Symbol source: the SAME base-currency getter the rest of the app uses
// (utils/format.getCurrencySymbol) — never a hardcoded 'S$'. Read fresh on every call (it's
// a cheap module-level lookup, not a network fetch) so a base-currency change takes effect
// without reloading this module.
//
// ALWAYS shows cents (grp(n, true)) — a name collision with a SEPARATE, correct `fBase` in
// `utils/format.ts` (which this module never imports; `budget.tsx` gets this one via
// `import * as PD from './planData'`) previously hid `cents ? ... : maximumFractionDigits: 0`
// behind an identical-looking call, so every Budget-tab total (Total available, Overspent,
// Underfunded, every group subtotal — 25+ call sites in budget.tsx alone) silently rounded to
// whole dollars while individual envelope rows (which read through fFig/fSmart, not fBase)
// stayed exact — a $5.50 overspend showed as "-S$6" one row above its own correct "-S$5.50".
// A caller that genuinely wants whole dollars has `fBaseWhole` for exactly that; `fBase` here
// is now identical to `fBaseCents` and kept as a separate name only for call-site clarity.
export function fBase(n: number): string {
  return (n < 0 ? '−' : '') + getCurrencySymbol() + grp(n, true)
}
export function fBaseCents(n: number): string {
  return (n < 0 ? '−' : '') + getCurrencySymbol() + grp(n, true)
}
export function fBaseWhole(n: number): string {
  const whole = Math.round(n)
  return (whole < 0 ? '−' : '') + getCurrencySymbol() + grp(whole, false)
}
export function fPct(n: number, frac?: boolean): string {
  const v = frac ? n * 100 : n
  return Math.round(v) + '%'
}
export function fSigned(n: number): string {
  return (n >= 0 ? '+' : '−') + getCurrencySymbol() + grp(n, false)
}
// From a million up, Outlook's tiles and Cash Flow's figures ARE the chart axis's formatter
// (fBaseCompact), so they share its unit, decimal separator and suffix in every number format:
// "₫24.95B" beside a "₫47.09B" tick, "24,95 Mrd. ₫" beside "47,09 Mrd. ₫". A figure that rounds
// up to a million at its own precision reads as that million, never "1000k" / "1000.00K".
function compactFrom(a: number, roundsToMillion: number): string | null {
  return a >= roundsToMillion ? fBaseCompact(Math.max(a, 1e6)) : null
}
// Coarse money for Outlook guidance figures — no false precision (S$307,800 → "S$310k").
export function fCoarse(n: number): string {
  const a = Math.abs(n),
    s = n < 0 ? '−' : ''
  const big = compactFrom(a, 995e3)
  if (big) return s + big
  const sym = getCurrencySymbol()
  if (a >= 1e3) return s + sym + Math.round(a / 1e4) * 10 + 'k'
  return s + sym + Math.round(a)
}
// Cash Flow balances and nets — two decimals in K.
export function fCF(n: number): string {
  const a = Math.abs(n),
    s = n < 0 ? '−' : ''
  const big = compactFrom(a, 999_995)
  if (big) return s + big
  const sym = getCurrencySymbol()
  if (a >= 1e3) return s + sym + (a / 1e3).toFixed(2) + 'K'
  return s + sym + a.toFixed(0)
}
// Figure cell — ALWAYS two decimals (owner ask, 2026-09-15, matching YNAB's own budget table).
// This is the budget TABLE's formatter (Assigned / Activity / Available and their subtotals),
// not a prose one: a column of tabular figures where some rows carry cents and some don't
// stops being a column — the decimal points no longer line up, so the eye can't compare
// magnitudes down the page, which is the entire reason the table is a grid. `fSmart` keeps the
// cents-only-when-fractional rule for prose ("S$20 more needed"), where it reads better.
export function fFig(n: number): { text: string; isZero: boolean; isNeg: boolean } {
  if (Math.abs(n) < 0.005) {
    return { text: getCurrencySymbol() + '0.00', isZero: true, isNeg: false }
  }
  return {
    text: (n < 0 ? '−' : '') + getCurrencySymbol() + grp(n, true),
    isZero: false,
    isNeg: n < 0,
  }
}
// smart money: cents only when fractional
export function fSmart(n: number): string {
  const cents = Math.round(Math.abs(n) * 100) % 100 !== 0
  return (n < 0 ? '−' : '') + getCurrencySymbol() + grp(n, cents)
}

/** A typed money amount — the Assigned cell's own parse, shared by every Plan money input:
 *  digits and a decimal point only, never negative, rounded to cents; blank or garbage is 0,
 *  never NaN. Keeping the "." matters: stripping it read "83.33" as 8333. */
export function parseMoneyInput(raw: string): number {
  const n = parseFloat(String(raw ?? '').replace(/[^0-9.]/g, ''))
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : 0
}

// ── Budget envelopes (YNAB-style) ────────────────────────────────────────────
// Two target types (tt):
//   'set_aside' — "Set aside another $X each month": assign the FULL $X every month; money
//                 builds up over time. This month's shortfall = X − assigned-this-month.
//                 (Use for bills, subscriptions, savings that accumulate.)
//   'refill'    — "Fill up to $X each month": top the envelope back up to $X; whatever you
//                 don't spend rolls over, so you only assign what's missing. Shortfall =
//                 X − (balance before this month's spending). Surplus above X = overfunded.
//                 (Use for groceries, fun money, dining out — spend-down categories.)
//   'balance'   — "Have a balance of $X by [date]": build an Available balance of $X by a
//                 deadline and don't spend it down — the by_date savings goal. Measures
//                 Available like 'refill' but never "overfunded" (reaching early is just done).
//                 (Use for long-term savings / sinking funds.)
// cad = funding cadence the target repeats on: 'weekly' | 'monthly' | 'yearly' | 'custom'
//       ('custom' is the dated by_date goal, always paired with the 'balance' flavor).
// Status (funded | underfunded | overspent | overfunded) and this-month `needed` are DERIVED,
// never hand-set, so the chips, summary counts and the target editor always agree.
/** What an envelope's target has funded so far and still needs this month — the target
 *  editor's own ring math, shared with the Underfunded quick-assign so the label and the click
 *  can never disagree. Independent of overspending (a row's status turns 'overspent' and its
 *  server `needed` reads 0 even while the target still has money to go). 'refill' / 'balance'
 *  measure the balance funded before spending; a dated set-aside measures cumulative
 *  contributions; a plain set-aside measures this month's assigned. */
export function targetProgress(c: {
  target?: number
  tt?: string | null
  cad?: string | null
  assigned: number
  activity: number
  available: number
  assignedToDate?: number
}): { monthlyTarget: number; funded: number; toGo: number } {
  const monthlyTarget = c.target || 0
  const measuresAvail = c.tt === 'refill' || c.tt === 'balance'
  const setAsideDated = c.tt === 'set_aside' && c.cad === 'custom'
  const funded = measuresAvail
    ? c.available - c.activity
    : setAsideDated
      ? (c.assignedToDate ?? c.assigned)
      : c.assigned
  return { monthlyTarget, funded, toGo: Math.max(0, monthlyTarget - funded) }
}

/** The per-envelope "Underfunded" quick-assign: the bigger of what the target still needs this
 *  month (`toGo`) and the overspending still to clear — so one click leaves the envelope both on
 *  target and not overspent, instead of funding the target first and leaving a spend bigger than
 *  the target red for a second click (owner, 2026-09-25). Both terms subtract the same assigned
 *  amount, so the max IS "assigned ≥ max(target, what was spent)". */
export function envelopeUnderfunded(toGo: number, available: number): number {
  const cover = available < -0.005 ? -available : 0
  return Math.max(toGo > 0.005 ? toGo : 0, cover)
}

export function statusOf(
  assigned: number,
  activity: number,
  available: number,
  target: number,
  tt: 'set_aside' | 'refill' | 'balance' | null,
): { status: 'funded' | 'underfunded' | 'overspent' | 'overfunded' | 'none'; needed: number } {
  const t = target || 0
  if (available < -0.005) return { status: 'overspent', needed: 0 }
  if (!t) return { status: 'none', needed: 0 }
  if (tt === 'refill' || tt === 'balance') {
    const fundedThisMonth = available - activity // rollover + assigned, before spending
    const need = Math.max(0, t - fundedThisMonth)
    if (need > 0.005) return { status: 'underfunded', needed: need }
    // A savings goal ('balance') is never "overfunded" — reaching it early is just funded.
    if (tt === 'refill' && available - t > 0.005) return { status: 'overfunded', needed: 0 }
    return { status: 'funded', needed: 0 }
  }
  const need = Math.max(0, t - assigned) // set aside the full amount each period
  if (need > 0.005) return { status: 'underfunded', needed: need }
  return { status: 'funded', needed: 0 }
}

export function cat(
  key: string,
  label: string,
  color: string,
  assigned: number,
  activity: number,
  available: number,
  target: number,
  tt: 'set_aside' | 'refill' | 'balance' | null,
  cad?: 'weekly' | 'monthly' | 'yearly',
): BudgetCategory {
  const ttResolved: 'set_aside' | 'refill' | 'balance' | null = target ? tt || 'set_aside' : null
  const s = statusOf(assigned, activity, available, target, ttResolved)
  return {
    key,
    label,
    color,
    assigned,
    activity,
    available,
    target,
    tt: ttResolved,
    cad: target ? cad || 'monthly' : null,
    status: s.status,
    needed: s.needed,
  }
}

// ── Exact cadence math (mirrors backend services/planning.needed_this_month) ────────────
// The prototype approximated a weekly target's monthly-equivalent as amount×52/12 and a
// by-date target's months-remaining via an average-month millisecond constant — both drift
// from the server's real per-month figures (a calendar month has 4 or 5 Mondays, and whole
// calendar months, not a 30.44-day average). These mirror the server exactly so the display
// (ring/progress/needed-this-month) never disagrees with what the server will actually bill.

// Number of days matching `weekday` in `ym` (YYYY-MM) — deterministic 4 or 5. `weekday` uses
// the SAME encoding as the server's target `due_weekday` (0=Monday..6=Sunday, default 0 —
// mirrors backend `_weeks_in_month(month, weekday)`, services/planning.py:1428-1444), converted
// here to JS `Date#getDay()` semantics (0=Sunday..6=Saturday) via `(weekday + 1) % 7`.
export function weeksInMonth(ym: string, weekday = 0): number {
  const y = Number(ym.slice(0, 4)),
    m = Number(ym.slice(5, 7))
  if (!y || !m) return 4
  const jsDow = (weekday + 1) % 7
  const last = new Date(y, m, 0).getDate()
  let count = 0
  for (let d = 1; d <= last; d++) if (new Date(y, m - 1, d).getDay() === jsDow) count++
  return count
}

// Whole months from the START of `ym` to `targetDate` (>=1). The current month counts, so a
// deadline within the displayed month → 1 (fund it all now).
export function monthsUntil(ym: string, targetDate?: string | null): number {
  if (!targetDate) return 1
  const ty = Number(targetDate.slice(0, 4)),
    tm = Number(targetDate.slice(5, 7))
  const my = Number(ym.slice(0, 4)),
    mm = Number(ym.slice(5, 7))
  if (!ty || !tm || !my || !mm) return 1
  return Math.max(1, (ty - my) * 12 + (tm - mm) + 1)
}

export function subtotal(cats: BudgetCategory[]): {
  assigned: number
  activity: number
  available: number
} {
  return cats.reduce(
    (a, c) => ({
      assigned: a.assigned + c.assigned,
      activity: a.activity + c.activity,
      available: a.available + c.available,
    }),
    { assigned: 0, activity: 0, available: 0 },
  )
}

// ── FIRE / retirement ────────────────────────────────────────────────────────
export const FIRE: FireConfig = {
  currentAge: 38,
  targetAge: 55,
  netWorth: 781485,
  monthlyRetirementSpend: 6000,
  withdrawalRate: 4,
  // Single source of truth: derived from the horizon via suggestSWR (never a stale constant).
  get recommendedSwr() {
    return suggestSWR(this.lifeExpectancy - this.targetAge)
  },
  expectedReturn: 6.5,
  volatility: 14,
  monthlySaving: 4200,
  lifeExpectancy: 90,
  fireNumber: 1800000,
  progressPct: 43.4,
  yearsToFi: 14.2,
  projectedFireAge: 52.2,
  monthlySavingNeeded: 5350,
  onTrack: true,
  retirementDuration: 35,
}

/** The only annualization boundary for monthly retirement spending. */
export function annualRetirementSpending(monthlyRetirementSpend: number): number {
  return monthlyRetirementSpend * 12
}

export function fireNumberFromMonthlySpend(
  monthlyRetirementSpend: number,
  withdrawalRatePct: number,
): number {
  return withdrawalRatePct > 0
    ? annualRetirementSpending(monthlyRetirementSpend) / (withdrawalRatePct / 100)
    : 0
}

/** Build a FireConfig with prototype defaults, overridden by any provided partial. */
export function makeFireConfig(partial?: Partial<Omit<FireConfig, 'recommendedSwr'>>): FireConfig {
  const base = { ...FIRE } as FireConfig & Record<string, unknown>
  if (partial) {
    // Skip null/undefined/non-finite values so a sparse or empty /api/fire response
    // (e.g. a missing date_of_birth → null current_age) never overwrites a default with
    // NaN — which would otherwise collapse `years` to NaN and produce empty MC bands.
    for (const [k, v] of Object.entries(partial)) {
      if (v == null) continue
      if (typeof v === 'number' && !Number.isFinite(v)) continue
      base[k] = v
    }
  }
  return base
}

// ── The simulation ────────────────────────────────────────────────────────────────────────
// There is no Monte Carlo in the browser. Every figure on the Outlook comes from the backend
// retirement engine (services/montecarlo.py) through outlookApi.ts — the same engine, random
// paths and saved-plan defaults Lucius's simulate_retirement uses, so the two cannot disagree
// (ADR 2026-10-14-one-retirement-engine-saving-is-a-fact). What stays here is the shape of what
// comes back.

// Rich / Broke / Dead — per-age stacked outcome bands: for every age, the share of futures that
// are Dead (mortality), Broke (portfolio depleted), or still have money — split into rich tiers
// (<2×, 2–5×, >5× the chosen base pot). `cond` is the same split conditional on being alive.
export interface OutcomeRow {
  age: number
  alive: number
  dead: number
  broke: number
  rich1: number
  rich2: number
  rich3: number
  cond: { broke: number; rich1: number; rich2: number; rich3: number }
}

// (The forecast-scorecard mock that lived here was removed — Outlook now reads the LIVE
//  GET /api/forecast-scorecard via useForecastScorecard + mapScorecard.)

// Safe withdrawal rate as a function of retirement horizon (Trinity/Bengen): ~5% over
// 20yr, 4% over the classic 30yr rule, ~3.5% over 40yr, ~3.25% approaching perpetual.
// THE one formula — used by both the FIRE Settings "Suggested" field and the Outlook caveat.
export function suggestSWR(years: number): number {
  let r: number
  if (years <= 20) r = 5.0
  else if (years <= 30)
    r = 5.0 - (years - 20) * 0.1 // 5.0 → 4.0
  else if (years <= 40)
    r = 4.0 - (years - 30) * 0.05 // 4.0 → 3.5
  else if (years <= 50)
    r = 3.5 - (years - 40) * 0.025 // 3.5 → 3.25
  else r = 3.25
  return Math.round(r / 0.25) * 0.25
}
