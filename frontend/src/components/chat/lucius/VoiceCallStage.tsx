import { useEffect, useRef } from 'react'
import { ChevronDown, Keyboard, Maximize2, Mic, MicOff, PhoneOff } from 'lucide-react'
import { expandCall, hangUp, minimizeCall, toggleMute, useVoiceCall } from './voiceCall'
import { callStatus, orbState, useCallClock } from './voiceCallStatus'
import { VoiceOrb } from './VoiceOrb'

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
}: {
  variant: 'screen' | 'card'
  busy: boolean
  expandable?: boolean
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
    (call.phase === 'live' && (busy || call.luciusTrouble))
  const root = useRef<HTMLDivElement>(null)
  const screen = variant === 'screen'

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
      <div className="lx-callstage-inner">
        <div className="lx-callstage-top">
          {top && (
            <span className={`lx-callbar-dot lx-callbar-dot--${status.dot}`} aria-hidden="true" />
          )}
          <span className="lx-callstage-clock" role="timer" aria-label="Call duration">
            {top}
          </span>
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
            {status.line || top}
          </span>
        </div>
        {
          <div className="lx-callstage-controls" data-ended={ended || undefined}>
            <div className="lx-callstage-ctl">
              <button
                type="button"
                className="lx-callstage-btn"
                aria-pressed={call.muted}
                aria-label={call.muted ? 'Unmute' : 'Mute'}
                disabled={call.phase !== 'live'}
                onClick={() => void toggleMute()}
              >
                {call.muted ? <MicOff size={screen ? 26 : 14} /> : <Mic size={screen ? 26 : 14} />}
              </button>
              <span className="lx-callstage-label">{call.muted ? 'Unmute' : 'Mute'}</span>
            </div>
            {screen && (
              // The phone's call covers the chat: Type folds it to the bar, over the message box.
              <div className="lx-callstage-ctl">
                <button
                  type="button"
                  className="lx-callstage-btn"
                  aria-label="Type a message"
                  disabled={ended}
                  onClick={minimizeCall}
                >
                  <Keyboard size={26} />
                </button>
                <span className="lx-callstage-label">Type</span>
              </div>
            )}
            <div className="lx-callstage-ctl">
              <button
                type="button"
                className="lx-callstage-btn lx-callstage-btn--end"
                aria-label="End call"
                disabled={ended}
                onClick={() => void hangUp()}
              >
                <PhoneOff size={screen ? 28 : 14} />
              </button>
              <span className="lx-callstage-label">End</span>
            </div>
          </div>
        }
      </div>
    </div>
  )
}
