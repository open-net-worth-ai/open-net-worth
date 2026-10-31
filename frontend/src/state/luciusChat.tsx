// luciusChat.tsx — shared chat state for the Lucius surface (dock + side panel).
//
// Owns one useChat() + useConversations() instance mounted once at the RouterShell
// level so the dock and side panel share the SAME conversation thread.

import { markChatSwitch } from './chatHistory'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import type { ReactNode } from 'react'
import { useChat } from '../hooks/useChat'
import type { ConvRowStatus, Message } from '../types/chat'
import { useConversations } from '../hooks/useConversations'
import type { ConversationMeta } from '../hooks/useConversations'
import { chatSessions } from './chatSessions'
import type { ConvSlice, TurnStatus } from './chatSessions'
import { onLiveSessions, setLiveSessions } from '../lucius/bus'
import { authFetch } from '../api/client'
import { isAfterIso, laterIso } from '../utils/isoTime'
import {
  clampEffortChoice,
  resolveAutomationConversationSelection,
  useModels,
  type EffortChoice,
} from '../hooks/useModels'

// ── Row status derivation (concurrent-conversations-ui-spec.md §1.1/§1.2/§1.3) ────────────
// A pure function, deliberately factored out of the provider closure, so the priority
// ordering (needs_you > working > done > failed > idle) and the E7 reload-survival
// fallback are unit-testable without rendering React.
export interface RowStatusInputs {
  /** This browser's own live TurnStatus for the conversation, if it has a slice. */
  ownStatus: TurnStatus | undefined
  /** This browser's own outcome of the last turn that finished, if it has a slice. */
  ownLastResult: ConvSlice['lastResult'] | undefined
  /** Whether ANY known source (this browser, another tab, an Automation run) has a turn
   *  in flight for this conversation right now — chatSessions.subscribeAll ∪ the
   *  GET /api/sessions/live poll. Deliberately NOT used as the source of `needs_you`: the
   *  backend ends the live episode the instant a turn parks for approval (see the E6 note
   *  in docs/qa/acceptance/concurrent-conversations.md) — `remoteLastTurnStatus` is. */
  working: boolean
  /** Whether `id` is the conversation currently on screen — "an answer you're already
   *  looking at is not unread" gates `done`/`failed` from the durable fallback; `needs_you`
   *  is deliberately NOT gated on this (§1.1: not cleared by viewing, only by answering). */
  isActive: boolean
  /** The server's durable last_turn_status for this conversation, if known (a different
   *  device/tab's turn, or this browser's own record after a reload wiped its slice). */
  remoteLastTurnStatus?: 'ok' | 'error' | 'awaiting_approval' | 'cancelled'
  /** This conversation is an Automation run whose run FAILED. Overrides "read": a failure
   *  stays on the row until a later turn (a retry) settles the run otherwise. */
  runFailed?: boolean
  /** ISO — when the conversation last changed on the server (`updated_at`). */
  remoteUpdatedAt?: string
  /** ISO, server clock — when that last turn ENDED (`last_turn_at`). Preferred over
   *  `remoteUpdatedAt`, which also moves when the person sends their own message. */
  remoteLastTurnAt?: string
  /** ISO, server clock — when ANY of this person's clients last read the conversation
   *  (`last_read_at`). What makes "read it on my phone" clear the marker here too. */
  remoteLastReadAt?: string
  /** ISO — when this browser last viewed this conversation (E7's LAST_VIEWED_KEY). */
  lastViewedAt?: string
}

export function resolveRowStatus(inputs: RowStatusInputs): ConvRowStatus {
  const status = resolveTurnStatus(inputs)
  if (!inputs.runFailed) return status
  // A failed run outlives being read. Unread it is `failed` (the unread marker, same as a
  // failed turn); read, it is `run_failed` — still "Failed", no longer unread. A run that
  // failed after a turn that said `ok` (`done`) is a failure all the same.
  if (status === 'done') return 'failed'
  return status === 'idle' ? 'run_failed' : status
}

