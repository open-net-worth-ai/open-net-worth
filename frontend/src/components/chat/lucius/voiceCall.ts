import { useSyncExternalStore } from 'react'
import {
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
  createLocalAudioTrack,
  type ConnectionQuality,
  type LocalAudioTrack,
  type Participant,
} from 'livekit-client'
import { authFetch } from '../../../api/client'
import { desktopBridge, desktopClientBridge } from '../../../platform/desktop'
import { emitConversationChanged } from '../../../lucius/bus'
import {
  clearPolicyReload,
  reloadForPolicy,
  takeReloadNotice,
  watchPolicyBlock,
} from './voicePagePolicy'
import { playMuteTone } from './voiceCallSounds'

// One voice call for the whole app (`voice_calls` flag), held OUTSIDE React. The first build kept
// the LiveKit room inside the Call button, so a chat that re-laid its composer (the landing screen
// becoming a conversation on the first message) unmounted the button and hung up mid-answer (live
// call, 2026-11-03). Components only read this store and send it commands.

export type CallPhase = 'idle' | 'connecting' | 'ringing' | 'live' | 'reconnecting' | 'ended'

export type CallProblemKind =
  | 'mic'
  | 'insecure'
  | 'setup'
  | 'dropped'
  | 'service'
  | 'other'
  /** This conversation can't take a call (an Automation's): the way out is another one. */
  | 'conversation'
  /** Not a problem: the page reloaded so it could reach the voice server, and a call needs a tap. */
  | 'ready'

export interface CallProblem {
  kind: CallProblemKind
  message: string
  /** The service's own words (LiveKit's refusal), for the notice's Details. */
  detail?: string
  /** The conversation the call was for: its problem doesn't follow you to another. */
  sessionId?: string
}

export type CallQuality = 'excellent' | 'good' | 'poor' | 'lost'

export interface CallState {
  phase: CallPhase
  sessionId: string | null
  muted: boolean
  /** ms epoch when Lucius picked up; null before. */
  connectedAt: number | null
  /** Length of the call that just ended, for the brief "Call ended · 2:14". */
  endedSeconds: number | null
  problem: CallProblem | null
  /** The call's big view (the stage) or the one-line bar — one choice for every surface. */
  view: 'stage' | 'bar'
  /** This device is still connected, but Lucius's side of the call has dropped: the room can't
   *  say so, as it is the broken part. The phone's own drop is the `reconnecting` phase. */
  luciusTrouble: boolean
  /** The weaker of the two connections, yours and Lucius's, as LiveKit rates them; null until it
   *  has rated one. */
  quality: CallQuality | null
  /** The browser says this device has no network. LiveKit's rating comes from its server, so it
   *  can't change once the network is gone, and it notices a drop only some seconds later. */
  offline: boolean
  /** Lucius has a turn running on the call's conversation, as the app reports it: away from the
   *  chat there is nothing else to say so. */
  luciusWorking: boolean
  /** Your words as the call hears them, updating as you speak; empty otherwise. */
  heard: string
  /** You've finished saying `heard`: it stays in the chat until your real message lands. */
  heardDone: boolean
  /** The call screen's caption shows `heard`: you're talking now, or just stopped and nothing has
   *  answered yet. Decided here, as only the order of events can tell (`noteYourWords`). */
  heardShown: boolean
  /** Lucius's words as the voice says them, in step with it; empty otherwise. */
  spoken: string
  /** He has finished saying `spoken`: it stays on screen a moment longer. */
  spokenDone: boolean
  /** The voice is talking now, as the agent itself reports it. Its words can fail to arrive on
   *  their own (`lk.transcription`), and then this is all that says so. */
  luciusSpeaking: boolean
  /** Everything heard on this call, in order, the last still growing while he says it: a line the
   *  voice says itself only reaches the chat later, and stays on screen until it has. */
  spokenLines: string[]
}

