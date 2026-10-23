// plan/budgetMove.tsx — the money-moves feature (design_handoffs/plan_money_moves_handoff),
// extended by the budget-envelopes handoff port (delta #9 — "RTA in the Move/Cover picker"):
//   • AvailCell: the Available figure becomes a tappable pill. Green (available>0) opens a
//     "Move" popover (amount + To picker); red (available<0) opens "Cover overspending from"
//     (source picker + the overspent amount). Zero pills stay non-interactive. An overfunded
//     row's "Move S$X" caption (budget.tsx CategoryRow) opens this SAME picker externally,
//     anchored + prefilled, via `moveReq` — it never moves money silently anymore.
//   • CatSelect: a searchable, grouped picker (ported verbatim from prototype/plan/budget.jsx
//     CatSelect :31-96) — search box, ✓ check + available-balance mask per row, "Ready to
//     Assign" always leads the list as its own "Budget" group.
//   • Both flows call ONE transfer (envelope↔envelope OR envelope↔RTA) = the adapter's
//     moveBetweenEnvelopes(from, to, amount) — `__rta` on either end is a live backend sentinel.
import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'
import * as PD from './planData'
import { getCurrencySymbol } from '../../../utils/format'
import { Chevron } from './icons'
import { Fig, MenuGlyph } from './budgetParts'
import type { LocalGroup } from './useBudgetModel'
import { RTA_KEY } from './useBudgetModel'
import { Button } from '../../ui'
import { Check } from '../../../ui/icons'

const { fFig, fSmart } = PD

// The minimal shape CatSelect needs from each row — a real LocalCat satisfies this
// structurally (it carries far more), and the synthetic "Ready to Assign" row is built
// directly to this shape (it has no backing category, so a full LocalCat would be a lie).
export interface CatSelectOption {
  key: string
  label: string
  color: string
  available: number
}
type CatOption = { name: string; cats: CatSelectOption[] }

// Pure — the Move/Cover picker's option list (handoff delta #9). "Ready to Assign" leads as
// its own "Budget" group — a first-class destination/source, NOT excluded for synced
// (Commitments) rows (the __rta sentinel is a plain money move, not a category write, so
// there's no reason to withhold it from a synced row). The row's own key is excluded from
// every OTHER group so it never appears as its own destination. Nothing is preselected.
export function buildMoveOptions(
  selfKey: string,
  allGroups: LocalGroup[],
  rta: number,
): CatOption[] {
  const rtaOption: CatOption = {
    name: 'Budget',
    cats: [{ key: RTA_KEY, label: 'Ready to Assign', color: 'var(--accent)', available: rta }],
  }
  return [
    rtaOption,
    ...allGroups
      .map((g) => ({ name: g.name, cats: g.cats.filter((x) => x.key !== selfKey) }))
      .filter((g) => g.cats.length),
  ]
}

// available-balance label, coloured green / red / muted-zero (shown on each picker row) — every
// envelope's available balance is a real figure, so it must mask under privacy mode too
// (previously leaked unmasked in the Move/Cover destination picker).
function availTag(n: number) {
  if (Math.abs(n) < 0.005)
    return <span className="pl-catsel-money zero mask">{getCurrencySymbol()}0</span>
  return <span className={'pl-catsel-money mask ' + (n < 0 ? 'bad' : 'good')}>{fFig(n).text}</span>
}

