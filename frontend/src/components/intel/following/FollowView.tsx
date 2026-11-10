import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  ActionMenu,
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  SummaryBar,
  type StatCard,
} from '../../ui'
import { Sparkles } from '../../../ui/icons'
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

function statCards(follow: Follow): StatCard[] {
  const model = follow.model
  if (!model) return []
  const top = model.holdings[0]
  const member = follow.kind === 'house_member'
  const pct = (n: number) => fPct(n, { fraction: true })
  const topFive = model.holdings.slice(0, 5).reduce((sum, h) => sum + h.weight, 0)
  const cards: StatCard[] = []
  if (top)
    cards.push({
      label: COPY.statTop,
      value: top.symbol,
      caption: `${pct(top.weight)} of the portfolio`,
    })
  if (model.holdings.length > 5)
    cards.push({
      label: COPY.statTopFive,
      value: pct(topFive),
      caption: `of the ${model.holdings.length} holdings shown`,
    })
  cards.push({
    label: member ? COPY.statChangesMember : COPY.statChanges,
    value: changeSummary(model) ?? COPY.noChanges,
    caption: member
      ? `${follow.activity.length} trades listed`
      : model.prev_as_of
        ? `${shortDate(model.prev_as_of)} to ${shortDate(model.as_of)}`
        : undefined,
  })
  return cards
}

/** One line under the name: who, and how old the data is. Never a claim that it is current. */
function metaLine(follow: Follow): string {
  const model = follow.model
  if (!model) return follow.subtitle
  const filed = `${follow.kind === 'house_member' ? 'Last report' : 'Filed'} ${shortDate(model.filed_at)}, ${ageText(model.age_days)}`
  const asOf = follow.kind === 'fund_13f' ? ` · Holdings as of ${shortDate(model.as_of)}` : ''
  return `${follow.subtitle} · ${filed}${asOf}`
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
    <Button size="sm" loading={refreshing} onClick={() => void refresh()}>
      {COPY.refresh}
    </Button>
  )
  const cards = statCards(follow)

  return (
    <>
      <section className="fol-hero" aria-label={follow.name}>
        <div className="fol-hero-top">
          <div className="fol-id">
            <h2 className="fol-name">
              {follow.name}
              {model?.estimated && <Badge tone="warn">{COPY.estimated}</Badge>}
              {follow.status === 'stale' && <Badge tone="warn">{COPY.outOfDate}</Badge>}
              {follow.status === 'error' && <Badge tone="neg">{COPY.couldNotUpdate}</Badge>}
            </h2>
            <div className="fol-by">{refreshing ? COPY.refreshing : metaLine(follow)}</div>
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
            <ActionMenu
              label={COPY.moreActions(follow.name)}
              title={follow.name}
              items={[
                { label: COPY.refresh, onSelect: () => void refresh(), disabled: refreshing },
                { label: COPY.unfollow, onSelect: () => setConfirming(true), tone: 'danger' },
              ]}
            />
          </div>
        </div>
        {cards.length > 0 && <SummaryBar cards={cards} />}
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
