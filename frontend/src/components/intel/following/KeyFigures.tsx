import { Tag } from '../../ui'
import { Tooltip } from '../../ui/Tooltip'
import type { Follow, FollowActivity, FollowModel } from '../../../api/followedPortfolios'
import { fCompactAmount } from '../../../utils/format'
import { COPY } from './following.copy'
import { shortDate, toneOf } from './following.format'

const signedAmount = (n: number) =>
  `${n > 0 ? '+' : n < 0 ? '−' : ''}${fCompactAmount(Math.abs(n), 'USD')}`
const signedCount = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n)}`

/** What the portfolio is, at a glance, at the top of Holdings (the header keeps only its name):
 *  its value and number of stocks, each with its change since the previous report, and when the
 *  numbers are from. A caution (an estimate, a report not filed yet) sits under them. */
export function KeyFigures({
  follow,
  model,
  latestTrade,
  missed,
}: {
  follow: Follow
  model: FollowModel
  latestTrade: FollowActivity | null
  missed: string | null
}) {
  const count = model.positions ?? model.holdings.length
  const valueChange =
    model.total_value != null && model.prev_total_value != null
      ? model.total_value - model.prev_total_value
      : null
  const countChange = model.prev_positions != null ? count - model.prev_positions : null
  return (
    <div className="fol-keyfigs">
      <div className="fol-ret3">
        {model.total_value != null && (
          <Figure
            label={COPY.figValue}
            value={fCompactAmount(model.total_value, 'USD')}
            change={valueChange != null ? signedAmount(valueChange) : null}
            tone={toneOf(valueChange)}
          />
        )}
        <Figure
          label={COPY.figStocks}
          value={String(count)}
          change={countChange ? signedCount(countChange) : null}
          tone={toneOf(countChange)}
        />
        {follow.kind === 'fund_13f' && (
          <Tooltip label={COPY.holdingsAsOf(shortDate(model.as_of))}>
            <span tabIndex={0}>
              <Figure label={COPY.figFiled} value={shortDate(model.filed_at)} />
            </span>
          </Tooltip>
        )}
        {follow.kind === 'etf_daily' && (
          <Figure label={COPY.figHoldingsOn} value={shortDate(model.as_of)} />
        )}
        {follow.kind === 'house_member' && latestTrade && (
          <Tooltip label={COPY.disclosedOn(shortDate(latestTrade.filed_at))}>
            <span tabIndex={0}>
              <Figure label={COPY.figLatestTrade} value={shortDate(latestTrade.date)} />
            </span>
          </Tooltip>
        )}
      </div>
      {(model.estimated || missed) && (
        <div className="fol-cautions">
          {model.estimated && (
            <Tooltip label={COPY.estimatedSubtitle}>
              <span tabIndex={0}>
                <Tag tone="warn">{COPY.estimated}</Tag>
              </span>
            </Tooltip>
          )}
          {missed && <Tag tone="warn">{COPY.notDisclosed(missed)}</Tag>}
        </div>
      )}
    </div>
  )
}

function Figure({
  label,
  value,
  change,
  tone,
}: {
  label: string
  value: string
  change?: string | null
  tone?: string
}) {
  return (
    <div className="fol-ret3-item">
      <span className="fol-ret3-l">{label}</span>
      <span className="fol-ret3-v">{value}</span>
      {change && <span className={`fol-keyfig-chg ${tone ?? ''}`}>{change}</span>}
    </div>
  )
}
