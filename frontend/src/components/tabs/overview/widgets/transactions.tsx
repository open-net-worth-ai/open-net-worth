// Overview v2 — Transactions widget group: recently added (by insertion time), recent (by
// txn date), and category transactions (recent activity in the categories the user picks).
// Each is a thin read over the ledger, rendered with the same compact row list used by the
// "Next 30 days" widget (`.ov-upcoming*` classes, reused rather than forked).

import type { ComponentType } from 'react'
import { fBase, fDate } from '../../../../utils/format'
import { Money, Skeleton, Text } from '../../../ui'
import { useDataVersion } from '../../../../hooks/useDataVersion'
import { useQueryCache } from '../../../../state/queryCache'
import { useCategories } from '../../../../hooks/serverState'
import { fetchJson } from '../../../../hooks/serverState/fetchJson'
import { fromApiTxn } from '../../accounts/data'
import type { ApiTxn, Txn } from '../../accounts/types'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder, WidgetSkeleton } from '../WidgetPlaceholder'
import { resolveCategoryIds } from '../categoryRefs'
import { rowCap } from '../widgetDisplayShared'

// Which way the money moved, from the legs — never the kind: an investment buy draws from
// its funding account (out) and sale proceeds land in one (in), but neither is an "expense".
type Flow = 'in' | 'out' | 'move'

function flowOf(t: Txn): Flow {
  if (t.fromAccountId && t.toAccountId) return 'move'
  return t.fromAccountId ? 'out' : 'in'
}

const FLOW_COLOR: Record<Flow, string> = {
  in: 'var(--good)',
  out: 'var(--bad)',
  move: 'var(--faint)',
}

function signedAmount(t: Txn, flow: Flow): number {
  const amt = Math.abs(t.baseAmount ?? 0)
  return flow === 'out' ? -amt : amt
}

function fmtQty(q: number): string {
  return q.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

interface RowView {
  label: string
  amount: number
  color: string
}

/** How one row reads. A transfer is money moving between the user's own accounts — named by
 *  its two accounts, never red or green, never signed. An investment cash leg is a buy or a
 *  sell, not spending or income — "Buy 2.1 VOO", its amount in the investment purple for both
 *  signs, the same as the register (accounts.css `.acct-td-amt.invest`). */
function rowView(t: Txn): RowView {
  const flow = flowOf(t)
  const named = t.payeeName || t.description
  if (t.kind === 'transfer' || flow === 'move') {
    const from = t.fromAccountName
    const to = t.toAccountName
    return {
      label: from && to ? `${from} → ${to}` : named || 'Transfer',
      amount: Math.abs(t.baseAmount ?? 0),
      color: 'var(--muted)',
    }
  }
  if (t.kind === 'investment') {
    const verb = flow === 'out' ? 'Buy' : 'Sell'
    const traded = t.ticker ? [t.quantity ? fmtQty(Math.abs(t.quantity)) : '', t.ticker] : []
    const label = traded.length
      ? [verb, ...traded].filter(Boolean).join(' ')
      : `${verb} · ${named || 'investment'}`
    return { label, amount: signedAmount(t, flow), color: 'var(--violet)' }
  }
  return { label: named || 'Unlabeled', amount: signedAmount(t, flow), color: FLOW_COLOR[flow] }
}

async function fetchTxnPage(path: string): Promise<{ transactions: ApiTxn[] }> {
  return fetchJson<{ transactions: ApiTxn[] }>(path)
}

function NavFooter({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className="ov-panel-link" onClick={onClick}>
      {label}
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    </button>
  )
}

function TxnListBody({
  txns,
  dateLabel,
}: {
  txns: Txn[]
  /** 'date' lists the txn's own date; 'created' lists the same date and, when the row was entered
   *  on another day (an import of last month's purchases), says when it was added. */
  dateLabel: 'date' | 'created'
}) {
  return (
    <div className="ov-upcoming">
      {txns.map((t) => {
        // The row carries its payee's name; the bank description is only for payee-less rows.
        const row = rowView(t)
        const added =
          dateLabel === 'created' && t.createdAt.slice(0, 10) !== t.date.slice(0, 10)
            ? fDate(t.createdAt)
            : null
        return (
          <div key={t.id} className="ov-upcoming-row">
            <span className="ov-upcoming-date">{fDate(t.date)}</span>
            <span className="ov-upcoming-label">
              {row.label}
              {added && <span className="ov-t-meta"> · added {added}</span>}
            </span>
            <span className="ov-upcoming-amt" style={{ color: row.color }}>
              <Money as="span">{fBase(row.amount)}</Money>
            </span>
          </div>
        )
      })}
    </div>
  )
}

const RecentlyAdded: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const version = useDataVersion(['ledger'])
  // All rows: limit=0 asks the server for every one.
  const cap = rowCap(instance, def)
  const key = `/api/transactions/recently-added?limit=${cap ?? 0}&exclude_openings=true`
  const { data, initialLoading } = useQueryCache(key, () => fetchTxnPage(key), {
    staleTime: 30_000,
    version,
  })
  const txns = (data?.transactions ?? []).map(fromApiTxn)
  const footer = onNavigate && (
    <NavFooter label="Open register" onClick={() => onNavigate('accounts')} />
  )
  if (initialLoading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={4} />
      </WidgetShell>
    )
  }
  if (!txns.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <Text as="p" size="sm" tone="faint">
          No transactions yet.
        </Text>
      </WidgetShell>
    )
  }
  return (
    <WidgetShell title={instance.title} base={def.title} footer={footer}>
      <TxnListBody txns={txns} dateLabel="created" />
    </WidgetShell>
  )
}

