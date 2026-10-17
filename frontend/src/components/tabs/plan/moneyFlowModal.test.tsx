// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
const mobile = vi.hoisted(() => ({ value: false }))
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => mobile.value }))

import { thisMonth } from '../../../hooks/useBudget'
import { fMonth } from '../../../utils/format'
import { MoneyFlowModal } from './moneyFlowModal'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
  mobile.value = false
})

// The real GET /api/cashflow/flow shape (services/cash_flow_graph.py), an overspent month.
const OVERSPENT = {
  month: '2026-08',
  base_currency: 'SGD',
  income_total: 3000,
  spend_total: 4000,
  saved: 0,
  shortfall: 1000,
  flow_total: 4000,
  savings_rate_pct: 0,
  columns: 4,
  nodes: [
    { id: 'total', label: 'Spent', column: 1, kind: 'total', amount: 4000, pct: 100 },
    { id: 'inc:cat_salary', label: 'Salary', column: 0, kind: 'income', amount: 3000, pct: 75 },
    { id: 'shortfall', label: 'From savings', column: 0, kind: 'shortfall', amount: 1000, pct: 25 },
    {
      id: 'sg:g_home',
      label: 'Housing',
      column: 2,
      kind: 'spend_group',
      amount: 4000,
      pct: 100,
      color_slot: 0,
    },
    {
      id: 'sp:cat_rent',
      label: 'Rent',
      column: 3,
      kind: 'spend',
      amount: 4000,
      pct: 100,
      color_slot: 0,
      category_ids: ['cat_rent'],
    },
  ],
  links: [
    { source: 'inc:cat_salary', target: 'total', amount: 3000 },
    { source: 'shortfall', target: 'total', amount: 1000 },
    { source: 'total', target: 'sg:g_home', amount: 4000 },
    { source: 'sg:g_home', target: 'sp:cat_rent', amount: 4000 },
  ],
}

function serve(body: unknown) {
  authFetch.mockImplementation(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response),
  )
}

describe('MoneyFlowModal', () => {
  it('shows an overspent month as covered from savings, never as saved', async () => {
    serve(OVERSPENT)
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)

    await waitFor(() =>
      expect(document.querySelector('.mf-kpi.neg .k')?.textContent).toBe('From savings'),
    )
    expect(document.querySelector('.mf-kpi.pos')).toBeNull()
    expect(screen.queryByText(/^Saved/)).toBeNull()
    expect(document.querySelectorAll('.mf-lnk')).toHaveLength(4)
    expect(authFetch.mock.calls[0]![0]).toContain('/api/cashflow/flow?month=2026-08')
  })

  it('opens the register filtered to a clicked spending category and month', async () => {
    serve(OVERSPENT)
    const onOpenRegister = vi.fn()
    const onClose = vi.fn()
    render(
      <MoneyFlowModal initialMonth="2026-08" onClose={onClose} onOpenRegister={onOpenRegister} />,
    )

    const rent = await screen.findByRole('link', { name: /Rent/ })
    fireEvent.click(rent)
    expect(onOpenRegister).toHaveBeenCalledWith({ category: 'cat_rent', month: '2026-08' })
    expect(onClose).toHaveBeenCalled()
  })

  it('steps months and never past the current one', async () => {
    serve({ ...OVERSPENT, nodes: [], links: [], flow_total: 0 })
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)

    await screen.findByText(`No income or spending recorded in ${fMonth('2026-08')}.`)
    fireEvent.click(screen.getByLabelText('Previous month'))
    await waitFor(() =>
      expect(authFetch.mock.calls.some(([u]) => String(u).includes('month=2026-07'))).toBe(true),
    )
  })

  it('labels the current month as month to date, and only that month', async () => {
    serve({ ...OVERSPENT, nodes: [], links: [], flow_total: 0 })
    render(<MoneyFlowModal initialMonth={thisMonth()} onClose={vi.fn()} />)

    expect(screen.getByText(`${fMonth(thisMonth())} to date`)).toBeTruthy()
    expect((screen.getByLabelText('Next month') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('Previous month'))
    expect(screen.queryByText(/ to date$/)).toBeNull()
  })
})

describe('MoneyFlowModal on a phone', () => {
  it('keeps the breakdown table shut until asked, so the chart has the sheet', async () => {
    mobile.value = true
    serve(OVERSPENT)
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)

    const toggle = await screen.findByRole('button', { name: /Breakdown/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelector('.mf-table')).toBeNull()
    expect(document.querySelector('.mf-chart--fill')).not.toBeNull()

    fireEvent.click(toggle)
    expect(document.querySelector('.mf-table')).not.toBeNull()
    expect(document.querySelector('.mf-chart--fill')).toBeNull()
  })
})

