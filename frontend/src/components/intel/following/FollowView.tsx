import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ActionMenu, Badge, Button, ConfirmDialog, EmptyState } from '../../ui'
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
import { ageText, shortDate } from './following.format'
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

/** Under the name: who it is, how old the filing is, where to read it, when we last looked.
 *  Never a claim that the data is current. */
function MetaLine({ follow }: { follow: Follow }) {
  const model = follow.model
  if (!model) return <>{follow.subtitle}</>
  const member = follow.kind === 'house_member'
  const filed = `${member ? 'Last report' : 'Filed'} ${shortDate(model.filed_at)}, ${ageText(model.age_days)}`
  return (
    <>
      {follow.subtitle} · {filed}
      {!member && ` · Holdings as of ${shortDate(model.as_of)}`}
      {follow.source_url && (
        <>
          {' · '}
          <a
            className="fol-link"
            href={follow.source_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {member ? COPY.viewReport : COPY.viewFiling}
            <ExternalLink size={11} aria-hidden="true" />
          </a>
        </>
      )}
      {follow.last_checked_at && (
        <span title={COPY.checkedTitle(fDateTime(follow.last_checked_at))}>
          {' · '}
          {COPY.checked(timeAgo(follow.last_checked_at))}
        </span>
      )}
    </>
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

/** The reason this screen exists: copying the portfolio. One primary action, said plainly. */
function CopyPanel({ follow, onAskLucius }: Pick<Props, 'follow' | 'onAskLucius'>) {
  const navigate = useNavigate()
  return (
    <aside className="fol-copy" aria-label={COPY.copyTitle}>
      <h3 className="fol-copy-title">{COPY.copyTitle}</h3>
      <p className="fol-copy-pitch">{COPY.copyPitch}</p>
      <div className="fol-copy-actions">
        <Button
          variant="primary"
          size="sm"
          onClick={() =>
            void navigate({
              to: '/automations',
              search: { template: 'copy-followed-portfolio' } as never,
            } as never)
          }
        >
          {COPY.copyCta}
        </Button>
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
      </div>
      <p className="fol-foot">{COPY.notAdvice}</p>
    </aside>
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
          <ActionMenu
            label={COPY.moreActions(follow.name)}
            title={follow.name}
            items={[
              { label: COPY.refresh, onSelect: () => void refresh(), disabled: refreshing },
              { label: COPY.unfollow, onSelect: () => setConfirming(true), tone: 'danger' },
            ]}
          />
        </div>
        {hasModel && (
          <div className="fol-hero-body">
            <PerformanceBlock follow={follow} period={period} onPeriod={setPeriod} />
            <CopyPanel follow={follow} onAskLucius={onAskLucius} />
          </div>
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
      {model?.estimated && <p className="fol-foot">{COPY.estimatedSubtitle}</p>}
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
