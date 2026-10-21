import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { fScheduleWhen, timeAgo } from '../../../utils/format'
import { Badge, Button, Cluster, FormField, Grid, Select, Stack, Switch, Text } from '../../ui'

// Scheduled two-way sync with the migrated YNAB plan (/api/ynab/sync): transactions and each
// month's Assigned, both directions. The server owns the schedule and the run; this component
// sets the schedule, starts "Sync now", polls while a run is going, and lists what the sync
// would not apply on its own (changed on both sides, or something YNAB's API cannot take) with
// both versions side by side and the choice of which one wins on BOTH sides.

interface Plan {
  id: string
  name: string
  currency: string | null
}
interface Version {
  date?: string
  account?: string
  payee?: string
  memo?: string
  category?: string | null
  amount: number | null
  cleared?: boolean
}
interface Item {
  id: string
  kind: 'conflict' | 'unsupported' | 'failed'
  entity: 'txn' | 'assigned'
  title: string
  reason: string
  onw: Version | null
  ynab: Version | null
}
interface LastRun {
  trigger: 'schedule' | 'manual'
  started_at: string
  finished_at: string
  ok: boolean
  error: string | null
  requests: number
  summary: string
}
interface SyncStatus {
  token_saved: boolean
  enabled: boolean
  interval: string
  intervals: string[]
  plan_id: string | null
  plan_name: string | null
  running: boolean
  last_run: LastRun | null
  next_run_at: string | null
  conflicts: Item[]
}

const ENDPOINT = '/api/ynab/sync'
const INTERVAL_LABELS: Record<string, string> = {
  '15m': 'Every 15 minutes',
  '30m': 'Every 30 minutes',
  '1h': 'Every hour',
  '6h': 'Every 6 hours',
  daily: 'Once a day',
  manual: 'Manual only',
}

async function detailOf(r: Response, fallback: string): Promise<string> {
  const d = await r.json().catch(() => ({}))
  return typeof d?.detail === 'string' ? d.detail : fallback
}

const money = (v: number | null | undefined) =>
  v == null
    ? ''
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function YnabSync() {
  const [planId, setPlanId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)

  const status = useQueryCache<SyncStatus>(
    ENDPOINT,
    async () => {
      const r = await authFetch(ENDPOINT)
      if (!r.ok) throw new Error(await detailOf(r, 'Could not read the sync status.'))
      return (await r.json()) as SyncStatus
    },
    { refetchInterval: polling ? 1500 : undefined },
  )
  const data = status.data
  const running = !!data?.running
  if (running !== polling) setPolling(running)

  const tokenSaved = !!data?.token_saved
  const plans = useQueryCache<Plan[]>(
    '/api/ynab/budgets',
    async () => {
      const r = await authFetch('/api/ynab/budgets')
      if (!r.ok) throw new Error(await detailOf(r, 'Could not list your YNAB plans.'))
      return ((await r.json()) as { budgets: Plan[] }).budgets
    },
    { enabled: tokenSaved },
  )

  async function send(label: string, url: string, method: string, body?: unknown) {
    setBusy(label)
    setError(null)
    try {
      const r = await authFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!r.ok) {
        setError(await detailOf(r, 'That did not work.'))
        return
      }
      await status.refresh()
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(null)
    }
  }

  if (status.initialLoading) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }
  if (status.error || !data) {
    return (
      <Cluster gap="--space-2">
        <Text size="sm" tone="bad">
          {status.error instanceof Error ? status.error.message : 'Could not load the sync.'}
        </Text>
        <Button size="sm" onClick={() => void status.refresh()}>
          Retry
        </Button>
      </Cluster>
    )
  }

  const chosen = data.plan_id ?? planId
  const planOptions = (plans.data ?? []).map((p) => ({
    value: p.id,
    label: p.currency ? `${p.name} (${p.currency})` : p.name,
  }))
  const setSettings = (patch: Record<string, unknown>) =>
    send('settings', `${ENDPOINT}/settings`, 'PUT', patch)

  return (
    <Stack gap="--space-4">
      <Text size="md" weight={600}>
        Two-way sync
      </Text>
      <Text size="sm" tone="faint">
        Keeps this book and your migrated YNAB plan in step: transactions and each month&apos;s
        Assigned, both directions. Anything it sends to YNAB is flagged red there, and a changed
        Assigned adds a dated line to the category&apos;s note. When both sides changed the same
        thing, nothing is overwritten: it waits below for your choice. Turning this on replaces the
        plain YNAB import for the plan.
      </Text>

      {!tokenSaved ? (
        <Text size="sm" tone="warn">
          Save your YNAB access token above first.
        </Text>
      ) : (
        <>
          {!data.plan_id && (
            <FormField label="YNAB plan to keep in sync" htmlFor="ynab-sync-plan">
              <Select
                id="ynab-sync-plan"
                value={planId}
                placeholder={plans.initialLoading ? 'Loading plans…' : 'Choose the migrated plan…'}
                options={planOptions}
                onChange={(e) => setPlanId(e.target.value)}
              />
            </FormField>
          )}
          <Cluster gap="--space-4" align="end" wrap>
            <Switch
              label="Sync automatically"
              checked={data.enabled}
              disabled={!!busy || running || !chosen}
              onChange={(e) => void setSettings({ enabled: e.target.checked, plan_id: chosen })}
            />
            <FormField label="Schedule" htmlFor="ynab-sync-interval">
              <Select
                id="ynab-sync-interval"
                value={data.interval}
                options={data.intervals.map((i) => ({
                  value: i,
                  label: INTERVAL_LABELS[i] ?? i,
                }))}
                disabled={!!busy || running}
                onChange={(e) => void setSettings({ interval: e.target.value })}
              />
            </FormField>
            <Button
              variant="primary"
              disabled={!data.enabled || running}
              loading={running || busy === 'run'}
              onClick={() => void send('run', `${ENDPOINT}/run`, 'POST')}
            >
              Sync now
            </Button>
          </Cluster>
        </>
      )}

      {error && (
        <Text size="sm" tone="bad">
          {error}
        </Text>
      )}
      {data.plan_name && (
        <Text size="xs" tone="faint">
          Plan: {data.plan_name}
        </Text>
      )}
      <RunSummary data={data} />

      {data.conflicts.length > 0 && (
        <Stack gap="--space-3">
          <Text size="sm" weight={600}>
            Needs your review ({data.conflicts.length})
          </Text>
          {data.conflicts.map((item) => (
            <ReviewItem
              key={item.id}
              item={item}
              busy={busy === item.id || running}
              onKeep={(keep) =>
                void send(item.id, `${ENDPOINT}/conflicts/${item.id}/resolve`, 'POST', { keep })
              }
            />
          ))}
        </Stack>
      )}
    </Stack>
  )
}

