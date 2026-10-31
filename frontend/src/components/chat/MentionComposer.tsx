// MentionComposer — the always-on chat composer: a single contenteditable surface that flows
// free text and non-editable entity-chip atoms INLINE (chips sit where they were typed, e.g.
// "[Meridian Bank Multiplier] merge with [Meridian Pay USD]"), and detects the "@" / "/" triggers itself so a
// mention can be inserted at ANY caret position — including after an earlier chip (the old
// textarea↔composer mode-switch lost "@" detection after the first pick; this owns it for the
// whole turn).
//
// DOM-as-source-of-truth: React renders an empty contenteditable and never reconciles its
// children. Chips are built imperatively (buildChipEl) and inserted at the caret; reads (serialize)
// walk the live childNodes. This sidesteps the contenteditable caret-jank that re-rendering chip
// atoms from props would cause. ChatPanel drives it through the imperative handle below.
//
// On send the content serializes to inline "[type: ref|label]" tokens (human-readable transcript)
// AND a STRUCTURED MentionRef[] in DOM order (the backend's resolution authority).

import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import type { MentionRef } from '../../types/mentions'
import { iconFor } from '../../lucius/entityRegistry'
import { detectAtToken, detectSlashToken, extendQueryEnd } from '../../utils/mentionTrigger'
import { readSegments, serializeSegments } from './mentionComposerSerialize'
import { GLYPH_SVG, TYPE_MARKER_SVG } from '../../lucius/identity'
import { colorFor, C } from '../../lucius/mentionColors'
import { isSafeCssColor } from '../../utils/mentionable'
import { useIsMobile } from '../../hooks/useIsMobile'
import { connectorLook } from './toolActivity/useToolOwnerLogos'

export type ComposerTrigger = { kind: '@' | '/'; query: string }

/** `focus: false` — write the content but take NEITHER focus nor the caret. A draft restored
 *  on mount must not: on the phone, focus hides the tab bar, and WebKit treats even a
 *  programmatic selection inside a contenteditable as focusing it. Default true. */
export type InsertOpts = { focus?: boolean }

export interface MentionComposerHandle {
  focus(): void
  blur(): void
  clear(): void
  /** Replace all content with plain text (prefill path). */
  setText(text: string): void
  /** Append plain text at the end of the editor WITHOUT clearing existing chips. */
  appendText(text: string, opts?: InsertOpts): void
  /** Insert a "@"/"/" trigger char at the caret (with a word-boundary space when needed) so the
   *  composer's own detection opens the picker — used by the toolbar @ / / buttons. */
  insertTrigger(mode: '@' | '/'): void
  /** Insert a chip at the active "@" trigger (replacing "@query"), or at the caret/end otherwise. */
  insertChip(m: MentionRef, opts?: InsertOpts): void
  /** Delete the active "@query" / "/query" fragment without inserting a chip (verb / agent pick). */
  removeActiveTrigger(): void
  serialize(): { text: string; mentions: MentionRef[] }
  isEmpty(): boolean
}

export interface ChipClickPayload {
  type: string
  ref: string
  label: string
  chipEl: HTMLElement
}

export interface MentionComposerProps {
  placeholder?: string
  disabled?: boolean
  onSubmit(): void
  /** Fired on every input/selection change with the active trigger (or null when none). */
  onTrigger(trigger: ComposerTrigger | null): void
  /** Fired whenever content changes so the parent can gate the send button. */
  onContentChange(isEmpty: boolean): void
  /** Fired when a chip is inserted (any path) so the parent can announce it to screen readers. */
  onInsertChip?(label: string): void
  /** Parent key handler — consumes picker navigation + Enter-to-send first. Returns true if handled. */
  onKeyDownCapture?(e: React.KeyboardEvent): boolean
  /** Focus the editor on mount (prototype Composer passes this). */
  autoFocus?: boolean
  /** Fired when the user clicks a chip body (not the remove ×). */
  onChipClick?(payload: ChipClickPayload): void
}

// Keys that move the caret WITHOUT firing an input event — the trigger has to be
// recomputed on these too, or the picker stays armed after you arrow out of an "@token".
const CARET_KEYS = new Set([
  'ArrowLeft',
  'ArrowRight',
  'ArrowUp',
  'ArrowDown',
  'Home',
  'End',
  'PageUp',
  'PageDown',
])

