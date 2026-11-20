import { chatSessions, type SentPayload } from './chatSessions'

// The outbox for messages sent while Lucius is busy. Each one is held in
// `ConvSlice.queuedMessages` until the server confirms it — and it never stays "queued"
// on a guess. A send the server never answered, and that a finished transcript does not
// name (`Message.clientId`), turns `failed` with Retry; Retry resends under the SAME id,
// which the server answers as a duplicate if it had the message after all (live report
// 2026-11-18: a question sent as a phone suspended sat at "Still queued" forever, never
// having reached the server).

/** Sends whose POST has not returned yet — never marked failed by a transcript read that
 *  merely raced their 202. */
const inFlight = new Set<string>()
/** What each outbox message was sent with, so Retry resends it exactly. */
const payloads = new Map<string, SentPayload>()

export function noteSending(id: string, payload: SentPayload): void {
  inFlight.add(id)
  payloads.set(id, payload)
}

export function noteSent(id: string): void {
  inFlight.delete(id)
}

export function payloadFor(id: string): SentPayload | undefined {
  return payloads.get(id)
}

/** The send failed, or nothing can say it arrived: show it with Retry. */
export function failQueued(ownerId: string, id: string): void {
  inFlight.delete(id)
  chatSessions.apply(ownerId, (s) => {
    if (!s.queuedMessages.some((m) => m.id === id && m.pending !== 'failed')) return s
    return {
      ...s,
      queuedMessages: s.queuedMessages.map((m) =>
        m.id === id ? { ...m, pending: 'failed' as const } : m,
      ),
    }
  })
}

/** Take a message out of the outbox (to resend it). */
export function withdrawQueued(ownerId: string, id: string): void {
  chatSessions.apply(ownerId, (s) =>
    s.queuedMessages.some((m) => m.id === id)
      ? { ...s, queuedMessages: s.queuedMessages.filter((m) => m.id !== id) }
      : s,
  )
}

/** A FINISHED transcript was just adopted. Whatever it names is delivered — it is on
 *  screen in its true place, so it leaves the outbox. Whatever it does not name, and is
 *  not still being sent, never reached the server: it fails, visibly. */
export function settleOutbox(ownerId: string, received: Set<string>): void {
  for (const id of received) payloads.delete(id)
  chatSessions.apply(ownerId, (s) => {
    if (s.queuedMessages.length === 0) return s
    const kept = s.queuedMessages
      .filter((m) => !received.has(m.id))
      .map((m) =>
        m.pending === 'queued' && !inFlight.has(m.id) ? { ...m, pending: 'failed' as const } : m,
      )
    const same =
      kept.length === s.queuedMessages.length && kept.every((m, i) => m === s.queuedMessages[i])
    return same ? s : { ...s, queuedMessages: kept }
  })
}

export function _resetOutboxForTests(): void {
  inFlight.clear()
  payloads.clear()
}
