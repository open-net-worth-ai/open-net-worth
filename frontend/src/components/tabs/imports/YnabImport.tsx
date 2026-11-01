import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import {
  Badge,
  Button,
  Cluster,
  ConfirmDialog,
  EmptyState,
  FormField,
  Meter,
  Panel,
  Select,
  Stack,
  SummaryBar,
  Text,
  type ConfirmDialogData,
} from '../../ui'
import { detailOf, messageOf, useRun, type Preview, type RunSummary } from './runs'

// The YNAB panels of Imports & sync: the connection (the token itself lives on the YNAB connector
// card and never reaches this screen) and the import (plan -> preview -> import -> re-check). The
// work is a server-owned job, so this only starts it and shows the run it created; the run's
// report opens as a page of its own.

interface Plan {
  id: string
  name: string
  currency: string | null
}
interface YnabStatus {
  has_access_token: boolean
  budget_name: string | null
}

type Launch = 'preview' | 'start' | 'verify'

const NONEMPTY_DIALOG: ConfirmDialogData = {
  title: 'Import alongside your existing accounts?',
  body: "This book already has accounts that didn't come from YNAB. They stay as they are and the plan is added next to them. A fresh book is the cleaner route.",
  confirmLabel: 'Import anyway',
}

export function YnabImport(props: {
  runs: RunSummary[] | undefined
  onStarted: () => void
  onOpen: (id: string) => void
}) {
  const navigate = useNavigate()
  const status = useQueryCache<YnabStatus>('/api/ynab/status', async () => {
    const r = await authFetch('/api/ynab/status')
    if (!r.ok) throw new Error(await detailOf(r, 'Could not read the YNAB connector.'))
    return (await r.json()) as YnabStatus
  })
  const tokenSaved = !!status.data?.has_access_token
  const plans = useQueryCache<Plan[]>(
    '/api/ynab/budgets',
    async () => {
      const r = await authFetch('/api/ynab/budgets')
      if (!r.ok) throw new Error(await detailOf(r, 'Could not list your YNAB plans.'))
      return ((await r.json()) as { budgets: Plan[] }).budgets
    },
    { enabled: tokenSaved },
  )

  const [planId, setPlanId] = useState('')
  const [busy, setBusy] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmDialogData | null>(null)

  const running = props.runs?.find((r) => r.status === 'running') ?? null
  // Pick the plan a run is already about, so a reopened page shows its progress.
  if (!planId && running) setPlanId(running.plan_id)
  // The newest finished preview of this plan holds the book state and warnings to act on.
  const latest = props.runs?.find(
    (r) => r.plan_id === planId && r.kind === 'preview' && r.status === 'done' && !r.archived_at,
  )
  const preview = useRun(latest?.id).data?.preview ?? null

  async function launch(action: Launch, confirmed = false) {
    setBusy(true)
    setStartError(null)
    try {
      const r = await authFetch(`/api/ynab/migration/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId, confirm_nonempty: confirmed }),
      })
      if (r.ok) {
        props.onStarted()
        return
      }
      const body = await r.json().catch(() => null)
      if (r.status === 409 && body?.detail?.error === 'confirm_nonempty')
        setConfirm(NONEMPTY_DIALOG)
      else setStartError(messageOf(body, 'Could not start.'))
    } catch {
      setStartError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  const importNow = () =>
    preview?.book_state === 'foreign' ? setConfirm(NONEMPTY_DIALOG) : void launch('start')

  return (
    <>
      <Panel
        title="Connection"
        actions={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void navigate({ to: '/connectors' } as never)}
          >
            Open connector
          </Button>
        }
      >
        <Cluster gap="--space-3">
          <Badge tone={tokenSaved ? 'pos' : 'warn'}>
            {tokenSaved ? 'Token saved' : 'No token'}
          </Badge>
          {status.data?.budget_name && <Text size="sm">{status.data.budget_name}</Text>}
          <Text size="sm" tone="faint">
            Uses the token saved on the YNAB connector.
          </Text>
        </Cluster>
      </Panel>

      <Panel
        title="Import"
        subtitle="Accounts, categories, envelopes, every transaction and each month's Assigned, checked against YNAB. Safe to run again: nothing is booked twice."
      >
        {status.initialLoading ? (
          <Text size="sm" tone="faint">
            Loading…
          </Text>
        ) : !tokenSaved ? (
          <EmptyState
            title="Save your YNAB access token first"
            body="The token lives on the YNAB connector. Once it is saved, pick a plan here."
            action={
              <Button
                variant="secondary"
                onClick={() => void navigate({ to: '/connectors' } as never)}
              >
                Open connector
              </Button>
            }
          />
        ) : (
          <Stack gap="--space-4">
            <PlanPicker
              plans={plans}
              planId={planId}
              disabled={!!running}
              onChange={setPlanId}
              action={
                <Cluster gap="--space-2">
                  <Button
                    variant="secondary"
                    disabled={!planId || !!running}
                    loading={busy}
                    onClick={() => void launch('preview')}
                  >
                    Preview
                  </Button>
                  <Button
                    variant="primary"
                    disabled={!preview || preview.currency_blocked || !!running}
                    onClick={importNow}
                  >
                    Import
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={!planId || !!running}
                    onClick={() => void launch('verify')}
                  >
                    Re-check
                  </Button>
                </Cluster>
              }
            />
            {startError && (
              <Text size="sm" tone="bad">
                {startError}
              </Text>
            )}
            {running && <LiveStrip run={running} onOpen={props.onOpen} />}
            {!running && preview && <PreviewSummary preview={preview} />}
          </Stack>
        )}
      </Panel>

      <ConfirmDialog
        data={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          setConfirm(null)
          void launch('start', true)
        }}
      />
    </>
  )
}

function PlanPicker(props: {
  plans: ReturnType<typeof useQueryCache<Plan[]>>
  planId: string
  disabled: boolean
  onChange: (id: string) => void
  action: React.ReactNode
}) {
  const { plans } = props
  if (plans.error) {
    return (
      <Cluster gap="--space-2">
        <Text size="sm" tone="bad">
          {plans.error instanceof Error ? plans.error.message : 'Could not list your plans.'}
        </Text>
        <Button size="sm" onClick={() => void plans.refresh()}>
          Retry
        </Button>
      </Cluster>
    )
  }
  if (plans.initialLoading) {
    return (
      <Text size="sm" tone="faint">
        Loading your YNAB plans…
      </Text>
    )
  }
  const options = (plans.data ?? []).map((p) => ({
    value: p.id,
    label: p.currency ? `${p.name} (${p.currency})` : p.name,
  }))
  if (options.length === 0) {
    return (
      <Text size="sm" tone="warn">
        This token can&apos;t see any YNAB plans.
      </Text>
    )
  }
  return (
    <FormField label="YNAB plan" htmlFor="imports-ynab-plan" required>
      <Cluster gap="--space-2">
        <Select
          id="imports-ynab-plan"
          value={props.planId}
          placeholder="Choose a plan…"
          options={options}
          disabled={props.disabled}
          onChange={(e) => props.onChange(e.target.value)}
        />
        {props.action}
      </Cluster>
    </FormField>
  )
}

const STRIP_LABEL = {
  preview: 'Reading your plan',
  import: 'Working',
  verify: 'Working',
  sync: 'Syncing',
}

function LiveStrip({ run, onOpen }: { run: RunSummary; onOpen: (id: string) => void }) {
  const { done, total } = run.progress
  return (
    <Stack gap="--space-2">
      <Cluster justify="between" gap="--space-3">
        <Text size="sm" weight={600}>
          {STRIP_LABEL[run.kind]}: {run.phase}
        </Text>
        <Button size="sm" variant="secondary" onClick={() => onOpen(run.id)}>
          View live report
        </Button>
      </Cluster>
      {total > 0 && (
        <Meter
          value={(done / total) * 100}
          ariaLabel="Run progress"
          valueText={`${done} of ${total}`}
        />
      )}
    </Stack>
  )
}

function PreviewSummary({ preview: p }: { preview: Preview }) {
  return (
    <Stack gap="--space-3">
      <Text size="sm" weight={600}>
        {p.plan_name} ({p.currency}) will bring across
      </Text>
      <SummaryBar
        cards={[
          { label: 'Accounts', value: String(p.accounts.length) },
          {
            label: 'Transactions',
            value: String(p.transactions),
            caption: `${p.transfers} transfers, ${p.splits} splits`,
          },
          { label: 'Categories', value: String(p.categories), caption: `${p.groups} groups` },
          { label: 'Budget months', value: String(p.budget_months) },
        ]}
      />
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
    </Stack>
  )
}
