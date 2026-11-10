import { useCallback, useRef } from 'react'
import { reducedMotion, useVoiceLevels, type VoiceSpeaker } from './voiceLevels'

// The full-screen call's light, so you can tell whose turn it is without reading: a glow rises from
// the bottom while you talk, and the screen's edges light up while Lucius does. When he starts
// answering, a wave spreads out from the orb first: that is the moment the wait ends (owner,
// 2026-11-06). Only the start of an answer: a pause between his sentences isn't a new turn.

const SAME_TURN_MS = 2000

const WAVE: Keyframe[] = [
  { opacity: 0, transform: 'scale(0.35)' },
  { opacity: 0.6, transform: 'scale(0.7)', offset: 0.25 },
  { opacity: 0, transform: 'scale(1.9)' },
]

export function VoiceCallGlow({ live }: { live: boolean }) {
  const root = useRef<HTMLDivElement>(null)
  const wave = useRef<HTMLSpanElement>(null)
  const previous = useRef<VoiceSpeaker>('')
  const luciusStopped = useRef(-Infinity)

  const onSpeaker = useCallback((speaker: VoiceSpeaker) => {
    const now = performance.now()
    if (speaker === 'lucius') {
      if (now - luciusStopped.current > SAME_TURN_MS && !reducedMotion()) {
        wave.current?.animate?.(WAVE, { duration: 600, easing: 'ease-out' })
      }
    } else if (speaker === 'you') {
      luciusStopped.current = -Infinity // you spoke: whatever he says next is a new answer
    } else if (previous.current === 'lucius') {
      luciusStopped.current = now
    }
    previous.current = speaker
  }, [])

  useVoiceLevels(root, live, onSpeaker)

  return (
    <div ref={root} className="lx-callglow" aria-hidden="true">
      <span className="lx-callglow-you" />
      <span className="lx-callglow-lucius" />
      <span ref={wave} className="lx-callglow-wave" />
    </div>
  )
}
