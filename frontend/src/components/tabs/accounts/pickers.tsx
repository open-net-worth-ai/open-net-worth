/**
 * pickers.tsx — Inline editor primitives and rich field pickers for the
 * Accounts register. Ported from prototype/accounts/app.jsx.
 * All components are presentational (props + callbacks only, no API calls).
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { Sparkles, Plus, X, Check, ChevronLeft, ChevronRight, Settings } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import type { Account, Category } from './types'
import { TYPE_LABELS, TYPE_ORDER } from './categories'

// ── Anchored fixed-position popover ──────────────────────────────────────────
// Lifts a picker out of the register's clipping scroll container.

export function useAnchoredFixed(
  open: boolean,
  anchorRef: React.RefObject<HTMLElement | null>,
  popW?: number,
): React.CSSProperties | null {
  const inlineOnPhone = useIsMobile()
  const [style, setStyle] = useState<React.CSSProperties | null>(null)
  useEffect(() => {
    if (!open || inlineOnPhone) return
    const place = () => {
      const a = anchorRef.current
      if (!a) return
      const r = a.getBoundingClientRect()
      const vw = window.innerWidth,
        vh = window.innerHeight
      const left = Math.max(8, Math.min(r.left, vw - (popW || 300) - 8))
      const spaceBelow = vh - r.bottom
      if (spaceBelow < 300 && r.top > spaceBelow) {
        setStyle({
          position: 'fixed',
          bottom: Math.round(vh - r.top + 4),
          left: Math.round(left),
          top: 'auto',
          maxHeight: Math.round(r.top - 16),
        })
      } else {
        setStyle({
          position: 'fixed',
          top: Math.round(r.bottom + 4),
          left: Math.round(left),
          maxHeight: Math.round(vh - r.bottom - 16),
        })
      }
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, inlineOnPhone]) // eslint-disable-line react-hooks/exhaustive-deps
  return open && !inlineOnPhone ? style : null
}

// Keep the keyboard-highlighted (.hi) row visible in a scrollable popover.
export function scrollHiIntoView(pop: HTMLElement | null) {
  if (!pop) return
  const el = pop.querySelector<HTMLElement>('.hi')
  if (!el) return
  const er = el.getBoundingClientRect(),
    pr = pop.getBoundingClientRect()
  if (er.top < pr.top) pop.scrollTop -= pr.top - er.top + 6
  else if (er.bottom > pr.bottom) pop.scrollTop += er.bottom - pr.bottom + 6
}

// ── SVG icon glyphs ───────────────────────────────────────────────────────

export function IconPlus({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}
export function IconChevrons({
  dir = 'left',
  size = 13,
}: {
  dir?: 'left' | 'right'
  size?: number
}) {
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
    >
      <path d={dir === 'left' ? 'M17 6l-6 6 6 6M11 6l-6 6 6 6' : 'M7 6l6 6-6 6M13 6l6 6-6 6'} />
    </svg>
  )
}
export function IconCheck({ size = 11 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12.5l4.5 4.5L19 6.5" />
    </svg>
  )
}
export function IconMinus({ size = 11 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3.4"
      strokeLinecap="round"
    >
      <path d="M6 12h12" />
    </svg>
  )
}

// ── Provenance icon (Source column) ──────────────────────────────────────

export function SourceIcon({
  source,
  variant = 'default',
}: {
  source: string
  /** `mono`: every source in the same outlined, current-colour glyph — for the review card's
   *  source line, where the register's purple ✦ sat oddly beside the wallet and calendar
   *  glyphs (owner, 2026-09-22). The register keeps its ✦. */
  variant?: 'default' | 'mono'
}) {
  if (source === 'ai')
    return variant === 'mono' ? (
      <span className="acct-src-ic" title="Added by Lucius">
        <Sparkles size={13} aria-hidden="true" />
      </span>
    ) : (
      <span className="acct-src-ic lucius" title="Added by Lucius">
        ✦
      </span>
    )
  if (source === 'bank')
    return (
      <span className="acct-src-ic" title="Bank sync">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
          <path d="M21 3v5h-5" />
          <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
          <path d="M3 21v-5h5" />
        </svg>
      </span>
    )
  if (source === 'import')
    return (
      <span className="acct-src-ic" title="Imported (file)">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 3v12" />
          <path d="M8 11l4 4 4-4" />
          <path d="M5 21h14" />
        </svg>
      </span>
    )
  if (source === 'reconcile')
    return (
      <span className="acct-src-ic" title="Reconcile adjustment">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4 8h13l-3-3" />
          <path d="M20 16H7l3 3" />
        </svg>
      </span>
    )
  if (source === 'automation')
    return (
      <span className="acct-src-ic" title="Added by an automation">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {/* Two linked steps — a workflow — not the rayed dot, which read as a brightness
              control (owner, 2026-09-21). The same glyph the automations tab uses. */}
          <rect width="8" height="8" x="3" y="3" rx="2" />
          <path d="M7 11v4a2 2 0 0 0 2 2h4" />
          <rect width="8" height="8" x="13" y="13" rx="2" />
        </svg>
      </span>
    )
  if (source === 'agent')
    return (
      <span className="acct-src-ic agent" title="Added by an agent">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="4" y="8" width="16" height="11" rx="2.5" />
          <path d="M12 8V4.5" />
          <circle cx="12" cy="3.5" r="1" />
          <path d="M9 13v1.5M15 13v1.5" />
        </svg>
      </span>
    )
  // A flag at the start of the line — where tracking began. The Phase 2 ADR deferred this and
  // the row fell through to "Manual entry", which it has never been (ADR 2026-07-28).
  if (source === 'opening')
    return (
      <span className="acct-src-ic" title="Starting balance">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M5 21V4" />
          <path d="M5 4h11l-2 3.5L16 11H5" />
        </svg>
      </span>
    )
  return (
    <span className="acct-payee-sub" title="Manual entry">
      –
    </span>
  )
}

