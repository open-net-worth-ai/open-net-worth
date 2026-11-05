import { useEffect, useRef } from 'react'
import { readLevels } from './voiceCall'

// The call's orb: a sphere of soft colour swaying like liquid, that swells with whoever is talking,
// you as much as Lucius. CSS only — the level loop writes two custom properties on every frame and
// React never re-renders for it.

export type OrbState = 'calling' | 'live' | 'working' | 'reconnecting' | 'ended'

// Soft in, softer out: the orb eases toward a voice instead of jumping with every syllable.
const ATTACK = 0.4
const RELEASE = 0.1
const SPEAKER_ON = 0.12
const SPEAKER_OFF = 0.06

function reducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

function useOrbLevels(ref: React.RefObject<HTMLDivElement | null>, live: boolean) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const still = reducedMotion()
    let lucius = 0
    let you = 0
    let speaker = ''
    let frame = 0
    const ease = (prev: number, next: number) =>
      prev + (next - prev) * (next > prev ? ATTACK : RELEASE)
    const tick = () => {
      frame = requestAnimationFrame(tick)
      if (document.hidden) return
      const raw = live ? readLevels() : { lucius: 0, you: 0 }
      lucius = ease(lucius, Math.sqrt(raw.lucius))
      you = ease(you, Math.sqrt(raw.you))
      if (!still) {
        el.style.setProperty('--lx-orb-lucius', lucius.toFixed(3))
        el.style.setProperty('--lx-orb-you', you.toFixed(3))
      }
      const loudest = lucius >= you ? 'lucius' : 'you'
      const level = Math.max(lucius, you)
      const next = level > SPEAKER_ON ? loudest : level < SPEAKER_OFF ? '' : speaker
      if (next !== speaker) {
        speaker = next
        if (speaker) el.dataset.speaker = speaker
        else delete el.dataset.speaker
      }
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      el.style.removeProperty('--lx-orb-lucius')
      el.style.removeProperty('--lx-orb-you')
      delete el.dataset.speaker
    }
  }, [ref, live])
}

export function VoiceOrb({
  state,
  muted,
  size = 'full',
}: {
  state: OrbState
  muted: boolean
  size?: 'full' | 'mini'
}) {
  const ref = useRef<HTMLDivElement>(null)
  useOrbLevels(ref, state === 'live')
  return (
    <div
      ref={ref}
      className={'lx-orb' + (size === 'mini' ? ' lx-orb--mini' : '')}
      data-state={state}
      data-muted={muted || undefined}
      aria-hidden="true"
    >
      <span className="lx-orb-halo" />
      <span className="lx-orb-core">
        <span className="lx-orb-pool" />
        <span className="lx-orb-pool" />
        <span className="lx-orb-pool" />
        <span className="lx-orb-pool" />
      </span>
    </div>
  )
}
