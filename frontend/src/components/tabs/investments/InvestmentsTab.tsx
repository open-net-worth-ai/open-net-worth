// InvestmentsTab — main shell for the Investments tab.
// Verbatim structure from prototype App() return: PageHeader → Masthead → Brief → mk-tabwrap/PageTabs → content.
// Holdings sub-tab: intel-layout (feed | side) from the prototype.
// Other 5 sub-tabs: existing implementations (performance/income/activity/options/equity).
//
// CSS: imports design.css for all mk-*/lx-*/mvr-*/sec-*/mk-rb-* classes.
// All intel-layout / intel-sec / intel-feed / intel-side come from index.css (already defined).

import './design.css'

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { ComputedRow } from '../../../types/portfolio'
import { useRegisterHandles, useRegisterScreenPrompts } from '../../../lucius/useScreenContract'
import { Container, Stack, PageTabs, Button, TabSkeleton, ErrorState } from '../../ui'
import { Grid2X2 } from '../../../ui/icons'
import type { SegmentedOption } from '../../ui'
import { ASSET_CLASS_LABEL, ASSET_CLASS_ORDER } from '../../../utils/constants'
import { useInvestmentSignals } from '../../../hooks/useInvestmentSignals'
import { requestIntelStoryFocus, useIntelStoryIndex } from '../../../hooks/useIntelStoryIndex'
import { useMastheadSparkline } from '../../../hooks/useMastheadSparkline'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { authFetch } from '../../../api/client'
import { fetchFeesSummary, type FeesSummary } from '../../../api/investments'

// Sub-tab panels
import { PerformanceTab } from './PerformanceTab'
import { IncomeTab } from './IncomeTab'
import { ActivityTab } from './ActivityTab'
import { OptionsTab } from './OptionsTab'
import { EquityTab } from './EquityTab'
import { WatchlistTab } from './WatchlistTab'
import { DisposalForm } from '../../shared/DisposalForm'
import { RecordDividendForm } from '../../shared/RecordDividendForm'
import { AcquireLotForm } from '../../shared/AcquireLotForm'
import { MovePositionModal, type MoveableAccount } from '../../shared/MovePositionModal'

// New design components
import { Masthead } from './Masthead'
import { Brief } from './Brief'
import { HoldingsFilterSheet } from './HoldingsFilterSheet'
import { FeedCards } from './FeedCards'
import { FeedTable } from './FeedTable'
import { Allocation } from './Allocation'
import { SectorGeo } from './SectorGeo'
import { Risk } from './Risk'
import { Rebalance } from './Rebalance'
import { EmptyHoldings } from './EmptyHoldings'
import {
  useInvestmentsBook,
  buildGroups,
  mergeByInstrument,
  shouldMergeAcrossAccounts,
  sortRows,
  type GroupByKey,
  type BookRow,
} from './useInvestmentsBook'
import { resolveInvestmentActiveTab, useCompactInvestmentsPane } from './useCompactInvestmentsPane'
import { useSubtabScrollReset } from './useSubtabScrollReset'

// ── Sub-tab types ─────────────────────────────────────────────────────────

type InvTab =
  | 'holdings'
  | 'watchlist'
  | 'analysis'
  | 'performance'
  | 'income'
  | 'activity'
  | 'options'
  | 'equity'
// 'analysis' exists whenever the cockpit column can't sit beside the feed. That includes a
// phone and a desktop viewport whose usable Investments pane is narrowed by Lucius.
const SEGMENTS: SegmentedOption<InvTab>[] = [
  { value: 'holdings', label: 'Holdings' },
  { value: 'watchlist', label: 'Watchlists' },
  { value: 'analysis', label: 'Analysis' },
  { value: 'performance', label: 'Performance' },
  { value: 'income', label: 'Income' },
  { value: 'activity', label: 'Activity' },
  { value: 'options', label: 'Options' },
  { value: 'equity', label: 'Equity Compensation' },
]
// The sub-tab is remembered for the VISIT (sessionStorage), not forever: within a visit
// Investments returns to the tab you were on, a fresh visit opens on Holdings (owner,
// 2026-10-10, same rule as Plan and the Dashboard). A deep link names its tab with ?sub=.
export const SUBTAB_KEY = 'inv-subtab'
const SUBTAB_VALUES = SEGMENTS.map((s) => s.value)
function loadSubtab(): InvTab {
  let v: InvTab | null = null
  try {
    v = sessionStorage.getItem(SUBTAB_KEY) as InvTab | null
  } catch {
    /* storage blocked: start on Holdings */
  }
  return v && SUBTAB_VALUES.includes(v) ? v : 'holdings'
}
function rememberSubtab(tab: InvTab): void {
  try {
    sessionStorage.setItem(SUBTAB_KEY, tab)
    localStorage.removeItem(SUBTAB_KEY) // the old forever-memory, dropped on first use
  } catch {
    /* storage blocked: the tab simply isn't remembered */
  }
}

