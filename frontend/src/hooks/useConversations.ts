import { useCallback, useEffect, useRef, useState } from 'react'
import { v4 as uuid } from 'uuid'
import { deleteMessages } from '../utils/chatStore'
import { authFetch } from '../api/client'
import { isStandalonePwa } from '../utils/pwa'
import { conversationTitleFromFirstMessage } from '../utils/conversationTitle'
import { laterIso } from '../utils/isoTime'

export interface ConversationMeta {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messageCount: number
  // Per-conversation model override ('' = use the instance default). Mirrors the
  // server-side per-session model so a chat keeps running on the chosen model.
  model: string
  // Who started it: 'automation' for a run's own conversation, absent for a chat. The
  // switcher groups on this — twenty runs of one Automation all carry ITS name, so
  // interleaved with real chats they read as twenty duplicates of the same thread.
  source?: string
  /** Set only on a public demo, for a conversation every visitor shares (an Automation run):
   *  readable, but it takes no message, so the chat never opens one by default. */
  readOnly?: boolean
  /** Transport the human is on ('telegram', …). UNSET = in-app. */
  channel?: string
  /** What started it: 'capture' (a Shortcut / Apple Pay booking turn) is a background
   *  conversation, hidden from the lists unless the person turns them on. UNSET = a chat. */
  origin?: string
  /** Who wrote the title: 'ai' (generated from the opening message), 'user' (a rename) or
   *  'auto'/absent (derived by this client). The server's copy wins for 'ai'/'user'. */
  titleSource?: string
  // Outcome of the last turn on this conversation, as the server durably recorded it —
  // 'ok' | 'error' | 'awaiting_approval' | 'cancelled' | absent (no turn has completed
  // yet, or the server predates this field). Feeds `statusFor()`'s durable source
  // (.claude/rfcs/concurrent-conversations-ui-spec.md §1.2/§1.3) for a conversation this
  // browser has no local slice for (a different device/tab's turn, or a fresh page load —
  // E6/E7). 'cancelled' — the user's own explicit Stop — is intentionally not surfaced as
  // any non-idle row state: nothing went wrong, and the user who stopped it already knows.
  lastTurnStatus?: 'ok' | 'error' | 'awaiting_approval' | 'cancelled'
  /** An Automation run's own outcome ('running' | 'waiting' | 'completed' | 'failed' |
   *  'cancelled'), on its conversation's row only. A run can fail after a turn that said 'ok'
   *  (an unfinished plan, a restart), so the turn status alone can't say "Failed". */
  runStatus?: string
  /** ISO, server clock — when that last turn ended. "A reply arrived" is this, not
   *  `updatedAt` (which also moves for the person's own message). */
  lastTurnAt?: string
  /** ISO, server clock — when ANY of this person's clients last read the conversation. The
   *  unread marker is `lastTurnAt` after this; it lives on the server so reading a reply on
   *  one device clears it on every other. */
  lastReadAt?: string
}

/** A conversation nobody typed into — a capture's booking turn. Kept as a record, listed only
 *  when the person turns background conversations on, and never opened by default. */
export function isBackgroundConversation(conv: Pick<ConversationMeta, 'origin'>): boolean {
  return !!conv.origin && conv.origin !== 'chat'
}

const ACTIVE_KEY = 'lucius_active_conversation'
const MODELS_KEY = 'lucius_conversation_models'
// Last-known conversation listing, so a cold boot paints the REAL conversation instead of
// an empty one. Metadata only (~50 short rows); the message bodies live in IndexedDB.
const LIST_KEY = 'lucius_conversation_list'
// Per-conversation "I last looked at this at time T" (E7 — concurrent-conversations-ui-spec.md
// §1.3). The in-memory `ConvSlice.lastResult` clears the moment a conversation becomes
// active, but that memory does not survive a reload — this durable map is what lets
// statusFor's server-record fallback tell "finished while you were away, still unseen"
// apart from "finished, but you already looked at it since."
const LAST_VIEWED_KEY = 'lucius_conversation_last_viewed'
// The one still-unpersisted (`persist: false`) draft conversation, if any — mirrors
// `pendingConvsRef` across a reload. Without this, a hard refresh while sitting on a never-
// typed-into draft loses it from memory entirely, and the boot reconciliation below (which
// only knows about *server* sessions) falls back to the most recent real conversation —
// silently swapping out the empty draft for old content. At most one row: `createConversation`
// overwrites it, `ensurePersisted` clears it.
const PENDING_KEY = 'lucius_pending_conversation'
const MAX_CONVS = 50