// ── Inline edit primitives ────────────────────────────────────────────────

export function EditText({
  value,
  type,
  align,
  onCommit,
  onCancel,
}: {
  value: string | number | null | undefined
  type?: string
  align?: 'right'
  onCommit: (v: string) => void
  onCancel: () => void
}) {
  const cancel = useRef(false)
  return (
    <input
      className={'acct-editin' + (align === 'right' ? ' r' : '')}
      type={type || 'text'}
      autoFocus
      defaultValue={value == null ? '' : String(value)}
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => {
        cancel.current ? onCancel() : onCommit(e.target.value)
      }}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') e.currentTarget.blur()
        else if (e.key === 'Escape') {
          cancel.current = true
          e.currentTarget.blur()
        }
      }}
    />
  )
}

export function EditSelect({
  value,
  options,
  onCommit,
  onCancel,
}: {
  value: string | null | undefined
  options: { value: string; label: string }[]
  onCommit: (v: string) => void
  onCancel: () => void
}) {
  return (
    <select
      className="acct-editin"
      autoFocus
      defaultValue={value ?? ''}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onCommit(e.target.value)}
      onBlur={onCancel}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') onCancel()
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

export function InlineField({
  onCancel,
  children,
}: {
  onCancel: () => void
  children: React.ReactNode
}) {
  const ref = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onCancel()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <span className="acct-inline-field" ref={ref} onClick={(e) => e.stopPropagation()}>
      {children}
    </span>
  )
}

export interface PayeeOption {
  value: string // payee display name (used for text-match filtering)
  label: string
  defaultCategory?: string | null
}

export function InlinePayee({
  value,
  payeeOptions,
  onManagePayees,
  commit,
}: {
  value: string | null
  payeeOptions: PayeeOption[]
  onManagePayees?: () => void
  commit: (patch: { payee: string; category?: string }) => void
}) {
  const [val, setVal] = useState(value || '')
  const pend = useRef<{ payee: string; cat?: string }>({ payee: value || '' })
  return (
    <AddPayeeField
      value={val}
      autoOpen
      onChange={(v, defCat) => {
        pend.current = { payee: v, cat: defCat ?? undefined }
        setVal(v)
      }}
      options={payeeOptions}
      accounts={[]}
      fromAccountId={null}
      onSelectTransfer={() => {}}
      onManagePayees={onManagePayees}
      advance={() => {
        const p = pend.current
        commit(p.cat ? { payee: p.payee, category: p.cat } : { payee: p.payee })
      }}
    />
  )
}

