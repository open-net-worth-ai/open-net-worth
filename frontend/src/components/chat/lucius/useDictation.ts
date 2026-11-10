import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { desktopBridge, desktopClientBridge } from '../../../platform/desktop'
import { phonePlatform } from '../../tabs/settings/sections/phoneShortcuts'

// Dictation into the message box (owner, 2026-11-03), with whatever this computer has:
// - the Mac desktop app: macOS's own dictation, which types into the focused box itself — live,
//   on the device, nothing to set up here (the app's Edit → Start Dictation…);
// - a browser: its own speech recognition, the audio handled by its provider (Google in Chrome
//   and Edge, Apple in Safari).
// Never the browser engine inside the desktop app — Electron has the API but no speech service
// behind it — whether it serves its own book or is connected to a server.

interface SpeechResult {
  readonly isFinal: boolean
  readonly 0: { readonly transcript: string }
}
interface SpeechEvent {
  readonly resultIndex: number
  readonly results: ArrayLike<SpeechResult>
}
interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: SpeechEvent) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}
type RecognitionCtor = new () => Recognition

function inDesktopApp(): boolean {
  return Boolean(desktopBridge() || desktopClientBridge())
}

// Not on an iPhone or iPad either: Safari there gives a page speech recognition ONCE — every later
// dictation got no sound (its own log, 2026-11-04) — and the keyboard's own microphone key
// dictates into the box anyway (owner).
function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined' || inDesktopApp() || phonePlatform() === 'ios') return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const NOTICE_MS = 6000
// The Mac's own dictation, started from here and not yet ended by us (or Esc), kept OUTSIDE any
// one message box: the first message moves the chat into a conversation, which brings a new box,
// and the red mic went with the old one while macOS went on typing (owner, 2026-11-04). Every box
// shows it; only the box that started it pulls the caret back. macOS may stop on its own after
// a silence without saying so; the next tap then only ends what is already over.
let systemListening = false
let systemOwner: symbol | null = null
const systemListeners = new Set<() => void>()

function setSystemListening(on: boolean, owner: symbol | null = null): void {
  systemOwner = on ? owner : null
  if (systemListening === on) return
  systemListening = on
  systemListeners.forEach((listener) => listener())
}

function useSystemListening(): boolean {
  return useSyncExternalStore(
    (listener) => {
      systemListeners.add(listener)
      return () => systemListeners.delete(listener)
    },
    () => systemListening,
    () => false,
  )
}

export function resetDictationForTests(): void {
  setSystemListening(false)
}

export type DictationProblem =
  | 'blocked'
  | 'no-service'
  | 'no-mic'
  | 'no-speech'
  | 'no-start'
  | 'system'
  | 'other'
  | null

// A start that ends this soon, having heard nothing and said nothing, never really started: Safari
// on an iPhone does that now and then, and the mic just went back to normal (owner, 2026-11-04).
const QUIET_END_MS = 1500

export interface Dictation {
  supported: boolean
  listening: boolean
  problem: DictationProblem
  /** The browser's own error code, when `problem` is `other`. */
  problemCode: string
  /** The problem is news — it just happened — and is said beside the mic, not only in its
   *  tooltip: a mic that only turned yellow read as "dictation does nothing" (owner, 2026-11-03). */
  noticed: boolean
  toggle(): void
  stop(): void
  /** The message it was writing was sent: the browser's dictation ends, dropping what it hasn't
   *  written yet; the Mac's keeps listening (the mic stays red). */
  cancel(): void
}

/** `onText(text, final)`: everything this dictation has heard, revised until `final`.
 *  `focusBox`: the caret into the message box — the system's dictation types where it is. */
