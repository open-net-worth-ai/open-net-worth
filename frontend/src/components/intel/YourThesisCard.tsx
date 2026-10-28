import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { Panel, Text } from '../ui'
import type { InvestmentThesis } from '../../types/assetDetail'
import { fPrice, getCurrencySymbol } from '../../utils/format'
import { ThesisEditor } from './ThesisEditor'
import { ThesisRoleBadge, ThesisView } from './ThesisView'
import { thesisHasContent, thesisRole } from './thesisFields'

// "Your Thesis" — why you own (or would buy) this instrument and the prices that matter to you.
// One thesis per instrument (services/investment_theses); held vs watched only changes what is shown:
// a holding adds its role, breaks-if, stop and trim. The read view is ThesisView and editing is
// ThesisEditor — the same pair the alerts screen shows.

interface Props {
  instrumentId: string
  ticker: string
  thesis: InvestmentThesis | null
  held: boolean
  price: number | null
  /** Max fraction digits for prices (undefined = the formatter's own, for crypto). */
  priceMaxFrac?: number
  /** The instrument's quote currency — what the page price and every level are in. */
  currency?: string
  onSaved: () => void
}

export function YourThesisCard({
  instrumentId,
  ticker,
  thesis,
  held,
  price,
  priceMaxFrac,
  currency,
  onSaved,
}: Props) {
  // The saved thesis shows at once; the refetch's copy replaces it as soon as `thesis` changes.
  const [justSaved, setJustSaved] = useState<{
    over: InvestmentThesis | null
    thesis: InvestmentThesis | null
  } | null>(null)
  const current = justSaved && justSaved.over === thesis ? justSaved.thesis : thesis
  const [editing, setEditing] = useState(false)

  const sym = currency ? getCurrencySymbol(currency) : ''
  const fmt = (v: number) => `${sym}${fPrice(v, priceMaxFrac)}`
  const hasContent = thesisHasContent(current, held)
  const role = thesisRole(current, held)

  const actions = editing ? undefined : (
    <button type="button" className="asd-rail-manage" onClick={() => setEditing(true)}>
      <Pencil size={12} aria-hidden="true" />
      {hasContent ? 'Edit' : 'Add'}
    </button>
  )

  return (
    <Panel
      title="Your Thesis"
      badge={role && !editing ? <ThesisRoleBadge role={role} /> : undefined}
      actions={actions}
      className="asd-rail-panel asd-thesis-card"
    >
      {editing ? (
        <ThesisEditor
          instrumentId={instrumentId}
          thesis={current}
          held={held}
          currency={currency}
          onSaved={(saved) => {
            setJustSaved({ over: thesis, thesis: saved })
            setEditing(false)
            onSaved()
          }}
          onCancel={() => setEditing(false)}
        />
      ) : hasContent && current ? (
        <ThesisView thesis={current} held={held} price={price} fmt={fmt} />
      ) : (
        <Text size="sm" tone="muted">
          {held ? `Write down why you own ${ticker}.` : `Write down why you’d buy ${ticker}.`}
        </Text>
      )}
    </Panel>
  )
}
