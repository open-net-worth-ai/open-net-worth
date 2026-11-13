import { useCallback, useEffect, useRef, useState } from 'react'
import type { FocusEvent, ReactNode } from 'react'

// The controls every Copy Trading toolbar uses, and nothing else: Investments' own (its
// search, its dropdown and its filter pills, the same classes), so the two screens match and
// these three screens can never drift apart from each other.

interface Chip<T extends string> {
  value: T
  label: ReactNode
  /** Shown beside the label; a filter with nothing in it shows 0, it never disappears, so the
   *  row never changes width under the reader. */
  count?: number
}

// Which sides of a scrolling chip row still hide chips ('start', 'end', 'both' or ''), so the
// row can fade on that side instead of slicing a chip in half (owner, 2026-11-14).
function useScrollEdges() {
  const ref = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState('')
  const update = useCallback(() => {
    const el = ref.current
    if (!el) return
    const start = el.scrollLeft > 1
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
    setEdges(start && end ? 'both' : start ? 'start' : end ? 'end' : '')
  }, [])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [update])
  return { ref, edges, update }
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Chip<T>[]
  value: T
  onChange: (v: T) => void
  label: string
}) {
  const { ref, edges, update } = useScrollEdges()
  return (
    <div
      ref={ref}
      className="mk-chips fol-chips"
      role="tablist"
      aria-label={label}
      data-more={edges || undefined}
      onScroll={update}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={value === o.value}
          className={`mk-chip${value === o.value ? ' is-on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count != null && <span className="mk-chip-n"> {o.count}</span>}
        </button>
      ))}
    </div>
  )
}

// A phone's keyboard shrinks the viewport AFTER the field takes focus, and the app scrolls an
// inner pane the browser does not bring the field back into, so the field ended up hidden behind
// the keyboard (owner, 2026-11-14). Once the visible area has resized, centre the field in it.
function revealAboveKeyboard(e: FocusEvent<HTMLInputElement>) {
  const field = e.currentTarget
  const viewport = window.visualViewport
  if (!viewport) return
  viewport.addEventListener('resize', () => field.scrollIntoView({ block: 'center' }), {
    once: true,
  })
}

export function SearchBox({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  label: string
  placeholder: string
}) {
  return (
    <label className="mk-search-wrap">
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        aria-hidden="true"
      >
        <circle cx="7" cy="7" r="4.5" />
        <path d="m10.4 10.4 3.1 3.1" strokeLinecap="round" />
      </svg>
      <input
        className="mk-search"
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={revealAboveKeyboard}
      />
    </label>
  )
}

export function ToolSelect<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  label: string
  className?: string
}) {
  return (
    <select
      className={`mk-toolsel${className ? ` ${className}` : ''}`}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}
