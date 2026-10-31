import type { Message } from '../types/chat'
import { authFetch } from '../api/client'
import { recordRevision } from './chatRevision'
import { blockCount, contentCount } from './chatBubbles'

// Conversation message bodies live in IndexedDB, not localStorage. localStorage
// caps at ~5MB per origin and throws QuotaExceededError once a long chat (full
// tool-result payloads, search results, thinking transcripts) overflows it —
// which, thrown from a render effect, used to crash the whole ChatPanel. IndexedDB's
// quota is disk-based (hundreds of MB to GBs) and supported in every modern browser,
// so conversations grow without a ceiling. All ops are best-effort: a storage
// failure must never take down the chat UI.

const DB_NAME = 'lucius'
const STORE = 'conversations'
// IndexedDB schema version — only ever increment; opening below the version already
// stored in the browser throws. Bump it when the SHAPE of a cached message changes, since
// the cache wins over the server copy for a conversation of unchanged length: v3 retired
// entries whose automationBrief was the bare name, which rendered a header reading "Ran "
// with nothing after it and no link to the run.
//
// v4 (2026-08-23): thinking became a POSITIONED BLOCK. A cached message carries the old
// shape — reasoning merged into the message-level `thinking` field and no thinking blocks —
// so an already-seen conversation kept rendering one blob above the first tool row however
// many times the backend reloaded. The server copy was right the whole time; the cache was
// simply preferred. This is the mechanism for that, and forgetting it is the failure mode it
// exists to prevent.
//
// v5 (2026-08-25): the `display` block a tool row renders from is DERIVED on every read
// (`ai/tools/display.py`, resolved server-side in `rehydrate_messages`) — so a fix to it
// reaches every conversation the moment the backend reloads, and reaches a CACHED one
// never. A search row printed its query twice, once folded into the sentence and once in
// the muted span beside it; the fix ships an empty `detail` for that case, and only a
// re-fetched message carries it.
const VERSION = 5

let dbPromise: Promise<IDBDatabase> | null = null

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      // Drop and recreate rather than migrate. Every entry is a RENDERING of a transcript
      // the server still holds, so the cost of discarding one is a single refetch on next
      // open — cheaper to reason about than a migration per shape change.
      if (db.objectStoreNames.contains(STORE)) db.deleteObjectStore(STORE)
      db.createObjectStore(STORE)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  // Don't cache a rejected promise — a transient failure shouldn't disable storage forever.
  dbPromise.catch(() => {
    dbPromise = null
  })
  return dbPromise
}

function idbGet(key: string): Promise<Message[] | null> {
  return openDB().then(
    (db) =>
      new Promise<Message[] | null>((resolve, reject) => {
        const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(key)
        req.onsuccess = () => resolve((req.result as Message[]) ?? null)
        req.onerror = () => reject(req.error)
      }),
  )
}

function idbPut(key: string, value: Message[]): Promise<void> {
  return openDB().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        tx.objectStore(STORE).put(value, key)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
      }),
  )
}

function idbDelete(key: string): Promise<void> {
  return openDB().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        tx.objectStore(STORE).delete(key)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
      }),
  )
}

// Rebuild a conversation from the backend transcript (source of truth). Used when
// nothing is cached locally — e.g. a conversation started on another browser/device,
// or one that predates this cache. Faithful for text + tool calls, and the cost chip
// (backend best-effort-joins it from cost_log.json — see assistant.rehydrate_messages);
// the purely visual extras that were never persisted (search cards, confirm cards)
// aren't reconstructable.
// How much history a remote load asks for. Measured on the heaviest real conversation
// (owner's automated-monitor thread, 2026-08-12): tail=60 shipped 1.21MB gzipped, tail=30
// shipped 115KB — the byte cliff is giant automation-digest messages sitting just past the
// 30-from-the-end mark. 30 still covers the render window (24) with margin; scrolling past
// the loaded tail needs the real fetch-older pagination (top of the chat perf backlog).
const TRANSCRIPT_FETCH_TAIL = 30

// Server-reported full message counts, by conversation — lets the UI distinguish "all
// loaded" from "there is deeper history on the server than this tail".
const remoteTotals = new Map<string, number>()

export function remoteTotalFor(convId: string): number | null {
  return remoteTotals.get(convId) ?? null
}

