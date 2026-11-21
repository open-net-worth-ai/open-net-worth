// The app's own dropdown for the Rule editor, in place of the browser's <select> list. A long
// list (categories, payees, accounts) is chips plus a search box: a single choice shows as one
// chip and lets go of focus once made; `multiple` keeps the list open to add more, each a chip
// with ×. A short list (all / any, an operator) is a plain button.
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Plus } from '../../../ui/icons'
import { Tag } from '../../ui'
import { scrollHiIntoView, useAnchoredFixed } from '../accounts/pickers'

const SEARCH_FROM = 10

type Option = readonly [string, string]

export function ComboSelect({
  id,
  value,
  options,
  placeholder,
  needed,
  multiple,
  onChange,
  onChangeMany,
  onCreate,
}: {
  id: string
  /** One id, or every chosen id when `multiple`. */
  value: string | string[]
  options: ReadonlyArray<Option>
  placeholder?: string
  needed?: boolean
  multiple?: boolean
  onChange?: (value: string) => void
  /** With `multiple`: every chosen id, in the order picked. */
  onChangeMany?: (values: string[]) => void
  /** Offer "Create “…”" for a typed name nothing matches; resolves to the new id. */
  onCreate?: (name: string) => Promise<string | null>
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  // A name made here shows at once, before the list it came from is fetched again.
  const [made, setMade] = useState<Option[]>([])
  const [error, setError] = useState<string | null>(null)
  const all = [...options, ...made.filter(([key]) => !options.some(([k]) => k === key))]
  const chosen = (Array.isArray(value) ? value : value ? [value] : []).filter(Boolean)
  const searchable = Boolean(multiple || onCreate) || all.length >= SEARCH_FROM
  const ql = q.trim().toLowerCase()
  const items = all.filter(
    ([key, label]) => label.toLowerCase().includes(ql) && !(multiple && chosen.includes(key)),
  )
  const canCreate =
    Boolean(onCreate) && ql !== '' && !all.some(([, label]) => label.toLowerCase() === ql)
  const labelOf = (key: string) => all.find(([k]) => k === key)?.[1] ?? key
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 280)
  const emit = (next: string[]) => (multiple ? onChangeMany?.(next) : onChange?.(next[0] ?? ''))

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
    setHi(0)
  }
  function choose(key: string) {
    if (multiple) {
      emit([...chosen, key])
      setQ('')
      setHi(0)
      return // the list stays open, and focus stays in the box, to add another
    }
    emit([key])
    close()
    inputRef.current?.blur() // a single choice is done once made
  }
  async function create() {
    const name = q.trim()
    setError(null)
    try {
      const created = await onCreate?.(name)
      if (!created) return
      // A value that is just the name normalized (a tag's slug) is shown as stored.
      const shown = created.toLowerCase() === name.toLowerCase() ? created : name
      setMade((current) => [...current, [created, shown] as const])
      choose(created)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not create it.')
    }
  }
  function onKey(event: KeyboardEvent) {
    if (!open) {
      if (['ArrowDown', 'Enter'].includes(event.key) || (!searchable && event.key === ' ')) {
        event.preventDefault()
        openAt()
      }
      if (searchable && event.key === 'Backspace' && q === '' && chosen.length) {
        emit(chosen.slice(0, -1))
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
    } else if (event.key === 'Backspace' && q === '' && chosen.length && multiple) {
      emit(chosen.slice(0, -1))
    }
  }

  const common = {
    id,
    role: 'combobox' as const,
    'aria-expanded': open,
    'aria-controls': `${id}-list`,
    onKeyDown: onKey,
  }
  const single = chosen[0] ? labelOf(chosen[0]) : ''
  return (
    <div className={`acct-payf rl-combo${needed ? ' needed' : ''}`} ref={ref}>
      {searchable ? (
        <div
          className={`rl-combo-field rl-combo-chips${open ? ' open' : ''}`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              event.preventDefault()
              inputRef.current?.focus()
            }
          }}
        >
          {/* While searching for a single value, the old chip makes way for the box. */}
          {!(open && !multiple) &&
            chosen.map((key) => (
              <Tag
                key={key}
                removeLabel={`Remove ${labelOf(key)}`}
                onRemove={() => emit(chosen.filter((k) => k !== key))}
              >
                {labelOf(key)}
              </Tag>
            ))}
          <input
            {...common}
            ref={inputRef}
            className="rl-combo-input"
            placeholder={
              chosen.length && !(open && !multiple)
                ? ''
                : open && single
                  ? `Search — now ${single}`
                  : placeholder
            }
            value={q}
            onFocus={openAt}
            onClick={() => !open && openAt()}
            onChange={(event) => {
              setQ(event.target.value)
              setOpen(true)
              setHi(0)
            }}
          />
        </div>
      ) : (
        <button
          {...common}
          type="button"
          className={`rl-combo-field rl-combo-btn${single ? '' : ' empty'}`}
          onClick={() => (open ? close() : openAt())}
        >
          {single || placeholder}
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
                aria-selected={chosen.includes(key)}
                className={`acct-payf-item${index === hi ? ' hi' : ''}${chosen.includes(key) ? ' on' : ''}`}
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
            {items.length === 0 && !canCreate && (
              <div className="acct-payf-empty">
                {multiple && chosen.length && !ql ? 'All chosen' : 'No matches'}
              </div>
            )}
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
