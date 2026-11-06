import { useEffect, useRef } from 'react'
import { ChevronDown, Keyboard, Maximize2, Mic, MicOff, PhoneOff } from 'lucide-react'
import { expandCall, hangUp, minimizeCall, toggleMute, useVoiceCall } from './voiceCall'
import { callSignal, callStatus, orbState, useCallClock } from './voiceCallStatus'
import { VoiceCallAttach } from './VoiceCallAttach'
import { VoiceCallGlow } from './VoiceCallGlow'
import { VoiceCallHeard } from './VoiceCallHeard'
import { VoiceCallMicLight } from './VoiceCallMicLight'
import { VoiceCallModel } from './VoiceCallModel'
import { VoiceCallSettings } from './VoiceCallSettings'
import { VoiceCallSignal } from './VoiceCallSignal'
import { VoiceOrb } from './VoiceOrb'
import { useSwipeToMinimize } from './useSwipeToMinimize'

// The call's view (`voice_calls` flag). `screen` is the phone's full-screen call; `card` is the
// call strip above the composer — the ONLY form on a computer, and the phone's call folded down
// (`expandable`: its button opens the full screen again). One look for a call everywhere: a
// second, minimised bar with its own buttons read as a different thing (owner, 2026-11-03).
// Folding the phone's screen (the arrow, Type, Esc) never hangs up. Each fact shows once:
// the clock at the top only while connected, the phase ("Ringing…") only under the orb. A wide
// card lays out as one strip — orb, status, controls — not a tall box of empty space (owner,
// 2026-11-03).

export function VoiceCallStage({
  variant,
  busy,
  expandable = false,
  onType = minimizeCall,
}: {
  variant: 'screen' | 'card'
  busy: boolean
  expandable?: boolean
  /** Away from the chat there is no message box to fold down to. */
  onType?: () => void
}) {
  const call = useVoiceCall()
  const now = useCallClock(call)
  const status = callStatus(call, now, busy)
  const ended = call.phase === 'ended'
  const top = ended ? status.text : status.time
  // Something is under way (ringing, reconnecting, Lucius working): the words shimmer.
  const waiting =
    call.phase === 'connecting' ||
    call.phase === 'ringing' ||
    call.phase === 'reconnecting' ||
    (call.phase === 'live' && (busy || call.luciusTrouble || call.offline))
  const signal = callSignal(call)
  const root = useRef<HTMLDivElement>(null)
  const screen = variant === 'screen'

  useSwipeToMinimize(root, minimizeCall, screen && !ended)

  useEffect(() => {
    if (screen) root.current?.focus()
  }, [screen])

  const dialog = screen
    ? { role: 'dialog', 'aria-modal': true, 'aria-label': 'Voice call with Lucius', tabIndex: -1 }
    : { role: 'group', 'aria-label': 'Voice call' }
  return (
    <div
      ref={root}
      className={`lx-callstage lx-callstage--${variant}`}
      {...dialog}
      onKeyDown={(e) => {
        if (screen && e.key === 'Escape' && !ended) {
          e.stopPropagation()
          minimizeCall()
        }
      }}
    >
      {screen && <VoiceCallGlow live={call.phase === 'live'} />}
      <div className="lx-callstage-inner">
        <div className="lx-callstage-top">
          {screen && (
            <button
              type="button"
              className="lx-callstage-min"
              data-ended={ended || undefined}
              tabIndex={ended ? -1 : undefined}
              aria-label="Minimise call"
              title="Minimise"
              onClick={minimizeCall}
            >
              <ChevronDown size={22} />
            </button>
          )}
          <span className="lx-callstage-when">
            {signal && <VoiceCallSignal signal={signal} />}
            <span className="lx-callstage-clock" role="timer" aria-label="Call duration">
              {top}
            </span>
          </span>
          {screen && <VoiceCallSettings ended={ended} />}
          {expandable && (
            <button
              type="button"
              className="lx-callstage-min"
              data-ended={ended || undefined}
              tabIndex={ended ? -1 : undefined}
              aria-label="Open call"
              title="Open call"
              onClick={expandCall}
            >
              <Maximize2 size={16} />
            </button>
          )}
        </div>
        <div className="lx-callstage-body">
          <VoiceOrb state={orbState(call, busy)} muted={call.muted} />
          <p
            className="lx-callstage-line"
            role="status"
            aria-live="polite"
            data-shimmer={waiting || undefined}
          >
            {status.line}
          </p>
          {/* The wide strip's own heading: who, then what — the line or, while connected,
              the clock. Hidden in the stacked layouts, which show line and clock in place. */}
          <span className="lx-callstage-name" aria-hidden="true">
            Lucius
          </span>
          <span
            className="lx-callstage-sub"
            aria-hidden="true"
            data-shimmer={(waiting && !!status.line) || undefined}
          >
            {status.stripLine ?? (status.line || top)}
          </span>
        </div>
        {
          <div className="lx-callstage-controls" data-ended={ended || undefined}>
            {screen && <VoiceCallHeard variant="screen" />}
            {screen && <VoiceCallAttach disabled={ended} busy={busy} />}
            {screen && <VoiceCallModel disabled={ended} />}
            {screen && (
              // The phone's call covers the chat: this folds it to the bar, over the message box.
              <div className="lx-callstage-ctl lx-callstage-ctl--type">
                <button
                  type="button"
                  className="lx-callstage-btn"
                  aria-label="Type a message"
                  disabled={ended}
                  onClick={onType}
                >
                  <Keyboard size={20} />
                </button>
                <span className="lx-callstage-label">Type</span>
              </div>
            )}
            <div className="lx-callstage-ctl lx-callstage-ctl--mic">
              <button
                type="button"
                className="lx-callstage-btn"
                aria-pressed={call.muted}
                aria-label={call.muted ? 'Unmute' : 'Mute'}
                disabled={ended}
                onClick={() => void toggleMute()}
              >
                {screen && <VoiceCallMicLight live={call.phase === 'live'} />}
                {call.muted ? <MicOff size={screen ? 26 : 14} /> : <Mic size={screen ? 26 : 14} />}
              </button>
              <span className="lx-callstage-label">{call.muted ? 'Unmute' : 'Mute'}</span>
            </div>
            <div className="lx-callstage-ctl lx-callstage-ctl--end">
              <button
                type="button"
                className="lx-callstage-btn lx-callstage-btn--end"
                aria-label="End call"
                disabled={ended}
                onClick={() => void hangUp()}
              >
                <PhoneOff size={screen ? 20 : 14} />
              </button>
              <span className="lx-callstage-label">End</span>
            </div>
          </div>
        }
      </div>
    </div>
  )
}