function resolveTurnStatus(inputs: RowStatusInputs): ConvRowStatus {
  const {
    ownStatus,
    ownLastResult,
    working,
    isActive,
    remoteLastTurnStatus,
    remoteUpdatedAt,
    remoteLastTurnAt,
    remoteLastReadAt,
    lastViewedAt,
  } = inputs
  const viewedAt = laterIso(lastViewedAt, remoteLastReadAt)
  const turnEndedAt = remoteLastTurnAt ?? remoteUpdatedAt
  // The server's record says this finished turn was already read — by this browser or by any
  // other client. A local slice that watched the turn end must still yield to that, or a reply
  // read on another device stays flagged here until a reload; the slice has no timestamp of
  // its own, so the server's record of WHEN the turn ended is the only honest comparison. Only
  // a record of the SAME outcome counts: a listing that has not caught up yet still describes
  // the previous turn, and its read stamp says nothing about the one this browser just saw.
  const readSince = (outcome: 'ok' | 'error') =>
    remoteLastTurnStatus === outcome && !!viewedAt && !isAfterIso(turnEndedAt, viewedAt)
  // needs_you outranks everything, including working, and is not gated on isActive — see
  // the field docs above. Sourced from TurnStatus (this browser's own conversation) or the
  // server's durable last_turn_status (everyone else's), never from `working`/is_live.
  if (ownStatus === 'awaiting_approval') return 'needs_you'
  if (remoteLastTurnStatus === 'awaiting_approval') return 'needs_you'
  if (working) return 'working'
  if (ownLastResult === 'error') return isActive || !readSince('error') ? 'failed' : 'idle'
  if (ownLastResult === 'ok' && !isActive) return readSince('ok') ? 'idle' : 'done'
  // The in-memory slice has no opinion (fresh page load, before any local turn history
  // exists for this id) — fall back to the server's durable record, gated on "ended since
  // anyone last read it" so a turn already seen doesn't re-flag itself as unread forever (E7).
  if ((ownLastResult === undefined || ownLastResult === 'none') && !isActive) {
    if (
      (remoteLastTurnStatus === 'ok' || remoteLastTurnStatus === 'error') &&
      isAfterIso(turnEndedAt, viewedAt)
    ) {
      return remoteLastTurnStatus === 'error' ? 'failed' : 'done'
    }
  }
  return 'idle'
}

// ── Draft text stash (dock → panel handoff) ────────────────────────────────────
// The dock serializes its composer text here before calling onExpand(); ChatPanel
// reads and clears it once on open so the draft lands in the shared LuciusComposer.
let _draftTextStash = ''
export function stashDraftText(text: string) {
  _draftTextStash = text
}
export function popDraftText(): string {
  const t = _draftTextStash
  _draftTextStash = ''
  return t
}

const DEFAULT_EFFORT: EffortChoice = 'medium'

