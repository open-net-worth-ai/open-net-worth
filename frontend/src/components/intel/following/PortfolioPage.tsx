import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  ActionMenu,
  Badge,
  Button,
  ConfirmDialog,
  ErrorState,
  PageTabs,
  SummaryBar,
  TabSkeleton,
} from '../../ui'
import { Bell, ChevronLeft } from '../../../ui/icons'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import {
  followPortfolio,
  refreshFollow,
  setFollowTicker,
  unfollowPortfolio,
  type PerfPeriod,
} from '../../../api/followedPortfolios'
import { COPY, askPrompt } from './following.copy'
import { ageText, shortDate } from './following.format'
import { ActivityTab } from './ActivityTab'
import { activitySections, hasActivity as anyActivity } from './activityRows'
import { HoldingRows } from './HoldingRows'
import { PerformanceBlock } from './PerformanceBlock'
import { usePreviewPage } from './useFollowing'
import { headlineCards } from './headlineCards'
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
  const page = usePreviewPage(portfolio.kind, portfolio.source_ref)
  const [tab, setTab] = useState<Tab>('activity')
  const [period, setPeriod] = useState<PerfPeriod>('12M')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

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
      search: { template: 'copy-followed-portfolio' } as never,
    } as never)
  }
  const menu = [
    ...(onAskLucius
      ? [
          {
            label: COPY.askLucius,
            onSelect: () => {
              sendNextPrefill()
              onAskLucius(askPrompt(follow.name))
            },
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
    ...(hasActivity
      ? [{ key: 'activity' as Tab, label: member ? COPY.tabTrades : COPY.tabActivity }]
      : []),
    { key: 'holdings', label: COPY.tabHoldings },
    { key: 'overview', label: COPY.tabOverview },
  ]
  const shownTab: Tab = tab === 'activity' && !hasActivity ? 'holdings' : tab

  return (
    <div className="fol-page">
      <button type="button" className="fol-back" onClick={onBack}>
        <ChevronLeft size={14} aria-hidden="true" />
        {COPY.backToAll}
      </button>
      <header className="fol-head">
        <div className="fol-id">
          <h2 className="fol-title">
            {follow.name}
            {model?.estimated && <Badge tone="warn">{COPY.estimated}</Badge>}
          </h2>
          <div className="fol-by fol-by--row">
            {follow.subtitle && <span>{follow.subtitle}</span>}
            {model && <span>{COPY.updated(follow.kind, shortDate(model.filed_at))}</span>}
            {model && model.age_days != null && model.age_days > 90 && (
              <span className="fol-stale">{COPY.positionsAge(ageText(model.age_days))}</span>
            )}
          </div>
        </div>
        {/* Icon actions stay level with the name at every width; the call to action sits
            with them on a wide screen and moves under the name on a narrow one. */}
        <div className="fol-icons">
          <Button
            size="sm"
            variant="ghost"
            loading={busy}
            className={followId ? 'fol-watch is-on' : 'fol-watch'}
            aria-label={followId ? COPY.unwatchLabel : COPY.watchLabel}
            aria-pressed={!!followId}
            title={followId ? COPY.unwatchLabel : COPY.watchLabel}
            iconLeft={<Bell size={15} fill={followId ? 'currentColor' : 'none'} />}
            onClick={() => (followId ? setConfirming(true) : void watch())}
          >
            <span className="fol-watch-label">{followId ? COPY.watching : COPY.watch}</span>
          </Button>
          <ActionMenu label={COPY.moreActions(follow.name)} title={follow.name} items={menu} />
        </div>
        <div className="fol-cta">
          <Button variant="primary" size="sm" onClick={() => void copyPortfolio()}>
            {COPY.copyCta}
          </Button>
        </div>
      </header>
      {error && <div className="fol-note fol-note--bad">{error}</div>}
      <PageTabs
        tabs={tabs}
        value={shownTab}
        onChange={setTab}
        ariaLabel={COPY.sectionsLabel(follow.name)}
      />
      {shownTab === 'activity' && (
        <ActivityTab follow={follow} perf={performance} names={names} onAskLucius={onAskLucius} />
      )}
      {shownTab === 'holdings' && model && (
        <HoldingRows
          model={model}
          period="12M"
          win={performance.periods['12M']}
          names={names}
          sectors={sectors}
          watchesToo={!followId}
          onSetTicker={follow.kind === 'fund_13f' ? setTicker : undefined}
        />
      )}
      {shownTab === 'overview' && model && <SummaryBar cards={headlineCards(model, performance)} />}
      {shownTab === 'overview' && (
        <PerformanceBlock
          perf={performance}
          loading={false}
          onRetry={() => void page.refresh()}
          period={period}
          onPeriod={setPeriod}
        />
      )}
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
