// useImportProgress — polls the live stage of a statement preview in flight (B20).
//
// The POST to /api/statements/preview (or its csv/preview alias) stays a synchronous call
// that can take several minutes for a scanned statement; this is a SEPARATE read the modal
// polls alongside it, keyed by a token it mints itself and sends along on the same request —
// same shape as the MCP add-server flow's `fetchProbeProgress`/`newProbeToken`
// (components/tabs/connectors/mcpApi.ts), reused here rather than re-implemented.
//
// A poll failure is never treated as the preview failing — the POST promise is the only
// thing that can fail this modal. A miss just means `stale` goes true and the next poll
// backs off (2s, 5s, 10s) instead of hammering a server that isn't answering; the local
// elapsed clock keeps ticking either way and resyncs from the server's own `elapsed_s` the
// moment a poll lands again.

import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'
import { newProbeToken } from '../components/tabs/connectors/mcpApi'
import { mapPreviewProgress, type PreviewProgress } from '../components/tabs/accounts/importHelpers'

export const newImportProgressToken = newProbeToken

const NORMAL_INTERVAL_MS = 1000
const FAILURE_BACKOFF_MS = [2000, 5000, 10000]

export async function fetchPreviewProgress(token: string): Promise<PreviewProgress | null> {
  try {
    const res = await authFetch(`/api/statements/preview-progress/${encodeURIComponent(token)}`, {
      cache: 'no-store',
    })
    if (!res.ok) return null
    return mapPreviewProgress(await res.json())
  } catch {
    return null
  }
}

export interface ImportProgressState {
  /** The last snapshot a poll actually delivered — null before the first one lands. */
  progress: PreviewProgress | null
  /** A poll is currently failing (network blip, or the server lost the token) — the caller
   *  shows "still working, losing touch" rather than treating it as the preview erroring. */
  stale: boolean
  /** Ticks every second on the client's own clock; resynced from `progress.elapsed_s`
   *  whenever a poll succeeds, so it never drifts far even through a stale patch. */
  elapsedS: number
}

/** Poll `token`'s progress once a second while `active`. Inert (returns the empty state,
 *  polls nothing) when `token` is null — the caller passes null whenever there's nothing to
 *  track (an old-server fallback, or outside the processing step). */
export function usePreviewProgress(token: string | null, active: boolean): ImportProgressState {
  const [progress, setProgress] = useState<PreviewProgress | null>(null)
  const [stale, setStale] = useState(false)
  const [elapsedS, setElapsedS] = useState(0)
  // Reset synchronously the moment `token`/`active` changes — react.dev's own "adjusting
  // state when a prop changes without an Effect" pattern, so a new preview never flashes the
  // PREVIOUS one's stage/elapsed for a frame before the polling effect below gets to run.
  const [trackedKey, setTrackedKey] = useState('')
  const key = `${token ?? ''}:${active}`
  if (key !== trackedKey) {
    setTrackedKey(key)
    setProgress(null)
    setStale(false)
    setElapsedS(0)
  }

  useEffect(() => {
    if (!active || !token) return
    let cancelled = false
    let failures = 0
    let timer: ReturnType<typeof setTimeout> | null = null
    async function poll() {
      const snap = await fetchPreviewProgress(token as string)
      if (cancelled) return
      let delay = NORMAL_INTERVAL_MS
      if (snap) {
        setProgress(snap)
        setElapsedS(snap.elapsed_s)
        setStale(false)
        failures = 0
      } else {
        setStale(true)
        delay = FAILURE_BACKOFF_MS[Math.min(failures, FAILURE_BACKOFF_MS.length - 1)] ?? 10_000
        failures += 1
      }
      if (snap?.done) return
      timer = setTimeout(poll, delay)
    }
    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [token, active])

  useEffect(() => {
    if (!active || !token) return
    const id = setInterval(() => setElapsedS((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [token, active])

  return { progress, stale, elapsedS }
}

// ── Async mode (B20 phase 2): a statement parsing/parsed server-side, tracked by its own id
// rather than a client-minted token — the statements list's lifecycle rows, and reopening a
// statement's review by id. ────────────────────────────────────────────────────────────────

export interface ParseStatusSnapshot {
  parseStatus: string | null
  progress: PreviewProgress | null
  error: string | null
}

export async function fetchParseStatus(statementId: string): Promise<ParseStatusSnapshot | null> {
  try {
    const res = await authFetch(`/api/statements/${encodeURIComponent(statementId)}/parse-status`, {
      cache: 'no-store',
    })
    if (!res.ok) return null
    const body = (await res.json()) as Record<string, unknown>
    return {
      parseStatus: typeof body.parse_status === 'string' ? body.parse_status : null,
      progress: mapPreviewProgress(body.progress),
      error: typeof body.error === 'string' ? body.error : null,
    }
  } catch {
    return null
  }
}

export interface StatementParseStatusState {
  snapshot: ParseStatusSnapshot | null
  stale: boolean
  elapsedS: number
}

/** Poll `statementId`'s durable parse status — the async-mode twin of `usePreviewProgress`,
 *  keyed by the STATEMENT's own id (never a client token) so it works across a page reload
 *  or from a different tab. Stops polling once the status is no longer `parsing` — nothing
 *  further will ever change. `intervalMs` lets the statements list poll gently (4s per row)
 *  while a reopened modal polls tightly (1s, matching the live sync experience). */
export function useStatementParseStatus(
  statementId: string | null,
  active: boolean,
  intervalMs: number = 1000,
  /** Bump to poll again after it stopped on a terminal status — a caller told the read is
   *  still running (a reopen that attached to the live job) resumes watching it. */
  epoch: number = 0,
): StatementParseStatusState {
  const [snapshot, setSnapshot] = useState<ParseStatusSnapshot | null>(null)
  const [stale, setStale] = useState(false)
  const [elapsedS, setElapsedS] = useState(0)
  const [trackedKey, setTrackedKey] = useState('')
  const key = `${statementId ?? ''}:${active}:${epoch}`
  if (key !== trackedKey) {
    setTrackedKey(key)
    setSnapshot(null)
    setStale(false)
    setElapsedS(0)
  }

  useEffect(() => {
    if (!active || !statementId) return
    let cancelled = false
    let failures = 0
    let timer: ReturnType<typeof setTimeout> | null = null
    async function poll() {
      const snap = await fetchParseStatus(statementId as string)
      if (cancelled) return
      let delay = intervalMs
      if (snap) {
        setSnapshot(snap)
        if (snap.progress) setElapsedS(snap.progress.elapsed_s)
        setStale(false)
        failures = 0
      } else {
        setStale(true)
        delay = FAILURE_BACKOFF_MS[Math.min(failures, FAILURE_BACKOFF_MS.length - 1)] ?? 10_000
        failures += 1
      }
      if (snap && snap.parseStatus !== 'parsing') return
      timer = setTimeout(poll, delay)
    }
    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [statementId, active, intervalMs, epoch])

  useEffect(() => {
    if (!active || !statementId) return
    const id = setInterval(() => setElapsedS((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [statementId, active])

  return { snapshot, stale, elapsedS }
}

/** Pauses a poll while the tab is hidden (the statements list's own requirement) — a small,
 *  reusable visibility gate rather than each caller wiring its own listener. */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}