/** Keep the caret visible inside the editor's own internal scroll (owner report: typing
 *  past the visible area — every composer instance: dock, mobile, the Lucius home tab —
 *  left the caret scrolled out of view below the fold, with no feedback that anything was
 *  still being typed). contentEditable does NOT auto-scroll to the caret the way a
 *  <textarea> does natively, so this has to be driven by hand on every input/caret move.
 *  A collapsed Range's own client rect IS the caret's on-screen position in every engine
 *  this app ships to — no temporary marker node needed. */
function scrollCaretIntoView(editor: HTMLElement): void {
  // Layout measurement (getClientRects/getBoundingClientRect) is a real-browser-only
  // concern — jsdom (the test environment) doesn't implement layout and can throw here.
  // This is a pure visual nicety: any failure just skips the scroll adjustment, never
  // breaks typing/sending itself.
  try {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return
    const range = sel.getRangeAt(0)
    if (!editor.contains(range.startContainer)) return
    const rects = range.getClientRects()
    const rect = rects.length > 0 ? rects[rects.length - 1] : range.getBoundingClientRect()
    // A degenerate all-zero rect — some engines report this for a collapsed caret sitting on
    // a freshly-empty line with nothing typed on it yet (the exact shape a Shift+Enter leaves
    // behind before the next keystroke) — carries no measurable position. But that state
    // overwhelmingly means "caret is on a new line at the end of what's being typed," so
    // scrolling all the way down is the right call rather than silently doing nothing (which
    // is what left the caret stranded below the fold — owner report).
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      editor.scrollTop = editor.scrollHeight
      return
    }
    const editorRect = editor.getBoundingClientRect()
    if (rect.bottom > editorRect.bottom) {
      editor.scrollTop += rect.bottom - editorRect.bottom + 4
    } else if (rect.top < editorRect.top) {
      editor.scrollTop -= editorRect.top - rect.top + 4
    }
  } catch {
    /* real-browser-only DOM measurement — see comment above */
  }
}

// ── Imperative DOM helpers ────────────────────────────────────────────────────────

// Inline Spark SVG — mirrors the Spark component exactly, used in the chip DOM
// since we can't render JSX here (imperative DOM build, not React render).
const SPARK_SVG =
  '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.94 14.06A2 2 0 0 0 8.5 12.6l-5.4-1.4a.4.4 0 0 1 0-.78l5.4-1.4A2 2 0 0 0 9.94 7.6l1.4-5.4a.4.4 0 0 1 .78 0l1.4 5.4A2 2 0 0 0 14.96 9l5.4 1.4a.4.4 0 0 1 0 .78l-5.4 1.4a2 2 0 0 0-1.44 1.44l-1.4 5.4a.4.4 0 0 1-.78 0z"/><path d="M19 4v3M20.5 5.5h-3M18 17v2M19 18h-2"/></svg>'

