import { authFetch } from '../../../../api/client'
import { useCategories } from '../../../../hooks/useCategories'
import { useQueryCache } from '../../../../state/queryCache'
import { fAmountNative, fDate } from '../../../../utils/format'
import {
  Badge,
  Cluster,
  ErrorState,
  Modal,
  Panel,
  Skeleton,
  Stack,
  SummaryBar,
  Text,
} from '../../../ui'
import {
  STATUS_LABEL,
  STATUS_TONE,
  formatDuration,
  formatWhen,
  useRun,
  type RunRecord,
} from '../../imports/runs'

// One bank sync run, opened from its History row (ADR 2026-11-13): what it read, what it booked
// (the transactions themselves, by the run's batch), what it held back and why.

const COUNT_LABELS: [string, string][] = [
  ['booked', 'Booked'],
  ['already_booked', 'Already in your book'],
  ['pending_skipped', 'Still pending at the bank, booked once they clear'],
  ['to_review', 'Sent to Needs your review'],
  ['linked', 'Pending ones that cleared, matched to what was booked'],
  ['deleted_before', 'Deleted by you before, not booked again'],
  ['held', 'Waiting on a review you haven’t decided'],
  ['errors', 'Errors'],
]

interface BookedRow {
  id: string
  date: string
  description: string
  payee_name?: string | null
  category: string | null
  from_account_id: string | null
  from_amount: number | null
  from_currency: string | null
  from_account_name?: string | null
  to_amount: number | null
  to_currency: string | null
  to_account_name?: string | null
}

function useBooked(runId: string) {
  const key = `/api/transactions?batch_id=${encodeURIComponent(`bank_sync:${runId}`)}`
  return useQueryCache<BookedRow[]>(key, async () => {
    const r = await authFetch(key)
    if (!r.ok) throw new Error('Could not load what this run booked.')
    const body = await r.json()
    return (Array.isArray(body) ? body : (body.transactions ?? body.items ?? [])) as BookedRow[]
  })
}

const text = (v: unknown) =>
  typeof v === 'string' ? v : v && typeof v === 'object' ? Object.values(v).join(': ') : ''

export function BankRunReport({ runId, onClose }: { runId: string; onClose: () => void }) {
  const run = useRun(runId)
  const r = run.data
  return (
    <Modal
      open
      size="lg"
      onClose={onClose}
      title={
        r ? (
          <Cluster gap="--space-3">
            {`Sync · ${formatWhen(r.started_at)}`}
            <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>
          </Cluster>
        ) : (
          'Sync'
        )
      }
    >
      {run.error ? (
        <ErrorState
          message={run.error instanceof Error ? run.error.message : 'Could not load that run.'}
          onRetry={() => void run.refresh()}
        />
      ) : !r ? (
        <Skeleton height={160} />
      ) : (
        <RunBody r={r} />
      )}
    </Modal>
  )
}

function RunBody({ r }: { r: RunRecord }) {
  const counts = (r.counts ?? {}) as unknown as Record<string, number>
  const did = COUNT_LABELS.filter(([k]) => (counts[k] ?? 0) > 0)
  const problems = [...(r.errors ?? []), ...((r.warnings ?? []) as unknown[])].map(text)
  return (
    <Stack gap="--space-5">
      <SummaryBar
        cards={[
          { label: 'Started by', value: r.trigger === 'scheduled' ? 'Schedule' : 'You' },
          { label: 'Started', value: formatWhen(r.started_at) },
          { label: 'Took', value: formatDuration(r) },
          { label: 'Read from the bank', value: String(counts.fetched ?? 0) },
        ]}
      />
      {r.status === 'failed' && <ErrorState title="This run failed" message={r.error} />}
      {did.length > 0 && (
        <Panel title="What it did">
          <dl className="bs-run-counts">
            {did.map(([k, label]) => (
              <div key={k}>
                <dt>{label}</dt>
                <dd>{counts[k]}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      )}
      {(counts.booked ?? 0) > 0 && <Booked runId={r.id} count={counts.booked ?? 0} />}
      {problems.length > 0 && (
        <Panel title="Problems">
          <ul className="bs-run-problems">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Panel>
      )}
    </Stack>
  )
}

function Booked({ runId, count }: { runId: string; count: number }) {
  const booked = useBooked(runId)
  const { catMap } = useCategories()
  const rows = booked.data ?? []
  return (
    <Panel title={`Booked (${count})`}>
      {booked.initialLoading ? (
        <Skeleton height={80} />
      ) : booked.error ? (
        <Text size="sm" tone="bad">
          Could not load what this run booked.
        </Text>
      ) : (
        <table className="bs-run-table">
          <tbody>
            {rows.map((t) => {
              const out = !!t.from_account_id
              const amount = out ? -(t.from_amount ?? 0) : (t.to_amount ?? 0)
              const currency = (out ? t.from_currency : t.to_currency) ?? undefined
              return (
                <tr key={t.id}>
                  <td className="when">{fDate(t.date)}</td>
                  <td>
                    <div>{t.payee_name || t.description}</div>
                    <div className="sub">
                      {(out ? t.from_account_name : t.to_account_name) ?? ''} ·{' '}
                      {t.category
                        ? (catMap[t.category]?.label ?? 'Uncategorized')
                        : 'Uncategorized'}
                    </div>
                  </td>
                  <td className={`amt${amount < 0 ? '' : ' in'}`}>
                    {`${amount < 0 ? '−' : '+'}${fAmountNative(Math.abs(amount), currency)}`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
