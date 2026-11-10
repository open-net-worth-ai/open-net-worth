import type { Message } from '../types/chat'

/** A bubble a server rebuild would never reproduce, so it is not content when comparing this
    browser's copy of a conversation with the transcript.

    - Empty: no blocks, no text, no attachments, no proposed changes. The synthetic "run died on its first
      call" stamp (assistant.rehydrate_messages, fixed 2026-08-16) is the canonical case — real
      on the client that saw the failure, absent from the server once a later turn succeeds.
    - A failure this client painted over nothing: an `error` with no blocks or cards, whatever
      its placeholder text. useChat paints "Sorry, something went wrong." when a send's POST
      throws — but a phone's "Load failed" can land AFTER the server accepted the message, so
      the transcript then holds the real reply and never this bubble (live report 2026-10-15).

    Counting either inflates the local side against the transcript, and the stale copy wins
    every catch-up. A photo sent with no caption is NOT empty: scored as one, the server's copy
    tied a watcher's on content and the watcher never showed it (live report 2026-10-26). */
export function isPlaceholderBubble(m: Message): boolean {
  if (m.blocks?.length || m.proposedChanges?.length || m.attachments?.length) return false
  return !m.text?.trim() || Boolean(m.error)
}

export function contentCount(messages: Message[] | null): number {
  return messages?.filter((m) => !isPlaceholderBubble(m)).length ?? 0
}

export function blockCount(messages: Message[] | null): number {
  return (messages ?? []).reduce((n, m) => n + (m.blocks?.length ?? 0), 0)
}
