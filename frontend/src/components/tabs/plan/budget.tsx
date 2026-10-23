// plan/budget.tsx — Budget sub-tab. Ported from prototype/plan/budget.jsx: Summary,
// CategoryRow, GroupBlock, BudgetTab. Wired to the real backend via useBudgetModel.
// Adds (owner-approved): the money-moves AvailCell (B4),
// Notes (B1, in budgetTarget), real-txn ActivityCell; + Lucius screen facts and category
// @-mentions reusing the existing dock.
import { useState, useRef, useEffect, useCallback, useLayoutEffect, useMemo } from 'react'
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from '@tanstack/react-router'
import { Tag } from 'lucide-react'
import * as PD from './planData'
import { monthLabel } from '../../../hooks/useBudget'
import type { BudgetMonth } from '../../../hooks/useBudget'
import { useCommitments } from '../../../hooks/useCommitments'
import { authFetch } from '../../../api/client'
import { getCurrencySymbol } from '../../../utils/format'
import { entityRefProps } from '../../../utils/mentionable'
import { useRegisterScreenFacts, useRegisterScreenPrompts } from '../../../lucius/useScreenContract'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { UndoPill } from './UndoPill'
import { RtaFloat } from './budgetRtaFloat'
import { CardPaymentPanel } from './budgetCardPayment'
import { useBreakpoint } from '../../../hooks/useBreakpoint'
import { usePointerResize } from '../../../hooks/usePointerResize'
import { Chevron, Arrow, Grip, Plus, X } from './icons'
import { Spark } from '../../../lucius/identity'
import {
  ColorDot,
  ColorIcon,
  MenuGlyph,
  Fig,
  Kebab,
  InlineEdit,
  FilterSelect,
  AutoAssign,
  AutoAssignPreviewModal,
  HiddenRow,
  ConfirmDeleteGroupModal,
} from './budgetParts'
import { MobileEnvelopeCard, MobileGroupHead, MobileNumsGrid } from './budgetCards'
import { AvailCell } from './budgetMove'
import type { MoveReq } from './budgetMove'
import { ActivityCell } from './budgetActivity'
import { RtaHero } from './budgetRta'
import { TargetEditor } from './budgetTarget'
import {
  AddCategoriesModal,
  AddToEnvelopeModal,
  MergeEnvelopeModal,
  RemoveCategoryModal,
  DeleteEnvelopeModal,
  NewEnvelopeModal,
  categoryKebabItems,
} from './budgetEnvelopeOps'
import { BudgetSummaryPanel, BudgetAutoAssignPanel, BudgetFutureAssignedPanel } from './budgetRail'
import { useBudgetModel, stableColor } from './useBudgetModel'
import type { LocalCat, LocalGroup, AutoPrevResult } from './useBudgetModel'
import { TabSkeleton } from '../../ui'

const { fBase, fSmart } = PD

export interface RegisterFilter {
  /** FEB-07 — a merged (N>1) envelope row's deep-link now carries every member category
   *  (never just the primary one), so the register shows the SAME set of transactions the
   *  popover it was opened from just listed. */
  category?: string | string[]
  month?: string
  /** Deep-link to the register pre-filtered to blank-category transactions (Outside
   *  envelopes' "Review in Accounts" / "Label it" — composes with `kind` to isolate
   *  uncategorized income from Uncategorized expense, both sharing the same blank-category bucket). */
  uncategorized?: boolean
  kind?: string
}
export interface BudgetTabProps {
  toast: (msg: string) => void
  /** Open the transaction detail drawer (deep-link to the Accounts register) for one raw txn. */
  onOpenTxn?: (raw: Record<string, unknown>) => void
  /** Open the Accounts register pre-filtered (handoff item 7 — "Review in Accounts"/"Label it"). */
  onOpenRegister?: (filter: RegisterFilter) => void
  /** Bumped by whoever owns the "Manage categories" modal (Plan.tsx) after a category mutation.
   *  The button moved OUT of this toolbar up to the Plan masthead beside FIRE Settings, and the
   *  modal moved with it — it has to stay reachable when a non-zero_based engine is home and
   *  this component is not even mounted. This is how the edit still refreshes the budget. */
  categoriesVersion?: number
}

// module-level drag payload
let DRAG: { type: 'cat' | 'group'; gid: string; key?: string } | null = null
type OverState =
  | { kind: 'cat'; gid: string; key: string; edge: 'top' | 'bottom' }
  | { kind: 'group'; gid: string }
  | null

// ── Summary (Ready-to-Assign card) ─────────────────────────────────────────────
export interface SummaryBudget {
  readyToAssign: number
  ageOfMoney: number | null
  ageForecastable: boolean
  onBudgetCash: number | null
  /** SIGNED Σ of every envelope's Available — negative when envelopes are net overspent and
   *  holding those holes until the roll (ADR 2026-09-09). */
  heldInEnvelopes: number
  /** SIGNED, ≤ 0 — what CASH spending with NO envelope holds back until the roll charges it
   *  (ADR 2026-09-10). Card spending never feeds it. */
  heldOutsideEnvelopes: number
  /** THIS month's card spending no envelope covered — an activity figure, not a card balance. */
  cardSpendNotCovered: number
  baseline: number
  /** The stored carry floor (`rollover_start`) — drives the "Fresh start · since …" line under
   *  the hero. Null = the whole history carries, and the line is not rendered at all. */
  rolloverStart: string | null
  hasForeignCash: boolean
  assignedInFuture: number
  /** The flow-framed RTA breakdown (design doc 2026-09-13) and the display labels/total it
   *  needs — optional so a caller that hasn't threaded them (e.g. a hand-built test fixture)
   *  still type-checks; `RtaHero` treats their absence exactly like `flow: null`. */
  flow?: BudgetMonth['flow']
  prevMonthLabel?: string
  totalAssigned?: number
  overspentCount: number
  overspentAmount: number
  underfundedCount: number
  underfundedAmount: number
  /** Σ every envelope's Available, negatives included — the plain envelope sum the header, the
   *  mini line and the table's Total row all print (ADR 2026-09-08 superseded the display-total
   *  fold and ADR 2026-09-09 keeps it: spending outside every envelope is the table's Outside
   *  envelopes section's job, not this number's). */
  totalAvailable: number
}
// ── The one coaching line under the header. Order matters: a NEGATIVE Ready to Assign is the
// most urgent thing true about the month and is checked FIRST — it is reachable with zero
// overspent envelopes and zero underfunded targets (a cutover baseline, or this month's
// uncovered card spending, drags it under while every envelope still reads funded), and the
// "every target's funded" fallthrough was cheerfully asserting the opposite in exactly that
// state. Covering an overspent envelope COSTS Ready to Assign (ADR 2026-09-09) — an envelope
// holds its own hole until the roll, and assigning there is what pays for it.
//
// A negative Ready to Assign has TWO causes under the restored identity and only one of them is
// over-assignment, so the branch splits on what the envelopes actually hold. Positive: money IS
// sitting in envelopes, moving some back is a real, available remedy. Zero or negative: nothing
// is there to move, so the shortfall is a cash gap — most often LAST month's overspend, clamped
// to 0 at the roll and charged here — and telling that user to "unassign" names an action with
// no target. (Card spending is never the cause: an unfunded swipe is RTA-neutral either way.)
export function BudgetInsight({ rta, b }: { rta: number; b: SummaryBudget }) {
  // Owner ask, 2026-09-15: the whole coaching ribbon is parked for now (an earlier pass only
  // hid the "every target's funded" branch — the overspent/underfunded/over-assigned ones
  // kept rendering). Not deleted: the verdicts below come back when it's wanted again, and
  // the caller drops the whole `.pl-sum-action` band when this returns null.
  const HIDDEN = true
  if (HIDDEN) return null
  const ovN = b.overspentCount,
    ufN = b.underfundedCount
  if (rta < -0.005 && b.heldInEnvelopes > 0.005)
    return (
      <>
        Over-assigned by <b className="mask">{fSmart(-rta)}</b> — move money back or unassign to get
        Ready to Assign to zero.
      </>
    )
  if (rta < -0.005)
    return (
      <>
        Ready to Assign is short by <b className="mask">{fSmart(-rta)}</b> — nothing is held in your
        envelopes to move back, so this is likely a rolled-over overspend charged this month. It
        clears as income arrives, or you can leave it.
      </>
    )
  if (ovN > 0)
    return (
      <>
        <b>
          {ovN} envelope{ovN > 1 ? 's are' : ' is'}
        </b>{' '}
        overspent — {ovN > 1 ? 'they hold' : 'it holds'} the shortfall until month-end. Cover{' '}
        {ovN > 1 ? 'them' : 'it'} from Ready to Assign or another envelope, or leave{' '}
        {ovN > 1 ? 'them' : 'it'} and the hole is charged to next month.
      </>
    )
  if (ufN > 0)
    return (
      <>
        <b>
          {ufN} target{ufN > 1 ? 's' : ''}
        </b>{' '}
        still need{ufN === 1 ? 's' : ''} <b className="mask">{fSmart(b.underfundedAmount)}</b> this
        month — assign it from your <b className="mask">{fBase(rta)}</b> ready to assign.
      </>
    )
  // Owner ask, 2026-09-15: park the "every target's funded" verdict for now — revisit later.
  return null
}

function Summary({
  b,
  month,
  onPrev,
  onNext,
  canPrev,
  filter,
  setFilter,
  showBrief,
  onFundUnderfunded,
}: {
  b: SummaryBudget
  month: string
  onPrev: () => void
  onNext: () => void
  canPrev: boolean
  filter: string | null
  setFilter: (f: string | null) => void
  showBrief: boolean
  /** RTA popover quick action (handoff delta #8) — opens the SAME auto-assign preview as the
   *  toolbar's "Underfunded". */
  onFundUnderfunded: () => void
}) {
  const rta = b.readyToAssign
  const ufN = b.underfundedCount
  const [briefOpen, setBriefOpen] = useState(false)
  // On a phone the pinned hero ate a fixed slice of an already-small viewport on every
  // scroll (owner ask) — let it scroll away with the rest of the page like Outlook's does.
  const isMobile = useIsMobile()
  return (
    <div
      className={
        'pl-summary' +
        (isMobile ? ' pl-summary--mobile' : ' pl-summary--pinned') +
        (briefOpen ? ' brief-open' : '')
      }
    >
      {/* Owner-caught regression, 2026-09-15: the "eyebrow+nav row / full-width figure / no
          Age of money" redesign was meant for MOBILE ONLY (it was fixing a phone-width
          cramming complaint) — it landed in the UNCONDITIONAL .pl-sum-row markup instead and
          overwrote the desktop layout too. Desktop goes back to its original nav / hero-block
          / Age-of-money single row, unchanged; mobile keeps the new stacked, Age-of-money-free
          layout. `heroFigure`/`freshStart` are defined once and interpolated into whichever
          branch is active — `isMobile` is a plain ternary here (not two always-mounted trees
          toggled by CSS), so RtaHero's own popover state is never duplicated. */}
      {(() => {
        const heroFigure = (
          <RtaHero
            rta={rta}
            monthLabel={month}
            onBudgetCash={b.onBudgetCash}
            heldInEnvelopes={b.heldInEnvelopes}
            heldOutsideEnvelopes={b.heldOutsideEnvelopes}
            cardSpendNotCovered={b.cardSpendNotCovered}
            baseline={b.baseline}
            assignedInFuture={b.assignedInFuture}
            flow={b.flow}
            prevMonthLabel={b.prevMonthLabel}
            totalAssigned={b.totalAssigned}
            underfundedCount={ufN}
            onFundUnderfunded={onFundUnderfunded}
            hasForeignCash={b.hasForeignCash}
          />
        )
        // The inflows/carried + identity explainers now live in the RTA breakdown popover
        // (click the number) — the hero stays clean (owner request). The zero-state "Every
        // dollar has a job" line is gone too (owner, 2026-07-03): a S$0 hero already says it,
        // and so is the "Fresh start · since …" line (owner, 2026-09-25): the ⋯ menu already
        // reads "Started fresh from <month>".
        const nav = (
          <span className="pl-mnav">
            <button onClick={onPrev} disabled={!canPrev} aria-label="Previous month">
              <Arrow dir="left" />
            </button>
            <span className="lbl">{month.replace(/^(\w{3})\w*/, '$1')}</span>
            <button onClick={onNext} aria-label="Next month">
              <Arrow dir="right" />
            </button>
          </span>
        )
        return isMobile ? (
          <div className="pl-sum-row">
            <div className="pl-sum-headrow">
              <span className="pl-sum-eyebrow">Ready to Assign</span>
              {nav}
            </div>
            {heroFigure}
          </div>
        ) : (
          /* Owner ask, 2026-09-15: figures LEFT, month nav hard RIGHT — the nav used to lead
             the row, which put the page's most important number in the middle of it. */
          <div className="pl-sum-row">
            <div className="pl-sum-hero-block">
              <span className="pl-sum-eyebrow">Ready to Assign</span>
              {heroFigure}
            </div>
            <div className="pl-sum-stat pl-sum-age">
              <span className="k">Age of money</span>
              <span className="v">
                {b.ageForecastable && b.ageOfMoney != null ? (
                  <>
                    {b.ageOfMoney}
                    <span className="u"> days</span>
                  </>
                ) : (
                  <span className="u">not enough history</span>
                )}
              </span>
            </div>
            {nav}
          </div>
        )
      })()}
      <div className="pl-sum-mini">
        {b.overspentCount > 0 && (
          <button
            className={'pl-sum-mini-stat bad as-chip' + (filter === 'overspent' ? ' on' : '')}
            onClick={() => setFilter(filter === 'overspent' ? null : 'overspent')}
          >
            <i />
            {b.overspentCount} overspent
          </button>
        )}
        {b.underfundedCount > 0 && (
          <button
            className={'pl-sum-mini-stat warn as-chip' + (filter === 'underfunded' ? ' on' : '')}
            onClick={() => setFilter(filter === 'underfunded' ? null : 'underfunded')}
          >
            <i />
            {b.underfundedCount} underfunded
          </button>
        )}
        {b.overspentCount === 0 && b.underfundedCount === 0 && (
          <span className="pl-sum-mini-stat ok">
            <i />
            On track
          </span>
        )}
        <span className="pl-sum-mini-stat">
          <span className="mask">{fBase(b.totalAvailable)}</span> available
        </span>
      </div>
      {showBrief &&
        (() => {
          const insight = BudgetInsight({ rta, b })
          if (insight == null) return null
          return (
            <div className="pl-sum-action">
              <span className="pl-sum-spark">
                <Spark />
              </span>
              <span className="pl-sum-insight">{insight}</span>
              <button
                className="pl-sum-brief-toggle"
                onClick={() => setBriefOpen((o) => !o)}
                aria-expanded={briefOpen}
                aria-label="Toggle insight detail"
              >
                <Chevron dir={briefOpen ? 'up' : 'down'} size={13} />
              </button>
            </div>
          )
        })()}
    </div>
  )
}

