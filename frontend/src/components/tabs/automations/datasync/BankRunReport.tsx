import { authFetch } from '../../../../api/client'
import { useCategories } from '../../../../hooks/useCategories'
import { useQueryCache } from '../../../../state/queryCache'
import { fAmountNative, fDate } from '../../../../utils/format'
import { Badge, Cluster, ErrorState, Modal, Skeleton, Text } from '../../../ui'
import {
  STATUS_LABEL,
  STATUS_TONE,
  formatDuration,
  formatWhen,
  useRun,
  type RunRecord,
} from '../../imports/runs'

// One bank sync run, opened from its History row (ADR 2026-11-13): a line on the run, a line on
// what it did, then the transactions it booked (read by the run's batch).

const DID: [string, (n: number) => string][] = [
  ['booked', (n) => `${n} booked`],
  ['already_booked', (n) => `${n} already in your book`],
  ['pending_skipped', (n) => `${n} still pending at the bank`],
  ['to_review', (n) => `${n} to review`],
  ['linked', (n) => `${n} cleared and matched`],
  ['deleted_before', (n) => `${n} you deleted before, skipped`],
  ['held', (n) => `${n} waiting on a review`],
  ['errors', (n) => `${n} ${n === 1 ? 'error' : 'errors'}`],
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
  const counts = (r?.counts ?? {}) as unknown as Record<string, number>
  return (
    <Modal
      open
      size="md"
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
      subtitle={
        r
          ? [
              r.trigger === 'scheduled' ? 'Scheduled' : 'Started by you',
              `took ${formatDuration(r)}`,
              `read ${counts.fetched ?? 0} from the bank`,
            ].join(' · ')
          : undefined
      }
    >
      {run.error ? (
        <ErrorState
          message={run.error instanceof Error ? run.error.message : 'Could not load that run.'}
          onRetry={() => void run.refresh()}
        />
      ) : !r ? (
        <Skeleton height={120} />
      ) : (
        <RunBody r={r} counts={counts} />
      )}
    </Modal>
  )
}

function RunBody({ r, counts }: { r: RunRecord; counts: Record<string, number> }) {
  const did = DID.filter(([k]) => (counts[k] ?? 0) > 0).map(([k, say]) => say(counts[k]!))
  const problems = [...(r.errors ?? []), ...((r.warnings ?? []) as unknown[])].map(text)
  return (
    <div className="bs-run">
      {r.status === 'failed' ? (
        <Text size="sm" tone="bad">
          {r.error || 'This run failed; nothing was booked.'}
        </Text>
      ) : (
        <Text size="sm">{did.length ? did.join(' · ') : 'Nothing new.'}</Text>
      )}
      {problems.map((p, i) => (
        <Text key={i} size="sm" tone="warn">
          {p}
        </Text>
      ))}
      {(counts.booked ?? 0) > 0 && <Booked runId={r.id} />}
    </div>
  )
}

function Booked({ runId }: { runId: string }) {
  const booked = useBooked(runId)
  const { catMap } = useCategories()
  if (booked.initialLoading) return <Skeleton height={80} />
  if (booked.error)
    return (
      <Text size="sm" tone="bad">
        Could not load what this run booked.
      </Text>
    )
  return (
    <ul className="bs-run-list" aria-label="Booked transactions">
      {(booked.data ?? []).map((t) => {
        const out = !!t.from_account_id
        const amount = out ? -(t.from_amount ?? 0) : (t.to_amount ?? 0)
        const currency = (out ? t.from_currency : t.to_currency) ?? undefined
        const account = (out ? t.from_account_name : t.to_account_name) ?? ''
        const category = t.category
          ? (catMap[t.category]?.label ?? 'Uncategorized')
          : 'Uncategorized'
        return (
          <li key={t.id}>
            <span className="when">{fDate(t.date)}</span>
            <span className="what">
              <span>{t.payee_name || t.description}</span>
              <span className="sub">{`${account} · ${category}`}</span>
            </span>
            <span className={`amt${amount < 0 ? '' : ' in'}`}>
              {`${amount < 0 ? '−' : '+'}${fAmountNative(Math.abs(amount), currency)}`}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