// ── CatSelect — searchable grouped picker (replaces the native <select>) ───────
export function CatSelect({
  options,
  value,
  onChange,
  placeholder,
}: {
  options: CatOption[]
  value: string
  onChange: (key: string) => void
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [pos, setPos] = useState<{
    left: number
    width: number
    top?: number
    bottom?: number
    maxH: number
  } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const flat = options.flatMap((g) => g.cats)
  const selected = flat.find((c) => c.key === value) || null

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const r = btnRef.current!.getBoundingClientRect()
    const below = window.innerHeight - r.bottom
    const above = r.top
    const flipUp = below < 240 && above > below
    const room = flipUp ? above - 12 : below - 12
    const maxH = Math.max(180, Math.min(420, Math.round(room)))
    setPos(
      flipUp
        ? {
            left: Math.round(r.left),
            width: Math.round(r.width),
            bottom: Math.round(window.innerHeight - r.top + 6),
            maxH,
          }
        : {
            left: Math.round(r.left),
            width: Math.round(r.width),
            top: Math.round(r.bottom + 6),
            maxH,
          },
    )
    requestAnimationFrame(() => searchRef.current?.focus())
  }, [open])

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    const onScroll = (e: Event) => {
      if (ref.current && ref.current.contains(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) setQ('')
  }, [open])

  const ql = q.trim().toLowerCase()
  const filtered = ql
    ? options
        .map((g) => ({
          name: g.name,
          cats: g.cats.filter((c) => c.label.toLowerCase().includes(ql)),
        }))
        .filter((g) => g.cats.length)
    : options
  const firstMatch = filtered.flatMap((g) => g.cats)[0]

  function pick(key: string) {
    onChange(key)
    setOpen(false)
    setQ('')
  }

  return (
    <div className="pl-catsel" ref={ref}>
      <button
        ref={btnRef}
        type="button"
        className={'pl-catsel-trigger' + (open ? ' open' : '')}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selected ? (
          <span className="pl-catsel-val">
            <i className="pl-cdot" style={{ background: selected.color }} />
            {selected.label}
          </span>
        ) : (
          <span className="pl-catsel-ph">{placeholder || 'Select an envelope…'}</span>
        )}
        <span className="pl-sel-caret">
          <Chevron dir={open ? 'up' : 'down'} size={13} />
        </span>
      </button>
      {open && pos && (
        <div
          className="pl-menu pl-menu--catsel"
          role="listbox"
          style={{
            position: 'fixed',
            left: pos.left + 'px',
            width: pos.width + 'px',
            top: pos.top != null ? pos.top + 'px' : 'auto',
            bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
            maxHeight: pos.maxH + 'px',
          }}
        >
          <div className="pl-catsel-search">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input
              ref={searchRef}
              value={q}
              placeholder="Search envelopes…"
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && firstMatch) {
                  e.preventDefault()
                  pick(firstMatch.key)
                }
              }}
            />
          </div>
          <div className="pl-catsel-list">
            {filtered.length === 0 ? (
              <div className="pl-menu-empty">No envelopes found</div>
            ) : (
              filtered.map((g) => (
                <div key={g.name}>
                  <div className="pl-menu-lbl">{g.name}</div>
                  {g.cats.map((c) => (
                    <button
                      key={c.key}
                      className={'pl-menu-item indent' + (c.key === value ? ' on' : '')}
                      role="option"
                      aria-selected={c.key === value}
                      onClick={() => pick(c.key)}
                    >
                      <span>
                        <MenuGlyph icon="envelope" color={c.color} />
                        {c.label}
                      </span>
                      <span className="pl-catsel-right">
                        {c.key === value && (
                          <Check className="pl-menu-check" size={12} aria-hidden="true" />
                        )}
                        {availTag(c.available)}
                      </span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── AvailCell — the tappable Available pill + Move / Cover popover ─────────────
export interface MoveReq {
  t: number
  amt: number
  rect: DOMRect
}
export function AvailCell({
  c,
  n,
  allGroups,
  onTransfer,
  rta,
  moveReq,
}: {
  c?: { key: string; label: string; available: number; status?: PD.BudgetCategory['status'] }
  n?: number
  allGroups?: LocalGroup[]
  onTransfer?: (fromKey: string, toKey: string, amount: number) => void
  /** Ready to Assign's current value — leads the destination/source list as its own row
   *  (handoff delta #9). Omitted (e.g. the Total row's plain figure) → RTA isn't offered. */
  rta?: number
  /** External-open request (the row's "Move S$X" caption action, handoff delta #9): opens
   *  THIS SAME picker, prefilled with the suggested amount and anchored to the clicked
   *  button's rect — nothing preselected, never moves anywhere without the user choosing. */
  moveReq?: MoveReq | null
}) {
  const val = c ? c.available : (n ?? 0)
  const { text, isZero, isNeg } = fFig(val)
  const [open, setOpen] = useState(false)
  const [dest, setDest] = useState('')
  const [amount, setAmount] = useState('')
  const [pos, setPos] = useState<{
    left: number
    W: number
    arrowX: number
    top?: number
    bottom?: number
    below: boolean
    root: Element
  } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const amtRef = useRef<HTMLInputElement>(null)
  const pendingAmtRef = useRef<number | null>(null)
  const pendingRectRef = useRef<DOMRect | null>(null)

  // Amber = "this envelope's target isn't met this month" (owner ask, 2026-09-15, matching
  // YNAB's own yellow Available pill). It's a THIRD tone, not a replacement: red still means
  // a negative balance and green still means money sitting there, so a row can only be amber
  // while it is non-negative — an overspent row is a bigger problem than an unmet target and
  // must keep saying so. Payment envelopes are excluded because their status is forced to
  // 'none' (they carry no target at all — their shortfall is `paymentUnderfunded`, a separate
  // quantity this cell never receives).
  const isUnder = !!c && !isNeg && c.status === 'underfunded'
  const tone = isNeg ? 'bad' : isUnder ? 'under' : 'good'
  const interactive = !!c && !!onTransfer && !isZero
  const mode: 'move' | 'cover' = isNeg ? 'cover' : 'move'
  const cover = Math.round(-val * 100) / 100

  // External open (a sibling "Move S$X" button elsewhere in the row) — the SAME picker,
  // prefilled with the suggested amount and anchored to the button that was actually clicked.
  useEffect(() => {
    if (!moveReq) return
    pendingAmtRef.current = moveReq.amt
    pendingRectRef.current = moveReq.rect
    setOpen(true)
  }, [moveReq])

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      pendingRectRef.current = null
      return
    }
    setDest('')
    setAmount(mode === 'move' ? String(pendingAmtRef.current ?? Math.round(val * 100) / 100) : '')
    pendingAmtRef.current = null
    const r = pendingRectRef.current ?? btnRef.current!.getBoundingClientRect()
    // CONF-3 — centered on the trigger + a pointer arrow, mirroring the prototype's own
    // popover math (and this app's OTHER fixed-position popovers — ActivityCell/RtaHero).
    const W = Math.min(420, window.innerWidth - 24)
    let left = Math.round(r.left + r.width / 2 - W / 2)
    left = Math.max(12, Math.min(left, window.innerWidth - W - 12))
    const below = window.innerHeight - r.bottom > 240
    const arrowX = Math.max(16, Math.min(W - 28, Math.round(r.left + r.width / 2 - left)))
    // Picked here, not during render: the portal target (the modal the trigger sits in, else the
    // app shell) is read off the trigger's DOM, which render may not touch.
    const root =
      ref.current?.closest('.pl-modal') ?? document.querySelector('.app-shell') ?? document.body
    setPos(
      below
        ? { left, W, arrowX, top: Math.round(r.bottom + 8), below, root }
        : { left, W, arrowX, bottom: Math.round(window.innerHeight - r.top + 8), below, root },
    )
    if (mode === 'move')
      requestAnimationFrame(() => {
        const el = amtRef.current
        if (el) {
          el.focus()
          el.setSelectionRange(el.value.length, el.value.length)
        }
      })
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return
    // The panel is portaled out of the table (below), so "inside" is the trigger OR the panel.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const h = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  if (!interactive) {
    // A zero that's underfunded is still amber — that's the commonest case of all (nothing
    // assigned yet, target unmet), and the neutral grey pill said nothing about it.
    if (isZero)
      return <span className={'pl-avail ' + (isUnder ? 'under mask' : 'zero')}>{text}</span>
    return <span className={'pl-avail mask ' + tone}>{text}</span>
  }

  const amt = Math.round((parseFloat(amount) || 0) * 100) / 100
  const options: CatOption[] = buildMoveOptions(c!.key, allGroups || [], rta ?? 0)
  const okEnabled =
    mode === 'move'
      ? !!dest && amt > 0.005 && amt <= Math.round(val * 100) / 100 + 0.005
      : !!dest && cover > 0.005

  function submit() {
    if (!onTransfer || !c) return
    if (mode === 'move') onTransfer(c.key, dest, amt)
    else onTransfer(dest, c.key, cover)
    setOpen(false)
  }

  return (
    <span className="pl-act-wrap" ref={ref}>
      <button
        ref={btnRef}
        className={'pl-avail pl-avail--btn mask ' + tone}
        title={mode === 'move' ? 'Move money' : 'Cover overspending'}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        {text}
      </button>
      {/* Portaled to the app shell: the desktop table's content carries a transform (the
          workspace scroll hold, plan.css), which would otherwise capture this fixed panel —
          positioning it against the table instead of the viewport and clipping it. */}
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-mvpop"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
              width: pos.W + 'px',
            }}
          >
            <span
              className="pl-mvpop-arrow"
              style={{
                left: pos.arrowX + 'px',
                [pos.below ? 'top' : 'bottom']: '-6px',
                transform: pos.below ? 'rotate(45deg)' : 'rotate(225deg)',
              }}
            />
            <div className="pl-mvpop-title">
              {mode === 'move' ? 'Move' : 'Cover overspending from'}
            </div>
            {mode === 'move' && (
              <div className="pl-mvpop-amt">
                <span className="pl-mvpop-cur">{getCurrencySymbol()}</span>
                {/* BUD-SWEEP-05 — type="number" doesn't support setSelectionRange (the focus
                  effect above threw on every open); text + inputMode="decimal" matches the
                  app's own .pl-assign-edit pattern and keeps the caret-at-end intent. */}
                <input
                  ref={amtRef}
                  type="text"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ''))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && okEnabled) submit()
                  }}
                />
              </div>
            )}
            {mode === 'move' && <div className="pl-mvpop-label">To</div>}
            <CatSelect
              options={options}
              value={dest}
              onChange={setDest}
              placeholder="Select an envelope…"
            />
            {mode === 'cover' && (
              <div className="pl-mvpop-hint">
                Covers <b className="mask">{fSmart(cover)}</b> overspent in {c!.label}.
              </div>
            )}
            <div className="pl-mvpop-foot">
              <Button variant="secondary" size="dense" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" size="dense" disabled={!okEnabled} onClick={submit}>
                OK
              </Button>
            </div>
          </div>,
          pos.root,
        )}
    </span>
  )
}

// re-export Fig so budget.tsx can pull both figure helpers from one place if convenient
export { Fig }
