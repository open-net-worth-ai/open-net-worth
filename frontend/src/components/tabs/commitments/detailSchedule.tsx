// Schedule tab — ported from detail3.jsx's `Schedule`/`InstRow`/`PayBox`/`LinkBox`/
// `RescheduleBox`. Every write (edit/delete/pay/link/reschedule) goes through
// `DetailMutations`, which is a real fetch against /api/commitments/* (see detailMutations.ts).
import { useEffect, useState } from 'react'
import type { Commitment, Installment } from '../../../types/commitments'
import { authFetch } from '../../../api/client'
import { fBase, fCurrency, getBaseCurrency, toBase } from '../../../utils/format'
import {
  fmtDate,
  fmtDay,
  installmentFunding,
  isTracker,
  type EnvelopeAccrual,
} from '../../../utils/commitments'
import { EntityRow } from '../../../lucius/EntityRow'
import { CheckIcon, PencilIcon, Spark } from './icons'
import { EditRow, type EditDraft } from './editors'
import type { DetailMutations } from './detailTypes'
import { ACCOUNTS_KEY, fetchJson } from '../../../hooks/serverState'
import { fetchCachedQuery } from '../../../state/queryCache'
import { Button, DateField } from '../../ui'
import { ArrowDown, ArrowUp, ArrowRight, ArrowUpRight } from '../../../ui/icons'

const STATUS_LABEL: Record<string, string> = {
  paid: 'Paid',
  due: 'Due',
  overdue: 'Overdue',
  upcoming: 'Upcoming',
}

function daysUntil(date: string): number {
  const [y, m, d] = (date || '').split('-').map(Number)
  if (!y) return 0
  const target = new Date(y, (m || 1) - 1, d || 1)
  const t = new Date()
  t.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - t.getTime()) / 86400000)
}

function dispStatus(i: Installment): string {
  if (i.status === 'paid') return 'paid'
  const d = daysUntil(i.due_date)
  if (d < 0) return 'overdue'
  if (i.status === 'due' || d <= 14) return 'due'
  return 'upcoming'
}

// ── mark-paid box: a smaller amount records a partial payment + splits off the balance ──
function PayBox({
  ccy,
  amount,
  onPay,
  onCancel,
}: {
  ccy: string
  amount: number
  onPay: (amt: number) => void
  onCancel: () => void
}) {
  const [amt, setAmt] = useState<number | string>(amount)
  const numAmt = Number(amt)
  const partial = numAmt > 0 && numAmt < amount
  return (
    <div className="c2-addbox" style={{ margin: '6px 0 4px' }}>
      <div className="c2-edit">
        <div className="c2-edit-row">
          <label className="c2-field">
            Amount paid ({ccy})
            <input
              className="c2-input amt"
              style={{ width: 130 }}
              type="number"
              value={amt}
              onChange={(e) => setAmt(e.target.value)}
              autoFocus
            />
          </label>
          <span className="c2-field-hint">
            {partial
              ? `Partial — ${fCurrency(amount - numAmt, ccy)} stays due as a "balance" row.`
              : 'Paying less records a partial and splits off the balance.'}
          </span>
        </div>
        <div className="c2-edit-btns">
          <button className="c2-mini-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="c2-mini-btn pri"
            onClick={() => {
              if (numAmt > 0) onPay(Math.min(numAmt, amount))
            }}
          >
            {partial ? 'Record partial' : 'Mark paid'}
          </button>
        </div>
      </div>
    </div>
  )
}

interface TxnCandidate {
  id: string
  desc: string
  date: string
  account: string
  amount_base: number
}