const RecentTransactions: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const version = useDataVersion(['ledger'])
  // All rows: no limit at all.
  const cap = rowCap(instance, def)
  const key = cap == null ? '/api/transactions' : `/api/transactions?limit=${cap}`
  const { data, initialLoading } = useQueryCache(key, () => fetchTxnPage(key), {
    staleTime: 30_000,
    version,
  })
  const txns = (data?.transactions ?? []).map(fromApiTxn)
  const footer = onNavigate && (
    <NavFooter label="Open register" onClick={() => onNavigate('accounts')} />
  )
  if (initialLoading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetSkeleton rows={4} />
      </WidgetShell>
    )
  }
  if (!txns.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <Text as="p" size="sm" tone="faint">
          No transactions yet.
        </Text>
      </WidgetShell>
    )
  }
  return (
    <WidgetShell title={instance.title} base={def.title} footer={footer}>
      <TxnListBody txns={txns} dateLabel="date" />
    </WidgetShell>
  )
}

function configuredCategories(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((c): c is string => typeof c === 'string' && !!c) : []
}

// Recent transactions in the categories this instance is set to (by category id). Backs both
// "Category transactions" (no default) and "Subscriptions" (preset to the starter category).
const CategoryTransactions: ComponentType<WidgetContentProps> = ({
  instance,
  def,
  onNavigate,
  onOpenRegister,
}) => {
  const version = useDataVersion(['ledger'])
  const { data: categories } = useCategories()
  const fallbackLabel = def.settings?.find((s) => s.key === 'categories')?.fallbackLabel
  const categoryIds = Array.isArray(categories)
    ? resolveCategoryIds(
        configuredCategories(instance.config?.categories),
        categories,
        fallbackLabel,
      )
    : []
  const cap = rowCap(instance, def)
  const params = new URLSearchParams(categoryIds.map((c) => ['category', c]))
  if (cap != null) params.set('limit', String(cap))
  const key = `/api/transactions?${params.toString()}`
  const { data } = useQueryCache(key, () => fetchTxnPage(key), {
    staleTime: 30_000,
    version,
    enabled: categoryIds.length > 0,
  })
  const txns = (data?.transactions ?? []).map(fromApiTxn)
  // "Open register" shows the same transactions this card lists — its categories, not all.
  const openRegister =
    onOpenRegister && categoryIds.length
      ? () => onOpenRegister({ category: categoryIds })
      : onNavigate && (() => onNavigate('accounts'))
  const footer = openRegister && <NavFooter label="Open register" onClick={openRegister} />
  if (!Array.isArray(categories)) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <Skeleton height={48} />
      </WidgetShell>
    )
  }
  if (!categoryIds.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <WidgetPlaceholder detail={<>Pick the categories to list in this widget's settings.</>} />
      </WidgetShell>
    )
  }
  if (!txns.length) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <WidgetPlaceholder detail={<>No transactions in these categories yet.</>} />
      </WidgetShell>
    )
  }
  return (
    <WidgetShell title={instance.title} base={def.title} footer={footer}>
      <TxnListBody txns={txns} dateLabel="date" />
    </WidgetShell>
  )
}

export const transactionsWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  'recent-added-txns': RecentlyAdded,
  'recent-txns': RecentTransactions,
  'category-txns': CategoryTransactions,
  subscriptions: CategoryTransactions,
}
