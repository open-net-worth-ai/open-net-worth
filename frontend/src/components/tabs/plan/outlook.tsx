// plan/outlook.tsx — "Outlook" sub-tab. The MERGE of the old Projections + Retirement tabs.
// They were always two windows onto ONE simulated life: accumulation (Projections) and
// drawdown (Retirement). This tab shows the whole arc on a SINGLE life chart, with ONE set of
// assumption levers, and — crucially — derives every headline number (including "earliest you
// could stop") from the SAME engine, so they can never drift apart again.
//
// Verbatim port of design_handoffs/plan_handoff/prototype/plan/outlook.jsx (1014 lines).
// Changes from the prototype:
//   • React globals → ES imports; window.PlanData → * as PD; window.PlanCharts → charts
//   • FIRE config seeded from /api/fire via useOutlookFire (real netWorth + fireVersion)
//   • STAGE_OVERRIDES dropped (demo personas; real config seeds directly)
//   • Forecast scorecard wired to LIVE GET /api/forecast-scorecard (mapScorecard); per-period
//     % miss = delta/|actual|; honest empty state when the book is too young to score
//   • Unused computed vars (headlineMode, yr1GrowthPct, adjustPct, reachesFire,
//     crossYear/crossAge, trim, planNest, planEnd, dchip) removed; showOddsAnyway
//     state removed — none appear in the rendered JSX.
//   • noUncheckedIndexedAccess: `!` added on all provably-safe array accesses.

import { useState, useEffect, useMemo, useRef, useTransition } from 'react'
import type { ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as PD from './planData'
import { fBaseCompact, getCurrencySymbol } from '../../../utils/format'
import { fBaseWhole, fSigned } from './planData'
import { authFetch } from '../../../api/client'
import { FanChart, RbdChart } from './charts'
import { X, Plus, Info, Chevron } from './icons'
import { Spark } from '../../../lucius/identity'
import { useOutlookFire } from './useOutlookFire'
import { useOutlookSim } from './useOutlookSim'
import type { SimRequest } from './useOutlookSim'
import type { OutlookLevers } from './outlookApi'
import { useForecastScorecard } from '../../../hooks/useForecastScorecard'
import type { ForecastScorecard } from '../../../api/insights'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePlanState } from './usePlanState'
import type { BaseOverride } from './usePlanState'
import { persistFirePlan } from './firePlanPersistence'
import {
  useRegisterScreenCommands,
  useRegisterScreenFacts,
  useRegisterScreenPrompts,
} from '../../../lucius/useScreenContract'
import { PLAN_RESET_SPEC, PLAN_STAGE_WHATIF_SPEC } from '../../../lucius/screenCommands'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { buildWhatifState } from './stageWhatif'
import { useScenarioOdds } from './scenarioOdds'
import { Button, MoneyField, NumberField } from '../../ui'
import { SquarePen } from '../../../ui/icons'
import { PlanningModal } from './PlanningModal'
import { OutlookWhatIfAsk } from './OutlookWhatIfAsk'

