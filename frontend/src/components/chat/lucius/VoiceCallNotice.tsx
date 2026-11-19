import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { desktopClientBridge } from '../../../platform/desktop'
import { useOpenVoiceSetup } from './VoiceCallButton'
import { dismissProblem, startCall, useVoiceCall } from './voiceCall'

// Why a call didn't start or stopped (`voice_calls` flag): the TOP ROW of the composer card, so it
// has the box's own width and corners (owner, 2026-11-03: inline in the toolbar was wrong, a strip
// wider than the box was weird). Plain words, the next step where there is one, and always
// dismissable. Only ever about a call you tried: a worker that is down says so when you press Call,
// not over a chat you're typing in, and pressing Call or Try again is what retries it (owner,
// 2026-11-05).

const LIVEKIT_CLOUD = 'https://cloud.livekit.io'

export function VoiceCallNotice({
  sessionId,
  busy = false,
  onNewChat,
  onOpenChat,
  everyCall = false,
}: {
  sessionId: string
  /** Away from the chat this is the only place a call's trouble can show, so it shows whichever
   *  conversation the call was for: matched against the one last open, a refusal could go unsaid
   *  (owner's phone, 2026-11-13). */
  everyCall?: boolean
  /** Lucius is answering something: a call's problem has been moved on from. */
  busy?: boolean
  /** Starts a new conversation, for a call this one can't take. */
  onNewChat?: () => void
  /** Away from the chat (a phone's other tabs) there's no conversation to replace: go to it. */
  onOpenChat?: () => void
}) {
  const call = useVoiceCall()
  const openSetup = useOpenVoiceSetup()
  // LiveKit's raw reason, folded away: the plain message stays the thing you read.
  const [detailOpen, setDetailOpen] = useState(false)
  // A call's problem stays with its conversation: it isn't about the one you've moved to.
  const problem =
    !everyCall && call.problem?.sessionId && call.problem.sessionId !== sessionId
      ? null
      : call.problem
  // Once you've moved on — sent Lucius a message — the call's problem is old news: it went on
  // showing above an unrelated chat (owner, 2026-11-03). No call is on when there is a problem,
  // so this turn is always yours.
  const hasProblem = problem !== null
  useEffect(() => {
    if (busy && hasProblem) dismissProblem()
  }, [busy, hasProblem])
  if (!problem) return null

  const micSettings = desktopClientBridge()?.openMicrophoneSettings
  return (
    <div className="lx-call-notice" role="alert" data-ready={problem.kind === 'ready' || undefined}>
      {problem.kind === 'ready' ? (
        <CheckCircle2 size={14} aria-hidden="true" />
      ) : (
        <AlertCircle size={14} aria-hidden="true" />
      )}
      <span className="lx-call-notice-text">{problem.message}</span>
      <button
        type="button"
        className="lx-call-notice-close"
        aria-label="Dismiss"
        onClick={dismissProblem}
      >
        <X size={14} />
      </button>
      {/* The ways out sit on a row of their own under the words: beside them, a long reason was
          squeezed into a narrow column on a phone (owner, 2026-11-03). */}
      <div className="lx-call-notice-actions">
        {problem.kind === 'setup' && (
          <button type="button" className="lx-call-notice-action" onClick={openSetup}>
            Voice calls settings
          </button>
        )}
        {problem.kind === 'mic' && micSettings && (
          // macOS never asks twice: a refused microphone is undone only in System Settings.
          <button
            type="button"
            className="lx-call-notice-action"
            onClick={() => void micSettings()}
          >
            Open System Settings
          </button>
        )}
        {problem.kind === 'service' && (
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
        {problem.kind === 'conversation' && onNewChat && (
          <button
            type="button"
            className="lx-call-notice-action"
            onClick={() => {
              dismissProblem()
              onNewChat()
            }}
          >
            Start a new chat
          </button>
        )}
        {problem.kind === 'conversation' && onOpenChat && (
          <button type="button" className="lx-call-notice-action" onClick={onOpenChat}>
            Open Lucius
          </button>
        )}
        {problem.kind !== 'insecure' && problem.kind !== 'conversation' && (
          <button
            type="button"
            className="lx-call-notice-action"
            onClick={() => void startCall(problem.sessionId ?? sessionId)}
          >
            {problem.kind === 'dropped'
              ? 'Call again'
              : problem.kind === 'ready'
                ? 'Call'
                : 'Try again'}
          </button>
        )}
        {problem.detail && (
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
      {detailOpen && problem.detail && <p className="lx-call-notice-detail">{problem.detail}</p>}
    </div>
  )
}
