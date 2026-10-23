import { Fragment, useMemo, useState, type ReactNode } from 'react'
import type { ComputedRow } from '../../types/portfolio'
import { Badge } from '../ui'
import { ASSET_CLASS_COLOR } from '../../utils/constants'
import {
  fDate,
  fPrice,
  fBase,
  fCurrency,
  fPct,
  plClass,
  getBaseCurrency,
  priceDigits,
} from '../../utils/format'
import { sortRows } from '../../hooks/usePortfolio'
import { LuciusAskButton } from './AskLucius'
import { EntityRow } from '../../lucius/EntityRow'
import { ChevronDown, ChevronRight } from '../../ui/icons'

// Human label + badge tone for each bond subtype.
const BOND_TYPE_LABEL: Record<string, string> = {
  market: 'Market',
  ssb: 'SSB',
  tbill: 'T-bill',
  etf: 'ETF',
  fd: 'FD',
}
const BOND_TYPE_TONE: Record<string, 'neutral' | 'pos' | 'muted'> = {
  market: 'neutral',
  ssb: 'pos',
  tbill: 'neutral',
  etf: 'muted',
  fd: 'neutral',
}

// Wrapper/form badge shown next to a holding's name. asset_class is the exposure (the grouping
// tab); this surfaces the form so a gold ETF under Commodities is still visibly an ETF.
const WRAPPER_LABEL: Record<string, string> = {
  etf: 'ETF',
  etc: 'ETC',
  etp: 'ETP',
  mutual_fund: 'Fund',
  reit: 'REIT',
  future: 'Future',
  physical: 'Physical',
  precious_metal: 'Physical',
}

// How to bucket rows when a group-by lens is active. 'none' = flat (the default / legacy shape).
export type GroupByKey = 'none' | 'asset_class' | 'account' | 'currency'

// Resolve the group key + a human label for a row, per the active lens.
function groupOf(row: ComputedRow, by: GroupByKey): { key: string; label: string } {
  if (by === 'asset_class') {
    const cls = row.asset_class || 'other'
    return { key: cls, label: cls.charAt(0).toUpperCase() + cls.slice(1).replace(/_/g, ' ') }
  }
  if (by === 'account') {
    // Groups by account name (r.category), intentionally not by institution/custodian —
    // per-wallet subtotals here, not per-custodian totals.
    const v = row.category || '—'
    return { key: v, label: v }
  }
  if (by === 'currency') {
    const c = (row.currency || getBaseCurrency()).toUpperCase()
    return { key: c, label: c }
  }
  return { key: '', label: '' }
}

interface Props {
  rows: ComputedRow[]
  showPL?: boolean
  searchable?: boolean
  filterable?: boolean
  showWeight?: boolean // show a Wt% column (share of weightTotal)
  weightTotal?: number // denominator for Wt% — typically the whole investment book
  // Group rows under collapsible headers with per-group subtotals. Default 'none' = flat,
  // byte-identical to the legacy table (so existing callers are unaffected).
  groupBy?: GroupByKey
  // Controlled sort. When supplied, the parent owns the sort state (so a multi-group view sorts
  // every group by one shared column); otherwise the table keeps its own internal sort.
  sortKey?: string
  sortDir?: number
  onSort?: (key: string) => void
  // When supplied, an actions column renders a "Sell" button per market position
  // (a row with a position_id). The parent opens the disposal flow.
  onRecordSale?: (row: ComputedRow) => void
  // When supplied, an actions column renders a "Dividend" button per market position
  // (P2-INV2). The parent opens the record-dividend flow.
  onRecordDividend?: (row: ComputedRow) => void
  // When supplied, an actions column renders a "Move" button per market position
  // so the user can reassign it to a different account.
  onMovePosition?: (row: ComputedRow) => void
  // When supplied, the symbol/name cell and a small icon in the actions cluster
  // become clickable affordances to open the per-asset intel detail view.
  // Hidden for rows that have no ticker (manual/cash/property positions).
  onOpenAssetDetail?: (row: ComputedRow) => void
  // Opt-in drill-down (P2-INV1): a market position row (one with a position_id) gets a
  // caret affordance; clicking it expands an inline detail row spanning the table width,
  // rendered by `renderExpanded(row)`. Default off, so existing callers (Cash.tsx) are
  // byte-identical. Used by the Investments tab for the per-holding lots / realized-P&L drill-down.
  expandable?: boolean
  renderExpanded?: (row: ComputedRow) => ReactNode
  // When true, a trailing ✦ "Ask Lucius about this holding" column is rendered for every market
  // position (a row with a position_id). Default off → zero layout cost for callers that don't
  // wire it. The chip stages a {type:'holding', ref:position_id} mention via the composer store.
  askable?: boolean
  // When true, replaces Qty/Cost/Price/Cur with bond-specific columns:
  // Type (badge), Coupon, Matures, Face. Intended for asset_class === 'bond' sections.
  bondColumns?: boolean
}

