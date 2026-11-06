import { useRef } from 'react'
import { useVoiceLevels } from './voiceLevels'

// The call's orb: a sphere of soft colour swaying like liquid, that swells with whoever is talking,
// you as much as Lucius. CSS only: useVoiceLevels moves it, and React never re-renders for it.

export type OrbState = 'calling' | 'live' | 'working' | 'reconnecting' | 'ended'

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
  useVoiceLevels(ref, state === 'live')
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
