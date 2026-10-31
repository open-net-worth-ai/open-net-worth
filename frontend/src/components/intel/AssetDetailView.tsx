import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { entityRefProps } from '../../utils/mentionable'
import { EntityRow } from '../../lucius/EntityRow'
import { useAssetDetail } from '../../hooks/useAssetDetail'
import { useAnalystConsensus } from '../../hooks/useAnalystConsensus'
import { useRegisterHandles, useRegisterScreenPrompts } from '../../lucius/useScreenContract'
import type { ScreenHandle } from '../../state/screenSnapshot'
import { fetchPositionIncome } from '../../api/portfolio'
import type { IncomeSummary } from '../../types/portfolio'
import { keyIssuesPassActive } from '../../types/assetDetail'
import { Button, EmptyState, ErrorState, Panel, Text } from '../ui'
import { Sunrise, Sunset, RotateCcw, TrendingUp, TrendingDown } from '../../ui/icons'
// The underline tab bar already used on the Connectors page (`.rd-tabs`/`.rd-tab`) — reused
// here rather than SegmentedControl's pill style. See the `.asd-subtabs` CSS below for why the
// styling is duplicated (scoped to `.asd-subtabs`) instead of importing connectors.css.
import { Tabs } from '../tabs/connectors/primitives'
import {
  fBase,
  fPrice,
  getCurrencySymbol,
  countryFlag,
  timeAgo,
  getDateLocale,
  fDate,
  fDateTime,
} from '../../utils/format'
import { AssetPriceChart } from './AssetPriceChart'
import { AssetDetailSkeleton, NewsSectionSkeleton } from './AssetDetailSkeleton'
import { ResearchingStatus } from './ResearchingStatus'
import { Skeleton } from '../ui/Skeleton'
import { RefreshingBanner } from './RefreshingBanner'
import { displayTicker } from './assetDisplay'
import { QuoteStats } from './QuoteStats'
import { MarketStatusChip } from './MarketStatusChip'
import { AnalystConsensus, AnalystConsensusRailCard } from './AnalystConsensus'
import { NewsCardGrid } from './NewsCardGrid'
import { LatestDevelopments } from './LatestDevelopments'
import { PriceMovementTimeline } from './PriceMovementTimeline'
import { KeyIssuesAccordion } from './KeyIssuesAccordion'
import { YourPositionCard } from './YourPositionCard'
import { PositionSubtab } from './PositionSubtab'
import { YourThesisCard } from './YourThesisCard'
import { PositionVerdictLine } from './PositionVerdictLine'

/** Under every take: it's an opinion, and Lucius isn't licensed to give advice. */
const TAKE_NOT_ADVICE =
  "Lucius isn't a licensed financial adviser. This is its view, not financial advice — the decision is always yours."
import { ClosedPositionHistory } from './ClosedPositionHistory'
import { AssetWatchlistMenu } from './AssetWatchlistMenu'
import { AssetAlertsCard } from './AssetAlertsCard'
import { KeyFactsCard } from './KeyFactsCard'
import { PeersCard } from './PeersCard'
import { FundamentalsSnapshot } from './FundamentalsSnapshot'
import { TechnicalsWidget } from './TechnicalsWidget'
import { AcquireLotForm } from '../shared/AcquireLotForm'
import { useRailResize } from './useRailResize'

type DetailTab = 'overview' | 'analysis' | 'position'
const DETAIL_TAB_VALUES: DetailTab[] = ['overview', 'analysis', 'position']
function parseDetailTab(v: unknown): DetailTab | null {
  return typeof v === 'string' && (DETAIL_TAB_VALUES as string[]).includes(v)
    ? (v as DetailTab)
    : null
}

// The URL (?sub=) is the ONLY carrier of the open tab — refresh, back/forward, deep links and
// peer drills all keep it there (every tab click mirrors it into the URL, replace-style).
// Nothing is remembered in storage: a fresh open of a ticker from a list always starts on
// Overview (owner-reproduced 2026-09-22: "why does it show Position if that was last
// opened?"; the same rule Settings follows — "let's not cache or store that").
function loadDetailTab(fromUrl?: string): DetailTab {
  return parseDetailTab(fromUrl) ?? 'overview'
}

// All three tabs are always shown (Position renders an empty state when the asset isn't held),
// so the same static list drives both the loading skeleton and the loaded view.
const DETAIL_TABS: { key: DetailTab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'analysis', label: 'Analysis' },
  { key: 'position', label: 'Position' },
]

// News splits into two sections of the same `news` list, by index — the first
// STORY_CARD_COUNT render as Stories & Analysis cards, the remainder as Latest
// Developments rows (capped at DEV_ROW_LIMIT with a View all toggle). The card
// count and the developments offset are the SAME constant so no item appears twice.
const STORY_CARD_COUNT = 6
const DEV_ROW_LIMIT = 10
// One column on a phone: ten rows ran a whole screen (owner ask 2026-09-22 — max 5).
const DEV_ROW_LIMIT_PHONE = 5

interface Props {
  instrumentId: string
  /** Name/code known from the list we navigated from — shown immediately in the
   *  first-load skeleton header instead of a gray bar. */
  seedName?: string
  seedTicker?: string
}

