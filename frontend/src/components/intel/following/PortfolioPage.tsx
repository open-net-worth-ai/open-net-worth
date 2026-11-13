import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ActionMenu, Button, ConfirmDialog, ErrorState, PageTabs } from '../../ui'
import { Bell } from '../../../ui/icons'
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
import { usePreviewPage, usePreviewReturns } from './useFollowing'
import { useElapsed, useSlow } from './useSlow'
import { useStickTop } from './useStickTop'
import { FetchBanner, PageSkeleton } from './FirstLoad'
import { Crumb } from './Crumb'
import { Toast } from '../../tabs/accounts/Toast'
import type { PortfolioRef } from './Discover'

type Tab = 'activity' | 'holdings' | 'compare'
const DEFAULT_RANGE = '1Y'

interface Props {
  portfolio: PortfolioRef
  onBack: () => void
  onChanged: () => Promise<unknown>
  onAskLucius?: (text: string) => void
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

function scrollerOf(el: HTMLElement): HTMLElement {
  let node: HTMLElement | null = el.parentElement
  while (node && !/(auto|scroll)/.test(getComputedStyle(node).overflowY)) node = node.parentElement
  return node ?? (document.scrollingElement as HTMLElement)
}

export function PortfolioPage({ portfolio, onBack, onChanged, onAskLucius }: Props) {
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  // Your own book: every table marks the stocks you hold.
  const { heldSet } = usePortfolioTickerMaps()
  const page = usePreviewPage(portfolio.kind, portfolio.source_ref)
  const returns = usePreviewReturns(portfolio.kind, portfolio.source_ref, page.data != null)
  const slow = useSlow(page.initialLoading)
  const seconds = useElapsed(page.initialLoading)
  // Opened on the five quarters its card read: ask again until the older ones are in, then
  // price every holding against the whole history.
  const extending = page.data?.history_complete === false
  const { refresh: refreshPage } = page
  const { refresh: refreshReturns } = returns
  useEffect(() => {
    if (!extending) return
    const timer = setInterval(() => void refreshPage(), 3000)
    return () => {
      clearInterval(timer)
      void refreshReturns()
    }
  }, [extending, refreshPage, refreshReturns])
  // The open tab lives in the address (?tab=), so a reload, Back and a shared link keep it.
  const search = useSearch({ strict: false }) as { tab?: string; range?: string }
  // One time range for every tab (?range=), 1Y by default: the same window as the card's return,
  // so the page opens on the figure the card showed (owner, 2026-11-13).
  const range = search.range ?? DEFAULT_RANGE
  const setRange = (next: string) =>
    void navigate({
      to: '/intel',
      search: ((prev: Record<string, unknown>) => ({
        ...prev,
        range: next === DEFAULT_RANGE ? undefined : next,
      })) as never,
      replace: true,
    } as never)
  // Overview became Comparison (owner, 2026-11-13): an old ?tab=overview link opens it.
  const tab: Tab =
    search.tab === 'holdings'
      ? 'holdings'
      : search.tab === 'compare' || search.tab === 'overview'
        ? 'compare'
        : 'activity'
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
  // What a tap on the bell just did: the app's toast, not a note in the page (owner,
  // 2026-11-13). It dismisses itself.
  const [notice, setNotice] = useState<string | null>(null)
  // Switching tabs from below the tab bar starts the new tab at its top, the tab bar in view:
  // a tab's height differs (Trading Activity is often twice Holdings), so keeping the old scroll
  // would land in the middle of the new one, or past its end.
  const tabsRef = useRef<HTMLDivElement>(null)
  // The tabs stay in view while the page scrolls (owner, 2026-11-13): pinned under the app's
  // top bar, and their height handed to what pins beneath them (Trading Activity's chart).
  const tabsTop = useStickTop(tabsRef)
  // After every render: the tabs do not exist while the page shows its skeleton, so a measure
  // made once, on mount, read 0 and the chart pinned over them.
  const [tabsHeight, setTabsHeight] = useState(0)
  // No dependency list on purpose (every render), and no loop: it sets only a changed height.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    const h = tabsRef.current?.offsetHeight ?? 0
    if (h !== tabsHeight) setTabsHeight(h)
  })
  // Each tab keeps its own scroll (owner, 2026-11-14): back to a tab is back where you were in
  // it, and a tab not yet opened starts at its top, just under the pinned name and tabs. Since
  // the tabs stick they never leave the screen, so the old "scrolled past them" reset never ran
  // and every tab opened at the last one's depth.
  const tabScroll = useRef<Partial<Record<Tab, number>>>({})
  const restoreTo = useRef<number | null>(null)
  const switchTab = (next: Tab) => {
    const bar = tabsRef.current
    const scroller = bar ? scrollerOf(bar) : null
    if (bar && scroller && next !== tab) {
      tabScroll.current[tab] = scroller.scrollTop
      // Where the tabs start to stick: the top of any tab's own content.
      const crumb = bar.parentElement?.querySelector<HTMLElement>('.fol-crumb')
      const style = getComputedStyle(bar)
      const natural = crumb
        ? crumb.getBoundingClientRect().bottom -
          scroller.getBoundingClientRect().top +
          scroller.scrollTop +
          parseFloat(getComputedStyle(bar.parentElement!).rowGap || '0') +
          parseFloat(style.marginTop || '0')
        : 0
      const stickAt = Math.max(0, natural - tabsTop)
      restoreTo.current = tabScroll.current[next] ?? Math.min(scroller.scrollTop, stickAt)
    }
    setTab(next)
  }
  useLayoutEffect(() => {
    const bar = tabsRef.current
    if (restoreTo.current == null || !bar) return
    scrollerOf(bar).scrollTop = restoreTo.current
    restoreTo.current = null
  }, [tab])

  if (page.initialLoading)
    return (
      <>
        <PageSkeleton name={portfolio.name} onBack={onBack} />
        {slow && (
          <FetchBanner
            title={COPY.fetchPageTitle(portfolio.kind, portfolio.name)}
            sub={COPY.fetchPageSub(portfolio.kind, seconds)}
          />
        )}
      </>
    )
  if (!page.data)
    return (
      <div className="fol-page">
        {/* A page that failed still offers the way back: the address remembers it, so without
            this a portfolio that cannot load was a dead end. */}
        <Crumb name={portfolio.name || COPY.crumbThis(portfolio.kind)} onBack={onBack} />
        <ErrorState
          title={COPY.loadFailedTitle}
          message={COPY.pageErrorBody}
          onRetry={() => void page.refresh()}
        />
      </div>
    )

  const { follow, follow_id: followId, names = {}, sectors = {} } = page.data
  // The page opens on the copy's line; every holding's own return replaces it when priced.
  const performance = returns.data ?? page.data.performance
  const model = follow.model
  const member = follow.kind === 'house_member'

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await Promise.all([onChanged(), page.refresh(), returns.refresh()])
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
    await Promise.all([onChanged(), page.refresh(), returns.refresh()])
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
    // Named for what it is: you beside them (owner, 2026-11-13).
    { key: 'compare', label: COPY.vsTitle(follow.name) },
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
    <div
      className="fol-page"
      style={{ ['--fol-tabs-h' as string]: `${tabsHeight}px` } as React.CSSProperties}
    >
      <Crumb name={follow.name} onBack={onBack} />
      {error && <div className="fol-note fol-note--bad">{error}</div>}
      <Toast message={notice} onDone={() => setNotice(null)} />
      {/* The name, its actions and the tabs stay in view together while the page scrolls
          (owner, 2026-11-14), at every width. */}
      <div ref={tabsRef} className="fol-tabs-sticky" style={{ top: tabsTop }}>
        <header className="fol-head">
          <div className="fol-id">
            <h2 className="fol-title">
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
                className={
                  followId ? `fol-watch is-on${isMobile ? '' : ' has-label'}` : 'fol-watch'
                }
                aria-label={followId ? COPY.unwatchLabel : COPY.watchLabel}
                aria-pressed={!!followId}
                iconLeft={<Bell size={15} fill={followId ? 'currentColor' : 'none'} />}
                onClick={() =>
                  followId
                    ? setConfirming(true)
                    : void watch().then(
                        (ok) => ok && setNotice(COPY.watchedNotice(follow.kind, follow.name)),
                      )
                }
              >
                {/* Watched says so in words; unwatched stays a quiet bell. */}
                {followId && !isMobile ? COPY.watching : null}
              </Button>
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
            range={range}
            onRange={setRange}
          />
        )}
        {/* Holdings: who they are and their key figures, then what they hold (owner,
            2026-11-13: the Overview's first half moved here). */}
        {shownTab === 'holdings' && model && (
          <div className="fol-sec">
            <AboutPortfolio follow={follow} model={model} names={names} sectors={sectors} />
            <KeyFigures follow={follow} model={model} latestTrade={latestTrade} missed={missed} />
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
          </div>
        )}
        {/* Comparison: them beside your own book, nothing else. */}
        {shownTab === 'compare' && model && (
          <div className="fol-sec">
            <OverviewTab
              perf={performance}
              name={follow.name}
              kind={follow.kind}
              sourceRef={follow.source_ref}
              range={range}
              onRange={setRange}
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
          if (followId)
            void act(() => unfollowPortfolio(followId)).then(
              (ok) => ok && setNotice(COPY.unwatchedNotice(follow.name)),
            )
        }}
        onCancel={() => setConfirming(false)}
      />
    </div>
  )
}
