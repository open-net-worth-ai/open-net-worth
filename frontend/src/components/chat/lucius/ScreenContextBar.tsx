// ScreenContextBar.tsx — "<recipient> can see this screen" row + toggle + Peek.
//
// Ports prototype app.jsx ScreenContextBar (lines 551-631).
// CSS classes from lucius.css: .lx-scope, .lx-scope-row, .lx-scope-ic, .lx-scope-txt,
// .lx-scope-sub, .lx-scope-peek, .lx-switch, .lx-switch-knob, .lx-scope-attached,
// .lx-handle-chip, .lx-scope-peekbox, .lx-scope-peek-h, .lx-scope-fact, .lx-scope-raw,
// .lx-handle-list, .lx-handle, .lx-handle-ic, .lx-handle-main, .lx-handle-l,
// .lx-handle-badge, .lx-handle-d, .lx-handle-act.
//
// Reads useScreenSnapshot() internally — hosts pass only visibleIds / layout props.

import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { createPortal } from 'react-dom'
import { Eye, EyeOff, Check, Plus } from '../../../ui/icons'
import { getScreenSnapshot } from '../../../lucius/screenContract'
import { hSvg } from '../../../lucius/identity'
import { useScreenSnapshot } from '../../../state/screenSnapshot'

const PEEK_VIEWPORT_EDGE = 4
const PEEK_ANCHOR_GAP = 6
const PEEK_MAX_HEIGHT = 244

export interface ScreenContextBarProps {
  visibleIds: Set<string>
  /** Slim inline variant for the dock's one-row toolbar: icon + Peek + switch only
   *  (title/counts fold into the tooltip; pinned chips fold into the Peek panel). */
  compact?: boolean
  /** Collapse the whole bar's Peek control (button + panel) — when the autocomplete is open. */
  collapsed?: boolean
  /** The user is composing a draft — auto-close the Peek PANEL but KEEP the button visible so
   *  they can re-open it; gives the draft room without losing the affordance. */
  composing?: boolean
  /** Whether the owning composer is visible. Deactivation closes any portaled Peek. */
  active?: boolean
  /** Label of the current recipient agent — shown as "{name} can see this screen". */
  recipientName?: string
}

