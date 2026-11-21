// @vitest-environment jsdom
//
// A card the bank says is IN CREDIT holds its balance: the list must not call it "owed"
// (the server works that out, `in_credit`, by the rule the balance check reads).
import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ConnectorAccountRow } from './ConnectorAccountRow'
import type { ProviderAccountRow } from './accountRows'

afterEach(cleanup)

const card: ProviderAccountRow = {
  provider_account_id: 'acme-card',
  name: 'ACME Card',
  mask: '4242',
  currency: 'EUR',
  balance: 50,
  cleared_balance: 50,
  account_type: 'credit',
  linked_account_id: null,
}

function show(row: ProviderAccountRow) {
  render(
    <ConnectorAccountRow
      row={row}
      linkedName=""
      accounts={[]}
      busy={false}
      onAdd={() => {}}
      onLink={() => {}}
      onUnlink={() => {}}
    />,
  )
}

it('a card in credit reads "in credit", never "owed"', () => {
  show({ ...card, in_credit: true })
  expect(screen.getByText('in credit')).toBeTruthy()
  expect(screen.queryByText('owed')).toBeNull()
})

it('a card that owes still reads "owed"', () => {
  show({ ...card, in_credit: false })
  expect(screen.getByText('owed')).toBeTruthy()
  expect(screen.queryByText('in credit')).toBeNull()
})