describe('MoneyFlowModal table view', () => {
  it('lists each spending group followed by its own categories', async () => {
    const node = (id: string, label: string, column: number, kind: string, amount: number) => ({
      id,
      label,
      column,
      kind,
      amount,
      pct: amount / 10,
    })
    serve({
      ...OVERSPENT,
      shortfall: 0,
      saved: 0,
      income_total: 1000,
      spend_total: 1000,
      flow_total: 1000,
      nodes: [
        node('total', 'Income', 1, 'total', 1000),
        node('inc:s', 'Salary', 0, 'income', 1000),
        node('sg:h', 'Housing', 2, 'spend_group', 600),
        node('sg:l', 'Living', 2, 'spend_group', 400),
        node('sp:rent', 'Rent', 3, 'spend', 600),
        node('sp:food', 'Food', 3, 'spend', 400),
      ],
      links: [
        { source: 'inc:s', target: 'total', amount: 1000 },
        { source: 'total', target: 'sg:h', amount: 600 },
        { source: 'total', target: 'sg:l', amount: 400 },
        { source: 'sg:h', target: 'sp:rent', amount: 600 },
        { source: 'sg:l', target: 'sp:food', amount: 400 },
      ],
    })
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Table' }))
    const rows = [...document.querySelectorAll('.mf-table tbody tr')].map((r) =>
      r.querySelector('td')!.textContent!.trim(),
    )
    expect(rows).toEqual(['Salary', 'Housing', 'Rent', 'Living', 'Food'])
  })
})

describe('MoneyFlowModal month stepping', () => {
  it('keeps the previous month on screen, dimmed, while the next one loads', async () => {
    authFetch.mockImplementation((url: string) =>
      String(url).includes('month=2026-07')
        ? new Promise(() => {}) // never resolves: the next month is still loading
        : Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(OVERSPENT),
          } as Response),
    )
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)
    await waitFor(() => expect(document.querySelector('.mf-kpis')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('Previous month'))

    expect(screen.getByText(fMonth('2026-07'))).toBeTruthy()
    expect(document.querySelector('.mf-body--stale')).toBeTruthy()
    expect(document.querySelector('.mf-kpis')).toBeTruthy()
    expect(document.querySelectorAll('.mf-lnk').length).toBeGreaterThan(0)
  })
})

describe('MoneyFlowModal drill-down filters', () => {
  const graphWith = (extra: object) => ({
    ...OVERSPENT,
    nodes: OVERSPENT.nodes.map((n) => (n.id === 'sp:cat_rent' ? { ...n, ...extra } : n)),
  })

  it('opens every member category of a merged envelope', async () => {
    serve(graphWith({ category_ids: ['cat_a', 'cat_b'] }))
    const onOpenRegister = vi.fn()
    render(
      <MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} onOpenRegister={onOpenRegister} />,
    )
    fireEvent.click(await screen.findByRole('link', { name: /Rent/ }))
    expect(onOpenRegister).toHaveBeenCalledWith({ category: ['cat_a', 'cat_b'], month: '2026-08' })
  })

  it('opens uncategorized spending for the Uncategorized row', async () => {
    serve(graphWith({ category_ids: [], uncategorized: true }))
    const onOpenRegister = vi.fn()
    render(
      <MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} onOpenRegister={onOpenRegister} />,
    )
    fireEvent.click(await screen.findByRole('link', { name: /Rent/ }))
    expect(onOpenRegister).toHaveBeenCalledWith({
      uncategorized: true,
      kind: 'expense',
      month: '2026-08',
    })
  })
})

describe('MoneyFlowModal hover tooltip', () => {
  it('names the flow under the pointer after moving from one flow to another', async () => {
    serve(OVERSPENT)
    render(<MoneyFlowModal initialMonth="2026-08" onClose={vi.fn()} />)
    await waitFor(() => expect(document.querySelectorAll('.mf-lnk').length).toBeGreaterThan(1))
    const [first, second] = [...document.querySelectorAll('.mf-lnk')]
    fireEvent.mouseEnter(first!, { clientX: 10, clientY: 10 })
    fireEvent.mouseMove(first!, { clientX: 11, clientY: 11 })
    const before = document.querySelector('.mf-tip-t')!.textContent
    // Entering the next flow and moving within it in the same tick must not restore the old one.
    fireEvent.mouseEnter(second!, { clientX: 40, clientY: 40 })
    fireEvent.mouseMove(second!, { clientX: 41, clientY: 41 })
    const after = document.querySelector('.mf-tip-t')!.textContent
    expect(after).not.toBe(before)
    expect((document.querySelector('.mf-tip') as HTMLElement).style.left).toBe('55px')
  })
})
