import { Fragment, useEffect, useState } from 'react'
import type {
  Lot,
  RealizedPnl,
  RealizedEvent,
  IncomeEvent,
  IncomeSummary,
} from '../../types/portfolio'
import {
  fAmountNative,
  fBase,
  fDate,
  fUnitCostNative,
  getBaseCurrency,
  plClass,
} from '../../utils/format'
import { authFetch } from '../../api/client'
import { EntityRow } from '../../lucius/EntityRow'
import { Button, Loading, Panel } from '../ui'
import { AcquireLotForm } from './AcquireLotForm'
import { DisposalForm } from './DisposalForm'
import { WhichAccountModal, type WriteTarget } from './WhichAccountModal'
import { Plus, ChevronDown, ChevronRight } from '../../ui/icons'

export type { WriteTarget }

interface Props {
  /** Null for a multi-account holding — see `merged` below. */
  positionId: string | null
  /** Instrument id — required to add a first lot when no position exists yet. */
  instrumentId?: string
  /** Instrument symbol/ticker — the label for each lot/disposal row's Lucius mention entity. */
  symbol: string
  /** Cost/settlement currency, shown in the Unit cost / Fees column headers. */
  costCcy: string
  /** Position market value in the base currency — apportioned across lots for per-lot unrealized. */
  base: number
  /** Position cost basis in the base currency (null when unknown). */
  costBase: number | null
  /** Pre-merged lots/realized/income for a multi-account holding — asset_detail.py's
   *  `_position_block` already combines these across every contributing position when
   *  `positionId` is null, so LotsPanel renders them directly instead of fetching (there's
   *  no single position_id to fetch by). Each row carries `account_name`, so every table
   *  gains an Account column to keep the cross-account mix legible. */
  merged?: { lots: Lot[]; realized: RealizedEvent[]; income: IncomeEvent[] }
  /** Per-account write targets when the holding is merged (or a closed history). */
  writeTargets?: WriteTarget[]
  /** Refetch the parent asset-detail after a lot/sale write. */
  onChanged?: () => void
}

