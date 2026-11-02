// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { usePlanState, type SavedScenario } from './usePlanState'
import { useCashflowData, LS_VERSION, type CashflowData } from './useCashflowData'
import { useBudget, type BudgetMonth } from '../../../hooks/useBudget'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

const scenario = (id: string, name = id): SavedScenario => ({
  id,
  name,
  successPct: 50,
  odds: 50,
  saving: 1,
  ret: 1,
  vol: 1,
  monthlyRetirementSpending: 1,
  swr: null,
  lifeExp: null,
  retireAge: 60,
  atNumber: false,
  crash: false,
  weakSeq: false,
  lostDec: false,
  highInfl: false,
})

const response = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))

const cashflow = (balance: number): CashflowData => ({
  months: [],
  low_balance_threshold: 100,
  cash_balance_base: balance,
  fixed_deposit_base: 0,
  investments_base: 0,
  liquid_balance_base: balance,
  actual_income_avg: 10,
  actual_expenses_avg: 5,
  monthly_income_avg: 10,
  monthly_expenses_avg: 5,
  baseline_source: 'transactions',
  income_runway_months: null,
  simulation_active: false,
  income_override: null,
  expense_override: null,
  expense_growth_pct: 3,
  income_growth_pct: 0,
  include: { fixed_deposits: false, investments: false },
  history_months: 12,
})
const budget = (month: string): BudgetMonth => ({
  month,
  base_currency: 'USD',
  ready_to_assign: 0,
  card_credit: 0,
  held_in_envelopes: 0,
  held_outside_envelopes: 0,
  card_spend_not_covered: 0,
  assigned_in_future_months: 0,
  assigned_in_future_by_month: [],
  assigned_categories: [],
  groups: [],
  totals: { assigned: 0, activity: 0, available: 0, underfunded: 0 },
  nav: { current: month, prev: month, earliest: month, can_go_back: false },
})

