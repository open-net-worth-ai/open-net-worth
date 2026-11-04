import { useCallback, useMemo, useState } from 'react'
import { tradingApi, tradingRoot } from '../../../api/trading'
import { useQueryCache } from '../../../state/queryCache'
import { useTradingOverview, useTradingRun } from '../../../hooks/useTrading'
import type { EvidenceSelection, TradingWorkspace } from '../../../types/trading'
import {
  Badge,
  Button,
  Cluster,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Loading,
  PageTabs,
  Panel,
  Stack,
  Text,
} from '../../ui'
import { TradingMandate } from './TradingMandate'
import { TradingMarketPortfolio } from './TradingMarketPortfolio'
import { TradingAgentWork } from './TradingAgentWork'
import { TradingEvidenceInspector } from './TradingEvidenceInspector'
import { WorkspaceForm } from './WorkspaceForm'
import { errorText, readable, statusTone, tradingTime } from './presentation'

export type TradingSection = 'overview' | 'activity' | 'orders' | 'holdings' | 'artifacts'
export function WorkspaceDetail({
  workspace,
  runId,
  onRun,
  onBack,
}: {
  workspace: TradingWorkspace
  runId: string
  section?: TradingSection
  onRun: (id: string) => void
  onSection?: (section: TradingSection) => void
  onBack: () => void
  onEdit?: () => void
}) {
  const overview = useTradingOverview(workspace.id)
  const current = overview.data?.current_run
  const selected = runId || current?.id || ''
  const { run, accounts, orders, fills, performance } = useTradingRun(selected)
  const active = run.data?.workspace_id === workspace.id ? run.data : undefined
  const definition = overview.data?.workspace ?? workspace
  const work = overview.data?.current_work
  const [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [create, setCreate] = useState(false)
  const [layer, setLayer] = useState('layer-market')
  const [selection, setSelection] = useState<EvidenceSelection | null>(null)
  const [inspector, setInspector] = useState(false)
  const linkedEvidence = useQueryCache(
    `${tradingRoot}/runs/${selected}/evidence/${selection?.kind}/${selection?.id}`,
    () => tradingApi.evidence(selected, selection!),
    { enabled: !!selected && !!selection },
  )
  const linkedWorkSelection: EvidenceSelection | null = linkedEvidence.data?.decision
    ? { kind: 'event', id: linkedEvidence.data.decision.id }
    : selection
  const highlightedFillIds = useMemo(
    () =>
      linkedEvidence.data?.fills.map((fill) => fill.id) ??
      (selection?.kind === 'fill' ? [selection.id] : []),
    [linkedEvidence.data, selection],
  )

  const selectEvidence = useCallback((value: EvidenceSelection) => {
    setSelection(value)
    setInspector(true)
  }, [])
  async function execute(operation: () => Promise<unknown>) {
    setBusy(true)
    setError('')
    try {
      await operation()
      setPending(null)
    } catch (err) {
      setError(errorText(err))
      setPending(null)
    } finally {
      setBusy(false)
    }
  }
  const currentSelection = !runId || runId === current?.id
  return (
    <Stack gap="--space-6">
      <Cluster justify="between">
        <Button variant="ghost" onClick={onBack}>
          All workspaces
        </Button>
        <Cluster>
          {active && (
            <>
              <Badge>{active.mode}</Badge>
              <Badge tone={statusTone(active.state)}>{readable(active.state)}</Badge>
              {active.state === 'ready' && (
                <Button variant="primary" disabled={busy} onClick={() => setPending('start')}>
                  Start workspace
                </Button>
              )}
              {active.state === 'running' && (
                <Button disabled={busy} onClick={() => setPending('pause')}>
                  Pause
                </Button>
              )}
              {active.state === 'paused' && (
                <Button disabled={busy} onClick={() => setPending('resume')}>
                  Resume
                </Button>
              )}
              {active.state === 'needs_reconciliation' && (
                <Button
                  disabled={busy}
                  onClick={() => void execute(() => tradingApi.action(active.id, 'reconcile'))}
                >
                  Reconcile account
                </Button>
              )}
              {['ready', 'running', 'paused', 'needs_reconciliation'].includes(active.state) && (
                <Button variant="danger" disabled={busy} onClick={() => setPending('stop')}>
                  End workspace
                </Button>
              )}
            </>
          )}
        </Cluster>
      </Cluster>
      <Text as="h2" size="lg" weight={600}>
        {definition.name}
      </Text>
      {overview.initialLoading && <Loading label="Loading delegated workspace…" />}
      {!!overview.error && (
        <ErrorState
          message={`Workspace updates interrupted. ${errorText(overview.error)}`}
          onRetry={() => void overview.refresh()}
        />
      )}
      {runId && current && runId !== current.id && (
        <Text tone="warn">
          Viewing an earlier execution record. Current workspace authority may have changed since
          this record.
        </Text>
      )}
      <TradingMandate workspace={definition} overview={overview.data} />
      {error && <ErrorState message={error} />}
      {selected && run.initialLoading && <Loading label="Loading workspace environment…" />}
      {selected && !!run.error && (
        <ErrorState message={errorText(run.error)} onRetry={() => void run.refresh()} />
      )}
      {!overview.initialLoading && !overview.error && !selected && (
        <EmptyState
          title="This workspace has not been activated"
          body="Review its environment and allocation before delegating autonomous work."
          action={<Button onClick={() => setCreate(true)}>Review delegation</Button>}
        />
      )}
      {active && (
        <>
          <Panel
            title="Current work"
            actions={
              <Badge>
                {readable(
                  currentSelection ? (work?.state ?? active.agent_state) : active.agent_state,
                )}
              </Badge>
            }
          >
            <Stack gap="--space-3">
              <Text>
                {currentSelection
                  ? (work?.summary ?? 'No current task has been reported.')
                  : 'Historical execution record. Its original work remains available below.'}
              </Text>
              {(work?.blocker || active.last_agent_error) && (
                <Text tone="warn">{work?.blocker ?? active.last_agent_error}</Text>
              )}
              <Text size="sm" tone="muted">
                {active.workspace_snapshot.agent_label} · Last activity{' '}
                {tradingTime(work?.last_activity_at ?? active.last_agent_at)} · Next wake{' '}
                {tradingTime(work?.next_wake_at ?? active.next_wake_at)} · Ends{' '}
                {active.end_at ? tradingTime(active.end_at) : 'Until stopped'}
              </Text>
              {active.state === 'running' && (
                <Text size="sm" tone="muted">
                  The host continues managed work automatically. Waiting and blocked states remain
                  visible; an external executor must maintain its own connection.
                </Text>
              )}
              {active.state === 'paused' && (
                <Text size="sm" tone="muted">
                  New agent decisions are paused. Pending order and cancellation status is shown
                  separately; holdings remain.
                </Text>
              )}
              {['completed', 'stopped'].includes(active.state) && (
                <Text size="sm" tone="muted">
                  Mandate, work history, files and remaining holdings are retained.
                </Text>
              )}
            </Stack>
          </Panel>
          {!!performance.error && (
            <ErrorState
              message={`Valuation updates interrupted. ${errorText(performance.error)}`}
              onRetry={() => void performance.refresh()}
            />
          )}
          <div className="trading-layer-tabs">
            <PageTabs
              ariaLabel="Workspace observation layers"
              value={layer}
              onChange={setLayer}
              tabs={[
                { key: 'layer-market', label: 'Market & portfolio' },
                { key: 'layer-work', label: 'Agent work' },
              ]}
            />
          </div>
          <div className="trading-observation-grid" data-layer={layer}>
            <div className="trading-market-layer">
              <TradingMarketPortfolio
                run={active}
                performance={performance.data}
                accounts={accounts.data?.items ?? []}
                orders={orders.data?.items ?? []}
                fills={fills.data?.items ?? []}
                highlightedFillIds={highlightedFillIds}
                busy={busy}
                onCancel={(order) => execute(() => tradingApi.cancelOrder(active.id, order.id))}
                onSelect={selectEvidence}
              />
            </div>
            <div className="trading-work-layer">
              <TradingAgentWork
                run={active}
                selection={linkedWorkSelection}
                highlightedEvent={
                  linkedEvidence.data?.decision ??
                  linkedEvidence.data?.events.find((event) => event.id === linkedWorkSelection?.id)
                }
                onSelect={selectEvidence}
              />
            </div>
          </div>
          {(accounts.error || orders.error || fills.error) && (
            <ErrorState
              message="Some account or execution records could not be refreshed. Previously loaded values may be stale."
              onRetry={() => {
                void accounts.refresh()
                void orders.refresh()
                void fills.refresh()
              }}
            />
          )}
          {selection && inspector && (
            <TradingEvidenceInspector
              runId={active.id}
              selection={selection}
              onSelect={selectEvidence}
              onClose={() => setInspector(false)}
            />
          )}
        </>
      )}
      {create && (
        <WorkspaceForm
          workspace={definition}
          onClose={() => setCreate(false)}
          onSaved={(_, id) => {
            setCreate(false)
            if (id) onRun(id)
          }}
        />
      )}
      <ConfirmDialog
        data={
          pending && active
            ? {
                title: `${readable(pending === 'stop' ? 'end' : pending)} workspace`,
                body:
                  pending === 'start'
                    ? 'Start autonomous work under the reviewed environment, allocation and mandate?'
                    : pending === 'resume'
                      ? 'Resume work under the current mandate. The host will continue managed work automatically.'
                      : 'Stop new agent work and request cancellation of open orders. Already submitted orders may still execute. Holdings remain in the account.',
                confirmLabel:
                  pending === 'stop'
                    ? 'End workspace'
                    : `${pending.charAt(0).toUpperCase()}${pending.slice(1)} workspace`,
                danger: pending === 'stop',
                confirmDisabled: busy,
              }
            : null
        }
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (active && pending) void execute(() => tradingApi.action(active.id, pending))
        }}
      />
    </Stack>
  )
}
