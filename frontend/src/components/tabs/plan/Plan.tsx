// plan/Plan.tsx — Plan tab shell. Ported from design_handoffs/plan_handoff/prototype/plan/app.jsx
// (the `App` shell + `FireSettingsModal`), adapted to the real RouterShell:
//   • drops the prototype's standalone nav rail / .onw-root / .pl-content (the shell owns those)
//   • drops the prototype's bespoke LuciusSide + TweaksPanel (Lucius is the shared dock; see
//     IMPLEMENTATION_PLAN.md "Lucius"). "Ask Lucius" routes to the onAskLucius prefill path.
//   • FIRE Settings persists to the real /api/profile (the server recomputes /api/fire).
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  ActionMenu,
  Button,
  Container,
  ErrorState,
  PageHeader,
  PageTabs,
  TabSkeleton,
} from '../../ui'
import type { ActionMenuItem } from '../../ui'
import { CategoriesManager } from './categoriesManager'
import { X } from './icons'
import { ChartArea, MoreHorizontal, Sunrise } from '../../../ui/icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useBreakpoint } from '../../../hooks/useBreakpoint'
import { fBaseCompact, getCurrencySymbol } from '../../../utils/format'
import type { RegisterFilter } from './budget'
import { BudgetLensPane } from './budgetLens/BudgetLensPane'
import { MethodMenu } from './budgetLens/MethodMenu'
import { METHODS } from './budgetLens/methodsData'
import { useBudgetLens } from './budgetLens/useBudgetLens'
import { CashFlowTab } from './cashflow'
import { OutlookTab } from './outlook'
import { usePlanSubTab } from './usePlanSubTab'
import { OutlookSetupModal } from './OutlookSetupModal'
import { PlanEmptyBook } from './PlanEmptyBook'
import { onScreenCommand } from '../../../lucius/bus'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { useQueryCache } from '../../../state/queryCache'
import { stickyTopbarOffset } from '../../../utils/stickyTopbarOffset'
import './plan.css'
import {
  PROFILE_KEY,
  fetchJson,
  useAccounts,
  useNetWorth,
  useProfile,
} from '../../../hooks/serverState'
import { PlanningModal } from './PlanningModal'
import { useFreshStart } from './useFreshStart'
import { thisMonth } from '../../../hooks/useBudget'
import { budgetSkeletonVariant } from '../../../utils/budgetMethodMemory'
import {
  EMPTY_FIRE_DRAFT,
  fireHorizon,
  fireNumberOf,
  fireSwrMatches,
  fireSwrNote,
  fireSwrSuggestion,
  saveFireDraft,
  seedFireDraft,
} from './fireSettings'
import type { FireDraft, FireProfile } from './fireSettings'

function hasBirthDate(value: unknown): boolean {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  return (
    !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value && date < new Date()
  )
}