export function ScreenContextBar({
  visibleIds,
  compact,
  collapsed,
  composing,
  active = true,
}: ScreenContextBarProps) {
  const { enabled, setEnabled, pinnedHandleIds, togglePin } = useScreenSnapshot()
  const [peek, setPeek] = useState(false)
  const [tick, setTick] = useState(0)
  const [peekPosition, setPeekPosition] = useState<{
    top: number
    left: number
    width: number
    maxHeight: number
  } | null>(null)
  const anchorRef = useRef<HTMLDivElement>(null)
  const peekRef = useRef<HTMLDivElement>(null)
  const peekTriggerRef = useRef<HTMLButtonElement>(null)
  const switchRef = useRef<HTMLButtonElement>(null)
  const peekId = useId()
  const peekHeadingId = useId()

  // Auto-close the Peek preview when a draft APPEARS (first char, or a staged chip with no
  // keystroke — the false→true edge of `composing`). The button stays (it's gated on `collapsed`,
  // not composing), so the user can re-open Peek. Deactivating the owning host closes the
  // body portal and resets it so it cannot reappear when that host becomes active again.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- close on host/composing edges.
    if (composing || !active) setPeek(false)
  }, [active, composing])

  // ...and close it on EVERY composer keystroke (start OR continue typing) — the edge effect above
  // can't see continued typing once a draft already exists — and on Escape. setPeek(false) no-ops
  // when already closed, so re-opening Peek while paused works; the next keystroke/Esc closes it.
  // The dock's own Esc handler defers while the Peek box is mounted, so Esc closes Peek first and
  // only collapses the dock on a subsequent press (layered Esc).
  useEffect(() => {
    const onInput = (e: Event) => {
      const t = e.target as Element | null
      if (t?.closest?.('.chat-composer, .lx-compose')) setPeek(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || !peekRef.current) return
      const restoreFocus = !!peekRef.current?.contains(e.target as Node)
      e.preventDefault()
      e.stopImmediatePropagation()
      setPeek(false)
      if (restoreFocus) peekTriggerRef.current?.focus()
    }
    document.addEventListener('input', onInput, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('input', onInput, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [])

  // (a) Cheap discrete refresh — always on. rAF-debounces bursts so it's safe to
  // keep mounted. Only fires on user actions (scroll, input, lx-rescan), not continuously.
  useEffect(() => {
    let raf: number
    const bump = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => setTick((t) => t + 1))
    }
    const onEvt = (e: Event) => {
      const target = e.target as Element | null
      if (target?.closest?.('.lx-dock, .lx-side, .lx-compose, .ac')) return
      bump()
    }
    window.addEventListener('lx-rescan', bump)
    document.addEventListener('input', onEvt, true)
    document.addEventListener('change', onEvt, true)
    // A screen with sub-tabs keeps every pane mounted and only moves its title attribute
    // (Plan: "Plan — Budget" → "Plan — Outlook"), so no scroll, input or rescan fires and the
    // header kept naming the tab the user had left. Filtered to that one attribute: it is rare.
    const titles = new MutationObserver(bump)
    titles.observe(document.body, {
      subtree: true,
      attributes: true,
      attributeFilter: ['data-ctx-title'],
    })
    return () => {
      titles.disconnect()
      window.removeEventListener('lx-rescan', bump)
      document.removeEventListener('input', onEvt, true)
      document.removeEventListener('change', onEvt, true)
      cancelAnimationFrame(raf)
    }
  }, [])

  // (b) MutationObserver — only while the Peek is open (the detailed live view).
  // Trailing-debounced ~200ms so a burst (slider drag, chart redraw) collapses to one.
  useEffect(() => {
    if (!peek) return
    let t: ReturnType<typeof setTimeout>
    const bump = () => {
      clearTimeout(t)
      t = setTimeout(() => setTick((n) => n + 1), 200)
    }
    const obs = new MutationObserver(bump)
    document
      .querySelectorAll('[data-ctx-region], [role="dialog"], [data-ctx-modal]')
      .forEach((el) =>
        obs.observe(el, {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true,
          attributeFilter: [
            'value',
            'aria-checked',
            'aria-selected',
            'data-ctx-fields',
            'data-ctx-state',
          ],
        }),
      )
    return () => {
      obs.disconnect()
      clearTimeout(t)
    }
  }, [peek])

  const snap = useMemo(
    () => getScreenSnapshot(visibleIds),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [visibleIds, peek, enabled, tick],
  )

  const inView = snap.facts.find((f) => f[0] === 'In view')
  const handles = snap.handles ?? []
  const pinnedHandles = handles.filter((h) => pinnedHandleIds.includes(h.id))
  const showPeek = peek && !collapsed && active

  useLayoutEffect(() => {
    if (!showPeek) return
    let raf = 0
    let last = ''
    const track = () => {
      const anchor = anchorRef.current
      const panel = peekRef.current
      if (anchor && panel) {
        const viewport = window.visualViewport
        const position = positionPeek(
          anchor.getBoundingClientRect(),
          panel.scrollHeight || panel.offsetHeight || PEEK_MAX_HEIGHT,
          {
            top: viewport?.offsetTop ?? 0,
            left: viewport?.offsetLeft ?? 0,
            width: viewport?.width ?? window.innerWidth,
            height: viewport?.height ?? window.innerHeight,
          },
          !!compact,
        )
        const { top, left, width, maxHeight } = position
        const key = `${top},${left},${width},${maxHeight}`
        if (key !== last) {
          last = key
          setPeekPosition(position)
        }
      }
      raf = requestAnimationFrame(track)
    }
    track()
    return () => cancelAnimationFrame(raf)
  }, [compact, showPeek])

  const tooltip =
    'Screen awareness — ' +
    snap.title +
    (inView ? ' • ' + inView[1] : '') +
    (handles.length ? ' • ' + handles.length + ' sources' : '')

  const onPeekTriggerKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'Tab' || e.shiftKey || !showPeek) return
    const firstHandle = peekRef.current?.querySelector<HTMLButtonElement>('.lx-handle')
    if (!firstHandle) return
    e.preventDefault()
    firstHandle.focus()
  }

  const onPeekPanelKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab') return
    const handles = e.currentTarget.querySelectorAll<HTMLButtonElement>('.lx-handle')
    if (e.shiftKey && e.target === handles[0]) {
      e.preventDefault()
      peekTriggerRef.current?.focus()
    } else if (!e.shiftKey && e.target === handles[handles.length - 1]) {
      e.preventDefault()
      switchRef.current?.focus()
    }
  }

  return (
    <div
      ref={anchorRef}
      className={'lx-scope' + (enabled ? '' : ' off') + (compact ? ' lx-scope--compact' : '')}
      data-tour="lucius-screen"
      title={compact ? tooltip : undefined}
    >
      {compact ? (
        <div className="lx-scope-row">
          <div className={'lx-aw' + (enabled ? ' on' : '')} title={tooltip}>
            {/* The label used to BE the switch (role="switch" on the whole strip) because the
                dock row had no width for a real one — it shared that row with the cost, the
                model picker and the effort select. Those moved into the composer's toolbar
                (2026-08-06), so the switch is back and the label is just a label again: an
                on/off state deserves a control that LOOKS like one. */}
            <span className="lx-aw-toggle">
              <span className="lx-aw-dot">◉</span>
              <span className="lx-aw-label">Screen awareness</span>
              <span className="lx-aw-sub">• {snap.title}</span>
            </span>
            {!collapsed && (
              <button
                ref={peekTriggerRef}
                className={'lx-aw-peek' + (peek ? ' open' : '')}
                aria-expanded={peek}
                aria-controls={peekId}
                aria-label="Preview what's shared with Lucius"
                title="Preview exactly what's shared this turn"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => setPeek((p) => !p)}
                onKeyDown={onPeekTriggerKeyDown}
              >
                {peek ? <EyeOff size={12} /> : <Eye size={12} />}
                {peek ? 'Hide' : 'Peek'}
              </button>
            )}
            <button
              ref={switchRef}
              className={'lx-switch' + (enabled ? ' on' : '')}
              role="switch"
              aria-checked={enabled}
              aria-label={
                enabled
                  ? 'Screen awareness on — click to stop sharing this screen'
                  : 'Screen awareness off — click to share this screen'
              }
              title={enabled ? 'Using this screen as context' : 'Not using this screen as context'}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setEnabled(!enabled)}
            >
              <span className="lx-switch-knob" />
            </button>
          </div>
        </div>
      ) : (
        <div className="lx-scope-row">
          <span className="lx-scope-ic">◉</span>
          <span className="lx-scope-txt">
            <b>Screen awareness</b>
            <span className="lx-scope-sub">
              {snap.title}
              {inView ? ' • ' + inView[1] : ''}
              {handles.length ? ' • ' + handles.length + ' sources' : ''}
            </span>
          </span>
          {!collapsed && (
            <button
              ref={peekTriggerRef}
              className={'lx-scope-peek' + (peek ? ' open' : '')}
              aria-expanded={peek}
              aria-controls={peekId}
              aria-label="Preview what's shared with Lucius"
              title="Preview exactly what's shared this turn"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setPeek((p) => !p)}
              onKeyDown={onPeekTriggerKeyDown}
            >
              {/* eye (closed) + eye-off (open) — CSS shows one per .open state */}
              <svg
                className="ic-eye"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              <svg
                className="ic-off"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M2 2l20 20" />
                <path d="M6.7 6.7C4 8.5 2 12 2 12s3.5 7 10 7c2 0 3.7-.5 5.3-1.3M9.9 5.2A10.6 10.6 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-2.4 3.2M9.9 9.9a3 3 0 0 0 4.2 4.2" />
              </svg>
              {peek ? 'Hide' : 'Peek'}
            </button>
          )}
          <button
            ref={switchRef}
            className={'lx-switch' + (enabled ? ' on' : '')}
            role="switch"
            aria-checked={enabled}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setEnabled(!enabled)}
            title={enabled ? 'Using this screen as context' : 'Not using this screen as context'}
          >
            <span className="lx-switch-knob" />
          </button>
        </div>
      )}

      {pinnedHandles.length > 0 && (
        <div className="lx-scope-attached">
          <span className="lx-scope-attached-l">Pinned</span>
          {pinnedHandles.map((h) => (
            <span key={h.id} className="lx-handle-chip">
              <span className="ic" dangerouslySetInnerHTML={{ __html: hSvg(h.type) }} />
              {h.label}
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => togglePin(h.id)}
                aria-label="Unpin"
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
              </button>
            </span>
          ))}
        </div>
      )}

      {showPeek &&
        createPortal(
          <div
            ref={peekRef}
            id={peekId}
            className="lx-scope-peekbox"
            role="region"
            aria-labelledby={peekHeadingId}
            data-portal-layer
            onKeyDown={onPeekPanelKeyDown}
            style={
              peekPosition
                ? {
                    top: peekPosition.top,
                    left: peekPosition.left,
                    width: peekPosition.width,
                    maxHeight: peekPosition.maxHeight,
                  }
                : { top: 0, left: 0, visibility: 'hidden' }
            }
          >
            <div className="lx-scope-peek-h" id={peekHeadingId}>
              Visible now — passed to Lucius
            </div>
            {snap.facts.map(([k, v], i) => (
              <div className="lx-scope-fact" key={i}>
                <span className="k">{k}</span>
                <span className="v">{v}</span>
              </div>
            ))}
            <div className="lx-scope-raw">
              + the full visible text — labels, % deltas and figures that aren't @-mentionable.
            </div>

            {handles.length > 0 && (
              <>
                <div className="lx-scope-peek-h" style={{ marginTop: 9 }}>
                  Also on this screen — fetched only if needed
                </div>
                <div className="lx-scope-raw" style={{ marginTop: 0, marginBottom: 4 }}>
                  Lucius knows these exist but hasn't read them. Pin to keep across the chat,
                  @-mention for one message, or leave it to fetch on demand.
                </div>
                <div className="lx-handle-list">
                  {handles.map((h) => {
                    const pinned = pinnedHandleIds.includes(h.id)
                    return (
                      <button
                        key={h.id}
                        className={
                          'lx-handle' + (pinned ? ' on' : '') + (h.type === 'view' ? ' view' : '')
                        }
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => togglePin(h.id)}
                      >
                        <span
                          className="lx-handle-ic"
                          dangerouslySetInnerHTML={{ __html: hSvg(h.type) }}
                        />
                        <span className="lx-handle-main">
                          <span className="lx-handle-l">
                            {h.label}
                            {h.type === 'view' && <span className="lx-handle-badge">not open</span>}
                          </span>
                          <span className="lx-handle-d">{h.descriptor}</span>
                        </span>
                        <span className="lx-handle-act">
                          {pinned ? (
                            <>
                              <Check size={12} aria-hidden="true" /> pinned
                            </>
                          ) : (
                            <>
                              <Plus size={12} aria-hidden="true" /> pin
                            </>
                          )}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </>
            )}
          </div>,
          document.body,
        )}
    </div>
  )
}

function positionPeek(
  anchor: Pick<DOMRect, 'top' | 'left' | 'width'>,
  panelHeight: number,
  viewport: { top: number; left: number; width: number; height: number },
  compact: boolean,
) {
  const desiredLeft = anchor.left + (compact ? 0 : -1)
  const desiredWidth = anchor.width + (compact ? 0 : 2)
  const width = Math.min(desiredWidth, Math.max(0, viewport.width - PEEK_VIEWPORT_EDGE * 2))
  const left = Math.max(
    viewport.left + PEEK_VIEWPORT_EDGE,
    Math.min(desiredLeft, viewport.left + viewport.width - PEEK_VIEWPORT_EDGE - width),
  )
  const panelBottom = Math.min(
    anchor.top - PEEK_ANCHOR_GAP,
    viewport.top + viewport.height - PEEK_VIEWPORT_EDGE,
  )
  const maxHeight = Math.min(
    PEEK_MAX_HEIGHT,
    Math.max(0, panelBottom - viewport.top - PEEK_VIEWPORT_EDGE),
  )
  return {
    top: panelBottom - Math.min(panelHeight, maxHeight),
    left,
    width,
    maxHeight,
  }
}
