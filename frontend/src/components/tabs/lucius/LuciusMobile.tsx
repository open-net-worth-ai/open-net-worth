// LuciusMobile — the phone's Lucius tab: full-screen chat, no persistent sidebar (no room on
// a phone) — history is reached via a hamburger that opens the same ConversationMenu
// full-screen overlay the docked panel already uses.
//
// This is a thin layout SHELL, mirroring LuciusHome.tsx's role on desktop: no chat logic
// lives here. The message list, composer, sending/streaming, and all conversation state are
// the exact same ChatPanel(homeMode)/useLuciusChat() the desktop home tab and the floating
// dock use — only the chrome AROUND that shared core differs per surface. Replaces the old
// approach of rendering the bare ChatPanel (whose own header/resizer are built for a floating
// dock) and bending it into a full page via `.app-layout--lucius .lx-side` CSS overrides.

import { useEffect, useState } from 'react'
import { ChatPanel, conversationGroups, conversationWhen } from '../../chat/ChatPanel'
import { ConversationMenu, shortWhen } from '../../chat/ConversationMenu'
import { ConversationBrowser } from '../../chat/ConversationBrowser'
import { useLuciusChat } from '../../../state/luciusChat'
import { FolderLock, Menu, SquarePen } from '../../../ui/icons'
import type { ArtifactRef } from '../../../types/chat'
import { VaultPanel } from '../../vault/VaultPanel'
import { Modal } from '../../ui/Modal'
import { useSessionState } from '../../../hooks/useSessionState'
import { DOCUMENTS_OPEN_KEY } from './LuciusHome'
import { ArtifactFullscreen } from './ArtifactFullscreen'
import './lucius-mobile.css'

function noop() {}

