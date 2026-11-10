// Overview v2 — Cash-flow + Planning widget group: spending pulse, cash flow,
// calendar, goals, crossover point, FIRE projection, cash-flow runway. Ported
// from the handoff's `overview_v2b/widgets2.jsx` (Spending/Cashflow/Goals/
// CalendarCard/Crossover) and the Planning-shape widgets in `insights.jsx`
// (FireProjection/CashRunway, renamed here plan-fire/plan-runway per the
// registry).
//
// Data: `GET /api/overview/budget` (envelopes, goals, the day-by-day calendar, the month's
// cash flow) and `GET /api/overview/projections` (passive-income coverage, the cash-runway
// depletion curve), plus `/api/fire` for the FIRE year. Every money figure arrives already
// summed, differenced and divided — this file does no arithmetic on money
// (ADR 2026-07-31-overview-aggregates-are-server-computed). It previously folded raw
// transactions into calendar days, divided an annual forecast by 12, and built the runway
// curve point by point.
//
// Every widget renders through WidgetShell (title/base/meta/actions/footer)
// so rename/accent/kebab chrome behaves identically to every other widget.

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentType, ReactNode, RefObject, SyntheticEvent } from 'react'
import { createPortal } from 'react-dom'
import { fBase, fBaseCompact, fDate, fMonth } from '../../../../utils/format'
import { Badge, MaskedText, Money, Skeleton, Text } from '../../../ui'
import type { WidgetContentProps } from '../types'
import { WidgetShell } from '../WidgetShell'
import { rowCap } from '../widgetDisplayShared'
import { useAnalytics } from '../useWidgetData'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import { OvChart, TICK, GRID, amountsHidden, areaFill, kTick, resolveColor } from '../charts'
import { useFire } from '../../../../hooks/useFire'
import { usePlanningSummary, useCashflowProjections } from '../../../../hooks/serverState'
import { cardCaveat } from '../../../../utils/cardCaveat'
import type { PlanningSummary } from '../../../../types/aggregates'

// ── shared: small icons + a footer "All X →" link ───────────────────────────

function ArrowIcon() {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  )
}

function ChevronIcon({ dir }: { dir: -1 | 1 }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={dir < 0 ? 'M15 18l-6-6 6-6' : 'M9 6l6 6-6 6'} />
    </svg>
  )
}

function FooterLink({
  label,
  tab,
  onNavigate,
}: {
  label: string
  tab?: string
  onNavigate?: (tab: string) => void
}) {
  if (!onNavigate || !tab) return null
  return (
    <button type="button" className="ov-panel-link" onClick={() => onNavigate(tab)}>
      {label}
      <ArrowIcon />
    </button>
  )
}

// ── Spending pulse (budget envelopes, month to date) ────────────────────────
// GET /api/planning/month — one row per non-archived envelope with assigned
// (this month's budget)/activity (negative = spent)/available.

// Envelope rows, the month totals, the pace/percent-of-budget figures and each bar's fill
// all come from `GET /api/overview/budget` — see
// ADR 2026-07-31-overview-aggregates-are-server-computed.