// ── Outside envelopes (handoff item 7 — replaces UnsortedRow + UnbudgetedGroup; ported
// verbatim from prototype/plan/budget.jsx OutsideEnvelopes, DOM+class+copy exact) ────────────
// One derived, read-only section for every kind of spending no envelope saw this month:
// Uncategorized (no category at all), Uncategorized income (already counted in Ready to Assign,
// just unlabeled), and per-category Unbudgeted (a real category, just not linked to any
// envelope). None of the three is a stored category/envelope — no drag, no color/rename/hide,
// no editable Assigned — but every Activity figure still opens the real transaction popover.
export interface OutsideEnvelopesData {
  unsortedActivity: number
  /** What the Uncategorized bucket HOLDS this month — ≤ 0, and NOT the same as its Activity: a
   *  card swipe with no category shows activity but holds nothing (ADR 2026-09-10). */
  unsortedAvailable: number
  uncatIncome: number
  unbudgeted: Array<{
    key: string
    label: string
    activity: number
    available: number
    color: string
  }>
}
interface OutsideRow {
  key: string
  kindClass: 'unsorted' | 'uncatincome' | 'unbudgeted'
  label: string
  color: string
  status: string
  actionLabel: string
  onAction: () => void
  activity: number
  /** The Available column. A real figure for the two HOLDING buckets (Uncategorized, Unbudgeted);
   *  `null` for uncategorized income, which holds nothing — it is already inside Ready to
   *  Assign — and so keeps rendering the em dash. */
  available: number | null
  cellTarget: {
    activity: number
    label: string
    color: string
    categoryIds?: string[]
    uncategorized?: boolean
    kind?: string
  }
}
// Header subtotal = Uncategorized + Unbudgeted ONLY — uncategorized income is deliberately
// EXCLUDED (prototype budget.jsx:933; it isn't "spending no envelope saw", it's unlabeled
// income already inside Ready to Assign).
export function buildOutsideRows(
  data: OutsideEnvelopesData,
  onReviewInAccounts: () => void,
  onLabelIncome: () => void,
  onAddToEnvelope: (categoryId: string, categoryLabel: string) => void,
): OutsideRow[] {
  const rows: OutsideRow[] = []
  if (Math.abs(data.unsortedActivity) > 0.005) {
    rows.push({
      key: '__unsorted__',
      kindClass: 'unsorted',
      label: 'Uncategorized',
      color: 'var(--warn)',
      status: 'transactions missing a category',
      actionLabel: 'Review in Accounts',
      onAction: onReviewInAccounts,
      activity: data.unsortedActivity,
      available: data.unsortedAvailable,
      // BUD-SWEEP-07 — Uncategorized's OWN figure is uncategorized EXPENSE only (the uncat-income
      // row below already scopes to kind:'income'); without kind:'expense' here the popover
      // fetched BOTH kinds and its footer net silently contradicted the rows it just listed.
      cellTarget: {
        activity: data.unsortedActivity,
        label: 'Uncategorized',
        color: 'var(--warn)',
        uncategorized: true,
        kind: 'expense',
      },
    })
  }
  if (data.uncatIncome > 0.005) {
    rows.push({
      key: '__uncatincome__',
      kindClass: 'uncatincome',
      label: 'Uncategorized income',
      color: 'var(--good)',
      status: 'already in Ready to Assign',
      actionLabel: 'Label it',
      onAction: onLabelIncome,
      activity: data.uncatIncome,
      available: null, // already inside Ready to Assign — there is nothing to hold
      cellTarget: {
        activity: data.uncatIncome,
        label: 'Uncategorized income',
        color: 'var(--good)',
        uncategorized: true,
        kind: 'income',
      },
    })
  }
  for (const u of data.unbudgeted) {
    // Activity OR Available — never Activity alone. A category can net to ~0 on Activity (a
    // cash expense cancelled by a same-label transfer/sell landing on a CARD) while still
    // HOLDING the whole cash hole, and `outsideAvailableSubtotal` below sums that hold whether
    // or not its row rendered: filtering on Activity alone published a subtotal no listed row
    // accounted for (review, 2026-09-10 — the display half of the `_unbudgeted_keys` fix).
    // Uncategorized above needs no such widening: only expenses ever reach the blank key, so its
    // Available can never be non-zero while its Activity is.
    if (Math.abs(u.activity) <= 0.005 && Math.abs(u.available) <= 0.005) continue
    rows.push({
      key: u.key,
      kindClass: 'unbudgeted',
      label: u.label,
      color: u.color,
      status: 'category not in an envelope',
      actionLabel: 'Add to envelope…',
      onAction: () => onAddToEnvelope(u.key, u.label),
      activity: u.activity,
      available: u.available,
      cellTarget: { activity: u.activity, label: u.label, color: u.color, categoryIds: [u.key] },
    })
  }
  return rows
}
export function outsideSubtotal(data: OutsideEnvelopesData): number {
  return (
    Math.round(
      (data.unsortedActivity + data.unbudgeted.reduce((s, c) => s + c.activity, 0)) * 100,
    ) / 100
  )
}
/** The Available column's own subtotal — what these buckets HOLD, which is the identity's
 *  `held_outside_envelopes` term (ADR 2026-09-10), not their Activity. Uncategorized income is
 *  excluded for the same reason it is excluded above: it holds nothing. */
export function outsideAvailableSubtotal(data: OutsideEnvelopesData): number {
  return (
    Math.round(
      (data.unsortedAvailable + data.unbudgeted.reduce((s, c) => s + c.available, 0)) * 100,
    ) / 100
  )
}

// FEB-05 — ONE hidden-row policy, ONE count source, reused by every auto-assign-adjacent
// surface (the toolbar filter chips, the Summary stats, the RTA popover's "Fund N underfunded
// targets" quick action, the AutoAssign menu's badge hints): a hidden row is EXCLUDED
// everywhere, matching buildClientAutoSteps/mapServerAutoSteps' own hidden filter — so the
// button's count can never promise more (or less) than what auto-assign will actually touch.
// (The one deliberate split: a cash-overpaid PAYMENT envelope counts in the display surfaces
// below but not in the Auto-assign hints, which never touch a locked card row — see the
// `autoAssignB` note in BudgetTab.)

// A row is "overspent" for the header badge, the filter chip and the coaching line. Ordinary
// rows speak the target vocabulary (`status`); a payment envelope (Credit Card Payments) has no
// target at all and mapCat forces its `status` to 'none' so it can never leak into the
// money-writing bulk auto-assign — so its own hole has to be read from the payment vocabulary
// directly. A CASH-overpaid card (you sent the card MORE than its envelope had set aside) is a
// genuine overspend and YNAB counts it in its "N Overspent" badge exactly like any other red
// category; we used to report 0 for that state. The GOLD case — a card that SPENT more via
// purchases than its envelope funds — is a DIFFERENT animal and deliberately stays out: that is
// card debt, not a cash hole, and it has its own figure in buildCardDebtUnderfunded.
export function isOverspentRow(c: LocalCat): boolean {
  return c.isPaymentEnvelope ? (c.availableForPayment ?? 0) < -0.005 : c.status === 'overspent'
}
// The size of that hole, read from whichever vocabulary the row speaks.
function overspentBy(c: LocalCat): number {
  return -(c.isPaymentEnvelope ? (c.availableForPayment ?? 0) : c.available)
}
export function buildFilterCounts(cats: LocalCat[]): Record<string, number> {
  // Payment envelopes (Credit Card Payments) never participate in the TARGET-based filter chips
  // (underfunded/overfunded/Money-available) — they carry their own Payment vocabulary and no
  // target (UI spec §6). Overspent is the one chip they can match, via isOverspentRow.
  const visible = cats.filter((c) => !c.hidden && !c.isPaymentEnvelope)
  return {
    all: visible.length,
    underfunded: visible.filter((c) => c.status === 'underfunded').length,
    overspent: cats.filter((c) => !c.hidden && isOverspentRow(c)).length,
    overfunded: visible.filter((c) => c.status === 'overfunded').length,
    funded: visible.filter((c) => c.available > 0.005).length,
  }
}
// Desktop rail's Underfunded stat (budgetRail.tsx's BudgetAutoAssignPanel) — a card's OWN
// payment envelope going underfunded is a different animal from an ordinary envelope's target
// (UI spec §6: payment envelopes carry no target at all), so it needs its own count/sum rather
// than reusing buildStatusTotals' underfunded bucket.
export function buildCardDebtUnderfunded(cats: LocalCat[]): { count: number; amount: number } {
  const under = cats.filter(
    (c) => c.isPaymentEnvelope && !c.hidden && (c.paymentUnderfunded ?? 0) > 0.005,
  )
  return { count: under.length, amount: under.reduce((s, c) => s + (c.paymentUnderfunded ?? 0), 0) }
}
// The rail's "Total target budget" line (owner ask, 2026-09-15) — YNAB calls the same figure
// "Cost to Be Me": what it costs to fully fund every target this month, INDEPENDENT of what's
// already assigned. That makes it the sum of each row's monthly-equivalent target amount
// (`c.target`, already cadence-resolved in useBudgetModel — a weekly target times this month's
// real 4-or-5 occurrences, a yearly one over 12, a by-date one over the months remaining), NOT
// the sum of `needed`, which is only the REMAINING shortfall and collapses to 0 once funded.
// Hidden rows are excluded the way every other total here excludes them, payment envelopes
// carry no target at all, and a SNOOZED target costs nothing this month by definition.
export function buildTargetsTotal(cats: LocalCat[]): number {
  const sum = cats
    .filter((c) => !c.hidden && !c.isPaymentEnvelope && !c.snoozed)
    .reduce((a, c) => a + (c.target || 0), 0)
  return Math.round(sum * 100) / 100
}
export function buildStatusTotals(cats: LocalCat[]): {
  overspentCount: number
  overspentAmount: number
  underfundedCount: number
  underfundedAmount: number
} {
  const visible = cats.filter((c) => !c.hidden && !c.isPaymentEnvelope)
  const overspent = cats.filter((c) => !c.hidden && isOverspentRow(c))
  const underfunded = visible.filter((c) => c.status === 'underfunded')
  return {
    overspentCount: overspent.length,
    overspentAmount: overspent.reduce((s, c) => s + overspentBy(c), 0),
    underfundedCount: underfunded.length,
    underfundedAmount: underfunded.reduce((s, c) => s + c.needed, 0),
  }
}
export function OutsideEnvelopes({
  data,
  month,
  onOpenTxn,
  onOpenRegister,
  onReviewInAccounts,
  onLabelIncome,
  onAddToEnvelope,
}: {
  data: OutsideEnvelopesData
  month: string
  onOpenTxn?: (raw: Record<string, unknown>) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  onReviewInAccounts: () => void
  onLabelIncome: () => void
  onAddToEnvelope: (categoryId: string, categoryLabel: string) => void
}) {
  const isMobile = useIsMobile()
  const rows = buildOutsideRows(data, onReviewInAccounts, onLabelIncome, onAddToEnvelope)
  if (!rows.length) return null
  const sub = outsideSubtotal(data)
  // What these buckets HOLD until the roll — the identity's `held_outside_envelopes` (ADR
  // 2026-09-10). Rendered through the SAME `Fig kind="available"` every envelope row's
  // Available cell uses, so a red bucket reads exactly like a red envelope.
  const availSub = outsideAvailableSubtotal(data)
  if (isMobile) {
    return (
      <div className="pl-mgroup">
        <MobileGroupHead
          className="pl-grow--outside"
          name={
            <span className="pl-gname-btn pl-gname--outside" style={{ cursor: 'default' }}>
              Outside envelopes
            </span>
          }
          count={<span className="pl-gcount">{rows.length}</span>}
          nums={
            <MobileNumsGrid
              assigned={<span className="pl-fig zero">—</span>}
              activity={<span className="mask">{fBase(sub)}</span>}
              available={<Fig n={availSub} kind="available" />}
            />
          }
        />
        <div className="pl-mgroup-body">
          {rows.map((r) => {
            const statusKindClass =
              r.kindClass === 'unsorted'
                ? 'unsorted'
                : r.kindClass === 'uncatincome'
                  ? 'income'
                  : 'unbudgeted'
            return (
              <MobileEnvelopeCard
                key={r.key}
                dot={
                  <span className="pl-cdot-wrap">
                    <span className="pl-cdot--btn" aria-hidden="true" style={{ cursor: 'default' }}>
                      <ColorIcon icon="tag" color={r.color} />
                    </span>
                  </span>
                }
                name={
                  <span className="pl-clabel" style={{ cursor: 'default' }}>
                    {r.label}
                  </span>
                }
                statusLine={<span className={'pl-cstatus ' + statusKindClass}>{r.status}</span>}
                actionButton={
                  <button className="pl-caction addenv pl-caction--mobile" onClick={r.onAction}>
                    {r.actionLabel}
                  </button>
                }
                nums={
                  <MobileNumsGrid
                    assigned={<span className="pl-fig zero">—</span>}
                    activity={
                      <ActivityCell
                        c={r.cellTarget}
                        month={month}
                        onOpenTxn={onOpenTxn}
                        onOpenRegister={onOpenRegister}
                      />
                    }
                    available={
                      r.available == null ? (
                        <span className="pl-fig zero">—</span>
                      ) : (
                        <Fig n={r.available} kind="available" />
                      )
                    }
                  />
                }
              />
            )
          })}
        </div>
      </div>
    )
  }
  return (
    <>
      <div
        className="pl-trow pl-grow pl-grow--outside"
        title="Spending that isn't drawing from any envelope — give it a category, or link its category to an envelope"
      >
        <span className="pl-gname">
          <span className="pl-grip pl-grip--group" aria-hidden="true" />
          <span className="pl-gtoggle">
            <span className="pl-gname-btn pl-gname--outside" style={{ cursor: 'default' }}>
              Outside envelopes
            </span>
          </span>
          <span className="pl-gcount">{rows.length}</span>
        </span>
        <span className="pl-c-num pl-gsub"></span>
        <span className="pl-c-num pl-gsub mask">{fBase(sub)}</span>
        <span className="pl-c-num pl-gsub mask">{fBase(availSub)}</span>
      </div>
      {rows.map((r) => {
        // Explicit per-kind class literals (never string-concatenated) so the CSS-class
        // guard's static scan can see every token — a codebase convention (mirrors the
        // funded/under/overfunded ternary a few lines below).
        const rowKindClass =
          r.kindClass === 'unsorted'
            ? 'pl-crow--unsorted'
            : r.kindClass === 'uncatincome'
              ? 'pl-crow--uncatincome'
              : 'pl-crow--unbudgeted'
        const statusKindClass =
          r.kindClass === 'unsorted'
            ? 'unsorted'
            : r.kindClass === 'uncatincome'
              ? 'income'
              : 'unbudgeted'
        return (
          <div key={r.key} className={'pl-trow pl-crow ' + rowKindClass}>
            <span className="pl-cname">
              <span className="pl-grip" aria-hidden="true" />
              <span className="pl-cdot-wrap">
                <span className="pl-cdot--btn" aria-hidden="true" style={{ cursor: 'default' }}>
                  <ColorIcon icon="tag" color={r.color} />
                </span>
              </span>
              <span className="pl-cmain">
                <span className="pl-cline">
                  <span className="pl-clabel" style={{ cursor: 'default' }}>
                    {r.label}
                  </span>
                  <span className={'pl-cstatus ' + statusKindClass}>{r.status}</span>
                </span>
              </span>
              <button className="pl-caction addenv" onClick={r.onAction}>
                {r.actionLabel}
              </button>
            </span>
            <span className="pl-c-num">
              <span className="pl-fig zero">—</span>
            </span>
            <span className="pl-c-num">
              <ActivityCell
                c={r.cellTarget}
                month={month}
                onOpenTxn={onOpenTxn}
                onOpenRegister={onOpenRegister}
              />
            </span>
            <span className="pl-c-num">
              {r.available == null ? (
                <span className="pl-fig zero">—</span>
              ) : (
                <Fig n={r.available} kind="available" />
              )}
            </span>
          </div>
        )
      })}
    </>
  )
}

