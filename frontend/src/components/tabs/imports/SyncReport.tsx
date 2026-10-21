import { useState } from 'react'
import { Badge, Button, Cluster, Panel, SegmentedControl, Stack, SummaryBar, Text } from '../../ui'
import { ReviewCard } from './ReviewItem'
import { sendSync, useSyncStatus } from './sync'
import type { AssignedChange, Note, RunRecord, TxnChange } from './runs'

// The report of a sync run: what went to YNAB, what came in, which Assigned figures moved, what
// was filed for review, deferred or refused. Empty sections are left out; a run that changed
// nothing says so. Long lists show 50 at a time.

const PAGE = 50

const money = (v: number | null | undefined, currency = '') =>
  v == null
    ? ''
    : `${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${currency ? ` ${currency}` : ''}`

function Paged<T>(props: { items: T[]; render: (page: T[]) => React.ReactNode; hidden?: number }) {
  const [shown, setShown] = useState(PAGE)
  const more = props.items.length - shown
  return (
    <Stack gap="--space-3">
      {props.render(props.items.slice(0, shown))}
      {more > 0 && (
        <Cluster>
          <Button size="sm" variant="secondary" onClick={() => setShown((n) => n + PAGE)}>
            Show {Math.min(more, PAGE)} more
          </Button>
        </Cluster>
      )}
      {!!props.hidden && (
        <Text size="xs" tone="faint">
          +{props.hidden} more not stored
        </Text>
      )}
    </Stack>
  )
}

function ChangeCell({ change }: { change: TxnChange }) {
  if (change.action === 'created') return <>Created</>
  if (change.action === 'deleted') return <>Deleted</>
  return (
    <Stack gap="--space-1">
      {change.fields.map((f) => (
        <Text key={f.name} size="sm">
          {f.name} {f.before || '—'} → {f.after || '—'}
        </Text>
      ))}
    </Stack>
  )
}

function TxnTable({ changes }: { changes: TxnChange[] }) {
  return (
    <table className="imp-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Payee</th>
          <th>Account</th>
          <th className="imp-table__num">Amount</th>
          <th>Change</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => (
          <tr key={`${c.txn_id}-${i}`}>
            <td data-label="Date">{c.date ?? ''}</td>
            <td data-label="Payee" className="imp-table__wrap">
              {c.payee || '(no payee)'}
            </td>
            <td data-label="Account">{c.account}</td>
            <td data-label="Amount" className="imp-table__num">
              {money(c.amount, c.currency)}
            </td>
            <td data-label="Change" className="imp-table__wrap">
              <ChangeCell change={c} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Transactions({ run }: { run: RunRecord }) {
  const all = run.txn_changes ?? []
  const sent = all.filter((c) => c.direction === 'sent')
  const pulled = all.filter((c) => c.direction === 'pulled')
  const [tab, setTab] = useState<'sent' | 'pulled'>(
    sent.length || !pulled.length ? 'sent' : 'pulled',
  )
  if (all.length === 0) return null
  const totals = run.totals
  return (
    <Panel title="Transactions">
      <Stack gap="--space-3">
        <SegmentedControl
          ariaLabel="Direction"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'sent', label: `Sent (${totals?.sent ?? sent.length})` },
            { value: 'pulled', label: `Pulled (${totals?.pulled ?? pulled.length})` },
          ]}
        />
        <Paged
          key={tab}
          items={tab === 'sent' ? sent : pulled}
          hidden={run.not_stored?.txn_changes}
          render={(page) => <TxnTable changes={page} />}
        />
      </Stack>
    </Panel>
  )
}

