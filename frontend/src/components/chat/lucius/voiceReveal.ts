import { useEffect, useRef, useState, type RefObject } from 'react'
import type { Message } from '../../../types/chat'
import { useVoiceCall } from './voiceCall'

// Lucius's answer shows in the chat as he says it, not all at once before the voice reaches it
// (owner, 2026-11-06). The chat's answer is written down whole; the words spoken are a cleaned copy
// (no markdown, no table rows), so the two are matched on letters and digits alone, which also
// works for a language written without spaces. Never lets an answer stay hidden: it shows whole
// when the voice is done, moves on or stalls, when the voice never starts, and when the call ends.

const WORD_CHAR = /[\p{L}\p{N}\p{M}]/u
const MATCH_CHARS = 12
/** How long a finished answer may wait for the voice to start. */
const VOICE_START_MS = 4000
/** How long the voice may go without a new word before the rest is shown. */
const VOICE_STALL_MS = 6000

function plain(text: string): string {
  return [...text]
    .filter((c) => WORD_CHAR.test(c))
    .join('')
    .toLowerCase()
}

/** The part of `text` said so far, or null when `spoken` isn't this text. */
export function spokenPrefix(text: string, spoken: string): string | null {
  const said = plain(spoken)
  if (!said || !plain(text).startsWith(said.slice(0, MATCH_CHARS))) return null
  const wanted = [...said].length
  let seen = 0
  let end = 0
  for (const c of text) {
    end += c.length
    if (WORD_CHAR.test(c)) seen += 1
    if (seen >= wanted) return text.slice(0, end)
  }
  return text
}

/** The message as the chat should draw it now. Only for the conversation's newest answer. */
export function useVoiceReveal(message: Message, sessionId: string): Message {
  const call = useVoiceCall()
  const onCall = call.phase === 'live' && call.sessionId === sessionId
  // Already there when the call began, or nothing to wait for: it never holds back.
  const [finished, setFinished] = useState(!onCall)
  const matched = useRef(false)
  const text = message.text || ''
  const prefix = !finished && onCall ? spokenPrefix(text, call.spoken) : null
  if (prefix !== null) matched.current = true
  const blocked = !!message.error || !!message.awaitingApproval

  useEffect(() => {
    if (finished) return
    const movedOn = matched.current && prefix === null
    if (!onCall || blocked || movedOn || (prefix !== null && call.spokenDone)) {
      setFinished(true)
      return
    }
    if (message.streaming) return
    const wait = matched.current ? VOICE_STALL_MS : VOICE_START_MS
    const timer = window.setTimeout(() => setFinished(true), wait)
    return () => window.clearTimeout(timer)
  }, [finished, onCall, blocked, prefix, call.spokenDone, message.streaming])

  if (finished || blocked) return message
  return { ...message, text: prefix ?? '', streaming: true }
}

/** Keeps a growing message in view, unless you've scrolled up to read something else. */
export function useFollowBottom(ref: RefObject<HTMLElement | null>, content: string) {
  useEffect(() => {
    let el = ref.current?.parentElement ?? null
    while (el && !/(auto|scroll)/.test(getComputedStyle(el).overflowY)) el = el.parentElement
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight
  }, [ref, content])
}
