// plan/cashflow.tsx — Cash Flow sub-tab (cfx-* classes).
// Verbatim port of design_handoffs/plan_handoff/prototype/plan/cashflow.jsx.
// Mock data (buildMonths / EXTRA / COMMIT_MONTHS / shape swing) replaced by GET /api/cashflow.
// Trajectory-shape what-if (Steady climb / Seasonal swings / …) is intentionally dropped —
// it was a mock-data flavor swing that is meaningless on real server-computed balances.
// `onAsk` renamed to `onAskLucius` to match the existing props interface.

import { useState, useRef, useEffect, Fragment } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { Button, ActionMenu, Cluster, EmptyState, TabSkeleton } from '../../ui'
import { LuciusCta } from '../../intro/LuciusCta'
import { SCREEN_INTROS } from '../../intro/screenIntros'
import { reportScreenFill } from '../../intro/screenState'
import { FlowIcon, X } from './icons'
import { ChartArea } from '../../../ui/icons'
import { fBase } from './planData'
import { getCurrencySymbol, millionsUp } from '../../../utils/format'
import { useCashflowData } from './useCashflowData'
import type { MappedRow } from './useCashflowData'
import { useRegisterScreenFacts, useRegisterScreenPrompts } from '../../../lucius/useScreenContract'
import { PlanningModal } from './PlanningModal'
import { FilterSelect } from './budgetParts'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { thisMonth } from '../../../hooks/useBudget'
import { MoneyFlowModal } from './moneyFlowModal'
import { cardCaveat } from '../../../utils/cardCaveat'
import type { RegisterFilter } from './budget'

// ── Helpers ────────────────────────────────────────────────────────────────────

function fCF(n: number): string {
  const a = Math.abs(n),
    s = n < 0 ? '−' : ''
  const sym = getCurrencySymbol()
  const big = millionsUp(a)
  if (big) return s + sym + big.value + big.unit
  if (a >= 1e3) return s + sym + (a / 1e3).toFixed(2) + 'K'
  return s + sym + a.toFixed(0)
}

const CAT_COLOR: Record<string, string> = {
  Commitment: 'var(--bad)',
  Funding: 'var(--good)',
  Income: 'var(--good)',
  Expenses: 'var(--warn)',
}

/** " · 4 days left" for the in-progress month, so its prorated recurring figure reads as a
 *  partial month rather than an unexplained shortfall. Empty for any other month. */
function daysLeftLabel(ym: string): string {
  const [y, m] = ym.split('-').map(Number)
  const today = new Date()
  if (!y || !m || y !== today.getFullYear() || m !== today.getMonth() + 1) return ''
  const inMonth = new Date(y, m, 0).getDate()
  const left = Math.max(inMonth - today.getDate(), 0)
  return ` • ${left} ${left === 1 ? 'day' : 'days'} left of ${inMonth}`
}

const HIST_VISIBLE = 3

// ── BalanceChart ───────────────────────────────────────────────────────────────
// SVG line + area split at the safety floor and at zero so segments recolour
// blue/amber/red by zone. Includes hover crosshair, low-point marker, zero-
// crossing "runs dry" marker, and a dashed safety-floor line.

