import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useLuciusChat } from '../../state/luciusChat'
import { useChatSuggestionsEnabled } from '../../hooks/useChatSuggestionsEnabled'
import { useIsCoarsePointer } from '../../hooks/useIsCoarsePointer'
import { useIsMobile } from '../../hooks/useIsMobile'
import {
  useModels,
  effortChoicesFor,
  clampEffortChoice,
  effortChoiceLabel,
  noVisionLabel,
  type EffortChoice,
} from '../../hooks/useModels'
import { useChatComposer } from '../../state/chatComposer'
import { useReauthDraftSource } from '../../state/useReauthDraftSource'
import { followsBottom, newScrollIntent, useChatScrollPin } from '../../utils/chatScroll'
import { fUsd, sumSessionCost } from '../../utils/costFormat'
import { fDateTime } from '../../utils/format'
import { ConversationBrowser } from './ConversationBrowser'
import { ConversationMenu, shortWhen } from './ConversationMenu'
import {
  TRANSCRIPT_WINDOW_INITIAL,
  TRANSCRIPT_WINDOW_STEP,
  windowedTranscript,
} from './transcriptWindow'
import type { Message } from '../../types/chat'
import { MessageBubble } from './MessageBubble'
import { CompactingActivity } from './CompactingActivity'
import { TurnActivity, turnProgress } from './TurnActivity'
import { CatchingUp, ChatLoading } from './ChatLoading'
import { LuciusComposer } from './lucius/Composer'
import { EffortChoices } from './lucius/ModelEffortRow'
import type { LuciusComposerHandle, ComposerParsed } from './lucius/Composer'
import { chatSessions } from '../../state/chatSessions'
import { loadEarlierMessages, restoreSuggestions } from '../../state/chatReducer'
import { positionalCounter } from '../../utils/chatStore'
import { authFetch } from '../../api/client'
import { useVisibleIdsValue } from '../../lucius/visibleIds'
import { captureAndSerialize } from '../../lucius/screenContract'
import { commandCatalogSnapshot } from '../../lucius/screenCommands'
import { useScreenSnapshot } from '../../state/screenSnapshot'
import { Spark } from '../../lucius/identity'
import { LuciusHomeStart } from '../intro/LuciusHomeStart'
import { dataChangeTurns } from './dataChangeTurns'
import { takeSendIntent } from '../../state/luciusSendIntent'
import { useNavigate } from '@tanstack/react-router'
import type { ConversationMeta } from '../../hooks/useConversations'
import { ChevronRight, Eraser, FolderLock, Menu, SquarePen } from '../../ui/icons'
import { ModelPicker, Skeleton } from '../ui'
import { ConversationApprovalPicker } from './ConversationApprovalPicker'
import { computeHomeEmpty } from './homeEmpty'
import { useProfile } from '../../hooks/serverState'
import { ActivityPanel } from '../tabs/lucius/ActivityPanel'
import { useTransactionSurfaces } from '../tabs/accounts/transactionSurfacesContext'
import { isOnboardingInit, ONBOARDING_INIT_SENTINEL } from '../../utils/onboardingSentinel'
import { applyScrollbarGutter } from '../../utils/scrollbarGutter'
import { composerDensity } from './composerDensity'
import { usePointerResize } from '../../hooks/usePointerResize'

// Documents from the side panel on any tab — loaded only when opened, so the chat that every
// tab carries doesn't carry the document viewer too.
const DocumentsSheet = lazy(() =>
  import('../vault/DocumentsSheet').then((m) => ({ default: m.DocumentsSheet })),
)

/** The kickoff message that makes Lucius speak first on a brand-new instance. Harness-authored:
 *  never rendered, never used to name a conversation. Mirrors `ai.tool_text.ONBOARDING_INIT`. */

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  prefill?: string
  onPrefillConsumed?: () => void
  onboarding?: boolean
  onOnboardingDone?: () => void
  /** Several panels can be mounted at once (Lucius tab, dock, mobile): only the one that claims
   *  the trigger sends it. Returns false when another panel already did. */
  claimOnboarding?: () => boolean
  /** Force screen-awareness off for this instance (hides the toggle, never captures)
   *  regardless of the user's global preference — the mobile Lucius tab passes this,
   *  since it replaces the screen rather than overlaying a still-visible one. */
  screenAwarenessDisabled?: boolean
  /** Skip the desktop resizable-panel inline width (which floors at MIN_WIDTH=300px —
   *  wider than half of most phone screens) so `.app-layout--lucius .lx-side`'s own
   *  `width: 100%` rule can size the panel instead. The mobile Lucius tab passes this;
   *  it renders as an ordinary full-page tab, not a resizable side panel. */
  fillWidth?: boolean
  /** Whether opening the panel should put the cursor straight into the composer.
   *  TRUE on desktop, where focus costs nothing. FALSE for the phone's Lucius TAB: there
   *  focus raises the software keyboard, so autofocusing meant merely *navigating* to
   *  Lucius covered half the screen and took the bottom navigation with it — which is
   *  what the owner reported as "when lucius tap open, the nav bar is disappearing"
   *  (2026-07-30). A tab you opened to read should not seize the keyboard; the user taps
   *  the composer when they actually want to type. */
  autoFocusComposer?: boolean
  /** Renders as the Lucius home tab's main pane instead of the floating dock/mobile panel:
   *  hides the dock-only header (burger/new-conversation/collapse — this tab's own sidebar
   *  and browser tab chrome already cover those), skips the dock's --chat-width CSS-var
   *  side effect (meaningless for an in-flow tab, and would fight the floating dock's own
   *  value if both are mounted at once), and swaps the empty-state copy for a personalized
   *  greeting. Every other `ChatPanel` consumer omits this and is unaffected. */
  homeMode?: boolean
  /** Whether this instance should react to the app-wide "open the chat" signal
   *  (state/chatComposer's subscribeOpen/requestOpen — fired by @-mention widgets,
   *  the command palette's "Ask Lucius", etc.). Default true. Only ever false for
   *  the floating dock's OWN instance while it is structurally unable to become
   *  visible (currently: while the Lucius home tab's own always-open instance is
   *  the one that should handle it instead) — without this, BOTH instances react
   *  to one request: two conversations get created and the visible instance never
   *  receives the composer focus/prefill this signal exists to deliver. */
  respondToGlobalOpen?: boolean
  /** Skip the landing screen's Activity panel (Active/Scheduled automation runs) — the phone's
   *  Lucius tab passes this to keep its empty state exactly the plain centered greeting it had
   *  before the panel existed (owner: "on mobile, let's not show these at all, let's just show
   *  the greeting message as in the past"). `fillWidth`/`screenAwarenessDisabled` are shared by
   *  BOTH `LuciusHome` and `LuciusMobile`, so neither can stand in for "is this mobile" —
   *  this is its own prop for exactly that one distinction. Desktop's `LuciusHome` omits it. */
  hideActivityPanel?: boolean
  /** Dismiss the software keyboard after a valid send. Mobile opts in; desktop keeps focus. */
  dismissKeyboardOnSend?: boolean
}

// detectAtToken / detectSlashToken now live in utils/mentionTrigger (the contenteditable composer
// detects triggers off the live caret using the same scanners). Re-exported here so the existing
// unit tests — which import them from this module — keep resolving.
export { detectAtToken, detectSlashToken } from '../../utils/mentionTrigger'

const MIN_WIDTH = 300
const DEFAULT_WIDTH = 400
// Cap the panel at half the viewport so it never covers more than 50% of the app
// (the MIN_WIDTH floor still wins on narrow/mobile screens).
function maxWidth() {
  return Math.max(MIN_WIDTH, Math.floor(window.innerWidth / 2))
}
function clampWidth(w: number) {
  return Math.max(MIN_WIDTH, Math.min(maxWidth(), w))
}

// The width preference is persisted as a fraction of the viewport (not pixels)
// so resizing the browser scales the panel proportionally with the rest of the
// app. The old pixel key is read once as a migration fallback.
const WIDTH_FRAC_KEY = 'chat-panel-frac'
function savedWidthFrac(): number {
  const frac = parseFloat(localStorage.getItem(WIDTH_FRAC_KEY) ?? '')
  if (Number.isFinite(frac) && frac > 0 && frac <= 1) return frac
  const px = parseInt(localStorage.getItem('chat-panel-width') ?? '', 10)
  return clampWidth(Number.isFinite(px) ? px : DEFAULT_WIDTH) / window.innerWidth
}

