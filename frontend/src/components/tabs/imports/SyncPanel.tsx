import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { fScheduleWhen, timeAgo } from '../../../utils/format'
import {
  Button,
  Cluster,
  FormField,
  Panel,
  Select,
  Stack,
  SummaryBar,
  Switch,
  Text,
} from '../../ui'
import { ReviewCard } from './ReviewItem'
import { INTERVAL_LABELS, sendSync, useSyncStatus, type SyncStatus } from './sync'
import { detailOf } from './runs'

// Scheduled two-way sync with the migrated YNAB plan: transactions and each month's Assigned,
// both directions. This sets the schedule, starts "Sync now", and lists what the sync would not
// apply on its own. Every run it makes is a record in the run history.

interface Plan {
  id: string
  name: string
  currency: string | null
}

export function SyncPanel({ onOpen }: { onOpen: (runId: string) => void }) {
  const status = useSyncStatus()
  const data = status.data
  const [planId, setPlanId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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

  async function send(label: string, path: string, method: string, body?: unknown) {
    setBusy(label)
    const problem = await sendSync(path, method, body)
    setError(problem)
    if (!problem) await status.refresh()
    setBusy(null)
  }

  if (status.initialLoading) {
    return (
      <Panel title="Sync">
        <Text size="sm" tone="faint">
          Loading…
        </Text>
      </Panel>
    )
  }
  if (status.error || !data) {
    return (
      <Panel title="Sync">
        <Cluster gap="--space-2">
          <Text size="sm" tone="bad">
            {status.error instanceof Error ? status.error.message : 'Could not load the sync.'}
          </Text>
          <Button size="sm" onClick={() => void status.refresh()}>
            Retry
          </Button>
        </Cluster>
      </Panel>
    )
  }

  const running = data.running
  const chosen = data.plan_id ?? planId
  const planOptions = (plans.data ?? []).map((p) => ({
    value: p.id,
    label: p.currency ? `${p.name} (${p.currency})` : p.name,
  }))
  const setSettings = (patch: Record<string, unknown>) =>
    send('settings', '/settings', 'PUT', patch)

  return (
    <Panel
      title="Sync"
      subtitle="Keeps this book and your migrated YNAB plan in step, both directions. Anything it sends to YNAB is flagged red there, and a changed Assigned adds a dated line to the category's note. When both sides changed the same thing, nothing is overwritten: it waits below for your choice."
    >
      <Stack gap="--space-4">
        {!tokenSaved ? (
          <Text size="sm" tone="warn">
            Save your YNAB access token on the YNAB connector first.
          </Text>
        ) : (
          <>
            {!data.plan_id && (
              <FormField label="YNAB plan to keep in sync" htmlFor="ynab-sync-plan">
                <Select
                  id="ynab-sync-plan"
                  value={planId}
                  placeholder={
                    plans.initialLoading ? 'Loading plans…' : 'Choose the migrated plan…'
                  }
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
                onClick={() => void send('run', '/run', 'POST')}
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
        {running && (
          <Cluster gap="--space-3">
            <Text size="sm" weight={600}>
              Syncing…
            </Text>
            {data.run_id && (
              <Button size="sm" variant="secondary" onClick={() => onOpen(data.run_id as string)}>
                View live report
              </Button>
            )}
          </Cluster>
        )}
        <Summary data={data} onOpen={onOpen} />
        {data.conflicts.length > 0 && (
          <Stack gap="--space-3">
            <Text size="sm" weight={600}>
              Needs your review ({data.conflicts.length})
            </Text>
            {data.conflicts.map((item) => (
              <ReviewCard
                key={item.id}
                item={item}
                busy={busy === item.id || running}
                onKeep={(keep) =>
                  void send(item.id, `/conflicts/${item.id}/resolve`, 'POST', { keep })
                }
              />
            ))}
          </Stack>
        )}
      </Stack>
    </Panel>
  )
}

function Summary({ data, onOpen }: { data: SyncStatus; onOpen: (runId: string) => void }) {
  const run = data.last_run
  const next = !data.enabled
    ? null
    : data.interval === 'manual'
      ? 'Manual only'
      : data.next_run_at
        ? fScheduleWhen(data.next_run_at)
        : 'Any moment now'
  if (!run) {
    return (
      <Text size="sm" tone="faint">
        Not run yet.
      </Text>
    )
  }
  return (
    <Stack gap="--space-2">
      <SummaryBar
        cards={[
          { label: 'Last run', value: timeAgo(run.finished_at) },
          ...(next ? [{ label: 'Next run', value: next }] : []),
          {
            label: 'Last result',
            value: run.ok ? run.summary : (run.error ?? 'Failed'),
            tone: run.ok ? undefined : ('neg' as const),
          },
        ]}
      />
      {run.run_id ? (
        <Cluster>
          <Button size="sm" variant="ghost" onClick={() => onOpen(run.run_id as string)}>
            Open the last report
          </Button>
        </Cluster>
      ) : (
        run.ok && (
          <Text size="xs" tone="faint">
            A scheduled run that changes nothing is not added to the run history.
          </Text>
        )
      )}
    </Stack>
  )
}