function buildChipEl(m: MentionRef): HTMLElement {
  const chip = document.createElement('span')
  // Keep 'mention-chip' for existing test selectors (.mention-chip / data-chip-*).
  // Add prototype classes on top so lucius.css styles apply.
  chip.className = 'chip inline mention-chip'
  chip.contentEditable = 'false'
  chip.dataset.chipType = m.type
  chip.dataset.chipRef = m.ref
  chip.dataset.chipLabel = m.label
  chip.dataset.id = m.ref || m.type
  // Persist the chip's presentation meta (date / signed amount / direction / color) so it
  // survives serialize() → composerDraft → seed() across the dock↔side hand-off. Without this
  // readSegments only recovers type/ref/label and the chip loses its price/amount on hand-off.
  if (m.chipDate) chip.dataset.chipDate = m.chipDate
  if (m.chipAmt) chip.dataset.chipAmt = m.chipAmt
  if (m.chipAmtNeg !== undefined) chip.dataset.chipAmtNeg = String(m.chipAmtNeg)
  if (m.color) chip.dataset.chipColor = m.color

  // 'agent' joins 'screen'/'source' as a UI-side chip type: the mention REGISTRY carries no
  // 'agent' provider (agent is a SURFACE — `surfaces ⊆ {entity,agent,verb}` — and routing
  // rides `responding_agent`, not a mention type), so the union cannot name it. Cast-guarded
  // to keep the rendering behaviour identical rather than inventing a backend type.
  const isAgent = (m.type as string) === 'agent'
  const isScope = (m.type as string) === 'screen'
  const isSource = (m.type as string) === 'source'

  // Glyph element — mirrors the prototype makeChip logic.
  // Color is resolved through colorFor() — never read item.dot ad-hoc here.
  // Transaction direction and account color are passed as item.dot so colorFor()
  // returns them at step 1 (explicit per-item override).
  const glEl = document.createElement('span')
  // The picker loaded the connectors read before this chip could be picked, so the mark is
  // already resolvable here — the same one the Connectors card shows.
  const logo = m.type === 'connector' ? connectorLook(m.ref).logo : undefined
  if (logo) {
    const img = document.createElement('img')
    img.className = 'chip-logo'
    img.src = logo
    img.alt = ''
    chip.appendChild(img)
  } else if (isScope || isSource) {
    glEl.className = 'chip-glyph'
    glEl.textContent = '◉'
  } else if (isAgent) {
    glEl.className = 'chip-glyph'
    glEl.innerHTML = SPARK_SVG
  } else {
    // Build the per-item dot for colorFor():
    //   transaction → direction tint (green income / red expense)
    //   any entity with its own display color (account tint, envelope/category dot)
    //     → that color (isSafeCssColor-guarded)
    //   others      → no dot (TYPE_COLOR default applies)
    let dot: string | undefined
    if (m.type === 'transaction' && m.chipAmtNeg !== undefined) {
      dot = m.chipAmtNeg ? C.neg : C.pos
    } else if (m.color && isSafeCssColor(m.color)) {
      dot = m.color
    }
    glEl.className = 'chip-gl'
    glEl.style.color = colorFor({ type: m.type, dot })
    if (GLYPH_SVG[m.type] !== undefined) glEl.innerHTML = GLYPH_SVG[m.type] ?? ''
    else if (TYPE_MARKER_SVG[m.type] !== undefined) glEl.innerHTML = TYPE_MARKER_SVG[m.type] ?? ''
    else glEl.textContent = iconFor(m.type)
  }
  if (!logo) chip.appendChild(glEl)

  const nameEl = document.createElement('span')
  nameEl.className = 'chip-name'
  nameEl.textContent = m.label
  chip.appendChild(nameEl)

  // Date meta (.chip-meta) and amount (.chip-amt) are set for any type that
  // carries these fields — transaction (signed, colored), account/holding (neutral).
  // chipAmtNeg undefined → neutral (no pos/neg class); true/false → neg/pos.
  if (m.chipDate) {
    const dateEl = document.createElement('span')
    dateEl.className = 'chip-meta'
    dateEl.textContent = m.chipDate
    chip.appendChild(dateEl)
  }
  if (m.chipAmt) {
    const amtEl = document.createElement('span')
    amtEl.className =
      'chip-amt money' + (m.chipAmtNeg === true ? ' neg' : m.chipAmtNeg === false ? ' pos' : '')
    amtEl.textContent = m.chipAmt
    chip.appendChild(amtEl)
  }

  return chip
}

function editorIsEmpty(editor: HTMLElement): boolean {
  return !editor.querySelector('.mention-chip') && (editor.textContent ?? '').trim() === ''
}

function placeCaretAtEnd(editor: HTMLElement): void {
  const range = document.createRange()
  range.selectNodeContents(editor)
  range.collapse(false)
  const sel = window.getSelection()
  sel?.removeAllRanges()
  sel?.addRange(range)
}

function isChip(node: Node | null | undefined): node is HTMLElement {
  return node instanceof HTMLElement && node.classList.contains('mention-chip')
}

/** insertChip's single trailing space — the caret's home after a pick, and the chip's own
 *  separator (browsers may have turned it into an nbsp). */
function isSeparatorChar(ch: string | undefined): boolean {
  return ch === ' ' || ch === '\u00a0'
}

type ChipHit = { chip: HTMLElement; eatSeparator: boolean }

/** The chip a collapsed caret would delete in `direction`, or null when the caret's neighbour
 *  is ordinary text. Resolves the caret from ANY container the engine may park it in: a text
 *  node (offset 0 / end), the editor itself (child index), or INSIDE the non-editable chip —
 *  WebKit places a tapped caret in the chip's own text, which the old text-node-only match
 *  missed, so native deletion then stepped the caret over the atom instead of removing it.
 *  Backward over the chip's own separator space counts as the chip too (`eatSeparator`):
 *  that is exactly where a pick leaves the caret, and eating only the invisible space first
 *  made every deletion take two presses (owner report, second round). */
