// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RealizedEvent } from '../../types/portfolio'
import { DisposalForm } from './DisposalForm'

const ACCOUNTS = [{ id: 'cash-1', name: 'Everyday Cash', kind: 'asset', type: 'cash' }]

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))
vi.mock('../../hooks/serverState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/serverState')>()
  return { ...actual, fetchJson: async () => ACCOUNTS }
})
vi.mock('../../state/queryCache', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../state/queryCache')>()
  return { ...actual, fetchCachedQuery: (_key: string, load: () => unknown) => load() }
})

const SALE = {
  id: 'rlz-1',
  position_id: 'pos-1',
  instrument_id: 'inst-1',
  quantity: 2,
  proceeds_per_unit: 12,
  proceeds_currency: 'SGD',
  disposal_fees: 0,
  disposed_at: '2026-03-13',
  method: 'fifo',
  is_taxable: true,
  proceeds_account_id: 'cash-1',
  transfer_to_account_id: null,
  txn_id: 'txn-deposit',
  consumptions: [],
} as unknown as RealizedEvent

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.includes('/lots'))
      return Promise.resolve({ ok: true, json: async () => ({ lots: [] }) })
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
  })
})
afterEach(cleanup)

// Choosing no account on a sale that has a deposit deletes that deposit and frees what it
// funded; the form said only that the cash "is not added anywhere" (UAT DS-SALE-NOACCT).
describe('DisposalForm — a sale with a deposit', () => {
  it('warns that choosing no account removes the deposit and what it funded', async () => {
    const { findByLabelText, findByText, queryByText } = render(
      <DisposalForm
        row={{ position_id: 'pos-1', symbol: 'ACME', label: 'ACME', currency: 'SGD' }}
        event={SALE}
        onDone={() => {}}
        onClose={() => {}}
      />,
    )
    const deposit = (await findByLabelText(/Deposit proceeds to/)) as HTMLSelectElement
    await waitFor(() => expect(deposit.value).toBe('cash-1'))
    expect(queryByText(/removes this sale's deposit/)).toBeNull()

    fireEvent.change(deposit, { target: { value: '' } })

    expect(await findByText(/removes this sale's deposit/)).toBeTruthy()
  })
})
