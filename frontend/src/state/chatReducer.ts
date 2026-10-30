// chatReducer.ts — message/event mutations over chatSessions.ts, all owner-parameterized.
//
// Every function here takes an explicit `ownerId` and writes ONLY to that conversation's
// slice (`chatSessions.apply(ownerId, …)`). None of these close over "the current
// conversation" — that closure is exactly the defect this RFC exists to remove (see
// docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md). A caller
// (useChat.ts) captures `ownerId` once, when a request starts, and passes it down; it is
// never re-read from whatever conversation happens to be on screen by the time an event
// arrives.

import { guideFromResult, requestGuide } from './guideRequests'
import type { Attachment, Message, MessageBlock, ProposedChange, TurnUsage } from '../types/chat'
import { fetchEarlierMessages, saveMessages } from '../utils/chatStore'
import { dispatchScreenCommand, emitDataChanged } from '../lucius/bus'
import { authFetch } from '../api/client'
import { chatSessions } from './chatSessions'

// One logical turn can arrive as SEVERAL SSE streams: a confirm/permission card ENDS the
// backend turn (the tool_use is left result-less until the user answers) and approving it
// opens a fresh stream that resumes the same work. Each stream reports only its OWN usage,
// so the chip has to add them up — otherwise the number shown after an approval is the
// continuation's cost alone and the tokens spent before the card silently vanish.
function mergeUsage(
  prev: TurnUsage | undefined,
  next: TurnUsage | undefined,
): TurnUsage | undefined {
  if (!next) return prev
  if (!prev) return next
  const add = (a?: number, b?: number) => (a ?? 0) + (b ?? 0)
  return {
    ...next,
    input_tokens: add(prev.input_tokens, next.input_tokens),
    cache_creation_tokens: add(prev.cache_creation_tokens, next.cache_creation_tokens),
    cache_read_tokens: add(prev.cache_read_tokens, next.cache_read_tokens),
    output_tokens: add(prev.output_tokens, next.output_tokens),
    cost_usd: add(prev.cost_usd, next.cost_usd),
  }
}

// ── Meta sink ────────────────────────────────────────────────────────────────────────
// There is exactly one useChat() instance for the whole app (LuciusChatProvider), so one
// current callback is enough — same as the per-render ref
// (`onMessagesUpdateRef.current = onMessagesUpdate`) it replaces. The point of routing
// through here rather than a React effect keyed on the VIEWED conversation is that a
// message-count/title change for a conversation this browser is NOT looking at must still
// reach `updateConversationMeta` (acceptance A3) — a React effect over the viewed slice's
// `messages` can only ever see the viewed conversation.
type MetaSink = (convId: string, count: number, firstUserMessage?: string) => void
let metaSink: MetaSink = () => {}

export function setMetaSink(sink: MetaSink): void {
  metaSink = sink
}

function persistAndNotify(ownerId: string): void {
  const slice = chatSessions.getSlice(ownerId)
  void saveMessages(ownerId, slice.messages)
  const firstUser = slice.messages.find((m) => m.role === 'user')
  metaSink(ownerId, slice.messages.length, firstUser?.text)
}

// ── Message primitives ──────────────────────────────────────────────────────────────

/** Replace a slice's messages via `updater`. A no-op updater (returns the SAME array) skips
 *  the persist/notify side effect, mirroring a reducer bailing on no change. */
export function setMessages(ownerId: string, updater: (prev: Message[]) => Message[]): void {
  let changed = false
  chatSessions.apply(ownerId, (s) => {
    const messages = updater(s.messages)
    if (messages === s.messages) return s
    changed = true
    return { ...s, messages }
  })
  if (changed) persistAndNotify(ownerId)
}

/** Prepend the previous page of transcript history (server-paged via `before=` on the
    positional counter of the earliest loaded message). Returns how many messages were
    actually added — 0 when there's nothing earlier, the earliest message carries no
    server position (live-born id), or the fetch failed. */
export async function loadEarlierMessages(ownerId: string): Promise<number> {
  const current = chatSessions.getSlice(ownerId).messages
  const earliest = current[0]
  if (!earliest) return 0
  const older = await fetchEarlierMessages(ownerId, earliest.id)
  if (!older || older.length === 0) return 0
  const have = new Set(current.map((m) => m.id))
  const fresh = older.filter((m) => !have.has(m.id))
  if (fresh.length === 0) return 0
  setMessages(ownerId, (prev) => {
    // Re-check against the CURRENT slice — a live event may have landed since the await.
    const ids = new Set(prev.map((m) => m.id))
    const toAdd = fresh.filter((m) => !ids.has(m.id))
    return toAdd.length ? [...toAdd, ...prev] : prev
  })
  return fresh.length
}

/** Epoch ms, guaranteed strictly greater than the last message already in `timeline` —
 *  never just `Date.now()` on its own. Two deliveries landing in the same JS event-loop
 *  tick (a real case: several queued messages drained back-to-back at one checkpoint,
 *  live report 2026-08-10) would otherwise tie, and a tie has no defined display order.
 *  `+1` (not `+epsilon`) keeps every stamp an honest whole millisecond, just a later one
 *  than reality would have given it on its own — later messages in a fast burst read as
 *  "a bit slower", never simultaneous. */
function tsAfter(timeline: Message[]): number {
  const last = timeline[timeline.length - 1]
  const now = Date.now()
  return last?.ts !== undefined ? Math.max(now, last.ts + 1) : now
}

/** Append a USER message directly to the true timeline. A plain append, deliberately.
 *
 *  There is no longer a pre-opened assistant bubble to reorder around — a bubble is created
 *  by the assistant's first output (see ensureAssistant) — so the list stays in true
 *  chronological order, which is also the order the transcript records.
 *
 *  For a message sent while a turn is ALREADY running, use `addQueuedMessage` instead —
 *  this one is only for a message that opens (or joins, once already streaming — see
 *  useChat.sendMessage's `wasAlreadyStreaming` branch) a turn with nothing else contesting
 *  its position in the array. */
export function addUserMessage(ownerId: string, msg: Message): void {
  setMessages(ownerId, (prev) => [...prev, { ...msg, ts: msg.ts ?? tsAfter(prev) }])
}

