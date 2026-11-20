// @vitest-environment jsdom
// The phone's compact chart stops at spending groups; tapping one drills into its categories.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => true }))

import { MoneyFlowModal } from './moneyFlowModal'

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const BASE = {
  month: '2026-09',
  base_currency: 'SGD',
  income_total: 1000,
  spend_total: 600,
  saved: 400,
  shortfall: 0,
  flow_total: 1000,
  savings_rate_pct: 40,
}
const node = (
  id: string,
  label: string,
  column: number,
  kind: string,
  amount: number,
  extra = {},
) => ({
  id,
  label,
  column,
  kind,
  amount,
  pct: amount / 10,
  ...extra,
})
const FULL = {
  ...BASE,
  columns: 4,
  nodes: [
    node('total', 'Income', 1, 'total', 1000),
    node('inc:s', 'Salary', 0, 'income', 1000),
    node('sg:h', 'Bills', 2, 'spend_group', 600, { color_slot: 0 }),
    node('sp:r', 'Rent', 3, 'spend', 450, {
      color_slot: 0,
      category_ids: ['cat_rent'],
      group_pct: 75,
    }),
    node('sp:u', 'Utilities', 3, 'spend', 150, {
      color_slot: 0,
      category_ids: ['cat_u'],
      group_pct: 25,
    }),
    node('saved', 'Saved', 2, 'saved', 400),
  ],
  links: [
    { source: 'inc:s', target: 'total', amount: 1000 },
    { source: 'total', target: 'sg:h', amount: 600 },
    { source: 'total', target: 'saved', amount: 400 },
    { source: 'sg:h', target: 'sp:r', amount: 450 },
    { source: 'sg:h', target: 'sp:u', amount: 150 },
  ],
}
const COMPACT = {
  ...BASE,
  columns: 3,
  nodes: [
    node('total', 'Income', 1, 'total', 1000),
    node('inc:s', 'Salary', 0, 'income', 1000),
    node('sg:h', 'Bills', 2, 'spend_group', 600, { color_slot: 0 }),
    node('saved', 'Saved', 2, 'saved', 400),
  ],
  links: [
    { source: 'inc:s', target: 'total', amount: 1000 },
    { source: 'total', target: 'sg:h', amount: 600 },
    { source: 'total', target: 'saved', amount: 400 },
  ],
}

describe('MoneyFlowModal phone drill-in', () => {
  it('taps a group to see its categories, then goes back', async () => {
    authFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(url.includes('compact=true') ? COMPACT : FULL),
      } as Response),
    )
    const onOpenRegister = vi.fn()
    render(
      <MoneyFlowModal initialMonth="2026-09" onClose={vi.fn()} onOpenRegister={onOpenRegister} />,
    )

    const bills = await screen.findByRole('link', { name: /^Bills/ })
    fireEvent.click(bills)

    await waitFor(() => expect(screen.getByText('All spending')).toBeTruthy())
    const chart = document.querySelector('.mf-chart')!
    expect(chart.textContent).toContain('Rent')
    expect(chart.textContent).toContain('75%')
    expect(chart.textContent).not.toContain('Salary')

    fireEvent.click(screen.getByRole('link', { name: /^Rent/ }))
    expect(onOpenRegister).toHaveBeenCalledWith({ category: 'cat_rent', month: '2026-09' })
  })

  it('returns to the overview from the back chip', async () => {
    authFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(url.includes('compact=true') ? COMPACT : FULL),
      } as Response),
    )
    render(<MoneyFlowModal initialMonth="2026-09" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('link', { name: /^Bills/ }))
    fireEvent.click(await screen.findByText('All spending'))
    expect(screen.queryByText('All spending')).toBeNull()
    expect(document.querySelector('.mf-chart')!.textContent).toContain('Salary')
  })

  it('opens a group from its wide ribbon, not only from the thin bar at its end', async () => {
    authFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(url.includes('compact=true') ? COMPACT : FULL),
      } as Response),
    )
    render(<MoneyFlowModal initialMonth="2026-09" onClose={vi.fn()} />)
    await screen.findByRole('link', { name: /^Bills/ })
    const ribbons = document.querySelectorAll('.mf-lnk.is-link')
    // Income → Bills opens Bills; Salary → Income and Income → Saved open nothing.
    expect(ribbons.length).toBe(1)
    fireEvent.click(ribbons[0]!)
    await waitFor(() => expect(screen.getByText('All spending')).toBeTruthy())
  })
})