/** Read the run header off a message, tolerating the shape an older build cached.
 *
 * It used to be the bare NAME. A conversation already in IndexedDB still holds that, and
 * the cache wins over the server copy unless the transcript grew — so reading `.name` off
 * it rendered a header saying "Ran " with nothing after it and no link. */
export function runBrief(
  value: Message['automationBrief'],
): { name: string; automationId?: string; runId?: string } | null {
  if (!value) return null
  if (typeof value === 'string') return { name: value }
  return value.name ? value : null
}

/** Split the switcher into chats and Automation runs.
 *
 * Every run of one Automation carries ITS name, so twenty of them interleaved with real
 * chats read as twenty duplicates of the same thread and the list becomes unusable for
 * both. Split, and each side gets a label that says what you are looking at.
 */
export function conversationGroups(conversations: ConversationMeta[]) {
  const runs = conversations.filter((c) => c.source === 'automation')
  // "Chats" means IN-APP conversations. Two different axes decide that, and conflating them
  // is what put the owner's Telegram thread in this sidebar (2026-08-21): `source` is an
  // ownership claim ('automation' — a RUNNER, and a security gate reads it), while `channel`
  // is the TRANSPORT the human is on. In-app is the absence of both — it needs no name of
  // its own, and naming it 'web' would be wrong the moment this runs in the desktop app.
  const chats = conversations.filter((c) => !c.source && !c.channel)
  return [
    {
      key: 'chat' as const,
      label: 'Chats',
      items: [...chats].sort(byRecency),
      total: chats.length,
    },
    {
      key: 'automation' as const,
      label: 'Automation runs',
      items: [...runs].sort(byRecency),
      total: runs.length,
    },
  ].filter((group) => group.items.length > 0)
}

/** Newest first, by when the conversation HAPPENED.
 *
 * Not by updated_at: every Automation session on this instance carries the same
 * updated_at to the millisecond (23:20:06.7…), because a maintenance pass touched them all
 * in one batch. Sorted by that they came out in an order with no meaning — the 7:10am run
 * sat below the 2:32am one. created_at is when the run actually started, which is what the
 * row shows and what the reader is looking for.
 */
function byRecency(a: ConversationMeta, b: ConversationMeta): number {
  return happenedAt(b) - happenedAt(a)
}

function happenedAt(conv: ConversationMeta): number {
  const created = Date.parse(conv.createdAt || '')
  const updated = Date.parse(conv.updatedAt || '')
  if (conv.source === 'automation') return Number.isNaN(created) ? updated || 0 : created
  return Math.max(Number.isNaN(updated) ? 0 : updated, Number.isNaN(created) ? 0 : created)
}

/** When it happened — ONE clock stamp, the same for every row.
 *
 * A run is stamped when it FIRED (created_at); a chat is stamped by its LAST MESSAGE
 * (updated_at, which only moves when the conversation actually grew — see
 * useConversations.updateConversationMeta).
 *
 * They render identically so the column reads as one column. "4m ago" above "Aug 5, 11:00 PM"
 * made two rows of the same list look like two different kinds of fact, and a relative stamp
 * is useless down a run of conversations from the same hour — there the clock IS the
 * identity. Relative time survives only as the fallback for an unparseable timestamp.
 */
export function conversationWhen(conv: ConversationMeta): string {
  const preferred =
    conv.source === 'automation'
      ? [conv.createdAt, conv.updatedAt]
      : [conv.updatedAt, conv.createdAt]
  for (const iso of preferred) {
    const at = new Date(iso || '')
    if (Number.isNaN(at.getTime())) continue
    return fDateTime(at)
  }
  // Neither timestamp parses. The row renders no stamp at all — every caller guards on the
  // empty string. Formatting the garbage anyway is how "Invalid Date" reaches a user.
  return ''
}

/** The nickname is how the user asked to be addressed, so it is used whole ("Mr. Smith"
 *  must not become "Mr."); only the full-name fallback is cut to its first word. */
export function greetingName(nickname?: string | null, name?: string | null): string {
  const nick = (nickname ?? '').trim()
  if (nick) return nick
  return (name ?? '').trim().split(/\s+/)[0] ?? ''
}

/** The Lucius home tab's empty-state greeting — six time-of-day buckets (not just
 *  morning/afternoon/evening) with a bit of personality on the less-common ones (owner
 *  reference: Claude.ai's own "It's a late-night jam session"), kept plain on the two buckets
 *  a typical workday actually sees most (morning/afternoon) so it doesn't wear thin on daily
 *  reload. `date` supplies both the hour (period) and the weekday (the two night buckets read
 *  differently on a weekend); `name` comes from `greetingName`, or undefined/empty before
 *  it's known — the greeting still reads fine with no name. */
export function homeGreeting(date: Date, name: string | undefined): string {
  const hour = date.getHours()
  const isWeekend = date.getDay() === 0 || date.getDay() === 6
  name = (name ?? '').trim()

  // "Still up?" takes the question mark AFTER the name, not after a bare period label like
  // every other bucket — handled as its own early return rather than forcing it through the
  // uniform "period, name" template below.
  if (hour < 5) return name ? `Still up, ${name}?` : 'Still up?'

  // "Winding down for the night" is already the longest phrase here on its own — appending a
  // name read as cramped/run-on (owner: keep the sentence+name combo from getting too long).
  // No name variant for this one bucket; every other bucket's phrase is short enough to carry
  // one.
  if (hour >= 21 && !isWeekend) return 'Winding down for the night'

  let period: string
  if (hour < 8) period = 'Rise and shine'
  else if (hour < 12) period = 'Good morning'
  else if (hour < 17) period = 'Good afternoon'
  else if (hour < 21) period = 'Good evening'
  else period = 'Happy weekend'

  return name ? `${period}, ${name}` : period
}

