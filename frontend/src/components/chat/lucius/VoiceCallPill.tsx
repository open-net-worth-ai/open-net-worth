import { useEffect, useRef } from 'react'
import { AlertCircle, ChevronUp, Mic, MicOff, PhoneOff, X } from 'lucide-react'
import { Spark } from '../../../lucius/identity'
import { desktopClientBridge } from '../../../platform/desktop'
import {
  dismissProblem,
  expandCall,
  hangUp,
  minimizeCall,
  startCall,
  toggleMute,
  useVoiceCall,
  type CallProblemKind,
} from './voiceCall'
import {
  callSignal,
  callStatus,
  orbState,
  pillMode,
  useCallClock,
  type PillMode,
} from './voiceCallStatus'
import { useOpenVoiceSetup, VoiceCallButton } from './VoiceCallButton'
import { VoiceCallSignal } from './VoiceCallSignal'
import { VoiceOrb } from './VoiceOrb'

// The dock's collapsed pill with voice calls on (`voice_calls` flag): Call beside Ask Lucius, and
// the whole call happens IN the pill — it grows to hold the orb, the clock, Mute and End, and
// shrinks back on hang-up. Opening the chat is there (the chevron), never needed. With the flag
// off the dock renders its old single pill button, untouched.

const HOVER_DWELL_MS = 200

const PROBLEM: Record<CallProblemKind, { label: string; action: string }> = {
  mic: { label: "Can't use the microphone", action: 'Try again' },
  dropped: { label: 'Call dropped', action: 'Call again' },
  setup: { label: "Call couldn't start", action: 'Set up' },
  service: { label: 'LiveKit refused the call', action: 'Try again' },
  insecure: { label: "Call couldn't start", action: 'Try again' },
  other: { label: "Call couldn't start", action: 'Try again' },
}

const IN_CALL_MODES: PillMode[] = ['calling', 'live', 'attention', 'reconnecting', 'poor']

