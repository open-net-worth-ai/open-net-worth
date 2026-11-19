import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useFlag } from '../../hooks/useFlag'
import {
  cancelTaRun,
  deleteTaRun,
  startTaRun,
  TaError,
  useTaRun,
  useTaRuns,
  useTaStatus,
} from '../../hooks/useTradingAgents'
import { TA_ACTIVE_STATUSES, TA_HISTORY_MAX } from '../../types/tradingAgents'
import type { TaRun, TaStatus } from '../../types/tradingAgents'
import { Button, Cluster, ConfirmDialog, ErrorState, Panel, Skeleton, Stack, Text } from '../ui'
import type { ConfirmDialogData } from '../ui'
import { TradingAgentsHistory } from './TradingAgentsHistory'
import { TradingAgentsProgress } from './TradingAgentsProgress'
import { TradingAgentsReport } from './TradingAgentsReport'
import { TradingAgentsRunForm } from './TradingAgentsRunForm'
import { TradingAgentsStopped } from './TradingAgentsStopped'

const TITLE = 'TradingAgents · research only'
const PAGE = 5

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
    case 'model_unavailable':
      return { message: status.availability.message, action: 'Choose models' }
    default:
      return { message: status.availability.message, action: 'Open setup' }
  }
}

function Disclaimer() {
  return (
    <Text size="xs" tone="faint" className="ta-disclaimer">
      Research only. Ratings come from AI models reading public data and can be wrong. Not financial
      advice, never an order.
    </Text>
  )
}

/** The Analysis-tab card for one equity: ask for a multi-agent research run, watch it, and read
 *  the rating and the reports behind it. Gated by the `trading_agents` flag: off (or not loaded
 *  yet) renders nothing. */
export function TradingAgentsCard({ ticker }: { instrumentId: string; ticker: string }) {
  const enabled = useFlag('trading_agents')
  if (enabled !== true) return null
  return <TradingAgentsPanel ticker={ticker} />
}

function TradingAgentsPanel({ ticker }: { ticker: string }) {
  const navigate = useNavigate()
  const status = useTaStatus()
  const [limit, setLimit] = useState(PAGE)
  const runs = useTaRuns(ticker, limit, true)
  const rows = runs.data?.runs ?? []
  const latest = rows[0]
  const latestActive = !!latest && TA_ACTIVE_STATUSES.includes(latest.status)
  const [picked, setPicked] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const featuredId = latestActive ? latest.id : (picked ?? latest?.id ?? null)
  const run = useTaRun(featuredId)
  const featured: TaRun | undefined = run.data?.id === featuredId ? run.data : undefined
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const [dialog, setDialog] = useState<ConfirmDialogData | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)

  // "Cancelling…" holds only while that run is still moving.
  const cancelling =
    !!featured && cancellingId === featured.id && TA_ACTIVE_STATUSES.includes(featured.status)

  const openSetup = () =>
    void navigate({ to: '/connectors', search: { connector: 'trading_agents' } } as never)

  async function cancel() {
    if (!featured) return
    setCancellingId(featured.id)
    try {
      await cancelTaRun(featured.id)
    } catch {
      setCancellingId(null)
    }
  }

  async function retry(of: TaRun) {
    setRetrying(true)
    setRetryError(null)
    try {
      await startTaRun(of.ticker, of.analysts, of.context_level)
      setPicked(null)
    } catch (e) {
      setRetryError(e instanceof TaError ? e.message : 'Could not start the analysis.')
    } finally {
      setRetrying(false)
    }
  }

  async function remove(id: string) {
    setDialog(null)
    await deleteTaRun(id)
    setPicked(null)
  }

  const st = status.data
  if (!st && status.error) {
    return (
      <Panel title={TITLE}>
        <ErrorState
          title="Couldn't load TradingAgents"
          message="Check the connection and try again."
          onRetry={() => void status.refresh()}
        />
      </Panel>
    )
  }
  if (!st || runs.initialLoading) {
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

  if (latestActive) {
    return (
      <Panel title={TITLE}>
        {featured ? (
          <TradingAgentsProgress
            run={featured}
            queuedBehind={Math.max(0, st.queue_length - 1)}
            cancelling={cancelling}
            onCancel={() => void cancel()}
          />
        ) : (
          <Skeleton height={36} width="100%" />
        )}
      </Panel>
    )
  }

  const blocked = unavailable(st)
  const history = (
    <TradingAgentsHistory
      runs={rows}
      total={runs.data?.total ?? 0}
      currentId={featuredId}
      onSelect={(id) => {
        setPicked(id)
        setComposing(false)
      }}
      onShowEarlier={
        limit < TA_HISTORY_MAX && (runs.data?.total ?? 0) > rows.length
          ? () => setLimit(TA_HISTORY_MAX)
          : null
      }
    />
  )
  const result = featured ? (
    featured.status === 'done' && featured.signal ? (
      <TradingAgentsReport run={featured} onDelete={() => setDialog({ ...DELETE_DIALOG })} />
    ) : (
      <TradingAgentsStopped
        run={featured}
        retrying={retrying}
        retryError={retryError}
        onRetry={() => void retry(featured)}
      />
    )
  ) : featuredId ? (
    <Skeleton height={36} width="100%" />
  ) : null

  const dialogs = (
    <ConfirmDialog
      data={dialog}
      onCancel={() => setDialog(null)}
      onConfirm={() => featured && void remove(featured.id)}
    />
  )

  if (blocked) {
    return (
      <Panel title={TITLE}>
        <Stack gap="--space-5">
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
          {featuredId && (
            <>
              {result}
              {history}
              <Disclaimer />
            </>
          )}
        </Stack>
        {dialogs}
      </Panel>
    )
  }

  const composeNew = rows.length === 0 || composing
  return (
    <Panel
      title={TITLE}
      actions={
        !composeNew ? (
          <Button variant="secondary" size="sm" onClick={() => setComposing(true)}>
            Run new analysis
          </Button>
        ) : undefined
      }
    >
      <Stack gap="--space-5">
        {composeNew ? (
          <TradingAgentsRunForm
            ticker={ticker}
            status={st}
            onStarted={() => {
              setPicked(null)
              setComposing(false)
            }}
            onCancel={rows.length > 0 ? () => setComposing(false) : undefined}
          />
        ) : (
          result
        )}
        {!composeNew && history}
        <Disclaimer />
      </Stack>
      {dialogs}
    </Panel>
  )
}