// ── Density / groupBy / sort persistence ─────────────────────────────────

const GROUP_KEY = 'inv-holdings-groupby'
const SORT_KEY = 'inv-holdings-sort'
const DENSITY_KEY = 'inv-holdings-density'
const GROUP_OPTIONS: GroupByKey[] = ['none', 'asset_class', 'account', 'currency']

// Exported for regression coverage of the default. Fresh/invalid → 'none' (No grouping);
// a previously-saved valid preference is respected.
export function loadGroupBy(): GroupByKey {
  const v = localStorage.getItem(GROUP_KEY) as GroupByKey | null
  return v && GROUP_OPTIONS.includes(v) ? v : 'none'
}
function loadSort(): { key: string; dir: number } {
  try {
    const v = JSON.parse(localStorage.getItem(SORT_KEY) || '')
    if (v && typeof v.key === 'string' && (v.dir === 1 || v.dir === -1)) return v
  } catch {
    /* fall through */
  }
  return { key: 'value', dir: -1 }
}
type Density = 'table' | 'cards'
function loadDensity(): Density {
  const v = localStorage.getItem(DENSITY_KEY)
  return v === 'table' ? 'table' : 'cards'
}

// ── Class label ──────────────────────────────────────────────────────────

function classLabel(cls: string): string {
  return ASSET_CLASS_LABEL[cls] ?? cls.charAt(0).toUpperCase() + cls.slice(1).replace(/_/g, ' ')
}

// ── Props ─────────────────────────────────────────────────────────────────

interface Props {
  rows: ComputedRow[]
  positionRows: ComputedRow[]
  /** The shared portfolio read has not answered yet. The shell no longer withholds this tab
   *  while it is in flight, so this is what keeps "No holdings yet" from being shown to an
   *  owner who has plenty. */
  loading?: boolean
  /** The portfolio read failed — an error, never "No holdings yet". */
  loadFailed?: boolean
  targets?: Record<string, number>
  riskConcentrationGuideline?: number
  riskStressDropPct?: number
  onAskLucius?: (text: string) => void
  refresh?: () => void
  onOpenAssetDetail?: (row: ComputedRow) => void
  onNavigate?: (tab: string) => void
  /** Deep-link request (?sub=equity — e.g. the asset-detail "Unvested" card's link). */
  subTabRequest?: string
}

// ── Main component ────────────────────────────────────────────────────────

