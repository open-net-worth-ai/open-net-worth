import { useCallback, useEffect, useRef, useState } from 'react'

const CLOSE_GRACE_MS = 250

// Hover-peek for a collapsed sidebar: opens while the pointer is over the toggle or the floating
// panel, closes after a short grace once it leaves both, and on Esc. `enabled` is false when the
// rail is already pinned open (or on a layout without one), which also closes any open peek.
export function useSidebarPeek(enabled: boolean) {
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // A rail re-docked by anything other than close() (e.g. a width-driven auto-expand) must not
  // leave `open` latched: re-collapsing later would show the peek with no fresh hover.
  const [wasEnabled, setWasEnabled] = useState(enabled)
  if (wasEnabled !== enabled) {
    setWasEnabled(enabled)
    if (!enabled) setOpen(false)
  }

  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }, [])
  const enter = useCallback(() => {
    cancel()
    if (enabled) setOpen(true)
  }, [cancel, enabled])
  const leave = useCallback(() => {
    cancel()
    timer.current = setTimeout(() => setOpen(false), CLOSE_GRACE_MS)
  }, [cancel])
  const close = useCallback(() => {
    cancel()
    setOpen(false)
  }, [cancel])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])
  useEffect(() => cancel, [cancel])

  return { open: enabled && open, enter, leave, close }
}
