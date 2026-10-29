import { useCallback, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { usePointerResize } from '../../hooks/usePointerResize'

export const RAIL_SHARE_KEY = 'asd-rail-share'
export const RAIL_MIN = 240
export const RAIL_MAX = 560
// The chart column keeps at least half the body, however far the rail is dragged.
const RAIL_MAX_SHARE = 0.5

function readShare(): number | null {
  try {
    const saved = Number(localStorage.getItem(RAIL_SHARE_KEY) || '')
    return Number.isFinite(saved) && saved > 0 && saved <= RAIL_MAX_SHARE ? saved : null
  } catch {
    return null
  }
}

function writeShare(share: number | null) {
  try {
    if (share == null) localStorage.removeItem(RAIL_SHARE_KEY)
    else localStorage.setItem(RAIL_SHARE_KEY, String(share))
  } catch {
    // Storage blocked (private mode) — the width just isn't remembered.
  }
}

/** Drag-to-resize for the asset page's right rail. null = the automatic width (index.css's
 *  `clamp(240px, 30%, 340px)`); a number = the SHARE of `.asd-body` the user dragged to, so the
 *  rail still scales with the pane when the Lucius dock opens or closes. */
export function useRailResize() {
  const [share, setShare] = useState<number | null>(readShare)
  const shareRef = useRef(share)
  const bodyEl = useRef<HTMLDivElement | null>(null)
  const railEl = useRef<HTMLDivElement | null>(null)
  // Callback refs, so the hook's result carries no ref object a render could read.
  const bodyRef = useCallback((el: HTMLDivElement | null) => {
    bodyEl.current = el
  }, [])
  const railRef = useCallback((el: HTMLDivElement | null) => {
    railEl.current = el
  }, [])
  const drag = useRef<{ startX: number; startWidth: number } | null>(null)

  const onStart = useCallback((e: PointerEvent) => {
    e.preventDefault()
    drag.current = {
      startX: e.clientX,
      startWidth: railEl.current?.getBoundingClientRect().width ?? RAIL_MIN,
    }
    document.body.style.cursor = 'col-resize'
  }, [])
  const onMove = useCallback((e: PointerEvent) => {
    const body = bodyEl.current?.clientWidth
    if (!drag.current || !body) return
    // The handle sits on the rail's LEFT edge — dragging left widens it.
    const want = drag.current.startWidth + drag.current.startX - e.clientX
    const max = Math.min(RAIL_MAX, body * RAIL_MAX_SHARE)
    const next = Math.min(max, Math.max(RAIL_MIN, want)) / body
    shareRef.current = next
    setShare(next)
  }, [])
  const onEnd = useCallback(() => {
    drag.current = null
    document.body.style.cursor = ''
    writeShare(shareRef.current)
  }, [])
  const onPointerDown = usePointerResize({ onStart, onMove, onEnd })
  const onDoubleClick = useCallback(() => {
    shareRef.current = null
    setShare(null)
    writeShare(null)
  }, [])

  const style =
    share == null
      ? undefined
      : ({
          '--asd-rail-w': `clamp(${RAIL_MIN}px, ${(share * 100).toFixed(2)}%, ${RAIL_MAX}px)`,
        } as CSSProperties)

  return { bodyRef, railRef, style, handleProps: { onPointerDown, onDoubleClick } }
}
