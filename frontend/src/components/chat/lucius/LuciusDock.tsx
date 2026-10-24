// LuciusDock.tsx — global floating dock (collapsed pill ↔ expanded card).
//
// Ports the prototype Dock (app.jsx lines 884-955) to consume the shared chat
// instance (useLuciusChat), the chatComposer store, and the screen-contract
// registry. Mounted ONCE in RouterShell inside the providers; only rendered
// when the side panel is closed ({!chatOpen && <LuciusDock …/>}).
//
// The dock owns the collapsed pill ↔ expanded composer card interaction.
// On "Open in side panel" it saves the full draft (text + chips) to the shared
// composerDraft in chatComposer, then calls props.onExpand to open the panel.
// On mount it seeds from composerDraft so the draft survives the side→dock
// transition (the dock unmounts while the panel is open).

import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { useLuciusChat } from '../../../state/luciusChat'
import { useModels, noVisionLabel } from '../../../hooks/useModels'
import { useChatComposer } from '../../../state/chatComposer'
import { useReauthDraftSource } from '../../../state/useReauthDraftSource'
import { useVisibleIdsValue } from '../../../lucius/visibleIds'
import { captureAndSerialize, screenSuggestions } from '../../../lucius/screenContract'
import { useScreenSnapshot } from '../../../state/screenSnapshot'
import { LuciusComposer } from './Composer'
import { ModelEffortCluster } from './ModelEffortRow'
import { ConversationApprovalPicker } from '../ConversationApprovalPicker'
import type { LuciusComposerHandle, ComposerParsed } from './Composer'
import { MessageBubble } from '../MessageBubble'
import { CompactingActivity } from '../CompactingActivity'
import { TurnActivity, turnProgress } from '../TurnActivity'
import { Spark } from '../../../lucius/identity'
import { useHasAi } from '../../../hooks/useHasAi'
import { SquarePen, PanelRight, ChevronDown } from '../../../ui/icons'
import type { Message } from '../../../types/chat'

// ── Props ────────────────────────────────────────────────────────────────────

interface Props {
  /** Called when the user clicks "Open in side panel". RouterShell sets chatOpen=true. */
  onExpand: () => void
  /** Where to send someone who has no model configured yet — the dock must not offer a chat
   *  it cannot deliver. Optional so existing call sites keep working unchanged. */
  onSetUpAi?: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Extract the last assistant message from the message list. */
function lastAssistantMessage(messages: Message[]): Message | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m && m.role === 'assistant') return m
  }
  return null
}

// ── LuciusLabel — brand mark + agent name inline ──────────────────────────────

function LuciusLabel({ size = 14 }: { size?: number }) {
  return (
    <span className="lx-dock-head-t">
      <Spark size={size} />
      <span className="lx-label-txt">Lucius</span>
    </span>
  )
}

// ── Component ────────────────────────────────────────────────────────────────

