// Composer.tsx — LuciusComposer: the shared input surface for the dock and side panel.
//
// Wraps MentionComposer (contenteditable + trigger detection + serialize) and adds:
//   - ScreenContextBar (context toggle + Peek)
//   - Attachment row (paperclip pick / paste / file drag)
//   - Inline Autocomplete (when @ or / trigger is active)
//   - Actions row (attach / @ / / / send)
//
// The imperative handle exposes focus/clear/serialize/insertChip/seed/isEmpty so the
// dock and side panel can drive it without drilling state.
//
// Ports prototype app.jsx Composer (lines 633-881).

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { makeMentionTokenRE } from '../../../types/mentions'
import type { MentionRef, MentionType } from '../../../types/mentions'
import type { Attachment } from '../../../types/chat'
import type { ScreenSnapshotWire } from '../../../state/screenSnapshot'
import { MentionComposer } from '../MentionComposer'
import type { MentionComposerHandle, ComposerTrigger, ChipClickPayload } from '../MentionComposer'
import { MentionDetail } from './MentionDetail'
import type { MentionDetailData } from './MentionDetail'
import { Autocomplete } from './Autocomplete'
import type { AutocompleteHandle } from './Autocomplete'
import { ScreenContextBar } from './ScreenContextBar'
import { LuciusDragTip, LuciusScreenTip } from '../../intro/LuciusTours'
import { captureAndSerialize } from '../../../lucius/screenContract'
import { commandCatalogSnapshot } from '../../../lucius/screenCommands'
import { useChatComposer } from '../../../state/chatComposer'
import { FileDropMask } from './FileDropMask'
import { useFileDropZone } from './useFileDropZone'
import { useScreenSnapshot } from '../../../state/screenSnapshot'
import type { MentionRow } from '../../../hooks/useMentionables'
import { Paperclip, AtSign, ArrowUp } from '../../../ui/icons'
import { isSafeCssColor } from '../../../utils/mentionable'
import { byId } from '../../../lucius/objectFields'
import { resolveChipMeta } from '../../../lucius/chipMeta'
import { toSendAttachments, useComposerAttachments } from './useComposerAttachments'
import type { AttachmentItem } from './useComposerAttachments'

// ── Public types ──────────────────────────────────────────────────────────────

export interface ComposerParsed {
  text: string
  mentions: MentionRef[]
  respondingAgent: string | null
  skills: string[]
  /** Uploaded attachments (ref_id-bearing) ready for sendMessage. */
  attachments: Attachment[]
  screen: ScreenSnapshotWire | null
}

