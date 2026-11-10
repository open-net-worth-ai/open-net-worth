// @vitest-environment jsdom
//
// The balance card explains its three figures with ONE "i" beside its title. An "i" under each
// small label was three hard-to-hit targets on a phone, each saying a line (owner, 2026-10-26).

import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AccountHeader } from './AccountHeader'
import { fromApiAccount } from './data'
import type { ApiAccount } from './types'

const ACCOUNT = fromApiAccount({
  id: 'acc-1',
  name: 'Harbor Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  balance: 900,
  balance_base: 900,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
} as ApiAccount)

const noop = () => {}

afterEach(cleanup)

it('puts one "i" beside the Balance title that explains all three figures', () => {
  const { container } = render(
    <AccountHeader
      account={ACCOUNT}
      dueInfo={null}
      stats={null}
      menuOpen={false}
      setMenuOpen={noop}
      collapsed={false}
      onReopen={noop}
      onAddTxn={noop}
      onEdit={noop}
      onArchive={noop}
      onDelete={noop}
      onManagePayees={noop}
      onManageCategories={noop}
      onImport={noop}
      onExport={noop}
      onReconcile={noop}
      onReview={noop}
      onStartReview={noop}
    />,
  )

  const info = screen.getByRole('button', { name: 'What these figures mean' })
  expect(container.querySelector('.acct-bal-k')?.contains(info)).toBe(true)
  expect(container.querySelectorAll('.acct-bal .info-pop-btn')).toHaveLength(1)

  fireEvent.click(info)
  const tip = screen.getByRole('tooltip').textContent ?? ''
  expect(tip).toContain('Cleared')
  expect(tip).toContain('Uncleared')
  expect(tip).toContain('Working balance')
})
