import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  ActionMenu,
  Badge,
  Button,
  Tag,
  ConfirmDialog,
  ErrorState,
  PageTabs,
  TabSkeleton,
} from '../../ui'
import { Bell, ChevronLeft } from '../../../ui/icons'
import { Spark } from '../../../lucius/identity'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import {
  followPortfolio,
  refreshFollow,
  setFollowTicker,
  unfollowPortfolio,
} from '../../../api/followedPortfolios'
import { COPY, askPrompt } from './following.copy'
import { shortDate } from './following.format'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePortfolioTickerMaps } from '../usePortfolioTickerMaps'
import { ActivityTab } from './ActivityTab'
import { activitySections, hasActivity as anyActivity, missedQuarter } from './activityRows'
import { HoldingRows } from './HoldingRows'
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
  const [tab, setTab] = useState<Tab>('activity')
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
          {/* Who it is, then the two facts every tab rests on, each its own chip so they never
              read as one sentence: the snapshot Holdings and Overview describe, and the newest
              dated trade (Trading Activity runs past the snapshot). */}
          <div className="fol-facts">
            {follow.subtitle && <span className="fol-by">{follow.subtitle}</span>}
            {/* A fund's snapshot is named by the report it comes from; an ETF's is its day. */}
            {model && follow.kind === 'fund_13f' && (
              <span title={COPY.holdingsAsOf(shortDate(model.as_of))}>
                <Tag tone="muted">{COPY.quarterlyFiled(shortDate(model.filed_at))}</Tag>
              </span>
            )}
            {model && follow.kind === 'etf_daily' && (
              <Tag tone="muted">{COPY.holdingsOn(shortDate(model.as_of))}</Tag>
            )}
            {latestTrade && (
              <span title={COPY.disclosedOn(shortDate(latestTrade.filed_at))}>
                <Tag tone="muted">{COPY.latestTrade(shortDate(latestTrade.date))}</Tag>
              </span>
            )}
            {missed && <Badge tone="warn">{COPY.notDisclosed(missed)}</Badge>}
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
          />
          <ActionMenu label={COPY.moreActions(follow.name)} title={follow.name} items={menu} />
        </div>
        {/* Lucius does the copying (an automation that trades your broker account to match), so
            it is the app's Lucius action: the primary button in Lucius's colour with its Spark. */}
        <div className="fol-cta">
          <Button
            size="sm"
            variant="primary"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            title={COPY.copyPitch}
            onClick={() => void copyPortfolio()}
          >
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
        <ActivityTab
          follow={follow}
          perf={performance}
          names={names}
          held={heldSet}
          onAskLucius={onAskLucius}
        />
      )}
      {shownTab === 'holdings' && model && (
        <HoldingRows
          model={model}
          period="12M"
          win={performance.periods['12M']}
          names={names}
          sectors={sectors}
          watchesToo={!followId}
          held={heldSet}
          onSetTicker={follow.kind === 'fund_13f' ? setTicker : undefined}
        />
      )}
      {shownTab === 'overview' && model && (
        <OverviewTab model={model} perf={performance} onRetry={() => void page.refresh()} />
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
