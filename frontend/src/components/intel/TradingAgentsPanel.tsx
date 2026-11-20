import { useNavigate } from '@tanstack/react-router'
import { TA_HISTORY_MAX } from '../../types/tradingAgentsConstants'
import type { TaStatus } from '../../types/tradingAgents'
import { Button, Cluster, ConfirmDialog, ErrorState, Panel, Skeleton, Stack, Text } from '../ui'
import type { ConfirmDialogData } from '../ui'
import { TradingAgentsHistory } from './TradingAgentsHistory'
import { TradingAgentsProgress } from './TradingAgentsProgress'
import { TradingAgentsReport } from './TradingAgentsReport'
import { TradingAgentsRunForm } from './TradingAgentsRunForm'
import { TradingAgentsStopped } from './TradingAgentsStopped'
import { useTaPanel } from './useTaPanel'
import type { TaFocus, TaPanelState, TaTakeView } from './useTaPanel'

const TITLE = 'Research view · TradingAgents'

const DELETE_DIALOG: ConfirmDialogData = {
  title: 'Delete this run?',
  body: 'The report, its cost record and the list of what was shared are removed.',
  confirmLabel: 'Delete run',
  danger: true,
}

interface Unavailable {
  message: string
  action: string | null
}

/** Why a run cannot start, in one line, and the one button that fixes it. */
function unavailable(status: TaStatus): Unavailable | null {
  switch (status.availability.status) {
    case 'ready':
      return status.disclaimer_accepted
        ? null
        : {
            message: 'Read and accept the research-only notice to run your first analysis.',
            action: 'Open setup',
          }
    case 'connector_off':
      return { message: "TradingAgents isn't set up yet.", action: 'Set up' }
    case 'connector_paused':
      return { message: 'The TradingAgents connector is paused.', action: 'Resume' }
    case 'installing':
      return { message: 'Installing TradingAgents…', action: null }
    case 'needs_reinstall':
      return { message: 'TradingAgents needs reinstalling after an update.', action: 'Open setup' }
    case 'model_unavailable':
      return { message: status.availability.message, action: 'Choose models' }
    default:
      return { message: status.availability.message, action: 'Open setup' }
  }
}

function Disclaimer() {
  return (
    <Text size="xs" tone="faint" className="ta-disclaimer">
      Research only. Ratings come from AI models and can be wrong. Not financial advice, never an
      order.
    </Text>
  )
}

function History({ p }: { p: TaPanelState }) {
  const total = p.runs.data?.total ?? 0
  return (
    <TradingAgentsHistory
      runs={p.rows}
      total={total}
      currentId={p.featuredId}
      onSelect={p.pick}
      onShowEarlier={
        p.limit < TA_HISTORY_MAX && total > p.rows.length
          ? () => p.showEarlier(TA_HISTORY_MAX)
          : null
      }
    />
  )
}

/** A finished featured run folds the history behind its "Show full analysis"; a stopped one
 *  keeps it in view. While the run loads there is no history to place yet. */
const stoppedRun = (p: TaPanelState) =>
  !!p.featured && !(p.featured.status === 'done' && p.featured.signal)

/** The featured run: its report, why it stopped, or a placeholder while it loads. */
function Result({ p, takeView }: { p: TaPanelState; takeView: TaTakeView | null }) {
  const run = p.featured
  if (!run) return p.featuredId ? <Skeleton height={36} width="100%" /> : null
  if (run.status === 'done' && run.signal) {
    return (
      <TradingAgentsReport
        run={run}
        view={takeView?.runId === run.id ? takeView.view : null}
        onDelete={p.askDelete}
        open={p.fullOpen}
        onToggle={p.toggleFull}
        history={<History p={p} />}
      />
    )
  }
  return (
    <TradingAgentsStopped
      run={run}
      retrying={p.retrying}
      retryError={p.retryError}
      onRetry={() => void p.retry(run)}
    />
  )
}

function DeleteDialog({ p }: { p: TaPanelState }) {
  const data: ConfirmDialogData | null = p.confirmingDelete
    ? {
        ...DELETE_DIALOG,
        body: (
          <Stack gap="--space-3">
            <span>{DELETE_DIALOG.body}</span>
            {p.deleteError && (
              <div role="alert">
                <Text size="sm" tone="bad">
                  {p.deleteError}
                </Text>
              </div>
            )}
          </Stack>
        ),
        confirmDisabled: p.deleting,
      }
    : null
  return <ConfirmDialog data={data} onCancel={p.closeDelete} onConfirm={() => void p.remove()} />
}

