import { useNavigate, useSearch } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { ActionMenu, Button, ConfirmDialog, ErrorState, PageTabs, TabSkeleton } from '../../ui'
import { Bell, ChevronLeft } from '../../../ui/icons'
import { Spark } from '../../../lucius/identity'
import { Tooltip } from '../../ui/Tooltip'
import {
  followPortfolio,
  refreshFollow,
  setFollowTicker,
  unfollowPortfolio,
} from '../../../api/followedPortfolios'
import { COPY, askPrompt } from './following.copy'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePortfolioTickerMaps } from '../usePortfolioTickerMaps'
import { ActivityTab } from './ActivityTab'
import { activitySections, hasActivity as anyActivity, missedQuarter } from './activityRows'
import { HoldingRows } from './HoldingRows'
import { KeyFigures } from './KeyFigures'
import { AboutPortfolio } from './AboutPortfolio'
import { OverviewTab } from './OverviewTab'
import { usePreviewPage } from './useFollowing'
import type { PortfolioRef } from './Discover'

type Tab = 'activity' | 'holdings' | 'overview'

interface Props {
  portfolio: PortfolioRef
  onBack: () => void
  onChanged: () => Promise<unknown>
  onAskLucius?: (text: string) => void
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function PortfolioPage({ portfolio, onBack, onChanged, onAskLucius }: Props) {
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  // Your own book: every table marks the stocks you hold.
  const { heldSet } = usePortfolioTickerMaps()
  const page = usePreviewPage(portfolio.kind, portfolio.source_ref)
  // The open tab lives in the address (?tab=), so a reload, Back and a shared link keep it.
  const search = useSearch({ strict: false }) as { tab?: string }
  // Overview was folded into Holdings: an old ?tab=overview link opens Holdings.
  const tab: Tab = search.tab === 'holdings' || search.tab === 'overview' ? search.tab : 'activity'
  const setTab = (next: Tab) =>
    void navigate({
      to: '/intel',
      search: ((prev: Record<string, unknown>) => ({
        ...prev,
        tab: next === 'activity' ? undefined : next,
      })) as never,
      replace: true,
    } as never)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  // Switching tabs from below the tab bar starts the new tab at its top, the tab bar in view:
  // a tab's height differs (Trading Activity is often twice Holdings), so keeping the old scroll
  // would land in the middle of the new one, or past its end.
  const tabsRef = useRef<HTMLDivElement>(null)
  const switchTab = (next: Tab) => {
    const bar = tabsRef.current
    if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ block: 'start' })
    setTab(next)
  }

  if (page.initialLoading)
    return <TabSkeleton variant="holdings" label={COPY.loadingPage} header={false} />
  if (!page.data)
    return (
      <ErrorState
        title={COPY.loadFailedTitle}
        message={COPY.pageErrorBody}
        onRetry={() => void page.refresh()}
      />
    )

  const { follow, follow_id: followId, performance, names = {}, sectors = {} } = page.data
  const model = follow.model
  const member = follow.kind === 'house_member'

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await Promise.all([onChanged(), page.refresh()])
      return true
    } catch (e) {
      setError(errorText(e))
      return false
    } finally {
      setBusy(false)
    }
  }
  const watch = () => act(() => followPortfolio(portfolio.kind, portfolio.source_ref))
  // A correction lives on a watched portfolio (it outranks the lookup on every refresh), so
  // fixing a ticker on one you only opened starts watching it; the dialog says so.
  const setTicker = async (key: string, symbol: string) => {
    const id = followId ?? (await followPortfolio(portfolio.kind, portfolio.source_ref)).follow.id
    await setFollowTicker(id, key, symbol)
    await Promise.all([onChanged(), page.refresh()])
  }
  const copyPortfolio = async () => {
    if (!followId && !(await watch())) return
    void navigate({
      to: '/automations',
      search: { template: 'copy-followed-portfolio', for: follow.name } as never,
    } as never)
  }
  const menu = [
    // On a phone the call to action lives here: the header keeps only the name.
    ...(isMobile ? [{ label: COPY.copyCta, onSelect: () => void copyPortfolio() }] : []),
    ...(onAskLucius
      ? [
          {
            label: COPY.askLucius,
            // A question to edit, not a request: it waits in Lucius's composer (as every other
            // "Ask Lucius" in the app does) until the user sends it.
            onSelect: () => onAskLucius(askPrompt(follow.name)),
          },
        ]
      : []),
    ...(followId
      ? [
          {
            label: COPY.refresh,
            onSelect: () => void act(() => refreshFollow(followId)),
            disabled: busy,
          },
        ]
      : []),
    ...(follow.source_url
      ? [
          {
            label: member ? COPY.viewReport : COPY.viewFiling,
            onSelect: () => window.open(follow.source_url!, '_blank', 'noopener,noreferrer'),
          },
        ]
      : []),
  ]
  // A portfolio with nothing to show as activity yet (an ETF's first daily read) opens on
  // its holdings, and offers no tab that would only say "nothing here".
  const hasActivity = anyActivity(activitySections(follow))
  const tabs: { key: Tab; label: string }[] = [
    ...(hasActivity ? [{ key: 'activity' as Tab, label: COPY.tabActivity }] : []),
    { key: 'holdings', label: COPY.tabHoldings },
    { key: 'overview', label: COPY.tabOverview },
  ]
  const missed = follow.kind === 'fund_13f' && model ? missedQuarter(model.as_of) : null
  // The newest dated trade: for a fund only when it is newer than the snapshot (else the
  // snapshot already says it all); for a member, always (their holdings are an estimate).
  const newest = follow.activity
    .filter((t) => t.counted && t.symbol && t.side !== 'exchange')
    .reduce<
      (typeof follow.activity)[number] | null
    >((best, t) => (!best || t.date > best.date ? t : best), null)
  const latestTrade =
    newest && (follow.kind === 'house_member' || !model || newest.date > model.as_of)
      ? newest
      : null
  const shownTab: Tab = tab === 'activity' && !hasActivity ? 'holdings' : tab

  return (
    <div className="fol-page">
      <header className="fol-head">
        <div className="fol-id">
          <h2 className="fol-title">
            {/* The way back sits on the name's own line: no line of its own above it. */}
            <Tooltip label={COPY.backToAllLabel}>
              <button
                type="button"
                className="fol-back-icon"
                aria-label={COPY.backToAllLabel}
                onClick={onBack}
              >
                <ChevronLeft size={20} aria-hidden="true" />
              </button>
            </Tooltip>
            <span className="fol-title-name">{follow.name}</span>
            {/* Whose it is, quiet after the name; it gives way first on a narrow screen. */}
            {follow.subtitle && <span className="fol-title-by">{follow.subtitle}</span>}
          </h2>
        </div>
        {/* Icon actions stay level with the name at every width; the call to action sits
            with them on a wide screen and moves under the name on a narrow one. */}
        <div className="fol-icons">
          <Tooltip label={followId ? COPY.unwatchLabel : COPY.watchLabel}>
            <Button
              size="sm"
              variant="ghost"
              loading={busy}
              className={followId ? 'fol-watch is-on' : 'fol-watch'}
              aria-label={followId ? COPY.unwatchLabel : COPY.watchLabel}
              aria-pressed={!!followId}
              iconLeft={<Bell size={15} fill={followId ? 'currentColor' : 'none'} />}
              onClick={() => (followId ? setConfirming(true) : void watch())}
            />
          </Tooltip>
          <ActionMenu label={COPY.moreActions(follow.name)} title={follow.name} items={menu} />
        </div>
        {/* Lucius does the copying (an automation that trades your broker account to match), so
            it is the app's Lucius action: the primary button in Lucius's colour with its Spark. */}
        <div className="fol-cta">
          <Tooltip label={COPY.copyPitch}>
            <Button
              size="sm"
              variant="primary"
              className="fi-lucius"
              iconLeft={<Spark size={15} ariaHidden />}
              onClick={() => void copyPortfolio()}
            >
              {COPY.copyCta}
            </Button>
          </Tooltip>
        </div>
      </header>
      {error && <div className="fol-note fol-note--bad">{error}</div>}
      <div ref={tabsRef}>
        <PageTabs
          tabs={tabs}
          value={shownTab}
          onChange={switchTab}
          ariaLabel={COPY.sectionsLabel(follow.name)}
        />
      </div>
      {/* At least a screen tall: a short tab never pulls the page up under the reader. */}
      <div className="fol-tabpanel">
        {shownTab === 'activity' && (
          <ActivityTab
            follow={follow}
            perf={performance}
            names={names}
            held={heldSet}
            onAskLucius={onAskLucius}
          />
        )}
        {/* One Holdings tab: what they hold and how it has done, then the holdings themselves. */}
        {shownTab === 'holdings' && model && (
          <HoldingRows
            model={model}
            period="12M"
            win={performance.periods['12M']}
            names={names}
            sectors={sectors}
            watchesToo={!followId}
            held={heldSet}
            exchanges={performance.exchanges}
            onSetTicker={follow.kind === 'fund_13f' ? setTicker : undefined}
          />
        )}
        {/* What it is and how it has done: a few sentences, its key figures, its return. */}
        {shownTab === 'overview' && model && (
          <div className="fol-sec">
            <AboutPortfolio follow={follow} model={model} names={names} sectors={sectors} />
            <KeyFigures follow={follow} model={model} latestTrade={latestTrade} missed={missed} />
            <OverviewTab
              perf={performance}
              name={follow.name}
              onRetry={() => void page.refresh()}
            />
          </div>
        )}
      </div>
      <ConfirmDialog
        data={
          confirming
            ? {
                title: COPY.unfollowTitle(follow.name),
                body: COPY.unfollowBody,
                confirmLabel: COPY.unwatch,
                danger: true,
              }
            : null
        }
        onConfirm={() => {
          setConfirming(false)
          if (followId) void act(() => unfollowPortfolio(followId))
        }}
        onCancel={() => setConfirming(false)}
      />
    </div>
  )
}