export function HoldingsTable({
  rows: initialRows,
  showPL = true,
  searchable,
  filterable,
  showWeight,
  weightTotal,
  groupBy = 'none',
  sortKey: sortKeyProp,
  sortDir: sortDirProp,
  onSort,
  onRecordSale,
  onRecordDividend,
  onMovePosition,
  onOpenAssetDetail,
  expandable,
  renderExpanded,
  askable,
  bondColumns,
}: Props) {
  const showActions =
    !!onRecordSale || !!onRecordDividend || !!onMovePosition || !!onOpenAssetDetail
  const canExpand = !!expandable && !!renderExpanded
  // Position ids of the currently-expanded drill-downs (drill-down mode only) — a transient
  // UI affordance, not persisted. Keyed by position_id so it survives a re-sort/re-group.
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set())
  // Internal sort state — used only when the parent doesn't drive sort (controlled mode).
  const [sortKeyState, setSortKey] = useState('_base')
  const [sortDirState, setSortDir] = useState(-1)
  const controlled = sortKeyProp !== undefined && onSort !== undefined
  const sortKey = controlled ? sortKeyProp! : sortKeyState
  const sortDir = controlled ? (sortDirProp ?? -1) : sortDirState
  const [search, setSearch] = useState('')
  const [account, setAccount] = useState('')
  // Collapsed group keys (group-by mode only) — a transient UI affordance, not persisted.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())

  const base = getBaseCurrency()
  const accounts = [...new Set(initialRows.map((r) => r.category))]

  const filtered = initialRows.filter((r) => {
    if (account && r.category !== account) return false
    if (
      search &&
      !((r.symbol || '') + r.label + r.note + (r.ticker || ''))
        .toLowerCase()
        .includes(search.toLowerCase())
    )
      return false
    return true
  })
  const rows = sortRows(filtered, sortKey, sortDir)

  function handleSort(key: string) {
    if (controlled) {
      onSort!(key)
      return
    }
    if (sortKey !== key) {
      setSortKey(key)
      setSortDir(-1)
    } else setSortDir((d) => d * -1)
  }

  function toggleGroup(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function toggleExpanded(positionId: string) {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(positionId)) next.delete(positionId)
      else next.add(positionId)
      return next
    })
  }

  function th(key: string, label: string) {
    const arrow = sortKey === key ? (sortDir > 0 ? '▲' : '▼') : ''
    return (
      <th key={key} data-k={key} onClick={() => handleSort(key)}>
        {label} <span className="ar">{arrow}</span>
      </th>
    )
  }

  // Total column count, so a group-header row can span the full width.
  // Bond columns replace qty/cost/price/cur with type/coupon/matures/face (same count = 4).
  const colCount =
    8 + // holding, account, qty/type, cost/coupon, price/matures, cur/face, total cost, value
    (canExpand ? 1 : 0) + // leading caret column
    (showWeight ? 1 : 0) +
    (showPL ? 4 : 0) +
    (showActions ? 1 : 0) +
    (askable ? 1 : 0) // trailing ✦ ask column

  // Weight as a share of the supplied book total (e.g. all investments), not this table's subtotal.
  const wpct = (v: number) => {
    if (!weightTotal) return '—'
    return fPct((v / weightTotal) * 100)
  }

  // Build the group list when a lens is active — order by descending group value, members already
  // in the post-sort `rows` order. Memoized off the post-filter/sort rows + lens.
  const groups = useMemo(() => {
    if (groupBy === 'none') return null
    const map = new Map<string, { label: string; rows: ComputedRow[]; total: number }>()
    for (const r of rows) {
      const g = groupOf(r, groupBy)
      let entry = map.get(g.key)
      if (!entry) {
        entry = { label: g.label, rows: [], total: 0 }
        map.set(g.key, entry)
      }
      entry.rows.push(r)
      entry.total += r._base || 0
    }
    return [...map.entries()].map(([key, v]) => ({ key, ...v })).sort((a, b) => b.total - a.total)
  }, [rows, groupBy])

  const totalBase = rows.reduce((s, r) => s + (r._base || 0), 0)
  const costRows = rows.filter((r) => r._cost_base != null)
  const totalCostBase = costRows.reduce((s, r) => s + (r._cost_base || 0), 0)
  const plRows = rows.filter((r) => r._pl != null)
  const totalPL = plRows.reduce((s, r) => s + (r._pl || 0), 0)
  const dayRows = rows.filter((r) => r._daily_pl != null)
  const totalDayPL = dayRows.reduce((s, r) => s + (r._daily_pl || 0), 0)

  function renderRow(r: ComputedRow, i: number) {
    // The native Qty/Cost/Price columns are the market quote, shown in the row's display
    // currency (_disp_ccy: the base currency for crypto, as a pair; else the quote currency).
    // Show them for a real priced holding that's either crypto or foreign-quoted; hide for a
    // base-currency cash/stock row where the base columns already say it all.
    const dispCcy = r._disp_ccy || r.quote_currency || r.currency
    const showQ = parseFloat(r.price) !== 1 && (r.asset_class === 'crypto' || dispCcy !== base)
    // A row is drillable only when drill-down is on AND it's a market position (has a position_id).
    const drillable = canExpand && !!r.position_id
    const isOpen = drillable && expandedRows.has(r.position_id!)
    const rowKey = r.position_id || `row-${i}`
    const accountName = r.account_name || r.institution || r.category
    // Sublabel leads with the base value, account secondary — mirrors the server holding
    // provider (services/mentions/providers.search_holding) so the DOM-harvested picker row
    // matches the backend-merged one.
    const subParts = [fBase(r._base)]
    if (accountName) subParts.push(accountName)
    const rowChildren = (
      <>
        {canExpand && (
          <td className="hold-drill-caret-col">
            {drillable && (
              <span className="hold-drill-caret">
                {isOpen ? (
                  <ChevronDown size="1em" aria-hidden="true" />
                ) : (
                  <ChevronRight size="1em" aria-hidden="true" />
                )}
              </span>
            )}
          </td>
        )}
        <td
          style={{
            maxWidth: '220px',
            borderLeft: `3px solid ${ASSET_CLASS_COLOR[r.asset_class] ?? 'var(--muted)'}`,
          }}
        >
          <span className="holding-cell" style={{ minWidth: 0, overflow: 'hidden' }}>
            <span className="holding-id">
              {onOpenAssetDetail && r.ticker ? (
                <button
                  type="button"
                  className="hold-intel-name-btn"
                  title={`View ${r.symbol || r.label} detail`}
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenAssetDetail(r)
                  }}
                >
                  <strong className="ui-truncate">{r.symbol || r.label}</strong>
                </button>
              ) : (
                <strong className="ui-truncate" title={r.symbol || r.label}>
                  {r.symbol || r.label}
                </strong>
              )}
              {WRAPPER_LABEL[r.asset_subclass ?? ''] && (
                <span className="wrapper-badge">{WRAPPER_LABEL[r.asset_subclass!]}</span>
              )}
              {r.vesting_state === 'unvested' && <Badge tone="muted">Locked</Badge>}
            </span>
            {r.symbol && (
              <span className="holding-name ui-truncate" title={r.label}>
                {r.label}
              </span>
            )}
            {r.note && (
              <span className="note ui-truncate" title={r.note}>
                {r.note}
              </span>
            )}
            {/* Inline bond meta badge — shown in non-bondColumns layouts so a bond is
                always identifiable even when grouped by account/currency (prototype L999). */}
            {!bondColumns && r.asset_class === 'bond' && (
              <span className="bond-meta-inline">
                {r.coupon_rate != null && r.coupon_freq && r.coupon_freq > 0
                  ? `${(r.coupon_rate * 100).toFixed(2)}%`
                  : null}
                {r.maturity_date ? ` • mat ${r.maturity_date.slice(0, 4)}` : ' • fund'}
              </span>
            )}
            {r.vesting_state === 'unvested' && r.ref_grant_last_vest && (
              <span className="note ui-truncate muted">
                Unvested grant • vests to {fDate(r.ref_grant_last_vest, 'short', 'always')}
              </span>
            )}
          </span>
        </td>
        <td>{r.account_name || r.institution || r.category}</td>
        {bondColumns ? (
          <>
            <td>
              {r.bond_type ? (
                <Badge tone={BOND_TYPE_TONE[r.bond_type] ?? 'neutral'}>
                  {BOND_TYPE_LABEL[r.bond_type] ?? r.bond_type}
                </Badge>
              ) : (
                <span className="muted">—</span>
              )}
            </td>
            <td>
              {r.coupon_freq === 0 ? (
                <Badge tone="muted">Zero-coupon</Badge>
              ) : r.coupon_rate != null ? (
                fPct(r.coupon_rate * 100)
              ) : (
                '—'
              )}
            </td>
            <td>
              {r.maturity_date ? (
                <>
                  {fDate(r.maturity_date, 'short', 'always')}
                  {r.days_to_maturity != null && (
                    <span className="bond-matures-in">({r.days_to_maturity}d)</span>
                  )}
                </>
              ) : (
                '—'
              )}
            </td>
            <td>{r.face_value != null ? fPrice(r.face_value, 2) : '—'}</td>
          </>
        ) : (
          <>
            <td>
              {showQ ? (+r.quantity).toLocaleString(undefined) + (r.unit ? ` ${r.unit}` : '') : '—'}
            </td>
            <td>
              {r._disp_unit_cost != null && showQ
                ? fPrice(r._disp_unit_cost, priceDigits(r.asset_class))
                : '—'}
            </td>
            <td>{showQ ? fPrice(r._disp_unit_price, priceDigits(r.asset_class)) : '—'}</td>
            <td>{r.asset_class === 'crypto' ? `${r.symbol || r.label}/${dispCcy}` : dispCcy}</td>
          </>
        )}
        <td>{r._cost_base != null ? fBase(r._cost_base) : '—'}</td>
        <td className={r._base < 0 ? 'neg' : ''}>{fBase(r._base)}</td>
        {showWeight && <td className="wt-col muted">{wpct(r._base)}</td>}
        {showPL &&
          (r.vesting_state === 'unvested' && r._ref_pl != null ? (
            <>
              <td
                className={`${plClass(r._ref_pl)} hold-ref-pl`}
                title="Since grant (based on grant price)"
              >
                {fBase(r._ref_pl)}
                <span className="hold-ref-marker"> sg</span>
              </td>
              <td
                className={`${plClass(r._ref_plpct)} hold-ref-pl`}
                title="Since grant (based on grant price)"
              >
                {fPct(r._ref_plpct, { sign: true })}
                <span className="hold-ref-marker"> sg</span>
              </td>
            </>
          ) : r._pl != null ? (
            <>
              <td className={plClass(r._pl)}>{fBase(r._pl)}</td>
              <td className={plClass(r._plpct)}>{fPct(r._plpct, { sign: true })}</td>
            </>
          ) : (
            <>
              <td className="muted">—</td>
              <td className="muted">—</td>
            </>
          ))}
        {showPL &&
          (r._daily_pl != null ? (
            <>
              <td className={plClass(r._daily_pl)}>{fBase(r._daily_pl)}</td>
              <td className={plClass(r._daily_plpct)}>{fPct(r._daily_plpct, { sign: true })}</td>
            </>
          ) : (
            <>
              <td className="muted">—</td>
              <td className="muted">—</td>
            </>
          ))}
        {showActions && (
          <td className="hold-actions-col">
            {r.position_id && onRecordSale && (
              <button
                className="hold-sell-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onRecordSale(r)
                }}
                title="Record a sale / disposal"
              >
                Sell
              </button>
            )}
            {r.position_id && onRecordDividend && (
              <button
                className="hold-sell-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onRecordDividend(r)
                }}
                title="Record a dividend / income"
              >
                Dividend
              </button>
            )}
            {r.position_id && onMovePosition && (
              <button
                className="hold-sell-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onMovePosition(r)
                }}
                title="Move to another account"
              >
                Move
              </button>
            )}
          </td>
        )}
        {askable && (
          <td className="hold-ask-col">
            {r.position_id && (
              <LuciusAskButton
                type="holding"
                refId={r.position_id}
                label={`${r.symbol || r.label}${r.label && r.symbol ? ` ${r.label}` : ''}`.trim()}
                className="hold-ask-btn"
              />
            )}
          </td>
        )}
      </>
    )
    const dataRow = r.position_id ? (
      <EntityRow
        key={rowKey}
        type="holding"
        id={r.position_id}
        label={(r.symbol || r.label).trim()}
        item={r}
        sublabel={subParts.join(' • ')}
        toFields={(it) => ({
          symbol: it.symbol,
          name: it.label,
          account: it.account_name || it.institution || it.category,
          asset_class: it.asset_class,
          quantity: it.quantity,
          currency: it.currency,
          // Per-share price in the instrument's OWN quote currency (e.g. "$380.73"), not a bare
          // number — so the @-mention chip shows a currency-correct price like the feed does.
          price:
            it._disp_unit_price != null
              ? fCurrency(it._disp_unit_price, it._disp_ccy || it.currency)
              : null,
          value_base: it._base,
          cost_base: it._cost_base,
          unrealized_base: it._pl,
          day_change_base: it._daily_pl,
        })}
        as="tr"
        className={drillable ? 'hold-drill-row' : undefined}
        onClick={drillable ? () => toggleExpanded(r.position_id!) : undefined}
        aria-expanded={drillable ? isOpen : undefined}
        style={drillable ? { cursor: 'pointer' } : undefined}
      >
        {rowChildren}
      </EntityRow>
    ) : (
      <tr
        key={rowKey}
        className={drillable ? 'hold-drill-row' : undefined}
        onClick={drillable ? () => toggleExpanded(r.position_id!) : undefined}
        aria-expanded={drillable ? isOpen : undefined}
        style={drillable ? { cursor: 'pointer' } : undefined}
      >
        {rowChildren}
      </tr>
    )
    if (!isOpen) return dataRow
    return (
      <Fragment key={rowKey}>
        {dataRow}
        <tr className="hold-drill-detail-row">
          <td colSpan={colCount}>{renderExpanded!(r)}</td>
        </tr>
      </Fragment>
    )
  }

  return (
    <>
      {(searchable || filterable) && (
        <div className="toolbar">
          {searchable && (
            <input
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
          {filterable && (
            <select value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">All accounts</option>
              {accounts.map((a) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          )}
        </div>
      )}
      <div className="holdings-table-scroll">
        <table>
          <thead>
            <tr>
              {canExpand && <th className="hold-drill-caret-col" />}
              {th('label', 'Holding')}
              {th('category', 'Account')}
              {bondColumns ? (
                <>
                  <th>Type</th>
                  <th>Coupon</th>
                  <th>Matures</th>
                  <th>Face</th>
                </>
              ) : (
                <>
                  {th('quantity', 'Qty')}
                  {th('cost_basis', 'Cost')}
                  {th('price', 'Price')}
                  {th('currency', 'Cur')}
                </>
              )}
              {th('_cost_base', 'Total Cost')}
              {th('_base', `Value (${base})`)}
              {showWeight && <th className="wt-col">Wt %</th>}
              {showPL && th('_pl', `P&L (${base})`)}
              {showPL && th('_plpct', 'P&L %')}
              {showPL && th('_daily_pl', `Day (${base})`)}
              {showPL && th('_daily_plpct', 'Day %')}
              {showActions && <th className="hold-actions-col" />}
              {askable && <th className="hold-ask-col" aria-hidden="true" />}
            </tr>
          </thead>
          {groups ? (
            groups.map((g) => {
              const isCollapsed = collapsed.has(g.key)
              return (
                <tbody key={g.key} className="hold-group">
                  <tr
                    className="hold-group-head"
                    onClick={() => toggleGroup(g.key)}
                    aria-expanded={!isCollapsed}
                  >
                    {/* Span everything up to the Value column, then carry the subtotal in
                        Value (+ Wt%) so the group's number sits under its column header. */}
                    <td colSpan={colCount - (showWeight ? 2 : 1)} className="hold-group-label">
                      <span className="hold-group-caret">
                        {isCollapsed ? (
                          <ChevronRight size="1em" aria-hidden="true" />
                        ) : (
                          <ChevronDown size="1em" aria-hidden="true" />
                        )}
                      </span>
                      <span className="hold-group-name">{g.label}</span>
                      <span className="hold-group-count">{g.rows.length}</span>
                    </td>
                    <td className={g.total < 0 ? 'neg' : ''}>{fBase(g.total)}</td>
                    {showWeight && <td className="wt-col muted">{wpct(g.total)}</td>}
                  </tr>
                  {!isCollapsed && g.rows.map((r, i) => renderRow(r, i))}
                </tbody>
              )
            })
          ) : (
            <tbody>{rows.map((r, i) => renderRow(r, i))}</tbody>
          )}
          <tfoot>
            <tr className="tfoot">
              <td colSpan={canExpand ? 7 : 6} style={{ textAlign: 'right' }}>
                Total
              </td>
              <td>{costRows.length ? fBase(totalCostBase) : '—'}</td>
              <td className={totalBase < 0 ? 'neg' : ''}>{fBase(totalBase)}</td>
              {showWeight && <td className="wt-col muted">{wpct(totalBase)}</td>}
              {showPL &&
                (plRows.length ? (
                  <>
                    <td className={plClass(totalPL)}>{fBase(totalPL)}</td>
                    <td className={plClass(totalPL)}>—</td>
                  </>
                ) : (
                  <>
                    <td className="muted">—</td>
                    <td className="muted">—</td>
                  </>
                ))}
              {showPL &&
                (dayRows.length ? (
                  <>
                    <td className={plClass(totalDayPL)}>{fBase(totalDayPL)}</td>
                    <td className={plClass(totalDayPL)}>—</td>
                  </>
                ) : (
                  <>
                    <td className="muted">—</td>
                    <td className="muted">—</td>
                  </>
                ))}
              {showActions && <td className="hold-actions-col" />}
              {askable && <td className="hold-ask-col" />}
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  )
}
