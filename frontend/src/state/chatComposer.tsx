// Chat-composer store — a tiny React context so any widget (Overview hero, a HoldingsTable
// row, a Planning envelope, a Cash txn row) can push a MentionRef into the chat composer even
// though the widgets and ChatPanel live in far-apart subtrees of App.tsx. Replaces the per-tab
// `onAskLucius` prop-drill for ENTITY mentions; the legacy plain-string prefill path is kept
// intact for free-text suggestions (briefing / skills) — mentions are additive.
//
// No new dependency: a ~React context with useState for the staged chips + a ref-backed
// subscriber for the "open the panel" bridge. `addMention` dedups by (type, ref).

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from 'react'
import type { ReactNode } from 'react'
import type { MentionRef } from '../types/mentions'
import { takeReauthDraft } from '../utils/gatewayReauth'

export interface RespondingAgent {
  value: string
  label: string
}

export interface PendingSkill {
  value: string
  label: string
}

/** Shared composer draft — text + staged mention chips. Both dock and side panel surfaces
 *  read this on mount/open and write it back on close/unmount so the full draft (text AND
 *  chips) is preserved symmetrically across dock↔side transitions. */
export interface ComposerDraft {
  text: string
  mentions: MentionRef[]
}

const EMPTY_DRAFT: ComposerDraft = { text: '', mentions: [] }

interface AddMentionOpts {
  /** Whether to fire the subscribeOpen signal after staging the mention.
   *  Default true — preserves existing callers (row ✦ buttons, side-panel drop).
   *  Pass false when the caller wants to open a surface other than the side panel
   *  (e.g. the dock drop handler dispatches 'lx-dock-open' instead). */
  open?: boolean
  fresh?: boolean
}

interface ChatComposerValue {
  /** One-shot text prefill (a banner's "Remind me", a suggested prompt) — drained by the
   *  ACTIVE composer exactly like pendingMentions, so it lands in whichever surface is
   *  visible (dock or side panel), mounted or not. */
  pendingText: string | null
  /** With ``keepChips``, the prefill is added after any staged mention chips instead of
   *  replacing the editor — for a caller that stages a chip AND a request in one tap. */
  setPendingText: (text: string, opts?: { keepChips?: boolean }) => void
  pendingTextKeepsChips: boolean
  clearPendingText: () => void

  pendingMentions: MentionRef[]
  addMention: (m: MentionRef, opts?: AddMentionOpts) => void
  removeMention: (idx: number) => void
  /** Empty the staged-mention inbox WITHOUT touching the responder/skill (used when the composer
   *  drains the inbox into its own DOM). `clearMentions` resets everything; this is mentions-only. */
  clearPendingMentions: () => void
  clearMentions: () => void
  respondingAgent: RespondingAgent | null
  setRespondingAgent: (a: RespondingAgent | null) => void
  /** Ordered `/` verb skills selected for this turn. Empty = default Lucius path. */
  pendingSkills: PendingSkill[]
  setPendingSkills: (skills: PendingSkill[]) => void
  // Bridge to open the panel + focus the composer (App.tsx subscribes). `fresh=true` asks the
  // panel to start a NEW conversation first (used by "Ask Lucius about <asset>" so the asset
  // question never appends to an unrelated existing thread).
  requestOpen: (fresh?: boolean) => void
  subscribeOpen: (fn: (fresh: boolean) => void) => () => void
  /** Shared composer draft (text + mention chips). Both surfaces read on mount/open and write
   *  on close/unmount so dock↔side transitions carry the full draft symmetrically. */
  composerDraft: ComposerDraft
  /** Synchronous mirror of composerDraft. The seed sites read THIS (not the state) inside their
   *  RAF so they pick up the OTHER surface's save even when both run in the same commit — the
   *  state value is still the pre-save render snapshot, which caused the dock to seed a stale
   *  draft (cleared side panel reappeared in the dock). */
  composerDraftRef: RefObject<ComposerDraft>
  setComposerDraft: (draft: ComposerDraft) => void
  clearComposerDraft: () => void
}

const ChatComposerContext = createContext<ChatComposerValue | null>(null)

/** The draft saved before a gateway re-sign-in, read once when the provider is first created. */
let restoredComposerDraft: ComposerDraft | undefined
function restoredDraft(): ComposerDraft {
  // Memoised: React may run a lazy initialiser twice, and the saved draft is handed out once.
  restoredComposerDraft ??= takeReauthDraft<ComposerDraft>('composer') ?? EMPTY_DRAFT
  return restoredComposerDraft
}

