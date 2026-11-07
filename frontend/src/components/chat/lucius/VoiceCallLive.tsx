import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { chatSessions } from '../../../state/chatSessions'
import type { Message } from '../../../types/chat'
import { useVoiceCall } from './voiceCall'
import { unheardAnswer, unsavedLines, useFollowBottom, useVoiceReveal } from './voiceReveal'

// What a call is saying right now, for whoever is looking: the chat streams it where the message
// will land, and the full call screen, which covers the chat, shows it above the mic. Not announced
// to a screen reader, as it changes with every word: the real messages are (owner, 2026-11-06).

function plainWords(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
}

/** The words you're saying, as a message at the end of the chat until the real one takes its place. */
export function VoiceCallHeardMessage({
  sessionId,
  lastUser,
}: {
  sessionId: string
  lastUser?: Pick<Message, 'id' | 'text'>
}) {
  const { heard, heardDone, sessionId: callSession } = useVoiceCall()
  const ref = useRef<HTMLDivElement>(null)
  // While you talk the newest message is an earlier one; a newer one after that is this one, even
  // when the saved words differ from the ones streamed.
  const before = useRef(lastUser?.id)
  if (!heardDone) before.current = lastUser?.id
  const sameWords = !!lastUser?.text && plainWords(lastUser.text).endsWith(plainWords(heard))
  const landed = !!heard && (sameWords || (heardDone && lastUser?.id !== before.current))
  const show = !!heard && callSession === sessionId && !landed
  useFollowBottom(ref, show ? heard : '')
  if (!show) return null
  return (
    <div ref={ref} className="msg-bubble msg-user lx-callheard-msg" aria-hidden="true">
      <div className="msg-text">{heard}</div>
    </div>
  )
}

type ChatLine = Pick<Message, 'role' | 'text' | 'ts'>

/** The lines heard that the chat doesn't have yet, either side of its newest answer. */
function useUnsavedLines(sessionId: string, messages: ChatLine[]) {
  const { spokenLines, connectedAt, sessionId: callSession } = useVoiceCall()
  return useMemo(() => {
    if (callSession !== sessionId || !spokenLines.length) return { above: [], below: [] }
    const his = messages
      .filter((m) => m.role === 'assistant' && (!m.ts || !connectedAt || m.ts >= connectedAt))
      .map((m) => m.text || '')
    return unsavedLines(spokenLines, his)
  }, [spokenLines, messages, connectedAt, callSession, sessionId])
}

function SpokenLines({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useFollowBottom(ref, lines.at(-1) ?? '')
  if (!lines.length) return null
  return (
    <div ref={ref} style={{ display: 'contents' }}>
      {lines.map((line, i) => (
        <div key={i} className="msg-bubble msg-assistant lx-callheard-msg" aria-hidden="true">
          <div className="msg-text">{line}</div>
        </div>
      ))}
    </div>
  )
}

/** Lines the voice said itself ("One sec", an answer to small talk) that came after the newest answer,
 *  or with no answer since: messages at the end of the chat until the real ones land. Lucius's own
 *  answers are already in the chat, and follow the voice there (``LastAnswerReveal``). */
export function VoiceCallSpokenMessage({
  sessionId,
  messages,
}: {
  sessionId: string
  messages: ChatLine[]
}) {
  const { above, below } = useUnsavedLines(sessionId, messages)
  // With your words the newest message, there's no answer for "above" to sit over yet.
  const lines = messages.at(-1)?.role === 'assistant' ? below : [...above, ...below]
  return <SpokenLines lines={lines} />
}

/** Lines said while Lucius worked on the answer below them. */
function SpokenAbove({ sessionId, messages }: { sessionId: string; messages: ChatLine[] }) {
  return <SpokenLines lines={useUnsavedLines(sessionId, messages).above} />
}

/** Draws the conversation's newest answer as far as Lucius has said it. */
function VoiceCallRevealed({
  message,
  sessionId,
  children,
}: {
  message: Message
  sessionId: string
  children: (message: Message) => ReactNode
}) {
  const shown = useVoiceReveal(message, sessionId)
  const ref = useRef<HTMLDivElement>(null)
  useFollowBottom(ref, shown.text)
  return (
    <div ref={ref} style={{ display: 'contents' }}>
      {children(shown)}
    </div>
  )
}

/** One message of the chat. Only the newest, if it's an answer, follows the voice: the hook wakes
 *  on every spoken word, so an older message must never carry it. */
export function LastAnswerReveal({
  message,
  messages,
  sessionId,
  live,
  children,
}: {
  message: Message
  messages: ChatLine[]
  sessionId: string
  live: boolean
  children: (message: Message) => ReactNode
}) {
  if (!live || message.role !== 'assistant') return <>{children(message)}</>
  return (
    <>
      <SpokenAbove sessionId={sessionId} messages={messages} />
      <VoiceCallRevealed message={message} sessionId={sessionId}>
        {children}
      </VoiceCallRevealed>
    </>
  )
}

const WORDS_LATE_MS = 1200

/** What to show while the voice talks and its words haven't come: the answer it's reading. */
function useAnswerWithoutWords(): string {
  const { luciusSpeaking, spoken, spokenLines, connectedAt, sessionId } = useVoiceCall()
  const id = sessionId ?? ''
  const subscribe = useCallback((cb: () => void) => chatSessions.subscribe(id, cb), [id])
  const slice = useSyncExternalStore(subscribe, () => chatSessions.getSlice(id))
  // The speaking state arrives just before the words do: this waits, or the whole answer flashed
  // up first on every line (test call, 2026-11-07).
  const noWords = luciusSpeaking && !spoken && !!id
  const [wordsLate, setWordsLate] = useState(false)
  useEffect(() => {
    if (!noWords) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWordsLate(false)
      return
    }
    const timer = window.setTimeout(() => setWordsLate(true), WORDS_LATE_MS)
    return () => window.clearTimeout(timer)
  }, [noWords])
  // Shown once for a line the voice said without words: the next such line isn't this answer.
  const showing = useRef('')
  const done = useRef('')
  if (!luciusSpeaking && showing.current) {
    done.current = showing.current
    showing.current = ''
  }
  if (!noWords || !wordsLate) return ''
  const his = slice.messages.filter(
    (m) => m.role === 'assistant' && (!m.ts || !connectedAt || m.ts >= connectedAt),
  )
  if (his.at(-1)?.streaming) return ''
  const answer = unheardAnswer(
    spokenLines,
    his.map((m) => m.text || ''),
  )
  if (answer === done.current) return ''
  if (answer) showing.current = answer
  return answer
}

/** The full call screen's caption, above the mic: you while you're talking, otherwise Lucius. */
export function VoiceCallCaption() {
  const { heard, heardShown, spoken } = useVoiceCall()
  const withoutWords = useAnswerWithoutWords()
  const yours = heardShown && !!heard
  const text = yours ? heard : spoken || withoutWords
  const box = useRef<HTMLDivElement>(null)
  const [long, setLong] = useState(false)
  // Measured, as how many lines some words take depends on the screen and the language.
  useLayoutEffect(() => {
    const el = box.current
    const words = el?.firstElementChild as HTMLElement | null | undefined
    setLong(!!el && !!words && words.offsetHeight > el.clientHeight)
  }, [text])
  if (!text) return null
  return (
    <div
      ref={box}
      className="lx-callcaption"
      data-who={yours ? 'you' : 'lucius'}
      data-long={long || undefined}
      aria-hidden="true"
    >
      <span>{text}</span>
    </div>
  )
}
