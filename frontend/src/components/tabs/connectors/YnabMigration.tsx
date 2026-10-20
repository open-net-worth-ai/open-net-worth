import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { Badge, Button, Checkbox, Cluster, FormField, Meter, Select, Stack, Text } from '../../ui'

// One-shot YNAB migration (token -> plan -> preview -> import -> report) over
// /api/ynab/migration. The work is a server-owned job, so this only starts it and polls its
// status: closing the tab does not stop it, and reopening picks the running job back up. The
// token itself is saved in the connector form above and never reaches this component.

interface Plan {
  id: string
  name: string
  currency: string | null
}
interface PreviewAccount {
  name: string
  ynab_type: string
  type: string
  closed: boolean
  opening_balance: number
  transactions: number
}
interface Preview {
  plan_name: string
  currency: string
  accounts: PreviewAccount[]
  categories: number
  groups: number
  transactions: number
  splits: number
  transfers: number
  budget_months: number
  warnings: string[]
  book_state: 'fresh' | 'rerun' | 'foreign'
  base_currency: string
  currency_change: boolean
  currency_blocked: boolean
  money_data: string[]
  prune: { categories: string[]; groups: string[] }
}
interface Check {
  group: 'account' | 'envelope' | 'ready'
  label: string
  expected: number | string
  actual: number | string
  ok: boolean
  note: string
}
interface Job {
  mode: 'preview' | 'import' | 'verify'
  plan_id: string
  status: 'running' | 'done' | 'failed'
  phase: string
  progress: { done: number; total: number }
  log: string[]
  counts: Record<string, number>
  problems: { ref: string; reason: string }[]
  warnings: string[]
  backup: string | null
  preview: Preview | null
  verification: { ok: boolean; mismatches: number; rows: Check[] } | null
  error: string | null
}
interface Status {
  token_saved: boolean
  job: Job | null
}

const ENDPOINT = '/api/ynab/migration'

async function detailOf(r: Response, fallback: string): Promise<string> {
  const d = await r.json().catch(() => ({}))
  const detail = d?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || fallback
}

const fmt = (v: number | string) =>
  typeof v === 'number'
    ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : v

