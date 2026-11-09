import { fAmountNative, fDate } from '../../../utils/format'
import { Skeleton, Stack } from '../../ui'
import { TYPE_LABELS, type AccountType } from '../accounts/categories'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import { Destination, LinkSummary, type LinkProps } from './ReviewLinks'
import { plural, type Preview, type PreviewTxn } from './runs'

// What a review shows of the plan itself: the line under its title, and its tabs' tables.

export function Meta({ preview: p }: { preview: Preview }) {
  return (
    <span className="ynr-meta">
      {[
        p.currency,
        plural(p.accounts.length, 'account'),
        plural(p.transactions, 'transaction'),
        plural(p.categories, 'category', 'categories'),
        plural(p.budget_months, 'budget month'),
      ].join(', ')}
    </span>
  )
}

export function Placeholder() {
  return (
    <div className="ynr-placeholder">
      <Stack gap="--space-4">
        {[92, 78, 85, 64, 88, 70].map((w) => (
          <Skeleton key={w} width={`${w}%`} height={12} />
        ))}
      </Stack>
    </div>
  )
}

function Empty({ children }: { children: string }) {
  return <p className="ynr-empty">{children}</p>
}

export function Accounts({ preview: p, ...link }: { preview: Preview } & LinkProps) {
  if (p.accounts.length === 0) return <Empty>{`This ${link.app.planWord} has no accounts.`}</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Account</th>
          <th>Type</th>
          <th>In this book</th>
          <th className="imp-table__num">Transactions</th>
          <th className="imp-table__num">Opening balance</th>
        </tr>
      </thead>
      {p.accounts.map((a) => (
        <tbody key={a.id ?? a.name}>
          <tr>
            <td data-label="Account">
              {a.name}
              {a.closed && <span className="ynr-dim"> (closed)</span>}
            </td>
            <td data-label="Type" className="ynr-dim">
              {TYPE_LABELS[a.type as AccountType] ?? a.type}
              {a.on_budget === false ? ' (tracking)' : ''}
            </td>
            <td data-label="In this book">
              <Destination account={a} {...link} />
            </td>
            <td data-label="Transactions" className="imp-table__num">
              {a.transactions}
            </td>
            <td data-label="Opening balance" className="imp-table__num">
              {a.link ? (
                <span className="ynr-dim">Its own</span>
              ) : (
                fAmountNative(a.opening_balance, p.currency)
              )}
            </td>
          </tr>
          {a.match && a.chosen && (
            <tr className="ynr-link">
              <td colSpan={5}>
                <LinkSummary account={a} match={a.match} currency={p.currency} {...link} />
              </td>
            </tr>
          )}
        </tbody>
      ))}
    </table>
  )
}

// A blank category on an inflow is money to assign, as in YNAB; on a spend it is uncategorized.
const NO_CATEGORY: Record<PreviewTxn['kind'], string> = {
  income: 'Ready to Assign',
  expense: 'Uncategorized',
  transfer: '',
}

export function Transactions({ app, preview: p }: { app: BudgetAppInfo; preview: Preview }) {
  const rows = p.transaction_list
  if (!rows) return <Empty>Review again to see the transactions.</Empty>
  if (rows.length === 0) return <Empty>{`This ${app.planWord} has no transactions.`}</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Account</th>
          <th>Payee</th>
          <th>Memo</th>
          <th>Category</th>
          <th className="imp-table__num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t, i) => (
          <tr key={i}>
            <td data-label="Date" className="ynr-dim ynr-nowrap">
              {fDate(t.date)}
            </td>
            <td data-label="Account">
              {t.kind === 'transfer' ? `${t.account ?? '?'} → ${t.to_account ?? '?'}` : t.account}
            </td>
            <td data-label="Payee" className="imp-table__wrap">
              {t.payee ?? ''}
            </td>
            <td data-label="Memo" className="imp-table__wrap ynr-dim">
              {t.memo ?? ''}
            </td>
            <td data-label="Category" className="ynr-dim">
              {t.category ?? NO_CATEGORY[t.kind]}
            </td>
            <td data-label="Amount" className="imp-table__num ynr-nowrap">
              {fAmountNative(t.amount, p.currency)}
            </td>
          </tr>
        ))}
      </tbody>
      {rows.length < p.transactions && (
        <tfoot>
          <tr>
            <td colSpan={6} className="ynr-dim">
              The newest {rows.length} of {p.transactions.toLocaleString()}. The import brings them
              all.
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}