export interface LuciusChatValue {
  // — from useConversations —
  conversations: ConversationMeta[]
  activeId: string
  conversationModel: string
  effort: EffortChoice
  setEffort: (effort: EffortChoice) => void
  updateConversationMeta: (id: string, messageCount: number, firstUserMessage?: string) => void
  renameConversation: (id: string, title: string) => void
  /** `replaceHistory`: a new chat the app opened on its own, not a navigation Back should undo. */
  createConversation: (opts?: { replaceHistory?: boolean }) => string
  switchConversation: (id: string, selection?: ConversationSelection) => void
  deleteConversation: (id: string) => void
  setConversationModel: (id: string, model: string) => void
  // — from useChat —
  messages: Message[]
  /** Sent while busy, not yet spliced into the true timeline — see
   *  chatSessions.ConvSlice.queuedMessages. */
  queuedMessages: Message[]
  streaming: boolean
  /** OFF ("one turn only", default) disables Send while streaming || sending — see
   *  ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md. */
  multiTurnEnabled: boolean
  /** Following a turn this browser did not start (an Automation run) — see useChat. */
  following: boolean
  suggestions: string[]
  suggestionsLoading: boolean
  hydrated: boolean
  /** A silent transcript re-read is in flight — see chatSessions.ConvSlice.catchingUp. */
  catchingUp: boolean
  /** A send whose 202 has not landed yet. Together with `streaming` this is what "busy"
   *  means to the composer — it flips the send control to Stop the instant you send,
   *  rather than after the server finishes routing (which took seconds). */
  sending: boolean
  /** Server-reported output tokens on the running turn (0 when idle). */
  liveOutputTokens: number
  /** Non-null only during the pre-turn context-fold ("compacting") phase — see
   *  chatSessions.ConvSlice.compacting / CompactingActivity. */
  compacting: { percent: number; outputTokens?: number } | null
  /** Epoch ms timestamp when the active turn started. Persists across screen navigation. */
  startedAt?: number
  sendMessage: ReturnType<typeof useChat>['sendMessage']
  resolvePendingToolUse: ReturnType<typeof useChat>['resolvePendingToolUse']
  fetchSuggestions: ReturnType<typeof useChat>['fetchSuggestions']
  stopStreaming: ReturnType<typeof useChat>['stopStreaming']
  retryLast: ReturnType<typeof useChat>['retryLast']
  /** Whether this browser sent a message to replay — an Automation conversation never has one. */
  canRetry: boolean
  respondToChange: ReturnType<typeof useChat>['respondToChange']
  previewCheckpoint: ReturnType<typeof useChat>['previewCheckpoint']
  restoreCheckpoint: ReturnType<typeof useChat>['restoreCheckpoint']
  // — which conversations are working right now, and each one's row-level status.
  // RFC .claude/rfcs/concurrent-conversations.md Phase 1 step 4;
  // .claude/rfcs/concurrent-conversations-ui-spec.md §1.2 defines the exact shape and
  // composition. Data only — the visual indicators (ConversationStatusBadge,
  // TurnStatusBar, the mobile badge) are a later, separate UI lane.
  streamingIds: Set<string>
  statusFor: (id: string) => ConvRowStatus
  /** Abort `id`'s in-flight turn regardless of `activeId` (§5.1.3 quick-Stop on a sidebar
   *  row). A sibling to `stopStreaming` above, not a replacement — `stopStreaming` stays
   *  scoped to the VIEWED conversation via the composer/TurnStatusBar (B5). */
  stopConversation: (id: string) => void
  /** Mark conversations read for every client (server-side), clearing their "New reply". */
  markRead: (ids: string[]) => void
}

export interface ConversationSelection {
  model: string
  effort: string
}

const LuciusChatContext = createContext<LuciusChatValue | null>(null)

interface ProviderProps {
  children: ReactNode
}

// How often to poll GET /api/sessions/live for the app-wide "who is working right now"
// set. Independent of which tab is mounted — see the effect below for why this cannot be
// Automations.tsx's job. A plain fixed interval, not adaptive: backing off while nothing is
// live would also remove the only signal that tells us something just started.
const LIVE_POLL_MS = 4_000
const FOCUS_REFRESH_MIN_MS = 10_000

/** Mount once inside ChatComposerProvider + ScreenSnapshotProvider in RouterShell.
 * Data refresh after AI writes rides the lucius bus (data_changed domains →
 * useDataVersion) — no refresh callbacks are threaded through here anymore. */