export function useDictation(
  onText: (text: string, final: boolean) => void,
  focusBox: () => void,
): Dictation {
  const [listening, setListening] = useState(false)
  const systemOn = useSystemListening()
  const [me] = useState(() => Symbol('dictation'))
  const [problem, setProblem] = useState<DictationProblem>(null)
  const [noticed, setNoticed] = useState(false)
  // The browser's own word for an error we have no plain words for (`other`).
  const [problemCode, setProblemCode] = useState('')
  const noticeTimer = useRef<number | undefined>(undefined)
  const rec = useRef<Recognition | null>(null)
  const sink = useRef(onText)
  useEffect(() => {
    sink.current = onText
  }, [onText])
  const Ctor = recognitionCtor()

  const dropped = useRef(false)
  const systemRef = useRef(false)
  useEffect(() => {
    systemRef.current = systemOn
  }, [systemOn])
  const stopSystem = useRef<() => void>(() => undefined)
  const stop = useCallback(() => {
    if (systemRef.current) stopSystem.current()
    rec.current?.stop()
  }, [])
  // The message it was writing was sent. The browser's dictation ends with it; the Mac's goes on
  // listening, the mic staying red until you tap it — its stop is not reliable at the moment of
  // sending, and dictating message after message is fine (owner, 2026-11-03).
  // Ended with stop() and its last words ignored — never abort(): on an iPhone an aborted
  // dictation left every later one on the page with the microphone on and no words (owner,
  // 2026-11-04: red, the phone listening, nothing typed after the first message).
  const cancel = useCallback(() => {
    if (!rec.current) return
    dropped.current = true
    rec.current.stop()
  }, [])
  useEffect(
    () => () => {
      dropped.current = true
      rec.current?.stop()
      window.clearTimeout(noticeTimer.current)
    },
    [],
  )
  // The tip has been read (or ignored): it goes, and the mic is its plain self again. A failure
  // never hides the mic — a browser that can't today may tomorrow; the next press says why
  // (owner, 2026-11-03: "always show that and show error instead").
  const settle = useCallback(() => {
    window.clearTimeout(noticeTimer.current)
    setNoticed(false)
    setProblem(null)
  }, [])
  const notice = useCallback(
    (next: DictationProblem, code = '') => {
      setProblem(next)
      setProblemCode(code)
      setNoticed(true)
      window.clearTimeout(noticeTimer.current)
      noticeTimer.current = window.setTimeout(settle, NOTICE_MS)
    },
    [settle],
  )
  // Typing or clicking anywhere is moving on: the tip doesn't sit over what you're doing.
  useEffect(() => {
    if (!noticed) return
    document.addEventListener('keydown', settle, true)
    document.addEventListener('pointerdown', settle, true)
    return () => {
      document.removeEventListener('keydown', settle, true)
      document.removeEventListener('pointerdown', settle, true)
    }
  }, [noticed, settle])

  const system = desktopClientBridge()?.startDictation ?? null
  // The desktop app ends it (its window resigns for a moment); the caret stays in the box.
  const endSystem = useCallback(() => {
    setSystemListening(false)
    void desktopClientBridge()
      ?.stopDictation?.()
      .catch(() => undefined)
  }, [])
  useEffect(() => {
    stopSystem.current = endSystem
  }, [endSystem])
  // Esc is macOS's own stop: the mic follows it. Nothing else is watched — macOS says nowhere
  // whether it is listening, and the caret leaving the box is also what the desktop app's own
  // start and stop do for a moment (owner, 2026-11-03).
  useEffect(() => {
    if (!systemOn) return
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setSystemListening(false)
    // The window coming back — macOS's "enable Dictation?" dialog approved, or a return from
    // another app — puts the caret back in the box, where dictation types: after the dialog it
    // listened with nowhere to write (owner, 2026-11-04).
    const back = () => systemOwner === me && focusBox()
    document.addEventListener('keydown', esc, true)
    window.addEventListener('focus', back)
    return () => {
      document.removeEventListener('keydown', esc, true)
      window.removeEventListener('focus', back)
    }
  }, [systemOn, focusBox, me])
  const toggle = useCallback(() => {
    if (system) {
      // A pressed mic stops it; a plain one starts it (owner, 2026-11-03).
      if (systemOn) {
        endSystem()
        return
      }
      focusBox()
      setSystemListening(true, me)
      system().catch(() => {
        setSystemListening(false)
        notice('system')
      })
      return
    }
    if (rec.current) {
      rec.current.stop()
      return
    }
    if (!Ctor) return
    // The WHOLE dictation, rebuilt from every result each time: browsers differ in how they
    // split it (Chrome adds a result per phrase, Safari grows one), and applying only the newest
    // left the box blank after the first message (owner, 2026-11-03).
    let heard = ''
    let said = false // a result or an error came: it did start
    const startedAt = Date.now()
    dropped.current = false
    const make = (): Recognition => {
      const r = new Ctor()
      r.lang = navigator.language || 'en-US'
      r.continuous = true
      r.interimResults = true
      r.onresult = (e) => {
        said = true
        const next = Array.from(e.results, (result) => result[0].transcript)
          .join(' ')
          .replace(/\s+/g, ' ')
          .trim()
        if (dropped.current) return
        heard = next
        sink.current(heard, false)
      }
      r.onerror = (e) => {
        said = true
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed') notice('blocked')
        // `network` is a browser with the API but no speech service behind it (Arc, Brave, the
        // Claude app's pane): trying again never helps there (owner, 2026-11-03).
        else if (e.error === 'network') notice('no-service')
        else if (e.error === 'audio-capture') notice('no-mic')
        else if (e.error === 'no-speech') notice('no-speech')
        // Ours: a sent message, a call. Anything else is said with the browser's own word for it.
        else if (e.error !== 'aborted') notice('other', e.error)
      }
      r.onend = () => {
        if (!dropped.current) sink.current(heard, true)
        if (rec.current === r) rec.current = null
        setListening(false)
        if (!said && !dropped.current && Date.now() - startedAt < QUIET_END_MS) notice('no-start')
      }
      return r
    }
    setProblem(null)
    setNoticed(false)
    // start() can throw — a previous one not quite closed (Safari, now and then): close it, and
    // try once more while this is still the tap that asked.
    for (let attempt = 0; attempt < 2; attempt++) {
      const r = make()
      rec.current = r
      try {
        r.start()
        setListening(true)
        return
      } catch {
        rec.current = null
        try {
          r.stop()
        } catch {
          // already closed
        }
      }
    }
    notice('no-start')
  }, [Ctor, notice, system, systemOn, focusBox, endSystem, me])

  return {
    supported: system !== null || Ctor !== null,
    listening: listening || systemOn,
    problem,
    problemCode,
    noticed,
    toggle,
    stop,
    cancel,
  }
}
