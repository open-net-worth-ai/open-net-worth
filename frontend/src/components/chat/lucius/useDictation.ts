import { useCallback, useEffect, useRef, useState } from 'react'
import { desktopBridge } from '../../../platform/desktop'

// Dictation into the message box with the BROWSER's own speech recognition (owner, 2026-11-03):
// free, no keys, the audio handled by the browser's provider (Google in Chrome and Edge, Apple
// in Safari). Not offered where it can't work: a browser without it, and the desktop app, whose
// built-in browser has the API but no speech service behind it.

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

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined' || desktopBridge()) return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const NOTICE_MS = 6000

export type DictationProblem = 'blocked' | 'no-service' | 'no-mic' | null

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

/** `onText(text, final)`: everything this dictation has heard, revised until `final`. */
export function useDictation(onText: (text: string, final: boolean) => void): Dictation {
  const [listening, setListening] = useState(false)
  const [problem, setProblem] = useState<DictationProblem>(null)
  const [noticed, setNoticed] = useState(false)
  const noticeTimer = useRef<number | undefined>(undefined)
  const rec = useRef<Recognition | null>(null)
  const sink = useRef(onText)
  useEffect(() => {
    sink.current = onText
  }, [onText])
  const Ctor = recognitionCtor()

  const stop = useCallback(() => rec.current?.stop(), [])
  const dropped = useRef(false)
  const cancel = useCallback(() => {
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
  const notice = useCallback((next: DictationProblem) => {
    setProblem(next)
    setNoticed(true)
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNoticed(false), NOTICE_MS)
  }, [])

  const toggle = useCallback(() => {
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
  }, [Ctor, notice])

  return { supported: Ctor !== null, listening, problem, noticed, toggle, stop, cancel }
}