function BalanceChart({
  months,
  minMonth,
  minBal,
  floor,
}: {
  months: MappedRow[]
  minMonth: MappedRow | undefined
  minBal: number
  floor: number
}) {
  const LOW_T = floor
  const W = 680,
    H = 132,
    pT = 18,
    pB = 14
  const vals = months.map((m) => m.balance)
  const lo = Math.min(...vals, LOW_T * 0.92)
  const hi = Math.max(...vals)
  const span = hi - lo || 1
  const n = months.length
  const X = (i: number) => (i / (n - 1)) * W
  const Y = (v: number) => pT + (1 - (v - lo) / span) * (H - pT - pB)
  const thrY = Y(LOW_T)
  const curIdx = months.findIndex((m) => m.isCurrent)
  const lowIdx = months.findIndex((m) => m === minMonth)
  const pctX = (i: number) => (X(i) / W) * 100

  // Split the balance line at the safety floor (and at zero) so only the
  // below-floor portions render amber, and below-zero portions red. Blue elsewhere.
  const lvls = [LOW_T, 0]
  const rawPts = months.map((m, i) => ({ x: X(i), v: m.balance }))
  const zoneOf = (v: number) => (v < 0 ? 'neg' : v < LOW_T ? 'tight' : 'ok')
  const segs: Array<Array<{ x: number; v: number }>> = []
  {
    let cur: Array<{ x: number; v: number }> = [rawPts[0]!]
    for (let i = 0; i < rawPts.length - 1; i++) {
      const a = rawPts[i]!,
        b = rawPts[i + 1]!
      const cuts: Array<{ t: number; x: number; v: number }> = []
      for (const L of lvls) {
        if ((a.v < L && b.v > L) || (a.v > L && b.v < L)) {
          const t = (L - a.v) / (b.v - a.v)
          cuts.push({ t, x: a.x + (b.x - a.x) * t, v: L })
        }
      }
      cuts.sort((p, q) => p.t - q.t)
      for (const c of cuts) {
        cur.push({ x: c.x, v: c.v })
        segs.push(cur)
        cur = [{ x: c.x, v: c.v }]
      }
      cur.push(b)
    }
    segs.push(cur)
  }
  const ZCOL: Record<string, string> = {
    ok: 'var(--accent)',
    tight: 'var(--warn)',
    neg: 'var(--bad)',
  }
  const segZone = (seg: Array<{ x: number; v: number }>) =>
    zoneOf(seg.reduce((s, p) => s + p.v, 0) / seg.length)
  const segLine = (seg: Array<{ x: number; v: number }>) =>
    'M' + seg.map((p) => `${p.x.toFixed(1)},${Y(p.v).toFixed(1)}`).join(' L')
  const segArea = (seg: Array<{ x: number; v: number }>) =>
    segLine(seg) + ` L${seg[seg.length - 1]!.x.toFixed(1)},${H} L${seg[0]!.x.toFixed(1)},${H} Z`

  const [hover, setHover] = useState<number | null>(null)
  const plotRef = useRef<HTMLDivElement>(null)
  function onMove(e: React.MouseEvent) {
    const box = plotRef.current!.getBoundingClientRect()
    const rel = (e.clientX - box.left) / box.width
    const idx = Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1))))
    setHover(idx)
  }
  const hm = hover != null ? months[hover]! : null
  const hTone = hm ? (hm.isNeg ? 'neg' : hm.isTight ? 'warn' : '') : ''
  const hPct = hover != null ? pctX(hover) : 0
  const tipShift = hPct < 14 ? '0%' : hPct > 86 ? '-100%' : '-50%'

  return (
    <div className="cfx-chart">
      <div
        className="cfx-chart-plot"
        ref={plotRef}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          width="100%"
          height={H}
          aria-hidden="true"
        >
          <defs>
            <linearGradient id="cfx-ok" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.30" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="cfx-tight" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--warn)" stopOpacity="0.32" />
              <stop offset="100%" stopColor="var(--warn)" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="cfx-neg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--bad)" stopOpacity="0.34" />
              <stop offset="100%" stopColor="var(--bad)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {segs.map((seg, i) => (
            <path key={'a' + i} d={segArea(seg)} fill={`url(#cfx-${segZone(seg)})`} />
          ))}
          <line
            x1="0"
            y1={thrY}
            x2={W}
            y2={thrY}
            stroke="var(--warn)"
            strokeWidth="1"
            strokeDasharray="3 4"
            vectorEffect="non-scaling-stroke"
            opacity="0.5"
          />
          {curIdx >= 0 && (
            <line
              x1={X(curIdx)}
              y1="0"
              x2={X(curIdx)}
              y2={H}
              stroke="var(--line-strong)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {segs.map((seg, i) => (
            <path
              key={'l' + i}
              d={segLine(seg)}
              fill="none"
              stroke={ZCOL[segZone(seg)] ?? 'var(--accent)'}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
        </svg>
        {lowIdx >= 0 &&
          minMonth &&
          (() => {
            const mkY = Y(minMonth.balance)
            const above = mkY > H / 2
            const lblTop = above ? 2 : H - 2
            const lblTY = above ? '0%' : '-100%'
            const lblTX = pctX(lowIdx) < 18 ? '-6%' : pctX(lowIdx) > 82 ? '-94%' : '-50%'
            return (
              <>
                <span
                  className="cfx-mk cfx-mk-low"
                  style={{ left: pctX(lowIdx) + '%', top: mkY + 'px' }}
                />
                <span
                  className="cfx-lbl cfx-lbl-low"
                  style={{
                    left: pctX(lowIdx) + '%',
                    top: lblTop + 'px',
                    transform: `translate(${lblTX}, ${lblTY})`,
                  }}
                >
                  Low • <b className="mask">{fCF(minBal)}</b>
                </span>
              </>
            )
          })()}
        {curIdx >= 0 && (
          <span
            className="cfx-mk cfx-mk-now"
            style={{ left: pctX(curIdx) + '%', top: Y(months[curIdx]!.balance) + 'px' }}
          />
        )}
        {(() => {
          // Zero-crossing: mark the point where cash first runs dry.
          let zi = -1
          for (let i = 1; i < months.length; i++) {
            if (months[i - 1]!.balance >= 0 && months[i]!.balance < 0) {
              zi = i
              break
            }
          }
          if (zi < 0) return null
          const a = months[zi - 1]!.balance,
            b = months[zi]!.balance
          const t = a / (a - b)
          const zx = X(zi - 1) + (X(zi) - X(zi - 1)) * t
          const zpct = (zx / W) * 100
          const zy = Y(0)
          const lblTX = zpct < 20 ? '-6%' : zpct > 80 ? '-94%' : '-50%'
          return (
            <>
              <span className="cfx-zero-line" style={{ left: zpct + '%' }} />
              <span className="cfx-mk cfx-mk-zero" style={{ left: zpct + '%', top: zy + 'px' }} />
              <span
                className="cfx-lbl cfx-lbl-zero"
                style={{ left: zpct + '%', top: zy + 'px', transform: `translate(${lblTX}, 11px)` }}
              >
                Runs dry • {months[zi]!.label}
              </span>
            </>
          )
        })()}
        {hm && (
          <>
            <span className="cfx-cross" style={{ left: hPct + '%' }} />
            <span
              className={'cfx-mk cfx-mk-hover ' + hTone}
              style={{ left: hPct + '%', top: Y(hm.balance) + 'px' }}
            />
            <span
              className="cfx-tip"
              style={{
                left: hPct + '%',
                top: Y(hm.balance) + 'px',
                transform: `translate(${tipShift}, calc(-100% - 14px))`,
              }}
            >
              <span className="cfx-tip-m">
                {hm.label}
                {hm.isCurrent ? ' • Today' : ''}
              </span>
              <span className={'cfx-tip-b mask ' + hTone}>{fCF(hm.balance)}</span>
              <span className={'cfx-tip-n mask ' + (hm.net >= 0 ? 'pos' : 'neg')}>
                {hm.net >= 0 ? '+' : '−'}
                {fCF(Math.abs(hm.net))} net
              </span>
            </span>
          </>
        )}
      </div>
      <div className="cfx-chart-axis">
        <span>{months[0]!.label}</span>
        <span className="cfx-thr-key">Safety floor {fCF(LOW_T)}</span>
        <span>{months[n - 1]!.label}</span>
      </div>
    </div>
  )
}

// ── LedgerRow ─────────────────────────────────────────────────────────────────

