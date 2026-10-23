// Actions & events tab — ported from detail3.jsx's `Actions`/`derivedActions`. `status:
// 'missed'` ("not happening") is REAL, persisted state (ADR 2026-07-22 backend addition) — not
// a client-only array like the design handoff (which had no backend to persist to).
// Per-action REMINDERS (the design's bell icon) were deferred in that same pass pending real
// backend wiring; now wired for real onto the existing scheduled-tasks/notify infrastructure
// (action=notify, schedule.type=once) — see setActionReminder in detailMutations.ts. Manual
// action entry ("+ Add action") was removed (owner directive) — Lucius is the only way in.
import { useState } from 'react'
import type { Commitment, CommitmentAction } from '../../../types/commitments'
import { fBase, fCurrency, toBase } from '../../../utils/format'
import { daysUntil, fmtDate, fmtDay } from '../../../utils/commitments'
import { BellIcon, CheckIcon, FilterIcon, Spark, XIcon } from './icons'
import type { DetailMutations } from './detailTypes'
import { buildDidntHint, buildImpactLine } from './impactLine'
import { Button, Modal } from '../../ui'
import { useNarrow } from './useNarrow'
import { entityRefProps } from '../../../utils/mentionable'
import { buildCtxFields } from '../../../lucius/ctxFields'
import { filterByDescriptor } from '../../../lucius/entityRegistry'
import { ArrowRight } from '../../../ui/icons'

// ── a synthetic checklist item derived from a not-yet-promoted funding draw (a loan draw-
//    down, an asset sale, a windfall receipt) — never persisted on its own; checking it off or
//    marking it "not happening" PROMOTES it into a real action via addAction. ──
interface DerivedAction {
  id: string
  label: string
  category: string
  due_date: string
  start_by: null
  amount: number | null
  currency: string
  counterparty: string
  note: string
  status: 'pending'
  funding_id: string
  derived: true
}

// How far apart a plan date and its real-world action date may sit and still be the same
// event. A month covers "available end of Feb" vs "he pays me on the 15th"; beyond that the
// two dates are making genuinely different claims and both deserve to be visible.
const DEDUP_DAY_WINDOW = 31

const daysApart = (a: string, b: string): number =>
  Math.abs(new Date(a + 'T00:00:00').getTime() - new Date(b + 'T00:00:00').getTime()) / 86400000

function derivedActions(c: Commitment, fx: Record<string, number>): DerivedAction[] {
  return (c.funding_plan || []).flatMap((f) => {
    if (f.disabled || !f.available_date) return []
    let label: string | null = null
    if (f.type === 'loan') label = `Draw down ${f.label}`
    else if (f.type === 'asset_sale') {
      label =
        f.sell_quantity != null
          ? `Sell ${f.sell_quantity} units — ${f.label.replace(/^Sell \d+ /, '')}`
          : `Sell ${f.label}`
    } else if (f.type === 'windfall') label = `Receive ${f.label}`
    if (!label) return []
    // Skip if a real action already describes this event. `funding_id` is the DESIGNED link
    // and is authoritative on its own — a plan date and the date you actually expect the money
    // are routinely months apart, so requiring `a.due_date === f.available_date` (as this did
    // until 2026-08-23) duplicated the row. Illustrative case: funding "Alex Tan repayment (d340M)"
    // dated 2026-09-10 beside action "Receive Alex Tan repayment (SGD 17k / d340M)" dated
    // 2027-01-20, same S$17,000, listed twice.
    //
    // For an UNLINKED action the value comparison reads the source's own frozen amount+currency
    // (never `value_base` alone, which is API-resolved and absent on a freshly-built row), and
    // the date test is a window rather than an equality.
    const srcBase =
      f.amount != null ? toBase(f.amount, f.currency || c.currency, fx) : (f.value_base ?? null)
    const already = (c.actions || []).some((a) => {
      if (a.funding_id && a.funding_id === f.id) return true
      if (a.amount == null || srcBase == null) return false
      if (Math.abs(toBase(a.amount, a.currency, fx) - srcBase) >= 2) return false
      return daysApart(a.due_date, f.available_date!) <= DEDUP_DAY_WINDOW
    })
    if (already) return []
    return [
      {
        id: `derived-${f.id}`,
        label,
        category: 'Funding plan',
        due_date: f.available_date,
        start_by: null,
        // f.amount/f.currency are the funding source's own frozen, real native pair — for
        // EVERY type now, including asset_sale (owner decision, 2026-07-26: the funding plan
        // stores what the user actually intends to raise, not a live-computed market value —
        // "the funding shouldn't be drifted by the market price, unless it's intentional").
        // This used to fall back to f.value_base (always base-currency) tagged with the
        // commitment's own native currency — a real bug: an SGD-scale number rendered with a
        // "₫" VND symbol, since those two never belonged together in the first place.
        amount: f.amount,
        currency: f.currency || c.currency,
        counterparty: f.note || '',
        note: '',
        status: 'pending' as const,
        funding_id: f.id,
        derived: true as const,
      },
    ]
  })
}

