import { fDate } from '../../utils/format'
import { amountInAccount, type FundingAccount, type LotCashRow } from './cashHints'

interface LotFundingHintProps {
  /** The chosen "Deduct from" account; undefined while accounts load. */
  account: FundingAccount | undefined
  /** '' for "Don't deduct". */
  accountId: string
  /** Quantity × unit cost + fees, in the lot's currency. */
  gross: number
  costCcy: string
  acquiredAt: string
  /** The row the lot is linked to now, once it has loaded. */
  cashRow: LotCashRow | null
  /** The lot's gross as saved, which the linked row was booked for. */
  savedGross: number | null
  /** The user changed the account on a lot that has a purchase row. */
  removing: boolean
  fx: Record<string, number>
}

/** The line under the lot form's "Deduct from" select: what saving does to the account's cash. */
export function LotFundingHint(props: LotFundingHintProps) {
  const { account, gross, acquiredAt, cashRow, removing } = props
  // Corrections leave a transfer the user made of the purchase alone, and so does
  // "Don't deduct": the lot just stops pointing at it (services/lots._release_cash_row).
  if (cashRow?.kind === 'transfer') {
    return (
      <span className="disposal-deposit-hint muted">
        This lot's cash is a transfer in your ledger. Saving the lot leaves that transfer as it is.
      </span>
    )
  }
  if (!props.accountId) {
    return removing ? (
      <span className="disposal-warn">
        Saving removes this lot's purchase transaction from the ledger. Anything it paid, such as an
        installment, goes back to unpaid.
      </span>
    ) : (
      <span className="disposal-deposit-hint muted">
        Funding not linked — your cash balance won't change when you add this lot.
      </span>
    )
  }
  if (!account || !(gross > 0)) return null
  const amount = amountInAccount({
    account,
    accountId: props.accountId,
    amount: gross,
    ccy: props.costCcy,
    leg: cashRow
      ? {
          accountId: cashRow.from_account_id,
          amount: cashRow.from_amount,
          currency: cashRow.from_currency,
        }
      : null,
    savedAmount: props.savedGross,
    fx: props.fx,
  })
  const start = account.initial_balance_date || ''
  if (start && acquiredAt && acquiredAt < start) {
    return (
      <span className="disposal-deposit-hint muted">
        {`${amount} is recorded against ${account.name}, but this date is before its starting balance (${fDate(start, 'short', 'always')}), so its balance won't change. Move the starting balance earlier to count it.`}
      </span>
    )
  }
  return (
    <span className="disposal-deposit-hint muted">{`${amount} will be deducted from ${account.name}.`}</span>
  )
}