// ── link-a-transaction picker: unlinked ledger txns near this amount ──
function LinkBox({
  label,
  baseAmount,
  onLink,
  onCancel,
}: {
  label: string
  baseAmount: number
  onLink: (txnId: string) => void
  onCancel: () => void
}) {
  const [cands, setCands] = useState<TxnCandidate[] | null>(null)

  useEffect(() => {
    let cancelled = false
    const tolerance = Math.max(500, baseAmount * 0.6)
    const min = Math.max(0, baseAmount - tolerance)
    const max = baseAmount + tolerance
    const qs = new URLSearchParams({
      min_amount: String(min),
      max_amount: String(max),
      limit: '30',
    })
    Promise.all([
      authFetch(`/api/transactions?${qs.toString()}`).then((r) => (r.ok ? r.json() : null)),
      fetchCachedQuery<unknown>(ACCOUNTS_KEY, () => fetchJson<unknown>(ACCOUNTS_KEY)).catch(
        () => null,
      ),
      authFetch('/api/commitments').then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([txnData, accounts, allCommitments]) => {
        if (cancelled) return
        const txns: Array<Record<string, unknown>> = Array.isArray(txnData)
          ? (txnData as Array<Record<string, unknown>>)
          : ((txnData as { transactions?: Array<Record<string, unknown>> } | null)?.transactions ??
            [])
        const accountNames = new Map<string, string>()
        for (const a of (accounts as Array<{ id: string; name: string }>) || [])
          accountNames.set(a.id, a.name)
        // Cross-commitment exclusion: a transaction already linked as ANY commitment's paid
        // installment can't be linked again — reuses the existing list endpoint, no new API.
        const linked = new Set<string>()
        for (const c of (allCommitments as Commitment[]) || []) {
          for (const i of c.installments) if (i.paid_txn_id) linked.add(i.paid_txn_id)
        }
        const rows: TxnCandidate[] = txns
          .filter((t) => !linked.has(t.id as string))
          .map((t) => ({
            id: t.id as string,
            desc: (t.description as string) || (t.memo as string) || '(no description)',
            date: t.date as string,
            account:
              accountNames.get(
                (t.to_account_id as string) || (t.from_account_id as string) || '',
              ) || '',
            amount_base: Number(t.base_amount ?? 0),
          }))
          .sort(
            (a, b) => Math.abs(a.amount_base - baseAmount) - Math.abs(b.amount_base - baseAmount),
          )
          .slice(0, 4)
        setCands(rows)
      })
      .catch(() => setCands([]))
    return () => {
      cancelled = true
    }
  }, [baseAmount])

  return (
    <div className="c2-addbox" style={{ margin: '6px 0 4px' }}>
      <div className="c3-linkbox-h">Link a transaction to "{label}"</div>
      {cands === null && <div className="c2-fund-empty">Looking for nearby transactions…</div>}
      {cands !== null && cands.length === 0 && (
        <div className="c2-fund-empty">No ledger transactions near {fBase(baseAmount)}.</div>
      )}
      {cands?.map((t) => (
        <div className="c3-txn" key={t.id}>
          <div className="c3-txn-main">
            <span className="c3-txn-d">{t.desc}</span>
            <span className="c3-txn-m">
              {fmtDate(t.date)}
              {t.account ? ` · ${t.account}` : ''}
            </span>
          </div>
          <span className="c3-txn-a money">{fBase(t.amount_base)}</span>
          <button className="c2-mini-btn pri" onClick={() => onLink(t.id)}>
            Link
          </button>
        </div>
      ))}
      <div className="c2-edit-btns" style={{ marginTop: 8 }}>
        <button className="c2-mini-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── reschedule box (overdue recovery) ──
function RescheduleBox({
  initial,
  onSave,
  onCancel,
}: {
  initial: string
  onSave: (date: string) => void
  onCancel: () => void
}) {
  const [date, setDate] = useState(initial)
  return (
    <div className="c2-addbox" style={{ margin: '6px 0 4px' }}>
      <div className="c2-edit">
        <div className="c2-edit-row">
          <label className="c2-field">
            New due date
            <DateField
              unstyled
              className="c2-input date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              autoFocus
            />
          </label>
          <span className="c2-field-hint">Keeps a note of the original date.</span>
        </div>
        <div className="c2-edit-btns">
          <button className="c2-mini-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="c2-mini-btn pri" onClick={() => date && onSave(date)}>
            Reschedule
          </button>
        </div>
      </div>
    </div>
  )
}

interface InstRowProps {
  c: Commitment
  i: Installment
  fx: Record<string, number>
  m: DetailMutations
  split: ReturnType<typeof installmentFunding>['byInst'][string] | undefined
}

function InstRow({ c, i, fx, m, split }: InstRowProps) {
  const [draft, setDraft] = useState<EditDraft | null>(null)
  const [box, setBox] = useState<'pay' | 'link' | 'resched' | null>(null)
  const s = dispStatus(i)
  // A fully-unfunded installment (alloc.length === 0, e.g. every source already spent by an
  // earlier same-due-date installment) must still show its "Unfunded" badge — that's the
  // worst case, not a reason to hide the box entirely.
  const showSplit =
    s !== 'paid' && !isTracker(c) && !!split && (split.alloc.length > 0 || split.shortfall > 0.5)

  if (draft) {
    return (
      <div className={'c2-si ' + s}>
        <div className="c2-si-rail">
          <span className="c2-si-dot" />
          <span className="c2-si-line" />
        </div>
        <div style={{ gridColumn: '2 / 4' }}>
          <EditRow
            draft={draft}
            setDraft={setDraft}
            withDate
            ccy={c.currency}
            descKey="description"
            descPlaceholder="Description · e.g. 50% on handover (optional)"
            onSave={() => {
              void m.updateInstallment(i.id, {
                label: draft.label,
                due_date: draft.due_date,
                amount: Number(draft.amount) || 0,
                description: draft.description,
              })
              setDraft(null)
            }}
            onCancel={() => setDraft(null)}
            onDelete={() => {
              void m.removeInstallment(i.id)
              setDraft(null)
            }}
          />
        </div>
      </div>
    )
  }

  return (
    <EntityRow
      as="div"
      className={'c2-si ' + s + (showSplit ? ' has-split' : '')}
      type="installment"
      id={i.id}
      label={i.label || `${c.name} instalment`}
      item={i}
      toFields={(it) => ({
        label: it.label,
        commitment_name: c.name,
        due_date: it.due_date,
        amount: it.amount,
        currency: c.currency,
        status: it.status,
      })}
      search={`${i.label} ${c.name} ${i.description}`}
      sublabel={`${c.name} · ${i.due_date}`}
    >
      <div className="c2-si-rail">
        <span className="c2-si-dot">{s === 'paid' ? <CheckIcon /> : null}</span>
        <span className="c2-si-line" />
      </div>
      <div>
        <div className="c2-si-label">
          {i.label}
          <button
            className="c2-iconbtn"
            aria-label={'Edit ' + i.label}
            onClick={() =>
              setDraft({
                label: i.label,
                due_date: i.due_date,
                amount: i.amount,
                description: i.description || '',
              })
            }
          >
            <PencilIcon />
          </button>
        </div>
        <div className="c2-si-meta">
          {fmtDate(i.due_date)}
          {i.description ? ` · ${i.description}` : ''}
          {s === 'paid' && i.paid_txn_id && (
            <>
              {' '}
              ·{' '}
              <button className="c2-link" onClick={() => m.onOpenTxn(i.paid_txn_id as string)}>
                txn <ArrowUpRight size={12} aria-hidden="true" />
              </button>
            </>
          )}
        </div>
        {box === 'pay' && (
          <PayBox
            ccy={c.currency}
            amount={i.amount}
            onCancel={() => setBox(null)}
            onPay={(amt) => {
              void m.payInstallment(i.id, amt)
              setBox(null)
            }}
          />
        )}
        {box === 'link' && (
          <LinkBox
            label={i.label}
            baseAmount={toBase(i.amount, c.currency, fx)}
            onCancel={() => setBox(null)}
            onLink={(txnId) => {
              void m.linkTxn(i.id, txnId)
              setBox(null)
            }}
          />
        )}
        {box === 'resched' && (
          <RescheduleBox
            initial={i.due_date}
            onCancel={() => setBox(null)}
            onSave={(date) => {
              void m.updateInstallment(i.id, {
                due_date: date,
                description:
                  (i.description ? i.description + ' · ' : '') + 'moved from ' + fmtDay(i.due_date),
              })
              setBox(null)
            }}
          />
        )}
      </div>
      <div className="c2-si-right">
        <span className="c2-si-amt money">{fCurrency(i.amount, c.currency)}</span>
        {c.currency !== getBaseCurrency() && (
          <span className="c3-si-base money">{fBase(toBase(i.amount, c.currency, fx))}</span>
        )}
        <div className="c3-si-metarow">
          {s === 'paid' ? (
            <span className="c2-si-badge paid">Paid</span>
          ) : (
            <>
              <span className={'c2-si-badge ' + s}>{STATUS_LABEL[s]}</span>
              <span className="c3-si-links">
                <button className="c2-link" onClick={() => setBox(box === 'pay' ? null : 'pay')}>
                  {isTracker(c) ? 'Mark done' : 'Mark paid'}
                </button>
                {!isTracker(c) && (
                  <button
                    className="c2-link dim2"
                    onClick={() => setBox(box === 'link' ? null : 'link')}
                  >
                    Link txn
                  </button>
                )}
                {s === 'overdue' && (
                  <button
                    className="c2-link dim2"
                    onClick={() => setBox(box === 'resched' ? null : 'resched')}
                  >
                    Reschedule
                  </button>
                )}
              </span>
            </>
          )}
        </div>
      </div>
      {showSplit && split && (
        <div className="c3-fundby">
          <div className="c3-fundby-h">
            <span className="c3-fundby-mark">
              <Spark size={12} />
            </span>{' '}
            Funded by <span className="c3-fundby-cap">auto-split from your funding plan</span>
          </div>
          <div className="c3-fundby-chips">
            {split.alloc.map((a) => (
              <span
                key={a.id}
                className={'c3-fchip' + (a.isEnvelope ? ' env' : '')}
                title={
                  a.label +
                  (a.isEnvelope
                    ? ' — budget target, capped at what it has saved'
                    : ' — one-time draw')
                }
              >
                <i className={'c2-fund-dot ' + a.type} />
                {a.label.replace(/\s*\(.*/, '')}
                <b className="money">{fBase(a.base)}</b>
              </span>
            ))}
            {split.shortfall > 0.5 && (
              <span className="c3-fchip short" title="No funding source covers this portion yet">
                Unfunded <b className="money">{fBase(split.shortfall)}</b>
              </span>
            )}
          </div>
          {split.envBase > 0.5 && (
            <div className="c3-fundby-guard">
              The set-aside covers only its saved share — the rest comes from one-time draws, so
              it's never overdrawn.
            </div>
          )}
          {split.shortfall > 0.5 && (
            <Button
              size="sm"
              variant="primary"
              className="fi-lucius c3-fundby-fix"
              iconLeft={<Spark size={15} />}
              onClick={() =>
                m.onAskLucius(
                  `The "${i.label}" installment on my "${c.name}" commitment (due ${fmtDate(i.due_date)}) has a ${fBase(split.shortfall)} funding gap no source covers yet. Help me close it.`,
                )
              }
            >
              Cover the gap with Lucius
              <ArrowRight size={14} aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </EntityRow>
  )
}

interface ScheduleProps {
  commitment: Commitment
  fx: Record<string, number>
  envelope: EnvelopeAccrual | null
  m: DetailMutations
}

export function Schedule({ commitment: c, fx, envelope, m }: ScheduleProps) {
  const [showAll, setShowAll] = useState(false)
  const tracker = isTracker(c)
  const many = c.installments.length > 10
  const fund = installmentFunding(c, (amt) => toBase(amt, c.currency, fx), envelope)
  const firstUnpaidIdx = Math.max(
    0,
    c.installments.findIndex((i) => i.status !== 'paid'),
  )
  let list = c.installments
  let hiddenAbove = 0
  let hiddenBelow = 0
  if (many && !showAll) {
    const start = Math.max(0, firstUnpaidIdx - 1)
    const end = firstUnpaidIdx + 5
    list = c.installments.slice(start, end)
    hiddenAbove = start
    hiddenBelow = Math.max(0, c.installments.length - end)
  }

  return (
    <div className="c2-exp-sec">
      <div className="c2-exp-sec-h">
        <span className="c2-exp-sec-t">{tracker ? 'Contributions' : 'Payment schedule'}</span>
        <span className="c2-exp-sec-note">
          {c.installments.filter((i) => i.status === 'paid').length}/{c.installments.length}{' '}
          {tracker ? 'made' : 'paid'} •{' '}
          <button
            className="c2-link lucius"
            onClick={() =>
              m.onAskLucius(
                `Help me add another ${tracker ? 'contribution' : 'installment'} to my "${c.name}" commitment's schedule.`,
              )
            }
          >
            Manage with Lucius <ArrowRight size={13} aria-hidden="true" />
          </button>
        </span>
      </div>
      <div>
        {many && !showAll && (
          <button
            className="c2-link dim2"
            style={{ marginBottom: 10 }}
            onClick={() => setShowAll(true)}
          >
            {hiddenAbove > 0 ? (
              <>
                <ArrowUp size={12} aria-hidden="true" /> {hiddenAbove} earlier • show all{' '}
                {c.installments.length}
              </>
            ) : (
              `Show all ${c.installments.length} payments…`
            )}
          </button>
        )}
        {list.map((i) => (
          <InstRow key={i.id} c={c} i={i} fx={fx} m={m} split={fund.byInst[i.id]} />
        ))}
        {many && !showAll && hiddenBelow > 0 && (
          <button className="c3-si-more" onClick={() => setShowAll(true)}>
            <span>
              {hiddenBelow} more payment{hiddenBelow > 1 ? 's' : ''}
            </span>
            <ArrowDown size={12} aria-hidden="true" />
          </button>
        )}
        {many && showAll && (
          <button
            className="c2-link dim2"
            style={{ marginTop: 6 }}
            onClick={() => setShowAll(false)}
          >
            Collapse
          </button>
        )}
      </div>
    </div>
  )
}
