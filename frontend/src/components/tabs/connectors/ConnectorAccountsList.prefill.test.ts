// "+ Create account…" from a provider row seeds the form with the provider's suggested type
// AND its balance in the form's terms — a card's amount owed positive whichever way round the
// provider reports it, so a debt can never be prefilled as an asset.

import { expect, it } from 'vitest'
import { bankInstitution, prefillFor } from './accountRows'

const row = {
  provider_account_id: 'p1',
  name: 'Plaid Silver Standard 0.1% Interest Saving',
  mask: '1111',
  currency: 'USD',
  balance: 210,
  account_type: 'cash' as const,
  linked_account_id: null,
}

it('carries the balance and the suggested type', () => {
  expect(prefillFor(row)).toEqual({
    type: 'cash',
    name: 'Plaid Silver Standard 0.1% Interest Saving',
    currency: 'USD',
    lastFour: '1111',
    institution: undefined, // no bank known for this row — blank, never "Plaid"
    initial: '210',
  })
})

it('a card balance is the amount owed, whichever sign the provider used', () => {
  const card = { ...row, account_type: 'credit' as const }
  expect(prefillFor({ ...card, balance: 410 }).initial).toBe('410')
  expect(prefillFor({ ...card, balance: -410 }).initial).toBe('410')
})

it('an overdrawn cash account keeps its sign; no balance leaves the field to the user', () => {
  expect(prefillFor({ ...row, balance: -25.5 }).initial).toBe('-25.5')
  expect(prefillFor({ ...row, balance: null }).initial).toBeUndefined()
})

it('no suggested type: no balance either, so a debt is never prefilled onto the Cash default', () => {
  const untyped = prefillFor({ ...row, account_type: null, balance: 410 })
  expect(untyped.type).toBeUndefined()
  expect(untyped.initial).toBeUndefined()
})

// ── new accounts from a bank join the group its other accounts are already in ───────────

const at = (id: string, linked: string | null, institution = 'Chase') => ({
  ...row,
  provider_account_id: id,
  institution,
  linked_account_id: linked,
})

it('reuses the institution the bank’s linked accounts are under, even after a rename', () => {
  const rows = [at('p1', 'a1'), at('p2', null)]
  const accounts = [{ id: 'a1', name: 'Checking', institution: 'Joint Chase' }]
  expect(bankInstitution(rows[1], rows, accounts, 'Plaid')).toBe('Joint Chase')
  expect(prefillFor(rows[1], 'Joint Chase').institution).toBe('Joint Chase')
})

it('takes the most common when the bank’s accounts are split, and ignores the placeholder', () => {
  const rows = [at('p1', 'a1'), at('p2', 'a2'), at('p3', 'a3'), at('p4', 'a4'), at('p5', null)]
  const accounts = [
    { id: 'a1', name: '1', institution: 'Joint Chase' },
    { id: 'a2', name: '2', institution: 'Joint Chase' },
    { id: 'a3', name: '3', institution: 'Chase' },
    { id: 'a4', name: '4', institution: 'Plaid' },
  ]
  expect(bankInstitution(rows[4], rows, accounts, 'Plaid')).toBe('Joint Chase')
})

it('a bank with nothing linked yet falls back to its own name; other banks never count', () => {
  const rows = [at('p1', 'a1', 'Wells Fargo'), at('p2', null, 'Chase')]
  const accounts = [{ id: 'a1', name: 'WF', institution: 'My WF' }]
  expect(bankInstitution(rows[1], rows, accounts, 'Plaid')).toBeUndefined()
  expect(prefillFor(rows[1]).institution).toBe('Chase')
})

it('a bank with no known name leaves the institution blank — never the connector "Plaid"', () => {
  expect(prefillFor({ ...row, institution: null }).institution).toBeUndefined()
  expect(prefillFor({ ...row, institution: '' }).institution).toBeUndefined()
})