export function EditCombo({
  value,
  options,
  allowCreate,
  onCommit,
  onCancel,
}: {
  value: string | null | undefined
  options: { value: string; label: string; dot?: string }[]
  allowCreate?: boolean
  onCommit: (v: string) => void
  onCancel: () => void
}) {
  const [q, setQ] = useState(value == null ? '' : String(value))
  const [hi, setHi] = useState(0)
  const cancel = useRef(false)
  const ql = q.trim().toLowerCase()
  const matches = options.filter((o) => o.label.toLowerCase().includes(ql))
  const exact = options.some((o) => o.label.toLowerCase() === ql)
  const showCreate = allowCreate && ql && !exact
  const items: { type: 'opt' | 'create'; value: string; label: string; dot?: string }[] = [
    ...matches.map((o) => ({ type: 'opt' as const, ...o })),
    ...(showCreate ? [{ type: 'create' as const, value: q.trim(), label: q.trim() }] : []),
  ]
  return (
    <div className="acct-combo" onClick={(e) => e.stopPropagation()}>
      <input
        className="acct-editin"
        autoFocus
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setHi(0)
        }}
        onBlur={() =>
          setTimeout(() => {
            if (!cancel.current) onCancel()
          }, 120)
        }
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setHi((h) => Math.min(h + 1, items.length - 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setHi((h) => Math.max(h - 1, 0))
          } else if (e.key === 'Enter') {
            e.preventDefault()
            cancel.current = true
            if (items[hi]) onCommit(items[hi].value)
          } else if (e.key === 'Escape') {
            cancel.current = true
            onCancel()
          }
        }}
      />
      {items.length > 0 && (
        <div className="acct-combo-pop" role="listbox">
          {items.map((it, i) => (
            <button
              key={it.type + it.value}
              className={
                'acct-combo-item' +
                (i === hi ? ' hi' : '') +
                (it.type === 'create' ? ' create' : '')
              }
              onMouseEnter={() => setHi(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                cancel.current = true
                onCommit(it.value)
              }}
            >
              {it.type === 'create' ? (
                <>
                  <Plus className="acct-combo-plus" size={12} aria-hidden="true" />
                  Create "<b>{it.label}</b>"
                </>
              ) : (
                <>
                  {it.dot && <i className="acct-combo-dot" style={{ background: it.dot }} />}
                  {it.label}
                </>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function AdvCheck({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (c: boolean) => void
  children?: React.ReactNode
}) {
  return (
    <button
      type="button"
      className="acct-advcheck"
      aria-pressed={checked}
      onClick={() => onChange(!checked)}
    >
      <span className={'acct-cb' + (checked ? ' on' : '')}>
        <IconCheck />
      </span>
      <span>{children}</span>
    </button>
  )
}

export function FilterCombo({
  value,
  options,
  anyLabel,
  onChange,
  popW = 300,
  clearable = true,
}: {
  value: string
  options: { value: string; label: string; dot?: string }[]
  anyLabel: string
  onChange: (v: string) => void
  popW?: number
  clearable?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
        setQ('')
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  })
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])
  const ql = q.trim().toLowerCase()
  const matches = options.filter((o) => o.label.toLowerCase().includes(ql))
  const items = clearable
    ? [{ value: '', label: anyLabel, any: true as const }, ...matches]
    : (matches as typeof matches)
  const selLabel = value ? (options.find((o) => o.value === value)?.label ?? value) : ''
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, popW)
  function onKey(e: React.KeyboardEvent) {
    e.stopPropagation()
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        e.preventDefault()
        setOpen(true)
        setHi(0)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      onChange(items[hi]?.value ?? '')
      setOpen(false)
      setQ('')
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      setQ('')
    }
  }
  return (
    <div className="acct-payf acct-filtercombo" ref={ref}>
      <input
        className="acct-addin"
        placeholder={anyLabel}
        value={open ? q : selLabel}
        onFocus={() => {
          setOpen(true)
          setQ('')
          setHi(0)
        }}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
          setHi(0)
        }}
        onKeyDown={onKey}
      />
      {clearable && value && !open ? (
        <button
          type="button"
          className="acct-filtercombo-clear"
          aria-label="Clear"
          onMouseDown={(e) => {
            e.preventDefault()
            onChange('')
          }}
        >
          <X size={12} aria-hidden="true" />
        </button>
      ) : (
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
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      )}
      {open && (
        <div className="acct-payf-pop" style={fx ?? undefined}>
          <div className="acct-payf-scroll" ref={popRef}>
            {items.map((it, i) => (
              <button
                key={(it as { any?: boolean }).any ? '__any' : it.value}
                className={
                  'acct-payf-item' +
                  (i === hi ? ' hi' : '') +
                  ((it as { any?: boolean }).any ? ' any' : '')
                }
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  // stopPropagation, not just preventDefault: a click here still bubbles to
                  // `document`, where an ancestor popover using this combo may have its own
                  // outside-click listener that would otherwise close it too.
                  e.preventDefault()
                  e.stopPropagation()
                  onChange(it.value)
                  setOpen(false)
                  setQ('')
                }}
              >
                {(it as { any?: boolean }).any ? (
                  anyLabel
                ) : (
                  <>
                    {(it as { dot?: string }).dot && (
                      <i
                        className="acct-payf-cdot"
                        style={{ background: (it as { dot?: string }).dot }}
                      />
                    )}
                    {it.label}
                  </>
                )}
              </button>
            ))}
            {matches.length === 0 && <div className="acct-payf-empty">No matches</div>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Rich pickers (grouped account, date, payee, category) ─────────────────

export interface AddAccountSelectProps {
  accounts: Account[]
  value: string
  onChange: (id: string) => void
  advance: (dir: number, fromEl?: HTMLElement | null) => void
  autoFocus?: boolean
  autoOpen?: boolean
}

export function AddAccountSelect({
  accounts,
  value,
  onChange,
  advance,
  autoFocus,
  autoOpen,
}: AddAccountSelectProps) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const ql = q.trim().toLowerCase()
  // Type-to-filter (like the payee field): the search box narrows the grouped list as you type.
  const grouped = TYPE_ORDER.map((g) => ({
    key: g,
    label: TYPE_LABELS[g],
    items: accounts.filter((a) => a.type === g && (!ql || a.name.toLowerCase().includes(ql))),
  })).filter((g) => g.items.length)
  const flat = grouped.flatMap((g) => g.items)
  useEffect(() => {
    if (autoOpen) {
      setOpen(true)
      setHi(0)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  })
  useEffect(() => {
    if (open) searchRef.current?.focus()
  }, [open])
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])
  const cur = accounts.find((a) => a.id === value)
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 320)
  const openMenu = () => {
    setQ('')
    setHi(0)
    setOpen(true)
  }
  const commit = (a: Account) => {
    onChange(a.id)
    setOpen(false)
    setQ('')
    requestAnimationFrame(() =>
      advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
    )
  }
  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'Enter') {
        e.preventDefault()
        advance(1)
      } else if (e.key === 'ArrowDown' || e.key === ' ') {
        e.preventDefault()
        openMenu()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, flat.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const a = flat[hi]
      if (a) commit(a)
      else advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]'))
    }
    // advance relative to THIS field's button (the search input isn't a [data-addfield], so pass it)
    else if (e.key === 'Tab') {
      e.preventDefault()
      const a = flat[hi]
      if (a) {
        onChange(a.id)
      }
      setOpen(false)
      advance(e.shiftKey ? -1 : 1, ref.current?.querySelector<HTMLElement>('[data-addfield]'))
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }
  return (
    <div className="acct-acctsel" ref={ref}>
      <button
        type="button"
        data-addfield
        autoFocus={autoFocus}
        className={'acct-addin acct-acctsel-btn' + (open ? ' open' : '')}
        onMouseDown={(e) => {
          if (open) {
            e.preventDefault()
            setOpen(false)
          }
        }}
        onClick={() => {
          if (!open) openMenu()
        }}
        onFocus={() => {
          if (!open) openMenu()
        }}
        onKeyDown={onKey}
      >
        <span className="acct-acctsel-nm">{cur ? cur.name : 'Select account'}</span>
        <svg
          className="acct-acctsel-caret"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="acct-acctsel-pop" role="listbox" ref={popRef} style={fx ?? undefined}>
          <input
            ref={searchRef}
            className="acct-sel-search"
            placeholder="Search accounts…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setHi(0)
            }}
            onKeyDown={onKey}
          />
          {grouped.length === 0 && <div className="acct-sel-empty">No accounts match “{q}”</div>}
          {grouped.map((g) => (
            <div className="acct-acctsel-grp" key={g.key}>
              <div className="acct-acctsel-h">{g.label}</div>
              {g.items.map((a) => {
                const fi = flat.indexOf(a)
                return (
                  <button
                    type="button"
                    key={a.id}
                    className={
                      'acct-acctsel-item' +
                      (a.id === value ? ' sel' : '') +
                      (fi === hi ? ' hi' : '')
                    }
                    onMouseEnter={() => setHi(fi)}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      commit(a)
                    }}
                  >
                    <span className="acct-acctsel-check">
                      {a.id === value ? <Check size={12} aria-hidden="true" /> : null}
                    </span>
                    <span className="acct-acctsel-nm">{a.name}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const MONF = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]
function ymd(d: Date): string {
  return (
    d.getFullYear() +
    '-' +
    String(d.getMonth() + 1).padStart(2, '0') +
    '-' +
    String(d.getDate()).padStart(2, '0')
  )
}

export interface AddDateFieldProps {
  value: string
  onChange: (v: string) => void
  advance: (dir: number, fromEl?: HTMLElement | null) => void
  autoFocus?: boolean
}

export function AddDateField({ value, onChange, advance, autoFocus }: AddDateFieldProps) {
  const [open, setOpen] = useState(false)
  const sel = value
    ? (() => {
        const [y = 0, m = 0, d = 0] = value.split('-').map(Number)
        return new Date(y, m - 1, d)
      })()
    : null
  const today = new Date()
  const [view, setView] = useState(() =>
    sel
      ? new Date(sel.getFullYear(), sel.getMonth(), 1)
      : new Date(today.getFullYear(), today.getMonth(), 1),
  )
  const [focusD, setFocusD] = useState(() => sel || today)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const start = sel || today
    setView(new Date(start.getFullYear(), start.getMonth(), 1))
    setFocusD(start)
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const isSame = (a: Date | null, b: Date | null) =>
    !!a &&
    !!b &&
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  const vy = view.getFullYear(),
    vm = view.getMonth()
  const first = new Date(vy, vm, 1).getDay(),
    days = new Date(vy, vm + 1, 0).getDate()
  const cells: (Date | null)[] = []
  for (let i = 0; i < first; i++) cells.push(null)
  for (let d = 1; d <= days; d++) cells.push(new Date(vy, vm, d))
  const pick = (d: Date) => {
    onChange(ymd(d))
    setOpen(false)
  }
  const moveFocusD = (delta: number) => {
    const n = new Date(focusD)
    n.setDate(n.getDate() + delta)
    setFocusD(n)
    setView(new Date(n.getFullYear(), n.getMonth(), 1))
  }
  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'Enter') {
        e.preventDefault()
        advance(1)
      } else if (e.key === 'ArrowDown' || e.key === ' ') {
        e.preventDefault()
        setOpen(true)
      }
    } else {
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        moveFocusD(-1)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        moveFocusD(1)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        moveFocusD(-7)
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        moveFocusD(7)
      } else if (e.key === 'Enter') {
        e.preventDefault()
        pick(focusD)
        advance(1)
      } else if (e.key === 'Tab') {
        e.preventDefault()
        pick(focusD)
        advance(e.shiftKey ? -1 : 1)
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setOpen(false)
      }
    }
  }
  const label = value
    ? (() => {
        const [y, m, d] = value.split('-')
        return m + '/' + d + '/' + y
      })()
    : 'Date'
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 244)
  return (
    <div className="acct-datef" ref={ref}>
      <button
        type="button"
        data-addfield
        autoFocus={autoFocus}
        className={'acct-addin acct-datef-btn' + (open ? ' open' : '')}
        onMouseDown={(e) => {
          if (open) {
            e.preventDefault()
            setOpen(false)
          }
        }}
        onClick={() => {
          if (!open) setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
      >
        <span className="acct-datef-lbl">{label}</span>
        <svg
          className="acct-datef-ic"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="acct-datef-pop" style={fx ?? undefined}>
          <div className="acct-datef-head">
            <button
              type="button"
              className="acct-datef-nav"
              onMouseDown={(e) => {
                e.preventDefault()
                setView(new Date(vy, vm - 1, 1))
              }}
              aria-label="Previous month"
            >
              <ChevronLeft size={14} aria-hidden="true" />
            </button>
            <span className="acct-datef-title">
              {MONF[vm]} {vy}
            </span>
            <button
              type="button"
              className="acct-datef-nav"
              onMouseDown={(e) => {
                e.preventDefault()
                setView(new Date(vy, vm + 1, 1))
              }}
              aria-label="Next month"
            >
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="acct-datef-dow">
            {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
              <span key={i}>{d}</span>
            ))}
          </div>
          <div className="acct-datef-grid">
            {cells.map((d, i) =>
              d ? (
                <button
                  type="button"
                  key={i}
                  className={
                    'acct-datef-day' +
                    (isSame(d, sel) ? ' sel' : '') +
                    (isSame(d, focusD) ? ' hi' : '') +
                    (isSame(d, today) ? ' today' : '')
                  }
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pick(d)
                    requestAnimationFrame(() =>
                      advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
                    )
                  }}
                >
                  {d.getDate()}
                </button>
              ) : (
                <span key={i} />
              ),
            )}
          </div>
          <div className="acct-datef-foot">
            <button
              type="button"
              className="acct-datef-link"
              onMouseDown={(e) => {
                e.preventDefault()
                pick(today)
                requestAnimationFrame(() =>
                  advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
                )
              }}
            >
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export interface AddPayeeFieldProps {
  value: string
  onChange: (v: string, defaultCategory?: string | null) => void
  options: PayeeOption[]
  advance: (dir: number, fromEl?: HTMLElement | null) => void
  onManagePayees?: () => void
  accounts: Account[]
  fromAccountId: string | null
  onSelectTransfer: (accountId: string) => void
  autoOpen?: boolean
}

