// plan/icons.tsx — shared inline-SVG icon components for the Plan tab redesign.
// Verbatim port from design_handoffs/plan_handoff/prototype/plan/budget.jsx.
// Every SVG path is reproduced exactly from the prototype.
import type { ReactNode } from 'react'
import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { LuciusMark } from '../../../ui/LuciusMark'

export function Spark({ size = 14 }: { size?: number }) {
  return <LuciusMark size={size} aria-hidden="true" />
}

export function Chevron({
  dir = 'down',
  size = 13,
}: {
  dir?: 'down' | 'right' | 'up' | 'left'
  size?: number
}) {
  const d = {
    down: 'M6 9l6 6 6-6',
    right: 'M9 6l6 6-6 6',
    up: 'M18 15l-6-6-6 6',
    left: 'M15 6l-6 6 6 6',
  }[dir]
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}

export function Arrow({ dir = 'right', size = 16 }: { dir?: 'left' | 'right'; size?: number }) {
  const d = dir === 'left' ? 'M19 12H5M11 18l-6-6 6-6' : 'M5 12h14M13 6l6 6-6 6'
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}

export function Grip() {
  return (
    <svg width="10" height="16" viewBox="0 0 10 16" fill="currentColor" aria-hidden="true">
      <circle cx="2.5" cy="3" r="1.25" />
      <circle cx="7.5" cy="3" r="1.25" />
      <circle cx="2.5" cy="8" r="1.25" />
      <circle cx="7.5" cy="8" r="1.25" />
      <circle cx="2.5" cy="13" r="1.25" />
      <circle cx="7.5" cy="13" r="1.25" />
    </svg>
  )
}

export function Dots() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="12" cy="5" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <circle cx="12" cy="19" r="1.7" />
    </svg>
  )
}

export function Plus({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

export function X({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

export function EyeOff({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c5 0 9 5 9 8a12 12 0 0 1-1.7 2.6M6.6 6.6C3.9 8.2 2 11 2 12c0 3 4 8 9 8a9 9 0 0 0 5.4-1.6M3 3l18 18M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    </svg>
  )
}

export function FolderIcon({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  )
}

export function InflowIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
    </svg>
  )
}

// Money flow (Sankey): three strands merging and splitting.
export function FlowIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 5c7 0 8 7 18 7M3 19c7 0 8-7 18-7M3 12h18" />
    </svg>
  )
}

// Hover/focus "i" info tooltip (verbatim from outlook.jsx / app.jsx — `.stat-info`/`.stat-pop`).
// `up` = tooltip above the marker (no `down` modifier); `r` = right-aligned popover.
export function Info({ text, up, r }: { text: ReactNode; up?: boolean; r?: boolean }) {
  const ref = useRef<HTMLSpanElement>(null)
  const [box, setBox] = useState<{ left: number; top: number; up: boolean } | null>(null)
  // The tooltip is PORTALED to <body> with fixed positioning so it escapes every
  // overflow:hidden ancestor (the chart card, the collapse-animation container) that used to
  // clip it; left is clamped into the viewport so it can never be cut off.
  const open = () => {
    const el = ref.current
    if (!el) return
    const rc = el.getBoundingClientRect()
    const W = 240
    let left = r ? rc.right - W : rc.left + rc.width / 2 - W / 2
    left = Math.max(10, Math.min(left, window.innerWidth - W - 10))
    const openUp = up ?? rc.top > 180 // default: open up unless near the very top
    setBox({ left, top: openUp ? rc.top - 8 : rc.bottom + 8, up: openUp })
  }
  const close = () => setBox(null)
  return (
    <span
      ref={ref}
      className="stat-info"
      tabIndex={0}
      role="button"
      aria-label="More info"
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={close}
    >
      i
      {box &&
        createPortal(
          <span
            role="tooltip"
            className="stat-pop-portal"
            style={{
              position: 'fixed',
              left: box.left,
              top: box.top,
              width: 240,
              transform: box.up ? 'translateY(-100%)' : 'none',
            }}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  )
}
