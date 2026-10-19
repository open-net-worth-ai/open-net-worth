import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { v4 as uuid } from 'uuid'
import type { Attachment, Message, ProposedChange } from '../types/chat'
import type { MentionRef } from '../types/mentions'
import type { ScreenSnapshotWire } from '../state/screenSnapshot'
import {
  applyRestoreResult,
  loadMessages,
  reconcileWithRemote,
  withRestoreMarks,
} from '../utils/chatStore'
import { fetchRevision, forgetRevision, syncedRevision } from '../utils/chatRevision'
import { blockCount, contentCount } from '../utils/chatBubbles'
import { authFetch } from '../api/client'
import { emitDataChanged, onLiveSessions } from '../lucius/bus'
import { useChatMultiTurnEnabled } from './useChatSuggestionsEnabled'
import { chatSessions } from '../state/chatSessions'
import {
  addUserMessage,
  addQueuedMessage,
  deliverQueuedMessage,
  ensureAssistant,
  clearAllStreaming,
  consumeEventStream,
  fetchSuggestionsFor,
  clearSuggestions,
  setMessages,
  updateLastAssistant,
  openAssistant,
  closeOpenAssistant,
  setMetaSink,
} from '../state/chatReducer'

// Data refresh rides the backend's `data_changed` SSE (write tools self-declare
// mutates= domains in the registry) → emitDataChanged → useDataVersion subscribers.
// The old hand-maintained frontend MUTATING_TOOLS list is gone.
//
// State lives in chatSessions.ts, one slice per conversation id — this hook is a
// SLICE-BINDER, not a state owner (RFC .claude/rfcs/concurrent-conversations.md Phase 1;
// ADR docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md). Every
// mutating call below captures `ownerId = conversationId` ONCE, at the top of the
// function, and never re-reads `conversationId` after that point — that is what lets a
// turn started while viewing A keep writing to A's slice even after the user switches the
// view to B.

/** How long a page must have been hidden before a still-`streaming` attach is presumed dead
 *  and reconnected on return. Long enough that a quick tab switch never tears down a healthy
 *  stream (each reconnect replays the turn); short enough to catch a phone's suspension. */
const STALE_STREAM_AWAY_MS = 15_000

/** Re-read the transcript, showing "Catching up…" ONLY when the server has already said the
 *  conversation changed. A re-read on a guess (first open after a reload, an older server)
 *  stays silent: showing the pill and then finding nothing new is a flash for nothing. */
async function catchUp(
  ownerId: string,
  local: Message[] | null,
  knownChanged: boolean,
): Promise<Message[] | null> {
  if (!knownChanged) return reconcileWithRemote(ownerId, local)
  chatSessions.apply(ownerId, (s) => (s.catchingUp ? s : { ...s, catchingUp: true }))
  try {
    return await reconcileWithRemote(ownerId, local)
  } finally {
    chatSessions.apply(ownerId, (s) => (s.catchingUp ? { ...s, catchingUp: false } : s))
  }
}

/** Re-read the transcript only if the server's change stamp moved since this browser's
 *  last fetch — an unchanged conversation costs a few bytes and shows nothing. No stamp to
 *  compare (never fetched here, an older server) falls back to a silent re-read. */
async function resyncIfChanged(ownerId: string): Promise<void> {
  const known = syncedRevision(ownerId)
  const current = known ? await fetchRevision(ownerId) : null
  if (known && current === known) return
  adoptReconciled(
    ownerId,
    await catchUp(ownerId, chatSessions.getSlice(ownerId).messages, Boolean(current)),
  )
}

/** Is the server's copy genuinely richer than what we have? Blocks count too: a chip, a
 *  receipt or a card arrives as a BLOCK on an existing message, leaving the count identical.
 *  Messages count as CONTENT, not bubbles — the measure chatStore.shouldAdoptRemote uses — or
 *  a failed-send bubble the server never had outvotes the reply it finished (2026-10-15). */
function isRicher(fresher: Message[], prev: Message[]): boolean {
  const ours = contentCount(prev)
  const theirs = contentCount(fresher)
  if (theirs !== ours) return theirs > ours
  return blockCount(fresher) > blockCount(prev)
}

/** Adopt the transcript re-read that ends an attach, unless a fresher attach owns the view. */
function adoptReconciled(ownerId: string, full: Message[] | null): void {
  // NOT gated on `cancelled`: the attach ending is what flips the slice idle,
  // which can drop `shouldAttach` and run the effect's teardown before the read
  // lands. After a suspension that read is the ONLY copy of the reply — dropping
  // it left a phone showing just the user's message (live report 2026-10-14). A
  // fresher attach is caught below by `streaming`, the check that means it.
  if (!full) return
  // A reconcile is a point-in-time HTTP snapshot. If a FRESHER attach has
  // already resumed streaming by the time it resolves — this connection's own
  // teardown is what triggered the reconcile in the first place, e.g. a queued
  // send forcing attachGen to force a fresh reconnect — that live stream is the
  // current authoritative source, never a snapshot that may predate it and
  // still be mid-flux around whatever the live stream is now handling. Adopting
  // it anyway showed messages in the wrong relative order for as long as it
  // took the NEXT reconcile to correct it (live report 2026-08-10: a queued
  // message visibly jumped above the response it was queued under, then
  // settled back once the whole exchange finished).
  if (chatSessions.getSlice(ownerId).status === 'streaming') {
    return forgetRevision(ownerId)
  }
  setMessages(ownerId, (prev) => (isRicher(full, prev) ? full : withRestoreMarks(prev, full)))
}

