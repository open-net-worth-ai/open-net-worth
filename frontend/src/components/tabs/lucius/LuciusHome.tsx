// LuciusHome — the desktop Lucius tab: a persistent, collapsible history sidebar beside the
// same ChatPanel the floating dock uses (rendered in homeMode). Mounted only when NOT mobile —
// see the render-gate note in RouterShell.tsx (mobileNav === 'lucius' still owns the phone's
// full-screen chat, unchanged).

import { useEffect, useRef, useState } from 'react'
import { ChatPanel, conversationGroups, conversationWhen } from '../../chat/ChatPanel'
import { ConversationBrowser } from '../../chat/ConversationBrowser'
import { LuciusHistorySidebar } from './LuciusHistorySidebar'
import { LuciusHistoryHoverList } from './LuciusHistoryHoverList'
import { ArtifactSidePanel } from './ArtifactSidePanel'
import { ArtifactFullscreen } from './ArtifactFullscreen'
import { ArtifactPanelProvider } from '../../../state/artifactPanel'
import { VaultPanel } from '../../vault/VaultPanel'
import { useLuciusChat } from '../../../state/luciusChat'
import { ChevronRight, FolderLock, SquarePen } from '../../../ui/icons'
import { usePointerResize } from '../../../hooks/usePointerResize'
import { useSessionState } from '../../../hooks/useSessionState'
import type { ArtifactRef } from '../../../types/chat'
import './lucius-home.css'

const COLLAPSE_KEY = 'luciusSideCollapsed'
const SIDEBAR_DEFAULT_W = 280
const SIDEBAR_MIN_W = 220
const SIDEBAR_MAX_W = 420
const SIDEBAR_WIDTH_KEY = 'luciusSideWidth'

const ARTIFACT_PANEL_DEFAULT_W = 480
const ARTIFACT_PANEL_MIN_W = 320
const ARTIFACT_PANEL_WIDTH_KEY = 'luciusArtifactPanelWidth'
// The chat column a right-hand panel may squeeze it down to before the history makes room.
const CHAT_MIN_W = 440

function clampSidebarWidth(w: number): number {
  return Math.max(SIDEBAR_MIN_W, Math.min(SIDEBAR_MAX_W, w))
}

// Half the screen, not a fixed pixel cap (owner, 2026-08-13) — the artifact panel must never
// crowd the chat pane out on a narrower window, but a fixed 800px cap either wasted space on
// an ultrawide monitor or was already too generous on a small laptop screen.
function artifactPanelMaxWidth(): number {
  return Math.floor(window.innerWidth * 0.5)
}

function clampArtifactPanelWidth(w: number): number {
  return Math.max(ARTIFACT_PANEL_MIN_W, Math.min(artifactPanelMaxWidth(), w))
}
// sessionStorage (not localStorage) — cleared when the tab/browser closes, so this fires
// once per genuinely NEW session, not on every reload/tab-switch within the same one
// (owner: a fresh browser session should open on a clean greeting, not resume mid-chat —
// but glancing at another app tab and coming back must not keep nuking the conversation).
const FRESH_SESSION_KEY = 'luciusFreshSessionStarted'
// sessionStorage, shared with LuciusMobile: Documents stays open across a refresh.
export const DOCUMENTS_OPEN_KEY = 'luciusDocumentsOpen'

function noop() {}

