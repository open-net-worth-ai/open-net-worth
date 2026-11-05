/**
 * screenContract.ts — Pure DOM reader for the data-ctx-* markup contract.
 *
 * Ported from prototype `mentions/app.jsx` getScreenSnapshot (lines 58–167)
 * and the handles/facts/prompts registry (data.jsx lines 412–439).
 *
 * This module is framework-agnostic: no React imports, no window.MData global.
 * All registry state lives in module-level variables (a TS-module singleton).
 */

import type { ScreenHandle, ScreenSnapshot, ScreenSnapshotWire } from './screenSnapshotWire'
import { serializeSnapshot } from './screenSnapshotWire'
import { buildCommandManifest } from './screenCommands'
import { parseCtxFields as _parseCtxFields } from './objectFields'
import { sanitizeUntrusted } from './security/fence'
import { pluralize } from '../utils/pluralize'

// ── Re-export the types we extend / consume ─────────────────────────────────
export type { ScreenHandle, ScreenSnapshot, ScreenSnapshotWire }

/**
 * Re-export parseCtxFields from objectFields so callers that import from
 * screenContract can share the identical parse with the popover layer.
 * Single source: edit objectFields.ts — both sites update.
 */
export { parseCtxFields } from './objectFields'

// ── Snapshot result shape ────────────────────────────────────────────────────
export interface ScreenSnapshotResult {
  title: string
  facts: [string, string][]
  raw: string
  handles: Pick<ScreenHandle, 'id' | 'type' | 'label' | 'descriptor' | 'source'>[]
}

// ── Module-level registry (replaces window.MData equivalents) ────────────────

/** Per-screen handles: deep sources, charts, off-screen tabs. */
let _handles: ScreenHandle[] = []

/**
 * Ambient handles: layered on by the shell for things the user deliberately
 * hid (e.g. collapsed nav) so they stay fetchable without a surface owning them.
 */
let _ambientHandles: ScreenHandle[] = []

/** Computed-facts hook: fn(visibleEntities) → [[label, value], …]. */
type ScreenFactsFn = (visibleIds: Set<string>) => [string, string][]
const _factsStack: ScreenFactsFn[] = []

/** Per-screen starter prompts: fn({title, modal}) → string[]. */
type ScreenPromptsFn = (ctx: { title: string; modal: string }) => string[]
const _promptsStack: ScreenPromptsFn[] = []

// ── Registry API ─────────────────────────────────────────────────────────────

export function setHandles(handles: ScreenHandle[]): void {
  _handles = Array.isArray(handles) ? handles : []
}

export function setAmbientHandles(handles: ScreenHandle[]): void {
  _ambientHandles = Array.isArray(handles) ? handles : []
}

export function allHandles(): ScreenHandle[] {
  return [..._ambientHandles, ..._handles]
}

export function handleById(id: string): ScreenHandle | undefined {
  return _ambientHandles.find((h) => h.id === id) ?? _handles.find((h) => h.id === id)
}

/** Replace every registration with this one (or none). */
export function setScreenFacts(fn: ScreenFactsFn | null): void {
  _factsStack.length = 0
  if (typeof fn === 'function') _factsStack.push(fn)
}

export function setScreenPrompts(fn: ScreenPromptsFn | null): void {
  _promptsStack.length = 0
  if (typeof fn === 'function') _promptsStack.push(fn)
}

// Registrations STACK: the newest speaks, and removing it hands the floor back to the one
// beneath. A single slot cleared by whoever left last meant a Plan sub-tab that merely
// stopped being visible took every other sub-tab's facts with it.
function stackRegistration<T>(stack: T[], fn: T): () => void {
  stack.push(fn)
  return () => {
    const i = stack.lastIndexOf(fn)
    if (i >= 0) stack.splice(i, 1)
  }
}

/** Add a computed-facts hook; returns its removal. */
export function registerScreenFacts(fn: ScreenFactsFn): () => void {
  return stackRegistration(_factsStack, fn)
}

/** Add a screen-prompts hook; returns its removal. */
export function registerScreenPrompts(fn: ScreenPromptsFn): () => void {
  return stackRegistration(_promptsStack, fn)
}

// ── DOM helpers ──────────────────────────────────────────────────────────────