// ── FIRE Settings modal — ported from app.jsx. Seeds from /api/profile; saves the inputs back
// (the server's /api/fire recomputes FIRE number / projection / the shared simulation).
// The field set, seeding, SWR suggestion, derived FIRE number and PUT payload live in
// ./fireSettings so this modal and the Settings → Profile one cannot disagree. ──
function FireSettingsModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const [s, setS] = useState<FireDraft>(EMPTY_FIRE_DRAFT)
  const [saving, setSaving] = useState(false)
  const profileQuery = useQueryCache(PROFILE_KEY, () => fetchJson<FireProfile>(PROFILE_KEY), {
    staleTime: 0,
  })

  // Seed the fields from the real profile each time the modal opens.
  useEffect(() => {
    if (!open || !profileQuery.data) return
    const p = profileQuery.data
    void Promise.resolve().then(() => setS(seedFireDraft(p)))
  }, [open, profileQuery.data])

  function set(k: keyof FireDraft, v: string) {
    setS((prev) => ({ ...prev, [k]: v === '' ? '' : Number(v) }))
  }

  const horizon = fireHorizon(s)
  const swrSuggest = fireSwrSuggestion(s)
  const swrMatches = fireSwrMatches(s)
  const swrNote = fireSwrNote(s)
  const fireNum = fireNumberOf(s)

  async function save() {
    setSaving(true)
    await saveFireDraft(s, profileQuery.data)
    setSaving(false)
    onSaved()
    onClose()
  }

  if (!open) return null
  const FIELDS: Array<{
    k: keyof FireDraft
    label: string
    step: number
    ph: string
    tip: string
  }> = [
    {
      k: 'monthlyRetirementSpending',
      label: 'Monthly retirement spending',
      step: 100,
      ph: `${getCurrencySymbol()} / month`,
      tip: "What you expect to spend per month once retired, in today's dollars. It is annualized only when calculating your FIRE number.",
    },
    {
      k: 'withdrawalRate',
      label: 'Safe withdrawal rate %',
      step: 0.1,
      ph: '4',
      tip: 'The % of savings withdrawn each year in retirement. The classic 4% rule assumes ~30 years — retiring early stretches it longer, so a lower rate is safer.',
    },
    {
      k: 'expectedReturn',
      label: 'Expected return %',
      step: 0.5,
      ph: '7, before inflation',
      tip: "Roughly how much your investments grow each year, before inflation. That means future balances look bigger than what they'll actually buy. For a \"today's money\" view, subtract your country's expected long-run inflation.",
    },
    {
      k: 'volatility',
      label: 'Return volatility %',
      step: 1,
      ph: '14–15 for diversified portfolio',
      tip: 'How much returns swing year to year (standard deviation). ~14–15% is typical for a diversified global portfolio; a higher number widens the range of outcomes in the simulations.',
    },
    {
      k: 'targetAge',
      label: 'Target retirement age',
      step: 1,
      ph: 'when you want to stop working',
      tip: "The age you want to stop working. Projections check whether you'll reach your FIRE number by then.",
    },
    {
      k: 'lifeExpectancy',
      label: 'Life expectancy',
      step: 1,
      ph: '90',
      tip: 'How long your money needs to last. A longer horizon lowers the safe withdrawal rate, since savings must stretch further.',
    },
    {
      k: 'monthlySaving',
      label: 'Monthly saving until retire',
      step: 50,
      ph: 'net savings per month',
      tip: 'Net amount you add to investments each month until you retire. Drives how fast you reach your FIRE number.',
    },
  ]
  return (
    <PlanningModal
      onClose={onClose}
      label="FIRE Settings"
      className="fire-modal"
      style={{ width: 560, maxWidth: '100%' }}
    >
      <div className="pl-modal-head">
        <h3>FIRE Settings</h3>
        <button className="pl-modal-x" onClick={onClose} aria-label="Close">
          <X />
        </button>
      </div>
      <div className="pl-modal-body" style={{ overflowY: 'auto', maxHeight: 'calc(85vh - 130px)' }}>
        <p style={{ margin: '0 0 16px', fontSize: 12.5, color: 'var(--faint)', lineHeight: 1.5 }}>
          These settings affect both the Cash Flow and Outlook tabs.
        </p>
        {fireNum !== null && (
          <div className="fire-formula">
            <span className="fire-formula-k">FIRE number</span>
            <span className="fire-formula-calc">
              {getCurrencySymbol()}
              {Number(s.monthlyRetirementSpending).toLocaleString()} × 12 ÷ {s.withdrawalRate}%
            </span>
            <span className="fire-formula-eq">=</span>
            <strong className="fire-formula-v mask">{fBaseCompact(fireNum)}</strong>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 20px' }}>
          {FIELDS.map(({ k, label, step, ph, tip }, i) => (
            <div key={k}>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11,
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'var(--faint)',
                  marginBottom: 6,
                }}
              >
                {label}
                {tip && (
                  <span
                    className="stat-info"
                    tabIndex={0}
                    role="button"
                    aria-label="More info"
                    style={{ flexShrink: 0 }}
                  >
                    i
                    <span
                      className={'stat-pop' + (i % 2 === 1 ? ' r' : '') + (i < 4 ? ' down' : '')}
                    >
                      {tip}
                    </span>
                  </span>
                )}
              </label>
              <input
                type="number"
                step={step}
                className="pl-input"
                placeholder={ph}
                value={s[k] === '' ? '' : s[k]}
                onChange={(e) => set(k, e.target.value)}
              />
              {k === 'withdrawalRate' &&
                swrSuggest !== null &&
                (swrMatches ? (
                  <div className="fire-suggest matched">
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                    <span>Matches a {horizon}-yr retirement</span>
                  </div>
                ) : (
                  <>
                    {swrNote && <div className="fire-suggest-note">{swrNote}</div>}
                    <Button
                      variant="secondary"
                      size="dense"
                      className="fire-suggest-use"
                      onClick={() => set('withdrawalRate', String(swrSuggest))}
                    >
                      Use {swrSuggest}%
                    </Button>
                  </>
                ))}
            </div>
          ))}
        </div>
      </div>
      <div className="pl-modal-foot">
        <Button variant="secondary" size="dense" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="primary" size="dense" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save settings'}
        </Button>
      </div>
    </PlanningModal>
  )
}