/** Paint a send that never reached the server: the failure IS assistant output, so it gets
 *  a bubble of its own (none is pre-opened), and the slice ends in `error` for Retry. */
function paintSendFailure(ownerId: string, error: string): void {
  ensureAssistant(ownerId)
  updateLastAssistant(ownerId, (m) => ({
    ...m,
    text: m.text || 'Sorry, something went wrong.',
    error,
    streaming: false,
  }))
  closeOpenAssistant(ownerId) // nothing else is coming for this bubble
  chatSessions.apply(ownerId, (s) => ({
    ...s,
    status: 'error',
    sending: false,
    lastResult: 'error',
    startedAt: undefined,
  }))
}

/** The attach that checked an unconfirmed send has ended and the transcript re-read is in:
 *  a message still unanswered never reached the server (or nothing can tell us it did). */
function settleUnconfirmedSend(ownerId: string): void {
  const pending = chatSessions.getSlice(ownerId).unconfirmedSend
  if (!pending) return
  chatSessions.apply(ownerId, (s) => ({ ...s, unconfirmedSend: null }))
  if (chatSessions.getSlice(ownerId).messages.at(-1)?.role === 'user') {
    paintSendFailure(ownerId, pending.error)
  }
}

export function useChat(
  conversationId: string,
  onMessagesUpdate: (convId: string, count: number, firstUserMessage?: string) => void,
  suggestionsEnabled = true,
) {
  // Exactly one useChat instance exists for the whole app (LuciusChatProvider), so one
  // current sink is enough — see chatReducer.setMetaSink for why this can't be a plain
  // React effect over `messages` (a conversation NOT on screen still needs to notify).
  useEffect(() => {
    setMetaSink(onMessagesUpdate)
  }, [onMessagesUpdate])

  const { enabled: multiTurnEnabled } = useChatMultiTurnEnabled()

  const subscribeSlice = useCallback(
    (cb: () => void) => chatSessions.subscribe(conversationId, cb),
    [conversationId],
  )
  const getSnapshot = useCallback(() => chatSessions.getSlice(conversationId), [conversationId])
  const slice = useSyncExternalStore(subscribeSlice, getSnapshot)

  // The '' sentinel (no conversation yet) must never read as hydrated — loadMessages('')
  // short-circuits to null, so hydrating it would report a fully-loaded empty conversation
  // before the app has one, painting the empty hero / firing suggestions / arming the
  // onboarding auto-send against a conversation that doesn't exist.
  const hydrated = Boolean(conversationId) && slice.hydrated
  const streaming = slice.status === 'streaming'

  // Effect A: hydrate a conversation's slice from IndexedDB → backend transcript, ONCE per
  // slice — never again on a later revisit. The slice already holds whatever this browser
  // knows, including anything a background turn wrote since the last visit; re-hydrating
  // on every switch (the old behaviour, forced by having only ONE global `messages` array)
  // would clobber that with a stale cache read.
  useEffect(() => {
    // Hydrated AND EMPTY is not a result worth protecting — it is the absence of one. The
    // guard exists so a stale cache read can't clobber a populated slice; an empty slice has
    // nothing to clobber. Without the length check, opening an Automation's conversation ONCE
    // while it is still empty (mid-run, or before the server has written the transcript at
    // all) stamps `{messages: [], hydrated: true}` permanently: this effect never re-runs for
    // that conversation, and `reconcileWithRemote` cannot rescue it either, because
    // `shouldAdoptRemote` returns false on an empty remote. A completed run with 38 messages
    // on the server then rendered the empty-state greeting forever, while a LIVE run looked
    // fine — the live-attach path populates the slice without going through hydration at all,
    // which is exactly why the bug looked random (owner, 2026-08-16).
    const cached = conversationId ? chatSessions.getSlice(conversationId) : null
    if (!conversationId || (cached?.hydrated && cached.messages.length > 0)) return
    let cancelled = false
    loadMessages(conversationId)
      .then((loaded) => {
        if (cancelled) return null
        // Same rule as the guard above: an already-hydrated slice wins ONLY if it actually
        // holds something. Keeping the bare `s.hydrated` test here would throw away the very
        // refetch the guard was widened to allow, leaving the empty slice sealed exactly as
        // before.
        chatSessions.apply(conversationId, (s) =>
          s.hydrated && s.messages.length > 0
            ? s
            : {
                ...s,
                messages: loaded ?? [],
                hydrated: true,
              },
        )
        // Then quietly check the server for a longer transcript. An Automation's
        // conversation is written server-side, so a cache captured mid-run is a stump —
        // see reconcileWithRemote. Silent: nothing is known to have changed yet.
        return catchUp(conversationId, loaded, false)
      })
      .then((fresher) => {
        if (!fresher) return
        if (cancelled) return forgetRevision(conversationId)
        // Only adopt it if nothing else has touched the conversation meanwhile — a turn
        // the user started while this was in flight owns the state now.
        setMessages(conversationId, (prev) =>
          isRicher(fresher, prev) ? fresher : withRestoreMarks(prev, fresher),
        )
      })
    return () => {
      cancelled = true
    }
  }, [conversationId])

  const fetchSuggestions = useCallback(
    () => fetchSuggestionsFor(conversationId, suggestionsEnabled),
    [conversationId, suggestionsEnabled],
  )

  // Effect A2: ATTACH to whatever turn is running on the VIEWED conversation right now —
  // one started by THIS browser (sendMessage / resolvePendingToolUse, below) just as much
  // as one started elsewhere (an Automation run, another tab/device). Server-owned turn
  // lifetime (concurrent-conversations RFC Phase 3): POST /api/chat and
  // POST /api/chat/{id}/resolve no longer stream a response — they return 202 the instant
  // the turn is spawned server-side, and EVERY turn's events reach a client only through
  // this same GET /api/sessions/:id/live attach, fed through the SAME consumeEventStream
  // reducer a run has always used. So a turn renders identically token-by-token regardless
  // of who started it, replays what has already happened on connect (never joins mid-
  // sentence), and closes itself when the turn ends.
  //
  // A pure function of slice state, not of who posted — this is what replaces the old
  // `!streaming` guard: `streaming` now means "this slice has a turn in flight", whether
  // this tab is the one that started it or not, and there is exactly one attach per slice
  // either way.
  const [liveSessions, setLiveSessionIds] = useState<string[]>([])
  useEffect(() => onLiveSessions(setLiveSessionIds), [])
  const shouldAttach = hydrated && (streaming || liveSessions.includes(conversationId))
  // Exposed as `following` — kept to its ORIGINAL, narrower meaning ("watching a turn this
  // browser did not start") for every existing consumer of the hook's return value; it is
  // not what drives the attach effect above.
  // "Someone else's turn": running, but not one we opened. It no longer gates Stop — a run
  // registers in `turns` like any chat turn, so the same endpoint cancels both.
  const following = streaming && !slice.startedHere

  useEffect(() => {
    if (!shouldAttach) return
    let cancelled = false
    const ctrl = new AbortController()
    const ownerId = conversationId
    void (async () => {
      // Mark the slice busy for the duration of ANY attach, not just one this browser
      // started. `streaming` means "this slice has a turn in flight" regardless of who
      // opened it (see the comment above) — but only sendMessage/resolve ever set it, so
      // following an Automation run left it false and the working indicator never showed
      // while the run's text streamed in. The finally below already resets it to idle.
      chatSessions.apply(ownerId, (s) =>
        s.status === 'streaming'
          ? s
          : {
              ...s,
              status: 'streaming',
              startedHere: false,
              startedAt: s.startedAt ?? Date.now(),
            },
      )
      // Read-and-clear, once, before this connection's own fetch even starts: only
      // resolvePendingToolUse ever sets this, synchronously, right before the attachGen
      // bump that triggers THIS firing — so it identifies this specific connection as a
      // deliberate resume. Clearing it immediately means a LATER, different attachGen bump
      // (an ordinary reconnect) never inherits a stale resume flag it never earned.
      const isResume = chatSessions.getSlice(ownerId).resumeInFlight
      if (isResume) {
        chatSessions.apply(ownerId, (s) => (s.resumeInFlight ? { ...s, resumeInFlight: false } : s))
      }
      try {
        const res = await authFetch(`/api/sessions/${ownerId}/live`, { signal: ctrl.signal })
        if (cancelled) return
        // The turn's events land on the LAST assistant message, which is what
        // consumeEventStream expects. sendMessage already opened a fresh empty one before
        // this effect could even fire (streaming only flips true after its POST's 202
        // lands — see sendMessage); resolvePendingToolUse re-opens the EXISTING card's
        // message instead of a new one. Either way, opening a SECOND bubble here would be
        // the exact duplication bug this design is required to avoid — only add one when
        // the last message is not already a live one.
        // No bubble opened on attach either — the reducer creates one when the stream
        // actually produces something, so a re-attach to a turn that emits nothing (a
        // finished turn, an empty replay) leaves no empty bubble behind.
        await consumeEventStream(ownerId, res, { suggestionsEnabled, isResume })
      } catch {
        // Disconnected or nothing live. The transcript is the durable record — reconcile
        // below repairs whatever the stream missed. Aborting THIS fetch (unmount, view
        // switch) must never stop the turn itself — it keeps running server-side and this
        // same effect re-attaches the next time the slice is viewed while still streaming.
      } finally {
        // The single most important rule here (RFC §5.5): every path that ends this
        // attach — success, error, or a plain disconnect — MUST clear `streaming`. A
        // spinner that never resolves is exactly the failure mode this exists to forbid.
        if (!cancelled) {
          chatSessions.apply(ownerId, (s) => (s.startedHere ? { ...s, startedHere: false } : s))
          clearAllStreaming(ownerId)
          // A user Stop sends NO event at all — backend/ai/turns.py deliberately: "a
          // deliberate Stop is not a failure... stream_end alone tells watchers nothing
          // more is coming." So turn_done/error (the only in-band events that otherwise
          // close the open bubble) never fire, and openAssistantId is left dangling on
          // the stopped turn's bubble. ensureAssistant's reuse check only verifies the
          // pointed-to message still EXISTS somewhere in the array — not that it's still
          // the trailing one (that laxity is deliberate, for a message queued mid-stream
          // while the SAME turn is still genuinely live) — so the next brand-new message
          // sent afterward had its real response silently written into the old, stale,
          // now out-of-order bubble instead of a fresh one. Closing here, gated on
          // `!cancelled` exactly like the streaming-clear above, only fires when THIS
          // attach concluded on its own (success, error, or Stop) — never when a fresher
          // reconnect superseded it (that path sets cancelled=true first), so a still-live
          // turn's own reconnect is unaffected. Live report 2026-08-11.
          closeOpenAssistant(ownerId)
          chatSessions.apply(ownerId, (s) =>
            s.status === 'streaming'
              ? { ...s, status: 'idle', startedAt: undefined }
              : { ...s, startedAt: undefined },
          )
          // Backfill against the transcript. Pass the REAL cache, not null — reconcileWithRemote's
          // own "never truncates" gate needs it (passing null made it treat the cache as
          // empty and always hand back the server's lossier reconstruction).
          void reconcileWithRemote(ownerId, chatSessions.getSlice(ownerId).messages).then(
            (full) => {
              adoptReconciled(ownerId, full)
              // A fresher attach owns the view and will settle the send when IT ends.
              if (chatSessions.getSlice(ownerId).status !== 'streaming') {
                settleUnconfirmedSend(ownerId)
              }
            },
          )
        }
      }
    })()
    return () => {
      cancelled = true
      ctrl.abort()
    }
  }, [shouldAttach, conversationId, suggestionsEnabled, slice.attachGen])

  // Coming back into view. A phone suspends a backgrounded page and drops its GET /live:
  // the attach's finally marks the slice idle and reconciles once, AT the drop, while the
  // turn is still running — and nothing ever looks again (hydration is once per slice, and
  // a still-live conversation never toggles `shouldAttach`). The view froze mid-turn while
  // the sidebar said "Needs you" (live report 2026-10-07). So on return: re-attach anything
  // live — the replay dedups what is already on screen — and re-read the transcript of a
  // turn that ended while we were away. A stream still `streaming` is reconnected only
  // after a long absence (a socket left half-open by suspension), never on a quick switch.
  const liveRef = useRef(liveSessions)
  liveRef.current = liveSessions
  useEffect(() => {
    if (!conversationId) return
    const ownerId = conversationId
    let hiddenAt = document.visibilityState === 'hidden' ? Date.now() : 0
    function onReturn(e: Event) {
      if (document.visibilityState === 'hidden') {
        if (e.type === 'visibilitychange') hiddenAt = Date.now()
        return
      }
      const away = hiddenAt ? Date.now() - hiddenAt : 0
      hiddenAt = 0
      const s = chatSessions.getSlice(ownerId)
      if (!s.hydrated) return
      if (s.status === 'streaming') {
        if (away >= STALE_STREAM_AWAY_MS || e.type === 'online') {
          chatSessions.apply(ownerId, (x) => ({ ...x, attachGen: x.attachGen + 1 }))
        }
        return
      }
      if (liveRef.current.includes(ownerId)) {
        chatSessions.apply(ownerId, (x) => ({ ...x, attachGen: x.attachGen + 1 }))
        return
      }
      void resyncIfChanged(ownerId)
    }
    document.addEventListener('visibilitychange', onReturn)
    window.addEventListener('pageshow', onReturn)
    window.addEventListener('online', onReturn)
    return () => {
      document.removeEventListener('visibilitychange', onReturn)
      window.removeEventListener('pageshow', onReturn)
      window.removeEventListener('online', onReturn)
    }
  }, [conversationId])

  const sendMessage = useCallback(
    async (
      text: string,
      attachments: Attachment[] = [],
      effort = 'medium',
      silent = false,
      model = '',
      mentions: MentionRef[] = [],
      respondingAgent: string | null = null,
      skills: string[] = [],
      screen: ScreenSnapshotWire | null = null,
      retry = false,
    ) => {
      if (!silent && !text.trim() && attachments.length === 0) return
      // Captured ONCE, here — never re-read from `conversationId` again in this call. If
      // the view moves on to another conversation mid-turn, every write below still lands
      // on THIS conversation (INV-2).
      const ownerId = conversationId
      const _slice = chatSessions.getSlice(ownerId)
      // Includes `sending`: a POST whose 202 has not landed yet is ALREADY busy. Reading
      // only `status` here is what let a fast second send open an orphan bubble.
      const busy = _slice.status === 'streaming' || _slice.sending
      // Pre-RFC hard guard, restored when multi-turn is off (ADR
      // docs/adr/2026-08-10-multi-turn-chat-defaults-off.md) — silent no-op, matching the
      // old `if (streaming) return`, never an error. The composer disables Send for the
      // same reason; this is the belt to its braces.
      if (busy && !multiTurnEnabled) return
      const wasAlreadyStreaming = _slice.status === 'streaming'
      const messageId = uuid()

      if (!silent) {
        chatSessions.apply(ownerId, (s) => ({
          ...s,
          lastSent: { text, attachments, effort, model, mentions, respondingAgent, skills, screen },
        }))
      }

      // Stale suggestions belong to the previous turn — clear them while we answer.
      clearSuggestions(ownerId)

      if (!silent) {
        // Inline, immediately — a message you sent belongs in the thread you sent it to.
        // A message sent while BUSY goes to its own queued list, never straight into
        // `messages` — see ConvSlice.queuedMessages for why: `messages` is the true,
        // ordered timeline, and a question typed ahead of its own answer must not be
        // able to steal the array slot that answer needs once it actually arrives.
        const msg: Message = { id: messageId, role: 'user', text, attachments }
        if (busy) {
          addQueuedMessage(ownerId, { ...msg, pending: 'queued' as const })
        } else {
          addUserMessage(ownerId, msg)
        }
      }

      // No assistant bubble opened here. It is created by the assistant's first output
      // (chatReducer.ensureAssistant); until then TurnActivity is the working indicator.
      // Pre-opening one was the root of the ordering, duplicate-bubble and stuck-spinner
      // bugs — it made the client guess the transcript's shape instead of mirroring it.

      // Synchronous, before the await: every later read of `busy` — including a send the
      // user fires half a second from now — must already see this conversation as busy.
      if (!busy)
        chatSessions.apply(ownerId, (s) => ({
          ...s,
          sending: true,
          liveOutputTokens: 0,
          startedAt: Date.now(),
        }))

      try {
        // POST /api/chat now returns 202 the instant the turn is spawned server-side
        // (concurrent-conversations RFC Phase 3) — it no longer streams a response. The
        // slice is marked 'streaming' only AFTER this resolves, deliberately: the
        // server's live episode is guaranteed open before ITS 202 is written, not before
        // some local state flip that might race the network round-trip itself, so the
        // attach effect (above) must never start until the 202 is actually in hand. When
        // `busy`, a turn is already streaming into this slice — nothing here needs to
        // flip status or open a watcher; the running turn's OWN attach effect delivers
        // whatever the server does with this message (a `message_delivered` event, or the
        // text itself once Lucius reaches it).
        const res = await authFetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: ownerId,
            messages: [{ role: 'user', content: text }],
            attachments: attachments.map((a) => a.ref_id),
            effort,
            model,
            ...(mentions.length ? { mentions } : {}),
            // null/absent → default Lucius path (server ignores missing or null). Never
            // send a stray non-null value when no agent was selected.
            responding_agent: respondingAgent ?? null,
            // Structured skill ids are deterministic host invocations, ordered like Codex's
            // UserInput::Skill items. Empty → the ordinary text-detection/model path.
            ...(skills.length ? { skills } : {}),
            // Screen-aware context: the structured snapshot of what the user is viewing
            // (when the feature is on + a screen is registered). Absent → default path,
            // server ignores it.
            ...(screen ? { screen } : {}),
            // Re-running a failed turn: the server replaces its failed attempt at this
            // message instead of storing a second copy beside it.
            ...(retry ? { retry: true } : {}),
            // Echoed back verbatim on `message_delivered` so the reducer can clear THIS
            // message's pending chip (never the server's internal fence nonce).
            client_message_id: messageId,
          }),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const ack = (await res.json()) as { accepted?: string }
        // `accepted` decides where this message lives, and ignoring it stranded messages in
        // the tray forever. The client's `busy` is its own belief; the SERVER decides. It
        // answers "queued" only when a turn really was running to inject into — otherwise
        // it started a FRESH turn ("started"), and a fresh turn never emits
        // `message_delivered`, so nothing would ever move the message out of the tray.
        // That is the common case for a send that races the end of a turn: the client still
        // thinks it is busy, the server knows it is not.
        if (ack?.accepted === 'started') {
          // The server started a FRESH turn rather than injecting — it never emits
          // `message_delivered` for that path, so nothing else would ever move this
          // message out of the queue. It is being processed RIGHT NOW; that is its
          // correct position in the true timeline. This is the send that raced the end
          // of a turn: the client still believed it was busy, the server knew it was
          // not — so only deliver if it was actually queued (`busy`) in the first
          // place; the not-busy path already went straight into `messages` above.
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            status: 'streaming',
            sending: false,
            startedHere: true,
            attachGen: s.attachGen + 1,
            startedAt: s.startedAt ?? Date.now(),
          }))
          if (busy) deliverQueuedMessage(ownerId, messageId)
        } else if (!busy) {
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            status: 'streaming',
            sending: false,
            startedHere: true,
            attachGen: s.attachGen + 1,
            startedAt: s.startedAt ?? Date.now(),
          }))
        } else if (!wasAlreadyStreaming) {
          // No confirmed-live attach yet (busy only via `sending`) — force one.
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            sending: false,
            attachGen: s.attachGen + 1,
            startedAt: s.startedAt ?? Date.now(),
          }))
        } else {
          // Already streaming — an attach is already live for it, no reconnect needed.
          chatSessions.apply(ownerId, (s) => (s.sending ? { ...s, sending: false } : s))
        }
      } catch (err) {
        chatSessions.apply(ownerId, (s) => ({ ...s, sending: false, startedAt: undefined }))
        if (busy) {
          // The message never reached the server — leave its pending chip up rather than
          // clear it as if delivered. The RUNNING turn (opened by someone else's send) is
          // untouched; only its OWN bubble/status would need error handling, and this send
          // never had one.
          return
        }
        if (err instanceof TypeError) {
          // A network failure is not "the server never got it": a phone's "Load failed"
          // can land after the server took the message and started the turn. Attach and
          // look — the turn streams in if it ran; settleUnconfirmedSend paints the failure
          // only if the message is still unanswered once the attach and re-read are done.
          chatSessions.apply(ownerId, (s) => ({
            ...s,
            status: 'streaming',
            startedHere: true,
            unconfirmedSend: { error: String(err) },
            attachGen: s.attachGen + 1,
            startedAt: Date.now(),
          }))
          return
        }
        paintSendFailure(ownerId, String(err))
      }
    },
    [conversationId, multiTurnEnabled],
  )

  // Resolve a paused (awaiting-approval) tool_use — RFC 2026-07-22. Approve/deny happens
  // SERVER-SIDE against the SAME tool_use_id the card is bound to (no confirm_token, no
  // model re-call); this posts the decision and the attach effect (above) picks up the
  // continuation via GET /live, exactly like an ordinary turn. Server-owned turn lifetime
  // (Phase 3) made this REQUIRED, not cosmetic: left request-driven, "approve, then close
  // the tab" would lose the continuation — the part that actually applies the approved
  // action's follow-through.
  const resolvePendingToolUse = useCallback(
    async (toolUseId: string, decision: 'approve' | 'deny', options?: { always?: boolean }) => {
      // You can only approve a card you are looking at — captured once, same as sendMessage.
      const ownerId = conversationId
      // Re-enter the streaming state on the SAME assistant message the card lives on. The
      // paused turn's `turn_done` closed the open round (see chatReducer.closeOpenAssistant),
      // so without this the resumed stream would find no pointer, open a BRAND NEW bubble,
      // and land the continuation there instead — a bubble still rendered as finished (no
      // cursor, no thinking block, copy/cost footer showing) sitting right above an orphan
      // second one. A pending card only ever lives on the LAST assistant message (the
      // architectural guarantee rehydrate_messages also relies on), so that scan is safe
      // specifically here. Mark only this card answered; `awaitingApproval` stays true while
      // a sibling card is unresolved, keeping the cost chip withheld until the whole
      // provider result group is complete. A stale `error` from the paused segment goes
      // too, since resolution is live. Optimistic — done before the request, unlike the
      // slice's `status` below (see sendMessage for why that one specifically must wait
      // for the 202).
      const cardMessage = [...chatSessions.getSlice(ownerId).messages]
        .reverse()
        .find((m) => m.role === 'assistant')
      if (cardMessage) openAssistant(ownerId, cardMessage.id)
      updateLastAssistant(ownerId, (m) => {
        const status: ProposedChange['status'] = decision === 'approve' ? 'confirmed' : 'dismissed'
        const resolution: ProposedChange['resolution'] =
          decision === 'deny' ? 'denied' : options?.always ? 'always_allowed' : 'approved'
        // `refused: undefined` clears any previous refusal, so a re-tap of a card the
        // server refused once paints its tick again (and can be un-painted again if it
        // refuses again).
        const answer = (c: ProposedChange): ProposedChange =>
          c.id === toolUseId
            ? {
                ...c,
                status,
                resolution,
                refused: undefined,
              }
            : c
        const proposedChanges = m.proposedChanges?.map(answer)
        const blocks = m.blocks?.map((block) =>
          block.type === 'change' && block.change.id === toolUseId
            ? { ...block, change: answer(block.change) }
            : block,
        )
        const stillPending = (proposedChanges || []).some((change) => !change.status)
        return {
          ...m,
          proposedChanges,
          blocks,
          streaming: true,
          awaitingApproval: stillPending,
          error: undefined,
        }
      })
      try {
        const res = await authFetch(`/api/chat/${ownerId}/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tool_use_id: toolUseId,
            decision,
            ...(options?.always ? { always: true } : {}),
          }),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        await res.json() // {session_id, turn_id, accepted} — the attach effect takes it from here
        chatSessions.apply(ownerId, (s) => ({
          ...s,
          status: 'streaming',
          startedHere: true,
          attachGen: s.attachGen + 1,
          // Tells the attach effect's own consumeEventStream call that ITS connection is a
          // deliberate resume, not a possible replay — see ConvSlice.resumeInFlight.
          resumeInFlight: true,
          startedAt: Date.now(),
        }))
      } catch (err) {
        // The decision never reached the server (network drop, server restarting, non-2xx),
        // so nothing ran and the call is still pending there. Un-paint the optimistic tick
        // exactly like a refused approval (chatReducer's `error` branch) so the card offers
        // Approve/Deny again instead of asserting "✓ Approved" above the error.
        const unpaint = (c: ProposedChange): ProposedChange =>
          c.id === toolUseId ? { ...c, status: undefined, refused: true } : c
        const message =
          err instanceof TypeError
            ? "Couldn't reach the server — nothing was approved or denied. Try again."
            : `Couldn't send your decision (${err instanceof Error ? err.message : String(err)}) — nothing ran. Try again.`
        updateLastAssistant(ownerId, (m) => ({
          ...m,
          error: message,
          streaming: false,
          awaitingApproval: true,
          proposedChanges: m.proposedChanges?.map(unpaint),
          blocks: m.blocks?.map((block) =>
            block.type === 'change' && block.change.id === toolUseId
              ? { ...block, change: unpaint(block.change) }
              : block,
          ),
        }))
        closeOpenAssistant(ownerId) // the resume failed outright — no continuation is coming
        chatSessions.apply(ownerId, (s) => ({
          ...s,
          status: 'error',
          sending: false,
          lastResult: 'error',
          startedAt: undefined,
        }))
      }
    },
    [conversationId],
  )

  // Persist the user's response (Confirm / Dismiss) to a proposed-change card so it
  // survives a re-render from storage. ChangePreview keeps only optimistic local state;
  // writing `status` onto the block is what makes the response durable and stops a
  // reloaded card being tappable again. Scoped to the VIEWED conversation — you can only
  // click a card you can see.
  const respondToChange = useCallback(
    (messageId: string, changeId: string | undefined, status: 'confirmed' | 'dismissed') => {
      const matches = (c: ProposedChange) => (changeId ? c.id === changeId : true)
      setMessages(conversationId, (prev) =>
        prev.map((m) => {
          if (m.id !== messageId) return m
          const blocks = m.blocks?.map((b) =>
            b.type === 'change' && matches(b.change)
              ? { ...b, change: { ...b.change, status } }
              : b,
          )
          // Every card on this message has now been answered, so the turn is no longer
          // waiting on the user — release the withheld cost chip. This is the path that
          // covers a card resolved WITHOUT the resolve endpoint (a legacy propose_* flow
          // confirms by sending a chat message, which opens a new bubble and would
          // otherwise leave this one flagged awaiting-approval, and chip-less, forever).
          const stillPending = (blocks || []).some((b) => b.type === 'change' && !b.change.status)
          return {
            ...m,
            blocks,
            awaitingApproval: m.awaitingApproval && stillPending,
            proposedChanges: m.proposedChanges?.map((c) => (matches(c) ? { ...c, status } : c)),
          }
        }),
      )
    },
    [conversationId],
  )

  // Stop scoped to the VIEWED conversation — calls POST /api/chat/{id}/stop rather than
  // aborting a fetch (server-owned turn lifetime, Phase 3): the fetch this browser holds
  // is now only the GET /live attach, and aborting a WATCHER must never stop the turn it
  // was watching — that is the whole point of a turn surviving the client (D1). No
  // turn_id in the body: the endpoint's default is "the turn currently executing on this
  // session", which is exactly what a Stop button scoped to the viewed conversation means.
  // A Stop button must never kill a turn you cannot see (acceptance B5) — captured once,
  // same as sendMessage, deliberately a separate call from it (the two are no longer
  // coupled behind one composer button). Fire-and-forget: the attach effect's own
  // finally clears `streaming` the instant the server delivers the resulting
  // CancelledError, so there is nothing further for this call to await.
  const stopStreaming = useCallback(() => {
    void authFetch(`/api/chat/${conversationId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
  }, [conversationId])

  const retryLast = useCallback(() => {
    const slice = chatSessions.getSlice(conversationId)
    if (!slice.lastSent || slice.status === 'streaming') return
    // Drop the failed assistant turn (the "Sorry…" bubble), keeping the user message that
    // opened it.
    setMessages(conversationId, (prev) => {
      const idx = [...prev].reverse().findIndex((m) => m.role === 'assistant')
      if (idx === -1) return prev
      return prev.slice(0, prev.length - 1 - idx)
    })
    // Re-run the SAME turn in place: silent=true so we don't append a duplicate user
    // bubble (the original one is still on screen) and preserve the full original context
    // (mentions/agent/skills/screen).
    const { text, attachments, effort, model, mentions, respondingAgent, skills, screen } =
      slice.lastSent
    sendMessage(
      text,
      attachments,
      effort,
      true,
      model,
      mentions,
      respondingAgent,
      skills,
      screen,
      true,
    )
  }, [conversationId, sendMessage])

  // Fetch the impact preview for a checkpoint (what restoring would change). Returns
  // { gone: true } if the checkpoint no longer exists (aged out / removed).
  const previewCheckpoint = useCallback(async (checkpointId: string) => {
    const res = await authFetch(`/api/checkpoints/${checkpointId}/preview`, { method: 'POST' })
    if (res.status === 404) return { gone: true as const }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }, [])

  // Restore data (and optionally the conversation) to a checkpoint — scoped to the VIEWED
  // conversation, same reasoning as respondToChange.
  const restoreCheckpoint = useCallback(
    async (checkpointId: string, mode: 'data' | 'conversation' | 'both' = 'data') => {
      const res = await authFetch(`/api/checkpoints/${checkpointId}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
      if (!res.ok) {
        if (res.status === 404) throw new Error('CHECKPOINT_GONE')
        const msg =
          res.status === 409
            ? 'A response is still in progress — try again in a moment.'
            : `Restore failed (HTTP ${res.status})`
        throw new Error(msg)
      }
      const result = await res.json()
      if (mode === 'both' || mode === 'conversation') {
        // Rewind the visible chat: drop the anchor user message and everything after.
        setMessages(conversationId, (prev) => {
          const idx = prev.findIndex((m) => m.checkpointId === checkpointId)
          return idx === -1 ? prev : prev.slice(0, idx)
        })
      } else {
        // Data-only: keep the chat. Mark the restored message (with its one-click Undo) and
        // the later turns it rewound past; an Undo reverses both — see applyRestoreResult.
        setMessages(conversationId, (prev) => applyRestoreResult(prev, checkpointId, result))
      }
      // Refresh all data-backed views.
      emitDataChanged(['*'])
      return result
    },
    [conversationId],
  )

  return {
    messages: slice.messages,
    /** Sent while busy, not yet spliced into the true timeline — its own list, never a
     *  filter over `messages`. See ConvSlice.queuedMessages. */
    queuedMessages: slice.queuedMessages,
    hydrated,
    /** A silent transcript re-read is in flight — see ConvSlice.catchingUp. */
    catchingUp: slice.catchingUp,
    streaming,
    /** OFF ("one turn only", default) means Send is disabled while `streaming || sending`
     *  — see ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md. */
    multiTurnEnabled,
    /** A send whose 202 has not landed. Drives the composer's Stop the INSTANT you send. */
    sending: slice.sending,
    /** Server-reported output tokens on the running turn. */
    liveOutputTokens: slice.liveOutputTokens,
    /** Non-null only during the pre-turn context-fold phase. See ConvSlice.compacting. */
    compacting: slice.compacting,
    // True while this panel is FOLLOWING a turn someone else started (an Automation run).
    // Deliberately separate from `streaming`: that flag also owns the composer and the
    // Stop button, neither of which apply to a run this browser cannot cancel. It exists
    // so the panel can show the same working indicator instead of looking idle while text
    // lands.
    following,
    sendMessage,
    resolvePendingToolUse,
    stopStreaming,
    retryLast,
    // Retry replays the last message THIS browser sent, so there has to be one. An
    // Automation conversation never has one — the scheduler opened the turn — and now that
    // a failed turn renders its error on reload, the Retry button beside it would render
    // and silently do nothing (`retryLast` returns early on a missing `lastSent`).
    canRetry: Boolean(slice.lastSent),
    respondToChange,
    previewCheckpoint,
    restoreCheckpoint,
    suggestions: slice.suggestions,
    suggestionsLoading: slice.suggestionsLoading,
    fetchSuggestions,
    startedAt: slice.startedAt,
  }
}