const Spending: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  const budget = usePlanningSummary()
  const data = budget.data ?? null
  const loading = budget.initialLoading
  const error = !!budget.error && data == null
  const footer = <FooterLink label="All envelopes" tab={def.source?.tab} onNavigate={onNavigate} />

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <Skeleton height={44} />
      </WidgetShell>
    )
  }
  if (error || !data) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Couldn't load this month's budget — try reloading." />
      </WidgetShell>
    )
  }

  const rows = data.envelopes.rows
  const totalSpent = data.envelopes.total_spent
  const budgetedSpent = data.envelopes.budgeted_spent ?? 0
  const budgetedTotal = data.envelopes.budgeted_total ?? 0
  const pace = Math.round(data.pace_pct)
  const pctOfBudget = Math.round(data.envelopes.pct_of_budget)
  // Budgeted spend against budgets — like for like. All spend over the budgeted total read
  // "369% of budget" when the only budgets were savings envelopes.
  const budgetLine =
    budgetedTotal > 0
      ? `${fBase(budgetedSpent)} of ${fBase(budgetedTotal)} budgeted (${pctOfBudget}%)`
      : 'No budgets set'

  const cap = rowCap(instance, def) ?? undefined
  const visible = cap ? rows.slice(0, cap) : rows
  const extra = rows.length - visible.length

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      meta={
        <>
          <Money as="span">{fBase(totalSpent)}</Money> spent
        </>
      }
      subtitle={
        <>
          Month to date • <MaskedText>{budgetLine}</MaskedText> • {pace}% through the month
        </>
      }
      footer={footer}
    >
      {visible.length ? (
        <>
          {visible.map((r) => {
            const budgeted = r.assigned > 0
            return (
              <div className="ov-env-row" key={r.key}>
                <span className="ov-env-nm">{r.label}</span>
                <span className="ov-env-bar">
                  {budgeted && (
                    <span
                      className="ov-env-fill"
                      style={{
                        width: `${r.fill_pct}%`,
                        background: r.over ? 'var(--bad)' : 'var(--accent)',
                      }}
                    />
                  )}
                </span>
                <span className="ov-env-amt">
                  <Money as="span">{fBase(r.spent)}</Money>{' '}
                  {budgeted ? (
                    <span>
                      / <Money as="span">{fBase(r.assigned)}</Money>
                    </span>
                  ) : (
                    <span>· no budget</span>
                  )}
                </span>
              </div>
            )
          })}
          {extra > 0 && (
            <div className="ov-more">
              +{extra} more envelope{extra === 1 ? '' : 's'}
            </div>
          )}
        </>
      ) : (
        <WidgetPlaceholder detail="No budget activity yet this month." />
      )}
    </WidgetShell>
  )
}

// ── Cash flow this month (money in vs out, P&L) ─────────────────────────────
// GET /api/ledger/summary — income in / expense out for the current month,
// recomputed into the current base currency (transfers/adjustments excluded).

const Cashflow: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  const budget = usePlanningSummary()
  const data = budget.data?.cashflow ?? null
  const loading = budget.initialLoading
  const error = !!budget.error && data == null

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <Skeleton height={64} />
      </WidgetShell>
    )
  }
  if (error || !data) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Couldn't load this month's cash flow." />
      </WidgetShell>
    )
  }

  // A month with no money yet shows the newest one that has some, and says so — "0 in, 0 out"
  // beside a full September read as a broken import (persona QA round 7).
  const fellBack = data.in_count + data.out_count === 0 ? budget.data?.cashflow_latest : null
  const shown = fellBack ?? data
  const max = shown.max
  const inCount = shown.in_count
  const outCount = shown.out_count

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      // The figure comes FIRST, "net" label after — `.ov-t-meta` ellipsizes
      // from the END when space runs out, so this ordering guarantees the
      // actual number survives truncation instead of the filler word.
      meta={
        <>
          <Text tone={shown.net >= 0 ? 'good' : 'bad'} weight={700}>
            {shown.net >= 0 ? '+' : '−'}
            <Money as="span">{fBaseCompact(Math.abs(shown.net))}</Money>
          </Text>{' '}
          net
        </>
      }
    >
      {fellBack && (
        <Text as="p" size="xs" tone="muted" className="ov-rep-note">
          {`${fMonth(fellBack.month, 'long')}. Nothing yet in ${fMonth(budget.data!.month, 'long')}.`}
        </Text>
      )}
      <div className="ov-cf-row ov-responsive-metric-row">
        <span className="ov-cf-lbl">In</span>
        <span className="ov-cf-bar ov-responsive-metric-bar">
          <span
            className="ov-cf-fill"
            style={{ width: `${(shown.total_in / max) * 100}%`, background: 'var(--good)' }}
          />
        </span>
        <span className="ov-cf-amt ov-responsive-metric-amount">
          <Money as="span">{fBaseCompact(shown.total_in)}</Money>
        </span>
      </div>
      <div className="ov-cf-sub ov-responsive-metric-detail">
        {inCount} income transaction{inCount === 1 ? '' : 's'}
      </div>
      <div className="ov-cf-row ov-responsive-metric-row">
        <span className="ov-cf-lbl">Out</span>
        <span className="ov-cf-bar ov-responsive-metric-bar">
          <span
            className="ov-cf-fill"
            style={{ width: `${(shown.total_out / max) * 100}%`, background: 'var(--bad)' }}
          />
        </span>
        <span className="ov-cf-amt ov-responsive-metric-amount">
          <Money as="span">{fBaseCompact(shown.total_out)}</Money>
        </span>
      </div>
      <div className="ov-cf-sub ov-responsive-metric-detail">
        {outCount} expense transaction{outCount === 1 ? '' : 's'}
      </div>
    </WidgetShell>
  )
}

