import { useEffect, useRef, useState } from 'react'
import { Phone, PhoneOff } from 'lucide-react'
import { Room, RoomEvent, Track } from 'livekit-client'
import { authFetch } from '../../../api/client'
import { useFlag } from '../../../hooks/useFlag'

// The composer's Call button (`voice_calls` flag). A call is a LiveKit room the voice worker
// joins (ADR 2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius): the fast front voice
// talks with you, and every real request becomes a normal Lucius turn on THIS conversation,
// so the chat shows it live and approval cards appear right here. Nothing is kept from the
// call in the browser; hanging up (or closing the tab) ends it.

type Phase = 'idle' | 'connecting' | 'ringing' | 'live'

interface CallGrant {
  url: string
  token: string
  session_id: string
}

/** The double ring a caller expects while Lucius "picks up" — WebAudio, no file to ship. */
function startRingback(): () => void {
  const Ctx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
  if (!Ctx) return () => {}
  const ctx = new Ctx()
  const burst = (at: number) => {
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0.0001, at)
    gain.gain.exponentialRampToValueAtTime(0.12, at + 0.02)
    gain.gain.setValueAtTime(0.12, at + 0.38)
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.4)
    gain.connect(ctx.destination)
    for (const hz of [400, 450]) {
      const osc = ctx.createOscillator()
      osc.frequency.value = hz
      osc.connect(gain)
      osc.start(at)
      osc.stop(at + 0.4)
    }
  }
  const cycle = () => {
    burst(ctx.currentTime)
    burst(ctx.currentTime + 0.6)
  }
  cycle()
  const timer = window.setInterval(cycle, 3000)
  return () => {
    window.clearInterval(timer)
    void ctx.close()
  }
}

export function VoiceCallButton({ sessionId }: { sessionId: string }) {
  const on = useFlag('voice_calls')
  const [phase, setPhase] = useState<Phase>('idle')
  const [problem, setProblem] = useState<string | null>(null)
  const roomRef = useRef<Room | null>(null)
  const stopRingRef = useRef<() => void>(() => {})
  const audioRef = useRef<HTMLElement[]>([])

  function cleanUp() {
    stopRingRef.current()
    stopRingRef.current = () => {}
    audioRef.current.forEach((el) => el.remove())
    audioRef.current = []
    roomRef.current = null
    setPhase('idle')
  }

  useEffect(() => () => void roomRef.current?.disconnect(), [])

  if (on !== true) return null

  async function start() {
    setProblem(null)
    setPhase('connecting')
    stopRingRef.current = startRingback()
    try {
      const r = await authFetch('/api/voice/call', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId || null }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Could not start the call.')
      }
      const grant = (await r.json()) as CallGrant
      const room = new Room({ adaptiveStream: true })
      roomRef.current = room
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach()
          el.className = 'lx-call-audio'
          document.body.appendChild(el)
          audioRef.current.push(el)
        }
      })
      // Lucius "picks up" when he first speaks: stop the ring then.
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        if (speakers.some((p) => p.identity !== room.localParticipant.identity)) {
          stopRingRef.current()
          stopRingRef.current = () => {}
          setPhase('live')
        }
      })
      room.on(RoomEvent.Disconnected, cleanUp)
      await room.connect(grant.url, grant.token)
      await room.localParticipant.setMicrophoneEnabled(true)
      setPhase((p) => (p === 'live' ? p : 'ringing'))
    } catch (e) {
      await roomRef.current?.disconnect()
      cleanUp()
      setProblem(e instanceof Error ? e.message : 'Could not start the call.')
    }
  }

  async function hangUp() {
    await roomRef.current?.disconnect()
    cleanUp()
  }

  const inCall = phase !== 'idle'
  const label =
    phase === 'connecting'
      ? 'Calling…'
      : phase === 'ringing'
        ? 'Ringing… (end call)'
        : phase === 'live'
          ? 'End call'
          : 'Call Lucius'
  return (
    <span className="lx-call-slot">
      <button
        type="button"
        className={'lx-tool' + (inCall ? ' lx-tool--call-live' : '')}
        title={problem || label}
        aria-label={label}
        aria-pressed={inCall}
        onMouseDown={(e) => e.preventDefault()}
        onClick={inCall ? hangUp : start}
      >
        {inCall ? <PhoneOff size={17} /> : <Phone size={17} />}
      </button>
      {problem && (
        <span className="lx-call-problem" role="status">
          {problem}
        </span>
      )}
    </span>
  )
}
