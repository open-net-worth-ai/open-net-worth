import { useNavigate } from '@tanstack/react-router'
import { Check, Sparkles } from 'lucide-react'
import { authFetch } from '../../../../api/client'
import { useCategories } from '../../../../hooks/useCategories'
import { useChatComposer } from '../../../../state/chatComposer'
import { useQueryCache } from '../../../../state/queryCache'
import { fAmountNative, fDate } from '../../../../utils/format'
import { Badge, Button, Cluster, ErrorState, Modal, Skeleton, Text } from '../../../ui'
import {
  STATUS_LABEL,
  STATUS_TONE,
  formatDuration,
  formatWhen,
  useRun,
  type RunRecord,
} from '../../imports/runs'
import { useTransactionSurfaces } from '../../accounts/transactionSurfacesContext'

// One bank sync run, opened from its History row (ADR 2026-11-13): one line on what it did; the
// transactions it booked, by day, drawn as the Accounts register draws them (its own classes and
// category pill: payee, category or Uncategorized, amount), each opening its detail drawer
// OVER the report so you can go row by row; View in register and Categorize with Lucius in the
// footer (owner, 2026-11-14). The account shows only when the run read more than one.

const DID: [string, (n: number) => string][] = [
  ['already_booked', (n) => `${n} already in your book`],
  ['pending', (n) => `${n} of them pending (uncleared)`],
  ['settled', (n) => `${n} pending cleared`],
  ['pending_removed', (n) => `${n} pending dropped by the bank`],
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
  // A foreign charge: what the merchant charged, beside the amount the account paid.
  original_amount?: number | null
  original_currency?: string | null
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
  const booked = (counts.booked ?? 0) > 0
  return (
    <Modal
      open
      size="md"
      onClose={onClose}
      title={
        r ? (
          <Cluster gap="--space-3">
            {`${r.plan_name || 'Bank'} sync`}
            {r.status !== 'done' && (
              <Badge tone={STATUS_TONE[r.status]}>{STATUS_LABEL[r.status]}</Badge>
            )}
          </Cluster>
        ) : (
          'Sync'
        )
      }
      subtitle={
        r
          ? [
              formatWhen(r.started_at),
              formatDuration(r),
              r.trigger === 'scheduled' ? 'scheduled' : 'started by you',
            ].join(' · ')
          : undefined
      }
      footer={r && booked ? <RunFooter r={r} onClose={onClose} /> : undefined}
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
  const n = counts.booked ?? 0
  return (
    <div className="bs-run">
      {r.status === 'failed' ? (
        <Text size="sm" tone="bad">
          {r.error || 'This run failed; nothing was booked.'}
        </Text>
      ) : (
        <p className="bs-run-sum">
          {n > 0 && <Check size={15} className="bs-run-ok" aria-hidden="true" />}
          <strong>{n ? `${n} booked` : 'Nothing new'}</strong>
          {did.length > 0 && <span className="bs-run-facts">{did.join(' · ')}</span>}
        </p>
      )}
      {problems.map((p, i) => (
        <Text key={i} size="sm" tone="warn">
          {p}
        </Text>
      ))}
      {n > 0 && <Booked runId={r.id} />}
    </div>
  )
}

function RunFooter({ r, onClose }: { r: RunRecord; onClose: () => void }) {
  const navigate = useNavigate()
  return (
    <div className="bs-run-foot">
      <Button
        size="sm"
        onClick={() => {
          onClose()
          void navigate({
            to: '/accounts',
            search: {
              batch: `bank_sync:${r.id}`,
              batchLabel: `${r.plan_name || 'Bank'} sync · ${formatWhen(r.started_at)}`,
            } as never,
          } as never)
        }}
      >
        View in register
      </Button>
      <CategorizeWithLucius r={r} onClose={onClose} />
    </div>
  )
}

/** Hands Lucius exactly this run's uncategorized rows: how many, from when to when, in which
 *  accounts, and "all of them". His transaction tool can't filter by run, so a bare "these"
 *  became "this month" and skipped the rest (owner test, 2026-11-14). The prompt lands in his
 *  box, in a new conversation, for the user to send. */
function CategorizeWithLucius({ r, onClose }: { r: RunRecord; onClose: () => void }) {
  const booked = useBooked(r.id)
  const composer = useChatComposer()
  const open = (booked.data ?? []).filter((t) => !t.category)
  if (!open.length) return null
  const dates = open.map((t) => t.date).sort()
  const first = dates[0]!
  const last = dates[dates.length - 1]!
  const accounts = [...new Set(open.map(accountOf))].filter(Boolean)
  const span = first === last ? fDate(first) : `${fDate(first)} – ${fDate(last)}`
  const prompt =
    `Categorize the ${open.length} uncategorized ${open.length === 1 ? 'transaction' : 'transactions'} ` +
    `the ${r.plan_name || 'bank'} sync booked on ${fDate(r.started_at.slice(0, 10))}: all of them, ` +
    `dated ${span}${accounts.length ? `, in ${accounts.join(', ')}` : ''}.`
  return (
    <Button
      size="sm"
      variant="primary"
      className="bs-run-lucius"
      iconLeft={<Sparkles size={13} aria-hidden="true" />}
      onClick={() => {
        composer.setPendingText(prompt)
        composer.requestOpen(true)
        onClose()
      }}
    >
      Categorize with Lucius
    </Button>
  )
}

const accountOf = (t: BookedRow) =>
  (t.from_account_id ? t.from_account_name : t.to_account_name) || ''

function Booked({ runId }: { runId: string }) {
  const booked = useBooked(runId)
  const { openTransactionById } = useTransactionSurfaces()
  const { catMap } = useCategories()
  if (booked.initialLoading) return <Skeleton height={80} />
  if (booked.error)
    return (
      <Text size="sm" tone="bad">
        Could not load what this run booked.
      </Text>
    )
  const rows = booked.data ?? []
  const manyAccounts = new Set(rows.map(accountOf)).size > 1
  const days: [string, BookedRow[]][] = []
  for (const t of rows) {
    const day = days[days.length - 1]
    if (day && day[0] === t.date) day[1].push(t)
    else days.push([t.date, [t]])
  }
  return (
    <div className="bs-run-days" aria-label="Booked transactions">
      {days.map(([date, txns]) => (
        <section key={date} className="bs-run-day">
          <h4 className="bs-run-date">{fDayLabel(date)}</h4>
          <ul className="bs-run-list">
            {txns.map((t) => (
              <BookedLine
                key={t.id}
                t={t}
                manyAccounts={manyAccounts}
                category={t.category ? catMap[t.category] : undefined}
                onOpen={() =>
                  openTransactionById(t.id, t.payee_name || t.description, { overModal: true })
                }
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

/** One booked transaction, drawn with the register's own cell classes. */
function BookedLine({
  t,
  manyAccounts,
  category,
  onOpen,
}: {
  t: BookedRow
  manyAccounts: boolean
  category?: { label: string; color: string }
  onOpen: () => void
}) {
  const out = !!t.from_account_id
  const amount = out ? -(t.from_amount ?? 0) : (t.to_amount ?? 0)
  const currency = (out ? t.from_currency : t.to_currency) ?? undefined
  return (
    <li>
      <button
        type="button"
        className="bs-run-row"
        aria-label={`Open ${t.payee_name || t.description}`}
        onClick={onOpen}
      >
        <span className="bs-run-payee">
          <span className="acct-payee-nm">{t.payee_name || t.description}</span>
          {manyAccounts && <span className="acct-payee-sub">{accountOf(t)}</span>}
        </span>
        <span className="bs-run-cat">
          {t.category ? (
            <CategoryPill meta={category} />
          ) : (
            <span className="acct-unsorted">Uncategorized</span>
          )}
        </span>
        <span className={`acct-td-amt ${amount < 0 ? 'neg' : 'pos'}`}>
          <span className="acct-amt-main">
            {`${amount < 0 ? '−' : '+'}${fAmountNative(Math.abs(amount), currency)}`}
          </span>
          {t.original_amount != null && t.original_currency && (
            <span className="acct-amt-orig">
              {fAmountNative(Math.abs(t.original_amount), t.original_currency)}
            </span>
          )}
        </span>
      </button>
    </li>
  )
}

/** The register's category pill (TxnRow's `categoryPill`): the category's colour, tinted. */
function CategoryPill({ meta }: { meta?: { label: string; color: string } }) {
  const color = meta?.color || '#7c8595'
  return (
    <span className="acct-pill" style={{ background: color + '26', color }}>
      {meta?.label || 'Uncategorized'}
    </span>
  )
}

/** "Sat, Nov 14" — one run spans days, not years. */
function fDayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}
