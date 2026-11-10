import type { Follow } from '../../../api/followedPortfolios'
import { signedPct, toneOf } from './following.format'
import { Monogram } from './Monogram'
import { usePerformance } from './useFollowing'

interface Props {
  follow: Follow
  selected: boolean
  onSelect: () => void
}

/** One followed portfolio in the switcher: its mark, its name, and the past 12 months at a glance. */
export function FollowChip({ follow, selected, onSelect }: Props) {
  const perf = usePerformance(follow.id)
  const year = perf.data?.periods['12M']
  const value = year?.status === 'ok' ? year.return : undefined
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      className={`fol-pick${selected ? ' is-on' : ''}`}
      onClick={onSelect}
    >
      <Monogram name={follow.name} size="sm" />
      <span className="fol-pick-name">{follow.name}</span>
      {value !== undefined && (
        <span className={`fol-pick-ret ${toneOf(value)}`}>{signedPct(value)}</span>
      )}
    </button>
  )
}