/** Append a message SENT WHILE BUSY to its own list — never to `messages` — so a question
 *  typed ahead of its answer cannot steal the array slot that answer is about to need. See
 *  `ConvSlice.queuedMessages` for the full incident this exists to fix. Moves into
 *  `messages` (its true position) via `deliverQueuedMessage`, never edited in place here.
 *  Deliberately left UNSTAMPED (no `ts`) — see `Message.ts`: a queued message's timestamp
 *  is its delivery time, not its send time. */
export function addQueuedMessage(ownerId: string, msg: Message): void {
  chatSessions.apply(ownerId, (s) => ({ ...s, queuedMessages: [...s.queuedMessages, msg] }))
}

/** Move a queued message into the true timeline, at whatever position is CURRENT right
 *  now — which, because this only ever runs the instant the server confirms it actually
 *  started being processed, is exactly its correct position (right before the answer
 *  that is about to be generated for it, not wherever it happened to land at send time).
 *  Stamped HERE, not at send time — this moment IS its true chronological position.
 *  No-op if the id isn't queued (already delivered, idempotent; or never was, defensive). */
export function deliverQueuedMessage(ownerId: string, messageId: string): void {
  let delivered = false
  chatSessions.apply(ownerId, (s) => {
    const idx = s.queuedMessages.findIndex((m) => m.id === messageId)
    if (idx === -1) return s
    delivered = true
    const msg = { ...s.queuedMessages[idx]!, pending: undefined, ts: tsAfter(s.messages) }
    return {
      ...s,
      queuedMessages: [...s.queuedMessages.slice(0, idx), ...s.queuedMessages.slice(idx + 1)],
      messages: [...s.messages, msg],
    }
  })
  if (delivered) persistAndNotify(ownerId)
}

export function addMessage(ownerId: string, msg: Message): void {
  setMessages(ownerId, (prev) => [...prev, msg])
}

function updateLast(
  ownerId: string,
  role: Message['role'],
  updater: (m: Message) => Message,
): void {
  setMessages(ownerId, (prev) => {
    const idx = [...prev].reverse().findIndex((m) => m.role === role)
    if (idx === -1) return prev
    const realIdx = prev.length - 1 - idx
    const current = prev[realIdx]
    if (!current) return prev
    const updated = [...prev]
    updated[realIdx] = updater(current)
    return updated
  })
}

/** @deprecated for live-round writes — those go through `updateOpenAssistant` now, keyed
 *  off `openAssistantId` rather than reverse-scanned by role/position. Still correct and
 *  still used for the handful of call sites that genuinely mean "the last assistant
 *  message" as a historical fact (e.g. dropping a failed turn on retry) rather than "the
 *  live round's target". */
export function updateLastAssistant(ownerId: string, updater: (m: Message) => Message): void {
  updateLast(ownerId, 'assistant', updater)
}

export function updateLastUser(ownerId: string, updater: (m: Message) => Message): void {
  updateLast(ownerId, 'user', updater)
}

/** Update the message `openAssistantId` currently names. No-op if unset or the id no
 *  longer resolves (defensive — should not happen in practice, since the pointer is only
 *  ever set to an id that was just written to `messages` in the same tick). THE write
 *  path for every live-round event (text, thinking, tool blocks, errors, turn_done) —
 *  see `ConvSlice.openAssistantId` for why this replaced scanning for "the last assistant
 *  message" or "the trailing element". */
function updateOpenAssistant(ownerId: string, updater: (m: Message) => Message): void {
  const id = chatSessions.getSlice(ownerId).openAssistantId
  if (!id) return
  setMessages(ownerId, (prev) => {
    const idx = prev.findIndex((m) => m.id === id)
    if (idx === -1) return prev
    const updated = [...prev]
    updated[idx] = updater(updated[idx]!)
    return updated
  })
}

/** Point `openAssistantId` at `id` — the round this conversation's live writes target.
 *  Exported for `resolvePendingToolUse` (useChat.ts): resuming a paused card must
 *  re-open the SAME message the card lives on before its continuation starts
 *  streaming, exactly as `ensureAssistant`/`reviveOwningMessage` do for every other
 *  way a round opens. */
export function openAssistant(ownerId: string, id: string): void {
  chatSessions.apply(ownerId, (s) => (s.openAssistantId === id ? s : { ...s, openAssistantId: id }))
}

/** Clear `streaming` on EVERY assistant message in this slice, not just the last.
 *
 *  RFC §5.5: "a spinner that never resolves is exactly the failure mode this exists to
 *  forbid." Belt and braces alongside `openAssistantId` being cleared at every boundary —
 *  an orphan spinner must not be reachable by any route even if something upstream of
 *  this file ever leaves the pointer stale. */
export function clearAllStreaming(ownerId: string): void {
  setMessages(ownerId, (prev) => {
    if (!prev.some((m) => m.role === 'assistant' && m.streaming)) return prev
    return prev.map((m) => (m.role === 'assistant' && m.streaming ? { ...m, streaming: false } : m))
  })
}

/** End the live round: no bubble is "open" until something opens a new one. Call at
 *  every genuine boundary — turn_done (the round is over), message_delivered (an
 *  injected message is always its own exchange, drained at a checkpoint BETWEEN rounds
 *  even though the outer turn keeps running and no turn_done fires for it), and a failed
 *  resume/send (no continuation is ever coming). */
export function closeOpenAssistant(ownerId: string): void {
  chatSessions.apply(ownerId, (s) =>
    s.openAssistantId === null ? s : { ...s, openAssistantId: null },
  )
  clearAllStreaming(ownerId)
}

/** Ensure there is a live assistant bubble to write into, creating one if not.
 *
 *  THE bubble-creation rule: a bubble exists because the assistant produced something,
 *  never because we guessed a turn was about to start. sendMessage used to open an empty
 *  one up-front, and every ordering bug in this file descended from that guess — it had to
 *  be hoisted above later user messages, re-created on delivery so replies did not render
 *  above their question, and left spinning forever when nothing ever filled it. The
 *  "working, nothing yet" gap it existed to cover is TurnActivity's job.
 *
 *  Idempotent: reuses whatever `openAssistantId` already names, wherever that message
 *  sits in the array — not only when it is the trailing element. A message queued into
 *  an already-running turn (chatReducer.addUserMessage) is appended AFTER the live
 *  bubble, in true send order, without touching the bubble itself — checking only the
 *  trailing element made the turn's very next token read "no live bubble here" and open
 *  a SECOND one after the queued message. `openAssistantId` is the one thing every
 *  writer and every boundary reads and clears, so there is nothing left to infer here. */
