/**
 * TagField.tsx — search, select or create tags, the way the category picker works: chosen tags
 * sit as chips, typing filters the tags already in use (`GET /api/transactions/tags`, most used
 * first), and a new name is offered as "Create …". Used by the detail drawer's editor, the
 * register's inline Tags cell and the inline add row, so all three behave the same.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus, X } from '../../../ui/icons'
import { slugTag } from './categories'
import { scrollHiIntoView, useAnchoredFixed } from './pickers'
import { tagItems, useTransactionTags, type TagItem } from './tagOptions'

export interface TagFieldProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  autoFocus?: boolean
  /** Compact chrome for a register cell / the add row. */
  compact?: boolean
  /** Focus left the field — the inline cell commits here. Receives the final tags (a half-typed
   *  name is added first, so leaving the field never drops it). */
  onLeave?: (tags: string[]) => void
  /** Escape with the list already closed — the inline cell discards its edit. */
  onCancel?: () => void
  /** Tab / Enter-on-empty in the add row: move to the next field. */
  onAdvance?: (dir: number, fromEl: HTMLElement | null) => void
  /** Marks the input as one of the add row's tab stops. */
  addField?: boolean
}

export function TagField({
  value,
  onChange,
  placeholder = 'Add tags…',
  autoFocus,
  compact,
  onLeave,
  onCancel,
  onAdvance,
  addField,
}: TagFieldProps) {
  const known = useTransactionTags()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  // onLeave fires once: Enter can leave, and the unmount that follows may also blur.
  const left = useRef(false)
  const leave = (tags: string[]) => {
    if (left.current || !onLeave) return
    left.current = true
    onLeave(tags)
  }
  const items = useMemo(() => tagItems(known, value, query), [known, value, query])
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 260)

  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])

  const add = (tag: string): string[] => {
    const t = slugTag(tag)
    if (!t || value.includes(t)) return value
    const next = [...value, t]
    onChange(next)
    return next
  }
  /** Adds whatever is half-typed, returning the resulting tag list. */
  const flush = (): string[] => {
    if (!slugTag(query)) return value
    setQuery('')
    return add(query)
  }
  const remove = (tag: string) => onChange(value.filter((t) => t !== tag))
  const choose = (it: TagItem) => {
    add(it.tag)
    setQuery('')
    setHi(0)
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) setOpen(true)
      else setHi((h) => Math.min(h + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (open && query && items[hi]) choose(items[hi])
      else if (query) flush()
      else if (onAdvance) onAdvance(1, inputRef.current)
      else leave(value)
    } else if (e.key === ',') {
      e.preventDefault()
      flush()
    } else if (e.key === 'Backspace' && !query && value.length) {
      e.preventDefault()
      remove(value[value.length - 1])
    } else if (e.key === 'Tab' && onAdvance) {
      e.preventDefault()
      flush()
      setOpen(false)
      onAdvance(e.shiftKey ? -1 : 1, inputRef.current)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      if (open) setOpen(false)
      else onCancel?.()
    }
  }

  return (
    <div
      className={'acct-tagf' + (compact ? ' compact' : '')}
      ref={ref}
      onClick={(e) => {
        e.stopPropagation()
        inputRef.current?.focus()
      }}
      onBlur={(e) => {
        if (ref.current?.contains(e.relatedTarget as Node | null)) return
        if (!e.currentTarget.isConnected) return
        setOpen(false)
        leave(flush())
      }}
    >
      {value.map((tag) => (
        <span key={tag} className="acct-detail-tag acct-tagf-chip">
          {tag}
          <button
            type="button"
            className="acct-tagf-x"
            tabIndex={-1}
            aria-label={`Remove tag ${tag}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => {
              e.stopPropagation()
              remove(tag)
            }}
          >
            <X size={10} aria-hidden="true" />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        className="acct-tagf-in"
        data-addfield={addField ? '' : undefined}
        aria-label="Tags"
        autoFocus={autoFocus}
        placeholder={value.length ? '' : placeholder}
        value={query}
        onFocus={() => {
          setOpen(true)
          setHi(0)
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          setHi(0)
        }}
        onKeyDown={onKey}
      />
      {open && (items.length > 0 || known.length === 0) && (
        <div className="acct-payf-pop acct-tagf-pop" style={fx ?? undefined}>
          <div className="acct-payf-scroll" ref={popRef} role="listbox" aria-label="Tags">
            <div className="acct-payf-h">
              {items.length === 1 && items[0].type === 'create' ? 'New tag' : 'Tags'}
            </div>
            {items.length === 0 && (
              <div className="acct-payf-empty">No tags yet — type one to create it</div>
            )}
            {items.map((it, i) => (
              <button
                key={it.type + it.tag}
                type="button"
                role="option"
                aria-selected={i === hi}
                tabIndex={-1}
                className={
                  'acct-payf-item' +
                  (i === hi ? ' hi' : '') +
                  (it.type === 'create' ? ' create' : '')
                }
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(it)
                }}
              >
                {it.type === 'create' ? (
                  <>
                    <Plus className="acct-payf-plus" size={12} aria-hidden="true" />
                    <span>
                      Create "<b>{it.tag}</b>"
                    </span>
                  </>
                ) : (
                  it.tag
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
