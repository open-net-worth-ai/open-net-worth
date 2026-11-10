import { useSyncExternalStore } from 'react'

/** The element for a target. A screen can carry a hidden twin of a control (a closed side
 *  panel keeps its own copy, slid out sideways), so a match off to the side never counts; nor
 *  does one inside a collapsed Lucius dock, which is a bar, not Lucius open beside you.
 *  `reach: 'screen'` also wants it in view now; `'page'` takes one further up or down the page,
 *  which a tour scrolls to (a phone shows less of a page at once). */
export function findTarget(
  target: string,
  reach: 'screen' | 'page' = 'screen',
): HTMLElement | null {
  // A target is a data-tour id, or a raw selector (starting "[") for "any element like this".
  const selector = target.startsWith('[') ? target : `[data-tour="${target}"]`
  for (const el of document.querySelectorAll<HTMLElement>(selector)) {
    const r = el.getBoundingClientRect()
    const shown = r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth
    const inView = r.bottom > 0 && r.top < innerHeight
    if (shown && (reach === 'page' || inView) && !el.closest('.lx-collapsed')) return el
  }
  return null
}

/** The part of `el` a person can actually see: its box cut to every scrolling or clipping
 *  ancestor and to the window. A register row wider than its sideways-scrolling table was
 *  ringed past the table's edge and off the screen. */
export function visibleRect(el: HTMLElement): DOMRect {
  const r = el.getBoundingClientRect()
  let left = r.left
  let top = r.top
  let right = r.right
  let bottom = r.bottom
  const clips = (v: string) => v === 'hidden' || v === 'auto' || v === 'scroll' || v === 'clip'
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    const s = getComputedStyle(a)
    const x = clips(s.overflowX)
    const y = clips(s.overflowY)
    if (!x && !y) continue
    const c = a.getBoundingClientRect()
    if (x) {
      left = Math.max(left, c.left)
      right = Math.min(right, c.right)
    }
    if (y) {
      top = Math.max(top, c.top)
      bottom = Math.min(bottom, c.bottom)
    }
  }
  left = Math.max(left, 0)
  top = Math.max(top, 0)
  right = Math.min(right, window.innerWidth)
  bottom = Math.min(bottom, window.innerHeight)
  return new DOMRect(left, top, Math.max(0, right - left), Math.max(0, bottom - top))
}

/** First-run setup is on screen. The app is rendered underneath it (the Lucius home among it),
 *  so without this a guide started behind setup and lit up a control the user couldn't see. */
export function inFirstRun(): boolean {
  return window.location.pathname.startsWith('/setup')
}

const DIALOGS = '[role="dialog"][aria-modal="true"], .modal-overlay'

/** A modal is up: a tour waits rather than stack a second overlay on it. */
export function modalOpen(): boolean {
  return !!document.querySelector(DIALOGS)
}

/** A modal OTHER than a feature intro's own (`.fi-modal`) is up — what an intro already on
 *  screen checks, since it is a dialog itself. */
export function otherModalOpen(): boolean {
  return Array.from(document.querySelectorAll(DIALOGS)).some(
    (el) => !el.matches('.fi-modal') && !el.querySelector('.fi-modal') && !el.closest('.fi-modal'),
  )
}

// One guide per visit, of any kind: once an intro, tour or tip has shown, every other one waits,
// still unseen, for a later visit. Persona QA (2026-10-13) met up to six in eight minutes —
// paperclip guide, Restore tip, then an intro on every screen opened — and read each new one
// as the app getting in the way of what they came to do (owner, 2026-10-14).
let guided = false
export function markGuided(): void {
  guided = true
}
export function guidedThisVisit(): boolean {
  return guided
}
/** Tests only: a new visit. */
export function resetGuidedForTests(): void {
  guided = false
}

// One guide on screen at a time: an intro waits while a tour or tip is showing (tips already
// wait for an open modal). A tip that started first used to end up under a screen intro.
let toursUp = 0
const tourListeners = new Set<() => void>()
const notifyTours = () => tourListeners.forEach((l) => l())

/** Marks a tour as showing; call the returned function when it closes. */
export function tourShowing(): () => void {
  toursUp += 1
  notifyTours()
  return () => {
    toursUp -= 1
    notifyTours()
  }
}

/** Whether a tour or tip is on screen right now. */
export function useTourShowing(): boolean {
  return useSyncExternalStore(
    (l) => {
      tourListeners.add(l)
      return () => tourListeners.delete(l)
    },
    () => toursUp > 0,
  )
}
