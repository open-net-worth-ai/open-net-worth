// "+ Create account…" from a provider row seeds the form with the provider's suggested type
// AND its balance in the form's terms — a card's amount owed positive whichever way round the
// provider reports it, so a debt can never be prefilled as an asset.

import { expect, it } from 'vitest'
import { prefillFor } from './accountRows'

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
  expect(prefillFor(row, 'Plaid')).toEqual({
    type: 'cash',
    name: 'Plaid Silver Standard 0.1% Interest Saving',
    currency: 'USD',
    lastFour: '1111',
    institution: 'Plaid',
    initial: '210',
  })
})

it('a card balance is the amount owed, whichever sign the provider used', () => {
  const card = { ...row, account_type: 'credit' as const }
  expect(prefillFor({ ...card, balance: 410 }, 'Plaid').initial).toBe('410')
  expect(prefillFor({ ...card, balance: -410 }, 'Plaid').initial).toBe('410')
})

it('an overdrawn cash account keeps its sign; no balance leaves the field to the user', () => {
  expect(prefillFor({ ...row, balance: -25.5 }, 'Plaid').initial).toBe('-25.5')
  expect(prefillFor({ ...row, balance: null }, 'Plaid').initial).toBeUndefined()
})

it('no suggested type: no balance either, so a debt is never prefilled onto the Cash default', () => {
  const untyped = prefillFor({ ...row, account_type: null, balance: 410 }, 'Plaid')
  expect(untyped.type).toBeUndefined()
  expect(untyped.initial).toBeUndefined()
})