// ── Linked-categories chip (EnvCatsBadge, handoff item 15 — ported verbatim from
// prototype/plan/budget.jsx:695-742: reuses the Kebab component's OWN classes — this is a
// disclosure menu, not a bespoke popover) ─────────────────────────────────────────────────
// Display rules (pure, testable): a count — "1 category" / "N categories" — on every envelope
// that funds at least one (owner, 2026-09-25: the "+ {other}" / "+ N more" / sole-label forms
// read badly; the names live one click away, in the chip's own menu). The chip SHOWS it as the
// tag glyph + the number (owner, same day); the words stay for screen readers.
const catCountNoun = (n: number) => (n === 1 ? 'category' : 'categories')
export function envCatsChipText(memberCount: number): string | null {
  if (memberCount === 0) return null
  return `${memberCount} ${catCountNoun(memberCount)}`
}
/** A category-count chip's face: the tag (the app's category glyph) + the count, with the noun
 *  for screen readers — so the chip's text still reads "2 categories". */
function CatCount({ n }: { n: number }) {
  return (
    <>
      <Tag size={11} strokeWidth={2.2} aria-hidden="true" />
      {n}
      <span className="sr-only"> {catCountNoun(n)}</span>
    </>
  )
}
// Render gate: every envelope with at least one category (a zero-category envelope gets its
// own "0 categories" hint chip instead).
export function envCatsBadgeVisible(memberKeys: string[]): boolean {
  return memberKeys.length > 0
}
/** The "0 categories" hint a zero-category envelope wears in the chip's place. Exported for the
 *  50/30/20 table, whose rows are the same envelopes (propCells.tsx). */
