import { useState } from 'react'
import { Button, Panel, Stack, Text } from '../ui'
import { ArrowRight, Plus } from '../../ui/icons'
import { fPrice } from '../../utils/format'
import { PriceAlertsModal } from './PriceAlertsModal'
import type { PriceAlertCondition } from '../../types/assetDetail'

const OP_SYM: Record<string, string> = { '>=': '≥', '<=': '≤', '>': '>', '<': '<' }

function chipText(a: PriceAlertCondition, currencySymbol: string): string {
  const up = a.op === '>=' || a.op === '>'
  if (a.kind === 'move') return `${up ? '↑' : '↓'} ${fPrice(a.value)}%`
  return `${OP_SYM[a.op] ?? a.op} ${currencySymbol}${fPrice(a.value)}`
}

interface EditTarget {
  id: string
  value: string
  label: string
  asset_class: string
  instrument_id?: string
  price_alerts: PriceAlertCondition[]
  monitor_news: boolean
  custom_feed_urls?: string[]
}

interface Props {
  ticker: string
  assetName: string
  /** The ticker as the page shows it (a coin's symbol, not its price-feed id) — for copy. */
  displaySymbol?: string
  assetClass: string
  /** The asset's instrument id — the modal's 52-week track reads the quote stats by it. */
  instrumentId?: string
  /** Watchlist item id when following — used by the mute toggle only; null when not on any watchlist. */
  itemId: string | null
  alerts: PriceAlertCondition[]
  /** Quote-currency CODE (e.g. EUR) — the modal renders the symbol from it. */
  currency: string
  currencySymbol: string
  livePrice?: number
  /** Today's change in percent, shown beside the live price in the modal header. */
  changePct?: number | null
  /** Current news/alert mute state, so the modal's mute toggle reflects reality. */
  monitorNews?: boolean
  /** This ticker's saved custom news feed URLs — Advanced: news sources. */
  customFeedUrls?: string[]
  /** GET-only refetch of the asset detail after alerts change. */
  onChanged: () => void
}

// Price-alert management for the asset-detail right rail. Lists the asset's current thresholds and
// opens the shared PriceAlertsModal (same rich editor as the Intel watchlist) to add/edit/remove
// them. Alerts are stored independently of watchlist membership — opening the editor is always safe
// regardless of following state.
export function AssetAlertsCard({
  ticker,
  assetName,
  displaySymbol,
  assetClass,
  instrumentId,
  itemId,
  alerts,
  currency,
  currencySymbol,
  livePrice,
  changePct,
  monitorNews = true,
  customFeedUrls,
  onChanged,
}: Props) {
  const [editing, setEditing] = useState<EditTarget | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function openEditor() {
    setError(null)
    setEditing({
      id: itemId ?? '',
      value: ticker,
      label: assetName,
      asset_class: assetClass,
      instrument_id: instrumentId,
      price_alerts: alerts,
      monitor_news: monitorNews,
      custom_feed_urls: customFeedUrls,
    })
  }

  const hasAlerts = alerts.length > 0

  // When alerts exist, "Manage" moves to a compact top-right header action (matching the
  // watchlist card's "Manage all →"). The empty state keeps a primary CTA in the body.
  const manageAction = hasAlerts ? (
    <button type="button" className="asd-rail-manage" onClick={() => void openEditor()}>
      Manage <ArrowRight size={12} />
    </button>
  ) : undefined

  return (
    <Panel title="Price Alerts" actions={manageAction} className="asd-rail-panel">
      <Stack gap="--space-3">
        {hasAlerts ? (
          <div className="asd-alerts-list">
            {alerts.map((a, i) => (
              <div key={i} className="asd-alert-row">
                <span className="intel-wlc-alert-chip">{chipText(a, currencySymbol)}</span>
                {(a.name || a.note) && (
                  <span className="asd-alert-note" title={a.note || a.name}>
                    {a.name || a.note}
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <>
            <Text size="sm" tone="faint">
              Get notified when {displaySymbol || ticker} crosses a price you set.
            </Text>
            <Button
              variant="primary"
              size="sm"
              iconLeft={<Plus size={14} aria-hidden="true" />}
              onClick={() => void openEditor()}
              className="asd-alerts-btn"
            >
              Add price alert
            </Button>
          </>
        )}

        {error && (
          <Text size="xs" tone="bad">
            {error}
          </Text>
        )}
      </Stack>

      {editing && (
        <PriceAlertsModal
          item={editing}
          ccy={currency}
          livePrice={livePrice}
          changePct={changePct}
          canRemove={false}
          backLabel="Close"
          onClose={() => setEditing(null)}
          onSaved={onChanged}
        />
      )}
    </Panel>
  )
}
