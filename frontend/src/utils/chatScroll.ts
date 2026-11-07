import { useEffect, type RefObject } from 'react'

/**
 * Returns true when the scroll container is within `threshold` pixels of its
 * bottom edge. Used by ChatPanel to decide whether to auto-scroll on new
 * content (stick-to-bottom) vs. leave the view alone (user scrolled up).
 */
export function isNearBottom(
  el: Pick<Element, 'scrollTop' | 'scrollHeight' | 'clientHeight'>,
  threshold = 80,
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

/** The message the reader's eye is on, and where it sits, while older ones load in above it. */
export interface HeldPlace {
  node: Element
  top: number
  /** The commit that finishes the load has been asked for: let go after it. */
  last: boolean
}

/** The first message showing at the top of `el`, or null with none on screen. */
export function placeOf(el: HTMLElement | null): HeldPlace | null {
  if (!el) return null
  const edge = el.getBoundingClientRect().top
  const node = [...el.querySelectorAll('.msg-bubble')].find(
    (bubble) => bubble.getBoundingClientRect().bottom > edge,
  )
  return node ? { node, top: node.getBoundingClientRect().top, last: false } : null
}

/** Puts that message back where it was. By the message, not by scrollHeight: older messages
 *  arrive in two commits (the fetched page, then the wider window), and a browser that anchors
 *  on its own has already moved the view, so by height the first went uncorrected in WebKit and
 *  the second was corrected twice in Chromium (measured, 2026-11-08). */
export function restorePlace(el: HTMLElement, held: HeldPlace): void {
  if (!held.node.isConnected) return
  const drift = held.node.getBoundingClientRect().top - held.top
  if (Math.abs(drift) >= 1) el.scrollTop += drift
  held.top = held.node.getBoundingClientRect().top
}

// How long after a scroll-down gesture landing in the "near" band re-arms the follow.
const DOWN_INTENT_MS = 600

/** What the reader's own scroll gestures have said about following the newest content. */
export interface ScrollIntent {
  /** They scrolled UP on purpose and have not come back to the bottom since. */
  readingUp: boolean
  /** When they last scrolled down (ms epoch). */
  downAt: number
}

export function newScrollIntent(): ScrollIntent {
  return { readingUp: false, downAt: 0 }
}

/**
 * Whether the transcript should keep following the newest content after a scroll event.
 *
 * Near the bottom means follow, except after the reader scrolled UP on purpose: a streaming
 * reply grows every few milliseconds and re-pins on each growth, so a gentle scroll up (a
 * trackpad, a wheel notch, an arrow key: all inside the 80px "near" band) was undone before
 * the next frame and a long reply could not be read from its top while it was still arriving
 * (persona QA 2026-10-14). A deliberate step up leaves the pin until the reader is truly back
 * at the bottom, or scrolls down into the band themselves. Direction is NOT inferred from
 * scrollTop here: content growing under the reader moves it as much as they do.
 */
export function followsBottom(
  el: Pick<Element, 'scrollTop' | 'scrollHeight' | 'clientHeight'>,
  intent: ScrollIntent,
  now = Date.now(),
): boolean {
  if (isNearBottom(el, 4)) {
    intent.readingUp = false
    return true
  }
  if (!intent.readingUp) return isNearBottom(el)
  if (isNearBottom(el) && now - intent.downAt < DOWN_INTENT_MS) {
    intent.readingUp = false
    return true
  }
  return false
}

/**
 * Watches the messages scroll container and its transcript wrapper. Whenever
 * the container's scrollHeight grows (content was added — streaming tokens,
 * the follow-up chips row, the cost/copy footer, late-loading images, etc.)
 * and the user is already near the bottom, snaps scrollTop to scrollHeight.
 *
 * This is a catch-all complement to the existing effects that key on
 * `messages.length` and `streaming`. Those cover the cases they were designed
 * for; this covers everything else that can grow the list after they fire
 * (e.g. `streaming` flips false, then suggestions + CostChip mount and push
 * the view below the fold — no React state change drives a re-render, so no
 * existing effect fires).
 *
 * Behaviour guarantees:
 * - Does NOT scroll if `isNearBottomRef.current` is false (user scrolled up).
 * - Resets naturally: `isNearBottomRef.current` is set back to true by the
 *   caller on send/conversation-switch, so the pin is re-armed for the next
 *   reply without any extra logic here.
 * - Cleans up the observer on unmount and whenever `containerRef` changes
 *   identity (conversation switch re-renders the same DOM node, but the ref
 *   object itself is stable, so cleanup really fires only on unmount).
 * - A reader's own gesture UP (wheel, finger drag, PageUp/ArrowUp/Home, the scrollbar) ends
 *   the follow at once, before the next growth can pull the view back, and stamps
 *   `scrollIntent` for `followsBottom`.
 * - `enabled: false` detaches the observers entirely — the caller's top-anchored
 *   layouts (ChatPanel's empty landing screen, where growth below the composer
 *   must NOT pull the view down) — and re-attaches with fresh height baselines
 *   when it flips back, so the first real growth afterwards still reads as growth.
 */
export function useChatScrollPin(
  containerRef: RefObject<HTMLElement | null>,
  isNearBottomRef: RefObject<boolean>,
  enabled = true,
  scrollIntent?: RefObject<ScrollIntent>,
): void {
  useEffect(() => {
    const el = containerRef.current
    if (!el || !enabled) return

    // Growth-gated: only a scrollHeight INCREASE (new content) snaps the view.
    // A same-height observer tick (style recalc, window resize without content
    // change) must not yank a user who is hovering near—but not at—the bottom.
    let lastScrollHeight = el.scrollHeight
    let lastClientHeight = el.clientHeight

    // A click inside the transcript is the READER acting on it — expanding a tool row,
    // opening a thought, unfolding an activity group. The nodes that appear are not new
    // content arriving from a turn; they are content the reader just asked to see. Snapping
    // to the bottom takes it straight back off the screen: expand a row while near the
    // bottom and you have to scroll UP to find the row you just opened (owner report,
    // 2026-08-25). The browser's own scroll anchoring already holds the position correctly
    // for this case, so the right move is to stay out of its way.
    //
    // Interaction-based rather than wired into each toggle: this covers every expandable in
    // the transcript, including ones not written yet. Keyboard counts — a row opened with
    // Enter is the same act as one opened with a mouse.
    let quietUntil = 0
    const READER_ACTED_MS = 500
    const readerActed = () => {
      quietUntil = Date.now() + READER_ACTED_MS
    }
    el.addEventListener('pointerdown', readerActed, true)
    el.addEventListener('keydown', readerActed, true)

    // The reader leaving the bottom: stop following NOW, synchronously. Waiting for the scroll
    // event is too late, the next streamed token grows the transcript first and re-pins it.
    const leaveBottom = () => {
      isNearBottomRef.current = false
      if (scrollIntent) scrollIntent.current.readingUp = true
    }
    const headingDown = () => {
      if (scrollIntent) scrollIntent.current.downAt = Date.now()
    }
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) leaveBottom()
      else if (e.deltaY > 0) headingDown()
    }
    let touchY = 0
    const onTouchStart = (e: TouchEvent) => {
      touchY = e.touches[0]?.clientY ?? 0
    }
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY ?? touchY
      if (y > touchY + 2)
        leaveBottom() // a finger moving down drags the content UP
      else if (y < touchY - 2) headingDown()
      touchY = y
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'PageUp' || e.key === 'Home' || e.key === 'ArrowUp') leaveBottom()
      else if (e.key === 'PageDown' || e.key === 'End' || e.key === 'ArrowDown') headingDown()
    }
    // A press on the container itself (not a child) is its scrollbar or padding; while it is
    // held, scroll events are the reader dragging the thumb, so being away from the bottom
    // means they put it there.
    let barHeld = false
    const onBarDown = (e: PointerEvent) => {
      barHeld = e.target === el
    }
    const onBarUp = () => {
      barHeld = false
    }
    const onScrollWhileHeld = () => {
      if (barHeld && !isNearBottom(el, 4)) leaveBottom()
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    el.addEventListener('keydown', onKey)
    el.addEventListener('pointerdown', onBarDown)
    el.addEventListener('scroll', onScrollWhileHeld, { passive: true })
    window.addEventListener('pointerup', onBarUp)
    window.addEventListener('pointercancel', onBarUp)

    const scrollToBottom = () => {
      const h = el.scrollHeight
      const ch = el.clientHeight
      const grew = h > lastScrollHeight
      // The PANE getting shorter hides the bottom just as surely as new content does, and the
      // growth-only gate could not see it: on a phone, dismissing the keyboard puts the tab bar
      // back, which takes ~57px off this container (--mb-tabbar-h returns to non-zero). Content
      // height never changed, so nothing re-pinned and the last messages sat below the fold
      // until you scrolled by hand (owner report 2026-08-06). Still gated on isNearBottom, so a
      // user who has scrolled up is never yanked.
      const shrank = ch < lastClientHeight
      // Baselines move either way: the expanded height IS the height now, so a later,
      // genuinely new message still reads as growth against it.
      lastScrollHeight = h
      lastClientHeight = ch
      if (Date.now() < quietUntil) return
      if ((!grew && !shrank) || !isNearBottomRef.current) return
      el.scrollTop = h
    }

    // MutationObserver: fires when any DOM node is added/removed inside the
    // container — proposed-change cards, follow-up suggestion chips, tool-call
    // result blocks, etc. that appear after the streaming effects have already
    // fired. The scroll container has a fixed height so a ResizeObserver on it
    // would never fire for inner content growth; MutationObserver is the right
    // primitive here.
    const mo = new MutationObserver(scrollToBottom)
    mo.observe(el, { childList: true, subtree: true })

    // ResizeObserver: the container catches viewport/panel size changes. Its first child is
    // the transcript wrapper; observing that too catches existing markdown, images and
    // content-visibility nodes settling to their real height without a child-list mutation.
    // Observing only the fixed-height container missed exactly that rehydration path.
    const ro = new ResizeObserver(scrollToBottom)
    ro.observe(el)
    if (el.firstElementChild) ro.observe(el.firstElementChild)

    return () => {
      mo.disconnect()
      ro.disconnect()
      el.removeEventListener('pointerdown', readerActed, true)
      el.removeEventListener('keydown', readerActed, true)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      el.removeEventListener('keydown', onKey)
      el.removeEventListener('pointerdown', onBarDown)
      el.removeEventListener('scroll', onScrollWhileHeld)
      window.removeEventListener('pointerup', onBarUp)
      window.removeEventListener('pointercancel', onBarUp)
    }
  }, [enabled]) // eslint-disable-line react-hooks/exhaustive-deps
}
