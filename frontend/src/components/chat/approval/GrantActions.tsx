import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { GRANT_SCOPE_LABELS, type GrantScope } from '../../../api/toolPermissions'
import { ChevronDown } from '../../../ui/icons'

const ALWAYS_NOTE = 'every future call, any amount • change in Settings'
// Inside an Automation run, Always allow joins that Automation's own list, not the global table.
const ALWAYS_NOTE_IN_AUTOMATION = 'every future run of this Automation • change on its page'

// The row needs this much width for [Deny] … [Allow once] [Always allow] on one line.
const WIDE_MIN_PX = 300

/** A permission card's buttons: Deny on the left, the two allow scopes grouped on the right
 * (owner, 2026-10-12 — Deny is a real outlined button, not faint text).
 * With room, both scopes are their own buttons; on a narrow card (the side panel) they group
 * into one [Allow once ▾] split button with "Always allow" behind the caret — the safe,
 * ordinary choice stays the obvious one either way (owner, 2026-10-11). Width is measured on
 * the row itself, so the same card adapts between the side panel, the dock and the Lucius
 * tab. Where it can't be measured (no ResizeObserver) it stays grouped. */
export function GrantActions({
  busy,
  disabled,
  onOnce,
  onAlways,
  onDeny,
  inAutomation = false,
}: {
  busy: GrantScope | null
  disabled: boolean
  onOnce: () => void
  onAlways: () => void
  onDeny: () => void
  inAutomation?: boolean
}) {
  const alwaysNote = inAutomation ? ALWAYS_NOTE_IN_AUTOMATION : ALWAYS_NOTE
  const rowRef = useRef<HTMLDivElement | null>(null)
  const wide = useFitsWidth(rowRef, WIDE_MIN_PX)
  const locked = busy !== null || disabled
  return (
    <div className="change-preview-actions change-actions-wrap" ref={rowRef}>
      <button
        className="change-discard-btn change-grant-btn--deny"
        disabled={busy !== null}
        onClick={onDeny}
      >
        Deny
      </button>
      <div className="change-grant-allow">
        {wide ? (
          <>
            <button
              className="change-confirm-btn change-grant-btn change-grant-btn--once"
              disabled={locked}
              onClick={onOnce}
            >
              {busy === 'once' ? 'Saving…' : GRANT_SCOPE_LABELS.once}
            </button>
            <button
              className="change-confirm-btn change-grant-btn change-grant-btn--always"
              disabled={locked}
              title={alwaysNote}
              onClick={onAlways}
            >
              {busy === 'always' ? 'Saving…' : GRANT_SCOPE_LABELS.always}
            </button>
          </>
        ) : (
          <GrantSplit
            busy={busy}
            locked={locked}
            alwaysNote={alwaysNote}
            onOnce={onOnce}
            onAlways={onAlways}
          />
        )}
      </div>
    </div>
  )
}

/** [Allow once ▾] — "Always allow" behind the caret, with its standing-scope caveat ON the
 * item (owner, 2026-07-21: shown under an "Allow once" default it read as if allow-once
 * applied forever). */
function GrantSplit({
  busy,
  locked,
  alwaysNote,
  onOnce,
  onAlways,
}: {
  busy: GrantScope | null
  locked: boolean
  alwaysNote: string
  onOnce: () => void
  onAlways: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  // Closes on outside click and Escape — a permission menu that stays open after you look
  // away is a menu you can mis-click.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="grant-split" ref={ref}>
      <button
        className="change-confirm-btn change-grant-btn change-grant-btn--once"
        disabled={locked}
        onClick={onOnce}
      >
        {/* The in-flight label lives on the visible button, not the menu item that started
            it: the menu closes on click, so a "Saving…" inside it would vanish at once. */}
        {busy ? 'Saving…' : GRANT_SCOPE_LABELS.once}
      </button>
      <button
        className="change-grant-btn grant-split__caret"
        disabled={locked}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Standing permission"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown size={12} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div className="grant-split__menu grant-split__menu--above" role="menu">
          <button
            role="menuitem"
            className="grant-split__item grant-split__item--always"
            disabled={locked}
            onClick={() => {
              setOpen(false)
              onAlways()
            }}
          >
            <span className="grant-split__item-label">{GRANT_SCOPE_LABELS.always}</span>
            <span className="grant-split__item-note">{alwaysNote}</span>
          </button>
        </div>
      )}
    </div>
  )
}

function useFitsWidth(ref: React.RefObject<HTMLElement | null>, minWidth: number): boolean {
  const [fits, setFits] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setFits(entry.contentRect.width >= minWidth)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref, minWidth])
  return fits
}
