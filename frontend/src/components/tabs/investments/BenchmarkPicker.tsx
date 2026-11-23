// The Performance chart's benchmark: the server's roster, grouped by region, plus any ticker on
// the market (issue #223). Typing filters the roster and searches the market the way the command
// palette does; a market pick is resolved (find-or-create), its history fetched, then saved as the
// ONE benchmark (the profile's `preferred_benchmark`). A popover on a pointer, a bottom sheet on a
// phone — the Dashboard's filter pill does the same.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from '../../../ui/icons'
import { BottomSheet } from '../../ui/BottomSheet'
import { TextField } from '../../ui'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { prepareBenchmark } from '../../../api/portfolio'
import type { BenchmarkRosterItem } from '../../../types/portfolio'
import { resolveMarket, useMarketSearch, type MarketResult } from '../automations/TickerPicker'

const POP_W = 320

interface Row {
  id: string
  label: string
  detail?: string
  group?: string
  roster?: BenchmarkRosterItem
  market?: MarketResult
}

function rowsFor(query: string, roster: BenchmarkRosterItem[], market: MarketResult[]): Row[] {
  const q = query.trim().toLocaleLowerCase()
  const own = roster
    .filter((b) => !q || b.label.toLocaleLowerCase().includes(q))
    .map((b) => ({ id: `r-${b.key || 'none'}`, label: b.label, group: b.group || '', roster: b }))
  if (!q) return own
  const listed = new Set(roster.map((b) => b.symbol.toLocaleLowerCase()))
  const fromMarket = market
    .filter((m) => !listed.has(m.symbol.toLocaleLowerCase()))
    .map((m) => ({
      id: `m-${m.price_source}-${m.price_source_id}`,
      label: m.symbol,
      detail: [m.name, m.exchange].filter(Boolean).join(' · '),
      group: 'Market',
      market: m,
    }))
  return [...own, ...fromMarket]
}

export function BenchmarkPicker({
  value,
  roster,
  onPick,
}: {
  value: string
  roster: BenchmarkRosterItem[]
  /** A roster key, or the entry of a ticker just prepared from the market search. */
  onPick: (key: string, added?: BenchmarkRosterItem) => void
}) {
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number; maxH: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const { results, loading } = useMarketSearch(query, open)
  const rows = useMemo(() => rowsFor(query, roster, results), [query, roster, results])
  const current = roster.find((b) => b.key === value)
  const label = current ? (current.key ? `vs ${current.label}` : current.label) : `vs ${value}`

  const close = () => {
    setOpen(false)
    setQuery('')
    setActive(0)
    setFailed(null)
  }

  useLayoutEffect(() => {
    if (!open || isMobile || !wrapRef.current) return
    const r = wrapRef.current.getBoundingClientRect()
    const left = Math.min(Math.max(8, r.right - POP_W), window.innerWidth - POP_W - 8)
    setPos({ top: r.bottom + 6, left, maxH: Math.max(220, window.innerHeight - r.bottom - 24) })
  }, [open, isMobile])

  // Opening the picker is asking to type. Focus once the popover is placed: it renders hidden
  // while it measures, and a hidden input can't take focus (`autoFocus` silently did nothing).
  // A phone keeps its keyboard down until the search box is tapped.
  useEffect(() => {
    if (open && !isMobile && pos) popRef.current?.querySelector('input')?.focus()
  }, [open, isMobile, pos])

  useEffect(() => {
    if (!open || isMobile) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || popRef.current?.contains(t)) return
      close()
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open, isMobile])

  const choose = async (row: Row) => {
    if (busy) return
    if (row.roster) {
      onPick(row.roster.key)
      close()
      return
    }
    if (!row.market) return
    setBusy(row.label)
    setFailed(null)
    try {
      const picked = await resolveMarket(row.market)
      const entry = picked ? await prepareBenchmark(picked.id) : null
      if (!entry) throw new Error('unresolved')
      onPick(entry.key, entry)
      close()
    } catch {
      setFailed(row.label)
    } finally {
      setBusy(null)
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, Math.max(rows.length - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && rows[active]) {
      e.preventDefault()
      void choose(rows[active])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      close()
    }
  }

  const body = (
    <>
      <TextField
        className="mk-bench-search"
        value={query}
        placeholder="Search any index, fund or ticker"
        aria-label="Search benchmarks"
        role="combobox"
        aria-expanded
        aria-controls="mk-bench-list"
        aria-activedescendant={rows[active] ? `mk-bench-${rows[active].id}` : undefined}
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
          setFailed(null)
        }}
        onKeyDown={onKey}
      />
      <div className="mk-bench-list" id="mk-bench-list" role="listbox" aria-label="Benchmarks">
        {rows.map((row, i) => {
          const heading = row.group && row.group !== rows[i - 1]?.group ? row.group : null
          const selected = !!row.roster && row.roster.key === value
          return (
            <div key={row.id} role="presentation">
              {heading && <div className="mk-bench-group">{heading}</div>}
              <button
                type="button"
                id={`mk-bench-${row.id}`}
                role="option"
                aria-selected={selected}
                className={
                  'kebab-item mk-bench-item' +
                  (i === active ? ' is-active' : '') +
                  (selected ? ' is-selected' : '')
                }
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => void choose(row)}
              >
                <span className="mk-bench-item-text">
                  <span className="mk-bench-item-label">{row.label}</span>
                  {row.detail && <span className="mk-bench-item-detail">{row.detail}</span>}
                </span>
                {selected && <Check size={13} strokeWidth={2.4} aria-hidden="true" />}
              </button>
            </div>
          )
        })}
        {query.trim() && loading && <div className="mk-bench-status">Searching the market…</div>}
        {query.trim() && !loading && rows.length === 0 && (
          <div className="mk-bench-status">No match for “{query.trim()}”.</div>
        )}
      </div>
      {busy && <div className="mk-bench-status">Getting {busy}’s price history…</div>}
      {failed && (
        <div className="mk-bench-status is-error" role="alert">
          Couldn’t use {failed} as a benchmark — it has no market price here.
        </div>
      )}
    </>
  )

  return (
    <div className="mk-bench" ref={wrapRef}>
      <button
        type="button"
        className="mk-toolsel mk-bench-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Benchmark: ${label}`}
        title={label}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="mk-bench-trigger-label">{label}</span>
        <ChevronDown size={12} strokeWidth={2.2} aria-hidden="true" />
      </button>
      {open && isMobile && (
        <BottomSheet title="Compare against" onClose={close}>
          <div className="mk-bench-sheet">{body}</div>
        </BottomSheet>
      )}
      {open &&
        !isMobile &&
        createPortal(
          <div
            ref={popRef}
            className="kebab-menu kebab-menu--portal mk-bench-pop"
            role="dialog"
            aria-label="Compare against"
            style={
              pos
                ? { top: pos.top, left: pos.left, width: POP_W, maxHeight: Math.min(440, pos.maxH) }
                : { visibility: 'hidden' }
            }
          >
            {body}
          </div>,
          document.body,
        )}
    </div>
  )
}