export interface LuciusComposerProps {
  onSend: (parsed: ComposerParsed) => void
  placeholder?: string
  autoFocus?: boolean
  visibleIds: Set<string>
  /** Render the ScreenContextBar inside the composer (default). The floating dock uses
   *  the compact full-width variant inside the same input stack. */
  screenBar?: boolean | 'compact'
  /** Screen id used for captureAndSerialize (e.g. current route path). */
  screenId?: string
  /** Force screen-awareness off regardless of the global toggle or `screenBar` —
   *  hides the bar entirely and never captures. See ChatPanel's own prop of the
   *  same name for why (the mobile Lucius tab). */
  screenAwarenessDisabled?: boolean
  /** Is the VIEWED conversation's turn running? Only affects the send control: with an
   *  EMPTY composer it becomes Stop. Typing is never blocked by it — see the button. */
  busy?: boolean
  /** Abort the viewed conversation's turn. Required for the Stop affordance to appear. */
  onStop?: () => void
  /** Multi-turn off + busy (ADR docs/adr/2026-08-10-multi-turn-chat-defaults-off.md):
   *  disables Send (typing stays open) but leaves Stop alone — cancelling the running
   *  turn is always safe, only a NEW one is blocked. */
  sendBlocked?: boolean
  /**
   * Whether this composer is the VISIBLE surface that should drain staged mentions.
   * The dock and the (always-mounted) side panel both render a LuciusComposer; without
   * this gate BOTH drain pendingMentions, so a chip lands in both and removing it from one
   * leaves a copy in the hidden one — they pile up on the next dock↔side hand-off. The dock
   * passes active (it only mounts when visible); the side panel passes active={open}.
   */
  active?: boolean
  /**
   * The active model's name when that model is KNOWN to reject image input — drives an
   * inline notice on the attachment row so the user learns it BEFORE sending, not from a
   * reply that says "I can't see the image you attached". Null/undefined (the default,
   * including "we don't know") renders nothing: a wrong warning claims a capability is
   * missing when it isn't. Callers derive it via modelRejectsImages (hooks/useModels).
   */
  noVisionModel?: string | null
  /**
   * The model that WILL read the image instead (backend-derived `image_reader`), or '' when
   * none is configured. Only consulted when `noVisionModel` is set, and it decides between
   * two materially different messages: "it'll be transcribed by X" vs "it won't be read at
   * all". Guessing between those is what made the first cut of this notice unhelpful.
   */
  imageReader?: string
  /**
   * Controls that ride in the toolbar row, between the @ // tool icons and Send — the
   * model picker, effort selector and session cost. They belong to the CALLER (the dock
   * and the side panel each build their own cluster), but they live inside the composer's
   * action row so the whole input is one block instead of a control strip stacked above a
   * text field.
   */
  controls?: ReactNode
  /** Rendered in the LEFT tool group, after attach/@//. For per-conversation
   *  settings that are not about the model. */
  leadingControls?: ReactNode
  /** The Lucius home tab's landing layout puts the composer near the TOP of the page (an
   *  Active/Scheduled panel sits below it) rather than pinned at the bottom like the dock —
   *  so the @/skill Autocomplete popup should open DOWNWARD here instead of its usual upward
   *  anchor, which would otherwise collide with the greeting above. Default false keeps the
   *  dock/mobile's existing upward behavior untouched. */
  homeMode?: boolean
  /** Handle file drags on the composer itself. The side panel turns this off and makes the
   *  whole panel the drop target, feeding files in through the handle's `addFiles`. */
  fileDrop?: boolean
  /** Release editor focus after a valid send so a phone's software keyboard closes.
   *  The mobile Lucius surface opts in; desktop composers keep focus for rapid follow-ups. */
  dismissKeyboardOnSend?: boolean
}

