import { useSyncExternalStore } from 'react'
import { DisconnectReason, Room, RoomEvent, Track } from 'livekit-client'
import { authFetch } from '../../../api/client'
import { emitConversationChanged } from '../../../lucius/bus'

// One voice call for the whole app (`voice_calls` flag), held OUTSIDE React. The first build kept
// the LiveKit room inside the Call button, so a chat that re-laid its composer (the landing screen
// becoming a conversation on the first message) unmounted the button and hung up mid-answer (live
// call, 2026-11-03). Components only read this store and send it commands.

export type CallPhase = 'idle' | 'connecting' | 'ringing' | 'live' | 'reconnecting' | 'ended'

export type CallProblemKind = 'mic' | 'insecure' | 'setup' | 'dropped' | 'other'

export interface CallProblem {
  kind: CallProblemKind
  message: string
}

export interface CallState {
  phase: CallPhase
  sessionId: string | null
  muted: boolean
  /** ms epoch when Lucius picked up; null before. */
  connectedAt: number | null
  /** Length of the call that just ended, for the brief "Call ended · 2:14". */
  endedSeconds: number | null
  problem: CallProblem | null
  /** The Voice calls setup dialog is open (over whatever screen the owner is on). */
  setupOpen: boolean
}

const IDLE: CallState = {
  phase: 'idle',
  sessionId: null,
  muted: false,
  connectedAt: null,
  endedSeconds: null,
  problem: null,
  setupOpen: false,
}

let state: CallState = IDLE
const listeners = new Set<() => void>()
let room: Room | null = null
let stopRing: () => void = () => {}
let audioEls: HTMLElement[] = []
let localHangUp = false
let endedTimer: number | undefined

function set(next: Partial<CallState>) {
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

export function useVoiceCall(): CallState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => state,
  )
}

/** The double ring a caller expects while Lucius "picks up" — WebAudio, no file to ship. */
function startRingback(): () => void {
  const Ctx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
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

/** Calls `onVoice` once, the moment `track` carries sound above silence (polled every 30 ms). */
function watchFirstWord(track: MediaStreamTrack, onVoice: () => void): void {
  const Ctx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctx) return
  const ctx = new Ctx()
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 512
  ctx.createMediaStreamSource(new MediaStream([track])).connect(analyser)
  const samples = new Float32Array(analyser.fftSize)
  const timer = window.setInterval(() => {
    if (!room) return stop()
    analyser.getFloatTimeDomainData(samples)
    let peak = 0
    for (const v of samples) peak = Math.max(peak, Math.abs(v))
    if (peak > 0.02) {
      stop()
      onVoice()
    }
  }, 30)
  function stop() {
    window.clearInterval(timer)
    void ctx.close()
  }
}

function classify(e: unknown): CallProblem {
  const name = e instanceof Error ? e.name : ''
  const message = e instanceof Error ? e.message : String(e)
  if (name === 'NotAllowedError' || /permission denied/i.test(message)) {
    return {
      kind: 'mic',
      message:
        "The microphone is blocked. Allow it for this site (the browser's address bar, or your system's privacy settings), then try again.",
    }
  }
  if (name === 'NotFoundError') {
    return { kind: 'mic', message: 'No microphone was found. Connect one, then try again.' }
  }
  return { kind: 'other', message: message || 'The call could not start.' }
}

const FOLLOW_MS = 1500
let followTimer: number | undefined

function teardown() {
  window.clearInterval(followTimer)
  stopRing()
  stopRing = () => {}
  audioEls.forEach((el) => el.remove())
  audioEls = []
  room = null
}

function finish(problem: CallProblem | null) {
  const seconds = state.connectedAt ? Math.round((Date.now() - state.connectedAt) / 1000) : null
  // The goodbye is recorded as the room closes — no signal left to carry it, so look once more.
  const ended = state.sessionId
  if (ended) window.setTimeout(() => emitConversationChanged(ended), 1000)
  teardown()
  if (problem) {
    set({ ...IDLE, problem, setupOpen: state.setupOpen })
    return
  }
  set({
    ...IDLE,
    phase: seconds !== null ? 'ended' : 'idle',
    endedSeconds: seconds,
    setupOpen: state.setupOpen,
  })
  window.clearTimeout(endedTimer)
  if (seconds !== null) endedTimer = window.setTimeout(() => set({ phase: 'idle' }), 3000)
}