export function AddPayeeField({
  value,
  onChange,
  options,
  advance,
  onManagePayees,
  accounts,
  fromAccountId,
  onSelectTransfer,
  autoOpen,
}: AddPayeeFieldProps) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!autoOpen) return
    setOpen(true)
    setHi(0)
    const el = ref.current?.querySelector<HTMLElement>('[data-addfield]')
    if (el) el.focus()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const ql = (value || '').trim().toLowerCase()
  type TItem =
    | {
        type: 'transfer'
        id: string
        label: string
        value?: undefined
        defaultCategory?: undefined
        _i: number
      }
    | {
        type: 'payee' | 'create'
        value: string
        label: string
        defaultCategory?: string | null
        id?: undefined
        color?: undefined
        _i: number
      }
  const transferMatches = accounts
    .filter((a) => a.id !== fromAccountId && ('to/from ' + a.name.toLowerCase()).includes(ql))
    .map((a) => ({
      type: 'transfer' as const,
      id: a.id,
      label: 'To/From ' + a.name,
      _i: 0,
    }))
  const payeeMatches = options
    .filter((o) => o.label.toLowerCase().includes(ql))
    .map((o) => ({
      type: 'payee' as const,
      value: o.value,
      label: o.label,
      defaultCategory: o.defaultCategory,
      _i: 0,
    }))
  const exact = options.some((o) => o.label.toLowerCase() === ql)
  const showCreate = ql && !exact && !ql.startsWith('to/from')
  const createItem: TItem[] = showCreate
    ? [{ type: 'create', value: value.trim(), label: value.trim(), _i: 0 }]
    : []
  const items: TItem[] = [...transferMatches, ...payeeMatches, ...createItem].map((it, i) => ({
    ...it,
    _i: i,
  }))
  const xfer = items.filter((it) => it.type === 'transfer')
  const pays = items.filter((it) => it.type !== 'transfer')
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  })
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])
  const choose = (it: TItem) => {
    if (it.type === 'transfer') onSelectTransfer(it.id)
    else onChange(it.value, it.defaultCategory)
    setOpen(false)
  }
  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'Enter') {
        e.preventDefault()
        advance(1)
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setOpen(true)
        setHi(0)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (items[hi]) choose(items[hi])
      requestAnimationFrame(() =>
        advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
      )
    } else if (e.key === 'Tab') {
      e.preventDefault()
      const dir = e.shiftKey ? -1 : 1
      if (items[hi]) choose(items[hi])
      requestAnimationFrame(() =>
        advance(dir, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
      )
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }
  const fxPayf = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 300)
  const renderItem = (it: TItem) => (
    <button
      key={it.type + (it.id ?? it.value) + it._i}
      className={
        'acct-payf-item' + (it._i === hi ? ' hi' : '') + (it.type === 'create' ? ' create' : '')
      }
      onMouseEnter={() => setHi(it._i)}
      onMouseDown={(e) => {
        e.preventDefault()
        choose(it)
        requestAnimationFrame(() =>
          advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
        )
      }}
    >
      {it.type === 'create' ? (
        <>
          <Plus className="acct-payf-plus" size={12} aria-hidden="true" />
          <span>
            Create "<b>{it.label}</b>"
          </span>
        </>
      ) : it.type === 'transfer' ? (
        it.label
      ) : (
        it.label
      )}
    </button>
  )
  return (
    <div className="acct-payf" ref={ref}>
      <input
        className="acct-addin"
        data-addfield
        placeholder="Payee"
        value={value}
        onFocus={() => {
          setOpen(true)
          setHi(0)
        }}
        onClick={() => {
          if (!open) {
            setOpen(true)
            setHi(0)
          }
        }}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setHi(0)
        }}
        onKeyDown={onKey}
      />
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
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
      {open && (
        <div className="acct-payf-pop" style={fxPayf ?? undefined}>
          <div className="acct-payf-scroll" ref={popRef}>
            {xfer.length > 0 && (
              <>
                <div className="acct-payf-h">Payments &amp; transfers</div>
                {xfer.map(renderItem)}
              </>
            )}
            <div className="acct-payf-h">
              {showCreate && pays.length <= 1 ? 'New payee' : 'Saved payees'}
            </div>
            {pays.length === 0 && <div className="acct-payf-empty">No saved payees</div>}
            {pays.map(renderItem)}
          </div>
          <button
            className="acct-payf-manage"
            onMouseDown={(e) => {
              e.preventDefault()
              setOpen(false)
              onManagePayees?.()
            }}
          >
            <Settings className="acct-payf-mic" size={12} aria-hidden="true" />
            Manage payees
          </button>
        </div>
      )}
    </div>
  )
}

