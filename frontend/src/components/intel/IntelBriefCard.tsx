import type { ReactNode } from 'react'
import { useHoldingsNarration, useIntelDigest } from '../../hooks/useIntelDigest'
import { MaskedText, Skeleton } from '../ui'
import { Sparkles } from '../../ui/icons'
import { IntelCardHead } from './IntelCardHead'
import { OpinionLine } from './OpinionLine'
import { holdingsBrief } from './holdingsBrief'
import type { BriefLevel } from './watchBrief'
import type { IntelDigest } from '../../types/intelDigest'

interface Props {
  onChatSuggest: (text: string) => void
  /** What the card hosts under the brief: the Holdings tab's per-holding desk. */
  children: ReactNode
  /** How much the brief says (Settings › Intelligence); 5 = Lucius's narrative, shown as the
   *  level-4 template while it loads or if it can't be written. */
  verbosity?: BriefLevel
}

function hasPortfolioData(digest: IntelDigest): boolean {
  return (
    !!digest.today?.has_data ||
    (digest.data_quality?.rows_count ?? 0) > 0 ||
    (digest.top_movers?.length ?? 0) > 0
  )
}

/** The Holdings tab's card: the brief's read of the day on top, then `children`. The brief's own
 *  loading and error states are confined to the narrative line — the holdings below never wait
 *  on (or vanish with) the digest. */
export function IntelBriefCard({ onChatSuggest, children, verbosity = 3 }: Props) {
  const { data, loading, error, refetch } = useIntelDigest()
  // A book with nothing in it yet is not "steady" — say so plainly, and don't ask Lucius to
  // narrate an empty digest.
  const empty = !!data && !hasPortfolioData(data)
  const narrated = useHoldingsNarration(verbosity === 5 && !empty)
  const narration = verbosity === 5 && !empty ? narrated.data?.narration?.trim() : null
  const narrative = empty
    ? 'No portfolio activity yet. Add an investment or follow a topic to start a brief.'
    : narration || (data ? holdingsBrief(data, verbosity === 5 ? 4 : verbosity) : null)

  return (
    <section className="intel-brief-card">
      <IntelCardHead
        icon={<Sparkles size={13} />}
        title="Holdings brief"
        ask={{
          label: 'Ask Lucius about this brief',
          onAsk: () =>
            onChatSuggest(
              'Walk me through my portfolio brief today — what moved, how I sit versus the market, and what needs a decision.',
            ),
        }}
        updatedAt={data?.generated_at}
      />

      <div className="intel-brief-body">
        {narrative ? (
          <>
            <p className="intel-brief-narrative">
              <MaskedText>{narrative}</MaskedText>
            </p>
            {narration && <OpinionLine opinion={narrated.data?.opinion} />}
          </>
        ) : loading ? (
          <Skeleton height={18} />
        ) : error ? (
          <p className="intel-brief-narrative intel-brief-narrative--muted">
            Brief unavailable right now.{' '}
            <button type="button" className="intel-brief-retry" onClick={refetch}>
              Retry
            </button>
          </p>
        ) : null}
      </div>

      {children}
    </section>
  )
}
