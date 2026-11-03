import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AlertCircle, X } from 'lucide-react'
import { desktopClientBridge } from '../../../platform/desktop'
import { useOpenVoiceSetup, useVoiceReadiness } from './VoiceCallButton'
import { dismissProblem, startCall, useVoiceCall } from './voiceCall'

// Why a call didn't start or stopped (`voice_calls` flag): the TOP ROW of the composer card, so it
// has the box's own width and corners (owner, 2026-11-03: inline in the toolbar was wrong, a strip
// wider than the box was weird). Plain words, the next step where there is one, a link to the
// app's logs for a worker problem, and always dismissable.

const LIVEKIT_CLOUD = 'https://cloud.livekit.io'

export function VoiceCallNotice({
  sessionId,
  busy = false,
}: {
  sessionId: string
  /** Lucius is answering something: a call's problem has been moved on from. */
  busy?: boolean
}) {
  const call = useVoiceCall()
  const readiness = useVoiceReadiness()
  const openSetup = useOpenVoiceSetup()
  const navigate = useNavigate()
  const [dismissedReason, setDismissedReason] = useState<string | null>(null)
  // LiveKit's raw reason, folded away: the plain message stays the thing you read.
  const [detailOpen, setDetailOpen] = useState(false)
  const problem = call.problem
  // Once you've moved on — sent Lucius a message — the call's problem is old news: it went on
  // showing above an unrelated chat (owner, 2026-11-03). No call is on when there is a problem,
  // so this turn is always yours.
  const hasProblem = problem !== null
  useEffect(() => {
    if (busy && hasProblem) dismissProblem()
  }, [busy, hasProblem])
  // Set up, but the worker can't take calls right now — say so before anyone presses Call.
  const workerReason =
    readiness?.ready && !readiness.worker.running
      ? readiness.worker.error || 'Voice calls are set up but not ready to take a call yet.'
      : null
  const showWorker = !problem && workerReason && workerReason !== dismissedReason
  if (!problem && !showWorker) return null

  const message = problem?.message ?? workerReason
  const dismiss = () => (problem ? dismissProblem() : setDismissedReason(workerReason))
  const micSettings = desktopClientBridge()?.openMicrophoneSettings
  const openLogs = () => void navigate({ to: '/settings', search: { sub: 'developer' } } as never)
  return (
    <div className="lx-call-notice" role="alert">
      <AlertCircle size={14} aria-hidden="true" />
      <span className="lx-call-notice-text">{message}</span>
      <button type="button" className="lx-call-notice-close" aria-label="Dismiss" onClick={dismiss}>
        <X size={14} />
      </button>
      {/* The ways out sit on a row of their own under the words: beside them, a long reason was
          squeezed into a narrow column on a phone (owner, 2026-11-03). */}
      <div className="lx-call-notice-actions">
        {problem?.kind === 'setup' && (
          <button type="button" className="lx-call-notice-action" onClick={openSetup}>
            Voice calls settings
          </button>
        )}
        {problem?.kind === 'mic' && micSettings && (
          // macOS never asks twice: a refused microphone is undone only in System Settings.
          <button
            type="button"
            className="lx-call-notice-action"
            onClick={() => void micSettings()}
          >
            Open System Settings
          </button>
        )}
        {problem?.kind === 'service' && (
          // Out of credit is fixed in the owner's LiveKit project, not here: take them there.
          <a
            className="lx-call-notice-link"
            href={LIVEKIT_CLOUD}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open LiveKit
          </a>
        )}
        {problem && problem.kind !== 'insecure' ? (
          <button
            type="button"
            className="lx-call-notice-action"
            onClick={() => void startCall(sessionId)}
          >
            {problem.kind === 'dropped' ? 'Call again' : 'Try again'}
          </button>
        ) : showWorker ? (
          <button type="button" className="lx-call-notice-action" onClick={openLogs}>
            View logs
          </button>
        ) : null}
        {problem?.detail && (
          <button
            type="button"
            className="lx-call-notice-action"
            aria-expanded={detailOpen}
            onClick={() => setDetailOpen((v) => !v)}
          >
            {detailOpen ? 'Hide details' : 'Details'}
          </button>
        )}
      </div>
      {detailOpen && problem?.detail && <p className="lx-call-notice-detail">{problem.detail}</p>}
    </div>
  )
}
