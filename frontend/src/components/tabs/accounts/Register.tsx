/**
 * Register.tsx — Accounts register table.
 * Owns: sort state, column widths (with localStorage persist + dblclick reset),
 *       infinite-scroll window, split-expand state, select-all popup.
 * Selection lives in the orchestrator (selectedIds, onToggle, etc.).
 *
 * Presentational — all mutations go via callbacks.
 */

import { Fragment, useCallback, useState, useEffect, useLayoutEffect, useRef, useMemo } from 'react'
import type { CSSProperties } from 'react'
import type { Txn, Account, Category, FxRates } from './types'
import type { PayeeOption } from './pickers'
import { IconCheck, IconMinus } from './pickers'
import { TxnRow } from './TxnRow'
import type { RowPatch } from './TxnRow'
import { TxnCard } from './TxnCard'
import { AddRow } from './AddRow'
import { fitRegisterColumns } from './registerFit'
import type { AddData } from './AddRow'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePointerResize } from '../../../hooks/usePointerResize'
import { Button, Cluster, EmptyState, Loading } from '../../ui'
import { Receipt } from '../../../ui/icons'
import { LuciusCta } from '../../intro/LuciusCta'
import { fDate } from '../../../utils/format'

export type { RowPatch }

/** "2026-07-10" → "July 10, 2026" — the mobile date-group header label (YNAB-style grouping,
 *  mission-control task 101). A ledger spans years, so the year is always shown. */
const fmtDateGroupLabel = (s: string): string => fDate(s, 'long', 'always')

// ── Column definitions ────────────────────────────────────────────────────

export interface RegCol {
  key: string
  w: number
  fixed?: boolean
  label?: string
  cls?: string
  accountOnly?: boolean
  min?: number
  sortable?: boolean
}

export const REG_COLS: RegCol[] = [
  { key: 'cb', w: 24, fixed: true, cls: 'acct-h-c' },
  { key: 'clr', w: 26, fixed: true, label: 'C', cls: 'acct-h-c', sortable: true },
  { key: 'account', w: 94, label: 'Account', accountOnly: true, min: 82, sortable: true },
  { key: 'date', w: 86, label: 'Date', min: 76, sortable: true },
  { key: 'payee', w: 118, label: 'Payee', min: 88, sortable: true },
  { key: 'category', w: 130, label: 'Category', min: 104, sortable: true },
  { key: 'description', w: 120, label: 'Description', min: 104, sortable: true },
  { key: 'memo', w: 120, label: 'Memo', min: 90, sortable: true },
  { key: 'source', w: 80, label: 'Source', min: 80, sortable: true, cls: 'acct-h-c acct-h-src' },
  { key: 'amount', w: 116, label: 'Amount', cls: 'r', min: 96, sortable: true },
]

const REG_W_KEY = 'acctColW_v1'
// The fill column (Description) shrinks to this floor as its neighbour grows — small enough to
// give real resize range, wide enough to stay readable (~10 chars + ellipsis).
const DESC_FILL_MIN = 72

/** Description is ALWAYS the flexible fill column (minmax(floor,1fr)) — it holds the long bank
 *  strings, so it absorbs leftover width and the table always fills its container (e.g. when the
 *  Lucius panel closes) with NO empty gap. It has no fixed width to drag directly; its resize
 *  handle transfers width to/from the adjacent column (see startResize), so you can still make it
 *  narrower — the neighbour widens and this 1fr shrinks. */
function gridTemplate(cols: RegCol[], widths: Record<string, number>): string {
  return cols
    .map((c) =>
      c.key === 'description' ? `minmax(${DESC_FILL_MIN}px, 1fr)` : `${widths[c.key] ?? c.w}px`,
    )
    .join(' ')
}
// The Description column FILLS by default (minmax(floor,1fr)) so the table always spans its
// container. Once the user explicitly drags its resize handle it becomes a fixed px (so it can
// shrink AND grow like every other column) and a trailing 1fr spacer track takes over the fill.
// This flag (persisted alongside the widths) records that explicit pin.
const REG_DESC_PIN_KEY = 'acctColDescPin_v1'

// ── Suppress click after column resize drag ────────────────────────────────