function readActiveId(): string {
  try {
    return localStorage.getItem(ACTIVE_KEY) || ''
  } catch {
    return ''
  }
}

function readCachedList(): ConversationMeta[] {
  try {
    const raw = localStorage.getItem(LIST_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (!Array.isArray(parsed)) return []
    // This feeds the FIRST paint, so a row from an older shape must not reach the header as
    // `undefined`. Anything that isn't a usable row is dropped; the fetch refills it.
    return (parsed as ConversationMeta[])
      .filter((c) => c && typeof c.id === 'string' && c.id !== '' && typeof c.title === 'string')
      .map((c) => ({ ...c, title: normalizeConversationTitle(c.title) }))
  } catch {
    return []
  }
}

function normalizeConversationTitle(title: string): string {
  return conversationTitleFromFirstMessage(title)
}

// The cache is bounded so it never grows past what a boot paint needs — but bounding by
// position (top N) alone can drop the row a *reload* needs most: if `activeId` isn't
// among the most-recently-updated MAX_CONVS (opened an old thread via the Browser without
// sending anything), the next boot's `readActiveId()` restores the pointer correctly while
// `readCachedList()` has no row for it — ChatPanel then falls back to an unrelated
// conversation's title AND model until /api/sessions reconciles. Always keep the active
// row in, even when it falls outside the top-N by recency.
function writeCachedList(rows: ConversationMeta[], activeId: string): void {
  try {
    const top = rows.slice(0, MAX_CONVS)
    const activeRow = activeId ? rows.find((c) => c.id === activeId) : undefined
    const withActive = activeRow && !top.some((c) => c.id === activeId) ? [activeRow, ...top] : top
    localStorage.setItem(LIST_KEY, JSON.stringify(withActive))
  } catch {
    /* storage full / unavailable — the server listing is still authoritative */
  }
}

// Per-conversation model overrides, persisted to localStorage as { [id]: model }.
// This gives an instant choice before the first turn; the server (chat POST +
// /api/sessions) is the durable, cross-device source of truth.
function readModelMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(MODELS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function writeModel(id: string, model: string): void {
  const map = readModelMap()
  // '' is a REAL choice (the instance default), so it is STORED, never deleted —
  // a missing entry and "use the default" must not be the same thing.
  map[id] = model
  try {
    localStorage.setItem(MODELS_KEY, JSON.stringify(map))
  } catch {
    /* storage full / unavailable — server-side persistence still applies */
  }
}

function readLastViewed(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LAST_VIEWED_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function writeLastViewed(id: string): Record<string, string> {
  const map = readLastViewed()
  map[id] = new Date().toISOString()
  try {
    localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify(map))
  } catch {
    /* storage full / unavailable — worst case a stale record re-flags a seen turn */
  }
  return map
}

// The server's own stamp replaces the optimistic local one: a local stamp is the CLIENT's
// clock, compared against `last_turn_at` which is the SERVER's, so keeping it would let clock
// skew hide a genuinely new reply (or flag a read one).
function adoptServerStamps(stamps: Record<string, string>): Record<string, string> {
  const map = { ...readLastViewed(), ...stamps }
  try {
    localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify(map))
  } catch {
    /* storage full / unavailable — the server's copy is the durable one */
  }
  return map
}

const READ_BATCH = 200
// A viewed transcript changes in bursts (hydration, then each message). The server stamp is
// only worth sending once it settles — and a turn's own end-of-turn write lands a few ms AFTER
// its last streamed message, which an immediate stamp would race.
const READ_DEBOUNCE_MS = 1_500

/** Tell the server these conversations were read. `null` = could not be reached — the
 *  caller keeps its optimistic local stamp, which is the best available until the next mark. */
async function apiMarkRead(ids: string[]): Promise<Record<string, string> | null> {
  try {
    const res = await authFetch('/api/sessions/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    })
    if (!res.ok) return null
    const data = await res.json()
    const stamps = data?.last_read_at
    return stamps && typeof stamps === 'object' ? (stamps as Record<string, string>) : null
  } catch {
    return null
  }
}

function readPendingConversation(): ConversationMeta | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed.id !== 'string' || typeof parsed.title !== 'string') return null
    return parsed as ConversationMeta
  } catch {
    return null
  }
}

