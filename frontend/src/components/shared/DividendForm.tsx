import { useEffect, useState } from 'react'
import type { IncomeEvent } from '../../types/portfolio'
import { authFetch } from '../../api/client'
import { notifyPortfolioChanged, useAccounts, type AccountLike } from '../../hooks/serverState'
import { fDate } from '../../utils/format'
import { readCachedFxRates } from '../../utils/fxCache'
import {
  Button,
  Cluster,
  ConfirmDialog,
  DateField,
  FormField,
  Modal,
  NumberField,
  Select,
  TextArea,
} from '../ui'
import type { ConfirmDialogData } from '../ui'
import { FormError } from './FormError'
import { amountInAccount, type LotCashRow } from './cashHints'

interface AccountLite extends AccountLike {
  id: string
  name: string
  kind: string
}

const KIND_OPTIONS = [
  { value: 'dividend', label: 'Dividend' },
  { value: 'interest', label: 'Interest' },
  { value: 'distribution', label: 'Distribution' },
]

const EMPTY: AccountLite[] = []

function errorText(data: unknown, status: number): string {
  const body = data as { detail?: unknown; error?: string }
  if (typeof body.detail === 'string') return body.detail
  return body.error || `Could not save it (HTTP ${status}).`
}

/** Correct or delete a recorded dividend from a holding's Dividends table. Its cash row is
 *  edited in place on the server (services/income_corrections), the way a lot's is. */
