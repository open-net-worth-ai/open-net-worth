import { useEffect, type RefObject } from 'react'

// Swipe the full call screen down to fold it back over the chat (owner, 2026-11-06). The screen
// follows the finger, and a short or slow pull springs back, so it can't fold by accident.
const START_PX = 8
const FAR_ENOUGH_PX = 120
const FLICK_PX_PER_MS = 0.6
const LEAVE_MS = 180

export function useSwipeToMinimize(
  ref: RefObject<HTMLElement | null>,
  onMinimize: () => void,
  enabled: boolean,
) {
  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    let startX = 0
    let startY = 0
    let startedAt = 0
    let pull = 0
    let tracking = false
    let leaving = 0

    const onStart = (e: TouchEvent) => {
      // The settings sheet has its own controls and scrolling.
      const inSheet = (e.target as Element | null)?.closest?.('.lx-callsettings')
      if (e.touches.length !== 1 || inSheet) return
      const { clientX, clientY } = e.touches[0]
      startX = clientX
      startY = clientY
      startedAt = e.timeStamp
      pull = 0
      tracking = true
      el.style.transition = 'none'
    }
    const onMove = (e: TouchEvent) => {
      if (!tracking) return
      const { clientX, clientY } = e.touches[0]
      const down = clientY - startY
      // Mostly sideways is not this gesture.
      if (pull === 0 && Math.abs(clientX - startX) > Math.abs(down)) return
      pull = down > START_PX ? down : 0
      el.style.transform = pull ? `translateY(${pull}px)` : ''
    }
    const onEnd = (e: TouchEvent) => {
      if (!tracking) return
      tracking = false
      const speed = pull / Math.max(1, e.timeStamp - startedAt)
      if (pull < FAR_ENOUGH_PX && speed < FLICK_PX_PER_MS) {
        el.style.transition = 'transform 0.2s ease'
        el.style.transform = ''
        return
      }
      if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        onMinimize()
        return
      }
      el.style.transition = `transform ${LEAVE_MS}ms ease-in`
      el.style.transform = 'translateY(100%)'
      leaving = window.setTimeout(onMinimize, LEAVE_MS)
    }

    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: true })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('touchcancel', onEnd)
    return () => {
      window.clearTimeout(leaving)
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
      el.style.transition = ''
      el.style.transform = ''
    }
  }, [ref, onMinimize, enabled])
}