export function EnvCatsEmptyHint() {
  return (
    <span
      className="pl-gcount pl-gcount--empty"
      title="Link a category to this envelope to start budgeting for it"
    >
      <CatCount n={0} />
    </span>
  )
}
/** Exported for the 50/30/20 table, whose rows are the same envelopes (PropRows.tsx). */
export function EnvCatsBadge({
  categoryIds,
  catLabel,
  catColor,
  onRemove,
  onAddCategories,
}: {
  categoryIds: string[]
  catLabel: (key: string) => string
  catColor: (key: string) => string
  onRemove: (categoryId: string, categoryLabel: string) => void
  onAddCategories: () => void
}) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{
    left: number
    top?: number
    bottom?: number
    root: Element
  } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const names = categoryIds.map((k) => ({ key: k, label: catLabel(k) }))
  const text = envCatsChipText(names.length)

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const r = btnRef.current!.getBoundingClientRect()
    const MENU_W = 236
    const up = window.innerHeight - r.bottom < 300
    const left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - MENU_W - 8)))
    // Picked here, not during render: the portal target (the modal the trigger sits in, else the
    // app shell) is read off the trigger's DOM, which render may not touch.
    const root =
      ref.current?.closest('.pl-modal') ?? document.querySelector('.app-shell') ?? document.body
    setPos(
      up
        ? { left, bottom: Math.round(window.innerHeight - r.top + 4), root }
        : { left, top: Math.round(r.bottom + 4), root },
    )
  }, [open])
  useEffect(() => {
    if (!open) return
    // The panel is portaled out of the table (below), so "inside" is the trigger OR the panel.
    const inside = (t: Node) => !!(ref.current?.contains(t) || popRef.current?.contains(t))
    const h = (e: MouseEvent) => {
      if (!inside(e.target as Node)) setOpen(false)
    }
    // A popover anchored to its trigger's rect must close when the PAGE scrolls (the anchor
    // moves), but `capture: true` also delivers scrolls from INSIDE the popover's own
    // scroller — so opening the list and reaching for it dismissed it instantly
    // (owner-reported, 2026-09-15). Same guard budgetActivity/budgetMove already use.
    const onScroll = (e: Event) => {
      if (inside(e.target as Node)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('mousedown', h)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', h)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open])

  if (!text) return null
  return (
    <span
      className={'pl-kebab-wrap pl-envcats-wrap' + (open ? ' open' : '')}
      ref={ref}
      style={{ marginLeft: 0, minWidth: 0 }}
    >
      <button
        ref={btnRef}
        className="pl-envcats pl-envcats--btn"
        title={'Funds ' + names.map((n) => n.label).join(', ') + ' — click to manage'}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        <CatCount n={names.length} />
      </button>
      {/* Portaled to the app shell: the desktop table's content carries a transform (the
          workspace scroll hold, plan.css), which would otherwise capture this fixed panel —
          positioning it against the table instead of the viewport and clipping it. */}
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            data-modal-escape-layer
            className="pl-menu pl-menu--kebab"
            style={{
              position: 'fixed',
              left: pos.left + 'px',
              top: pos.top != null ? pos.top + 'px' : 'auto',
              bottom: pos.bottom != null ? pos.bottom + 'px' : 'auto',
              right: 'auto',
              minWidth: 236,
            }}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="pl-menu-lbl">
              Funds {names.length} categor{names.length === 1 ? 'y' : 'ies'}
            </div>
            {names.map((n) => (
              <div key={n.key} className="pl-envcat-item">
                <span className="pl-envcat-name">
                  <MenuGlyph icon="tag" color={catColor(n.key)} />
                  {n.label}
                </span>
                <button
                  className="pl-envcat-x"
                  title={'Remove ' + n.label + ' from this envelope'}
                  onClick={() => {
                    setOpen(false)
                    onRemove(n.key, n.label)
                  }}
                >
                  <X size={11} />
                </button>
              </div>
            ))}
            <div className="pl-menu-div" />
            <button
              className="pl-menu-item"
              onClick={() => {
                setOpen(false)
                onAddCategories()
              }}
            >
              Add categories…
            </button>
          </div>,
          pos.root,
        )}
    </span>
  )
}

// ── Category row ────────────────────────────────────────────────────────────────
interface RowHandlers {
  dnd: Dnd
  over: OverState
  month: string
  allGroups: LocalGroup[]
  catLabel: (key: string) => string
  catColor: (key: string) => string
  /** Ready to Assign's current value — threaded into AvailCell (handoff delta #9) so it can
   *  lead the Move/Cover picker's option list as its own "Budget" row. */
  rta: number
  onOpenCommitment: (c: LocalCat) => void
  /** Credit Card Payments (payment_envelope mode) — the payment-row equivalent of
   *  onOpenCommitment; a locked payment row's click routes here instead. */
  onOpenCardPayment: (c: LocalCat) => void
  onAssign: (key: string, value: number) => void
  onAction: (c: LocalCat, kind: string) => void
  onRename: (c: LocalCat, label: string) => void
  onRecolor: (envelopeId: string, color: string) => void
  onMoveTo: (key: string, from: string, to: string) => void
  onToggleHide: (key: string) => void
  onDeleteEnvelope: (c: LocalCat) => void
  onEditTarget: (c: LocalCat, gid: string, mode?: string) => void
  onTransfer: (fromKey: string, toKey: string, amount: number) => void
  onMerge: (c: LocalCat) => void
  onSplit: (c: LocalCat, categoryId: string, categoryLabel: string) => void
  onAddCats: (c: LocalCat) => void
  onOpenTxn?: (raw: Record<string, unknown>) => void
  onOpenRegister?: (filter: RegisterFilter) => void
}
export function CategoryRow({
  c,
  gid,
  dnd,
  over,
  otherGroups,
  locked,
  month,
  allGroups,
  catLabel,
  catColor,
  rta,
  onOpenCommitment,
  onOpenCardPayment,
  onAssign,
  onAction,
  onRename,
  onRecolor,
  onMoveTo,
  onToggleHide,
  onDeleteEnvelope,
  onEditTarget,
  onTransfer,
  onMerge,
  onSplit,
  onAddCats,
  onOpenTxn,
  onOpenRegister,
}: RowHandlers & { c: LocalCat; gid: string; otherGroups: LocalGroup[]; locked: boolean }) {
  const [renaming, setRenaming] = useState(false)
  const [assigning, setAssigning] = useState(false)
  const [assignDraft, setAssignDraft] = useState('')
  // Overfunded row's "Move S$X" action (handoff delta #9) — asks AvailCell to open its OWN
  // Move picker, anchored to the clicked button and prefilled with the excess; it never
  // releases the money silently anymore (the old one-click release is gone).
  const [moveReq, setMoveReq] = useState<MoveReq | null>(null)
  const assignRef = useRef<HTMLInputElement>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (assigning && assignRef.current) {
      assignRef.current.focus()
      assignRef.current.select()
    }
  }, [assigning])
  function startAssign() {
    setAssignDraft(c.assigned ? String(Math.round(c.assigned * 100) / 100) : '')
    setAssigning(true)
  }
  function commitAssign() {
    onAssign(c.key, PD.parseMoneyInput(String(assignDraft)))
    setAssigning(false)
  }
  // A payment row (Credit Card Payments, payment_envelope mode) has no target — gate the
  // target-based status/progress-bar/Cover-Fund-Move machinery off entirely for it (UI spec
  // §6: never fold paymentUnderfunded/overpaid into this vocabulary); its own status line +
  // Payment cell are computed separately below.
  const isOver = !c.isPaymentEnvelope && c.status === 'overspent'
  const under = !c.isPaymentEnvelope && c.status === 'underfunded'
  const overfunded = !c.isPaymentEnvelope && c.status === 'overfunded'
  const funded = !c.isPaymentEnvelope && c.status === 'funded'
  // Nothing left and money spent this month → YNAB's "Fully Spent" (owner, 2026-09-25): for a
  // no-target envelope that read as blank, and for a met target it said only "Funded". A target
  // still short keeps its "more needed". Display-only: the status the filters read is unchanged.
  const fullySpent =
    !c.isPaymentEnvelope &&
    !isOver &&
    !under &&
    Math.abs(c.available) <= 0.005 &&
    c.activity < -0.005
  const excess = overfunded ? c.available - (c.target || 0) : 0
  // Payment state (priority overpaid > underfunded > funded, UI spec §3.2/§9).
  const payAvail = c.availableForPayment ?? 0
  const payUnder = c.paymentUnderfunded ?? 0
  const paymentOverpaid = !!c.isPaymentEnvelope && payAvail < 0
  const paymentUnderfundedNow = !!c.isPaymentEnvelope && !paymentOverpaid && payUnder > 0.005
  const paymentOwed = c.paymentOwed ?? 0
  const monthlyNeed = c.assigned + Math.max(0, c.needed)
  const pct =
    funded || overfunded
      ? 100
      : monthlyNeed > 0
        ? Math.min(100, (c.assigned / monthlyNeed) * 100)
        : 0
  const overTotal = c.assigned + (isOver ? -c.available : 0)
  const greenPct = isOver && overTotal > 0 ? Math.min(100, (c.assigned / overTotal) * 100) : 0
  const dragging = DRAG && DRAG.type === 'cat' && DRAG.key === c.key
  const dropHere = over && over.kind === 'cat' && over.key === c.key && !dragging
  const showBadge = envCatsBadgeVisible(c.categoryIds)
  // Eaten progress bar tooltip formula, ported verbatim from prototype/plan/budget.jsx:803
  // (identical for overspent and non-overspent rows — an overspent row's own activity is
  // negative enough that "Spent X of Y" still applies).
  const eatenPool = c.available - c.activity
  // A commitment envelope that has a LINKED CATEGORY is USER-OWNED (migrated, ADR 2026-07-10) — it
  // behaves like a normal envelope for TARGET editing + rename (its Synced badge/group stay, and it
  // stays undraggable since the sync self-heals group placement). Only a payment envelope or a
  // still-zero-category (bot-managed) synced row keeps the fully-locked "click opens the commitment".
  const userOwned = !c.isPaymentEnvelope && !!(c.categoryIds && c.categoryIds.length)
  const targetLocked = locked && !userOwned
  const barTitle =
    c.activity < -0.005 && eatenPool > 0.005
      ? `Spent ${fSmart(-c.activity)} of ${fSmart(eatenPool)}` +
        (c.target && !targetLocked ? ' — click to edit target' : '')
      : c.target && !targetLocked
        ? 'Edit target'
        : undefined
  // Lucius @-mention harvest — the Plan row's identity IS the envelope (owner decision
  // 2026-07-11, ADR 2026-07-11-envelope-first-class-mention-type): EVERY row emits its own
  // ENVELOPE entity, linked or not, tinted by the envelope's display color. Category
  // mentions come from category surfaces (Manage categories, the register) — never from here.
  const mentionProps = entityRefProps(
    'envelope',
    c.envelopeId,
    c.label,
    undefined,
    undefined,
    c.color,
  )
  const isMobile = useIsMobile()

  // ── Shared nodes — pre-built once, consumed VERBATIM by both the desktop `.pl-trow` row
  // below and the mobile `.pl-mcard` shell (budgetCards.tsx). Only the layout wrapper differs;
  // every sub-component instance (ColorDot/Kebab/ActivityCell/AvailCell/EnvCatsBadge/the target
  // bar) and every status/threshold rule is computed exactly once. ──
  const gripNode = locked ? (
    <span className="pl-grip" aria-hidden="true" />
  ) : (
    <button
      className="pl-grip"
      draggable
      onDragStart={dnd.catStart(gid, c.key)}
      onClick={(e) => e.stopPropagation()}
      aria-label={'Drag ' + c.label}
      title="Drag to reorder"
    >
      <Grip />
    </button>
  )
  // The envelope owns its display colour (handoff item 5) — the dot PATCHes the ENVELOPE now,
  // so even a zero-category envelope (no backing category) is recolorable; only the
  // locked/Commitments treatment stays a static swatch.
  const dotNode = locked ? (
    <span className="pl-cdot-wrap">
      <span className="pl-cdot--btn" aria-hidden="true" style={{ cursor: 'default' }}>
        <ColorIcon icon="envelope" color={c.color} />
      </span>
    </span>
  ) : (
    <ColorDot
      color={c.color}
      onPick={(col) => onRecolor(c.envelopeId, col)}
      label={'Color for ' + c.label}
      icon="envelope"
    />
  )
  const nameNode = renaming ? (
    <InlineEdit
      initial={c.label}
      onSave={(t) => {
        onRename(c, t)
        setRenaming(false)
      }}
      onCancel={() => setRenaming(false)}
    />
  ) : targetLocked ? (
    <span className="pl-clabel" style={{ cursor: 'pointer' }} title={'Open ' + c.label}>
      {c.label}
    </span>
  ) : (
    <span
      className="pl-clabel"
      title="Edit target — double-click to rename"
      onClick={(e) => {
        e.stopPropagation()
        if (clickTimer.current) return
        clickTimer.current = setTimeout(() => {
          clickTimer.current = null
          onEditTarget(c, gid)
        }, 220)
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        if (clickTimer.current) {
          clearTimeout(clickTimer.current)
          clickTimer.current = null
        }
        setRenaming(true)
      }}
    >
      {c.label}
    </span>
  )
  // Commitments-synced row → an explicit chip to open THAT commitment (the row/label click now
  // edits the target on a user-owned commitment envelope, so this restores the deep-link).
  // Never on a Card-Payment row (no commitment behind it).
  const openCommitmentChip = locked && !c.isPaymentEnvelope && (
    <button
      type="button"
      className="pl-crow-open"
      title={'Open ' + c.label + ' commitment'}
      aria-label={'Open ' + c.label + ' commitment'}
      onClick={(e) => {
        e.stopPropagation()
        onOpenCommitment(c)
      }}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M9 7h8v8M17 7 7 17" />
      </svg>
    </button>
  )
  // CONF-1 — the badge is unreachable on a locked (Commitments-synced) row (its ✕/"Add
  // categories…" popover writes envelope ops a bot-managed row 422s on anyway); the
  // zero-category hint is for a user-created zero-category envelope only, same gate.
  const badgeOrHintNode =
    !renaming && !locked && showBadge ? (
      <EnvCatsBadge
        categoryIds={c.categoryIds}
        catLabel={catLabel}
        catColor={catColor}
        onRemove={(key, label) => onSplit(c, key, label)}
        onAddCategories={() => onAddCats(c)}
      />
    ) : !renaming && !locked && c.categoryIds.length === 0 ? (
      <EnvCatsEmptyHint />
    ) : null
  const statusNode =
    !renaming &&
    (c.isPaymentEnvelope ? (
      // A card's payment envelope shows NO overpaid status at all (owner ask, 2026-09-15). It
      // went "S$X sent above what you'd set aside" → "Overpaid" → gone, in one session, which
      // is the tell: paying a card MORE than you'd set aside isn't a problem the way an
      // overspent envelope is — it's just money that left by a route the budget didn't
      // predict. The fact is still on the row, in the Available cell's own signed figure
      // (AvailCell renders `available_for_payment` raw, negative and red), so nothing is
      // hidden; only the editorial label calling it a fault is gone. The state itself is
      // untouched — `paymentOverpaid` still gates the underfunded branch below, because a card
      // that's been overpaid is not simultaneously short.
      paymentOverpaid ? null : paymentUnderfundedNow ? (
        <span className="pl-cstatus under">
          <span className="mask">{fSmart(payUnder)}</span> more needed for this bill
        </span>
      ) : payAvail > 0.005 ? (
        <span className="pl-cstatus funded">Funded</span>
      ) : null
    ) : isOver ? (
      // Just the word (owner ask, 2026-09-15, matching YNAB) — the overspent AMOUNT is already
      // the row's own Available cell, in red, two columns to the right. Repeating it here made
      // the longest, loudest string on the row a duplicate of a figure the reader can see.
      <span className="pl-cstatus over">Overspent</span>
    ) : under ? (
      <span className="pl-cstatus under">
        <span className="mask">{fSmart(c.needed)}</span> more needed
      </span>
    ) : overfunded ? (
      <span className="pl-cstatus overfunded">
        <span className="mask">{fSmart(excess)}</span> over target
      </span>
    ) : fullySpent ? (
      <span className="pl-cstatus spent">Fully Spent</span>
    ) : funded ? (
      // YNAB's "Funded. Spent $5.50 of $200.00" (owner, 2026-09-25) — the bar tooltip's own
      // figures; just "Funded" until something is spent.
      <span className="pl-cstatus funded">
        <span className="pl-cstatus-word">Funded</span>
        {c.activity < -0.005 && eatenPool > 0.005 && (
          <span className="pl-cstatus-detail">
            . Spent <span className="mask">{fSmart(-c.activity)}</span> of{' '}
            <span className="mask">{fSmart(eatenPool)}</span>
          </span>
        )}
      </span>
    ) : null)
  const targetBarNode =
    !renaming && !c.isPaymentEnvelope && (c.target || isOver || fullySpent) ? (
      <span className={'pl-tbar-wrap' + (c.target && !locked ? ' is-target' : '')} title={barTitle}>
        <span className="pl-tbar">
          {isOver ? (
            <>
              <span className="pl-tbar-fill funded" style={{ width: greenPct + '%' }} />
              <span className="pl-tbar-fill over" style={{ width: 100 - greenPct + '%' }} />
            </>
          ) : fullySpent && !c.target ? (
            <span className="pl-tbar-fill spent funded" style={{ width: '100%' }} />
          ) : (
            (() => {
              // YNAB-style "eaten" bar (handoff item 12, prototype budget.jsx:807-819): within the
              // funded length, the share already spent renders hatched; only what's still
              // available stays solid.
              const cls = overfunded ? 'overfunded' : funded ? 'funded' : 'under'
              const spentShare =
                c.activity < -0.005 && eatenPool > 0.005 ? Math.min(1, -c.activity / eatenPool) : 0
              if (!spentShare)
                return <span className={'pl-tbar-fill ' + cls} style={{ width: pct + '%' }} />
              const spentW = pct * spentShare
              return (
                <>
                  <span className={'pl-tbar-fill spent ' + cls} style={{ width: spentW + '%' }} />
                  <span className={'pl-tbar-fill ' + cls} style={{ width: pct - spentW + '%' }} />
                </>
              )
            })()
          )}
        </span>
      </span>
    ) : null
  // `.pl-caction--mobile` (undoes the desktop hover-revealed absolute positioning) is the only
  // bit that varies by layout — everything else about the button is identical either way.
  const actionCls = (base: string) => base + (isMobile ? ' pl-caction--mobile' : '')
  // MOBILE ONLY (owner ask, 2026-09-15): on desktop this was a hover-revealed "Cover S$290.71"
  // / "Fund S$20" chip that appeared over the row under the cursor — unnecessary, since every
  // one of those actions is already reachable from the row itself (the Available pill opens
  // the same move/cover picker, the Assigned cell takes a number directly) and a control that
  // only exists while you happen to be hovering is the hardest kind to find on purpose. The
  // mobile card keeps it: there is no hover there, it renders in normal flow as the card's own
  // full-width CTA, and it is that layout's only route to these actions.
  const actionButtonNode =
    !renaming &&
    isMobile &&
    (isOver ? (
      <button className={actionCls('pl-caction cover')} onClick={() => onAction(c, 'cover')}>
        Cover <span className="money mask">{fSmart(-c.available)}</span>
      </button>
    ) : under ? (
      <button className={actionCls('pl-caction fund')} onClick={() => onAction(c, 'fund')}>
        Fund <span className="money mask">{fSmart(c.needed)}</span>
      </button>
    ) : overfunded ? (
      <button
        className={actionCls('pl-caction')}
        onClick={(e) =>
          setMoveReq({
            t: Date.now(),
            amt: Math.round(excess * 100) / 100,
            rect: e.currentTarget.getBoundingClientRect(),
          })
        }
      >
        Move <span className="money mask">{fSmart(excess)}</span>
      </button>
    ) : null)
  const kebabNode = locked ? (
    <span
      className="pl-kebab-wrap"
      aria-hidden="true"
      style={{ visibility: 'hidden', pointerEvents: 'none' }}
    >
      <button className="pl-kebab" tabIndex={-1}>
        <Grip />
      </button>
    </span>
  ) : (
    <Kebab
      label={c.label + ' options'}
      items={categoryKebabItems(c, otherGroups, allGroups, catLabel, {
        onRename: () => setRenaming(true),
        onEditTarget: () => onEditTarget(c, gid, 'edit'),
        onMoveTo: (groupId) => onMoveTo(c.key, gid, groupId),
        onToggleHide: () => onToggleHide(c.key),
        onDeleteEnvelope: () => onDeleteEnvelope(c),
        onMerge: () => onMerge(c),
        onSplit: (key, label) => onSplit(c, key, label),
        onAddCats: () => onAddCats(c),
      })}
    />
  )
  const assignNode = assigning ? (
    <span className="pl-assign-edit-wrap" onClick={(e) => e.stopPropagation()}>
      <span className="pl-assign-cur">{getCurrencySymbol()}</span>
      <input
        ref={assignRef}
        className="pl-assign-edit"
        inputMode="decimal"
        value={assignDraft}
        onChange={(e) => setAssignDraft(e.target.value.replace(/[^0-9.]/g, ''))}
        onBlur={commitAssign}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commitAssign()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            setAssigning(false)
          }
        }}
      />
    </span>
  ) : (
    <button
      className={'pl-assign mask' + (c.assigned < 0.005 ? ' zero' : '')}
      title="Click to assign"
      onClick={(e) => {
        e.stopPropagation()
        startAssign()
      }}
    >
      {c.assigned < 0.005 ? fBase(0) : fBase(c.assigned)}
    </button>
  )
  const activityNode = (
    <ActivityCell c={c} month={month} onOpenTxn={onOpenTxn} onOpenRegister={onOpenRegister} />
  )
  const availabilityCellNode = c.isPaymentEnvelope ? (
    <AvailCell n={payAvail} />
  ) : (
    <AvailCell c={c} allGroups={allGroups} onTransfer={onTransfer} rta={rta} moveReq={moveReq} />
  )
  const rowClick = (e: React.MouseEvent) => {
    if (
      (e.target as HTMLElement).closest(
        '.pl-grip, .pl-kebab-wrap, .pl-caction, .pl-assign, .pl-alink, .pl-avail--btn, .pl-inline-input, input, button',
      )
    )
      return
    if (targetLocked) {
      ;(c.isPaymentEnvelope ? onOpenCardPayment : onOpenCommitment)(c)
      return
    }
    onEditTarget(c, gid)
  }

  if (isMobile) {
    const mobileCls = [isOver && 'overspent', c.hidden && 'cat-hidden'].filter(Boolean).join(' ')
    // Owner feedback: on mobile, the status line ("$X overspent"/"more needed"/"over target")
    // duplicates BOTH the action button below it (Cover/Fund/Move already shows the same
    // figure) and the Available figure in the numbers row — drop it whenever an action button
    // is present. "Funded" (no button, no $ amount) and the payment-envelope status (a
    // DIFFERENT figure than the PAYMENT cell shows: the shortfall, not what's set aside)
    // aren't actually redundant, so they stay.
    const mobileStatusLine = actionButtonNode ? null : statusNode
    return (
      <MobileEnvelopeCard
        className={mobileCls}
        style={locked ? { cursor: 'pointer' } : undefined}
        draggable
        mentionProps={mentionProps}
        onClick={rowClick}
        dot={dotNode}
        name={nameNode}
        openCommitmentChip={openCommitmentChip}
        badgeOrHint={badgeOrHintNode}
        kebab={kebabNode}
        targetBar={targetBarNode}
        statusLine={mobileStatusLine}
        actionButton={actionButtonNode}
        nums={
          <MobileNumsGrid
            availableLabel={c.isPaymentEnvelope ? 'PAYMENT' : 'AVAILABLE'}
            assigned={assignNode}
            activity={activityNode}
            available={availabilityCellNode}
          />
        }
      />
    )
  }
  return (
    <div
      className={
        'pl-trow pl-crow' +
        (isOver ? ' overspent' : '') +
        (c.hidden ? ' cat-hidden' : '') +
        (locked ? ' pl-crow--locked' : '') +
        (dragging ? ' dragging' : '') +
        (dropHere ? ((over as { edge: string }).edge === 'top' ? ' over-top' : ' over-bottom') : '')
      }
      style={locked ? { cursor: 'pointer' } : undefined}
      // Always draggable for the GLOBAL mention drag (drag row → Lucius dock); reorder DnD
      // stays locked-gated separately (no grip, no rowOver/rowDrop below — and those guard
      // on the module DRAG state, which a mention drag never sets).
      draggable
      {...mentionProps}
      onClick={rowClick}
      {...(locked
        ? {}
        : {
            onDragOver: dnd.rowOver(gid, c.key),
            onDrop: dnd.rowDrop(gid, c.key),
            onDragEnd: dnd.end,
          })}
    >
      <span className="pl-cname">
        {gripNode}
        {dotNode}
        <span className="pl-cmain">
          <span className="pl-cline">
            {nameNode}
            {openCommitmentChip}
            {badgeOrHintNode}
            {statusNode}
          </span>
          {targetBarNode}
        </span>
        {actionButtonNode}
        {kebabNode}
      </span>
      <span className="pl-c-num">{assignNode}</span>
      <span className="pl-c-num">{activityNode}</span>
      <span className="pl-c-num">
        {c.isPaymentEnvelope ? (
          <span
            className="pl-paycell"
            title={
              paymentOverpaid
                ? `You sent ${fSmart(-payAvail)} more to ${c.label} than you had set aside.`
                : paymentUnderfundedNow
                  ? `You have a ${fSmart(paymentOwed)} bill but only ${fSmart(payAvail)} set aside for ${c.label}.`
                  : undefined
            }
          >
            <span className="pl-paycell-label">Payment</span>
            {availabilityCellNode}
          </span>
        ) : (
          availabilityCellNode
        )}
      </span>
    </div>
  )
}