const IDLE: CallState = {
  heard: '',
  heardDone: false,
  heardShown: false,
  spoken: '',
  spokenDone: false,
  luciusSpeaking: false,
  spokenLines: [],
  phase: 'idle',
  sessionId: null,
  muted: false,
  connectedAt: null,
  endedSeconds: null,
  problem: null,
  view: 'stage',
  luciusTrouble: false,
  quality: null,
  offline: false,
  luciusWorking: false,
}

let state: CallState = takeReloadNotice()
  ? { ...IDLE, problem: { kind: 'ready', message: 'Calls are ready.' } }
  : IDLE
const listeners = new Set<() => void>()
let room: Room | null = null
let stopRing: () => void = () => {}
let audioEls: HTMLElement[] = []
let localHangUp = false
let endedTimer: number | undefined

/** The conversation the latest call was for, which every problem from then on belongs to. */
let callSession: string | null = null

function set(next: Partial<CallState>) {
  const problem =
    next.problem && callSession ? { sessionId: callSession, ...next.problem } : next.problem
  state = { ...state, ...next, ...(next.problem !== undefined && { problem }) }
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

function rawLevel(side: Side): number {
  const m = meters[side]
  if (!m) return 0
  m.analyser.getFloatTimeDomainData(m.samples)
  let sum = 0
  for (const v of m.samples) sum += v * v
  return Math.sqrt(sum / m.samples.length)
}

function rms(side: Side): number {
  const level = rawLevel(side)
  // Lucius's voice arrives at full volume (speech ~0.02–0.2 RMS). Yours comes through the
  // browser's echo cancelling and noise suppression, much quieter: a higher gain above a small
  // noise floor, so the orb answers your voice as readily as his (owner, 2026-11-03).
  if (side === 'you') return Math.min(1, Math.max(0, level - MIC_FLOOR) * MIC_GAIN)
  return Math.min(1, level * 6)
}

const MIC_FLOOR = 0.004
const MIC_GAIN = 16

/** How loud LiveKit says you are, from the audio it receives. An iPhone's analyser on the mic read
 *  exactly zero through a whole call while Lucius answered every word, so nothing moved when you
 *  spoke (owner's phone, 2026-11-06). Coarser than the analyser, but it's never wrong about that. */
function serverLevel(): number {
  return room?.localParticipant.audioLevel ?? 0
}

// How loud his voice must be to count as him talking, and how long after it to keep counting: it
// dips between words, and the room rings on a moment after he stops.
const LUCIUS_TALKING = 0.05
const LUCIUS_TAIL_MS = 500
let luciusHeardAt = -Infinity

export function readLevels(): { lucius: number; you: number; luciusTalking: boolean } {
  const lucius = rms('lucius')
  const now = performance.now()
  if (lucius > LUCIUS_TALKING) luciusHeardAt = now
  const luciusTalking = now - luciusHeardAt < LUCIUS_TAIL_MS
  if (state.muted) return { lucius, you: 0, luciusTalking }
  // On speaker the mic hears him too, and it read as you talking (owner, 2026-11-06). So while he
  // is talking, you only count when the mic is louder than he is: echo stays under his own level,
  // and someone cutting in goes over it.
  if (luciusTalking && rawLevel('you') <= rawLevel('lucius')) {
    return { lucius, you: 0, luciusTalking }
  }
  return { lucius, you: rms('you') || serverLevel(), luciusTalking }
}

function closeLevels(): void {
  delete meters.lucius
  delete meters.you
  void levelCtx?.close()
  levelCtx = null
}

// Where the microphone was refused decides where it is turned back on: inside the desktop app it
// is the system's switch for the app — there is no address bar (owner, 2026-11-03).
function micBlockedMessage(): string {
  if (desktopClientBridge()?.openMicrophoneSettings)
    return "Open Net Worth isn't allowed to use the microphone. Turn it on in System Settings → Privacy & Security → Microphone, then try again."
  if (desktopBridge() || desktopClientBridge())
    return "Open Net Worth isn't allowed to use the microphone. Turn it on in your system's privacy settings, then try again."
  return "The microphone is blocked. Allow it for this site (the browser's address bar, or your system's privacy settings), then try again."
}

function classify(e: unknown): CallProblem {
  const name = e instanceof Error ? e.name : ''
  const message = e instanceof Error ? e.message : String(e)
  if (name === 'NotAllowedError' || /permission denied/i.test(message)) {
    return { kind: 'mic', message: micBlockedMessage() }
  }
  if (name === 'NotFoundError') {
    return { kind: 'mic', message: 'No microphone was found. Connect one, then try again.' }
  }
  // LiveKit's own words name no cause or next step: they stay behind Details.
  if (/signal connection/i.test(message)) {
    return {
      kind: 'setup',
      message: "Couldn't connect to your voice server. Check your internet and LiveKit settings.",
      detail: message,
    }
  }
  return { kind: 'other', message: message || 'The call could not start.' }
}

const FOLLOW_MS = 1500
let followTimer: number | undefined

// Lucius's side dropping looked like a healthy call that had stopped hearing you (owner,
// 2026-11-05): ask the app every couple of seconds. No answer means its network is down; an answer
// can still say its line is broken, which outlasts the network coming back. One slow or lost
// reply on a phone is nothing: only two in a row count.
export const HEALTH_MS = 2000
const HEALTH_TIMEOUT_MS = 2500
const HEALTH_MISSES = 2
let healthTimer: number | undefined
let micTimer: number | undefined
let micPeak = 0
let serverPeak = 0

/** What the phone's mic is doing, for the server's log: state and loudness, never sound. "The mic
 *  misses me on a quiet line" had nothing to go on, as only the phone knows this (owner, 2026-11-06). */
function micReport(): string {
  const track = mic?.mediaStreamTrack
  const report = [
    `track=${track?.readyState ?? 'none'}`,
    `system_muted=${track?.muted ? 1 : 0}`,
    `enabled=${track?.enabled ? 1 : 0}`,
    `app_muted=${state.muted ? 1 : 0}`,
    `audio=${levelCtx?.state ?? 'none'}`,
    `peak=${micPeak.toFixed(3)}`,
    `server_peak=${serverPeak.toFixed(3)}`,
    `page=${document.visibilityState}`,
    `streams=${transcriptStreams}`,
    `words=${heardUpdates}`,
    `caption=${state.heardShown && state.heard ? 'you' : state.spoken ? 'lucius' : 'none'}`,
    `voice=${state.luciusSpeaking ? 1 : 0}`,
  ].join(',')
  micPeak = 0
  serverPeak = 0
  return report
}

function watchLuciusLine(roomName: string): void {
  let misses = 0
  let asking = false
  // Between reports, or a word said between two of them would never show.
  micPeak = 0
  serverPeak = 0
  micTimer = window.setInterval(() => {
    micPeak = Math.max(micPeak, rawLevel('you'))
    serverPeak = Math.max(serverPeak, serverLevel())
  }, 100)
  healthTimer = window.setInterval(async () => {
    if (asking || state.phase !== 'live') return
    asking = true
    const giveUp = new AbortController()
    const timer = window.setTimeout(() => giveUp.abort(), HEALTH_TIMEOUT_MS)
    let said = false
    let working = false
    try {
      const query = `room=${encodeURIComponent(roomName)}&mic=${encodeURIComponent(micReport())}`
      const r = await authFetch(`/api/voice/call/health?${query}`, {
        signal: giveUp.signal,
      })
      // A tunnel answers 5xx for an app it can't reach; a 4xx is the app itself, so it's there.
      misses = r.status >= 500 ? misses + 1 : 0
      if (r.ok) {
        const health = (await r.json()) as { trouble?: boolean; working?: boolean }
        said = Boolean(health.trouble)
        working = Boolean(health.working)
      }
    } catch {
      misses += 1
    }
    window.clearTimeout(timer)
    asking = false
    const trouble = said || misses >= HEALTH_MISSES
    if (state.phase !== 'live') return
    if (state.luciusTrouble !== trouble || state.luciusWorking !== working) {
      set({ luciusTrouble: trouble, luciusWorking: working })
    }
  }, HEALTH_MS)
}

const markOffline = () => set({ offline: true })
const markOnline = () => set({ offline: false })

function watchDeviceNetwork(): void {
  window.addEventListener('offline', markOffline)
  window.addEventListener('online', markOnline)
}

function unwatchDeviceNetwork(): void {
  window.removeEventListener('offline', markOffline)
  window.removeEventListener('online', markOnline)
}

const QUALITY_WORST_FIRST: CallQuality[] = ['lost', 'poor', 'good', 'excellent']
const qualities = new Map<string, CallQuality>()

function noteQuality(rated: ConnectionQuality, who: Participant): void {
  const quality = rated as string as CallQuality
  if (QUALITY_WORST_FIRST.includes(quality)) qualities.set(who.identity, quality)
  else qualities.delete(who.identity) // LiveKit's "unknown": no longer a rating to go by
  const rates = [...qualities.values()]
  set({ quality: QUALITY_WORST_FIRST.find((q) => rates.includes(q)) ?? null })
}

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

// LiveKit's transcript of a call. Each update of your words is a new stream holding all of the
// segment so far, the last marked final. Lucius's words are one stream that grows, paced to his voice.
const TRANSCRIPT_TOPIC = 'lk.transcription'
// How long Lucius's last words stay up once he's finished.
const LIVE_WORDS_HOLD_MS = 1500
// Far more than can be waiting to reach the chat.
const MAX_SPOKEN_LINES = 40
// Going on talking this soon after a segment ends is the same sentence.
const HEARD_SAME_SENTENCE_MS = 3000
// The chat swaps your words for the real message when it lands, which is only saved once the voice
// starts or the request goes out: clearing them on a short timer left a gap with nothing there
// (owner, 2026-11-06). This is only for a message that never comes.
const HEARD_KEEP_MS = 10000
// A segment that never gets its final word mustn't leave stale words on screen.
const HEARD_STALE_MS = 6000
// How long your finished words stay on the call screen with no reply yet. The chat keeps them longer.
const HEARD_CAPTION_MS = 3500
let heardTimer: number | undefined
let captionTimer: number | undefined
let sentenceTimer: number | undefined
let spokenTimer: number | undefined
let heardLatest = 0
let spokenLatest = 0
// Finished segments of what you're still saying: a long sentence comes in several, and each
// restarting the line lost the words before it.
let heardBefore = ''
// His line is being spoken, and the update before this one left your segment unfinished.
let lineOpen = false
let inSegment = false
let transcriptStreams = 0
let heardUpdates = 0

type TranscriptReader = AsyncIterable<string> & {
  info: { attributes?: Record<string, string> }
  readAll: () => Promise<string>
}

async function showSpoken(next: Room, reader: TranscriptReader) {
  const order = ++spokenLatest
  const current = () => room === next && order === spokenLatest
  let text = ''
  window.clearTimeout(spokenTimer)
  window.clearTimeout(captionTimer)
  window.clearTimeout(sentenceTimer)
  lineOpen = true
  // Whatever you said before this line stays said: it can't join what you say after it.
  heardBefore = ''
  set({ heardShown: false, spokenLines: [...state.spokenLines.slice(1 - MAX_SPOKEN_LINES), ''] })
  try {
    for await (const chunk of reader) {
      text += chunk
      if (current()) {
        const earlier = state.spokenLines.slice(0, -1)
        set({ spoken: text.trim(), spokenDone: false, spokenLines: [...earlier, text.trim()] })
      }
    }
  } catch {
    // The stream broke, as at a hang-up: what was said stays said.
  }
  if (!current()) return
  lineOpen = false
  set({ spokenDone: true })
  spokenTimer = window.setTimeout(() => {
    if (order === spokenLatest) set({ spoken: '', spokenDone: false })
  }, LIVE_WORDS_HOLD_MS)
}

function sameWords(a: string, b: string): boolean {
  const plain = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
  return !!plain(b) && plain(a).endsWith(plain(b))
}

/** Whether the caption shows your words, from what has happened since they began. Your words came
 *  back after Lucius stopped, however the last rule was written, as each late update found another
 *  way past it (owner, 2026-11-06, 2026-11-07). So: while he's talking the caption is his; a word
 *  still arriving once he's stopped is you talking now; and a finished sentence shows only if it
 *  was showing, so one that arrives late, for words he has already answered, stays gone. */
function noteYourWords(final: boolean): void {
  const firstOfSegment = !inSegment
  inSegment = !final
  window.clearTimeout(captionTimer)
  let shown = state.heardShown
  if (lineOpen || state.luciusSpeaking) shown = false
  else if (!final || firstOfSegment) shown = true
  if (shown && final) {
    captionTimer = window.setTimeout(() => set({ heardShown: false }), HEARD_CAPTION_MS)
  }
  state = { ...state, heardShown: shown }
}

/** His voice started or stopped, whether or not its words have come. With no words the caption
 *  stayed on yours, so your earlier question showed while he answered it (owner, 2026-11-07). */
function noteHisVoice(speaking: boolean): void {
  if (speaking === state.luciusSpeaking) return
  if (!speaking) {
    set({ luciusSpeaking: false })
    return
  }
  window.clearTimeout(captionTimer)
  window.clearTimeout(sentenceTimer)
  heardBefore = ''
  if (lineOpen) {
    set({ luciusSpeaking: true, heardShown: false })
    return
  }
  // An earlier line still on screen isn't this one.
  window.clearTimeout(spokenTimer)
  set({ luciusSpeaking: true, heardShown: false, spoken: '', spokenDone: false })
}

function watchTranscript(next: Room) {
  next.registerTextStreamHandler(TRANSCRIPT_TOPIC, async (rawReader, from) => {
    const reader = rawReader as unknown as TranscriptReader
    transcriptStreams += 1
    const attributes = reader.info.attributes ?? {}
    const track = attributes['lk.transcribed_track_id']
    const own =
      from.identity === next.localParticipant.identity ||
      (!!track && next.localParticipant.trackPublications.has(track))
    if (!own) return showSpoken(next, reader)
    const order = ++heardLatest
    const text = (await reader.readAll()).trim()
    // Streams can finish out of order: a slower, shorter one mustn't overwrite newer words.
    if (!text || room !== next || order !== heardLatest) return
    heardUpdates += 1
    const final = attributes['lk.transcription_final'] === 'true'
    // The same sentence finishing twice (a tidied copy): it isn't more words, or anything new.
    if (final && !inSegment && sameWords(heardBefore || state.heard, text)) return
    const heard = heardBefore ? `${heardBefore} ${text}` : text
    noteYourWords(final)
    window.clearTimeout(heardTimer)
    window.clearTimeout(sentenceTimer)
    if (final) {
      heardBefore = heard
      sentenceTimer = window.setTimeout(() => (heardBefore = ''), HEARD_SAME_SENTENCE_MS)
    }
    heardTimer = window.setTimeout(
      () => {
        heardBefore = ''
        inSegment = false
        set({ heard: '', heardDone: false, heardShown: false })
      },
      final ? HEARD_KEEP_MS : HEARD_STALE_MS,
    )
    set({ heard, heardDone: final })
  })
}

function teardown() {
  window.clearTimeout(heardTimer)
  window.clearTimeout(sentenceTimer)
  window.clearTimeout(spokenTimer)
  window.clearTimeout(captionTimer)
  heardLatest = 0
  spokenLatest = 0
  heardBefore = ''
  lineOpen = false
  inSegment = false
  window.clearInterval(followTimer)
  window.clearInterval(healthTimer)
  window.clearInterval(micTimer)
  window.clearTimeout(leftTimer)
  window.clearTimeout(pickupTimer)
  qualities.clear()
  unwatchDeviceNetwork()
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
    set({ ...IDLE, problem })
    return
  }
  set({
    ...IDLE,
    phase: seconds !== null ? 'ended' : 'idle',
    endedSeconds: seconds,
    // "Call ended" shows where the call was: a folded-down phone call jumped back to full
    // screen just to say it had ended (owner, 2026-11-03).
    view: state.view,
  })
  window.clearTimeout(endedTimer)
  if (seconds !== null) endedTimer = window.setTimeout(() => set({ phase: 'idle' }), 3000)
}

