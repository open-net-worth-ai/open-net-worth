import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Badge, Button, ConfirmDialog, EmptyState } from '../../ui'
import { RefreshCw, Sparkles } from '../../../ui/icons'
import { fPct } from '../../../utils/format'
import { refreshFollow, unfollowPortfolio, type Follow } from '../../../api/followedPortfolios'
import { COPY, askPrompt } from './following.copy'
import { ageText, changeSummary, shortDate } from './following.format'
import { HoldingRows } from './HoldingRows'
import { TradeRows } from './TradeRows'

interface Props {
  follow: Follow
  onChanged: () => Promise<unknown>
  onUnfollowed: (id: string) => void
  onAskLucius?: (text: string) => void
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="fol-stat">
      <dt>{label}</dt>
      <dd>
        {value}
        {sub && <small>{sub}</small>}
      </dd>
    </div>
  )
}

function Stats({ follow }: { follow: Follow }) {
  const model = follow.model
  if (!model) return null
  const top = model.holdings[0]
  const member = follow.kind === 'house_member'
  return (
    <dl className="fol-stats">
      <Stat
        label={member ? COPY.statLastReport : COPY.statFiled}
        value={shortDate(model.filed_at)}
        sub={ageText(model.age_days)}
      />
      <Stat
        label={COPY.statHoldings}
        value={String(model.holdings.length)}
        sub={member ? COPY.statEstimated : `as of ${shortDate(model.as_of)}`}
      />
      {top && (
        <Stat
          label={COPY.statTop}
          value={top.symbol}
          sub={`${fPct(top.weight, { fraction: true })} of the portfolio`}
        />
      )}
      <Stat
        label={member ? COPY.statChangesMember : COPY.statChanges}
        value={changeSummary(model) ?? COPY.noChanges}
        sub={
          member
            ? `${follow.activity.length} trades listed`
            : model.prev_as_of
              ? `${shortDate(model.prev_as_of)} to ${shortDate(model.as_of)}`
              : undefined
        }
      />
    </dl>
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

export function FollowView({ follow, onChanged, onUnfollowed, onAskLucius }: Props) {
  const navigate = useNavigate()
  const [refreshing, setRefreshing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const model = follow.model

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
  const refreshButton = (
    <Button
      variant="ghost"
      size="sm"
      loading={refreshing}
      iconLeft={<RefreshCw size={14} />}
      onClick={() => void refresh()}
    >
      {COPY.refresh}
    </Button>
  )

  return (
    <>
      <section className="fol-hero" aria-label={follow.name}>
        <div className="fol-hero-top">
          <div>
            <h2 className="fol-name">
              {follow.name}
              {model?.estimated && <Badge tone="warn">{COPY.estimated}</Badge>}
              {follow.status === 'stale' && <Badge tone="warn">{COPY.outOfDate}</Badge>}
              {follow.status === 'error' && <Badge tone="neg">{COPY.couldNotUpdate}</Badge>}
            </h2>
            <div className="fol-by">{follow.subtitle}</div>
          </div>
          <div className="fol-actions">
            <Button
              size="sm"
              iconLeft={<Sparkles size={14} />}
              onClick={() => onAskLucius?.(askPrompt(follow.name))}
            >
              {COPY.askLucius}
            </Button>
            <Button
              size="sm"
              onClick={() =>
                void navigate({
                  to: '/automations',
                  search: { template: 'copy-followed-portfolio' } as never,
                } as never)
              }
            >
              {COPY.setUpAutomation}
            </Button>
            {refreshButton}
            <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
              {COPY.unfollow}
            </Button>
          </div>
        </div>
        <Stats follow={follow} />
      </section>
      <Notes follow={follow} error={actionError} />
      {model && model.holdings.length > 0 ? (
        <HoldingRows model={model} />
      ) : follow.status === 'error' ? (
        <EmptyState title={COPY.loadFailedTitle} body={follow.error} action={refreshButton} />
      ) : (
        <EmptyState title={COPY.nothingYetTitle} body={COPY.nothingYetBody} />
      )}
      {follow.kind === 'house_member' && (
        <TradeRows activity={follow.activity} unread={model?.unread_reports ?? 0} />
      )}
      <p className="fol-foot">
        {model?.estimated && `${COPY.estimatedSubtitle} `}
        {COPY.copyBody} {COPY.notAdvice}
      </p>
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