function ActiveBody({ p, queueLength }: { p: TaPanelState; queueLength: number }) {
  if (!p.featured) return <Skeleton height={36} width="100%" />
  return (
    <Stack gap="--space-3">
      <TradingAgentsProgress
        run={p.featured}
        queuedBehind={Math.max(0, queueLength - 1)}
        cancelling={p.cancelling}
        onCancel={() => void p.cancel()}
      />
      {p.cancelError && (
        <div role="alert">
          <Text size="xs" tone="bad">
            {p.cancelError}
          </Text>
        </div>
      )}
    </Stack>
  )
}

function BlockedBody({
  p,
  blocked,
  takeView,
}: {
  p: TaPanelState
  blocked: Unavailable
  takeView: TaTakeView | null
}) {
  const navigate = useNavigate()
  const openSetup = () =>
    void navigate({ to: '/connectors', search: { connector: 'trading_agents' } } as never)
  return (
    <Stack gap="--space-4">
      <Cluster justify="between" gap="--space-4">
        <Text size="sm" tone="faint">
          {blocked.message}
        </Text>
        {blocked.action && (
          <Button variant="secondary" size="sm" onClick={openSetup}>
            {blocked.action}
          </Button>
        )}
      </Cluster>
      {p.featuredId && (
        <>
          <Result p={p} takeView={takeView} />
          {stoppedRun(p) && <History p={p} />}
          <Disclaimer />
        </>
      )}
    </Stack>
  )
}

function ReadyBody({
  p,
  status,
  ticker,
  takeView,
}: {
  p: TaPanelState
  status: TaStatus
  ticker: string
  takeView: TaTakeView | null
}) {
  const composeNew = p.rows.length === 0 || p.composing
  return (
    <Stack gap="--space-4">
      {composeNew ? (
        <TradingAgentsRunForm
          ticker={ticker}
          status={status}
          onStarted={() => {
            p.clearPick()
            p.setComposing(false)
          }}
          onCancel={p.rows.length > 0 ? () => p.setComposing(false) : undefined}
        />
      ) : (
        <Result p={p} takeView={takeView} />
      )}
      {!composeNew && stoppedRun(p) && <History p={p} />}
      <Disclaimer />
    </Stack>
  )
}

/** The card body for one ticker, one state at a time: loading, live run, not available (the last
 *  report stays readable), or the form / last report with its history. */
export function TradingAgentsPanel({
  ticker,
  focus,
  takeView = null,
}: {
  ticker: string
  focus: TaFocus | null
  takeView?: TaTakeView | null
}) {
  const p = useTaPanel(ticker, focus)
  const st = p.status.data
  if (!st && p.status.error) {
    return (
      <Panel title={TITLE}>
        <ErrorState
          title="Couldn't load TradingAgents"
          message="Check the connection and try again."
          onRetry={() => void p.status.refresh()}
        />
      </Panel>
    )
  }
  if (!st || p.runs.initialLoading) {
    return (
      <Panel title={TITLE}>
        <div className="asd-skel-stack" aria-busy="true">
          <Skeleton height={13} width="92%" />
          <Skeleton height={13} width="76%" />
          <Skeleton height={36} width="100%" />
        </div>
      </Panel>
    )
  }
  if (p.latestActive) {
    return (
      <Panel title={TITLE}>
        <ActiveBody p={p} queueLength={st.queue_length} />
      </Panel>
    )
  }
  const blocked = unavailable(st)
  const showNew = !blocked && p.rows.length > 0 && !p.composing
  return (
    <Panel
      title={TITLE}
      actions={
        showNew ? (
          <Button variant="secondary" size="sm" onClick={() => p.setComposing(true)}>
            Run new analysis
          </Button>
        ) : undefined
      }
    >
      {blocked ? (
        <BlockedBody p={p} blocked={blocked} takeView={takeView} />
      ) : (
        <ReadyBody p={p} status={st} ticker={ticker} takeView={takeView} />
      )}
      <DeleteDialog p={p} />
    </Panel>
  )
}
