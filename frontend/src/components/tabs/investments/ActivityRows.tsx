// ActivityRows — the two presentations of the Activity ledger's rows, sharing one set of
// type metadata and formatters. ActivityTab owns the filters and swaps between them on
// useIsMobile: the five-column table for a wide layout, the two-line card list for a phone.

import { fCurrency, fDate, moneyClass } from '../../../utils/format'
import type { ActivityRow } from './useActivityView'

// ── Type metadata ─────────────────────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  buy: 'Buy',
  sell: 'Sell',
  dividend: 'Dividend',
  coupon: 'Coupon',
  interest: 'Interest',
  deposit: 'Deposit',
  withdrawal: 'Withdrawal',
  transfer: 'Transfer',
  vest: 'Vest',
  fee: 'Fee',
}

// Maps an activity type → pill variant class suffix (matches prototype ACT_PILL)
const TYPE_PILL: Record<string, string> = {
  buy: 'buy',
  sell: 'sell',
  dividend: 'income',
  coupon: 'income',
  interest: 'income',
  deposit: 'deposit',
  withdrawal: 'deposit',
  transfer: 'transfer',
  vest: 'vest',
  fee: 'fee',
}

// ── Formatting helpers ────────────────────────────────────────────────────────

function fmtDate(iso: string): string {
  return iso ? fDate(iso) : '—'
}

function fmtSigned(amount: number, currency: string): string {
  const abs = fCurrency(Math.abs(amount), currency)
  return (amount >= 0 ? '+' : '−') + abs
}

// A row the backend marks `signed: false` moved no cash: a vest's figure is the informational
// FMV, shown without a +/− (ACT-6); a transfer has no figure at all, so a dash rather than
// a misleading "+S$0.00" (owner, 2026-09-04: a custodian move that read as a $0 Sell).
function fmtAmount(r: ActivityRow, currency: string): string {
  if (r.signed) return fmtSigned(r.amount_base, currency)
  return r.amount_base === 0 ? '—' : fCurrency(Math.abs(r.amount_base), currency)
}

// Income and cash-in read green, fees red, vests and transfers muted (informational, no
// cash movement); trades are plain ink — a buy is a direction, not a loss.
function amountColor(type: string): string {
  if (['dividend', 'coupon', 'interest', 'deposit'].includes(type)) return 'var(--good)'
  if (type === 'fee') return 'var(--bad)'
  if (type === 'vest' || type === 'transfer') return 'var(--muted)'
  return 'var(--ink)'
}

function TypePill({ type }: { type: string }) {
  return (
    <span className={'act-pill ' + (TYPE_PILL[type] ?? 'sell')}>{TYPE_LABEL[type] ?? type}</span>
  )
}

// ── Row renderers ─────────────────────────────────────────────────────────────

const EMPTY_MSG = 'No activity in this range. Try a wider range or clear filters.'

interface RowsProps {
  rows: ActivityRow[]
  baseCurrency: string
}

/** Wide layout: the five-column ledger table (scrolls horizontally inside its card). */
export function ActivityRows({ rows, baseCurrency }: RowsProps) {
  return (
    <div style={{ overflowX: 'auto', paddingTop: 4 }}>
      <table className="act-tbl">
        <thead>
          <tr>
            <th>Date</th>
            <th>Type</th>
            <th>Details</th>
            <th>Account</th>
            <th className="num">Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.source_id}>
              <td className="act-date">{fmtDate(r.date)}</td>
              <td className="not-money">
                <TypePill type={r.type} />
              </td>
              <td className="not-money">
                {r.symbol && <span className="act-sym">{r.symbol} </span>}
                <span className={r.detail ? 'act-detail money' : 'act-detail'}>
                  {r.detail || r.name || ''}
                </span>
              </td>
              <td className="act-acct not-money">{r.account_name || '—'}</td>
              <td className={'num ' + moneyClass()} style={{ color: amountColor(r.type) }}>
                {fmtAmount(r, baseCurrency)}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td
                colSpan={5}
                style={{ textAlign: 'center', color: 'var(--faint)', padding: '28px' }}
              >
                {EMPTY_MSG}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

/** Phone layout: one two-line card per event, nothing to scroll sideways. Line 1 is what
 * happened (symbol + type pill) and how much; line 2 the trade detail and when/where. */
export function ActivityCards({ rows, baseCurrency }: RowsProps) {
  if (rows.length === 0) return <div className="act-cards-empty">{EMPTY_MSG}</div>
  return (
    <ul className="act-cards" aria-label="Activity">
      {rows.map((r) => {
        // A row with no symbol (a cash movement) leads with its name; the detail line then
        // stays free for the figure rather than repeating that name. With neither (an
        // unlinked vest) the pill alone leads — "Vest [Vest]" would say it twice.
        const title = r.symbol || r.name
        const sub = r.detail || (r.symbol ? r.name : '') || ''
        return (
          <li key={r.source_id} className="act-mcard">
            <div className="act-mcard-main">
              {title && <span className="act-sym">{title}</span>}
              <TypePill type={r.type} />
            </div>
            <span
              className={'act-mcard-amt ' + moneyClass()}
              style={{ color: amountColor(r.type) }}
            >
              {fmtAmount(r, baseCurrency)}
            </span>
            <span className={r.detail ? 'act-mcard-sub money' : 'act-mcard-sub'}>{sub}</span>
            <span className="act-mcard-meta">
              {fmtDate(r.date)} · {r.account_name || '—'}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