function suppressNextClick() {
  const handler = (e: MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
  }
  document.addEventListener('click', handler, { capture: true, once: true })
}

// ── Register ──────────────────────────────────────────────────────────────

export interface RegisterProps {
  txns: Txn[]
  /** Server-reported total for the current scope — for the "loaded of total" footer + select-all. */
  total: number
  accounts: Account[]
  showAccount: boolean
  selectedId: string | null
  selectedIds: Set<string>
  onToggle: (id: string) => void
  onToggleAll: (ids: string[]) => void
  onClearSelection: () => void
  onSelectRange: (ids: string[]) => void
  onEdit?: (t: Txn) => void
  onOpenDetail?: (t: Txn) => void
  onUpdate: (id: string, patch: RowPatch) => void
  onAdd: (data: AddData) => boolean | void
  adding: boolean
  onAdding: (v: boolean) => void
  onManagePayees?: () => void
  categories: Category[]
  onCreateCategory?: (
    prefill: string,
    kind: 'income' | 'expense' | 'investment' | 'transfer',
    cb: (id: string) => void,
  ) => void
  label?: string
  allEmpty: boolean
  /** True while the initial/filtered txn fetch is in flight — distinguishes "still loading" from
   *  a genuinely empty result so the register shows a spinner, not the empty-state illustration. */
  loadingTxns: boolean
  /** True while a fetch will visibly replace the rows on screen (a filter change, or the data
   *  changed) — the header progress bar. A remount's silent revalidation is not one. */
  reloadingRows?: boolean
  /** True while an infinite-scroll `loadMore` append is in flight — rows are already on screen,
   *  so this renders a small trailing indicator instead of the full-list spinner. */
  isLoadingMore: boolean
  onAddTxn?: () => void
  onImport?: () => void
  onAskLucius?: (text: string) => void
  onClearFilters?: () => void
  payeeOptions: PayeeOption[]
  payeeNames: Record<string, string> // payeeId → display name
  rates: FxRates
  /** Column keys the user has hidden via the Columns picker (localStorage `acctColVis_v1`).
   *  Fixed columns (cb/clr/amount) are never in this set — see `columns.tsx`. */
  hiddenCols: Set<string>
}

function transactionsMessage(accountName?: string): string {
  return (
    `Help me add the transactions for ${accountName ? `my ${accountName} account` : 'my accounts'}. ` +
    'I can send a statement or screenshot instead of typing. Ask me one thing at a time.'
  )
}