export function LotsPanel({
  positionId,
  instrumentId,
  symbol,
  costCcy,
  base,
  costBase,
  merged,
  writeTargets = [],
  onChanged,
}: Props) {
  const [fetchedLots, setFetchedLots] = useState<Lot[] | null>(null)
  const [lotsError, setLotsError] = useState(false)
  const [fetchedRealized, setFetchedRealized] = useState<RealizedEvent[] | null>(() =>
    positionId ? null : [],
  )
  const [realizedBase, setRealizedBase] = useState('')
  const [realizedError, setRealizedError] = useState(false)
  const [fetchedDividends, setFetchedDividends] = useState<IncomeEvent[] | null>(() =>
    positionId ? null : [],
  )
  const [dividendsBase, setDividendsBase] = useState('')
  const [dividendsError, setDividendsError] = useState(false)
  const [tick, setTick] = useState(0)
  const [adding, setAdding] = useState(false)
  const [editLot, setEditLot] = useState<Lot | null>(null)
  const [saleFor, setSaleFor] = useState<{
    positionId: string
    event?: RealizedEvent
  } | null>(null)
  const [pickingSale, setPickingSale] = useState(false)

  function afterWrite() {
    setTick((n) => n + 1)
    onChanged?.()
  }

  useEffect(() => {
    if (merged || !positionId) return
    let alive = true
    // A sold-out-then-rebought holding lists its earlier (sold) lots too, like one that
    // never split — the same with_predecessors scope as the realized/dividend reads below.
    authFetch(`/api/positions/${positionId}/lots?with_predecessors=true`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { lots: Lot[] }) => {
        if (alive) setFetchedLots(d.lots || [])
      })
      .catch(() => {
        if (alive) setLotsError(true)
      })
    return () => {
      alive = false
    }
  }, [positionId, merged, tick])

  useEffect(() => {
    if (merged || !positionId) return
    let alive = true
    // Scope realized history to THIS position — not the instrument — so a disposal never
    // bleeds onto a sibling position of the same instrument (e.g. the unvested-grant row
    // showing a sale that came from the sellable holding). with_predecessors keeps the sale of
    // a holding sold out and rebought at the same account, which the rebuy left on its old row.
    const params = new URLSearchParams({ position_id: positionId, with_predecessors: 'true' })
    authFetch(`/api/realized-pnl?${params.toString()}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: RealizedPnl) => {
        if (alive) {
          setFetchedRealized(d.events || [])
          setRealizedBase(d.base_currency || '')
        }
      })
      .catch(() => {
        if (alive) setRealizedError(true)
      })
    return () => {
      alive = false
    }
  }, [positionId, merged, tick])

  useEffect(() => {
    if (merged || !positionId) return
    let alive = true
    // Scoped to THIS position (and its sold-out predecessors), same as realized above — a
    // position-scoped path, not a query param (income.py's GET /api/positions/{id}/income).
    authFetch(`/api/positions/${positionId}/income?with_predecessors=true`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: IncomeSummary) => {
        if (alive) {
          setFetchedDividends(d.events || [])
          setDividendsBase(d.base_currency || '')
        }
      })
      .catch(() => {
        if (alive) setDividendsError(true)
      })
    return () => {
      alive = false
    }
  }, [positionId, merged, tick])

  // Merged (multi-account) data wins over a fetch — merged is already loaded synchronously
  // from the asset-detail response, so there's nothing to fetch in that case at all.
  const lots = merged ? merged.lots : fetchedLots
  const realized = merged ? merged.realized : fetchedRealized
  const dividends = merged ? merged.income : fetchedDividends
  const showAccount = !!merged

  // Every lot is history: a fully sold one stays listed (marked Sold) instead of vanishing,
  // so a holding sold out and bought back still shows its earlier buys (found 2026-10-12).
  const openLots = lots ? lots.filter((l) => l.remaining_qty > 0) : null
  const closedCount = lots && openLots ? lots.length - openLots.length : 0
  const lotsTitle =
    lots && openLots
      ? closedCount
        ? `Acquisition lots (${openLots.length} open · ${closedCount} closed)`
        : `Acquisition lots (${lots.length})`
      : 'Acquisition lots'
  const realizedCount = realized ? realized.length : null
  const dividendsCount = dividends ? dividends.length : null

  const targets = writeTargets.length
    ? writeTargets
    : lots
      ? uniqueTargets(lots, realized || [])
      : []

  function startAddLot() {
    setAdding(true)
  }

  function startRecordSale() {
    if (positionId) {
      setSaleFor({ positionId })
      return
    }
    const only = targets[0]
    if (targets.length === 1 && only) {
      setSaleFor({ positionId: only.positionId })
      return
    }
    if (targets.length > 1) setPickingSale(true)
  }

  const defaultAccountId =
    (positionId && targets.find((t) => t.positionId === positionId)?.accountId) ||
    (targets.length === 1 ? targets[0]?.accountId : undefined)

  return (
    <div className="lots-panel">
      <Panel
        title={lotsTitle}
        actions={
          <Button
            variant="ghost"
            size="sm"
            iconLeft={<Plus size={13} aria-hidden="true" />}
            onClick={startAddLot}
          >
            Add lot
          </Button>
        }
      >
        <LotsSection
          lots={lots}
          openLots={openLots}
          error={lotsError}
          symbol={symbol}
          costCcy={costCcy}
          positionBase={base}
          positionCostBase={costBase}
          showAccount={showAccount}
          onEditLot={setEditLot}
          onAddLot={startAddLot}
        />
      </Panel>

      <Panel
        title={realizedCount != null ? `Realized history (${realizedCount})` : 'Realized history'}
        actions={
          <Button
            variant="ghost"
            size="sm"
            iconLeft={<Plus size={13} aria-hidden="true" />}
            onClick={startRecordSale}
          >
            Record sale
          </Button>
        }
      >
        <RealizedSection
          events={realized}
          error={realizedError}
          baseCurrency={realizedBase}
          symbol={symbol}
          showAccount={showAccount}
          onEditSale={(ev) => setSaleFor({ positionId: ev.position_id, event: ev })}
        />
      </Panel>

      <Panel title={dividendsCount != null ? `Dividends (${dividendsCount})` : 'Dividends'}>
        <DividendsSection
          events={dividends}
          error={dividendsError}
          baseCurrency={dividendsBase}
          symbol={symbol}
          showAccount={showAccount}
        />
      </Panel>
      {adding && (
        <AcquireLotForm
          symbol={symbol}
          costCcy={costCcy}
          instrumentId={instrumentId}
          lockSymbol
          defaultAccountId={defaultAccountId}
          onDone={afterWrite}
          onClose={() => setAdding(false)}
        />
      )}
      {editLot && (
        <AcquireLotForm
          symbol={symbol}
          costCcy={costCcy}
          instrumentId={instrumentId}
          lockSymbol
          lot={editLot}
          onDone={afterWrite}
          onClose={() => setEditLot(null)}
        />
      )}
      {saleFor && (
        <DisposalForm
          row={{
            position_id: saleFor.positionId,
            symbol,
            label: symbol,
            currency: costCcy,
          }}
          event={saleFor.event}
          onDone={afterWrite}
          onClose={() => setSaleFor(null)}
        />
      )}
      {pickingSale && (
        <WhichAccountModal
          targets={targets}
          onClose={() => setPickingSale(false)}
          onPick={(t) => {
            setPickingSale(false)
            setSaleFor({ positionId: t.positionId })
          }}
        />
      )}
    </div>
  )
}

function uniqueTargets(lots: Lot[], events: RealizedEvent[]): WriteTarget[] {
  const byId = new Map<string, WriteTarget>()
  for (const row of [...lots, ...events]) {
    if (!row.position_id || byId.has(row.position_id)) continue
    byId.set(row.position_id, {
      positionId: row.position_id,
      accountId: row.account_id,
      accountName: row.account_name || row.account_id,
    })
  }
  return [...byId.values()]
}

// ── Acquisition lots ────────────────────────────────────────────────────────────
function LotsSection({
  lots,
  openLots,
  error,
  symbol,
  costCcy,
  positionBase,
  positionCostBase,
  showAccount,
  onEditLot,
  onAddLot,
}: {
  lots: Lot[] | null
  openLots: Lot[] | null
  error: boolean
  symbol: string
  costCcy: string
  positionBase: number
  positionCostBase: number | null
  /** Multi-account holding — add an Account column so the cross-account mix stays legible. */
  showAccount: boolean
  onEditLot: (lot: Lot) => void
  onAddLot: () => void
}) {
  if (error) return <div className="lots-empty">Could not load lots for this holding.</div>
  if (lots === null) return <Loading />
  if (!lots.length || !openLots) {
    return (
      <div className="lots-empty">
        No lots recorded for this holding yet.{' '}
        <Button variant="secondary" size="sm" onClick={onAddLot}>
          Add lot
        </Button>
      </div>
    )
  }

  // Unrealized is apportioned over OPEN lots only — a sold lot has no market value.
  const totalRemainingQty = openLots.reduce((s, l) => s + l.remaining_qty, 0)
  const costKnownLots = openLots.filter((l) => l.remaining_basis != null)
  const totalRemainingBasis = costKnownLots.reduce((s, l) => s + (l.remaining_basis || 0), 0)

  function lotMarketBase(l: Lot): number {
    if (totalRemainingQty <= 0) return 0
    return positionBase * (l.remaining_qty / totalRemainingQty)
  }
  function lotCostBase(l: Lot): number | null {
    if (l.remaining_basis == null || positionCostBase == null) return null
    if (totalRemainingBasis <= 0) return 0
    return positionCostBase * (l.remaining_basis / totalRemainingBasis)
  }
  function lotUnrealizedBase(l: Lot): number | null {
    const cost = lotCostBase(l)
    if (cost == null) return null
    return lotMarketBase(l) - cost
  }

  const base = getBaseCurrency()

  return (
    <>
      <div style={{ overflowX: 'auto' }}>
        <table
          className={`lots-table lots-table--acq${showAccount ? ' lots-table--acct-col' : ''}`}
        >
          <thead>
            <tr>
              <th>Acquired</th>
              <th>Original</th>
              <th>Remaining</th>
              <th>Unit cost{costCcy ? ` (${costCcy})` : ''}</th>
              <th>Fees</th>
              <th>Kind</th>
              <th>Term</th>
              <th>Unrealized ({base})</th>
              {showAccount && <th>Account</th>}
            </tr>
          </thead>
          <tbody>
            {lots.map((l) => {
              const eff = l.effective_unit_cost
              const closed = l.remaining_qty <= 0
              const gain = closed ? null : lotUnrealizedBase(l)
              return (
                // Lucius mention entity: a draggable lot the user can @-mention / drag into the dock.
                <EntityRow
                  key={l.id}
                  as="tr"
                  type="lot"
                  id={l.id}
                  label={symbol}
                  sublabel={`${kindLabel(l.kind)} • ${l.remaining_qty} left • ${fDate(l.acquired_at, 'short', 'always')}`}
                  item={l}
                  toFields={(it) => ({ symbol, kind: it.kind, acquired_at: it.acquired_at })}
                  className={`lots-row--clickable${closed ? ' lots-row--closed' : ''}`}
                  onClick={() => onEditLot(l)}
                >
                  <td>{l.acquired_at ? fDate(l.acquired_at, 'short', 'always') : '—'}</td>
                  <td>{l.original_qty.toLocaleString(undefined)}</td>
                  <td>
                    {l.remaining_qty.toLocaleString(undefined)}
                    {closed && <span className="lots-closed-badge">Sold</span>}
                  </td>
                  <td>
                    {eff == null ? (
                      <span className="lots-unknown">Unknown</span>
                    ) : (
                      fUnitCostNative(eff, l.cost_currency || costCcy)
                    )}
                  </td>
                  <td>{l.fees ? fAmountNative(l.fees, l.cost_currency || costCcy) : '—'}</td>
                  <td>{kindLabel(l.kind)}</td>
                  <td>
                    {closed ? (
                      '—'
                    ) : (
                      <span
                        className={`term-badge term-${l.term_to_date}`}
                        title={termTitle(l.term_to_date)}
                      >
                        {l.term_to_date}
                      </span>
                    )}
                  </td>
                  <td className={gain == null ? 'muted' : plClass(gain)}>
                    {gain == null ? '—' : (gain >= 0 ? '+' : '') + fBase(gain)}
                  </td>
                  {showAccount && <td>{l.account_name || '—'}</td>}
                </EntityRow>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="lots-note">
        Unrealized gain = current market value − cost-known basis, in the base currency (recomputed
        from the native leg).
      </div>
    </>
  )
}

// ── Realized history ─────────────────────────────────────────────────────────────
function RealizedSection({
  events,
  error,
  baseCurrency,
  symbol,
  showAccount,
  onEditSale,
}: {
  events: RealizedEvent[] | null
  error: boolean
  baseCurrency: string
  symbol: string
  /** Multi-account holding — add an Account column so the cross-account mix stays legible. */
  showAccount: boolean
  onEditSale: (event: RealizedEvent) => void
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  if (error)
    return <div className="lots-empty">Could not load realized history for this holding.</div>
  if (events === null) return <Loading />
  if (!events.length)
    return <div className="lots-empty">No disposals recorded for this holding yet.</div>

  const base = baseCurrency || getBaseCurrency()
  const columns = showAccount ? 8 : 7
  const knownGains = events.filter((e) => e.realized_gain_base != null)
  const total = knownGains.reduce((s, e) => s + (e.realized_gain_base || 0), 0)
  const hasUnknown = events.some((e) => e.realized_gain_base == null)

  return (
    <>
      <div style={{ overflowX: 'auto' }}>
        <table
          className={`lots-table lots-table--rel${showAccount ? ' lots-table--acct-col' : ''}`}
        >
          <thead>
            <tr>
              <th>Disposed</th>
              <th>Qty</th>
              <th>Buy price</th>
              <th>Sell price</th>
              <th>Method</th>
              <th>Term</th>
              <th>Realized ({base})</th>
              {showAccount && <th>Account</th>}
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => {
              const gain = ev.realized_gain_base
              const buy = avgBuyPrice(ev)
              const term = eventTerm(ev)
              const isOpen = expanded.has(ev.id)
              return (
                <Fragment key={ev.id}>
                  {/* Lucius mention entity: a draggable disposal the user can @-mention / drag into the dock. */}
                  <EntityRow
                    as="tr"
                    type="disposal"
                    id={ev.id}
                    label={symbol}
                    sublabel={`${ev.quantity} units • ${fDate(ev.disposed_at, 'short', 'always')}`}
                    item={ev}
                    toFields={(it) => ({ symbol, method: it.method, disposed_at: it.disposed_at })}
                    className="lots-row--clickable"
                    onClick={() => onEditSale(ev)}
                  >
                    <td>
                      {ev.consumptions.length > 0 && (
                        <button
                          type="button"
                          className="lots-expand-btn"
                          aria-expanded={isOpen}
                          aria-label={
                            isOpen ? 'Hide the lots this sale used' : 'Show the lots this sale used'
                          }
                          onClick={(e) => {
                            e.stopPropagation()
                            toggle(ev.id)
                          }}
                        >
                          {isOpen ? (
                            <ChevronDown size={12} aria-hidden="true" />
                          ) : (
                            <ChevronRight size={12} aria-hidden="true" />
                          )}
                        </button>
                      )}
                      {fDate(ev.disposed_at, 'short', 'always')}
                    </td>
                    <td>{ev.quantity.toLocaleString(undefined)}</td>
                    <td className="muted">
                      {buy.price == null
                        ? '—'
                        : fUnitCostNative(buy.price, buy.currency ?? ev.proceeds_currency)}
                    </td>
                    <td>{fUnitCostNative(ev.proceeds_per_unit, ev.proceeds_currency)}</td>
                    <td>{ev.method === 'specific_id' ? 'Specific' : 'FIFO'}</td>
                    <td>
                      <span className={`term-badge term-${term}`} title={termTitle(term)}>
                        {term}
                      </span>
                    </td>
                    <td className={gain == null ? 'muted' : plClass(gain)}>
                      {gain == null ? '—' : (gain >= 0 ? '+' : '') + fBase(gain)}
                    </td>
                    {showAccount && <td>{ev.account_name || '—'}</td>}
                  </EntityRow>
                  {isOpen && (
                    <tr className="lots-consumption-row">
                      <td colSpan={columns}>
                        <ConsumptionTable event={ev} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {/* Same fix as the Dividends card's total line below — a plain summary line under the
       table, right-aligned to the card's edge, not a <tfoot> cell trying to land under the
       Realized column (colspan math against a seven/eight column header was the same brittle
       shape that was visibly misaligned there). */}
      <div className="lots-total-line">
        Total realized{hasUnknown ? ' (known)' : ''}:{' '}
        <span className={plClass(total)}>{(total >= 0 ? '+' : '') + fBase(total)}</span>
      </div>
    </>
  )
}

// ── Dividend / income history ─────────────────────────────────────────────────────
function DividendsSection({
  events,
  error,
  baseCurrency,
  symbol,
  showAccount,
}: {
  events: IncomeEvent[] | null
  error: boolean
  baseCurrency: string
  symbol: string
  /** Multi-account holding — add an Account column so the cross-account mix stays legible. */
  showAccount: boolean
}) {
  if (error)
    return <div className="lots-empty">Could not load dividend history for this holding.</div>
  if (events === null) return <Loading />
  if (!events.length)
    return <div className="lots-empty">No dividends recorded for this holding yet.</div>

  const base = baseCurrency || getBaseCurrency()
  const total = events.reduce((s, e) => s + (e.base_amount ?? 0), 0)
  const hasUnknown = events.some((e) => e.base_amount === null)

  return (
    <>
      <div style={{ overflowX: 'auto' }}>
        <table
          className={`lots-table lots-table--div${showAccount ? ' lots-table--acct-col' : ''}`}
        >
          <thead>
            <tr>
              <th>Paid</th>
              <th>Kind</th>
              <th>Gross</th>
              <th>Tax withheld</th>
              <th>Net ({base})</th>
              <th>Method</th>
              {showAccount && <th>Account</th>}
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              // Lucius mention entity: a draggable income event, same `income` type RecentIncomePanel uses.
              <EntityRow
                key={ev.id}
                as="tr"
                type="income"
                id={ev.id}
                label={symbol}
                sublabel={`${incomeKindLabel(ev.income_kind)} • ${fDate(ev.paid_at, 'short', 'always')}`}
                item={ev}
                toFields={(it) => ({ symbol, income_kind: it.income_kind, paid_at: it.paid_at })}
              >
                <td>{ev.paid_at ? fDate(ev.paid_at, 'short', 'always') : '—'}</td>
                <td>{incomeKindLabel(ev.income_kind)}</td>
                <td>{fAmountNative(ev.gross_amount, ev.currency)}</td>
                <td>{ev.tax_withheld ? fAmountNative(ev.tax_withheld, ev.currency) : '—'}</td>
                <td className={ev.base_amount === null ? 'muted' : undefined}>
                  {ev.base_amount === null
                    ? `${fAmountNative(ev.native_net_amount ?? ev.gross_amount - ev.tax_withheld, ev.currency)} (FX unavailable)`
                    : fBase(ev.base_amount)}
                </td>
                <td>{ev.is_drp ? 'DRP' : 'Cash'}</td>
                {showAccount && <td>{ev.account_name || '—'}</td>}
              </EntityRow>
            ))}
          </tbody>
        </table>
      </div>
      {/* A plain summary line under the table, right-aligned to the card's edge — not tied to
       any column position (a colspan/grid match against six+ header widths is brittle and was
       visibly misaligned in practice). */}
      <div className="lots-total-line">
        Total received{hasUnknown ? ' (known)' : ''}:{' '}
        <span className={plClass(total)}>{fBase(total)}</span>
      </div>
    </>
  )
}

function incomeKindLabel(kind: string): string {
  switch (kind) {
    case 'dividend':
      return 'Dividend'
    case 'interest':
      return 'Interest'
    case 'distribution':
      return 'Distribution'
    default:
      return kind
  }
}

function kindLabel(kind: string): string {
  switch (kind) {
    case 'buy':
      return 'Buy'
    case 'drp':
      return 'Dividend reinvest'
    case 'transfer_in':
      return 'Transfer in'
    case 'grant':
      return 'Grant'
    case 'opening':
      return 'Opening'
    default:
      return kind.replace(/_/g, ' ')
  }
}

// Weighted-average cost per unit across all consumed lots for a disposal.
// Returns null when no lot has a known cost basis.
function avgBuyPrice(ev: RealizedEvent): { price: number | null; currency: string | null } {
  const known = ev.consumptions.filter((c) => c.unit_cost != null)
  const first = known[0]
  if (!first) return { price: null, currency: null }
  const totalQty = known.reduce((s, c) => s + c.qty, 0)
  const weighted = known.reduce((s, c) => s + c.qty * (c.unit_cost ?? 0), 0)
  return { price: totalQty > 0 ? weighted / totalQty : null, currency: first.cost_currency }
}

function termTitle(term: string): string {
  switch (term) {
    case 'short':
      return 'Short-term: held < 1 year. Usually taxed at ordinary income rates.'
    case 'long':
      return 'Long-term: held > 1 year. Usually taxed at preferential capital gains rates.'
    case 'mixed':
      return 'The lots this sale used have different holding periods — expand the row to see each.'
    default:
      return 'Holding period unknown — no acquisition date recorded.'
  }
}

/** One term for the whole sale when every lot it used agrees; 'mixed' when they differ (a
 *  sale across an undated opening lot and dated buys used to read "unknown"). */
function eventTerm(ev: RealizedEvent): 'long' | 'short' | 'unknown' | 'mixed' {
  const terms = new Set(ev.consumptions.map((c) => c.term))
  if (terms.size > 1) return 'mixed'
  const [only] = [...terms]
  return only === 'long' || only === 'short' ? only : 'unknown'
}

/** The lots one sale consumed, oldest first — what the row's weighted "Buy price" averages. */
function ConsumptionTable({ event }: { event: RealizedEvent }) {
  return (
    <table className="lots-consumption-table">
      <thead>
        <tr>
          <th>Lot acquired</th>
          <th>Qty</th>
          <th>Unit cost</th>
          <th>Term</th>
          <th>Gain ({event.proceeds_currency})</th>
        </tr>
      </thead>
      <tbody>
        {event.consumptions.map((c, i) => (
          <tr key={`${c.lot_id}-${i}`}>
            <td>{c.acquired_at ? fDate(c.acquired_at, 'short', 'always') : 'Date unknown'}</td>
            <td>{c.qty.toLocaleString(undefined)}</td>
            <td>
              {c.unit_cost == null ? (
                <span className="lots-unknown">Unknown</span>
              ) : (
                fUnitCostNative(c.unit_cost, c.cost_currency || event.proceeds_currency)
              )}
            </td>
            <td>
              <span className={`term-badge term-${c.term}`} title={termTitle(c.term)}>
                {c.term}
              </span>
            </td>
            <td className={c.realized_gain == null ? 'muted' : plClass(c.realized_gain)}>
              {c.realized_gain == null
                ? '—'
                : (c.realized_gain >= 0 ? '+' : '') +
                  fAmountNative(c.realized_gain, event.proceeds_currency)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
