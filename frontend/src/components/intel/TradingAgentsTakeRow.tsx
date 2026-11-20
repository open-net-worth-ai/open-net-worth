import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useFlag } from '../../hooks/useFlag'
import {
  cancelTaRun,
  startTaRun,
  TaError,
  useTaContextPreview,
  useTaEstimate,
  useTaRun,
  useTaRuns,
  useTaStatus,
} from '../../hooks/useTradingAgents'
import type { TradingAgentsInput } from '../../types/assetDetail'
import type { TaRunSummary, TaStatus } from '../../types/tradingAgents'
import { TA_ACTIVE_STATUSES } from '../../types/tradingAgentsConstants'
import { fCurrency } from '../../utils/format'
import { fElapsed, runPlanLabel, runProviders, stageRows } from '../../utils/tradingAgents'
import { Button, Loading } from '../ui'
import { TradingAgentsInputChip } from './TradingAgentsInputChip'
import { TradingAgentsShared } from './TradingAgentsShared'

/** A finished run older than this is no longer offered as research for the take. */
const USABLE_DAYS = 14
/** How long "updating this take…" may last before the owner is offered a manual refresh. */
const UPDATING_PATIENCE_MS = 2 * 60 * 1000
const DAY_MS = 86_400_000

const USD = (n: number) => fCurrency(n, 'USD')
const messageOf = (e: unknown, fallback: string) => (e instanceof TaError ? e.message : fallback)

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

/** The newest finished run still worth reading: rated, and no older than `USABLE_DAYS`. */
function usableRun(rows: TaRunSummary[]): TaRunSummary | undefined {
  const now = Date.now()
  return rows.find(
    (r) =>
      r.status === 'done' && r.signal && (now - Date.parse(r.trade_date)) / DAY_MS <= USABLE_DAYS,
  )
}

function useOpenSetup() {
  const navigate = useNavigate()
  return () =>
    void navigate({ to: '/connectors', search: { connector: 'trading_agents' } } as never)
}