export function YnabMigration() {
  const [planId, setPlanId] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const [polling, setPolling] = useState(false)
  const status = useQueryCache<Status>(
    ENDPOINT,
    async () => {
      const r = await authFetch(ENDPOINT)
      if (!r.ok) throw new Error(await detailOf(r, 'Could not read the migration status.'))
      return (await r.json()) as Status
    },
    { refetchInterval: polling ? 1000 : undefined },
  )
  const job = status.data?.job ?? null
  const running = job?.status === 'running'
  if (running !== polling) setPolling(running)

  const tokenSaved = !!status.data?.token_saved
  const plans = useQueryCache<Plan[]>(
    '/api/ynab/budgets',
    async () => {
      const r = await authFetch('/api/ynab/budgets')
      if (!r.ok) throw new Error(await detailOf(r, 'Could not list your YNAB plans.'))
      return ((await r.json()) as { budgets: Plan[] }).budgets
    },
    { enabled: tokenSaved },
  )

  // Pick the plan a job is already about, so a reopened tab shows its progress and report.
  if (!planId && job) setPlanId(job.plan_id)

  async function launch(mode: 'preview' | 'start' | 'verify') {
    setBusy(true)
    setStartError(null)
    try {
      const r = await authFetch(`${ENDPOINT}/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId, confirm_nonempty: confirm }),
      })
      if (!r.ok) {
        setStartError(await detailOf(r, 'Could not start.'))
        return
      }
      await status.refresh()
    } catch {
      setStartError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  if (status.initialLoading) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }
  if (status.error) {
    return (
      <Cluster gap="--space-2">
        <Text size="sm" tone="bad">
          {status.error instanceof Error ? status.error.message : 'Could not load the migration.'}
        </Text>
        <Button size="sm" onClick={() => void status.refresh()}>
          Retry
        </Button>
      </Cluster>
    )
  }

  const preview = job && job.plan_id === planId ? job.preview : null
  const report = job && job.plan_id === planId ? job.verification : null
  const planOptions = (plans.data ?? []).map((p) => ({
    value: p.id,
    label: p.currency ? `${p.name} (${p.currency})` : p.name,
  }))

  return (
    <Stack gap="--space-4">
      <Text size="md" weight={600}>
        Move your whole YNAB plan across
      </Text>
      <Text size="sm" tone="faint">
        Imports your accounts, categories, envelopes, every transaction and each month&apos;s
        budgeted amounts, then checks the result against YNAB. Safe to run again: nothing is booked
        twice. Best into a fresh book — take a backup first.
      </Text>

      {!tokenSaved ? (
        <Text size="sm" tone="warn">
          Save your YNAB access token above first, then pick a plan here.
        </Text>
      ) : plans.error ? (
        <Cluster gap="--space-2">
          <Text size="sm" tone="bad">
            {plans.error instanceof Error ? plans.error.message : 'Could not list your plans.'}
          </Text>
          <Button size="sm" onClick={() => void plans.refresh()}>
            Retry
          </Button>
        </Cluster>
      ) : plans.initialLoading ? (
        <Text size="sm" tone="faint">
          Loading your YNAB plans…
        </Text>
      ) : planOptions.length === 0 ? (
        <Text size="sm" tone="warn">
          This token can&apos;t see any YNAB plans.
        </Text>
      ) : (
        <FormField label="YNAB plan" htmlFor="ynab-mig-plan" required>
          <Cluster gap="--space-2">
            <Select
              id="ynab-mig-plan"
              value={planId}
              placeholder="Choose a plan…"
              options={planOptions}
              disabled={running}
              onChange={(e) => {
                setPlanId(e.target.value)
                setConfirm(false)
              }}
            />
            <Button
              variant="secondary"
              disabled={!planId || running}
              loading={busy}
              onClick={() => void launch('preview')}
            >
              Preview
            </Button>
          </Cluster>
        </FormField>
      )}

      {startError && (
        <Text size="sm" tone="bad">
          {startError}
        </Text>
      )}

      {running && job && (
        <Stack gap="--space-2">
          <Text size="sm" weight={600}>
            {job.mode === 'preview' ? 'Reading your plan' : 'Working'}: {job.phase}
          </Text>
          {job.progress.total > 0 && (
            <Meter
              value={(job.progress.done / job.progress.total) * 100}
              ariaLabel="Migration progress"
              valueText={`${job.progress.done} of ${job.progress.total}`}
            />
          )}
          {job.log.map((line) => (
            <Text key={line} size="xs" tone="faint">
              {line}
            </Text>
          ))}
        </Stack>
      )}

      {!running && job?.status === 'failed' && job.plan_id === planId && (
        <Text size="sm" tone="bad">
          {job.error}
        </Text>
      )}

      {!running && preview && (
        <PreviewPanel
          preview={preview}
          confirm={confirm}
          onConfirm={setConfirm}
          busy={busy}
          importing={job?.mode === 'import'}
          onImport={() => void launch('start')}
        />
      )}

      {!running && job?.mode === 'import' && job.plan_id === planId && job.status === 'done' && (
        <ImportSummary job={job} />
      )}

      {!running && report && (
        <Report report={report} busy={busy} onRecheck={() => void launch('verify')} />
      )}
    </Stack>
  )
}

function PreviewPanel(props: {
  preview: Preview
  confirm: boolean
  onConfirm: (v: boolean) => void
  busy: boolean
  importing: boolean
  onImport: () => void
}) {
  const { preview: p } = props
  const foreign = p.book_state === 'foreign'
  return (
    <Stack gap="--space-3">
      <Text size="sm" weight={600}>
        {p.plan_name} ({p.currency}) will bring across
      </Text>
      <Text size="sm">
        {p.accounts.length} accounts · {p.transactions} transactions ({p.transfers} transfers,{' '}
        {p.splits} splits) · {p.categories} categories and envelopes in {p.groups} groups ·{' '}
        {p.budget_months} budget months
      </Text>
      <Stack gap="--space-1">
        {p.accounts.map((a) => (
          <Cluster key={a.name} justify="between" gap="--space-2">
            <Text size="xs">
              {a.name}
              {a.closed ? ' (closed, will be archived)' : ''} · {a.type}
            </Text>
            <Text size="xs" tone="faint">
              {a.transactions} transactions · opens at {fmt(a.opening_balance)}
            </Text>
          </Cluster>
        ))}
      </Stack>
      {p.book_state === 'rerun' && (
        <Text size="xs" tone="faint">
          This book already holds an earlier import of this plan; running again adds only what is
          new.
        </Text>
      )}
      {p.currency_change && (
        <Text size="xs" tone="warn">
          The book&apos;s base currency will change from {p.base_currency} to {p.currency}.
        </Text>
      )}
      {p.currency_blocked && (
        <Text size="xs" tone="bad">
          This book&apos;s base currency is {p.base_currency} but the plan is {p.currency}, and the
          book already holds {p.money_data.join(', ')}. Change the base currency in Settings first,
          or import into an empty book.
        </Text>
      )}
      {(p.prune.categories.length > 0 || p.prune.groups.length > 0) && (
        <Text size="xs" tone="faint">
          Unused starter items that will be removed:{' '}
          {[...p.prune.categories, ...p.prune.groups].join(', ')}. Anything you created, funded or
          renamed is kept.
        </Text>
      )}
      {p.warnings.map((w) => (
        <Text key={w} size="xs" tone="warn">
          {w}
        </Text>
      ))}
      {foreign && (
        <Checkbox
          checked={props.confirm}
          onChange={(e) => props.onConfirm(e.target.checked)}
          label="This book already has accounts that didn't come from YNAB"
          description="Import alongside them anyway. A fresh book is the cleaner route."
        />
      )}
      <Cluster>
        <Button
          variant="primary"
          disabled={(foreign && !props.confirm) || p.currency_blocked}
          loading={props.busy}
          onClick={props.onImport}
        >
          {props.importing ? 'Import again' : 'Import into Open Net Worth'}
        </Button>
      </Cluster>
    </Stack>
  )
}

function ImportSummary({ job }: { job: Job }) {
  return (
    <Stack gap="--space-1">
      <Text size="sm" weight={600}>
        Import finished
      </Text>
      {job.log.map((line) => (
        <Text key={line} size="xs" tone="faint">
          {line}
        </Text>
      ))}
      {job.problems.length > 0 && (
        <Stack gap="--space-1">
          <Text size="xs" tone="bad">
            {job.problems.length} transaction(s) were not booked:
          </Text>
          {job.problems.slice(0, 10).map((p) => (
            <Text key={p.ref} size="xs" tone="faint">
              {p.ref}: {p.reason || 'refused'}
            </Text>
          ))}
        </Stack>
      )}
      {job.warnings.map((w) => (
        <Text key={w} size="xs" tone="warn">
          {w}
        </Text>
      ))}
    </Stack>
  )
}

function Report(props: {
  report: NonNullable<Job['verification']>
  busy: boolean
  onRecheck: () => void
}) {
  const { report } = props
  return (
    <Stack gap="--space-2">
      <Cluster justify="between">
        <Cluster gap="--space-2">
          <Text size="sm" weight={600}>
            Checked against YNAB
          </Text>
          <Badge tone={report.ok ? 'pos' : 'neg'}>
            {report.ok ? 'All match' : `${report.mismatches} to look at`}
          </Badge>
        </Cluster>
        <Button size="sm" loading={props.busy} onClick={props.onRecheck}>
          Re-check
        </Button>
      </Cluster>
      {report.rows.map((row) => (
        <div key={row.label}>
          <Cluster justify="between" gap="--space-2" wrap={false}>
            <Text size="xs" tone={row.ok ? undefined : 'bad'}>
              {row.ok ? '✓' : '✗'} {row.label}
            </Text>
            <Text size="xs" tone={row.ok ? 'faint' : 'bad'}>
              YNAB {fmt(row.expected)} · here {fmt(row.actual)}
            </Text>
          </Cluster>
          {row.note && (
            <Text size="2xs" tone="faint">
              {row.note}
            </Text>
          )}
        </div>
      ))}
      {!report.ok && (
        <Text size="xs" tone="faint">
          A transaction count that differs usually means a row was not booked (listed above). An
          envelope or Ready to Assign difference means the two apps&apos; budget rules gave
          different numbers for your history; compare that month in both before relying on it.
        </Text>
      )}
    </Stack>
  )
}
