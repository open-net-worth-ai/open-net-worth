import type { Follow } from '../../../api/followedPortfolios'
import { signedPct, toneOf } from './following.format'
import { usePerformance } from './useFollowing'

interface Props {
  follow: Follow
  selected: boolean
  onSelect: () => void
}

/** One followed portfolio in the switcher: the app's filter chip (`.mk-chip`), with the past
 *  12 months where a list chip shows its count. */
export function FollowChip({ follow, selected, onSelect }: Props) {
  const perf = usePerformance(follow.id)
  const year = perf.data?.periods['12M']
  const value = year?.status === 'ok' ? year.return : undefined
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      className={`mk-chip${selected ? ' is-on' : ''}`}
      onClick={onSelect}
    >
      {follow.name}
      {value !== undefined && (
        <span className={`mk-chip-n ${toneOf(value)}`}>{signedPct(value)}</span>
      )}
    </button>
  )
}