export function VoiceCallPill({
  sessionId,
  busy,
  needsApproval,
  onAsk,
  onHoverOpen,
  onOpenDock,
}: {
  sessionId: string
  /** Lucius is working on the call's request (the call's own turn included). */
  busy: boolean
  /** The conversation is waiting on an approval card the pill can't show. */
  needsApproval: boolean
  /** Ask Lucius: open the dock on its composer. */
  onAsk: () => void
  /** Hovering Ask Lucius opens the dock, as the plain pill does. */
  onHoverOpen: () => void
  onOpenDock: () => void
}) {
  const call = useVoiceCall()
  const now = useCallClock(call)
  const mode = pillMode(call, needsApproval)
  const status = callStatus(call, now, busy)
  const signal = callSignal(call)
  const openSetup = useOpenVoiceSetup()
  const root = useRef<HTMLDivElement>(null)
  const dwell = useRef<number | undefined>(undefined)
  const lastMode = useRef(mode)

  // The button you pressed unmounts as the pill changes shape: keep keyboard focus in the pill.
  useEffect(() => {
    const was = lastMode.current
    lastMode.current = mode
    const el = root.current
    if (!el || was === mode) return
    // Within a call nothing unmounts: the connection changing is no reason to move focus.
    if (IN_CALL_MODES.includes(was) && IN_CALL_MODES.includes(mode)) return
    const focused = document.activeElement
    if (focused && focused !== document.body && !el.contains(focused)) return
    const target =
      mode === 'rest'
        ? el.querySelector<HTMLElement>('.lx-call-btn')
        : el.querySelector<HTMLElement>('.lx-dock-pill-open')
    target?.focus()
  }, [mode])
  useEffect(() => () => window.clearTimeout(dwell.current), [])

  const label =
    mode === 'rest'
      ? 'Lucius'
      : mode === 'problem'
        ? 'Voice call problem'
        : 'Voice call with Lucius'
  return (
    <div
      ref={root}
      className="lx-dock-pill lx-dock-pill--call"
      role="group"
      aria-label={label}
      data-mode={mode}
      data-working={(mode === 'live' && busy) || undefined}
      data-muted={call.muted || undefined}
    >
      {mode === 'rest' && (
        <>
          <VoiceCallButton sessionId={sessionId} tone="pill" />
          <button
            type="button"
            className="lx-dock-pill-ask"
            onMouseEnter={() => {
              window.clearTimeout(dwell.current)
              dwell.current = window.setTimeout(onHoverOpen, HOVER_DWELL_MS)
            }}
            onMouseLeave={() => window.clearTimeout(dwell.current)}
            onClick={() => {
              window.clearTimeout(dwell.current)
              onAsk()
            }}
          >
            <Spark size={13} />
            Ask Lucius
          </button>
        </>
      )}

      {mode === 'problem' && call.problem && (
        <>
          <span className="lx-dock-pill-alert" aria-hidden="true">
            <AlertCircle size={13} />
          </span>
          <button type="button" className="lx-dock-pill-open" onClick={onOpenDock}>
            <span className="lx-dock-pill-time" role="alert">
              {PROBLEM[call.problem.kind].label}
            </span>
          </button>
          {(() => {
            // On a Mac desktop app a refused microphone is undone in System Settings, never by
            // calling again: macOS doesn't ask twice.
            const micSettings =
              call.problem.kind === 'mic'
                ? desktopClientBridge()?.openMicrophoneSettings
                : undefined
            return (
              <button
                type="button"
                className="lx-dock-pill-review"
                onClick={() =>
                  micSettings
                    ? void micSettings()
                    : call.problem?.kind === 'setup'
                      ? openSetup()
                      : void startCall(sessionId)
                }
              >
                {micSettings ? 'Open Settings' : PROBLEM[call.problem.kind].action}
              </button>
            )
          })()}
          <button
            type="button"
            className="lx-dock-pill-btn"
            aria-label="Dismiss"
            onClick={dismissProblem}
          >
            <X size={14} />
          </button>
        </>
      )}

      {mode === 'ended' && (
        <span className="lx-dock-pill-time" role="status">
          {status.text}
        </span>
      )}

      {(mode === 'calling' ||
        mode === 'live' ||
        mode === 'reconnecting' ||
        mode === 'poor' ||
        mode === 'attention') && (
        <>
          <button
            type="button"
            className="lx-dock-pill-open"
            aria-label="Open call"
            onClick={() => {
              expandCall()
              onOpenDock()
            }}
          >
            <VoiceOrb state={orbState(call, busy)} muted={call.muted} size="mini" />
            <span
              className="lx-dock-pill-time"
              role="status"
              aria-live={mode === 'attention' ? 'assertive' : 'polite'}
            >
              {mode === 'attention' ? 'Needs approval' : status.short}
            </span>
            {/* Only when there's something to tell: full bars on the pill would be clutter. */}
            {signal && signal.bars <= 1 && <VoiceCallSignal signal={signal} />}
            {mode === 'live' && <ChevronUp size={14} aria-hidden="true" />}
          </button>
          {mode === 'attention' && (
            <button
              type="button"
              className="lx-dock-pill-review"
              onClick={() => {
                minimizeCall()
                onOpenDock()
              }}
            >
              Review
            </button>
          )}
          <button
            type="button"
            className="lx-dock-pill-btn"
            aria-pressed={call.muted}
            aria-label={call.muted ? 'Unmute' : 'Mute'}
            disabled={call.phase !== 'live'}
            onClick={() => void toggleMute()}
          >
            {call.muted ? <MicOff size={14} /> : <Mic size={14} />}
          </button>
          <button
            type="button"
            className="lx-dock-pill-btn lx-dock-pill-btn--end"
            aria-label="End call"
            onClick={() => void hangUp()}
          >
            <PhoneOff size={14} />
          </button>
        </>
      )}
    </div>
  )
}
