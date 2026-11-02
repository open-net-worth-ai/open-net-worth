import { useEffect, useState } from 'react'
import { Mic, MicOff } from 'lucide-react'
import { Button } from '../../ui'
import { hangUp, toggleMute, useVoiceCall, type CallState } from './voiceCall'

// What the composer card shows while a call is open (`voice_calls` flag): one status line, Mute
// and End call. No captions — the chat above already shows your words and Lucius's answers, and
// its approval cards stay tappable. Typing waits until you hang up, so voice and text turns
// never interleave.

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${String(seconds % 60).padStart(2, '0')}`
}

function statusOf(call: CallState, now: number, busy: boolean): { text: string; dot: string } {
  switch (call.phase) {
    case 'connecting':
      return { text: 'Calling…', dot: 'wait' }
    case 'ringing':
      return { text: 'Ringing…', dot: 'wait' }
    case 'reconnecting':
      return { text: 'Reconnecting…', dot: 'warn' }
    case 'ended':
      return { text: `Call ended · ${clock(call.endedSeconds ?? 0)}`, dot: 'wait' }
    default: {
      const secs = call.connectedAt ? Math.max(0, Math.round((now - call.connectedAt) / 1000)) : 0
      return { text: busy ? 'Lucius is working…' : `Connected · ${clock(secs)}`, dot: 'live' }
    }
  }
}

export function VoiceCallBar({ busy }: { busy: boolean }) {
  const call = useVoiceCall()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (call.phase !== 'live') return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [call.phase])

  const status = statusOf(call, now, busy)
  const ended = call.phase === 'ended'
  return (
    <div className="lx-callbar" role="group" aria-label="Voice call">
      <span className={`lx-callbar-dot lx-callbar-dot--${status.dot}`} aria-hidden="true" />
      <span className="lx-callbar-status" aria-live="polite">
        <span className="lx-callbar-time">{status.text}</span>
      </span>
      {!ended && (
        <>
          <Button
            variant="secondary"
            size="sm"
            aria-pressed={call.muted}
            iconLeft={call.muted ? <MicOff size={14} /> : <Mic size={14} />}
            onClick={() => void toggleMute()}
            disabled={call.phase !== 'live'}
          >
            {call.muted ? 'Unmute' : 'Mute'}
          </Button>
          <Button variant="danger" size="sm" onClick={() => void hangUp()}>
            End call
          </Button>
        </>
      )}
    </div>
  )
}