function EventsChevron() {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

function LedgerRow({
  row,
  expanded,
  onToggle,
  isMobile,
  onOpenFlow,
}: {
  row: MappedRow
  expanded: boolean
  onToggle: () => void
  isMobile?: boolean
  /** Actual months and the current one (month to date) — a future month has no transactions. */
  onOpenFlow?: (ym: string) => void
}) {
  const flowBtn = onOpenFlow && (row.isHistory || row.isCurrent) && (
    <button
      type="button"
      className="cfx-flow-btn"
      aria-label={`See money flow for ${row.label}`}
      title="Money flow"
      onClick={(e) => {
        e.stopPropagation()
        onOpenFlow(row.ym)
      }}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <FlowIcon />
    </button>
  )
  const hasEvents = row.events.length > 0
  const balTone = row.isNeg ? 'neg' : row.isTight ? 'warn' : ''
  const stateCls = row.isCurrent
    ? 'today'
    : row.isNeg
      ? 'neg'
      : row.isTight
        ? 'tight'
        : row.isHistory
          ? 'hist'
          : ''
  const clickProps = hasEvents
    ? {
        onClick: onToggle,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggle()
          }
        },
        role: 'button' as const,
        tabIndex: 0,
        'aria-expanded': expanded,
      }
    : {}

  // ── Mobile: a card, not a table row — the 5-column grid forces a 680px+
  // minimum width, which on a phone means every row is horizontally scrolled.
  // Reuses the SAME `.pl-mcard`/`.pl-mnum` shells the Budget envelope cards
  // build on (budgetCards.tsx) so the two sub-tabs read as one card language.
  if (isMobile) {
    return (
      <div className="cfx-rowwrap">
        <div
          className={
            'pl-mcard cfx-mcard ' +
            stateCls +
            (expanded ? ' open' : '') +
            (hasEvents ? ' has-events' : '')
          }
          data-cfx-today={row.isCurrent || undefined}
          {...clickProps}
        >
          <div className="pl-mcard-head">
            <span className="pl-mcard-name">
              <span className="cfx-m-name">
                {row.mName} <span className="cfx-m-yr">'{String(row.year).slice(2)}</span>
              </span>
              {row.isCurrent && <span className="cfx-tag today">Today</span>}
              {row.isHistory && !row.isCurrent && <span className="cfx-tag hist">Actual</span>}
              {flowBtn}
            </span>
            <span className={'cfx-balv mask cfx-mcard-bal' + (balTone ? ' ' + balTone : '')}>
              <RowBalance row={row} />
            </span>
          </div>
          <div className="pl-mcard-div" />
          <div className="pl-mcard-nums">
            <div className="pl-mnum">
              <span className="pl-mnum-lbl" title={GROSS_NOTE}>
                In
              </span>
              <span className="cfx-mnum-v mask pos">+{fCF(row.total_in)}</span>
            </div>
            <div className="pl-mnum">
              <span className="pl-mnum-lbl" title={GROSS_NOTE}>
                Out
              </span>
              <span className="cfx-mnum-v mask">−{fCF(row.total_out)}</span>
            </div>
            <div className="pl-mnum">
              <span className="pl-mnum-lbl">Net</span>
              <span className={'cfx-mnum-v mask ' + (row.net >= 0 ? 'pos' : 'neg')}>
                {row.net >= 0 ? '+' : '−'}
                {fCF(Math.abs(row.net))}
              </span>
            </div>
          </div>
          {hasEvents && (
            <span className={'cfx-tag evt' + (expanded ? ' open' : '')}>
              {row.events.length} event{row.events.length > 1 ? 's' : ''}
              <EventsChevron />
            </span>
          )}
        </div>

        {expanded && hasEvents && <LedgerRowDetail row={row} />}
      </div>
    )
  }

  return (
    <div className="cfx-rowwrap">
      <div
        className={
          'cfx-row ' + stateCls + (expanded ? ' open' : '') + (hasEvents ? ' has-events' : '')
        }
        data-cfx-today={row.isCurrent || undefined}
        {...clickProps}
      >
        <div className="cfx-m">
          <span className="cfx-m-name">
            {row.mName} <span className="cfx-m-yr">'{String(row.year).slice(2)}</span>
          </span>
          {row.isCurrent && <span className="cfx-tag today">Today</span>}
          {row.isHistory && !row.isCurrent && <span className="cfx-tag hist">Actual</span>}
          {hasEvents && (
            <span className={'cfx-tag evt' + (expanded ? ' open' : '')}>
              {row.events.length} event{row.events.length > 1 ? 's' : ''}
              <EventsChevron />
            </span>
          )}
          {flowBtn}
        </div>

        <span className="cfx-col cfx-in mask">+{fCF(row.total_in)}</span>
        <span className="cfx-col cfx-out mask">−{fCF(row.total_out)}</span>
        <span className={'cfx-col cfx-net mask ' + (row.net >= 0 ? 'pos' : 'neg')}>
          {row.net >= 0 ? '+' : '−'}
          {fCF(Math.abs(row.net))}
        </span>
        <span className={'cfx-col cfx-balv mask' + (balTone ? ' ' + balTone : '')}>
          <RowBalance row={row} />
        </span>
      </div>

      {expanded && hasEvents && <LedgerRowDetail row={row} />}
    </div>
  )
}

/** What In and Out count. Reports nets a refund against its category, so the same month reads
 *  smaller there (persona QA 2026-10-14: about S$5K apart on each side, and the owner doubted
 *  both). */
const GROSS_NOTE =
  'In and Out are gross: a refund or reimbursement counts as money in, and the purchase it undoes still counts as money out. Reports nets refunds against their categories, so the same month looks smaller there.'

/** A month-end balance, or "Before tracking" for a past month earlier than any cash account's
 *  Starting Balance, where nothing was counted yet (it is not zero). */
function RowBalance({ row }: { row: MappedRow }) {
  if (row.balanceKnown) return <>{fCF(row.balance)}</>
  return (
    <span className="cfx-bal-none" title="Before any of your cash accounts were tracked">
      Before tracking
    </span>
  )
}

