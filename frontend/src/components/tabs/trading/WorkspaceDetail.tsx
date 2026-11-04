import { useState } from 'react'
import { tradingApi } from '../../../api/trading'
import { useTradingRun, useTradingRuns } from '../../../hooks/useTrading'
import type { TradingWorkspace, TradingRun, TradingOrder } from '../../../types/trading'
import {
  Badge,
  Button,
  Cluster,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Grid,
  Loading,
  Modal,
  PageTabs,
  Panel,
  Select,
  Stack,
  SummaryBar,
  Text,
} from '../../ui'
import { RunForm } from './RunForm'
import { RunTools } from './RunTools'
import { TradingActivity } from './TradingActivity'
import { TradingAccounts, TradingHoldings } from './TradingHoldings'
import { TradingOrders } from './TradingOrders'
import { TradingPerformance } from './TradingPerformance'
import {
  errorText,
  readable,
  statusTone,
  tradingMoney,
  tradingPercent,
  tradingTime,
} from './presentation'

export type TradingSection = 'overview' | 'activity' | 'orders' | 'holdings' | 'artifacts'
export function WorkspaceDetail({
  workspace,
  runId,
  section,
  onRun,
  onSection,
  onBack,
  onEdit,
}: {
  workspace: TradingWorkspace
  runId: string
  section: TradingSection
  onRun: (id: string) => void
  onSection: (section: TradingSection) => void
  onBack: () => void
  onEdit: () => void
}) {
  const runs = useTradingRuns(workspace.id)
  const [newRun, setNewRun] = useState(false)
  const selected = runId || runs.data?.items[0]?.id || ''
  const { run, accounts, orders, fills, performance } = useTradingRun(selected)
  const [mandate, setMandate] = useState(false)
  const [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
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
  const active = run.data
  const stats = performance.data
  return (
    <Stack gap="--space-6">
      <Cluster justify="between">
        <Button variant="ghost" onClick={onBack}>
          All workspaces
        </Button>
        <Cluster>
          <Button onClick={onEdit}>Edit definition</Button>
          <Button variant="primary" onClick={() => setNewRun(true)}>
            New run
          </Button>
        </Cluster>
      </Cluster>
      <Text as="h2" size="lg" weight={600}>
        {workspace.name}
      </Text>
      {runs.initialLoading && <Loading label="Loading runs…" />}
      {!!runs.error && (
        <ErrorState message={errorText(runs.error)} onRetry={() => void runs.refresh()} />
      )}
      {!runs.initialLoading && !runs.error && !runs.data?.items.length && (
        <EmptyState
          title="No runs yet"
          body="Choose an environment, capital allocation and period for the first run."
          action={
            <Button variant="primary" onClick={() => setNewRun(true)}>
              Create a run
            </Button>
          }
        />
      )}
      {!!runs.data?.items.length && (
        <Select
          aria-label="Selected run"
          value={selected}
          onChange={(e) => onRun(e.target.value)}
          options={runs.data.items.map((item) => ({
            value: item.id,
            label: `${item.mode} · ${tradingTime(item.created_at)} · ${readable(item.state)}`,
          }))}
        />
      )}
      {selected && run.initialLoading && <Loading label="Loading trading run…" />}
      {selected && !!run.error && (
        <ErrorState message={errorText(run.error)} onRetry={() => void run.refresh()} />
      )}
      {active && (
        <>
          <Cluster justify="between">
            <Cluster>
              <Badge>{active.mode}</Badge>
              <Badge tone={statusTone(active.state)}>{readable(active.state)}</Badge>
              <Text size="sm" tone="muted">
                Environment time {tradingTime(active.current_time)}
              </Text>
            </Cluster>
            <Cluster>
              <Button onClick={() => setMandate(true)}>View mandate</Button>
              {active.state === 'ready' && (
                <Button variant="primary" disabled={busy} onClick={() => setPending('start')}>
                  Start run
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
              {['ready', 'running', 'paused', 'needs_reconciliation'].includes(active.state) && (
                <Button variant="danger" disabled={busy} onClick={() => setPending('stop')}>
                  Stop run
                </Button>
              )}
              {active.state === 'needs_reconciliation' && (
                <Button
                  disabled={busy}
                  onClick={() => void execute(() => tradingApi.action(active.id, 'reconcile'))}
                >
                  Reconcile
                </Button>
              )}
            </Cluster>
          </Cluster>
          {error && <ErrorState message={error} />}
          {active.state === 'needs_reconciliation' && (
            <Text tone="warn">
              Reconciliation required. New orders are blocked until account state and unknown
              outcomes have been checked.
            </Text>
          )}
          {active.last_agent_error && <ErrorState message={active.last_agent_error} />}
          {stats && (
            <SummaryBar
              cards={[
                {
                  label: 'Equity',
                  value: tradingMoney(stats.equity, stats.base_currency),
                  caption: stats.valuation_complete ? '' : 'Awaiting valuation',
                },
                { label: 'Return', value: tradingPercent(stats.return_pct) },
                { label: 'Maximum drawdown', value: tradingPercent(stats.max_drawdown_pct) },
                {
                  label: 'Available cash',
                  value: tradingMoney(stats.available_cash, stats.base_currency),
                },
                {
                  label: 'Agent cost',
                  value: tradingMoney(stats.model_cost_usd ?? active.model_cost_usd, 'USD'),
                  caption: 'Separate from trading P&L',
                },
              ]}
            />
          )}
          {!!performance.error && (
            <ErrorState
              message={`Valuation updates interrupted. ${errorText(performance.error)}`}
              onRetry={() => void performance.refresh()}
            />
          )}
          <Panel title="Run status">
            <Stack gap="--space-4">
              <Text>
                {active.workspace_snapshot.agent_label} · {readable(active.agent_state)} · Ends{' '}
                {tradingTime(active.end_at)}
              </Text>
              <Text size="sm" tone="muted">
                Next wake-up {tradingTime(active.next_wake_at)} · Strategy version{' '}
                {active.agent_version}
              </Text>
              <RunTools run={active} />
              {active.mode === 'backtest' && active.state === 'running' && (
                <Button
                  disabled={busy}
                  onClick={() => void execute(() => tradingApi.action(active.id, 'advance'))}
                >
                  Advance historical environment
                </Button>
              )}
            </Stack>
          </Panel>
          <PageTabs
            ariaLabel="Run sections"
            value={section}
            onChange={onSection}
            tabs={[
              { key: 'overview', label: 'Overview' },
              { key: 'activity', label: 'Activity' },
              { key: 'orders', label: 'Orders' },
              { key: 'holdings', label: 'Holdings' },
              { key: 'artifacts', label: 'Artifacts' },
            ]}
          />
          <div role="tabpanel" aria-labelledby={`ui-page-tab-${section}`}>
            {section === 'overview' && (
              <Stack gap="--space-6">
                <Grid>
                  {stats && <TradingPerformance performance={stats} />}
                  <MandatePanel run={active} />
                </Grid>
                <TradingAccounts accounts={accounts.data?.items ?? []} />
              </Stack>
            )}
            {section === 'activity' && <TradingActivity key={active.id} runId={active.id} />}
            {section === 'artifacts' && (
              <TradingActivity key={active.id} runId={active.id} artifactsOnly />
            )}
            {section === 'orders' && (
              <TradingOrders
                orders={orders.data?.items ?? []}
                fills={fills.data?.items ?? []}
                busy={busy}
                onCancel={(order: TradingOrder) =>
                  execute(() => tradingApi.cancelOrder(active.id, order.id))
                }
              />
            )}
            {section === 'holdings' && <TradingHoldings accounts={accounts.data?.items ?? []} />}
          </div>
          {(accounts.error || orders.error || fills.error) && (
            <ErrorState
              message="Some execution records could not be refreshed. Previously loaded values may be stale."
              onRetry={() => {
                void accounts.refresh()
                void orders.refresh()
                void fills.refresh()
              }}
            />
          )}
        </>
      )}
      {newRun && (
        <RunForm
          workspace={workspace}
          onClose={() => setNewRun(false)}
          onSaved={(item) => {
            setNewRun(false)
            onRun(item.id)
          }}
        />
      )}
      {mandate && active && (
        <Modal open onClose={() => setMandate(false)} title="This run’s frozen mandate" size="lg">
          <MandatePanel run={active} />
        </Modal>
      )}
      <ConfirmDialog
        data={
          pending && active
            ? {
                title: `${readable(pending)} run`,
                body:
                  pending === 'start'
                    ? 'Start autonomous trading in the displayed environment using this run’s frozen account allocation and mandate?'
                    : pending === 'resume'
                      ? 'Resume agent actions under the existing mandate?'
                      : 'Stop new agent actions and request cancellation of open orders. Orders already sent may still execute. Holdings remain in the account.',
                confirmLabel:
                  pending === 'stop'
                    ? 'Stop run'
                    : `${pending.charAt(0).toUpperCase()}${pending.slice(1)} run`,
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

function MandatePanel({ run }: { run: TradingRun }) {
  const definition = run.workspace_snapshot
  return (
    <Panel title="Frozen mandate">
      <Stack gap="--space-4">
        <Text>{definition.instructions || 'No additional instructions.'}</Text>
        <Text>
          {definition.agent_label} · {definition.agent_kind} · Workspace revision{' '}
          {definition.revision}
        </Text>
        <Text>
          Period: {tradingTime(run.start_at)} – {tradingTime(run.end_at)}
        </Text>
        <Text>
          Allocation:{' '}
          {Object.entries(run.initial_capital)
            .map(([currency, value]) => tradingMoney(value, currency))
            .join(' · ')}
        </Text>
        <Text>Markets: {definition.mandate.allowed_asset_classes.join(', ')}</Text>
        <Text>
          Maximum order:{' '}
          {definition.mandate.max_order_notional == null
            ? 'Available authorized capital'
            : tradingMoney(definition.mandate.max_order_notional, run.base_currency)}
        </Text>
        <Text>
          Agent usage budget: {tradingMoney(definition.mandate.max_model_cost_usd, 'USD')}
        </Text>
        <Text>
          Simulation fees / slippage: {String(definition.mandate.fee_bps)} /{' '}
          {String(definition.mandate.slippage_bps)} bps
        </Text>
        <Text>Dataset: {run.data_version}</Text>
        <Text tone="muted">
          Stop policy: cancel open orders, retain holdings. Run environment and account bindings
          cannot be changed.
        </Text>
      </Stack>
    </Panel>
  )
}
