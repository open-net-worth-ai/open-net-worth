// BackgroundStatementNotices — the in-app popup for a statement read that finished while the
// owner was elsewhere (owner, 2026-10-06: "notification when a background statement read
// finishes … at least in app as a popup").
//
// Mounted once, app-wide (TransactionSurfacesProvider), so it works from any tab. It polls the
// existing `GET /api/statements/{id}/parse-status` for every statement in the
// backgroundStatements store — only while the page is visible — and, when a read reaches a
// terminal state, stops tracking it and shows one popup per statement until dismissed. "Review"
// hands the statement to the Accounts tab (requestStatementReview) and asks the shell to go there.
// A read that has just been sent to the background shows here too, briefly, as "Reading in the
// background" — the same pill in the same place, so it turns into "Ready to review" instead of a
// second toast stacking up beside it (owner, 2026-10-25).

import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { useDocumentVisible } from '../../../hooks/useImportProgress'
import { X } from '../../../ui/icons'
import {
  backgroundStatements,
  requestStatementReview,
  subscribeBackgroundStatements,
  untrackBackgroundStatement,
  type BackgroundStatement,
} from './backgroundStatements'

type Finished = BackgroundStatement & { status: 'ready' | 'failed' | 'interrupted' }

const POLL_MS = 4000
const READING_MS = 5000
const TERMINAL = new Set(['ready', 'failed', 'interrupted'])

export function BackgroundStatementNotices({ onOpenAccounts }: { onOpenAccounts?: () => void }) {
  const [tracked, setTracked] = useState(backgroundStatements)
  const [finished, setFinished] = useState<Finished[]>([])
  const [hiddenReading, setHiddenReading] = useState<string[]>([])
  const [now, setNow] = useState(() => Date.now())
  const visible = useDocumentVisible()

  const reading = tracked.filter(
    (s) =>
      s.startedAt !== undefined &&
      now - s.startedAt < READING_MS &&
      !hiddenReading.includes(s.id) &&
      !finished.some((f) => f.id === s.id),
  )
  const nextExpiry = reading.length
    ? Math.min(...reading.map((s) => (s.startedAt as number) + READING_MS))
    : null
  useEffect(() => {
    if (nextExpiry === null) return
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, nextExpiry - Date.now()))
    return () => window.clearTimeout(timer)
  }, [nextExpiry])

  useEffect(
    () =>
      subscribeBackgroundStatements(() => {
        setTracked(backgroundStatements())
        setNow(Date.now())
      }),
    [],
  )

  useEffect(() => {
    if (!tracked.length || !visible) return
    let cancelled = false
    const poll = async () => {
      for (const s of tracked) {
        try {
          const r = await authFetch(`/api/statements/${s.id}/parse-status`)
          if (r.status === 404) {
            untrackBackgroundStatement(s.id) // the statement was deleted meanwhile
            continue
          }
          if (!r.ok) continue
          const body = (await r.json()) as { parse_status?: string | null }
          const status = body.parse_status
          if (!status || !TERMINAL.has(status)) continue
          untrackBackgroundStatement(s.id)
          if (cancelled) return
          setFinished((list) => [
            ...list.filter((f) => f.id !== s.id),
            { ...s, status: status as Finished['status'] },
          ])
        } catch {
          /* offline / transient — the next tick retries */
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [tracked, visible])

  if (!finished.length && !reading.length) return null
  const dismiss = (id: string) => setFinished((list) => list.filter((f) => f.id !== id))
  return (
    <div className="bgst-stack mb-anchored" role="status" aria-live="polite">
      {reading.map((r) => (
        <div key={r.id} className="bgst-note">
          <span className="bgst-text">
            <span className="bgst-title">Reading in the background</span>
            <span className="bgst-detail">{r.filename || 'your statement'}</span>
          </span>
          <button
            type="button"
            className="bgst-x"
            aria-label="Dismiss"
            onClick={() => setHiddenReading((ids) => [...ids, r.id])}
          >
            <X size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        </div>
      ))}
      {finished.map((f) => {
        // The app's own toast pill (acct-toast / as-toast / ov-toast): one line, no card, no
        // colour bar — the state is in the words.
        const title =
          f.status === 'ready'
            ? 'Ready to review'
            : f.status === 'failed'
              ? 'Couldn’t read'
              : 'Reading stopped'
        return (
          <div key={f.id} className="bgst-note">
            <span className="bgst-text">
              <span className="bgst-title">{title}</span>
              <span className="bgst-detail">{f.filename || 'your statement'}</span>
            </span>
            {f.status === 'ready' && (
              <button
                type="button"
                className="bgst-review"
                onClick={() => {
                  requestStatementReview({
                    id: f.id,
                    accountId: f.accountId,
                    filename: f.filename,
                    startedAt: f.startedAt,
                  })
                  onOpenAccounts?.()
                  dismiss(f.id)
                }}
              >
                Review
              </button>
            )}
            <button
              type="button"
              className="bgst-x"
              aria-label="Dismiss"
              onClick={() => dismiss(f.id)}
            >
              <X size={14} strokeWidth={2} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
