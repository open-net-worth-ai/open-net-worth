import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import type { Message } from '../../../types/chat'
import { useVoiceCall } from './voiceCall'
import { spokenPrefix, useFollowBottom, useVoiceReveal } from './voiceReveal'

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

/** A line the voice says itself ("One sec", an answer to small talk), as a message at the end of the
 *  chat while it's said, until the real one lands. Lucius's own answers are already in the chat, and
 *  follow the voice there (``LastAnswerReveal``). */
export function VoiceCallSpokenMessage({
  sessionId,
  lastAssistant,
}: {
  sessionId: string
  lastAssistant?: Pick<Message, 'id' | 'text'>
}) {
  const { lastSpoken, sessionId: callSession } = useVoiceCall()
  const ref = useRef<HTMLDivElement>(null)
  const inChat = !!lastAssistant?.text && spokenPrefix(lastAssistant.text, lastSpoken) !== null
  const show = !!lastSpoken && callSession === sessionId && !inChat
  useFollowBottom(ref, show ? lastSpoken : '')
  if (!show) return null
  return (
    <div ref={ref} className="msg-bubble msg-assistant lx-callheard-msg" aria-hidden="true">
      <div className="msg-text">{lastSpoken}</div>
    </div>
  )
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
  sessionId,
  live,
  children,
}: {
  message: Message
  sessionId: string
  live: boolean
  children: (message: Message) => ReactNode
}) {
  if (!live || message.role !== 'assistant') return <>{children(message)}</>
  return (
    <VoiceCallRevealed message={message} sessionId={sessionId}>
      {children}
    </VoiceCallRevealed>
  )
}

/** The full call screen's caption, above the mic: you while you're talking, otherwise Lucius. */
export function VoiceCallCaption() {
  const { heard, heardDone, spoken } = useVoiceCall()
  // Your words stay in the chat until they land, but here Lucius takes over once you've finished.
  const yours = !!heard && !(heardDone && spoken)
  const text = yours ? heard : spoken
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
