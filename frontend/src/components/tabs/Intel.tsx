import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { AssetDetailView } from '../intel/AssetDetailView'
import { QuoteDetailView } from '../intel/QuoteDetailView'
import { getBaseCurrency } from '../../utils/format'
import { Cluster, Container, EmptyState, PageTabs, Stack, TabSkeleton } from '../ui'
import { Plus, Radar } from '../../ui/icons'
import { ScanToast } from '../intel/ScanToast'
import { ScanConfirmModal } from '../intel/ScanConfirmModal'
import { IntelPageHeader } from '../intel/IntelPageHeader'
import { MacroColdStart } from '../intel/MacroColdStart'
import { LuciusCta } from '../intro/LuciusCta'
import { IntelBriefCard } from '../intel/IntelBriefCard'
import { verbosityOf } from '../intel/monitoringConfig'
import { WatchPulseCard } from '../intel/WatchPulseCard'
import { IntelFeedPanel } from '../intel/IntelFeedPanel'
import { TopicsStrip } from '../intel/TopicsStrip'
import { TickerDesk } from '../intel/TickerDesk'
import { listIdOf, tabOfList, tabOfStory } from '../intel/intelTabs'
import { useWatchPulse } from '../../hooks/useWatchPulse'
import { PULSE_SORT_KEYS } from '../../hooks/usePulseSort'
import { takeIntelStoryFocus } from '../../hooks/useIntelStoryIndex'
import type { WatchPulseRow } from '../../types/watchPulse'
import { WatchlistsSection } from '../intel/WatchlistsSection'
import type { WatchlistItem } from '../intel/WatchlistsSection'
import { MonitorsWorkspace } from '../intel/MonitorsWorkspace'
import { PriceAlertsModal } from '../intel/PriceAlertsModal'
import { TopicDetailModal } from '../intel/TopicDetailModal'
import { ConfirmDialog } from '../intel/ConfirmDialog'
import type { ConfirmSpec } from '../intel/ConfirmDialog'
import { useIntelData } from '../intel/useIntelData'
import { useIntelTab } from '../intel/useIntelTab'
import { isArchived, useTabFeeds } from '../intel/useTabFeeds'
import { alertItemForRow } from '../intel/watchlistItems'
import { useIntelScan } from '../intel/useIntelScan'
import { usePortfolioTickerMaps } from '../intel/usePortfolioTickerMaps'
import { useSignalActions } from '../intel/useSignalActions'
import { useStoryFocus } from '../intel/useStoryFocus'
import { useWatchlistRemoval } from '../intel/useWatchlistRemoval'

const HOLDINGS_MESSAGE =
  'Help me add my investments so Intel can follow them. Ask which brokers I use; I can send a ' +
  'screenshot or statement of my holdings instead of typing. Ask me one thing at a time.'

interface IntelProps {
  onChatSuggest: (text: string) => void
  // Ticker-backed assets carry only `ticker` (the /quote/$symbol route resolves it
  // itself); the id-only shape is the /asset/$instrumentId fallback for tickerless
  // (manual/private) holdings.
  selectedAsset?:
    | { instrument_id: string; label?: string; ticker?: string }
    | { ticker: string; label?: string }
    | null
  // When true the monitors workspace is open because the router is on /intel/monitors.
  showMonitorsRoute?: boolean
  /** Deep-link request (?sub=holdings | list:<id> | macro, or the legacy watching). */
  subTabRequest?: string
  /** Intel is only the backdrop behind Settings or Help — the URL isn't Intel's to write. */
  backdrop?: boolean
}

export function Intel({
  onChatSuggest,
  selectedAsset,
  showMonitorsRoute,
  subTabRequest,
  backdrop,
}: IntelProps) {
  if (selectedAsset && 'instrument_id' in selectedAsset) {
    return (
      <Container size="wide">
        <AssetDetailView
          instrumentId={selectedAsset.instrument_id}
          seedName={selectedAsset.label}
          seedTicker={selectedAsset.ticker}
        />
      </Container>
    )
  }
  if (selectedAsset) {
    return (
      <Container size="wide">
        <QuoteDetailView ticker={selectedAsset.ticker} seedName={selectedAsset.label} />
      </Container>
    )
  }
  return (
    <IntelFeed
      onChatSuggest={onChatSuggest}
      showMonitorsRoute={showMonitorsRoute}
      subTabRequest={subTabRequest}
      backdrop={backdrop}
    />
  )
}