// Group display order for the category picker — groups are now kind-driven (not hardcoded vocab).
const CAT_GRP_ORDER = [
  'Income',
  'Refund to a budget envelope',
  'Expenses',
  'Transfer',
  'Investment',
]

export interface AddCategoryFieldProps {
  value: string
  onChange: (key: string) => void
  options: (Category & { group: string })[]
  advance: (dir: number, fromEl?: HTMLElement | null) => void
  onCreate?: (prefill: string, cb: (id: string) => void) => void
  autoFocus?: boolean
  clearable?: boolean
  /** The closed button's text while nothing is picked (default "Category…"). */
  placeholder?: string
  /** Offer "+ New category" only once a name is typed — for a caller that creates straight from
   *  the search text, with no dialog of its own to ask for one. */
  createNeedsName?: boolean
}

export function AddCategoryField({
  value,
  onChange,
  options,
  advance,
  onCreate,
  autoFocus,
  clearable,
  placeholder = 'Category…',
  createNeedsName,
}: AddCategoryFieldProps) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const ql = q.trim().toLowerCase()
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  })
  useEffect(() => {
    if (open) searchRef.current?.focus()
  }, [open])
  useEffect(() => {
    if (open) scrollHiIntoView(popRef.current)
  }, [hi, open])
  const cur = options.find((o) => o.id === value)
  const groupedAll = useMemo(() => {
    const seen: Record<string, { label: string; items: typeof options }> = {}
    const out: { label: string; items: typeof options }[] = []
    options.forEach((o) => {
      const g = o.group || 'Other'
      if (!seen[g]) {
        seen[g] = { label: g, items: [] }
        out.push(seen[g])
      }
      seen[g].items.push(o)
    })
    out.sort((a, b) => CAT_GRP_ORDER.indexOf(a.label) - CAT_GRP_ORDER.indexOf(b.label))
    return out
  }, [options])
  // Type-to-filter (like the payee field).
  const grouped = ql
    ? groupedAll
        .map((g) => ({ ...g, items: g.items.filter((o) => o.label.toLowerCase().includes(ql)) }))
        .filter((g) => g.items.length)
    : groupedAll
  const flat = grouped.flatMap((g) => g.items)
  useEffect(() => {
    if (autoFocus) {
      setOpen(true)
      setHi(0)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const openMenu = () => {
    setQ('')
    setHi(0)
    setOpen(true)
  }
  const commit = (o: (typeof options)[number]) => {
    onChange(o.id)
    setOpen(false)
    setQ('')
    requestAnimationFrame(() =>
      advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]')),
    )
  }
  const fx = useAnchoredFixed(open, ref as React.RefObject<HTMLElement>, 320)
  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'Enter') {
        e.preventDefault()
        advance(1)
      } else if (e.key === 'ArrowDown' || e.key === ' ') {
        e.preventDefault()
        openMenu()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, flat.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const o = flat[hi]
      if (o) commit(o)
      else advance(1, ref.current?.querySelector<HTMLElement>('[data-addfield]'))
    }
    // advance relative to THIS field's button (the search input isn't a [data-addfield], so pass it)
    else if (e.key === 'Tab') {
      e.preventDefault()
      const o = flat[hi]
      if (o) {
        onChange(o.id)
      }
      setOpen(false)
      advance(e.shiftKey ? -1 : 1, ref.current?.querySelector<HTMLElement>('[data-addfield]'))
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }
  return (
    <div className="acct-acctsel" ref={ref}>
      <button
        type="button"
        data-addfield
        autoFocus={autoFocus}
        className={'acct-addin acct-acctsel-btn' + (open ? ' open' : '')}
        onMouseDown={(e) => {
          if (open) {
            e.preventDefault()
            setOpen(false)
          }
        }}
        onClick={() => {
          if (!open) openMenu()
        }}
        onFocus={() => {
          if (!open) openMenu()
        }}
        onKeyDown={onKey}
      >
        {cur && <i className="acct-acctsel-dot" style={{ background: cur.color }} />}
        <span className="acct-acctsel-nm">{cur ? cur.label : placeholder}</span>
        <svg
          className="acct-acctsel-caret"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="acct-acctsel-pop" role="listbox" ref={popRef} style={fx ?? undefined}>
          <input
            ref={searchRef}
            className="acct-sel-search"
            placeholder="Search categories…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setHi(0)
            }}
            onKeyDown={onKey}
          />
          {clearable && (
            <button
              className="acct-catnew-btn"
              onMouseDown={(e) => {
                e.preventDefault()
                onChange('')
                setOpen(false)
              }}
            >
              — None —
            </button>
          )}
          {onCreate && (!createNeedsName || q.trim()) && (
            <button
              className="acct-catnew-btn"
              onMouseDown={(e) => {
                e.preventDefault()
                setOpen(false)
                onCreate(q, (id) => {
                  if (id) {
                    onChange(id)
                    advance(1)
                  }
                })
              }}
            >
              <Plus className="acct-catnew-plus" size={12} aria-hidden="true" /> New category
              {q ? ` “${q}”` : ''}
            </button>
          )}
          {grouped.length === 0 && !onCreate && (
            <div className="acct-sel-empty">No categories match “{q}”</div>
          )}
          {grouped.map((g) => (
            <div className="acct-acctsel-grp" key={g.label}>
              <div className="acct-acctsel-h">{g.label}</div>
              {g.items.map((o) => {
                const fi = flat.indexOf(o)
                return (
                  <button
                    type="button"
                    key={o.id}
                    className={
                      'acct-acctsel-item' +
                      (o.id === value ? ' sel' : '') +
                      (fi === hi ? ' hi' : '')
                    }
                    onMouseEnter={() => setHi(fi)}
                    onMouseDown={(e) => {
                      e.preventDefault()
                      commit(o)
                    }}
                  >
                    <span className="acct-acctsel-check">
                      {o.id === value ? <Check size={12} aria-hidden="true" /> : null}
                    </span>
                    <i className="acct-acctsel-dot" style={{ background: o.color }} />
                    <span className="acct-acctsel-nm">{o.label}</span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
