// chatSessions.ts — per-conversation chat state, a vanilla external store, NOT React state.
//
// RFC .claude/rfcs/concurrent-conversations.md (Phase 1) · ADR
// docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md
//
// A turn belongs to the conversation that started it, never to whichever conversation is
// on screen. Before this, the single useChat() instance held ONE messages array
// parameterized by the VIEWED conversation id, so a turn's SSE reducer wrote into whatever
// the user had switched to — conversation A's tokens landing on conversation B's bubble.
// Every conversation now owns its OWN slice, keyed by conversation id; the view is a
// SELECTOR over a slice, never its owner.
//
// Why an external store and not `useState<Map<id, ConvSlice>>`: a new Map identity on
// every token re-renders every context consumer — dock, panel, mobile shell, every
// message bubble — for a single delta in ONE conversation. `subscribe(id, cb)` notifies
// ONLY the subscribers of the slice that actually changed; `subscribeAll` is a second,
// coarser channel for STATUS transitions only (streaming started/ended), so a token
// landing in a background conversation never wakes a component that only cares "who is
// working right now" (the sidebar's live dots).

import type { Attachment, Message } from '../types/chat'
import type { MentionRef } from '../types/mentions'
import type { ScreenSnapshotWire } from './screenSnapshot'

export type TurnStatus = 'idle' | 'streaming' | 'awaiting_approval' | 'error'

/** Full context of the last user-initiated send in this conversation, so Retry can
 *  re-run the SAME turn (same mentions/agent/skills/screen), not a stripped-down version. */
export interface SentPayload {
  text: string
  attachments: Attachment[]
  effort: string
  model: string
  mentions: MentionRef[]
  respondingAgent: string | null
  skills: string[]
  screen: ScreenSnapshotWire | null
}