// ── Group block ─────────────────────────────────────────────────────────────────
interface Dnd {
  catStart: (gid: string, key: string) => (e: React.DragEvent) => void
  groupStart: (gid: string) => (e: React.DragEvent) => void
  rowOver: (gid: string, key: string) => (e: React.DragEvent) => void
  rowDrop: (gid: string, key: string) => (e: React.DragEvent) => void
  groupOver: (gid: string) => (e: React.DragEvent) => void
  groupDrop: (gid: string) => (e: React.DragEvent) => void
  end: () => void
}
interface GroupHandlers extends RowHandlers {
  onToggleCollapse: (gid: string) => void
  onRenameGroup: (gid: string, name: string) => void
  onDeleteGroup: (gid: string) => void
  // "Add envelope" (handoff item 4) opens NewEnvelopeModal — replaces the old inline add-row.
  onAddCatStart: (gid: string) => void
  onHideGroup: (gid: string) => void
  onOpenCommitments: () => void
  /** Credit Card Payments (payment_envelope mode) — the group-level Synced pill's target when
   *  every member row is a payment envelope. */
  onOpenCardPayments: () => void
}
export function GroupBlock({
  g,
  allGroups,
  h,
}: {
  g: LocalGroup
  allGroups: LocalGroup[]
  h: GroupHandlers
}) {
  const [renaming, setRenaming] = useState(false)
  const isMobile = useIsMobile()
  const collapsed = g.collapsed
  // Collapsing a mobile card pulls the NEXT card up into the same screen position the finger
  // just tapped — iOS Safari can then deliver a second, spurious click to whatever now sits
  // there the instant it lands, reading as "opens and closes multiple times" on one tap
  // (owner-reported). A short cooldown on this ONE group's own toggle can't stop a ghost click
  // from LANDING on a DIFFERENT group, but it does stop the couple of ways a single physical
  // tap can re-fire the SAME element's handler (bubbled + direct, or a duplicate synthetic
  // event) from applying twice and visibly flipping the state back.
  const lastToggleAt = useRef(0)
  const guardedToggle = () => {
    const now = Date.now()
    if (now - lastToggleAt.current < 350) return
    lastToggleAt.current = now
    h.onToggleCollapse(g.id)
  }
  const groupDragging = DRAG && DRAG.type === 'group' && DRAG.gid === g.id
  const groupOver = h.over && h.over.kind === 'group' && h.over.gid === g.id && !groupDragging
  const others = allGroups.filter((x) => x.id !== g.id)
  const locked = !!g.synced
  // Credit Card Payments (payment_envelope mode) — a synced group whose members are ALL
  // payment envelopes (never mixed with Commitments rows); swaps the Synced pill's target and
  // the Available subtotal's source (UI spec §3.1/§5), no new group field required.
  const isPaymentGroup = g.cats.length > 0 && g.cats.every((c) => c.isPaymentEnvelope)
  const availSubtotal = isPaymentGroup
    ? g.cats.reduce((s, c) => s + (c.availableForPayment ?? 0), 0)
    : g.subtotal.available
  // Name/chevron cluster and the Synced-pill-or-add+kebab cluster are real interactive
  // components (InlineEdit/Kebab) — shared verbatim between the desktop row and the mobile
  // header; only the trailing Cats list wrapper differs (no drag grip on mobile).
  const nameToggleNode =
    renaming && !locked ? (
      <InlineEdit
        initial={g.name}
        onSave={(t) => {
          h.onRenameGroup(g.id, t)
          setRenaming(false)
        }}
        onCancel={() => setRenaming(false)}
      />
    ) : (
      <span className="pl-gtoggle">
        <button
          className="pl-gtog-btn"
          onClick={(e) => {
            e.stopPropagation()
            guardedToggle()
          }}
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand ' + g.name : 'Collapse ' + g.name}
        >
          <span className="pl-chevron">
            <Chevron dir={collapsed ? 'right' : 'down'} size={13} />
          </span>
        </button>
        {locked || isMobile ? (
          // Renaming-by-tapping-the-label is a desktop affordance only — on mobile the whole
          // card (this label included) toggles collapse instead (owner ask); rename is still
          // reachable there via the card's Kebab menu ("Rename group").
          <span className="pl-gname-btn" style={{ cursor: locked ? 'default' : undefined }}>
            {g.name}
          </span>
        ) : (
          <button
            className="pl-gname-btn"
            onClick={(e) => {
              e.stopPropagation()
              setRenaming(true)
            }}
            title={'Rename ' + g.name}
          >
            {g.name}
          </button>
        )}
      </span>
    )
  const trailingNode = locked ? (
    <button
      type="button"
      className="pl-gsynced"
      onClick={(e) => {
        e.stopPropagation()
        ;(isPaymentGroup ? h.onOpenCardPayments : h.onOpenCommitments)()
      }}
      title={
        isPaymentGroup
          ? 'Synced from your credit cards — click to open Accounts. Assign and move money for these here in Plan.'
          : 'Synced from your Commitments — click to open the Commitments tab. Assign and move money for these here in Plan.'
      }
    >
      Synced
    </button>
  ) : (
    <>
      <button
        className="pl-gadd"
        title={'Add an envelope to ' + g.name}
        aria-label="Add envelope"
        onClick={(e) => {
          e.stopPropagation()
          h.onAddCatStart(g.id)
        }}
      >
        <Plus />
      </button>
      <Kebab
        label={g.name + ' group options'}
        items={[
          { label: 'Rename group', onClick: () => setRenaming(true) },
          { label: 'Add envelope', onClick: () => h.onAddCatStart(g.id) },
          { label: 'Hide group', onClick: () => h.onHideGroup(g.id) },
          { divider: true },
          { label: 'Delete group', danger: true, onClick: () => h.onDeleteGroup(g.id) },
        ]}
      />
    </>
  )
  const rowsNode =
    !collapsed &&
    g.cats.map((c) => (
      <CategoryRow
        key={c.key}
        c={c}
        gid={g.id}
        dnd={h.dnd}
        over={h.over}
        otherGroups={others}
        locked={locked}
        month={h.month}
        allGroups={allGroups}
        catLabel={h.catLabel}
        catColor={h.catColor}
        rta={h.rta}
        onOpenCommitment={h.onOpenCommitment}
        onOpenCardPayment={h.onOpenCardPayment}
        onAssign={h.onAssign}
        onAction={h.onAction}
        onRename={h.onRename}
        onRecolor={h.onRecolor}
        onMoveTo={h.onMoveTo}
        onToggleHide={h.onToggleHide}
        onDeleteEnvelope={h.onDeleteEnvelope}
        onEditTarget={h.onEditTarget}
        onTransfer={h.onTransfer}
        onMerge={h.onMerge}
        onSplit={h.onSplit}
        onAddCats={h.onAddCats}
        onOpenTxn={h.onOpenTxn}
        onOpenRegister={h.onOpenRegister}
      />
    ))

  if (isMobile) {
    return (
      <div className="pl-mgroup">
        <MobileGroupHead
          name={nameToggleNode}
          count={<span className="pl-gcount">{g.cats.length}</span>}
          trailing={trailingNode}
          onToggle={guardedToggle}
          collapsed={collapsed}
          nums={
            <MobileNumsGrid
              assigned={<span className="mask">{fBase(g.subtotal.assigned)}</span>}
              activity={<span className="mask">{fBase(g.subtotal.activity)}</span>}
              available={<span className="mask">{fBase(availSubtotal)}</span>}
            />
          }
        />
        {rowsNode && <div className="pl-mgroup-body">{rowsNode}</div>}
      </div>
    )
  }
  return (
    <>
      <div
        className={
          'pl-trow pl-grow' + (groupOver ? ' grow-over' : '') + (groupDragging ? ' dragging' : '')
        }
        // Owner ask, 2026-09-15: the whole header row toggles the group, not just the 13px
        // chevron. Every interactive child (grip, rename, add, kebab, Synced, and the chevron
        // itself) already calls stopPropagation — they were written for the mobile card, whose
        // body has always been tappable; desktop simply never got the handler. The chevron
        // stays a real <button> so keyboard/AT users keep a focusable, labelled control.
        onClick={guardedToggle}
        onDragOver={h.dnd.groupOver(g.id)}
        onDrop={h.dnd.groupDrop(g.id)}
        onDragEnd={h.dnd.end}
      >
        <span className="pl-gname">
          {locked ? (
            <span className="pl-grip pl-grip--group" aria-hidden="true" />
          ) : (
            <button
              className="pl-grip pl-grip--group"
              draggable
              onDragStart={h.dnd.groupStart(g.id)}
              onClick={(e) => e.stopPropagation()}
              aria-label={'Drag group ' + g.name}
              title="Drag to reorder groups"
            >
              <Grip />
            </button>
          )}
          {nameToggleNode}
          <span className="pl-gcount">{g.cats.length}</span>
          {trailingNode}
        </span>
        <span className="pl-c-num pl-gsub mask">{fBase(g.subtotal.assigned)}</span>
        <span className="pl-c-num pl-gsub mask">{fBase(g.subtotal.activity)}</span>
        <span className="pl-c-num pl-gsub mask">{fBase(availSubtotal)}</span>
      </div>
      {rowsNode}
    </>
  )
}

// Fetch the REAL number of ledger transactions across one or more category keys (summed),
// used by the delete-confirmation modals so the "All transactions N" count is honest rather
// than estimated. Returns null while loading; re-fetches whenever the key set changes.
function useCatTxnCount(keys: string[] | null): number | null {
  const [count, setCount] = useState<number | null>(null)
  const keyStr = keys && keys.length ? keys.slice().sort().join(',') : ''
  useEffect(() => {
    if (!keyStr) {
      setCount(null)
      return
    }
    let alive = true
    setCount(null)
    Promise.all(
      keyStr.split(',').map((k) =>
        authFetch(`/api/transactions?category=${encodeURIComponent(k)}&limit=1`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => (d?.total as number) || 0)
          .catch(() => 0),
      ),
    ).then((totals) => {
      if (alive) setCount(totals.reduce((a, b) => a + b, 0))
    })
    return () => {
      alive = false
    }
  }, [keyStr])
  return count
}

// ── Budget sub-tab ──────────────────────────────────────────────────────────────
const RAIL_SHARE_KEY = 'pl-budget-rail-share'

/** The width the rail's share is a fraction OF: viewport minus the left nav minus the docked
 *  Lucius panel — the same basis plan.css's automatic `clamp(...)` formula uses, read from the
 *  same two root variables. */
function railAvailableWidth(): number {
  const root = getComputedStyle(document.documentElement)
  const px = (name: string) => parseFloat(root.getPropertyValue(name)) || 0
  const available = window.innerWidth - px('--lx-sidebar-w') - px('--chat-width')
  return available > 0 ? available : window.innerWidth
}

/** The share as a LIVE CSS expression over those same variables — so when the Lucius dock
 *  opens or closes, `--chat-width` changes and the rail re-scales with no JS involved, exactly
 *  like the automatic width. */
function railShareToCss(share: number, min: number, max: number): string {
  return `clamp(${min}px, calc((100vw - var(--lx-sidebar-w, 52px) - var(--chat-width, 0px)) * ${share.toFixed(4)}), ${max}px)`
}