async function fetchRemoteMessages(
  convId: string,
  tail?: number,
  before?: number,
): Promise<Message[] | null> {
  // Guard the empty/placeholder id — otherwise this fires GET /api/sessions//messages
  // (double slash) → a 404 console error on every page load before a conversation exists.
  if (!convId) return null
  try {
    const params = new URLSearchParams()
    if (tail && tail > 0) params.set('tail', String(tail))
    if (before && before > 0) params.set('before', String(before))
    const qs = params.toString()
    const res = await authFetch(`/api/sessions/${convId}/messages${qs ? `?${qs}` : ''}`)
    if (!res.ok) return null
    const data = await res.json()
    if (typeof data.total === 'number') remoteTotals.set(convId, data.total)
    recordRevision(convId, data.revision)
    return Array.isArray(data.messages) ? (data.messages as Message[]) : null
  } catch {
    return null
  }
}

/** The positional counter of a transcript-rehydrated message id (`{convId}-{counter}`),
    or null for ids minted by live streaming — those carry no server position, so a
    conversation whose earliest message is live-born can't page (and never needs to:
    a fully-local conversation already holds everything this browser saw). */
export function positionalCounter(convId: string, messageId: string): number | null {
  if (!messageId.startsWith(`${convId}-`)) return null
  const rest = messageId.slice(convId.length + 1)
  return /^\d+$/.test(rest) ? Number(rest) : null
}

/** Fetch the page of messages strictly BEFORE the earliest one the client holds.
    Returns null when that id carries no server position (see positionalCounter) or
    when it is already the first message. */
export async function fetchEarlierMessages(
  convId: string,
  earliestId: string,
  tail: number = TRANSCRIPT_FETCH_TAIL,
): Promise<Message[] | null> {
  const before = positionalCounter(convId, earliestId)
  if (before === null || before <= 1) return null
  return fetchRemoteMessages(convId, tail, before)
}

/** The full input/result for one elided tool block — the fetch-on-tap half of
    transcript elision (backend strips oversized tool payloads from the list fetch). */
