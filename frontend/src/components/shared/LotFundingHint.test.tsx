// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { LotFundingHint } from './LotFundingHint'
import { amountInAccount, removeLotCashNote } from './cashHints'

afterEach(cleanup)

const SGD_CASH = { name: 'Everyday Cash', currency: 'SGD' }
const FX = { SGD: 1, USD: 1.35 }

// A USD lot paid from an SGD account said "201.00 will be deducted", but about S$271 left the
// account (hand UAT, 2026-11-10). The figure is now in the account's own currency.
describe('amountInAccount', () => {
  const base = { account: SGD_CASH, accountId: 'cash', amount: 201, ccy: 'USD', fx: FX }

  it('keeps the plain figure when the account is in the trade currency', () => {
    expect(
      amountInAccount({
        ...base,
        account: { name: 'USD cash', currency: 'USD' },
        leg: null,
        savedAmount: null,
      }),
    ).toBe('201.00')
  })

  it("uses the linked row's own rate on the same account", () => {
    const leg = { accountId: 'cash', amount: 271.35, currency: 'SGD' }
    expect(amountInAccount({ ...base, amount: 202, leg, savedAmount: 201 })).toBe(
      '272.70 SGD (202.00 USD)',
    )
  })

  it("says today's rate for a new row or another account", () => {
    const leg = { accountId: 'other', amount: 271.35, currency: 'SGD' }
    expect(amountInAccount({ ...base, leg, savedAmount: 201 })).toBe(
      "About 271.35 SGD (201.00 USD at today's rate)",
    )
  })

  it('names the conversion when there is no rate to show a figure', () => {
    expect(amountInAccount({ ...base, fx: { SGD: 1 }, leg: null, savedAmount: null })).toBe(
      "201.00 USD, converted to SGD at today's rate,",
    )
  })
})

describe('LotFundingHint', () => {
  const props = {
    account: SGD_CASH,
    accountId: 'cash',
    gross: 201,
    costCcy: 'USD',
    acquiredAt: '2026-03-02',
    cashRow: null,
    savedGross: 201,
    removing: false,
    fx: FX,
  }

  // Corrections leave a purchase row the user turned into a transfer alone, so the form must
  // not promise a deduction for it (hand UAT, 2026-11-10).
  it('promises no deduction for a row that is now a transfer', () => {
    const { container } = render(
      <LotFundingHint {...props} cashRow={{ kind: 'transfer', from_account_id: 'cash' }} />,
    )
    expect(container.textContent).toMatch(/is a transfer in your ledger/)
    expect(container.textContent).not.toMatch(/will be deducted/)
  })

  it('says the transfer stays even when no account is chosen', () => {
    const { container } = render(
      <LotFundingHint {...props} accountId="" removing cashRow={{ kind: 'transfer' }} />,
    )
    expect(container.textContent).toMatch(/leaves that transfer as it is/)
    expect(container.textContent).not.toMatch(/removes this lot's purchase/)
  })

  it('states the deduction in the account currency', () => {
    const { container } = render(<LotFundingHint {...props} />)
    expect(container.textContent).toBe(
      "About 271.35 SGD (201.00 USD at today's rate) will be deducted from Everyday Cash.",
    )
  })
})

describe('removeLotCashNote', () => {
  it('says a transfer stays when the lot is removed', () => {
    expect(removeLotCashNote('txn-1', { kind: 'transfer' })).toMatch(/transfer .* which stays/)
  })

  it('says the purchase row goes with an ordinary lot', () => {
    expect(removeLotCashNote('txn-1', { kind: 'investment' })).toMatch(/removed too/)
  })

  it('adds nothing for a lot with no cash row', () => {
    expect(removeLotCashNote(null, null)).toBe('')
  })
})
