import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { fAmountNative, fDate } from '../../../utils/format'
import { Button, Cluster, Modal, PageTabs, Select, Skeleton, Stack, Text } from '../../ui'
import { messageOf, useRun, type Preview, type PreviewTxn } from './runs'
import type { SyncDirection } from './sync'

// The review before a YNAB import, in one window: a line saying what the plan holds, what it
// changes in this book, then the plan itself in three tabs (accounts, categories, transactions)
// over a pane of fixed height, so neither switching tabs nor the read finishing resizes it. It
// is bound to ONE preview run; the import it starts turns the sync on itself when it succeeds
// (the server does it, so closing the page mid-import loses nothing).

type SyncChoice = SyncDirection | 'off'
type Pane = 'accounts' | 'categories' | 'transactions'

const SYNC_OPTIONS: { value: SyncChoice; label: string }[] = [
  { value: 'both', label: 'Keep in sync both ways' },
  { value: 'from_ynab', label: 'Sync from YNAB only' },
  { value: 'to_ynab', label: 'Sync to YNAB only' },
  { value: 'off', label: 'Don’t sync, import once' },
]

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`

export function YnabReview({
  runId,
  planLabel,
  onClose,
  onStarted,
}: {
  runId: string
  /** "Name (CUR)" from the picker, so the window is titled before the plan is read. */
  planLabel: string
  onClose: () => void
  onStarted: () => void
}) {
  const run = useRun(runId).data
  const p = run?.status === 'done' ? run.preview : null
  const reading = !run || run.status === 'running'
  const [pane, setPane] = useState<Pane>('accounts')
  const [sync, setSync] = useState<SyncChoice>('both')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function importNow() {
    if (!run || !p || p.currency_blocked) return
    setBusy(true)
    setError(null)
    try {
      const r = await authFetch('/api/ynab/migration/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The note above the tabs says the plan goes in beside the book's own accounts;
        // importing after reading it is the confirmation.
        body: JSON.stringify({
          plan_id: run.plan_id,
          confirm_nonempty: p.book_state === 'foreign',
          sync_direction: sync === 'off' ? null : sync,
        }),
      })
      if (!r.ok) {
        setError(messageOf(await r.json().catch(() => null), 'Could not start the import.'))
        return
      }
      onStarted()
      onClose()
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  const failed = !reading && !p
  return (
    <Modal
      open
      size="xl"
      onClose={onClose}
      title={`Import ${p ? p.plan_name : planLabel}`}
      subtitle={reading ? 'Reading it from YNAB…' : p ? <Meta preview={p} /> : undefined}
      footer={
        <Cluster justify="between" gap="--space-3" className="ynr-footer">
          <Select
            className="ynr-sync"
            aria-label="After import"
            value={sync}
            options={SYNC_OPTIONS}
            onChange={(e) => setSync(e.target.value as SyncChoice)}
          />
          <Cluster gap="--space-2">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!p || p.currency_blocked}
              loading={busy}
              onClick={importNow}
            >
              Import
            </Button>
          </Cluster>
        </Cluster>
      }
    >
      <Stack gap="--space-4">
        {failed ? (
          <Text size="sm" tone="bad">
            {run?.error || 'The plan could not be read.'}
          </Text>
        ) : (
          p && <Notes preview={p} />
        )}
        {error && (
          <Text size="sm" tone="bad">
            {error}
          </Text>
        )}
        {!failed && (
          <div>
            <PageTabs
              ariaLabel="What the plan holds"
              value={pane}
              onChange={setPane}
              tabs={[
                { key: 'accounts', label: 'Accounts', badge: p?.accounts.length },
                { key: 'categories', label: 'Categories', badge: p?.categories },
                { key: 'transactions', label: 'Transactions', badge: p?.transactions },
              ]}
            />
            <div
              className="ynr-pane"
              role="tabpanel"
              aria-labelledby={`ui-page-tab-${pane}`}
              aria-busy={reading}
            >
              {!p ? (
                <Placeholder />
              ) : pane === 'accounts' ? (
                <Accounts preview={p} />
              ) : pane === 'categories' ? (
                <Categories preview={p} />
              ) : (
                <Transactions preview={p} />
              )}
            </div>
          </div>
        )}
      </Stack>
    </Modal>
  )
}

function Meta({ preview: p }: { preview: Preview }) {
  return (
    <span className="ynr-meta">
      {[
        p.currency,
        plural(p.accounts.length, 'account'),
        plural(p.transactions, 'transaction'),
        plural(p.categories, 'category', 'categories'),
        plural(p.budget_months, 'budget month'),
      ].join(' · ')}
    </span>
  )
}

// What the import does to this book beyond adding the plan — each one sentence, only when true.
function Notes({ preview: p }: { preview: Preview }) {
  const lines: { text: string; bad?: boolean }[] = []
  if (p.currency_blocked)
    lines.push({
      bad: true,
      text: `Can’t import: this book is in ${p.base_currency} and already has data, and the plan is in ${p.currency}. Import into an empty book, or one in ${p.currency}.`,
    })
  if (p.book_state === 'foreign' && !p.currency_blocked)
    lines.push({
      text: 'This book already has accounts that didn’t come from YNAB. They stay as they are, and the plan is added next to them.',
    })
  if (p.book_state === 'rerun')
    lines.push({ text: 'This plan was imported before; importing again adds only what is new.' })
  if (p.currency_change)
    lines.push({
      text: `The book’s base currency changes from ${p.base_currency} to ${p.currency}.`,
    })
  const pruned = [...p.prune.categories, ...p.prune.groups]
  if (pruned.length > 0)
    lines.push({ text: `Unused starter items are removed: ${pruned.join(', ')}.` })
  for (const w of p.warnings) lines.push({ text: w })
  if (lines.length === 0) return null
  return (
    <ul className="ynr-notes">
      {lines.map((l) => (
        <li key={l.text} className={l.bad ? 'ynr-note ynr-note--bad' : 'ynr-note'}>
          {l.text}
        </li>
      ))}
    </ul>
  )
}

function Placeholder() {
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

function Accounts({ preview: p }: { preview: Preview }) {
  if (p.accounts.length === 0) return <Empty>This plan has no accounts.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Account</th>
          <th>Type</th>
          <th className="imp-table__num">Transactions</th>
          <th className="imp-table__num">Opening balance</th>
        </tr>
      </thead>
      <tbody>
        {p.accounts.map((a) => (
          <tr key={a.name}>
            <td data-label="Account">
              {a.name}
              {a.closed && <span className="ynr-dim"> · closed</span>}
            </td>
            <td data-label="Type" className="ynr-dim">
              {a.type}
              {a.on_budget === false ? ' · tracking' : ''}
            </td>
            <td data-label="Transactions" className="imp-table__num">
              {a.transactions}
            </td>
            <td data-label="Opening balance" className="imp-table__num">
              {fAmountNative(a.opening_balance, p.currency)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Categories({ preview: p }: { preview: Preview }) {
  const groups = p.category_groups
  if (!groups) return <Empty>Review again to see the categories by name.</Empty>
  if (groups.length === 0) return <Empty>This plan has no categories of its own.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Category</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.name}>
          <tr className="ynr-group">
            <th scope="rowgroup">
              {g.name}
              <span className="ynr-dim"> · {g.categories.length}</span>
            </th>
          </tr>
          {g.categories.map((c) => (
            <tr key={c}>
              <td data-label="Category">{c}</td>
            </tr>
          ))}
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

function Transactions({ preview: p }: { preview: Preview }) {
  const rows = p.transaction_list
  if (!rows) return <Empty>Review again to see the transactions.</Empty>
  if (rows.length === 0) return <Empty>This plan has no transactions.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Account</th>
          <th>Payee</th>
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
              {t.memo && <span className="ynr-dim"> · {t.memo}</span>}
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
            <td colSpan={5} className="ynr-dim">
              The newest {rows.length} of {p.transactions.toLocaleString()}. The import brings them
              all.
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}
