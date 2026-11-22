// Currencies as a panel: the server lists the pairs (held currencies against the base, then the
// watched ones), takes each live cross rate and turns it round for a flipped pair. The view only
// draws the rows and writes the flip setting.

import { ArrowLeftRight } from 'lucide-react'
import { fBase, fPrice } from '../../../../utils/format'
import { MaskedText, Text } from '../../../ui'
import { WidgetShell } from '../WidgetShell'
import { WidgetPlaceholder } from '../WidgetPlaceholder'
import type { WidgetInstance } from '../types'
import type { PanelDefinition, PanelViewProps } from './panel'
import type { FxPanel } from './types'

function stringList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((k): k is string => typeof k === 'string') : []
}

/** The "Watch pairs" setting stores `[base, quote]` arrays; the query takes "B/Q". */
function watchPairs(instance: WidgetInstance): string[] {
  const raw = Array.isArray(instance.config?.pairs) ? instance.config.pairs : []
  return raw
    .filter(
      (p): p is [string, string] =>
        Array.isArray(p) && p.length === 2 && typeof p[0] === 'string' && typeof p[1] === 'string',
    )
    .map(([b, q]) => `${b}/${q}`)
}

function FxView({ instance, def, onPatch, data, loading }: PanelViewProps<FxPanel>) {
  const flipped = stringList(instance.config?.flipped)
  const toggleFlip = (key: string) => {
    const next = flipped.includes(key) ? flipped.filter((k) => k !== key) : [...flipped, key]
    onPatch({ config: { ...instance.config, flipped: next } })
  }
  if (data && !data.rows.length) {
    return (
      <WidgetShell title={instance.title} base={def.title}>
        <WidgetPlaceholder
          detail={<>All your holdings are in {data.base_currency} — nothing to convert.</>}
        />
      </WidgetShell>
    )
  }
  return (
    <WidgetShell title={instance.title} base={def.title}>
      <div className="ov-fx-list">
        {(data?.rows ?? []).map((r) => (
          <div className="ov-fx-row" key={r.key}>
            <button
              type="button"
              className="ov-fx-pair"
              onClick={() => toggleFlip(r.key)}
              title={`Show ${r.from} per ${r.to}`}
              aria-label={`${r.from} / ${r.to}. Show ${r.from} per ${r.to} instead`}
            >
              {r.from} / {r.to}
              <ArrowLeftRight size={12} aria-hidden="true" className="ov-fx-flip" />
            </button>
            <span className="ov-fx-note">
              {r.held && r.held_amount_base != null ? (
                <MaskedText>{`${fBase(r.held_amount_base)} ${r.held_state ?? 'held'}`}</MaskedText>
              ) : (
                'Watching'
              )}
            </span>
            <span className="ov-fx-rate money">
              {r.rate != null ? `${fPrice(r.rate, 4)} ${r.to}` : loading ? '…' : '—'}
            </span>
          </div>
        ))}
      </div>
      {data?.error && (
        <Text as="p" size="xs" tone="bad">
          Couldn't fetch live rates.
        </Text>
      )}
      <Text as="p" size="xs" tone="faint">
        Live mid rates. Day-over-day change isn't tracked for currencies yet.
      </Text>
    </WidgetShell>
  )
}

export const fxPanel: PanelDefinition<FxPanel> = {
  query: (instance) => ({
    source: 'panel_fx',
    params: { pairs: watchPairs(instance), flipped: stringList(instance.config?.flipped) },
  }),
  signals: ['portfolio', 'ledger', 'accounts', 'assets'],
  View: FxView,
}
