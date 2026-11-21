// The app's own dropdown for the Rule editor, in place of the browser's <select> list: a field that
// opens a popover anchored under it (the ledger's picker chrome). A long list (categories, payees,
// accounts) takes typing to filter; a short one (all / any, an operator) is a plain button.
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Plus } from '../../../ui/icons'
import { scrollHiIntoView, useAnchoredFixed } from '../accounts/pickers'

const SEARCH_FROM = 8

export function ComboSelect({
  id,
  value,
  options,
  placeholder,
  needed,
  onChange,
  onCreate,
}: {
  id: string
  value: string
  options: ReadonlyArray<readonly [string, string]>
  placeholder?: string
  needed?: boolean
  onChange: (value: string) => void
  /** Offer "Create “…”" for a typed name nothing matches; resolves to the new id. */
  onCreate?: (name: string) => Promise<string | null>
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  // A name made here shows at once, before the list it came from is fetched again.
  const [made, setMade] = useState<Array<readonly [string, string]>>([])
  const [error, setError] = useState<string | null>(null)
  const all = [...options, ...made.filter(([key]) => !options.some(([k]) => k === key))]
  const searchable = Boolean(onCreate) || all.length >= SEARCH_FROM
  const ql = q.trim().toLowerCase()
  const items = all.filter(([, label]) => label.toLowerCase().includes(ql))
  const canCreate =
    Boolean(onCreate) && ql !== '' && !all.some(([, label]) => label.toLowerCase() === ql)
  const selected = all.find(([key]) => key === value)?.[1] ?? ''
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
  async function create() {
    const name = q.trim()
    close()
    setError(null)
    try {
      const created = await onCreate?.(name)
      if (!created) return
      setMade((current) => [...current, [created, name] as const])
      onChange(created)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not create it.')
    }
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
      setHi((h) => Math.min(h + 1, items.length - (canCreate ? 0 : 1)))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const item = items[hi]
      if (item) choose(item[0])
      else if (canCreate) void create()
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
            {canCreate && (
              <button
                type="button"
                role="option"
                aria-selected={false}
                className={`acct-payf-item rl-combo-create${hi === items.length ? ' hi' : ''}`}
                onMouseEnter={() => setHi(items.length)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  void create()
                }}
              >
                <Plus size={12} aria-hidden="true" /> Create “{q.trim()}”
              </button>
            )}
            {items.length === 0 && !canCreate && <div className="acct-payf-empty">No matches</div>}
          </div>
        </div>
      )}
      {error && (
        <span className="a3-inline-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}

/** A link-style button opening a short menu that floats above the page, so a scrolling
 *  dialog body never clips it (an absolutely placed menu inside one was cut off). */
export function AnchoredMenu({
  label,
  trigger,
  items,
}: {
  label: string
  trigger: ReactNode
  items: Array<{ label: string; onSelect: () => void }>
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 220)
  useEffect(() => {
    if (!open) return
    const onDoc = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  return (
    <div className="rl-anchored" ref={ref}>
      <button
        type="button"
        className="a3-linkbtn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => event.key === 'Escape' && setOpen(false)}
      >
        {trigger}
      </button>
      {open && (
        <div
          className="acct-payf-pop rl-menu-pop"
          style={fx ?? undefined}
          role="menu"
          aria-label={label}
        >
          <div className="acct-payf-scroll">
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className="acct-payf-item"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setOpen(false)
                  item.onSelect()
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
