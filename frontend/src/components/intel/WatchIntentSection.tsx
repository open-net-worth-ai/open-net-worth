import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { Panel, Text } from '../ui'
import { fetchThesisFor } from '../../api/investmentTheses'
import type { InvestmentThesis } from '../../types/assetDetail'
import { useQueryCache } from '../../state/queryCache'
import { fPrice, getCurrencySymbol } from '../../utils/format'
import { ThesisEditor } from './ThesisEditor'
import { ThesisRoleBadge, ThesisStart, ThesisView } from './ThesisView'
import { thesisHasContent, thesisRole } from './thesisFields'

// "Why you're watching" — the stock's investment thesis, shown and edited with the same ThesisView
// and ThesisEditor as the asset page's Your Thesis card: read first, Edit swaps in the form. Its
// reason, buy zone and fair value are what the Watching pulse turns into rules ("Entered your buy
// zone", "12% below your fair value", "Earnings in 3 days — check your thesis").

export interface WatchIntent {
  thesis?: string
  buy_below?: number
  buy_above?: number
  fair_value?: number
}

interface Props {
  sym: string
  instrumentId?: string
  /** Today's price, for the read view's bar and distances — null when there's no quote. */
  price?: number | null
  /** After a save — the caller reloads the watch items whose intent the thesis feeds. */
  onSaved: () => void
}

export function WatchIntentSection({ sym, instrumentId, price = null, onSaved }: Props) {
  const key = `/api/investment-theses/for?instrument_id=${instrumentId ?? ''}&ticker=${sym}`
  const query = useQueryCache(key, () => fetchThesisFor({ instrumentId, ticker: sym }))
  const [editing, setEditing] = useState(false)
  // The saved thesis shows at once; the refetch's copy replaces it as soon as it lands.
  const [justSaved, setJustSaved] = useState<{
    over: InvestmentThesis | null
    thesis: InvestmentThesis | null
  } | null>(null)
  const ctx = query.data
  const thesis = ctx && justSaved && justSaved.over === ctx.thesis ? justSaved.thesis : ctx?.thesis
  const hasContent = !!ctx && thesisHasContent(thesis ?? null, ctx.held)
  const role = ctx ? thesisRole(thesis ?? null, ctx.held) : undefined
  const ccy = ctx ? getCurrencySymbol(ctx.currency) : ''
  const fmt = (v: number) => `${ccy}${fPrice(v)}`

  const actions =
    hasContent && !editing ? (
      <button type="button" className="asd-rail-manage" onClick={() => setEditing(true)}>
        <Pencil size={12} aria-hidden="true" />
        Edit
      </button>
    ) : undefined

  return (
    <Panel
      title="Why you're watching"
      subtitle={`Turns ${sym}'s price moves into alerts about your thesis, not just the price.`}
      badge={role && !editing ? <ThesisRoleBadge role={role} /> : undefined}
      actions={actions}
    >
      {query.initialLoading ? (
        <Text size="sm" tone="muted">
          Loading…
        </Text>
      ) : !ctx ? (
        <Text size="sm" tone="muted">
          {query.error ? 'Couldn’t load the thesis.' : `Open ${sym}’s page once to start a thesis.`}
        </Text>
      ) : editing ? (
        <ThesisEditor
          instrumentId={ctx.instrument_id}
          thesis={thesis ?? null}
          held={ctx.held}
          currency={ctx.currency}
          onSaved={(saved) => {
            setJustSaved({ over: ctx.thesis, thesis: saved })
            setEditing(false)
            void query.refresh()
            onSaved()
          }}
          onCancel={() => setEditing(false)}
        />
      ) : hasContent && thesis ? (
        <ThesisView thesis={thesis} held={ctx.held} price={price} fmt={fmt} />
      ) : (
        <ThesisStart sym={sym} held={ctx.held} onClick={() => setEditing(true)} />
      )}
    </Panel>
  )
}