function FullscreenChartSurface({
  open,
  onClose,
  children,
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
}) {
  if (!open) return <div className="pl-panel pl-chart">{children}</div>
  return (
    <Dialog.Root
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="pl-chart-backdrop" />
        <Dialog.Content className="pl-panel pl-chart pl-chart-full" aria-describedby={undefined}>
          <Dialog.Title className="ui-visually-hidden">Life projection chart</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// Map the live ForecastScorecard API → the trust-strip's display shape. Per-period miss is the
// signed % gap (delta / |actual|); the tech tiles roll up from the cashflow/net-worth accuracy
// blocks. Returns null when the book is too young to score (forecastable:false / no closed periods)
// so the strip shows an honest "building a track record" state instead of fabricated numbers.
interface ScoreView {
  cfMape: number
  projectedMonthlyNet: number
  monthsTracked: number
  periods: Array<{ label: string; cf: number; nw: number }>
  tech: {
    mape: number
    avgMiss: number
    bias: number
    nwMape: number
    nwAvgMiss: number
    nwDrift: number
  }
}
function mapScorecard(d: ForecastScorecard | null): ScoreView | null {
  if (!d || !d.forecastable || d.periods.length === 0) return null
  const pct = (delta: number, actual: number) =>
    Math.abs(actual) > 0.005 ? (delta / Math.abs(actual)) * 100 : 0
  return {
    cfMape: Math.round(d.cashflow_accuracy.mean_abs_pct_error ?? 0),
    projectedMonthlyNet: d.projected_monthly_net,
    monthsTracked: d.data_quality.periods_available,
    periods: d.periods.map((p) => ({
      label: p.label,
      cf: pct(p.cashflow_delta, p.cashflow_actual),
      nw:
        p.net_worth_delta != null && p.net_worth_actual != null
          ? pct(p.net_worth_delta, p.net_worth_actual)
          : 0,
    })),
    tech: {
      mape: Math.round(d.cashflow_accuracy.mean_abs_pct_error ?? 0),
      avgMiss: Math.round(d.cashflow_accuracy.mean_abs_error),
      bias: Math.round(d.cashflow_accuracy.bias),
      nwMape: Math.round(d.net_worth_accuracy.mean_abs_pct_error ?? 0),
      nwAvgMiss: Math.round(d.net_worth_accuracy.mean_abs_error),
      nwDrift: Math.round(d.net_worth_accuracy.bias),
    },
  }
}

// ── Public props ──────────────────────────────────────────────────────────────
export interface OutlookTabProps {
  onAskLucius: (text: string) => void
  toast: (msg: string) => void
  onOpenSettings: () => void
  /** Real net worth (GET /api/net-worth, the authoritative computation) — seeds FIRE.netWorth. */
  total: number
  /** True while /api/net-worth's FIRST fetch is still in flight — `total` is a 0 placeholder
   *  until then, never a confirmed value. Keeps useOutlookFire from seeding (and simulating)
   *  off that placeholder, which otherwise flashes a real-looking but wrong odds % on open. */
  totalLoading?: boolean
  /** Bumped by the shell's FIRE Settings modal on save → re-fetch /api/fire. */
  fireVersion: number
  /** False while this sub-tab is mounted-but-hidden — gates the sim so it doesn't compute
   *  until the user actually views Outlook. */
  active?: boolean
  /** Called after "Update my FIRE plan" also writes the matching fields back to the profile
   *  (FIRE Settings' own store), so the shell can bump fireVersion the same way a Settings-modal
   *  save does — keeping both directions on one shared source of truth. */
  onFireSettingsSaved?: () => void
}

// ── Local types ───────────────────────────────────────────────────────────────

interface LifeEvent {
  id: string
  label: string
  amount: number
  age: number
}

// A locally-created event needs an id before its first save (the server mints one for
// anything that arrives without). Counter-suffixed because two adds inside one millisecond
// would otherwise collide, and delete/drag/edit all match on this id.
let evSeq = 0
const newEventId = () => `e${Date.now().toString(36)}${(evSeq++).toString(36)}`

interface IncomeStream {
  label: string
  amount: number
  age: number
  endAge?: number | null
}

interface ShockCfg {
  crashAge?: number | null
  weakFrom?: number | null
  weakTo?: number | null
  lostFrom?: number | null
  lostTo?: number | null
  inflFrom?: number | null
  inflTo?: number | null
}

interface SavedScenario {
  id: string
  name: string
  successPct: number
  odds: number
  saving: number
  ret: number
  vol: number
  monthlyRetirementSpending: number
  swr: number | null
  lifeExp: number | null
  retireAge: number
  atNumber: boolean
  streams?: IncomeStream[]
  events?: LifeEvent[]
  crash: boolean
  weakSeq: boolean
  lostDec: boolean
  highInfl: boolean
  shockCfg?: ShockCfg
}

interface PlanBState {
  id?: string
  name: string
  successPct: number
  odds: number
  retireAge: number
}

interface PartialState {
  saving?: number
  ret?: number
  vol?: number
  monthlyRetirementSpending?: number
  swr?: number
  lifeExp?: number
  retireAge?: number
  atNumber?: boolean
  events?: LifeEvent[]
  crash?: boolean
  weakSeq?: boolean
  lostDec?: boolean
  highInfl?: boolean
}

// ── Life events as draggable timeline objects (house / kids / inheritance / sabbatical) ──
// amount < 0 = a one-off SPEND; amount > 0 = a WINDFALL (e.g. inheritance) — signed like a
// ledger amount (money out is negative). Each re-runs the sim.
const EVENT_PRESETS = [
  { label: '🏠 Home', amount: -150000, dAge: 3 },
  { label: '👶 New baby', amount: -90000, dAge: 2 },
  { label: '🎓 Tuition', amount: -120000, dAge: 12 },
  { label: '💰 Inheritance', amount: 200000, dAge: 10 },
  { label: '✈️ Sabbatical', amount: -60000, dAge: 5 },
]

// ── Collapsible section (tidies the secondary controls under the levers) ──
function Section({
  title,
  sub,
  action,
  defaultOpen,
  mark,
  cmd,
  children,
}: {
  title: string
  sub?: ReactNode
  action?: ReactNode
  defaultOpen?: boolean
  mark?: ReactNode
  cmd?: { n: number; open: boolean }
  children: ReactNode
}) {
  const [open, setOpen] = useState(!!defaultOpen)
  useEffect(() => {
    if (cmd && cmd.n) setOpen(cmd.open)
  }, [cmd])
  return (
    <div className={'pl-sec' + (open ? ' open' : '')}>
      <div className="pl-sec-head">
        <button className="pl-sec-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <span className="pl-sec-caret" aria-hidden="true">
            ›
          </span>
          {mark && (
            <span className="pl-sec-mark" aria-hidden="true">
              {mark}
            </span>
          )}
          <span className="pl-sec-title">{title}</span>
          {sub && <span className="pl-sec-sub">{sub}</span>}
        </button>
        {open && action}
      </div>
      <div className="pl-sec-anim" data-open={open ? 'true' : 'false'}>
        <div className="pl-sec-anim-inner">
          <div className="pl-sec-body">{children}</div>
        </div>
      </div>
    </div>
  )
}

// accuracy bands for the forecast scorecard (cash flow held tighter than market-driven net worth)
const ACC_BANDS = { cf: [10, 25], nw: [20, 40] }
function accTone(v: number, kind: 'cf' | 'nw') {
  const a = Math.abs(v),
    b = ACC_BANDS[kind] ?? ACC_BANDS.cf
  return a <= b[0]! ? 'good' : a <= b[1]! ? 'warn' : 'bad'
}
// coarse money for guidance figures — no false precision (S$307,800 → "S$310k")
function fCoarse(n: number) {
  const a = Math.abs(n),
    s = n < 0 ? '−' : ''
  const sym = getCurrencySymbol()
  if (a >= 1e6) return s + sym + (a / 1e6).toFixed(2).replace(/\.?0+$/, '') + 'M'
  if (a >= 1e3) return s + sym + Math.round(a / 1e4) * 10 + 'k'
  return s + sym + Math.round(a)
}

// ── Click-to-type lever (slider + editable value) — ported from the old Projections tab ──
export function Lever({
  label,
  display,
  value,
  min,
  max,
  step,
  onChange,
  sub,
  ariaLabel,
  ariaValueText,
  editAriaLabel,
  suffix,
}: {
  label: string
  display: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  sub?: ReactNode
  ariaLabel?: string
  ariaValueText?: string
  editAriaLabel?: string
  suffix?: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const skipNextBlurCommit = useRef(false)
  function startEdit() {
    setDraft(String(value))
    setEditing(true)
  }
  function onDraft(e: React.ChangeEvent<HTMLInputElement>) {
    let v = e.target.value.replace(/[^0-9.]/g, '')
    const i = v.indexOf('.')
    if (i !== -1) v = v.slice(0, i + 1) + v.slice(i + 1).replace(/\./g, '')
    setDraft(v)
  }
  function commit() {
    let n = parseFloat(draft)
    if (!isFinite(n)) {
      setEditing(false)
      return
    }
    n = Math.min(max, Math.max(min, Math.round(n * 100) / 100))
    onChange(n)
    setEditing(false)
  }
  return (
    <div className="pl-lever">
      <div className="pl-lever-top">
        <span className="pl-lever-lbl">{label}</span>
        <span className="pl-lever-input-wrap">
          <NumberField
            className="pl-lever-input"
            value={editing ? draft : value}
            min={min}
            max={max}
            step={step}
            aria-label={
              editAriaLabel ?? `Edit ${label.toLowerCase()}; current ${ariaValueText ?? display}`
            }
            onFocus={startEdit}
            onChange={onDraft}
            onBlur={() => {
              if (skipNextBlurCommit.current) skipNextBlurCommit.current = false
              else commit()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                skipNextBlurCommit.current = true
                commit()
              } else if (e.key === 'Escape') {
                skipNextBlurCommit.current = true
                setDraft(String(value))
                setEditing(false)
              }
            }}
          />
          {suffix && (
            <span className="pl-lever-input-suffix" aria-hidden="true">
              {suffix}
            </span>
          )}
        </span>
      </div>
      <input
        className="pl-range"
        type="range"
        min={min}
        max={max}
        step={step}
        value={Math.min(Math.max(value, min), max)}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={ariaLabel ?? label}
        aria-valuetext={ariaValueText}
      />
      {sub && <div className="pl-lever-sub">{sub}</div>}
    </div>
  )
}

export function MonthlySavingLever({
  saving,
  baselineSaving,
  onChange,
}: {
  saving: number
  baselineSaving: number
  onChange: (value: number) => void
}) {
  const display = fBaseWhole(saving)
  return (
    <div className="pl-lever pl-lever--money">
      <label className="pl-lever-lbl" htmlFor="monthly-saving-lever">
        Monthly saving
      </label>
      <MoneyField
        id="monthly-saving-lever"
        aria-label="Monthly saving per month"
        value={saving}
        formattedValue={display}
        suffix="/month"
        onValueChange={onChange}
      />
      <div className="pl-lever-sub">
        until you retire • plan: <b>{fBaseWhole(baselineSaving)}/month</b>
      </div>
    </div>
  )
}

export function RetirementSpendingLever({
  monthlySpending,
  withdrawalRate,
  onChange,
}: {
  monthlySpending: number
  withdrawalRate: number
  onChange: (value: number) => void
}) {
  const annualSpending = PD.annualRetirementSpending(monthlySpending)
  const fireNumber = PD.fireNumberFromMonthlySpend(monthlySpending, withdrawalRate)
  return (
    <div className="pl-lever pl-lever--money">
      <label className="pl-lever-lbl" htmlFor="monthly-retirement-spending-lever">
        Retirement spending
      </label>
      <MoneyField
        id="monthly-retirement-spending-lever"
        aria-label="Monthly retirement spending"
        value={monthlySpending}
        formattedValue={fBaseWhole(monthlySpending)}
        suffix="/month"
        onValueChange={onChange}
      />
      <div className="pl-lever-sub">
        {fBaseWhole(annualSpending)}/year • FIRE number{' '}
        <b className="mask">{fCoarse(fireNumber)}</b>
      </div>
    </div>
  )
}

const INIT_STREAMS: IncomeStream[] = []

// ── Main component ─────────────────────────────────────────────────────────────
export function OutlookTab(props: OutlookTabProps) {
  const { onAskLucius: onAsk, toast } = props
  // "What if?" is a full sidebar on desktop but too tall/cramped to scroll to on a phone —
  // it opens as a bottom sheet there instead (same PlanningModal primitive as every other
  // mobile sheet in this tab), never inline in the page flow.
  const isMobile = useIsMobile()
  const {
    config: f,
    loading: fireLoading,
    error: fireError,
    retry: retryFire,
    configVersion: fireConfigVersion,
  } = useOutlookFire(props.total, props.fireVersion, props.totalLoading ?? false)

  // Scenarios + FIRE baseline persisted on the server via /api/plan/*.
  const {
    scenarios,
    baseline: baseOverride,
    loading: planStateLoading,
    version: planStateVersion,
    refetch: refetchPlanState,
    addScenario: addScenarioApi,
    updateScenario: updateScenarioApi,
    deleteScenario: deleteScenarioApi,
    saveBaseline,
  } = usePlanState()

  // Deferred render: the Monte-Carlo sim runs inside useMemo and blocks paint. We gate all
  // heavy useMemos on simEnabled (false on first render) then flip it inside a startTransition
  // after mount, giving the browser one frame to paint the shell before the sim starts.
  const [, startTransition] = useTransition()
  const [simEnabled, setSimEnabled] = useState(false)
  // Tracks whether a chart drag is in progress — suppresses the recompute indicator so the
  // chart tracks the cursor without flickering a "Recalculating…" label.
  const draggingRef = useRef(false)
  // Guards addEvent() against a rapid repeat click on the same life-event preset — see its use.
  const lastPresetAddRef = useRef<{ label: string; at: number } | null>(null)
  // On a narrow card, the two "Ask Lucius" insight ribbons below collapse their text to one
  // ellipsized line (plan.css's @container rule) — these expand it back to full, wrapped text.
  const [heroBriefOpen, setHeroBriefOpen] = useState(false)
  const [rbdBriefOpen, setRbdBriefOpen] = useState(false)
  // Bumped on drag-end so the result re-settles at the full (5000) path count after a drag.
  const [settleTick, setSettleTick] = useState(0)
  const [savingPlan, setSavingPlan] = useState(false)
  const [profileSyncPending, setProfileSyncPending] = useState<BaseOverride | null>(null)

  // Re-seed levers when the stored baseline loads AND when a refetch (Lucius wrote plan
  // state → data_changed('plan')) actually CHANGED the baseline. A scenario-only write
  // (create/update/delete_plan_scenario) refreshes the shelf without touching the levers —
  // re-seeding on those was clobbering a just-tapped scenario and wiping its highlight
  // ("tap does nothing" repro). version 0 = the initial load (silent).
  const seededPlanVersion = useRef(-1)
  const seededBaselineJson = useRef<string | null>(null)
  useEffect(() => {
    if (planStateLoading || seededPlanVersion.current === planStateVersion) return
    const isRefetch = seededPlanVersion.current >= 0
    seededPlanVersion.current = planStateVersion
    const b = baseOverride
    const bJson = JSON.stringify(b ?? null)
    const baselineChanged = bJson !== seededBaselineJson.current
    seededBaselineJson.current = bJson
    if (!b || (isRefetch && !baselineChanged)) return
    startTransition(() => {
      if (b.saving != null) setSaving(b.saving)
      if (b.ret != null) setRet(b.ret)
      if (b.vol != null) setVol(b.vol)
      if (b.monthlyRetirementSpending != null)
        setMonthlyRetirementSpending(b.monthlyRetirementSpending)
      if (b.swr != null) setSwr(b.swr)
      if (b.lifeExp != null) setLifeExp(b.lifeExp)
      if (b.retireAge != null) setRetireAge(b.retireAge)
      if (Array.isArray(b.streams)) setStreams(b.streams)
      if (Array.isArray(b.events)) setEvents(b.events)
    })
    if (isRefetch) {
      setActiveScen(null)
      setLoadedFrom(null)
      toast('Plan updated from chat')
    }
  }, [planStateLoading, planStateVersion]) // eslint-disable-line react-hooks/exhaustive-deps

  // Lucius wrote plan state (baseline/scenarios) server-side → re-pull it.
  const planDomainV = useDataVersion('plan')
  useEffect(() => {
    if (planDomainV > 0) void refetchPlanState()
  }, [planDomainV, refetchPlanState])

  // Defer the first sim past the first paint so the tab is immediately interactive — AND until
  // BOTH /api/fire AND the saved plan baseline have resolved. fireLoading alone isn't enough:
  // the lever state (saving/ret/.../events) is seeded from `BASE`, which falls back to the raw
  // profile defaults until the baselineSeeded effect above re-seeds it from `baseOverride` —
  // gated on planStateLoading, a SEPARATE fetch. Flipping simEnabled on fireLoading alone let
  // the first sim run (and PAINT a result) against those un-customized defaults before the
  // real baseline re-seed landed — a real-looking but wrong % that then visibly jumped once the
  // saved plan (e.g. staged events, a lower retire age) applied a moment later.
  useEffect(() => {
    if (!fireLoading && !planStateLoading && (props.active ?? true)) {
      startTransition(() => setSimEnabled(true))
    }
  }, [fireLoading, planStateLoading, props.active]) // eslint-disable-line react-hooks/exhaustive-deps

  // Seed the levers from the real /api/fire config, and RE-seed whenever the user saves new
  // FIRE settings (fireVersion bump → useOutlookFire re-gates loading → fresh config here), so
  // the chart regenerates from the new plan. On the FIRST load only, a saved baseline wins
  // (applied by the baselineSeeded effect); a later explicit settings save overrides it.
  //
  // Keyed on fireConfigVersion (advances ONLY inside useOutlookFire's fetch success callback,
  // alongside `f` itself) rather than `fireLoading`: `fireLoading` flips via a SEPARATE effect
  // in useOutlookFire that commits one render before its fetch actually resolves, so an effect
  // here keyed on it could mark props.fireVersion "handled" using a still-stale `f` — permanently
  // skipping the real re-seed once the fresh config lands a moment later.
  const seededVer = useRef(-1)
  useEffect(() => {
    if (fireConfigVersion !== props.fireVersion || seededVer.current === props.fireVersion) return
    const firstSeed = seededVer.current === -1
    seededVer.current = props.fireVersion
    if (firstSeed && baseOverride) return
    setSaving(f.monthlySaving)
    setRet(f.expectedReturn)
    setVol(f.volatility)
    setMonthlyRetirementSpending(f.monthlyRetirementSpend)
    setSwr(f.withdrawalRate)
    setLifeExp(f.lifeExpectancy)
    setRetireAge(f.targetAge)
    // A later explicit settings save (Settings modal, or a Lucius profile edit) is the new
    // source of truth for the saved plan too — sync the Outlook baseline to match instead of
    // letting it keep silently shadowing the fresh values in BASE.* (the "plan: X" captions).
    if (!firstSeed) {
      void saveBaseline({
        saving: f.monthlySaving,
        ret: f.expectedReturn,
        vol: f.volatility,
        monthlyRetirementSpending: f.monthlyRetirementSpend,
        swr: f.withdrawalRate,
        lifeExp: f.lifeExpectancy,
        retireAge: f.targetAge,
        streams,
        events,
      })
    }
  }, [fireConfigVersion, props.fireVersion]) // eslint-disable-line react-hooks/exhaustive-deps

  // Clear the drag flag on any mouseup so the indicator re-appears on the next slider change.
  useEffect(() => {
    const onUp = () => {
      const wasDragging = draggingRef.current
      draggingRef.current = false
      if (wasDragging) setSettleTick((t) => t + 1)
    }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('touchend', onUp)
    return () => {
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('touchend', onUp)
    }
  }, [])

  const BASE = {
    saving: baseOverride?.saving != null ? baseOverride.saving : f.monthlySaving,
    ret: baseOverride?.ret != null ? baseOverride.ret : f.expectedReturn,
    vol: baseOverride?.vol != null ? baseOverride.vol : f.volatility,
    monthlyRetirementSpending:
      baseOverride?.monthlyRetirementSpending != null
        ? baseOverride.monthlyRetirementSpending
        : f.monthlyRetirementSpend,
    retireAge: baseOverride?.retireAge != null ? baseOverride.retireAge : f.targetAge,
    swr: baseOverride?.swr != null ? baseOverride.swr : f.withdrawalRate,
    lifeExp: baseOverride?.lifeExp != null ? baseOverride.lifeExp : f.lifeExpectancy,
  }
  const BASE_STREAMS: IncomeStream[] = baseOverride?.streams ? baseOverride.streams : INIT_STREAMS
  const BASE_EVENTS: LifeEvent[] = baseOverride?.events ? baseOverride.events : []
  // f.currentAge is a precise, fractional age from date_of_birth (e.g. 28.1) — load-bearing
  // for the sim's month-level math, so it stays fractional THERE. But "the age this happens"
  // is a discrete year a person picks, never a fraction of one — every default/bound/typed
  // value for an event or stream age must round through this, not raw f.currentAge.
  const currentAgeR = Math.round(f.currentAge)

  // ── One unified set of assumptions ──
  const [saving, setSaving] = useState(BASE.saving)
  const [ret, setRet] = useState(BASE.ret)
  const [vol, setVol] = useState(BASE.vol)
  const [monthlyRetirementSpending, setMonthlyRetirementSpending] = useState(
    BASE.monthlyRetirementSpending,
  )
  const [swr, setSwr] = useState(BASE.swr)
  const [lifeExp, setLifeExp] = useState(BASE.lifeExp)
  const [retireAge, setRetireAge] = useState(BASE.retireAge)
  const [atNumber, setAtNumber] = useState(false) // conditional milestone: retire when I hit my number
  const [streams, setStreams] = useState<IncomeStream[]>(BASE_STREAMS)
  const [events, setEvents] = useState<LifeEvent[]>(BASE_EVENTS) // life events on the timeline
  // Mirrors of events/streams for plan.stage_whatif to read/write SYNCHRONOUSLY, immune to
  // React's render/commit timing — see the handler below for why relying on the `events`/
  // `streams` closure (which only reflects the LAST committed render) isn't enough on its own.
  const eventsRef = useRef(events)
  eventsRef.current = events
  const streamsRef = useRef(streams)
  streamsRef.current = streams
  const [crash, setCrash] = useState(false)
  const [weakSeq, setWeakSeq] = useState(false)
  const [lostDec, setLostDec] = useState(false)
  const [highInfl, setHighInfl] = useState(false)
  const [chartFocus, setChartFocus] = useState<string | null>(null) // which annotation layer the panel is hovering
  const [, setPreset] = useState<string | null>('base')
  const [activeScen, setActiveScen] = useState<string | null>(null)
  const [loadedFrom, setLoadedFrom] = useState<string | null>(null)

  // chart mode
  const [chartMode, setChartMode] = useState<'networth' | 'rbd'>('networth')
  const [chartFull, setChartFull] = useState(false)
  const chartH = chartFull ? Math.max(360, Math.round(window.innerHeight * 0.56)) : 250
  const [rbdCond, setRbdCond] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(true)
  const [secCmd, setSecCmd] = useState({ n: 0, open: false })
  const [allExpanded, setAllExpanded] = useState(false)
  function toggleAllSecs() {
    const open = !allExpanded
    setAllExpanded(open)
    setSecCmd((c) => ({ n: c.n + 1, open }))
  }

  // scenario library + comparison
  const [naming, setNaming] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [draftName, setDraftName] = useState('')
  const [confirmDel, setConfirmDel] = useState<SavedScenario | null>(null)
  const [planB, setPlanB] = useState<PlanBState | null>(null)

  // add-income modal
  const [addingIncome, setAddingIncome] = useState(false)
  const [newLabel, setNewLabel] = useState('')
  const [newAmount, setNewAmount] = useState('')
  const [newAge, setNewAge] = useState('')
  const [newEndAge, setNewEndAge] = useState('')
  const [editIncomeIdx, setEditIncomeIdx] = useState<number | null>(null)
  // market-shock windows: per-shock age overrides (null/absent = use the sensible default)
  const [shockCfg, setShockCfg] = useState<ShockCfg>({})
  const [editShock, setEditShock] = useState<string | null>(null)
  const [shockFrom, setShockFrom] = useState('')
  const [shockTo, setShockTo] = useState('')
  const [addingEvent, setAddingEvent] = useState(false)
  const [evLabel, setEvLabel] = useState('')
  const [evAmount, setEvAmount] = useState('')
  const [evKind, setEvKind] = useState(1)
  const [evAge, setEvAge] = useState('')
  const [editEventId, setEditEventId] = useState<string | null>(null)

  // trust strip
  const [showTrack, setShowTrack] = useState(false)
  const [showTech, setShowTech] = useState(false)

  const streamsKey = JSON.stringify(streams)
  const eventsKey = JSON.stringify(events)
  const isBase =
    saving === BASE.saving &&
    ret === BASE.ret &&
    vol === BASE.vol &&
    monthlyRetirementSpending === BASE.monthlyRetirementSpending &&
    swr === BASE.swr &&
    lifeExp === BASE.lifeExp &&
    retireAge === BASE.retireAge &&
    !atNumber &&
    eventsKey === JSON.stringify(BASE_EVENTS) &&
    !crash &&
    !weakSeq &&
    !lostDec &&
    !highInfl &&
    streamsKey === JSON.stringify(BASE_STREAMS)

  // does the live lever set still equal a given saved scenario? (drives the "current" highlight on rows)
  // Null levers fall back to BASE with the SAME rule recallScenario applies — a sparse
  // (AI-created) scenario must not have its highlight cleared the instant it loads.
  function scenEq(s: SavedScenario | undefined) {
    if (!s) return false
    return (
      (s.saving ?? BASE.saving) === saving &&
      (s.ret ?? BASE.ret) === ret &&
      (s.vol ?? BASE.vol) === vol &&
      (s.monthlyRetirementSpending ?? BASE.monthlyRetirementSpending) ===
        monthlyRetirementSpending &&
      (s.swr == null ? BASE.swr : s.swr) === swr &&
      (s.lifeExp == null ? BASE.lifeExp : s.lifeExp) === lifeExp &&
      !!s.atNumber === atNumber &&
      (s.atNumber || (s.retireAge ?? BASE.retireAge) === retireAge) &&
      JSON.stringify(Array.isArray(s.streams) ? s.streams : BASE_STREAMS) === streamsKey &&
      JSON.stringify(Array.isArray(s.events) ? s.events : []) === eventsKey &&
      !!s.crash === crash &&
      !!s.weakSeq === weakSeq &&
      !!s.lostDec === lostDec &&
      !!s.highInfl === highInfl
    )
  }
  // clear the active-scenario highlight once the live levers no longer match what was loaded
  useEffect(() => {
    if (activeScen && !scenEq(scenarios.find((x) => x.id === activeScen))) setActiveScen(null)
    if (loadedFrom && !scenarios.some((x) => x.id === loadedFrom)) setLoadedFrom(null)
  }, [
    saving,
    ret,
    vol,
    monthlyRetirementSpending,
    swr,
    lifeExp,
    retireAge,
    atNumber,
    streamsKey,
    eventsKey,
    crash,
    weakSeq,
    lostDec,
    highInfl,
  ]) // eslint-disable-line react-hooks/exhaustive-deps

  // Effective market-shock windows, for the chart annotations and the shock editor: a per-shock
  // override if set, else a sensible default. The server resolves the SAME defaults for the
  // simulation itself (services/retirement_plan._windows) — keep the two in step. Whole ages
  // only: a default that starts at today's age read "Lost decade • 44.8–54.8".
  const shockWin = {
    crash: { from: Math.round(shockCfg.crashAge != null ? shockCfg.crashAge : retireAge) },
    weak: {
      from: Math.round(shockCfg.weakFrom != null ? shockCfg.weakFrom : retireAge),
      to: Math.round(shockCfg.weakTo != null ? shockCfg.weakTo : Math.min(lifeExp, retireAge + 10)),
    },
    lost: {
      from: Math.round(shockCfg.lostFrom != null ? shockCfg.lostFrom : f.currentAge),
      to: Math.round(
        shockCfg.lostTo != null ? shockCfg.lostTo : Math.min(lifeExp, f.currentAge + 10),
      ),
    },
    infl: {
      from: Math.round(shockCfg.inflFrom != null ? shockCfg.inflFrom : f.currentAge),
      to: Math.round(shockCfg.inflTo != null ? shockCfg.inflTo : lifeExp),
    },
  }
  const shockKey = JSON.stringify(shockCfg)
  // The "safe to retire" bar — mirrors services/retirement_plan.SAFE_BAR, which computes the age.
  const BAR = 0.8
  const fireNumber = Math.round(PD.fireNumberFromMonthlySpend(monthlyRetirementSpending, swr))

  // ── THE simulation lives on the server (services/montecarlo.py): this screen and Lucius's
  //    simulate_retirement run the SAME engine on the SAME random paths and saved-plan defaults,
  //    so they cannot disagree. We send the levers; useOutlookSim posts them (latest-wins, fewer
  //    paths while dragging) + reads the saved plan back as the dashed baseline. ──
  const levers: OutlookLevers = {
    saving,
    ret,
    vol,
    monthlyRetirementSpending,
    swr,
    lifeExp,
    retireAge,
    streams,
    events,
    crash,
    weakSeq,
    lostDec,
    highInfl,
    shockCfg,
  }
  const simReq: SimRequest = { levers, chartMode, full: !draggingRef.current }
  // The server reads net worth itself; a MEANINGFUL move (0.5% — live prices tick constantly)
  // re-runs the fan so it never sits on a stale pot.
  const nwBucket = Math.round(f.netWorth / Math.max(1, Math.abs(f.netWorth) * 0.005))
  const bundleKey = [
    saving,
    ret,
    vol,
    monthlyRetirementSpending,
    swr,
    lifeExp,
    retireAge,
    streamsKey,
    eventsKey,
    crash,
    weakSeq,
    lostDec,
    highInfl,
    shockKey,
    chartMode,
    settleTick,
    nwBucket,
  ].join('|')
  const planKey = JSON.stringify([baseOverride, fireConfigVersion, nwBucket])
  const {
    bundle,
    planResult,
    pending: simPending,
    error: simError,
    retry: retrySim,
    settledKey,
  } = useOutlookSim(simReq, bundleKey, planKey, simEnabled)
  const result = bundle?.result ?? null
  const earliest = bundle?.earliest ?? null
  const extraSave = bundle?.extraSave ?? null
  const gapSave = bundle?.gapSave ?? null
  const numberAge = bundle?.numberAge ?? null
  const rbdData = bundle?.rbdData ?? null

  // ── headline figures, all read off `result` ──
  // `result` is null until simEnabled transitions true (after first paint), then non-null
  // for all subsequent renders including in-progress transitions (which show the OLD result).
  const simLoading = result === null
  const success = result?.successProbability ?? 0
  const successPct = Math.round(success * 100)
  const depletion = result?.depletionProbability ?? 0
  const nestEgg = result?.nestEggAtRetire ?? 0
  const endingP50 = result?.medianEnding ?? 0
  const endingP10 = result?.p10Ending ?? 0
  const endingP90 = result?.p90Ending ?? 0
  const tone = success >= 0.8 ? 'pos' : success >= 0.5 ? 'warn' : 'neg'
  const verdictLabel =
    success >= 0.8
      ? 'Likely to last'
      : success >= 0.5
        ? 'At risk of running out'
        : 'Unlikely to last'

  const fundedPct = Math.min(100, Math.round((f.netWorth / fireNumber) * 100))
  const recSwr = PD.suggestSWR(lifeExp - retireAge)

  // Age the TYPICAL (p50) path first reaches the FIRE number — the accumulation milestone
  let fireReachAge: number | null = null
  if (result)
    for (let i = 1; i < result.bands.length; i++) {
      if (result.bands[i]!.p50 >= fireNumber && result.bands[i - 1]!.p50 < fireNumber) {
        const t =
          (fireNumber - result.bands[i - 1]!.p50) /
          (result.bands[i]!.p50 - result.bands[i - 1]!.p50)
        fireReachAge = Math.round(
          result.bands[i - 1]!.age + t * (result.bands[i]!.age - result.bands[i - 1]!.age),
        )
        break
      }
    }
  // Ages derive from a fractional currentAge (e.g. 28.1 from a date_of_birth), so round for
  // display — a raw subtraction shows "0.10000000000000142 yrs" etc.
  const retireAgeR = Math.round(retireAge)
  const reachVsTarget = fireReachAge != null ? retireAgeR - fireReachAge : null

  const planPct = Math.round((planResult?.successProbability ?? 0) * 100)
  const deltaPts = successPct - planPct

  // LIVE shelf odds — a stored odds/successPct is a save-time snapshot (it bakes in that
  // day's net worth + age and silently goes stale), so the shelf re-reads every row from
  // the SAME server engine whenever the inputs move; stored values are only the bootstrap.
  const liveOdds = useScenarioOdds(
    scenarios,
    JSON.stringify([baseOverride, fireConfigVersion, nwBucket]),
    simEnabled && !fireLoading,
  )

  // Refresh a recalled scenario's PERSISTED odds (what headless/AI readers see) — but
  // ONLY from a sim result that provably corresponds to the recalled levers:
  // settledKey === bundleKey. Trusting `simPending` alone raced (it flips one render
  // late), which once persisted the BASELINE's 8% onto a 39% scenario. One write per tap.
  const oddsWroteFor = useRef<string | null>(null)
  useEffect(() => {
    if (!activeScen || planStateLoading || result === null) return
    if (settledKey !== bundleKey || oddsWroteFor.current === activeScen) return
    const s = scenarios.find((x) => x.id === activeScen)
    if (!s || !scenEq(s) || s.odds === successPct) return
    oddsWroteFor.current = activeScen
    void updateScenarioApi(s.id, { ...s, odds: successPct, successPct })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeScen, settledKey, bundleKey, result, successPct, scenarios, planStateLoading])
  useEffect(() => {
    if (!activeScen) oddsWroteFor.current = null
  }, [activeScen])

  const earliestAge = earliest ? Math.round(earliest.age) : null
  const stopVsTarget = earliestAge != null ? retireAgeR - earliestAge : null

  // ── adaptive headline: gate emphasis on YEARS-TO-RETIREMENT, not a toggle ──
  const yearsToRet = retireAge - f.currentAge

  // conditional retirement: numberAge (from the worker bundle) feeds the "retire at your number" toggle
  useEffect(() => {
    if (atNumber && numberAge != null) setRetireAge(Math.round(numberAge))
  }, [atNumber, numberAge])
  const numberUnreachable = atNumber && numberAge == null

  // ── edge state: very high success → you may be UNDER-spending ──
  const underSpending =
    success >= 0.95 && monthlyRetirementSpending < f.monthlyRetirementSpend * 1.6

  // ── scenarios ──
  const currencySym = getCurrencySymbol()
  const PRESETS: [string, string, PartialState][] = [
    ['base', 'My FIRE plan', {}],
    ['early', 'Retire 5 yrs earlier', { retireAge: BASE.retireAge - 5 }],
    ['save', `Save +${currencySym}1k/mo`, { saving: BASE.saving + 1000 }],
    [
      'home',
      `Buy a ${currencySym}150k home`,
      { events: [{ id: 'home', label: '🏠 Home', age: currentAgeR + 3, amount: -150000 }] },
    ],
  ]

  function applyState(partial: PartialState) {
    startTransition(() => {
      const s: Required<PartialState> = {
        saving: BASE.saving,
        ret: BASE.ret,
        vol: BASE.vol,
        monthlyRetirementSpending: BASE.monthlyRetirementSpending,
        swr: BASE.swr,
        lifeExp: BASE.lifeExp,
        retireAge: BASE.retireAge,
        atNumber: false,
        events: BASE_EVENTS,
        crash: false,
        weakSeq: false,
        lostDec: false,
        highInfl: false,
        ...partial,
      }
      setSaving(s.saving)
      setRet(s.ret)
      setVol(s.vol)
      setMonthlyRetirementSpending(s.monthlyRetirementSpending)
      setSwr(s.swr)
      setLifeExp(s.lifeExp)
      setAtNumber(s.atNumber)
      if (!s.atNumber) setRetireAge(s.retireAge)
      setEvents(s.events)
      eventsRef.current = s.events // synchronous — see plan.stage_whatif's handler comment
      setCrash(s.crash)
      setWeakSeq(s.weakSeq)
      setLostDec(s.lostDec)
      setHighInfl(s.highInfl)
      setShockCfg({})
    })
  }
  function applyPreset(key: string) {
    const p = PRESETS.find((x) => x[0] === key)!
    setPreset(key)
    applyState(p[2])
  }
  // The sim runs off-thread (worker), so lever changes update state IMMEDIATELY — the slider
  // tracks the cursor at 60fps and the chart catches up when the worker posts back.
  const lever = (setter: (v: number) => void) => (v: number) => {
    setter(v)
    setPreset(null)
  }
  // ONE transition for ALL of it — the lever resets (applyState) already ride a
  // transition, and splitting the marks/streams into an urgent update commits an
  // intermediate frame where activeScen is cleared while the levers still hold the
  // old scenario → diverged flips true for a beat → the Save/Create actions FLASH
  // (live repro: only when switching back to My FIRE plan; scenario rows were
  // already atomic via recallScenario).
  function resetPlan() {
    startTransition(() => {
      applyPreset('base')
      setStreams(BASE_STREAMS)
      streamsRef.current = BASE_STREAMS // synchronous — see plan.stage_whatif's handler comment
      setActiveScen(null)
      setLoadedFrom(null)
    })
  }
  // Mirrors FireSettingsModal.save() in Plan.tsx (the other direction of this same sync) so the
  // Outlook baseline and the FIRE Settings profile fields share ONE source of truth regardless
  // of which screen last wrote them — see updateBaseline() below.
  async function syncFireSettingsToProfile() {
    const response = await authFetch('/api/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fire_monthly_retirement_spending: monthlyRetirementSpending,
        fire_withdrawal_rate_pct: swr,
        fire_expected_return_pct: ret,
        fire_volatility_pct: vol,
        retirement_target_age: retireAge,
        fire_life_expectancy: lifeExp,
        monthly_saving: saving,
      }),
    })
    if (!response.ok) throw new Error(`PUT /api/profile → HTTP ${response.status}`)
    props.onFireSettingsSaved?.()
  }
  // Write the current levers, events & income back onto the baseline "My FIRE plan" (persisted),
  // AND onto the FIRE Settings profile fields — "Update my FIRE plan" is the one save action for
  // what the user thinks of as a single plan, shown on two screens.
  async function updateBaseline() {
    if (savingPlan) return
    const baseline = {
      saving,
      ret,
      vol,
      monthlyRetirementSpending,
      swr,
      lifeExp,
      retireAge,
      streams,
      events,
    }
    const baselineAlreadySaved =
      profileSyncPending != null && JSON.stringify(profileSyncPending) === JSON.stringify(baseline)
    setSavingPlan(true)
    try {
      const profileSynced = await persistFirePlan(
        baseline,
        saveBaseline,
        syncFireSettingsToProfile,
        baselineAlreadySaved,
      )
      setProfileSyncPending(profileSynced ? null : baseline)
      setPreset('base')
      setActiveScen(null)
      setLoadedFrom(null)
      toast(profileSynced ? 'FIRE plan updated' : 'Plan saved, but FIRE settings could not sync')
    } catch {
      toast('Could not save FIRE plan')
    } finally {
      setSavingPlan(false)
    }
  }
  // baseline the live levers are measured against: a loaded scenario if one is anchored, else the FIRE plan
  const loadedScen = loadedFrom ? scenarios.find((x) => x.id === loadedFrom) : null
  const diverged = loadedScen ? activeScen !== loadedFrom : !isBase || profileSyncPending != null
  const baselineLabel = loadedScen ? loadedScen.name : 'my FIRE plan'
  function resetToBaseline() {
    if (loadedScen) recallScenario(loadedScen)
    else resetPlan()
  }

  function suggestName() {
    if (atNumber) return 'Retire at my number'
    if (retireAge !== BASE.retireAge) return 'Retire at ' + retireAge
    if (events.length > 0)
      return (
        events[0]!.label.replace(/^[^\w]+\s*/, '') +
        (events.length > 1 ? ' +' + (events.length - 1) : '')
      )
    if (saving !== BASE.saving)
      return 'Save ' + (saving > BASE.saving ? '+' : '') + fBaseWhole(saving - BASE.saving) + '/mo'
    if (crash) return '2008 crash'
    if (weakSeq) return 'Weak first decade'
    if (lostDec) return 'Lost decade'
    if (highInfl) return 'High inflation'
    if (swr !== BASE.swr) return swr + '% withdrawal'
    if (lifeExp !== BASE.lifeExp) return 'Plan to ' + lifeExp
    if (ret !== BASE.ret || vol !== BASE.vol) return ret + '% / ' + vol + '% vol'
    return 'My scenario'
  }
  function openSave() {
    setDraftName(suggestName())
    setNaming(true)
  }
  function saveCurrent() {
    if (loadedFrom) saveScenarioInPlace()
    else void updateBaseline()
  }
  async function confirmSave() {
    const s: Omit<SavedScenario, 'id'> = {
      name: draftName.trim() || suggestName(),
      successPct,
      odds: successPct,
      saving,
      ret,
      vol,
      monthlyRetirementSpending,
      swr,
      lifeExp,
      retireAge,
      atNumber,
      streams,
      events,
      crash,
      weakSeq,
      lostDec,
      highInfl,
      shockCfg,
    }
    const created = await addScenarioApi(s).catch(() => {
      toast('Could not save scenario')
      return null
    })
    if (!created) return
    setActiveScen(created.id)
    setLoadedFrom(created.id)
    setNaming(false)
    toast('Scenario saved')
  }
  function saveScenarioInPlace() {
    if (!loadedFrom) return
    const existing = scenarios.find((s) => s.id === loadedFrom)
    if (!existing) return
    void updateScenarioApi(loadedFrom, {
      ...existing,
      successPct,
      odds: successPct,
      saving,
      ret,
      vol,
      monthlyRetirementSpending,
      swr,
      lifeExp,
      retireAge,
      atNumber,
      streams,
      events,
      crash,
      weakSeq,
      lostDec,
      highInfl,
      shockCfg,
    })
    setActiveScen(loadedFrom)
    setNaming(false)
    toast('Scenario updated')
  }
  function recallScenario(s: SavedScenario) {
    startTransition(() => {
      setSaving(s.saving != null ? s.saving : BASE.saving)
      setRet(s.ret != null ? s.ret : BASE.ret)
      setVol(s.vol != null ? s.vol : BASE.vol)
      setMonthlyRetirementSpending(
        s.monthlyRetirementSpending != null
          ? s.monthlyRetirementSpending
          : BASE.monthlyRetirementSpending,
      )
      setSwr(s.swr != null ? s.swr : BASE.swr)
      setLifeExp(s.lifeExp != null ? s.lifeExp : BASE.lifeExp)
      setAtNumber(!!s.atNumber)
      if (!s.atNumber) setRetireAge(s.retireAge != null ? s.retireAge : BASE.retireAge)
      setStreams(Array.isArray(s.streams) ? s.streams : BASE_STREAMS)
      setEvents(Array.isArray(s.events) ? s.events : [])
      setCrash(!!s.crash)
      setWeakSeq(!!s.weakSeq)
      setLostDec(!!s.lostDec)
      setHighInfl(!!s.highInfl)
      setShockCfg(s.shockCfg && typeof s.shockCfg === 'object' ? s.shockCfg : {})
      setPreset(null)
      setActiveScen(s.id)
      setLoadedFrom(s.id)
    })
  }
  function removeScenario(id: string) {
    void deleteScenarioApi(id)
  }
  function commitRename() {
    if (editingId == null) return
    const id = editingId
    const nm = editName.trim()
    const existing = scenarios.find((s) => s.id === id)
    if (!existing) {
      setEditingId(null)
      return
    }
    void updateScenarioApi(id, { ...existing, name: nm || existing.name })
    setEditingId(null)
  }

  function openAddIncome() {
    setEditIncomeIdx(null)
    setNewLabel('')
    setNewAmount('')
    setNewAge('')
    setNewEndAge('')
    setAddingIncome(true)
  }
  function openEditIncome(i: number) {
    const s = streams[i]
    if (!s) return
    setEditIncomeIdx(i)
    setNewLabel(s.label)
    setNewAmount(String(s.amount))
    setNewAge(String(Math.round(s.age)))
    setNewEndAge(s.endAge != null ? String(Math.round(s.endAge)) : '')
    setAddingIncome(true)
  }
  // market-shock editor (per-shock window)
  const SHOCK_META: Record<
    string,
    { label: string; single: boolean; on: boolean; set: (v: boolean) => void; desc: string }
  > = {
    crash: {
      label: 'Crash',
      single: true,
      on: crash,
      set: setCrash,
      desc: 'A sudden ~30% market drop in one year. The age is when it hits.',
    },
    weak: {
      label: 'Weak returns',
      single: false,
      on: weakSeq,
      set: setWeakSeq,
      desc: 'A stretch of poor returns — most damaging in the years right after you retire.',
    },
    lost: {
      label: 'Lost decade',
      single: false,
      on: lostDec,
      set: setLostDec,
      desc: 'A long flat stretch where the market goes roughly nowhere.',
    },
    infl: {
      label: 'High inflation',
      single: false,
      on: highInfl,
      set: setHighInfl,
      desc: 'Higher-than-planned inflation eating into real returns across the window.',
    },
  }
  function openShockEditor(key: string) {
    const w = shockWin[key as keyof typeof shockWin]
    setShockFrom(String(w.from))
    setShockTo('to' in w && w.to != null ? String(w.to) : '')
    setEditShock(key)
  }
  function applyShock() {
    const key = editShock
    if (!key) return
    const from = parseInt(String(shockFrom).replace(/[^0-9]/g, ''), 10)
    const to = parseInt(String(shockTo).replace(/[^0-9]/g, ''), 10)
    const cfg = { ...shockCfg }
    if (key === 'crash') {
      cfg.crashAge = isFinite(from) ? from : null
      setCrash(true)
    } else if (key === 'weak') {
      cfg.weakFrom = isFinite(from) ? from : null
      cfg.weakTo = isFinite(to) ? to : null
      setWeakSeq(true)
    } else if (key === 'lost') {
      cfg.lostFrom = isFinite(from) ? from : null
      cfg.lostTo = isFinite(to) ? to : null
      setLostDec(true)
    } else if (key === 'infl') {
      cfg.inflFrom = isFinite(from) ? from : null
      cfg.inflTo = isFinite(to) ? to : null
      setHighInfl(true)
    }
    setShockCfg(cfg)
    setPreset(null)
    setEditShock(null)
  }
  function disableShock(key: string) {
    SHOCK_META[key]!.set(false)
    setPreset(null)
    setEditShock(null)
  }
  function openAddEvent() {
    setEditEventId(null)
    setEvLabel('')
    setEvAmount('')
    setEvKind(1)
    setEvAge(String(currentAgeR + 5))
    setAddingEvent(true)
  }
  function openEditEvent(ev: LifeEvent) {
    setEditEventId(ev.id)
    setEvLabel(ev.label)
    setEvAmount(String(Math.abs(ev.amount)))
    // evKind is a UI-only direction flag (1 = "Money out (spend)" chip, -1 = "Money in
    // (windfall)" chip) — the STORED amount is signed the other way (spend is negative), so
    // this inverts on read and confirmAddEvent inverts again on write.
    setEvKind(ev.amount < 0 ? 1 : -1)
    setEvAge(String(Math.round(ev.age)))
    setAddingEvent(true)
  }
  function confirmAddEvent() {
    if (!evAmount) return
    const amount = -evKind * Math.round(Number(evAmount))
    const age = Math.round(
      Math.min(lifeExp - 1, Math.max(currentAgeR, Number(evAge) || currentAgeR + 5)),
    )
    const label = evLabel.trim() || 'Event'
    if (editEventId != null) {
      setEvents((l) => l.map((x) => (x.id === editEventId ? { ...x, label, amount, age } : x)))
      setAddingEvent(false)
      setPreset(null)
      toast('Life event updated')
      return
    }
    setEvents((l) => [...l, { id: newEventId(), label, amount, age }])
    setAddingEvent(false)
    setPreset(null)
    toast('Added to the chart — drag it to fine-tune')
  }
  function confirmAdd() {
    if (!newAmount || !newAge) return
    const age = Math.round(Number(newAge))
    const endAge =
      newEndAge !== '' && Math.round(Number(newEndAge)) > age ? Math.round(Number(newEndAge)) : null
    const entry: IncomeStream = {
      label: newLabel || 'Income',
      amount: Number(newAmount),
      age,
      endAge,
    }
    if (editIncomeIdx != null) {
      setStreams((s) => s.map((x, idx) => (idx === editIncomeIdx ? entry : x)))
      setAddingIncome(false)
      setPreset(null)
      toast('Income stream updated — odds updated')
      return
    }
    setStreams((s) => [...s, entry])
    setAddingIncome(false)
    setPreset(null)
    toast('Income stream added — odds updated')
  }
  function removeStream(i: number) {
    setStreams((s) => s.filter((_, idx) => idx !== i))
    setPreset(null)
  }
  function addEvent(p: { label: string; amount: number; dAge: number }) {
    // Unlike the custom-event modal (whose "Add to chart" click closes the modal, so a
    // double-click's second click lands on nothing), this preset button stays visible and
    // clickable after every add — a rapid repeat click (impatience while "Recalculating…"
    // shows, an accidental double-click) each independently appends a duplicate with no
    // feedback distinguishing "already added" from "not registered yet". Ignore a repeat of
    // the SAME preset within a beat of the last one; a deliberate second one seconds later
    // (e.g. two kids) still goes through.
    const now = Date.now()
    if (
      lastPresetAddRef.current &&
      lastPresetAddRef.current.label === p.label &&
      now - lastPresetAddRef.current.at < 800
    )
      return
    lastPresetAddRef.current = { label: p.label, at: now }
    setEvents((l) => [
      ...l,
      {
        id: newEventId(),
        label: p.label,
        amount: p.amount,
        age: Math.round(Math.min(lifeExp - 1, currentAgeR + p.dAge)),
      },
    ])
    setPreset(null)
    toast('Added to the chart — drag it to the right age')
  }
  function removeEvent(id: string) {
    setEvents((l) => l.filter((x) => x.id !== id))
    setPreset(null)
  }
  function moveEvent(id: string, age: number) {
    draggingRef.current = true
    setEvents((l) => l.map((x) => (x.id === id ? { ...x, age } : x)))
    setPreset(null)
  }

  const overlayBands = isBase ? null : (planResult?.bands ?? null)

  // Annotations the chart draws for the active what-if state.
  const chartShocks: Array<{
    key: string
    label: string
    kind: 'point' | 'span'
    age?: number
    from?: number
    to?: number
    color: string
  }> = []
  if (lostDec)
    chartShocks.push({
      key: 'lost',
      label: 'Lost decade',
      kind: 'span',
      from: shockWin.lost.from,
      to: shockWin.lost.to,
      color: 'var(--bad)',
    })
  if (highInfl)
    chartShocks.push({
      key: 'infl',
      label: 'High inflation',
      kind: 'span',
      from: shockWin.infl.from,
      to: shockWin.infl.to,
      color: 'var(--warn)',
    })
  if (weakSeq)
    chartShocks.push({
      key: 'weak',
      label: 'Weak returns',
      kind: 'span',
      from: shockWin.weak.from,
      to: shockWin.weak.to,
      color: 'var(--bad)',
    })
  if (crash)
    chartShocks.push({
      key: 'crash',
      label: 'Crash',
      kind: 'point',
      age: shockWin.crash.from,
      color: 'var(--bad)',
    })

  // Forecast scorecard — LIVE data from GET /api/forecast-scorecard (mapped by mapScorecard).
  // `sc` is null while loading or when the book is too young to score → honest empty state.
  const { data: scData } = useForecastScorecard('1Y')
  const sc = useMemo(() => mapScorecard(scData), [scData])
  const reliable: { label: string; tone: 'pos' | 'warn' | 'neg' } =
    sc == null
      ? { label: 'Building', tone: 'warn' }
      : sc.cfMape <= 10
        ? { label: 'Reliable', tone: 'pos' }
        : sc.cfMape <= 25
          ? { label: 'Usually close', tone: 'warn' }
          : { label: 'Loose', tone: 'neg' }

  // Lucius screen driving — the two-way contract: chat what-ifs land on the live
  // levers exactly like a hand on the sliders (ephemeral; reload discards). Active
  // only while this sub-tab is visible AND seeded — otherwise the command parks on
  // the bus and replays when the screen activates.
  useRegisterScreenCommands(
    [
      {
        spec: PLAN_STAGE_WHATIF_SPEC,
        handler: (args) => {
          // Events/streams accumulate on top of whatever is CURRENTLY staged, not the saved
          // baseline — a lever omitted from a call falls back to baseline (a slider has one
          // value, so that's the obvious per-call contract), but a life event doesn't have
          // that "one current value" — resetting the array to baseline on every call silently
          // drops whatever a PRIOR call staged unless every future call re-lists it verbatim.
          // That's exactly what happened live: "add the Porsche" dropped the house purchase
          // a caller had staged two turns earlier, with no error or signal that it vanished.
          // plan.reset stays the explicit way back to the saved plan.
          //
          // Read/write eventsRef/streamsRef, NOT the `events`/`streams` closure — the closure
          // only reflects the last COMMITTED render. Two plan.stage_whatif calls dispatched
          // back-to-back (the model calling the tool twice in one turn, no render in between)
          // would otherwise both read the SAME stale base and the second overwrites the
          // first's addition — the exact bug this whole thing is guarding against, just moved
          // from "the model forgot to resend" to "the harness raced itself". The refs are
          // mutated synchronously below, so the Nth call always sees the (N-1)th's result
          // regardless of render timing — a guarantee the model's behavior can't undermine.
          const { partial, streams: nextStreams } = buildWhatifState(args, {
            base: BASE,
            baseEvents: eventsRef.current,
            baseStreams: streamsRef.current,
            currentAge: f.currentAge,
            // Baseline-relative contract: when the call omits life_exp, applyState
            // resets the horizon to BASE.lifeExp — so age clamps must use the SAME
            // bound, never the live slider value (review finding, 2026-07-11).
            lifeExp: BASE.lifeExp,
          })
          // events is synced inside applyState itself; streams isn't applyState's concern.
          streamsRef.current = nextStreams
          applyState(partial)
          setStreams(nextStreams)
          setActiveScen(null)
          setLoadedFrom(null)
          toast('Lucius staged a what-if — nothing saved')
        },
      },
      {
        spec: PLAN_RESET_SPEC,
        handler: () => {
          resetPlan()
          toast('Back to your saved plan')
        },
      },
    ],
    (props.active ?? true) && !planStateLoading && !fireLoading,
  )

  // Lucius screen awareness — live figures + suggested prompts for the Outlook view.
  useRegisterScreenFacts((_ids) => {
    if (planStateLoading) return []
    const potLabel = yearsToRet > 0 ? `Pot at ${retireAge}` : 'Pot today'
    const potValue = yearsToRet > 0 ? fCoarse(nestEgg) : fCoarse(f.netWorth)
    const facts: [string, string][] = [
      ['Money-lasts probability', `${successPct}% • ${verdictLabel}`],
      ['Safe-to-retire age', earliestAge != null ? `~${earliestAge}` : 'No age clears 80% odds'],
      ['FIRE number', `${fCoarse(fireNumber)} (${fundedPct}% funded)`],
      [potLabel, potValue],
      ['Withdrawal rate vs suggested', `${swr}% (suggested ${recSwr}%)`],
      // The inputs too, so Lucius quotes and re-runs THIS screen's plan instead of guessing
      // his own: without them he answered 89% where the screen said 72%, then "can't reproduce
      // the screen's 72%" (persona QA 2026-10-13).
      [
        'Inputs behind these figures',
        `saving ${fCoarse(saving)}/mo until age ${retireAge} • spending ${fCoarse(monthlyRetirementSpending)}/mo in retirement • ` +
          `expected return ${ret}% • volatility ${vol}% • money must last to ${lifeExp} • ` +
          `starts from net worth today ${fCoarse(f.netWorth)} (retirement accounts included)`,
      ],
    ]
    // Exact, ground-truth labels for whatever's CURRENTLY staged — plan.stage_whatif's
    // removeEvents/removeStreams need a label to match against, and guessing from memory is
    // exactly what went wrong live (an unmatched guess silently removed nothing, so the model
    // reached for plan.reset instead and wiped everything, not just the one stale item).
    if (events.length) {
      const shown = events.slice(0, 8)
      const rest = events.length - shown.length
      facts.push([
        'Staged life events (removeEvents target)',
        shown.map((e) => `"${e.label}" ${fCoarse(e.amount)} age ${Math.round(e.age)}`).join(' • ') +
          (rest > 0 ? ` • +${rest} more not shown` : ''),
      ])
    }
    if (streams.length) {
      const shown = streams.slice(0, 8)
      const rest = streams.length - shown.length
      facts.push([
        'Staged income streams (removeStreams target)',
        shown
          .map(
            (s) =>
              `"${s.label}" ${fCoarse(s.amount)}/mo from ${Math.round(s.age)}` +
              (s.endAge != null ? ` to ${Math.round(s.endAge)}` : ''),
          )
          .join(' • ') + (rest > 0 ? ` • +${rest} more not shown` : ''),
      ])
    }
    return facts
  })
  useRegisterScreenPrompts(() => [
    'What is my chance of outliving my money?',
    'What if I retire 5 years earlier?',
    'How much more must I save to hit 90% confidence?',
    'Is my withdrawal rate safe?',
  ])

  // suppress TypeScript unused-variable errors for variables that are computed but intentionally
  // not rendered in this version of the prototype JSX (kept for future-iteration parity).
  void underSpending
  void depletion
  void gapSave

  // Show the indicator during initial load AND during in-flight transitions (slider/settings changes).
  // Suppress during chart drag so the fan tracks the cursor without a label appearing.
  const showIndicator = simLoading || (simPending && !draggingRef.current)

  return (
    <div className={'pl-sub pl-outlook' + (railCollapsed ? ' rail-collapsed' : '')}>
      {fireError || (simError && simLoading) ? (
        <div className="pl-main">
          <div
            style={{ padding: '32px 0', textAlign: 'center', color: 'var(--bad)', fontSize: 13 }}
          >
            {fireError ? "Couldn't load your plan settings" : "Couldn't run your outlook"}
            {' • '}
            <button className="pl-link" onClick={fireError ? retryFire : retrySim}>
              Retry
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="pl-main">
            {simError && (
              <div
                role="status"
                style={{ padding: '6px 0', color: 'var(--bad)', fontSize: 'var(--text-sm)' }}
              >
                Couldn't refresh your outlook — some figures may be out of date{' • '}
                <button className="pl-link" onClick={retrySim}>
                  Retry
                </button>
              </div>
            )}
            {/* ════════ 1 • HEADLINE — chance your money lasts ════════ */}
            <div className={'pl-summary' + (heroBriefOpen ? ' brief-open' : '')}>
              <div className="pl-sum-row">
                <div className="pl-sum-hero-block">
                  <div className="pl-sum-top">
                    <span className="pl-sum-eyebrow">Your financial future</span>
                    <span className={'pl-badge ' + (simLoading ? 'warn' : tone)}>
                      {simLoading ? (
                        'Calculating…'
                      ) : (
                        <>
                          {verdictLabel}
                          {isBase ? '' : ' • what-if'}
                          <Info
                            text={
                              success >= 0.8
                                ? 'Likely to last: your savings are very likely to last through age ' +
                                  lifeExp +
                                  '. It does not mean you’ve hit your FIRE number yet; see the date below.'
                                : success >= 0.5
                                  ? 'At risk of running out: your money lasts in some futures but not enough of them. This is about your money surviving retirement, not whether you’ve hit your FIRE number yet.'
                                  : 'Unlikely to last: in most simulated futures your money runs out before age ' +
                                    lifeExp +
                                    '. This is about survival of your savings, not whether you’ve hit your FIRE number yet.'
                            }
                          />
                        </>
                      )}
                    </span>
                    {!simLoading && !isBase && deltaPts !== 0 && (
                      <span className={'pl-lever-delta ' + (deltaPts > 0 ? 'up' : 'down')}>
                        {deltaPts > 0 ? '▲ +' : '▼ '}
                        {Math.abs(deltaPts)} vs plan
                      </span>
                    )}
                  </div>
                  <div className="pl-sum-heroline">
                    <div
                      className={'pl-sum-hero ' + (simLoading ? 'warn' : tone)}
                      style={{ fontSize: 34 }}
                    >
                      {simLoading ? '—' : `${successPct}%`}
                    </div>
                    {diverged && (
                      <button
                        className="pl-wchip pl-wchip--reset"
                        style={{ padding: '3px 11px', fontSize: 11 }}
                        onClick={resetToBaseline}
                        title={'Reset to ' + baselineLabel}
                      >
                        ↺ Reset
                      </button>
                    )}
                  </div>
                  <div className="pl-sum-sub">
                    chance your money lasts through age {lifeExp} if you retire at {retireAge}{' '}
                    <Info text="Across thousands of simulated market histories — including the retirement years — the share where your money never runs out. Above ~80% is comfortable." />
                  </div>
                </div>

                <div className="pl-sum-metrics">
                  <div className="pl-sum-stat">
                    <span className="k">
                      Safe to retire?{' '}
                      <Info
                        text={
                          'The youngest retirement age where the SAME simulation still keeps your money alive to ' +
                          lifeExp +
                          ' at least ' +
                          Math.round(BAR * 100) +
                          '% of the time. Derived from this chart — not a separate estimate.'
                        }
                      />
                    </span>
                    <span
                      className={
                        'v ' +
                        (simLoading
                          ? 'warn'
                          : stopVsTarget != null && stopVsTarget >= 0
                            ? 'pos'
                            : 'warn')
                      }
                    >
                      {simLoading ? '…' : earliestAge != null ? '~' + earliestAge : '—'}
                    </span>
                    <span className="s">
                      {simLoading
                        ? ''
                        : earliestAge == null
                          ? 'no age clears ' + Math.round(BAR * 100) + '% odds'
                          : stopVsTarget != null && stopVsTarget > 0
                            ? stopVsTarget +
                              ' yr' +
                              (stopVsTarget === 1 ? '' : 's') +
                              ' before your ' +
                              retireAgeR +
                              ' plan'
                            : stopVsTarget === 0
                              ? 'right at your ' + retireAgeR + ' plan'
                              : stopVsTarget != null
                                ? -stopVsTarget +
                                  ' yr' +
                                  (stopVsTarget === -1 ? '' : 's') +
                                  ' past your ' +
                                  retireAgeR +
                                  ' plan'
                                : ''}
                    </span>
                  </div>
                  <div className="pl-sum-stat">
                    <span className="k">
                      FIRE number{' '}
                      <Info
                        text={
                          'The pot that makes you work-optional: your annual retirement spending (' +
                          fCoarse(PD.annualRetirementSpending(monthlyRetirementSpending)) +
                          ') divided by your ' +
                          swr +
                          '% safe withdrawal rate. Hit this and your investments can cover your life without a paycheck.'
                        }
                      />
                    </span>
                    <span className="v mask">{fCoarse(fireNumber)}</span>
                    <span className="s">{fundedPct}% funded today</span>
                  </div>
                  {yearsToRet > 0 ? (
                    <div className="pl-sum-stat">
                      <span className="k">
                        Pot at {retireAge}{' '}
                        <Info text="The pot when you stop work is a DISTRIBUTION, not one number — this is the p10–p90 spread where the same fan crosses the retirement line." />
                      </span>
                      <span className="v mask">{simLoading ? '…' : fCoarse(nestEgg)}</span>
                      <span className="s">
                        {simLoading ? (
                          ''
                        ) : (
                          <>
                            range {fCoarse(result!.nestEggBand.p10)}–
                            {fCoarse(result!.nestEggBand.p90)}
                          </>
                        )}
                      </span>
                    </div>
                  ) : (
                    <div className="pl-sum-stat">
                      <span className="k">Pot today</span>
                      <span className="v mask">{fCoarse(f.netWorth)}</span>
                      <span className="s">drawing down from here</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="pl-sum-action">
                <span className="pl-sum-spark">
                  <Spark />
                </span>
                <span className="pl-sum-insight">
                  {simLoading ? (
                    <>Running the simulation…</>
                  ) : success >= 0.85 ? (
                    <>
                      You're in good shape — money lasts in <b>{successPct}%</b> of futures
                      {earliestAge != null && stopVsTarget != null && stopVsTarget > 0 ? (
                        <>
                          , and you could stop as early as <b>~{earliestAge}</b>.
                        </>
                      ) : (
                        <>.</>
                      )}
                    </>
                  ) : extraSave != null ? (
                    <>
                      Saving <b className="mask">{fBaseWhole(extraSave)} more a month</b> would lift
                      this to <b>90%</b>. Working one extra year does about the same.
                    </>
                  ) : (
                    <>Trimming spend or your withdrawal rate moves this more than saving alone.</>
                  )}
                </span>
                <button
                  className="pl-sum-brief-toggle"
                  onClick={() => setHeroBriefOpen((o) => !o)}
                  aria-expanded={heroBriefOpen}
                  aria-label="Toggle insight detail"
                >
                  <Chevron dir={heroBriefOpen ? 'up' : 'down'} size={13} />
                </button>
                <span className="pl-sum-acts">
                  <button
                    className="pl-sum-act pl-ai"
                    title="Puts this question in the Lucius chat for you to send"
                    onClick={() => onAsk('How do I improve the chance my money lasts?')}
                  >
                    <Spark size={11} />
                    Ask Lucius how to improve
                  </button>
                  <button
                    className="pl-sum-act pl-ai"
                    title="Puts this question in the Lucius chat for you to send"
                    onClick={() => onAsk('Am I saving enough to retire at ' + retireAge + '?')}
                  >
                    <Spark size={11} />
                    Ask Lucius if I'm saving enough
                  </button>
                </span>
              </div>
            </div>

            {/* ════════ 2 • ONE LIFE CHART — net worth, or the outcomes-by-age view ════════ */}
            <FullscreenChartSurface open={chartFull} onClose={() => setChartFull(false)}>
              <div className="pl-panel-head">
                <div className="pl-panel-headgroup">
                  <span className="pl-panel-title">
                    {chartMode === 'rbd' ? (
                      <>
                        {isMobile ? 'Outcomes by age' : 'How things could go at each age'}{' '}
                        <Info text="For every age, the share of futures where you are no longer living (from a survival curve), your money has run out, or you still have money. It tells the honest story: running out at 92 only matters if you are alive at 92, which most people are not, so a plain success rate overstates the risk." />
                      </>
                    ) : (
                      <>
                        {isMobile ? 'Your money over time' : 'Your money across your life'}{' '}
                        <Info
                          text={
                            'One continuous path from today through retirement to age ' +
                            lifeExp +
                            '. It climbs while you save, bends where you stop working, then draws down — shaded by how markets might treat you.'
                          }
                        />
                      </>
                    )}
                  </span>
                  <span className="pl-panel-sub">
                    {chartMode === 'rbd' ? (
                      rbdCond ? (
                        isMobile ? (
                          'money outcomes only'
                        ) : (
                          'conditional on being alive • money outcomes only'
                        )
                      ) : isMobile ? (
                        'mortality + money outcomes'
                      ) : (
                        'mortality + money, stacked to 100% at every age'
                      )
                    ) : result ? (
                      <>{result.nPaths.toLocaleString()} simulated paths</>
                    ) : null}
                    {showIndicator && (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                          marginLeft: result ? 10 : 0,
                          color: 'var(--faint)',
                          fontSize: 11,
                          verticalAlign: 'middle',
                        }}
                      >
                        <span
                          aria-hidden="true"
                          style={{
                            display: 'inline-block',
                            width: 9,
                            height: 9,
                            borderRadius: '50%',
                            border: '1.5px solid currentColor',
                            borderTopColor: 'transparent',
                            animation: 'ui-spin 0.7s linear infinite',
                            flexShrink: 0,
                          }}
                        />
                        Recalculating…
                      </span>
                    )}
                  </span>
                </div>
                <div className="pl-panel-actions">
                  <div className="pl-seg">
                    <button
                      className={'pl-seg-b' + (chartMode === 'networth' ? ' on' : '')}
                      onClick={() => setChartMode('networth')}
                      title="Net worth"
                      aria-label="Net worth view"
                    >
                      <span className="pl-seg-b-icon" aria-hidden="true">
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polyline points="3 17 9 11 13 15 21 5" />
                          <polyline points="15 5 21 5 21 11" />
                        </svg>
                      </span>
                      <span className="pl-seg-b-label">Net worth</span>
                    </button>
                    <button
                      className={'pl-seg-b' + (chartMode === 'rbd' ? ' on' : '')}
                      onClick={() => setChartMode('rbd')}
                      title="How things could go at each age"
                      aria-label="Outcomes by age view"
                    >
                      <span className="pl-seg-b-icon" aria-hidden="true">
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <rect x="3" y="4" width="18" height="4" rx="1" />
                          <rect x="3" y="10" width="18" height="4" rx="1" />
                          <rect x="3" y="16" width="18" height="4" rx="1" />
                        </svg>
                      </span>
                      <span className="pl-seg-b-label">Outcomes by age</span>
                    </button>
                  </div>
                  {/* Meaningless on mobile — the chart is already full-width there, and the
                      fullscreen overlay just re-renders the same-width chart taller. */}
                  {!isMobile && (
                    <button
                      className="pl-chart-full-btn"
                      onClick={() => setChartFull((v) => !v)}
                      title={chartFull ? 'Exit full screen' : 'View full screen'}
                      aria-label={chartFull ? 'Exit full screen' : 'View full screen'}
                    >
                      {chartFull ? (
                        <svg
                          width="15"
                          height="15"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M8 3v3a2 2 0 0 1-2 2H3" />
                          <path d="M21 8h-3a2 2 0 0 1-2-2V3" />
                          <path d="M3 16h3a2 2 0 0 1 2 2v3" />
                          <path d="M16 21v-3a2 2 0 0 1 2-2h3" />
                        </svg>
                      ) : (
                        <svg
                          width="15"
                          height="15"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M8 3H5a2 2 0 0 0-2 2v3" />
                          <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
                          <path d="M3 16v3a2 2 0 0 0 2 2h3" />
                          <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
                        </svg>
                      )}
                    </button>
                  )}
                </div>
              </div>
              <div className="pl-panel-body">
                {chartMode === 'rbd' ? (
                  <>
                    {rbdData ? (
                      <RbdChart data={rbdData} height={chartH} conditional={rbdCond} />
                    ) : (
                      <div style={{ height: chartH }} />
                    )}
                    <div className="pl-rbd-legend">
                      {!rbdCond && (
                        <span>
                          <i style={{ background: '#5b626e', opacity: 0.5 }} />
                          No longer living
                        </span>
                      )}
                      <span>
                        <i style={{ background: 'var(--bad)' }} />
                        Money ran out
                      </span>
                      <span>
                        <i style={{ background: '#bfe3cf' }} />
                        Have money (under 2×)
                      </span>
                      <span>
                        <i style={{ background: '#46c08d' }} />
                        2–5× your pot
                      </span>
                      <span>
                        <i style={{ background: '#1d9e75' }} />
                        Over 5× your pot
                      </span>
                      <label className="pl-rbd-toggle pl-checkrow">
                        <input
                          type="checkbox"
                          checked={rbdCond}
                          onChange={(e) => setRbdCond(e.target.checked)}
                        />
                        <span className="pl-checkbox" aria-hidden="true" />
                        <span className="pl-checkrow-lbl">Condition on survival</span>
                      </label>
                    </div>
                    <div
                      className={'pl-sum-action' + (rbdBriefOpen ? ' brief-open' : '')}
                      style={{ borderRadius: 12, border: '1px solid var(--line)', marginTop: 14 }}
                    >
                      <span className="pl-sum-spark">
                        <Spark />
                      </span>
                      <span className="pl-sum-insight">
                        If you keep saving steadily, you're far more likely to{' '}
                        <b>die with money left over</b> than to run out. The few "ran out" cases
                        mostly happen only if you live unusually long. Turn on{' '}
                        <b>Condition on survival</b> to set the odds of dying aside and just compare
                        the money outcomes.
                      </span>
                      <button
                        className="pl-sum-brief-toggle"
                        onClick={() => setRbdBriefOpen((o) => !o)}
                        aria-expanded={rbdBriefOpen}
                        aria-label="Toggle insight detail"
                      >
                        <Chevron dir={rbdBriefOpen ? 'up' : 'down'} size={13} />
                      </button>
                      <span className="pl-sum-acts">
                        <button
                          className="pl-sum-act pl-ai"
                          onClick={() =>
                            onAsk(
                              'Using the outcomes-by-age view, am I more likely to run out of money, or to leave a big surplus?',
                            )
                          }
                        >
                          <Spark size={11} />
                          Ask Lucius
                        </button>
                      </span>
                    </div>
                  </>
                ) : (
                  <>
                    {/* onRetireChange uses a direct setter (not lever) so chart-dragging the Retire
                    pill stays synchronous — lever() wraps in startTransition which would lag. */}
                    {result ? (
                      <FanChart
                        bands={result.bands}
                        height={chartH}
                        retireAge={retireAge}
                        fireNumber={fireNumber}
                        depletionPct={depletion}
                        overlay={overlayBands}
                        color="var(--primary)"
                        nestEggDist={result.nestEggBand}
                        fragile={yearsToRet <= 12}
                        onRetireChange={
                          atNumber
                            ? null
                            : (v: number) => {
                                draggingRef.current = true
                                setRetireAge(Math.round(v))
                                setPreset(null)
                              }
                        }
                        retireMin={currentAgeR + 1}
                        retireMax={Math.round(lifeExp) - 2}
                        events={events}
                        onEventDrag={moveEvent}
                        onEventRemove={removeEvent}
                        safeStopAge={earliestAge}
                        shocks={chartShocks}
                        incomeStreams={streams}
                        focusLayer={chartFocus}
                        showDry
                      />
                    ) : (
                      <div style={{ height: chartH }} />
                    )}
                    {result && (
                      <div
                        className="pl-phasebar"
                        style={
                          {
                            '--split':
                              Math.max(
                                2,
                                Math.min(
                                  98,
                                  ((8 +
                                    ((retireAge - result.bands[0]!.age) /
                                      (result.bands[result.bands.length - 1]!.age -
                                        result.bands[0]!.age)) *
                                      (760 - 16)) /
                                    760) *
                                    100,
                                ),
                              ) + '%',
                          } as React.CSSProperties
                        }
                      >
                        <span className="ph build">Building wealth</span>
                        <span className="ph draw">Drawing down</span>
                        <span className="ph-tick">◇ Retire</span>
                      </div>
                    )}
                    <div className="pl-legend" style={{ marginTop: 10 }}>
                      <span>
                        <i style={{ background: 'var(--primary)', height: 2, marginBottom: 3 }} />
                        typical path (p50)
                      </span>
                      <span>
                        <i
                          style={{
                            background: 'color-mix(in srgb, var(--primary) 28%, transparent)',
                          }}
                        />
                        good-to-bad luck range
                      </span>
                      <span>
                        <i className="dash" style={{ borderTopColor: 'var(--warn)' }} />
                        FIRE number
                      </span>
                      <span>
                        <i
                          style={{
                            width: 9,
                            height: 9,
                            borderRadius: 2,
                            background: 'var(--primary)',
                          }}
                        />
                        pot at retirement (p10–p90)
                      </span>
                      {yearsToRet <= 12 && (
                        <span>
                          <i
                            style={{
                              width: 9,
                              height: 9,
                              borderRadius: 2,
                              background: 'var(--warn)',
                              opacity: 0.5,
                            }}
                          />
                          fragile decade{' '}
                          <Info text="The ±5 years around your retirement date (shaded amber). A market fall right as you stop working is far more damaging than the same fall earlier — you're withdrawing, not adding, so the pot has less chance to recover. This is 'sequence-of-returns' risk." />
                        </span>
                      )}
                      {overlayBands && planResult && (
                        <span>
                          <i className="dash" />
                          your plan{' '}
                          <b className="mask" style={{ color: 'var(--muted)' }}>
                            {fCoarse(planResult.medianEnding)}
                          </b>{' '}
                          left
                        </span>
                      )}
                    </div>

                    <div className="pl-tiles pl-tiles--out" style={{ marginTop: 18 }}>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          <span>
                            Pot at {retireAge}
                            {isMobile ? '' : ' • p50'}
                          </span>{' '}
                          <Info
                            up
                            text={
                              'The median (p50) pot when you stop work — a distribution, not one number. Range ' +
                              (result
                                ? fCoarse(result.nestEggBand.p10) +
                                  '–' +
                                  fCoarse(result.nestEggBand.p90) +
                                  '.'
                                : '…')
                            }
                          />
                        </div>
                        <div className="pl-tile-v mask">{result ? fCoarse(nestEgg) : '…'}</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          <span>
                            Left at {lifeExp}
                            {isMobile ? '' : ' • p50'}
                          </span>{' '}
                          <Info
                            up
                            r
                            text="The median (50th-percentile) balance still left at your life expectancy."
                          />
                        </div>
                        <div className="pl-tile-v pos mask">{fCoarse(endingP50)}</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Worst case
                          {isMobile ? '' : ' • p10'}{' '}
                          <Info
                            up
                            text={
                              'The unlucky 1-in-10 (10th-percentile) outcome at age ' +
                              lifeExp +
                              ' — a stress test, not a forecast.'
                            }
                          />
                        </div>
                        <div
                          className="pl-tile-v mask"
                          style={{ color: endingP10 < 1000 ? 'var(--bad)' : 'var(--muted)' }}
                        >
                          {endingP10 < 1000 ? 'Runs dry' : fCoarse(endingP10)}
                        </div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Best case
                          {isMobile ? '' : ' • p90'}{' '}
                          <Info
                            up
                            r
                            text={
                              'The lucky 1-in-10 (90th-percentile) outcome at age ' +
                              lifeExp +
                              ' — only 1 path in 10 ends this high or higher.'
                            }
                          />
                        </div>
                        <div className="pl-tile-v pos mask">{fCoarse(endingP90)}</div>
                      </div>
                    </div>

                    {fireReachAge != null && (
                      <div className="pl-milestone-note">
                        <span className="pl-mn-row">
                          <span>
                            You{' '}
                            <b>
                              <span className="pl-mn-g warn">◆</span> reach your FIRE number around{' '}
                              {fireReachAge}
                            </b>
                            {reachVsTarget != null &&
                              (reachVsTarget > 0 ? (
                                <>
                                  , {reachVsTarget} yr{reachVsTarget === 1 ? '' : 's'} before your{' '}
                                  {retireAgeR} target
                                </>
                              ) : reachVsTarget < 0 ? (
                                <>
                                  , {-reachVsTarget} yr{reachVsTarget === -1 ? '' : 's'} after your{' '}
                                  {retireAgeR} target
                                </>
                              ) : (
                                <>, right at your {retireAgeR} target</>
                              ))}
                            {earliestAge != null ? (
                              <>
                                {' '}
                                — but it's only{' '}
                                <b>
                                  <span className="pl-mn-g good">◇</span> safe to retire around{' '}
                                  {earliestAge}
                                </b>
                                , since a bad market right after you quit could drain a pot that
                                just barely hit the number. That gap is the cushion that keeps your
                                money alive through {lifeExp}.
                              </>
                            ) : (
                              <>.</>
                            )}
                          </span>
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </FullscreenChartSurface>
          </div>
          {/* /pl-main (headline + chart) */}

          {/* ════════ 3 • ONE SET OF LEVERS — the left rail ════════ */}
          {railCollapsed && (
            <button
              className="pl-rail-fab mb-anchored"
              onClick={() => setRailCollapsed(false)}
              title="What if? — drag any lever to see the impact"
              aria-label="Open What if?"
            >
              <svg
                width="19"
                height="19"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <line x1="4" y1="9" x2="20" y2="9" />
                <line x1="4" y1="15" x2="20" y2="15" />
                <circle cx="10" cy="9" r="2.5" fill="var(--primary)" />
                <circle cx="15" cy="15" r="2.5" fill="var(--primary)" />
              </svg>
              <span className="pl-rail-fab-label">What if?</span>
            </button>
          )}
          {(() => {
            const whatIfPanel = (
              <div className="pl-panel pl-needle">
                <div className="pl-panel-head">
                  <button
                    className="pl-needle-collapse"
                    onClick={() => setRailCollapsed(true)}
                    title="Collapse the levers"
                    aria-label="Collapse"
                  >
                    «
                  </button>
                  <span className="pl-panel-title">
                    What if? <Info text="Drag any lever — the chart and odds update live." />
                  </span>
                  <div className="pl-head-actions">
                    {diverged && (
                      <button
                        className="pl-wchip pl-wchip--reset"
                        style={{ padding: '3px 10px', fontSize: 11 }}
                        onClick={resetToBaseline}
                        title={'Reset to ' + baselineLabel}
                      >
                        ↺ Reset
                      </button>
                    )}
                    <button
                      className="pl-alltoggle"
                      onClick={toggleAllSecs}
                      aria-label={allExpanded ? 'Collapse every section' : 'Expand every section'}
                      title={allExpanded ? 'Collapse all' : 'Expand all'}
                    >
                      {allExpanded ? (
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
                          <polyline points="6 3 12 8 18 3" />
                          <polyline points="6 21 12 16 18 21" />
                        </svg>
                      ) : (
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
                          <polyline points="6 8 12 3 18 8" />
                          <polyline points="6 16 12 21 18 16" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>
                <div className="pl-panel-body">
                  <OutlookWhatIfAsk
                    onAskLucius={onAsk}
                    onPicked={isMobile ? () => setRailCollapsed(true) : undefined}
                  />
                  {numberUnreachable && (
                    <div className="pl-edge warn">
                      <b>You don't hit your number in time.</b> Save more or trim spend to reach it
                      before {lifeExp}.
                    </div>
                  )}

                  <div className="pl-sec-group">
                    <Section
                      title="Scenarios"
                      cmd={secCmd}
                      defaultOpen
                      action={
                        diverged ? (
                          <div className="pl-sec-actions">
                            <button
                              className="pl-sec-action"
                              onClick={saveCurrent}
                              disabled={savingPlan}
                              title={
                                loadedFrom
                                  ? 'Overwrite the loaded scenario'
                                  : 'Update your FIRE plan'
                              }
                            >
                              {savingPlan ? 'Saving…' : profileSyncPending ? 'Retry sync' : 'Save'}
                            </button>
                            <button
                              className="pl-sec-action pl-sec-action--create"
                              onClick={openSave}
                              title="Save as a new scenario"
                            >
                              <Plus size={11} /> Create
                            </button>
                          </div>
                        ) : undefined
                      }
                    >
                      <div className="pl-scenrows">
                        <div className={'pl-scenrow' + (!activeScen && !loadedFrom ? ' on' : '')}>
                          <button
                            className="pl-scenrow-load"
                            onClick={resetPlan}
                            title="Load your FIRE plan back onto the levers"
                          >
                            <span className="pl-scenrow-name">My FIRE plan</span>
                            {isBase && !activeScen ? (
                              <span className="pl-scenrow-tag">current</span>
                            ) : !loadedFrom && !isBase ? (
                              <span className="pl-scenrow-tag edited">unsaved</span>
                            ) : (
                              <span className="pl-scenrow-odds">
                                {planResult != null ? `${planPct}%` : '—'}
                              </span>
                            )}
                          </button>
                        </div>
                        {scenarios.map((s) => (
                          <div
                            key={s.id}
                            className={
                              'pl-scenrow' +
                              (activeScen === s.id || loadedFrom === s.id ? ' on' : '')
                            }
                          >
                            {editingId === s.id ? (
                              <input
                                className="pl-scenrow-edit"
                                value={editName}
                                autoFocus
                                onChange={(e) => setEditName(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') commitRename()
                                  if (e.key === 'Escape') setEditingId(null)
                                }}
                                onBlur={commitRename}
                                aria-label="Scenario name"
                              />
                            ) : (
                              <button
                                className="pl-scenrow-load"
                                onClick={() => recallScenario(s)}
                                title="Load these settings"
                              >
                                <span className="pl-scenrow-name">{s.name}</span>
                                {activeScen === s.id ? (
                                  <span className="pl-scenrow-tag">current</span>
                                ) : loadedFrom === s.id ? (
                                  <span className="pl-scenrow-tag edited">unsaved</span>
                                ) : (
                                  <span
                                    className="pl-scenrow-odds"
                                    title={
                                      (liveOdds[s.id] ?? s.odds) == null
                                        ? 'Computing…'
                                        : `Chance your money lasts with this scenario's settings${s.atNumber ? '' : `, retiring at ${s.retireAge}`}`
                                    }
                                  >
                                    {(liveOdds[s.id] ?? s.odds) != null
                                      ? `${liveOdds[s.id] ?? s.odds}%`
                                      : '—'}
                                    {!s.atNumber && s.retireAge !== BASE.retireAge && (
                                      <span className="pl-scenrow-age"> at {s.retireAge}</span>
                                    )}
                                  </span>
                                )}
                              </button>
                            )}
                            {/* Explicit rename affordance — rename no longer rides double-click on the
                          load button (a quick second switch-tap opened the editor and read as a
                          frozen row). */}
                            {editingId !== s.id && (
                              <button
                                className="pl-scenrow-pencil"
                                onClick={() => {
                                  setEditingId(s.id)
                                  setEditName(s.name)
                                }}
                                aria-label="Rename scenario"
                                title="Rename"
                              >
                                <SquarePen size={11} />
                              </button>
                            )}
                            <button
                              className="pl-scenrow-x"
                              onClick={() => setConfirmDel(s)}
                              aria-label="Delete scenario"
                              title="Delete"
                            >
                              <X size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </Section>
                    <Section title="Plan levers" cmd={secCmd}>
                      <div className="pl-levers">
                        <MonthlySavingLever
                          saving={saving}
                          baselineSaving={BASE.saving}
                          onChange={lever(setSaving)}
                        />
                        {atNumber ? (
                          <div className="pl-lever">
                            <div className="pl-lever-top">
                              <span className="pl-lever-lbl">Retirement age</span>
                              <span
                                className="pl-lever-val"
                                style={{ cursor: 'default', opacity: 0.92 }}
                              >
                                {numberAge != null ? '~' + Math.round(numberAge) : '—'}
                              </span>
                            </div>
                            <input
                              className="pl-range"
                              type="range"
                              min={currentAgeR + 1}
                              max={70}
                              step={1}
                              value={Math.min(
                                70,
                                Math.max(currentAgeR + 1, numberAge || f.targetAge),
                              )}
                              disabled
                              readOnly
                              aria-label="Retirement age (conditional)"
                            />
                            <div className="pl-lever-sub">
                              {numberAge != null ? (
                                <>derived — follows your number</>
                              ) : (
                                <>not reached within your plan</>
                              )}
                            </div>
                          </div>
                        ) : (
                          <Lever
                            label="Retirement age"
                            display={String(retireAgeR)}
                            value={retireAge}
                            min={currentAgeR + 1}
                            max={70}
                            step={1}
                            onChange={lever(setRetireAge)}
                            sub={
                              <>
                                drag on the chart too • plan: <b>{BASE.retireAge}</b>
                              </>
                            }
                          />
                        )}
                        <RetirementSpendingLever
                          monthlySpending={monthlyRetirementSpending}
                          withdrawalRate={swr}
                          onChange={lever(setMonthlyRetirementSpending)}
                        />
                        <Lever
                          label="Withdrawal rate"
                          display={swr + '%'}
                          value={swr}
                          min={2.5}
                          max={6}
                          step={0.1}
                          suffix="%"
                          onChange={lever(setSwr)}
                          sub={
                            <>
                              safe % drawn yearly • lower lifts FIRE number • plan:{' '}
                              <b>{BASE.swr}%</b>
                            </>
                          }
                        />
                        <Lever
                          label="Plan until age"
                          display={String(lifeExp)}
                          value={lifeExp}
                          min={75}
                          max={100}
                          step={1}
                          onChange={lever(setLifeExp)}
                          sub={
                            <>
                              how long money must last • plan: <b>{BASE.lifeExp}</b>
                            </>
                          }
                        />
                        <Lever
                          label="Expected return"
                          display={ret + '%'}
                          value={ret}
                          min={0}
                          max={12}
                          step={0.1}
                          suffix="%"
                          onChange={lever(setRet)}
                          sub={
                            <>
                              before inflation • plan: <b>{BASE.ret}%</b>
                            </>
                          }
                        />
                        <Lever
                          label="Volatility"
                          display={vol + '%'}
                          value={vol}
                          min={6}
                          max={28}
                          step={1}
                          suffix="%"
                          onChange={lever(setVol)}
                          sub={
                            <>
                              market swings • plan: <b>{BASE.vol}%</b>
                            </>
                          }
                        />
                      </div>
                      <label className="pl-checkrow" style={{ marginTop: 14 }}>
                        <input
                          type="checkbox"
                          checked={atNumber}
                          onChange={(e) => {
                            setAtNumber(e.target.checked)
                            if (!e.target.checked) setRetireAge(BASE.retireAge)
                            setPreset(null)
                          }}
                        />
                        <span className="pl-checkbox" aria-hidden="true" />
                        <span className="pl-checkrow-lbl">Retire when I hit my FIRE number</span>
                      </label>
                    </Section>

                    {/* ── On your timeline ── */}
                    <Section
                      title="Events &amp; income"
                      cmd={secCmd}
                      sub={
                        events.length + streams.length > 0
                          ? events.length + streams.length
                          : undefined
                      }
                    >
                      <div className="pl-subsecs">
                        <Section
                          title="Market shocks"
                          cmd={secCmd}
                          sub={chartShocks.length || undefined}
                        >
                          <div className="pl-lever-desc">
                            Tap a shock to switch it on and set the years it hits — each tints a
                            window on the chart above.
                          </div>
                          <div className="pl-whatif-row" style={{ marginTop: 10 }}>
                            <button
                              className={'pl-wchip' + (crash ? ' on' : '')}
                              onClick={() => openShockEditor('crash')}
                              onMouseEnter={() => setChartFocus('crash')}
                              onMouseLeave={() => setChartFocus(null)}
                            >
                              Crash • {shockWin.crash.from}
                            </button>
                            <button
                              className={'pl-wchip' + (weakSeq ? ' on' : '')}
                              onClick={() => openShockEditor('weak')}
                              onMouseEnter={() => setChartFocus('weak')}
                              onMouseLeave={() => setChartFocus(null)}
                            >
                              Weak returns • {shockWin.weak.from}–{shockWin.weak.to}
                            </button>
                            <button
                              className={'pl-wchip' + (lostDec ? ' on' : '')}
                              onClick={() => openShockEditor('lost')}
                              onMouseEnter={() => setChartFocus('lost')}
                              onMouseLeave={() => setChartFocus(null)}
                            >
                              Lost decade • {shockWin.lost.from}–{shockWin.lost.to}
                            </button>
                            <button
                              className={'pl-wchip' + (highInfl ? ' on' : '')}
                              onClick={() => openShockEditor('infl')}
                              onMouseEnter={() => setChartFocus('infl')}
                              onMouseLeave={() => setChartFocus(null)}
                            >
                              High inflation • {shockWin.infl.from}–{shockWin.infl.to}
                            </button>
                          </div>
                        </Section>

                        <Section title="Life events" cmd={secCmd} sub={events.length || undefined}>
                          <div className="pl-lever-desc">
                            Add a one-off — it appears as a flag on the chart above; drag it to the
                            age it happens.
                          </div>
                          <div className="pl-events-add">
                            <button
                              className="pl-event-add-opt pl-event-add-custom"
                              onClick={openAddEvent}
                            >
                              <Plus size={11} /> Custom
                            </button>
                            {EVENT_PRESETS.map((p) => (
                              <button
                                key={p.label}
                                className="pl-event-add-opt"
                                onClick={() => addEvent(p)}
                              >
                                {p.label}{' '}
                                <b className="mask">
                                  {p.amount < 0 ? '−' : '+'}
                                  {fBaseCompact(Math.abs(p.amount))}
                                </b>
                              </button>
                            ))}
                          </div>
                          {events.length > 0 && (
                            <div className="pl-event-rows">
                              {events.map((ev) => (
                                <div key={ev.id} className="pl-event-row">
                                  <button
                                    className="pl-event-row-main"
                                    onClick={() => openEditEvent(ev)}
                                    title="Edit this life event"
                                  >
                                    <span className="pl-event-row-lbl">{ev.label}</span>
                                    <span
                                      className={
                                        'pl-event-row-amt' + (ev.amount > 0 ? ' windfall' : '')
                                      }
                                    >
                                      {ev.amount < 0 ? '−' : '+'}
                                      {fBaseCompact(Math.abs(ev.amount))}
                                    </span>
                                    <span className="pl-event-row-age">
                                      age {Math.round(ev.age)}
                                    </span>
                                  </button>
                                  <button
                                    className="pl-event-row-x"
                                    onClick={() => removeEvent(ev.id)}
                                    aria-label="Remove"
                                  >
                                    <X size={11} />
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </Section>

                        <Section
                          title="Guaranteed income"
                          cmd={secCmd}
                          sub={streams.length || undefined}
                        >
                          <div className="pl-lever-desc">
                            CPF LIFE, pension, annuities, rental — income that doesn't depend on the
                            market offsets your withdrawals. Its start age shows as a floor mark on
                            the chart.
                          </div>
                          <div
                            className="pl-income-row"
                            style={{ marginTop: 10 }}
                            onMouseEnter={() => setChartFocus('income')}
                            onMouseLeave={() => setChartFocus(null)}
                          >
                            {streams.map((s, i) => (
                              <span key={i} className="pl-tag pl-tag--edit">
                                <button
                                  className="pl-tag-edit"
                                  onClick={() => openEditIncome(i)}
                                  title="Edit this income source"
                                >
                                  {s.label} +<span className="mask">{fBaseWhole(s.amount)}</span>/mo
                                  •{' '}
                                  {s.endAge
                                    ? Math.round(s.age) + '–' + Math.round(s.endAge)
                                    : Math.round(s.age) + '+'}
                                </button>
                                <button
                                  className="x"
                                  onClick={() => removeStream(i)}
                                  aria-label="Remove"
                                >
                                  <X size={10} />
                                </button>
                              </span>
                            ))}
                            <button className="pl-add-income" onClick={openAddIncome}>
                              <Plus size={11} /> Add income
                            </button>
                          </div>
                        </Section>
                      </div>
                    </Section>

                    <Section
                      title="Compare two plans"
                      cmd={secCmd}
                      sub={planB ? 'A vs B' : undefined}
                      action={
                        !planB ? (
                          <button
                            className="pl-sec-action"
                            onClick={() => {
                              setPlanB({
                                name: isBase ? 'Snapshot' : 'What-if',
                                odds: successPct,
                                successPct,
                                retireAge,
                              })
                              toast('Saved as Plan B')
                            }}
                          >
                            <Plus size={11} /> Plan B
                          </button>
                        ) : undefined
                      }
                    >
                      {planB ? (
                        (() => {
                          const bPct = Math.round(planB.successPct || planB.odds)
                          return (
                            <>
                              <div className="pl-compare" style={{ marginTop: 4 }}>
                                <div
                                  className={'pl-compare-card' + (successPct > bPct ? ' win' : '')}
                                >
                                  <div className="pl-compare-lbl">
                                    Plan A{' '}
                                    <span style={{ color: 'var(--faint)', fontWeight: 600 }}>
                                      • live
                                    </span>
                                  </div>
                                  <div
                                    className={
                                      'pl-compare-pct ' + (successPct >= 80 ? 'pos' : 'neg')
                                    }
                                  >
                                    {successPct}%
                                  </div>
                                  <div
                                    style={{ fontSize: 12, color: 'var(--faint)', marginTop: 4 }}
                                  >
                                    money lasts • retire {retireAge}
                                  </div>
                                  {successPct > bPct && (
                                    <div className="pl-compare-win">✓ Higher odds</div>
                                  )}
                                </div>
                                <div
                                  className={'pl-compare-card' + (bPct > successPct ? ' win' : '')}
                                >
                                  <div className="pl-compare-lbl">
                                    Plan B{' '}
                                    <span style={{ color: 'var(--faint)', fontWeight: 600 }}>
                                      • {planB.name}
                                    </span>
                                  </div>
                                  <div className={'pl-compare-pct ' + (bPct >= 80 ? 'pos' : 'neg')}>
                                    {bPct}%
                                  </div>
                                  <div
                                    style={{ fontSize: 12, color: 'var(--faint)', marginTop: 4 }}
                                  >
                                    money lasts • retire {planB.retireAge}
                                  </div>
                                  {bPct > successPct && (
                                    <div className="pl-compare-win">✓ Higher odds</div>
                                  )}
                                </div>
                              </div>
                              <Button
                                variant="secondary"
                                size="dense"
                                style={{ marginTop: 10, fontSize: 12 }}
                                onClick={() => setPlanB(null)}
                              >
                                Clear comparison
                              </Button>
                            </>
                          )
                        })()
                      ) : (
                        <div style={{ fontSize: 12.5, color: 'var(--faint)' }}>
                          Save the current levers as Plan B, then keep tweaking Plan A to compare
                          side-by-side.
                        </div>
                      )}
                    </Section>
                  </div>
                </div>
              </div>
            )
            return isMobile ? (
              !railCollapsed && (
                <PlanningModal
                  onClose={() => setRailCollapsed(true)}
                  label="What if?"
                  className="pl-rail-sheet"
                >
                  {whatIfPanel}
                </PlanningModal>
              )
            ) : (
              <aside className="pl-rail">{whatIfPanel}</aside>
            )
          })()}
          {/* /pl-rail */}

          <div className="pl-main pl-main--trust">
            {/* ════════ 4 • TRUST STRIP — forecast accuracy, demoted ════════ */}
            <div className="pl-trust">
              <span
                className={'pl-trust-dot ' + reliable.tone}
                style={{
                  background:
                    reliable.tone === 'pos'
                      ? 'var(--good)'
                      : reliable.tone === 'warn'
                        ? 'var(--warn)'
                        : 'var(--bad)',
                }}
              />
              {sc == null ? (
                <span className="pl-trust-txt">
                  We'll show how close our forecasts have been once a few months have closed and we
                  can compare them against your actuals.
                </span>
              ) : (
                <>
                  <span className="pl-trust-txt">
                    Our cash-flow forecasts land within <b>~{sc.cfMape}% of plan</b> —{' '}
                    {sc.cfMape <= 10
                      ? 'reliable, solid for budgeting'
                      : sc.cfMape <= 25
                        ? 'usually close, fine for budgeting'
                        : 'loose, so treat them as a rough guide'}
                    . The net-worth line swings with markets, so treat it as a guide, not a promise.
                  </span>
                  <button className="pl-trust-toggle" onClick={() => setShowTrack((s) => !s)}>
                    {showTrack ? 'Hide track record ▾' : 'See track record ▸'}
                  </button>
                </>
              )}
            </div>

            {showTrack && sc && (
              <div className="pl-panel">
                <div className="pl-panel-head">
                  <span className="pl-panel-title">How close have our forecasts been?</span>
                  <span className="pl-panel-sub">
                    most recent {sc.periods.length} months • actuals vs what we projected
                  </span>
                </div>
                <div className="pl-panel-body">
                  <div className="pl-acc-sum">
                    <div className="pl-acc-sum-i">
                      <span
                        className={
                          'pl-acc-dot ' +
                          (reliable.tone === 'pos'
                            ? 'good'
                            : reliable.tone === 'neg'
                              ? 'bad'
                              : 'warn')
                        }
                      />
                      <span>
                        <b>Cash flow</b> — lands within ~{sc.cfMape}% of plan; usually close, fine
                        for budgeting but worth watching the off months.
                      </span>
                    </div>
                    <div className="pl-acc-sum-i">
                      <span className={'pl-acc-dot ' + accTone(sc.tech.nwMape, 'nw')} />
                      <span>
                        <b>Net worth</b> — stays fairly close on average, but it swings with the
                        markets, so treat the projected line as a guide for what's ahead.
                      </span>
                    </div>
                  </div>

                  <div className="pl-acc">
                    <div className="pl-acc-head">
                      <div>Month</div>
                      <div>Cash-flow miss</div>
                      <div>Net-worth miss</div>
                    </div>
                    {sc.periods.map((p) => (
                      <div key={p.label} className="pl-acc-row">
                        <div className="pl-acc-m">{p.label}</div>
                        <div className="pl-acc-cell">
                          <div className="pl-acc-bar">
                            <span
                              className={
                                'pl-acc-fill ' +
                                (Math.abs(p.cf) <= 10
                                  ? 'good'
                                  : Math.abs(p.cf) <= 25
                                    ? 'warn'
                                    : 'bad') +
                                ' ' +
                                (p.cf >= 0 ? 'pos' : 'neg')
                              }
                              style={{ width: (Math.min(Math.abs(p.cf), 25) / 25) * 50 + '%' }}
                            />
                          </div>
                          <span className="pl-acc-v">
                            {p.cf > 0 ? '+' : '−'}
                            {Math.abs(p.cf).toFixed(1)}%
                          </span>
                        </div>
                        <div className="pl-acc-cell">
                          <div className="pl-acc-bar">
                            <span
                              className={
                                'pl-acc-fill ' +
                                (Math.abs(p.nw) <= 20
                                  ? 'good'
                                  : Math.abs(p.nw) <= 40
                                    ? 'warn'
                                    : 'bad') +
                                ' ' +
                                (p.nw >= 0 ? 'pos' : 'neg')
                              }
                              style={{ width: (Math.min(Math.abs(p.nw), 40) / 40) * 50 + '%' }}
                            />
                          </div>
                          <span className="pl-acc-v">
                            {p.nw > 0 ? '+' : '−'}
                            {Math.abs(p.nw).toFixed(1)}%
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="pl-acc-legend">
                    <span>
                      Cash flow is judged tightly (on track ≤ 10%); market-driven net worth gets a
                      looser bar (≤ 20%).
                    </span>
                    <span className="pl-acc-key">
                      <i className="good" />
                      on track
                      <i className="warn" />
                      off
                      <i className="bad" />
                      way off
                    </span>
                  </div>
                  <button className="pl-tech-toggle" onClick={() => setShowTech((s) => !s)}>
                    {showTech ? 'Hide the technical numbers ▾' : 'Show the technical numbers ▸'}
                  </button>
                  {showTech && (
                    <div className="pl-tiles pl-tiles--tech" style={{ marginTop: 14 }}>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Cash-flow error (MAPE){' '}
                          <Info
                            up
                            text="Mean absolute percentage error — the average size of our monthly cash-flow miss, ignoring direction. Lower is better."
                          />
                        </div>
                        <div className="pl-tile-v">{sc.tech.mape}%</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Cash-flow avg miss{' '}
                          <Info
                            up
                            text="The typical dollar gap between forecast and actual net cash flow in a month."
                          />
                        </div>
                        <div className="pl-tile-v mask">{fBaseWhole(sc.tech.avgMiss)}</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Cash-flow bias{' '}
                          <Info
                            up
                            r
                            text="Whether we lean optimistic or pessimistic. A negative figure means we tend to under-forecast — actuals usually come in below plan."
                          />
                        </div>
                        <div className="pl-tile-v neg mask">{fSigned(sc.tech.bias)}</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Net-worth error (MAPE){' '}
                          <Info
                            up
                            text="Average size of our monthly net-worth miss, as a %. Naturally larger and noisier than cash flow, since markets move."
                          />
                        </div>
                        <div className="pl-tile-v">{sc.tech.nwMape}%</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Net-worth avg miss{' '}
                          <Info
                            up
                            text="The typical dollar gap between forecast and actual net worth in a month."
                          />
                        </div>
                        <div className="pl-tile-v mask">{fBaseWhole(sc.tech.nwAvgMiss)}</div>
                      </div>
                      <div className="pl-tile">
                        <div className="pl-tile-k">
                          Net-worth drift{' '}
                          <Info
                            up
                            r
                            text="Whether the projected net-worth line gradually pulls above or below actuals over time. Positive means the projection tends to run ahead."
                          />
                        </div>
                        <div className="pl-tile-v mask">{fSigned(sc.tech.nwDrift)}</div>
                      </div>
                    </div>
                  )}
                  <div className="pl-foot">
                    These stats use all{' '}
                    <b style={{ color: 'var(--muted)' }}>{sc.monthsTracked} closed months</b> on
                    record; the table above shows the most recent{' '}
                    <b style={{ color: 'var(--muted)' }}>{sc.periods.length}</b>. We projected ~
                    <span className="mask">{fBaseWhole(sc.projectedMonthlyNet)}</span>/mo net.
                  </div>
                </div>
              </div>
            )}
          </div>
          {/* /pl-main (trust) */}
        </>
      )}

      {/* ════════ modals ════════ */}
      {editShock &&
        (() => {
          const meta = SHOCK_META[editShock]!
          const single = meta.single
          return (
            <PlanningModal
              onClose={() => setEditShock(null)}
              label={meta.label}
              style={{ width: 380 }}
            >
              <div className="pl-modal-head">
                <h3>{meta.label}</h3>
                <button
                  className="pl-modal-x"
                  onClick={() => setEditShock(null)}
                  aria-label="Close"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="pl-modal-body">
                <div className="pl-foot" style={{ marginBottom: 14, lineHeight: 1.5 }}>
                  {meta.desc}
                </div>
                <div style={{ display: 'flex', gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <label className="pl-modal-label" htmlFor="shock-from">
                      {single ? 'Age it hits' : 'From age'}
                    </label>
                    <input
                      id="shock-from"
                      className="pl-input"
                      type="number"
                      min={currentAgeR}
                      max={lifeExp}
                      step={1}
                      value={shockFrom}
                      onChange={(e) => setShockFrom(e.target.value)}
                    />
                  </div>
                  {!single && (
                    <div style={{ flex: 1 }}>
                      <label className="pl-modal-label" htmlFor="shock-to">
                        To age
                      </label>
                      <input
                        id="shock-to"
                        className="pl-input"
                        type="number"
                        min={currentAgeR}
                        max={lifeExp}
                        step={1}
                        value={shockTo}
                        onChange={(e) => setShockTo(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              </div>
              <div className="pl-modal-foot">
                {meta.on && (
                  <Button
                    variant="secondary"
                    size="dense"
                    style={{ marginRight: 'auto', color: 'var(--bad)' }}
                    onClick={() => disableShock(editShock!)}
                  >
                    Turn off
                  </Button>
                )}
                <Button variant="secondary" size="dense" onClick={() => setEditShock(null)}>
                  Cancel
                </Button>
                <Button variant="primary" size="dense" disabled={!shockFrom} onClick={applyShock}>
                  {meta.on ? 'Save' : 'Add shock'}
                </Button>
              </div>
            </PlanningModal>
          )
        })()}
      {addingEvent && (
        <PlanningModal
          onClose={() => setAddingEvent(false)}
          label="Edit life event"
          style={{ width: 380 }}
        >
          <div className="pl-modal-head">
            <h3>{editEventId != null ? 'Edit life event' : 'Add a life event'}</h3>
            <button className="pl-modal-x" onClick={() => setAddingEvent(false)} aria-label="Close">
              <X size={16} />
            </button>
          </div>
          <div className="pl-modal-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label className="pl-modal-label" htmlFor="ev-label">
                  What is it?
                </label>
                <input
                  id="ev-label"
                  className="pl-input"
                  placeholder="Wedding, new car, big trip…"
                  value={evLabel}
                  onChange={(e) => setEvLabel(e.target.value)}
                />
              </div>
              <div>
                <label className="pl-modal-label" htmlFor="ev-amount">
                  Amount
                </label>
                <input
                  id="ev-amount"
                  className="pl-input"
                  type="number"
                  min={0}
                  step={1000}
                  placeholder="50000"
                  value={evAmount}
                  onChange={(e) => setEvAmount(e.target.value)}
                />
              </div>
              <div>
                <label className="pl-modal-label">Direction</label>
                <div className="pl-whatif-row" style={{ marginTop: 6 }}>
                  <button
                    className={'pl-wchip' + (evKind === 1 ? ' on' : '')}
                    onClick={() => setEvKind(1)}
                  >
                    Money out (spend)
                  </button>
                  <button
                    className={'pl-wchip' + (evKind === -1 ? ' on' : '')}
                    onClick={() => setEvKind(-1)}
                  >
                    Money in (windfall)
                  </button>
                </div>
              </div>
              <div>
                <label className="pl-modal-label" htmlFor="ev-age">
                  Age it happens
                </label>
                <input
                  id="ev-age"
                  className="pl-input"
                  type="number"
                  min={currentAgeR}
                  max={lifeExp}
                  step={1}
                  placeholder={String(currentAgeR + 5)}
                  value={evAge}
                  onChange={(e) => setEvAge(e.target.value)}
                />
              </div>
            </div>
          </div>
          <div className="pl-modal-foot">
            <Button variant="secondary" size="dense" onClick={() => setAddingEvent(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="dense" disabled={!evAmount} onClick={confirmAddEvent}>
              {editEventId != null ? 'Save' : 'Add to chart'}
            </Button>
          </div>
        </PlanningModal>
      )}
      {addingIncome && (
        <PlanningModal
          onClose={() => setAddingIncome(false)}
          label="Add income stream"
          style={{ width: 380 }}
        >
          <div className="pl-modal-head">
            <h3>{editIncomeIdx != null ? 'Edit income stream' : 'Add income stream'}</h3>
            <button
              className="pl-modal-x"
              onClick={() => setAddingIncome(false)}
              aria-label="Close"
            >
              <X size={16} />
            </button>
          </div>
          <div className="pl-modal-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label className="pl-modal-label" htmlFor="inc-label">
                  Label
                </label>
                <input
                  id="inc-label"
                  className="pl-input"
                  placeholder="CPF LIFE, Pension, Rental…"
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                />
              </div>
              <div>
                <label className="pl-modal-label" htmlFor="inc-amount">
                  Monthly amount
                </label>
                <input
                  id="inc-amount"
                  className="pl-input"
                  type="number"
                  min={0}
                  step={100}
                  placeholder="0"
                  value={newAmount}
                  onChange={(e) => setNewAmount(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <label className="pl-modal-label" htmlFor="inc-age">
                    Start age
                  </label>
                  <input
                    id="inc-age"
                    className="pl-input"
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    placeholder="65"
                    value={newAge}
                    onChange={(e) => setNewAge(e.target.value)}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label className="pl-modal-label" htmlFor="inc-end">
                    End age{' '}
                    <span
                      style={{
                        color: 'var(--faint)',
                        fontWeight: 500,
                        textTransform: 'none',
                        letterSpacing: 0,
                      }}
                    >
                      • optional
                    </span>
                  </label>
                  <input
                    id="inc-end"
                    className="pl-input"
                    type="number"
                    min={0}
                    max={100}
                    step={1}
                    placeholder="lifelong"
                    value={newEndAge}
                    onChange={(e) => setNewEndAge(e.target.value)}
                  />
                </div>
              </div>
            </div>
          </div>
          <div className="pl-modal-foot">
            <Button variant="secondary" size="dense" onClick={() => setAddingIncome(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="dense"
              disabled={!newAmount || !newAge}
              onClick={confirmAdd}
            >
              {editIncomeIdx != null ? 'Save' : 'Add'}
            </Button>
          </div>
        </PlanningModal>
      )}

      {naming &&
        (() => {
          const loadedScenCurrent = loadedFrom ? scenarios.find((x) => x.id === loadedFrom) : null
          const dirty = !!loadedScenCurrent && activeScen !== loadedFrom
          return (
            <PlanningModal
              onClose={() => setNaming(false)}
              label="Save scenario"
              style={{ width: 400 }}
            >
              <div className="pl-modal-head">
                <h3>{dirty ? 'Save changes' : 'Save scenario'}</h3>
                <button className="pl-modal-x" onClick={() => setNaming(false)} aria-label="Close">
                  <X size={16} />
                </button>
              </div>
              <div className="pl-modal-body">
                {dirty && (
                  <div className="pl-foot" style={{ marginBottom: 12, lineHeight: 1.5 }}>
                    You loaded <b style={{ color: 'var(--muted)' }}>{loadedScenCurrent!.name}</b>{' '}
                    and changed the levers. Update it in place, or keep it and save these settings
                    as a new scenario.
                  </div>
                )}
                <label className="pl-modal-label" htmlFor="scen-name">
                  {dirty ? 'New scenario name' : 'Name'}
                </label>
                <input
                  id="scen-name"
                  className="pl-input"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') confirmSave()
                  }}
                  placeholder="e.g. Retire at 52"
                />
                <div className="pl-foot" style={{ marginTop: 12 }}>
                  Captures retire <b style={{ color: 'var(--muted)' }}>{retireAge}</b> •{' '}
                  <b className="mask" style={{ color: 'var(--muted)' }}>
                    {fBaseWhole(saving)}
                  </b>
                  /mo • {ret}% return • retirement spending{' '}
                  <b className="mask" style={{ color: 'var(--muted)' }}>
                    {fBaseWhole(monthlyRetirementSpending)}/mo
                  </b>{' '}
                  →{' '}
                  <b
                    className={'' + tone}
                    style={{
                      color:
                        tone === 'pos'
                          ? 'var(--good)'
                          : tone === 'warn'
                            ? 'var(--warn)'
                            : 'var(--bad)',
                    }}
                  >
                    {successPct}% odds
                  </b>
                </div>
              </div>
              <div className="pl-modal-foot">
                {dirty ? (
                  <>
                    <Button variant="secondary" size="dense" onClick={() => setNaming(false)}>
                      Cancel
                    </Button>
                    <Button variant="secondary" size="dense" onClick={confirmSave}>
                      Save as new
                    </Button>
                    <Button variant="primary" size="dense" onClick={saveScenarioInPlace}>
                      Update scenario
                    </Button>
                  </>
                ) : (
                  <>
                    <Button variant="secondary" size="dense" onClick={() => setNaming(false)}>
                      Cancel
                    </Button>
                    <Button variant="primary" size="dense" onClick={confirmSave}>
                      Save scenario
                    </Button>
                  </>
                )}
              </div>
            </PlanningModal>
          )
        })()}

      {confirmDel && (
        <PlanningModal
          onClose={() => setConfirmDel(null)}
          label="Delete scenario"
          style={{ width: 380 }}
        >
          <div className="pl-modal-head">
            <h3>Delete scenario</h3>
            <button className="pl-modal-x" onClick={() => setConfirmDel(null)} aria-label="Close">
              <X size={16} />
            </button>
          </div>
          <div className="pl-modal-body">
            <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.55 }}>
              Delete <b style={{ color: 'var(--ink)' }}>{confirmDel.name}</b>? This can't be undone.
            </p>
          </div>
          <div className="pl-modal-foot">
            <Button variant="secondary" size="dense" onClick={() => setConfirmDel(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="dense"
              onClick={() => {
                removeScenario(confirmDel.id)
                if (planB && planB.id === confirmDel.id) setPlanB(null)
                setConfirmDel(null)
                toast('Scenario deleted')
              }}
            >
              Delete
            </Button>
          </div>
        </PlanningModal>
      )}
    </div>
  )
}
