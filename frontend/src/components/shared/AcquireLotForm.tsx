import { useEffect, useState } from 'react'
import type { Lot } from '../../types/portfolio'
import { fAmountNative, fDate, getBaseCurrency } from '../../utils/format'
import { authFetch } from '../../api/client'
import {
  ACCOUNTS_KEY,
  notifyPortfolioChanged,
  useAccounts,
  type AccountLike,
} from '../../hooks/serverState'
import { invalidateQueryCache } from '../../state/queryCache'
import {
  Button,
  Cluster,
  ConfirmDialog,
  FormField,
  Modal,
  NumberField,
  Select,
  TextArea,
  TextField,
  DateField,
} from '../ui'
import type { ConfirmDialogData } from '../ui'
import { toLocalISODate } from './DisposalForm'
import { FormError } from './FormError'
import { LotFundingHint } from './LotFundingHint'
import { removeLotCashNote, type LotCashRow } from './cashHints'
import { readCachedFxRates } from '../../utils/fxCache'

// `extends AccountLike` supplies the index signature `useAccounts<T>()`'s constraint demands.
export interface AccountLite extends AccountLike {
  id: string
  name: string
  kind: string
  type?: string
  archived?: boolean
  /** The account's Starting Balance date — cash dated before it isn't in its balance. */
  initial_balance_date?: string | null
}

interface Props {
  /** Prefill. Omit or leave empty when the user should type a ticker (Add holding). */
  symbol?: string
  costCcy?: string
  instrumentId?: string
  /** Quote / asset page: symbol is shown but not editable. */
  lockSymbol?: boolean
  defaultAccountId?: string
  lot?: Lot | null
  onDone: () => void
  onClose: () => void
}

const KIND_OPTIONS = [
  { value: 'buy', label: 'Buy' },
  { value: 'drp', label: 'Dividend reinvest' },
  { value: 'transfer_in', label: 'Transfer in' },
  { value: 'grant', label: 'Grant' },
  { value: 'opening', label: 'Opening balance' },
]

const NEW_ACCOUNT = '__new__'
const EMPTY_ACCOUNTS: AccountLite[] = []

function kindLabel(kind: string): string {
  return KIND_OPTIONS.find((o) => o.value === kind)?.label || kind.replace(/_/g, ' ')
}

/** One sale a lot correction touched — returned by PATCH /api/lots/{id}. */
interface SaleUpdate {
  event_id: string
  disposed_at: string
  realized_gain_before: number | null
  realized_gain_after: number | null
  currency: string
}

function saleUpdateLine(sale: SaleUpdate): string {
  const when = fDate(sale.disposed_at, 'short', 'always')
  const money = (v: number | null) =>
    v == null
      ? 'unknown'
      : `${v >= 0 ? '+' : ''}${fAmountNative(v, sale.currency)} ${sale.currency}`
  const before = sale.realized_gain_before
  const after = sale.realized_gain_after
  const same = before != null && after != null ? Math.abs(before - after) < 0.005 : before === after
  return same
    ? `Sale on ${when} re-dated — gain unchanged (${money(after)})`
    : `Sale on ${when}: gain ${money(before)} → ${money(after)}`
}

function isPartial(lot: Lot): boolean {
  return lot.remaining_qty + 1e-9 < lot.original_qty
}

function parseHttpError(data: unknown, status: number): string {
  const body = data as { detail?: unknown; error?: string }
  if (typeof body.detail === 'string') return body.detail
  return body.error || `Could not save the lot (HTTP ${status}).`
}

function isDuplicateConflict(status: number, message: string): boolean {
  return status === 409 && /identical/i.test(message)
}

function holdingAccounts(accounts: AccountLite[]): AccountLite[] {
  const live = accounts.filter((a) => !a.archived && a.kind === 'asset')
  const hold = live.filter((a) => a.type === 'investment' || a.type === 'retirement')
  return hold.length ? hold : live
}

function fundingAccounts(accounts: AccountLite[]): AccountLite[] {
  return accounts.filter((a) => !a.archived && a.kind === 'asset')
}

/** Trim float noise for form fields without inventing fixed decimal places. */
function formatCostNum(n: number): string {
  if (!Number.isFinite(n)) return ''
  return String(parseFloat(n.toFixed(10)))
}

function parseOptionalNum(raw: string): number | null {
  const t = raw.trim()
  if (t === '') return null
  const n = parseFloat(t)
  return Number.isFinite(n) ? n : null
}

type CostEditField = 'qty' | 'unit' | 'total'

