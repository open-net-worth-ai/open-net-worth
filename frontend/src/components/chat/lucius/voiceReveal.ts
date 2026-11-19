import { useEffect, useRef, useState } from 'react'
import type { Message } from '../../../types/chat'
import { useVoiceCall } from './voiceCall'

// Lucius's answer shows in the chat as he says it, not all at once before the voice reaches it
// (owner, 2026-11-06). The chat's answer is written down whole; the words spoken are a cleaned copy
// (no markdown, no table rows), so the two are matched on letters and digits alone, which also
// works for a language written without spaces. Never lets an answer stay hidden: it shows whole
// when the voice is done, moves on or stalls, when the voice never starts, and when the call ends.

const WORD_CHAR = /[\p{L}\p{N}\p{M}]/u
const MATCH_CHARS = 12
/** Past this a line is safely an answer even if the rest differs from what's written. */
const LONG_LINE_CHARS = 24
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

/** Which chat message each line heard already is, if any. Each message counts once, so a second "One
 *  sec" isn't taken for the first. Exact matches go first, then the longest lines: a line is what's
 *  been said so far of its message, and a long answer is spoken as a cleaned copy of it. */
function savedAs(lines: string[], messages: string[]): (number | null)[] {
  const said = lines.map(plain)
  const saved = messages.map(plain)
  const found: (number | null)[] = lines.map(() => null)
  const taken = new Set<number>()
  const claim = (i: number, fits: (message: string, line: string) => boolean) => {
    const at = saved.findIndex((m, j) => !taken.has(j) && fits(m, said[i] ?? ''))
    if (at >= 0) {
      taken.add(at)
      found[i] = at
    }
  }
  said.forEach((_, i) => claim(i, (m, line) => m === line))
  const longest = said
    .map((_, i) => i)
    .sort((a, b) => (said[b] ?? '').length - (said[a] ?? '').length)
  for (const i of longest) {
    if (found[i] !== null) continue
    claim(
      i,
      (m, line) =>
        m.startsWith(line) ||
        (line.length >= LONG_LINE_CHARS && m.startsWith(line.slice(0, MATCH_CHARS))),
    )
  }
  return found
}

/** The lines heard on a call that the chat doesn't have yet, either side of its newest message from
 *  Lucius: a line said while he works only lands once he's done (owner, 2026-11-06). That message,
 *  once heard, is the divider. Not heard yet, it's still to come, so everything so far goes above it,
 *  as the server will place it. `messages` are his since the call began, an empty one still writing. */
export function unsavedLines(
  lines: string[],
  messages: string[],
): { above: string[]; below: string[] } {
  const said = lines.filter((line) => line.trim())
  const found = savedAs(said, messages)
  const newest = messages.length - 1
  const divider = found.lastIndexOf(newest)
  const waiting = (from: number, to: number) =>
    said.slice(from, to).filter((_, i) => found[from + i] === null)
  if (divider >= 0) return { above: waiting(0, divider), below: waiting(divider + 1, said.length) }
  return messages.length
    ? { above: waiting(0, said.length), below: [] }
    : { above: [], below: waiting(0, said.length) }
}

/** His newest answer, if none of it has been seen as he said it. For a voice talking with no words
 *  arriving: `lk.transcription` sometimes sends none for a line (owner, 2026-11-07). */
export function unheardAnswer(lines: string[], messages: string[]): string {
  const newest = messages.length - 1
  const answer = messages[newest]
  if (!answer?.trim()) return ''
  const said = lines.filter((line) => line.trim())
  return savedAs(said, messages).includes(newest) ? '' : answer
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
