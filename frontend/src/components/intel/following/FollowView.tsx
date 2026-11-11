import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ActionMenu, Badge, Button, ConfirmDialog, EmptyState, StatInfo } from '../../ui'
import { ExternalLink } from '../../../ui/icons'
import { fDateTime, timeAgo } from '../../../utils/format'
import { Spark } from '../../../lucius/identity'
import { sendNextPrefill } from '../../../state/luciusSendIntent'
import {
  refreshFollow,
  unfollowPortfolio,
  type Follow,
  type PerfPeriod,
} from '../../../api/followedPortfolios'
import { COPY, askPrompt } from './following.copy'
import { ageText, holdingsAge, shortDate } from './following.format'
import { HoldingRows } from './HoldingRows'
import { Monogram } from './Monogram'
import { MovesChart } from './MovesChart'
import { PerformanceBlock } from './PerformanceBlock'
import { TradeRows } from './TradeRows'
import { usePerformance } from './useFollowing'

interface Props {
  follow: Follow
  onChanged: () => Promise<unknown>
  onUnfollowed: (id: string) => void
  onAskLucius?: (text: string) => void
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Under the name: who it is, and how old the positions are. Nothing else competes with it. */
function MetaLine({ follow }: { follow: Follow }) {
  const model = follow.model
  if (!model) return <>{follow.subtitle}</>
  const asOf =
    follow.kind === 'house_member'
      ? COPY.lastReport(shortDate(model.filed_at), ageText(model.age_days))
      : COPY.positionsAsOf(shortDate(model.as_of), holdingsAge(model.as_of))
  return (
    <>
      {follow.subtitle} · {asOf}
    </>
  )
}

/** The card's foot, beside the chart legend: where the data comes from and when we last looked. */
function SourceLine({ follow }: { follow: Follow }) {
  return (
    <div className="fol-source">
      {follow.source_url && (
        <a className="fol-link" href={follow.source_url} target="_blank" rel="noopener noreferrer">
          {follow.kind === 'house_member' ? COPY.viewReport : COPY.viewFiling}
          <ExternalLink size={11} aria-hidden="true" />
        </a>
      )}
      {follow.last_checked_at && (
        <span>
          {COPY.checked(timeAgo(follow.last_checked_at))}
          <StatInfo text={COPY.checkedInfo(fDateTime(follow.last_checked_at))} />
        </span>
      )}
    </div>
  )
}

function Notes({ follow, error }: { follow: Follow; error: string | null }) {
  const model = follow.model
  return (
    <>
      {error && <div className="fol-note fol-note--bad">{error}</div>}
      {follow.status === 'stale' && model && (
        <div className="fol-note fol-note--warn">
          {follow.kind === 'house_member'
            ? COPY.staleMember
            : COPY.staleFund(ageText(model.age_days))}
        </div>
      )}
      {follow.status === 'error' && model && (
        <div className="fol-note fol-note--bad">
          {COPY.updateFailed(follow.error ?? '', shortDate(model.as_of))}
        </div>
      )}
    </>
  )
}

/** The header's actions: the app's standard small buttons, the primary one last before the menu. */
function HeroActions({ follow, onAskLucius }: Pick<Props, 'follow' | 'onAskLucius'>) {
  const navigate = useNavigate()
  return (
    <>
      {onAskLucius && (
        <Button
          size="sm"
          iconLeft={<Spark className="fol-spark" size={14} filled ariaHidden />}
          aria-label={COPY.askLabel(follow.name)}
          onClick={() => {
            sendNextPrefill()
            onAskLucius(askPrompt(follow.name))
          }}
        >
          {COPY.askLucius}
        </Button>
      )}
      <Button
        variant="primary"
        size="sm"
        title={COPY.copyPitch}
        onClick={() =>
          void navigate({
            to: '/automations',
            search: { template: 'copy-followed-portfolio' } as never,
          } as never)
        }
      >
        {COPY.copyCta}
      </Button>
    </>
  )
}

export function FollowView({ follow, onChanged, onUnfollowed, onAskLucius }: Props) {
  const [refreshing, setRefreshing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [period, setPeriod] = useState<PerfPeriod>('12M')
  const perf = usePerformance(follow.id)
  const model = follow.model
  const hasModel = !!model && model.holdings.length > 0

  async function run(action: () => Promise<unknown>, failed: (why: string) => string) {
    setActionError(null)
    try {
      await action()
      await onChanged()
      return true
    } catch (e) {
      setActionError(failed(errorText(e)))
      return false
    }
  }
  async function refresh() {
    setRefreshing(true)
    await run(() => refreshFollow(follow.id), COPY.refreshFailed)
    setRefreshing(false)
  }
  async function unfollow() {
    setConfirming(false)
    if (await run(() => unfollowPortfolio(follow.id), COPY.saveFailed)) onUnfollowed(follow.id)
  }

  return (
    <>
      <section className="fol-hero" aria-label={follow.name}>
        <div className="fol-hero-top">
          <Monogram name={follow.name} />
          <div className="fol-id">
            <h2 className="fol-name">
              {follow.name}
              {model?.estimated && <Badge tone="warn">{COPY.estimated}</Badge>}
              {follow.status === 'stale' && <Badge tone="warn">{COPY.outOfDate}</Badge>}
              {follow.status === 'error' && <Badge tone="neg">{COPY.couldNotUpdate}</Badge>}
            </h2>
            <div className="fol-by">
              {refreshing ? COPY.refreshing : <MetaLine follow={follow} />}
            </div>
          </div>
          {hasModel && (
            <div className="fol-actions">
              <HeroActions follow={follow} onAskLucius={onAskLucius} />
            </div>
          )}
          <ActionMenu
            className="fol-menu"
            label={COPY.moreActions(follow.name)}
            title={follow.name}
            items={[
              { label: COPY.refresh, onSelect: () => void refresh(), disabled: refreshing },
              { label: COPY.unfollow, onSelect: () => setConfirming(true), tone: 'danger' },
            ]}
          />
        </div>
        {hasModel && (
          <PerformanceBlock
            follow={follow}
            period={period}
            onPeriod={setPeriod}
            foot={<SourceLine follow={follow} />}
          />
        )}
      </section>
      <Notes follow={follow} error={actionError} />
      {!hasModel &&
        (follow.status === 'error' ? (
          <EmptyState
            title={COPY.loadFailedTitle}
            body={follow.error}
            action={
              <Button size="sm" loading={refreshing} onClick={() => void refresh()}>
                {COPY.refresh}
              </Button>
            }
          />
        ) : (
          <EmptyState title={COPY.nothingYetTitle} body={COPY.nothingYetBody} />
        ))}
      {hasModel && (
        <div className="fol-cols">
          {follow.kind === 'fund_13f' ? (
            <MovesChart model={model} who={follow.name} onAskLucius={onAskLucius} />
          ) : (
            <TradeRows
              activity={follow.activity}
              unread={model.unread_reports}
              returns={perf.data?.trade_returns ?? {}}
              who={follow.name}
              onAskLucius={onAskLucius}
            />
          )}
          <HoldingRows model={model} period={period} win={perf.data?.periods[period]} />
        </div>
      )}
      {hasModel && (
        <p className="fol-foot">
          {model.estimated && `${COPY.estimatedSubtitle} `}
          {COPY.copyPitch} {COPY.notAdvice}
        </p>
      )}
      <ConfirmDialog
        data={
          confirming
            ? {
                title: COPY.unfollowTitle(follow.name),
                body: COPY.unfollowBody,
                confirmLabel: COPY.unfollow,
                danger: true,
              }
            : null
        }
        onConfirm={() => void unfollow()}
        onCancel={() => setConfirming(false)}
      />
    </>
  )
}
