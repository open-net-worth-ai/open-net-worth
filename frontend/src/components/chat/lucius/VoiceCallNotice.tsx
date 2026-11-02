import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AlertCircle, X } from 'lucide-react'
import { Button } from '../../ui'
import { useOpenVoiceSetup, useVoiceReadiness } from './VoiceCallButton'
import { dismissProblem, startCall, useVoiceCall } from './voiceCall'

// Why a call didn't start or stopped (`voice_calls` flag): the TOP ROW of the composer card, so it
// has the box's own width and corners (owner, 2026-11-03: inline in the toolbar was wrong, a strip
// wider than the box was weird). Plain words, the next step where there is one, a link to the
// app's logs for a worker problem, and always dismissable.

export function VoiceCallNotice({ sessionId }: { sessionId: string }) {
  const call = useVoiceCall()
  const readiness = useVoiceReadiness()
  const openSetup = useOpenVoiceSetup()
  const navigate = useNavigate()
  const [dismissedReason, setDismissedReason] = useState<string | null>(null)
  const problem = call.problem
  // Set up, but the worker can't take calls right now — say so before anyone presses Call.
  const workerReason =
    readiness?.ready && !readiness.worker.running
      ? readiness.worker.error || 'Voice calls are set up but not ready to take a call yet.'
      : null
  const showWorker = !problem && workerReason && workerReason !== dismissedReason
  if (!problem && !showWorker) return null

  const message = problem?.message ?? workerReason
  const dismiss = () => (problem ? dismissProblem() : setDismissedReason(workerReason))
  const openLogs = () => void navigate({ to: '/settings', search: { sub: 'developer' } } as never)
  return (
    <div className="lx-call-notice" role="alert">
      <AlertCircle size={14} aria-hidden="true" />
      <span className="lx-call-notice-text">{message}</span>
      {problem?.kind === 'mic' || problem?.kind === 'dropped' || problem?.kind === 'other' ? (
        <Button variant="ghost" size="dense" onClick={() => void startCall(sessionId)}>
          {problem.kind === 'dropped' ? 'Call again' : 'Try again'}
        </Button>
      ) : problem?.kind === 'setup' ? (
        <Button variant="ghost" size="dense" onClick={openSetup}>
          Voice calls settings
        </Button>
      ) : showWorker ? (
        <Button variant="ghost" size="dense" onClick={openLogs}>
          View logs
        </Button>
      ) : null}
      <button type="button" className="lx-call-notice-close" aria-label="Dismiss" onClick={dismiss}>
        <X size={14} />
      </button>
    </div>
  )
}
