import { useEffect, useState } from 'react'
import type { ComputedRow, Lot, RealizedEvent } from '../../types/portfolio'
import { fAmountNative, fBase, fDate, plClass } from '../../utils/format'
import { authFetch } from '../../api/client'
import { ACCOUNTS_KEY, fetchJson, notifyPortfolioChanged } from '../../hooks/serverState'
import { fetchCachedQuery } from '../../state/queryCache'
import { DateField, Modal } from '../ui'

/** Enough of a holding to open the sale form from Investments or the quote page. */
export type DisposalTarget = Pick<ComputedRow, 'position_id' | 'symbol' | 'label' | 'currency'> &
  Partial<Pick<ComputedRow, 'account_id' | 'category' | 'liquidity'>>

interface Props {
  row: DisposalTarget
  /** When set, this is an edit: PATCH /api/disposals/{id} (undo+rebook). */
  event?: RealizedEvent | null
  // Called after a disposal is recorded so the parent can refresh the portfolio.
  onDone: () => void
  onClose: () => void
}

type Method = 'fifo' | 'specific_id'

// Local-date YYYY-MM-DD (NOT toISOString, which formats in UTC and shifts the date a day
// early for users behind UTC near midnight). disposed_at drives the holding-period term +
// tax period, so the default must be the user's local calendar date. (INV1-3)
// Exported (not a component) so the pure logic is unit-testable; harmless to fast-refresh.
// eslint-disable-next-line react-refresh/only-export-components
export function toLocalISODate(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// Disposal-route success payload (services/lots._apply_dispose). We surface the booked
// realized gain; the full event is read back via /api/realized-pnl on the Investments tab.
interface DisposalResult {
  ok?: boolean
  event_id?: string
  realized_gain?: number | null
  realized_gain_base?: number | null
  proceeds_account_id?: string | null
  transfer_to_account_id?: string | null
  cash_leg_id?: string | null
  error?: string
}

// The cash accounts the net proceeds can be deposited into (the optional INV1-2 picker).
interface AccountLite {
  id: string
  name: string
  kind: string
}

// Record a sale / disposal of a holding. Lot selection is FIFO (default) or specific-ID
// (per the INV1 RFC §11): the user picks lots + per-lot qty. ALL gain arithmetic is the
// service's (POST /api/positions/{id}/disposals → apply_lot_change); this form only
// collects input and renders the realized gain the service returns.
function lotsWithRestoredQty(lots: Lot[], event?: RealizedEvent | null): Lot[] {
  if (!event) return lots
  const extra = new Map<string, number>()
  for (const c of event.consumptions) {
    extra.set(c.lot_id, (extra.get(c.lot_id) || 0) + c.qty)
  }
  return lots.map((l) => {
    const add = extra.get(l.id)
    if (!add) return l
    return { ...l, remaining_qty: l.remaining_qty + add }
  })
}

function picksFromEvent(event?: RealizedEvent | null): Record<string, string> {
  if (!event || event.method !== 'specific_id') return {}
  const next: Record<string, string> = {}
  for (const c of event.consumptions) {
    next[c.lot_id] = String((parseFloat(next[c.lot_id] || '0') || 0) + c.qty)
  }
  return next
}

export function DisposalForm({ row, event, onDone, onClose }: Props) {
  const positionId = row.position_id || event?.position_id || ''
  const proceedsCcy = row.currency || event?.proceeds_currency || ''
  const editing = !!event

  const [lots, setLots] = useState<Lot[]>([])
  // Start in the loading state only when there's a position to fetch lots for; with no
  // position id there's nothing to load (deriving the initial value avoids a synchronous
  // setState inside the effect).
  const [lotsLoading, setLotsLoading] = useState(!!positionId)
  const [lotsError, setLotsError] = useState(false)

  const [quantity, setQuantity] = useState(() => (event ? String(event.quantity) : ''))
  const [proceeds, setProceeds] = useState(() => (event ? String(event.proceeds_per_unit) : ''))
  // Selling commission/fee — nets the proceeds in the service math and feeds fee
  // analytics (fee-capture sweep 2026-07-13; this form previously had no fee field).
  const [fees, setFees] = useState(() => (event?.disposal_fees ? String(event.disposal_fees) : ''))
  const [disposedAt, setDisposedAt] = useState(() => event?.disposed_at || toLocalISODate())
  const [method, setMethod] = useState<Method>(() =>
    event?.method === 'specific_id' ? 'specific_id' : 'fifo',
  )
  const [isTaxable, setIsTaxable] = useState(event ? event.is_taxable : true)
  // INV1-2: where the net proceeds get deposited. '' = don't add to any account (so the
  // recorded sale is intentionally just a cost-basis event, with a clear note that net worth
  // won't move). A chosen account books a transfer of the net proceeds via the money path.
  const [proceedsAccountId, setProceedsAccountId] = useState(() => event?.proceeds_account_id || '')
  // Where the shares went on a transfer between the user's own accounts. '' = not one of
  // their accounts (a gift / write-off). A label on the disposal, not a second leg — the
  // receiving lots are still acquired on the other side.
  const [transferToAccountId, setTransferToAccountId] = useState(
    () => event?.transfer_to_account_id || '',
  )
  const [accounts, setAccounts] = useState<AccountLite[]>([])
  // lot_id → qty string, for specific-ID selection.
  const [picks, setPicks] = useState<Record<string, string>>(() => picksFromEvent(event))

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DisposalResult | null>(null)

  useEffect(() => {
    if (!positionId) return
    let alive = true
    authFetch(`/api/positions/${positionId}/lots`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { lots: Lot[] }) => {
        if (!alive) return
        setLots(d.lots || [])
        setLotsLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setLotsError(true)
        setLotsLoading(false)
      })
    return () => {
      alive = false
    }
  }, [positionId])

  // Asset accounts the proceeds can be deposited into (INV1-2). Best-effort: a failed fetch
  // just leaves the picker empty (the user can still record the sale + skip the deposit).
  useEffect(() => {
    let alive = true
    fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
      .then((data) => {
        if (alive && Array.isArray(data)) {
          setAccounts(data.filter((a) => a.kind === 'asset'))
        }
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!event?.txn_id || event.proceeds_account_id) return
    let alive = true
    authFetch(`/api/transactions/${event.txn_id}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((txn: { to_account_id?: string | null }) => {
        if (alive && txn.to_account_id) setProceedsAccountId(txn.to_account_id)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [event])

  const openLots = lotsWithRestoredQty(lots, event).filter((l) => l.remaining_qty > 0)
  const totalOpen = openLots.reduce((s, l) => s + l.remaining_qty, 0)
  // Parsed sell qty for a lot's specific-ID input (0 when blank/unparseable).
  const pickQty = (lotId: string): number => parseFloat(picks[lotId] ?? '') || 0
  const pickedQty = openLots.reduce((s, l) => s + pickQty(l.id), 0)

  function setPick(lotId: string, value: string) {
    setPicks((prev) => ({ ...prev, [lotId]: value }))
  }

  const qtyNum = parseFloat(quantity) || 0
  const proceedsNum = parseFloat(proceeds) || 0
  const feesNum = parseFloat(fees) || 0
  // Net proceeds = qty × proceeds/unit − fees. This is what gets deposited when a
  // destination account is chosen; shown beside the picker so the user sees the
  // amount before confirming. Transfer-outs have no proceeds.
  const netProceeds = isTaxable ? Math.max(qtyNum * proceedsNum - feesNum, 0) : 0
  const specificMismatch =
    method === 'specific_id' && qtyNum > 0 && Math.abs(pickedQty - qtyNum) > 1e-9
  const overSell = qtyNum > totalOpen + 1e-9
  // Money guard (P2-INV1c): a TAXABLE sale with no proceeds would book the full cost basis
  // as a fabricated loss — almost always a forgotten sale price. Block it in the form (the
  // service refuses it too, 422); the intentional $0 path is the transfer-out toggle below.
  const missingProceeds = isTaxable && proceedsNum <= 0

  const canSubmit =
    !!positionId &&
    qtyNum > 0 &&
    !overSell &&
    !missingProceeds &&
    !submitting &&
    (method === 'fifo' || (pickedQty > 0 && !specificMismatch))

  async function submit() {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const body: Record<string, unknown> = {
        quantity: qtyNum,
        proceeds_per_unit: parseFloat(proceeds) || 0,
        proceeds_currency: proceedsCcy || undefined,
        method,
        disposed_at: disposedAt,
        is_taxable: isTaxable,
      }
      if (feesNum > 0) body.disposal_fees = feesNum
      // INV1-2: only send a destination when one is chosen AND there are proceeds to deposit
      // (a transfer-out books no cash, so the service ignores it anyway).
      if (proceedsAccountId && isTaxable) {
        body.proceeds_account_id = proceedsAccountId
      }
      if (transferToAccountId && !isTaxable) {
        body.transfer_to_account_id = transferToAccountId
      }
      if (method === 'specific_id') {
        body.lot_selection = openLots
          .filter((l) => pickQty(l.id) > 0)
          .map((l) => ({ lot_id: l.id, qty: pickQty(l.id) }))
      }
      const url = event ? `/api/disposals/${event.id}` : `/api/positions/${positionId}/disposals`
      const res = await authFetch(url, {
        method: event ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data: DisposalResult = await res.json().catch(() => ({}) as DisposalResult)
      if (!res.ok) {
        // 422 from the service carries the domain message (oversell / bad lot id);
        // FastAPI wraps it under `detail`.
        const detail = (data as unknown as { detail?: string }).detail
        throw new Error(detail || data.error || `Could not record the sale (HTTP ${res.status}).`)
      }
      if (data.error) throw new Error(data.error)
      setResult(data)
      notifyPortfolioChanged()
      onDone()
    } catch (e) {
      setError((e as Error).message || 'Could not record the sale.')
    } finally {
      setSubmitting(false)
    }
  }

  const name = row.symbol || row.label
  // INV1-4: account + reachability disambiguate WHICH tranche this is when the same
  // symbol is held more than once (e.g. two ONWAI lots in one account). Shown as a subtitle
  // under the name. `bucket` was split by ADR 2026-07-31; on the frontend it meant
  // REACHABILITY, so `liquidity` is its successor here (entitlement is `vesting_state`).
  const subtitleParts = [row.category, row.liquidity].filter(Boolean)
  const accountName = (id: string) => accounts.find((a) => a.id === id)?.name || ''

  return (
    <Modal
      open
      onClose={onClose}
      title={
        <>
          {editing ? 'Edit sale' : 'Record sale'} — <span className="disposal-name">{name}</span>
        </>
      }
      subtitle={subtitleParts.length > 0 ? subtitleParts.join(' • ') : undefined}
      size="lg"
      className="modal-card--disposal"
      footer={
        !result ? (
          <div className="disposal-actions">
            <button className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button className="btn-primary" disabled={!canSubmit} onClick={submit}>
              {submitting
                ? editing
                  ? 'Saving…'
                  : 'Recording…'
                : editing
                  ? 'Save'
                  : isTaxable
                    ? 'Record sale'
                    : 'Record transfer'}
            </button>
          </div>
        ) : undefined
      }
    >
      {result ? (
        <div className="disposal-result">
          <div className="disposal-result-icon">✓</div>
          <div className="disposal-result-body">
            <div className="disposal-result-title">
              {editing
                ? isTaxable
                  ? 'Sale updated'
                  : 'Transfer updated'
                : isTaxable
                  ? 'Sale recorded'
                  : 'Transfer recorded'}
            </div>
            {!isTaxable ? (
              <div className="muted">
                {transferToAccountId
                  ? `Moved to ${accountName(transferToAccountId)} — basis carried over, no gain booked.`
                  : 'Transfer-out — basis carried out, no gain booked.'}
              </div>
            ) : result.realized_gain == null ? (
              <div className="muted">Realized gain unknown (cost basis not recorded).</div>
            ) : (
              <div className="disposal-result-gain">
                Realized gain{' '}
                <span className={plClass(result.realized_gain)}>
                  {(result.realized_gain >= 0 ? '+' : '') +
                    fAmountNative(result.realized_gain, proceedsCcy)}
                  {result.realized_gain_base != null && (
                    <span className="muted">
                      {' '}
                      (
                      {(result.realized_gain_base >= 0 ? '+' : '') +
                        fBase(result.realized_gain_base)}
                      )
                    </span>
                  )}
                </span>
              </div>
            )}
            {/* INV1-2: be explicit about whether the cash landed anywhere, so a recorded
                  sale never silently drops net worth without the user knowing. */}
            {isTaxable &&
              (result.cash_leg_id ? (
                <div className="muted disposal-deposit-note">
                  Net proceeds deposited to {accountName(proceedsAccountId) || 'the chosen account'}
                  .
                </div>
              ) : (
                <div className="muted disposal-deposit-note">
                  Proceeds were NOT added to any account — your net worth will drop by the sale
                  value until you record the cash.
                </div>
              ))}
          </div>
          <button className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      ) : (
        <>
          {lotsError ? (
            <div className="disposal-error">Could not load the lots for this holding.</div>
          ) : lotsLoading ? (
            <div className="muted disposal-loading">Loading lots…</div>
          ) : openLots.length === 0 ? (
            <div className="disposal-error">
              No open units to sell for this holding — it's already fully disposed. Add to the
              position from the portfolio, or ask Lucius to record a new acquisition.
            </div>
          ) : (
            <>
              <div className="disposal-grid">
                <label>
                  Quantity
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={quantity}
                    onChange={(e) => setQuantity(e.target.value)}
                    placeholder={`max ${totalOpen.toLocaleString(undefined, { maximumFractionDigits: 8 })}`}
                  />
                </label>
                <label>
                  Proceeds / unit{proceedsCcy ? ` (${proceedsCcy})` : ''}
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={proceeds}
                    onChange={(e) => setProceeds(e.target.value)}
                  />
                </label>
                <label>
                  Fees{proceedsCcy ? ` (${proceedsCcy})` : ''}
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={fees}
                    onChange={(e) => setFees(e.target.value)}
                    placeholder="0"
                  />
                </label>
                <label>
                  Date
                  <DateField
                    unstyled
                    value={disposedAt}
                    onChange={(e) => setDisposedAt(e.target.value)}
                  />
                </label>
              </div>

              <div className="disposal-method">
                <span className="disposal-method-label">Lot selection</span>
                <div className="inv-seg">
                  <button
                    className={`inv-seg-chip${method === 'fifo' ? ' active' : ''}`}
                    onClick={() => setMethod('fifo')}
                  >
                    FIFO
                  </button>
                  <button
                    className={`inv-seg-chip${method === 'specific_id' ? ' active' : ''}`}
                    onClick={() => setMethod('specific_id')}
                  >
                    Specific lots
                  </button>
                </div>
              </div>

              {method === 'specific_id' && (
                <div className="disposal-lots">
                  <table className="realized-lots">
                    <thead>
                      <tr>
                        <th>Acquired</th>
                        <th>Remaining</th>
                        <th>Unit cost</th>
                        <th>Term</th>
                        <th>Sell qty</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openLots.map((l) => (
                        <tr key={l.id}>
                          <td>{l.acquired_at ? fDate(l.acquired_at, 'short', 'always') : '—'}</td>
                          <td>{l.remaining_qty.toLocaleString(undefined)}</td>
                          <td>
                            {l.unit_cost == null
                              ? '—'
                              : fAmountNative(l.unit_cost, l.cost_currency || proceedsCcy)}
                          </td>
                          <td>
                            <span className={`term-badge term-${l.term_to_date}`}>
                              {l.term_to_date}
                            </span>
                          </td>
                          <td>
                            <input
                              className="disposal-lot-qty"
                              type="number"
                              min="0"
                              max={l.remaining_qty}
                              step="any"
                              value={picks[l.id] ?? ''}
                              onChange={(e) => setPick(l.id, e.target.value)}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {specificMismatch && (
                    <div className="disposal-warn">
                      Selected {pickedQty.toLocaleString(undefined)} of {qtyNum.toLocaleString()} —
                      the picked quantities must sum to the sale quantity.
                    </div>
                  )}
                </div>
              )}

              {/* INV1-2: optional cash deposit. Recording a sale books NO cash on its own,
                    so net worth drops by the sale value unless the proceeds are deposited.
                    Only meaningful for a real sale — hidden for a transfer-out (no proceeds). */}
              {isTaxable && (
                <label className="disposal-deposit">
                  Deposit proceeds to
                  <select
                    value={proceedsAccountId}
                    onChange={(e) => setProceedsAccountId(e.target.value)}
                  >
                    <option value="">Don't add to an account</option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                  {proceedsAccountId ? (
                    netProceeds > 0 && (
                      <span className="disposal-deposit-hint muted">
                        {fAmountNative(netProceeds, proceedsCcy)} will be added to{' '}
                        {accountName(proceedsAccountId)}.
                      </span>
                    )
                  ) : (
                    <span className="disposal-deposit-hint muted">
                      The sale is recorded but the cash is not added anywhere, so your net worth
                      drops by the sale value.
                    </span>
                  )}
                </label>
              )}

              <label className="disposal-transfer">
                <input
                  type="checkbox"
                  checked={!isTaxable}
                  onChange={(e) => setIsTaxable(!e.target.checked)}
                />
                Not a sale — transferring out, gifting, or writing off. No money received, so no
                profit/loss is recorded and the original cost moves with the shares.
              </label>

              {/* A move between the user's own accounts names its destination, so Activity
                    reads "1242 units → IBKR" instead of an anonymous transfer-out. The
                    source account is not offered — a transfer goes somewhere ELSE. */}
              {!isTaxable && (
                <label className="disposal-deposit">
                  Moved to
                  <select
                    value={transferToAccountId}
                    onChange={(e) => setTransferToAccountId(e.target.value)}
                  >
                    <option value="">Not one of my accounts — gift or write-off</option>
                    {accounts
                      .filter((a) => a.id !== row.account_id)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                  </select>
                  {transferToAccountId && (
                    <span className="disposal-deposit-hint muted">
                      Only the removal is recorded here. Add the shares to{' '}
                      {accountName(transferToAccountId)} as a transfer-in lot with the same cost.
                    </span>
                  )}
                </label>
              )}

              {overSell && (
                <div className="disposal-warn">
                  Quantity exceeds the {totalOpen.toLocaleString(undefined)} open units.
                </div>
              )}
              {missingProceeds && qtyNum > 0 && (
                <div className="disposal-warn">
                  Enter the proceeds per unit. A $0 sale would book the full cost basis as a loss —
                  for a gift / transfer with no value received, tick "transfer-out" below.
                </div>
              )}
              {error && <div className="disposal-error">{error}</div>}
            </>
          )}
        </>
      )}
    </Modal>
  )
}