export function Plan({
  onAskLucius,
  onOpenTxn,
  onOpenRegister,
  subTabRequest,
}: {
  onAskLucius: (text: string) => void
  onOpenTxn?: (raw: Record<string, unknown>) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  /** Deep-link request (?sub=outlook — e.g. a parked screen-command chip). */
  subTabRequest?: string
}) {
  const navigate = useNavigate()
  const openAccounts = () =>
    navigate({
      to: '/accounts',
      search: { account: undefined, txn: undefined, review: undefined },
    })
  const isMobile = useIsMobile()
  // The ONE net worth (GET /api/net-worth) — seeds Outlook's FIRE projection. Not a
  // client-side portfolio sum; see useNetWorth's doc comment and
  // ADR 2026-07-31-single-source-net-worth.
  const netWorthQuery = useNetWorth()
  const accountsQuery = useAccounts()
  const profileQuery = useProfile()
  // A zero balance can be real. No accounts is the first-run predicate: a forecast built
  // from default profile assumptions must not masquerade as a judgment about the user.
  const emptyBook = accountsQuery.data?.length === 0
  const missingBirthDate = !hasBirthDate(profileQuery.data?.date_of_birth)
  const retirementSpending = Number(
    profileQuery.data?.fire_monthly_retirement_spending ?? profileQuery.data?.monthly_expenses ?? 0,
  )
  const [tab, setTab] = usePlanSubTab(subTabRequest)
  // Desktop Budget is a fixed-height WORKSPACE (owner, 2026-09-15): the shell column is bound
  // to the viewport and the envelope table and the rail scroll as two independent panes under
  // static hero + toolbar chrome — the YNAB shape — instead of one page scroll with a sticky
  // stack simulated on top of it (which needed a height cap, a cut edge and four measured
  // offsets to keep a too-tall rail reachable, and still blocked the wheel when the rail
  // happened to fit). Same gate as the rail itself (budget.tsx showRail): ≥1024px, not mobile.
  // Cash Flow, Outlook and every phone/tablet width keep the ordinary page-scrolling flow.
  const bp = useBreakpoint()
  // Lens selection lives here, not in BudgetLensPane: the control renders in the sticky
  // masthead (on the PageTabs row) while the panes it switches render below in .pl-tabwrap.
  const lens = useBudgetLens()
  const firstRun = lens.introLoaded && !lens.introDismissed && !lens.hasBudgetData
  // The workspace is Zero-Based's OWN layout (its table + rail panes scroll inside a fixed page).
  // 50/30/20 and the first-run chooser are ordinary pages — in the workspace nothing page-scrolls,
  // so anything taller than the window would be clipped (owner, 2026-09-25).
  const workspace =
    tab === 'budget' &&
    !isMobile &&
    (bp === 'lg' || bp === 'xl') &&
    lens.homeId === 'zero_based' &&
    !firstRun
  // In the workspace nothing page-scrolls, so the chrome above the panes — title row, sub-tabs
  // row — would sit there forever eating vertical space the panes
  // need on a laptop. It must instead behave exactly as it does on an ordinary page (owner,
  // 2026-09-15, after an instant fold was rejected): scroll AWAY 1:1 with the gesture, with
  // the RTA hero and the toolbar pinning once they reach the top, and on the way back the
  // pane's content returns to its top first and THEN the chrome comes back, again 1:1.
  //
  // Driven by the TABLE pane's native scroll position (plan.css turns it into transforms through
  // a CSS scroll timeline on Chrome 116+/Safari 26+; Firefox gets the same length from this
  // script, one scroll event at a time). This effect publishes the lengths the CSS needs, keeps
  // the one re-basable offset below, and routes wheels that land outside the table pane.
  const pageRef = useRef<HTMLDivElement>(null)
  // The effect below re-runs when the Budget lens finishes loading — its pane (and the hero the
  // chrome tracks) only mounts then.
  const lensLoading = tab === 'budget' && lens.loading
  useEffect(() => {
    const page = pageRef.current
    if (!workspace || !page) return
    // Engines with CSS scroll timelines (Chrome 116+, Safari 26+) feed the two scroll positions
    // to the CSS themselves, on the compositor. Others (Firefox) get them from this script, one
    // scroll event at a time, into the SAME variables — one model, one rendering.
    const sda =
      typeof CSS !== 'undefined' &&
      CSS.supports('animation-timeline: scroll()') &&
      CSS.supports('timeline-scope: --x')
    page.classList.add('pl-page--sda')
    if (!sda) page.classList.add('pl-page--sda-script')
    // Layout-based (offsetTop chain up to the page, which is position:relative), so the
    // transforms the CSS applies never contaminate the measurement.
    const offsetWithinPage = (el: HTMLElement) => {
      let y = 0
      let n: HTMLElement | null = el
      while (n && n !== page) {
        y += n.offsetTop
        n = n.offsetParent as HTMLElement | null
      }
      return y
    }
    // ── The model ─────────────────────────────────────────────────────────────────────────
    // The chrome's travel is T = clamp(0, max, k + t), t being the TABLE pane's scrollTop:
    // plan.css computes it from that pane's scroll timeline and holds the table's rows by T, so
    // the chrome moves first and the rows after. k (--pl-base) is only ever re-based in ways
    // that keep T where it is, so nothing can drift:
    //   · the table scrolled up past the point where the chrome is fully back → k = −t, so the
    //     next scroll down moves the chrome at once instead of first repaying the overshoot;
    //   · a wheel on the side panel brings the chrome back → k = T − t, so the table pane's
    //     move shows only as chrome, never as its rows.
    // The side panel never drives T through its own scroll — a two-pane hand-off drifted
    // (owner, 2026-09-25). A wheel over it moves ONLY the chrome (through the table pane while
    // its rows are held), and at the panel's own ends it simply stops: a gesture on the panel
    // never moves the table's rows.
    const MAIN = '.pl-budget-main'
    const RAIL = '.pl-budget-rail'
    const pane = (sel: string) => page.querySelector<HTMLElement>(sel)
    let k = 0
    let tMain = Math.max(0, pane(MAIN)?.scrollTop ?? 0)
    let tRail = Math.max(0, pane(RAIL)?.scrollTop ?? 0)
    let maxTravel = 0
    const publishK = () => page.style.setProperty('--pl-base', `${k}px`)
    const travel = () => Math.min(maxTravel, Math.max(0, k + tMain))
    const onScroll = (e: Event) => {
      const el = e.target
      if (!(el instanceof HTMLElement)) return
      if (el.matches(RAIL)) {
        tRail = Math.max(0, el.scrollTop)
        return
      }
      if (!el.matches(MAIN)) return
      tMain = Math.max(0, el.scrollTop)
      if (!sda) page.style.setProperty('--pl-t-main', `${tMain}px`)
      if (k + tMain < -0.5) {
        k = -tMain
        publishK()
      }
    }
    const wheelDelta = (e: WheelEvent) => (e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY)
    // Nothing page-scrolls in the workspace, so a wheel over the chrome (header, hero, toolbar)
    // hit no scroller and did nothing (owner, 2026-09-25): it goes to the table pane, like a
    // page. The listeners are bound to those elements (and the panel) ONLY, never the page: a
    // non-passive wheel listener makes the browser wait on script before scrolling anything
    // beneath it.
    const onChromeWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return
      const main = pane(MAIN)
      if (!main) return
      e.preventDefault()
      main.scrollBy({ top: wheelDelta(e) })
    }
    const onRailWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return
      const main = pane(MAIN)
      const rail = e.currentTarget as HTMLElement
      if (!main) return
      const dy = wheelDelta(e)
      const T = travel()
      if (dy > 0) {
        // Down: the chrome leaves first — never more than what's left of its travel, so the
        // table pane only moves while its rows are held — then the panel scrolls.
        if (T >= maxTravel - 0.5) return
        if (k + tMain < 0) {
          k = -tMain
          publishK()
        }
        const take = Math.min(dy, maxTravel - T)
        e.preventDefault()
        main.scrollBy({ top: take })
        tMain = Math.max(0, main.scrollTop)
        if (dy > take) rail.scrollBy({ top: dy - take })
      } else if (dy < 0) {
        // Up: the panel's own content comes back first; at its top, the chrome follows.
        if (tRail > 0.5 || T <= 0.5) return
        k = T - tMain
        publishK()
        e.preventDefault()
        main.scrollBy({ top: -Math.min(-dy, T) })
        tMain = Math.max(0, main.scrollTop)
      }
    }
    const wheelBound = new Map<HTMLElement, (e: WheelEvent) => void>()
    const bindWheel = () => {
      const want = new Map<HTMLElement, (e: WheelEvent) => void>()
      for (const sel of ['.ui-page-header', '.pl-mast-sticky', '.pl-summary', '.pl-toolbar'])
        page.querySelectorAll<HTMLElement>(sel).forEach((el) => want.set(el, onChromeWheel))
      page.querySelectorAll<HTMLElement>(RAIL).forEach((el) => want.set(el, onRailWheel))
      for (const [el, fn] of wheelBound)
        if (want.get(el) !== fn) {
          el.removeEventListener('wheel', fn)
          wheelBound.delete(el)
        }
      for (const [el, fn] of want)
        if (!wheelBound.has(el)) {
          el.addEventListener('wheel', fn, { passive: false })
          wheelBound.set(el, fn)
        }
    }
    page.addEventListener('scroll', onScroll, true)
    // The one length the CSS needs: --pl-max, the chrome's total travel. The hero no longer
    // pins (owner, 2026-09-25: "too annoying"), but the title and sub-tabs rows do (owner, same
    // day) — the hero scrolls away beneath them until the toolbar rests under the sub-tabs
    // row. Re-measured on resize and content changes, never per frame (offsetTop forces layout).
    let lastMax = -1
    const measure = () => {
      const toolbar = page.querySelector<HTMLElement>('.pl-toolbar')
      if (!toolbar) return
      const hero = page.querySelector<HTMLElement>('.pl-summary--pinned')
      const pinned = page.querySelector<HTMLElement>(':scope > .pl-mast-sticky')
      // The toolbar rests where its buttons sit exactly as far below the tab strip as the table
      // sits below them (owner, 2026-09-25): resting it under the sub-tabs row stacked that
      // row's bottom padding on the toolbar's own top padding — 35px above the buttons, 20px
      // below. Its top padding slides under the (opaque) sub-tabs row instead.
      const strip = pinned?.querySelector<HTMLElement>('.ui-page-tabs') ?? pinned
      const stripBottom = strip ? offsetWithinPage(strip) + strip.offsetHeight : 0
      const pad = getComputedStyle(toolbar)
      const restAt =
        stripBottom + (parseFloat(pad.paddingBottom) || 0) - (parseFloat(pad.paddingTop) || 0)
      // …and never less than what tucks the hero's own bottom edge under the sub-tabs row: the
      // toolbar overlaps the hero by 1px (the shared `- 1px` formula), so its bottom border
      // showed as a hairline across the page (owner, same day).
      const rowBottom = pinned ? offsetWithinPage(pinned) + pinned.offsetHeight : 0
      const heroTuck = hero ? offsetWithinPage(hero) + hero.offsetHeight - rowBottom : 0
      const max = Math.max(0, offsetWithinPage(toolbar) - restAt, heroTuck)
      maxTravel = max
      if (max !== lastMax) {
        page.style.setProperty('--pl-max', `${max}px`)
        lastMax = max
      }
    }
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    const observeChrome = () => {
      ro?.disconnect()
      for (const sel of [
        '.ui-page-header',
        '.pl-mast-sticky',
        '.pl-summary--pinned',
        '.pl-toolbar',
      ])
        page.querySelectorAll<HTMLElement>(sel).forEach((el) => ro?.observe(el))
      bindWheel()
      measure()
    }
    // Chrome pieces mount and unmount with the data; re-observe when the page's children change.
    const mo = new MutationObserver(observeChrome)
    mo.observe(page, { childList: true, subtree: true })
    window.addEventListener('resize', measure)
    publishK()
    observeChrome()
    return () => {
      mo.disconnect()
      ro?.disconnect()
      window.removeEventListener('resize', measure)
      for (const [el, fn] of wheelBound) el.removeEventListener('wheel', fn)
      page.removeEventListener('scroll', onScroll, true)
      page.classList.remove('pl-page--sda', 'pl-page--sda-script')
      for (const v of ['--pl-max', '--pl-base', '--pl-t-main']) page.style.removeProperty(v)
    }
  }, [workspace, lensLoading])
  const [fireOpen, setFireOpen] = useState(false)
  // "Manage categories" is owned here, not by any one method's pane (owner, 2026-08-23):
  // categories are not zero-based's property — every engine maps spending through them — so the
  // control must not vanish when another engine is home and BudgetTab is unmounted. It sits in
  // the Budget "…" menu (and 50/30/20's own toolbar); it left the page header beside FIRE
  // Settings on 2026-09-25 (owner). The counter is how an edit here still refreshes the budget.
  const [catMgrOpen, setCatMgrOpen] = useState(false)
  const [catVersion, setCatVersion] = useState(0)
  const [fireVersion, setFireVersion] = useState(0)
  // Shared by the FIRE Settings modal's save AND Outlook's "Update my FIRE plan" — whichever
  // screen last wrote the profile's FIRE fields, this is what tells the OTHER screen to re-fetch
  // and stay in sync (one shared source of truth, not two stores that silently drift apart).
  const handleFireSettingsChanged = () => setFireVersion((v) => v + 1)
  const [toast, setToast] = useState<string | null>(null)

  // A plan.* screen command needs the Outlook sub-tab visible to apply (its commands
  // register only while active). Passive: observes without consuming — the parked
  // command replays into Outlook's own handler once it activates.
  useEffect(() => onScreenCommand('plan.', () => setTab('outlook'), { passive: true }), [setTab])

  // Lucius persisted a profile change (salary/FIRE fields) → re-seed from /api/fire,
  // exactly like a FIRE-settings save (fixes the pre-existing staleness bug where a
  // chat update_profile never reached the Outlook levers).
  const profileDomainV = useDataVersion('profile')

  // Keep the sticky-stack offsets (--pl-mast-h / --pl-tools-h) exact at all times — a light rAF
  // sync loop that reads two offsetHeights per frame and only writes a CSS var when it changes
  // (ported verbatim from app.jsx; catches reflow-only height changes the ResizeObserver missed).
  useEffect(() => {
    const root = document.documentElement
    let raf = 0,
      lastM = -1,
      lastT = -1,
      lastS = -1,
      lastB = -1,
      lastStuck: boolean | null = null
    const tick = () => {
      const mast = document.querySelector('.pl-mast-sticky') as HTMLElement | null
      const tools = document.querySelector('.pl-toolbar, .cfx-bar') as HTMLElement | null
      const topbar = document.querySelector('.topbar') as HTMLElement | null
      // Tablet/desktop scroll the document, so the sibling shell bar occupies the same sticky
      // containing block. Phone scrolls .app-layout and therefore needs no shell-bar offset.
      const b = topbar ? stickyTopbarOffset(topbar, window.innerWidth) : 0
      const m = mast ? mast.offsetHeight : 0
      const t = tools ? tools.offsetHeight : 0
      const s = 0 // the RTA hero no longer pins, so nothing below it offsets by its height
      if (b !== lastB) {
        root.style.setProperty('--pl-top-h', b + 'px')
        lastB = b
      }
      if (m !== lastM) {
        root.style.setProperty('--pl-mast-h', m + 'px')
        lastM = m
      }
      if (t !== lastT) {
        root.style.setProperty('--pl-tools-h', t + 'px')
        lastT = t
      }
      if (s !== lastS) {
        root.style.setProperty('--pl-sum-h', s + 'px')
        lastS = s
      }
      // `.is-stuck` on the Budget toolbar (2026-09-15): true once it has actually pinned under
      // the masthead, so plan.css can show a dissolve strip below it ONLY then — at rest the
      // strip would tint the top of whatever sits in the 16px gap under it. getBoundingClientRect
      // is a pure read here (this loop already forces layout via offsetHeight), and the class
      // is orthogonal to React's constant className, so a re-render never wipes it.
      if (tools && mast && tools.classList.contains('pl-toolbar')) {
        const stuck = tools.getBoundingClientRect().top <= mast.getBoundingClientRect().bottom + 0.5
        if (stuck !== lastStuck) {
          tools.classList.toggle('is-stuck', stuck)
          lastStuck = stuck
        }
      }
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [])

  const showToast = useRef((msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 2400)
  }).current

  // "Start fresh from <this month>…" — a BUDGET action (it moves the envelope carry floor), so
  // it only offers itself while the Budget sub-tab is showing, and its config read is gated the
  // same way. Always keyed to the CURRENT month, never the browsed one: starting fresh in a
  // month you happen to be looking back at is not a thing anyone means.
  const onBudget = tab === 'budget'
  const freshStart = useFreshStart(thisMonth(), showToast, onBudget)
  const budgetOverflowItems: ActionMenuItem[] = onBudget
    ? [{ label: freshStart.label, onSelect: freshStart.open }]
    : []

  // A phone has no room for the MethodMenu in .pl-mast-row (gated !isMobile below), so its
  // overflow menu carries the same two actions: switch to the other method (confirmed in
  // SwitchMethodModal, exactly as on desktop) and the read-only comparison.
  const methodItems: ActionMenuItem[] = onBudget
    ? [
        ...METHODS.filter((m) => m.id !== lens.homeId).map((m) => ({
          label: `Switch to ${m.name}`,
          onSelect: () => lens.requestSwitch(m.id),
        })),
        { label: 'Compare methods…', onSelect: lens.openCompare },
      ]
    : []
  const manageCategories: ActionMenuItem = {
    label: 'Manage categories',
    onSelect: () => setCatMgrOpen(true),
  }
  const planOverflowItems: ActionMenuItem[] = [
    manageCategories,
    { label: 'FIRE Settings', onSelect: () => setFireOpen(true) },
    ...methodItems,
    ...budgetOverflowItems,
  ]

  return (
    <Container size="full">
      {/* `full` (not `wide`, which still caps at 1440px) — Accounts/Investments use no Container
          at all, so they never cap either; this matches that on wide screens (owner ask).
          .pl-page is FULL-WIDTH on every sub-tab (the header/tabs never shift when
          switching — owner); the per-tab width constraint lives on .pl-tabwrap below.
          The tabs bar MUST stay inside this tall column: position:sticky can never
          travel past its parent's bounds (a short header wrapper broke stickiness). */}
      <div
        className={
          // Full width on every sub-tab (owner ask) — matches Accounts/Investments, which
          // never cap their content width either. Previously only Outlook/Budget got this;
          // Cash Flow was still capped at 1120px (.pl-tabwrap's default), which is the one
          // this generalizes.
          'pl-page pl-page--wide' +
          // The 16px apron is breathing room under a PINNED hero (the RTA card, sticky on
          // desktop's Budget). Budget's hero doesn't pin on mobile either (un-pinned, owner
          // ask — a phone-width hero shouldn't eat a fixed slice of the viewport on every
          // scroll) — with nothing to reserve room under, the fat apron read as a stray gap
          // between the sticky tabs and the toolbar once the hero scrolled past.
          (tab !== 'budget' || isMobile ? ' pl-page--flatmast' : '') +
          (workspace ? ' pl-page--workspace' : '')
        }
        data-ctx-region="content"
        ref={pageRef}
      >
        {/* Screen-awareness title for the Lucius snapshot (reuses the existing dock). */}
        <span
          data-ctx-title={
            'Plan — ' + (tab === 'budget' ? 'Budget' : tab === 'cashflow' ? 'Cash Flow' : 'Outlook')
          }
          style={{ display: 'none' }}
          aria-hidden="true"
        />
        <PageHeader
          title="Plan"
          subtitle="Budget your month, project your wealth, and chart your path to financial independence"
          actions={
            isMobile ? (
              // Separate controls each wrapped the masthead onto its own row on phones and one
              // got clipped at the viewport edge — they fold into ONE overflow menu instead of
              // each shipping its own responsive collapse — the budgeting-method choices
              // included (methodItems).
              <ActionMenu
                label="Plan settings"
                trigger={<MoreHorizontal size={18} aria-hidden="true" />}
                items={planOverflowItems}
              />
            ) : (
              <Button variant="secondary" size="dense" onClick={() => setFireOpen(true)}>
                FIRE Settings
              </Button>
            )
          }
        />
        <div className="pl-mast-sticky">
          {/* Tabs left, lens control right, ONE row — the same shape Connectors uses. Sharing
              the row means the lens adds no height to the sticky masthead, so --pl-mast-h is
              identical on every sub-tab and nothing below shifts when you switch. The lens
              renders on Budget ONLY (a reading of the budget, not of Cash Flow/Outlook) and
              desktop ONLY — on phones it lives in the header's overflow menu above instead.
              The "…" beside it is the same story for the same reason: a SUB-TAB-scoped action
              belongs on the sub-tab's own row, not up in the page header among Plan-level
              buttons (which are the two that stay put on every sub-tab). */}
          <div className="pl-mast-row">
            <PageTabs
              ariaLabel="Plan sections"
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'budget', label: 'Budget' },
                { key: 'cashflow', label: 'Cash Flow' },
                { key: 'outlook', label: 'Outlook' },
              ]}
            />
            {onBudget && !isMobile && (
              <div className="pl-mast-tools">
                <MethodMenu lens={lens} />
                <ActionMenu
                  label="Budget options"
                  trigger={<MoreHorizontal size={18} aria-hidden="true" />}
                  items={[manageCategories, ...budgetOverflowItems]}
                />
              </div>
            )}
          </div>
        </div>
        {/* All three sub-tabs stay MOUNTED (hidden when inactive) so their data is retained —
            switching is instant with no re-fetch flash. The Outlook sim is gated on `active`
            so it doesn't compute while hidden. */}
        <div className="pl-tabwrap">
          <div style={tab === 'budget' ? undefined : { display: 'none' }}>
            {/* The engine catalogue decides WHICH pane mounts; until it answers, the real
                masthead stays up and the body skeleton is the method this device last showed. */}
            {lens.loading ? (
              <TabSkeleton
                variant={budgetSkeletonVariant()}
                label="Loading your budget…"
                header={false}
              />
            ) : (
              <BudgetLensPane
                lens={lens}
                onAskLucius={onAskLucius}
                toast={showToast}
                onOpenTxn={onOpenTxn}
                onOpenRegister={onOpenRegister}
                onManageCategories={() => setCatMgrOpen(true)}
                categoriesVersion={catVersion}
              />
            )}
          </div>
          <div style={tab === 'cashflow' ? undefined : { display: 'none' }}>
            {accountsQuery.initialLoading ? (
              <TabSkeleton variant="cashflow" label="Loading cash flow…" header={false} />
            ) : accountsQuery.data === undefined ? (
              <ErrorState
                message="Could not load accounts for cash flow."
                onRetry={() => void accountsQuery.refresh()}
              />
            ) : emptyBook ? (
              <PlanEmptyBook
                icon={<ChartArea size={28} strokeWidth={1.5} aria-hidden="true" />}
                title="Add an account to see cash flow"
                body="Cash flow needs a cash balance before it can show a projection."
                onAddAccount={openAccounts}
                onAskLucius={onAskLucius}
              />
            ) : (
              <CashFlowTab
                onAskLucius={onAskLucius}
                toast={showToast}
                onOpenRegister={onOpenRegister}
              />
            )}
          </div>
          <div style={tab === 'outlook' ? undefined : { display: 'none' }}>
            {accountsQuery.initialLoading || profileQuery.initialLoading ? (
              <TabSkeleton variant="cashflow" label="Loading outlook…" header={false} />
            ) : accountsQuery.data === undefined || profileQuery.data === undefined ? (
              <ErrorState
                message="Could not load the inputs for your outlook."
                onRetry={() => {
                  void accountsQuery.refresh()
                  void profileQuery.refresh()
                }}
              />
            ) : emptyBook ? (
              <PlanEmptyBook
                icon={<Sunrise size={28} strokeWidth={1.5} aria-hidden="true" />}
                title="Add your finances to see an outlook"
                body="Your retirement outlook starts with an account balance. No forecast is available yet."
                onAddAccount={openAccounts}
                onAskLucius={onAskLucius}
              />
            ) : missingBirthDate || retirementSpending <= 0 ? (
              <OutlookSetupModal
                suggestedSpending={Number(profileQuery.data.monthly_expenses) || 0}
                needsBirthDate={missingBirthDate}
                needsSpending={retirementSpending <= 0}
                needsSaving={
                  retirementSpending <= 0 &&
                  profileQuery.data.monthly_saving == null &&
                  profileQuery.data.monthly_net_income == null
                }
                onSaved={handleFireSettingsChanged}
                onAskLucius={onAskLucius}
              />
            ) : (
              <OutlookTab
                active={tab === 'outlook'}
                onAskLucius={onAskLucius}
                toast={showToast}
                total={netWorthQuery.data?.net_worth ?? 0}
                totalLoading={netWorthQuery.initialLoading}
                fireVersion={fireVersion + profileDomainV}
                onFireSettingsSaved={handleFireSettingsChanged}
              />
            )}
          </div>
        </div>
      </div>

      <FireSettingsModal
        open={fireOpen}
        onClose={() => setFireOpen(false)}
        onSaved={handleFireSettingsChanged}
      />
      {catMgrOpen && (
        <CategoriesManager
          toast={showToast}
          onClose={() => setCatMgrOpen(false)}
          onMutated={() => setCatVersion((v) => v + 1)}
        />
      )}
      {freshStart.node}
      {toast && (
        <div className="pl-toast mb-anchored" role="status" aria-live="polite" aria-atomic="true">
          {toast}
        </div>
      )}
    </Container>
  )
}