export function ChatPanel({
  open,
  onOpenChange,
  prefill,
  onPrefillConsumed,
  onboarding,
  claimOnboarding,
  onOnboardingDone,
  screenAwarenessDisabled = false,
  fillWidth = false,
  autoFocusComposer = true,
  homeMode = false,
  respondToGlobalOpen = true,
  hideActivityPanel = false,
  dismissKeyboardOnSend = false,
}: Props) {
  const setOpen = onOpenChange

  const composerRef = useRef<LuciusComposerHandle>(null)
  useReauthDraftSource(composerRef)
  const [widthFrac, setWidthFrac] = useState(savedWidthFrac)
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  const width = clampWidth(Math.round(widthFrac * viewportWidth))
  // What the toolbar cluster (cost · model · effort) has room for — see composerDensity.ts
  // for the measured thresholds. `width` is the desktop resizable panel's clamped pixel
  // width, which floors at MIN_WIDTH (300) on any viewport under ~600px — so it read
  // "narrow" on EVERY phone, even though `fillWidth` mode (LuciusMobile) renders the
  // composer at the full, much wider viewport. Measure against the composer's actual
  // rendered width in that mode instead.
  const density = composerDensity(fillWidth ? viewportWidth : width)
  // Distinct from `fillWidth`: LuciusHome (the desktop home tab) also passes `fillWidth`, so
  // it alone can't tell "phone" from "wide desktop tab". Used to trim the toolbar cluster to
  // just the model name on a real phone screen — no session-cost figure, no effort trailing
  // label (owner: phone has less room and less reason to see either at a glance; both stay
  // ONE tap away inside the model picker's own panel).
  const isMobile = useIsMobile()
  const isCoarsePointer = useIsCoarsePointer()
  // A side panel is primarily opened to read. On iPad, programmatic composer focus summons the
  // software keyboard over that content, so reserve focus for an explicit tap. Fine-pointer
  // desktops keep their keyboard-first shortcut flow.
  const shouldAutoFocusComposer = autoFocusComposer && !isCoarsePointer

  const { subscribeOpen, composerDraftRef, setComposerDraft, clearComposerDraft } =
    useChatComposer()

  const { visibleIds } = useVisibleIdsValue()
  const { enabled: screenEnabledGlobal, pinnedHandleIds } = useScreenSnapshot()
  const screenEnabled = !screenAwarenessDisabled && screenEnabledGlobal

  // A chip/quick send must carry the SAME live screen snapshot a typed send captures —
  // a screen-less turn clears the per-turn command/handle manifest by design, which
  // would make a follow-up run_screen_command see "available: none".
  // A surface that replaces the screen still sends the parked command catalog (no content).
  const captureScreen = () =>
    screenEnabled
      ? captureAndSerialize('lucius-side', visibleIds, pinnedHandleIds)
      : screenAwarenessDisabled
        ? commandCatalogSnapshot()
        : undefined

  // ── Shared chat thread (same instance as LuciusDock) ─────────────────────────
  const {
    conversations,
    activeId,
    conversationModel,
    effort,
    setEffort,
    createConversation,
    switchConversation,
    deleteConversation,
    renameConversation,
    setConversationModel,
    messages,
    queuedMessages,
    hydrated,
    catchingUp,
    streaming,
    multiTurnEnabled,
    following,
    sendMessage,
    resolvePendingToolUse,
    stopStreaming,
    sending,
    liveOutputTokens,
    compacting,
    startedAt,
    retryLast,
    canRetry,
    respondToChange,
    previewCheckpoint,
    restoreCheckpoint,
    suggestions,
    suggestionsLoading,
    statusFor,
    stopConversation,
  } = useLuciusChat()

  // Read-latest-without-resubscribing for the subscribeOpen effect below — conversations
  // changes on every streamed token, and closing over it directly would tear down/rebuild
  // that subscription that often instead of just reading its current value on each fresh-open.
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations
  const activeIdForFreshOpenRef = useRef(activeId)
  activeIdForFreshOpenRef.current = activeId

  // Only read for the greeting below (homeMode) — safe to call unconditionally, the shared
  // query cache dedupes this against every other useProfile() call site in the app.
  const profile = useProfile()

  // `messages` IS the true, ordered timeline now — a queued send lives in its own list
  // (chatSessions.ConvSlice.queuedMessages) until the server actually delivers it, so it
  // can never sit ahead of an answer that hasn't been generated yet. `stillQueued` renders
  // BELOW the activity line rather than in the thread proper — the line is the
  // read/unread boundary.
  // `__ONBOARDING_INIT__` is the harness sentinel that makes Lucius greet a brand-new
  // instance first. The user never typed it and must never see it. It is already filtered
  // server-side (ai/assistant.py's reader, and db/session_io's titler), and sendMessage's
  // `silent` flag keeps it out of the optimistic insert — but it still reached the thread
  // through the live stream and rendered as the owner's own bubble (owner, 2026-08-22).
  // Filtered HERE too, at the one seam every path funnels through.
  const delivered = messages.filter((m) => !(m.role === 'user' && isOnboardingInit(m.text || '')))
  const stillQueued = queuedMessages

  // Render only the transcript's tail. Mounting every markdown bubble in one synchronous
  // commit froze the tab switch for 5–10s on a phone once a conversation reached ~150
  // messages. Nothing is dropped — "show earlier" widens the window.
  const [transcriptWindow, setTranscriptWindow] = useState(TRANSCRIPT_WINDOW_INITIAL)
  const { visible: visibleDelivered, hiddenCount: hiddenTranscript } = windowedTranscript(
    delivered,
    transcriptWindow,
  )
  const changedTurns = dataChangeTurns(visibleDelivered)

  // If `activeId` is set but its row hasn't reconciled into `conversations` yet (the boot
  // cache is bounded — see useConversations.writeCachedList), fall back to `undefined`, NOT
  // `conversations[0]`: picking an unrelated conversation would show ITS title and, sharper
  // still, ITS model override in the picker below until the fetch lands. `conversations[0]`
  // is only the right fallback for the genuine "no conversation yet" case (`activeId` itself
  // empty).
  const activeConversation =
    conversations.find((c) => c.id === activeId) ?? (activeId ? undefined : conversations[0])

  // The sidebar, and the full list behind it. `browsingConvs` holds the filter it opened
  // with, so "View all runs" lands on runs rather than making you pick again.
  const [menuOpen, setMenuOpen] = useState(false)
  const [documentsOpen, setDocumentsOpen] = useState(false)
  const [browsingConvs, setBrowsingConvs] = useState<'chat' | 'automation' | null>(null)
  // One ordering for the header, the sidebar and the browser — they disagreed for a build,
  // and a switcher that orders its rows differently from the list behind it is worse than
  // either ordering on its own.
  const convGroups = useMemo(() => conversationGroups(conversations), [conversations])

  // Tapping the run header (inside a message bubble the user is already reading) sends the
  // MAIN content area to that run, leaving the panel on the conversation you are reading —
  // that conversation almost always already IS the run's own, so switching would be a no-op
  // at best. Same route the Automations tab drives itself with, so a deep link and a tap land
  // identically.
  //
  // The Activity panel's landing-screen rows (ActivityPanel.tsx) are a DIFFERENT caller with a
  // different starting point: there is no "conversation you are reading" yet (isHomeEmpty — an
  // empty/new draft), so leaving the panel alone left it on that empty draft instead of the run
  // just opened (owner report: tapping an Active row opened the automation's detail correctly
  // but the Lucius panel beside it showed a blank "New conversation", not the run being looked
  // at). `sessionId`, passed only by that caller, opts into ALSO switching the panel to the
  // run's own conversation — every other caller omits it and keeps the original behavior.
  const navigate = useNavigate()
  const openRun = useCallback(
    (brief: { automationId?: string; runId?: string; sessionId?: string }) => {
      if (!brief.automationId) return
      // Same guard as handleSwitchConversation (below) — inlined rather than called, so this
      // callback's own deps stay correct/complete instead of chasing that function's identity
      // (a plain, non-memoized declaration re-created every render).
      if (brief.sessionId && brief.sessionId !== activeId) switchConversation(brief.sessionId)
      // Navigating away from the Lucius tab's own full-page instance leaves this panel on
      // screen only as the docked side panel — request it open so Lucius stays visible
      // beside the automation instead of vanishing (owner report: tapping an automation from
      // the Lucius tab opened the automation but silently dropped the panel).
      setOpen(true)
      void navigate({
        to: '/automations',
        search: (() => ({ automation: brief.automationId, run: brief.runId })) as never,
      })
    },
    [navigate, activeId, switchConversation, setOpen],
  )
  // In place, over whatever is showing — the cards are hosted at the app level now, not on
  // the Accounts tab, so there is no tab to go to first.
  const { openReview } = useTransactionSurfaces()
  const openTransactionReview = useCallback(() => openReview('all'), [openReview])

  // Master switch (Settings → Appearance → Chat).
  const { enabled: suggestionsEnabled, loaded: suggestionsPrefLoaded } = useChatSuggestionsEnabled()

  // When the switch is off, hide the AI suggestion chips immediately.
  const visibleSuggestions = suggestionsEnabled ? suggestions : []
  const suggestionsBusy = suggestionsEnabled ? suggestionsLoading : false

  // Per-conversation model picker.
  const modelInfo = useModels(true)
  // Pending drafts have no `activeConversation` row by design; the provider owns the
  // reactive model selection separately so picker and first send still agree.
  const activeModel = conversationModel

  // Effort drives only the reasoning budget now — show exactly the tiers the
  // selected model accepts, hide the dial when none. Full ladder until models load
  // so it never flickers. On a model switch, clamp a now-unsupported selection to
  // the nearest tier (xhigh → high, etc.) and persist it so the send path agrees.
  const effortChoices = useMemo(
    () =>
      modelInfo.loaded
        ? effortChoicesFor(activeModel, modelInfo.models, modelInfo.defaultModel)
        : (['low', 'medium', 'high', 'xhigh', 'max'] as EffortChoice[]),
    [activeModel, modelInfo.loaded, modelInfo.models, modelInfo.defaultModel],
  )
  const showEffort = effortChoices.length > 0
  useEffect(() => {
    if (!showEffort) return
    const clamped = clampEffortChoice(effort, effortChoices)
    if (clamped !== effort) {
      setEffort(clamped)
    }
  }, [effort, effortChoices, setEffort, showEffort])

  // Running cost total for this conversation.
  const sessionCost = useMemo(() => sumSessionCost(messages), [messages])

  const dragging = useRef(false)
  const sideRef = useRef<HTMLElement>(null)
  const messagesAreaRef = useRef<HTMLDivElement>(null)
  const footRef = useRef<HTMLDivElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  // What the reader's own scroll gestures said about following the newest content, so a
  // deliberate step up is not undone while a reply streams (see followsBottom).
  const scrollIntentRef = useRef(newScrollIntent())

  // homeMode's empty state groups the greeting and the composer together as one visually
  // centered unit (owner reference screenshots) rather than the dock's usual layout — a
  // scrolling transcript with the composer pinned to the panel's bottom edge regardless of
  // how little content is above it.
  //
  // Deliberately NOT gated on `hydrated`/`messages.length` (the message-level fetch, which
  // can lag a beat after first paint) — that gate made this LAYOUT decision flip after the
  // fact: composer renders bottom-pinned on first paint (nothing hydrated yet), then SNAPS
  // to the centered position the instant hydration confirms zero messages (owner report: "on
  // refresh the composer moves from bottom to the middle"). `activeConversation.messageCount`
  // is metadata from the conversation LIST, already available from the localStorage boot
  // cache on the very first render — using it instead means the correct layout is there from
  // the first paint, no jump. A brand new conversation with no `activeConversation` entry yet
  // (or `undefined`, before the list itself loads) is also empty by construction.
  //
  // `hydrated`/`messages.length` DO feed `computeHomeEmpty` since 2026-09-06, but only in one
  // direction: a hydrated slice that HAS messages can veto the greeting, never summon it. The
  // flip described above (bottom → centered once hydration confirms zero) therefore cannot
  // come back; the only move now possible is centered → bottom-docked, and only when the
  // listing said 0 for a conversation that actually has messages — the stale-listing bug the
  // veto exists to fix (ADR 2026-09-06-the-transcript-in-hand-outranks-the-listing).
  //
  // Computed HERE (moved up from beside its own JSX usage further down) specifically so the
  // ResizeObserver effect below can key on it — see that effect's own comment for why.
  //
  // `!onboarding` too: the onboarding auto-trigger effect below fires deterministically the
  // moment this mounts, with no user action in between — so the centered empty state was
  // visible for one flash before immediately snapping to the bottom-docked layout as Lucius's
  // greeting started streaming in. A jump the user never caused, on the very first screen
  // they ever see. Since onboarding guarantees the conversation won't stay empty, skip
  // straight to the bottom-docked layout — nothing to transition FROM.
  const isHomeEmpty = computeHomeEmpty({
    homeMode,
    onboarding: onboarding === true,
    streaming,
    sending,
    hydrated,
    sliceMessageCount: messages.length,
    listedMessageCount: activeConversation ? activeConversation.messageCount : undefined,
  })

  // .lx-side-scroll's own bottom padding tracks the FLOATING footer's actual rendered
  // height (--lx-foot-h) — the footer grows/shrinks (the Screen Awareness bar, attachments,
  // multi-line drafts), and a fixed guess is always wrong for one of those states: too tall
  // for the short composer (a dead gap), too short for the tall one (the last line ends up
  // hidden behind the composer at rest, not just optionally scrolled behind it on purpose).
  //
  // Keyed on `isHomeEmpty`, not `[]`: `composerFooter` (below) renders at TWO different tree
  // positions depending on `isHomeEmpty` (inside `.lx-side-scroll-inner` on the empty landing
  // screen; a sibling of `.lx-side-scroll` everywhere else) — React can't reuse a DOM node
  // across a parent change, so `footRef.current` is a BRAND NEW element every time
  // `isHomeEmpty` flips. A `[]`-deps effect observed only the ORIGINAL node once, forever —
  // `--lx-foot-h` froze at whatever the empty-state composer measured the moment you first
  // opened any real conversation, and every conversation after that (this tab's ChatPanel
  // instance is never remounted, LuciusHome.tsx renders it with no `key`) inherited that same
  // stale value — matching the owner report exactly: "old conversations ... shown at the
  // bottom and hidden by the composer," happening "frequently and consistently." Re-running
  // this effect on every flip re-observes whichever node is CURRENT at that moment.
  //
  // The same observer also keeps `--lx-gutter` (the scrollbar track `.lx-side-scroll` and
  // `.lx-side-foot` actually reserve — see utils/scrollbarGutter.ts) current on the panel
  // root. Observing the SCROLL element for that: its content box shrinks the instant the OS
  // switches from overlay to classic scrollbars (a mouse plugged in, the "Show scroll bars"
  // setting changed), which is exactly the moment the padding has to re-derive.
  useLayoutEffect(() => {
    const footEl = footRef.current
    const scrollEl = messagesAreaRef.current
    const sideEl = sideRef.current
    if (!footEl || !scrollEl || !sideEl) return
    const measure = () => {
      scrollEl.style.setProperty('--lx-foot-h', `${footEl.offsetHeight}px`)
      // Also on the panel root, for what floats above the composer outside the scroll area.
      sideEl.style.setProperty('--lx-foot-h', `${footEl.offsetHeight}px`)
      applyScrollbarGutter(scrollEl, sideEl)
    }
    const ro = new ResizeObserver(measure)
    ro.observe(footEl)
    ro.observe(scrollEl)
    measure()
    return () => ro.disconnect()
  }, [isHomeEmpty])

  // Same trick for `streaming` — the fresh-conversation guard reads it without re-subscribing.
  const streamingRef = useRef(false)
  useEffect(() => {
    streamingRef.current = streaming
  }, [streaming])

  // Sync width to a CSS variable so the app-layout margin AND fixed overlays track it — only
  // meaningful for the floating dock. homeMode renders in-flow inside the Lucius tab's own
  // grid, so this would otherwise fight the dock's own value if both happen to be mounted.
  useEffect(() => {
    if (homeMode) return
    document.documentElement.style.setProperty('--chat-width', open ? width + 'px' : '0px')
  }, [width, open, homeMode])

  // Track the viewport width for proportional rescale.
  useEffect(() => {
    function onResize() {
      setViewportWidth(window.innerWidth)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Open the panel + focus the composer when a widget stages a mention.
  useEffect(() => {
    return subscribeOpen((fresh) => {
      if (!respondToGlobalOpen) return
      setOpen(true)
      if (fresh && !streamingRef.current) {
        const alreadyOnAnEmptyOne = !conversationsRef.current.some(
          (c) => c.id === activeIdForFreshOpenRef.current,
        )
        if (!alreadyOnAnEmptyOne) createConversation()
      }
      if (shouldAutoFocusComposer) {
        requestAnimationFrame(() => composerRef.current?.focus())
      }
    })
  }, [subscribeOpen, setOpen, createConversation, respondToGlobalOpen, shouldAutoFocusComposer])

  // homeMode: focus the composer whenever the active conversation changes — starting a new
  // chat or picking one from the sidebar should drop the cursor straight into the field, the
  // way opening the tab itself already does via autoFocusComposer. Also gated on
  // autoFocusComposer itself, not just homeMode: LuciusMobile.tsx reuses homeMode (for its
  // greeting/layout) but passes autoFocusComposer={false} for the exact historical reason that
  // prop exists — focus raises the phone's software keyboard, which the
  // `.app-shell.mb-shell:has(...:focus) .mb-tabbar { display:none }` rule (index.css) then
  // reads as "keyboard is up, hide the tab bar." Without this guard, opening/switching
  // conversations on the mobile tab silently focused the composer and the bottom nav vanished
  // — the SAME bug already fixed once for the tab's initial open (owner, 2026-07-30),
  // reintroduced through this second, independent focus trigger.
  useEffect(() => {
    if (!homeMode || !shouldAutoFocusComposer) return
    requestAnimationFrame(() => composerRef.current?.focus())
  }, [homeMode, activeId, shouldAutoFocusComposer])

  // On panel open: restore the full composer draft (text + chips) from the shared
  // composerDraft store. seed() is a single synchronous call — chips and text are written
  // in the same tick, so there is no race with a concurrent pendingMentions drain.
  // On panel close: save the current composer content back to composerDraft so the dock
  // can restore it when it remounts (dock unmounts while the side panel is open).
  //
  // A LAYOUT effect, and the restore runs SYNCHRONOUSLY inside it — both are load-bearing.
  // seed() clears before it writes, while the composer's own pendingText/pendingMentions
  // drains (Composer.tsx) are PASSIVE effects in a child. React flushes every layout effect
  // of a commit before any passive effect of that commit, so this ordering makes the restore
  // the FIRST writer of an open transition and the inbox drain the LAST — the same
  // "restore, then append what the user just staged" order the already-open case produces.
  // Deferring the restore by a frame (it used to sit in requestAnimationFrame) inverted that:
  // a prompt suggestion or an @-chip staged by the very tap that opened the panel was drained
  // into the composer and then wiped one frame later by a seed() carrying an empty draft, so
  // the composer sat empty and the tap was silently lost (QA D2, 2026-08-14). Anything that
  // writes composerDraft for this restore to read must therefore land no later than the
  // mutation phase — an event handler (LuciusDock's "Open in side panel") or a layout-effect
  // cleanup, never a passive one.
  // Starts false, not `open`: a panel that MOUNTS open (the phone's Lucius tab, the desktop
  // home tab — both exist only while their tab is showing) counts as just opened, so the
  // draft saved by the unmount below is restored on the way back. Nothing else changes for
  // the side panel, which mounts closed.
  const wasOpen = useRef(false)
  const mounting = useRef(true)
  useLayoutEffect(() => {
    const justOpened = open && !wasOpen.current
    const justClosed = !open && wasOpen.current
    const onMount = mounting.current
    wasOpen.current = open
    mounting.current = false
    if (justOpened) {
      // Read the freshest draft from the synchronous ref, not the render snapshot (a save
      // landing in this same commit would otherwise read back pre-save). ALWAYS seed — even
      // when the draft is empty — because the side panel stays MOUNTED while closed, so
      // seed() must CLEAR any stale chips, not leave the previous draft showing after the
      // dock cleared it.
      //
      // A restore may focus only when the same input-capability policy as every other
      // automatic focus path allows it. This is not just a mount concern: leaving the desktop
      // Lucius tab turns the already-mounted docked panel from closed to open, and focusing its
      // restored draft raises the iPad keyboard over the destination tab.
      const { text, mentions } = composerDraftRef.current
      composerRef.current?.seed(
        mentions.map((m) => ({ mention: m })),
        text,
        { focus: !onMount && shouldAutoFocusComposer },
      )
    } else if (justClosed) {
      // Always save the CURRENT content — including empty — so clearing the side panel
      // propagates to the dock. Guarding on isEmpty() left a stale draft behind, so a
      // cleared panel reopened the dock with its old text/chips.
      const current = composerRef.current
      if (current) {
        const { text, mentions } = current.serialize()
        setComposerDraft({ text, mentions })
      }
    }
    // composerDraft is read only on justOpened; setComposerDraft is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, shouldAutoFocusComposer])

  // On unmount while open: the home surfaces above are unmounted by a tab switch, and the
  // composer is DOM-owned, so its text and chips would die with the DOM (owner report:
  // "switching between tabs loses the input text, mention chips included"). Save exactly
  // what a close saves; the mount-restore above brings it back. A LAYOUT cleanup on purpose:
  // React runs a deleted parent's layout cleanups before its children's, so composerRef still
  // holds the handle and the editor is still in the document here — a passive cleanup would
  // run after useImperativeHandle had detached it. Guarded on the last committed `open`
  // (wasOpen) so a side panel unmounting while CLOSED never overwrites the draft the dock holds.
  useLayoutEffect(() => {
    return () => {
      if (!wasOpen.current) return
      // The handle, not a DOM node: still attached at this point (see above).
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const current = composerRef.current
      if (!current) return
      const { text, mentions } = current.serialize()
      setComposerDraft({ text, mentions })
    }
    // setComposerDraft is stable; wasOpen/composerRef never change identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // When a prefill arrives (briefing suggestion / Ask Lucius free text), seed it.
  useEffect(() => {
    if (!prefill || !open) return
    onPrefillConsumed?.()
    if (takeSendIntent()) {
      // A new request gets its own conversation: never appended to whatever thread was open
      // (an Automation run, yesterday's chat). Sent once the new conversation is active.
      const onStartedThread = conversations.some((c) => c.id === activeId)
      if (onStartedThread && !streaming) {
        pendingSendRef.current = prefill
        createConversation()
        return
      }
      handleSuggestion(prefill)
      return
    }
    requestAnimationFrame(() => {
      composerRef.current?.seed([], prefill)
    })
    // handleSuggestion reads the live effort/model; re-running on their change would re-send.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill, open])

  const pendingSendRef = useRef<string | null>(null)
  useEffect(() => {
    const text = pendingSendRef.current
    if (!text) return
    pendingSendRef.current = null
    handleSuggestion(text)
    // Runs on the render where the new conversation became active; handleSuggestion is fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

  // Auto-trigger Lucius greeting on fresh onboarding setup. The sentinel text still rides
  // along (filtered from the transcript/title the same as before) purely so this stays a
  // recognisable harness-authored turn; the actual playbook is the "onboarding-new-user"
  // skill, host-invoked deterministically via `skills` — not the model choosing to read a
  // magic string and decide to act on it.
  const onboardingFiredRef = useRef(false)
  useEffect(() => {
    if (!onboarding || !hydrated || onboardingFiredRef.current || messages.length > 0 || streaming)
      return
    onboardingFiredRef.current = true
    if (claimOnboarding && !claimOnboarding()) return
    sendMessage(ONBOARDING_INIT_SENTINEL, [], 'medium', true, '', [], null, ['onboarding-new-user'])
    onOnboardingDone?.()
  }, [onboarding, hydrated, messages.length, streaming])

  // Fetch starter suggestions when the conversation is empty. Skipped entirely in homeMode —
  // the Lucius home tab's empty state deliberately shows no suggestion chips (owner: keep it
  // simple), so there is nothing to spend the generation call on.
  useEffect(() => {
    if (
      !open ||
      onboarding ||
      !hydrated ||
      !suggestionsPrefLoaded ||
      !suggestionsEnabled ||
      homeMode
    )
      return
    if (streaming) return
    // The empty state is deliberately just the shared greeting and a short composer hint.
    // Starter prompts add noise before the user has expressed an intent, so only restore
    // follow-up suggestions a conversation already paid for.
    if (messages.length > 0) restoreSuggestions(activeId)
  }, [
    open,
    onboarding,
    hydrated,
    suggestionsPrefLoaded,
    suggestionsEnabled,
    homeMode,
    activeId,
    messages.length,
    streaming,
  ])

  // Drag-to-resize (left edge of the panel). Pointer capture keeps the drag alive across
  // artifact iframes while the class disables their hit testing for this interaction.
  const onResizePointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      dragging.current = true
      document.body.style.cursor = 'ew-resize'
      document.body.classList.add('chat-resizing')
    },
    onMove: (event) => {
      if (!dragging.current) return
      const w = clampWidth(window.innerWidth - event.clientX)
      const frac = w / window.innerWidth
      setWidthFrac(frac)
      localStorage.setItem(WIDTH_FRAC_KEY, String(frac))
    },
    onEnd: () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.classList.remove('chat-resizing')
    },
  })

  // Every stick-to-bottom below exists so the NEWEST message — at the bottom of a transcript —
  // stays in view. The empty landing screen (homeMode + isHomeEmpty) is the one layout where
  // the thing that matters sits at the TOP: greeting → composer, with the Activity panel
  // loading in underneath. There, "content grew" means the Scheduled list arrived, and pinning
  // to the bottom scrolled the greeting under the topbar and the composer half off the screen
  // with no user action (owner screenshot, 2026-09-20). None of the pins may run on it; the
  // transcript's own jump still fires the moment a first message turns it into a transcript.
  const landingScreen = homeMode && isHomeEmpty

  // On conversation switch, JUMP straight to the latest message once its history loads.
  const switchJumpRef = useRef(false)
  useEffect(() => {
    switchJumpRef.current = true
    isNearBottomRef.current = true
  }, [activeId])
  useLayoutEffect(() => {
    const el = messagesAreaRef.current
    if (!el || !switchJumpRef.current || landingScreen) return
    el.scrollTop = el.scrollHeight
    // Lazily-rendered bubbles above (content-visibility) can resolve their real heights
    // just after this jump — re-pin one frame later so the view stays on the last message.
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight
    })
    if (messages.length > 0) switchJumpRef.current = false
  }, [activeId, messages, landingScreen])

  // A fresh conversation starts back at the small render window.
  useEffect(() => {
    setTranscriptWindow(TRANSCRIPT_WINDOW_INITIAL)
  }, [activeId])

  // Beyond the locally-loaded messages there may be deeper server history: the remote
  // load is a TAIL (chatStore.TRANSCRIPT_FETCH_TAIL), and ids are positional
  // (`{session}-{counter}`), so the earliest loaded message's counter says exactly how
  // many exist before it. A live-born earliest id (no server position) reads as 0 —
  // a fully-local conversation already holds everything this browser saw.
  const earliest = delivered[0]
  const earliestCounter = earliest ? positionalCounter(activeId, earliest.id) : null
  const serverEarlier = earliestCounter !== null && earliestCounter > 1 ? earliestCounter - 1 : 0
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const loadingEarlierRef = useRef(false)
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // Expanding adds content ABOVE the viewport; keep what the eye was on in place.
  const transcriptAnchorRef = useRef<number | null>(null)
  const showEarlierMessages = () => {
    if (hiddenTranscript > 0) {
      transcriptAnchorRef.current = messagesAreaRef.current?.scrollHeight ?? null
      setTranscriptWindow((w) => w + TRANSCRIPT_WINDOW_STEP)
      return
    }
    // Local window exhausted — page the previous slice down from the server. Prepending
    // alone doesn't move anything (the window shows the LAST N), so the scroll anchor is
    // captured right before the window widens to reveal what arrived.
    if (serverEarlier > 0 && !loadingEarlierRef.current) {
      loadingEarlierRef.current = true
      setLoadingEarlier(true)
      const ownerId = activeId
      void loadEarlierMessages(ownerId).then((added) => {
        loadingEarlierRef.current = false
        setLoadingEarlier(false)
        // The messages landed on ownerId's slice regardless; only the VIEW adjustments
        // (window widening, scroll anchor) must not leak onto a conversation the user
        // switched to while the fetch was in flight.
        if (added > 0 && activeIdRef.current === ownerId) {
          transcriptAnchorRef.current = messagesAreaRef.current?.scrollHeight ?? null
          setTranscriptWindow((w) => w + added)
        }
      })
    }
  }
  useLayoutEffect(() => {
    const el = messagesAreaRef.current
    const prev = transcriptAnchorRef.current
    transcriptAnchorRef.current = null
    if (el && prev !== null) el.scrollTop += el.scrollHeight - prev
  }, [transcriptWindow])

  // Scroll to bottom when new messages arrive (if already near bottom). Counts
  // `queuedMessages` too — a message you just sent while busy renders below the
  // activity line (see `stillQueued`), not inside `messages`, but it is still new
  // content the reader just added and expects to see without scrolling for it.
  const prevLengthRef = useRef(messages.length + queuedMessages.length)
  useLayoutEffect(() => {
    const el = messagesAreaRef.current
    if (!el) return
    const total = messages.length + queuedMessages.length
    if (total > prevLengthRef.current && isNearBottomRef.current && !landingScreen) {
      el.scrollTop = el.scrollHeight
    }
    prevLengthRef.current = total
  }, [messages.length, queuedMessages.length, landingScreen])

  useEffect(() => {
    const el = messagesAreaRef.current
    if (!el || !isNearBottomRef.current || landingScreen) return
    el.scrollTop = el.scrollHeight
  }, [messages, queuedMessages, streaming, landingScreen])

  useChatScrollPin(messagesAreaRef, isNearBottomRef, !landingScreen, scrollIntentRef)

  useEffect(() => {
    if (suggestions.length === 0) return
    const el = messagesAreaRef.current
    if (!el || !isNearBottomRef.current || landingScreen) return
    el.scrollTop = el.scrollHeight
  }, [suggestions, landingScreen])

  // ── Send ──────────────────────────────────────────────────────────────────────

  const handleSend = useCallback(
    (parsed: ComposerParsed) => {
      // Deliberately NOT gated on `streaming` — a conversation that is already busy stays
      // sendable (acceptance C1). See docs/adr/2026-08-06-a-turn-belongs-to-its-conversation-not-to-the-view.md.
      const { text, mentions, respondingAgent, skills, attachments, screen } = parsed
      if (!text && mentions.length === 0 && attachments.length === 0) return
      isNearBottomRef.current = true
      // parsed.attachments are uploaded Attachment[] (ref_id-bearing) — LuciusComposer
      // uploads to /api/upload on add, so the old ChatPanel-level upload path is gone.
      sendMessage(
        text,
        attachments,
        effort,
        false,
        activeModel,
        mentions,
        respondingAgent ?? undefined,
        skills,
        screen ?? undefined,
      )
      // Clear the composer after dispatch (the dock does this too); otherwise the sent text
      // lingers in the input. Also clear the shared draft store so the dock doesn't restore
      // a sent message when it remounts.
      composerRef.current?.clear()
      clearComposerDraft()
    },
    [sendMessage, effort, activeModel, clearComposerDraft],
  )

  function handleSuggestion(text: string) {
    isNearBottomRef.current = true
    sendMessage(text, [], effort, false, activeModel, [], undefined, undefined, captureScreen())
  }

  // ── Conversation CRUD ──────────────────────────────────────────────────────────

  // Conversation actions (new / clear / switch) only touch the message HISTORY — the composer
  // draft (typed text + staged chips) is preserved so the user never loses unsent input.
  // None of these gate on `streaming` any more (RFC concurrent-conversations, acceptance
  // B1/B4): a turn belongs to its own conversation's slice now, so switching, starting, or
  // clearing a DIFFERENT conversation can never disturb one that is mid-turn.
  //
  // `persist: false` (same deferred-creation the Lucius home tab uses): don't hit the
  // server/sidebar until the conversation actually has a real message. A `persist: false`
  // conversation is never added to `conversations`, so "the active id isn't in the list" IS
  // "already sitting on an empty/unpersisted conversation" — reusing that instead of minting
  // another one means clicking New twice in a row doesn't leave an orphaned pending draft.
  function handleNewConversation() {
    const alreadyOnAnEmptyOne = !conversations.some((c) => c.id === activeId)
    if (alreadyOnAnEmptyOne) return
    createConversation()
  }

  function handleSwitchConversation(id: string) {
    if (id === activeId) return
    switchConversation(id)
  }

  // Deleting a conversation with an in-flight turn cancels that turn FIRST rather than
  // silently no-op'ing (acceptance B6) — scoped to the TARGET conversation's own slice, not
  // just the currently-viewed one, so deleting a background conversation from the
  // switcher/browser is just as safe as deleting the one on screen.
  //
  // Server-owned turn lifetime (concurrent-conversations RFC Phase 3): the turn is now a
  // background task the server drives, not a fetch this browser holds — aborting a local
  // AbortController here would no longer reach it (and for a background id there may be no
  // locally-mounted watcher to abort at all). POST /api/chat/{id}/stop is the only thing
  // that actually reaches the server-side task; deleteConversation() only proceeds after
  // it's been asked to stop, same ordering as before.
  function handleDeleteConversation(id: string) {
    if (chatSessions.statusFor(id) === 'streaming') {
      void authFetch(`/api/chat/${id}/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
    }
    deleteConversation(id)
  }

  // ── Render ─────────────────────────────────────────────────────────────────────

  if (browsingConvs) {
    // Same shell as the real panel — the `on` class and the width style are what make it
    // visible and wide; an <aside className="lx-side"> without them is a collapsed
    // zero-width element, which is exactly what it rendered as.
    return (
      <aside
        className={'lx-side' + (open ? ' on' : '')}
        data-dropzone="side"
        aria-hidden={!open}
        style={{ width: open && !fillWidth ? width + 'px' : undefined }}
      >
        <ConversationBrowser
          conversations={conversations}
          activeId={activeId}
          initialKind={browsingConvs}
          formatWhen={conversationWhen}
          statusFor={statusFor}
          onClose={() => setBrowsingConvs(null)}
          onDelete={handleDeleteConversation}
          onRename={renameConversation}
          onStop={stopConversation}
          onSelect={(id) => {
            setBrowsingConvs(null)
            handleSwitchConversation(id)
          }}
        />
      </aside>
    )
  }

  // `isHomeEmpty` is computed earlier now (beside the ResizeObserver effect it drives) — see
  // that comment for why.

  // Extracted so the SAME footer instance can be placed in one of two spots (never both at
  // once — two live mounts would fight over `footRef`/`composerRef` and double the input):
  // right under the greeting on the empty landing screen (owner: "push the input higher, the
  // activity list goes below it"), or in its usual pinned-bottom spot everywhere else.
  const composerFooter = (
    <div className="lx-side-foot" ref={footRef}>
      <div className="lx-inputblock">
        <LuciusComposer
          /* Approvals govern the CONVERSATION, so it rides in the LEFT tool group beside
             attach/@//— not in `controls`, which is the model/effort/cost cluster. */
          leadingControls={
            <span data-tour="lucius-approval">
              <ConversationApprovalPicker sessionId={activeId} />
            </span>
          }
          /* Model / effort / cost ride in the composer's toolbar row, right-aligned against
             Send — not as a strip above the field. */
          controls={
            <>
              {/* No session-cost figure on a real phone — less room, less reason to see it at
                  a glance (owner). Desktop keeps it. */}
              {density.showCost && !isMobile && sessionCost > 0 && (
                <span className="chat-session-cost" title={`Session cost: ${fUsd(sessionCost)}`}>
                  {fUsd(sessionCost)}
                </span>
              )}
              {/* ONE control: the tier rides in the trigger, and the panel opens on a root
                  menu (Model › / Effort ›) — see EffortChoices for why it is not a peer
                  <select>, and ModelPicker's `panes` for why the two lists never share a
                  panel. Hidden on the narrowest panels — the trigger's own label had no
                  room left and clipped mid-word. */}
              {density.showModel && (
                <ModelPicker
                  className="chat-model-picker"
                  label="Chat model"
                  size="sm"
                  models={modelInfo.models}
                  value={activeModel}
                  defaultLabel="Default"
                  defaultModelId={modelInfo.defaultModel}
                  onChange={(next) => setConversationModel(activeId, next)}
                  // The trigger shows just the model name on a phone — no effort trailing
                  // label; the tier stays reachable one tap in, via `panes` below (unchanged).
                  trailing={
                    !isMobile && density.showEffortLabel && showEffort
                      ? effortChoiceLabel(clampEffortChoice(effort, effortChoices))
                      : null
                  }
                  panes={
                    showEffort
                      ? [
                          {
                            key: 'effort',
                            label: 'Effort',
                            value: effortChoiceLabel(clampEffortChoice(effort, effortChoices)),
                            content: (close) => (
                              <EffortChoices
                                value={clampEffortChoice(effort, effortChoices)}
                                choices={effortChoices}
                                onChange={(lvl) => {
                                  setEffort(lvl)
                                  close()
                                }}
                              />
                            ),
                          },
                        ]
                      : undefined
                  }
                />
              )}
            </>
          }
          ref={composerRef}
          busy={streaming || sending}
          sendBlocked={(streaming || sending) && !multiTurnEnabled}
          onStop={stopStreaming}
          onSend={handleSend}
          placeholder="Ask Lucius anything — @ to mention, / for skills…"
          autoFocus={open && shouldAutoFocusComposer}
          visibleIds={visibleIds}
          screenId="lucius-side"
          active={open}
          /* Only true while the composer is actually rendered in its TOP/landing position
             (isHomeEmpty) — once a real conversation starts on this tab, `composerFooter`
             relocates to the normal pinned-bottom spot (see its two render sites above/below),
             where the popup opening downward would run off the viewport instead of avoiding
             it. `homeMode` alone would stay true for that pinned-bottom case too. */
          homeMode={homeMode && isHomeEmpty}
          screenAwarenessDisabled={screenAwarenessDisabled}
          noVisionModel={noVisionLabel(activeModel, modelInfo.models, modelInfo.defaultModel)}
          imageReader={modelInfo.imageReader}
          dismissKeyboardOnSend={dismissKeyboardOnSend}
        />
      </div>
    </div>
  )

  return (
    <aside
      ref={sideRef}
      className={'lx-side' + (open ? ' on' : '') + (isHomeEmpty ? ' lx-side--home-empty' : '')}
      data-dropzone="side"
      aria-hidden={!open}
      style={{ width: open && !fillWidth ? width + 'px' : undefined }}
    >
      {/* Left-edge resizer — meaningless once fillWidth/homeMode own the sizing. */}
      {!homeMode && (
        <div
          className="lx-side-resizer"
          onPointerDown={onResizePointerDown}
          title="Drag to resize"
        />
      )}

      {!homeMode && documentsOpen && (
        <Suspense fallback={null}>
          <DocumentsSheet onClose={() => setDocumentsOpen(false)} />
        </Suspense>
      )}
      {!homeMode && menuOpen && (
        <ConversationMenu
          groups={convGroups}
          activeId={activeId}
          formatWhen={shortWhen}
          statusFor={statusFor}
          onClose={() => setMenuOpen(false)}
          onNewChat={() => {
            setMenuOpen(false)
            handleNewConversation()
          }}
          onSelect={(id) => {
            setMenuOpen(false)
            handleSwitchConversation(id)
          }}
          onRename={renameConversation}
          onDelete={handleDeleteConversation}
          onStop={stopConversation}
          onViewAll={(kind) => {
            setMenuOpen(false)
            setBrowsingConvs(kind)
          }}
        />
      )}

      {/* ── Header ── */}
      {!homeMode && (
        <div className="lx-side-head" ref={dropdownRef}>
          <button
            type="button"
            className="lx-side-burger"
            onClick={() => setMenuOpen(true)}
            aria-label="Conversations"
            title="Conversations"
          >
            <Menu size={17} />
          </button>

          {/* LEFT + MIDDLE: identity, then the conversation you are in. They sit in one
            wrapper because they are one line of text at two sizes — the wrapper is what
            baseline-aligns them to each other (see .lx-side-head-id).
            Switching lives in the sidebar behind the hamburger — a dropdown could not hold a
            heading, a date grouping and a "new chat" action without becoming a small page in
            the worst place for one. */}
          <div className="lx-side-head-id">
            {/* The spark is a sibling of the label, not inside it: it is an icon, so it wants
              CENTRING, while the two runs of text want their BASELINES aligned. Nesting it in
              the label made it part of that line box and pulled the whole pair 0.7px up. */}
            <Spark size={17} />
            <span className="lx-side-t">
              <span className="lx-side-t-inner">
                <span className="lx-label-txt">Lucius</span>
              </span>
            </span>

            <div className="conv-switcher-wrap">
              <span className="conv-switcher-title">
                {activeConversation?.title ?? 'New conversation'}
              </span>
            </div>
          </div>

          {/* RIGHT: action buttons */}
          <div className="lx-side-head-actions">
            <button
              className="lx-side-x"
              onClick={() => setDocumentsOpen(true)}
              title="Documents"
              aria-label="Documents"
              data-tour="lucius-documents"
            >
              <FolderLock size={15} />
            </button>

            <button
              className="lx-side-x"
              onClick={handleNewConversation}
              title="New conversation"
              aria-label="New conversation"
            >
              <SquarePen size={15} />
            </button>

            {messages.length > 0 && (
              <button
                className="lx-side-x"
                title="Clear conversation"
                aria-label="Clear conversation"
                onClick={() => {
                  // Keep the composer draft — clearing affects history only. Not gated on
                  // `streaming` (same reasoning as handleNewConversation above). No "already
                  // empty" guard needed here — this button only renders when
                  // messages.length > 0, so the conversation being left is always a real one.
                  createConversation()
                }}
              >
                <Eraser size={15} />
              </button>
            )}

            <button
              className="lx-side-x"
              onClick={() => setOpen(false)}
              aria-label="Collapse"
              title="Collapse"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      )}

      {/* ── Messages ── */}
      <div
        className="lx-side-scroll"
        ref={messagesAreaRef}
        onScroll={() => {
          const el = messagesAreaRef.current
          if (!el) return
          isNearBottomRef.current = followsBottom(el, scrollIntentRef.current)
          // Soft top fade only once scrolled (CSS keys on it), so text dissolves under the
          // top edge like Claude's transcript without dimming the first message at rest.
          el.toggleAttribute('data-scrolled', el.scrollTop > 2)
          // Infinite scroll upward: nearing the top auto-reveals earlier messages —
          // nobody should have to hunt for the "show earlier" button (it stays as the
          // explicit fallback). The anchor ref doubles as an in-flight guard: set when
          // an expansion starts, cleared by the layout effect that restores position.
          if (
            el.scrollTop < 600 &&
            (hiddenTranscript > 0 || serverEarlier > 0) &&
            transcriptAnchorRef.current === null
          ) {
            showEarlierMessages()
          }
        }}
      >
        {/* The SCROLL CONTAINER (.lx-side-scroll, above) stays full-width so its native
            scrollbar sits at the true edge of the pane in homeMode — matching the reference
            (Claude's own scrollbar hugs the window edge, not the message column) rather than
            tracking a centered, narrower content column. This inner wrapper carries the
            .lx-side-scroll's own flex-column/gap layout unchanged (so wrapping is invisible
            to the dock/mobile, which get no extra class here) — only homeMode's extra class
            below adds the width cap + centering, scoped in lucius-home.css. */}
        <div className={'lx-side-scroll-inner' + (homeMode ? ' lx-side-scroll-inner--home' : '')}>
          {/* homeMode shows the greeting off `isHomeEmpty` (the conversation's cached
            `messageCount`, available synchronously from localStorage on first paint) rather
            than waiting on `hydrated` — hydration means "the transcript finished loading from
            IndexedDB / a network fetch", a ~2s round trip for a conversation with nothing
            cached yet, but a conversation already known to be empty has nothing to wait for:
            hydration will only ever CONFIRM zero messages, never contradict it. */}
          {(homeMode ? isHomeEmpty : hydrated && messages.length === 0 && !streaming) ? (
            <>
              <div
                className={
                  'lx-side-empty lx-side-empty--greeting' +
                  (homeMode ? ' lx-side-empty--home' : ' lx-side-empty--dock')
                }
              >
                <div className="lx-side-empty-t">
                  {homeGreeting(
                    new Date(),
                    greetingName(profile.data?.nickname, profile.data?.name),
                  )}
                </div>
                {!homeMode && (
                  <div className="lx-side-empty-hint">
                    Use @ to add context with accounts, transactions, and more. Use / for skills.
                  </div>
                )}
              </div>
              {/* Composer sits right under the greeting on the empty landing screen — "pushed
              higher" (owner) rather than pinned at the very bottom — with the Activity panel
              below IT, so the visual order top-to-bottom is greeting → input → activity. Only
              one of the two `composerFooter` render sites is ever live at once (isHomeEmpty
              flips which); see its definition above `return`. */}
              {homeMode && composerFooter}
              {homeMode && <LuciusHomeStart onSend={handleSuggestion} />}
              {/* Activity panel — landing-screen only (homeMode's empty state), rendered as a
              sibling right after the composer so it reads as "below" it while staying inside
              the same scrollable/centered region the composer's own layout already accounts
              for — a footer-relative placement would have had to fight the composer's two
              different positioning strategies (unpinned+static on desktop's home tab vs.
              pinned+absolute on the phone's Lucius tab, lucius-home.css/lucius.css). Mobile
              keeps the established automation sections hidden, but still exposes urgent
              ledger review work. */}
              {homeMode && (
                <ActivityPanel
                  onOpenAutomation={(automationId, runId, sessionId) => {
                    // A run row (active/completed) has its own conversation: open it right here in
                    // Lucius. Only a Scheduled row (no run, no conversation yet) goes to the
                    // Automations screen (owner, 2026-10-03).
                    if (sessionId) {
                      if (sessionId !== activeId) switchConversation(sessionId)
                      return
                    }
                    openRun({ automationId, runId })
                  }}
                  onOpenTransactionReview={openTransactionReview}
                  showAutomationSections={!hideActivityPanel}
                />
              )}
            </>
          ) : (
            <>
              {/* The transcript has not loaded yet — without this the area sat blank, which
                  reads as an empty conversation rather than one on its way. */}
              {activeId && !hydrated && messages.length === 0 && !streaming && !sending && (
                <ChatLoading />
              )}
              {hiddenTranscript + serverEarlier > 0 && (
                <button
                  type="button"
                  className="lx-history-more"
                  onClick={showEarlierMessages}
                  disabled={loadingEarlier}
                >
                  {loadingEarlier
                    ? 'Loading earlier messages…'
                    : `Show ${hiddenTranscript + serverEarlier} earlier message${
                        hiddenTranscript + serverEarlier === 1 ? '' : 's'
                      }`}
                </button>
              )}
              {visibleDelivered.map((msg, i) => {
                const brief = runBrief(msg.automationBrief)
                return brief ? (
                  // An Automation's opening message is its whole instruction sheet — a
                  // screenful of prose the reader already wrote, pushing the actual run off
                  // the top of the panel. Collapse it to one line naming the Automation, and
                  // link it to the run: the panel is often open over a different tab, and
                  // "which run is this?" should not need hunting for. The marker comes from
                  // the server (assistant.rehydrate_messages), which knows the session's
                  // source, title and run — the panel never guesses from the session id.
                  <button
                    key={msg.id}
                    type="button"
                    className="lx-run-brief"
                    disabled={!brief.automationId}
                    onClick={() => openRun(brief)}
                  >
                    <Spark size={13} />
                    <span className="lx-run-brief-t">Ran {brief.name}</span>
                    {brief.automationId && <ChevronRight size={14} />}
                  </button>
                ) : (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    restoreGuide={changedTurns.has(msg.id)}
                    onSendMessage={(text) =>
                      sendMessage(
                        text,
                        [],
                        effort,
                        false,
                        activeModel,
                        [],
                        undefined,
                        undefined,
                        captureScreen(),
                      )
                    }
                    onResolvePendingToolUse={resolvePendingToolUse}
                    onRespondToChange={respondToChange}
                    onOpenAiSettings={() => {
                      void navigate({ to: '/settings', search: { sub: 'model' } } as never)
                    }}
                    onRetry={
                      msg.role === 'assistant' &&
                      msg.error &&
                      i === visibleDelivered.length - 1 &&
                      canRetry
                        ? retryLast
                        : undefined
                    }
                    onPreviewCheckpoint={previewCheckpoint}
                    onRestoreCheckpoint={restoreCheckpoint}
                  />
                )
              })}

              {/* Follow-up suggestions after a completed turn — a skeleton row while they're
                  still generating, so the gap doesn't read as "nothing is coming." Widths
                  vary to look like real chip labels, not a uniform loading bar. */}
              {messages.length > 0 &&
                !streaming &&
                !following &&
                suggestionsBusy &&
                visibleSuggestions.length === 0 && (
                  <div className="lx-dock-followups lx-side-followups" aria-hidden="true">
                    <Skeleton width={92} height={24} radius={999} />
                    <Skeleton width={148} height={24} radius={999} />
                    <Skeleton width={116} height={24} radius={999} />
                  </div>
                )}

              {/* Follow-up suggestions after a completed turn */}
              {messages.length > 0 && !streaming && !following && visibleSuggestions.length > 0 && (
                <div className="lx-dock-followups lx-side-followups">
                  {visibleSuggestions.map((s) => (
                    <button key={s} onClick={() => handleSuggestion(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
          {/* The activity line is the BOUNDARY, not just an indicator: everything above it
            Lucius has read, everything below it is still waiting. Last item in the
            transcript rather than a fixed bar, so on a long conversation it lands at the
            bottom of the scroll area, where the eye already is. `compacting` preempts this
            boundary entirely — nothing has been read into a new turn yet while a fold is
            still streaming. */}
          {compacting ? (
            <CompactingActivity
              percent={compacting.percent}
              outputTokens={compacting.outputTokens}
              startedAt={startedAt}
            />
          ) : (
            (streaming || sending) && (
              <TurnActivity
                key={messages.filter((m) => m.role === 'assistant').length}
                tokens={liveOutputTokens}
                effort={effort}
                startedAt={startedAt}
                progress={turnProgress(messages)}
              />
            )
          )}

          {/* Not yet read — rendered BELOW the activity line for exactly that reason. */}
          {stillQueued.map((msg) => (
            <MessageBubble
              key={msg.id}
              // EVERY queued message carries its own label. Owner call: a message with no
              // marker reads as sent, and "sent" is a claim about what the model has seen —
              // it must not be made for a message still sitting in the queue.
              message={msg}
              onSendMessage={(text) =>
                sendMessage(
                  text,
                  [],
                  effort,
                  false,
                  activeModel,
                  [],
                  undefined,
                  undefined,
                  captureScreen(),
                )
              }
            />
          ))}
          <div />
        </div>
      </div>

      {/* ── Footer: ONE input field — model/effort toolbar + LuciusComposer (.lx-inputblock) ──
          Skipped here when the empty-landing render site above already placed it (homeMode &&
          isHomeEmpty) — `composerFooter` must mount exactly once. */}
      {catchingUp && hydrated && messages.length > 0 && !streaming && !sending && <CatchingUp />}
      {!(homeMode && isHomeEmpty) && composerFooter}
    </aside>
  )
}