function chipAtCaret(editor: HTMLElement, direction: 'backward' | 'forward'): ChipHit | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return null
  const { startContainer: node, startOffset: offset } = sel.getRangeAt(0)
  if (!editor.contains(node)) return null
  if (node !== editor) {
    const inside = (node instanceof HTMLElement ? node : node.parentElement)?.closest(
      '.mention-chip',
    )
    if (inside && editor.contains(inside))
      return { chip: inside as HTMLElement, eatSeparator: false }
  }
  const isText = node.nodeType === Node.TEXT_NODE
  const text = node.textContent ?? ''
  let neighbour: Node | null = null
  if (node === editor) {
    neighbour =
      (direction === 'backward' ? editor.childNodes[offset - 1] : editor.childNodes[offset]) ?? null
    if (direction === 'backward' && neighbour?.nodeType === Node.TEXT_NODE) {
      const t = neighbour.textContent ?? ''
      if (t.length === 1 && isSeparatorChar(t[0]) && isChip(neighbour.previousSibling)) {
        return { chip: neighbour.previousSibling, eatSeparator: true }
      }
      neighbour = null
    }
  } else if (direction === 'backward') {
    if (offset === 0) neighbour = node.previousSibling
    else if (isText && offset === 1 && isSeparatorChar(text[0]) && isChip(node.previousSibling)) {
      return { chip: node.previousSibling, eatSeparator: true }
    }
  } else {
    const len = isText ? text.length : node.childNodes.length
    if (offset >= len) neighbour = node.nextSibling
  }
  return isChip(neighbour) ? { chip: neighbour, eatSeparator: false } : null
}

// ── Component ──────────────────────────────────────────────────────────────────────