function clean(s: string | null | undefined): string {
  return (s ?? '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim()
}

/** innerText (layout-aware) with textContent fallback for non-browser environments (jsdom). */
function innerTextOf(el: Element): string {
  return (el as HTMLElement).innerText ?? el.textContent ?? ''
}

/**
 * Collect visible text from a region while skipping opted-out subtrees
 * ([data-ctx-ignore], [role=tooltip], [role=menu]). Used for the VISIBLE TEXT
 * fallback so ignored decorative elements don't leak into the snapshot.
 */
function filteredText(root: Element): string {
  const parts: string[] = []
  function walk(node: Node): void {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? ''
      if (text.trim()) parts.push(text)
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return
    const el = node as Element
    // Skip opted-out subtrees entirely
    if (el.matches(CTX_IGNORED_SELECTOR)) return
    for (const child of Array.from(el.childNodes)) walk(child)
  }
  walk(root)
  return parts.join(' ')
}

/**
 * Guidance surfaces: first-visit intro modals (`.fi-modal`) and the page tour's bubble layer
 * (`.pt-layer`). They explain a screen with EXAMPLE figures (a sample portfolio, a sample
 * budget), so reading one as "what the user is looking at" hands Lucius numbers that are not
 * the user's — he once reported "Cash on hand S$4,124" to someone holding S$2,150
 * (2026-10-14). A surface that must never be read joins this list; the matching test renders
 * the real components, so renaming a class here fails loudly rather than leaking silently.
 */
const GUIDE_SURFACES = '.fi-modal, .pt-layer, [data-ctx-guide]'

/** Everything a snapshot reader skips: opted-out, secret, transient popups and guidance. */
export const CTX_IGNORED_SELECTOR = `[data-ctx-ignore], [data-ctx-secret], [role="tooltip"], [role="menu"], ${GUIDE_SURFACES}`

/** True when the element (or an ancestor) is opted out of context.
 *  data-ctx-secret excludes credential/secret regions from the WHOLE snapshot. */
function isIgnored(el: Element): boolean {
  return !!el.closest(CTX_IGNORED_SELECTOR)
}

/** Row id: prefer data-ctx-id, fall back to data-entity-id. */
function rowId(el: Element): string | null {
  return el.getAttribute('data-ctx-id') ?? el.getAttribute('data-entity-id')
}

/**
 * Serialize an element's on-screen fields. Uses `data-ctx-fields` JSON when
 * present; falls back to the element's visible innerText (stripping decorative
 * prefix chars). No central type table — each row declares its own fields.
 *
 * Uses the shared parseCtxFields from objectFields.ts so the snapshot and
 * the chip-tap popover parse data-ctx-fields identically.
 */
function fieldStr(el: HTMLElement): string {
  const f = _parseCtxFields(el.getAttribute('data-ctx-fields'))
  if (f) {
    // Sanitize at ingress: every key and value entering the snapshot is defanged
    // so downstream consumers (popover, recents, send-time wrap) never see raw
    // hostile text from a crafted data-ctx-fields attribute.
    return Object.entries(f)
      .map(([k, v]) => `${sanitizeUntrusted(k, 60)}=${sanitizeUntrusted(String(v), 140)}`)
      .join(' | ')
  }
  return sanitizeUntrusted(
    clean(innerTextOf(el))
      .replace(/\n/g, ' ')
      .replace(/^[○◯✓✔✗••\s]+/, '')
      .trim(),
    1600,
  )
}

type CountByType = Record<string, number>

function byType(els: Element[]): CountByType {
  const c: CountByType = {}
  els.forEach((el) => {
    const t = el.getAttribute('data-ctx-type') ?? 'item'
    c[t] = (c[t] ?? 0) + 1
  })
  return c
}

function fmtCounts(c: CountByType): string {
  return Object.entries(c)
    .map(([t, n]) => `${n} ${pluralize(t, n)}`)
    .join(' • ')
}

// SECURITY: a control's VALUE must never reach the model if it could hold a
// credential or secret — passwords, API keys, bearer tokens, OTPs, connector
// configs. The snapshot ships form values to the LLM every turn, so this is a
// hard skip (defense-in-depth: also mark secret regions with data-ctx-secret /
// data-ctx-ignore). Matches field type, autocomplete hint, and name/id/label.
const _SECRET_RE =
  /pass|secret|token|api[-_]?key|otp|credential|\bcvv\b|\bpin\b|private[-_]?key|seed[-_]?phrase|mnemonic/i
function isSecretControl(el: Element, label: string): boolean {
  const input = el as HTMLInputElement
  if (input.type === 'password') return true
  const ac = (el.getAttribute('autocomplete') ?? '').toLowerCase()
  if (ac === 'current-password' || ac === 'new-password' || ac === 'one-time-code') return true
  if (el.closest('[data-ctx-secret]')) return true
  return _SECRET_RE.test(`${input.name ?? ''} ${input.id ?? ''} ${label}`)
}

/**
 * Harvest visible form control values from a content region.
 * Reads input/select/textarea/[role=switch]/[role=checkbox] with a resolvable
 * label; hidden, disabled, ignored, and SECRET-bearing controls are skipped.
 */
function harvestForm(root: Element): string[] {
  const out: string[] = []
  const controls = root.querySelectorAll(
    'input, select, textarea, [role="switch"], [role="checkbox"]',
  )
  controls.forEach((el) => {
    const input = el as HTMLInputElement
    if (input.type === 'hidden' || input.type === 'file' || input.disabled) return
    if (isIgnored(el)) return
    // Skip credential/secret controls before reading any value (password fields,
    // API-key/token inputs, even when revealed as type="text"). See isSecretControl.
    if (isSecretControl(el, '')) return
    // Skip controls that are not rendered (display:none / visibility:hidden).
    // Use getComputedStyle rather than offsetParent so this works in jsdom too.
    const cs = getComputedStyle(el as HTMLElement)
    if (cs.display === 'none' || cs.visibility === 'hidden') return

    let label = ''
    if (input.id) {
      try {
        const escapedId = CSS.escape(input.id)
        const labelEl = root.querySelector(`label[for="${escapedId}"]`)
        if (labelEl) label = clean(labelEl.textContent)
      } catch {
        const labelEl = root.querySelector(`label[for="${input.id}"]`)
        if (labelEl) label = clean(labelEl.textContent)
      }
    }
    if (!label) label = el.getAttribute('aria-label') ?? ''
    if (!label) {
      const parentLabel = el.closest('label')
      if (parentLabel) label = clean(parentLabel.textContent)
    }
    label = (label || el.getAttribute('name') || '').slice(0, 40)
    if (!label) return
    // Re-check with the resolved label (a secret-named control may only reveal
    // itself through its label text, e.g. "API Key").
    if (isSecretControl(el, label)) return

    let value: string
    const role = el.getAttribute('role')
    if (role === 'switch' || role === 'checkbox' || input.type === 'checkbox') {
      value = el.getAttribute('aria-checked') === 'true' || input.checked ? 'on' : 'off'
    } else if (el.tagName === 'SELECT') {
      const select = el as HTMLSelectElement
      value =
        select.options[select.selectedIndex] != null
          ? clean(select.options[select.selectedIndex]!.text)
          : ''
    } else if (input.value) {
      value = input.value
    } else if (input.placeholder) {
      value = `(default: ${input.placeholder})`
    } else {
      value = '(empty)'
    }
    // Sanitize at ingress — form values from the DOM are untrusted.
    out.push(`${sanitizeUntrusted(label, 60)} = ${sanitizeUntrusted(value, 140)}`)
  })
  return out
}

// ── Core: getScreenSnapshot ──────────────────────────────────────────────────

/**
 * Capture a two-layer snapshot of the current DOM state.
 *
 * @param visibleIds - Set of entity ids currently in the viewport (from
 *   useScreenContract's scroll/resize scan). Rows in this set → VISIBLE NOW;
 *   rows in the DOM but not in the set → ALSO ON THIS SCREEN.
 */
export function getScreenSnapshot(visibleIds?: Set<string>): ScreenSnapshotResult {
  const visSet = visibleIds ?? new Set<string>()

  // ── Title ─────────────────────────────────────────────────────────────────
  // Chain: (1) [data-ctx-title] inside [data-ctx-region="content"] (screen's own title),
  // (2) any [data-ctx-title] on the page, (3) [data-ctx-screen] on the route wrapper,
  // (4) bare fallback.
  const contentRegionEl = document.querySelector('[data-ctx-region="content"]')
  const contentTitleEl = contentRegionEl?.querySelector('[data-ctx-title]') ?? null
  const anyTitleEl = document.querySelector('[data-ctx-title]')
  const screenEl = document.querySelector('[data-ctx-screen]')
  const titleEl = contentTitleEl ?? anyTitleEl
  const title = titleEl
    ? titleEl.getAttribute('data-ctx-title') || clean(titleEl.textContent)
    : screenEl?.getAttribute('data-ctx-screen') || 'this screen'

  // ── Content / nav regions ─────────────────────────────────────────────────
  let contentEls: Element[] = [...document.querySelectorAll('[data-ctx-region="content"]')]
  // When no screen has registered a [data-ctx-region="content"], use the route-level
  // wrapper ([data-ctx-screen]) so uninstrumented screens still capture visible text.
  if (contentEls.length === 0 && screenEl) contentEls = [screenEl]
  const navEls: Element[] = [...document.querySelectorAll('[data-ctx-region="nav"]')]
  const navLabel = navEls[0]?.getAttribute('data-ctx-region-label') ?? 'NAV'

  // ── Modal occlusion ───────────────────────────────────────────────────────
  // An open dialog is what the user is focused on; page behind is occluded.
  // Treat the dialog as the primary content region on top.
  // Dialogs stack in document order (each portals to the end of <body>): the last is on top.
  const openDialogs = [...document.querySelectorAll('[data-ctx-modal], [role="dialog"]')]
    .filter((el) => !isIgnored(el))
    .reverse()
  const modalEl = openDialogs[0] ?? null
  contentEls = [...openDialogs, ...contentEls]

  // ── View state ────────────────────────────────────────────────────────────
  const states: string[] = [...document.querySelectorAll('[data-ctx-state]')]
    .filter((el) => !isIgnored(el))
    .map((el) => el.getAttribute('data-ctx-state') || clean(el.textContent))
    .filter(Boolean) as string[]

  // Auto-capture active tabs inside content regions (no per-screen wiring).
  contentEls.forEach((region) => {
    region.querySelectorAll('[role="tab"][aria-selected="true"]').forEach((t) => {
      const list = t.closest('[role="tablist"]')
      const groupLabel = list?.getAttribute('aria-label') ?? 'Tab'
      const v = `${groupLabel}: ${clean(t.textContent)}`
      if (clean(t.textContent) && !states.includes(v)) states.push(v)
    })
  })

  // ── Row classification helpers ────────────────────────────────────────────
  const regionOf = (el: Element): 'content' | 'nav' | null => {
    if (contentEls.some((r) => r.contains(el))) return 'content'
    if (navEls.some((r) => r.contains(el))) return 'nav'
    return null
  }
  const isSeen = (el: Element): boolean => {
    const id = rowId(el)
    return id !== null && visSet.has(id)
  }

  // All context rows in the DOM for the current view, in document order.
  let rowEls = [...document.querySelectorAll('[data-ctx], [data-entity-id]')].filter(
    (el) => regionOf(el) !== null && !isIgnored(el),
  )

  // When a detail dialog is open over the list (e.g. the transaction drawer), the SAME entity
  // is on screen twice — once in the dialog, once in its row behind. Drop the background dupe
  // so Lucius doesn't report the focused item as two ("appears to be a duplicate display").
  if (modalEl) {
    const modalIds = new Set(
      [...modalEl.querySelectorAll('[data-entity-id]')]
        .map((el) => el.getAttribute('data-entity-id'))
        .filter((id): id is string => !!id),
    )
    rowEls = rowEls.filter((el) => {
      const id = el.getAttribute('data-entity-id')
      return !(id && modalIds.has(id) && !modalEl.contains(el))
    })
  }

  const contentRows = rowEls.filter((el) => regionOf(el) === 'content')
  const navRows = rowEls.filter((el) => regionOf(el) === 'nav')
  const cView = contentRows.filter(isSeen)
  const cOff = contentRows.filter((el) => !isSeen(el))
  const nView = navRows.filter(isSeen)
  const nOff = navRows.filter((el) => !isSeen(el))

  // ── Facts ─────────────────────────────────────────────────────────────────
  const facts: [string, string][] = [['Screen', title]]

  document.querySelectorAll('[data-ctx-fact]').forEach((el) => {
    if (isIgnored(el)) return
    const k = el.getAttribute('data-ctx-fact')
    const v = clean(el.textContent)
    // Sanitize at ingress — fact keys and values come from the DOM.
    if (k && v) facts.push([sanitizeUntrusted(k, 60), sanitizeUntrusted(v, 140)])
  })

  const inViewStr = fmtCounts(byType([...cView, ...nView]))
  if (inViewStr) facts.push(['In view', inViewStr])
  if (states.length) facts.push(['Viewing', states.join(' • ')])

  if (modalEl) {
    const modalTitleEl = modalEl.querySelector('[data-ctx-title]')
    const modalTitle = clean(
      modalTitleEl ? modalTitleEl.textContent : (modalEl.getAttribute('aria-label') ?? 'modal'),
    )
    facts.push(['Open dialog', modalTitle])
  }

  // Computed-facts hook: surfaces can register a fn to add derived facts.
  const screenFacts = _factsStack[_factsStack.length - 1]
  if (screenFacts) {
    const visibleIdsArg = new Set(
      [...cView, ...nView].map((el) => rowId(el)).filter((id): id is string => id !== null),
    )
    try {
      const extra = screenFacts(visibleIdsArg)
      if (Array.isArray(extra)) extra.forEach((p) => facts.push(p))
    } catch {
      // ignore
    }
  }

  const offStr = fmtCounts(byType([...cOff, ...nOff]))
  if (offStr) facts.push(['Also on screen', offStr])

  // Dedupe by label (a value may be declared in more than one place).
  const seenF = new Set<string>()
  const dedupFacts = facts.filter(([k]) => {
    const lk = (k ?? '').toLowerCase()
    if (seenF.has(lk)) return false
    seenF.add(lk)
    return true
  })

  // ── Two-layer body ────────────────────────────────────────────────────────
  let raw = ''

  if (states.length) {
    raw +=
      "VIEW STATE — the lens on this screen. The visible rows below are filtered / sorted / scoped by this, so do NOT treat them as the user's full data:\n" +
      states.map((s) => `- ${s}`).join('\n')
  }

  if (modalEl) {
    raw +=
      (raw ? '\n\n' : '') +
      'A DIALOG IS OPEN — this is what the user is focused on right now; the rest of the screen is behind it.'
    // Here, ahead of any rows: a list behind a dialog must not push out what the dialog says.
    const dialogText = clean(openDialogs.map((el) => filteredText(el)).join('\n')).slice(0, 1600)
    if (dialogText) raw += `\nThe dialog says (top dialog first):\n${dialogText}`
  }

  if (cView.length) {
    raw +=
      (raw ? '\n\n' : '') +
      'VISIBLE NOW — the rows the user is actually looking at, top-to-bottom (row 1 = topmost). ' +
      '"first / top / the Nth <thing>" = the topmost row of that kind in THIS list; count every row incl. ' +
      'salary, interest, transfers and pending — never skip one. Each carries its id + on-screen fields:\n' +
      cView.map((el, i) => `${i + 1}. ${fieldStr(el as HTMLElement)}`).join('\n')
  }

  if (nView.length) {
    raw +=
      (raw ? '\n\n' : '') +
      `${navLabel} (visible):\n` +
      nView.map((el) => `- ${fieldStr(el as HTMLElement)}`).join('\n')
  }

  if (cOff.length) {
    raw +=
      (raw ? '\n\n' : '') +
      'ALSO ON THIS SCREEN — same view but scrolled out of sight or behind the assistant panel ' +
      '(the user is NOT looking at these now). Use for "all / how many / summarize / find", NOT for "first / top / this":\n' +
      cOff.map((el) => `- ${fieldStr(el as HTMLElement)}`).join('\n')
  }

  if (nOff.length) {
    raw +=
      (raw ? '\n\n' : '') +
      `${navLabel} (off-screen / behind panel):\n` +
      nOff.map((el) => `- ${fieldStr(el as HTMLElement)}`).join('\n')
  }

  // Form-harvest: input/select/textarea/switch values aren't in innerText.
  const formLines = [...new Set(contentEls.flatMap((e) => harvestForm(e)))]
  if (formLines.length) {
    raw +=
      (raw ? '\n\n' : '') +
      'FORM / CONTROLS ON SCREEN (the live values of inputs, sliders, toggles and dropdowns the user can see and change here):\n' +
      formLines.map((l) => `- ${l}`).join('\n')
  }

  // Non-list surfaces (e.g. asset detail): value lives in rendered text, not rows.
  if (contentRows.length < 3 && contentEls.length > 0) {
    // Collect visible text while excluding opted-out subtrees. We walk the
    // content region's text nodes rather than innerText of the whole element,
    // so [data-ctx-ignore]/[role=tooltip]/[role=menu] subtrees are excluded.
    const visibleText = contentEls
      .filter((region) => !openDialogs.includes(region))
      .map((region) => filteredText(region))
      .join('\n')
    if (visibleText) {
      raw +=
        (raw ? '\n\n' : '') +
        'VISIBLE TEXT (everything rendered on this screen — labels, figures, % deltas and chrome that are not @-mentionable):\n' +
        clean(visibleText).slice(0, 1600)
    }
  }

  raw = raw.slice(0, 3200)

  // ── Handles ───────────────────────────────────────────────────────────────
  // `source` MUST survive — it's the only thing get_screen_detail can hydrate from. Dropping it
  // here made every on-demand handle reach the backend source-less → the hydrator echoed the
  // descriptor as placeholder text instead of the real series/holdings/article.
  const handles = allHandles().map(({ id, type, label, descriptor, source }) => ({
    id,
    type,
    label,
    descriptor,
    source,
  }))

  return { title, facts: dedupFacts, raw, handles }
}

// ── Build a ScreenSnapshot compatible with state/screenSnapshot.tsx ──────────

/**
 * Build a full ScreenSnapshot from a result + an explicit screen id.
 * Facts are converted from the flat [[label, value]] pairs to a Record.
 */
export function buildScreenSnapshot(id: string, result: ScreenSnapshotResult): ScreenSnapshot {
  const facts: Record<string, string> = {}
  result.facts.forEach(([k, v]) => {
    facts[k] = v
  })
  const handles: ScreenHandle[] = result.handles.map((h) => ({
    id: h.id,
    type: h.type,
    label: h.label,
    descriptor: h.descriptor,
    source: h.source,
  }))
  return {
    id,
    title: result.title,
    facts,
    visibleText: result.raw,
    handles,
    commands: buildCommandManifest(),
    asOf: new Date().toISOString(),
  }
}

/** Convenience: capture + serialize to wire shape in one call. */
export function captureAndSerialize(
  screenId: string,
  visibleIds: Set<string>,
  pinnedHandleIds: string[] = [],
): ScreenSnapshotWire {
  const result = getScreenSnapshot(visibleIds)
  const snapshot = buildScreenSnapshot(screenId, result)
  return serializeSnapshot(snapshot, pinnedHandleIds)
}

// ── screenSuggestions ────────────────────────────────────────────────────────

/**
 * Return screen-aware starter prompts. Each screen registers its own via
 * setScreenPrompts(fn); falls back to generic prompts when not registered.
 */
export function screenSuggestions(): string[] {
  // Same chain as getScreenSnapshot: content-scoped title → any title → route screen name.
  const contentRegionEl2 = document.querySelector('[data-ctx-region="content"]')
  const contentTitleEl2 = contentRegionEl2?.querySelector('[data-ctx-title]') ?? null
  const anyTitleEl2 = document.querySelector('[data-ctx-title]')
  const screenEl2 = document.querySelector('[data-ctx-screen]')
  const titleEl2 = contentTitleEl2 ?? anyTitleEl2
  const title = titleEl2
    ? (titleEl2.getAttribute('data-ctx-title') ?? titleEl2.textContent?.trim() ?? 'this screen')
    : screenEl2?.getAttribute('data-ctx-screen') || 'this screen'

  const modal =
    [...document.querySelectorAll('[data-ctx-modal], [role="dialog"]')].find(
      (el) => !isIgnored(el),
    ) ?? null
  let modalTitle = ''
  if (modal) {
    const modalTitleEl = modal.querySelector('[data-ctx-title]')
    if (modalTitleEl) {
      // Explicit title element inside the dialog
      modalTitle = (modalTitleEl.textContent ?? '').replace(/\s+/g, ' ').trim()
    } else {
      // Use aria-label (the dialog's accessible name) first; fall back to textContent
      modalTitle = (modal.getAttribute('aria-label') ?? modal.textContent ?? '')
        .replace(/\s+/g, ' ')
        .trim()
    }
  }

  const screenPrompts = _promptsStack[_promptsStack.length - 1]
  if (screenPrompts) {
    try {
      const r = screenPrompts({ title, modal: modalTitle })
      if (Array.isArray(r) && r.length > 0) return r
    } catch {
      // ignore
    }
  }

  if (modalTitle) {
    return [`Summarize what's in ${modalTitle}`, `Anything I should clean up in ${modalTitle}?`]
  }

  return [
    `What's important on the ${title} screen?`,
    'Summarize this screen',
    'How did my portfolio move today?',
    "What's my net-worth trend?",
  ]
}