export function DividendForm({
  event,
  symbol,
  onDone,
  onClose,
}: {
  event: IncomeEvent
  symbol: string
  onDone: () => void
  onClose: () => void
}) {
  const [paidAt, setPaidAt] = useState(event.paid_at || '')
  const [kind, setKind] = useState<string>(event.income_kind || 'dividend')
  const [gross, setGross] = useState(String(event.gross_amount ?? ''))
  const [tax, setTax] = useState(event.tax_withheld ? String(event.tax_withheld) : '')
  const [note, setNote] = useState(event.note || '')
  const [accountId, setAccountId] = useState('')
  // Sent only once the user picks it: the row's account loads in the background, and saving
  // before it lands would send "no account" and remove the cash (the lot form's lesson).
  const [accountTouched, setAccountTouched] = useState(false)
  const [cashRow, setCashRow] = useState<LotCashRow | null>(null)
  const [fx] = useState(readCachedFxRates)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmDialogData | null>(null)
  const [deleting, setDeleting] = useState(false)
  const { data } = useAccounts<AccountLite>()
  const accounts = (data ?? EMPTY).filter((a) => !a.archived && a.kind === 'asset')
  const ccy = (event.currency || '').toUpperCase()

  useEffect(() => {
    if (!event.txn_id) return
    let alive = true
    authFetch(`/api/transactions/${event.txn_id}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((txn: LotCashRow) => {
        if (!alive) return
        setCashRow(txn)
        if (txn.to_account_id) setAccountId(txn.to_account_id)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [event.txn_id])

  const grossNum = parseFloat(gross)
  const taxNum = parseFloat(tax) || 0
  const net = Number.isFinite(grossNum) ? Math.max(grossNum - taxNum, 0) : 0
  const valid =
    !!paidAt && Number.isFinite(grossNum) && grossNum >= 0 && taxNum >= 0 && taxNum <= grossNum
  const transfer = cashRow?.kind === 'transfer'

  async function save() {
    setSubmitting(true)
    setError(null)
    const body: Record<string, unknown> = {
      paid_at: paidAt,
      income_kind: kind,
      gross_amount: grossNum,
      tax_withheld: taxNum,
      note,
    }
    if (accountTouched && !event.is_drp) body.account_id = accountId || null
    try {
      const res = await authFetch(`/api/income/${event.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const out = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(errorText(out, res.status))
      notifyPortfolioChanged()
      onDone()
      onClose()
    } catch (e) {
      setError((e as Error).message || 'Could not save it.')
    } finally {
      setSubmitting(false)
    }
  }

  function askDelete() {
    const cash = event.is_drp
      ? ' The reinvested shares stay as a lot; remove that lot too if they should go.'
      : transfer
        ? ' Its cash is a transfer in your ledger, which stays.'
        : event.txn_id
          ? ' Its cash transaction is removed too.'
          : ''
    setConfirm({
      title: `Delete this ${kindLabel(event.income_kind)}?`,
      body: `This removes the ${kindLabel(event.income_kind)} paid ${fDate(event.paid_at, 'short', 'always')} from this holding's income.${cash}`,
      confirmLabel: 'Delete',
      danger: true,
      confirmDisabled: deleting,
    })
  }

  async function remove() {
    setDeleting(true)
    setError(null)
    try {
      const res = await authFetch(`/api/income/${event.id}?remove_cash=true`, { method: 'DELETE' })
      const out = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(errorText(out, res.status))
      setConfirm(null)
      notifyPortfolioChanged()
      onDone()
      onClose()
    } catch (e) {
      setConfirm(null)
      setError((e as Error).message || 'Could not delete it.')
    } finally {
      setDeleting(false)
    }
  }

  const account = accounts.find((a) => a.id === accountId)
  return (
    <>
      <Modal
        open
        onClose={onClose}
        title={`Edit ${kindLabel(event.income_kind)}`}
        subtitle={`${symbol} · ${fDate(event.paid_at, 'short', 'always')}`}
        footer={
          <Cluster justify="between">
            <Button variant="danger" size="sm" onClick={askDelete} disabled={submitting}>
              Delete
            </Button>
            <Cluster>
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button variant="primary" loading={submitting} disabled={!valid} onClick={save}>
                Save
              </Button>
            </Cluster>
          </Cluster>
        }
      >
        <div className="disposal-grid acquire-lot-grid">
          <FormField label="Paid" required htmlFor="div-paid">
            <DateField id="div-paid" value={paidAt} onChange={(e) => setPaidAt(e.target.value)} />
          </FormField>
          <FormField label="Kind" htmlFor="div-kind">
            <Select
              id="div-kind"
              options={KIND_OPTIONS}
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            />
          </FormField>
          <FormField label={`Gross (${ccy})`} required htmlFor="div-gross">
            <NumberField
              id="div-gross"
              min={0}
              step="any"
              value={gross}
              onChange={(e) => setGross(e.target.value)}
            />
          </FormField>
          <FormField
            label={`Tax withheld (${ccy})`}
            htmlFor="div-tax"
            error={taxNum > grossNum ? "Can't be more than the gross amount." : undefined}
          >
            <NumberField
              id="div-tax"
              min={0}
              step="any"
              value={tax}
              onChange={(e) => setTax(e.target.value)}
            />
          </FormField>
          <FormField label="Note" htmlFor="div-note" span="full">
            <TextArea
              id="div-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </FormField>
        </div>
        {event.is_drp ? (
          <div className="muted disposal-deposit-note">
            This dividend was reinvested, so it has no cash. Its shares are a lot: correct them in
            the lots table.
          </div>
        ) : (
          <label className="disposal-deposit">
            Paid into
            <Select
              options={[
                { value: '', label: "Don't add to an account" },
                ...accounts.map((a) => ({ value: a.id, label: a.name })),
              ]}
              value={accountId}
              onChange={(e) => {
                setAccountId(e.target.value)
                setAccountTouched(true)
              }}
            />
            {transfer ? (
              <span className="disposal-deposit-hint muted">
                This dividend's cash is a transfer in your ledger. Saving leaves that transfer as it
                is.
              </span>
            ) : accountId ? (
              net > 0 && (
                <span className="disposal-deposit-hint muted">
                  {amountInAccount({
                    account,
                    accountId,
                    amount: net,
                    ccy,
                    leg: cashRow
                      ? {
                          accountId: cashRow.to_account_id,
                          amount: cashRow.to_amount,
                          currency: cashRow.to_currency,
                        }
                      : null,
                    savedAmount: Math.max(event.gross_amount - (event.tax_withheld || 0), 0),
                    fx,
                  })}{' '}
                  goes into {account?.name ?? 'the account'}.
                </span>
              )
            ) : event.txn_id && accountTouched ? (
              <span className="disposal-warn">
                Saving removes this dividend's cash transaction from the ledger.
              </span>
            ) : (
              <span className="disposal-deposit-hint muted">
                No cash is recorded for this dividend.
              </span>
            )}
          </label>
        )}
        <FormError message={error} />
      </Modal>
      <ConfirmDialog
        data={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void remove()}
      />
    </>
  )
}

function kindLabel(kind: string): string {
  return kind === 'interest' ? 'interest' : kind === 'distribution' ? 'distribution' : 'dividend'
}
