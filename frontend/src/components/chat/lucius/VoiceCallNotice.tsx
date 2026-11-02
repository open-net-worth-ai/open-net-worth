import { AlertCircle, X } from 'lucide-react'
import { Button } from '../../ui'
import { useOpenVoiceSetup, useVoiceReadiness } from './VoiceCallButton'
import { dismissProblem, startCall, useVoiceCall } from './voiceCall'

// Why a call didn't start or stopped (`voice_calls` flag) — a strip just above the composer, in
// normal flow so it pushes the chat up rather than covering the toolbar (owner: errors are fine,
// inline in the toolbar is not). It names the real reason and, where there is one, the next step,
// and stays until dismissed or the next call starts: a genuine error is never auto-hidden.

export function VoiceCallNotice({ sessionId }: { sessionId: string }) {
  const call = useVoiceCall()
  const readiness = useVoiceReadiness()
  const openSetup = useOpenVoiceSetup()
  const problem = call.problem
  // Set up, but the worker can't take calls — say so before anyone presses Call.
  const workerReason =
    readiness?.ready && !readiness.worker.running
      ? readiness.worker.error ||
        'Voice calls are set up but not taking calls yet. Check Connectors → Voice calls.'
      : null
  if (!problem && !workerReason) return null

  const message = problem?.message ?? workerReason
  const action =
    problem?.kind === 'mic' || problem?.kind === 'dropped' || problem?.kind === 'other' ? (
      <Button variant="ghost" size="dense" onClick={() => void startCall(sessionId)}>
        {problem.kind === 'dropped' ? 'Call again' : 'Try again'}
      </Button>
    ) : problem?.kind === 'insecure' ? null : (
      <Button variant="ghost" size="dense" onClick={openSetup}>
        Open Voice calls setup
      </Button>
    )
  return (
    <div className="lx-call-notice" role="alert">
      <AlertCircle size={14} aria-hidden="true" />
      <span className="lx-call-notice-text">{message}</span>
      {action}
      {problem && (
        <button
          type="button"
          className="lx-call-notice-close"
          aria-label="Dismiss"
          onClick={dismissProblem}
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}