// ── Calendar card (money in/out by day this month) ──────────────────────────
// GET /api/transactions?date_from=&date_to= — the register's own filtered
// list, bucketed client-side by day (no day-granular endpoint exists).

interface CalendarDayModel {
  day: number
  date: Date
  inAmt: number
  outAmt: number
  /** Server-computed in − out for the day. */
  net: number
  isToday: boolean
}

interface CalendarModel {
  label: string
  isCurrent: boolean
  startDow: number
  days: CalendarDayModel[]
  totalIn: number
  totalOut: number
}

const DOW = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

function ymOffset(offset: number): string {
  const first = new Date()
  first.setDate(1)
  first.setMonth(first.getMonth() + offset)
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}`
}

/** Lay the server's per-day in/out figures onto a calendar grid. No money is derived here —
 *  the amounts, the month totals and the weekday offset all arrive computed. */
function buildCalendarModel(
  ym: string,
  calendar: PlanningSummary['calendar'] | null,
): CalendarModel {
  const [y, m1] = ym.split('-').map(Number) as [number, number]
  const m = m1 - 1
  const today = new Date()
  const isCurrent = today.getFullYear() === y && today.getMonth() === m
  return {
    label: fMonth(new Date(y, m, 1)),
    isCurrent,
    startDow: calendar?.first_weekday ?? new Date(y, m, 1).getDay(),
    days: (calendar?.days ?? []).map((d) => ({
      day: d.day,
      date: new Date(y, m, d.day),
      inAmt: d.in,
      outAmt: d.out,
      net: d.net,
      isToday: isCurrent && d.day === today.getDate(),
    })),
    totalIn: calendar?.total_in ?? 0,
    totalOut: calendar?.total_out ?? 0,
  }
}

interface CalendarHover {
  d: CalendarDayModel
  left: number
  top: number
  above: boolean
}

// The popover's footprint, used to pick a side and keep it on screen (viewport px).
const CAL_POP_HALF_W = 100
const CAL_POP_H = 180

function CalendarPopover({
  hover,
  pinned,
  onOpenRegister,
  popRef,
  onPointerStay,
  onPointerLeave,
}: {
  hover: CalendarHover
  /** True for a tap-pinned popover (dismissed by the scrim) — false for the mouse-hover one,
   *  which stays open while the pointer is over it so "Open in register" is reachable. */
  pinned?: boolean
  onOpenRegister?: () => void
  popRef?: RefObject<HTMLDivElement | null>
  onPointerStay?: () => void
  onPointerLeave?: () => void
}) {
  const net = hover.d.net
  const hasActivity = hover.d.inAmt > 0 || hover.d.outAmt > 0
  // A body portal at fixed viewport coordinates: the widget body clips to its own scroll box
  // (overview.css), and a popover above a top-row day was losing its header to that clip.
  return createPortal(
    <div
      ref={popRef}
      className={`ov-cal-pop${hover.above ? ' ov-cal-pop--above' : ''}`}
      data-pinned={pinned || undefined}
      style={{ left: hover.left, top: hover.top }}
      onMouseEnter={onPointerStay}
      onMouseLeave={onPointerLeave}
    >
      <div className="ov-cal-pop-d">
        {hover.d.date.toLocaleDateString(undefined, { weekday: 'short' })}, {fDate(hover.d.date)}
      </div>
      {hover.d.inAmt > 0 && (
        <div className="ov-cal-pop-row">
          <span className="ov-cal-pop-dot ov-cal-pop-dot--in" />
          Money in
          <b>
            +<Money as="span">{fBase(hover.d.inAmt)}</Money>
          </b>
        </div>
      )}
      {hover.d.outAmt > 0 && (
        <div className="ov-cal-pop-row">
          <span className="ov-cal-pop-dot ov-cal-pop-dot--out" />
          Money out
          <b>
            −<Money as="span">{fBase(hover.d.outAmt)}</Money>
          </b>
        </div>
      )}
      {hasActivity ? (
        <div className="ov-cal-pop-net">
          Net
          <b>
            {net >= 0 ? '+' : '−'}
            <Money as="span">{fBase(Math.abs(net))}</Money>
          </b>
        </div>
      ) : (
        pinned && (
          <Text as="p" size="sm" tone="faint">
            No transactions.
          </Text>
        )
      )}
      {onOpenRegister && (
        <button type="button" className="ov-panel-link" onClick={onOpenRegister}>
          Open in register
        </button>
      )}
    </div>,
    document.body,
  )
}

const Calendar: ComponentType<WidgetContentProps> = ({ instance, def, onOpenRegister }) => {
  const [offset, setOffset] = useState(0)
  const [hover, setHover] = useState<CalendarHover | null>(null)
  // Tap/click-pinned popover — separate from `hover` so a tap survives the day button losing
  // focus (a mouse-only hover would otherwise be dismissed by its own onBlur before the
  // popover's "Open in register" button's click ever fires). Dismissed by re-tapping the same
  // day, tapping another day, or tapping the scrim outside it — never by blur.
  const [pinned, setPinned] = useState<CalendarHover | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const leaveTimer = useRef<number | null>(null)
  const ym = ymOffset(offset)
  const budget = usePlanningSummary(ym)
  const loading = budget.initialLoading
  const error = !!budget.error && budget.data == null
  const calendar = budget.data?.calendar ?? null
  const model = useMemo(() => buildCalendarModel(ym, calendar), [ym, calendar])

  // The popover is a fixed-position body portal, so it is placed in VIEWPORT coordinates and
  // sits flush against the day (no gap): the pointer must be able to travel from the cell
  // straight into it without crossing the row above, whose own mouseenter would swap or
  // clear the hover.
  const cellPos = (e: SyntheticEvent<HTMLButtonElement>, d: CalendarDayModel): CalendarHover => {
    const cell = e.currentTarget.getBoundingClientRect()
    const left = Math.max(
      CAL_POP_HALF_W + 8,
      Math.min(cell.left + cell.width / 2, window.innerWidth - CAL_POP_HALF_W - 8),
    )
    const above = cell.top > CAL_POP_H + 8
    return { d, left, top: above ? cell.top : cell.bottom, above }
  }

  const cancelLeave = () => {
    if (leaveTimer.current != null) {
      window.clearTimeout(leaveTimer.current)
      leaveTimer.current = null
    }
  }
  // Leaving the grid schedules the dismissal instead of firing it: the popover is not a DOM
  // descendant of the grid (it is portalled), so moving the mouse INTO it leaves the grid.
  // Its own mouseenter cancels the timer.
  const scheduleLeave = () => {
    cancelLeave()
    leaveTimer.current = window.setTimeout(() => setHover(null), 150)
  }
  useEffect(() => cancelLeave, [])

  // Fixed coordinates go stale the moment anything scrolls or resizes — dismiss rather than
  // drift.
  useEffect(() => {
    if (!hover && !pinned) return
    const dismiss = () => {
      setHover(null)
      setPinned(null)
    }
    window.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      window.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [hover, pinned])

  const onEnter = (e: SyntheticEvent<HTMLButtonElement>, d: CalendarDayModel) => {
    cancelLeave()
    if (!d.inAmt && !d.outAmt) {
      setHover(null)
      return
    }
    setHover(cellPos(e, d))
  }

  const openRegisterFor = (h: CalendarHover) =>
    onOpenRegister &&
    (() => {
      onOpenRegister({ dateOn: `${ym}-${String(h.d.day).padStart(2, '0')}` })
      setPinned(null)
      setHover(null)
    })

  const onTap = (e: SyntheticEvent<HTMLButtonElement>, d: CalendarDayModel) => {
    // `cellPos` reads `e.currentTarget` — must resolve it HERE, synchronously, while the
    // synthetic event is still valid. A functional setState updater is not guaranteed to run
    // within that window (e.g. under StrictMode's double-invocation), so passing `e` into one
    // and calling `cellPos` from inside it crashes on the second, deferred call.
    const pos = cellPos(e, d)
    setPinned((p) => (p && p.d === d ? null : pos))
  }

  const nav = (
    <div className="ov-cal-navwrap">
      <button
        type="button"
        className="ov-cal-nav"
        aria-label="Previous month"
        onClick={() => {
          setHover(null)
          setPinned(null)
          setOffset((o) => o - 1)
        }}
      >
        <ChevronIcon dir={-1} />
      </button>
      <button
        type="button"
        className="ov-cal-month"
        title="Back to this month"
        onClick={() => {
          setHover(null)
          setPinned(null)
          setOffset(0)
        }}
      >
        {model.label}
        {!model.isCurrent && <span className="ov-cal-dotmark" aria-hidden="true" />}
      </button>
      <button
        type="button"
        className="ov-cal-nav"
        aria-label="Next month"
        onClick={() => {
          setHover(null)
          setPinned(null)
          setOffset((o) => o + 1)
        }}
      >
        <ChevronIcon dir={1} />
      </button>
    </div>
  )

  const meta =
    model.totalIn || model.totalOut ? (
      <>
        <Text tone="good" weight={600}>
          +<Money as="span">{fBaseCompact(model.totalIn)}</Money>
        </Text>{' '}
        <Text tone="bad" weight={600}>
          −<Money as="span">{fBaseCompact(model.totalOut)}</Money>
        </Text>
      </>
    ) : undefined

  return (
    <WidgetShell title={instance.title} base={def.title} meta={meta} subhead={nav}>
      {loading ? (
        <Skeleton height={180} />
      ) : error ? (
        <WidgetPlaceholder detail="Couldn't load this month's transactions." />
      ) : (
        <div className="ov-cal-wrap" ref={wrapRef} onMouseLeave={scheduleLeave}>
          <div className="ov-cal">
            {DOW.map((d, i) => (
              <span key={`h${i}`} className="ov-cal-dow">
                {d}
              </span>
            ))}
            {Array.from({ length: model.startDow }).map((_, i) => (
              <span key={`b${i}`} className="ov-cal-pad" />
            ))}
            {model.days.map((d) => {
              const active = d.inAmt > 0 || d.outAmt > 0
              const isHot = hover?.d === d || pinned?.d === d
              const cls =
                'ov-cal-cell' +
                (d.isToday ? ' ov-cal-cell--today' : '') +
                (active ? ' ov-cal-cell--has' : '') +
                (isHot ? ' ov-cal-cell--hot' : '')
              return (
                <button
                  key={d.day}
                  type="button"
                  className={cls}
                  onMouseEnter={(e) => onEnter(e, d)}
                  onFocus={(e) => onEnter(e, d)}
                  onBlur={(e) => {
                    // Focus moving INTO the popover (its "Open in register" button) must not
                    // dismiss it — that blur fires before the button's own click ever can.
                    if (!popRef.current?.contains(e.relatedTarget as Node | null)) setHover(null)
                  }}
                  onClick={(e) => onTap(e, d)}
                >
                  <span className="ov-cal-day">{d.day}</span>
                  <span className="ov-cal-dots">
                    {d.inAmt > 0 && <i className="ov-cal-dot ov-cal-dot--in" />}
                    {d.outAmt > 0 && <i className="ov-cal-dot ov-cal-dot--out" />}
                  </span>
                </button>
              )
            })}
          </div>
          {pinned &&
            createPortal(
              <span
                className="info-pop-scrim ov-cal-scrim"
                onPointerDown={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  setPinned(null)
                }}
              />,
              document.body,
            )}
          {/* ONE popover for hover and pin: a click on the hovered day used to swap the hover
              popover for a separate pinned one, remounting it — it vanished and reappeared. */}
          {(pinned ?? hover) && (
            <CalendarPopover
              hover={(pinned ?? hover)!}
              pinned={!!pinned}
              popRef={pinned ? undefined : popRef}
              onPointerStay={pinned ? undefined : cancelLeave}
              onPointerLeave={pinned ? undefined : () => setHover(null)}
              onOpenRegister={openRegisterFor((pinned ?? hover)!)}
            />
          )}
        </div>
      )}
    </WidgetShell>
  )
}

// ── Goals (the user's savings goals + dated envelope targets) ───────────────
// Goals ("save X by date Y", services/targets) hydrated into base currency, plus any
// `by_date` envelope target (needs_whole_amount picks contributions vs balance).

const Goals: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  // Progress per dated target — including the `needs_whole_amount` flavor rule that picks
  // contributions-to-date vs balance, and the completion percentage — is server-computed.
  const budget = usePlanningSummary()
  const data = budget.data ?? null
  const loading = budget.initialLoading
  const error = !!budget.error && data == null
  const footer = <FooterLink label="All goals" tab={def.source?.tab} onNavigate={onNavigate} />

  if (loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} footer={footer}>
        <Skeleton height={64} />
      </WidgetShell>
    )
  }
  if (error || !data) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Couldn't load your Plan goals — try reloading." />
      </WidgetShell>
    )
  }

  const rows = data.goals
  const cap = rowCap(instance, def) ?? undefined
  const visible = cap ? rows.slice(0, cap) : rows
  const extra = rows.length - visible.length

  return (
    <WidgetShell title={instance.title} base={def.title} footer={footer}>
      {rows.length ? (
        <>
          {visible.map((g) => {
            const pct = Math.round(g.pct)
            return (
              <div className="ov-goal" key={g.key}>
                <div className="ov-goal-top">
                  <span className="ov-goal-nm">
                    {g.label}{' '}
                    {g.risk && (
                      <Badge tone="warn">{g.status === 'past_due' ? 'past due' : 'behind'}</Badge>
                    )}
                  </span>
                  <span className="ov-goal-pct money">{pct}%</span>
                </div>
                <div className="ov-env-bar ov-goal-bar">
                  <span
                    className="ov-env-fill"
                    style={{ width: `${pct}%`, background: g.risk ? 'var(--warn)' : 'var(--good)' }}
                  />
                </div>
                <div className="ov-goal-sub">
                  <Money as="span">{fBase(g.have)}</Money> of{' '}
                  <Money as="span">{fBase(g.target)}</Money>
                  {g.by ? ` • by ${fMonth(g.by, 'short', 'always')}` : ''}
                </div>
              </div>
            )
          })}
          {extra > 0 && (
            <div className="ov-more">
              +{extra} more goal{extra === 1 ? '' : 's'}
            </div>
          )}
        </>
      ) : (
        <WidgetPlaceholder detail={<>No savings goals yet — add one in Plan.</>} />
      )}
    </WidgetShell>
  )
}

// ── Crossover point (passive income vs spending) ────────────────────────────
// Today's real passive income (GET /api/income/forecast — dividends/interest,
// annualized) vs the recurring baseline spending (GET /api/cashflow — the average monthly
// expense the cash-flow projection runs on, not a measured average); the projected
// crossover YEAR reuses the FIRE engine (GET /api/fire) — reaching the FIRE
// number IS, by construction, the year a safe withdrawal can fully cover
// annual expenses, which is the same "crossover" this widget projects.

const Crossover: ComponentType<WidgetContentProps> = ({ instance, def, onNavigate }) => {
  // FIRE seeds from the ONE net worth — see OverviewDataContext and ADR
  // 2026-07-31-single-source-net-worth. The passive-vs-spending figures (the monthly passive
  // income, the coverage percentage and the shortfall) are the SERVER's: the widget used to
  // divide the annual forecast by 12 and take a percentage of average expenses itself.
  const { netWorth, loading: portfolioLoading } = useAnalytics()
  const fire = useFire(netWorth ?? 0)
  const projections = useCashflowProjections()
  const crossover = projections.data?.crossover
  const footer = <FooterLink label="Open Plan" tab={def.source?.tab} onNavigate={onNavigate} />

  if ((portfolioLoading && netWorth == null) || fire.loading || projections.initialLoading) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <Skeleton height={100} />
      </WidgetShell>
    )
  }
  if (projections.error || !crossover?.known) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Needs at least one month of spending history to project a crossover." />
      </WidgetShell>
    )
  }

  const expensesMonthly = crossover.expenses_monthly
  const passiveMonthly = crossover.passive_monthly
  const pct = Math.round(crossover.coverage_pct)
  const toGo = crossover.to_go_monthly
  let yearLabel = '—'
  if (fire.data) {
    if (fire.data.progress_pct >= 100) yearLabel = 'Now'
    else if (fire.data.years_to_fire != null)
      yearLabel = String(new Date().getFullYear() + Math.round(fire.data.years_to_fire))
  }

  return (
    <WidgetShell title={instance.title} base={def.title} bodyAlign="center" footer={footer}>
      <div className="ov-cross">
        <div className="ov-cross-hd">
          <span className="ov-cross-yr">{yearLabel}</span>
          <span className="ov-cross-note">projected</span>
        </div>
        <div className="ov-env-bar ov-goal-bar">
          <span className="ov-env-fill" style={{ width: `${pct}%`, background: 'var(--good)' }} />
        </div>
        <div className="ov-cross-stats">
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Passive income now</span>
            <b className="ov-cross-val">
              <Money as="span">{fBaseCompact(passiveMonthly)}</Money>/mo
            </b>
          </div>
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Baseline spending</span>
            <b className="ov-cross-val">
              <Money as="span">{fBaseCompact(expensesMonthly)}</Money>/mo
            </b>
          </div>
          <div className="ov-cross-stat">
            <span className="ov-cross-lbl">Covered</span>
            <b className="ov-cross-val">{pct}%</b>
          </div>
        </div>
        {toGo > 0 && (
          <div className="ov-cross-cap">
            <Money as="span">{fBaseCompact(toGo)}</Money>/mo to go
          </div>
        )}
      </div>
    </WidgetShell>
  )
}

// ── FIRE projection + cash-flow runway (compact Outlook mirrors) ────────────
// Both reuse the shared insights-style layout (a headline row of two stats +
// a compact OvChart) rather than the full Plan/Outlook chart, per the ADR's
// "a simplified single-figure/mini-chart view is fine" allowance.

function InsHeadStat({
  value,
  label,
  color,
}: {
  value: ReactNode
  label: ReactNode
  color?: string
}) {
  return (
    <div>
      <div className="ov-ins-big" style={color ? { color } : undefined}>
        {value}
      </div>
      <span className="ov-t-meta">{label}</span>
    </div>
  )
}

const PlanFire: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  // FIRE seeds from the ONE net worth (GET /api/net-worth), not a client-side portfolio sum —
  // see OverviewDataContext and ADR 2026-07-31-single-source-net-worth.
  const { netWorth, loading: portfolioLoading } = useAnalytics()
  const total = netWorth ?? 0
  const { data, loading, error } = useFire(total)

  if ((portfolioLoading && netWorth == null) || loading) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-ins-body">
        <Skeleton height={140} />
      </WidgetShell>
    )
  }
  if (error || !data || data.monthly_retirement_spending <= 0) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder
          detail={
            total <= 0
              ? 'Net worth is still loading.'
              : 'Set your monthly retirement spending in Plan to see a projection.'
          }
        />
      </WidgetShell>
    )
  }

  const fiLabel =
    data.projected_fire_age != null ? `FI ~${Math.round(data.projected_fire_age)}` : 'FI —'
  // MATH-28: current_age (and every trajectory point's age) is null without a date of
  // birth on file — the chart plots against age, so swap it for a one-line hint instead
  // of an axis of "null" labels; the years-based header stats above are unaffected.
  const ageKnown = data.current_age != null
  const ACC = resolveColor('var(--accent)')
  const WARN = resolveColor('var(--warn)')
  const cfg = {
    type: 'line' as const,
    data: {
      labels: data.trajectory.map((p) => String(p.age ?? p.year)),
      datasets: [
        {
          label: 'Net worth',
          data: data.trajectory.map((p) => p.nw),
          borderColor: ACC,
          backgroundColor: areaFill(ACC),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: true,
        },
        {
          label: 'FIRE number',
          data: data.trajectory.map((p) => p.target),
          borderColor: WARN,
          borderWidth: 1.2,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items: Array<{ dataIndex: number }>) => {
              const age = data.trajectory[items[0]?.dataIndex ?? 0]?.age
              return age != null ? `Age ${age}` : ''
            },
            label: (ctx: { dataset: { label?: string }; raw: unknown }) =>
              `${ctx.dataset.label}: ${amountsHidden() ? '***' : fBaseCompact(Number(ctx.raw))}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: TICK(), maxTicksLimit: 6 }, grid: { display: false } },
        y: { ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() } },
      },
    },
  }

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      bodyClass="ov-ins-body"
      meta={fiLabel}
    >
      <div className="ov-ins-headrow">
        <InsHeadStat
          value={<Money as="span">{fBaseCompact(data.fire_number)}</Money>}
          label="FIRE number"
        />
        <InsHeadStat
          value={data.years_to_fire != null ? `${data.years_to_fire.toFixed(1)} yrs` : '50+ yrs'}
          label="to independence"
          color="var(--good)"
        />
      </div>
      {ageKnown ? (
        <OvChart
          config={cfg}
          sig={`${data.trajectory.length}|${data.fire_number}`}
          minHeight={110}
        />
      ) : (
        <Text as="p" size="sm" tone="faint">
          Add your date of birth in Profile to see ages
        </Text>
      )}
    </WidgetShell>
  )
}