export async function startCall(sessionId: string, view: CallState['view'] = 'bar'): Promise<void> {
  if (room || (state.phase === 'connecting' && !restarting)) return
  restarting = false
  const thisCall = ++callAttempt
  window.clearTimeout(endedTimer)
  callSession = sessionId || null
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
  // Folded unless the caller says otherwise: a call started from another screen shouldn't take it
  // over, and the pill or the strip opens it when you want it (owner, 2026-11-05).
  set({ ...IDLE, phase: 'connecting', sessionId, view })
  const policy = watchPolicyBlock()
  try {
    // The microphone first, THEN the ring: opening it reconfigures the audio device (a
    // Bluetooth headset switches to its call mode), which broke whichever ring was playing —
    // the second one, every time it opened mid-ring (owner, 2026-11-03).
    const opened = await createLocalAudioTrack()
    // Read afresh: a hang-up while the microphone opened changed it (TS keeps the narrowed type).
    if (thisCall !== callAttempt || (state.phase as CallPhase) !== 'connecting') {
      opened.stop() // hung up while the microphone was opening
      return
    }
    mic = opened
    meter('you', opened.mediaStreamTrack)
    stopRing = startRingback()
    if (state.muted) await opened.mute() // pressed Mute while the microphone was opening
    const r = await authFetch('/api/voice/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId || null }),
    })
    if (thisCall !== callAttempt) return
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      teardown()
      // A limit reached (429: the demo's call caps, a token budget) isn't fixed in settings: say
      // why and offer Try again, not a Voice calls settings button that can't help.
      const kind: CallProblemKind =
        r.status === 429 ? 'other' : r.status === 423 ? 'conversation' : 'setup'
      set({ ...IDLE, problem: { kind, message: d.detail || 'The call could not start.' } })
      return
    }
    const grant = (await r.json()) as {
      url: string
      token: string
      room: string
      session_id: string
    }
    // Hung up while the call was being asked for: it went on connecting behind an idle screen,
    // microphone and all. The agent the app asked for gives up by itself (``CALLER_WAIT_S``).
    if ((state.phase as CallPhase) !== 'connecting') return
    const next = new Room({ adaptiveStream: true })
    room = next
    transcriptStreams = 0
    heardUpdates = 0
    watchTranscript(next)
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
    next.on(RoomEvent.ParticipantAttributesChanged, (_changed, who) => {
      const agentState = who.attributes?.['lk.agent.state']
      if (who.identity !== next.localParticipant.identity && agentState) {
        noteHisVoice(agentState === 'speaking')
      }
    })
    next.on(RoomEvent.ConnectionQualityChanged, noteQuality)
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
    clearPolicyReload()
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
    watchLuciusLine(grant.room)
    watchDeviceNetwork()
    await next.localParticipant.publishTrack(mic)
  } catch (e) {
    if (thisCall !== callAttempt) return // a restart has already replaced this attempt
    // Hanging up while it was still connecting rejects the connect ("Client initiated
    // disconnect"): what you just did, not a problem to report (owner, 2026-11-04).
    const mine = localHangUp
    localHangUp = true
    await room?.disconnect()
    if (!mine && policy.blocked()) {
      if (reloadForPolicy()) return
      const detail = e instanceof Error ? e.message : String(e)
      const message = 'Your browser blocked the connection to your voice server.'
      finish({ kind: 'other', message, detail })
    } else if (!mine) finish(classify(e))
    else if (room) finish(null)
  } finally {
    policy.stop()
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

/** Works from the moment you press Call: you may not want to be heard when Lucius picks up. */
export async function toggleMute(): Promise<void> {
  if (state.phase === 'idle' || state.phase === 'ended') return
  const muted = !state.muted
  set({ muted })
  if (levelCtx) playMuteTone(levelCtx, muted)
  // The call's own track: "enable the microphone" could open a SECOND one beside it.
  if (!mic) return
  const before = mic.mediaStreamTrack
  await (muted ? mic.mute() : mic.unmute())
  // Unmuting can reacquire the mic as a new track, and the level was left listening to the dead
  // one: nothing moved when you spoke after that (owner's phone, 2026-11-06).
  if (mic && mic.mediaStreamTrack !== before) meter('you', mic.mediaStreamTrack)
}

const WORKER_WAIT_MS = 30_000
const WORKER_POLL_MS = 500
let restarting = false
/** Which start is current: a restart can overtake one still opening the microphone or connecting,
 *  and the phase can't tell them apart, since the new one is `connecting` too. */
let callAttempt = 0

/** Saving the voice settings restarts the worker, so it may be a few seconds before it can answer. */
async function workerRunning(stillWanted: () => boolean): Promise<boolean> {
  const giveUpAt = Date.now() + WORKER_WAIT_MS
  while (stillWanted() && Date.now() < giveUpAt) {
    try {
      const r = await authFetch('/api/voice/config')
      const config = r.ok ? ((await r.json()) as { worker?: { running?: boolean } }) : null
      if (config?.worker?.running) return true
    } catch {
      // The app is restarting the worker, or the network blinked: ask again.
    }
    await new Promise((done) => window.setTimeout(done, WORKER_POLL_MS))
  }
  return false
}

/** Runs `change` with the call hung up, then calls back into the same conversation: a call reads
 *  its settings once, when it starts, even one still connecting (owner, 2026-11-06). With no call on, it only runs `change`.
 *  Ending the call meanwhile wins: nothing calls back. */
export async function restartCall(change: () => Promise<void>): Promise<void> {
  const { sessionId, view, phase } = state
  if (phase === 'idle' || phase === 'ended') return change()
  callAttempt++
  await hangUp()
  if (room) finish(null)
  window.clearTimeout(endedTimer)
  set({ ...IDLE, phase: 'connecting', sessionId, view })
  const stillWanted = () => state.phase === 'connecting' && state.sessionId === sessionId
  try {
    await change()
  } catch (e) {
    if (stillWanted()) {
      const message = e instanceof Error ? e.message : 'Could not change the voice settings.'
      set({ ...IDLE, problem: { kind: 'other', message } })
    }
    return
  }
  const ready = await workerRunning(stillWanted)
  if (!stillWanted()) return
  if (!ready) {
    set({ ...IDLE, problem: { kind: 'other', message: 'Voice calls are still starting up.' } })
    return
  }
  restarting = true
  await startCall(sessionId, view)
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

/** Tests only: put the store back to its starting state. */
export function resetVoiceCallForTests(): void {
  teardown()
  window.clearTimeout(endedTimer)
  callSession = null
  luciusHeardAt = -Infinity
  restarting = false
  state = IDLE
  listeners.forEach((l) => l())
}

/** Tests only: put the store in a given state (a phase the views must draw). */
export function setVoiceCallForTests(next: Partial<CallState>): void {
  set(next)
}