describe('Plan query retention', () => {
  beforeEach(() => {
    clearQueryCache()
    localStorage.clear()
    authFetch.mockReset()
  })

  it('retains scenarios and baseline across a tab remount, then revalidates', async () => {
    authFetch.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/plan/scenarios') return response({ scenarios: [scenario('one')] })
      return response({ baseline: { saving: 10 } })
    })
    const first = renderHook(() => usePlanState())
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    first.unmount()

    let resolveScenarios!: (value: Response) => void
    let resolveBaseline!: (value: Response) => void
    authFetch.mockImplementation(
      (input) =>
        new Promise((resolve) => {
          if (String(input) === '/api/plan/scenarios') resolveScenarios = resolve
          else resolveBaseline = resolve
        }),
    )
    const second = renderHook(() => usePlanState())
    expect(second.result.current.loading).toBe(false)
    expect(second.result.current.scenarios[0]?.id).toBe('one')
    expect(second.result.current.baseline).toEqual({ saving: 10 })

    await act(async () => {
      resolveScenarios(new Response(JSON.stringify({ scenarios: [scenario('two')] })))
      resolveBaseline(new Response(JSON.stringify({ baseline: { saving: 20 } })))
    })
    await waitFor(() => expect(second.result.current.scenarios[0]?.id).toBe('two'))
  })

  it('writes successful optimistic scenario mutations through to the remount cache', async () => {
    authFetch.mockImplementation((input, init) => {
      if (init?.method === 'PUT') return response({})
      if (String(input) === '/api/plan/scenarios') return response({ scenarios: [scenario('one')] })
      return response({ baseline: null })
    })
    const first = renderHook(() => usePlanState())
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    await act(() => first.result.current.updateScenario('one', scenario('one', 'renamed')))
    first.unmount()

    authFetch.mockImplementation(() => new Promise(() => {}))
    const second = renderHook(() => usePlanState())
    expect(second.result.current.loading).toBe(false)
    expect(second.result.current.scenarios[0]?.name).toBe('renamed')
  })

  it('rejects a failed baseline write after restoring the previous cached baseline', async () => {
    authFetch.mockImplementation((input, init) => {
      if (init?.method === 'PUT') return Promise.resolve(new Response(null, { status: 500 }))
      if (String(input) === '/api/plan/scenarios') return response({ scenarios: [] })
      return response({ baseline: { saving: 10 } })
    })
    const plan = renderHook(() => usePlanState())
    await waitFor(() => expect(plan.result.current.loading).toBe(false))

    await expect(act(() => plan.result.current.saveBaseline({ saving: 20 }))).rejects.toThrow(
      'PUT /api/plan/baseline',
    )
    await waitFor(() => expect(plan.result.current.baseline).toEqual({ saving: 10 }))
  })

  it('restores the previous cached baseline after a transport failure', async () => {
    authFetch.mockImplementation((input, init) => {
      if (init?.method === 'PUT') return Promise.reject(new Error('offline'))
      if (String(input) === '/api/plan/scenarios') return response({ scenarios: [] })
      return response({ baseline: { saving: 10 } })
    })
    const plan = renderHook(() => usePlanState())
    await waitFor(() => expect(plan.result.current.loading).toBe(false))

    await expect(act(() => plan.result.current.saveBaseline({ saving: 20 }))).rejects.toThrow(
      'offline',
    )
    await waitFor(() => expect(plan.result.current.baseline).toEqual({ saving: 10 }))
  })

  it('keys cash-flow retention by the complete parameterized URL', async () => {
    authFetch.mockImplementation((input) =>
      response(cashflow(String(input).includes('income_override=99') ? 99 : 10)),
    )
    const first = renderHook(() => useCashflowData())
    await waitFor(() => expect(first.result.current.data?.cash_balance_base).toBe(10))
    act(() => {
      first.result.current.setIncomeVal('99')
      first.result.current.load('99', '', false, false, '3', '0', true)
    })
    await waitFor(() => expect(first.result.current.data?.cash_balance_base).toBe(99))
    first.unmount()
    // Must carry the CURRENT version: an unversioned/stale record has its what-if
    // overrides dropped on load by design, so the remount would fetch the un-overridden
    // baseline and this would be testing the migration rather than URL-keyed retention.
    localStorage.setItem('onwCashflowView', JSON.stringify({ v: LS_VERSION, incomeVal: '99' }))

    authFetch.mockImplementation(() => new Promise(() => {}))
    const second = renderHook(() => useCashflowData())
    expect(second.result.current.loading).toBe(false)
    expect(second.result.current.data?.cash_balance_base).toBe(99)
  })

  it('updates the safety floor after the profile changes without replacing an in-progress edit', async () => {
    let threshold = 100
    authFetch.mockImplementation(() =>
      response({ ...cashflow(10), low_balance_threshold: threshold }),
    )
    const hook = renderHook(() => useCashflowData())
    await waitFor(() => expect(hook.result.current.floorVal).toBe('100'))

    threshold = 500
    act(() => hook.result.current.load())
    await waitFor(() => expect(hook.result.current.floorVal).toBe('500'))
    expect(hook.result.current.floor).toBe(500)

    act(() => hook.result.current.setFloorVal('750'))
    threshold = 600
    act(() => hook.result.current.load())
    await waitFor(() => expect(hook.result.current.data?.low_balance_threshold).toBe(600))
    expect(hook.result.current.floorVal).toBe('750')
  })

  it('retains the keyed budget month and supplementary Plan reads on remount', async () => {
    authFetch.mockImplementation((input, init) => {
      const url = String(input)
      if (init?.method === 'POST') return response({})
      if (url === '/api/categories' || url.includes('include_hidden=true')) return response([])
      if (url === '/api/planning/notes') return response({ notes: { env_1: 'remember me' } })
      if (url.startsWith('/api/planning/health')) return response({ status: 'healthy' })
      if (url.startsWith('/api/planning/month')) return response(budget('2026-07'))
      return response({})
    })
    const first = renderHook(() => useBudget('2026-07'))
    await waitFor(() => expect(first.result.current.data?.month).toBe('2026-07'))
    await waitFor(() => expect(first.result.current.notes.env_1).toBe('remember me'))
    first.unmount()

    authFetch.mockImplementation(() => new Promise(() => {}))
    const second = renderHook(() => useBudget('2026-07'))
    expect(second.result.current.loading).toBe(false)
    expect(second.result.current.data?.month).toBe('2026-07')
    expect(second.result.current.notes.env_1).toBe('remember me')
  })
})
