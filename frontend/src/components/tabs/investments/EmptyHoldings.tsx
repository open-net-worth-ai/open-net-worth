// EmptyHoldings — empty state when no positions exist.

import { Button, Cluster, EmptyState } from '../../ui'
import { TrendingUp } from '../../../ui/icons'
import { LuciusCta } from '../../intro/LuciusCta'
import type { UntrackedExcluded } from '../../../types/aggregates'
import { fBase } from '../../../utils/format'
import { Money } from '../../ui/Money'
import { untrackedWhere } from './untracked'

interface Props {
  onAddHolding?: () => void
  onAskLucius?: (text: string) => void
  /** A Brokerage total account's total with no holdings entered: invested, not "nothing". */
  untracked?: UntrackedExcluded | null
}

const HOLDINGS_MESSAGE =
  'Help me add my investments. Ask which brokers I use; I can send a screenshot or statement ' +
  'of my holdings instead of typing. Ask me one thing at a time.'

export function EmptyHoldings({ onAddHolding, onAskLucius, untracked }: Props) {
  return (
    <EmptyState
      fill
      icon={<TrendingUp size={28} strokeWidth={1.5} aria-hidden="true" />}
      title={untracked ? 'No holdings entered yet' : 'No holdings yet'}
      body={
        untracked ? (
          <>
            <Money>{fBase(untracked.total)}</Money> at {untrackedWhere(untracked)} is counted as
            invested, but its holdings aren&apos;t entered. Add them to see your allocation, drift
            and risk.
          </>
        ) : (
          'Connect a brokerage or add a position to start tracking your investments. Lucius will build your allocation, drift, and risk views automatically.'
        )
      }
      action={
        <Cluster gap="--space-3" justify="center">
          <Button variant="primary" size="sm" onClick={onAddHolding} data-tour="setup-investments">
            Add holding
          </Button>
          <LuciusCta label="Add with Lucius" message={HOLDINGS_MESSAGE} onAskLucius={onAskLucius} />
        </Cluster>
      }
    />
  )
}
