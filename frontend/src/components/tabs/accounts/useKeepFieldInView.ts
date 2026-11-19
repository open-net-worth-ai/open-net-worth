import { useCallback, useRef } from 'react'

const isTextField = (el: Element | null): el is HTMLElement =>
  !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')

/**
 * Keeps the field you are typing in visible inside a scroll container. The keyboard shrinks the
 * container AFTER the field takes focus, so the browser's own scroll-on-focus ran against the
 * old height and left the review card's tag search under the footer (owner report 2026-11-20).
 * Re-scrolls when the container is resized while a field inside it has focus — no keyboard
 * detection, no timings: it reacts to the size the browser actually gave the container.
 */
export function useKeepFieldInView<T extends HTMLElement>(): (el: T | null) => void {
  const cleanup = useRef<(() => void) | null>(null)
  return useCallback((el: T | null) => {
    cleanup.current?.()
    cleanup.current = null
    if (!el || typeof ResizeObserver === 'undefined') return
    const reveal = () => {
      const active = document.activeElement
      if (isTextField(active) && el.contains(active)) active.scrollIntoView?.({ block: 'start' })
    }
    const observer = new ResizeObserver(reveal)
    observer.observe(el)
    cleanup.current = () => observer.disconnect()
  }, [])
}
