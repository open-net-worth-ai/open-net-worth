import { fAmountNative, toBase } from '../../utils/format'

/** A record's linked cash row, as `GET /api/transactions/:id` returns it. */
export interface LotCashRow {
  kind?: string | null
  from_account_id?: string | null
  from_amount?: number | null
  from_currency?: string | null
  to_account_id?: string | null
  to_amount?: number | null
  to_currency?: string | null
}

export interface FundingAccount {
  name: string
  currency?: string
  initial_balance_date?: string | null
}

/** One leg of the linked row: which account it sits on and what it holds there. */
export interface CashLeg {
  accountId?: string | null
  amount?: number | null
  currency?: string | null
}

const money = (n: number, ccy: string) => `${fAmountNative(n, ccy)} ${ccy}`

/** What moves in or out of the account, said in the account's own currency. A trade in another
 *  currency is converted when its row is booked: a new row at today's rate, a corrected one on
 *  the same account at the rate it was first booked at (services/lots._edit_cash_row). A USD
 *  lot paid from SGD said "201.00 will be deducted" while S$271 left (hand UAT, 2026-11-10). */
export function amountInAccount(p: {
  account: FundingAccount | undefined
  accountId: string
  amount: number
  ccy: string
  leg: CashLeg | null
  savedAmount: number | null
  fx: Record<string, number>
}): string {
  const ccy = (p.ccy || '').toUpperCase()
  const acctCcy = (p.account?.currency || '').toUpperCase()
  if (!acctCcy || !ccy || acctCcy === ccy) return fAmountNative(p.amount, ccy)
  const leg = p.leg
  if (
    leg?.accountId === p.accountId &&
    (leg.currency || '').toUpperCase() === acctCcy &&
    leg.amount != null &&
    p.savedAmount
  ) {
    return `${money((leg.amount * p.amount) / p.savedAmount, acctCcy)} (${money(p.amount, ccy)})`
  }
  if (p.fx[ccy] && p.fx[acctCcy]) {
    const converted = toBase(p.amount, ccy, p.fx, acctCcy)
    return `About ${money(converted, acctCcy)} (${money(p.amount, ccy)} at today's rate)`
  }
  return `${money(p.amount, ccy)}, converted to ${acctCcy} at today's rate,`
}

/** What the Remove-lot confirmation adds about the lot's cash (UAT DS-LOT-REMOVE). */
export function removeLotCashNote(txnId: string | null | undefined, cashRow: LotCashRow | null) {
  if (!txnId) return ''
  if (cashRow?.kind === 'transfer') return ' Its cash is a transfer in your ledger, which stays.'
  return ' Its purchase transaction is removed too, and anything it paid, such as an installment, goes back to unpaid.'
}