function AssignedTable({ changes }: { changes: AssignedChange[] }) {
  return (
    <table className="imp-table">
      <thead>
        <tr>
          <th>Category</th>
          <th>Month</th>
          <th className="imp-table__num">Before → After</th>
          <th>Direction</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => (
          <tr key={`${c.category}-${c.month}-${i}`}>
            <td data-label="Category" className="imp-table__wrap">
              {c.category}
            </td>
            <td data-label="Month">{c.month}</td>
            <td data-label="Before → After" className="imp-table__num">
              {money(c.before)} → {money(c.after)}
            </td>
            <td data-label="Direction">{c.direction === 'sent' ? 'Sent to YNAB' : 'Pulled in'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Notes({ title, notes, hidden }: { title: string; notes: Note[]; hidden?: number }) {
  if (notes.length === 0) return null
  return (
    <Panel title={title} badge={notes.length}>
      <Stack gap="--space-3">
        {notes.map((n, i) => (
          <div key={`${n.item}-${i}`}>
            <Text size="sm" weight={600}>
              {n.item}
            </Text>
            <Text as="div" size="xs" tone="faint">
              {n.reason}
            </Text>
          </div>
        ))}
        {!!hidden && (
          <Text size="xs" tone="faint">
            +{hidden} more not stored
          </Text>
        )}
      </Stack>
    </Panel>
  )
}

function Conflicts({ run }: { run: RunRecord }) {
  const live = useSyncStatus().data
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const filed = run.conflicts ?? []
  if (filed.length === 0) return null
  const open = new Set((live?.conflicts ?? []).map((c) => c.id))

  async function keep(id: string, side: 'onw' | 'ynab') {
    setBusy(id)
    setError(await sendSync(`/conflicts/${id}/resolve`, 'POST', { keep: side }))
    setBusy(null)
  }

  return (
    <Panel title="Conflicts filed" badge={filed.length}>
      <Stack gap="--space-4">
        {error && (
          <Text size="sm" tone="bad">
            {error}
          </Text>
        )}
        {filed.map((c) =>
          open.has(c.id) ? (
            <ReviewCard
              key={c.id}
              item={{
                id: c.id,
                kind: 'conflict',
                title: c.description,
                reason: c.reason,
                onw: c.onw_value,
                ynab: c.ynab_value,
              }}
              busy={busy === c.id || !!live?.running}
              onKeep={(side) => void keep(c.id, side)}
            />
          ) : (
            <Cluster key={c.id} gap="--space-2">
              <Text size="sm">{c.description}</Text>
              <Badge tone="muted">Resolved</Badge>
            </Cluster>
          ),
        )}
        {!!run.not_stored?.conflicts && (
          <Text size="xs" tone="faint">
            +{run.not_stored.conflicts} more not stored
          </Text>
        )}
      </Stack>
    </Panel>
  )
}

export function SyncSections({ run }: { run: RunRecord }) {
  const t = run.totals
  const assigned = run.assigned_changes ?? []
  const quiet = !!t && !Object.values(t).some(Boolean)
  if (!t) return null
  return (
    <>
      {quiet && run.status === 'done' ? (
        <Panel title="Result">
          <Text size="sm">Nothing changed.</Text>
        </Panel>
      ) : (
        <SummaryBar
          cards={[
            { label: 'Sent', value: String(t.sent) },
            { label: 'Pulled', value: String(t.pulled) },
            { label: 'Assigned changes', value: String(t.assigned) },
            {
              label: 'Conflicts filed',
              value: String(t.conflicts),
              tone: t.conflicts ? 'warn' : undefined,
            },
            { label: 'Deferred', value: String(t.deferred) },
            { label: 'Errors', value: String(t.errors), tone: t.errors ? 'neg' : undefined },
          ]}
        />
      )}
      <Transactions run={run} />
      {assigned.length > 0 && (
        <Panel title="Assigned">
          <Paged
            items={assigned}
            hidden={run.not_stored?.assigned_changes}
            render={(page) => <AssignedTable changes={page} />}
          />
        </Panel>
      )}
      <Conflicts run={run} />
      <Notes title="Deferred" notes={run.deferred ?? []} hidden={run.not_stored?.deferred} />
      <Notes title="Errors" notes={run.errors ?? []} hidden={run.not_stored?.errors} />
    </>
  )
}
