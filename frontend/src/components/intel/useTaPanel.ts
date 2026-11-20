import { useEffect, useState } from 'react'
import {
  cancelTaRun,
  deleteTaRun,
  startTaRun,
  TaError,
  useTaRun,
  useTaRuns,
  useTaStatus,
} from '../../hooks/useTradingAgents'
import type { TaRun } from '../../types/tradingAgents'
import { TA_ACTIVE_STATUSES } from '../../types/tradingAgentsConstants'

const PAGE = 5

/** The run to open and bring into view, from outside the card (the take's TradingAgents chip).
 *  `seq` makes asking for the same run twice a new request. */
export interface TaFocus {
  runId: string
  seq: number
}

/** What the take said about the run it used: the run's id and the server's one-line reading of
 *  its rating, which the card shows under that run's rating. */
export interface TaTakeView {
  runId: string
  view: string | null
}

// Whether "Show full analysis" is open, per ticker, for this page load only — not stored.
const fullOpenByTicker = new Map<string, boolean>()

/** Test-only: forget which cards were left open. */
export function _resetTaPanelMemoryForTests(): void {
  fullOpenByTicker.clear()
}

const message = (e: unknown, fallback: string) => (e instanceof TaError ? e.message : fallback)

/** What the owner can do to the run in front of them, each action with its own busy flag and
 *  the server's own sentence when it fails. */
function useRunActions(featured: TaRun | undefined, onGone: () => void) {
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // "Cancelling…" holds only while that run is still moving.
  const cancelling =
    !!featured && cancellingId === featured.id && TA_ACTIVE_STATUSES.includes(featured.status)

  async function cancel() {
    if (!featured) return
    setCancellingId(featured.id)
    setCancelError(null)
    try {
      await cancelTaRun(featured.id)
    } catch (e) {
      setCancellingId(null)
      setCancelError(message(e, 'Could not cancel the analysis.'))
    }
  }

  async function retry(of: TaRun) {
    setRetrying(true)
    setRetryError(null)
    try {
      await startTaRun(of.ticker, of.analysts, of.context_level)
      onGone()
    } catch (e) {
      setRetryError(message(e, 'Could not start the analysis.'))
    } finally {
      setRetrying(false)
    }
  }

  async function remove() {
    if (!featured) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteTaRun(featured.id)
      setConfirmingDelete(false)
      onGone()
    } catch (e) {
      setDeleteError(message(e, 'Could not delete the run.'))
    } finally {
      setDeleting(false)
    }
  }

  return {
    cancelling,
    cancelError,
    cancel,
    retrying,
    retryError,
    retry,
    confirmingDelete,
    deleting,
    deleteError,
    remove,
    askDelete: () => {
      setDeleteError(null)
      setConfirmingDelete(true)
    },
    closeDelete: () => setConfirmingDelete(false),
  }
}

/** Everything the asset card shows and does for one ticker: the status, the run list, the
 *  featured run (the live one, else the picked one, else the latest) and its actions. */
export function useTaPanel(ticker: string, focus: TaFocus | null) {
  const status = useTaStatus()
  const [limit, setLimit] = useState(PAGE)
  const runs = useTaRuns(ticker, limit, true)
  const rows = runs.data?.runs ?? []
  const latest = rows[0]
  const latestActive = !!latest && TA_ACTIVE_STATUSES.includes(latest.status)
  const [picked, setPicked] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [fullOpen, setFullOpen] = useState(() => fullOpenByTicker.get(ticker) ?? false)
  const [handledSeq, setHandledSeq] = useState(0)
  if (focus && focus.seq !== handledSeq) {
    setHandledSeq(focus.seq)
    setPicked(focus.runId)
    setComposing(false)
    setFullOpen(true)
  }
  useEffect(() => {
    fullOpenByTicker.set(ticker, fullOpen)
  }, [ticker, fullOpen])
  // A pick that is no longer in the list (a deleted run) falls back to the latest run.
  const pickedId = picked && rows.some((r) => r.id === picked) ? picked : null
  const featuredId = latestActive ? latest.id : (pickedId ?? latest?.id ?? null)
  const run = useTaRun(featuredId)
  const featured: TaRun | undefined = run.data?.id === featuredId ? run.data : undefined
  const actions = useRunActions(featured, () => setPicked(null))
  return {
    status,
    runs,
    rows,
    latestActive,
    featuredId,
    featured,
    limit,
    showEarlier: (max: number) => setLimit(max),
    pick: (id: string) => {
      setPicked(id)
      setComposing(false)
    },
    composing,
    setComposing,
    fullOpen,
    toggleFull: () => setFullOpen((o) => !o),
    clearPick: () => setPicked(null),
    ...actions,
  }
}

export type TaPanelState = ReturnType<typeof useTaPanel>