export function InvestmentsTab({
  rows: allRows,
  positionRows,
  loading,
  loadFailed = false,
  targets,
  riskConcentrationGuideline = 20,
  riskStressDropPct = 30,
  onAskLucius,
  refresh,
  onOpenAssetDetail,
  onNavigate,
  subTabRequest,
}: Props) {
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  const { paneAnchorRef, cockpitInSubtab } = useCompactInvestmentsPane(isMobile)
  // The tab wrapper is both the pane-width anchor and the sticky-scroll reference point. Kept
  // as state (a callback ref) because it mounts only after the loading skeleton.
  const [tabwrap, setTabwrapEl] = useState<HTMLDivElement | null>(null)
  const setTabwrap = useCallback(
    (el: HTMLDivElement | null) => {
      setTabwrapEl(el)
      paneAnchorRef(el)
    },
    [paneAnchorRef],
  )
  // A ?sub= link wins from the first render, as on Plan: starting on the remembered tab let the
  // URL mirror and the deep-link effect below fight over the tab.
  const [invtab, setInvtab] = useState<InvTab>(() =>
    subTabRequest && (SUBTAB_VALUES as string[]).includes(subTabRequest)
      ? (subTabRequest as InvTab)
      : loadSubtab(),
  )
  // Live Intel stories per ticker — the same cached read the Intel tab uses (no extra fetch).
  const storyIndex = useIntelStoryIndex()
  const openIntelStory = useCallback(
    (storyId: string) => {
      requestIntelStoryFocus(storyId)
      onNavigate?.('intel')
    },
    [onNavigate],
  )
  // Mirror the active sub-tab into the URL (?sub=) on every change, not just storage —
  // same fix as Plan's ?sub= (owner: "fix it for all tabs, not just plan tab"). replace: true
  // so casual tab-clicking doesn't spam browser history; the visit's memory is the fallback
  // for arriving at a bare /investments with no ?sub=.
  useEffect(() => {
    rememberSubtab(invtab)
    void navigate({
      to: '/investments',
      search: ((prev: any) => ({ ...prev, sub: invtab })) as any,
      replace: true,
    } as never)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invtab])

  // Chip/card deep-link (?sub=equity) — apply once per distinct request value so the
  // user can still switch sub-tabs manually afterwards.
  const lastSubReq = useRef<string | null>(null)
  useEffect(() => {
    if (!subTabRequest || lastSubReq.current === subTabRequest) return
    lastSubReq.current = subTabRequest
    if ((SUBTAB_VALUES as string[]).includes(subTabRequest)) setInvtab(subTabRequest as InvTab)
  }, [subTabRequest])

  const [search, setSearch] = useState('')
  const [searchFocus, setSearchFocus] = useState(false)
  const [acct, setAcct] = useState('')
  const [filter, setFilter] = useState<string>('all') // asset class filter chip
  const [groupBy, setGroupByState] = useState<GroupByKey>(loadGroupBy)
  const [sort, setSortState] = useState(loadSort)
  const [density, setDensityState] = useState<Density>(loadDensity)
  const [targetEdits, setTargetEdits] = useState<Record<string, number> | null>(null)
  const [guideline, setGuideline] = useState(riskConcentrationGuideline)
  const [dropPct, setDropPct] = useState(riskStressDropPct)
  const [addHolding, setAddHolding] = useState(false)

  const setGroupBy = (g: GroupByKey) => {
    setGroupByState(g)
    localStorage.setItem(GROUP_KEY, g)
  }
  const setDensity = (d: Density) => {
    setDensityState(d)
    localStorage.setItem(DENSITY_KEY, d)
  }
  // Card/table stays switchable on a phone (owner, 2026-07-30 — reversing the earlier
  // "force cards" call). The table is 1280px wide by design; it must scroll INSIDE
  // .mk-tbl-scroll, never widen the page — see the min-width:0 chain in design.css.
  // Grouping IS dropped on a phone: the row only has space for what earns it, and group
  // headers cost a lot of vertical space on a small screen.
  const effGroupBy: GroupByKey = isMobile ? 'none' : groupBy
  const handleSort = (key: string) => {
    setSortState((prev) => {
      const next = key === prev.key ? { key, dir: prev.dir * -1 } : { key, dir: -1 }
      localStorage.setItem(SORT_KEY, JSON.stringify(next))
      return next
    })
  }

  // Modals
  const [saleRow, setSaleRow] = useState<ComputedRow | null>(null)
  const [dividendRow, setDividendRow] = useState<ComputedRow | null>(null)
  const [pnlVersion, setPnlVersion] = useState(0)
  const [incomeVersion, setIncomeVersion] = useState(0)
  const [moveRow, setMoveRow] = useState<ComputedRow | null>(null)
  const [moveAccounts] = useState<MoveableAccount[]>([])
  const [fees, setFees] = useState<FeesSummary | null>(null)

  useEffect(() => {
    let alive = true
    fetchFeesSummary()
      .then((f) => alive && setFees(f))
      .catch(() => alive && setFees(null))
    return () => {
      alive = false
    }
  }, [pnlVersion])

  // Core data. `invRows` supplies row IDENTITY (for modals and asset-detail); every book-level
  // figure below is the server's — see useInvestmentsBook and
  // ADR 2026-07-31-overview-aggregates-are-server-computed.
  const invRows = positionRows

  const { values: sparkValues } = useMastheadSparkline(30)

  // Book + derived view-model
  const d = useInvestmentsBook(invRows)
  // 'analysis' has no wide-pane or empty-book equivalent. Preserve the stored selection so
  // it returns when the pane narrows/data arrives, but never render a blank panel meanwhile.
  const activeTab: InvTab = resolveInvestmentActiveTab(invtab, cockpitInSubtab, d.empty)
  useSubtabScrollReset(activeTab, tabwrap)
  const segments = cockpitInSubtab ? SEGMENTS : SEGMENTS.filter((s) => s.value !== 'analysis')
  const totalDay = d.todayPL
  const totalPL = d.totalPL
  const { signals, decisionsCount, loading: briefLoading } = useInvestmentSignals()

  // Deep-source handles: the full positions table and 1Y net-worth series are not
  // visible as rows in the snapshot (only the viewport-visible holdings are) — register
  // them so Lucius can fetch them on demand via get_screen_detail.
  useRegisterHandles([
    {
      id: 'holdings-all',
      type: 'table',
      label: 'All holdings',
      descriptor: 'Full positions table with values, weights and P&L',
      source: 'holdings:all',
    },
    {
      id: 'net-worth-1y',
      type: 'series',
      label: 'Net worth – 1Y',
      descriptor: '1-year daily net-worth series',
      source: 'net_worth_series:1Y',
    },
  ])

  // Per-subtab starter prompts — verbatim from the former investments/LuciusDock DOCK_LX.
  // Registered via the screen-prompt registry so the global dock surfaces them automatically.
  useRegisterScreenPrompts(() => {
    const map: Record<InvTab, string[]> = {
      // 'Plan a tax-efficient trim' / 'Tax at my next vest' were dropped —
      // the tax feature was removed, so Lucius could only answer these from general
      // reasoning with no real figures behind it, no hedge.
      holdings: ["Explain today's moves", 'Rebalance toward my targets', 'Where am I overweight?'],
      watchlist: [
        "What's moving on my watchlist?",
        'Which watched names are near their 52-week low?',
        'Compare my watchlist with what I hold',
      ],
      analysis: [
        'Where am I overweight?',
        'Rebalance toward my targets',
        "What's my concentration risk?",
      ],
      performance: ['Am I beating SPY?', 'What drove my return?', 'Show my risk-adjusted return'],
      income: [
        'Project my next 12 months',
        'Which holdings pay the most?',
        "When's my next dividend?",
      ],
      activity: ['Summarize this month', 'How much did I invest?', 'Export my transactions'],
      options: [
        "What's my assignment risk?",
        'Roll my covered calls',
        'How much premium have I collected?',
      ],
      equity: [
        'When do my next vests hit?',
        'How many units are still unvested?',
        'Draft a sell-to-cover plan',
      ],
    }
    return map[activeTab] ?? map.holdings
  })

  // Asset class filter options
  const presentClasses = [...new Set(invRows.map((r) => r.asset_class))].sort((a, b) => {
    const ia = ASSET_CLASS_ORDER.indexOf(a)
    const ib = ASSET_CLASS_ORDER.indexOf(b)
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib)
  })
  const accounts = [...new Set(invRows.map((r) => r.account_name || r.category))]

  // Filter + sort the rows
  const baseRows = d.all.filter(
    (r) =>
      (!acct || r.account === acct) &&
      (!search || (r.sym + ' ' + r.name).toLowerCase().includes(search.toLowerCase())),
  )
  // A grouping that doesn't itself distinguish accounts (asset class / currency / none) must
  // merge same-instrument rows held at more than one account into one summed row — grouping
  // by account, or an explicit account filter, is exempt (rows are already scoped to one
  // account; merging would substitute back the cross-account total — see
  // shouldMergeAcrossAccounts's own comment). Merge BEFORE the class-filter chips are counted
  // so the chip badge and the visible list always agree (merge never mixes classes — one
  // instrument has one class).
  const mergedBase = shouldMergeAcrossAccounts(effGroupBy, acct)
    ? mergeByInstrument(baseRows, d.mergedRows ?? [])
    : baseRows
  const counts: Record<string, number> = { all: mergedBase.length }
  presentClasses.forEach((c) => {
    counts[c] = mergedBase.filter((r) => r.cls === c).length
  })
  const filters = [
    { id: 'all', label: 'All' },
    ...presentClasses
      .filter((c) => d.sections.some((s) => s.cls === c))
      .map((c) => ({ id: c, label: classLabel(c) })),
  ]
  const filteredRows = sortRows(
    mergedBase.filter((r) => filter === 'all' || r.cls === filter),
    sort.key,
    sort.dir,
  )
  const groups = buildGroups(filteredRows, effGroupBy, d.grand)

  // Allocation with edits applied
  // The server computes each class's CURRENT share (money) and its stored TARGET. While the
  // user is dragging the target sliders, overlay the unsaved edits — substituting a target
  // percentage the user is typing, never recomputing the current share from it.
  const alloc = useMemo(() => {
    const edits = targetEdits ?? targets
    if (!edits) return d.alloc
    return d.alloc.map((a) =>
      edits[a.cls] == null ? a : { ...a, tgt: Math.round(edits[a.cls]! * 100) },
    )
  }, [d.alloc, targetEdits, targets])

  // Handle asset detail open from book rows
  const handleOpenAsset = useCallback(
    (r: BookRow) => {
      const raw = r._raw
      if (onOpenAssetDetail) onOpenAssetDetail(raw)
    },
    [onOpenAssetDetail],
  )

  // The cockpit cards. Rendered as the side column in a wide pane, and as the whole
  // Analysis sub-tab in a narrow pane — one definition, so the two can't drift.
  const cockpit = (
    <>
      <Allocation
        alloc={alloc}
        d={d}
        onSaveTargets={(edits) => {
          setTargetEdits(edits)
          authFetch('/api/profile', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_allocation: edits }),
          }).catch(() => {})
        }}
      />
      <SectorGeo d={d} />
      <Risk
        risk={d.risk}
        grand={d.grand}
        untracked={d.untracked}
        guideline={guideline}
        dropPct={dropPct}
        onSaveSettings={({ guideline: g, dropPct: dp }) => {
          setGuideline(g)
          setDropPct(dp)
          authFetch('/api/profile', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              risk_concentration_guideline: g,
              risk_stress_drop_pct: dp,
            }),
          }).catch(() => {})
        }}
      />
      <Rebalance alloc={alloc} thresholdPp={d.thresholdPp} untracked={d.untracked} />
    </>
  )

  // Holdings panel
  // `d.empty` is true both when nothing is owned AND while the reads are still in flight —
  // only the second one gets a spinner, never the "add your first holding" pitch.
  const holdingsPanel = d.empty ? (
    loading || d.loading ? (
      <TabSkeleton variant="holdings" label="Loading your holdings…" header={false} />
    ) : loadFailed ? (
      <ErrorState
        title="Your holdings couldn’t load"
        message="Nothing is missing from your book. It couldn’t be read just now, so try again or ask Lucius what went wrong."
        onRetry={refresh}
      />
    ) : (
      <EmptyHoldings
        onAddHolding={() => setAddHolding(true)}
        onAskLucius={onAskLucius}
        untracked={d.untracked}
      />
    )
  ) : (
    <div className="intel-layout">
      {/* Feed column */}
      <div className="intel-feed">
        {/* Search + class pills are ONE header: on a phone it pins below the sub-tabs as a
            unit (the same header Watchlists uses), so the pills never scroll away mid-list. */}
        <div className="mk-feed-head">
          {/* While the phone search has focus (or holds a query) it takes the whole row —
            at shared width a query is wider than the field and you can't read what you
            typed. The filters come back as soon as it is dismissed. */}
          <div
            className={`mk-toolbar${isMobile && (searchFocus || search) ? ' mk-toolbar--searching' : ''}`}
          >
            <label className="mk-search-wrap">
              <svg
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <circle cx="7" cy="7" r="4.5" />
                <path d="m10.4 10.4 3.1 3.1" strokeLinecap="round" />
              </svg>
              <input
                className="mk-search"
                aria-label="Search holdings"
                placeholder={isMobile ? 'Search…' : 'Search holdings…'}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onFocus={() => setSearchFocus(true)}
                onBlur={() => setSearchFocus(false)}
              />
            </label>
            {/* Phone and narrow Lucius panes: asset class + account collapse into one filter. */}
            <HoldingsFilterSheet
              className="mk-pane-filter"
              presentation={isMobile ? 'sheet' : 'popover'}
              accountOptions={[
                { id: '', label: 'All accounts' },
                ...accounts.map((a) => ({ id: a, label: a })),
              ]}
              accountValue={acct}
              onAccountChange={setAcct}
            />
            {!isMobile && (
              <select
                className="mk-toolsel mk-account-filter"
                value={acct}
                onChange={(e) => setAcct(e.target.value)}
                aria-label="Filter by account"
              >
                <option value="">All accounts</option>
                {accounts.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            )}
            {!isMobile && (
              <div
                className={`mk-group-wrap${groupBy === 'none' ? '' : ' is-active'}`}
                title={
                  groupBy === 'none' ? 'No grouping' : `Grouped by ${groupBy.replace('_', ' ')}`
                }
              >
                <Grid2X2 size={17} strokeWidth={1.7} aria-hidden="true" />
                <select
                  className="mk-toolsel"
                  value={groupBy}
                  onChange={(e) => setGroupBy(e.target.value as GroupByKey)}
                  aria-label={
                    groupBy === 'none'
                      ? 'Grouping: No grouping'
                      : `Grouping: ${groupBy.replace('_', ' ')}`
                  }
                >
                  <option value="none">No grouping</option>
                  <option value="asset_class">Group: Asset class</option>
                  <option value="account">Group: Account</option>
                  <option value="currency">Group: Currency</option>
                </select>
              </div>
            )}
            <div className="mk-density" role="radiogroup" aria-label="Holdings density">
              <button
                type="button"
                role="radio"
                aria-checked={density === 'cards'}
                aria-label="Card view"
                title="Card view"
                className={density === 'cards' ? 'on' : ''}
                onClick={() => setDensity('cards')}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <rect x="2" y="2.5" width="12" height="4.5" rx="1.2" />
                  <rect x="2" y="9" width="12" height="4.5" rx="1.2" />
                </svg>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={density === 'table'}
                aria-label="Table view"
                title="Table view"
                className={density === 'table' ? 'on' : ''}
                onClick={() => setDensity('table')}
              >
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <line x1="2.5" y1="4" x2="13.5" y2="4" />
                  <line x1="2.5" y1="8" x2="13.5" y2="8" />
                  <line x1="2.5" y1="12" x2="13.5" y2="12" />
                </svg>
              </button>
            </div>
            <Button
              variant="primary"
              size="sm"
              className="mk-add-holding"
              onClick={() => setAddHolding(true)}
            >
              Add
            </Button>
          </div>

          {/* Every width, phone included: asset class is pills, the filter button is account-only. */}
          <div className="intel-feed-bar">
            <div className="mk-chips" role="tablist" aria-label="Filter holdings by class">
              {filters.map((ff) => (
                <button
                  key={ff.id}
                  type="button"
                  role="tab"
                  aria-selected={filter === ff.id}
                  className={`mk-chip${filter === ff.id ? ' is-on' : ''}`}
                  onClick={() => setFilter(ff.id)}
                >
                  {ff.label}
                  <span className="mk-chip-n">{counts[ff.id] ?? 0}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredRows.length === 0 ? (
          <div className="mk-empty-filter">No holdings match your filters.</div>
        ) : density === 'cards' ? (
          <FeedCards
            groups={groups}
            onOpenAssetDetail={handleOpenAsset}
            sort={sort}
            onSort={handleSort}
            thresholdPp={d.thresholdPp}
            storyIndex={storyIndex}
            onOpenStory={onNavigate ? openIntelStory : undefined}
          />
        ) : (
          <FeedTable
            groups={groups}
            grand={d.grand}
            sort={sort}
            onSort={handleSort}
            onSell={(r) => setSaleRow(r._raw)}
            onDividend={(r) => setDividendRow(r._raw)}
            onOpenAssetDetail={handleOpenAsset}
            storyIndex={storyIndex}
            onOpenStory={onNavigate ? openIntelStory : undefined}
          />
        )}

        {fees && fees.total_fees_base > 0 && (
          <p className="mk-feedbar-note">
            Trading fees paid (all-time): fees reflected in cost basis and proceeds.
          </p>
        )}
      </div>

      {/* Side cockpit column only while it can remain beside the feed. */}
      {!cockpitInSubtab && <div className="intel-side">{cockpit}</div>}
    </div>
  )

  // A cold book has no derived data to render the real masthead or brief yet. Keeping its
  // placeholder down inside the Holdings pane produced a disconnected table with no portfolio
  // context; first load must reserve the whole mobile Investments screen.
  const initialPortfolioLoading = d.empty && (loading || d.loading)
  if (initialPortfolioLoading) {
    return (
      <Container
        size="full"
        className="intel-tab"
        data-ctx-region="content"
        data-ctx-title="Investments"
      >
        <TabSkeleton variant="holdings" label="Loading investments…" header={false} />
      </Container>
    )
  }

  return (
    <Container
      size="full"
      className="intel-tab"
      data-ctx-region="content"
      data-ctx-title="Investments"
    >
      <Stack gap="--space-6">
        {!d.empty && (
          <Masthead
            d={d}
            sparkValues={sparkValues.length >= 2 ? sparkValues : undefined}
            totalDay={totalDay}
            totalPL={totalPL}
          />
        )}

        {!d.empty && (
          <Brief
            d={d}
            signals={signals}
            decisionsCount={decisionsCount}
            loading={briefLoading}
            onNavigate={onNavigate}
            onOpenAssetDetail={(sym) => {
              const row =
                invRows.find((r) => r.instrument_id === sym) ??
                invRows.find((r) => (r.symbol || r.ticker || r.label) === sym)
              if (row && onOpenAssetDetail) onOpenAssetDetail(row)
            }}
          />
        )}

        <div ref={setTabwrap} className="mk-tabwrap">
          <PageTabs
            ariaLabel="Investments view"
            value={activeTab}
            onChange={setInvtab}
            tabs={segments.map((s) => ({ key: s.value, label: s.label as string }))}
          />

          {/* Stable min-height so switching sub-tabs (or a first-load <Loading/>,
              or a short empty state like Options) doesn't collapse the page and
              cause a scroll/flicker jump. */}
          <div className="inv-subtab">
            {activeTab === 'holdings' && holdingsPanel}

            {activeTab === 'watchlist' && (
              <WatchlistTab onNavigate={onNavigate} onAskLucius={onAskLucius} />
            )}

            {activeTab === 'analysis' && !d.empty && <div className="inv-analysis">{cockpit}</div>}

            {activeTab === 'performance' && (
              <PerformanceTab
                noHoldings={d.empty && !(loading || d.loading) && !loadFailed}
                onAddHolding={() => setAddHolding(true)}
                onAskLucius={onAskLucius}
              />
            )}

            {activeTab === 'income' && (
              <IncomeTab version={incomeVersion} onAskLucius={onAskLucius} />
            )}

            {activeTab === 'activity' && (
              <ActivityTab onNavigate={onNavigate} onAskLucius={onAskLucius} />
            )}

            {activeTab === 'options' && <OptionsTab onAskLucius={onAskLucius} />}

            {activeTab === 'equity' && <EquityTab rows={positionRows} onAskLucius={onAskLucius} />}
          </div>
        </div>

        <div style={{ height: 96 }} />
      </Stack>

      {addHolding && (
        <AcquireLotForm
          onDone={() => {
            refresh?.()
          }}
          onClose={() => setAddHolding(false)}
        />
      )}

      {saleRow && (
        <DisposalForm
          row={saleRow}
          onDone={() => {
            refresh?.()
            setPnlVersion((v) => v + 1)
          }}
          onClose={() => setSaleRow(null)}
        />
      )}

      {dividendRow && (
        <RecordDividendForm
          row={dividendRow}
          defaultKind={dividendRow.asset_class === 'bond' ? 'interest' : 'dividend'}
          onDone={() => {
            refresh?.()
            setIncomeVersion((v) => v + 1)
          }}
          onClose={() => setDividendRow(null)}
        />
      )}

      {moveRow && (
        <MovePositionModal
          open={!!moveRow}
          row={moveRow}
          accounts={moveAccounts.filter((a) => a.id !== moveRow.account_id)}
          allRows={allRows}
          onMoved={() => {
            refresh?.()
            setPnlVersion((v) => v + 1)
          }}
          onClose={() => setMoveRow(null)}
        />
      )}
    </Container>
  )
}