export function LuciusHome({
  prefill,
  onPrefillConsumed,
  onOpenChange,
  onboarding,
  onOnboardingDone,
  claimOnboarding,
}: {
  prefill?: string
  onPrefillConsumed?: () => void
  // Forwarded to the embedded ChatPanel's own onOpenChange — this instance is always visually
  // "open" (it IS the tab), but a navigation away (e.g. tapping an automation) still needs to
  // tell the docked side panel to open so Lucius stays visible once the route changes out from
  // under this tab. Defaults to a no-op for callers that don't care (e.g. tests).
  onOpenChange?: (open: boolean) => void
  // Forwarded to the embedded ChatPanel — arms its auto-greeting on fresh onboarding setup.
  // FirstRun's onDone navigates straight to THIS tab (not the docked panel), so this is the
  // instance that must actually receive the flag for the greeting to fire (owner report,
  // 2026-08-25: "where is the onboarding?" — RouterShell's docked ChatPanel had it, this one
  // never did, and the docked instance is never the one on screen after first run finishes).
  onboarding?: boolean
  onOnboardingDone?: () => void
  // Forwarded to ChatPanel: only one mounted panel may send the welcome.
  claimOnboarding?: () => boolean
}) {
  const {
    conversations,
    activeId,
    createConversation,
    switchConversation,
    deleteConversation,
    renameConversation,
    statusFor,
    markRead,
    stopConversation,
  } = useLuciusChat()

  // A genuinely NEW session (sessionStorage flag not set yet — see FRESH_SESSION_KEY below)
  // starts with the history collapsed by default, regardless of whatever was persisted from a
  // previous session (owner: keep the landing view clean on a fresh start). A reload WITHIN
  // the same session, or any later manual toggle, still reads/writes the normal localStorage
  // preference below — this only overrides the very first paint of a new session.
  const [collapsed, setCollapsed] = useState(() =>
    sessionStorage.getItem(FRESH_SESSION_KEY) ? localStorage.getItem(COLLAPSE_KEY) === '1' : true,
  )
  // Set the instant the sidebar collapses — the reopen button/hover-wrap appears in roughly
  // the same screen position the "Hide chat history" button the user just clicked was in, so
  // the still-resting cursor fires a genuine mouseenter on it immediately (owner report, real
  // device: the hover-preview list "shows up by default" right after collapsing, with no
  // actual hover gesture). A brief cooldown after collapsing suppresses exactly that stale
  // enter without touching genuine hovers that happen any time after.
  const collapsedAtRef = useRef(0)
  // Hover/focus peek at the conversation list while collapsed (owner, 2026-08-13) — see the
  // render below for why this is mounted conditionally rather than always-mounted-and-CSS-hidden.
  const [peekingHistory, setPeekingHistory] = useState(false)
  // The popover (300px, LuciusHistoryHoverList) is far wider than its 34px trigger button and
  // sits a few px below it — a cursor travelling from the button toward a row anywhere but the
  // popover's own leftmost sliver crosses a dead zone that belongs to neither element's DOM
  // subtree, firing mouseleave on the wrap before it ever arrives (owner report: closes the
  // instant you move toward it). A CSS gap-bridge only covers the straight-down path, not a
  // diagonal one, so the close is debounced instead — geometry-independent. Re-entering the
  // wrap (button OR popover, since the popover is a DOM child of it) within the window cancels
  // the pending close.
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const CLOSE_GRACE_MS = 300
  // A row's Rename/Delete kebab (ActionMenu) portals its dropdown to document.body — outside
  // this wrap's own DOM subtree — so moving the cursor from the trigger into the dropdown
  // fires mouseleave on the wrap exactly like a genuine "moved away" would, scheduling the
  // close out from under a menu the user is still using (owner report: "tapping Rename or
  // Delete is closing the floating panel"). This flag, set by ConversationRowMenu's
  // onMenuOpenChange, tells the scheduled close to stand down while any row's menu is open;
  // the menu's own close re-arms it in case the mouse has genuinely left by then.
  const menuOpenRef = useRef(false)
  const cancelScheduledClose = () => {
    if (closeTimerRef.current !== null) {
      clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }
  const scheduleClose = () => {
    cancelScheduledClose()
    closeTimerRef.current = setTimeout(() => {
      if (menuOpenRef.current) return
      setPeekingHistory(false)
    }, CLOSE_GRACE_MS)
  }
  const handleRowMenuOpenChange = (open: boolean) => {
    menuOpenRef.current = open
    if (open) cancelScheduledClose()
    // On close: deliberately do NOT re-schedule here. Selecting Rename leaves the row showing
    // an inline, in-DOM (not portaled) rename input the user still needs to type into and
    // commit — auto-closing ~300ms later yanked it away mid-edit. Selecting Delete removes
    // that one row but the user may well want to keep browsing/deleting others. Either way,
    // the wrap's own onMouseLeave (still attached, unchanged) is what closes the flyout once
    // the cursor genuinely, subsequently leaves it — nothing here needs to force that.
  }
  useEffect(() => cancelScheduledClose, [])
  const toggleCollapsed = () =>
    setCollapsed((prev) => {
      const next = !prev
      if (next) collapsedAtRef.current = Date.now()
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
      // A stale `true` from an earlier hover/click-through survives the peek's own block
      // unmounting (it's separate state, not reset by conditional rendering) — without this,
      // re-collapsing later renders the peek immediately on remount, no fresh hover needed,
      // reproducing the exact "shows up by default" bug the cooldown above was written to
      // suppress (owner report, real device: had to hover in and out just to make it go away).
      cancelScheduledClose()
      setPeekingHistory(false)
      return next
    })
  const HOVER_COOLDOWN_MS = 400
  const startPeeking = () => {
    cancelScheduledClose()
    if (Date.now() - collapsedAtRef.current < HOVER_COOLDOWN_MS) return
    setPeekingHistory(true)
  }

  // Drag-to-resize the sidebar's own width — mirrors ChatPanel's docked-panel resizer
  // (same clamp/cursor/localStorage pattern), a separate control for a separate element:
  // that one resizes ChatPanel itself, this one resizes LuciusHistorySidebar beside it.
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY))
    return Number.isFinite(saved) && saved > 0 ? clampSidebarWidth(saved) : SIDEBAR_DEFAULT_W
  })
  const shellRef = useRef<HTMLDivElement>(null)
  const [artifactPanelWidth, setArtifactPanelWidth] = useState(() => {
    const saved = Number(localStorage.getItem(ARTIFACT_PANEL_WIDTH_KEY))
    return Number.isFinite(saved) && saved > 0
      ? clampArtifactPanelWidth(saved)
      : ARTIFACT_PANEL_DEFAULT_W
  })
  const resizingRef = useRef(false)

  // Double-clicking the resize handle restores the default width — same convention
  // VS Code/most split-pane UIs use for "go back to normal."
  function resetSidebarWidth() {
    setSidebarWidth(SIDEBAR_DEFAULT_W)
    localStorage.setItem(SIDEBAR_WIDTH_KEY, String(SIDEBAR_DEFAULT_W))
  }

  const onSidebarResizePointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      resizingRef.current = true
      document.body.style.cursor = 'ew-resize'
      // Pointer capture plus this temporary iframe opt-out keeps drags smooth over artifacts.
      document.body.classList.add('lucius-resizing')
    },
    onMove: (event) => {
      if (!resizingRef.current || !shellRef.current) return
      const rect = shellRef.current.getBoundingClientRect()
      const w = clampSidebarWidth(event.clientX - rect.left)
      setSidebarWidth(w)
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(w))
    },
    onEnd: () => {
      resizingRef.current = false
      document.body.style.cursor = ''
      document.body.classList.remove('lucius-resizing')
    },
  })

  // The artifact side panel — opened via ArtifactPanelProvider below (ArtifactCard's "Open"
  // button calls this instead of its own default fullscreen takeover). `artifactFullscreen`
  // is presentation only (panel vs. true edge-to-edge takeover, ArtifactFullscreen.tsx) —
  // ONE open artifact, not two separate ArtifactRef states that could drift out of sync with
  // each other. Both close automatically when the active conversation changes (owner,
  // 2026-08-13: an artifact belongs to the message that produced it — leaving it open across
  // a conversation switch would show stale content next to an unrelated chat, same reasoning
  // Claude.ai's own canvas uses).
  const [openArtifact, setOpenArtifact] = useState<ArtifactRef | null>(null)
  const [artifactFullscreen, setArtifactFullscreen] = useState(false)
  const closeArtifact = () => {
    setOpenArtifact(null)
    setArtifactFullscreen(false)
  }
  useEffect(() => {
    closeArtifact()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

  // Opening the panel auto-collapses the history sidebar to make room (owner, 2026-08-13) —
  // but only when there ISN'T room: on a wide screen the history, the chat and the panel all
  // fit, and closing the history there was just a surprise (owner, 2026-09-28: "why when open
  // documents, the side bar of chat history closed?"). A TRANSIENT visual change only, not
  // written to COLLAPSE_KEY: the user's actual sidebar preference is untouched. Only
  // collapses; never force-reopens on close — the existing reopen chevron covers that.
  const panelCrowdsChat = () =>
    (shellRef.current?.clientWidth ?? window.innerWidth) - sidebarWidth - artifactPanelWidth <
    CHAT_MIN_W
  useEffect(() => {
    if (openArtifact && panelCrowdsChat()) setCollapsed(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when an artifact opens
  }, [openArtifact])

  // Documents (internally the Vault) shares the artifact panel's right-hand slot (and its
  // width): an artifact opened from it covers it, and closing that artifact lands back on it.
  // Unlike an
  // artifact it is not tied to a conversation, so switching chats leaves it open.
  // Remembered for the browser session, so a refresh brings it back (owner report).
  const [documentsFlag, setDocumentsFlag] = useSessionState(DOCUMENTS_OPEN_KEY)
  const vaultOpen = documentsFlag === '1'
  const setVaultOpen = (open: boolean) => setDocumentsFlag(open ? '1' : null)
  const toggleVault = () => {
    const opening = !vaultOpen || !!openArtifact
    if (openArtifact) closeArtifact()
    setVaultOpen(opening)
    // Same transient collapse as an opening artifact (above) — only when there is no room.
    if (opening && panelCrowdsChat()) setCollapsed(true)
  }
  const rightPanelOpen = !!openArtifact || vaultOpen

  const artifactResizingRef = useRef(false)

  function resetArtifactPanelWidth() {
    setArtifactPanelWidth(ARTIFACT_PANEL_DEFAULT_W)
    localStorage.setItem(ARTIFACT_PANEL_WIDTH_KEY, String(ARTIFACT_PANEL_DEFAULT_W))
  }

  const onArtifactResizePointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      artifactResizingRef.current = true
      document.body.style.cursor = 'ew-resize'
      document.body.classList.add('lucius-resizing')
    },
    onMove: (event) => {
      if (!artifactResizingRef.current || !shellRef.current) return
      const rect = shellRef.current.getBoundingClientRect()
      // Dragged from the panel's LEFT edge (it's docked to the right), so width is the
      // distance from the pointer to the shell's right edge.
      const w = clampArtifactPanelWidth(rect.right - event.clientX)
      setArtifactPanelWidth(w)
      localStorage.setItem(ARTIFACT_PANEL_WIDTH_KEY, String(w))
    },
    onEnd: () => {
      artifactResizingRef.current = false
      document.body.style.cursor = ''
      document.body.classList.remove('lucius-resizing')
    },
  })

  const [browsingConvs, setBrowsingConvs] = useState<'chat' | 'automation' | null>(null)
  const groups = conversationGroups(conversations)

  // A `persist: false` conversation is never added to `conversations` (that's the whole
  // point — it stays out of the sidebar/server until it has real content), so "the active
  // id isn't in the list" IS "already sitting on an empty/unpersisted conversation." Reusing
  // that instead of minting another one satisfies two owner reports at once: "New shouldn't
  // trigger when I'm already on a new conversation tab", and it's also what makes a
  // not-yet-typed-into conversation never hit the server/sidebar at all (ensurePersisted in
  // luciusChat.tsx promotes it the moment a real message is actually sent).
  const startFresh = () => {
    const alreadyOnAnEmptyOne = !conversations.some((c) => c.id === activeId)
    if (alreadyOnAnEmptyOne) return
    createConversation()
  }

  // Ref guard against React StrictMode's dev double-invoke (would otherwise start two
  // fresh conversations on first mount) — not a dependency-array concern, this must run
  // at most once per actual mount regardless of how many times the effect body re-fires.
  const freshSessionCheckedRef = useRef(false)
  useEffect(() => {
    if (freshSessionCheckedRef.current) return
    freshSessionCheckedRef.current = true
    if (!sessionStorage.getItem(FRESH_SESSION_KEY)) {
      sessionStorage.setItem(FRESH_SESSION_KEY, '1')
      startFresh()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (browsingConvs) {
    return (
      <div className="lucius-home-shell">
        <div className="lucius-home-browser-wrap">
          <ConversationBrowser
            conversations={conversations}
            activeId={activeId}
            initialKind={browsingConvs}
            formatWhen={conversationWhen}
            statusFor={statusFor}
            onClose={() => setBrowsingConvs(null)}
            onDelete={deleteConversation}
            onRename={renameConversation}
            onStop={stopConversation}
            onSelect={(id) => {
              setBrowsingConvs(null)
              switchConversation(id)
            }}
          />
        </div>
      </div>
    )
  }

  return (
    <div
      ref={shellRef}
      className="lucius-home-shell"
      style={{
        gridTemplateColumns: [
          !collapsed ? `${sidebarWidth}px` : null,
          'minmax(0,1fr)',
          rightPanelOpen ? `${artifactPanelWidth}px` : null,
        ]
          .filter(Boolean)
          .join(' '),
      }}
    >
      {!collapsed && (
        <div className="lucius-home-side-wrap">
          <LuciusHistorySidebar
            groups={groups}
            activeId={activeId}
            statusFor={statusFor}
            onNewChat={startFresh}
            onSelect={switchConversation}
            onRename={renameConversation}
            onDelete={deleteConversation}
            onStop={stopConversation}
            onViewAll={setBrowsingConvs}
            onMarkAllRead={markRead}
            onCollapse={toggleCollapsed}
          />
          <div
            className="lucius-home-side-resizer"
            onPointerDown={onSidebarResizePointerDown}
            onDoubleClick={resetSidebarWidth}
            title="Drag to resize (double-click to reset)"
          />
        </div>
      )}
      <main className="lucius-home-main">
        {collapsed && (
          <div className="lucius-home-collapsed-bar">
            <div
              className="lucius-home-reopen-wrap"
              onMouseEnter={startPeeking}
              onMouseLeave={scheduleClose}
              onBlur={(e) => {
                // Only close when focus actually LEAVES the wrap (e.g. Tab-ing past the last
                // row) — a plain onBlur fires on every focus move WITHIN it too (button →
                // first row), which would close the popover the instant it opened via keyboard.
                // Immediate (not debounced): a keyboard user tabbing away isn't crossing any
                // dead zone, so there's nothing here for the grace period to protect against.
                //
                // EXCEPT while a row's kebab menu is open: clicking Rename/Delete moves focus
                // from the (real, in-DOM) kebab trigger to a menu item that's portaled to
                // document.body (ActionMenu.tsx) — genuinely outside this wrap's subtree, so
                // `relatedTarget` reads as "left" even though the user is still using the menu
                // this wrap is showing. That closed the flyout IMMEDIATELY on the click, with
                // no debounce to protect it (owner report: "tapping rename still closes the
                // floating one" — the mouseleave debounce elsewhere already covers the mouse
                // path; this is the keyboard/focus path the same fix didn't reach). Skip the
                // close here and let handleRowMenuOpenChange's own scheduling take over once
                // the menu itself actually closes.
                if (!e.currentTarget.contains(e.relatedTarget) && !menuOpenRef.current) {
                  cancelScheduledClose()
                  setPeekingHistory(false)
                }
              }}
            >
              <button
                type="button"
                className="lucius-home-reopen"
                onClick={toggleCollapsed}
                onFocus={() => {
                  cancelScheduledClose()
                  setPeekingHistory(true)
                }}
                aria-label="Show chat history"
                title="Show chat history"
              >
                <ChevronRight size={14} />
              </button>
              {/* Hover/focus-only peek at the conversation list — switch without permanently
                  re-expanding the sidebar (owner, 2026-08-13). Mounted only while peeking
                  (not always-mounted-but-CSS-hidden): jsdom/testing-library don't evaluate
                  stylesheet display:none, so an always-mounted version made "the sidebar is
                  collapsed" queries find its rows anyway — a real regression caught by
                  LuciusHome.test.tsx, not just a test-authoring nicety. */}
              {peekingHistory && (
                <LuciusHistoryHoverList
                  groups={groups}
                  activeId={activeId}
                  statusFor={statusFor}
                  onSelect={switchConversation}
                  onRename={renameConversation}
                  onDelete={deleteConversation}
                  onStop={stopConversation}
                  onViewAll={setBrowsingConvs}
                  onMenuOpenChange={handleRowMenuOpenChange}
                />
              )}
            </div>
            {/* With the sidebar collapsed, "New" (LuciusHistorySidebar's own button) isn't
                mounted at all — without this, starting a fresh chat required re-expanding
                the sidebar first (owner, 2026-08-13). */}
            <button
              type="button"
              className="lucius-home-reopen"
              onClick={startFresh}
              aria-label="New conversation"
              title="New conversation"
            >
              <SquarePen size={14} />
            </button>
          </div>
        )}
        <button
          type="button"
          className={`lucius-home-vault-btn${vaultOpen && !openArtifact ? ' is-on' : ''}`}
          onClick={toggleVault}
          aria-pressed={vaultOpen && !openArtifact}
          title="Your documents, statements, receipts and saved artifacts"
          data-tour="lucius-documents"
        >
          <FolderLock size={15} strokeWidth={1.6} />
          Documents
        </button>
        <ArtifactPanelProvider onOpen={setOpenArtifact}>
          <ChatPanel
            open
            onOpenChange={onOpenChange ?? noop}
            prefill={prefill}
            onPrefillConsumed={onPrefillConsumed}
            fillWidth
            autoFocusComposer
            homeMode
            screenAwarenessDisabled
            onboarding={onboarding}
            onOnboardingDone={onOnboardingDone}
            claimOnboarding={claimOnboarding}
          />
        </ArtifactPanelProvider>
      </main>
      {rightPanelOpen && !(openArtifact && artifactFullscreen) && (
        <div className="lucius-home-artifact-wrap">
          <div
            className="lucius-home-artifact-resizer"
            onPointerDown={onArtifactResizePointerDown}
            onDoubleClick={resetArtifactPanelWidth}
            title="Drag to resize (double-click to reset)"
          />
          {openArtifact ? (
            <ArtifactSidePanel
              artifact={openArtifact}
              onClose={closeArtifact}
              onExpand={() => setArtifactFullscreen(true)}
            />
          ) : (
            <VaultPanel onClose={() => setVaultOpen(false)} onOpenArtifact={setOpenArtifact} />
          )}
        </div>
      )}
      {openArtifact && artifactFullscreen && (
        <ArtifactFullscreen
          artifact={openArtifact}
          onClose={closeArtifact}
          onMinimize={() => setArtifactFullscreen(false)}
        />
      )}
    </div>
  )
}
