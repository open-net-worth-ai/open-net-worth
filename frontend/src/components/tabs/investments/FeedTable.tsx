// FeedTable — compact table density feed. Verbatim port of prototype FeedTable().
// Full column set: Holding, Account, Qty, Cost, Price, Cur, Value, Wt%, P&L, P&L%, Day%, actions.

import { useRef, useState } from 'react'
import { IntelStoryDot } from '../../shared/IntelStoryDot'
import type { TickerStories } from '../../../hooks/useIntelStoryIndex'
import { storyKey } from './storyKey'
import type { BookGroup, BookRow } from './useInvestmentsBook'
import { fBase, fUnitPrice, fPct, priceDigits } from '../../../utils/format'
import { ASSET_CLASS_COLOR, paletteColor } from '../../../utils/constants'
import { EntityRow } from '../../../lucius/EntityRow'
import { KebabMenu } from '../planning/KebabMenu'
import { usePointerResize } from '../../../hooks/usePointerResize'

const WRAP: Record<string, string> = {
  etf: 'ETF',
  etc: 'ETC',
  etp: 'ETP',
  bondetf: 'ETF',
  govt: 'GOVT',
}

// Default px widths for fixed columns. Holding + Account have no default → flex-fill.
const COL_DEFAULTS: Record<string, number> = {
  sym: 155,
  account: 155,
  qty: 120,
  cost: 100,
  price: 100,
  ccy: 56,
  value: 100,
  wt: 64,
  pl: 110,
  plpct: 76,
  day: 76,
  actions: 56,
}
const FLEX_COLS = new Set(['sym', 'account']) // double-click handle resets to default
// Columns excluded from localStorage — either not user-resizable (actions) or
// likely to need default bumps as data scales (qty).
const NO_PERSIST = new Set(['actions', 'qty'])
const LS_KEY = 'inv-col-widths'

function loadWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const stored = JSON.parse(raw)
      // Strip NO_PERSIST cols so stale stored values never override current defaults
      for (const k of NO_PERSIST) delete stored[k]
      return { ...COL_DEFAULTS, ...stored }
    }
  } catch {
    /* ignore */
  }
  return { ...COL_DEFAULTS }
}

function tone(v: number | null): string {
  if (v == null) return 'var(--muted)'
  return v >= 0 ? 'var(--good)' : 'var(--bad)'
}

/** Signed base amount, or a dash when the figure is unknown (no cost basis recorded). */
function fSignedBase(v: number | null): string {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '−') + fBase(Math.abs(v))
}

interface SortState {
  key: string
  dir: number
}

interface Props {
  groups: BookGroup[]
  grand: number
  sort: SortState
  onSort: (key: string) => void
  onSell?: (row: BookRow) => void
  onDividend?: (row: BookRow) => void
  onOpenAssetDetail?: (row: BookRow) => void
  /** Upper-cased ticker → live Intel stories (useIntelStoryIndex). */
  storyIndex?: Map<string, TickerStories>
  onOpenStory?: (storyId: string) => void
}