function IntelFeed({
  onChatSuggest,
  showMonitorsRoute,
  subTabRequest,
  backdrop,
}: {
  onChatSuggest: (text: string) => void
  showMonitorsRoute?: boolean
  subTabRequest?: string
  backdrop?: boolean
}) {
  const navigate = useNavigate()
  const data = useIntelData()
  const { stories, alerts, watchlist, items, watchlists, tickerMeta, loadWatchlist } = data
  const { tickerCcy, ownedTickers, heldSet, luciusCtx } = usePortfolioTickerMaps()
  const nav = useIntelTab({
    watchlists: watchlist ? watchlists : null,
    subTabRequest,
    monitorsOpen: showMonitorsRoute,
    backdrop,
  })
  const { tab } = nav
  const [active, setActive] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null)
  // The watchlist SECTION the ticker was opened from (distinct from the tab's list — I7 Blocker 2).
  const [editAsset, setEditAsset] = useState<{ item: WatchlistItem; from?: string } | null>(null)
  const [editTopic, setEditTopic] = useState<WatchlistItem | null>(null)
  const { registerRef, scrollToStory } = useStoryFocus()
  const actions = useSignalActions(stories, setConfirm)
  const scan = useIntelScan(stories, loadWatchlist)
  const { data: pulse } = useWatchPulse()
  const heldPulse = useWatchPulse('held')

  const { tabDefs, tabs, tickersByList, feedStories, feedByTab, archiveByTab } = useTabFeeds(
    stories,
    items,
    watchlists,
    heldSet,
  )

  // The sidebar's per-ticker move and story dot, for held AND watched names.
  const dayPctByTicker = useMemo<Record<string, number>>(() => {
    const m: Record<string, number> = {}
    for (const r of [...(heldPulse.data?.rows ?? []), ...(pulse?.rows ?? [])])
      if (r.day_pct != null) m[r.ticker] = r.day_pct
    return m
  }, [pulse, heldPulse.data])
  const storyTickers = useMemo(
    () => new Set(feedStories.map((s) => (s.ticker ?? '').toUpperCase()).filter(Boolean)),
    [feedStories],
  )

  const tabListId = listIdOf(tab)
  // The sidebar card shows the tab's list: My holdings on Holdings, the list on a list tab (none
  // on Topics & macro — topics are in no list; its lead card lists and adds them).
  const activeListId = tabListId ?? watchlists.find((w) => w.is_auto_sync)?.id ?? 'default'
  const activeList = watchlists.find((w) => w.id === activeListId)
  const tabTickers = tabListId ? tickersByList.get(tabListId) : undefined
  // The list's tickers you own come from the held pulse (with their position) — the watched pulse
  // never carries a name you hold.
  const ownedRows = useMemo(
    () => (heldPulse.data?.rows ?? []).filter((r) => tabTickers?.has(r.ticker)),
    [heldPulse.data, tabTickers],
  )
  const removal = useWatchlistRemoval({
    watchlists,
    activeListId,
    setConfirm,
    onRemoved: () => {
      setEditAsset(null)
      setEditTopic(null)
      void loadWatchlist()
    },
  })

  // Jump to a story wherever it lives: its tab (and its archive, if it's archived), no type
  // filter, then scroll to it the moment its card is on the page.
  function focusStory(id: string) {
    const target = stories.find((x) => x.id === id)
    if (target) {
      const archived = isArchived(target)
      const where = tabOfStory(id, tab, archived ? archiveByTab : feedByTab, tabDefs)
      nav.jumpTo(where ?? tab, archived)
    } else {
      nav.setFilter('all')
    }
    setActive(id)
    scrollToStory(id)
  }

  // A story dot on an Investments holding hands off here (useIntelStoryIndex): focus it once the
  // feed has loaded.
  useEffect(() => {
    if (data.loading) return
    const pending = takeIntelStoryFocus()
    // Scheduled, not synchronous: this effect only READS an external hand-off (sessionStorage).
    if (pending) queueMicrotask(() => focusStory(pending))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.loading])

  const openManage = () => navigate({ to: '/intel/monitors' })
  const editAlertsForRow = (row: WatchPulseRow) =>
    setEditAsset({ item: alertItemForRow(items, row) })
  // A list picked inside the Manage workspace opens its tab behind the dialog, so closing it
  // lands on the list you were managing.
  const selectList = (id: string) => {
    if (watchlists.some((x) => x.id === id)) nav.setTab(tabOfList(watchlists, id))
  }
  const addTopic = (topic: string) =>
    onChatSuggest(
      `Follow "${topic}" as a topic (topics aren't kept in a watchlist), then run a scan. Create 3–5 targeted search queries for it, referencing my relevant holdings where applicable.`,
    )

  const cfg = watchlist?.config
  // Story actions + the tab's archive toggle, shared by every story list on the page.
  const storyProps = {
    active,
    showArchive: nav.showArchive,
    onToggleArchive: nav.toggleArchive,
    onRead: actions.markRead,
    onDismiss: actions.dismiss,
    onRestore: actions.restore,
    onDelete: actions.requestDelete,
    onMarkAllRead: actions.markAllRead,
    onAskLucius: onChatSuggest,
    registerRef,
  }
  const deskProps = {
    ...storyProps,
    onFocusStory: focusStory,
    onEditAlerts: editAlertsForRow,
    // The page header's News freshness chip already says this when monitoring is on.
    newsScannedAt: cfg?.enabled ? undefined : (cfg?.last_scan_at ?? null),
  }
  const heldSummary = heldPulse.data?.summary

  // First load is one page-shaped placeholder; later refreshes keep the live content.
  if (data.loading) {
    return (
      <Container size="wide" className="intel-tab">
        <TabSkeleton variant="intel" label="Loading intelligence…" />
      </Container>
    )
  }

  const liveItem = (item: WatchlistItem) => items.find((i) => i.id === item.id) ?? item
  const assetItem = editAsset ? liveItem(editAsset.item) : null
  const topicItem = editTopic ? liveItem(editTopic) : null

  return (
    <Container size="wide" className="intel-tab">
      <ScanConfirmModal
        open={scan.confirmOpen}
        onCancel={scan.cancel}
        onConfirm={() => void scan.run()}
      />

      <Stack gap="--space-6">
        <IntelPageHeader
          config={cfg}
          scanning={scan.scanning}
          onScan={() => !scan.scanning && scan.askToScan()}
        />

        {/* My holdings · your ticker watchlists · Topics & macro. */}
        <div className="intel-tabs-row">
          <PageTabs ariaLabel="Intelligence view" value={tab} onChange={nav.setTab} tabs={tabs} />
          <button
            type="button"
            className="intel-tabs-new"
            onClick={openManage}
            title="Create or manage watchlists"
          >
            <Plus size={13} aria-hidden="true" />
            New list
          </button>
        </div>

        {/* Topics & macro is one column: topics are in no list, so there's no list pane. */}
        <div className={`intel-layout${tab === 'macro' ? ' intel-layout--solo' : ''}`}>
          <div className="intel-feed">
            <div role="tabpanel" aria-labelledby={`ui-page-tab-${tab}`} className="intel-tab-lead">
              {tab === 'holdings' ? (
                // One card: the brief's read of the day on top, then everything you own —
                // portfolio-wide stories, then each holding's price, flags and stories.
                <IntelBriefCard
                  onChatSuggest={onChatSuggest}
                  verbosity={verbosityOf(cfg ?? null, 'holdings')}
                >
                  <TickerDesk
                    {...deskProps}
                    rows={heldPulse.data?.rows ?? []}
                    benchSymbol={heldPulse.data?.benchmark?.symbol}
                    loading={heldPulse.loading && !heldPulse.data}
                    unpriced={heldSummary ? heldSummary.tracked - heldSummary.priced : 0}
                    noun={{ one: 'holding', many: 'holdings' }}
                    sortKey={PULSE_SORT_KEYS.holdings}
                    empty={
                      <EmptyState
                        icon={<Radar size={28} strokeWidth={1.5} aria-hidden="true" />}
                        title="No priced holdings yet"
                        body="News, risk flags and price moves on what you own land here."
                        action={
                          <Cluster gap="--space-3" justify="center">
                            <LuciusCta
                              label="Add with Lucius"
                              message={HOLDINGS_MESSAGE}
                              onAskLucius={onChatSuggest}
                            />
                          </Cluster>
                        }
                      />
                    }
                    feed={feedByTab.holdings}
                    archive={archiveByTab.holdings}
                  />
                </IntelBriefCard>
              ) : tabListId && activeList ? (
                // One card per list: each ticker on it (owned ones marked) with its price, trend
                // and flags, and the news scan's stories on it folded in.
                <WatchPulseCard
                  verbosity={verbosityOf(cfg ?? null, 'watchlist')}
                  key={tab}
                  scope={{
                    id: activeList.id,
                    name: activeList.name,
                    tickerCount: tabTickers?.size ?? 0,
                  }}
                  ownedRows={ownedRows}
                  ownedLoading={heldPulse.loading && !heldPulse.data}
                  onAddToWatchlist={openManage}
                  onChatSuggest={onChatSuggest}
                  renderBody={(rows, unpriced) => (
                    <TickerDesk
                      {...deskProps}
                      rows={rows}
                      benchSymbol={pulse?.benchmark?.symbol}
                      loading={false}
                      unpriced={unpriced}
                      noun={{ one: 'ticker', many: 'tickers' }}
                      sortKey={PULSE_SORT_KEYS.lists}
                      markOwned
                      empty={<div className="intel-feed-empty">Nothing on this list yet.</div>}
                      feed={feedByTab[tab] ?? []}
                      archive={archiveByTab[tab] ?? []}
                    />
                  )}
                />
              ) : (
                <>
                  <TopicsStrip
                    topics={items.filter((i) => i.type === 'topic' || i.type === 'company')}
                    onEditTopic={setEditTopic}
                    onAddTopic={addTopic}
                  />
                  {feedByTab.macro.length + archiveByTab.macro.length === 0 ? (
                    <MacroColdStart
                      scanning={scan.scanning}
                      onScan={scan.askToScan}
                      onAskLucius={onChatSuggest}
                    />
                  ) : (
                    <IntelFeedPanel
                      {...storyProps}
                      feed={feedByTab.macro}
                      archive={archiveByTab.macro}
                      filter={nav.filter}
                      onFilter={nav.setFilter}
                      emptyText="No macro or topic stories right now."
                    />
                  )}
                </>
              )}
            </div>
          </div>

          {/* sidebar cockpit: the tab's list of tickers */}
          {tab !== 'macro' && (
            <div className="intel-side">
              <WatchlistsSection
                key={activeListId}
                watchlists={watchlists}
                items={items}
                alerts={alerts}
                activeListId={activeListId}
                tickerMeta={tickerMeta}
                tickerCcy={tickerCcy}
                ownedTickers={ownedTickers}
                dayPctByTicker={dayPctByTicker}
                storyTickers={storyTickers}
                onEditAlerts={(item, from) => setEditAsset({ item, from })}
                onManageAll={openManage}
                onRefresh={loadWatchlist}
                loading={watchlist === null && !data.watchlistError}
                error={data.watchlistError}
              />
            </div>
          )}
        </div>
      </Stack>

      {/* modals */}
      {assetItem && (
        <PriceAlertsModal
          key={assetItem.id}
          item={{ ...assetItem, price_alerts: alerts.filter((a) => a.ticker === assetItem.value) }}
          livePrice={tickerMeta[assetItem.value]?.price}
          ccy={tickerCcy[assetItem.value] || getBaseCurrency()}
          luciusCtx={luciusCtx}
          canRemove={!!assetItem.id && assetItem.added_by !== 'auto' && !activeList?.is_auto_sync}
          backLabel="Back to watchlist"
          onClose={() => setEditAsset(null)}
          onSaved={() => {
            void loadWatchlist()
            void data.loadAlerts()
          }}
          onRemove={() => removal.requestRemoveTicker(assetItem, editAsset?.from)}
        />
      )}
      {topicItem && (
        <TopicDetailModal
          key={topicItem.id}
          item={topicItem}
          onClose={() => setEditTopic(null)}
          onSaved={loadWatchlist}
          onDelete={() => removal.requestUnfollowTopic(topicItem)}
        />
      )}
      {showMonitorsRoute && (
        <MonitorsWorkspace
          watchlists={watchlists}
          items={items}
          alerts={alerts}
          activeListId={activeListId}
          onSelectList={selectList}
          tickerMeta={tickerMeta}
          tickerCcy={tickerCcy}
          ownedTickers={ownedTickers}
          luciusCtx={luciusCtx}
          onEditAlerts={(item, from) => setEditAsset({ item, from })}
          onEditTopic={setEditTopic}
          onRefresh={loadWatchlist}
          // Navigate, never history.back(): the entry behind /intel/monitors is not always /intel.
          // Opened from Settings' close (…/monitors → /settings → /monitors), back landed in
          // Settings, whose close pushed /monitors again — the two dialogs trapped each other.
          onClose={() => void navigate({ to: '/intel', search: { sub: tab } })}
          onOpenSettings={() => void navigate({ to: '/settings', search: { sub: 'intel' } })}
        />
      )}
      {confirm && <ConfirmDialog spec={confirm} onCancel={() => setConfirm(null)} />}

      <ScanToast scan={scan.scan} onClose={scan.dismiss} />
    </Container>
  )
}
