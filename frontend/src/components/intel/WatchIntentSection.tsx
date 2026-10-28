import { useState } from 'react'
import { Panel, Text } from '../ui'
import { fetchThesisFor } from '../../api/investmentTheses'
import { useQueryCache } from '../../state/queryCache'
import { ThesisEditor } from './ThesisEditor'

// "Why you're watching" — the stock's investment thesis, edited here with the same ThesisEditor as
// the asset page's Your Thesis card, so both show every field. Its reason, buy zone and fair value
// are what the Watching pulse turns into rules ("Entered your buy zone", "12% below your fair
// value", "Earnings in 3 days — check your thesis").

export interface WatchIntent {
  thesis?: string
  buy_below?: number
  buy_above?: number
  fair_value?: number
}

interface Props {
  sym: string
  instrumentId?: string
  /** After a save — the caller reloads the watch items whose intent the thesis feeds. */
  onSaved: () => void
}

export function WatchIntentSection({ sym, instrumentId, onSaved }: Props) {
  const key = `/api/investment-theses/for?instrument_id=${instrumentId ?? ''}&ticker=${sym}`
  const query = useQueryCache(key, () => fetchThesisFor({ instrumentId, ticker: sym }))
  // Cancel and Save start the form over from the stored thesis.
  const [round, setRound] = useState(0)
  const [saved, setSaved] = useState(false)
  const ctx = query.data

  return (
    <Panel
      title="Why you're watching"
      subtitle={`Turns ${sym}'s price moves into alerts about your thesis, not just the price.`}
      badge={saved ? <span className="watch-intent-saved">Saved</span> : undefined}
    >
      {query.initialLoading ? (
        <Text size="sm" tone="muted">
          Loading…
        </Text>
      ) : ctx ? (
        <ThesisEditor
          key={`${round}:${ctx.thesis?.updated_at ?? ''}`}
          instrumentId={ctx.instrument_id}
          thesis={ctx.thesis}
          held={ctx.held}
          currency={ctx.currency}
          onSaved={() => {
            setSaved(true)
            setRound((r) => r + 1)
            void query.refresh()
            onSaved()
          }}
          onCancel={() => {
            setSaved(false)
            setRound((r) => r + 1)
          }}
        />
      ) : (
        <Text size="sm" tone="muted">
          {query.error ? 'Couldn’t load the thesis.' : `Open ${sym}’s page once to start a thesis.`}
        </Text>
      )}
    </Panel>
  )
}
