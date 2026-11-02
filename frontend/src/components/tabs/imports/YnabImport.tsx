import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { Button, Cluster, FormField, Meter, Select, Stack, Text } from '../../ui'
import { detailOf, messageOf, type RunSummary } from './runs'
import { YnabReview } from './YnabReview'

// The import step of the YNAB data sync: pick a plan, then Review. Review reads the plan afresh
// (a preview run, which writes nothing) and opens it in one window — accounts, categories,
// transactions, what changes in this book and how to sync — where the import starts. The token
// lives on the YNAB connector and never reaches this screen; an import is a server-owned job, so
// this only shows its progress.

interface Plan {
  id: string
  name: string
  currency: string | null
}
interface YnabStatus {
  has_access_token: boolean
  budget_name: string | null
}

export function YnabImport(props: {
  runs: RunSummary[] | undefined
  onStarted: () => void
  onOpen: (id: string) => void
}) {
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
  const [reviewId, setReviewId] = useState<string | null>(null)

  // An import (or a re-check) in flight owns the step; a preview runs inside the review window.
  const running = props.runs?.find((r) => r.status === 'running' && r.kind !== 'preview') ?? null
  if (!planId && running) setPlanId(running.plan_id)
  else if (!planId && plans.data?.length === 1) setPlanId(plans.data[0]!.id)

  async function review() {
    setBusy(true)
    setStartError(null)
    try {
      const r = await authFetch('/api/ynab/migration/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setStartError(messageOf(body, 'Could not read the plan.'))
        return
      }
      props.onStarted()
      setReviewId((body as { id: string }).id)
    } catch {
      setStartError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {status.initialLoading ? (
        <Text size="sm" tone="faint">
          Loading…
        </Text>
      ) : !tokenSaved ? (
        <Text size="sm" tone="faint">
          Save your YNAB access token on the YNAB connector first.
        </Text>
      ) : (
        <Stack gap="--space-4">
          <PlanPicker plans={plans} planId={planId} disabled={!!running} onChange={setPlanId} />
          {running ? (
            <LiveStrip run={running} onOpen={props.onOpen} />
          ) : (
            <div>
              <Button
                size="sm"
                variant="primary"
                disabled={!planId}
                loading={busy}
                onClick={() => void review()}
              >
                Review…
              </Button>
            </div>
          )}
          {startError && (
            <Text size="sm" tone="bad">
              {startError}
            </Text>
          )}
        </Stack>
      )}
      {reviewId && (
        <YnabReview
          runId={reviewId}
          planLabel={planLabel(plans.data?.find((x) => x.id === planId))}
          onClose={() => setReviewId(null)}
          onStarted={props.onStarted}
        />
      )}
    </>
  )
}

const planLabel = (p: Plan | undefined) =>
  !p ? 'your YNAB plan' : p.currency ? `${p.name} (${p.currency})` : p.name

function PlanPicker(props: {
  plans: ReturnType<typeof useQueryCache<Plan[]>>
  planId: string
  disabled: boolean
  onChange: (id: string) => void
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
    label: planLabel(p),
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
      <Select
        id="imports-ynab-plan"
        value={props.planId}
        placeholder="Choose a plan…"
        options={options}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value)}
      />
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