export function LuciusChatProvider({ children }: ProviderProps) {
  const {
    conversations,
    activeId,
    conversationModel,
    updateConversationMeta,
    renameConversation,
    switchConversation: switchConversationRaw,
    deleteConversation,
    setConversationModel: setConversationModelRaw,
    confirmConversationModel,
    lastViewed,
    touchLastViewed,
    markRead,
    refreshConversations,
    ensurePersisted,
    createConversation: createConversationRaw,
  } = useConversations()
  const modelInfo = useModels(true)
  const [effort, setEffortState] = useState<EffortChoice>(
    () => (localStorage.getItem('chat-effort') as EffortChoice) || DEFAULT_EFFORT,
  )
  const setEffort = useCallback((next: EffortChoice) => {
    setEffortState(next)
    localStorage.setItem('chat-effort', next)
  }, [])
  // Effort is a single global preference (not per-conversation like model), so nothing
  // resets it on its own. Without this, a new conversation started model reset to the
  // instance default while effort silently carried over from whatever was last picked —
  // the same "new conversation" action leaving two related controls out of sync.
  const createConversation = useCallback(
    (opts?: { replaceHistory?: boolean }) => {
      setEffort(DEFAULT_EFFORT)
      if (!opts?.replaceHistory) markChatSwitch()
      return createConversationRaw()
    },
    [createConversationRaw, setEffort],
  )
  const pendingAutomationSelectionRef = useRef<{
    id: string
    selection: ConversationSelection
  } | null>(null)
  const applyAutomationSelection = useCallback(
    (id: string, selection: ConversationSelection) => {
      const adopted = resolveAutomationConversationSelection(
        selection,
        modelInfo.models,
        modelInfo.defaultModel,
      )
      setConversationModelRaw(id, adopted.model)
      setEffort(adopted.effort)
    },
    [modelInfo.models, modelInfo.defaultModel, setConversationModelRaw, setEffort],
  )
  const setConversationModel = useCallback(
    (id: string, model: string) => {
      if (pendingAutomationSelectionRef.current?.id === id) {
        pendingAutomationSelectionRef.current = null
      }
      setConversationModelRaw(id, model)
    },
    [setConversationModelRaw],
  )
  const switchConversation = useCallback(
    (id: string, selection?: ConversationSelection) => {
      if (selection) {
        // Automation configuration is a fallback, not an explicit human preference. Only
        // adopt it when it is in the enabled catalogue; otherwise select the instance
        // default (`''`) in both the picker and the request body.
        if (modelInfo.loaded) {
          pendingAutomationSelectionRef.current = null
          applyAutomationSelection(id, selection)
        } else {
          pendingAutomationSelectionRef.current = { id, selection }
          setConversationModelRaw(id, '')
          setEffort(
            clampEffortChoice(selection.effort || 'medium', [
              'low',
              'medium',
              'high',
              'xhigh',
              'max',
            ]),
          )
        }
      }
      if (id !== activeId) markChatSwitch()
      switchConversationRaw(id)
    },
    [
      activeId,
      modelInfo.loaded,
      applyAutomationSelection,
      setConversationModelRaw,
      setEffort,
      switchConversationRaw,
    ],
  )
  useEffect(() => {
    if (!modelInfo.loaded) return
    const pending = pendingAutomationSelectionRef.current
    if (!pending) return
    pendingAutomationSelectionRef.current = null
    if (pending.id !== activeId) return
    applyAutomationSelection(pending.id, pending.selection)
  }, [activeId, applyAutomationSelection, modelInfo.loaded])

  // Automation runs can hand us a durable backend session before it appears in the
  // conversation index. Honour the explicit id so the panel hydrates that session
  // instead of silently falling back to the first local conversation.
  const activeConversation = conversations.find((c) => c.id === activeId)
  // `activeId` is authoritative and read FIRST: its row in `conversations` can be briefly
  // absent (a fresh mint, or a boot cache mid-reconcile — see
  // useConversations.writeCachedList, which now always keeps the active row in, closing
  // the gap this used to leave for a whole session). The MESSAGES hydrate straight off
  // `activeId` itself (useChat → IndexedDB/backend), never off this metadata lookup — a
  // missing row here means stale title/model for one render, not wrong messages. '' is the
  // honest "no conversation yet" sentinel for the one case where `activeId` is itself empty.
  const conversationId = activeId || activeConversation?.id || conversations[0]?.id || ''

  const {
    messages,
    queuedMessages,
    streaming,
    multiTurnEnabled,
    sending,
    liveOutputTokens,
    compacting,
    following,
    suggestions,
    suggestionsLoading,
    hydrated,
    catchingUp,
    startedAt,
    sendMessage: rawSendMessage,
    resolvePendingToolUse,
    fetchSuggestions,
    stopStreaming,
    retryLast,
    canRetry,
    respondToChange,
    previewCheckpoint,
    restoreCheckpoint,
    // `updateConversationMeta` is passed straight through — no wrapper closing over
    // `conversationId`. That closure used to be the latent bug: under concurrency it would
    // stamp the VIEWED conversation's metadata with the STREAMING conversation's message
    // count and title (docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md).
    // useChat now calls this with an explicit owner id every time, so there is nothing left
    // to close over.
  } = useChat(conversationId, updateConversationMeta, true)

  // Promote a `persist: false` conversation (Lucius home tab's deferred-creation flow) the
  // instant it has real content — before the underlying send, so the PATCH/sidebar-listing
  // race never shows a message arriving into a conversation the sidebar doesn't know about
  // yet. A no-op for every normal (eagerly-persisted) conversation, i.e. every existing
  // caller of sendMessage (dock, mobile) is unaffected — ensurePersisted only acts on an id
  // it actually minted as pending.
  const sendMessage = useCallback(
    async (...args: Parameters<typeof rawSendMessage>) => {
      ensurePersisted(conversationId)
      await rawSendMessage(...args)
      confirmConversationModel(conversationId)
    },
    [ensurePersisted, conversationId, rawSendMessage, confirmConversationModel],
  )

  // Clear the "unread done/failed" marker the instant a conversation becomes the viewed
  // one — "an answer you're already looking at is not unread"
  // (concurrent-conversations-ui-spec.md §1.1).
  //
  // The conversation being LEFT needs the same clear. Its `lastResult` is set the moment a
  // turn ends — including one you sat and watched — and nothing reset it on the way out, so
  // switching away flagged "New reply" on an answer you had just read, until a reload. What
  // you saw while it was on screen is read: clear it and tell the server so every client
  // agrees. A turn still running here is untouched — it sets `lastResult` again when it ends,
  // by which point the conversation really is one you are not looking at.
  const viewedIdRef = useRef('')
  useEffect(() => {
    const left = viewedIdRef.current
    viewedIdRef.current = conversationId
    if (left && left !== conversationId) {
      chatSessions.apply(left, (s) => (s.lastResult === 'none' ? s : { ...s, lastResult: 'none' }))
      markRead([left])
    }
    if (!conversationId) return
    chatSessions.apply(conversationId, (s) =>
      s.lastResult === 'none' ? s : { ...s, lastResult: 'none' },
    )
  }, [conversationId, markRead])

  // A read made on another device only reaches this browser with the next listing. Re-read it
  // when the window comes back into view, so "I read it on my phone" is gone from here by the
  // time you look — throttled, since focus and visibility both fire on a single return.
  const lastFocusRefreshRef = useRef(0)
  useEffect(() => {
    function onReturn() {
      if (document.visibilityState === 'hidden') return
      const now = Date.now()
      if (now - lastFocusRefreshRef.current < FOCUS_REFRESH_MIN_MS) return
      lastFocusRefreshRef.current = now
      void refreshConversations()
    }
    document.addEventListener('visibilitychange', onReturn)
    window.addEventListener('focus', onReturn)
    return () => {
      document.removeEventListener('visibilitychange', onReturn)
      window.removeEventListener('focus', onReturn)
    }
  }, [refreshConversations])

  // ...and keep the DURABLE stamp fresh for the conversation on screen. Stamping only on
  // switch-in left a turn that completed while you were reading it looking unread the
  // moment you switched away: `updated_at` had moved past the stamp. Keyed on the message
  // count so it re-stamps as the transcript grows under your eyes.
  const viewedCount = messages.length
  useEffect(() => {
    if (conversationId) touchLastViewed(conversationId)
  }, [conversationId, viewedCount, touchLastViewed])

  // App-wide "who is working right now", independent of which tab is mounted.
  //
  // GET /api/sessions/live already existed on the backend with exactly ONE frontend
  // caller — Automations.tsx, which derived the live set from ITS OWN per-automation run
  // poll (scoped to whichever automation happened to be selected there) and reset it to []
  // on unmount. So the `working` indicator only ever lit up while sitting on the
  // Automations tab, looking at the right automation — everywhere else, including the
  // Lucius panel this whole feature exists for, the live set was permanently empty. Poll
  // the real endpoint directly, here, so it is populated regardless of which surface is
  // open. Automations.tsx keeps consuming the SAME bus unchanged; it is no longer the
  // producer.
  //
  // `titled` rides the same poll: a new conversation's AI title is written in the
  // background and can land after its turn ended (and after the refresh that ending
  // triggered), so an entry this poll has not seen yet re-reads the listing too.
  useEffect(() => {
    let cancelled = false
    const seenTitled = new Map<string, number>()
    async function poll() {
      try {
        const res = await authFetch('/api/sessions/live')
        if (cancelled || !res.ok) return
        const data = await res.json()
        const ids = Array.isArray(data.session_ids) ? data.session_ids : []
        setLiveSessions(ids)
        const titled: Record<string, unknown> =
          data.titled && typeof data.titled === 'object' ? data.titled : {}
        let fresh = false
        for (const [id, at] of Object.entries(titled)) {
          if (typeof at !== 'number' || seenTitled.get(id) === at) continue
          seenTitled.set(id, at)
          fresh = true
        }
        if (fresh) void refreshConversations()
      } catch {
        // A failed poll must not clear a real `working` state — keep the last known-good
        // set (mirrors apiFetchSessions' "a failed listing is not an empty one").
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), LIVE_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [refreshConversations])

  const [liveSessionIds, setLiveSessionIdsState] = useState<string[]>([])
  useEffect(() => onLiveSessions(setLiveSessionIdsState), [])

  // E4: when a session LEAVES the live set its turn just ended, which is the exact moment
  // its outcome (`last_turn_status`) became knowable — so re-read the listing. Without this
  // useConversations fetched /api/sessions once at mount and never again, so a turn that
  // finished in a conversation this browser never touched (an Automation run, another tab,
  // a background turn you switched away from) stayed invisible until a reload: no `done`
  // row, no mobile tab badge. Keyed off the DEPARTURE, not the arrival — a turn that is
  // still running has no outcome to fetch.
  const prevLiveRef = useRef<string[]>([])
  useEffect(() => {
    const prev = prevLiveRef.current
    prevLiveRef.current = liveSessionIds
    if (prev.some((id) => !liveSessionIds.includes(id))) void refreshConversations()
  }, [liveSessionIds, refreshConversations])

  // This browser's own busy conversations — a coarser subscription than the per-slice one
  // useChat binds to: it only wakes on a STATUS transition (streaming started/ended), never
  // on message content, so a token landing in a background conversation can't re-render
  // every consumer of `streamingIds` (see chatSessions.subscribeAll).
  const ownStreamingIds = useSyncExternalStore(
    chatSessions.subscribeAll,
    chatSessions.getStreamingIdsSnapshot,
  )
  const streamingIds = useMemo(
    () => new Set([...ownStreamingIds, ...liveSessionIds]),
    [ownStreamingIds, liveSessionIds],
  )

  const statusFor = useCallback(
    (id: string): ConvRowStatus => {
      const slice = chatSessions.peekSlice(id)
      const meta = conversations.find((c) => c.id === id)
      return resolveRowStatus({
        ownStatus: slice?.status,
        ownLastResult: slice?.lastResult,
        working: streamingIds.has(id),
        isActive: id === activeId,
        remoteLastTurnStatus: meta?.lastTurnStatus,
        runFailed: meta?.runStatus === 'failed',
        remoteUpdatedAt: meta?.updatedAt,
        remoteLastTurnAt: meta?.lastTurnAt,
        remoteLastReadAt: meta?.lastReadAt,
        lastViewedAt: lastViewed[id],
      })
    },
    [streamingIds, conversations, activeId, lastViewed],
  )

  // Quick-Stop on a `working` sidebar row (§5.1.3) — cancels BY ID regardless of `activeId`.
  // Server-owned turn lifetime (concurrent-conversations RFC Phase 3) retired the
  // per-slice AbortController this used to abort directly: a turn's actual work is now a
  // background task the server drives, and for a BACKGROUND row (id !== activeId) there is
  // no locally-mounted attach effect watching it at all — sendMessage's own fetch is just
  // the quick POST that returns a 202, not a long-lived stream anymore. So this posts
  // POST /api/chat/{id}/stop (same endpoint useChat.ts's stopStreaming calls) and lets the
  // existing polls (GET /api/sessions/live → streamingIds, GET /api/sessions →
  // last_turn_status, both already wired into statusFor above) reflect the real outcome —
  // no local optimistic flip, so the row can never show 'idle' while the server is still
  // mid-cancellation.
  const stopConversation = useCallback((id: string) => {
    void authFetch(`/api/chat/${id}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
  }, [])

  // ── aria-live announcer (§5.6) — one shared, always-mounted region, not one per row. ──
  // Announces a TRANSITION into done/failed/needs_you (never `working` — a "started"
  // announcement on every turn would be noise the visual dot already covers for a sighted
  // user). Skips the very first computation (mount) so a page load doesn't recite every
  // pre-existing unread row at once — only genuine transitions after that.
  const [announcement, setAnnouncement] = useState('')
  const prevRowStatusRef = useRef<Map<string, ConvRowStatus> | null>(null)
  useEffect(() => {
    const prev = prevRowStatusRef.current
    const next = new Map(conversations.map((c) => [c.id, statusFor(c.id)] as const))
    prevRowStatusRef.current = next
    if (!prev) return
    for (const c of conversations) {
      const status = next.get(c.id)
      if (status === prev.get(c.id)) continue
      if (status !== 'done' && status !== 'failed' && status !== 'needs_you') continue
      setAnnouncement(
        status === 'done'
          ? `${c.title} finished`
          : status === 'failed'
            ? `${c.title} failed`
            : `${c.title} needs your approval.`,
      )
      break
    }
    // conversations/streamingIds/activeId are the signals that can move a row's status;
    // statusFor is a stable derivation of them, not an independent trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations, streamingIds, activeId])

  const value: LuciusChatValue = {
    conversations,
    activeId,
    conversationModel,
    effort,
    setEffort,
    updateConversationMeta,
    renameConversation,
    createConversation,
    switchConversation,
    deleteConversation,
    setConversationModel,
    messages,
    queuedMessages,
    streaming,
    multiTurnEnabled,
    sending,
    liveOutputTokens,
    compacting,
    startedAt,
    following,
    suggestions,
    suggestionsLoading,
    hydrated,
    catchingUp,
    sendMessage,
    resolvePendingToolUse,
    fetchSuggestions,
    stopStreaming,
    retryLast,
    canRetry,
    respondToChange,
    previewCheckpoint,
    restoreCheckpoint,
    streamingIds,
    statusFor,
    stopConversation,
    markRead,
  }

  return (
    <LuciusChatContext.Provider value={value}>
      {children}
      <div className="ui-visually-hidden" role="status" aria-live="polite">
        {announcement}
      </div>
    </LuciusChatContext.Provider>
  )
}

/** Read the shared Lucius chat state. Must be used inside <LuciusChatProvider>. */
export function useLuciusChat(): LuciusChatValue {
  const ctx = useContext(LuciusChatContext)
  if (!ctx) throw new Error('useLuciusChat must be used within <LuciusChatProvider>')
  return ctx
}

/** Optional form for reusable surfaces that also render in isolated tests and embeds. */
export function useLuciusChatOptional(): LuciusChatValue | null {
  return useContext(LuciusChatContext)
}
