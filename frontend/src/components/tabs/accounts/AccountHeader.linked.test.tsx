// @vitest-environment jsdom
//
// An account linked to a connector says so in its header line ("• Plaid", with a link glyph),
// so linking on the Connectors page is visible where the account itself is read — and tapping it
// opens that connector's page.

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AccountHeader } from './AccountHeader'
import { fromApiAccount } from './data'
import type { ApiAccount } from './types'

const noop = () => {}

afterEach(cleanup)

function header(externalIds: unknown[]) {
  const account = fromApiAccount({
    id: 'acc-1',
    name: 'Harbor Checking',
    kind: 'asset',
    type: 'cash',
    currency: 'USD',
    balance: 0,
    balance_base: 0,
    cleared_balance: 0,
    cleared_balance_base: 0,
    external_ids: externalIds,
  } as ApiAccount)
  return render(
    <AccountHeader
      account={account}
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
}

const link = (ref: string) => ({
  source: 'plaid',
  ref,
  name: 'Gold Checking',
  mask: '0000',
  linked_at: '2026-10-29',
  provider_label: 'Plaid',
})

it('names the connector once, however many of its accounts are linked here', () => {
  header([link('p1'), link('p2')])
  expect(screen.getAllByRole('button', { name: 'Plaid' })).toHaveLength(1)
})

it("tapping it opens that connector's page", () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  header([link('p1')])
  fireEvent.click(screen.getByRole('button', { name: 'Plaid' }))
  expect(assign).toHaveBeenCalledWith('/connectors?connector=plaid')
  vi.unstubAllGlobals()
})

it('says nothing for an account with no links', () => {
  const { container } = header([])
  expect(container.querySelector('.acct-head-linked')).toBeNull()
})
