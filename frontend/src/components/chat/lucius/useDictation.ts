import { useCallback, useEffect, useRef, useState } from 'react'
import { desktopBridge, desktopClientBridge } from '../../../platform/desktop'

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

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined' || inDesktopApp()) return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const NOTICE_MS = 6000
export type DictationProblem = 'blocked' | 'no-service' | 'no-mic' | 'system' | null

export interface Dictation {
  supported: boolean
  listening: boolean
  problem: DictationProblem
  /** The problem is news — it just happened — and is said beside the mic, not only in its
   *  tooltip: a mic that only turned yellow read as "dictation does nothing" (owner, 2026-11-03). */
  noticed: boolean
  toggle(): void
  stop(): void
  /** End it and drop what it hasn't written yet — the message it was writing has been sent. */
  cancel(): void
}

/** `onText(text, final)`: everything this dictation has heard, revised until `final`.
 *  `focusBox`: the caret into the message box — the system's dictation types where it is. */
export function useDictation(
  onText: (text: string, final: boolean) => void,
  focusBox: () => void,
): Dictation {
  const [listening, setListening] = useState(false)
  // The Mac's own dictation, started from here and not yet ended. macOS says nothing when it
  // stops on its own, so this also follows what ends it there: the caret leaving the box, or Esc.
  const [systemOn, setSystemOn] = useState(false)
  const [problem, setProblem] = useState<DictationProblem>(null)
  const [noticed, setNoticed] = useState(false)
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
  const cancel = useCallback(() => {
    if (systemRef.current) stopSystem.current()
    if (!rec.current) return
    dropped.current = true
    rec.current.abort()
  }, [])
  useEffect(
    () => () => {
      rec.current?.abort()
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
    (next: DictationProblem) => {
      setProblem(next)
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
    setSystemOn(false)
    void desktopClientBridge()
      ?.stopDictation?.()
      .catch(() => undefined)
  }, [])
  useEffect(() => {
    stopSystem.current = endSystem
  }, [endSystem])
  useEffect(() => {
    if (!systemOn) return
    const box = document.activeElement
    const off = () => endSystem()
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setSystemOn(false)
    box?.addEventListener('blur', off)
    document.addEventListener('keydown', esc, true)
    return () => {
      box?.removeEventListener('blur', off)
      document.removeEventListener('keydown', esc, true)
    }
  }, [systemOn, endSystem])
  const toggle = useCallback(() => {
    if (system) {
      // Pressing again ends it: starting macOS's dictation a second time does not (owner,
      // 2026-11-03).
      if (systemOn) {
        endSystem()
        return
      }
      focusBox()
      setSystemOn(true)
      system().catch(() => {
        setSystemOn(false)
        notice('system')
      })
      return
    }
    if (rec.current) {
      rec.current.stop()
      return
    }
    if (!Ctor) return
    const r = new Ctor()
    r.lang = navigator.language || 'en-US'
    r.continuous = true
    r.interimResults = true
    // The WHOLE dictation, rebuilt from every result each time: browsers differ in how they
    // split it (Chrome adds a result per phrase, Safari grows one), and applying only the newest
    // left the box blank after the first message (owner, 2026-11-03).
    let heard = ''
    dropped.current = false
    r.onresult = (e) => {
      if (dropped.current) return
      heard = Array.from(e.results, (result) => result[0].transcript)
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim()
      sink.current(heard, false)
    }
    r.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') notice('blocked')
      // `network` is a browser with the API but no speech service behind it (Arc, Brave, the
      // Claude app's pane): trying again never helps there (owner, 2026-11-03).
      else if (e.error === 'network') notice('no-service')
      else if (e.error === 'audio-capture') notice('no-mic')
    }
    r.onend = () => {
      if (!dropped.current) sink.current(heard, true)
      rec.current = null
      setListening(false)
    }
    rec.current = r
    setProblem(null)
    setNoticed(false)
    setListening(true)
    r.start()
  }, [Ctor, notice, system, systemOn, focusBox, endSystem])

  return {
    supported: system !== null || Ctor !== null,
    listening: listening || systemOn,
    problem,
    noticed,
    toggle,
    stop,
    cancel,
  }
}