export function BudgetTab({
  toast,
  onOpenTxn,
  onOpenRegister,
  categoriesVersion = 0,
}: BudgetTabProps) {
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  // Desktop-only (≥1024px) right-hand rail (RFC feat/budget-summary-rail) — gated on the
  // breakpoint hook, not just CSS, so it never mounts (and never fetches/renders its panels)
  // below the threshold.
  const bp = useBreakpoint()
  const showRail = !isMobile && (bp === 'lg' || bp === 'xl')
  // Drag-to-resize the rail (owner request): null = the automatic proportional width
  // (plan.css's `clamp(...)` formula); a number = the SHARE of the available width the user
  // dragged to (0..1 of viewport minus the left nav minus the docked Lucius panel — the same
  // basis the automatic formula uses), remembered across sessions. A share, not pixels
  // (owner, 2026-09-15): an absolute width ignored the Lucius dock opening and closing, so the
  // rail kept its size while the table alone absorbed the whole 400px — now both scale, exactly
  // as the automatic width does. Applied through the same [240, 560] px clamp regardless of how
  // it was set, so a share saved on a wide monitor can't overflow a narrower window. The top of
  // that range stays above the automatic formula's own cap (500px as of 2026-09-15) — a drag
  // range that ended AT the cap would make the handle useless in exactly the case someone
  // reaches for it, on a wide screen where the auto width is already maxed.
  const RAIL_MIN = 240,
    RAIL_MAX = 560
  const [railShare, setRailShare] = useState<number | null>(() => {
    const saved = Number(localStorage.getItem(RAIL_SHARE_KEY) || '')
    return Number.isFinite(saved) && saved > 0 && saved < 1 ? saved : null
  })
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const railElRef = useRef<HTMLElement | null>(null)
  // Callback ref: the legacy-width migration below must run when the rail MOUNTS, and the rail
  // is rendered only after the early loading return (and only when showRail), so an effect
  // keyed on a plain ref would run once with nothing to read and never again.
  const [railNode, setRailNode] = useState<HTMLElement | null>(null)
  const railRefCb = useCallback((el: HTMLElement | null) => {
    railElRef.current = el
    setRailNode(el)
  }, [])
  // One-time migration of the pre-share PIXEL value (key `pl-budget-rail-w`): convert it
  // against the live available width so the rail keeps the size the owner last saw, then drop
  // the old key. Runs when the rail MOUNTS, not in the state initializer — that ran during the
  // tab's first render, before the shell had applied the expanded nav's --lx-sidebar-w, so the
  // basis was the collapsed nav's and the converted rail came out ~50px narrower.
  useEffect(() => {
    if (!railNode || railShare != null) return
    const legacyPx = Number(localStorage.getItem('pl-budget-rail-w') || '')
    if (!Number.isFinite(legacyPx) || legacyPx <= 0) return
    localStorage.removeItem('pl-budget-rail-w')
    const share = Math.min(RAIL_MAX, Math.max(RAIL_MIN, legacyPx)) / railAvailableWidth()
    localStorage.setItem(RAIL_SHARE_KEY, String(share))
    setRailShare(share)
  }, [railNode, railShare])
  const onRailResizeStart = useCallback((e: PointerEvent) => {
    e.preventDefault()
    const startWidth = railElRef.current?.getBoundingClientRect().width ?? RAIL_MIN
    dragRef.current = { startX: e.clientX, startWidth }
    document.body.style.cursor = 'col-resize'
  }, [])
  const onRailResizeMove = useCallback((e: PointerEvent) => {
    if (!dragRef.current) return
    // The handle sits on the rail's LEFT edge — dragging it LEFT (negative delta) widens
    // the rail, since the rail's own right edge is pinned to the viewport.
    const delta = dragRef.current.startX - e.clientX
    const next = Math.min(RAIL_MAX, Math.max(RAIL_MIN, dragRef.current.startWidth + delta))
    setRailShare(next / railAvailableWidth())
  }, [])
  const onRailResizeEnd = useCallback(() => {
    dragRef.current = null
    document.body.style.cursor = ''
    setRailShare((share) => {
      if (share != null) localStorage.setItem(RAIL_SHARE_KEY, String(share))
      return share
    })
  }, [])
  const onRailHandlePointerDown = usePointerResize({
    onStart: onRailResizeStart,
    onMove: onRailResizeMove,
    onEnd: onRailResizeEnd,
  })
  const onRailHandleReset = useCallback(() => {
    setRailShare(null)
    localStorage.removeItem(RAIL_SHARE_KEY)
  }, [])
  const m = useBudgetModel()
  // The "Manage categories" modal is owned by Plan.tsx now (it must stay reachable when another
  // engine is home and this component is unmounted), so a category edit made there refreshes the
  // budget through this counter rather than a direct onMutated={m.refreshAll} wire. Skips the
  // initial render — 0 is "nothing edited yet", not an edit.
  const didMountCats = useRef(false)
  useEffect(() => {
    if (!didMountCats.current) {
      didMountCats.current = true
      return
    }
    m.refreshAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoriesVersion])
  // Resolve a Commitments-synced budget row back to its commitment via the envelope_id FK
  // (planning.sync_commitment_envelopes is the FK's only writer) so a row can deep-link into it.
  const { commitments } = useCommitments()
  const commitmentIdByEnvelope = useMemo(() => {
    const map = new Map<string, string>()
    for (const c of commitments) if (c.envelope_id) map.set(c.envelope_id, c.id)
    return map
  }, [commitments])
  const [filter, setFilter] = useState<string | null>(null)
  const [over, setOver] = useState<OverState>(null)
  const [, force] = useState(0)
  const [addingGroup, setAddingGroup] = useState(false)
  const [newEnvGroup, setNewEnvGroup] = useState<string | null>(null)
  const [delGroup, setDelGroup] = useState<string | null>(null)
  const [delEnvelope, setDelEnvelope] = useState<LocalCat | null>(null)
  const [delEnvelopeBusy, setDelEnvelopeBusy] = useState(false)
  const [removeCatModal, setRemoveCatModal] = useState<{
    c: LocalCat
    categoryId: string
    categoryLabel: string
  } | null>(null)
  const [tgtCat, setTgtCat] = useState<{ cat: LocalCat; gid: string; mode?: string } | null>(null)
  // A card's payment envelope open in the side panel (desktop) — the rail shows ONE detail at a
  // time, so opening either this or a target closes the other.
  const [payKey, setPayKey] = useState<string | null>(null)
  const [mergeCat, setMergeCat] = useState<LocalCat | null>(null)
  const [addCatsFor, setAddCatsFor] = useState<LocalCat | null>(null)
  const [addCatsBusy, setAddCatsBusy] = useState(false)
  const [addToEnvFor, setAddToEnvFor] = useState<{ key: string; label: string } | null>(null)
  const [addToEnvBusy, setAddToEnvBusy] = useState(false)
  // Auto-assign preview (handoff delta #10) — every toolbar mode previews before applying.
  const [autoPrev, setAutoPrev] = useState<AutoPrevResult | null>(null)

  const model = m.model
  // key → label for EVERY category (including a merged envelope's non-primary members,
  // which have no row of their own) — the badge tooltip + chip popover + kebab submenu.
  const catLabel = useCallback(
    (key: string) => m.categories.find((c) => c.id === key)?.label ?? 'Uncategorized',
    [m.categories],
  )
  // key → the CATEGORY's own colour (handoff item 15 — "member dots use each category's own
  // colour", distinct from the envelope's own colour used for the row dot).
  const catColor = useCallback(
    (key: string) => m.categories.find((c) => c.id === key)?.color || stableColor(key),
    [m.categories],
  )
  // Real transaction counts for the group delete-confirmation modal (null = still loading).
  // useCatTxnCount queries /api/transactions?category=... — it needs the REAL category keys
  // (a merged envelope can hold several), never c.key (the envelope id).
  const delGroupCount = useCatTxnCount(
    delGroup
      ? (model.find((x) => x.id === delGroup)?.cats.flatMap((c) => c.categoryIds) ?? null)
      : null,
  )
  const match = (c: LocalCat) => {
    if (filter === 'underfunded') return c.status === 'underfunded'
    // Same predicate the Overspent chip COUNTS with — a cash-overpaid payment envelope is in
    // both or in neither, never a badge of 1 over an empty table.
    if (filter === 'overspent') return isOverspentRow(c)
    if (filter === 'overfunded') return c.status === 'overfunded'
    if (filter === 'funded') return c.available > 0.005
    return true
  }
  const allCats = model.flatMap((g) => g.cats)
  // FEB-05 — hidden-exclusive everywhere (buildFilterCounts/buildStatusTotals below).
  const filterCounts = buildFilterCounts(allCats)
  const unsortedActivity = m.unsortedActivity
  const unbudgetedActivity = m.unbudgetedActivity
  const uncatIncome = m.data?.uncategorized_income_base ?? 0
  const totals = allCats.reduce(
    (a, c) => ({
      assigned: a.assigned + c.assigned,
      activity: a.activity + c.activity,
      available: a.available + c.available,
    }),
    { assigned: 0, activity: 0, available: 0 },
  )
  // The identity's two envelope-side terms (ADR 2026-09-09). `held_in_envelopes` tracks the
  // hero's OPTIMISTIC figure so the breakdown's lines still add up mid-write: the payload's own
  // number plus what our un-refetched writes have done to it (the offset is zeroed on every
  // sync). It is not re-derived from the live rows — that put it one render ahead of the four
  // terms read from `m.data` and fired the breakdown's defect warning on every normal load.
  // `card_spend_not_covered` needs no offset at all: it moves only with card-spend ACTIVITY,
  // which no assignment of ours creates or retires.
  const heldInEnvelopes = Math.round(((m.data?.held_in_envelopes ?? 0) + m.heldOffset) * 100) / 100
  // No offset, for the same reason `card_spend_not_covered` needs none: it moves only with
  // uncategorized/unlinked CASH ACTIVITY, which no assignment of ours creates or retires.
  const heldOutsideEnvelopes = m.data?.held_outside_envelopes ?? 0
  const cardSpendNotCovered = m.data?.card_spend_not_covered ?? 0
  const hiddenGroups = model.filter((g) => g.hidden)
  const hiddenCatGroups = model
    .filter((g) => !g.hidden)
    .map((g) => ({ id: g.id, name: g.name, cats: g.cats.filter((c) => c.hidden) }))
    .filter((g) => g.cats.length)
  const hiddenCount =
    hiddenGroups.reduce((a, g) => a + g.cats.length, 0) +
    hiddenCatGroups.reduce((a, g) => a + g.cats.length, 0)
  const display = model
    .filter((g) => !g.hidden)
    .map((g) => ({ ...g, cats: g.cats.filter((c) => !c.hidden && match(c)) }))
    .filter((g) => filter == null || g.cats.length)
    // A synced (bot-managed) group with nothing in it yet is empty machinery, not something a
    // user built on purpose — unlike a manual group they're mid-way through filling, which
    // stays visible. Hide it outright rather than show a header over zero rows (owner ask,
    // 2026-09-15).
    .filter((g) => !g.synced || g.cats.length)
    // Commitments group is always pinned to the top, above all other categories.
    .sort((a, b) => (a.synced ? -1 : 0) - (b.synced ? -1 : 0))

  const { overspentCount, overspentAmount, underfundedCount, underfundedAmount } =
    buildStatusTotals(allCats)
  // FEB-05 — an auto-assign hint promises exactly what the write will TOUCH, and
  // buildClientAutoSteps never moves money into a locked payment envelope. So the two
  // Auto-assign surfaces (toolbar menu + desktop rail) count the payment-free rows, while the
  // header badge/chip/coaching line above them count a cash-overpaid card the way YNAB does.
  const cardDebtUnderfunded = buildCardDebtUnderfunded(allCats)
  const autoAssignB = {
    ...buildStatusTotals(allCats.filter((c) => !c.isPaymentEnvelope)),
    totals,
    // Folded into the ONE "Underfunded" hint (owner ask, 2026-09-15) — the action itself still
    // only funds envelope targets, buildClientAutoSteps already excludes payment envelopes.
    cardUnderfundedAmount: cardDebtUnderfunded.amount,
  }
  const ageOfMoney = m.health?.age_of_money

  // ── Lucius screen facts + prompts (reuses the existing dock) ──
  useRegisterScreenFacts(() => {
    if (!m.data) return []
    const facts: [string, string][] = [
      ['Ready to Assign', fBase(m.rta)],
      ['Overspent', overspentCount ? `${overspentCount} • ${fBase(overspentAmount)}` : 'none'],
      [
        'Underfunded',
        underfundedCount ? `${underfundedCount} • ${fBase(underfundedAmount)}` : 'none',
      ],
      ['Total available', fBase(totals.available)],
    ]
    if (ageOfMoney?.forecastable && ageOfMoney.age_days != null)
      facts.push(['Age of money', `${ageOfMoney.age_days} days`])
    if (Math.abs(unsortedActivity) > 0.005)
      facts.push(['Uncategorized (no category)', fBase(Math.abs(unsortedActivity))])
    if (Math.abs(unbudgetedActivity) > 0.005)
      facts.push(['Unbudgeted (no envelope)', fBase(Math.abs(unbudgetedActivity))])
    return facts
  })
  useRegisterScreenPrompts(() => [
    'How much do I have left to assign?',
    'Which categories are overspent?',
    'Help me fund all my targets',
    "What's my age of money?",
  ])

  // ── drag-and-drop ──
  function cleanup() {
    DRAG = null
    setOver(null)
    force((n) => n + 1)
  }
  // Commitments (synced) is a locked, backend-managed group — a row dropped onto its header
  // could never be moved back out (its rows are undraggable, its kebab is hidden), so it's
  // never a valid drop target for either a category move or a group reorder.
  const isSyncedGroup = (gid: string) => !!model.find((g) => g.id === gid)?.synced
  const dnd: Dnd = {
    catStart: (gid, key) => (e) => {
      e.stopPropagation()
      DRAG = { type: 'cat', gid, key }
      e.dataTransfer.effectAllowed = 'move'
      try {
        e.dataTransfer.setData('text/plain', key)
      } catch {
        /* noop */
      }
      const row = (e.currentTarget as HTMLElement).closest('.pl-crow')
      if (row) e.dataTransfer.setDragImage(row, 24, 18)
      requestAnimationFrame(() => force((n) => n + 1))
    },
    groupStart: (gid) => (e) => {
      DRAG = { type: 'group', gid }
      e.dataTransfer.effectAllowed = 'move'
      try {
        e.dataTransfer.setData('text/plain', gid)
      } catch {
        /* noop */
      }
      const row = (e.currentTarget as HTMLElement).closest('.pl-grow')
      if (row) e.dataTransfer.setDragImage(row, 24, 16)
      requestAnimationFrame(() => force((n) => n + 1))
    },
    rowOver: (gid, key) => (e) => {
      if (!DRAG || DRAG.type !== 'cat') return
      e.preventDefault()
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      const edge = e.clientY - r.top < r.height / 2 ? 'top' : 'bottom'
      setOver((o) =>
        o && o.kind === 'cat' && o.key === key && o.edge === edge
          ? o
          : { kind: 'cat', gid, key, edge },
      )
    },
    rowDrop: (gid, key) => (e) => {
      if (!DRAG) return
      e.preventDefault()
      if (DRAG.type === 'cat')
        m.dropCatOnRow(
          { gid: DRAG.gid, key: DRAG.key! },
          gid,
          key,
          over && over.kind === 'cat' ? { edge: over.edge } : null,
          toast,
        )
      cleanup()
    },
    groupOver: (gid) => (e) => {
      if (!DRAG || isSyncedGroup(gid)) return
      e.preventDefault()
      setOver((o) => (o && o.kind === 'group' && o.gid === gid ? o : { kind: 'group', gid }))
    },
    groupDrop: (gid) => (e) => {
      if (!DRAG || isSyncedGroup(gid)) return
      e.preventDefault()
      if (DRAG.type === 'group') m.reorderGroupBefore(DRAG.gid, gid, toast)
      else if (DRAG.type === 'cat') m.moveCatBetween(DRAG.key!, gid, null, toast)
      cleanup()
    },
    end: () => cleanup(),
  }

  // "Delete envelope…" (handoff item 2) — always confirms via DeleteEnvelopeModal (the copy
  // itself explains that categories/transactions are untouched, so there's no history-based
  // fast path like the old category-delete had).
  function requestDeleteEnvelope(c: LocalCat) {
    setDelEnvelope(c)
  }
  async function confirmDeleteEnvelope() {
    if (!delEnvelope) return
    setDelEnvelopeBusy(true)
    try {
      await m.deleteEnvelope(delEnvelope.envelopeId, delEnvelope.label, toast)
      setDelEnvelope(null)
    } catch {
      /* toast already shown by the model; keep the modal open to retry/cancel */
    } finally {
      setDelEnvelopeBusy(false)
    }
  }
  function requestDeleteGroup(gid: string) {
    if (model.length <= 1) {
      toast('Keep at least one group')
      return
    }
    const g = model.find((x) => x.id === gid)
    if (!g) return
    // The backend always moves members to "Others" — no picker to gate on, unlike delete-cat.
    const hasHistory = g.cats.some(
      (c) =>
        Math.abs(c.activity) > 0.005 ||
        Math.abs(c.assigned) > 0.005 ||
        Math.abs(c.available) > 0.005,
    )
    if (!hasHistory) {
      m.deleteGroupConfirmed(gid, null, toast)
      return
    }
    setDelGroup(gid)
  }

  const liveB: SummaryBudget = {
    readyToAssign: m.rta,
    onBudgetCash: m.data?.on_budget_cash ?? null,
    heldInEnvelopes,
    heldOutsideEnvelopes,
    cardSpendNotCovered,
    baseline: m.data?.pre_budget_baseline ?? 0,
    rolloverStart: m.data?.rollover_start ?? null,
    hasForeignCash: m.data?.has_foreign_cash ?? false,
    assignedInFuture: m.data?.assigned_in_future_months ?? 0,
    flow: m.data?.flow ?? null,
    prevMonthLabel: m.data?.nav?.prev ? monthLabel(m.data.nav.prev) : '',
    totalAssigned: totals.assigned,
    ageOfMoney: ageOfMoney?.age_days ?? null,
    ageForecastable: !!ageOfMoney?.forecastable,
    overspentCount,
    overspentAmount,
    underfundedCount,
    underfundedAmount,
    totalAvailable: totals.available,
  }
  const FILTERS: [string, string][] = [
    ['all', 'All'],
    ['underfunded', 'Underfunded'],
    ['overspent', 'Overspent'],
    ['overfunded', 'Overfunded'],
    ['funded', 'Money available'],
  ]

  // sticky-header horizontal-scroll mirror
  const tbodyWrapRef = useRef<HTMLDivElement>(null)
  const theadInnerRef = useRef<HTMLDivElement>(null)
  const syncHead = () => {
    const body = tbodyWrapRef.current,
      head = theadInnerRef.current
    if (body && head) head.style.transform = 'translateX(' + -body.scrollLeft + 'px)'
  }
  useEffect(syncHead)

  // Auto-assign preview (handoff delta #10) — shared by the toolbar's own picker AND the RTA
  // popover's "Fund N underfunded targets" quick action (handoff delta #8): both just ask for
  // a preview by mode label; the modal below is the ONE confirm surface either way.
  async function runAutoAssign(label: string) {
    try {
      const prev = await m.buildAutoPreview(label)
      if (!prev) {
        // "Underfunded" displays envelope-underfunded + card debt combined (owner ask,
        // 2026-09-15) so there's one number, matching real YNAB — but the action itself only
        // ever funds envelope TARGETS (buildClientAutoSteps excludes payment envelopes on
        // purpose; card debt needs an actual payment, not an assignment). When the whole
        // total was card debt, the generic "Nothing to assign" read as broken — the number on
        // screen said $X, the click did nothing, with no explanation why.
        toast(
          label === 'Underfunded' && cardDebtUnderfunded.amount > 0.005
            ? 'That total is card debt — pay it down in Credit Card Payments, not by assigning'
            : 'Nothing to assign',
        )
        return
      }
      setAutoPrev(prev)
    } catch {
      toast('Failed to preview auto-assign')
    }
  }

  const h: GroupHandlers = {
    dnd,
    over,
    month: m.month,
    allGroups: model,
    catLabel,
    catColor,
    rta: m.rta,
    onOpenTxn,
    onOpenRegister,
    onAssign: (key, value) => m.assignTo(key, value, toast),
    onAction: (c, kind) => m.moneyOp(c.key, kind, undefined, toast),
    onTransfer: (from, to, amount) => m.moveBetweenEnvelopes(from, to, amount, toast),
    // A true 1:1 row still renames its category (renameCat needs the REAL category key —
    // c.key is the envelope id, never pass that); an N>1 (merged) row OR a zero-category
    // envelope row (RFC envelope⊥category full split — "Add envelope", nothing linked yet)
    // renames the ENVELOPE directly (P4 spec §2.4) — there's no category key to rename for
    // either, and the categories underneath (if any) keep their own labels.
    onRename: (c, label) =>
      c.categoryIds.length === 1
        ? m.renameCat(c.categoryIds[0]!, label, toast)
        : m.renameEnvelope(c.envelopeId, label, toast),
    // The row dot now owns the ENVELOPE'S colour (handoff item 5), not the category's.
    onRecolor: (envelopeId, color) => m.recolorEnvelope(envelopeId, color, toast),
    onMoveTo: (key, _from, to) => {
      m.moveCatBetween(key, to, null, toast)
      toast('Moved to ' + (model.find((g) => g.id === to)?.name ?? ''))
    },
    onToggleHide: (key) => m.toggleHide(key, toast),
    onDeleteEnvelope: requestDeleteEnvelope,
    onEditTarget: (c, gid, mode) => {
      setPayKey(null)
      setTgtCat({ cat: c, gid, mode })
    },
    onMerge: (c) => setMergeCat(c),
    // "Remove from envelope" (handoff item 1) always confirms first — never an immediate write.
    onSplit: (c, key, label) => setRemoveCatModal({ c, categoryId: key, categoryLabel: label }),
    onAddCats: (c) => setAddCatsFor(c),
    // Deep-link the SPECIFIC commitment (resolve envelope_id → commitment id); fall back to the
    // list when the row isn't a synced commitment (or the FK hasn't loaded yet).
    onOpenCommitment: (c: LocalCat) => {
      const cid = commitmentIdByEnvelope.get(c.envelopeId)
      navigate({ to: '/commitments', search: (cid ? { open: cid } : {}) as never })
    },
    onOpenCommitments: () => navigate({ to: '/commitments', search: { open: undefined } }),
    // Credit Card Payments (payment_envelope mode): a card's row opens its payment detail, like
    // YNAB's inspector (owner, 2026-09-25) — in the side panel, or a modal without one (a phone
    // got no explanation or Assign otherwise, 2026-09-28). The group header opens Accounts (no
    // per-card deep link: the row carries no account_id).
    onOpenCardPayment: (c: LocalCat) => {
      setTgtCat(null)
      setPayKey(c.key)
    },
    onOpenCardPayments: () =>
      navigate({ to: '/accounts', search: { account: undefined, txn: undefined } }),
    onToggleCollapse: m.toggleCollapse,
    onRenameGroup: (gid, name) => m.renameGroup(gid, name, toast),
    onDeleteGroup: requestDeleteGroup,
    // "Add envelope" (handoff item 4) opens NewEnvelopeModal — never flips an already-expanded
    // group back to collapsed (the group must stay visible for the new row to land in).
    onAddCatStart: (gid) => {
      if (filter) setFilter(null)
      if (model.find((g) => g.id === gid)?.collapsed) m.toggleCollapse(gid)
      setNewEnvGroup(gid)
    },
    onHideGroup: (gid) => m.hideGroup(gid, toast),
  }

  const tgtNote = tgtCat ? (m.notes?.[tgtCat.cat.key] ?? '') : ''
  // Shared with BOTH presentations of the target editor (issue #3, feat/budget-summary-rail
  // follow-up) — the floating PlanningModal (root-level, !showRail) and the rail-inline Panel
  // (inside <aside>, showRail) wire the exact same handlers to the exact same `tgtCat`; only
  // the CHROME around the form differs, never this data/logic.
  const payCat = (payKey && allCats.find((c) => c.key === payKey)) || null
  const payPanelProps = (c: LocalCat) => ({
    c,
    month: m.month,
    note: m.notes?.[c.key] ?? '',
    onSaveNote: (text: string) => m.setNote(c.key, text),
    onBack: () => setPayKey(null),
    onOpenAccounts: () =>
      navigate({ to: '/accounts', search: { account: undefined, txn: undefined } }),
    onAssign: (value: number) => m.assignTo(c.key, value, toast),
  })
  const targetEditorProps = tgtCat && {
    cat: allCats.find((c) => c.key === tgtCat.cat.key) || tgtCat.cat,
    initialMode: tgtCat.mode,
    onCancel: () => setTgtCat(null),
    onSave: (t: Parameters<typeof m.saveTarget>[1]) => {
      m.saveTarget(tgtCat.cat.key, t, toast)
      setTgtCat(null)
    },
    onRemove: () => {
      m.removeTarget(tgtCat.cat.key, toast)
      setTgtCat(null)
    },
    onMoney: (key: string, kind: string, amount?: number) => m.moneyOp(key, kind, amount, toast),
    onFund: (msg: string) => toast(msg),
    onSnooze: (snooze: boolean) =>
      m.snoozeTarget(allCats.find((c) => c.key === tgtCat.cat.key) || tgtCat.cat, snooze, toast),
    note: tgtNote,
    onSaveNote: (text: string) => m.setNote(tgtCat.cat.key, text),
  }
  const newEnvGroupName = newEnvGroup ? (model.find((g) => g.id === newEnvGroup)?.name ?? '') : ''
  const outsideData: OutsideEnvelopesData = {
    unsortedActivity,
    unsortedAvailable: m.unsortedAvailable,
    uncatIncome,
    unbudgeted: (m.unbudgetedGroup?.categories ?? []).map((c) => ({
      ...c,
      color: catColor(c.key),
    })),
  }
  const reviewUnsortedInAccounts = () => onOpenRegister?.({ uncategorized: true, month: m.month })
  const labelUncatIncome = () =>
    onOpenRegister?.({ uncategorized: true, kind: 'income', month: m.month })

  if (m.loading && !m.data)
    return (
      <div className="pl-sub">
        <TabSkeleton variant="budget" label="Loading your budget…" header={false} />
      </div>
    )

  return (
    <div className={'pl-sub' + (isMobile ? '' : ' pl-sub--rta-float')} style={{ gap: 0 }}>
      {/* Full width, above the table+rail split entirely (owner ask, 2026-09-14 — matches
          YNAB's own layout: the RTA hero spans the whole page, the table and its side panel
          sit below it, not beside it at the same height). */}
      <Summary
        b={liveB}
        month={m.monthLabel}
        onPrev={m.onPrev}
        onNext={m.onNext}
        canPrev={!!m.data?.nav?.can_go_back}
        filter={filter}
        setFilter={setFilter}
        showBrief={true}
        onFundUnderfunded={() => runAutoAssign('Underfunded')}
      />
      {!isMobile && (
        <RtaFloat
          rta={liveB.readyToAssign}
          overspentCount={liveB.overspentCount}
          underfundedCount={liveB.underfundedCount}
          totalAvailable={liveB.totalAvailable}
          filter={filter}
          setFilter={setFilter}
        />
      )}
      {/* Full width, above BOTH columns (owner ask, 2026-09-15): the rail pins under this row
          and, when taller than the room below it, travels under it and the hero like content
          under a header — scoped to the table column, the rail's rows crossed the button row
          mid-scroll. DESKTOP 21px/20px: the box pins 1px INTO the hero (the shared `- 1px`
          sticky formula), so 21px reads as 20px of gap above the buttons; the 20px below is
          the gap to the table header AND to the rail's card, both of which pin flush under
          this box's bottom via its measured height (--pl-tools-h, Plan.tsx).
          MOBILE: the toolbar DOES pin — under the masthead (`.pl-mast-sticky`, whose apron is
          only 4px on this flat-mast page), not under the hero, which scrolls away. Two rounds
          of "zero the top padding, the hero's 16px margin is the whole gap" (2026-09-15) got
          the AT-REST spacing right (16px above, 16px below) but left the pinned toolbar 3px
          under the segment control (owner-caught, round 3). So: 16px of top padding for the
          pinned state, pulled up by a matching negative margin so at rest the box butts
          against the hero's margin and the visible gap stays exactly 16px. */}
      <div
        className="pl-toolbar"
        style={isMobile ? { padding: '16px 0', marginTop: '-16px' } : { padding: '21px 0 20px' }}
      >
        <div className="pl-filter">
          <div className="pl-filter-chips">
            {FILTERS.map(([key, label]) => (
              <button
                key={key}
                className={'pl-fchip' + ((filter || 'all') === key ? ' on' : '')}
                onClick={() => setFilter(key === 'all' ? null : key)}
              >
                {label}
                <span className="c">{filterCounts[key]}</span>
              </button>
            ))}
          </div>
          <FilterSelect
            filters={FILTERS}
            counts={filterCounts}
            value={filter}
            onChange={setFilter}
          />
        </div>
        <div className="pl-toolbar-actions">
          <AutoAssign onPick={(label) => runAutoAssign(label)} b={autoAssignB} />
          {/* No hover-revealed "+ Add group" header on mobile (there's no table header at
            all) — a persistent button keeps group creation reachable on a phone. Lives in
            the toolbar row itself (not its own line below) so the filter/Auto-assign/Add
            group controls share one line instead of pushing the card list down further. */}
          {isMobile && (
            <button
              type="button"
              className="pl-madd-group"
              onClick={() => setAddingGroup(true)}
              title="Add an envelope group"
            >
              {/* Owner feedback: "Add" is redundant with the leading + icon — "Group" alone
                reads the same, one word shorter, matching the compact toolbar row. */}
              <Plus size={11} /> Group
            </button>
          )}
        </div>
      </div>
      <div
        // `is-switching` while the month query is bridging with the PREVIOUS month's figures
        // (useBudget.monthSwitching): dimmed and inert for the round-trip. Keeping the layout
        // mounted is what killed the flicker; dimming it is what stops the old numbers from
        // reading as the new month's, and `pointer-events: none` is what stops an assign
        // landing against a figure that is about to change (owner ask, 2026-09-15).
        className={
          'pl-budget-layout' +
          (showRail ? ' has-rail' : '') +
          (m.monthSwitching ? ' is-switching' : '')
        }
        style={
          showRail && railShare != null
            ? ({ '--pl-rail-w': railShareToCss(railShare, RAIL_MIN, RAIL_MAX) } as CSSProperties)
            : undefined
        }
      >
        <div className="pl-budget-main">
          {isMobile ? (
            <div className="pl-mlist">
              {addingGroup && (
                <div className="pl-mcard pl-maddrow">
                  <InlineEdit
                    placeholder="New group name…  (Enter to add, Esc to cancel)"
                    onSave={(t) => {
                      m.addGroup(t, toast)
                      setAddingGroup(false)
                    }}
                    onCancel={() => setAddingGroup(false)}
                  />
                </div>
              )}
              {display.map((g) => (
                <GroupBlock key={g.id} g={g} allGroups={model} h={h} />
              ))}
              {display.length === 0 && (
                <div className="pl-trow pl-emptyrow">
                  {filter ? (
                    <span>
                      Nothing matches this filter —{' '}
                      <button className="pl-empty-clear" onClick={() => setFilter(null)}>
                        clear
                      </button>
                    </span>
                  ) : (
                    <span>
                      No envelopes yet —{' '}
                      <button className="pl-empty-clear" onClick={() => setAddingGroup(true)}>
                        add a group
                      </button>
                      , then use its &ldquo;+&rdquo; to create your first envelope.
                    </span>
                  )}
                </div>
              )}
              {!filter && (
                <OutsideEnvelopes
                  data={outsideData}
                  month={m.month}
                  onOpenTxn={onOpenTxn}
                  onOpenRegister={onOpenRegister}
                  onReviewInAccounts={reviewUnsortedInAccounts}
                  onLabelIncome={labelUncatIncome}
                  onAddToEnvelope={(key, label) => setAddToEnvFor({ key, label })}
                />
              )}
              {hiddenCount > 0 && (
                <HiddenRow
                  hiddenGroups={hiddenGroups}
                  catGroups={hiddenCatGroups}
                  onUnhideCat={(key) => m.toggleHide(key, toast)}
                  onUnhideGroup={(gid) => m.unhideGroup(gid, toast)}
                  onUnhideOneFromGroup={(gid, key) => m.unhideOneFromGroup(gid, key, toast)}
                  onShowAll={() => m.unhideAll(toast)}
                />
              )}
              <div className="pl-mtotal">
                <span className="pl-mtotal-lbl">Total</span>
                <MobileNumsGrid
                  assigned={<span className="pl-fig sub mask">{fBase(totals.assigned)}</span>}
                  activity={<span className="pl-fig sub mask">{fBase(totals.activity)}</span>}
                  available={<AvailCell n={totals.available} />}
                />
              </div>
            </div>
          ) : (
            <div className="pl-tablewrap">
              <div className="pl-thead-wrap">
                <div className="pl-thead-clip">
                  <div className="pl-table-inner" ref={theadInnerRef}>
                    <div className="pl-trow pl-thead">
                      <span className="pl-thead-cat">
                        Envelope
                        <button
                          className="pl-thead-addbtn"
                          onClick={() => setAddingGroup(true)}
                          title="Add an envelope group"
                        >
                          <Plus size={11} /> Add group
                        </button>
                      </span>
                      <span className="num">Assigned</span>
                      <span className="num">Activity</span>
                      <span className="num">Available</span>
                    </div>
                  </div>
                </div>
              </div>
              <div className="pl-tbody-wrap" ref={tbodyWrapRef} onScroll={syncHead}>
                <div className="pl-table-inner">
                  <div className="pl-tbody">
                    {addingGroup && (
                      <div className="pl-trow pl-addrow pl-addrow--grp">
                        <span className="pl-gname">
                          <span className="pl-grip pl-grip--group" aria-hidden="true" />
                          <InlineEdit
                            placeholder="New group name…  (Enter to add, Esc to cancel)"
                            onSave={(t) => {
                              m.addGroup(t, toast)
                              setAddingGroup(false)
                            }}
                            onCancel={() => setAddingGroup(false)}
                          />
                        </span>
                      </div>
                    )}
                    {display.map((g) => (
                      <GroupBlock key={g.id} g={g} allGroups={model} h={h} />
                    ))}
                    {display.length === 0 && (
                      <div className="pl-trow pl-emptyrow">
                        {filter ? (
                          <span>
                            Nothing matches this filter —{' '}
                            <button className="pl-empty-clear" onClick={() => setFilter(null)}>
                              clear
                            </button>
                          </span>
                        ) : (
                          <span>
                            No envelopes yet —{' '}
                            <button className="pl-empty-clear" onClick={() => setAddingGroup(true)}>
                              add a group
                            </button>
                            , then use its &ldquo;+&rdquo; to create your first envelope.
                          </span>
                        )}
                      </div>
                    )}
                    {/* Outside envelopes only makes sense against the FULL book — a status filter
                  (Overspent/Underfunded/…) narrows the envelope rows above, and this derived
                  section has no status of its own to filter by (prototype budget.jsx:1495). */}
                    {!filter && (
                      <OutsideEnvelopes
                        data={outsideData}
                        month={m.month}
                        onOpenTxn={onOpenTxn}
                        onOpenRegister={onOpenRegister}
                        onReviewInAccounts={reviewUnsortedInAccounts}
                        onLabelIncome={labelUncatIncome}
                        onAddToEnvelope={(key, label) => setAddToEnvFor({ key, label })}
                      />
                    )}
                    {hiddenCount > 0 && (
                      <HiddenRow
                        hiddenGroups={hiddenGroups}
                        catGroups={hiddenCatGroups}
                        onUnhideCat={(key) => m.toggleHide(key, toast)}
                        onUnhideGroup={(gid) => m.unhideGroup(gid, toast)}
                        onUnhideOneFromGroup={(gid, key) => m.unhideOneFromGroup(gid, key, toast)}
                        onShowAll={() => m.unhideAll(toast)}
                      />
                    )}
                    <div className="pl-trow pl-trow--total">
                      <span className="pl-tname">Total</span>
                      <span className="pl-c-num pl-fig sub mask">{fBase(totals.assigned)}</span>
                      <span className="pl-c-num pl-fig sub mask">{fBase(totals.activity)}</span>
                      <span className="pl-c-num">
                        <AvailCell n={totals.available} />
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
        {showRail && (
          /* The column wrapper is the grid item; the rail inside it is the scroll pane. The
             drag handle hangs OUTSIDE the rail's box (negative left, into the grid gap), so it
             lives on the wrapper — inside a scroll pane it would be clipped. */
          <div className="pl-budget-rail-col">
            <div
              className="pl-budget-rail-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize rail"
              title="Drag to resize · double-click to reset"
              onPointerDown={onRailHandlePointerDown}
              onDoubleClick={onRailHandleReset}
            />
            <aside className="pl-budget-rail" aria-label="This month at a glance" ref={railRefCb}>
              {/* .pl-pane-body is the rail's scroll content as ONE box, so the workspace can
                  offset it (a transform) while the chrome above scrolls away — see plan.css. */}
              <div className="pl-pane-body">
                {/* Selecting a category's target swaps the rail to its detail (YNAB's own
                behavior, issue #3) — replacing Summary/Auto-assign for as long as it's open,
                rather than floating a modal on top of them. */}
                {targetEditorProps ? (
                  // Keyed per envelope + open mode: its form state is seeded once, on mount, so
                  // reusing one instance across envelopes carried the previous draft over.
                  <TargetEditor
                    key={targetEditorProps.cat.key + ':' + (targetEditorProps.initialMode ?? '')}
                    inline
                    {...targetEditorProps}
                  />
                ) : payCat ? (
                  <CardPaymentPanel key={payCat.key} {...payPanelProps(payCat)} />
                ) : (
                  <>
                    <BudgetSummaryPanel
                      onBudgetCash={liveB.onBudgetCash}
                      monthLabel={m.monthLabel}
                      totalAssigned={totals.assigned}
                      totalActivity={totals.activity}
                      totalAvailable={totals.available}
                      totalTargets={buildTargetsTotal(allCats)}
                    />
                    <BudgetAutoAssignPanel
                      onPick={(label) => runAutoAssign(label)}
                      b={autoAssignB}
                    />
                    <BudgetFutureAssignedPanel data={m.data?.assigned_in_future_by_month ?? []} />
                  </>
                )}
              </div>
            </aside>
          </div>
        )}
      </div>

      {delGroup &&
        (() => {
          const g = model.find((x) => x.id === delGroup)
          if (!g) return null
          return (
            <ConfirmDeleteGroupModal
              groupName={g.name}
              count={g.cats.length}
              txns={delGroupCount ?? undefined}
              onCancel={() => setDelGroup(null)}
              onConfirm={() => {
                m.deleteGroupConfirmed(g.id, 'Others', toast)
                setDelGroup(null)
              }}
            />
          )
        })()}
      {delEnvelope && (
        <DeleteEnvelopeModal
          envelopeId={delEnvelope.envelopeId}
          month={m.month}
          envelopeName={delEnvelope.label}
          categoryLabels={delEnvelope.categoryIds.map(catLabel)}
          busy={delEnvelopeBusy}
          onCancel={() => {
            if (!delEnvelopeBusy) setDelEnvelope(null)
          }}
          onConfirm={confirmDeleteEnvelope}
        />
      )}
      {removeCatModal && (
        <RemoveCategoryModal
          envelopeName={removeCatModal.c.label}
          categoryLabel={removeCatModal.categoryLabel}
          sole={removeCatModal.c.categoryIds.length === 1}
          onCancel={() => setRemoveCatModal(null)}
          onConfirm={() => {
            m.splitCategory(
              removeCatModal.c.envelopeId,
              removeCatModal.categoryId,
              removeCatModal.categoryLabel,
              removeCatModal.c.label,
              toast,
            )
            setRemoveCatModal(null)
          }}
        />
      )}
      {newEnvGroup && (
        <NewEnvelopeModal
          groupName={newEnvGroupName}
          allGroups={model}
          categories={m.categories}
          hiddenKeys={m.hiddenKeys}
          rowCount={model.reduce((n, g) => n + g.cats.length, 0)}
          onCancel={() => setNewEnvGroup(null)}
          onConfirm={(name, color, categoryIds) => {
            m.addEnvelopeLocal(newEnvGroup, name, color, categoryIds, toast)
            setNewEnvGroup(null)
          }}
        />
      )}
      {mergeCat && (
        <MergeEnvelopeModal
          source={mergeCat}
          allGroups={model}
          onCancel={() => setMergeCat(null)}
          onConfirm={(targetId, targetName) => {
            m.mergeEnvelope(mergeCat.envelopeId, targetId, mergeCat.label, targetName, toast)
            setMergeCat(null)
          }}
        />
      )}
      {addCatsFor && (
        <AddCategoriesModal
          target={addCatsFor}
          allGroups={model}
          categories={m.categories}
          hiddenKeys={m.hiddenKeys}
          catLabel={catLabel}
          busy={addCatsBusy}
          onCancel={() => {
            if (!addCatsBusy) setAddCatsFor(null)
          }}
          onConfirm={async (picks) => {
            setAddCatsBusy(true)
            try {
              await m.addCategoriesToEnvelope(addCatsFor.envelopeId, addCatsFor.label, picks, toast)
            } finally {
              setAddCatsBusy(false)
              setAddCatsFor(null)
            }
          }}
        />
      )}
      {addToEnvFor && (
        <AddToEnvelopeModal
          categoryLabel={addToEnvFor.label}
          allGroups={model}
          busy={addToEnvBusy}
          onCancel={() => {
            if (!addToEnvBusy) setAddToEnvFor(null)
          }}
          onConfirm={async (targetId, targetName) => {
            setAddToEnvBusy(true)
            try {
              await m.linkCategoryToEnvelope(
                addToEnvFor.key,
                targetId,
                addToEnvFor.label,
                targetName,
                toast,
              )
            } finally {
              setAddToEnvBusy(false)
              setAddToEnvFor(null)
            }
          }}
        />
      )}
      {/* Rail present (showRail): the SAME editor renders inline inside <aside> above instead —
          never both, and never the floating modal once a rail exists. */}
      {targetEditorProps && !showRail && (
        <TargetEditor
          key={targetEditorProps.cat.key + ':' + (targetEditorProps.initialMode ?? '')}
          {...targetEditorProps}
        />
      )}
      {payCat && !targetEditorProps && !showRail && (
        <CardPaymentPanel key={payCat.key} inline={false} {...payPanelProps(payCat)} />
      )}
      {autoPrev && (
        <AutoAssignPreviewModal
          prev={autoPrev}
          onCancel={() => setAutoPrev(null)}
          onConfirm={() => {
            m.applyAutoPreview(autoPrev, toast)
            setAutoPrev(null)
          }}
        />
      )}
      {/* Undo pill (handoff delta #11) — renders at the BudgetTab root, like the prototype. */}
      <UndoPill undo={m.undo} onUndo={() => m.doUndo(toast)} />
    </div>
  )
}
