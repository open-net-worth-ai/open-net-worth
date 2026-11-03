// @vitest-environment jsdom
//
// Accounts created while their bank had no name were filed under "Plaid". Once the bank is
// named, the list offers to file them under it — the user's click, through the ordinary account
// edit — and never touches an institution the user chose themselves.

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FileUnderBankFix } from './FileUnderBankFix'
import { misfiled, type ProviderAccountRow } from './accountRows'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
})

const row = (id: string, linked: string | null, institution = 'Chase'): ProviderAccountRow => ({
  provider_account_id: id,
  name: id,
  mask: '0000',
  currency: 'USD',
  balance: 1,
  account_type: 'cash',
  institution,
  linked_account_id: linked,
})

const ACCOUNTS = [
  { id: 'a1', name: 'Everyday Checking', institution: 'Plaid' },
  { id: 'a2', name: 'Savings', institution: '' },
  { id: 'a3', name: 'Mine', institution: 'My Credit Union' }, // the user's own choice
  { id: 'a4', name: 'Already right', institution: 'Chase' },
]

it('finds only accounts under the placeholder or blank, grouped by their bank', () => {
  const rows = [row('p1', 'a1'), row('p2', 'a2'), row('p3', 'a3'), row('p4', 'a4'), row('p5', null)]
  const found = misfiled(rows, ACCOUNTS, 'Plaid')
  expect([...found.keys()]).toEqual(['Chase'])
  expect(found.get('Chase')!.map((a) => a.id)).toEqual(['a1', 'a2'])
})

it('files them under the bank with one click, then refreshes', async () => {
  mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) } as Response)
  const onFixed = vi.fn()
  render(
    <FileUnderBankFix
      rows={[row('p1', 'a1'), row('p2', 'a2')]}
      accounts={ACCOUNTS}
      providerLabel="Plaid"
      onFixed={onFixed}
    />,
  )
  expect(screen.getByText('2 accounts are filed under Plaid instead of Chase.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'File them under Chase' }))
  await waitFor(() => expect(onFixed).toHaveBeenCalled())
  for (const id of ['a1', 'a2'])
    expect(mockAuthFetch).toHaveBeenCalledWith(`/api/accounts/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ institution: 'Chase' }),
    })
})

it('says nothing when every linked account is already under its bank', () => {
  const { container } = render(
    <FileUnderBankFix
      rows={[row('p4', 'a4')]}
      accounts={ACCOUNTS}
      providerLabel="Plaid"
      onFixed={() => {}}
    />,
  )
  expect(container.textContent).toBe('')
})