export function Register({
  txns,
  total,
  accounts,
  showAccount,
  selectedId,
  selectedIds,
  onToggle,
  onToggleAll,
  onClearSelection,
  onSelectRange,
  onEdit,
  onOpenDetail,
  onUpdate,
  onAdd,
  adding,
  onAdding,
  onManagePayees,
  categories,
  onCreateCategory,
  allEmpty,
  loadingTxns,
  reloadingRows = false,
  isLoadingMore,
  onAddTxn,
  onImport,
  onAskLucius,
  onClearFilters,
  payeeOptions,
  payeeNames,
  rates,
  hiddenCols,
}: RegisterProps) {
  const isMobile = useIsMobile()
  const [splits, setSplits] = useState<Record<string, boolean>>({})
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    const base = Object.fromEntries(REG_COLS.map((c) => [c.key, c.w]))
    try {
      const saved = JSON.parse(localStorage.getItem(REG_W_KEY) || '{}')
      const merged = { ...base, ...saved }
      if (!(merged.category >= base.category!)) merged.category = base.category
      return merged
    } catch (_) {
      return base
    }
  })
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({
    key: 'date',
    dir: 'desc',
  })
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const headClipRef = useRef<HTMLDivElement | null>(null)
  const drag = useRef<{
    key: string
    min: number
    max: number
    startX: number
    startW: number
  } | null>(null)
  const resizeSign = useRef(1)
  const resizeMoved = useRef(false)
  // Visible columns = drop account when not in all-accounts view, drop any the user hid via the
  // Columns picker. cb/clr/amount can never be hidden (never enter `hiddenCols`). The header,
  // TxnRow, and AddRow all render off THIS ordered set (via `colKeys`) so the grid stays aligned.
  const cols = REG_COLS.filter((c) => (!c.accountOnly || showAccount) && !hiddenCols.has(c.key))
  const colKeys = cols.map((c) => c.key)
  const colKeyStr = colKeys.join()
  // Every row reads its tracks from `--reg-tmpl` on the register root. React sets it from the
  // user's widths; the ResizeObserver below overwrites it with the widths that FIT the room
  // (registerFit.ts) without a render — the width changes every frame while the Lucius panel
  // opens or a window is dragged, and re-rendering every row each frame made that stutter
  // (owner, 2026-10-25). State changes only when the grid starts or stops overflowing.
  const tmpl = 'var(--reg-tmpl)'
  const baseTmpl = gridTemplate(cols, widths)
  const [overflow, setOverflow] = useState(false)
  const regRef = useRef<HTMLDivElement | null>(null)
  const fitInput = useRef({ cols, widths })
  const acctMap = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts])
  // Category map from server data — used for pill label + colour in the register.
  const catMap = useMemo(
    () => Object.fromEntries(categories.map((c) => [c.id, c])) as Record<string, Category>,
    [categories],
  )

  function toggleSort(key: string) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'date' || key === 'amount' ? 'desc' : 'asc' },
    )
  }
  function sortVal(t: Txn, key: string): string | number {
    switch (key) {
      case 'date':
        return t.date
      case 'clr':
        return t.cleared ? 2 : t.needsReview ? 0 : 1
      case 'amount': {
        const inflow =
          t.kind === 'income' || (t.kind === 'investment' && !t.fromAccountId && !!t.toAccountId)
        return (inflow ? 1 : -1) * (t.baseAmount || 0)
      }
      case 'category':
        return (catMap[t.category ?? '']?.label || t.category || '~').toLowerCase()
      case 'source':
        return t.source || ''
      case 'account': {
        const inflow =
          t.kind === 'income' || (t.kind === 'investment' && !t.fromAccountId && !!t.toAccountId)
        const aid = inflow ? t.toAccountId : t.fromAccountId || t.toAccountId
        const a = aid ? acctMap[aid] : null
        return (a ? a.name : '').toLowerCase()
      }
      case 'description':
        return (t.description || '').toLowerCase()
      case 'memo':
        return (t.memo || '').toLowerCase()
      case 'payee': {
        const name = t.payeeId ? (payeeNames[t.payeeId] ?? '') : (t.description ?? '')
        return name.toLowerCase()
      }
      default:
        return ''
    }
  }
  const sorted = useMemo(() => {
    const arr = txns.slice()
    const dir = sort.dir === 'asc' ? 1 : -1
    arr.sort((a, b) => {
      const va = sortVal(a, sort.key),
        vb = sortVal(b, sort.key)
      if (va < vb) return -1 * dir
      if (va > vb) return 1 * dir
      return a.date < b.date ? 1 : a.date > b.date ? -1 : 0
    })
    return arr
  }, [txns, sort, acctMap, payeeNames]) // eslint-disable-line react-hooks/exhaustive-deps

  // Rows flow into the .acct-main scroll (collapsing-header model); render the full loaded set.
  // Pagination is server-side (AccountsTab's .acct-main onScroll → loadMore) — no client windowing.
  const visible = sorted
  const visibleIds = visible.map((t) => t.id)

  // Shift-click selects the contiguous range from the last-clicked row, in displayed order.
  const lastToggled = useRef<string | null>(null)
  const rowToggle = (id: string, shift?: boolean) => {
    if (shift && lastToggled.current) {
      const a = visibleIds.indexOf(lastToggled.current),
        b = visibleIds.indexOf(id)
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a < b ? [a, b] : [b, a]
        onSelectRange(visibleIds.slice(lo, hi + 1))
        return
      }
    }
    onToggle(id)
    lastToggled.current = id
  }
  const shownAllOn = visible.length > 0 && visibleIds.every((id) => selectedIds.has(id))
  const shownSomeOn = !shownAllOn && visible.some((t) => selectedIds.has(t.id))

  const applyFit = useCallback(() => {
    const root = regRef.current
    const body = scrollRef.current
    if (!root || !body) return
    const { cols: fitCols, widths: fitWidths } = fitInput.current
    const fit = fitRegisterColumns(fitCols, fitWidths, body.clientWidth)
    root.style.setProperty('--reg-tmpl', gridTemplate(fitCols, fit.widths))
    setOverflow(fit.overflow)
  }, [])
  // After React has written the user's widths into --reg-tmpl, fit them to the room again.
  useLayoutEffect(() => {
    fitInput.current = { cols, widths }
    applyFit()
  }, [colKeyStr, widths, applyFit]) // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    const body = scrollRef.current
    if (!body || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(applyFit)
    ro.observe(body)
    return () => ro.disconnect()
  }, [applyFit])

  // When the register overflows horizontally (narrow width / Lucius open), the rows scroll in
  // .acct-reg-scroll; keep the pinned column header aligned by mirroring its scrollLeft.
  useEffect(() => {
    const body = scrollRef.current,
      clip = headClipRef.current
    if (!body || !clip) return
    const sync = () => {
      clip.scrollLeft = body.scrollLeft
    }
    sync()
    body.addEventListener('scroll', sync, { passive: true })
    return () => body.removeEventListener('scroll', sync)
  }, [])

  const onColumnPointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      event.stopPropagation()
      const handle = event.target as HTMLElement
      const col = handle.dataset.columnKey
      const resizeCol = REG_COLS.find((candidate) => candidate.key === col)
      if (!resizeCol) return
      // The fill column (Description) has no fixed width to drag. Its handle sits on the boundary
      // with the NEXT visible column, so dragging it resizes THAT column with an inverted sign:
      // drag left → the neighbour widens → the 1fr Description shrinks; drag right → neighbour
      // narrows → Description grows. The table stays full-width the whole time (no gap). A fixed
      // column resizes itself normally.
      let target = resizeCol
      let sign = 1
      let max = Infinity
      if (resizeCol.key === 'description') {
        const nextKey = colKeys[colKeys.indexOf('description') + 1]
        const nextCol = nextKey ? REG_COLS.find((c) => c.key === nextKey) : undefined
        if (!nextCol) return // no neighbour to trade width with (Description is last) — nothing to do
        target = nextCol
        sign = -1
        // Cap the neighbour's growth so Description only shrinks to its DESC_FILL_MIN floor, not
        // past it (which would overflow the table — the "memo keeps growing" the owner hit). Room
        // to give = Description's CURRENT rendered px minus its floor. jsdom (no layout) measures 0
        // → leave uncapped so tests exercise the transfer directly.
        const descCell = handle.parentElement
        const descPx = descCell ? descCell.getBoundingClientRect().width : 0
        const startWNext = widths[nextCol.key] ?? nextCol.w
        if (descPx > 0) max = startWNext + Math.max(0, descPx - DESC_FILL_MIN)
      }
      const startW = widths[target.key] ?? target.w
      drag.current = { key: target.key, min: target.min || 44, max, startX: event.clientX, startW }
      resizeSign.current = sign
      resizeMoved.current = false
      document.body.style.cursor = 'col-resize'
    },
    onMove: (event) => {
      const d = drag.current
      if (!d) return
      if (Math.abs(event.clientX - d.startX) > 2) resizeMoved.current = true
      const w = Math.max(
        d.min,
        Math.min(d.max, d.startW + resizeSign.current * (event.clientX - d.startX)),
      )
      setWidths((prev) => ({ ...prev, [d.key]: w }))
    },
    onEnd: () => {
      document.body.style.cursor = ''
      setWidths((prev) => {
        try {
          localStorage.setItem(REG_W_KEY, JSON.stringify(prev))
        } catch (_) {}
        return prev
      })
      drag.current = null
      if (resizeMoved.current) suppressNextClick()
    },
  })
  // Double-click any resize handle: reset every column to its default width. Description is always
  // the minmax(floor,1fr) fill, so this just restores default neighbour widths.
  function resetWidths() {
    const base = Object.fromEntries(REG_COLS.map((c) => [c.key, c.w]))
    setWidths(base)
    try {
      localStorage.removeItem(REG_W_KEY)
      localStorage.removeItem(REG_DESC_PIN_KEY)
    } catch (_) {}
  }

  // A filter change or a data-changed reload keeps the old rows on screen until the new page
  // lands, so without this the table looks finished while the query is still running.
  const refetching = reloadingRows && sorted.length > 0
  const loadBar = refetching && <span className="acct-reg-loadbar" aria-hidden="true" />

  return (
    <div
      ref={regRef}
      className={
        'acct-reg ' +
        (showAccount ? 'all' : 'single') +
        (isMobile ? ' cards' : '') +
        (overflow ? ' overflow' : '') +
        (refetching ? ' refetching' : '')
      }
      style={{ '--reg-tmpl': baseTmpl } as CSSProperties}
      aria-busy={refetching || undefined}
    >
      {isMobile ? (
        <div className="acct-card-headbar">
          <span className="acct-cbwrap">
            <span
              className={'acct-cb' + (shownAllOn ? ' on' : shownSomeOn ? ' part' : '')}
              title={selectedIds.size > 0 ? 'Clear selection' : 'Select all loaded rows'}
              onClick={() => {
                if (selectedIds.size > 0) {
                  onClearSelection()
                  return
                }
                onToggleAll(visibleIds)
              }}
            >
              {shownSomeOn ? <IconMinus /> : <IconCheck />}
            </span>
          </span>
          <span className="acct-card-headbar-count">
            {sorted.length}
            {total > sorted.length ? ` of ${total}` : ''} transaction
            {sorted.length === 1 ? '' : 's'}
          </span>
          {loadBar}
        </div>
      ) : (
        <div className="acct-reg-headwrap">
          {loadBar}
          <div className="acct-reg-headclip" ref={headClipRef}>
            <div className="acct-reg-head" style={{ gridTemplateColumns: tmpl }}>
              {cols.map((c) => (
                <span
                  key={c.key}
                  className={
                    'acct-h' +
                    (c.cls ? ' ' + c.cls : '') +
                    (c.sortable ? ' sortable' : '') +
                    (sort.key === c.key ? ' sorted' : '')
                  }
                  onClick={c.sortable ? () => toggleSort(c.key) : undefined}
                >
                  {c.key === 'cb' ? (
                    <span className="acct-cbwrap">
                      <span
                        className={'acct-cb' + (shownAllOn ? ' on' : shownSomeOn ? ' part' : '')}
                        title={selectedIds.size > 0 ? 'Clear selection' : 'Select all loaded rows'}
                        onClick={(e) => {
                          e.stopPropagation()
                          if (selectedIds.size > 0) {
                            onClearSelection()
                            return
                          }
                          onToggleAll(visibleIds)
                        }}
                      >
                        {shownSomeOn ? <IconMinus /> : <IconCheck />}
                      </span>
                    </span>
                  ) : (
                    <span className="acct-h-lbl">{c.label || ''}</span>
                  )}
                  {c.sortable && sort.key === c.key && (
                    <span className={'acct-sort-ind ' + sort.dir}></span>
                  )}
                  {!c.fixed && (
                    <span
                      className="acct-col-resize"
                      data-column-key={c.key}
                      onPointerDown={onColumnPointerDown}
                      onClick={(e) => e.stopPropagation()}
                      onDoubleClick={(e) => {
                        e.stopPropagation()
                        resetWidths()
                      }}
                      title="Drag to resize • double-click to reset all"
                    />
                  )}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
      <div
        className="acct-reg-scroll"
        ref={scrollRef}
        data-select-scope
        data-selected-total={selectedIds.size}
      >
        <AddRow
          active={adding}
          onClose={() => onAdding(false)}
          accounts={accounts}
          showAccount={showAccount}
          selectedId={selectedId}
          payeeOptions={payeeOptions}
          tmpl={tmpl}
          colKeys={colKeys}
          onAdd={onAdd}
          onManagePayees={onManagePayees}
          categories={categories}
          onCreateCategory={onCreateCategory}
        />
        {sorted.length === 0 ? (
          loadingTxns ? (
            <Loading label="Loading transactions…" />
          ) : allEmpty ? (
            <EmptyState
              fill
              icon={<Receipt size={28} strokeWidth={1.5} aria-hidden="true" />}
              title="No transactions yet"
              body="Add one by hand, or import a statement to bring them in automatically."
              action={
                <Cluster gap="--space-3" justify="center">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => (onAddTxn ? onAddTxn() : onAdding(true))}
                  >
                    Add transaction
                  </Button>
                  {onImport && (
                    <Button variant="secondary" size="sm" onClick={onImport}>
                      Import from file
                    </Button>
                  )}
                  <LuciusCta
                    label="Add with Lucius"
                    message={transactionsMessage(
                      selectedId ? acctMap[selectedId]?.name : undefined,
                    )}
                    onAskLucius={onAskLucius}
                  />
                </Cluster>
              }
            />
          ) : (
            <div className="acct-empty">
              <div className="t">No transactions match your filters.</div>
              {onClearFilters && (
                <button className="acct-empty-clear" onClick={onClearFilters}>
                  Clear filters
                </button>
              )}
            </div>
          )
        ) : isMobile ? (
          // Grouped by date, YNAB-style (task 101) — only when the list is actually IN date
          // order (the default sort); a non-date sort scatters same-date rows non-contiguously,
          // so a header per row-to-row date change would read as noise, not groups. In that
          // case fall back to the plain ungrouped cards, each showing its own date (unchanged).
          sort.key === 'date' ? (
            (() => {
              let lastDate: string | null = null
              return sorted.map((t) => {
                const isNewGroup = t.date !== lastDate
                lastDate = t.date
                return (
                  <Fragment key={t.id}>
                    {isNewGroup && (
                      <div className="acct-card-dategroup">{fmtDateGroupLabel(t.date)}</div>
                    )}
                    <TxnCard
                      t={t}
                      accounts={accounts}
                      showAccount={showAccount}
                      selectedId={selectedId}
                      selected={selectedIds.has(t.id)}
                      onToggle={rowToggle}
                      onUpdate={onUpdate}
                      onOpenDetail={onOpenDetail}
                      payeeName={t.payeeId ? (payeeNames[t.payeeId] ?? null) : null}
                      rates={rates}
                      catMap={catMap}
                      splitOpen={!!splits[t.id]}
                      onSplit={(id) => setSplits((s) => ({ ...s, [id]: !s[id] }))}
                      showDate={false}
                    />
                  </Fragment>
                )
              })
            })()
          ) : (
            sorted.map((t) => (
              <TxnCard
                key={t.id}
                t={t}
                accounts={accounts}
                showAccount={showAccount}
                selectedId={selectedId}
                selected={selectedIds.has(t.id)}
                onToggle={rowToggle}
                onUpdate={onUpdate}
                onOpenDetail={onOpenDetail}
                payeeName={t.payeeId ? (payeeNames[t.payeeId] ?? null) : null}
                rates={rates}
                catMap={catMap}
                splitOpen={!!splits[t.id]}
                onSplit={(id) => setSplits((s) => ({ ...s, [id]: !s[id] }))}
              />
            ))
          )
        ) : (
          sorted.map((t) => (
            <TxnRow
              key={t.id}
              t={t}
              accounts={accounts}
              showAccount={showAccount}
              selectedId={selectedId}
              selected={selectedIds.has(t.id)}
              onToggle={rowToggle}
              onEdit={onEdit}
              onUpdate={onUpdate}
              onOpenDetail={onOpenDetail}
              onManagePayees={onManagePayees}
              onCreateCategory={onCreateCategory}
              payeeOptions={payeeOptions}
              payeeName={t.payeeId ? (payeeNames[t.payeeId] ?? null) : null}
              tmpl={tmpl}
              colKeys={colKeys}
              rates={rates}
              categories={categories}
              catMap={catMap}
              splitOpen={!!splits[t.id]}
              onSplit={(id) => setSplits((s) => ({ ...s, [id]: !s[id] }))}
            />
          ))
        )}
        {isLoadingMore && (
          <div className="acct-loading-more">
            <span className="acct-loading-more__spinner" aria-hidden="true" />
            Loading more…
          </div>
        )}
        {sorted.length > 0 && (
          <div className="acct-reg-count">
            {sorted.length}
            {total > sorted.length ? ` of ${total}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}