type ActionLike = CommitmentAction | DerivedAction

function aStatus(a: ActionLike): 'done' | 'missed' | 'overdue' | 'pending' {
  if (a.status === 'done') return 'done'
  if (a.status === 'missed') return 'missed'
  if (a.due_date && daysUntil(a.due_date) < 0) return 'overdue'
  return 'pending'
}
const resolved = (a: ActionLike): boolean => aStatus(a) === 'done' || aStatus(a) === 'missed'
const isDerived = (a: ActionLike): a is DerivedAction => 'derived' in a && a.derived === true

interface ActionsProps {
  commitment: Commitment
  fx: Record<string, number>
  m: DetailMutations
}

export function Actions({ commitment: c, fx, m }: ActionsProps) {
  const narrow = useNarrow()
  const [showAllActs, setShowAllActs] = useState(false)
  const [fStatus, setFStatus] = useState('all')
  const [fCat, setFCat] = useState('all')
  const [filterOpen, setFilterOpen] = useState(false)

  const acts: ActionLike[] = [...(c.actions || []), ...derivedActions(c, fx)]
  const cats = [
    ...new Set(acts.map((a) => (isDerived(a) ? 'Funding plan' : a.category)).filter(Boolean)),
  ].sort()
  const nOf = (s: string) => acts.filter((a) => aStatus(a) === s).length
  const sorted = [...acts]
    .filter((a) => fStatus === 'all' || aStatus(a) === fStatus)
    .filter((a) => fCat === 'all' || (isDerived(a) ? 'Funding plan' : a.category) === fCat)
    .sort((a, b) => {
      if (resolved(a) !== resolved(b)) return resolved(a) ? 1 : -1
      return (a.due_date || '').localeCompare(b.due_date || '')
    })
  const pending = acts.filter((a) => !resolved(a)).length
  const many = sorted.length > 8
  const visible = many && !showAllActs ? sorted.slice(0, 6) : sorted

  const promote = (a: DerivedAction, status: 'done' | 'missed') =>
    m.addAction({
      id: `act-${a.funding_id}-${Date.now()}`,
      label: a.label,
      category: 'financing',
      due_date: a.due_date,
      start_by: a.start_by,
      amount: a.amount,
      currency: a.currency,
      counterparty: a.counterparty,
      note: a.note,
      status,
      funding_id: a.funding_id,
    })

  const toggleDone = (a: ActionLike) => {
    if (isDerived(a)) void promote(a, 'done')
    else void m.updateAction(a.id, { status: a.status === 'done' ? 'pending' : 'done' })
  }

  const missPrompt = (a: ActionLike, fundBase: number | null) =>
    `I marked "${a.label}" as not happening on my "${c.name}" commitment${fundBase != null ? ` — it was funding ${fBase(fundBase)}` : ''}. Help me look at the impact and suggest a recovery plan.`

  const setMissed = (a: ActionLike, missed: boolean) => {
    const fid = a.funding_id || null
    if (isDerived(a)) {
      if (missed)
        void promote(a, 'missed').then(() => fid && void m.updateFunding(fid, { disabled: true }))
      return
    }
    void m.setActionMissed(a.id, fid, missed)
  }

  return (
    <div className="c2-exp-sec">
      <div className="c2-exp-sec-h">
        <span className="c2-exp-sec-t">Actions &amp; events</span>
        {narrow ? (
          <span className="c2-exp-sec-note c3-act-tools">
            <button
              className="c2-link lucius"
              onClick={() =>
                m.onAskLucius(
                  `Help me add an action or reminder to my "${c.name}" commitment's checklist.`,
                )
              }
            >
              Manage with Lucius <ArrowRight size={13} aria-hidden="true" />
            </button>
            <button
              className={
                'c2-fund-typefilter-btn' + (fStatus !== 'all' || fCat !== 'all' ? ' active' : '')
              }
              aria-label="Filter actions"
              onClick={() => setFilterOpen(true)}
            >
              <FilterIcon />
            </button>
          </span>
        ) : (
          <span className="c2-exp-sec-note c3-act-tools">
            <select
              className="c2-select"
              value={fStatus}
              onChange={(e) => {
                setFStatus(e.target.value)
                setShowAllActs(true)
              }}
              aria-label="Filter by status"
            >
              <option value="all">All statuses ({acts.length})</option>
              <option value="overdue">Overdue ({nOf('overdue')})</option>
              <option value="pending">Pending ({nOf('pending')})</option>
              <option value="done">Done ({nOf('done')})</option>
              <option value="missed">Not happening ({nOf('missed')})</option>
            </select>
            <select
              className="c2-select"
              value={fCat}
              onChange={(e) => {
                setFCat(e.target.value)
                setShowAllActs(true)
              }}
              aria-label="Filter by category"
            >
              <option value="all">All categories</option>
              {cats.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <button
              className="c2-link lucius"
              onClick={() =>
                m.onAskLucius(
                  `Help me add an action or reminder to my "${c.name}" commitment's checklist.`,
                )
              }
            >
              Manage with Lucius <ArrowRight size={13} aria-hidden="true" />
            </button>
          </span>
        )}
      </div>

      {narrow && filterOpen && (
        <Modal open onClose={() => setFilterOpen(false)} title="Filter actions">
          <div className="c3-menu-sheet" role="menu">
            <div className="c3-menu-label">Status</div>
            {[
              { v: 'all', label: `All statuses (${acts.length})` },
              { v: 'overdue', label: `Overdue (${nOf('overdue')})` },
              { v: 'pending', label: `Pending (${nOf('pending')})` },
              { v: 'done', label: `Done (${nOf('done')})` },
              { v: 'missed', label: `Not happening (${nOf('missed')})` },
            ].map((o) => (
              <button
                key={o.v}
                className={'c3-menu-it' + (fStatus === o.v ? ' on' : '')}
                role="menuitemradio"
                aria-checked={fStatus === o.v}
                onClick={() => {
                  setFStatus(o.v)
                  setShowAllActs(true)
                  setFilterOpen(false)
                }}
              >
                {o.label}
                {fStatus === o.v && <CheckIcon />}
              </button>
            ))}
            <div className="c3-menu-divider" />
            <div className="c3-menu-label">Category</div>
            <button
              className={'c3-menu-it' + (fCat === 'all' ? ' on' : '')}
              role="menuitemradio"
              aria-checked={fCat === 'all'}
              onClick={() => {
                setFCat('all')
                setShowAllActs(true)
                setFilterOpen(false)
              }}
            >
              All categories
              {fCat === 'all' && <CheckIcon />}
            </button>
            {cats.map((k) => (
              <button
                key={k}
                className={'c3-menu-it' + (fCat === k ? ' on' : '')}
                role="menuitemradio"
                aria-checked={fCat === k}
                onClick={() => {
                  setFCat(k)
                  setShowAllActs(true)
                  setFilterOpen(false)
                }}
              >
                {k}
                {fCat === k && <CheckIcon />}
              </button>
            ))}
          </div>
        </Modal>
      )}

      {sorted.length === 0 && acts.length > 0 && (
        <div className="c2-fund-empty">Nothing matches these filters.</div>
      )}
      {!acts.length && (
        <div className="c2-fund-empty">
          No actions yet — paperwork, signatures, and reminders tied to this commitment land here.
        </div>
      )}
      <div>
        {visible.map((a) => {
          const st = aStatus(a)
          const isDone = st === 'done'
          const isMissedRow = st === 'missed'
          const overdue = st === 'overdue'
          const fid = a.funding_id || null
          const fund = fid ? (c.funding_plan || []).find((f) => f.id === fid) : null
          const fundBase = fund?.value_base ?? null
          const category = isDerived(a) ? 'Funding plan' : a.category

          return (
            <div
              key={a.id}
              className={
                'c2-act' +
                (isDone ? ' done' : '') +
                (isMissedRow ? ' missed' : '') +
                (overdue ? ' od-row' : '')
              }
              {...(isDerived(a)
                ? {}
                : {
                    ...entityRefProps(
                      'commitment_action',
                      a.id,
                      a.label,
                      `${a.label} ${c.name} ${a.note ?? ''}`,
                      `${c.name} • ${a.due_date}`,
                    ),
                    'data-ctx': '',
                    'data-ctx-type': 'commitment_action',
                    'data-ctx-fields': buildCtxFields(
                      filterByDescriptor('commitment_action', {
                        label: a.label,
                        commitment_name: c.name,
                        category,
                        due_date: a.due_date,
                        status: st,
                      }),
                    ),
                    draggable: true,
                  })}
            >
              <div className="c2-act-main">
                <div className="c2-act-t">
                  {a.label}
                  {fund && !isMissedRow && !isDone && (
                    <span
                      className="c3-act-load"
                      title="This step unlocks a funding source — mark it not happening and that funding drops from the projection"
                    >
                      unlocks funding
                    </span>
                  )}
                </div>
                <div className="c2-act-meta">
                  {isDerived(a) ? (
                    <span
                      className="c2-act-cat"
                      title="Generated from the funding plan — edit the funding source to change it"
                    >
                      {category}
                    </span>
                  ) : (
                    <span className="c2-act-cat">{category}</span>
                  )}
                  {isMissedRow ? (
                    // The impact rides as a title= tooltip rather than a permanently-rendered
                    // hint line — the badge moved inline into the meta row to stop costing a
                    // full extra row per action. Only claims the funding consequence when this
                    // action actually gates a funding source.
                    <span
                      className="c3-missbadge"
                      title={
                        fund
                          ? 'Marked not happening — the funding source it unlocked has dropped from the projection'
                          : 'Marked not happening — excluded from this commitment’s schedule'
                      }
                    >
                      Not happening
                    </span>
                  ) : (
                    <>
                      {a.start_by && !isDone && (
                        <span className="c3-startby">start by {fmtDay(a.start_by)}</span>
                      )}
                      <span className={'c2-act-due' + (overdue ? ' od' : '')}>
                        {overdue ? 'Overdue • ' : ''}
                        {a.due_date ? fmtDate(a.due_date) : '—'}
                      </span>
                    </>
                  )}
                  {a.counterparty && <span>• {a.counterparty}</span>}
                </div>
                {a.note && !isMissedRow && <div className="c2-act-note">{a.note}</div>}
                {isMissedRow && (
                  <div className="c3-act-impact">
                    <div className="c3-impact-line">
                      {buildImpactLine({
                        commitmentName: c.name,
                        action: a as CommitmentAction,
                        fundingAmountBase: fundBase,
                      })}
                    </div>
                    <div className="c3-impact-cta">
                      <Button
                        size="sm"
                        variant="primary"
                        className="fi-lucius"
                        iconLeft={<Spark size={15} />}
                        onClick={() => m.onAskLucius(missPrompt(a, fundBase))}
                      >
                        Plan the fix with Lucius
                        <ArrowRight size={14} aria-hidden="true" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setMissed(a, false)}>
                        Undo
                      </Button>
                    </div>
                  </div>
                )}
              </div>
              <div className="c2-act-trail">
                {!isDerived(a) && a.due_date ? (
                  <button
                    className={'c2-act-bell' + (a.reminder_automation_id ? ' on' : '')}
                    aria-label={a.reminder_automation_id ? 'Cancel reminder' : 'Remind me'}
                    title={a.reminder_automation_id ? 'Cancel reminder' : 'Remind me'}
                    onClick={() => void m.setActionReminder(a, !a.reminder_automation_id)}
                  >
                    <BellIcon on={!!a.reminder_automation_id} />
                  </button>
                ) : (
                  <span className="c2-act-bell" aria-hidden="true" />
                )}
                <span className="c2-act-amt money">
                  {a.amount != null ? fCurrency(a.amount, a.currency || c.currency) : ''}
                </span>
                {!isMissedRow && (
                  <span className="c2-act-icon-acts">
                    <button
                      className={'c2-need-icon-btn pay' + (isDone ? ' on' : '')}
                      aria-label={isDone ? 'Undo done' : 'Mark done'}
                      title={isDone ? 'Undo done' : 'Mark done'}
                      onClick={() => toggleDone(a)}
                    >
                      <CheckIcon />
                    </button>
                    {!isDone && (
                      <button
                        className="c2-need-icon-btn miss"
                        aria-label="Not happening"
                        title={buildDidntHint(a as CommitmentAction, fundBase)}
                        onClick={() => setMissed(a, true)}
                      >
                        <XIcon />
                      </button>
                    )}
                  </span>
                )}
              </div>
            </div>
          )
        })}
        {many && (
          <button
            className="c2-link dim2"
            style={{ marginTop: 8 }}
            onClick={() => setShowAllActs(!showAllActs)}
          >
            {showAllActs ? 'Collapse' : `Show all ${sorted.length} actions…`}
          </button>
        )}
      </div>
      {pending === 0 && acts.length > 0 && (
        <div className="c2-fund-empty" style={{ marginTop: 8 }}>
          Nothing pending — every action is done or marked not happening.
        </div>
      )}
    </div>
  )
}