export function ensureAssistant(ownerId: string): void {
  const slice = chatSessions.getSlice(ownerId)
  if (slice.openAssistantId && slice.messages.some((m) => m.id === slice.openAssistantId)) return
  const id = `a_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
  setMessages(ownerId, (prev) => [
    ...prev,
    {
      id,
      role: 'assistant' as const,
      text: '',
      streaming: true,
      toolCalls: [],
      blocks: [],
      proposedChanges: [],
      ts: tsAfter(prev),
    },
  ])
  openAssistant(ownerId, id)
}

/**
 * Grow the CURRENT thinking block, or open a new one after any other kind of block.
 *
 * A turn thinks, calls a tool, then thinks again — and those are two separate thoughts that
 * belong either side of the tool row. Appending to the trailing block only when it is itself
 * a thinking block is what keeps them apart: anything else in between (a tool, a text run)
 * ends the current thought.
 *
 * `thinking` is still accumulated alongside, as the message-level field older transcripts and
 * readers use.
 */
function appendThinking(m: Message, text: string): Message {
  const blocks = [...(m.blocks || [])]
  const last = blocks[blocks.length - 1]
  if (last?.type === 'thinking') {
    blocks[blocks.length - 1] = { type: 'thinking', content: last.content + text }
  } else {
    blocks.push({ type: 'thinking', content: text })
  }
  return { ...m, thinking: (m.thinking || '') + text, blocks }
}

export function appendBlock(ownerId: string, block: MessageBlock): void {
  ensureAssistant(ownerId)
  updateOpenAssistant(ownerId, (m) => ({ ...m, blocks: [...(m.blocks || []), block] }))
}

// An external-MCP tool captured an image (a screenshot, e.g.) — rendered the SAME way a
// chat photo upload is (`m.attachments`, MessageBubble.tsx's `AttachmentThumb`), not as a
// new block type.
export function appendAttachments(ownerId: string, attachments: Attachment[]): void {
  ensureAssistant(ownerId)
  updateOpenAssistant(ownerId, (m) => ({
    ...m,
    attachments: [...(m.attachments || []), ...attachments],
  }))
}

export function appendText(ownerId: string, text: string): void {
  ensureAssistant(ownerId)
  updateOpenAssistant(ownerId, (m) => {
    const blocks = [...(m.blocks || [])]
    const last = blocks[blocks.length - 1]
    if (last?.type === 'text') {
      blocks[blocks.length - 1] = { type: 'text', content: last.content + text }
    } else {
      blocks.push({ type: 'text', content: text })
    }
    return { ...m, blocks, text: m.text + text }
  })
}

// ── Suggestions ──────────────────────────────────────────────────────────────────────
// Per-owner sequence guard so a stale in-flight response (from a conversation switched
// away from, or superseded by a newer turn in the SAME conversation) can't overwrite a
// fresher one. Module-private — not part of ConvSlice, since it is pure bookkeeping for
// this file, not state any consumer reads.
const suggestSeqs = new Map<string, number>()

function bumpSuggestSeq(ownerId: string): number {
  const next = (suggestSeqs.get(ownerId) ?? 0) + 1
  suggestSeqs.set(ownerId, next)
  return next
}

// ── suggestion persistence ───────────────────────────────────────────────────
//
// A follow-up set costs a model call to produce, and lived only in the in-memory slice — so
// a reload threw it away and nothing brought it back (`ChatPanel` re-fetches only for an
// EMPTY conversation, to get starter chips). Paying for the same three chips again on every
// reload is the waste; storing them is not (owner, 2026-08-23).
//
// Keyed by the message they were generated FROM. A conversation that has moved on since —
// another tab, another device, a turn this browser did not see — has a different last
// message id, so the stale set is ignored rather than shown against an exchange it was
// never about.
const SUGGEST_CACHE_KEY = 'onw:chat-suggestions'
const SUGGEST_CACHE_MAX = 40

type SuggestCache = Record<string, { lastMessageId: string; suggestions: string[] }>

function readSuggestCache(): SuggestCache {
  try {
    const raw = localStorage.getItem(SUGGEST_CACHE_KEY)
    return raw ? (JSON.parse(raw) as SuggestCache) : {}
  } catch {
    return {}
  }
}

function lastMessageIdOf(ownerId: string): string {
  const msgs = chatSessions.getSlice(ownerId).messages
  return msgs.length ? (msgs[msgs.length - 1]?.id ?? '') : ''
}

function saveSuggestions(ownerId: string, suggestions: string[]): void {
  try {
    const cache = readSuggestCache()
    if (suggestions.length === 0) delete cache[ownerId]
    else cache[ownerId] = { lastMessageId: lastMessageIdOf(ownerId), suggestions }
    // Bounded: this is a convenience cache, not a store. Oldest keys go first.
    const keys = Object.keys(cache)
    for (const k of keys.slice(0, Math.max(0, keys.length - SUGGEST_CACHE_MAX))) delete cache[k]
    localStorage.setItem(SUGGEST_CACHE_KEY, JSON.stringify(cache))
  } catch {
    /* private mode / quota — the chips are a convenience, never a reason to fail */
  }
}

/**
 * Put back the suggestions this conversation already paid for, if they still belong to the
 * exchange on screen. Returns true when something was restored, so the caller knows not to
 * spend a model call.
 */
export function restoreSuggestions(ownerId: string): boolean {
  const entry = readSuggestCache()[ownerId]
  if (!entry || entry.suggestions.length === 0) return false
  if (entry.lastMessageId !== lastMessageIdOf(ownerId)) return false
  bumpSuggestSeq(ownerId)
  chatSessions.apply(ownerId, (s) => ({
    ...s,
    suggestions: entry.suggestions,
    suggestionsLoading: false,
  }))
  return true
}

export function clearSuggestions(ownerId: string): void {
  bumpSuggestSeq(ownerId)
  chatSessions.apply(ownerId, (s) =>
    s.suggestions.length === 0 && !s.suggestionsLoading
      ? s
      : { ...s, suggestions: [], suggestionsLoading: false },
  )
}

// How long the placeholder bars may stand in for chips. The generator is a model call that can
// stall behind a slow provider; past this the request is dropped and the bars go, because grey
// bars that never resolve read as a broken screen (persona QA round 9) and chips are optional.
export const SUGGESTIONS_TIMEOUT_MS = 20_000

// Fetch dynamic message suggestions for ONE conversation. The backend auto-detects mode
// from the session: an empty session → starter questions; a session with a completed
// assistant turn → follow-ups based on the last exchange. Best-effort.
export async function fetchSuggestionsFor(ownerId: string, enabled: boolean): Promise<void> {
  const slice = chatSessions.getSlice(ownerId)
  // An Automation run is a record of work, not an invitation to chat — generating
  // suggestions under a finished run is noise and costs a model call per run. Once the user
  // replies in it, it IS a conversation, and the reply prompt promises chips there too.
  const isUntouchedRun =
    Boolean(slice.messages[0]?.automationBrief) &&
    !slice.messages.slice(1).some((m) => m.role === 'user')
  if (isUntouchedRun || !enabled) {
    clearSuggestions(ownerId)
    saveSuggestions(ownerId, [])
    return
  }
  const seq = bumpSuggestSeq(ownerId)
  chatSessions.apply(ownerId, (s) => ({ ...s, suggestionsLoading: true }))
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), SUGGESTIONS_TIMEOUT_MS)
  try {
    const res = await authFetch('/api/chat/suggestions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: ownerId }),
      signal: abort.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (seq === suggestSeqs.get(ownerId)) {
      const next = Array.isArray(data.suggestions) ? (data.suggestions as string[]) : []
      chatSessions.apply(ownerId, (s) => ({ ...s, suggestions: next }))
      saveSuggestions(ownerId, next)
    }
  } catch {
    if (seq === suggestSeqs.get(ownerId))
      chatSessions.apply(ownerId, (s) => ({ ...s, suggestions: [] }))
  } finally {
    clearTimeout(timer)
    if (seq === suggestSeqs.get(ownerId))
      chatSessions.apply(ownerId, (s) => ({ ...s, suggestionsLoading: false }))
  }
}

// ── Replay dedup ─────────────────────────────────────────────────────────────────────
//
// Live incident 2026-08-10: reconnecting mid-turn (e.g. useChat's attachGen forcing a
// fresh GET /live when a message gets queued into an already-streaming turn) receives a
// REPLAY of everything the live-session buffer has published for this turn so far —
// necessary for a genuinely fresh viewer, but this browser may have ALREADY rendered
// some or all of it via the connection that is being torn down, and nothing here was
// idempotent against that. Confirmed against a real session transcript: the backend
// called each tool exactly once, but a reconnect's replay made the UI render the tool
// calls AND the answer text twice — a duplicated tool chip, then a second, separate
// "finished" bubble once the first one's own `streaming` flag had already been cleared.
//
// Derived from the CURRENT message state, not a separately tracked set — ids are minted
// server-side and never reused, so "a tool block with this id already exists anywhere in
// this conversation" is an exact, always-in-sync signal that a repeat is unambiguously a
// replay, never a coincidence. No separate state to keep correct or clean up.
function toolBlockExists(ownerId: string, id: string): boolean {
  return chatSessions
    .getSlice(ownerId)
    .messages.some(
      (m) => m.role === 'assistant' && m.blocks?.some((b) => b.type === 'tool' && b.id === id),
    )
}

function skillBlockExists(ownerId: string, id: string): boolean {
  return chatSessions
    .getSlice(ownerId)
    .messages.some(
      (m) => m.role === 'assistant' && m.blocks?.some((b) => b.type === 'skill' && b.id === id),
    )
}

/** Point `openAssistantId` at the message that owns this tool block — the message a
 *  detected replay is actually continuing, so subsequent events (the rest of the
 *  replay, AND any genuinely new content the reconnected stream continues with) land
 *  back on it instead of `ensureAssistant` opening a redundant new bubble just because
 *  the pointer had gone stale across the torn-down connection. Position-independent —
 *  the owner does not need to be the trailing message, only to still exist. Returns the
 *  owner (for `armSkipFromReplay`'s snapshot) or undefined if the block's message is
 *  somehow gone. */
function reviveOwningMessage(ownerId: string, toolId: string): Message | undefined {
  const owner = chatSessions
    .getSlice(ownerId)
    .messages.find(
      (m) => m.role === 'assistant' && m.blocks?.some((b) => b.type === 'tool' && b.id === toolId),
    )
  if (!owner) return undefined
  openAssistant(ownerId, owner.id)
  if (!owner.streaming) {
    setMessages(ownerId, (prev) =>
      prev.map((m) => (m.id === owner.id ? { ...m, streaming: true } : m)),
    )
  }
  return owner
}

function reviveOwningSkillMessage(ownerId: string, activityId: string): Message | undefined {
  const owner = chatSessions
    .getSlice(ownerId)
    .messages.find(
      (message) =>
        message.role === 'assistant' &&
        message.blocks?.some((block) => block.type === 'skill' && block.id === activityId),
    )
  if (!owner) return undefined
  openAssistant(ownerId, owner.id)
  if (!owner.streaming) {
    setMessages(ownerId, (prev) =>
      prev.map((message) => (message.id === owner.id ? { ...message, streaming: true } : message)),
    )
  }
  return owner
}

/** Update a `type: 'tool'` block with this id wherever it lives — not just on "the
 *  last assistant message". A replayed tool_done must still reach its block even in
 *  the (now rare, still possible for an older/unhealed session) case where it ended
 *  up on an earlier message. Idempotent: applying the same update twice is harmless. */
function updateToolBlockAnywhere(
  ownerId: string,
  id: string,
  updater: (b: Extract<MessageBlock, { type: 'tool' }>) => MessageBlock,
): void {
  setMessages(ownerId, (prev) => {
    let changed = false
    const next = prev.map((m) => {
      if (m.role !== 'assistant' || !m.blocks?.some((b) => b.type === 'tool' && b.id === id)) {
        return m
      }
      changed = true
      return {
        ...m,
        blocks: m.blocks.map((b) =>
          b.type === 'tool' && b.id === id
            ? updater(b as Extract<MessageBlock, { type: 'tool' }>)
            : b,
        ),
      }
    })
    return changed ? next : prev
  })
}

function updateSkillBlockAnywhere(
  ownerId: string,
  id: string,
  updater: (block: Extract<MessageBlock, { type: 'skill' }>) => MessageBlock,
): void {
  setMessages(ownerId, (prev) => {
    let changed = false
    const next = prev.map((message) => {
      if (
        message.role !== 'assistant' ||
        !message.blocks?.some((block) => block.type === 'skill' && block.id === id)
      ) {
        return message
      }
      changed = true
      return {
        ...message,
        blocks: message.blocks.map((block) =>
          block.type === 'skill' && block.id === id
            ? updater(block as Extract<MessageBlock, { type: 'skill' }>)
            : block,
        ),
      }
    })
    return changed ? next : prev
  })
}

function failLoadingSkillBlocks(ownerId: string, error: string): void {
  setMessages(ownerId, (prev) => {
    let changed = false
    const next = prev.map((message) => {
      if (
        message.role !== 'assistant' ||
        !message.blocks?.some((block) => block.type === 'skill' && block.status === 'loading')
      ) {
        return message
      }
      changed = true
      return {
        ...message,
        blocks: message.blocks.map((block) =>
          block.type === 'skill' && block.status === 'loading'
            ? { ...block, status: 'failed' as const, error }
            : block,
        ),
      }
    })
    return changed ? next : prev
  })
}

// ── SSE consumption ──────────────────────────────────────────────────────────────────

// Consume ONE SSE response, dispatching every event onto `ownerId`'s LAST assistant
// message — shared by a fresh turn (sendMessage, which adds that message first) and
// resolving a pending tool_use (resolvePendingToolUse, RFC 2026-07-22), which does NOT add
// a new message: the card it is resolving already lives on the last assistant message, and
// the resumed turn's events (further text/tool blocks) continue landing on that SAME
// message — "one call, in place", never a second bubble for the same action.
export async function consumeEventStream(
  ownerId: string,
  res: Response,
  opts: { suggestionsEnabled: boolean; isResume?: boolean },
): Promise<void> {
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let proposedChange = false // turn ended on a pending confirm card — skip follow-ups
  // Only ids that existed BEFORE this SSE stream are replay anchors. A fresh loading row
  // is followed immediately by loaded in the same stream; treating that terminal update as
  // replay would skip genuine next-round text by the length of any pre-activation preamble.
  const replaySkillIds = new Set(
    chatSessions
      .getSlice(ownerId)
      .messages.flatMap((message) =>
        (message.blocks || []).filter((block) => block.type === 'skill').map((block) => block.id),
      ),
  )

  // A reconnect to an ALREADY-streaming turn (chatSessions.attachGen forcing a fresh
  // GET /live) replays every event the server has published for this turn so far, from
  // the start — including text this browser already rendered via the connection being
  // torn down. Unlike a tool block (deduped by id via toolBlockExists), text_delta and
  // thinking_delta have no id, so dedup them by PREFIX LENGTH instead: the FIRST time a
  // replayed (already-known) tool block is recognized, snapshot how much text/thinking
  // the message it belongs to already carries, then skip exactly that many replayed
  // characters — wherever they land relative to further tool events — before resuming
  // real appends.
  //
  // The previous approach flagged a sticky `replaying` boolean the instant a replayed
  // tool_use_start was recognized, cleared only by a FRESH tool_use_start — so a turn
  // whose replayed tail led straight into brand-new text with no further tool call
  // never cleared it, and every real token after the replay was silently dropped. Live
  // report 2026-08-10: an answer stopped mid-word ("It's separ") on the live stream but
  // was complete after a reload — the backend had it, the client just threw it away.
  //
  // Armed ONLY off a recognized replayed tool block, once — never re-armed by a second
  // one in the same stream (that would re-inflate the counter past text already
  // consumed) and never guessed from the trailing message's `streaming` flag alone: a
  // resumed permission-card continuation (resolvePendingToolUse) also leaves that flag
  // true with pre-pause text already on the message, but is a genuinely fresh
  // continuation, not a replay — arming off it there would truncate the START of the
  // resumed answer instead of fixing anything.
  let skipArmed = false
  let skipTextChars = 0
  let skipThinkingChars = 0
  function armSkipFromReplay(owner: Message): void {
    if (skipArmed) return
    skipArmed = true
    skipTextChars = owner.text.length
    skipThinkingChars = (owner.thinking || '').length
  }

  // A prior attach for this conversation may have left a bubble open without ever
  // reaching a genuine close (turn_done/error) — a plain disconnect (network blip, a dev
  // server --reload restart) rather than a clean end. The dedup above only keys off a
  // recognized tool block, so a turn with NO tool calls (a pure smalltalk reply) has
  // nothing to arm it — this connection's full replay then opens a BRAND NEW bubble via
  // ensureAssistant instead of continuing the interrupted one. Live report 2026-08-10: two
  // stacked "Thinking" pills for one "how are you" greeting.
  //
  // Gated on `opts.isResume`, an EXPLICIT signal from the caller — not on `openAssistantId`
  // being unset. That pointer alone cannot tell two cases apart: a paused permission-card
  // continuing via resolvePendingToolUse ALSO leaves its message with no `usage` yet (the
  // pause ends the stream before any turn_done, so nothing ever stamps it) and explicitly
  // calls `openAssistant` itself before this connection starts — arming off it there would
  // truncate the START of the resumed answer (chatReducer.replayTextResume.test.ts). But a
  // plain reconnect that SUPERSEDES another still-open attach to the same live turn also
  // finds `openAssistantId` already set — deliberately left that way, for continuity — with
  // nothing about it distinguishing "a deliberate resume opened this" from "an earlier,
  // still-active connection did, and I am its replay." Live report: a fresh conversation's
  // answer rendered as a truncated copy immediately followed by the complete one — the
  // reconnect's replay was never recognized as one, because `openAssistantId` was already
  // set by the connection it superseded, not by a deliberate resume. `useChat.ts` now
  // threads that fact through explicitly (`resumeInFlight`, set only by
  // resolvePendingToolUse) instead of leaving this function to infer it.
  const _openSlice = chatSessions.getSlice(ownerId)
  const _trailing = _openSlice.messages.at(-1)
  if (
    !opts.isResume &&
    _trailing?.role === 'assistant' &&
    _trailing.usage === undefined &&
    !_trailing.error
  ) {
    openAssistant(ownerId, _trailing.id)
    armSkipFromReplay(_trailing)
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = line.slice(6).trim()
      if (data === '[DONE]') break

      try {
        const event = JSON.parse(data)

        // INV-2: an event naming a DIFFERENT session than the one this stream belongs to
        // is refused, not silently applied. Belt and braces — the stream is already routed
        // by owner (`ownerId` is captured once, at request start, and never re-read from
        // the view) — but a wire event carrying its own session_id (RFC Phase 2) gets
        // checked here too, so a superseded/misrouted event can never land on the wrong
        // slice. Real Phase-1 events don't carry `session_id` yet, so this is inert until
        // then; the test suite exercises it directly.
        if (typeof event.session_id === 'string' && event.session_id !== ownerId) {
          console.warn('[chatReducer] refused an event addressed to another conversation', {
            ownerId,
            eventSessionId: event.session_id,
            type: event.type,
          })
          continue
        }

        if (event.type === 'user_message') {
          // The message that started this turn, announced for devices that did not send it
          // (live report 2026-10-26: a phone's photo showed its reply on the desktop, never
          // itself). The sender already holds it under this same id — or in its queue, when
          // its send raced the end of a turn — so only a stranger to it adds a bubble.
          const id: string | undefined = event.message_id
          if (id) {
            deliverQueuedMessage(ownerId, id)
            if (!chatSessions.getSlice(ownerId).messages.some((m) => m.id === id)) {
              closeOpenAssistant(ownerId)
              addUserMessage(ownerId, {
                id,
                role: 'user',
                text: event.text ?? '',
                attachments: event.attachments ?? [],
              })
            }
          }
        } else if (event.type === 'message_delivered') {
          // Mid-turn injection (concurrent-conversations RFC): the server just spliced a
          // QUEUED message into the running turn (or, on the residue-fold-in path, into a
          // freshly spawned one) — move it out of `queuedMessages` and into the true
          // timeline, RIGHT NOW, which is exactly its correct position (see
          // ConvSlice.queuedMessages). Carries the CLIENT's own message id (never a
          // server-internal fence nonce), so this is a simple id match, not a positional
          // guess. No-op if the id isn't queued (a stale event after a reload, or already
          // delivered — idempotent either way).
          const deliveredId: string | undefined = event.message_id
          if (deliveredId) deliverQueuedMessage(ownerId, deliveredId)
          // An injected message is ALWAYS its own exchange — rehydrate_messages treats
          // any real injected text as ending the prior turn's bubble (never merges the
          // next assistant round into it), but turn_done never fires here (the OUTER
          // turn is still running), so nothing told the live path the same boundary
          // just occurred. Without this, ensureAssistant correctly finds the still-
          // `streaming` pre-injection bubble (see its own comment) and keeps writing
          // the NEW exchange into it — a totally different question's answer appending
          // onto the previous one with no break between them. Live report 2026-08-10:
          // asking about a second commitment mid-turn had its whole answer glued onto
          // the first commitment's answer, no separation, one "and alloy?" bubble
          // similarly swallowing the tail of an unrelated ORCL answer. Closing the open
          // round (not `status` — the turn itself is still running) is what makes the
          // very next token open a fresh bubble instead of reusing this one.
          closeOpenAssistant(ownerId)
        } else if (event.type === 'thinking_delta') {
          ensureAssistant(ownerId)
          if (skipThinkingChars > 0) {
            if (event.text.length <= skipThinkingChars) {
              skipThinkingChars -= event.text.length
            } else {
              const fresh = event.text.slice(skipThinkingChars)
              skipThinkingChars = 0
              updateOpenAssistant(ownerId, (m) => appendThinking(m, fresh))
            }
          } else {
            updateOpenAssistant(ownerId, (m) => appendThinking(m, event.text))
          }
        } else if (event.type === 'text_delta') {
          if (skipTextChars > 0) {
            if (event.text.length <= skipTextChars) {
              skipTextChars -= event.text.length
            } else {
              appendText(ownerId, event.text.slice(skipTextChars))
              skipTextChars = 0
            }
          } else {
            appendText(ownerId, event.text)
          }
        } else if (event.type === 'tool_use_start') {
          if (
            event.name !== 'web_search' &&
            event.name !== 'fetch_page' &&
            event.name !== 'activate_skill'
          ) {
            if (toolBlockExists(ownerId, event.id)) {
              // A replay of a tool call this conversation already fully rendered — no
              // duplicate block, no new bubble opened for it. Revive the bubble it
              // belongs to, so whatever comes next — more replay, or genuinely new
              // content this reconnect continues with — lands back on it too.
              const owner = reviveOwningMessage(ownerId, event.id)
              if (owner) armSkipFromReplay(owner)
            } else {
              // ensureAssistant FIRST: without it, the pointer from BEFORE this turn's
              // own user message was appended could still be null/stale and the write
              // below would silently no-op. Calling it here, before either write, is
              // what makes a turn's first tool call open its OWN bubble rather than
              // landing nowhere or on a stale target. Live report 2026-08-10: a
              // web_search chip rendered under the answer to the PRIOR question, one
              // turn early.
              ensureAssistant(ownerId)
              updateOpenAssistant(ownerId, (m) => ({
                ...m,
                toolCalls: [...(m.toolCalls || []), event.name],
              }))
              appendBlock(ownerId, {
                type: 'tool',
                name: event.name,
                id: event.id,
                status: 'streaming',
                // Resolved server-side and carried on the event (ai/tools/display.py), so
                // the row reads as a sentence from its very first frame rather than
                // flashing a raw tool name until something else fills it in.
                ...(event.display ? { display: event.display } : {}),
              })
            }
          }
        } else if (event.type === 'search_start') {
          // web_search/fetch_page skip the generic tool_use_start branch above (its own
          // events instead), so this — not that — is often the FIRST event of a turn;
          // without ensureAssistant it lands on the previous turn's bubble (see the
          // tool_use_start comment above for the exact mechanism and live report).
          ensureAssistant(ownerId)
          updateOpenAssistant(ownerId, (m) => ({
            ...m,
            searches: [...(m.searches || []), { query: event.query, results: [], loading: true }],
            blocks: [
              ...(m.blocks || []),
              { type: 'search', query: event.query, results: [], loading: true },
            ],
          }))
        } else if (event.type === 'search_results') {
          updateOpenAssistant(ownerId, (m) => {
            const searches = [...(m.searches || [])]
            const si = searches.findLastIndex((s) => s.query === event.query && s.loading)
            if (si !== -1)
              searches[si] = { query: event.query, results: event.results, loading: false }
            const blocks = [...(m.blocks || [])]
            const bi = [...blocks]
              .reverse()
              .findIndex(
                (b) =>
                  b.type === 'search' &&
                  b.query === event.query &&
                  (b as { loading: boolean }).loading,
              )
            if (bi !== -1)
              blocks[blocks.length - 1 - bi] = {
                type: 'search',
                query: event.query,
                results: event.results,
                loading: false,
              }
            return { ...m, searches, blocks }
          })
        } else if (event.type === 'fetch_start') {
          ensureAssistant(ownerId)
          updateOpenAssistant(ownerId, (m) => ({
            ...m,
            fetches: [...(m.fetches || []), event.url],
            blocks: [...(m.blocks || []), { type: 'fetch', url: event.url }],
          }))
        } else if (event.type === 'duplicate_review') {
          appendBlock(ownerId, {
            type: 'duplicate_review',
            duplicates: event.duplicates,
            batchId: event.batch_id,
          })
        } else if (event.type === 'artifact') {
          // Compact reference only (id/title/filename/type/size) — ArtifactCard fetches
          // the document bytes from /api/artifacts/{id} on mount.
          appendBlock(ownerId, { type: 'artifact', artifact: event.artifact })
        } else if (event.type === 'mcp_image') {
          // An external-MCP tool captured an image — same AttachmentThumb a chat photo
          // upload renders, backed by GET /api/uploads/{ref_id}/image.
          appendAttachments(ownerId, event.attachments as Attachment[])
        } else if (event.type === 'proposed_change') {
          proposedChange = true
          const change: ProposedChange = { ...event.change, id: event.tool_use_id }
          // Self-sufficient, matching every other block-appending handler here
          // (appendBlock/appendText/appendAttachments all call this themselves) — never
          // rely on an earlier event in the same turn having already opened the bubble.
          // A confirm card silently vanishing is worse than a stray empty bubble.
          ensureAssistant(ownerId)
          updateOpenAssistant(ownerId, (m) => ({
            ...m,
            proposedChanges: [...(m.proposedChanges || []), change],
            blocks: [...(m.blocks || []), { type: 'change', change }],
          }))
        } else if (event.type === 'skill_loading') {
          if (skillBlockExists(ownerId, event.id)) {
            if (replaySkillIds.has(event.id)) {
              const owner = reviveOwningSkillMessage(ownerId, event.id)
              if (owner) armSkipFromReplay(owner)
            }
          } else {
            appendBlock(ownerId, {
              type: 'skill',
              id: event.id,
              skillId: event.skill_id,
              name: event.skill_name || event.skill_id,
              status: 'loading',
              source: event.source,
            })
          }
        } else if (event.type === 'skill_loaded' || event.type === 'skill_failed') {
          const status = event.type === 'skill_loaded' ? 'loaded' : 'failed'
          if (skillBlockExists(ownerId, event.id)) {
            if (replaySkillIds.has(event.id)) {
              const owner = reviveOwningSkillMessage(ownerId, event.id)
              if (owner) armSkipFromReplay(owner)
            }
            updateSkillBlockAnywhere(ownerId, event.id, (block) => ({
              ...block,
              name: event.skill_name || block.name,
              status,
              ...(event.error ? { error: event.error } : {}),
            }))
          } else {
            appendBlock(ownerId, {
              type: 'skill',
              id: event.id,
              skillId: event.skill_id,
              name: event.skill_name || event.skill_id,
              status,
              source: event.source,
              ...(event.error ? { error: event.error } : {}),
            })
          }
        } else if (event.type === 'tool_executing') {
          // Wherever its block lives, not just "the last assistant message" — a
          // replayed tool_use_start is now suppressed entirely (see above), but an
          // older/already-open session may still have a block sitting on an earlier
          // message, and this must still reach it.
          updateToolBlockAnywhere(ownerId, event.id, (b) => ({
            ...b,
            status: 'executing' as const,
            input: event.input,
          }))
        } else if (event.type === 'tool_done') {
          // status: 'done' is set HERE, not left for turn_done's bulk sweep — a tool
          // whose result just arrived must never keep showing as busy. Live incident
          // 2026-08-10: the bulk sweep only touches the LAST assistant message at
          // turn_done, so any block that ends up on an earlier message (however that
          // happens) was stuck showing the busy spinner forever, even though its
          // result had long since arrived. Also routed to wherever its block lives
          // (see tool_executing above), for the same reason.
          updateToolBlockAnywhere(ownerId, event.id, (b) => ({
            ...b,
            status: 'done' as const,
            result: event.result,
          }))
          // Lucius asked the app to light up one of its guides (show_guide). Each guide shows
          // once per person, so a replayed result asking again changes nothing on screen.
          const guide = guideFromResult(event.result)
          if (guide) requestGuide(guide)
        } else if (event.type === 'data_changed') {
          emitDataChanged(Array.isArray(event.domains) ? event.domains : [])
        } else if (event.type === 'screen_command') {
          if (event.command?.name) {
            dispatchScreenCommand({
              name: event.command.name,
              args: event.command.args ?? {},
            })
          }
          // A connector setup keeps its card even when Connectors was already open and the form
          // opened at once: the card names the connector and carries Connect (owner, 2026-10-31:
          // "where is that connect card gone?"). Every other command's chip is only for parked.
          const setup = event.command?.name === 'connectors.open_setup'
          if (event.status === 'parked' || setup) {
            appendBlock(ownerId, {
              type: 'screen_command',
              command: { name: event.command?.name ?? '', args: event.command?.args ?? {} },
              open: event.open ?? (setup ? { route: 'connectors' } : {}),
            })
          }
        } else if (event.type === 'write_receipt') {
          // ONE chip per turn showing what the turn actually wrote to the ledger (ADR
          // 2026-07-20). Arrives right after checkpoint_created, and carries that
          // checkpoint's id so the chip's Undo is a restore rather than a second mechanism.
          if (event.receipt) appendBlock(ownerId, { type: 'receipt', receipt: event.receipt })
        } else if (event.type === 'checkpoint_created') {
          // Attach the checkpoint to the user message that opened this turn (the most
          // recent user message) so a Restore button can appear on it.
          const cpId = event.checkpoint?.id
          if (cpId) updateLastUser(ownerId, (m) => ({ ...m, checkpointId: cpId, restored: false }))
        } else if (event.type === 'error') {
          // A provider can fail before text, thinking, or a tool opens a bubble.
          ensureAssistant(ownerId)
          // An error naming a `tool_use_id` is a REFUSED approval: the server could not
          // redeem the card, so nothing ran. `resolvePendingToolUse` already painted it
          // "✓ Approved" optimistically, and leaving that tick up under a red error is the
          // UI asserting a write that never happened — un-paint it (back to unanswered) so
          // the card tells the truth. Any other error leaves card state alone.
          const refusedId: string | undefined = event.tool_use_id
          failLoadingSkillBlocks(ownerId, event.message || 'Skill activation failed.')
          const unpaint = (c: ProposedChange): ProposedChange =>
            c.id === refusedId ? { ...c, status: undefined, refused: true } : c
          updateOpenAssistant(ownerId, (m) => ({
            ...m,
            error: event.message,
            setupHint: event.setup_hint === true,
            streaming: false,
            ...(refusedId
              ? {
                  proposedChanges: m.proposedChanges?.map(unpaint),
                  blocks: m.blocks?.map((block) =>
                    block.type === 'change' && block.change.id === refusedId
                      ? { ...block, change: unpaint(block.change) }
                      : block,
                  ),
                }
              : {}),
          }))
          closeOpenAssistant(ownerId)
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            status: 'error',
            lastResult: 'error',
            startedAt: undefined,
          }))
        } else if (event.type === 'turn_progress') {
          const n = typeof event.output_tokens === 'number' ? event.output_tokens : 0
          chatSessions.apply(ownerId, (s) =>
            s.liveOutputTokens === n ? s : { ...s, liveOutputTokens: n },
          )
        } else if (event.type === 'compacting_start') {
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            compacting: { percent: 0 },
            startedAt: s.startedAt ?? Date.now(),
          }))
        } else if (event.type === 'compacting_progress') {
          const pct = Math.min(
            100,
            Math.max(0, typeof event.percent === 'number' ? event.percent : 0),
          )
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            compacting: {
              percent: pct,
              outputTokens:
                typeof event.output_tokens === 'number'
                  ? event.output_tokens
                  : s.compacting?.outputTokens,
            },
          }))
        } else if (event.type === 'compacting_done') {
          chatSessions.apply(ownerId, (s) => (s.compacting ? { ...s, compacting: null } : s))
        } else if (event.type === 'turn_done') {
          // Defensive clear: compacting_done should already have cleared this, but a
          // dropped/reconnected SSE stream could lose that one event — a turn ending is
          // itself proof compacting (for this turn) is over, whether or not that event
          // was ever seen.
          chatSessions.apply(ownerId, (s) => (s.compacting ? { ...s, compacting: null } : s))
          // `proposedChange` ⇒ the backend ENDED this stream on a confirm/permission card
          // and is waiting for the user; the work is NOT finished. Record that on the
          // message so the bubble withholds its cost chip (the visual full stop) until the
          // card is answered and the resumed stream truly completes.
          const paused = proposedChange
          // Record the OUTCOME. Nothing set 'ok' before — only 'error' — so the in-memory
          // "this finished while you were elsewhere" signal never fired and the row status
          // fell through to the durable server fallback for every success. A turn that
          // ended on a confirm card is NOT finished, so it stays out of this.
          if (!paused) {
            chatSessions.apply(ownerId, (s) =>
              s.lastResult === 'ok' ? s : { ...s, lastResult: 'ok' },
            )
          }
          updateOpenAssistant(ownerId, (m) => ({
            ...m,
            streaming: false,
            awaitingApproval: paused,
            usage: mergeUsage(m.usage, event.usage),
            blocks: (m.blocks || []).map((b) =>
              b.type === 'tool' ? { ...b, status: 'done' as const } : b,
            ),
          }))
          // The round is over either way — a paused turn resumes via a NEW round
          // (resolvePendingToolUse re-opens the pointer explicitly), never by this
          // connection continuing to write here.
          closeOpenAssistant(ownerId)
          // A paused turn hasn't actually finished — `lastResult`/`status` only move past
          // 'streaming' once the work is really done (concurrent-conversations-ui-spec §1.2).
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            status: paused ? 'awaiting_approval' : s.status,
            lastResult: paused ? s.lastResult : 'ok',
            startedAt: paused ? undefined : s.startedAt,
          }))
          // Generate follow-up suggestions based on the exchange just completed — but not
          // when the turn ended on a pending confirm card (the user is mid-confirmation, so
          // a wasted Haiku call and irrelevant follow-ups).
          if (!proposedChange) void fetchSuggestionsFor(ownerId, opts.suggestionsEnabled)
        }
      } catch {
        /* malformed JSON line — skip */
      }
    }
  }
}