export interface ConvSlice {
  /** Owner id — the authority for every write. Always equal to the map key it lives under. */
  id: string
  messages: Message[]
  hydrated: boolean
  status: TurnStatus
  /** A send is in flight but its 202 has not landed yet.
   *
   *  `status` cannot cover this: it only flips to 'streaming' AFTER POST /api/chat
   *  resolves, and that POST first builds the system prompt, runs auto-model routing and
   *  resolves mention/screen context — seconds, not milliseconds. During that window the
   *  conversation IS busy but nothing said so, and every consumer read `busy === false`:
   *  the composer kept offering Send instead of Stop, a second send skipped its "Queued"
   *  chip, and — worst — `if (!busy)` opened a SECOND assistant bubble whose spinner
   *  nothing ever cleared (the attach's cleanup only clears the LAST assistant message).
   *  Set synchronously before the fetch, cleared when it settles. Deliberately NOT part of
   *  `shouldAttach`: attaching still waits for the 202, which is what guarantees the
   *  server's live episode is open first. */
  sending: boolean
  /** Did THIS browser start the in-flight turn? Distinguishes a turn you sent from one
   *  you are merely watching (an Automation run, another tab) — which is what decides
   *  whether Stop may be offered. Cannot be derived from `status` any more: following now
   *  sets 'streaming' too, so "is it running" and "is it mine" are separate facts. */
  startedHere: boolean
  /** Bumped every time this browser kicks off a turn (sendMessage, resolvePendingToolUse).
   *
   *  useChat's attach effect keys on `shouldAttach = hydrated && (streaming ||
   *  liveSessions.includes(id))` — a boolean OR of two independently-updated signals.
   *  React only re-runs an effect when a dependency's VALUE changes, and `liveSessions` is
   *  refreshed by its own 4s poll: if it already reports this conversation live at the
   *  exact moment `streaming` flips false→true (approving a card right as the poll landed),
   *  the OR'd boolean never toggles, so no fresh `GET /live` opens for the new turn — the
   *  old connection already closed when the previous one paused. The server finishes,
   *  correctly calls `live_sessions.end()`, and nobody is listening. Included directly in
   *  the effect's dependency array so a new turn always forces a fresh attach regardless
   *  of what the OR'd boolean happens to read. */
  attachGen: number
  /** Output tokens so far on the RUNNING turn — the server's accumulated figure
   *  (`turn_progress`), never a client-side estimate. Reset when a turn starts. */
  liveOutputTokens: number
  /** Non-null only during the pre-turn context-fold ("compacting") phase — set on
   *  `compacting_start`, updated on each `compacting_progress`, cleared on
   *  `compacting_done` (or defensively on `turn_done`, in case one was dropped by a
   *  reconnect). `percent`/`outputTokens` are real, server-reported figures — see
   *  CompactingActivity and backend/ai/assistant.py::_generate_fold_summary. */
  compacting: { percent: number; outputTokens?: number } | null
  abort: AbortController | null
  suggestions: string[]
  suggestionsLoading: boolean
  lastSent: SentPayload | null
  // Outcome of the last turn that actually finished, independent of `status` (which
  // resets to 'idle'/'awaiting_approval' once the request settles). Cleared to 'none' the
  // instant this conversation becomes the viewed one — "an answer you're already looking
  // at is not unread". Beyond the RFC's original shape — added per
  // .claude/rfcs/concurrent-conversations-ui-spec.md §1.2, which drives the conversation
  // list's done/failed row status from this field.
  lastResult: 'none' | 'ok' | 'error'
  /** The id of the assistant message a LIVE round's output is currently landing on, or
   *  null if no round is open. THE single source of truth for "where does the next
   *  token go" — replaces inferring it from array position (a queued send appends
   *  after the live bubble) or from scanning `streaming` flags (which a torn-down
   *  reconnect leaves in whatever state the last write left them, and which nothing
   *  ties to turn/round BOUNDARIES on its own).
   *
   *  Set the moment a bubble is created or resumed (ensureAssistant, reviveOwningMessage,
   *  resolvePendingToolUse). Cleared ONLY at a genuine boundary — turn_done (the round is
   *  over) or message_delivered (an injected message is ALWAYS its own exchange; the
   *  server drains it at a natural checkpoint between rounds, so whatever comes next is a
   *  new round even though the OUTER turn is still running and no turn_done fires). A
   *  reconnect's fresh `consumeEventStream` call does NOT touch this — it lives on the
   *  slice, not the connection, so it correctly survives a torn-down/reopened attach and
   *  keeps pointing at the same bubble until an actual boundary clears it. Before this,
   *  every one of those signals was inferred ad hoc and each new interaction pattern
   *  (queuing, injection, reconnect replay) needed its own special case to keep guessing
   *  right — this pointer is the one explicit fact all of them read and write instead.
   *  ADR docs/adr/2026-08-10-open-assistant-id-is-the-live-round-pointer.md. */
  openAssistantId: string | null
  /** Sent while a turn was already running, not yet spliced into it server-side — its
   *  OWN list, never a flag on `messages`. `messages` is the true, ORDERED timeline; a
   *  message you type gets a guaranteed array slot the instant you hit send, while its
   *  answer only gets one once it actually finishes generating. Storing a queued send in
   *  `messages` immediately meant the send always won that race: type two questions
   *  before the first is answered and BOTH land ahead of the first answer, which then
   *  gets appended after them — the array reads Q1,Q2,A1,Q3,A2,A3 even though the
   *  backend answered in perfect Q1,A1,Q2,A2,Q3,A3 order (confirmed against the raw
   *  session transcript). A message here moves into `messages` — appended at whatever
   *  is CURRENTLY the end, which is exactly its correct position — the instant it is
   *  actually delivered (`message_delivered`, or the `accepted: "started"` race path;
   *  see `deliverQueuedMessage`), never at send time. */
  queuedMessages: Message[]
  /** Set by `resolvePendingToolUse`, synchronously, right before it bumps `attachGen` to
   *  trigger the resumed connection — and read-and-cleared by that connection alone, once.
   *
   *  The one bit `consumeEventStream`'s replay-dedup fallback (chatReducer.ts) cannot get
   *  from `openAssistantId` by itself: that pointer is ALSO left set (deliberately, for
   *  continuity) when one attach is superseded by another reconnect to the same still-live
   *  turn — so "already set" cannot tell a genuine resume (this connection's tokens are
   *  brand new, must never be skipped) apart from a plain reconnect (this connection's
   *  tokens may be a replay, must be dedup-checked). Live report: asking a question in a
   *  fresh conversation rendered a truncated copy of the answer immediately followed by the
   *  complete one — the reconnect's replay was never recognized as a replay because
   *  `openAssistantId` was already set by the superseded connection, not by a deliberate
   *  resume. */
  resumeInFlight: boolean
  /** A silent transcript re-read is in flight — the server check after a cache hydrate, or
   *  the resync on coming back to the app. Drives the delayed "Catching up…" line; never
   *  the working indicator, since no turn is running. */
  catchingUp: boolean
  /** A send whose POST failed on the NETWORK (a fetch TypeError, no HTTP status) — which
   *  says nothing about whether the server got it: a phone's "Load failed" routinely lands
   *  after the server accepted the message and started the turn (live report 2026-10-15).
   *  Holds the error until the attach that checks for that turn ends; only then, and only
   *  if the message is still unanswered, is the failure painted. */
  unconfirmedSend: { error: string } | null
  /** Epoch ms timestamp when the active turn (or compacting phase) started.
   *  Persists across component mount/unmount so in-progress timers don't reset. */
  startedAt?: number
}

