// plan/useCashflowData.ts — data types + fetch hook for the Cash Flow sub-tab.
// Types mirror the existing CashFlow.tsx pattern; fetch mirrors its buildUrl logic.
// Persists all view state (what-if inputs, collapsed years, filter) to localStorage
// under 'onwCashflowView' per the prototype spec.

import { useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { fMonth } from '../../../utils/format'

// ── Server response types (mirrors CashFlow.tsx) ─────────────────────────────

export interface CashEvent {
  label: string
  category: string
  direction: 'in' | 'out'
  amount_base: number
  note: string
  status: string
  /** Server marks the averaged VARIABLE buckets so the UI can group them apart from the
   *  named recurring streams — an average over many items is not a bill. */
  variable?: boolean
}

export interface ServerMonthRow {
  year_month: string
  label: string
  is_current: boolean
  is_history: boolean
  events: CashEvent[]
  total_in: number
  total_out: number
  net: number
  balance: number
  /** False for a past month before any cash account was tracked: `balance` is then 0 only
   *  because nothing was counted yet. */
  balance_tracked?: boolean
  is_tight: boolean
  is_negative: boolean
}

export interface CashflowData {
  months: ServerMonthRow[]
  low_balance_threshold: number
  cash_balance_base: number
  fixed_deposit_base: number
  investments_base: number
  liquid_balance_base: number
  actual_income_avg: number
  actual_expenses_avg: number
  monthly_income_avg: number
  monthly_expenses_avg: number
  baseline_source: 'transactions' | 'profile' | 'mixed'
  income_runway_months: number | null
  simulation_active: boolean
  income_override: number | null
  expense_override: number | null
  expense_growth_pct: number
  income_growth_pct: number
  include: { fixed_deposits: boolean; investments: boolean }
  history_months: number
}

// ── Prototype-compatible row shape ────────────────────────────────────────────

export interface MappedEvent {
  cat: string
  dir: 'in' | 'out'
  amt: number
  label: string
  note?: string
  /** A detected recurring stream (salary, rent, a subscription) rather than a dated
   *  one-off. In the CURRENT month its amount is prorated to the days still remaining. */
  recurring?: boolean
  /** The averaged VARIABLE bucket (irregular income / everyday spending) — not one payment
   *  but a 3-month average over many, so it gets its own group rather than sitting silently
   *  at the end of the named streams. */
  variable?: boolean
}

export interface MappedRow {
  ym: string
  year: number
  mName: string
  label: string
  isHistory: boolean
  isCurrent: boolean
  events: MappedEvent[] // non-recurring only (matching prototype's "scheduled" events)
  recIn: number // recurring income baseline for this month
  recOut: number // recurring expense baseline for this month
  total_in: number
  total_out: number
  net: number
  balance: number
  /** False before any cash account was tracked: show "Before tracking", never a zero. */
  balanceKnown: boolean
  isTight: boolean // balance < floor && balance >= 0
  isNeg: boolean // balance < 0
}

// Map server months to the prototype row shape.
//
// Recurring events are DISPLAYED, not stripped. They used to be hidden because the server sent
// the whole recurring baseline as two opaque blobs ("Salary" / "Living expenses") that carried
// no information worth a row. Since the stream model
// (.claude/rfcs/2026-07-27-cashflow-stream-model.md) each detected stream arrives as its OWN
// named event — Salary, Rent, Spotify — which is exactly the breakdown that explains where a
// month's "+S$26.94K recurring in" came from (owner: "otherwise users don't understand where
// it's coming from"). Hiding them left the headline number unexplainable.
//
// recIn/recOut stay = total minus non-recurring, so the header total is unchanged and still
// reconciles even if the server ever emits a recurring amount with no event row.
// isTight uses the caller's floor (may differ from server's low_balance_threshold).
function mapMonths(serverMonths: ServerMonthRow[], floor: number): MappedRow[] {
  return serverMonths.map((m) => {
    const parts = m.year_month.split('-')
    const year = parseInt(parts[0]!, 10)
    const scheduled = m.events.filter((e) => e.status !== 'recurring')
    const evIn = scheduled
      .filter((e) => e.direction === 'in')
      .reduce((s, e) => s + e.amount_base, 0)
    const evOut = scheduled
      .filter((e) => e.direction === 'out')
      .reduce((s, e) => s + e.amount_base, 0)
    // Dated one-offs first (the month's news), then the named recurring streams, then the
    // averaged variable buckets last — three groups the UI separates with headers.
    const recurringNamed = m.events.filter((e) => e.status === 'recurring' && !e.variable)
    const variableBuckets = m.events.filter((e) => e.status === 'recurring' && !!e.variable)
    const displayEvents = [...scheduled, ...recurringNamed, ...variableBuckets]
    return {
      ym: m.year_month,
      year,
      mName: fMonth(m.year_month, 'short', 'never'),
      label: m.label,
      isHistory: !!m.is_history,
      isCurrent: m.is_current,
      events: displayEvents.map((e) => ({
        cat: e.category,
        dir: e.direction,
        amt: e.amount_base,
        label: e.label,
        note: e.note || undefined,
        recurring: e.status === 'recurring',
        variable: !!e.variable,
      })),
      recIn: Math.max(0, m.total_in - evIn),
      recOut: Math.max(0, m.total_out - evOut),
      total_in: m.total_in,
      total_out: m.total_out,
      net: m.net,
      balance: m.balance,
      balanceKnown: m.balance_tracked !== false,
      isTight: m.balance_tracked !== false && m.balance < floor && m.balance >= 0,
      isNeg: m.balance < 0,
    }
  })
}

// ── URL builder (mirrors CashFlow.tsx buildUrl) ───────────────────────────────

function buildUrl(
  incomeVal: string,
  expenseVal: string,
  includeFixedDeposits: boolean,
  includeInvestments: boolean,
  expGrowth: string,
  incGrowth: string,
  withCommitments: boolean,
): string {
  const params = new URLSearchParams({ months: '40' })
  const inc = parseFloat(incomeVal)
  const exp = parseFloat(expenseVal)
  if (!isNaN(inc) && incomeVal.trim() !== '') params.set('income_override', String(inc))
  if (!isNaN(exp) && expenseVal.trim() !== '') params.set('expense_override', String(exp))
  if (includeFixedDeposits) params.set('include_fixed_deposits', '1')
  if (includeInvestments) params.set('include_investments', '1')
  const eg = parseFloat(expGrowth)
  const ig = parseFloat(incGrowth)
  if (!isNaN(eg)) params.set('expense_growth_pct', String(eg))
  if (!isNaN(ig)) params.set('income_growth_pct', String(ig))
  if (!withCommitments) params.set('include_commitments', 'false')
  return `/api/cashflow?${params}`
}

// ── localStorage key ──────────────────────────────────────────────────────────

const LS_KEY = 'onwCashflowView'

// Bump when the MEANING of a persisted field changes, so stale values can't silently drive
// the projection. v2: the income/expense what-if boxes used to be auto-seeded with the
// server's baseline and persisted, which meant every returning user carried a permanent
// `income_override` — pinning a months-old number over the live baseline and lighting
// "WHAT-IF ACTIVE" for someone who set nothing. Those two fields are dropped once on upgrade;
// everything else (collapsed years, filter, asset scope) is genuine user choice and kept.
// v3: the asset-scope `includedClasses` Set (arbitrary account-type keys) is replaced by two
// booleans, `includeFixedDeposits` / `includeInvestments`. Nothing is purged for this bump and
// nothing needs to be: neither key existed in v2, so a v2 payload has neither and both fall to
// their `false` default, while the stale `includedClasses` array is simply never read again.
// The bump records that the persisted shape moved; only `incomeVal`/`expenseVal` are dropped.
export const LS_VERSION = 3

function loadSaved(): Record<string, unknown> {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || '{}') as Record<string, unknown>
    if (saved['v'] !== LS_VERSION) {
      delete saved['incomeVal']
      delete saved['expenseVal']
    }
    return saved
  } catch {
    return {}
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useCashflowData() {
  const saved = loadSaved()

  // What-if state — persisted via localStorage.
  const [incomeVal, setIncomeVal] = useState<string>(
    (saved['incomeVal'] as string | undefined) ?? '',
  )
  const [expenseVal, setExpenseVal] = useState<string>(
    (saved['expenseVal'] as string | undefined) ?? '',
  )
  const [expGrowth, setExpGrowth] = useState<string>(
    (saved['expGrowth'] as string | undefined) ?? '3',
  )
  const [incGrowth, setIncGrowth] = useState<string>(
    (saved['incGrowth'] as string | undefined) ?? '0',
  )
  // Floor is profile-backed (PUT /api/profile), not a session what-if. It is seeded
  // from the server's low_balance_threshold on first load and never written to localStorage.
  const [floorVal, setFloorVal] = useState<string>('')
  const [includeFixedDeposits, setIncludeFixedDeposits] = useState<boolean>(
    (saved['includeFixedDeposits'] as boolean | undefined) ?? false,
  )
  const [includeInvestments, setIncludeInvestments] = useState<boolean>(
    (saved['includeInvestments'] as boolean | undefined) ?? false,
  )
  const [includeCommitments, setIncludeCommitments] = useState<boolean>(
    (saved['includeCommitments'] as boolean | undefined) ?? true,
  )

  // UI state — also persisted per prototype spec.
  const [collapsedYears, setCollapsedYears] = useState<Set<number>>(
    new Set((saved['collapsedYears'] as number[] | undefined) ?? []),
  )
  const [filter, setFilter] = useState<'all' | 'events' | 'tight'>(
    (saved['filter'] as 'all' | 'events' | 'tight' | undefined) ?? 'all',
  )

  // Fetch state.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastServerFloor = useRef<number | null>(null)
  const [requestUrl, setRequestUrl] = useState(() =>
    buildUrl(
      incomeVal,
      expenseVal,
      includeFixedDeposits,
      includeInvestments,
      expGrowth,
      incGrowth,
      includeCommitments,
    ),
  )
  const query = useQueryCache(
    requestUrl,
    async () => {
      const response = await authFetch(requestUrl)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<CashflowData>
    },
    { staleTime: 0 },
  )
  const data = query.data ?? null
  const loading = query.initialLoading
  const error = query.error && data === null ? 'Failed to load cash flow data.' : ''

  // Persist all view state to localStorage on every change.
  useEffect(() => {
    try {
      localStorage.setItem(
        LS_KEY,
        JSON.stringify({
          v: LS_VERSION,
          incomeVal,
          expenseVal,
          expGrowth,
          incGrowth,
          includeFixedDeposits,
          includeInvestments,
          includeCommitments,
          collapsedYears: [...collapsedYears],
          filter,
        }),
      )
    } catch {
      // localStorage may be unavailable in private mode; ignore silently.
    }
  }, [
    incomeVal,
    expenseVal,
    expGrowth,
    incGrowth,
    includeFixedDeposits,
    includeInvestments,
    includeCommitments,
    collapsedYears,
    filter,
  ])

  function load(
    incVal: string = incomeVal,
    expVal: string = expenseVal,
    fixedDeposits: boolean = includeFixedDeposits,
    investments: boolean = includeInvestments,
    expG: string = expGrowth,
    incG: string = incGrowth,
    withCommits: boolean = includeCommitments,
  ): void {
    const nextUrl = buildUrl(incVal, expVal, fixedDeposits, investments, expG, incG, withCommits)
    if (nextUrl === requestUrl) void query.refresh()
    else setRequestUrl(nextUrl)
  }

  const serverFloor = data?.low_balance_threshold
  useEffect(() => {
    if (serverFloor == null) return
    const previous = lastServerFloor.current
    lastServerFloor.current = serverFloor
    void Promise.resolve().then(() => {
      // The income/expense what-if boxes are DELIBERATELY not seeded with a value.
      //
      // They used to be, and it made the tab permanently lie: `buildUrl` sends
      // `income_override` whenever the box is non-empty, so an auto-filled box meant every
      // single request carried an override — the server answered `simulation_active: true`
      // and the UI lit "WHAT-IF ACTIVE" for a user who had set nothing (owner: "i really
      // don't have anything set!"). Worse, the value was persisted to localStorage, so that
      // stale number PINNED the projection: it kept overriding the live baseline even as the
      // underlying finances (and, after the stream model, the baseline's whole definition)
      // moved on.
      //
      // Empty now means exactly what it should — "use the server's baseline" — and the
      // inputs already render that baseline as a `placeholder`, so nothing is hidden from
      // the user. Only a value the user actually typed is an override.
      // See .claude/rfcs/2026-07-27-cashflow-stream-model.md.
      setFloorVal((current) =>
        current === '' || (previous != null && current === String(Math.round(previous)))
          ? String(Math.round(serverFloor))
          : current,
      )
    })
  }, [serverFloor])

  function debouncedLoad(
    incVal: string,
    expVal: string,
    fixedDeposits: boolean,
    investments: boolean,
    expG: string,
    incG: string,
    withCommits: boolean,
  ): void {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(
      () => load(incVal, expVal, fixedDeposits, investments, expG, incG, withCommits),
      500,
    )
  }

  function toggleFixedDeposits(): void {
    const next = !includeFixedDeposits
    setIncludeFixedDeposits(next)
    load(incomeVal, expenseVal, next, includeInvestments, expGrowth, incGrowth, includeCommitments)
  }

  function toggleInvestments(): void {
    const next = !includeInvestments
    setIncludeInvestments(next)
    load(
      incomeVal,
      expenseVal,
      includeFixedDeposits,
      next,
      expGrowth,
      incGrowth,
      includeCommitments,
    )
  }

  function resetSettings(): void {
    // EMPTY, not the baseline number: "reset" must clear the override, and any value here —
    // even one equal to the baseline — is sent as `income_override` and keeps the tab in
    // what-if mode. Writing the baseline back made Reset all look like it did nothing (owner:
    // "when i tap on reset all, it doesn't do anything neither") because the box still showed
    // a number and the badge stayed lit.
    const di = ''
    const de = ''
    const df = data ? String(Math.round(data.low_balance_threshold)) : ''
    setIncomeVal(di)
    setExpenseVal(de)
    setExpGrowth('3')
    setIncGrowth('0')
    setIncludeFixedDeposits(false)
    setIncludeInvestments(false)
    setIncludeCommitments(true)
    setFloorVal(df)
    load(di, de, false, false, '3', '0', true)
  }

  // Save the safety floor to the user profile (server-backed). After the PUT the
  // projection is re-fetched so the server's is_tight flags and low_balance_threshold
  // reflect the new value. Errors are silently swallowed — the local UI state is
  // already consistent; a network failure just means the value didn't persist.
  async function saveFloorToProfile(val: string): Promise<void> {
    const n = parseFloat(val)
    if (isNaN(n)) return
    try {
      await authFetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cashflow_low_balance_threshold: n }),
      })
      invalidateQueryCache('/api/profile')
    } catch {
      // Network failure: local floor display stays correct; server just didn't persist.
    }
    load()
  }

  function numOr(s: string, d: number): number {
    const n = Number(s)
    return s === '' || Number.isNaN(n) ? d : n
  }

  const floor = numOr(floorVal, data?.low_balance_threshold ?? 0)
  const months = data ? mapMonths(data.months, floor) : []

  return {
    data,
    loading,
    error,
    months,
    floor,
    incomeVal,
    setIncomeVal,
    expenseVal,
    setExpenseVal,
    expGrowth,
    setExpGrowth,
    incGrowth,
    setIncGrowth,
    floorVal,
    setFloorVal,
    includeFixedDeposits,
    includeInvestments,
    includeCommitments,
    setIncludeCommitments,
    collapsedYears,
    setCollapsedYears,
    filter,
    setFilter,
    load,
    debouncedLoad,
    toggleFixedDeposits,
    toggleInvestments,
    resetSettings,
    saveFloorToProfile,
    numOr,
  }
}
