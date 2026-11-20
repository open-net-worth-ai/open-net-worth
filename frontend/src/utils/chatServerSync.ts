import type { Message } from '../types/chat'
import { fetchServerSnapshot, positionalCounter, reconcileAgainst, saveMessages } from './chatStore'

/** What one read of the server's transcript means for this browser's copy.
 *  `authoritative`: the conversation is finished and `messages` IS it — show it as is.
 *  Otherwise `messages` is the best guess for a conversation still being written, or null
 *  when this browser's copy should stay. */
export interface ServerSync {
  messages: Message[] | null
  authoritative: boolean
  /** Client ids of the messages the server holds — settles this browser's outbox. */
  received: Set<string>
}

/** Read the transcript and decide. The one entry point for every catch-up: hydration,
 *  the end of a live attach, a return to the app. */
export async function syncWithServer(
  convId: string,
  local: Message[] | null,
): Promise<ServerSync | null> {
  if (!convId) return null
  const snap = await fetchServerSnapshot(convId)
  if (!snap) return null
  const received = serverClientIds(snap.messages)
  if (snap.live === false && snap.messages.length > 0) {
    const messages = takeServerTranscript(convId, local, snap.messages, received)
    void saveMessages(convId, messages)
    return { messages, authoritative: true, received }
  }
  return {
    messages: reconcileAgainst(convId, local, snap.messages),
    authoritative: false,
    received,
  }
}

/** When no turn is running, the server's transcript is the whole, finished conversation —
 *  so this browser takes it as it is. No "which copy is richer" judgement: that guessing
 *  is what let a stale copy win over a finished answer, on the phone and after a restart
 *  (live report 2026-11-18). ADR
 *  `2026-11-18-a-finished-conversation-is-the-servers-transcript`.
 *
 *  `remote` is a TAIL. Messages older than it are kept from `local` only when they carry
 *  the server's own positional id (`{conv}-{n}`) below the tail's first — the only ones
 *  that can be placed exactly. Anything else this browser drew (a live-born `a_…` bubble,
 *  a message it typed) is superseded by the server's copy of the same exchange; history
 *  older than the tail that this browser holds only under live-born ids is re-fetched by
 *  paging, which keys off the server ids this leaves at the top. */
export function takeServerTranscript(
  convId: string,
  local: Message[] | null,
  remote: Message[],
  received: Set<string> = serverClientIds(remote),
): Message[] {
  const first = remote.length ? positionalCounter(convId, remote[0]!.id) : null
  const older =
    first === null || first <= 1
      ? []
      : (local ?? []).filter((m) => {
          const n = positionalCounter(convId, m.id)
          return n !== null && n < first
        })
  return [
    ...keepLocalOnly(local, [...older, ...remote]),
    ...unreceivedFailure(convId, local, received),
  ]
}

/** A send the server never got — this browser's user bubble, followed by nothing or only
 *  an error bubble: the server's copy cannot hold it, so it stays, after the server's
 *  messages, with its Retry (or for `settleUnconfirmedSend` to paint as failed). Exact by
 *  id: a message the server holds is named in `received`. */
function unreceivedFailure(
  convId: string,
  local: Message[] | null,
  received: Set<string>,
): Message[] {
  if (!local?.length) return []
  let i = local.length - 1
  while (i >= 0 && local[i]!.role === 'assistant' && local[i]!.error) i--
  const ask = local[i]
  if (!ask || ask.role !== 'user') return []
  if (positionalCounter(convId, ask.id) !== null || received.has(ask.id)) return []
  return local.slice(i)
}

/** The one thing a server rebuild cannot draw: the "set up a provider" hint on a failed
 *  turn's error bubble, decided by the live error event. Kept when the server's copy still
 *  ends on that same failure. */
function keepLocalOnly(local: Message[] | null, merged: Message[]): Message[] {
  const mine = local?.at(-1)
  const theirs = merged.at(-1)
  if (!mine?.setupHint || !mine.error || !theirs?.error || theirs.setupHint) return merged
  return [...merged.slice(0, -1), { ...theirs, setupHint: true }]
}

/** Client ids of every message the server holds, as its transcript names them. */
export function serverClientIds(messages: Message[]): Set<string> {
  const ids = new Set<string>()
  for (const m of messages) if (m.clientId) ids.add(m.clientId)
  return ids
}