export function ChatComposerProvider({ children }: { children: ReactNode }) {
  const [pendingMentions, setPendingMentions] = useState<MentionRef[]>([])
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [pendingTextKeepsChips, setPendingTextKeepsChips] = useState(false)
  const [respondingAgent, setRespondingAgent] = useState<RespondingAgent | null>(null)
  const [pendingSkills, setPendingSkills] = useState<PendingSkill[]>([])
  const [composerDraft, setComposerDraftState] = useState<ComposerDraft>(restoredDraft)
  // Synchronous mirror so a seed reading it in a RAF always sees the latest write (the state
  // snapshot lags one render behind a same-commit setComposerDraft).
  const composerDraftRef = useRef<ComposerDraft>(composerDraft)
  // Subscribers to the "open the chat panel" signal (App.tsx). A Set so multiple listeners are
  // tolerated and unsubscribe is cheap; held in a ref so subscribing never re-renders consumers.
  const openSubs = useRef<Set<(fresh: boolean) => void>>(new Set())

  const requestOpen = useCallback((fresh = false) => {
    openSubs.current.forEach((fn) => fn(fresh))
  }, [])

  const subscribeOpen = useCallback((fn: (fresh: boolean) => void) => {
    openSubs.current.add(fn)
    return () => {
      openSubs.current.delete(fn)
    }
  }, [])

  const addMention = useCallback(
    (m: MentionRef, opts?: AddMentionOpts) => {
      setPendingMentions((prev) =>
        prev.some((x) => x.type === m.type && x.ref === m.ref) ? prev : [...prev, m],
      )
      // Slide the panel out + focus the composer, unless the caller explicitly opts out
      // (e.g. dock drop dispatches 'lx-dock-open' to open the dock instead). Done after the
      // state update so the chip is present when the panel reads pendingMentions.
      if (opts?.open !== false) {
        requestOpen(opts?.fresh ?? false)
      }
    },
    [requestOpen],
  )

  const removeMention = useCallback((idx: number) => {
    setPendingMentions((prev) => prev.filter((_, i) => i !== idx))
  }, [])

  const clearPendingMentions = useCallback(() => {
    setPendingMentions((prev) => (prev.length ? [] : prev))
  }, [])

  const clearMentions = useCallback(() => {
    setPendingMentions((prev) => (prev.length ? [] : prev))
    setRespondingAgent(null)
    setPendingSkills([])
  }, [])

  const setPendingTextCb = useCallback((text: string, opts?: { keepChips?: boolean }) => {
    setPendingTextKeepsChips(Boolean(opts?.keepChips))
    setPendingText(text)
  }, [])
  const clearPendingText = useCallback(() => {
    setPendingText(null)
    setPendingTextKeepsChips(false)
  }, [])

  const setComposerDraft = useCallback((draft: ComposerDraft) => {
    composerDraftRef.current = draft
    setComposerDraftState(draft)
  }, [])

  const clearComposerDraft = useCallback(() => {
    composerDraftRef.current = EMPTY_DRAFT
    setComposerDraftState(EMPTY_DRAFT)
  }, [])

  const value = useMemo<ChatComposerValue>(
    () => ({
      pendingMentions,
      addMention,
      removeMention,
      clearPendingMentions,
      clearMentions,
      respondingAgent,
      setRespondingAgent,
      pendingSkills,
      setPendingSkills,
      requestOpen,
      subscribeOpen,
      composerDraft,
      composerDraftRef,
      setComposerDraft,
      clearComposerDraft,
      pendingText,
      setPendingText: setPendingTextCb,
      pendingTextKeepsChips,
      clearPendingText,
    }),
    [
      pendingMentions,
      addMention,
      removeMention,
      clearPendingMentions,
      clearMentions,
      respondingAgent,
      pendingSkills,
      requestOpen,
      subscribeOpen,
      composerDraft,
      setComposerDraft,
      clearComposerDraft,
      pendingText,
      pendingTextKeepsChips,
      setPendingTextCb,
      clearPendingText,
    ],
  )

  return <ChatComposerContext.Provider value={value}>{children}</ChatComposerContext.Provider>
}

export function useChatComposer(): ChatComposerValue {
  const ctx = useContext(ChatComposerContext)
  if (!ctx) throw new Error('useChatComposer must be used within <ChatComposerProvider>')
  return ctx
}

/** Non-throwing variant for surfaces that merely OFFER a chat affordance (a "Remind me"
 *  button on a banner) and should render fine outside the provider (tests, embeds). */
export function useChatComposerOptional(): ChatComposerValue | null {
  return useContext(ChatComposerContext)
}

// Convenience: the affordance hook. A widget that manages its own layout can call this to get a
// stable mention dispatcher without wrapping with <AskLucius>.
export function useLuciusMention() {
  const { addMention } = useChatComposer()
  return useCallback((m: MentionRef, opts?: AddMentionOpts) => addMention(m, opts), [addMention])
}