function writePendingConversation(c: ConversationMeta | null): void {
  try {
    if (c) localStorage.setItem(PENDING_KEY, JSON.stringify(c))
    else localStorage.removeItem(PENDING_KEY)
  } catch {
    /* storage full / unavailable — the draft just won't survive a hard reload */
  }
}

function makeConversation(): ConversationMeta {
  const now = new Date().toISOString()
  return {
    id: uuid(),
    title: 'New conversation',
    createdAt: now,
    updatedAt: now,
    messageCount: 0,
    // '' = the instance default (Settings → AI Models). Automatic model switching was
    // removed (ADR 2026-08-06), so this is the only default there is; an explicit user
    // selection overrides it per conversation.
    model: '',
  }
}

// Fresh PWA launch → replace the persisted "resume this conversation" pointer with a brand
// new, still-unpersisted draft (the exact shape createConversation()'s own persist:false path
// produces), so opening the installed app after fully closing it starts a NEW conversation
// instead of resuming wherever the user left off (owner, 2026-08-13). Writing it as a PENDING
// draft (not just clearing ACTIVE_KEY) matters: the boot reconciliation effect below already
// knows how to keep a pending draft alive instead of falling back to the server's most recent
// real session — reusing that existing path rather than special-casing "no match" a second way.
// Gated on sessionStorage, not localStorage: sessionStorage is scoped to ONE browsing-context
// instance and — per iOS's own handling of standalone home-screen web apps — is cleared
// exactly when that instance is actually terminated and relaunched, so a plain reload or
// backgrounding within the SAME still-alive instance keeps it and still resumes normally.
// Runs once at module load, before any readActiveId()/readPendingConversation() call below.
const PWA_SESSION_SEEN_KEY = 'lucius_pwa_session_seen'
if (isStandalonePwa()) {
  try {
    if (!sessionStorage.getItem(PWA_SESSION_SEEN_KEY)) {
      sessionStorage.setItem(PWA_SESSION_SEEN_KEY, '1')
      const draft = makeConversation()
      writePendingConversation(draft)
      localStorage.setItem(ACTIVE_KEY, draft.id)
    }
  } catch {
    /* storage unavailable — falls back to the normal resume behavior */
  }
}

/** The server's conversation listing, or `null` when it could not be read.
 *
 * A FAILED listing is not an empty one. Returning [] for a 401/502/timeout made the two
 * indistinguishable, and the boot path reads "empty" as "mint a new conversation" — so one
 * dropped request (a resumed mobile tab, a token refresh, cellular) minted a ghost session
 * and overwrote the pointer to the real thread. `null` never mints.
 */
async function apiFetchSessions(): Promise<ConversationMeta[] | null> {
  try {
    const res = await authFetch('/api/sessions')
    if (!res.ok) return null
    const data = await res.json()
    if (!Array.isArray(data)) return null
    const localModels = readModelMap()
    return data.map(
      (s: {
        id: string
        title: string
        created_at: string
        updated_at: string
        message_count: number
        model?: string
        source?: string
        read_only?: boolean
        channel?: string
        origin?: string
        title_source?: string
        last_turn_status?: string
        run_status?: string
        last_turn_at?: string
        last_read_at?: string
      }) => {
        const title = normalizeConversationTitle(s.title)
        if (title !== s.title) void apiPatchSession(s.id, title, { auto: true })
        return {
          id: s.id,
          title,
          createdAt: s.created_at,
          updatedAt: s.updated_at,
          messageCount: s.message_count,
          // A present server value wins even when it is blank: blank is the explicit
          // "use the app default" choice, not a missing value that may resurrect a stale
          // browser override. Legacy rows that truly omit `model` still use the local map.
          model: typeof s.model === 'string' ? s.model.trim() : (localModels[s.id] || '').trim(),
          source: s.source,
          ...(s.read_only === true && { readOnly: true }),
          channel: s.channel,
          ...(s.origin && { origin: s.origin }),
          titleSource: s.title_source,
          lastTurnStatus: lastTurnStatusOf(s.last_turn_status),
          ...(s.run_status && { runStatus: s.run_status }),
          lastTurnAt: s.last_turn_at,
          lastReadAt: s.last_read_at,
        }
      },
    )
  } catch {
    return null
  }
}