function RunSummary({ data }: { data: SyncStatus }) {
  const run = data.last_run
  const next = !data.enabled
    ? null
    : data.interval === 'manual'
      ? 'Manual only'
      : data.next_run_at
        ? fScheduleWhen(data.next_run_at)
        : 'Any moment now'
  return (
    <Stack gap="--space-1">
      {data.running && (
        <Text size="sm" weight={600}>
          Syncing…
        </Text>
      )}
      {run ? (
        <Text size="sm" tone={run.ok ? undefined : 'bad'}>
          Last run {timeAgo(run.finished_at)}: {run.ok ? run.summary : (run.error ?? 'Failed')}
        </Text>
      ) : (
        <Text size="sm" tone="faint">
          Not run yet.
        </Text>
      )}
      {next && (
        <Text size="xs" tone="faint">
          Next run: {next}
        </Text>
      )}
    </Stack>
  )
}

function VersionCard({ title, v }: { title: string; v: Version | null }) {
  return (
    <Stack gap="--space-1">
      <Text size="xs" weight={600}>
        {title}
      </Text>
      {v === null ? (
        <Text size="sm" tone="faint">
          Deleted
        </Text>
      ) : (
        <>
          {v.amount != null && <Text size="sm">{money(v.amount)}</Text>}
          {v.payee && <Text size="sm">{v.payee}</Text>}
          {(v.date || v.account) && (
            <Text size="xs" tone="faint">
              {[v.date, v.account].filter(Boolean).join(' · ')}
            </Text>
          )}
          {v.category && <Text size="xs">{v.category}</Text>}
          {v.memo && (
            <Text size="xs" tone="faint">
              {v.memo}
            </Text>
          )}
          {v.cleared !== undefined && (
            <Text size="xs" tone="faint">
              {v.cleared ? 'Cleared' : 'Uncleared'}
            </Text>
          )}
        </>
      )}
    </Stack>
  )
}

const KIND_LABEL: Record<Item['kind'], string> = {
  conflict: 'Changed in both',
  unsupported: "YNAB can't take it",
  failed: 'Refused',
}

function ReviewItem(props: { item: Item; busy: boolean; onKeep: (keep: 'onw' | 'ynab') => void }) {
  const { item } = props
  return (
    <Stack gap="--space-2">
      <Cluster gap="--space-2" align="center">
        <Text size="sm" weight={600}>
          {item.title}
        </Text>
        <Badge tone={item.kind === 'conflict' ? 'warn' : 'neg'}>{KIND_LABEL[item.kind]}</Badge>
      </Cluster>
      <Text size="xs" tone="faint">
        {item.reason}
      </Text>
      <Grid min={200} gap="--space-3">
        <VersionCard title="Open Net Worth" v={item.onw} />
        <VersionCard title="YNAB" v={item.ynab} />
      </Grid>
      <Cluster gap="--space-2">
        {(item.kind !== 'unsupported' || item.ynab === null) && (
          <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('onw')}>
            Keep Open Net Worth&apos;s
          </Button>
        )}
        <Button size="sm" disabled={props.busy} onClick={() => props.onKeep('ynab')}>
          Keep YNAB&apos;s
        </Button>
      </Cluster>
    </Stack>
  )
}