function emptySlice(id: string): ConvSlice {
  return {
    id,
    messages: [],
    hydrated: false,
    status: 'idle',
    sending: false,
    startedHere: false,
    attachGen: 0,
    liveOutputTokens: 0,
    compacting: null,
    abort: null,
    suggestions: [],
    suggestionsLoading: false,
    lastSent: null,
    lastResult: 'none',
    openAssistantId: null,
    queuedMessages: [],
    resumeInFlight: false,
    catchingUp: false,
    unconfirmedSend: null,
    startedAt: undefined,
  }
}

const slices = new Map<string, ConvSlice>()
const subscribers = new Map<string, Set<() => void>>()
const globalSubscribers = new Set<() => void>()
let streamingSnapshot: string[] = []

/** Get (creating if needed) a conversation's slice. Stable reference across calls until
 *  `apply` next replaces it — this is what lets useSyncExternalStore bail out cleanly. */
function getSlice(id: string): ConvSlice {
  let slice = slices.get(id)
  if (!slice) {
    slice = emptySlice(id)
    slices.set(id, slice)
  }
  return slice
}

/** Read-only lookup that does NOT create a slice — for scanning many ids (e.g. a
 *  conversation list computing each row's status) without leaving a permanent empty
 *  entry behind for every conversation merely glanced at. */
function peekSlice(id: string): ConvSlice | undefined {
  return slices.get(id)
}

/** Subscribe to ONE conversation's slice. Only fires for writes to THAT id — a token
 *  landing in conversation A never wakes a subscriber bound to B. */
function subscribe(id: string, cb: () => void): () => void {
  let set = subscribers.get(id)
  if (!set) {
    set = new Set()
    subscribers.set(id, set)
  }
  set.add(cb)
  return () => {
    set.delete(cb)
    if (set.size === 0) subscribers.delete(id)
  }
}

/** Subscribe to STATUS transitions across every conversation (streaming started/ended).
 *  Deliberately NOT fired on message content — a text_delta must never wake a listener
 *  that only cares which conversations are working right now. */
function subscribeAll(cb: () => void): () => void {
  globalSubscribers.add(cb)
  return () => {
    globalSubscribers.delete(cb)
  }
}

function recomputeStreamingSnapshot(): void {
  const ids = Array.from(slices.values())
    .filter((s) => s.status === 'streaming')
    .map((s) => s.id)
    .sort()
  const unchanged =
    ids.length === streamingSnapshot.length && ids.every((v, i) => v === streamingSnapshot[i])
  if (!unchanged) streamingSnapshot = ids
}

/** Snapshot for useSyncExternalStore — a stable reference while the streaming SET is
 *  unchanged, even though individual slices keep mutating underneath it. */
function getStreamingIdsSnapshot(): string[] {
  return streamingSnapshot
}

function statusFor(id: string): TurnStatus {
  return slices.get(id)?.status ?? 'idle'
}

/** Apply an update to ONE conversation's slice. `fn` returning the SAME reference is a
 *  no-op — no notify, no re-render, same as a reducer bailing on no change. */
function apply(id: string, fn: (s: ConvSlice) => ConvSlice): void {
  const prev = getSlice(id)
  const next = fn(prev)
  if (next === prev) return
  slices.set(id, next)
  const statusChanged = next.status !== prev.status
  if (statusChanged) recomputeStreamingSnapshot()
  for (const cb of subscribers.get(id) ?? []) cb()
  if (statusChanged) for (const cb of globalSubscribers) cb()
}

export const chatSessions = {
  getSlice,
  peekSlice,
  subscribe,
  subscribeAll,
  apply,
  statusFor,
  getStreamingIdsSnapshot,
}

// DEV-ONLY manual test hook for CompactingActivity — there is no real way to trigger a
// fold on demand yet (needs 60+ messages + real token pressure; a real /compact command
// is a separate, not-yet-built feature). Vite strips this whole block from a production
// build (`import.meta.env.DEV` is statically false there), so it never ships.
//
// Console usage, with the compacting bar's conversation open:
//   __mockCompacting(35)        → 35%, no token count
//   __mockCompacting(35, 820)   → 35% · ↓ 820 tokens
//   __mockCompacting()          → clears it (same as a real compacting_done)
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(
    window as unknown as { __mockCompacting: (percent?: number, outputTokens?: number) => void }
  ).__mockCompacting = (percent?: number, outputTokens?: number) => {
    const value = typeof percent === 'number' ? { percent, outputTokens } : null
    for (const id of slices.keys()) apply(id, (s) => ({ ...s, compacting: value }))
  }
}