const PlanRunway: ComponentType<WidgetContentProps> = ({ instance, def }) => {
  // The depletion curve is projected server-side, point by point — the widget used to build
  // `cash - burn * i` itself, which is money math on a chart's worth of points.
  const projections = useCashflowProjections()
  const data = projections.data?.runway

  if (projections.initialLoading) {
    return (
      <WidgetShell title={instance.title} base={def.title} fill bodyClass="ov-ins-body">
        <Skeleton height={140} />
      </WidgetShell>
    )
  }
  if (projections.error || !data?.known) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder detail="Needs at least one recorded expense to estimate a burn rate." />
      </WidgetShell>
    )
  }

  const cash = data.cash
  const burn = data.burn
  const runway = data.runway_months
  const caveat = cardCaveat(data.cards_missing_spending)
  const points = data.trajectory
  const ACC = resolveColor('var(--accent)')
  const cfg = {
    type: 'line' as const,
    data: {
      labels: points.map((_, i) => `+${i}mo`),
      datasets: [
        {
          label: 'Cash',
          data: points,
          borderColor: ACC,
          backgroundColor: areaFill(ACC),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items: Array<{ dataIndex: number }>) => `+${items[0]?.dataIndex ?? 0} mo`,
            label: (ctx: { raw: unknown }) =>
              `Cash: ${amountsHidden() ? '***' : fBaseCompact(Number(ctx.raw))}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: TICK(), maxTicksLimit: 6 }, grid: { display: false } },
        y: { ticks: { color: TICK(), callback: kTick }, grid: { color: GRID() } },
      },
    },
  }

  return (
    <WidgetShell
      title={instance.title}
      base={def.title}
      fill
      bodyClass="ov-ins-body"
      meta={runway != null ? `${runway.toFixed(1)} mo` : '∞'}
    >
      <div className="ov-ins-headrow">
        <InsHeadStat value={<Money as="span">{fBaseCompact(cash)}</Money>} label="cash on hand" />
        <InsHeadStat
          value={runway != null ? `${runway.toFixed(1)} mo` : 'No burn'}
          label={
            <>
              runway at <Money as="span">{fBaseCompact(burn)}</Money>/mo burn
            </>
          }
          color="var(--good)"
        />
      </div>
      {caveat && (
        <Text as="p" size="xs" tone="faint">
          {caveat}
        </Text>
      )}
      <OvChart config={cfg} sig={`${cash}|${burn}|${data.horizon_months}`} minHeight={110} />
    </WidgetShell>
  )
}

export const cashflowPlanningWidgetComponents: Record<string, ComponentType<WidgetContentProps>> = {
  spending: Spending,
  cashflow: Cashflow,
  calendar: Calendar,
  goals: Goals,
  crossover: Crossover,
  'plan-fire': PlanFire,
  'plan-runway': PlanRunway,
}