export function FeedTable({
  groups,
  grand,
  sort,
  onSort,
  onSell,
  onDividend,
  onOpenAssetDetail,
  storyIndex,
  onOpenStory,
}: Props) {
  const [colWidths, setColWidths] = useState<Record<string, number>>(loadWidths)
  const [isDragging, setIsDragging] = useState(false)
  const draggingRef = useRef(false)
  const resize = useRef<{
    col: string
    startX: number
    startWidth: number
    currentWidth: number
  } | null>(null)

  function colW(col: string): { width: number } | undefined {
    const stored = colWidths[col]
    if (stored != null) return { width: stored }
    const def = COL_DEFAULTS[col]
    if (def != null) return { width: def }
    return undefined
  }

  const onColumnPointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      event.stopPropagation()
      const handle = event.target as HTMLElement
      const col = handle.dataset.columnKey
      if (!col) return
      let startW: number
      if (colWidths[col] != null) {
        startW = colWidths[col]
      } else if (COL_DEFAULTS[col] != null) {
        startW = COL_DEFAULTS[col]
      } else {
        // flex column — measure rendered width
        const th = handle.closest('th')
        startW = th ? th.getBoundingClientRect().width : 120
      }
      draggingRef.current = true
      setIsDragging(true)
      resize.current = { col, startX: event.clientX, startWidth: startW, currentWidth: startW }
    },
    onMove: (event) => {
      const drag = resize.current
      if (!drag) return
      drag.currentWidth = Math.max(48, drag.startWidth + (event.clientX - drag.startX))
      setColWidths((prev) => ({ ...prev, [drag.col]: drag.currentWidth }))
    },
    onEnd: () => {
      const drag = resize.current
      if (!drag) return
      setColWidths((prev) => {
        const next = { ...prev, [drag.col]: drag.currentWidth }
        const toSave = Object.fromEntries(Object.entries(next).filter(([k]) => !NO_PERSIST.has(k)))
        localStorage.setItem(LS_KEY, JSON.stringify(toSave))
        return next
      })
      resize.current = null
      setIsDragging(false)
      // Delay so the th's onClick sees draggingRef.current = true and skips sort
      setTimeout(() => {
        draggingRef.current = false
      }, 0)
    },
  })

  // Delete stored px override for a flex column → returns to equal-share fill
  function resetFlexCol(col: string) {
    setColWidths((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([k]) => k !== col))
      const toSave = Object.fromEntries(Object.entries(next).filter(([k]) => !NO_PERSIST.has(k)))
      localStorage.setItem(LS_KEY, JSON.stringify(toSave))
      return next
    })
  }

  function Th({ k, label }: { k: string; label: string }) {
    return (
      <th
        onClick={() => {
          if (!draggingRef.current) onSort(k)
        }}
        title="Sort by this column"
      >
        {label} <span className="ar">{sort.key === k ? (sort.dir > 0 ? '▲' : '▼') : ''}</span>
        <span
          className="mk-col-handle"
          data-column-key={k}
          onPointerDown={onColumnPointerDown}
          onDoubleClick={
            FLEX_COLS.has(k)
              ? (e) => {
                  e.stopPropagation()
                  resetFlexCol(k)
                }
              : undefined
          }
          onClick={(e) => e.stopPropagation()}
        />
      </th>
    )
  }

  return (
    <div className="mk-tbl-wrap">
      <div className="mk-tbl-scroll">
        <table className={`mk-tbl${isDragging ? ' mk-tbl--dragging' : ''}`}>
          <colgroup>
            <col style={colW('sym')} />
            <col style={colW('account')} />
            <col style={colW('qty')} />
            <col style={colW('cost')} />
            <col style={colW('price')} />
            <col style={colW('ccy')} />
            <col style={colW('value')} />
            <col style={colW('wt')} />
            <col style={colW('pl')} />
            <col style={colW('plpct')} />
            <col style={colW('day')} />
            <col style={colW('actions')} />
          </colgroup>
          <thead>
            <tr>
              <Th k="sym" label="Holding" />
              <Th k="account" label="Account" />
              <Th k="qty" label="Qty" />
              <Th k="cost" label="Cost" />
              <Th k="price" label="Price" />
              <Th k="ccy" label="Cur" />
              <Th k="value" label="Value" />
              <Th k="wt" label="Wt %" />
              <Th k="pl" label="P&amp;L" />
              <Th k="plpct" label="P&amp;L %" />
              <Th k="day" label="Day %" />
              <th className="nostick" aria-hidden="true" />
            </tr>
          </thead>
          {groups.map((g) => (
            <tbody key={g.key}>
              {g.label != null && (
                <tr className="mk-grp-head">
                  <td className="left" colSpan={6}>
                    <span className="mk-grp-label">
                      <span
                        className="mk-grp-dot"
                        style={{ background: g.color || 'var(--muted)' }}
                      />
                      <span className="mk-grp-nm">{g.label}</span>
                      <span className="mk-grp-ct">{g.rows.length}</span>
                    </span>
                  </td>
                  <td className="mk-grp-val mask">{fBase(g.subtotal)}</td>
                  <td className="mk-grp-val">{g.cur}%</td>
                  <td colSpan={4} />
                </tr>
              )}
              {g.rows.map((r) => {
                const badge = WRAP[r.sub]
                const merged = r.mergedCount > 1
                // A merged (multi-account) row has no single POSITION to hang a Sell/Dividend
                // action on — the ambiguity is deliberate (see per-account detail in the
                // asset-detail Position tab instead). It's still one SECURITY, though — "Ask
                // Lucius about this" stays meaningful, just re-anchored to the instrument
                // (mention type "instrument", ref: instrument_id — the security, not a
                // holding) instead of the position, below.
                const positionId = merged ? undefined : r._raw.position_id
                const instrumentId = r._raw.instrument_id
                const cells = (
                  <>
                    <td className="left">
                      <span className="mk-hcell">
                        {g.label == null && (
                          <span
                            className="mk-row-dot"
                            style={{ background: ASSET_CLASS_COLOR[r.cls] || paletteColor(r.cls) }}
                            aria-hidden
                          />
                        )}
                        <span className="mk-hsym">{r.sym}</span>
                        {badge && <span className="mk-wrap-badge">{badge}</span>}
                        {onOpenStory && (
                          <IntelStoryDot
                            stories={storyIndex?.get(storyKey(r))}
                            onOpen={onOpenStory}
                          />
                        )}
                        <span className="mk-hnm"> • {r.name}</span>
                        {r.coupon != null && (
                          <span className="mk-bond-meta">
                            {r.coupon}%{' '}
                            {r.maturity ? '• mat ' + String(r.maturity).slice(0, 4) : '• fund'}
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="muted mk-acctd not-money" title={r.account}>
                      {r.account}
                    </td>
                    <td className="mask">{r.qty.toLocaleString()}</td>
                    <td className="mask">{fUnitPrice(r.cost, r.ccy, priceDigits(r.cls))}</td>
                    <td className="not-money">{fUnitPrice(r.price, r.ccy, priceDigits(r.cls))}</td>
                    <td className="muted not-money">{r.ccy}</td>
                    <td className="mask">{fBase(r.value)}</td>
                    <td>{r.wt.toFixed(1)}%</td>
                    <td className="mask" style={{ color: tone(r.pl) }}>
                      {fSignedBase(r.pl)}
                    </td>
                    <td style={{ color: tone(r.pl) }}>{r.plpct == null ? '—' : fPct(r.plpct)}</td>
                    <td className="not-money" style={{ color: tone(r.day), fontWeight: 600 }}>
                      {fPct(r.day)}
                    </td>
                    <td className="mk-act-cell not-money" onClick={(e) => e.stopPropagation()}>
                      {!merged && (
                        <KebabMenu
                          label={`${r.sym || r.name} actions`}
                          portal
                          items={[
                            { label: 'Record sell', onClick: () => onSell?.(r) },
                            { label: 'Record dividend', onClick: () => onDividend?.(r) },
                          ]}
                        />
                      )}
                    </td>
                  </>
                )
                if (!positionId) {
                  if (!instrumentId) {
                    return (
                      <tr
                        key={r.rowKey}
                        className="data drill"
                        onClick={() => onOpenAssetDetail?.(r)}
                      >
                        {cells}
                      </tr>
                    )
                  }
                  return (
                    <EntityRow
                      key={r.rowKey}
                      as="tr"
                      type="instrument"
                      id={instrumentId}
                      label={r.sym || r.name}
                      search={[r.sym, r.name].filter(Boolean).join(' ')}
                      item={r}
                      toFields={(it) => ({
                        symbol: it.sym || null,
                        name: it.name || null,
                        asset_class: it.cls || null,
                        // Per-share price is quoted in the instrument's OWN currency (it.ccy),
                        // not the portfolio base — show "$380.73"/"€35.81", never "S$380.73".
                        quote_currency: it.ccy || null,
                        latest_price: fUnitPrice(it.price, it.ccy, priceDigits(it.cls)),
                      })}
                      className="data drill"
                      onClick={() => onOpenAssetDetail?.(r)}
                    >
                      {cells}
                    </EntityRow>
                  )
                }
                return (
                  <EntityRow
                    key={r.rowKey}
                    as="tr"
                    type="holding"
                    id={positionId}
                    label={r.sym || r.name}
                    search={[r.sym, r.name, r.account].filter(Boolean).join(' ')}
                    item={r}
                    toFields={(it) => ({
                      symbol: it.sym || null,
                      name: it.name || null,
                      account: it.account || null,
                      asset_class: it.cls || null,
                      quantity: it.qty.toLocaleString(),
                      currency: it.ccy || null,
                      // Per-share price is quoted in the instrument's OWN currency (it.ccy),
                      // not the portfolio base — show "$380.73"/"€35.81", never "S$380.73".
                      price: fUnitPrice(it.price, it.ccy, priceDigits(it.cls)),
                      value_base: fBase(it.value),
                      cost_base: it.cost > 0 ? fBase(it.cost) : null,
                      unrealized_base: it.pl == null ? null : fBase(it.pl),
                      // day_change_base is a money field (descriptor type "money"); emit the
                      // absolute base-currency daily P&L, not the % move (it.day is a percent).
                      day_change_base:
                        it._raw?._daily_pl == null
                          ? null
                          : (it._raw._daily_pl >= 0 ? '+' : '−') +
                            fBase(Math.abs(it._raw._daily_pl)),
                    })}
                    className="data drill"
                    onClick={() => onOpenAssetDetail?.(r)}
                  >
                    {cells}
                  </EntityRow>
                )
              })}
            </tbody>
          ))}
          <tfoot>
            <tr>
              <td className="left" colSpan={6} style={{ textAlign: 'right' }}>
                Total
              </td>
              <td className="mask">{fBase(grand)}</td>
              <td>100%</td>
              <td colSpan={4} />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}