export async function fetchToolDetail(
  convId: string,
  toolUseId: string,
): Promise<{ name?: string; input?: Record<string, unknown>; result?: string } | null> {
  if (!convId || !toolUseId) return null
  try {
    const res = await authFetch(
      `/api/sessions/${convId}/tool-results/${encodeURIComponent(toolUseId)}`,
    )
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export async function loadMessages(convId: string): Promise<Message[] | null> {
  if (!convId) return null
  let local: Message[] | null = null
  try {
    local = await idbGet(convId)
  } catch {
    /* IndexedDB unavailable — fall through to the backend */
  }

  // Prefer the local cache: it holds the richer rendered version (with cards/chips)
  // for conversations seen in this browser. Only rebuild from the backend when
  // there's nothing cached, then cache the result so the next open is instant.
  if (local && local.length) return local

  const remote = await fetchRemoteMessages(convId, TRANSCRIPT_FETCH_TAIL)
  if (remote && remote.length) {
    void saveMessages(convId, remote)
    return remote
  }
  return local
}

/** Should a reconcile adopt the remote snapshot over the local cache?

    With tail-fetching, array LENGTH can no longer signal growth (a 60-tail of a longer
    conversation is always 60). Message ids are positional and stable
    (`{session}-{counter}`), so when BOTH last ids carry a counter the ORDER is exact:
    a higher remote counter is "the transcript advanced past what this cache saw" —
    the stump-cache case — and a lower one is a server still catching up to a cache
    that ran ahead mid-turn (adopting that would truncate; regression caught by
    chatStore.reconcile.test.ts). When the counters aren't comparable (a live-born
    uuid last id — e.g. a message the user just typed), length is the fallback signal,
    but the local side is sized by MEANINGFUL messages, not raw count: a synthetic
    placeholder bubble (see chatBubbles.isPlaceholderBubble) never represents server content, so it must not
    make a stale cache look as long as, or longer than, a remote that merged several
    server-format rows into fewer UI bubbles. A tie on that count falls through to
    total block count, the same richness signal `useChat.isRicher` applies after —
    so a still-open live turn (rich in blocks, tied in bubble count) is never
    truncated by a same-length remote tail.

    EQUAL last ids are not "nothing changed": an Automation run's whole multi-round turn is
    ONE assistant message with a stable positional id, so it GROWS under that id for the
    length of the run. A cache taken mid-run holds `-2` with 16 blocks; the finished run's
    `-2` has 39. Only the LAST message's own block count is compared there — not totals —
    because the remote is a tail: a full old local cache legitimately holds more blocks in
    total than a fresh 30-tail of the same unchanged conversation, and must stay. */
export function shouldAdoptRemote(local: Message[] | null, remote: Message[]): boolean {
  if (remote.length === 0) return false
  const localLastMsg = local?.[local.length - 1]
  const remoteLastMsg = remote[remote.length - 1]
  const localLast = localLastMsg?.id
  const remoteLast = remoteLastMsg?.id
  if (!localLast) return true
  if (remoteLast === localLast) {
    if (hasOpenCard(remote) && !hasOpenCard(local)) return true
    return (remoteLastMsg?.blocks?.length ?? 0) > (localLastMsg?.blocks?.length ?? 0)
  }
  const split = (id: string) => /^(.+)-(\d+)$/.exec(id)
  const a = split(localLast)
  const b = remoteLast ? split(remoteLast) : null
  if (a && b && a[1] === b[1]) return Number(b[2]) > Number(a[2])
  if (hasOpenCard(remote) && !hasOpenCard(local)) return true
  const localCount = contentCount(local)
  if (remote.length !== localCount) return remote.length > localCount
  return blockCount(remote) > blockCount(local)
}

/** A reply this browser saw START but never saw finish — the page was suspended (a phone
    switching apps) after the thinking arrived and before the answer did. Both copies hold
    the same messages with the same blocks, so neither `shouldAdoptRemote` nor
    `useChat.isRicher` can tell the half-drawn bubble from the finished one, and the cache
    won forever: thinking on screen, no answer, on the phone AND after a restart (live report
    2026-10-30). Returns `local` with its last bubble replaced by the server's finished reply,
    or null when that is not the case.

    The server writes a turn's user message and reply together, AT THE END — so mid-turn its
    tail still ends on the PREVIOUS reply. Both checks below rule that out: the server's last
    reply must answer the same user message as ours, and must not be the reply before it. */
export function completeUnfinishedTail(
  local: Message[] | null,
  remote: Message[],
): Message[] | null {
  if (!local || local.length < 2 || remote.length < 2) return null
  const mine = local[local.length - 1]
  const theirs = remote[remote.length - 1]
  if (mine.role !== 'assistant' || mine.usage || mine.error) return null
  if (theirs.role !== 'assistant' || theirs.error || !theirs.text?.trim()) return null
  if (theirs.text.trim().length <= (mine.text ?? '').trim().length) return null
  const myAsk = local[local.length - 2]
  const theirAsk = remote[remote.length - 2]
  if (myAsk.role !== 'user' || theirAsk.role !== 'user') return null
  if ((myAsk.text ?? '').trim() !== (theirAsk.text ?? '').trim()) return null
  if ((myAsk.attachments?.length ?? 0) !== (theirAsk.attachments?.length ?? 0)) return null
  const previousReply = local[local.length - 3]
  if (previousReply?.role === 'assistant' && previousReply.text.trim() === theirs.text.trim()) {
    return null
  }
  return [...local.slice(0, -1), theirs]
}

/**
 * Second-pass reconcile against the backend transcript, for a conversation that was
 * served from the local cache.
 *
 * The cache is written by whatever this browser happened to SEE. Open an Automation's
 * conversation while its run is still going and the cache freezes at that moment —
 * often just the opening instructions. `loadMessages` then keeps returning that stump
 * forever, because a non-empty cache short-circuits the backend read. The transcript
 * looked truncated even though the server had the whole thing.
 *
 * So: fetch the transcript anyway, in the background, and hand back a longer one if
 * there is one. Returns null when the cache is already current — the common case, and
 * the caller re-renders nothing.
 */
export async function reconcileWithRemote(
  convId: string,
  local: Message[] | null,
): Promise<Message[] | null> {
  if (!convId) return null
  const remote = await fetchRemoteMessages(convId, TRANSCRIPT_FETCH_TAIL)
  if (!remote) return null
  // A run parked on an approval card adds the card to the LAST message rather than
  // adding a message, so a cache taken before the card existed has the same last id and
  // would win forever — `shouldAdoptRemote` folds that in beside the advanced-past check.
  if (!shouldAdoptRemote(local, remote)) {
    const completed = completeUnfinishedTail(local, remote)
    if (completed) {
      void saveMessages(convId, completed)
      return completed
    }
    // The cache stays — but whether a message is restored is the SERVER's fact, never this
    // browser's: a restore (or its Undo) made in another tab or device must show here too.
    const marked = local ? withRestoreMarks(local, remote) : local
    if (!marked || marked === local) return null
    void saveMessages(convId, marked)
    return marked
  }
  void saveMessages(convId, remote)
  return remote
}

/** Copy the server's restore state for each checkpointed message — "Restored to before this
 *  message · Undo" (`restored` / `undoCheckpointId`) and "a later restore rewound past this
 *  turn" (`checkpointSuperseded`), all rebuilt by rehydrate_messages from the checkpoint
 *  index — onto `target`, matched by checkpoint id. Only messages the `source` snapshot
 *  covers are touched — a remote tail says nothing about older ones. Returns `target`
 *  itself when nothing differs. */
export function withRestoreMarks(target: Message[], source: Message[]): Message[] {
  const marks = new Map<string, Message>()
  for (const m of source) if (m.checkpointId) marks.set(m.checkpointId, m)
  let changed = false
  const out = target.map((m) => {
    const src = m.checkpointId ? marks.get(m.checkpointId) : undefined
    if (!src) return m
    if (
      Boolean(m.restored) === Boolean(src.restored) &&
      m.undoCheckpointId === src.undoCheckpointId &&
      Boolean(m.checkpointSuperseded) === Boolean(src.checkpointSuperseded)
    )
      return m
    changed = true
    return {
      ...m,
      restored: Boolean(src.restored),
      undoCheckpointId: src.undoCheckpointId,
      checkpointSuperseded: Boolean(src.checkpointSuperseded),
    }
  })
  return changed ? out : target
}

/** What `POST /api/checkpoints/{id}/restore` returns that the chat paints. The two id lists
 *  are absent from an older server — read as empty. */
export interface RestoreResult {
  undo_checkpoint_id?: string | null
  superseded_checkpoint_ids?: string[]
  revived_checkpoint_ids?: string[]
}

/** Paint a data-only restore of `checkpointId` onto the live chat, exactly as a reload would
 *  rebuild it. The restored message gets its Undo; later turns the restore rewound past lose
 *  Restore (`checkpointSuperseded`). When the call WAS an Undo (`checkpointId` is someone's
 *  pre-restore checkpoint) that message flips back and the turns it superseded return. */
export function applyRestoreResult(
  messages: Message[],
  checkpointId: string,
  result: RestoreResult,
): Message[] {
  const superseded = new Set(result.superseded_checkpoint_ids ?? [])
  const revived = new Set(result.revived_checkpoint_ids ?? [])
  return messages.map((m) => {
    if (!m.checkpointId) return m
    if (m.checkpointId === checkpointId) {
      return { ...m, restored: true, undoCheckpointId: result.undo_checkpoint_id ?? undefined }
    }
    if (m.undoCheckpointId === checkpointId) {
      return { ...m, restored: false, undoCheckpointId: undefined }
    }
    if (superseded.has(m.checkpointId)) return { ...m, checkpointSuperseded: true }
    if (revived.has(m.checkpointId)) return { ...m, checkpointSuperseded: false }
    return m
  })
}

/** Does this conversation end on an unanswered confirm/permission card? */
function hasOpenCard(messages: Message[] | null): boolean {
  const last = messages?.[messages.length - 1]
  return Boolean(last?.awaitingApproval || last?.proposedChanges?.length)
}

export async function saveMessages(convId: string, messages: Message[]): Promise<void> {
  try {
    await idbPut(convId, messages)
  } catch {
    /* best-effort — never let a storage write crash the chat UI */
  }
}

export async function deleteMessages(convId: string): Promise<void> {
  try {
    await idbDelete(convId)
  } catch {
    /* best-effort */
  }
}

/** Forget every cached conversation body — used when the book underneath them was replaced. */
export async function clearAllMessages(): Promise<void> {
  try {
    const db = await openDB()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      tx.objectStore(STORE).clear()
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    /* best-effort */
  }
}