export async function startCall(sessionId: string): Promise<void> {
  if (room || state.phase === 'connecting') return
  window.clearTimeout(endedTimer)
  if (!navigator.mediaDevices?.getUserMedia) {
    set({
      ...IDLE,
      problem: {
        kind: 'insecure',
        message: 'Calls need a secure address: open the app on localhost or over HTTPS.',
      },
    })
    return
  }
  localHangUp = false
  set({ ...IDLE, phase: 'connecting', sessionId })
  stopRing = startRingback()
  try {
    const r = await authFetch('/api/voice/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId || null }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      stopRing()
      set({ ...IDLE, problem: { kind: 'setup', message: d.detail || 'The call could not start.' } })
      return
    }
    const grant = (await r.json()) as { url: string; token: string; session_id: string }
    const next = new Room({ adaptiveStream: true })
    room = next
    // Lucius "picks up" when he first speaks: stop the ring and start the clock then. The level
    // is read straight off his audio — LiveKit's active-speaker report lags a few hundred ms, so
    // the ring played over his "Hi" (live call, 2026-11-03); that report stays as a fallback.
    const pickedUp = () => {
      if (state.phase !== 'ringing' && state.phase !== 'connecting') return
      stopRing()
      stopRing = () => {}
      set({ phase: 'live', connectedAt: Date.now() })
    }
    next.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        const el = track.attach()
        el.className = 'lx-call-audio'
        document.body.appendChild(el)
        audioEls.push(el)
        if (track.mediaStreamTrack) watchFirstWord(track.mediaStreamTrack, pickedUp)
      }
    })
    next.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      if (speakers.some((p) => p.identity !== next.localParticipant.identity)) pickedUp()
    })
    next.on(RoomEvent.Reconnecting, () => set({ phase: 'reconnecting' }))
    next.on(RoomEvent.Reconnected, () => set({ phase: state.connectedAt ? 'live' : 'ringing' }))
    next.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
      // Lucius hangs up after a goodbye by closing the room — an ordinary end, like the
      // owner's own hang-up. Any other disconnect is a dropped call, said as such.
      const ended = localHangUp || reason === DisconnectReason.ROOM_DELETED
      finish(ended ? null : { kind: 'dropped', message: 'The call dropped.' })
    })
    await next.connect(grant.url, grant.token)
    set({ sessionId: grant.session_id, phase: 'ringing' })
    // The call's small talk is recorded in a few ms — gone before the 4s live poll looks — so
    // while a call is on, the open chat checks its conversation's revision (a few bytes when
    // nothing changed) and the live poll looks, every 1.5s. Polled from here, not pushed over
    // the room: a data message from the in-app worker crashed LiveKit's native layer (2026-11-03).
    followTimer = window.setInterval(() => emitConversationChanged(grant.session_id), FOLLOW_MS)
    await next.localParticipant.setMicrophoneEnabled(true)
  } catch (e) {
    localHangUp = true
    await room?.disconnect()
    finish(classify(e))
  }
}

export async function hangUp(): Promise<void> {
  localHangUp = true
  if (room) await room.disconnect()
  else finish(null)
}

export async function toggleMute(): Promise<void> {
  if (!room) return
  const muted = !state.muted
  await room.localParticipant.setMicrophoneEnabled(!muted)
  set({ muted })
}

export function dismissProblem(): void {
  set({ problem: null })
}

export function openVoiceSetup(): void {
  set({ setupOpen: true, problem: null })
}

export function closeVoiceSetup(): void {
  set({ setupOpen: false })
}

// One setup dialog for the app, whichever chat surfaces are mounted (panel and dock can both be):
// the first host to mount renders it.
let setupHost: symbol | null = null
export function claimSetupHost(token: symbol): boolean {
  if (setupHost === null) setupHost = token
  return setupHost === token
}
export function releaseSetupHost(token: symbol): void {
  if (setupHost === token) setupHost = null
}

/** Tests only: put the store back to its starting state. */
export function resetVoiceCallForTests(): void {
  teardown()
  window.clearTimeout(endedTimer)
  state = IDLE
  listeners.forEach((l) => l())
}
