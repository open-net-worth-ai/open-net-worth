import { createPortal } from 'react-dom'
import { useFlag } from '../../../hooks/useFlag'
import { useHasAi } from '../../../hooks/useHasAi'
import { readActiveId } from '../../../hooks/useConversations'
import { VoiceCallButton } from './VoiceCallButton'
import { VoiceCallNotice } from './VoiceCallNotice'
import { VoiceCallStage } from './VoiceCallStage'
import { VoiceOrb } from './VoiceOrb'
import { expandCall, minimizeCall, useVoiceCall } from './voiceCall'
import { callStatus, orbState, useCallClock } from './voiceCallStatus'

// A phone's call away from the Lucius tab (`voice_calls` flag). The chat is only mounted on that
// tab, so anywhere else a live call had nothing on screen: no status and no way to hang up (owner,
// 2026-11-05). Not the desktop's wide pill, which would take too much of a phone screen: a round
// phone with no call on, and during one a small pill with the orb and the clock, or what is going
// on instead: trouble turns the orb amber and says so. Tapping it opens the full call screen in place.

function ActiveCallPill() {
  const call = useVoiceCall()
  const now = useCallClock(call)
  const status = callStatus(call, now, call.luciusWorking)
  return (
    <button type="button" className="lx-call-fab" aria-label="Open call" onClick={expandCall}>
      <VoiceOrb state={orbState(call, call.luciusWorking)} muted={call.muted} />
      <span className="lx-call-fab-text" role="status">
        {status.short}
      </span>
    </button>
  )
}

export function VoiceCallFab({
  onLuciusTab,
  onOpenChat,
}: {
  /** That tab holds the call itself, and its own Call button. */
  onLuciusTab: boolean
  onOpenChat: () => void
}) {
  const on = useFlag('voice_calls')
  const call = useVoiceCall()
  const { hasAi } = useHasAi()
  if (on !== true || onLuciusTab) return null

  if (call.phase !== 'idle' && call.view === 'stage') {
    return createPortal(
      <VoiceCallStage
        variant="screen"
        busy={call.luciusWorking}
        onType={() => {
          minimizeCall()
          onOpenChat()
        }}
      />,
      document.body,
    )
  }
  const sessionId = call.sessionId ?? readActiveId()
  return (
    <div className="lx-call-float mb-anchored">
      <VoiceCallNotice sessionId={sessionId} onOpenChat={onOpenChat} everyCall />
      {call.phase !== 'idle' ? (
        <ActiveCallPill />
      ) : (
        hasAi && <VoiceCallButton sessionId={sessionId} tone="fab" />
      )}
    </div>
  )
}