export const MentionComposer = forwardRef<MentionComposerHandle, MentionComposerProps>(
  function MentionComposer(
    {
      placeholder,
      disabled,
      onSubmit,
      onTrigger,
      onContentChange,
      onInsertChip,
      onKeyDownCapture,
      autoFocus,
      onChipClick,
    },
    ref,
  ) {
    const editorRef = useRef<HTMLDivElement>(null)
    const isMobile = useIsMobile()
    // Latest props captured in a ref so the document-level selectionchange listener (mounted once)
    // always calls through to current callbacks without re-subscribing.
    const cb = useRef({ onTrigger, onContentChange })
    cb.current = { onTrigger, onContentChange }

    function refresh(): void {
      const editor = editorRef.current
      if (!editor) return
      const hasChip = !!editor.querySelector('.mention-chip')
      const raw = editor.textContent ?? ''
      // Placeholder hides as soon as there's ANY character — a typed space IS input, so it must
      // clear the placeholder (untrimmed). Send-eligibility (onContentChange) stays trimmed so a
      // whitespace-only draft still counts as empty and can't be sent.
      editor.classList.toggle('is-empty', !hasChip && raw.length === 0)
      cb.current.onContentChange(!hasChip && raw.trim() === '')
      scrollCaretIntoView(editor)
    }

    // Resolve the active "@"/"/" trigger from the live collapsed caret. The walk is bounded to the
    // current text node — chips break text nodes, so a trigger can never span across one.
    function detectTrigger(): {
      kind: '@' | '/'
      query: string
      node: Text
      start: number
      end: number
    } | null {
      const editor = editorRef.current
      const sel = window.getSelection()
      if (!editor || !sel || !sel.isCollapsed || sel.rangeCount === 0) return null
      const node = sel.focusNode
      if (!node || node.nodeType !== Node.TEXT_NODE || !editor.contains(node)) return null
      const text = node.textContent ?? ''
      const offset = sel.focusOffset
      const slash = detectSlashToken(text, offset)
      if (slash) {
        // Extend past the caret to the end of the CURRENT word, not just what's before
        // it — otherwise moving the caret left through an already-typed query silently
        // truncates it (see extendQueryEnd's own comment). `end` is also what
        // deleteTriggerFragment replaces on pick, so this keeps search text and
        // replacement range consistent — picking a result replaces the WHOLE word, never
        // leaving a dangling remainder after the caret.
        const end = extendQueryEnd(text, offset)
        return {
          kind: '/',
          query: text.slice(slash.slashPos + 1, end),
          node: node as Text,
          start: slash.slashPos,
          end,
        }
      }
      const at = detectAtToken(text, offset)
      if (at) {
        const end = extendQueryEnd(text, offset)
        return {
          kind: '@',
          query: text.slice(at.atPos + 1, end),
          node: node as Text,
          start: at.atPos,
          end,
        }
      }
      return null
    }

    function emitTrigger(): void {
      const t = detectTrigger()
      cb.current.onTrigger(t ? { kind: t.kind, query: t.query } : null)
    }

    // Delete the active trigger fragment ("@query"/"/query"). Returns the range collapsed where it
    // was, ready for a chip insert. Null when there's no live trigger.
    function deleteTriggerFragment(): Range | null {
      const t = detectTrigger()
      const editor = editorRef.current
      if (!t || !editor || !editor.contains(t.node)) return null
      const range = document.createRange()
      const max = (t.node.textContent ?? '').length
      range.setStart(t.node, Math.min(t.start, max))
      range.setEnd(t.node, Math.min(t.end, max))
      range.deleteContents()
      return range
    }

    function currentEditorRange(): Range | null {
      const editor = editorRef.current
      const sel = window.getSelection()
      if (!editor) return null
      if (sel && sel.rangeCount && sel.focusNode && editor.contains(sel.focusNode)) {
        return sel.getRangeAt(0)
      }
      const range = document.createRange()
      range.selectNodeContents(editor)
      range.collapse(false)
      return range
    }

    function insertChip(m: MentionRef, opts?: InsertOpts): void {
      const editor = editorRef.current
      if (!editor) return
      const focus = opts?.focus ?? true
      if (focus) editor.focus()
      // An inline "@" pick replaces the "@query" fragment in place. A trigger-less insert (a
      // widget's ✦ staged from outside the composer) has no caret intent → append at the END,
      // predictably, rather than at whatever stale offset focus() left behind.
      let range = deleteTriggerFragment()
      if (!range) {
        // Trigger-less insert (drag-drop / staged from a widget) → append at the END. If the
        // editor is empty it still holds the browser's <br> placeholder; clear it first so the
        // chip doesn't land BELOW a blank line (the recurring drag-into-box newline).
        if (editorIsEmpty(editor)) editor.textContent = ''
        range = document.createRange()
        range.selectNodeContents(editor)
        range.collapse(false)
      }
      range.deleteContents()
      const chip = buildChipEl(m)
      const trailing = document.createTextNode(' ') // nbsp: gives the caret a home + a word boundary
      // One fragment so chip-then-space order is deterministic (separate insertNode calls reverse).
      const frag = document.createDocumentFragment()
      frag.append(chip, trailing)
      range.insertNode(frag)
      if (focus) {
        // Caret just after the trailing space.
        const after = document.createRange()
        after.setStartAfter(trailing)
        after.collapse(true)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(after)
      }
      refresh()
      cb.current.onTrigger(null)
      onInsertChip?.(m.label)
    }

    function insertText(text: string): void {
      const range = currentEditorRange()
      if (!range) return
      range.deleteContents()
      const node = document.createTextNode(text)
      range.insertNode(node)
      const after = document.createRange()
      // Place the caret INSIDE the text node (not setStartAfter, which lands in the parent
      // element). Trigger detection needs sel.focusNode to be the text node — otherwise the
      // "@"/"/" the toolbar button inserts is not detected and the picker closes immediately.
      after.setStart(node, node.length)
      after.collapse(true)
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(after)
      refresh()
      emitTrigger()
    }

    // A dedicated line break, not insertText('\n') or a hand-rolled Range.insertNode: two
    // different manual-DOM-surgery approaches (a bare '\n' text node, then a manually inserted
    // <br> positioned via Range/Selection) BOTH reproduced the exact same owner-reported bug
    // ("shift+enter does nothing, the second press works") — instrumentation confirmed the event
    // handler ran identically every single press, so the bug was never in when/whether we acted,
    // only in how reliably OUR OWN caret math matched what the browser then chose to render.
    // execCommand — deprecated in spec terms, but still universally supported, and still what
    // production editors reach for exactly here — hands the insertion AND the caret placement to
    // the browser's own tested editing engine (the same one a native, un-intercepted Shift+Enter
    // already uses) instead of us re-deriving it. It also fires a real `input` event, so the
    // existing onInput handler (refresh + trigger detection) picks it up for free.
    function insertLineBreak(): void {
      // jsdom (tests) has no execCommand at all; fall back to the manual <br> insert there —
      // real browsers always take the branch above.
      if (typeof document.execCommand === 'function') {
        document.execCommand('insertLineBreak')
      } else {
        const range = currentEditorRange()
        if (range) {
          range.deleteContents()
          const br = document.createElement('br')
          range.insertNode(br)
          const after = document.createRange()
          after.setStartAfter(br)
          after.collapse(true)
          const sel = window.getSelection()
          sel?.removeAllRanges()
          sel?.addRange(after)
        }
      }
      refresh()
      emitTrigger()
    }

    function handleKeyDown(e: React.KeyboardEvent): void {
      // Parent first: it consumes picker navigation (↑↓↵⇥esc) while a picker is open.
      if (onKeyDownCapture?.(e)) return
      // Enter sends; the composer owns this (it sees IME state, which a parent handler can't).
      if (e.key === 'Enter' && !(e.nativeEvent as { isComposing?: boolean }).isComposing) {
        // A touch keyboard's Return key never carries a real Shift modifier — there is no
        // physical shift key to hold — so "Enter sends, Shift+Enter breaks the line" has no
        // way to reach the newline case on mobile. Enter always breaks the line there instead;
        // sending is the visible Send button's job. Desktop keeps Enter-sends/Shift+Enter-newline.
        if (isMobile || e.shiftKey) {
          e.preventDefault()
          insertLineBreak()
          return
        }
        e.preventDefault()
        if (!disabled) onSubmit()
        return
      }
      // Desktop path: a physical Backspace/Delete is identified here. preventDefault also
      // suppresses the beforeinput that would otherwise follow, so the chip is removed once.
      if (e.key === 'Backspace' || e.key === 'Delete') {
        if (deleteAdjacentChip(e.key === 'Backspace' ? 'backward' : 'forward')) e.preventDefault()
      }
    }

    /** Remove the chip the caret is on / next to. Returns true when a chip was removed. */
    function deleteAdjacentChip(direction: 'backward' | 'forward'): boolean {
      const editor = editorRef.current
      if (!editor) return false
      const hit = chipAtCaret(editor, direction)
      if (!hit) return false
      const { chip, eatSeparator } = hit
      const after = document.createRange()
      after.setStartBefore(chip)
      after.collapse(true)
      const separator = chip.nextSibling
      if (eatSeparator && separator instanceof Text) {
        separator.deleteData(0, 1)
        if (separator.length === 0) separator.remove()
      }
      chip.remove()
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(after)
      refresh()
      emitTrigger()
      return true
    }

    function handlePaste(e: React.ClipboardEvent): void {
      const text = e.clipboardData?.getData('text/plain')
      if (text) {
        // Strip formatting — insert as plain text so the contenteditable never holds rich HTML.
        e.preventDefault()
        insertText(text)
      } else if (e.clipboardData && e.clipboardData.files.length > 0) {
        // Image/file paste: block the contenteditable's default insert; ChatPanel's document-level
        // paste listener still fires (preventDefault ≠ stopPropagation) and handles the upload.
        e.preventDefault()
      }
    }

    function handleClick(e: React.MouseEvent): void {
      const chip = (e.target as HTMLElement).closest<HTMLElement>('.mention-chip')
      if (chip && onChipClick) {
        e.preventDefault()
        onChipClick({
          type: chip.dataset.chipType ?? '',
          ref: chip.dataset.chipRef ?? '',
          label: chip.dataset.chipLabel ?? '',
          chipEl: chip,
        })
      }
    }

    // Honour autoFocus: focus the editor after mount, after the DOM is committed.
    useEffect(() => {
      if (autoFocus) {
        requestAnimationFrame(() => editorRef.current?.focus())
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // Mobile path (owner report): a touch keyboard routes Backspace through the IME —
    // keydown carries `key: 'Unidentified'` (keyCode 229) — so handleKeyDown never matches
    // and the browser's native delete just steps the caret over the non-editable chip.
    // `beforeinput` names the intent (inputType) on every keyboard and is cancelable, so it
    // is the reliable place to eat the chip. Attached natively: React's onBeforeInput is a
    // keypress/composition polyfill that never fires for deletions.
    useEffect(() => {
      const editor = editorRef.current
      if (!editor) return
      function onBeforeInput(e: Event) {
        const inputType = (e as InputEvent).inputType
        if (inputType !== 'deleteContentBackward' && inputType !== 'deleteContentForward') return
        if (deleteAdjacentChip(inputType === 'deleteContentBackward' ? 'backward' : 'forward')) {
          e.preventDefault()
        }
      }
      editor.addEventListener('beforeinput', onBeforeInput)
      return () => editor.removeEventListener('beforeinput', onBeforeInput)
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // Re-detect the trigger when the caret moves by click/arrow (not just on input). selectionchange
    // is a document event; guard to selections inside this editor.
    useEffect(() => {
      function onSelectionChange() {
        const editor = editorRef.current
        const sel = window.getSelection()
        if (!editor || !sel || !sel.focusNode || !editor.contains(sel.focusNode)) return
        emitTrigger()
      }
      document.addEventListener('selectionchange', onSelectionChange)
      return () => document.removeEventListener('selectionchange', onSelectionChange)
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    useImperativeHandle(ref, () => ({
      focus() {
        const editor = editorRef.current
        if (!editor) return
        editor.focus()
        placeCaretAtEnd(editor)
      },
      blur() {
        editorRef.current?.blur()
      },
      clear() {
        const editor = editorRef.current
        if (!editor) return
        editor.replaceChildren()
        refresh()
        cb.current.onTrigger(null)
      },
      setText(text: string) {
        const editor = editorRef.current
        if (!editor) return
        editor.replaceChildren()
        if (text) editor.appendChild(document.createTextNode(text))
        refresh()
        cb.current.onTrigger(null)
      },
      appendText(text: string, opts?: InsertOpts) {
        const editor = editorRef.current
        if (!editor || !text) return
        editor.appendChild(document.createTextNode(text))
        if (opts?.focus ?? true) placeCaretAtEnd(editor)
        refresh()
        cb.current.onTrigger(null)
      },
      insertTrigger(mode: '@' | '/') {
        const editor = editorRef.current
        if (!editor) return
        editor.focus()
        // If focus didn't land inside the editor (e.g. the @ button stole it), drop the caret
        // at the end so the char is inserted somewhere sensible.
        const sel = window.getSelection()
        if (!sel || !sel.focusNode || !editor.contains(sel.focusNode)) placeCaretAtEnd(editor)
        // The trigger char must sit at a word boundary or detectAtToken/detectSlashToken won't fire.
        const s = window.getSelection()
        let needSpace = false
        if (s && s.focusNode && s.focusNode.nodeType === Node.TEXT_NODE) {
          const before = (s.focusNode.textContent ?? '').slice(0, s.focusOffset)
          needSpace = before.length > 0 && !/[\s ]$/.test(before)
        } else {
          const txt = editor.textContent ?? ''
          needSpace = txt.length > 0 && !/[\s ]$/.test(txt)
        }
        // insertText() inserts at the caret AND re-runs trigger detection → onTrigger fires →
        // the parent opens the Autocomplete with an empty query.
        insertText((needSpace ? ' ' : '') + mode)
      },
      insertChip,
      removeActiveTrigger() {
        if (deleteTriggerFragment()) {
          refresh()
          cb.current.onTrigger(null)
        }
      },
      serialize() {
        const editor = editorRef.current
        return editor ? serializeSegments(readSegments(editor)) : { text: '', mentions: [] }
      },
      isEmpty() {
        const editor = editorRef.current
        return editor ? editorIsEmpty(editor) : true
      },
    }))

    return (
      <div
        ref={editorRef}
        className="chat-composer is-empty"
        data-placeholder={placeholder ?? ''}
        role="textbox"
        aria-multiline="true"
        aria-label="Message"
        contentEditable={!disabled}
        suppressContentEditableWarning
        onInput={() => {
          refresh()
          emitTrigger()
        }}
        onKeyDown={handleKeyDown}
        // Caret moves without an input event, so the trigger has to be recomputed here
        // too — otherwise arrowing out of an "@token" leaves the picker armed and it eats
        // the next Enter.
        onKeyUp={(e) => {
          if (CARET_KEYS.has(e.key)) {
            emitTrigger()
            if (editorRef.current) scrollCaretIntoView(editorRef.current)
          }
        }}
        onPaste={handlePaste}
        onClick={handleClick}
        // Prevent browser-native drop-text insertion into the contenteditable.
        // Without this, dropping a [data-entity-id] element (which sets text/plain
        // in its dataTransfer) causes the browser to inject the ref string as a
        // text node before any JS handler can stop it, leaving a stray newline.
        // The document-level drop handler in dragRightClick.tsx handles the actual
        // chip insertion via the addMention path.
        onDrop={(e) => e.preventDefault()}
      />
    )
  },
)