export function AcquireLotForm({
  symbol: symbolProp = '',
  costCcy: costCcyProp = '',
  instrumentId: instrumentIdProp,
  lockSymbol = false,
  defaultAccountId,
  lot,
  onDone,
  onClose,
}: Props) {
  const editing = !!lot
  // A lot a sale has used is still correctable (2026-10-13 — it used to be frozen, so a user
  // couldn't date an opening lot they had sold). Its quantity can't go below what was sold.
  const soldQty = lot && isPartial(lot) ? lot.original_qty - lot.remaining_qty : 0
  const sold = soldQty > 0

  const [symbol, setSymbol] = useState(() => (lot ? symbolProp : symbolProp.toUpperCase()))
  const [resolvedInstrumentId, setResolvedInstrumentId] = useState(instrumentIdProp || '')
  const [resolvedCcy, setResolvedCcy] = useState(costCcyProp)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  // A lot whose purchase date is UNKNOWN (a legacy opening balance) edits as an empty date —
  // never prefilled with today: saving a note or cost used to stamp today's date on it and
  // drop the holding from every earlier day of net-worth history (found 2026-10-09).
  const [acquiredAt, setAcquiredAt] = useState(() =>
    lot ? lot.acquired_at || '' : toLocalISODate(),
  )
  const dateUnknown = !!lot && !lot.acquired_at
  const [quantity, setQuantity] = useState(() => (lot ? String(lot.original_qty) : ''))
  const [unitCost, setUnitCost] = useState(() =>
    lot?.unit_cost != null ? String(lot.unit_cost) : '',
  )
  const [fees, setFees] = useState(() => (lot && lot.fees ? String(lot.fees) : ''))
  const [totalSpent, setTotalSpent] = useState(() => {
    if (lot?.unit_cost == null || !lot.original_qty) return ''
    return formatCostNum(lot.original_qty * lot.unit_cost + (lot.fees || 0))
  })
  /** Which of qty / unit / total the user last typed — drives which field is derived. */
  const [lastCostEdit, setLastCostEdit] = useState<CostEditField | null>(null)
  const [kind, setKind] = useState(lot?.kind || 'buy')
  const [note, setNote] = useState(lot?.note || '')
  const [holdingAccountId, setHoldingAccountId] = useState(
    lot?.account_id || defaultAccountId || '',
  )
  const [newAccountName, setNewAccountName] = useState('')
  const [fundingAccountId, setFundingAccountId] = useState('')
  // Sent on an edit only once the user picks it: the lot's current funding account loads in
  // the background, and saving before it lands sent null — which deleted the purchase.
  const [fundingTouched, setFundingTouched] = useState(false)
  const [cashRow, setCashRow] = useState<LotCashRow | null>(null)
  const [fx] = useState(readCachedFxRates)
  const savedGross =
    lot?.unit_cost != null ? lot.original_qty * lot.unit_cost + (lot.fees || 0) : null
  const { data: accountsData, error: accountsQueryError } = useAccounts<AccountLite>()
  const accounts = accountsData ?? EMPTY_ACCOUNTS
  const accountsError = !!accountsQueryError

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<'added' | 'updated' | 'removed' | null>(null)
  const [salesUpdated, setSalesUpdated] = useState<SaleUpdate[]>([])
  const [confirm, setConfirm] = useState<ConfirmDialogData | null>(null)
  const [pendingDuplicate, setPendingDuplicate] = useState(false)
  const [removing, setRemoving] = useState(false)

  // Ticker / lot denomination only — never the book base (that made Add holding show
  // "Unit cost (SGD)" for a USD ticker before resolve).
  const costCcy = lot?.cost_currency || resolvedCcy || costCcyProp || ''
  const symbolLocked = editing || lockSymbol
  const accountLocked = editing

  useEffect(() => {
    if (editing || holdingAccountId) return
    if (defaultAccountId) {
      setHoldingAccountId(defaultAccountId)
      return
    }
    const hold = holdingAccounts(accounts)
    const only = hold[0]
    if (hold.length === 1 && only) setHoldingAccountId(only.id)
  }, [accounts, defaultAccountId, editing, holdingAccountId])

  useEffect(() => {
    if (!lot?.txn_id) return
    let alive = true
    authFetch(`/api/transactions/${lot.txn_id}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((txn: LotCashRow) => {
        if (!alive) return
        setCashRow(txn)
        if (txn.from_account_id) setFundingAccountId(txn.from_account_id)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [lot?.txn_id])

  const qtyNum = parseFloat(quantity) || 0
  const costNum = parseOptionalNum(unitCost)
  const feesNum = parseFloat(fees) || 0
  const totalNum = parseOptionalNum(totalSpent)
  const gross =
    costNum != null && Number.isFinite(costNum) ? Math.max(qtyNum * costNum + feesNum, 0) : 0
  const newAccount = holdingAccountId === NEW_ACCOUNT
  const accountChosen =
    (!newAccount && !!holdingAccountId) || (newAccount && newAccountName.trim().length > 0)
  // A lot records units already held: a purchase can't be dated after today (found 2026-10-12 —
  // a 2027 date was accepted and counted in today's holding). The lot choke refuses it too.
  const today = toLocalISODate()
  const futureDate = !!acquiredAt && acquiredAt > today
  const belowSold = sold && qtyNum + 1e-9 < soldQty
  const canSubmit =
    !submitting &&
    !futureDate &&
    !belowSold &&
    (editing
      ? qtyNum > 0 && (!!acquiredAt || dateUnknown)
      : qtyNum > 0 && !!acquiredAt && !!symbol.trim() && accountChosen)

  /** Total is gross (incl. fees): unit = (total − fees) / qty; total = qty × unit + fees. */
  function applyCostEdit(field: CostEditField, nextRaw: string) {
    const prev = lastCostEdit
    setLastCostEdit(field)
    const feesVal = parseFloat(fees) || 0
    const qtyStr = field === 'qty' ? nextRaw : quantity
    const unitStr = field === 'unit' ? nextRaw : unitCost
    const totalStr = field === 'total' ? nextRaw : totalSpent
    const qtyVal = parseOptionalNum(qtyStr)
    const unitVal = parseOptionalNum(unitStr)
    const totalVal = parseOptionalNum(totalStr)

    if (field === 'qty') {
      setQuantity(nextRaw)
      if (qtyVal == null || qtyVal <= 0) return
      // Keep Total when that was last owned (or unit is empty); otherwise keep unit price.
      if (totalVal != null && (prev === 'total' || unitVal == null)) {
        const unitDerived = (totalVal - feesVal) / qtyVal
        if (unitDerived >= 0 && Number.isFinite(unitDerived)) {
          setUnitCost(formatCostNum(unitDerived))
        }
        return
      }
      if (unitVal != null) {
        setTotalSpent(formatCostNum(qtyVal * unitVal + feesVal))
      }
      return
    }
    if (field === 'unit') {
      setUnitCost(nextRaw)
      if (unitVal == null) return
      if (totalVal != null && (prev === 'total' || qtyVal == null || qtyVal <= 0) && unitVal > 0) {
        const qtyDerived = (totalVal - feesVal) / unitVal
        if (qtyDerived > 0 && Number.isFinite(qtyDerived)) {
          setQuantity(formatCostNum(qtyDerived))
        }
        return
      }
      if (qtyVal != null && qtyVal > 0) {
        setTotalSpent(formatCostNum(qtyVal * unitVal + feesVal))
      }
      return
    }
    setTotalSpent(nextRaw)
    if (totalVal == null) return
    if (qtyVal != null && qtyVal > 0) {
      const unitDerived = (totalVal - feesVal) / qtyVal
      if (unitDerived >= 0 && Number.isFinite(unitDerived)) {
        setUnitCost(formatCostNum(unitDerived))
      }
      return
    }
    if (unitVal != null && unitVal > 0) {
      const qtyDerived = (totalVal - feesVal) / unitVal
      if (qtyDerived > 0 && Number.isFinite(qtyDerived)) {
        setQuantity(formatCostNum(qtyDerived))
      }
    }
  }

  function onFeesChange(nextRaw: string) {
    setFees(nextRaw)
    const feesVal = parseFloat(nextRaw) || 0
    const qtyVal = parseOptionalNum(quantity)
    const unitVal = parseOptionalNum(unitCost)
    const totalVal = parseOptionalNum(totalSpent)
    // Keep the figure the user last owned: total stays gross, or qty×unit rebuilds total.
    if (lastCostEdit === 'total' && totalVal != null && qtyVal != null && qtyVal > 0) {
      const unitDerived = (totalVal - feesVal) / qtyVal
      if (unitDerived >= 0 && Number.isFinite(unitDerived)) {
        setUnitCost(formatCostNum(unitDerived))
      }
      return
    }
    if (qtyVal != null && qtyVal > 0 && unitVal != null) {
      setTotalSpent(formatCostNum(qtyVal * unitVal + feesVal))
    }
  }

  const holdOpts = holdingAccounts(accounts)
  const fundOpts = fundingAccounts(accounts)
  const accountName = (id: string) => accounts.find((a) => a.id === id)?.name || ''

  async function resolveSymbol(raw: string): Promise<string | null> {
    const query = raw.trim().toUpperCase()
    if (!query) {
      setResolvedInstrumentId('')
      setSymbolError(null)
      return null
    }
    if (lockSymbol && instrumentIdProp) {
      setResolvedInstrumentId(instrumentIdProp)
      setSymbolError(null)
      return instrumentIdProp
    }
    const res = await authFetch('/api/instruments/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    })
    const data = (await res.json().catch(() => ({}))) as {
      resolved?: boolean
      message?: string
      instrument?: { id: string; symbol?: string; quote_currency?: string }
    }
    if (!res.ok || !data.resolved || !data.instrument?.id) {
      setResolvedInstrumentId('')
      setSymbolError(data.message || `Couldn't resolve '${query}' to a tradeable instrument.`)
      return null
    }
    setResolvedInstrumentId(data.instrument.id)
    if (data.instrument.symbol) setSymbol(data.instrument.symbol)
    if (data.instrument.quote_currency) setResolvedCcy(data.instrument.quote_currency)
    setSymbolError(null)
    return data.instrument.id
  }

  async function ensureAccountId(): Promise<string> {
    if (!newAccount) return holdingAccountId
    const res = await authFetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: newAccountName.trim(),
        type: 'investment',
        currency: costCcy || getBaseCurrency(),
        initial_balance: 0,
      }),
    })
    const data = (await res.json().catch(() => ({}))) as { id?: string; detail?: unknown }
    if (!res.ok || !data.id) throw new Error(parseHttpError(data, res.status))
    invalidateQueryCache(ACCOUNTS_KEY)
    return data.id
  }

  async function save(allowDuplicate = false) {
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      if (editing && lot) {
        const body: Record<string, unknown> = {
          // Only a date the user actually changed is sent — an untouched one stays as stored.
          ...(acquiredAt !== (lot.acquired_at || '') ? { acquired_at: acquiredAt } : {}),
          quantity: qtyNum,
          unit_cost: costNum,
          fees: feesNum,
          kind,
          note,
          ...(fundingTouched ? { funding_account_id: fundingAccountId || null } : {}),
        }
        const res = await authFetch(`/api/lots/${lot.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(parseHttpError(data, res.status))
        setSalesUpdated(Array.isArray(data.sales_updated) ? data.sales_updated : [])
        setResult('updated')
        notifyPortfolioChanged()
        onDone()
        return
      }
      if (qtyNum <= 0 || !acquiredAt || !accountChosen) {
        throw new Error('Symbol, account, date, and quantity are required.')
      }
      const instId = resolvedInstrumentId || (await resolveSymbol(symbol))
      if (!instId) throw new Error(symbolError || 'Choose a symbol.')
      const accountId = await ensureAccountId()
      const body: Record<string, unknown> = {
        account_id: accountId,
        quantity: qtyNum,
        unit_cost: costNum,
        // Omit when unknown — backend stamps instrument.quote_currency. Never send base.
        ...(costCcy ? { cost_currency: costCcy } : {}),
        fees: feesNum,
        acquired_at: acquiredAt,
        kind,
        note,
        allow_duplicate: allowDuplicate,
      }
      if (fundingAccountId) body.funding_account_id = fundingAccountId
      const res = await authFetch(`/api/instruments/${instId}/lots`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const message = parseHttpError(data, res.status)
        if (isDuplicateConflict(res.status, message)) {
          setPendingDuplicate(true)
          setConfirm({
            title: 'Identical lot already exists',
            body: 'This looks identical to an open lot already on this holding. Add it anyway?',
            confirmLabel: 'Add anyway',
          })
          return
        }
        throw new Error(message)
      }
      setResult('added')
      notifyPortfolioChanged()
      onDone()
    } catch (e) {
      setError((e as Error).message || 'Could not save the lot.')
    } finally {
      setSubmitting(false)
    }
  }

  function askRemove() {
    if (!lot) return
    setConfirm({
      title: 'Remove this lot?',
      // Removing a lot also removes its purchase transaction, and frees what it paid (UAT DS-LOT-REMOVE).
      body: `This will permanently remove ${lot.original_qty} units acquired on ${lot.acquired_at ? fDate(lot.acquired_at, 'short', 'always') : 'an unknown date'}. Unrealized P&L and cost basis will update.${removeLotCashNote(lot.txn_id, cashRow)}`,
      confirmLabel: 'Remove',
      danger: true,
      confirmDisabled: removing,
    })
  }

  async function removeLot() {
    if (!lot) return
    setRemoving(true)
    setError(null)
    try {
      const res = await authFetch(`/api/lots/${lot.id}`, { method: 'DELETE' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(parseHttpError(data, res.status))
      setConfirm(null)
      setResult('removed')
      notifyPortfolioChanged()
      onDone()
    } catch (e) {
      setConfirm(null)
      setError((e as Error).message || 'Could not remove the lot.')
    } finally {
      setRemoving(false)
    }
  }

  const title = editing ? 'Edit lot' : lockSymbol ? 'Add lot' : 'Add holding'
  // After a save the lot prop is the pre-edit row — its old date would read as unchanged.
  const subtitle =
    lot && !result
      ? [
          kindLabel(lot.kind),
          sold
            ? `${lot.remaining_qty.toLocaleString(undefined)} of ${lot.original_qty.toLocaleString(undefined)} left`
            : `${lot.original_qty.toLocaleString(undefined)} units`,
          lot.acquired_at ? fDate(lot.acquired_at, 'short', 'always') : 'date unknown',
        ].join(' · ')
      : undefined
  const primaryLabel = editing ? 'Save' : lockSymbol ? 'Add lot' : 'Add holding'

  return (
    <>
      <Modal
        open
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        size="lg"
        footer={
          !result ? (
            <Cluster justify="between">
              {editing && !sold ? (
                <Button variant="danger" size="sm" onClick={askRemove} disabled={submitting}>
                  Remove lot
                </Button>
              ) : (
                <span />
              )}
              <Cluster>
                <Button variant="secondary" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  loading={submitting}
                  disabled={!canSubmit}
                  onClick={() => save()}
                >
                  {primaryLabel}
                </Button>
              </Cluster>
            </Cluster>
          ) : undefined
        }
      >
        {result ? (
          <div className="disposal-result">
            <div className="disposal-result-icon">✓</div>
            <div className="disposal-result-body">
              <div className="disposal-result-title">
                {result === 'added'
                  ? lockSymbol
                    ? 'Lot added'
                    : 'Holding added'
                  : result === 'removed'
                    ? 'Lot removed'
                    : 'Lot updated'}
              </div>
              {result === 'updated' && salesUpdated.length > 0 && (
                <ul className="acquire-lot-sales-updated">
                  {salesUpdated.map((sale) => (
                    <li key={sale.event_id}>{saleUpdateLine(sale)}</li>
                  ))}
                </ul>
              )}
            </div>
            <button className="btn-primary" onClick={onClose}>
              Done
            </button>
          </div>
        ) : (
          <>
            {sold && lot && (
              <div className="lots-frozen-notice">
                {soldQty.toLocaleString(undefined)} of {lot.original_qty.toLocaleString(undefined)}{' '}
                {lot.remaining_qty > 0 ? 'have been sold' : 'sold — none left'}. You can still
                correct it: the sale keeps the shares it took, and a new date, cost or fees updates
                that sale's holding period and gain. To remove this lot, undo the sale first.
              </div>
            )}
            <div className="disposal-grid acquire-lot-grid">
              <FormField
                label="Symbol"
                required
                htmlFor="acq-symbol"
                error={symbolError ?? undefined}
              >
                <TextField
                  id="acq-symbol"
                  value={symbol}
                  readOnly={symbolLocked}
                  placeholder="e.g. AAPL"
                  onChange={(e) => {
                    setSymbol(e.target.value.toUpperCase())
                    setResolvedInstrumentId('')
                    setResolvedCcy('')
                    setSymbolError(null)
                  }}
                  onBlur={(e) => {
                    if (!symbolLocked) void resolveSymbol(e.target.value)
                  }}
                />
              </FormField>
              <FormField label="Account" required htmlFor="acq-account">
                {accountLocked ? (
                  <div>{lot?.account_name || accountName(lot?.account_id || '') || '—'}</div>
                ) : (
                  <Select
                    id="acq-account"
                    placeholder="Choose account…"
                    options={[
                      ...holdOpts.map((a) => ({ value: a.id, label: a.name })),
                      { value: NEW_ACCOUNT, label: '+ New account…' },
                    ]}
                    value={holdingAccountId}
                    onChange={(e) => {
                      setHoldingAccountId(e.target.value)
                      if (e.target.value !== NEW_ACCOUNT) setNewAccountName('')
                    }}
                  />
                )}
                {!accountLocked && newAccount && (
                  <TextField
                    id="acq-new-account"
                    placeholder="Account name (e.g. IBKR)"
                    value={newAccountName}
                    onChange={(e) => setNewAccountName(e.target.value)}
                    aria-label="New account name"
                  />
                )}
              </FormField>
              <FormField
                label="Acquired date"
                required={!dateUnknown}
                htmlFor="acq-date"
                help={
                  dateUnknown && !acquiredAt
                    ? 'Unknown — set it if you know when you bought these.'
                    : undefined
                }
                error={futureDate ? "That date hasn't happened yet." : undefined}
              >
                <DateField
                  id="acq-date"
                  className="acquire-lot-datefield-input"
                  wrapperClassName="acquire-lot-datefield"
                  value={acquiredAt}
                  max={today}
                  onChange={(e) => setAcquiredAt(e.target.value)}
                />
              </FormField>
              <FormField
                label="Quantity"
                required
                htmlFor="acq-qty"
                error={
                  belowSold
                    ? `Can't be less than the ${soldQty.toLocaleString(undefined)} already sold — edit the sale to sell fewer.`
                    : undefined
                }
              >
                <NumberField
                  id="acq-qty"
                  min={0}
                  step="any"
                  value={quantity}
                  onChange={(e) => applyCostEdit('qty', e.target.value)}
                />
              </FormField>
              <FormField
                label={`Unit cost${costCcy ? ` (${costCcy})` : ''}`}
                htmlFor="acq-cost"
                help={
                  costNum == null && totalNum == null
                    ? "Leave blank if cost basis is unknown — unrealized P&L won't be shown for this lot."
                    : undefined
                }
              >
                <NumberField
                  id="acq-cost"
                  min={0}
                  step="any"
                  value={unitCost}
                  onChange={(e) => applyCostEdit('unit', e.target.value)}
                />
              </FormField>
              <FormField
                label={`Total spent${costCcy ? ` (${costCcy})` : ''}`}
                htmlFor="acq-total"
                help="Gross purchase including fees — fills unit cost from quantity, or quantity from unit cost."
              >
                <NumberField
                  id="acq-total"
                  min={0}
                  step="any"
                  value={totalSpent}
                  onChange={(e) => applyCostEdit('total', e.target.value)}
                />
              </FormField>
              <FormField label={`Fees${costCcy ? ` (${costCcy})` : ''}`} htmlFor="acq-fees">
                <NumberField
                  id="acq-fees"
                  min={0}
                  step="any"
                  value={fees}
                  onChange={(e) => onFeesChange(e.target.value)}
                  placeholder="0"
                />
              </FormField>
              <FormField label="Kind" htmlFor="acq-kind">
                <Select
                  id="acq-kind"
                  options={KIND_OPTIONS}
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                />
              </FormField>
              <FormField label="Note" htmlFor="acq-note" span="full">
                <TextArea
                  id="acq-note"
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </FormField>
            </div>

            <label className="disposal-deposit acquire-lot-funding">
              Deduct from
              <Select
                options={[
                  { value: '', label: "Don't deduct from an account" },
                  ...fundOpts.map((a) => ({ value: a.id, label: a.name })),
                ]}
                value={fundingAccountId}
                onChange={(e) => {
                  setFundingAccountId(e.target.value)
                  setFundingTouched(true)
                }}
              />
              {accountsError && (
                <span className="disposal-deposit-hint muted">
                  Could not load accounts. You can still save the lot without linking cash.
                </span>
              )}
              <LotFundingHint
                account={accounts.find((a) => a.id === fundingAccountId)}
                accountId={fundingAccountId}
                gross={gross}
                costCcy={costCcy}
                acquiredAt={acquiredAt}
                cashRow={cashRow}
                savedGross={savedGross}
                // Saving deletes the purchase this lot has, and frees what it paid (UAT DS-LOT-NODEDUCT).
                removing={!!lot?.txn_id && fundingTouched}
                fx={fx}
              />
            </label>
            <FormError message={error} />
          </>
        )}
      </Modal>
      <ConfirmDialog
        data={confirm}
        onCancel={() => {
          setConfirm(null)
          setPendingDuplicate(false)
        }}
        onConfirm={() => {
          if (pendingDuplicate) {
            setConfirm(null)
            setPendingDuplicate(false)
            void save(true)
            return
          }
          void removeLot()
        }}
      />
    </>
  )
}