export function AssetDetailView({ instrumentId, seedName, seedTicker }: Props) {
  const navigate = useNavigate()
  // strict: false — this view mounts on both /quote/$symbol and /asset/$instrumentId.
  const search = useSearch({ strict: false }) as { sub?: string }
  const {
    data,
    loading,
    refreshing,
    backgroundRefreshing,
    error,
    refresh,
    refetch,
    refreshNotice,
  } = useAssetDetail(instrumentId)
  // Prefetched at the top level (not gated on the Analysis tab) + session-cached,
  // so the Analysis tab is ready before it's ever opened and never re-fetches.
  const analyst = useAnalystConsensus(instrumentId)
  const {
    bodyRef: railBodyRef,
    railRef,
    style: railStyle,
    snapped: railSnapped,
    handleProps: railHandleProps,
  } = useRailResize()
  const [activeTab, setActiveTab] = useState<DetailTab>(() => loadDetailTab(search.sub))
  // One effect covers three cases: (1) mirror a manual tab click into the URL, (2) deep-link /
  // back-forward (?sub=analysis) for the SAME instrument, and (3) the Intel tab reusing one
  // AssetDetailView instance across tickers (no remount), where `instrumentId` changes under
  // it — the new ticker starts on Overview unless its URL says otherwise.
  const prevInstrumentId = useRef(instrumentId)
  const isFirstRender = useRef(true)
  useEffect(() => {
    const instrumentChanged = prevInstrumentId.current !== instrumentId
    prevInstrumentId.current = instrumentId
    if (!isFirstRender.current && instrumentChanged) {
      const nextTab = loadDetailTab(search.sub)
      if (nextTab !== activeTab) {
        setActiveTab(nextTab)
        return
      }
    }
    isFirstRender.current = false
    void navigate({
      search: ((prev: Record<string, unknown>) => ({ ...prev, sub: activeTab })) as never,
      replace: true,
    } as never)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, instrumentId, search.sub])
  const [devExpanded, setDevExpanded] = useState(false)
  const [addLotOpen, setAddLotOpen] = useState(false)
  // The header's own live quote and the chart's intraday bars are two INDEPENDENT Yahoo
  // feeds that don't update in lockstep — whichever is actually fresher wins in the header
  // display (owner call, 2026-09-01: "whatever's newer wins"). See AssetPriceChart's
  // `onLatestPoint` doc for the reproduction. Functional update bails out (returns the
  // SAME object) when the value is unchanged, so this doesn't re-render on every chart
  // recompute — only on a genuine new tick.
  const [chartLatest, setChartLatest] = useState<{ price: number; at: string } | null>(null)
  const handleChartLatestPoint = useCallback((p: number, at: string) => {
    setChartLatest((prev) => (prev && prev.price === p && prev.at === at ? prev : { price: p, at }))
  }, [])

  // Dividends received for the held position — fetched ONCE here rather than separately in
  // PositionSubtab and YourPositionCard, since both need the exact same figure (unlike
  // realized P&L, which PositionSubtab and LotsPanel fetch independently because they want
  // different shapes of the same endpoint). Null while loading, on error, or when there's no
  // single position_id to scope to (a merged multi-account holding) — every consumer renders
  // an em-dash in that case, matching PositionSubtab's own realized-pnl fallback.
  const [income, setIncome] = useState<IncomeSummary | null>(null)
  const incomePositionId = data?.position?.held ? data.position.position_id : null
  useEffect(() => {
    if (!incomePositionId) {
      setIncome(null)
      return
    }
    let alive = true
    fetchPositionIncome(incomePositionId)
      .then((d) => {
        if (alive) setIncome(d)
      })
      .catch(() => {
        /* leave null → an em-dash */
      })
    return () => {
      alive = false
    }
  }, [incomePositionId])

  // Build handles for deep content: news articles (individually fetchable via their URL),
  // price series behind the chart, the currently-inactive tab as a view handle so Lucius
  // knows it exists and can pull it without pretending the user saw it, plus analyst
  // consensus, peers, and tax lots as table handles.
  const handles: ScreenHandle[] = []
  if (data) {
    const { asset, news, position } = data
    // News stories — up to 8, each individually cited via its URL.
    news.slice(0, 8).forEach((n, i) => {
      if (n.url) {
        handles.push({
          id: `st-${i}`,
          type: 'story',
          label: n.source || n.domain || 'Article',
          descriptor: n.title,
          source: n.url,
        })
      }
    })
    // Price series behind the chart.
    if (asset.ticker || data.price.last != null) {
      handles.push({
        id: 'price-1y',
        type: 'series',
        label: 'Price history – 1Y',
        descriptor: '~250 daily closes behind the chart',
        source: `price_series:${instrumentId}:1Y`,
      })
    }
    // The inactive tab — Lucius knows it exists and can request it, but it isn't
    // passed as if the user is already looking at it.
    if (activeTab === 'overview') {
      handles.push({
        id: 'view-analysis',
        type: 'view',
        label: 'Analysis tab',
        descriptor: 'Not open — analyst consensus & key issues',
        source: 'view:analysis',
      })
    } else {
      handles.push({
        id: 'view-overview',
        type: 'view',
        label: 'Overview tab',
        descriptor: 'Not open — price, chart, stats, news, Lucius take',
        source: 'view:overview',
      })
    }
    // Analyst consensus (rating, price targets, EPS estimates — Analysis tab).
    handles.push({
      id: 'tbl-analyst',
      type: 'table',
      label: 'Analyst consensus',
      descriptor: 'Analyst ratings, price targets & EPS estimates',
      source: `analyst:${instrumentId}`,
    })
    // Peers comparison (right-rail card).
    handles.push({
      id: 'tbl-peers',
      type: 'table',
      label: 'Peer comparison',
      descriptor: 'Peer tickers — price & daily change',
      source: `peers:${instrumentId}`,
    })
    // Tax lots — off-screen, shown in the Position tab when held. Only when a single
    // concrete position backs the holding — a multi-account (merged) holding has no one
    // position id to fetch lots for.
    if (position?.held && position.position_id) {
      handles.push({
        id: 'tbl-lots',
        type: 'table',
        label: 'Position lots & realized',
        descriptor: 'Acquisition lots + realized disposals (off-screen)',
        source: `transactions:account:${position.position_id}`,
      })
    }
    // Key issues / bull-bear case — available as a fetchable story.
    if (data.key_issues.length > 0) {
      handles.push({
        id: 'doc-issues',
        type: 'story',
        label: 'Bull & bear case',
        descriptor: `${data.key_issues.length} key issues`,
        source: `intel:${instrumentId}:key_issues`,
      })
    }
  }
  useRegisterHandles(handles)

  useRegisterScreenPrompts(({ title }) => [
    `Is ${title} a good entry?`,
    'How much of my portfolio is this?',
    'Bull vs bear case?',
    'Summarize the latest news',
  ])

  // Static nav chrome — no data dependency, so it renders for real even during the
  // first-load skeleton (the user can switch tabs / go back while the body loads).
  const subTabs = <Tabs<DetailTab> tabs={DETAIL_TABS} value={activeTab} onChange={setActiveTab} />

  if (loading) {
    return (
      <AssetDetailSkeleton name={seedName || undefined} ticker={seedTicker} subTabs={subTabs} />
    )
  }

  if (error || !data) {
    return (
      <div className="asd-error">
        <ErrorState
          message="Could not load asset data. The instrument may not be found or the server is unavailable."
          onRetry={refresh}
        />
      </div>
    )
  }

  const {
    asset,
    position,
    price,
    news,
    key_issues,
    facts,
    peers,
    watchlist,
    portfolio_impact,
    has_narrative,
  } = data

  // Crypto keeps its real sub-cent precision (a $0.0009 token's 4th decimal IS the price);
  // every other asset class caps at 2 — a stock's 3rd/4th decimal is usually just an
  // FX-converted artifact, not information (owner ask 2026-08-31: "$186.0967" read as
  // needless precision for PLTR).
  const priceMaxFrac = asset.asset_class === 'crypto' ? undefined : 2
  // Whichever of {header's own live quote, chart's own last intraday bar} is actually
  // newer wins — see the `chartLatest` state doc above. change_abs/change_pct are
  // recomputed against the SAME baseline the backend's own change already implies
  // (price.last - price.change_abs — true regardless of what that baseline actually IS,
  // extended-session close or a plain prior close), so price/change/pct stay mutually
  // consistent even when the displayed price gets swapped for a fresher chart tick.
  // Closed market with the last session's after-hours print in its own column: the headline
  // is the CLOSE, full stop — the chart's newest tape point IS that after-hours print, and
  // letting it win here put the same number in both columns (owner-reproduced 2026-09-20).
  const closedWithAfterHours = !price.is_extended && !!price.last_after_hours
  const chartIsNewer =
    !closedWithAfterHours &&
    chartLatest != null &&
    price.as_of != null &&
    new Date(chartLatest.at).getTime() > new Date(price.as_of).getTime()
  const changeBaseline =
    price.last != null && price.change_abs != null ? price.last - price.change_abs : null
  const effPrice = chartIsNewer ? chartLatest!.price : price.last
  const effAsOf = chartIsNewer ? chartLatest!.at : price.as_of
  const effChangeAbs =
    chartIsNewer && changeBaseline != null ? effPrice! - changeBaseline : price.change_abs
  const effChangePct =
    chartIsNewer && changeBaseline != null && changeBaseline !== 0
      ? (effPrice! / changeBaseline - 1) * 100
      : price.change_pct
  const priceFmt = effPrice != null ? fPrice(effPrice, priceMaxFrac) : '—'
  const currSym = getCurrencySymbol(price.currency)
  // Movement direction drives both tone classes; use change_pct (the headline %
  // movement) and fall back to change_abs so a flat day reads neutral, not green.
  const moveBasis = effChangePct ?? effChangeAbs ?? 0
  const priceTone =
    moveBasis > 0 ? 'asd-price-up' : moveBasis < 0 ? 'asd-price-down' : 'asd-price-flat'
  const signOf = (n: number) => (n > 0 ? '+' : n < 0 ? '-' : '')
  // The pct pill leads with a trend arrow instead of +/- text (owner ask 2026-09-01) — the
  // dollar-amount span right next to it keeps the +/- sign, so direction still reads even
  // if the pill's color alone doesn't (colorblind-safe redundancy).
  const trendArrow = (n: number) =>
    n > 0 ? (
      <TrendingUp size={11} className="asd-price-pct-arrow" aria-hidden />
    ) : n < 0 ? (
      <TrendingDown size={11} className="asd-price-pct-arrow" aria-hidden />
    ) : null
  const subline = [
    displayTicker(asset),
    asset.exchange,
    asset.country ? countryFlag(asset.country) : null,
  ]
    .filter(Boolean)
    .join(' • ')

  const hasNews = news.length > 0
  // has_narrative gated too, not just a non-empty array (owner-reproduced live bug,
  // 2026-09-03, UBER open-then-watch): the analyst pass writes key_issues and
  // portfolio_narrative together, but a model turn can (and, live, does) fill key_issues while
  // leaving portfolio_narrative empty — the backend's OWN bar for "this pass actually landed"
  // (analyst._key_issues_force_stale's `has_content` check) requires narrative specifically,
  // and then retries in the background. Treating key_issues alone as "landed" showed a
  // thin/under-grounded partial result (2 generic bull/bear lines, none tied to the same-day
  // news already on screen) as if it were the final take, with no indication a fuller pass was
  // still coming. Gating on has_narrative here means that partial result now falls through to
  // `keyIssuesGenerating` below instead.
  const hasIssues = key_issues.length > 0 && has_narrative
  // Lifted out of the "Lucius's take" card's own render block (below) so the page-level
  // RefreshingBanner can also read it — UI review 2026-08-31, live-reproduced: both banners
  // independently gated on backgroundRefreshing rendered the SAME "Analysing key
  // developments ✦" text on screen at once when this card was actively generating. The card's
  // own badge already carries this specific message; the page banner now defers to it instead
  // of repeating it.
  //
  // R4 fix (owner-reproduced live bug, 2026-08-31, KODK: a hung on-demand LLM call left this
  // showing "Generating" for 10+ minutes straight with nothing ever landing). Gated on the REAL
  // in-flight signals (`refreshing`/`backgroundRefreshing`) — NOT merely `!has_narrative` alone.
  // An EARLIER version of this fix (R3) dropped that check specifically to satisfy a test
  // asserting the badge shows even before `backgroundRefreshing` catches up on first paint —
  // that test encoded the wrong trade-off (a brief, sub-second-to-~2s gap before the poll
  // confirms a pass has started is far better than an indicator that can never turn off once
  // the backend stops responding); the test itself was updated instead of reverting this check
  // again. `has_narrative` is still checked (never show "Generating" over stale-but-real cached
  // content) alongside `has_ticker` (a no-ticker holding can never generate anything).
  const generatingLuciusTake =
    !has_narrative && (refreshing || backgroundRefreshing) && data.has_ticker
  // Drives a dedicated "Key Issues — generating" placeholder (owner-reproduced 2026-08-31, live
  // KO cold-open: with nothing here, the Key Issues panel just didn't render at all once news
  // had already landed but key_issues hadn't — no skeleton, no banner, no empty-state, a blank
  // gap the user flagged as "confusing"). Distinct from `generatingLuciusTake` — that one covers
  // the position-framed `portfolio_impact` card, gated on a position existing at all
  // (`Boolean(portfolio_impact)`, a known held-only gap); key_issues is a bull/bear debate about
  // the STOCK, generated for held/watched/ad-hoc alike, so this has no such gate. `refreshing`
  // covers a truly cold ad-hoc open (the news/facts/peers POST and the on-demand key_issues pass
  // both fire from the same page load); `backgroundRefreshing` covers a held/watched reopen
  // (key_issues top-up only, no POST at all). `keyIssuesPassActive` is the backend's own stamp
  // for that pass: both client flags can lapse while it still runs (a Refresh POST returns
  // before the separate key_issues LLM call finishes), which dropped the Analysis tab to the
  // "No key issues yet" empty state with the bull/bear seconds away.
  const keyIssuesGenerating =
    !hasIssues &&
    (refreshing || backgroundRefreshing || keyIssuesPassActive(data)) &&
    data.has_ticker
  // Drives the "Generating" state on KeyFactsCard/PeersCard (owner-reproduced live bug,
  // 2026-08-31, KODK/HPE/XOM: neither card had ANY loading state — Key Facts silently showed
  // only Symbol/Exchange with no indication more was coming, Peers rendered nothing at all).
  //
  // R5 fix: `refreshing` ALONE turned out insufficient in practice — live-verified the facts/
  // peers synthesis call can run 60-120+ real seconds (this app's own logs: 65s-116s for a
  // cold ad-hoc ticker), and `refreshing` is scoped to this hook's own in-flight POST, which a
  // long-running mobile session can lose track of. `refresh_stage` (`_do_refresh`'s own
  // searching/analysing/ready progress) is server truth and already kept fresh by the SAME
  // fast-poll `backgroundRefreshing` arms — OR'd in as a second, independent source rather than
  // replacing `refreshing`, so this can only gain coverage, never lose it. Each card
  // additionally gates on its OWN content being sparse/empty (see their own `generating` prop
  // docs) so this never shows over content that's already landed, and never shows forever once
  // real content lands even if a stage value lingers.
  const factsGenerating =
    data.has_ticker && (refreshing || (!!data.refresh_stage && data.refresh_stage !== 'ready'))

  // Rendered ONCE, placed TWICE — the desktop sidebar slot (.asd-rail, always visible,
  // any tab) and a narrow-viewport slot inline in the tab it's actually about (Overview
  // for Your Position, Analysis for Facts/Peers) — CSS (.asd-desktop-only/.asd-mobile-
  // only, index.css) shows exactly one instance at a time, same 720px breakpoint the
  // rail already reflows at. A single render call each avoids two independent copies of
  // local UI state (e.g. KeyFactsCard's own expanded toggle) drifting out of sync (owner
  // ask 2026-09-01: rail cards dumped below the news feed on mobile forced a long scroll
  // to reach them — "should we move these to [where they're actually relevant] instead of
  // showing them at the bottom like this?").
  const yourPositionBlock =
    position && position.held && position.position_id ? (
      <EntityRow
        as="div"
        type="holding"
        id={position.position_id}
        label={asset.ticker ?? asset.name}
        item={position}
        toFields={(p) => ({
          symbol: asset.ticker ?? asset.symbol,
          name: asset.name,
          asset_class: asset.asset_class,
          quantity: p.qty != null ? String(p.qty) : null,
          currency: p.cost_currency,
          price: p.qty ? fBase(p.market_value_base / p.qty) : null,
          value_base: fBase(p.market_value_base),
          cost_base: p.cost_basis != null ? fBase(p.cost_basis) : null,
          unrealized_base: p.unrealized_pl_base != null ? fBase(p.unrealized_pl_base) : null,
        })}
        role="button"
        tabIndex={0}
        className="asd-pos-card-link"
        aria-label="Open the Position tab"
        onClick={() => setActiveTab('position')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setActiveTab('position')
          }
        }}
        draggable
      >
        <YourPositionCard
          position={position}
          symbol={asset.ticker ?? asset.symbol}
          income={income}
          assetClass={asset.asset_class}
        />
      </EntityRow>
    ) : position && position.held ? (
      // Held across multiple accounts — no single position_id to hang a mention on.
      <div
        role="button"
        tabIndex={0}
        className="asd-pos-card-link"
        aria-label="Open the Position tab"
        onClick={() => setActiveTab('position')}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setActiveTab('position')
          }
        }}
      >
        <YourPositionCard
          position={position}
          symbol={asset.ticker ?? asset.symbol}
          income={income}
          assetClass={asset.asset_class}
        />
      </div>
    ) : (
      data.has_ticker && (
        <Panel title="Your Position" className="asd-rail-panel">
          <Text size="sm" tone="muted">
            You don&apos;t hold {asset.ticker ?? asset.name}.
          </Text>
        </Panel>
      )
    )

  const thesisBlock = (
    <YourThesisCard
      instrumentId={instrumentId}
      ticker={asset.ticker ?? asset.symbol}
      thesis={data.thesis ?? null}
      held={!!position?.held}
      price={effPrice ?? null}
      priceMaxFrac={priceMaxFrac}
      currency={price.currency}
      onSaved={refetch}
    />
  )

  const priceAlertsBlock = data.has_ticker && asset.ticker && (
    <AssetAlertsCard
      ticker={asset.ticker}
      assetName={asset.name}
      assetClass={asset.asset_class}
      instrumentId={data.instrument_id}
      itemId={watchlist.item_id ?? null}
      alerts={watchlist.price_alerts}
      currency={price.currency}
      currencySymbol={currSym}
      livePrice={price.last ?? undefined}
      changePct={price.change_pct}
      monitorNews={watchlist.monitor_news ?? true}
      customFeedUrls={watchlist.custom_feed_urls}
      onChanged={refetch}
    />
  )

  // Shown on every tab except Analysis (gated where it's rendered below) — the fuller
  // Estimates/Ratings breakdown already lives there; "See all" jumps straight to it
  // instead of duplicating that layout here.
  const analystConsensusBlock = data.has_ticker && (
    <AnalystConsensusRailCard
      data={analyst.data}
      loading={analyst.loading}
      currency={price.currency}
      onSeeAll={() => setActiveTab('analysis')}
    />
  )

  const factsAndPeersBlock = (
    <>
      <KeyFactsCard
        asset={asset}
        instrumentId={data.instrument_id}
        facts={facts}
        cryptoFacts={data.crypto_facts}
        generating={factsGenerating}
      />
      <PeersCard peers={peers} generating={factsGenerating} />
      {/* Bundled with Facts/Peers (not a separate rail-only element) — it's primarily
          about THAT content's provenance, so it travels with it into the Analysis tab
          on mobile instead of being left behind, orphaned, in the desktop-only rail. */}
      <Text size="xs" tone="faint" className="asd-rail-disclaimer">
        {data.last_refreshed_at ? `Data via ${data.asset.asset_class} search. ` : ''}
        {price.state === 'live' && price.provider
          ? `Live via ${price.provider === 'yahoo' ? 'Yahoo Finance' : 'CoinGecko'}${price.as_of ? ` • Updated ${timeAgo(price.as_of)}` : ''}. `
          : ''}
        Company facts are AI-generated and may be inaccurate.
      </Text>
      {/* On-chart TradingView logo is hidden on both charts (assetPriceChartLwc.ts,
          TechnicalsWidget) via `attributionLogo: false` — its license lets us do that in
          exchange for keeping this written notice + link visible somewhere on the page. */}
      <Text size="xs" tone="faint" className="asd-rail-disclaimer">
        Charted with{' '}
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
          TradingView Lightweight Charts™
        </a>
        . Copyright © 2025 TradingView, Inc.
      </Text>
    </>
  )

  return (
    <div className="asd-root" data-ctx-region="content">
      {/* Header */}
      <div className="asd-header">
        <div className="asd-header-left">
          <div
            className="asd-header-text"
            // Scoped to the title block, NOT the whole `.asd-root` page (owner report
            // 2026-08-11): long-press/right-click/drag all delegate via
            // `target.closest('[data-entity-id]')` (dragRightClick.tsx), so tagging the
            // entire page made "Ask Lucius about this" fire from a long-press ANYWHERE
            // on an asset detail page, not just its title.
            {...entityRefProps('instrument', instrumentId, asset.ticker ?? asset.name)}
          >
            <h2 className="asd-asset-name" data-ctx-title={asset.name}>
              {asset.name}
            </h2>
          </div>
        </div>
        <div className="asd-header-right">
          {data.has_ticker && asset.ticker && (
            <AssetWatchlistMenu
              ticker={asset.ticker}
              assetClass={asset.asset_class}
              onChanged={refetch}
            />
          )}
          {refreshNotice ? (
            <span className="asd-updated-at" role="status">
              {refreshNotice}
            </span>
          ) : (
            data.last_refreshed_at && (
              <span className="asd-updated-at" title={fDateTime(data.last_refreshed_at)}>
                Analysis updated {timeAgo(data.last_refreshed_at)}
              </span>
            )
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            disabled={refreshing}
            className="asd-refresh-btn"
            iconLeft={<RotateCcw size={14} />}
            aria-label={refreshing ? 'Refreshing' : 'Refresh'}
            title="Refresh"
          >
            {/* Text collapses at narrow widths (icon stays) — see the container query in
                index.css (owner ask 2026-08-31: "small screen ... not showing too many
                buttons like that"). */}
            <span className="asd-refresh-btn-label">{refreshing ? 'Refreshing…' : 'Refresh'}</span>
          </Button>
        </div>
        {/* Exchange/session state — its own full-width row within `.asd-header` (a direct
            sibling of `.asd-header-left`/`.asd-header-right`, not nested inside the title's
            box) so it gets the header's FULL width, not just whatever's left after the icon
            cluster (owner-reproduced live bug, 2026-09-08: nested inside `.asd-header-left`,
            the exchange name got ellipsized even with free space visibly unused to its right,
            because it shared the title row's narrower box). See `.asd-header`'s CSS comment
            for how `flex: 0 0 100%` forces it onto this second line. Collapses to a bare dot
            at narrow widths instead of wrapping (`.asd-market` itself, not a separate element
            — see the container query in index.css) — the full state/exchange/tz text is
            still in the `title` attribute either way. */}
        {(subline || price.market) && (
          <div className="asd-asset-subline-row">
            {subline && <span className="asd-asset-subline">{subline}</span>}
            <MarketStatusChip market={price.market} />
            <EarningsChip iso={data.freshness?.next_earnings_at} />
          </div>
        )}
      </div>

      {/* Sub-tab nav */}
      <div className="asd-subtabs">{subTabs}</div>

      {/* Price block — full width above the grid so the right rail (Your Position)
          aligns its top with the chart, not the price. */}
      {activeTab === 'overview' && (
        <div className="asd-price-block">
          <div className="asd-price-col">
            <div className="asd-price-num-row">
              <span className="asd-price-big" data-ctx-fact="Price">
                {currSym}
                {priceFmt}
              </span>
              {effChangeAbs != null && (
                <span className={`asd-price-change ${priceTone}`}>
                  {signOf(effChangeAbs)}
                  {fPrice(Math.abs(effChangeAbs), priceMaxFrac)}
                </span>
              )}
              {effChangePct != null && (
                <span className={`asd-price-pct-pill ${priceTone}`} data-ctx-fact="Today">
                  {trendArrow(effChangePct)}
                  {Math.abs(effChangePct).toFixed(2)}%
                </span>
              )}
            </div>
            {effAsOf && (
              <span className="asd-price-caption">
                {/* `last` above IS the extended print while one is live (owner ask
                    2026-08-31: pre/post prices feed net worth/P&L too now, not just
                    display) — so this caption describes THAT number, and the genuinely
                    different regular close gets its own column beside this one instead of
                    repeating this same value under a wrong "At close" label
                    (owner-reproduced live confusion, 2026-08-31). */}
                {price.is_extended ? (
                  <>
                    {(() => {
                      const Icon = price.extended_session === 'pre' ? Sunrise : Sunset
                      return <Icon size={12} className="asd-price-extended-icon" />
                    })()}
                    {price.extended_session === 'pre' ? 'Pre-market: ' : 'After hours: '}
                  </>
                ) : closedWithAfterHours ? (
                  // Perplexity's own caption for this layout: "At close: 18 Sep".
                  'At close: '
                ) : (
                  'As of '
                )}
                {/* The exchange's OWN local time, not the viewer's browser timezone — a
                    market-close timestamp read in the viewer's own zone can land on a
                    different calendar day/hour than when the bell actually rang (owner-
                    reproduced live confusion, 2026-08-31: "4:00 AM" for a 4:00 PM EDT close,
                    viewed from SGT). Matches Yahoo's own convention. Falls back to the
                    viewer's zone when no market timezone is known (e.g. crypto). */}
                {/* Explicit component fields, NOT dateStyle/timeStyle — the two are mutually
                    exclusive with timeZoneName per the Intl spec; combining them throws
                    "Invalid option : option" (owner-reproduced live CRASH, 2026-08-31, this
                    exact line — confirmed from the real browser stack trace). */}
                {price.is_extended ? (
                  // Pre/post is always TODAY — a bare time (Yahoo's own convention: "4:28:12
                  // PM EDT", no date) reads as current; a full date next to it read as if the
                  // print might be from a different day, which it never is. A narrow (mobile)
                  // viewport drops seconds + the tz name too — just "4:40 PM", matching
                  // Perplexity's own compact mobile layout.
                  <>
                    <span className="asd-price-wide-only">
                      {new Date(effAsOf!).toLocaleTimeString(getDateLocale(), {
                        hour: 'numeric',
                        minute: '2-digit',
                        second: '2-digit',
                        timeZone: price.market?.timezone || undefined,
                        timeZoneName: price.market?.timezone ? 'short' : undefined,
                      })}
                    </span>
                    <span className="asd-price-narrow-only">
                      {new Date(effAsOf!).toLocaleTimeString(getDateLocale(), {
                        hour: 'numeric',
                        minute: '2-digit',
                        timeZone: price.market?.timezone || undefined,
                      })}
                    </span>
                  </>
                ) : /^\d{4}-\d{2}-\d{2}$/.test(effAsOf!) ? (
                  // A recorded close from local history is a calendar day with no time; read
                  // as a UTC instant it showed "Oct 26, 8:00 PM EDT" for Oct 27's close.
                  fDate(effAsOf!)
                ) : (
                  // The year is noise almost always — only worth showing once the price is
                  // stale enough to be from a DIFFERENT calendar year (owner ask 2026-09-01).
                  new Date(effAsOf!).toLocaleString(getDateLocale(), {
                    month: 'short',
                    day: 'numeric',
                    ...(new Date(effAsOf!).getFullYear() !== new Date().getFullYear()
                      ? { year: 'numeric' as const }
                      : {}),
                    hour: 'numeric',
                    minute: '2-digit',
                    timeZone: price.market?.timezone || undefined,
                    timeZoneName: price.market?.timezone ? 'short' : undefined,
                  })
                )}
              </span>
            )}
          </div>
          {price.is_extended &&
            price.at_close &&
            (() => {
              const close = price.at_close!
              const closeBasis = close.change_pct ?? close.change_abs
              const closeTone =
                closeBasis == null
                  ? 'asd-price-flat'
                  : closeBasis > 0
                    ? 'asd-price-up'
                    : closeBasis < 0
                      ? 'asd-price-down'
                      : 'asd-price-flat'
              return (
                <div className="asd-price-col">
                  {/* Reuses the primary block's OWN classes (asd-price-big/-change/-pct-pill),
                      not a look-alike variant — the two columns must be pixel-identical in
                      font/weight/spacing, never a slightly-off copy (owner-reproduced live
                      bug, 2026-08-31: a custom combined "-1.05 (-1.63%)" span with no pill
                      read as visibly different typography from its neighbor). */}
                  <div className="asd-price-num-row">
                    <span className="asd-price-big">
                      {currSym}
                      {fPrice(close.price, priceMaxFrac)}
                    </span>
                    {close.change_abs != null && (
                      <span className={`asd-price-change ${closeTone}`}>
                        {signOf(close.change_abs)}
                        {fPrice(Math.abs(close.change_abs), priceMaxFrac)}
                      </span>
                    )}
                    {close.change_pct != null && (
                      <span className={`asd-price-pct-pill ${closeTone}`}>
                        {trendArrow(close.change_pct)}
                        {Math.abs(close.change_pct).toFixed(2)}%
                      </span>
                    )}
                  </div>
                  <span className="asd-price-caption">
                    {/* "At close" + its value are ONE flex child, not two — .asd-price-
                        caption's own `gap` (for the icon/label/time SEPARATE children the
                        extended caption above uses) would otherwise inject an unwanted gap
                        between "At close" and its immediately-following colon
                        (owner-reproduced live bug, 2026-09-01: "At close : Aug 31"). */}
                    <span>
                      At close
                      {close.as_of && (
                        <>
                          {/* Unlike the Pre-market/After-hours caption above, this close can be
                              from a DIFFERENT calendar day than "now" (viewed during tomorrow's
                              pre-market, "At close" is still describing yesterday) — a bare time
                              reads as ambiguous there, so the date goes in front (owner ask
                              2026-09-01). */}
                          <span className="asd-price-wide-only">
                            {`: ${new Date(close.as_of).toLocaleString(getDateLocale(), {
                              month: 'short',
                              day: 'numeric',
                              hour: 'numeric',
                              minute: '2-digit',
                              second: '2-digit',
                              timeZone: price.market?.timezone || undefined,
                              timeZoneName: 'short',
                            })}`}
                          </span>
                          {/* Narrow (mobile): a short DATE instead of the exact time — the
                              close is always ~4pm, so the time itself says nothing a viewer
                              doesn't already know; which DAY this close is from is the more
                              useful fact in the space (matches Perplexity's own compact
                              mobile layout: "At close: 31 Aug"). */}
                          <span className="asd-price-narrow-only">
                            {`: ${new Date(close.as_of).toLocaleDateString(getDateLocale(), {
                              month: 'short',
                              day: 'numeric',
                              timeZone: price.market?.timezone || undefined,
                            })}`}
                          </span>
                        </>
                      )}
                    </span>
                  </span>
                </div>
              )
            })()}
          {!price.is_extended &&
            price.last_after_hours &&
            (() => {
              // Closed market (e.g. the weekend): the close leads, and the LAST session's
              // after-hours print takes the second column — the mirror of the live-extended
              // layout above, same primitives so the two columns stay pixel-identical.
              const ah = price.last_after_hours!
              const ahBasis = ah.change_pct ?? ah.change_abs
              const ahTone =
                ahBasis == null
                  ? 'asd-price-flat'
                  : ahBasis > 0
                    ? 'asd-price-up'
                    : ahBasis < 0
                      ? 'asd-price-down'
                      : 'asd-price-flat'
              return (
                <div className="asd-price-col">
                  <div className="asd-price-num-row">
                    <span className="asd-price-big">
                      {currSym}
                      {fPrice(ah.price, priceMaxFrac)}
                    </span>
                    {ah.change_abs != null && (
                      <span className={`asd-price-change ${ahTone}`}>
                        {signOf(ah.change_abs)}
                        {fPrice(Math.abs(ah.change_abs), priceMaxFrac)}
                      </span>
                    )}
                    {ah.change_pct != null && (
                      <span className={`asd-price-pct-pill ${ahTone}`}>
                        {trendArrow(ah.change_pct)}
                        {Math.abs(ah.change_pct).toFixed(2)}%
                      </span>
                    )}
                  </div>
                  <span className="asd-price-caption">
                    <Sunset size={12} className="asd-price-extended-icon" />
                    <span>
                      After hours
                      {ah.as_of && (
                        <>
                          {`: ${new Date(ah.as_of).toLocaleTimeString(getDateLocale(), {
                            hour: 'numeric',
                            minute: '2-digit',
                            timeZone: price.market?.timezone || undefined,
                          })}`}
                        </>
                      )}
                    </span>
                  </span>
                </div>
              )
            })()}
        </div>
      )}

      {/* Body */}
      <div className="asd-body" ref={railBodyRef} style={railStyle}>
        <div className="asd-main">
          {/* Chart */}
          {activeTab === 'overview' && (
            <Panel flush className="asd-chart-panel">
              <div className="panel-section">
                <AssetPriceChart
                  instrumentId={instrumentId}
                  currency={price.currency}
                  assetClass={asset.asset_class}
                  // `last`/`as_of` are already extended-aware (see backend resolve_price_quote)
                  // — no ternary needed here anymore, they ARE the live value whichever session
                  // it came from.
                  livePrice={price.last}
                  liveAt={price.as_of}
                  onLatestPoint={handleChartLatestPoint}
                  // The chart's dashed "Prev close" reference line is ALWAYS the close of the
                  // most recently completed REGULAR session — Perplexity's own chart carries the
                  // identical annotation ("Prev close: US$513.53") regardless of session state;
                  // showing TODAY's regular close there instead (an earlier version of this line,
                  // reasoning that the extended-hours bars move against that close) was a real
                  // mislabel — a line captioned "Prev close" showing today's own close reads as
                  // simply wrong (owner-reproduced live bug, 2026-09-01, against Perplexity's own
                  // UI). Which close `at_close` itself already IS depends on which extended
                  // session we're in: during POST, `at_close` is TODAY's just-finished regular
                  // close, so its own change_abs recovers YESTERDAY's; during PRE, `at_close` IS
                  // already the most recently completed regular close (yesterday's, relative to
                  // the upcoming session) — subtracting its own change_abs would walk back ANOTHER
                  // day (owner-reproduced live bug, 2026-09-01: PRE showed $513.53 instead of
                  // $507.29 — that extra subtraction landing on the close before the close).
                  // Outside an extended session this is unchanged: `last - change_abs` already
                  // yields yesterday's close there (change_abs stays anchored to prev_close for a
                  // REGULAR quote — see resolve_price_quote).
                  prevClose={
                    price.is_extended && price.at_close && price.extended_session === 'pre'
                      ? price.at_close.price
                      : price.is_extended && price.at_close && price.at_close.change_abs != null
                        ? price.at_close.price - price.at_close.change_abs
                        : price.last != null && price.change_abs != null
                          ? price.last - price.change_abs
                          : null
                  }
                  // Crypto trades 24/7 — there is no closing bell, so no VISIBLE "Prev close"
                  // reference line/label (owner call, 2026-08-11). prevClose itself still feeds
                  // the chart's up/down coloring so it agrees with the header's % change (the
                  // 1D-baseline fix below — a real prior incident where the two disagreed);
                  // this only hides the labeled annotation, not that internal comparator.
                  showPrevCloseLine={asset.asset_class !== 'crypto'}
                  marketOpen={price.market?.is_open ?? false}
                  // 1D's fixed pre/regular/post window (owner ask 2026-08-31: "don't stretch a
                  // partial day's tape to fill the whole chart") — read straight off
                  // price.market.session_bounds, NOT gated on price.extended_hours: the same
                  // stretch problem hits REGULAR hours too (a bug caught live the moment PRE
                  // rolled into REGULAR — session_bounds is fetched for any is_open state, see
                  // backend market_status(with_session_bounds=True), extended_hours is only the
                  // pre/post QUOTE). Still null for a venue Yahoo has no session data for at
                  // all — that one keeps the existing fit-to-data behavior.
                  sessionBounds={price.market?.session_bounds ?? null}
                  timezone={price.market?.timezone ?? null}
                />
              </div>
              <QuoteStats
                instrumentId={instrumentId}
                currency={price.currency}
                kind={asset.asset_class === 'crypto' ? 'crypto' : 'equity'}
              />
            </Panel>
          )}

          {/* Overview: news + key issues */}
          {activeTab === 'overview' && (
            <>
              {/* Narrow layout only (see .asd-rail above) — Lucius's take now leads, ahead of
                  the Your Position card it's about (owner ask, 2026-09-16: reversed from the
                  prior "position first" order). */}
              {(Boolean(portfolio_impact) || generatingLuciusTake) &&
                (() => {
                  // Owner ask 2026-08-30: whenever this card renders, it shows exactly one of
                  // two states — a real generated take, or actively generating — NEVER a bare
                  // numbers-only lead line standing in for "not analyzed yet". Renders when
                  // `portfolio_impact` already has content (a live position lead line and/or a
                  // cached narrative, see backend.services.asset_detail._compose_
                  // portfolio_impact) OR `generatingLuciusTake` is true — the latter is gated on
                  // real in-flight signals (`refreshing`/`backgroundRefreshing`), not merely
                  // "no narrative yet" (R3 fix, 2026-08-31: a watch-only/ad-hoc instrument with
                  // NEITHER a position lead NOR a narrative yet used to have no card at all
                  // rather than a generating one — the on-demand trigger fires for every
                  // resolvable-ticker instrument on page open now, held/watched/ad-hoc alike,
                  // same as the Key Issues panel below).
                  // Owner ask 2026-08-31: stage-specific copy (searching/analysing/retrying, the
                  // same labels ResearchingStatus shows for the news skeleton) read as noisy and
                  // inconsistent across devices on this card specifically — always show the same
                  // short, plain label here instead, regardless of refresh_stage.
                  const progressLabel = 'Generating'
                  const generating = generatingLuciusTake
                  return (
                    <div className="asd-pi-wrap">
                      <div className="asd-pi-head">
                        <p className="asd-pi-label">Lucius's take on your position ✦</p>
                        {/* Card-scoped counterpart to the page-level RefreshingBanner (owner
                            clarification, ADR addendum "an on-demand top-up"): the banner alone
                            doesn't make clear THIS card is what's being refreshed. */}
                        {(backgroundRefreshing || generating || keyIssuesPassActive(data)) && (
                          <span
                            className="asd-pi-updating"
                            role="status"
                            aria-label="Lucius's take is updating in the background"
                          >
                            <span className="ui-btn__spinner" aria-hidden="true" />
                            {generating ? progressLabel : 'Updating'}
                          </span>
                        )}
                      </div>
                      {generating ? (
                        <div
                          className="asd-pi-body-skeleton"
                          role="status"
                          aria-label={progressLabel}
                        >
                          <Skeleton height={13} width="94%" />
                          <Skeleton height={13} width="80%" />
                          <Skeleton height={13} width="52%" />
                        </div>
                      ) : (
                        <>
                          <p className="asd-pi-body">{portfolio_impact}</p>
                          {data.position_verdict && (
                            <PositionVerdictLine
                              verdict={data.position_verdict}
                              priceMaxFrac={priceMaxFrac}
                              instrumentId={instrumentId}
                              name={asset.name}
                              ticker={asset.ticker}
                              alerts={data.watchlist.price_alerts}
                            />
                          )}
                          <p className="asd-pi-disclaimer">{TAKE_NOT_ADVICE}</p>
                        </>
                      )}
                    </div>
                  )
                })()}

              {/* Notable Price Movement — per-session timeline (live row / market-closed notice
                  on top, then each close with its cited explanation). Own fetch + adaptive poll
                  (useMovementTimeline); hides itself for a no-ticker holding or when no data.
                  Sits AFTER Lucius's take (owner ask 2026-09-20). */}
              <PriceMovementTimeline
                instrumentId={instrumentId}
                hasTicker={data.has_ticker}
                maxFrac={priceMaxFrac}
              />

              <div className="asd-mobile-only">{yourPositionBlock}</div>
              <div className="asd-mobile-only">{thesisBlock}</div>
              <div className="asd-mobile-only">{priceAlertsBlock}</div>
              <div className="asd-mobile-only">{analystConsensusBlock}</div>

              {refreshing && !hasNews && !hasIssues && (
                <NewsSectionSkeleton header={<ResearchingStatus stage={data?.refresh_stage} />} />
              )}

              {hasNews && (
                <Panel>
                  <div className="asd-section-head">
                    <span className="asd-section-title">Stories &amp; Analysis</span>
                  </div>
                  <NewsCardGrid items={news} limit={STORY_CARD_COUNT} />
                </Panel>
              )}

              {news.length > STORY_CARD_COUNT && (
                <Panel>
                  <LatestDevelopments
                    items={news}
                    offset={STORY_CARD_COUNT}
                    limit={DEV_ROW_LIMIT}
                    phoneLimit={DEV_ROW_LIMIT_PHONE}
                    expanded={devExpanded}
                    onToggle={() => setDevExpanded((v) => !v)}
                  />
                </Panel>
              )}

              {hasIssues && (
                <Panel>
                  <div className="asd-section-head">
                    <span className="asd-section-title">Key Issues</span>
                  </div>
                  <KeyIssuesAccordion issues={key_issues} />
                </Panel>
              )}

              {keyIssuesGenerating && (
                <Panel>
                  <div className="asd-section-head">
                    <span className="asd-section-title">Key Issues</span>
                    <span
                      className="asd-pi-updating"
                      role="status"
                      aria-label="Key issues are generating"
                    >
                      <span className="ui-btn__spinner" aria-hidden="true" />
                      Generating
                    </span>
                  </div>
                  <div className="asd-skel-stack">
                    <Skeleton height={13} width="92%" />
                    <Skeleton height={13} width="76%" />
                    <Skeleton height={13} width="85%" />
                  </div>
                </Panel>
              )}

              {!hasNews &&
                !hasIssues &&
                !keyIssuesGenerating &&
                !refreshing &&
                !backgroundRefreshing && (
                  <EmptyState
                    title="No news or analysis yet"
                    body={
                      data.has_ticker
                        ? 'Click Refresh to fetch the latest news and key issues for this asset.'
                        : 'This asset has no ticker symbol, so automated news fetching is unavailable.'
                    }
                    action={
                      data.has_ticker ? (
                        <Button variant="primary" size="sm" onClick={refresh} disabled={refreshing}>
                          {refreshing ? 'Refreshing…' : 'Refresh now'}
                        </Button>
                      ) : undefined
                    }
                  />
                )}
            </>
          )}

          {/* Analysis tab: Key Issues → Estimates → Ratings → FA → Technicals */}
          {activeTab === 'analysis' && (
            <>
              {hasIssues ? (
                <Panel>
                  <div className="asd-section-head">
                    <span className="asd-section-title">Key Issues</span>
                  </div>
                  <KeyIssuesAccordion issues={key_issues} />
                </Panel>
              ) : keyIssuesGenerating ? (
                <Panel>
                  <div className="asd-section-head">
                    <span className="asd-section-title">Key Issues</span>
                    <span
                      className="asd-pi-updating"
                      role="status"
                      aria-label="Key issues are generating"
                    >
                      <span className="ui-btn__spinner" aria-hidden="true" />
                      Generating
                    </span>
                  </div>
                  <div className="asd-skel-stack">
                    <Skeleton height={13} width="92%" />
                    <Skeleton height={13} width="76%" />
                    <Skeleton height={13} width="85%" />
                  </div>
                </Panel>
              ) : (
                <EmptyState
                  title="No key issues yet"
                  body="Lucius hasn't written the bull and bear case for this asset yet. Refresh to try again."
                  action={
                    data.has_ticker ? (
                      <Button variant="primary" size="sm" onClick={refresh} disabled={refreshing}>
                        {refreshing ? 'Refreshing…' : 'Refresh now'}
                      </Button>
                    ) : undefined
                  }
                />
              )}
              <AnalystConsensus
                data={analyst.data}
                loading={analyst.loading}
                currency={price.currency}
                section="estimates"
              />
              <AnalystConsensus
                data={analyst.data}
                loading={analyst.loading}
                currency={price.currency}
                section="ratings"
              />
              {data.has_ticker && (
                <FundamentalsSnapshot instrumentId={instrumentId} currency={price.currency} />
              )}
              {data.has_ticker && <TechnicalsWidget instrumentId={instrumentId} />}
              {/* Narrow layout only — Facts/Peers travel with Analysis content. */}
              <div className="asd-mobile-only">{factsAndPeersBlock}</div>
            </>
          )}

          {/* Position tab: holding summary strip + full acquisition/realized lots when held;
              a fully-divested holding still shows its real history (ClosedPositionHistory);
              a true empty state only when NEITHER applies (never held at all). Tab is always
              shown. */}
          {activeTab === 'position' &&
            (position && position.held ? (
              <PositionSubtab
                assetClass={asset.asset_class}
                position={position}
                symbol={asset.ticker ?? asset.symbol}
                instrumentId={instrumentId}
                income={income}
                onChanged={refetch}
              />
            ) : position && position.has_history ? (
              <ClosedPositionHistory
                position={position}
                symbol={asset.ticker ?? asset.symbol}
                instrumentId={instrumentId}
                onChanged={refetch}
              />
            ) : (
              <EmptyState
                title="No position in this asset"
                body={`You don't currently hold ${asset.ticker ?? asset.name}. Record your first acquisition to start tracking it here.`}
                action={
                  <Button variant="primary" size="sm" onClick={() => setAddLotOpen(true)}>
                    Add lot
                  </Button>
                }
              />
            ))}

          {/* Sticky banner inside asd-main so it centres over this column only, not the full
              page width (which includes the 340px right rail). `refreshing` (the explicit POST
              pipeline — news/facts/peers) and `backgroundRefreshing` (the on-demand key_issues
              top-up — a quiet background pass this hook never POSTed for) each have their OWN
              stage field (refresh_stage / key_issues_refresh_stage, split R2 fix 2026-08-31 —
              see useAssetDetail's watch effect) and are checked independently: the banner stays
              up as long as EITHER active pipeline hasn't reached "ready", so a cold ad-hoc open
              (both pipelines fire together, and news/facts/peers routinely finishes first) keeps
              showing progress for the whole wait instead of hiding the instant the FASTER of the
              two lands. "ready" (owner report 2026-08-11: "I see lucius's take is updated but
              then the bar is still showing") means the genuinely time-sensitive content for that
              pipeline has landed, even though background company-profile work may still be
              finishing server-side. Deliberately never feeds the full-skeleton takeover above
              (there's already content on screen whenever this fires), only this non-blocking
              indicator. */}
          {((refreshing && data?.refresh_stage !== 'ready') ||
            (backgroundRefreshing && data?.key_issues_refresh_stage !== 'ready')) &&
            (hasNews || hasIssues) &&
            !generatingLuciusTake && (
              <div className="asd-ask-bar mb-anchored">
                <RefreshingBanner
                  stage={
                    refreshing && data?.refresh_stage !== 'ready'
                      ? data?.refresh_stage
                      : data?.key_issues_refresh_stage
                  }
                />
              </div>
            )}
        </div>

        {/* Right rail — desktop only below 720px (the SAME breakpoint .asd-body itself
            reflows to a single column at, see index.css) — the narrow layout instead
            places Your Position inline in Overview and Facts/Peers inline in Analysis
            (below), each next to the content it's actually about. */}
        <div className="asd-rail asd-desktop-only" ref={railRef}>
          <div
            className={`asd-rail-handle${railSnapped ? ' asd-rail-handle--snapped' : ''}`}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize side panel"
            title="Drag to resize · drag back or double-click for the default width"
            {...railHandleProps}
          />
          {yourPositionBlock}
          {thesisBlock}
          {priceAlertsBlock}
          {/* Hidden only on Analysis, which already has the full Estimates/Ratings
              breakdown — shown on every other tab (Overview, Position). */}
          {activeTab !== 'analysis' && analystConsensusBlock}
          {factsAndPeersBlock}
        </div>
      </div>
      {addLotOpen && (
        <AcquireLotForm
          symbol={asset.ticker ?? asset.symbol}
          costCcy={position?.cost_currency || price.currency || asset.currency}
          instrumentId={instrumentId}
          lockSymbol
          onDone={() => {
            refetch()
          }}
          onClose={() => setAddLotOpen(false)}
        />
      )}
    </div>
  )
}

// Next earnings date from the price feed's own timestamp (never guessed); shown only when it is
// within the next six weeks — the window in which it changes what you'd do.
const EARNINGS_WINDOW_DAYS = 45
function EarningsChip({ iso }: { iso?: string }) {
  if (!iso) return null
  const when = new Date(iso)
  if (isNaN(when.getTime())) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const day = new Date(when)
  day.setHours(0, 0, 0, 0)
  const days = Math.round((day.getTime() - today.getTime()) / 86_400_000)
  if (days < 0 || days > EARNINGS_WINDOW_DAYS) return null
  const date = fDate(when)
  const text = days === 0 ? 'Earnings today' : days === 1 ? 'Earnings tomorrow' : `Earnings ${date}`
  return (
    <span
      className="asd-earnings-chip"
      title={`Next earnings report: ${fDateTime(when, 'short', 'always')}`}
    >
      {text}
      {days > 1 && days <= 14 && <span className="asd-earnings-chip-in"> · in {days} days</span>}
    </span>
  )
}