/** The server's turn outcome, narrowed to what a row shows. A turn the watchdog cut off
 *  (`timeout`) or a restart broke (`interrupted`) failed as surely as one that raised — left
 *  unmapped, those rows never said "Failed" at all (owner report 2026-10-14). */
export function lastTurnStatusOf(raw: string | undefined): ConversationMeta['lastTurnStatus'] {
  if (raw === 'timeout' || raw === 'interrupted') return 'error'
  return raw === 'ok' || raw === 'error' || raw === 'awaiting_approval' || raw === 'cancelled'
    ? raw
    : undefined
}

/** Merge a freshly-fetched server listing over the previously-known rows.
 *
 * Server rows win for everything — EXCEPT: a listing must never SHRINK a conversation this
 * client already has content for. A `message_count: 0` server row for an id the local copy
 * already reports as non-empty is a STALE read of that listing (owner report, phone,
 * 2026-09-06: a stale `message_count: 0` for a six-message conversation made the panel paint
 * the empty-conversation greeting over it the instant a turn ended) — keep the local count
 * and title in that one case. This protection applies regardless of `keepLocalOnly`.
 *
 * `keepLocalOnly` controls a SEPARATE question: what to do with a row `prev` knows about that
 * `rows` doesn't mention at all. `true` appends it after the server rows — the documented
 * `refreshConversations` case, protecting an optimistic adopt (e.g. `switchConversation`'s
 * placeholder) the server hasn't caught up on yet. `false` drops it — the boot effect and
 * `switchConversation`'s own authoritative refetch, where an id missing from the FULL listing
 * normally means the conversation was deleted (e.g. on another device), and keeping it around
 * (then writing it back into the localStorage boot cache) would mean it can never be evicted. */
export function mergeServerRows(
  prev: ConversationMeta[],
  rows: ConversationMeta[],
  opts: { keepLocalOnly: boolean },
): ConversationMeta[] {
  const prevById = new Map(prev.map((c) => [c.id, c]))
  const merged = rows.map((row) => {
    const local = prevById.get(row.id)
    // A read stamp only moves forward. A listing requested BEFORE this client marked the
    // conversation read can land AFTER the mark's own response — taking it wholesale would
    // flip the row back to unread until the next refresh.
    const lastReadAt = laterIso(row.lastReadAt, local?.lastReadAt)
    const withRead = lastReadAt === row.lastReadAt ? row : { ...row, lastReadAt }
    if (local && row.messageCount === 0 && local.messageCount > 0) {
      // ...except a title the server OWNS. A conversation's first turn has not persisted a
      // message yet, so its row reads 0 for exactly as long as the AI title is landing —
      // keeping the local title here hid that title until the reply finished.
      const serverOwnsTitle = row.titleSource === 'ai' || row.titleSource === 'user'
      return {
        ...withRead,
        messageCount: local.messageCount,
        title: serverOwnsTitle ? row.title : local.title,
      }
    }
    return withRead
  })
  if (!opts.keepLocalOnly) return merged
  const seen = new Set(rows.map((r) => r.id))
  return [...merged, ...prev.filter((c) => !seen.has(c.id))]
}

/** `auto: true` marks a title this client derived itself (first message, placeholder,
 *  normalization). The server lets it yield to the AI-written title and to a rename, so a
 *  late automatic write can never replace either. Only a rename the person typed omits it. */