export function LuciusMobile({
  prefill,
  onPrefillConsumed,
  onboarding,
  onOnboardingDone,
  claimOnboarding,
}: {
  prefill?: string
  onPrefillConsumed?: () => void
  // Forwarded to the embedded ChatPanel — arms its auto-greeting on fresh onboarding setup,
  // same as LuciusHome.tsx's desktop counterpart. Missing here meant a phone-width viewport
  // never armed the greeting (RouterShell's <LuciusMobile> call never had these to pass) and
  // never got the "skip the centered empty state, we already know this won't stay empty"
  // fix either — both live entirely off this prop reaching ChatPanel.
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
    stopConversation,
  } = useLuciusChat()

  const [menuOpen, setMenuOpen] = useState(false)
  // Documents takes the whole screen on a phone; an artifact opened from it goes full screen
  // on top, and closing that lands back on Documents.
  const [documentsFlag, setDocumentsFlag] = useSessionState(DOCUMENTS_OPEN_KEY)
  const vaultOpen = documentsFlag === '1'
  const setVaultOpen = (open: boolean) => setDocumentsFlag(open ? '1' : null)
  // "Ask Lucius about this" from a long-press menu stages the chip and asks for the chat
  // (`lx-dock-open`) — here that means getting Documents out of the way, or the chip lands
  // in a composer the sheet is covering (owner report).
  useEffect(() => {
    const showChat = () => setDocumentsFlag(null)
    window.addEventListener('lx-dock-open', showChat)
    return () => window.removeEventListener('lx-dock-open', showChat)
  }, [setDocumentsFlag])
  const [vaultArtifact, setVaultArtifact] = useState<ArtifactRef | null>(null)
  const [browsingConvs, setBrowsingConvs] = useState<'chat' | 'automation' | null>(null)
  const groups = conversationGroups(conversations)
  // Same resolution as the docked panel's header (ChatPanel's `activeConversation`), so the
  // two surfaces never name different conversations. An unpersisted draft has no row, so the
  // empty greeting gets no title rather than a "New conversation" label that says nothing.
  const activeTitle = (
    conversations.find((c) => c.id === activeId) ?? (activeId ? undefined : conversations[0])
  )?.title

  // Same "don't create a duplicate if already sitting on an empty one" guard as the desktop
  // home tab and the docked panel's own New button — a `persist: false` conversation (the
  // default now) never joins `conversations`, so "the active id isn't in the list" IS
  // "already on an empty/unpersisted draft."
  const startFresh = () => {
    const alreadyOnAnEmptyOne = !conversations.some((c) => c.id === activeId)
    if (alreadyOnAnEmptyOne) return
    createConversation()
  }

  if (browsingConvs) {
    return (
      <div className="lucius-mobile-shell">
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
    )
  }

  return (
    <div className="lucius-mobile-shell">
      <div className="lucius-mobile-head">
        <div className="lucius-mobile-head-fade" aria-hidden="true" />
        <div className="lucius-mobile-head-blur lucius-mobile-head-blur-3" aria-hidden="true" />
        <div className="lucius-mobile-head-blur lucius-mobile-head-blur-2" aria-hidden="true" />
        <div className="lucius-mobile-head-blur lucius-mobile-head-blur-1" aria-hidden="true" />
        <button
          type="button"
          className="lucius-mobile-head-btn"
          onClick={() => setMenuOpen(true)}
          aria-label="Conversations"
          title="Conversations"
        >
          <Menu size={20} />
        </button>
        {activeTitle && (
          // Keyed on the text so the AI-written title that replaces the first-message one
          // after the first turn fades in instead of snapping.
          <button
            key={activeTitle}
            type="button"
            className="lucius-mobile-head-title"
            onClick={() => setMenuOpen(true)}
            title={activeTitle}
          >
            {activeTitle}
          </button>
        )}
        <div className="lucius-mobile-head-group">
          <button
            type="button"
            className="lucius-mobile-head-btn"
            onClick={() => setVaultOpen(true)}
            data-tour="lucius-documents"
            aria-label="Documents"
            title="Documents"
          >
            <FolderLock size={19} strokeWidth={1.6} />
          </button>
          <button
            type="button"
            className="lucius-mobile-head-btn"
            onClick={startFresh}
            aria-label="New conversation"
            title="New conversation"
          >
            <SquarePen size={18} />
          </button>
        </div>
      </div>

      {/* A tall sheet over the chat, like Settings — swipe down, tap the chat or × to close.
          Hidden (not closed) while an artifact opened from it is full screen: that viewer
          lives in this shell, under the modal layer; closing it lands back on Documents. */}
      <Modal
        open={vaultOpen && !vaultArtifact}
        onClose={() => setVaultOpen(false)}
        title="Documents"
        size="lg"
        className="vault-sheet"
      >
        <VaultPanel
          layout="sheet"
          onClose={() => setVaultOpen(false)}
          onOpenArtifact={setVaultArtifact}
          onAsked={() => setVaultOpen(false)}
        />
      </Modal>
      {vaultArtifact && (
        <ArtifactFullscreen artifact={vaultArtifact} onClose={() => setVaultArtifact(null)} />
      )}

      {menuOpen && (
        <ConversationMenu
          groups={groups}
          activeId={activeId}
          formatWhen={shortWhen}
          statusFor={statusFor}
          onClose={() => setMenuOpen(false)}
          onNewChat={() => {
            setMenuOpen(false)
            startFresh()
          }}
          onSelect={(id) => {
            setMenuOpen(false)
            switchConversation(id)
          }}
          onRename={renameConversation}
          onDelete={deleteConversation}
          onStop={stopConversation}
          onViewAll={(kind) => {
            setMenuOpen(false)
            setBrowsingConvs(kind)
          }}
        />
      )}

      <ChatPanel
        open
        onOpenChange={noop}
        prefill={prefill}
        onPrefillConsumed={onPrefillConsumed}
        fillWidth
        autoFocusComposer={false}
        homeMode
        screenAwarenessDisabled
        hideActivityPanel
        dismissKeyboardOnSend
        onboarding={onboarding}
        onOnboardingDone={onOnboardingDone}
        claimOnboarding={claimOnboarding}
      />
    </div>
  )
}