export interface LuciusComposerHandle {
  focus(): void
  clear(preserveSkills?: boolean): void
  serialize(): ComposerParsed
  insertChip(m: MentionRef): void
  /** Seed the composer with items + text (dock → panel handoff). Focuses the editor unless
   *  `focus: false` — a restore on MOUNT must not, since focus is what hides the phone's tab
   *  bar (and a programmatic focus raises no keyboard on iOS, so the bar just vanishes). */
  seed(items: Array<{ mention: MentionRef }>, text: string, opts?: { focus?: boolean }): void
  /** Append text to the composer WITHOUT clearing existing chips. Used for dock→panel text
   *  handoff when mention chips are being drained from the pending store concurrently. */
  setText(text: string): void
  isEmpty(): boolean
  /** Attach files dropped somewhere other than the composer (the side panel's drop zone). */
  addFiles(files: FileList): void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** "a.csv", "a.csv and b.pdf", "a.csv, b.pdf and 2 more" — for a sentence about files. */
function fileNames(items: AttachmentItem[]): string {
  const names = items.map((a) => a.name)
  if (names.length <= 2) return names.join(' and ')
  return `${names.slice(0, 2).join(', ')} and ${names.length - 2} more`
}

function fmtBytes(n: number): string {
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}

// ── Component ─────────────────────────────────────────────────────────────────

export const LuciusComposer = forwardRef<LuciusComposerHandle, LuciusComposerProps>(
  function LuciusComposer(
    {
      onSend,
      placeholder,
      autoFocus,
      visibleIds,
      screenBar = true,
      screenId = 'lucius',
      active = true,
      screenAwarenessDisabled = false,
      busy = false,
      onStop,
      sendBlocked = false,
      noVisionModel = null,
      imageReader = '',
      controls = null,
      leadingControls = null,
      homeMode = false,
      dismissKeyboardOnSend = false,
      fileDrop = true,
    },
    ref,
  ) {
    const mentionRef = useRef<MentionComposerHandle>(null)
    const acRef = useRef<AutocompleteHandle>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    // Anchor for the portaled Autocomplete popup (see Autocomplete.tsx) — the popup measures
    // this element's rect rather than relying on CSS position:absolute against an ancestor,
    // because its true ancestor chain (.lx-side-foot) clips anything extending past its own
    // small box (owner report 2026-08-28, "the popup is not showing up on mobile").
    const composeRootRef = useRef<HTMLDivElement>(null)

    const [trigger, setTrigger] = useState<ComposerTrigger | null>(null)
    const [isEmpty, setIsEmpty] = useState(true)
    const att = useComposerAttachments()
    const { attachments, clear: clearAttachments, addFiles: addAttachmentFiles } = att
    const [attDetail, setAttDetail] = useState<AttachmentItem | null>(null)
    // Why a send did not go (or is waiting), said where the reader is looking. A refused send
    // used to do nothing at all: the text stayed in the box, no reason, and a second attempt
    // typed the message in again (QA, 2026-10-14).
    const [notice, setNotice] = useState<{
      text: string
      refused: boolean
      /** Refused only because a reply was running: true only while one still is. */
      whileBusy?: boolean
    } | null>(null)
    // Send was pressed while a file was still uploading: it fires when the last upload lands
    // instead of being thrown away.
    const [sendWhenReady, setSendWhenReady] = useState(false)
    const [chipDetail, setChipDetail] = useState<MentionDetailData | null>(null)

    const {
      respondingAgent,
      pendingSkills,
      pendingMentions,
      clearPendingMentions,
      setPendingSkills,
      pendingText,
      pendingTextKeepsChips,
      clearPendingText,
    } = useChatComposer()
    const { enabled: screenEnabledGlobal, pinnedHandleIds } = useScreenSnapshot()
    const screenEnabled = !screenAwarenessDisabled && screenEnabledGlobal
    const showScreenBar = screenBar && !screenAwarenessDisabled

    // Drain pending mentions from the chatComposer store into the editor chips — but ONLY for
    // the visible surface (active). Otherwise the dock AND the always-mounted side panel both
    // drain the same mention, so a chip removed from one survives in the hidden other and they
    // accumulate across hand-offs.
    useEffect(() => {
      if (!active || !pendingMentions.length || !mentionRef.current) return
      pendingMentions.forEach((m) => mentionRef.current!.insertChip(m))
      clearPendingMentions()
    }, [active, pendingMentions, clearPendingMentions])

    // One-shot text prefill (banner "Remind me", suggested prompts) — same drain discipline
    // as pendingMentions so it lands in the VISIBLE surface only, mounted or not.
    useEffect(() => {
      if (!active || !pendingText || !mentionRef.current) return
      if (pendingTextKeepsChips) mentionRef.current.appendText(` ${pendingText}`)
      else mentionRef.current.setText(pendingText)
      mentionRef.current.focus()
      clearPendingText()
    }, [active, pendingText, pendingTextKeepsChips, clearPendingText])

    // ── Attachment helpers ────────────────────────────────────────────────────

    function addFiles(files: FileList | null) {
      setNotice((n) => (n?.refused ? null : n))
      att.addFiles(files)
    }
    const drop = useFileDropZone(addFiles, fileDrop)

    function removeAtt(id: string) {
      att.remove(id)
      setAttDetail((d) => (d?.id === id ? null : d))
      // The reader changed what is being sent, so a send that was waiting on it is off.
      setSendWhenReady(false)
      setNotice(null)
    }

    // ── Screen capture ────────────────────────────────────────────────────────

    const buildScreen = useCallback((): ScreenSnapshotWire | null => {
      // A surface that replaces the screen still sends the parked command catalog (no content).
      if (!screenEnabled) return screenAwarenessDisabled ? commandCatalogSnapshot() : null
      return captureAndSerialize(screenId, visibleIds, pinnedHandleIds)
    }, [screenEnabled, screenAwarenessDisabled, screenId, visibleIds, pinnedHandleIds])

    // ── Send ──────────────────────────────────────────────────────────────────

    function refuse(text: string, whileBusy = false) {
      setSendWhenReady(false)
      setNotice({ text, refused: true, whileBusy })
    }

    function doSend() {
      const mc = mentionRef.current
      if (!mc) return
      // Multi-turn off + busy: useChat.sendMessage no-ops too, so say so here.
      if (sendBlocked) {
        return refuse(
          'Lucius is still replying. Stop the reply to send this now, or wait for it.',
          true,
        )
      }
      // A file that did not upload would be silently left out of the message, which is worse
      // than not sending: the reader believes Lucius has it.
      if (att.failed.length) {
        const names = fileNames(att.failed)
        return refuse(`${names} could not be uploaded. Remove it, or add it again, to send.`)
      }
      if (att.uploading.length) {
        setSendWhenReady(true)
        setNotice({
          text: `Sending as soon as ${fileNames(att.uploading)} finishes uploading…`,
          refused: false,
        })
        return
      }
      setSendWhenReady(false)
      setNotice(null)
      const { text, mentions: serializedMentions } = mc.serialize()
      const activeSkills = pendingSkills.filter((skill) =>
        serializedMentions.some(
          (mention) => mention.type === 'skill' && mention.ref === skill.value,
        ),
      )
      const activeSkillIds = new Set(activeSkills.map((skill) => skill.value))
      const mentions = activeSkills.length
        ? serializedMentions.filter(
            (mention) => mention.type !== 'skill' || !activeSkillIds.has(mention.ref),
          )
        : serializedMentions
      const sendAtts = toSendAttachments(attachments)
      const parsed: ComposerParsed = {
        text,
        mentions,
        respondingAgent: respondingAgent?.value ?? null,
        skills: activeSkills.map((skill) => skill.value),
        attachments: sendAtts,
        screen: buildScreen(),
      }
      if (!text.trim() && !mentions.length && !sendAtts.length && !activeSkills.length) return
      onSend(parsed)
      if (dismissKeyboardOnSend) mc.blur()
    }

    useEffect(() => {
      if (sendWhenReady && att.uploading.length === 0) doSend()
      // doSend closes over this render's attachments, which is exactly what must be sent.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sendWhenReady, att.uploading.length])

    // ── Picker helpers ────────────────────────────────────────────────────────

    function openPicker(mode: '@' | '/') {
      // Insert a real "@"/"/" char into the editor so the chip-insert path can replace it and the
      // composer's own detection keeps the query accurate as the user types. Then force-open the
      // picker immediately — without the real char, a bare setTrigger would be cleared by the next
      // emitTrigger(null) (the original bug where the @ and / buttons did nothing).
      mentionRef.current?.insertTrigger(mode)
      setTrigger({ kind: mode, query: '' })
    }

    function insertMention(row: MentionRow) {
      const mc = mentionRef.current
      if (!mc) return
      const mention: MentionRef = {
        type: row.type as MentionRef['type'],
        ref: row.id,
        label: row.label,
      }
      // Prefer on-screen data-ctx-fields via byId (same source as the output chip)
      // so input and output chips are byte-for-byte identical for any entity type.
      // Fall back to sublabel parsing for transactions when the entity is off-screen
      // (e.g. the picker result came from the backend search, not the visible DOM).
      const onScreenRecord = byId(row.type, row.id)
      const meta = resolveChipMeta(row.type, onScreenRecord, row.sublabel)
      if (meta.chipDate !== undefined) mention.chipDate = meta.chipDate
      if (meta.chipAmt !== undefined) mention.chipAmt = meta.chipAmt
      if (meta.chipAmtNeg !== undefined) mention.chipAmtNeg = meta.chipAmtNeg
      // Carry the entity's own display color (account tint, envelope/category dot)
      // so the chip glyph is tinted to match its on-screen dot.
      if (row.color && isSafeCssColor(row.color)) {
        mention.color = row.color
      }
      mc.insertChip(mention)
      mc.removeActiveTrigger()
      setTrigger(null)
    }

    function handlePick(row: MentionRow) {
      const mc = mentionRef.current
      if (!mc) return
      if (row.type === 'skill' && row.surface === 'verb') {
        setPendingSkills([
          ...pendingSkills.filter((skill) => skill.value !== row.id),
          { value: row.id, label: row.label },
        ])
        insertMention(row)
        return
      }
      insertMention(row)
    }

    function handleContext(row: MentionRow) {
      // "+ as context" — inserts normally; role differentiation (data-role="ctx")
      // is a future wave enhancement on the chip-insert path.
      insertMention(row)
    }

    // ── Chip detail popover ───────────────────────────────────────────────────

    function handleChipClick(payload: ChipClickPayload): void {
      const rect = payload.chipEl.getBoundingClientRect()
      const entityId = payload.ref || payload.type
      // Toggle: clicking the same chip again closes the detail.
      if (chipDetail && chipDetail.type === payload.type && chipDetail.id === entityId) {
        setChipDetail(null)
        return
      }
      // Read chip-carried presentation fields for the off-screen fallback.
      // When the entity element is not in the DOM, MentionDetail builds a
      // minimal record from these instead of making a network call.
      const ds = payload.chipEl.dataset
      setChipDetail({
        type: payload.type,
        label: payload.label,
        id: entityId,
        // data-entity-sublabel (set by entityRefProps callers) carries the
        // rich sublabel string (e.g. "payee · account · date · ±amount").
        sublabel: payload.chipEl.getAttribute('data-entity-sublabel') ?? undefined,
        chipDate: ds.chipDate,
        chipAmt: ds.chipAmt,
        rect,
      })
    }

    // ── Keyboard: forward to autocomplete nav when open ───────────────────────

    function handleKeyDown(e: React.KeyboardEvent): boolean {
      if (trigger && acRef.current) {
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          acRef.current.move(1)
          return true
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault()
          acRef.current.move(-1)
          return true
        }
        // Cmd/Ctrl+Left/Right switch the picker's section tabs (owner request 2026-08-11).
        // NOT bare Left/Right: a first attempt tried intercepting only at the query's
        // caret edges (right after "@" / end of what's typed) so plain arrow-key text
        // editing stayed untouched everywhere else — in practice that still visibly
        // fought normal cursor movement (owner report 2026-08-11, "using left and right
        // arrow also affecting the cursor"), because the boundary is reached constantly
        // in ordinary short queries. A modifier is unambiguous: it's never how anyone
        // moves the caret one character, so there's nothing left to conflict with.
        if ((e.metaKey || e.ctrlKey) && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
          e.preventDefault()
          acRef.current.moveSection(e.key === 'ArrowLeft' ? -1 : 1)
          return true
        }
        // Shift+Enter is ALWAYS a newline, never a pick. Without the guard an open picker
        // ate it, so the first press picked a mention and only the second broke the line
        // (owner-reported "I have to double tap a lot", 2026-08-01).
        if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && acRef.current.count > 0) {
          e.preventDefault()
          acRef.current.pickHighlighted()
          return true
        }
        if (e.key === 'Escape') {
          e.preventDefault()
          setTrigger(null)
          return true
        }
      }
      return false
    }

    // ── Imperative handle ─────────────────────────────────────────────────────

    useImperativeHandle(
      ref,
      () => ({
        focus() {
          mentionRef.current?.focus()
        },
        clear(preserveSkills = false) {
          mentionRef.current?.clear()
          clearAttachments()
          setSendWhenReady(false)
          setNotice(null)
          setAttDetail(null)
          setTrigger(null)
          if (!preserveSkills) setPendingSkills([])
        },
        serialize(): ComposerParsed {
          const mc = mentionRef.current
          if (!mc) {
            return {
              text: '',
              mentions: [],
              respondingAgent: null,
              skills: [],
              attachments: [],
              screen: null,
            }
          }
          const { text, mentions: serializedMentions } = mc.serialize()
          const activeSkills = pendingSkills.filter((skill) =>
            serializedMentions.some(
              (mention) => mention.type === 'skill' && mention.ref === skill.value,
            ),
          )
          const activeSkillIds = new Set(activeSkills.map((skill) => skill.value))
          const mentions = activeSkills.length
            ? serializedMentions.filter(
                (mention) => mention.type !== 'skill' || !activeSkillIds.has(mention.ref),
              )
            : serializedMentions
          return {
            text,
            mentions,
            respondingAgent: respondingAgent?.value ?? null,
            skills: activeSkills.map((skill) => skill.value),
            attachments: toSendAttachments(attachments),
            screen: buildScreen(),
          }
        },
        insertChip(m: MentionRef) {
          mentionRef.current?.insertChip(m)
        },
        seed(items: Array<{ mention: MentionRef }>, text: string, opts?: { focus?: boolean }) {
          const mc = mentionRef.current
          if (!mc) return
          const focus = opts?.focus ?? true
          mc.clear()
          if (!text) {
            // No serialized transcript (mentions-only) → insert the chips directly.
            items.forEach(({ mention }) => mc.insertChip(mention, { focus }))
            if (focus) mc.focus()
            return
          }
          // Reconstruct the draft from its serialized transcript: emit plain-text runs verbatim
          // and turn each "[type: ref|label]" token back into a CHIP (enriched from `items` by ref
          // so the chip's date/amount/color survive the dock↔side hand-off). The previous code
          // inserted the chips AND appended the raw transcript, so the token text rendered RAW
          // alongside the chip on the receiving surface.
          const byRef = new Map(
            items.map(({ mention }) => [`${mention.type}:${mention.ref}`, mention]),
          )
          const re = makeMentionTokenRE()
          let last = 0
          let m: RegExpExecArray | null
          while ((m = re.exec(text)) !== null) {
            const before = text.slice(last, m.index)
            if (before) mc.appendText(before, { focus })
            const type = (m[1] ?? '') as MentionType
            const ref = (m[2] ?? '').trim()
            const label = (m[3] ?? '').trim()
            mc.insertChip(byRef.get(`${type}:${ref}`) ?? { type, ref, label: label || ref }, {
              focus,
            })
            last = re.lastIndex
          }
          const tail = text.slice(last)
          if (tail) mc.appendText(tail, { focus })
          if (focus) mc.focus()
        },
        setText(text: string) {
          const mc = mentionRef.current
          if (!mc || !text) return
          // Append text without clearing — existing chips (drained from pendingMentions in a
          // concurrent useEffect) survive. Caret ends at the end.
          mc.appendText(text)
          mc.focus()
        },
        isEmpty() {
          return isEmpty && attachments.length === 0
        },
        addFiles(files: FileList) {
          setNotice((n) => (n?.refused ? null : n))
          addAttachmentFiles(files)
        },
      }),
      [
        isEmpty,
        attachments,
        clearAttachments,
        addAttachmentFiles,
        respondingAgent,
        pendingSkills,
        setPendingSkills,
        buildScreen,
      ],
    )

    // ── Render ────────────────────────────────────────────────────────────────

    return (
      <div
        ref={composeRootRef}
        className={'lx-compose' + (drop.over ? ' dragfile' : '')}
        onDrop={(e) => {
          // Always prevent default so entity-drags (non-file) don't inject their
          // text/label into the contenteditable — the chip is inserted imperatively
          // via the pendingMentions path (dragRightClick → addMention → insertChip).
          e.preventDefault()
          drop.handlers.onDrop(e)
        }}
        onDragEnter={drop.handlers.onDragEnter}
        onDragOver={drop.handlers.onDragOver}
        onDragLeave={drop.handlers.onDragLeave}
        onPaste={(e) => {
          const files = e.clipboardData?.files
          if (files?.length) {
            e.preventDefault()
            addFiles(files)
          }
        }}
      >
        {drop.over && <FileDropMask />}

        {showScreenBar && !homeMode && (
          <>
            <LuciusScreenTip />
            <LuciusDragTip />
          </>
        )}
        {showScreenBar &&
          (screenBar === 'compact' ? (
            <div className="lx-me-row lx-me-row--full">
              <ScreenContextBar
                compact
                active={active}
                visibleIds={visibleIds}
                collapsed={!!trigger}
                composing={!isEmpty}
                recipientName={respondingAgent?.label}
              />
            </div>
          ) : (
            <ScreenContextBar
              active={active}
              visibleIds={visibleIds}
              // The @/ autocomplete overlays this row → hide the whole Peek control (button + panel).
              collapsed={!!trigger}
              // A draft (text/chip) → auto-close just the Peek PANEL so it doesn't eat typing space,
              // but KEEP the Peek button so the user can re-open the preview mid-draft.
              composing={!isEmpty}
              recipientName={respondingAgent?.label}
            />
          ))}

        {noVisionModel && attachments.some((a) => a.kind === 'image') && (
          <div className="lx-att-warn" role="status">
            {imageReader ? (
              <>
                {noVisionModel} can’t see images — {imageReader} will read it and pass the text
                along.
              </>
            ) : (
              <>
                {noVisionModel} can’t see images, and no model is set to read them — Lucius won’t
                see this attachment. Pick a vision model above, or set one under Settings → AI Model
                → Task models → Image reading.
              </>
            )}
          </div>
        )}

        {attachments.length > 0 && (
          <div className="lx-atts">
            {attachments.map((a) => (
              <button
                key={a.id}
                className={`lx-att${a.uploading ? ' lx-att--uploading' : ''}${a.failed ? ' lx-att--failed' : ''}`}
                onClick={() => setAttDetail(a)}
                title={a.failed ? 'This file did not upload' : 'View attachment'}
              >
                {a.kind === 'image' && a.url ? (
                  <img className="lx-att-thumb" src={a.url} alt="" />
                ) : (
                  <span className="lx-att-ic">
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M5 4h11l3 3v13H5Z" />
                      <path d="M8 9h7M8 13h7M8 17h4" />
                    </svg>
                  </span>
                )}
                <span className="lx-att-nm">{a.name}</span>
                {(a.dim != null || a.size != null) && (
                  <span className="lx-att-dim">{a.dim ?? fmtBytes(a.size)}</span>
                )}
                <span
                  className="lx-att-x"
                  onClick={(e) => {
                    e.stopPropagation()
                    removeAtt(a.id)
                  }}
                  aria-label="Remove"
                >
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                  >
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </span>
              </button>
            ))}
          </div>
        )}

        {/* "Still replying" stops being true the moment the reply ends; left up, it told a
            reader to wait for something that had already finished (Retry, persona QA round 5). */}
        {notice && !(notice.whileBusy && !sendBlocked) && (
          <div
            className={`lx-send-note${notice.refused ? ' lx-send-note--refused' : ''}`}
            role={notice.refused ? 'alert' : 'status'}
          >
            {notice.text}
          </div>
        )}

        {attDetail && (
          <div className="lx-att-detail" onClick={() => setAttDetail(null)}>
            <div className="lx-att-card" onClick={(e) => e.stopPropagation()}>
              {attDetail.kind === 'image' && attDetail.url ? (
                <img className="lx-att-preview" src={attDetail.url} alt="" />
              ) : (
                <div className="lx-att-preview file">
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M6 3h8l4 4v14H6Z" />
                    <path d="M14 3v4h4" />
                  </svg>
                </div>
              )}
              <div className="lx-att-meta">
                <div className="lx-att-meta-nm">{attDetail.name}</div>
                <div className="lx-att-meta-sub">
                  {attDetail.type || 'file'} • {fmtBytes(attDetail.size)}
                </div>
              </div>
              <button className="lx-att-remove" onClick={() => removeAtt(attDetail.id)}>
                Remove attachment
              </button>
            </div>
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,.pdf,.csv,.txt,.xlsx,.docx"
          style={{ display: 'none' }}
          onChange={(e) => {
            addFiles(e.target.files)
            e.currentTarget.value = ''
          }}
        />

        {trigger && (
          <Autocomplete
            ref={acRef}
            mode={trigger.kind}
            query={trigger.query}
            visibleIds={visibleIds}
            onPick={handlePick}
            onContext={handleContext}
            onClose={() => setTrigger(null)}
            openDown={homeMode}
            anchorRef={composeRootRef}
          />
        )}

        <MentionDetail detail={chipDetail} onClose={() => setChipDetail(null)} />

        <div className="lx-editrow">
          <MentionComposer
            ref={mentionRef}
            placeholder={placeholder ?? 'Ask Lucius…'}
            // Editor stays editable while streaming so the user can compose the next message;
            // doSend gates the actual send until `busy` clears.
            disabled={false}
            onSubmit={doSend}
            onTrigger={(t) => {
              setTrigger(t)
            }}
            onContentChange={(empty) => {
              setIsEmpty(empty)
              // Typing or removing a chip changes the content — dismiss any open detail popover.
              setChipDetail((d) => (d ? null : d))
              // Editing is the answer to a refusal; a waiting notice stays until it resolves.
              setNotice((n) => (n?.refused ? null : n))
            }}
            autoFocus={autoFocus}
            onKeyDownCapture={handleKeyDown}
            onChipClick={handleChipClick}
          />
          <div className="lx-actions">
            <div className="lx-tools-left">
              <button
                className="lx-tool"
                title="Attach file or image"
                data-tour="lucius-attach"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip size={17} />
              </button>
              <button
                className="lx-tool"
                title="Mention (@)"
                data-tour="lucius-mention"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => openPicker('@')}
              >
                <AtSign size={17} />
              </button>
              <button
                className="lx-tool"
                title="Skills (/)"
                data-tour="lucius-skills"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => openPicker('/')}
              >
                {/* Steeper slash — more upright than lucide's 45° Slash icon */}
                <svg viewBox="0 0 17 17" fill="none" aria-hidden="true" width="17" height="17">
                  <line
                    x1="10.5"
                    y1="2"
                    x2="6.5"
                    y2="15"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
              {/* Caller-owned, LEFT group: things that govern this CONVERSATION (approvals),
                  not the reply (model/effort). Deliberately not in `controls` — putting the
                  approval mode beside the model picker read as a model setting, which it is
                  not (owner, 2026-08-27: "why the hell is it being shown in the model picker?
                  it's a different thing"). */}
              {leadingControls}
            </div>
            {/* Caller-owned cluster (model / effort / cost), right-aligned against Send. */}
            {controls && <div className="lx-actions-controls">{controls}</div>}
            {/* Stop shows whenever this conversation is working AND either the composer
                is empty (nothing to send anyway) OR multi-turn is off (nothing CAN be
                sent right now — see sendBlocked). Same button, same look, either way:
                you can always cancel, you just cannot ALSO queue a second message while
                multi-turn is off. With multi-turn on and something typed, Send still
                queues into the running turn (acceptance C1/C2). */}
            {busy && onStop && (sendBlocked || (isEmpty && attachments.length === 0)) ? (
              <button
                className="lx-send lx-stop"
                onMouseDown={(e) => e.preventDefault()}
                onClick={onStop}
                aria-label="Stop"
                title="Stop this conversation's turn"
              >
                <span className="lx-stop-sq" />
              </button>
            ) : (
              <button
                className="lx-send"
                disabled={!!(isEmpty && attachments.length === 0)}
                aria-busy={sendWhenReady || undefined}
                onMouseDown={(e) => e.preventDefault()}
                onClick={doSend}
                aria-label="Send"
              >
                <ArrowUp size={18} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </div>
      </div>
    )
  },
)
