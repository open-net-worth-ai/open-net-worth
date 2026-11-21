// The app's own dropdown for the Rule editor, in place of the browser's <select> list: a field that
// opens a popover anchored under it (the ledger's picker chrome). A long list (categories, payees,
// accounts) takes typing to filter; a short one (all / any, an operator) is a plain button.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { scrollHiIntoView, useAnchoredFixed } from '../accounts/pickers'

const SEARCH_FROM = 8

export function ComboSelect({
  id,
  value,
  options,
  placeholder,
  needed,
  onChange,
}: {
  id: string
  value: string
  options: ReadonlyArray<readonly [string, string]>
  placeholder?: string
  needed?: boolean
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchable = options.length >= SEARCH_FROM
  const ql = q.trim().toLowerCase()
  const items = options.filter(([, label]) => label.toLowerCase().includes(ql))
  const selected = options.find(([key]) => key === value)?.[1] ?? ''
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 280)

  useEffect(() => {
    if (!open) return
    const onDoc = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])

  function close() {
    setOpen(false)
    setQ('')
  }
  function openAt() {
    setOpen(true)
    setQ('')
    setHi(
      Math.max(
        0,
        options.findIndex(([key]) => key === value),
      ),
    )
  }
  function choose(key: string) {
    onChange(key)
    close()
  }
  function onKey(event: KeyboardEvent) {
    if (!open) {
      if (['ArrowDown', 'Enter', ' '].includes(event.key) && !(searchable && event.key === ' ')) {
        event.preventDefault()
        openAt()
      }
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHi((h) => Math.min(h + 1, items.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const item = items[hi]
      if (item) choose(item[0])
    } else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
    }
  }

  const common = {
    id,
    role: 'combobox' as const,
    'aria-expanded': open,
    'aria-controls': `${id}-list`,
    onKeyDown: onKey,
  }
  return (
    <div className={`acct-payf rl-combo${needed ? ' needed' : ''}`} ref={ref}>
      {searchable ? (
        <input
          {...common}
          className="acct-addin rl-combo-field"
          placeholder={selected || placeholder}
          value={open ? q : selected}
          onFocus={openAt}
          onClick={() => !open && openAt()}
          onChange={(event) => {
            setQ(event.target.value)
            setOpen(true)
            setHi(0)
          }}
        />
      ) : (
        <button
          {...common}
          type="button"
          className={`rl-combo-field rl-combo-btn${selected ? '' : ' empty'}`}
          onClick={() => (open ? close() : openAt())}
        >
          {selected || placeholder}
        </button>
      )}
      <svg
        className="acct-payf-caret"
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
      {open && (
        <div
          className="acct-payf-pop rl-combo-pop"
          style={fx ? { ...fx, width: Math.max(ref.current?.offsetWidth ?? 0, 180) } : undefined}
        >
          <div className="acct-payf-scroll" ref={popRef} id={`${id}-list`} role="listbox">
            {items.map(([key, label], index) => (
              <button
                key={key}
                type="button"
                role="option"
                aria-selected={key === value}
                className={`acct-payf-item${index === hi ? ' hi' : ''}${key === value ? ' on' : ''}`}
                onMouseEnter={() => setHi(index)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  choose(key)
                }}
              >
                {label}
              </button>
            ))}
            {items.length === 0 && <div className="acct-payf-empty">No matches</div>}
          </div>
        </div>
      )}
    </div>
  )
}