async function apiPatchSession(
  id: string,
  title: string,
  opts?: { auto?: boolean },
): Promise<void> {
  await authFetch(`/api/sessions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts?.auto ? { title, auto: true } : { title }),
  })
}

export function useConversations() {
  // Seeded SYNCHRONOUSLY from localStorage so the first paint already targets the
  // conversation you were last in: useChat hydrates it from IndexedDB immediately, and the
  // header/switcher render its real title. Starting empty meant every boot showed the
  // "New conversation" hero for the length of a GET /api/sessions round trip — invisible on
  // localhost, the entire screen on a phone. The fetch below reconciles both.
  const [conversations, setConversations] = useState<ConversationMeta[]>(readCachedList)
  const [activeId, setActiveIdState] = useState<string>(readActiveId)
  const activeIdRef = useRef(activeId)
  const [lastViewed, setLastViewed] = useState<Record<string, string>>(readLastViewed)
  const pendingConvsRef = useRef(
    (() => {
      const p = readPendingConversation()
      return new Map<string, ConversationMeta>(p ? [[p.id, p]] : [])
    })(),
  )
  // A draft is deliberately absent from `conversations`, but its model is still live UI
  // state. Keep selections made in this mount separate from the server listing so an async
  // reconciliation cannot overwrite a picker choice before its first send persists it.
  const [modelSelections, setModelSelections] = useState<Record<string, string>>(() => {
    const pending = readPendingConversation()
    return pending ? { [pending.id]: pending.model || '' } : {}
  })
  const conversationModel = Object.prototype.hasOwnProperty.call(modelSelections, activeId)
    ? modelSelections[activeId]!
    : (conversations.find((c) => c.id === activeId)?.model ?? '')

  useEffect(() => {
    apiFetchSessions().then((sessions) => {
      // Could not read the listing. Keep the restored conversation and the saved pointer —
      // its messages are cached locally, so the thread still renders. Only mint when there
      // is genuinely nothing to fall back to, since the composer needs a real id to write
      // into; that mint has no previous thread to strand.
      if (!sessions) {
        if (activeIdRef.current) return
        mintFirstConversation()
        return
      }
      // A read-only row (a public demo's shared Automation run) takes no message, so it is
      // never the default; with only those, open a fresh chat and keep them listed.
      // Nor is a background capture: it is a record, not where you left off.
      const first = sessions.find((s) => !s.readOnly && !isBackgroundConversation(s))
      if (!first) {
        mintFirstConversation(sessions)
        return
      }
      setConversations((prev) => mergeServerRows(prev, sessions, { keepLocalOnly: false }))
      const saved = activeIdRef.current || readActiveId()
      // A still-unpromoted draft never reaches the server, so it never appears in `sessions`
      // — that alone can't distinguish "stale pointer, fall back" from "you're mid-draft,
      // keep it." The persisted pending marker can.
      const pending = readPendingConversation()
      const savedIsPendingDraft = !!pending && pending.id === saved
      const active = sessions.some((s) => s.id === saved) || savedIsPendingDraft ? saved : first.id
      activeIdRef.current = active
      setActiveIdState(active)
      localStorage.setItem(ACTIVE_KEY, active)
    })

    function mintFirstConversation(serverRows: ConversationMeta[] = []) {
      const saved = activeIdRef.current || readActiveId()
      const pending = readPendingConversation()
      if (pending && pending.id === saved) {
        if (serverRows.length) {
          setConversations((prev) => mergeServerRows(prev, serverRows, { keepLocalOnly: true }))
        }
        // No real sessions exist yet, but the saved pointer is a legitimate still-unpersisted
        // draft — keep it rather than minting a real conversation to replace it.
        activeIdRef.current = saved
        setActiveIdState(saved)
        localStorage.setItem(ACTIVE_KEY, saved)
        return
      }
      const c = makeConversation()
      apiPatchSession(c.id, c.title, { auto: true }).catch(() => {})
      setConversations([c, ...serverRows])
      activeIdRef.current = c.id
      setActiveIdState(c.id)
      localStorage.setItem(ACTIVE_KEY, c.id)
    }
  }, [])

  // Keep the boot cache current. Metadata only, and bounded — see LIST_KEY.
  useEffect(() => {
    writeCachedList(conversations, activeId)
  }, [conversations, activeId])

  const readTimersRef = useRef(new Map<string, number>())
  useEffect(() => {
    const timers = readTimersRef.current
    return () => {
      for (const t of timers.values()) window.clearTimeout(t)
      timers.clear()
    }
  }, [])

  const applyReadStamps = useCallback((stamps: Record<string, string>) => {
    setLastViewed(adoptServerStamps(stamps))
    setConversations((prev) => {
      let changed = false
      const next = prev.map((c) => {
        const stamp = stamps[c.id]
        if (!stamp) return c
        const lastReadAt = laterIso(c.lastReadAt, stamp)
        if (lastReadAt === c.lastReadAt) return c
        changed = true
        return { ...c, lastReadAt }
      })
      return changed ? next : prev
    })
  }, [])

  /** Mark conversations read — for EVERY client, not just this browser.
   *
   *  The local stamp lands first so a row clears this frame; the server then answers with
   *  its own stamp, which is what other devices see. An id that is still an unsent draft has
   *  no server row and is left to the local stamp alone. */
  const markRead = useCallback(
    (ids: string[]) => {
      const unique = [...new Set(ids.filter(Boolean))]
      if (unique.length === 0) return
      for (const id of unique) {
        window.clearTimeout(readTimersRef.current.get(id))
        readTimersRef.current.delete(id)
      }
      let local: Record<string, string> = {}
      for (const id of unique) local = writeLastViewed(id)
      setLastViewed(local)
      const persisted = unique.filter((id) => !pendingConvsRef.current.has(id))
      for (let i = 0; i < persisted.length; i += READ_BATCH) {
        void apiMarkRead(persisted.slice(i, i + READ_BATCH)).then((stamps) => {
          if (stamps) applyReadStamps(stamps)
        })
      }
    },
    [applyReadStamps],
  )

  /** Re-stamp "I have looked at this" for a conversation that is ON SCREEN right now.
   *
   *  switchConversation stamps on the way IN, which is not enough: a turn that finishes
   *  while you are sitting in the conversation ends AFTER that stamp, so the row flags
   *  itself "New reply" the instant you switch away — for an answer you watched arrive.
   *  Called whenever the viewed conversation's transcript changes: the local stamp is
   *  immediate, the server's trails the burst (see READ_DEBOUNCE_MS). */
  const touchLastViewed = useCallback(
    (id: string) => {
      if (!id) return
      setLastViewed(writeLastViewed(id))
      const timers = readTimersRef.current
      window.clearTimeout(timers.get(id))
      timers.set(
        id,
        window.setTimeout(() => {
          timers.delete(id)
          if (pendingConvsRef.current.has(id)) return
          void apiMarkRead([id]).then((stamps) => {
            if (stamps) applyReadStamps(stamps)
          })
        }, READ_DEBOUNCE_MS),
      )
    },
    [applyReadStamps],
  )

  const setActiveId = useCallback((id: string) => {
    activeIdRef.current = id
    localStorage.setItem(ACTIVE_KEY, id)
    setActiveIdState(id)
  }, [])

  /** Re-read the server listing to pick up `last_turn_status` / `last_turn_at`.
   *
   * E4's gap: this hook fetched /api/sessions ONCE at mount, so a turn that finished in a
   * conversation THIS browser never touched — an Automation run, another tab, a background
   * turn you switched away from — never updated the row. The Lucius tab badge and the
   * `done` row state both read that field, so both stayed invisible until a reload. The
   * caller (LuciusChatProvider) fires this when a session leaves the live set, which is
   * exactly the moment its outcome became knowable.
   *
   * A null listing is a FAILED read, not an empty one — same rule as boot: never let a
   * dropped request blank a list we already have. */
  const refreshConversations = useCallback(async () => {
    const rows = await apiFetchSessions()
    if (!rows) return
    setConversations((prev) => {
      const merged = mergeServerRows(prev, rows, { keepLocalOnly: true })
      const changed = merged.length !== prev.length || merged.some((c, i) => c !== prev[i])
      return changed ? merged : prev
    })
  }, [])

  const updateConversationMeta = useCallback(
    (id: string, messageCount: number, firstUserMessage?: string) => {
      setConversations((prev) => {
        const now = new Date().toISOString()
        const updated = prev.map((c) => {
          if (c.id !== id) return c
          const title =
            firstUserMessage && (c.title === 'New conversation' || c.title.includes('[skill:'))
              ? conversationTitleFromFirstMessage(firstUserMessage)
              : c.title
          if (title !== c.title) apiPatchSession(id, title, { auto: true }).catch(() => {})
          // Only a conversation that GREW has been updated. This fires on every hydration
          // too — opening a conversation reports the count it already had — and stamping
          // `now` there made "last updated" mean "last looked at": a conversation you
          // merely glanced at jumped above one you actually used, and then dropped back on
          // reload, because the server was never told. The server's own updated_at moves
          // when a turn happens, which is the same rule.
          const grew = messageCount > c.messageCount
          if (!grew && title === c.title && messageCount === c.messageCount) return c
          return { ...c, title, messageCount, updatedAt: grew ? now : c.updatedAt }
        })
        // Identity-stable when nothing actually moved. This fires on every messages change —
        // i.e. every streamed token — and a fresh array each time re-rendered every chat
        // consumer and rewrote the boot cache for no change at all.
        if (updated.every((c, i) => c === prev[i])) return prev
        updated.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
        return updated
      })
    },
    [],
  )

  // Set the per-conversation model override ('' clears it back to the default).
  // Persists locally for instant memory; the server is updated on the next chat
  // POST (which carries `model`), so there is no separate write here.
  const setConversationModel = useCallback((id: string, model: string) => {
    const trimmed = (model || '').trim()
    writeModel(id, trimmed)
    setModelSelections((prev) => ({ ...prev, [id]: trimmed }))
    const pending = pendingConvsRef.current.get(id)
    if (pending) {
      const updated = { ...pending, model: trimmed }
      pendingConvsRef.current.set(id, updated)
      writePendingConversation(updated)
    }
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, model: trimmed } : c)))
  }, [])

  // The POST /api/chat response is the acknowledgement boundary: the backend persisted
  // the exact explicit model before returning 202. Retire the optimistic overlay only
  // then, so later authoritative /api/sessions updates (including another device) can win.
  const confirmConversationModel = useCallback((id: string) => {
    setModelSelections((prev) => {
      if (!Object.prototype.hasOwnProperty.call(prev, id)) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
  }, [])

  const renameConversation = useCallback((id: string, title: string) => {
    const normalized = normalizeConversationTitle(title)
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title: normalized } : c)))
    apiPatchSession(id, normalized).catch(() => {})
  }, [])

  // A `persist: false` conversation exists ONLY client-side (an id + activeId pointer) until
  // `ensurePersisted` promotes it — no PATCH, no entry in `conversations` (so it never shows
  // in the sidebar/switcher), until it actually has something to show. This is now the
  // DEFAULT: every real caller (the docked panel's New/Clear, the floating dock's New/Clear,
  // the Lucius home tab, the app-wide fresh-open signal) wants the deferred form, so
  // `createConversation()` with no args means `persist: false` — `{ persist: true }` is the
  // explicit opt-in for the one remaining case that genuinely wants it eagerly in the list
  // (the capacity regression test, which is specifically about the EAGER list's behavior).
  const createConversation = useCallback(
    (opts?: { persist?: boolean }): string => {
      const c = makeConversation()
      if (opts?.persist !== true) {
        pendingConvsRef.current.set(c.id, c)
        writePendingConversation(c)
        setActiveId(c.id)
        return c.id
      }
      apiPatchSession(c.id, c.title, { auto: true }).catch(() => {})
      // NOT capped here — same reasoning as switchConversation below: MAX_CONVS bounds the
      // localStorage BOOT CACHE (writeCachedList), not the live in-memory list.
      // ConversationBrowser searches/renders directly off this array with no independent
      // fetch of its own, so slicing it here silently hid every conversation past the cap
      // (verified: seed 60 server-backed conversations, call createConversation() once →
      // the oldest 10 vanished from state, unreachable in the Browser until a reload).
      setConversations((prev) => [c, ...prev])
      setActiveId(c.id)
      return c.id
    },
    [setActiveId],
  )

  /** Promote a `persist: false` conversation the moment it actually has content — called
   *  from luciusChat.tsx right before a real send. No-op for an id that was never pending
   *  (i.e. every conversation created the normal, eager way) or already promoted. */
  const ensurePersisted = useCallback((id: string) => {
    const pending = pendingConvsRef.current.get(id)
    if (!pending) return
    pendingConvsRef.current.delete(id)
    writePendingConversation(null)
    apiPatchSession(pending.id, pending.title, { auto: true }).catch(() => {})
    setConversations((prev) => (prev.some((c) => c.id === id) ? prev : [pending, ...prev]))
  }, [])

  const switchConversation = useCallback(
    (id: string) => {
      setActiveId(id)
      // E7: stamp "I looked at this now" durably (survives a reload), the moment the
      // switch happens — not on some later render. statusFor's server-record fallback
      // compares this against the conversation's updated_at to tell "finished while you
      // were away, still unseen" apart from "finished, but you already looked since".
      markRead([id])
      // ADOPT a session the client has never seen. An Automation run mints its
      // conversation server-side AFTER this list was fetched, so tapping that run
      // set an activeId pointing at nothing: the panel had no entry to render and
      // silently kept showing the previous thread. Setting the id alone was never
      // enough — the list is what the panel renders from.
      //
      // The row is inserted optimistically (so the switch is instant) and then
      // reconciled from /api/sessions, which is authoritative for title/counts and
      // already returns Automation sessions. Messages come from
      // /api/sessions/{id}/messages on demand, so nothing else has to change.
      setConversations((prev) => {
        if (prev.some((c) => c.id === id)) return prev
        const now = new Date().toISOString()
        const placeholder: ConversationMeta = {
          id,
          title: 'Automation run',
          createdAt: now,
          updatedAt: now,
          messageCount: 0,
          model: '',
        }
        // NOT capped here. The cap exists to bound a list this client grows itself; the
        // fetched list is server-backed and every row in it is reachable. Slicing it to 50
        // after adopting a run threw away everything below — and since Automation runs are
        // always the most recent, "everything below" was every real chat. The switcher
        // ended up showing 50 runs of one Automation and no way back to a conversation.
        return [placeholder, ...prev]
      })
      // Then take the authoritative list wholesale rather than patching the one row: it
      // carries the real title, counts and source for every session, including any other
      // runs that have appeared since this list was fetched.
      void apiFetchSessions().then((rows) => {
        // null = the listing could not be read; keep the optimistic row rather than
        // blanking a switcher we still have a good local copy of.
        if (!rows || !rows.some((c) => c.id === id)) return
        setConversations((prev) => mergeServerRows(prev, rows, { keepLocalOnly: false }))
      })
    },
    [setActiveId, markRead],
  )

  const deleteConversation = useCallback(
    async (id: string) => {
      try {
        const response = await authFetch(`/api/chat/${id}`, { method: 'DELETE' })
        if (!response.ok) return
      } catch {
        return
      }
      deleteMessages(id)
      setConversations((prev) => {
        let updated = prev.filter((c) => c.id !== id)
        if (id === activeIdRef.current) {
          let next = updated.find((c) => !isBackgroundConversation(c))
          if (!next) {
            next = makeConversation()
            apiPatchSession(next.id, next.title, { auto: true }).catch(() => {})
            updated = [next]
          }
          setActiveId(next.id)
        }
        return updated
      })
    },
    [setActiveId],
  )

  return {
    conversations,
    activeId,
    conversationModel,
    updateConversationMeta,
    renameConversation,
    createConversation,
    ensurePersisted,
    switchConversation,
    deleteConversation,
    setConversationModel,
    confirmConversationModel,
    lastViewed,
    touchLastViewed,
    markRead,
    refreshConversations,
  }
}