/** (a) A run for this ticker is moving: its stage, the clock, Cancel, and a link to the card. */
function ActiveLine({ run, onOpen }: { run: TaRunSummary; onOpen: (runId: string) => void }) {
  const full = useTaRun(run.id)
  const now = useNow()
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const detail = full.data?.id === run.id ? full.data : undefined
  const stage = detail ? stageRows(detail, now).find((s) => s.state === 'active') : undefined
  const started = Date.parse(run.started_at ?? run.created_at)
  const elapsed = Number.isNaN(started) ? 0 : (now - started) / 1000
  const where = run.status === 'queued' ? 'Queued' : (stage?.label ?? 'Starting')

  async function cancel() {
    setCancelling(true)
    setError(null)
    try {
      await cancelTaRun(run.id)
    } catch (e) {
      setCancelling(false)
      setError(messageOf(e, 'Could not cancel the analysis.'))
    }
  }

  return (
    <div className="ta-take-row">
      <span role="status">
        TradingAgents research · {where} · {fElapsed(elapsed)}
      </span>
      <Button variant="ghost" size="sm" disabled={cancelling} onClick={() => void cancel()}>
        {cancelling ? 'Cancelling…' : 'Cancel'}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => onOpen(run.id)}>
        View
      </Button>
      {error && (
        <span className="ta-take-row__error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

/** (c) The run is in and the backend is refreshing the take by itself; a long wait offers a
 *  plain revalidate. */
function UpdatingLine({ runId, onRefresh }: { runId: string; onRefresh: () => void }) {
  const [waitedFor, setWaitedFor] = useState<string | null>(null)
  useEffect(() => {
    const id = setTimeout(() => setWaitedFor(runId), UPDATING_PATIENCE_MS)
    return () => clearTimeout(id)
  }, [runId])
  return (
    <div className="ta-take-row">
      <span role="status">TradingAgents result ready — updating this take…</span>
      {waitedFor === runId && (
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          Refresh this take
        </Button>
      )}
    </div>
  )
}

/** Why a run cannot start from here, in one quiet line. */
function setupMessage(status: TaStatus): { text: string; link: boolean } | null {
  const { status: s, message } = status.availability
  if (s === 'ready') {
    return status.disclaimer_accepted
      ? null
      : { text: 'Accept the research-only notice to use TradingAgents.', link: true }
  }
  if (s === 'installing') return { text: 'Installing TradingAgents…', link: false }
  if (s === 'connector_off') return { text: "TradingAgents isn't set up yet.", link: true }
  if (s === 'connector_paused')
    return { text: 'The TradingAgents connector is paused.', link: true }
  if (s === 'not_installed') return { text: "TradingAgents isn't installed.", link: true }
  if (s === 'needs_reinstall') {
    return { text: 'TradingAgents needs reinstalling after an update.', link: true }
  }
  return { text: message, link: true }
}

/** (e) TradingAgents cannot run yet. */
function SetupLine({ text, link }: { text: string; link: boolean }) {
  const openSetup = useOpenSetup()
  return (
    <div className="ta-take-row">
      <span>{text}</span>
      {link && (
        <Button variant="ghost" size="sm" onClick={openSetup}>
          Set up TradingAgents
        </Button>
      )}
    </div>
  )
}

/** (d) Nothing usable yet: one button, what it costs, what it shares. The run starts only on the
 *  click, with the configured analysts and context level. */
function OfferLine({ ticker, status }: { ticker: string; status: TaStatus }) {
  const [starting, setStarting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [showShared, setShowShared] = useState(false)
  const estimate = useTaEstimate(ticker, status.analysts, true)
  const preview = useTaContextPreview(ticker, status.context_level, showShared)
  const plan = status.models ? runPlanLabel({ transports: status.models.transports }) : null
  const providers = status.models ? runProviders(status.models) : []
  const cost = estimate.data
  const hint = !cost
    ? null
    : cost.plan_included
      ? `uses your ${plan ?? 'subscription'}`
      : cost.priced
        ? `about ${USD(cost.estimate_usd)}`
        : 'cost unknown'

  async function start() {
    setStarting(true)
    setRefusal(null)
    try {
      await startTaRun(ticker, status.analysts, status.context_level)
    } catch (e) {
      setRefusal(messageOf(e, 'Could not start the analysis.'))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="ta-take-row">
      <Button variant="secondary" size="sm" loading={starting} onClick={() => void start()}>
        Add TradingAgents research
      </Button>
      {hint && <span>{hint}</span>}
      <details
        className="ta-take-row__shared"
        onToggle={(e) => setShowShared((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary>What&apos;s shared</summary>
        {preview.data ? (
          <TradingAgentsShared context={preview.data} providers={providers} />
        ) : (
          <Loading label="Reading your holdings…" />
        )}
      </details>
      {refusal && (
        <span className="ta-take-row__error" role="alert">
          {refusal}
        </span>
      )}
    </div>
  )
}

function TakeRowBody({
  ticker,
  input,
  onOpen,
  onRefresh,
}: {
  ticker: string
  input: TradingAgentsInput | null
  onOpen: (runId: string) => void
  onRefresh: () => void
}) {
  const status = useTaStatus()
  const runs = useTaRuns(ticker, 5, true)
  const st = status.data
  const rows = runs.data?.runs ?? []
  if (!st || !runs.data) return null
  const latest = rows[0]
  if (latest && TA_ACTIVE_STATUSES.includes(latest.status)) {
    return <ActiveLine run={latest} onOpen={onOpen} />
  }
  const usable = usableRun(rows)
  if (input && (!usable || usable.id === input.run_id)) {
    return <TradingAgentsInputChip input={input} onOpen={onOpen} />
  }
  if (usable) return <UpdatingLine runId={usable.id} onRefresh={onRefresh} />
  const setup = setupMessage(st)
  if (setup) return <SetupLine {...setup} />
  return <OfferLine ticker={ticker} status={st} />
}

/** The take's TradingAgents row, one line under the take: the research the take used, or the way
 *  to add it. One state at a time (live run, in use, updating, offer, set up). Hidden while the
 *  `trading_agents` flag is off; the caller shows it only for an equity with a ticker. */
export function TradingAgentsTakeRow(props: {
  ticker: string
  input: TradingAgentsInput | null
  onOpen: (runId: string) => void
  onRefresh: () => void
}) {
  const enabled = useFlag('trading_agents')
  if (enabled !== true) return null
  return <TakeRowBody {...props} />
}
