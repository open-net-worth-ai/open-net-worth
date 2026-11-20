// @vitest-environment jsdom
//
// Regression: the sidebar's "Net worth" header showed Assets/Debts summed from the account
// GROUPS (cash + investments + retirement + property), which silently excludes owned assets
// (Assets tab) — an owned asset isn't an account and belongs to no group. On a book with an
// owned asset, Assets − Debts stopped equaling the Net worth figure shown directly above them
// by exactly the owned-assets total. Fix: `assets` is now DEFINED as `netWorth + debts` (the
// same rearrangement, in reverse, as the Assets-tab summary strip: own − owe = net worth), so
// the three numbers can never disagree.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { setBaseCurrency, setDisplayFormats } from '../../../utils/format'
import { Sidebar, type SidebarTotals } from './Sidebar'

const zeroTotals: SidebarTotals = {
  cash: 0,
  fixed_deposit: 0,
  investment: 0,
  retirement: 0,
  receivable: 0,
  credit: 0,
  loan: 0,
  liabilities: -390900,
}
const noop = vi.fn()

afterEach(() => {
  cleanup()
  setBaseCurrency('SGD')
  setDisplayFormats({})
})

function renderSidebar(netWorth: number, totals: SidebarTotals) {
  return render(
    <Sidebar
      accounts={[]}
      groups={[]}
      totals={totals}
      selectedId={null}
      netWorth={netWorth}
      collapsed={false}
      onSelect={noop}
      onAdd={noop}
      onManagePayees={noop}
    />,
  )
}

describe('Sidebar — Assets is derived from net worth, never re-summed from account groups', () => {
  it('Assets − Debts equals the net worth shown above, even though every account-group total is zero', () => {
    // Every group total is 0 (as if the only real value is an owned asset, which contributes
    // to no group) — the old bug would have rendered Assets as S$0.
    renderSidebar(363029, zeroTotals)

    // liab = 390900, so assets must be 363029 + 390900 = 753929.
    expect(screen.getByText('S$753.93K')).toBeTruthy()
    expect(screen.getByText('S$390.9K')).toBeTruthy()
    // The "All accounts" row totals the groups, so it is S$0 here by design; the Assets figure is not.
    expect(
      screen.queryAllByText('S$0').filter((el) => !el.classList.contains('acct-all-v')),
    ).toEqual([])
  })

  it('renders billion-scale VND sidebar figures through the canonical compact formatter', () => {
    setBaseCurrency('VND')
    setDisplayFormats({ number_format: 'comma' })

    renderSidebar(2_008_437_944, { ...zeroTotals, liabilities: 0 })

    expect(screen.getAllByText('₫2.01B').length).toBeGreaterThan(0)
  })
})
