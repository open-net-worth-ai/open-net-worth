// @vitest-environment jsdom
//
// One Plaid connector, several banks: step ② lists each linked bank by name with its own
// Remove (after a confirm), and offers "Link another bank" once one is linked.

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { PlaidBanksStep } from './PlaidBanksStep'
import type { PlaidLink } from './usePlaidLink'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
})

const idle: PlaidLink = {
  starting: false,
  waiting: false,
  checking: false,
  justLinked: false,
  renewed: null,
  renewedKind: 'reconnect',
  error: null,
  setError: () => {},
  linkCount: 0,
  start: async () => {},
  checkNow: () => {},
}

const BANKS = [
  { key: 'k1', institution_name: 'Wells Fargo', linked_at: '2026-10-20T00:00:00Z' },
  { key: 'k2', institution_name: 'Chase', linked_at: '2026-10-29T00:00:00Z' },
]

function step(banks = BANKS, onChanged = () => {}) {
  return render(
    <PlaidBanksStep
      banks={banks}
      state="done"
      link={idle}
      keysEntered
      saving={false}
      onLink={() => {}}
      onChanged={onChanged}
    />,
  )
}

it('lists every linked bank and offers to link another', () => {
  step()
  const list = screen.getByRole('list', { name: 'Linked banks' })
  expect(within(list).getByText('Wells Fargo')).toBeTruthy()
  expect(within(list).getByText('Chase')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Link another bank' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Step 2: Link your banks' })).toBeTruthy()
})

it('with no bank yet, the one action is Link bank', () => {
  step([])
  expect(screen.queryByRole('list', { name: 'Linked banks' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Link bank' })).toBeTruthy()
})

it('removes only the chosen bank, after a confirm', async () => {
  mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({ removed: true }) } as Response)
  const onChanged = vi.fn()
  step(BANKS, onChanged)
  fireEvent.click(screen.getByRole('button', { name: 'Remove Wells Fargo' }))
  expect(await screen.findByText('Remove Wells Fargo?')).toBeTruthy()
  expect(mockAuthFetch).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
  await waitFor(() =>
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/plaid/banks/k1/remove', { method: 'POST' }),
  )
  await waitFor(() => expect(onChanged).toHaveBeenCalled())
})

it('says when the last fetch could not reach every bank', () => {
  render(
    <PlaidBanksStep
      banks={BANKS}
      caution="Wells Fargo: Plaid /transactions/get failed: HTTP 400 — login"
      state="done"
      link={idle}
      keysEntered
      saving={false}
      onLink={() => {}}
      onChanged={() => {}}
    />,
  )
  expect(screen.getByText(/couldn't reach every bank — Wells Fargo/)).toBeTruthy()
})

it('a bank whose sign-in expired offers Reconnect, which renews THAT bank in place', () => {
  const start = vi.fn(async () => {})
  render(
    <PlaidBanksStep
      banks={[{ ...BANKS[0], needs_reconnect: true }, BANKS[1]]}
      state="done"
      link={{ ...idle, start }}
      keysEntered
      saving={false}
      onLink={() => {}}
      onChanged={() => {}}
    />,
  )
  expect(screen.getByText(/Sign-in expired — nothing syncs until you reconnect/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
  expect(start).toHaveBeenCalledWith({ bank: 'k1', addAccounts: false })
  // The healthy bank offers Add accounts instead — the account picker, same bank.
  fireEvent.click(screen.getByRole('button', { name: 'Add accounts at Chase' }))
  expect(start).toHaveBeenLastCalledWith({ bank: 'k2', addAccounts: true })
})

it('after a reconnect, says the accounts and links are unchanged', () => {
  render(
    <PlaidBanksStep
      banks={BANKS}
      state="done"
      link={{ ...idle, justLinked: true, renewed: 'Wells Fargo' }}
      keysEntered
      saving={false}
      onLink={() => {}}
      onChanged={() => {}}
    />,
  )
  expect(
    screen.getByText('Wells Fargo reconnected — its accounts and links are unchanged.'),
  ).toBeTruthy()
})