export function LuciusDock({ onExpand, onSetUpAi }: Props) {
  // "Ask Lucius" on an instance with no provider is a promise the app cannot keep — a dead end
  // for anyone who declined the model step during first run. Hiding it would be worse: it is
  // the only visible route back to configuring him. So the pill stays and tells the truth.
  const { hasAi, loaded: aiLoaded } = useHasAi()
  const needsSetup = aiLoaded && !hasAi
  const [open, setOpen] = useState(false)
  const dockRef = useRef<HTMLDivElement>(null)
  const answerRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<LuciusComposerHandle>(null) as RefObject<LuciusComposerHandle>
  useReauthDraftSource(composerRef)

  const {
    messages,
    streaming,
    multiTurnEnabled,
    following,
    suggestions,
    activeId,
    conversationModel,
    effort,
    setEffort,
    setConversationModel,
    createConversation,
    sendMessage,
    resolvePendingToolUse,
    stopStreaming,
    sending,
    liveOutputTokens,
    compacting,
    startedAt,
    respondToChange,
    previewCheckpoint,
    restoreCheckpoint,
  } = useLuciusChat()

  // Same image-input warning the side panel shows — the dock is the primary Lucius surface
  // (ADR 0028), so a user attaching a receipt here must get the same heads-up. useModels is
  // module-cached and the dock's own ModelEffortRow already loads it, so this is not a new fetch.
  const dockModels = useModels(true)
  const { requestOpen, composerDraftRef, setComposerDraft, clearComposerDraft } = useChatComposer()

  // On mount: seed the composer from the shared composerDraft (populated by the side panel when
  // it was open). This makes side→dock symmetric with dock→side. Guard with a ref so the seed
  // only fires once even in React Strict Mode's double-invoke in development.
  const seededRef = useRef(false)
  useEffect(() => {
    if (seededRef.current) return
    seededRef.current = true
    // Read the draft INSIDE the RAF from the synchronous ref, not the captured state: the side
    // panel's close-save (setComposerDraft) runs in the same commit but the state snapshot here
    // is pre-save, so a cleared side panel would otherwise re-seed the dock with its old content.
    requestAnimationFrame(() => {
      const { text, mentions } = composerDraftRef.current
      if ((text || mentions.length) && composerRef.current) {
        composerRef.current.seed(
          mentions.map((m) => ({ mention: m })),
          text,
        )
      }
    })
    // composerDraftRef is stable; seed only on first mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // On unmount: save the current composer content to the shared store so the other surface can
  // restore it. The dock unmounts whenever chatOpen=true, so this fires on every dock→side
  // transition (including the indirect path where requestOpen() is called by an external widget).
  // React runs cleanup synchronously before DOM removal, so composerRef.current is still valid.
  useEffect(() => {
    return () => {
      // Always save the CURRENT content — including empty — so clearing the dock propagates
      // to the side panel (symmetric with the panel→dock save). Guarding on isEmpty() left a
      // stale draft behind when the dock was cleared.
      const current = composerRef.current
      if (!current) return
      const { text, mentions } = current.serialize()
      setComposerDraft({ text, mentions })
    }
    // setComposerDraft is stable (useCallback). composerRef never changes identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setComposerDraft])

  // Open the dock when a drag-drop onto the dock (or a right-click "Ask Lucius") fires
  // 'lx-dock-open'. dragRightClick.tsx dispatches this instead of calling requestOpen()
  // so the drop lands in the dock, not in the side panel.
  useEffect(() => {
    const onDockOpen = () => setOpen(true)
    window.addEventListener('lx-dock-open', onDockOpen)
    return () => window.removeEventListener('lx-dock-open', onDockOpen)
  }, [])
  const { visibleIds } = useVisibleIdsValue()
  const { enabled: screenEnabled, pinnedHandleIds } = useScreenSnapshot()

  const lastAnswer = lastAssistantMessage(messages)

  // The dock shows the LAST EXCHANGE — the most recent user message + Lucius's reply to it — so the
  // answer is never shown without the question that prompted it (the user found that inconvenient).
  const lastExchange = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === 'user') return messages.slice(i)
    }
    return lastAnswer ? [lastAnswer] : []
    // An Automation run's opening message is the Automation's whole instruction sheet, and
    // "the last exchange" walks back to the nearest USER message — which, on a run whose
    // reply is still coming, is that sheet. The dock is a peek at the answer, so it drops
    // the brief entirely rather than filling itself with prose nobody is reading here. The
    // side panel shows it, collapsed to one line, where the run is the subject.
  })().filter((m) => !m.automationBrief)

  // Dispatch a rescan when the dock opens/closes or an answer arrives, so the
  // visibleIds viewport scan accounts for dock occlusion.
  useEffect(() => {
    const r = requestAnimationFrame(() => window.dispatchEvent(new Event('lx-rescan')))
    const t = setTimeout(() => window.dispatchEvent(new Event('lx-rescan')), 260)
    return () => {
      cancelAnimationFrame(r)
      clearTimeout(t)
    }
  }, [open, lastAnswer])

  // Keep the dock answer pinned to the bottom as it streams past its max-height, so the
  // user always sees the latest content (not the top of an overflowing answer).
  useEffect(() => {
    const el = answerRef.current
    if (!el) return
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight
    })
    return () => cancelAnimationFrame(id)
  }, [lastAnswer, open])

  // Outside-click / Escape collapse — respecting drag sources + pickers + detail popovers.
  useEffect(() => {
    if (!open) return

    const onDoc = (e: MouseEvent) => {
      const target = e.target as Element
      if (
        target.closest?.('[draggable="true"]') ||
        // Only exempt entity-id clicks on right-click (button 2): the context-menu
        // "Ask Lucius about this" fires contextmenu AFTER mousedown, so we must not
        // close the dock on the preceding right-click mousedown. Left-clicks on a
        // large container that has data-entity-id (e.g. the entire AssetDetailView
        // root) must still close the dock.
        (e.button === 2 && target.closest?.('[data-entity-id]')) ||
        target.closest?.('[data-dropzone]')
      )
        return
      if (
        dockRef.current &&
        !dockRef.current.contains(target) &&
        !target.closest?.('.ac') &&
        // Any overlay a control INSIDE the dock renders through a portal — the model
        // picker's .mp-pop / .mp-sheet above all. It is visually part of the dock but
        // lands on document.body, so `dockRef.contains` reads it as "outside" and
        // picking a model dismissed the whole dock. Same exemption ui/Modal.tsx makes.
        !target.closest?.('[data-portal-layer]') &&
        !target.closest?.('.mx-detail')
      ) {
        setOpen(false)
      }
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      // Layered Esc: collapse the inner layers FIRST (the @/ autocomplete, the Peek preview, an
      // open mention-detail popover). Each closes itself on Esc and React removes it next tick, so
      // the dock only collapses once none of them are on screen — on a subsequent Esc.
      if (
        e.key === 'Escape' &&
        !document.querySelector('.ac') &&
        !document.querySelector('.lx-scope-peekbox') &&
        !document.querySelector('[data-portal-layer]') &&
        !document.querySelector('.mx-detail')
      ) {
        setOpen(false)
      }
    }

    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Send handler — calls the shared sendMessage from useLuciusChat.
  const handleSend = useCallback(
    (parsed: ComposerParsed) => {
      const { text, mentions, respondingAgent, skills, attachments, screen } = parsed
      if (!text.trim() && !mentions.length && !attachments.length) return
      setOpen(true)
      sendMessage(
        text,
        // parsed.attachments are already uploaded Attachment[] (ref_id-bearing).
        attachments,
        effort,
        false,
        conversationModel,
        mentions,
        respondingAgent ?? undefined,
        skills,
        screen ?? undefined,
      )
      composerRef.current?.clear()
      clearComposerDraft()
    },
    [sendMessage, effort, conversationModel, clearComposerDraft],
  )

  // "Open in side panel" — serialize the current composer draft, save it to the shared
  // composerDraft store (where ChatPanel reads it on open via seed()), then open the panel.
  //
  // This replaces the old addMention-per-chip + stashDraftText pattern. Using composerDraft
  // is race-free: seed() is a single synchronous call that writes both text and chips, so
  // there is no concurrent useEffect drain competing with a setText call.
  const handleExpand = useCallback(() => {
    const current = composerRef.current
    if (current && !current.isEmpty()) {
      const { text, mentions } = current.serialize()
      setComposerDraft({ text, mentions })
    } else {
      clearComposerDraft()
    }
    composerRef.current?.clear(true)
    setOpen(false)
    requestOpen()
    onExpand()
  }, [setComposerDraft, clearComposerDraft, requestOpen, onExpand])

  // Screen-aware starter suggestions.
  const sugs = screenSuggestions()

  const hasContent = !composerRef.current?.isEmpty() || messages.length > 0 || streaming

  // Quick-send a suggestion text via the shared chat. Carries the SAME live screen
  // snapshot a typed send captures — a chip-sent turn must not clear the per-turn
  // command/handle manifest (run_screen_command would see "available: none").
  const sendSuggestion = useCallback(
    (text: string) => {
      setOpen(true)
      const screen = screenEnabled
        ? captureAndSerialize('lucius-dock', visibleIds, pinnedHandleIds)
        : null
      sendMessage(
        text,
        [],
        effort,
        false,
        conversationModel,
        [],
        undefined,
        undefined,
        screen ?? undefined,
      )
      composerRef.current?.clear()
    },
    [sendMessage, effort, conversationModel, screenEnabled, visibleIds, pinnedHandleIds],
  )

  return (
    <div
      ref={dockRef}
      className={'lx-dock' + (open ? '' : ' lx-collapsed')}
      data-dropzone="dock"
      role="complementary"
      aria-label="Ask Lucius"
    >
      {/* Collapsed pill */}
      <button
        className="lx-dock-pill"
        onMouseEnter={() => {
          if (!needsSetup) setOpen(true)
        }}
        onClick={() => {
          if (needsSetup) {
            onSetUpAi?.()
            return
          }
          setOpen(true)
          requestAnimationFrame(() => composerRef.current?.focus())
        }}
        aria-label={needsSetup ? 'Set up Lucius' : 'Ask Lucius'}
      >
        <Spark size={13} />
        {needsSetup ? 'Set up Lucius' : 'Ask Lucius'}
      </button>

      {/* Expanded card */}
      <div
        className="lx-dock-inner"
        style={{
          opacity: open ? 1 : 0,
          transform: open ? 'none' : 'translateY(10px) scale(.92)',
          pointerEvents: open ? 'auto' : 'none',
        }}
      >
        {/* Header */}
        <div className="lx-dock-head">
          <LuciusLabel />
          <div className="lx-dock-head-actions">
            {/* New conversation — shown only when there are messages. Not gated on
                `streaming` (RFC concurrent-conversations, acceptance B4): starting a
                conversation is a DIFFERENT conversation's slice than whatever is mid-turn, so
                it can never disturb it. */}
            {messages.length > 0 && (
              <button
                className="lx-dock-collapse"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  // Keep the composer draft — a new conversation resets history, not your
                  // input. `persist: false` (matches ChatPanel.tsx's own New/Clear) — don't
                  // hit the server until the conversation actually has a real message. No
                  // "already empty" guard needed: this button only renders when
                  // messages.length > 0, so the conversation being left is always a real one.
                  createConversation()
                }}
                aria-label="New conversation"
                title="New conversation"
              >
                <SquarePen size={14} />
              </button>
            )}
            <button
              className="lx-dock-collapse"
              onClick={handleExpand}
              aria-label="Open in side panel"
              title="Open in side panel"
            >
              <PanelRight size={15} />
            </button>
            <button
              className="lx-dock-collapse"
              onClick={() => setOpen(false)}
              aria-label="Collapse"
              title="Collapse"
            >
              <ChevronDown size={15} />
            </button>
          </div>
        </div>

        {/* Latest assistant answer — full MessageBubble so the dock has the SAME streaming
            markdown + tool-call pills + "Reading…" + ChangePreview confirmations + citations
            as the side panel (not a stripped-down raw-text view). */}
        {lastExchange.length > 0 && (
          <div className="lx-dock-answer" ref={answerRef}>
            {lastExchange.map((m) => (
              <MessageBubble
                key={m.id}
                message={m}
                onSendMessage={(text) =>
                  sendMessage(
                    text,
                    [],
                    effort,
                    false,
                    conversationModel,
                    [],
                    undefined,
                    undefined,
                    screenEnabled
                      ? captureAndSerialize('lucius-dock', visibleIds, pinnedHandleIds)
                      : undefined,
                  )
                }
                onResolvePendingToolUse={resolvePendingToolUse}
                onRespondToChange={respondToChange}
                onPreviewCheckpoint={previewCheckpoint}
                onRestoreCheckpoint={restoreCheckpoint}
              />
            ))}
          </div>
        )}

        {/* Follow-up suggestion chips — shown after streaming completes */}
        {messages.length > 0 && !streaming && suggestions.length > 0 && (
          <div className="lx-dock-followups">
            {suggestions.map((c) => (
              <button
                key={c}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => sendSuggestion(c)}
              >
                {c}
              </button>
            ))}
          </div>
        )}

        {/* Starter suggestions — shown when there's no content yet */}
        {!hasContent && sugs.length > 0 && (
          <div className="lx-dock-sugs">
            {sugs.map((s) => (
              <button
                key={s}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => sendSuggestion(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Model/effort toolbar — same controls as the side panel footer, so the
            dock user can see and change what model/effort this conversation uses. */}
        {/* Same read/unread boundary as the side panel — see ChatPanel. compacting preempts
            it entirely, same reasoning. */}
        {compacting ? (
          <CompactingActivity
            percent={compacting.percent}
            outputTokens={compacting.outputTokens}
            startedAt={startedAt}
          />
        ) : (
          (streaming || sending) && (
            <TurnActivity
              key={messages.length}
              tokens={liveOutputTokens}
              effort={effort}
              startedAt={startedAt}
              progress={turnProgress(messages)}
            />
          )
        )}

        {/* Screen bar + composer + its toolbar are ONE input field — see .lx-inputblock. */}
        <div className="lx-inputblock">
          {/* Shared composer — model/effort/cost ride in ITS toolbar row, next to Send. */}
          <LuciusComposer
            controls={
              <ModelEffortCluster
                storedModel={conversationModel}
                onModelChange={(v) => setConversationModel(activeId, v)}
                messages={messages}
                effort={effort}
                onEffortChange={setEffort}
              />
            }
            leadingControls={<ConversationApprovalPicker sessionId={activeId} />}
            ref={composerRef}
            busy={(streaming && !following) || sending}
            sendBlocked={((streaming && !following) || sending) && !multiTurnEnabled}
            onStop={stopStreaming}
            onSend={handleSend}
            placeholder="Ask Lucius — type @ to mention, / for skills…"
            visibleIds={visibleIds}
            screenBar="compact"
            screenId="lucius-dock"
            active={open}
            noVisionModel={noVisionLabel(
              conversationModel,
              dockModels.models,
              dockModels.defaultModel,
            )}
            imageReader={dockModels.imageReader}
          />
        </div>
      </div>
    </div>
  )
}