function LedgerRowDetail({ row }: { row: MappedRow }) {
  return (
    <div className="cfx-detail">
      <div className="cfx-events">
        {/* Header wording is per month KIND, because one phrase cannot be true of all
                three. The retired wording called these items scheduled, which was wrong
                everywhere it appeared: a past month
                lists what actually happened, and a future month lists recurring streams plus
                an average — none of which is "scheduled" (owner: "i don't think we should say
                this, nothing is scheduled here though"). The CURRENT month is also prorated
                to the days still remaining — the elapsed days are already in its real
                transactions — so it says how many days that is rather than leaving a
                mysteriously small figure. */}
        <div className="cfx-events-h">
          {row.isHistory
            ? 'What happened this month'
            : row.isCurrent
              ? `Rest of this month • ${fCF(row.recIn)} recurring in • ${fCF(row.recOut)} recurring out${daysLeftLabel(row.ym)}`
              : `Expected this month • ${fCF(row.recIn)} recurring in • ${fCF(row.recOut)} recurring out`}
        </div>
        {row.events.map((ev, i) => {
          // Events are ordered one-offs first, recurring last. Mark the boundary so a
          // long month (127 real transactions in July) doesn't bury its recurring
          // backbone in the same undifferentiated wall — these are two different kinds
          // of thing: what happened vs. what repeats.
          // Three groups, in order: dated one-offs → named recurring streams → the
          // averaged variable buckets. Each boundary gets its own header, so the two
          // averages at the bottom read as their own thing instead of trailing off the
          // end of the stream list.
          const prev = i > 0 ? row.events[i - 1]! : null
          const startsRecurring = !!prev && ev.recurring && !ev.variable && !prev.recurring
          const startsVariable = !!prev && !!ev.variable && !prev.variable
          return (
            <Fragment key={i}>
              {/* Its OWN line, styled exactly like the section header above — an
                      absolutely-positioned label inside the event row collided with the
                      narrow category column and wrapped ("RECURRING" stacked over
                      "INCOME"). A sibling in the flex column just works. */}
              {startsRecurring && (
                <div className="cfx-events-h cfx-ev-group">
                  Recurring{row.isCurrent ? ' • rest of month' : ''}
                </div>
              )}
              {startsVariable && (
                <div className="cfx-events-h cfx-ev-group">Variable • averaged</div>
              )}
              <div className={'cfx-ev' + (ev.recurring ? ' recurring' : '')}>
                <span className="cfx-ev-cat" style={{ color: CAT_COLOR[ev.cat] || 'var(--faint)' }}>
                  {ev.cat}
                </span>
                <span className={'cfx-ev-amt mask ' + (ev.dir === 'in' ? 'pos' : 'neg')}>
                  {ev.dir === 'in' ? '+' : '−'}
                  {fBase(ev.amt)}
                </span>
                <span className="cfx-ev-label">{ev.label}</span>
                {ev.note && <span className="cfx-ev-note">{ev.note}</span>}
              </div>
            </Fragment>
          )
        })}
      </div>
    </div>
  )
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface CashFlowTabProps {
  onAskLucius: (text: string) => void
  toast: (msg: string) => void
  onOpenRegister?: (filter: RegisterFilter) => void
}

function hasProjectionInputs(data: ReturnType<typeof useCashflowData>['data']): boolean {
  return (
    !!data &&
    (data.monthly_income_avg !== 0 ||
      data.monthly_expenses_avg !== 0 ||
      data.months.some(
        (month) => !month.is_current && !month.is_history && month.events.length > 0,
      ))
  )
}

// ── CashFlowTab ───────────────────────────────────────────────────────────────

export function CashFlowTab(props: CashFlowTabProps) {
  const { toast } = props

  const {
    data,
    loading,
    error,
    months,
    floor,
    incomeVal,
    setIncomeVal,
    expenseVal,
    setExpenseVal,
    expGrowth,
    setExpGrowth,
    incGrowth,
    setIncGrowth,
    floorVal,
    setFloorVal,
    includeFixedDeposits,
    includeInvestments,
    includeCommitments,
    setIncludeCommitments,
    collapsedYears,
    setCollapsedYears,
    filter,
    setFilter,
    load,
    debouncedLoad,
    toggleFixedDeposits,
    toggleInvestments,
    resetSettings,
    saveFloorToProfile,
  } = useCashflowData()
  // Its first-visit intro offers setup on an empty projection, a walkthrough on a real one.
  useEffect(() => {
    if (data) reportScreenFill('plan-cashflow', hasProjectionInputs(data) ? 'filled' : 'empty')
  }, [data])

  const isMobile = useIsMobile()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [flowMonth, setFlowMonth] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [showHist, setShowHist] = useState(false)

  // Sticky ledger header: mirror the body's horizontal scroll onto it so columns stay aligned.
  const cfxBodyRef = useRef<HTMLDivElement>(null)
  const cfxHeadRef = useRef<HTMLDivElement>(null)
  const syncCfxHead = () => {
    const body = cfxBodyRef.current,
      head = cfxHeadRef.current
    if (body && head) head.style.transform = 'translateX(' + -body.scrollLeft + 'px)'
  }
  useEffect(syncCfxHead)

  // Scroll the current-month row to just under the sticky header.
  const doScrollToday = () => {
    const row = document.querySelector('[data-cfx-today="true"]')
    if (!row) return
    let sc: Element | null = row.parentElement
    while (sc && sc !== document.body) {
      const s = window.getComputedStyle(sc)
      if (
        (s.overflowY === 'auto' || s.overflowY === 'scroll') &&
        (sc as HTMLElement).scrollHeight > (sc as HTMLElement).clientHeight + 5
      )
        break
      sc = sc.parentElement
    }
    const scroller =
      sc && sc !== document.body ? sc : document.scrollingElement || document.documentElement
    const headH =
      parseInt(getComputedStyle(document.documentElement).getPropertyValue('--pl-mast-h')) || 0
    const headWrap = document.querySelector('.cfx-thead-wrap')
    const headWrapH = headWrap ? (headWrap as HTMLElement).offsetHeight : 0
    const rowTop = row.getBoundingClientRect().top
    const refTop =
      scroller === document.scrollingElement || scroller === document.documentElement
        ? 0
        : scroller.getBoundingClientRect().top
    ;(scroller as HTMLElement).scrollTop += rowTop - refTop - headH - headWrapH - 8
  }

  const current = months.find((m) => m.isCurrent)

  const jumpToToday = () => {
    if (current && collapsedYears.has(current.year)) {
      setCollapsedYears((p) => {
        const n = new Set(p)
        n.delete(current.year)
        return n
      })
      setTimeout(doScrollToday, 60)
    } else {
      doScrollToday()
    }
  }

  // Plan keeps all three sub-tabs mounted, so this one is registered with Lucius only while it
  // is the sub-tab on screen (the URL's ?sub= mirrors it): a hidden pane that registered last
  // handed Lucius cash-flow figures while the user was on Budget (QA, 2026-10-14).
  const onCashFlowTab = useRouterState({
    select: (s) => (s.location.search as { sub?: string }).sub === 'cashflow',
  })

  // Screen-awareness markers: registered before early returns (React hooks rule).
  // The fact factory re-derives from `data` + `months` on every Lucius snapshot
  // so it always reflects the latest fetch, including what-if overrides.
  useRegisterScreenFacts(() => {
    if (!data || !hasProjectionInputs(data) || months.length === 0) return []
    const futureMos = months.filter((m) => !m.isHistory)
    const cur = months.find((m) => m.isCurrent)
    const minB = futureMos.length > 0 ? Math.min(...futureMos.map((m) => m.balance)) : 0
    const minMo = futureMos.find((m) => m.balance === minB)
    const tight = futureMos.filter((m) => m.isTight || m.isNeg).length
    const rwy =
      data.income_runway_months != null ? data.income_runway_months.toFixed(1) + ' months' : '—'
    // `cur.balance` is the month's PROJECTED END balance, not what is in the accounts today.
    // Labelled "Cash on hand" it read as a mismatch with the user's real cash, and Lucius
    // doubted the book (a S$4,124 projection against S$2,150 held).
    return [
      [
        `Projected balance at end of ${cur ? cur.label : 'this month'}`,
        fBase(cur ? cur.balance : 0),
      ],
      ['Financial runway', rwy],
      ['Monthly burn', fBase(data.monthly_expenses_avg)],
      ['Lowest projected balance', fBase(minB) + (minMo ? ' • ' + minMo.label : '')],
      ['Tight months', String(tight)],
    ]
  }, onCashFlowTab)
  useRegisterScreenPrompts(
    () => [
      'How long will my cash last?',
      'What if I cut my spending by 10%?',
      'When does my balance dip below the safety floor?',
      'What if I draw on my investments?',
    ],
    onCashFlowTab,
  )

  if (loading && !data) {
    return (
      <div className="pl-sub cfx" style={{ gap: 0, marginTop: 16 }}>
        <TabSkeleton variant="cashflow" label="Loading cash flow…" header={false} />
      </div>
    )
  }
  if (error) {
    return (
      <div className="pl-sub cfx" style={{ gap: 0, marginTop: 16 }}>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--bad)', fontSize: 13 }}>
          {error}
        </div>
      </div>
    )
  }
  if (!data || months.length === 0) return null
  if (!hasProjectionInputs(data)) {
    return (
      <EmptyState
        fill
        icon={<ChartArea size={28} strokeWidth={1.5} aria-hidden="true" />}
        title="No cash flow to project yet"
        body="Add monthly income and spending, or a future commitment to build a projection."
        action={
          <Cluster gap="--space-3" justify="center">
            <LuciusCta
              label="Set up with Lucius"
              message={SCREEN_INTROS['plan-cashflow'].lucius}
              onAskLucius={props.onAskLucius}
            />
          </Cluster>
        }
      />
    )
  }

  // Derived values from mapped months.
  const future = months.filter((m) => !m.isHistory)
  const assetBoost =
    (data.include.fixed_deposits ? data.fixed_deposit_base : 0) +
    (data.include.investments ? data.investments_base : 0)
  const floorChanged = floorVal !== '' && parseFloat(floorVal) !== data.low_balance_threshold
  const simActive =
    data.simulation_active ||
    includeFixedDeposits ||
    includeInvestments ||
    !includeCommitments ||
    floorChanged ||
    expGrowth !== '3' ||
    incGrowth !== '0'

  const avgNet = future.length > 0 ? future.reduce((s, m) => s + m.net, 0) / future.length : 0
  const inc = data.monthly_income_avg
  const exp = data.monthly_expenses_avg
  const savingPct = inc > 0 ? Math.round((avgNet / inc) * 100) : 0
  const endBal = future.length > 0 ? future[future.length - 1]!.balance : 0
  const runway = data.income_runway_months != null ? data.income_runway_months.toFixed(1) : '—'
  const rwyN = Number(runway)
  const rwyTone =
    data.income_runway_months == null ? '' : rwyN < 3 ? 'neg' : rwyN < 6 ? 'warn' : 'pos'
  const caveat = cardCaveat(data.cards_missing_spending)
  const cardsNote =
    caveat && `${caveat}. The runway and the projection read better than the book is.`
  const minBal = future.length > 0 ? Math.min(...future.map((m) => m.balance)) : 0
  const minMonth = future.find((m) => m.balance === minBal)
  const totalNet = future.reduce((s, m) => s + m.net, 0)
  const tightN = future.filter((m) => m.isTight || m.isNeg).length

  // Per-year net change (full year, independent of the active filter).
  const yearNet: Record<number, number> = {}
  months.forEach((m) => {
    yearNet[m.year] = (yearNet[m.year] ?? 0) + m.net
  })

  const FILTERS: [string, string][] = [
    ['all', 'All months'],
    ['events', 'Has events'],
    ['tight', 'Tight only'],
  ]
  const filterCounts: Record<string, number> = {
    all: months.length,
    events: months.filter((m) => m.events.length > 0).length,
    tight: months.filter((m) => m.isTight || m.isNeg).length,
  }

  // Collapse older actuals so the projection stays the focus.
  const earlierActuals = months.filter((m) => m.isHistory && !m.isCurrent)
  const collapsing = filter === 'all' && !showHist && earlierActuals.length > HIST_VISIBLE
  const hiddenCount = collapsing ? earlierActuals.length - HIST_VISIBLE : 0
  const hiddenSet = new Set(collapsing ? earlierActuals.slice(0, hiddenCount).map((m) => m.ym) : [])
  const canCollapse = filter === 'all' && earlierActuals.length > HIST_VISIBLE

  const visible = months.filter((m) => {
    if (filter === 'events') return m.events.length > 0
    if (filter === 'tight') return m.isTight || m.isNeg
    return !hiddenSet.has(m.ym)
  })

  const groups: Array<{ year: number; rows: MappedRow[] }> = []
  visible.forEach((m) => {
    let g = groups[groups.length - 1]
    if (!g || g.year !== m.year) {
      g = { year: m.year, rows: [] }
      groups.push(g)
    }
    g.rows.push(m)
  })

  function toggle(ym: string) {
    setExpanded((p) => {
      const n = new Set(p)
      n.has(ym) ? n.delete(ym) : n.add(ym)
      return n
    })
  }
  function toggleYear(y: number) {
    setCollapsedYears((p) => {
      const n = new Set(p)
      n.has(y) ? n.delete(y) : n.add(y)
      return n
    })
  }
  const allYears = [...new Set(months.map((m) => m.year))]
  const allCollapsed = allYears.length > 0 && allYears.every((y) => collapsedYears.has(y))
  function toggleAllYears() {
    setCollapsedYears(allCollapsed ? new Set() : new Set(allYears))
  }

  // Quick scenarios — anchor on the projection's OWN baseline (streams + variable residual),
  // not the retired `actual_*_avg` flat average that still folds in one-off windfalls. A
  // "Baseline" scenario anchored on the old number would silently be a what-if, not a baseline.
  const BASE_IN = Math.round(data.monthly_income_avg)
  const BASE_OUT = Math.round(data.monthly_expenses_avg)
  const LEAN = String(Math.round(BASE_OUT * 0.8))
  const SCENARIOS = [
    // Baseline = NO override at all (empty), not "override with the baseline number" — the
    // latter is indistinguishable from a real what-if to `buildUrl` and would keep the tab
    // permanently in scenario mode. The inputs show BASE_IN/BASE_OUT as placeholders.
    {
      key: 'baseline',
      label: 'Baseline',
      incomeVal: '',
      expenseVal: '',
      expGrowth: '3',
      incGrowth: '0',
    },
    // Each preset overrides ONLY what it actually changes; '' leaves that side on the live
    // baseline. Pinning the unchanged side to today's number (what these used to do) froze
    // the scenario against a snapshot — a "pay raise" projected from a stale income figure
    // rather than growth applied to the current baseline.
    {
      key: 'raise',
      label: 'Pay raise',
      incomeVal: '',
      expenseVal: '',
      expGrowth: '3',
      incGrowth: '6',
    },
    {
      key: 'lean',
      label: 'Lean spending',
      incomeVal: '',
      expenseVal: LEAN,
      expGrowth: '3',
      incGrowth: '0',
    },
    {
      key: 'inflation',
      label: 'High inflation',
      incomeVal: '',
      expenseVal: '',
      expGrowth: '8',
      incGrowth: '0',
    },
    {
      key: 'lose',
      label: 'Lose income',
      incomeVal: '0',
      expenseVal: '',
      expGrowth: '3',
      incGrowth: '0',
    },
  ]
  const activeScenario = SCENARIOS.find(
    (s) =>
      incomeVal === s.incomeVal &&
      expenseVal === s.expenseVal &&
      expGrowth === s.expGrowth &&
      incGrowth === s.incGrowth,
  )
  function applyScenario(s: (typeof SCENARIOS)[0]) {
    setIncomeVal(s.incomeVal)
    setExpenseVal(s.expenseVal)
    setExpGrowth(s.expGrowth)
    setIncGrowth(s.incGrowth)
    load(
      s.incomeVal,
      s.expenseVal,
      includeFixedDeposits,
      includeInvestments,
      s.expGrowth,
      s.incGrowth,
      includeCommitments,
    )
  }

  function exportCSV() {
    const head = [
      'Month',
      'Money In (SGD)',
      'Money Out (SGD)',
      'Net (SGD)',
      'Balance (SGD)',
      'Status',
    ]
    const status = (m: MappedRow) =>
      m.isCurrent ? 'Current' : m.isHistory ? 'Actual' : 'Projected'
    const rows = months.map((m) => [
      m.label,
      Math.round(m.total_in),
      Math.round(m.total_out),
      Math.round(m.net),
      m.balanceKnown ? Math.round(m.balance) : '',
      status(m),
    ])
    const esc = (c: unknown) => {
      const s = String(c)
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
    }
    const csv = [head, ...rows].map((r) => r.map(esc).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'cashflow-projection.csv'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    toast('Exported cashflow-projection.csv')
  }

  return (
    <div className="pl-sub cfx" style={{ gap: 0, marginTop: 16 }}>
      {/* ── Hero: current cash + balance trajectory ── */}
      <div className="cfx-hero">
        <div className="cfx-hero-top">
          <div className="cfx-hero-heads">
            <div className="cfx-hero-head">
              {/* Real spendable cash NOW (the same number runway divides) — NOT the current
                  month's projected end balance, which adds a full month of future net on top. */}
              <div className="cfx-eyebrow">Cash on hand • today</div>
              <div className="cfx-hero-bal mask">{fBase(data.liquid_balance_base)}</div>
              <div className="cfx-hero-sub">
                <span className="up">
                  +<span className="mask">{fCF(inc)}</span>/mo in
                </span>
                <span className="cfx-sub-sep">•</span>
                <span>
                  <span className="mask">−{fCF(exp)}</span>/mo out
                </span>
              </div>
            </div>
            <div className="cfx-hero-div" />
            <div className="cfx-hero-head">
              <div className="cfx-eyebrow">Financial runway</div>
              <div className={'cfx-hero-bal cfx-runway ' + rwyTone}>
                {runway}
                <span className="u">mo</span>
              </div>
              <div className="cfx-hero-sub">
                if income stopped, at <span className="mask">{fCF(exp)}</span>/mo burn
                {assetBoost > 0 && (
                  <span className="cfx-sub-note">
                    incl. <span className="mask">{fCF(assetBoost)}</span> positions
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="cfx-hero-actions">
            {simActive && <span className="cfx-sim">What-if active</span>}
            <div className="cfx-hero-btns">
              <Button
                variant="secondary"
                size="dense"
                onClick={() => setFlowMonth(thisMonth())}
                aria-label="Money flow"
                title="Money flow — where this month's income has gone so far"
              >
                <FlowIcon />
                {!isMobile && 'Money flow'}
              </Button>
              <Button
                variant={simActive ? 'primary' : 'secondary'}
                size="dense"
                onClick={() => setSettingsOpen(true)}
              >
                {simActive ? 'Adjust scenario' : 'What-if'}
              </Button>
            </div>
          </div>
        </div>

        {months.length >= 2 && (
          <BalanceChart
            months={months.filter((m) => m.balanceKnown)}
            minMonth={minMonth}
            minBal={minBal}
            floor={floor}
          />
        )}

        <div className="cfx-stats">
          <div className="cfx-stat">
            <span className="k">Avg net / mo</span>
            <span className={'v mask ' + (avgNet >= 0 ? 'pos' : 'neg')}>
              {avgNet >= 0 ? '+' : '−'}
              {fCF(Math.abs(avgNet))}
            </span>
          </div>
          <div className="cfx-stat">
            <span
              className="k"
              title="The projected months ahead on average: net change divided by projected income. The Dashboard shows last month's actual rate and the last 3 months' average, and Budget this month's pace, so they differ."
            >
              Savings rate
            </span>
            <span className={'v ' + (savingPct >= 0 ? 'pos' : 'neg')}>
              {savingPct > 0 ? '+' : ''}
              {savingPct}%
            </span>
          </div>
          <div className="cfx-stat">
            <span className="k">Net • {future.length} mo</span>
            <span className={'v mask ' + (totalNet >= 0 ? 'pos' : 'neg')}>
              {totalNet >= 0 ? '+' : ''}
              {fCF(totalNet)}
            </span>
          </div>
          <div className="cfx-stat">
            <span className="k">Projected balance</span>
            <span className="v mask">{fCF(endBal)}</span>
          </div>
        </div>
        {cardsNote && <div className="cfx-card-note">{cardsNote}</div>}
      </div>

      {/* ── Outlook + filter ── */}
      <div className="cfx-bar">
        {!isMobile && (
          <div className="cfx-outlook">
            <span>
              Lowest point <b className={'mask ' + (minBal < floor ? 'warn' : '')}>{fCF(minBal)}</b>
              {minMonth ? ` • ${minMonth.label}` : ''}
            </span>
            <span className="cfx-dot" />
            <span>
              Tight months <b className={tightN > 0 ? 'warn' : ''}>{tightN}</b>
            </span>
          </div>
        )}
        {isMobile ? (
          <div className="cfx-bar-r cfx-bar-r--mobile">
            <FilterSelect
              filters={FILTERS}
              counts={filterCounts}
              value={filter}
              onChange={(v) => setFilter((v ?? 'all') as 'all' | 'events' | 'tight')}
            />
            <ActionMenu
              label="Cash flow actions"
              items={[
                ...(canCollapse
                  ? [
                      {
                        label: showHist
                          ? 'Hide earlier months'
                          : `Show ${earlierActuals.length - HIST_VISIBLE} earlier months`,
                        onSelect: () => setShowHist((h) => !h),
                      },
                    ]
                  : []),
                {
                  label: allCollapsed ? 'Expand all' : 'Collapse all',
                  onSelect: toggleAllYears,
                },
                { label: 'Export CSV', onSelect: exportCSV },
                { label: 'Jump to today', onSelect: jumpToToday },
              ]}
            />
          </div>
        ) : (
          <div className="cfx-bar-r">
            <button className="cfx-barbtn" onClick={toggleAllYears}>
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {allCollapsed ? (
                  <polyline points="7 13 12 18 17 13" />
                ) : (
                  <polyline points="7 11 12 6 17 11" />
                )}
                {allCollapsed ? (
                  <polyline points="7 7 12 12 17 7" />
                ) : (
                  <polyline points="7 17 12 12 17 17" />
                )}
              </svg>
              {allCollapsed ? 'Expand all' : 'Collapse all'}
            </button>
            <button
              className="cfx-barbtn"
              onClick={exportCSV}
              title="Download the projection as CSV"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Export
            </button>
            <button
              className="cfx-barbtn"
              onClick={jumpToToday}
              title="Scroll to the current month"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="9" />
                <polyline points="12 7 12 12 15 14" />
              </svg>
              Today
            </button>
            <div className="cfx-seg">
              {FILTERS.map(([k, l]) => (
                <button
                  key={k}
                  className={filter === k ? 'on' : ''}
                  onClick={() => setFilter(k as 'all' | 'events' | 'tight')}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Ledger ── */}
      {isMobile ? (
        <div className="cfx-ledger cfx-ledger--mobile">
          {groups.map((g) => {
            const yc = collapsedYears.has(g.year)
            return (
              <div key={g.year} className="cfx-group">
                <div
                  className={'cfx-year-mobile' + (yc ? ' collapsed' : '')}
                  onClick={() => toggleYear(g.year)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      toggleYear(g.year)
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-expanded={!yc}
                >
                  <span className="cfx-year-y">
                    <svg
                      className="cfx-year-chev"
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                    {g.year}
                    <span className="cfx-year-count">{g.rows.length} mo</span>
                  </span>
                  <span className="cfx-year-figs">
                    <span
                      className={'cfx-year-net ' + ((yearNet[g.year] ?? 0) >= 0 ? 'pos' : 'neg')}
                    >
                      <span className="cfx-year-figlbl">Net</span>
                      {(yearNet[g.year] ?? 0) >= 0 ? '+' : '−'}
                      {fCF(Math.abs(yearNet[g.year] ?? 0))}
                    </span>
                    <span className="cfx-year-bal mask">
                      <span className="cfx-year-figlbl">Balance</span>
                      <RowBalance row={g.rows[g.rows.length - 1]!} />
                    </span>
                  </span>
                </div>
                {!yc && (
                  <div className="cfx-mlist">
                    {g.rows.map((m) => (
                      <LedgerRow
                        key={m.ym}
                        row={m}
                        expanded={expanded.has(m.ym)}
                        onToggle={() => toggle(m.ym)}
                        isMobile
                        onOpenFlow={setFlowMonth}
                      />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
          {visible.length === 0 && <div className="cfx-empty">No months match this filter.</div>}
        </div>
      ) : (
        <div className="cfx-ledger">
          <div className="cfx-thead-wrap">
            <div className="cfx-thead-clip">
              <div className="cfx-thead-inner" ref={cfxHeadRef}>
                <div className="cfx-colhead">
                  <span>Month</span>
                  <span className="r" title={GROSS_NOTE}>
                    In
                  </span>
                  <span className="r" title={GROSS_NOTE}>
                    Out
                  </span>
                  <span className="r">Net</span>
                  <span className="r">Balance</span>
                </div>
              </div>
            </div>
          </div>
          <div className="cfx-ledger-scroll" ref={cfxBodyRef} onScroll={syncCfxHead}>
            <div className="cfx-body-inner">
              {canCollapse && (
                <button
                  className={'cfx-histtoggle' + (showHist ? ' open' : '')}
                  onClick={() => setShowHist((h) => !h)}
                >
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="18 15 12 9 6 15" />
                  </svg>
                  {showHist
                    ? 'Hide earlier actuals'
                    : `Show ${earlierActuals.length - HIST_VISIBLE} earlier ${earlierActuals.length - HIST_VISIBLE === 1 ? 'month' : 'months'}`}
                </button>
              )}

              {groups.map((g) => {
                const yc = collapsedYears.has(g.year)
                return (
                  <div key={g.year} className="cfx-group">
                    <div
                      className={'cfx-year' + (yc ? ' collapsed' : '')}
                      onClick={() => toggleYear(g.year)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          toggleYear(g.year)
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      aria-expanded={!yc}
                    >
                      <span className="cfx-year-y">
                        <svg
                          className="cfx-year-chev"
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.6"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polyline points="6 9 12 15 18 9" />
                        </svg>
                        {g.year}
                        <span className="cfx-year-count">{g.rows.length} mo</span>
                      </span>
                      <span></span>
                      <span></span>
                      <span
                        className={'cfx-year-net ' + ((yearNet[g.year] ?? 0) >= 0 ? 'pos' : 'neg')}
                      >
                        {(yearNet[g.year] ?? 0) >= 0 ? '+' : '−'}
                        {fCF(Math.abs(yearNet[g.year] ?? 0))}
                      </span>
                      <span className="cfx-year-bal mask">
                        <RowBalance row={g.rows[g.rows.length - 1]!} />
                      </span>
                    </div>
                    {!yc &&
                      g.rows.map((m) => (
                        <LedgerRow
                          key={m.ym}
                          row={m}
                          expanded={expanded.has(m.ym)}
                          onToggle={() => toggle(m.ym)}
                          onOpenFlow={setFlowMonth}
                        />
                      ))}
                  </div>
                )
              })}
              {visible.length === 0 && (
                <div className="cfx-empty">No months match this filter.</div>
              )}
            </div>
          </div>
        </div>
      )}

      <p className="pl-foot" style={{ marginTop: 14 }}>
        Tap any month with events for its breakdown. Actuals are settled months — real cash may
        differ from prior balance + net due to transfers and investments.
      </p>
      <p className="pl-foot">{GROSS_NOTE}</p>

      {flowMonth && (
        <MoneyFlowModal
          initialMonth={flowMonth}
          onClose={() => setFlowMonth(null)}
          onOpenRegister={props.onOpenRegister}
        />
      )}

      {/* ── Settings modal ── */}
      {settingsOpen && (
        <PlanningModal
          onClose={() => setSettingsOpen(false)}
          label="Projection settings"
          className="cfx-modal"
          style={{
            width: 540,
            maxHeight: 'calc(100vh - 48px)',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div className="pl-modal-head">
            <h3>Projection settings</h3>
            <button
              className="pl-modal-x"
              onClick={() => setSettingsOpen(false)}
              aria-label="Close"
            >
              <X />
            </button>
          </div>
          <div
            className="pl-modal-body"
            style={{ overflowY: 'auto', flex: '1 1 auto', minHeight: 0 }}
          >
            <div className="cfx-mgroup" style={{ marginTop: 0 }}>
              <div className="cfx-mgroup-h">Quick scenarios</div>
              <div className="cfx-mdesc">
                Apply a preset, then fine-tune below. Each sets income, expenses and growth.
              </div>
              <div className="cfx-scenarios">
                {SCENARIOS.map((s) => (
                  <button
                    key={s.key}
                    className={
                      'cfx-scenario' + (activeScenario && activeScenario.key === s.key ? ' on' : '')
                    }
                    onClick={() => applyScenario(s)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="cfx-mgroup">
              <div className="cfx-mgroup-h">Monthly cash flow</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 16px' }}>
                <div>
                  <label className="pl-modal-label" htmlFor="cf-inc">
                    Monthly income
                  </label>
                  <input
                    id="cf-inc"
                    className="pl-input"
                    type="number"
                    min={0}
                    step={100}
                    value={incomeVal}
                    placeholder={String(BASE_IN)}
                    onChange={(e) => {
                      setIncomeVal(e.target.value)
                      debouncedLoad(
                        e.target.value,
                        expenseVal,
                        includeFixedDeposits,
                        includeInvestments,
                        expGrowth,
                        incGrowth,
                        includeCommitments,
                      )
                    }}
                  />
                </div>
                <div>
                  <label className="pl-modal-label" htmlFor="cf-exp">
                    Monthly expenses
                  </label>
                  <input
                    id="cf-exp"
                    className="pl-input"
                    type="number"
                    min={0}
                    step={100}
                    value={expenseVal}
                    placeholder={String(BASE_OUT)}
                    onChange={(e) => {
                      setExpenseVal(e.target.value)
                      debouncedLoad(
                        incomeVal,
                        e.target.value,
                        includeFixedDeposits,
                        includeInvestments,
                        expGrowth,
                        incGrowth,
                        includeCommitments,
                      )
                    }}
                  />
                </div>
                <div>
                  <label className="pl-modal-label" htmlFor="cf-incg">
                    Income growth %
                  </label>
                  <input
                    id="cf-incg"
                    className="pl-input"
                    type="number"
                    min={0}
                    max={20}
                    step={0.5}
                    value={incGrowth}
                    onChange={(e) => {
                      setIncGrowth(e.target.value)
                      debouncedLoad(
                        incomeVal,
                        expenseVal,
                        includeFixedDeposits,
                        includeInvestments,
                        expGrowth,
                        e.target.value,
                        includeCommitments,
                      )
                    }}
                  />
                </div>
                <div>
                  <label className="pl-modal-label" htmlFor="cf-expg">
                    Expense growth %
                  </label>
                  <input
                    id="cf-expg"
                    className="pl-input"
                    type="number"
                    min={0}
                    max={20}
                    step={0.5}
                    value={expGrowth}
                    onChange={(e) => {
                      setExpGrowth(e.target.value)
                      debouncedLoad(
                        incomeVal,
                        expenseVal,
                        includeFixedDeposits,
                        includeInvestments,
                        e.target.value,
                        incGrowth,
                        includeCommitments,
                      )
                    }}
                  />
                </div>
              </div>
              <div className="cfx-mnote">
                Growth compounds monthly — income growth models raises, expense growth models
                inflation.
              </div>
            </div>

            <div className="cfx-mgroup">
              <div className="cfx-mgroup-h">Runway &amp; safety</div>

              <div className="cfx-mtitle">Asset scope</div>
              <div className="cfx-mdesc">
                Cash on hand is always included. Add what you&apos;d sell or break to survive
                longer.
              </div>
              <div className="cfx-assets">
                <button
                  className={'cfx-asset' + (includeFixedDeposits ? ' on' : '')}
                  onClick={toggleFixedDeposits}
                >
                  <span className="cfx-check">
                    <svg
                      width="11"
                      height="11"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </span>
                  <span className="cfx-asset-label">Fixed deposits</span>
                  <span className="cfx-asset-val mask">{fBase(data.fixed_deposit_base)}</span>
                </button>
                <button
                  className={'cfx-asset' + (includeInvestments ? ' on' : '')}
                  onClick={toggleInvestments}
                >
                  <span className="cfx-check">
                    <svg
                      width="11"
                      height="11"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </span>
                  <span className="cfx-asset-label">Investments (net of settlement cash)</span>
                  <span className="cfx-asset-val mask">{fBase(data.investments_base)}</span>
                </button>
              </div>

              <div className="cfx-mtitle" style={{ marginTop: 18 }}>
                Safety floor
              </div>
              <div className="cfx-mdesc">
                The minimum cash cushion to keep. Months below it count as tight; the balance curve
                turns amber under this line (red below zero).
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 16px' }}>
                <div>
                  <label className="pl-modal-label" htmlFor="cf-floor">
                    Floor amount ({getCurrencySymbol()})
                  </label>
                  <input
                    id="cf-floor"
                    className="pl-input"
                    type="number"
                    min={0}
                    step={5000}
                    value={floorVal}
                    placeholder={String(Math.round(data.low_balance_threshold))}
                    onChange={(e) => setFloorVal(e.target.value)}
                    onBlur={() => {
                      void saveFloorToProfile(floorVal)
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="cfx-mgroup">
              <div className="cfx-mgroup-h">Commitments</div>
              <div className="cfx-mdesc">
                Overlay committed installments and funding draws. Turn off to see the plan without
                them.
              </div>
              <button
                className="cfx-toggle-row"
                onClick={() => {
                  const next = !includeCommitments
                  setIncludeCommitments(next)
                  load(
                    incomeVal,
                    expenseVal,
                    includeFixedDeposits,
                    includeInvestments,
                    expGrowth,
                    incGrowth,
                    next,
                  )
                }}
                aria-pressed={includeCommitments}
              >
                <span className="lbl">Include commitment payments &amp; funding</span>
                <span className={'pl-tgt-switch' + (includeCommitments ? ' on' : '')}>
                  <span />
                </span>
              </button>
            </div>
          </div>
          <div className="pl-modal-foot">
            <Button variant="secondary" size="dense" onClick={resetSettings}>
              Reset all
            </Button>
            <Button
              variant="primary"
              size="dense"
              onClick={() => {
                void saveFloorToProfile(floorVal)
                setSettingsOpen(false)
                toast('Projection updated')
              }}
            >
              Apply
            </Button>
          </div>
        </PlanningModal>
      )}
    </div>
  )
}
