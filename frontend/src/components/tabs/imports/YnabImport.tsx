import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { Button, Cluster, FormField, Meter, Select, Stack, Text } from '../../ui'
import { YNAB_APP, type BudgetAppInfo } from '../automations/datasync/budgetApps'
import { usePlans, type Plan } from './plans'
import { detailOf, messageOf, type RunSummary } from './runs'
import { YnabReview } from './YnabReview'

// The import step of a budget app's data sync: pick a plan, then Review. Review reads the plan afresh
// (a preview run, which writes nothing) and opens it in one window — accounts, categories,
// transactions, what changes in this book and how to sync — where the import starts. The sign-in
// lives on the app's connector and never reaches this screen; an import is a server-owned job, so
// this only shows its progress.

type ConnectorStatus = Record<string, unknown> & { has_encryption_password?: boolean }

export function YnabImport({
  app = YNAB_APP,
  ...props
}: {
  app?: BudgetAppInfo
  runs: RunSummary[] | undefined
  /** The plan to start from, ahead of any the connector remembers. */
  startFrom?: string | null
  onStarted: () => void
  onOpen: (id: string) => void
  onConnect?: () => void
}) {
  const status = useQueryCache<ConnectorStatus>(`${app.apiPrefix}/status`, async () => {
    const r = await authFetch(`${app.apiPrefix}/status`)
    if (!r.ok) throw new Error(await detailOf(r, `Could not read the ${app.name} connector.`))
    return (await r.json()) as ConnectorStatus
  })
  const tokenSaved = !!status.data && app.signInSaved(status.data)
  const plans = usePlans(app, tokenSaved)

  const [planId, setPlanId] = useState('')
  const [busy, setBusy] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [reviewId, setReviewId] = useState<string | null>(null)
  const [blockedAtStart, setBlockedAtStart] = useState<string | null>(null)

  // An import (or a re-check) in flight owns the step; a preview runs inside the review window.
  const running = props.runs?.find((r) => r.status === 'running' && r.kind !== 'preview') ?? null
  const preferred = props.startFrom ?? (status.data ? app.savedPlanId(status.data) : null)
  if (!planId && running) setPlanId(running.plan_id)
  else if (!planId && plans.data?.some((p) => p.id === preferred)) setPlanId(preferred!)
  else if (!planId && plans.data?.length === 1) setPlanId(plans.data[0]!.id)
  const needsPassword =
    !!plans.data?.find((p) => p.id === planId)?.encrypted && !status.data?.has_encryption_password

  async function review() {
    setBusy(true)
    setStartError(null)
    try {
      const r = await authFetch(`${app.apiPrefix}/migration/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId }),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setStartError(messageOf(body, `Could not read the ${app.planWord}.`))
        return
      }
      props.onStarted()
      setBlockedAtStart((body as { blocked_reason?: string | null }).blocked_reason ?? null)
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
          {app.saveFirst}
        </Text>
      ) : (
        <Stack gap="--space-4">
          <PlanPicker
            app={app}
            plans={plans}
            planId={planId}
            disabled={!!running}
            onChange={setPlanId}
          />
          {needsPassword && (
            <Text size="sm" tone="warn">
              This budget is encrypted. Add its encryption password first.{' '}
              <button type="button" className="a3-linkbtn" onClick={props.onConnect}>
                Open the connection
              </button>
            </Text>
          )}
          {running ? (
            <LiveStrip app={app} run={running} onOpen={props.onOpen} />
          ) : (
            <div>
              <Button
                size="sm"
                variant="primary"
                disabled={!planId || needsPassword}
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
          app={app}
          runId={reviewId}
          planLabel={planLabel(
            app,
            plans.data?.find((x) => x.id === planId),
          )}
          blockedAtStart={blockedAtStart}
          onClose={() => setReviewId(null)}
          onStarted={props.onStarted}
        />
      )}
    </>
  )
}

const planLabel = (app: BudgetAppInfo, p: Plan | undefined) =>
  !p ? `your ${app.name} ${app.planWord}` : p.currency ? `${p.name} (${p.currency})` : p.name

export function PlanPicker({
  app,
  plans,
  planId,
  disabled = false,
  onChange,
  id = `imports-${app.key}-plan`,
  label = `${app.name} ${app.planWord}`,
  help,
}: {
  app: BudgetAppInfo
  plans: ReturnType<typeof usePlans>
  planId: string
  disabled?: boolean
  onChange: (id: string) => void
  id?: string
  label?: string
  help?: React.ReactNode
}) {
  if (plans.error) {
    return (
      <Cluster gap="--space-2">
        <Text size="sm" tone="bad">
          {plans.error instanceof Error
            ? plans.error.message
            : `Could not list your ${app.planWord}s.`}
        </Text>
        <Button
          size="sm"
          className={app.asksTypeAndCurrency ? 'tap-target' : undefined}
          onClick={() => void plans.refresh()}
        >
          Retry
        </Button>
      </Cluster>
    )
  }
  if (plans.initialLoading) {
    return (
      <Text size="sm" tone="faint">
        {`Loading your ${app.name} ${app.planWord}s…`}
      </Text>
    )
  }
  const options = (plans.data ?? []).map((p) => ({
    value: p.id,
    label: p.encrypted ? `${planLabel(app, p)} (encrypted)` : planLabel(app, p),
  }))
  if (options.length === 0) {
    return (
      <Text size="sm" tone="warn">
        {`This ${app.tokenNoun} can't see any ${app.name} ${app.planWord}s.`}
      </Text>
    )
  }
  return (
    <FormField label={label} htmlFor={id} required help={help}>
      <Select
        id={id}
        value={planId}
        placeholder={`Choose a ${app.planWord}…`}
        options={options}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </FormField>
  )
}

const STRIP_LABEL = {
  import: 'Working',
  verify: 'Working',
  sync: 'Syncing',
}

function LiveStrip({
  app,
  run,
  onOpen,
}: {
  app: BudgetAppInfo
  run: RunSummary
  onOpen: (id: string) => void
}) {
  const { done, total } = run.progress
  const label = run.kind === 'preview' ? `Reading your ${app.planWord}` : STRIP_LABEL[run.kind]
  return (
    <Stack gap="--space-2">
      <Cluster justify="between" gap="--space-3">
        <Text size="sm" weight={600}>
          {label}: {run.phase}
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
