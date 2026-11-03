import { useSyncExternalStore } from 'react'
import {
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type LocalAudioTrack,
} from 'livekit-client'
import { authFetch } from '../../../api/client'
import { emitConversationChanged } from '../../../lucius/bus'

// One voice call for the whole app (`voice_calls` flag), held OUTSIDE React. The first build kept
// the LiveKit room inside the Call button, so a chat that re-laid its composer (the landing screen
// becoming a conversation on the first message) unmounted the button and hung up mid-answer (live
// call, 2026-11-03). Components only read this store and send it commands.

export type CallPhase = 'idle' | 'connecting' | 'ringing' | 'live' | 'reconnecting' | 'ended'

export type CallProblemKind = 'mic' | 'insecure' | 'setup' | 'dropped' | 'service' | 'other'

export interface CallProblem {
  kind: CallProblemKind
  message: string
  /** The service's own words (LiveKit's refusal), for the notice's Details. */
  detail?: string
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
  /** The call's big view (the stage) or the one-line bar — one choice for every surface. */
  view: 'stage' | 'bar'
}

const IDLE: CallState = {
  phase: 'idle',
  sessionId: null,
  muted: false,
  connectedAt: null,
  endedSeconds: null,
  problem: null,
  setupOpen: false,
  view: 'stage',
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
const RING_CYCLES = 40 // two minutes of ringing, far past any pickup

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
  // Every ring is scheduled up front on the AUDIO clock: timed by a page timer, a ring due
  // while the page was busy (the call connecting) came late and broken (owner, 2026-11-03).
  const start = ctx.currentTime
  for (let k = 0; k < RING_CYCLES; k++) {
    burst(start + k * 3)
    burst(start + k * 3 + 0.6)
  }
  return () => void ctx.close()
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

// How loud each side is right now, 0–1, for the call screen's orb. Read on every animation
// frame (`readLevels`), so it lives outside the store: a level never re-renders React.
type Side = 'lucius' | 'you'
let levelCtx: AudioContext | null = null
const meters: Partial<
  Record<Side, { analyser: AnalyserNode; samples: Float32Array<ArrayBuffer> }>
> = {}

function openLevels(): void {
  const Ctx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  // Made inside the tap that starts the call: a phone keeps a context made later suspended.
  if (Ctx && !levelCtx) levelCtx = new Ctx()
}

function meter(side: Side, track: MediaStreamTrack): void {
  if (!levelCtx) return
  const analyser = levelCtx.createAnalyser()
  analyser.fftSize = 512
  levelCtx.createMediaStreamSource(new MediaStream([track])).connect(analyser)
  meters[side] = { analyser, samples: new Float32Array(analyser.fftSize) }
  void levelCtx.resume()
}

function rms(side: Side): number {
  const m = meters[side]
  if (!m) return 0
  m.analyser.getFloatTimeDomainData(m.samples)
  let sum = 0
  for (const v of m.samples) sum += v * v
  const level = Math.sqrt(sum / m.samples.length)
  // Lucius's voice arrives at full volume (speech ~0.02–0.2 RMS). Yours comes through the
  // browser's echo cancelling and noise suppression, much quieter: a higher gain above a small
  // noise floor, so the orb answers your voice as readily as his (owner, 2026-11-03).
  if (side === 'you') return Math.min(1, Math.max(0, level - MIC_FLOOR) * MIC_GAIN)
  return Math.min(1, level * 6)
}

const MIC_FLOOR = 0.004
const MIC_GAIN = 16

export function readLevels(): { lucius: number; you: number } {
  return { lucius: rms('lucius'), you: state.muted ? 0 : rms('you') }
}

function closeLevels(): void {
  delete meters.lucius
  delete meters.you
  void levelCtx?.close()
  levelCtx = null
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

// The server's side can end a call without the room closing — the voice worker crashing, or the
// app restarting — and the page sat "Connected" alone in an empty room. Lucius leaving ends the
// call (a goodbye deletes the room within this grace, which reads as an ordinary end); so does
// nobody picking up.
export const LEFT_GRACE_MS = 1500
export const PICKUP_TIMEOUT_MS = 30_000
let leftTimer: number | undefined
let pickupTimer: number | undefined
let endProblem: CallProblem | null = null

// The call's microphone, opened BEFORE the ringing starts and published once connected.
let mic: LocalAudioTrack | null = null

function teardown() {
  window.clearInterval(followTimer)
  window.clearTimeout(leftTimer)
  window.clearTimeout(pickupTimer)
  mic?.stop()
  mic = null
  closeLevels()
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
    // "Call ended" shows where the call was: a folded-down phone call jumped back to full
    // screen just to say it had ended (owner, 2026-11-03).
    view: state.view,
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
  endProblem = null
  openLevels()
  set({ ...IDLE, phase: 'connecting', sessionId })
  try {
    // The microphone first, THEN the ring: opening it reconfigures the audio device (a
    // Bluetooth headset switches to its call mode), which broke whichever ring was playing —
    // the second one, every time it opened mid-ring (owner, 2026-11-03).
    const opened = await createLocalAudioTrack()
    // Read afresh: a hang-up while the microphone opened changed it (TS keeps the narrowed type).
    if ((state.phase as CallPhase) !== 'connecting') {
      opened.stop() // hung up while the microphone was opening
      return
    }
    mic = opened
    meter('you', opened.mediaStreamTrack)
    stopRing = startRingback()
    const r = await authFetch('/api/voice/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId || null }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      teardown()
      // A limit reached (429: the demo's call caps, a token budget) isn't fixed in settings: say
      // why and offer Try again, not a Voice calls settings button that can't help.
      const kind: CallProblemKind = r.status === 429 ? 'other' : 'setup'
      set({ ...IDLE, problem: { kind, message: d.detail || 'The call could not start.' } })
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
      window.clearTimeout(pickupTimer)
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
        if (track.mediaStreamTrack) {
          watchFirstWord(track.mediaStreamTrack, pickedUp)
          meter('lucius', track.mediaStreamTrack)
        }
      }
    })
    next.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      if (speakers.some((p) => p.identity !== next.localParticipant.identity)) pickedUp()
    })
    next.on(RoomEvent.ParticipantConnected, () => window.clearTimeout(leftTimer))
    next.on(RoomEvent.ParticipantDisconnected, () => {
      if (next.remoteParticipants.size > 0) return
      window.clearTimeout(leftTimer)
      leftTimer = window.setTimeout(() => {
        if (room !== next) return
        endProblem = { kind: 'dropped', message: 'Lucius left the call. Call again to keep going.' }
        void next.disconnect()
      }, LEFT_GRACE_MS)
    })
    next.on(RoomEvent.Reconnecting, () => set({ phase: 'reconnecting' }))
    next.on(RoomEvent.Reconnected, () => set({ phase: state.connectedAt ? 'live' : 'ringing' }))
    next.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
      // Lucius hangs up after a goodbye by closing the room — an ordinary end, like the
      // owner's own hang-up. Any other disconnect is a dropped call, said as such.
      const ended = !endProblem && (localHangUp || reason === DisconnectReason.ROOM_DELETED)
      const byUs = localHangUp
      finish(ended ? null : (endProblem ?? { kind: 'dropped', message: 'The call dropped.' }))
      if (!byUs) void explainEnd()
    })
    await next.connect(grant.url, grant.token)
    set({ sessionId: grant.session_id, phase: 'ringing' })
    pickupTimer = window.setTimeout(() => {
      if (room !== next || state.phase !== 'ringing') return
      endProblem = { kind: 'setup', message: "Lucius didn't pick up." }
      void next.disconnect()
    }, PICKUP_TIMEOUT_MS)
    // The call's small talk is recorded in a few ms — gone before the 4s live poll looks — so
    // while a call is on, the open chat checks its conversation's revision (a few bytes when
    // nothing changed) and the live poll looks, every 1.5s. Polled from here, not pushed over
    // the room: a data message from the in-app worker crashed LiveKit's native layer (2026-11-03).
    followTimer = window.setInterval(() => emitConversationChanged(grant.session_id), FOLLOW_MS)
    await next.localParticipant.publishTrack(mic)
  } catch (e) {
    localHangUp = true
    await room?.disconnect()
    finish(classify(e))
  }
}

/** A call that ended on its own may have a reason the server knows — LiveKit refusing it (out
 *  of credit) — which the page can't see: ask, and show it instead (owner, 2026-11-03). */
async function explainEnd(): Promise<void> {
  try {
    const r = await authFetch('/api/voice/config')
    if (!r.ok) return
    const d = (await r.json()) as {
      worker?: { call_problem?: { message: string; detail?: string } | null }
    }
    const why = d.worker?.call_problem
    if (why && !room && (state.phase === 'idle' || state.phase === 'ended')) {
      set({ phase: 'idle', problem: { kind: 'service', message: why.message, detail: why.detail } })
    }
  } catch {
    // Best effort: the plain "dropped" / "didn't pick up" note still stands.
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
  // The call's own track: "enable the microphone" could open a SECOND one beside it.
  if (mic) await (muted ? mic.mute() : mic.unmute())
  set({ muted })
}

export function minimizeCall(): void {
  set({ view: 'bar' })
}

export function expandCall(): void {
  set({ view: 'stage' })
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

/** Tests only: put the store in a given state (a phase the views must draw). */
export function setVoiceCallForTests(next: Partial<CallState>): void {
  set(next)
}
