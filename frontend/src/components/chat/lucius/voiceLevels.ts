import { useEffect } from 'react'
import { readLevels } from './voiceCall'

// How loud each side of the call is, eased, for whatever moves with the voices (the orb, the
// glow). The loop writes two custom properties on every frame and React never re-renders for it.

// Soft in, softer out: it eases toward a voice instead of jumping with every syllable.
const ATTACK = 0.4
const RELEASE = 0.1
const SPEAKER_ON = 0.12
const SPEAKER_OFF = 0.06

export type VoiceSpeaker = 'lucius' | 'you' | ''

export function reducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

export function useVoiceLevels(
  ref: React.RefObject<HTMLElement | null>,
  live: boolean,
  onSpeaker?: (speaker: VoiceSpeaker) => void,
) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const still = reducedMotion()
    let lucius = 0
    let you = 0
    let speaker: VoiceSpeaker = ''
    let frame = 0
    const ease = (prev: number, next: number) =>
      prev + (next - prev) * (next > prev ? ATTACK : RELEASE)
    const tick = () => {
      frame = requestAnimationFrame(tick)
      if (document.hidden) return
      const raw = live ? readLevels() : { lucius: 0, you: 0, luciusTalking: false }
      lucius = ease(lucius, Math.sqrt(raw.lucius))
      you = ease(you, Math.sqrt(raw.you))
      if (!still) {
        el.style.setProperty('--lx-orb-lucius', lucius.toFixed(3))
        el.style.setProperty('--lx-orb-you', you.toFixed(3))
      }
      if (raw.luciusTalking) el.dataset.luciusTalking = ''
      else delete el.dataset.luciusTalking
      const loudest = lucius >= you ? 'lucius' : 'you'
      const level = Math.max(lucius, you)
      const next = level > SPEAKER_ON ? loudest : level < SPEAKER_OFF ? '' : speaker
      if (next !== speaker) {
        speaker = next
        if (speaker) el.dataset.speaker = speaker
        else delete el.dataset.speaker
        onSpeaker?.(speaker)
      }
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      el.style.removeProperty('--lx-orb-lucius')
      el.style.removeProperty('--lx-orb-you')
      delete el.dataset.speaker
      delete el.dataset.luciusTalking
    }
  }, [ref, live, onSpeaker])
}
