import { EntityRow } from '../../../lucius/EntityRow'
import { useState } from 'react'
import './options.css'
import {
  SummaryBar,
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Modal,
  Cluster,
  TabSkeleton,
} from '../../ui'
import { ChartCandlestick } from '../../../ui/icons'
import type { StatCard } from '../../ui'
import { fDate, plClass } from '../../../utils/format'
import { useOptionsView } from './useOptionsView'
import type { OptionPosition } from './useOptionsView'
import { Spark } from '../../../lucius/identity'
import { RecordOptionModal } from './RecordOptionModal'
import { CloseOptionModal } from './CloseOptionModal'
import { LuciusCta } from '../../intro/LuciusCta'
import { deleteOption } from '../../../api/options'

interface Props {
  onAskLucius?: (text: string) => void
}

// Format a signed monetary amount with real typographic minus (−).
function fSigned(n: number, baseCurrency: string): string {
  const abs = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: baseCurrency,
    maximumFractionDigits: 0,
  }).format(Math.round(Math.abs(n)))
  return (n >= 0 ? '+' : '−') + abs
}

interface SectionProps {
  title: string
  sub: string
  positions: OptionPosition[]
  baseCurrency: string
  onCloseRow: (p: OptionPosition) => void
  onDeleteRow: (p: OptionPosition) => void
}

function OptionsSection({
  title,
  sub,
  positions,
  baseCurrency,
  onCloseRow,
  onDeleteRow,
}: SectionProps) {
  if (!positions.length) return null
  return (
    <div className="mk-panel">
      <div className="mk-panel-head">
        <span className="mk-panel-title">{title}</span>
        <span className="mk-panel-spacer" />
        <span className="mk-panel-sub">{sub}</span>
      </div>
      <div className="mk-panel-body" style={{ overflowX: 'auto' }}>
        <table className="opt-tbl">
          <thead>
            <tr>
              <th>Position</th>
              <th>Contract</th>
              <th>Expiry</th>
              <th className="num">Premium</th>
              <th className="num">Open P&amp;L</th>
              <th>Status</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <EntityRow
                as="tr"
                key={p.id}
                type="option_contract"
                id={p.id}
                label={`${p.underlying_symbol} ${p.strike}${p.option_type.slice(0, 1).toUpperCase()} ${fDate(p.expiry, 'short', 'always')}`}
                item={p}
                toFields={(it) => ({
                  underlying: it.underlying_symbol,
                  option_type: it.option_type,
                  side: it.side,
                  strike: it.strike,
                  expiry: it.expiry,
                  contracts: it.contracts,
                })}
                search={`${p.underlying_symbol} ${p.option_type} ${p.strategy_tag}`}
                sublabel={`${p.side} • ${fDate(p.expiry, 'short', 'always')}`}
              >
                <td>
                  <span className="opt-under">{p.underlying_symbol}</span>
                  <span className="opt-strat">{p.strategy_tag}</span>
                  <div className="opt-note">{p.note}</div>
                </td>
                <td>
                  <span className="opt-contract">
                    {(p.side === 'short' ? '−' : '+') + p.contracts} {p.option_type} @{' '}
                    {p.strike.toLocaleString()}
                  </span>
                </td>
                <td className="not-money">
                  <span className="opt-contract">{fDate(p.expiry, 'short', 'always')}</span>
                  <span className={'opt-dte' + (p.dte <= 14 ? ' soon' : '')}>{p.dte}d</span>
                </td>
                <td className={`num money ${plClass(p.premium)}`}>
                  {fSigned(p.premium, baseCurrency)}
                </td>
                <td className={`num money ${plClass(p.mark)}`}>{fSigned(p.mark, baseCurrency)}</td>
                <td className="not-money">
                  <Badge tone={p.status === 'ITM' ? 'warn' : 'muted'}>{p.status}</Badge>
                </td>
                <td className="opt-row-actions not-money">
                  <Button variant="ghost" size="dense" onClick={() => onCloseRow(p)}>
                    Close
                  </Button>
                  <Button variant="ghost" size="dense" onClick={() => onDeleteRow(p)}>
                    Delete
                  </Button>
                </td>
              </EntityRow>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const OPTIONS_MESSAGE =
  'Help me record my options. Ask whether they are covered calls, cash-secured puts or long ' +
  'options; I can send a screenshot or statement of my positions instead of typing. Ask me one ' +
  'thing at a time.'

export function OptionsTab({ onAskLucius }: Props) {
  const {
    baseCurrency,
    summaryCards,
    incomePositions,
    hedgePositions,
    netMark,
    loading,
    error,
    refetch,
  } = useOptionsView()
  const [recording, setRecording] = useState(false)
  const [closing, setClosing] = useState<OptionPosition | null>(null)
  const [deleting, setDeleting] = useState<OptionPosition | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  if (loading) {
    return <TabSkeleton variant="options" label="Loading options…" header={false} />
  }

  if (error) {
    return <ErrorState message="Could not load options positions." onRetry={refetch} />
  }

  const hasPositions = incomePositions.length > 0 || hedgePositions.length > 0
  const done = () => {
    setRecording(false)
    setClosing(null)
    refetch()
  }
  const openDelete = (p: OptionPosition | null) => {
    setDeleteError(null)
    setDeleting(p)
  }

  const modals = (
    <>
      {recording && <RecordOptionModal onClose={() => setRecording(false)} onDone={done} />}
      {closing && (
        <CloseOptionModal position={closing} onClose={() => setClosing(null)} onDone={done} />
      )}
      {deleting && (
        <Modal
          open
          onClose={() => openDelete(null)}
          title="Delete option entry"
          size="sm"
          footer={
            <Cluster gap="--space-3" justify="between">
              <Button variant="ghost" onClick={() => openDelete(null)} disabled={deleteBusy}>
                Cancel
              </Button>
              <Button
                variant="danger"
                loading={deleteBusy}
                onClick={() => {
                  setDeleteBusy(true)
                  setDeleteError(null)
                  deleteOption(deleting.id)
                    .then(() => {
                      setDeleting(null)
                      setDeleteBusy(false)
                      refetch()
                    })
                    .catch((e) => {
                      setDeleteError(e instanceof Error ? e.message : 'Delete failed.')
                      setDeleteBusy(false)
                    })
                }}
              >
                Delete
              </Button>
            </Cluster>
          }
        >
          <p className="opt-close-line">
            Removes the mistaken entry {deleting.underlying_symbol} {deleting.option_type} @{' '}
            {deleting.strike.toLocaleString()} — it does NOT book any cash leg. A genuine exit
            should use Close instead.
          </p>
          {deleteError && <ErrorState message={deleteError} />}
        </Modal>
      )}
    </>
  )

  if (!hasPositions) {
    return (
      <>
        <EmptyState
          fill
          icon={<ChartCandlestick size={28} strokeWidth={1.5} aria-hidden="true" />}
          title="No options positions"
          body="Covered calls, cash-secured puts and hedges will appear here — with expiry countdowns, strikes and open P&L."
          action={
            <Cluster gap="--space-3" justify="center">
              <Button variant="primary" size="sm" onClick={() => setRecording(true)}>
                Record option
              </Button>
              <LuciusCta
                label="Add with Lucius"
                message={OPTIONS_MESSAGE}
                onAskLucius={onAskLucius}
              />
            </Cluster>
          }
        />
        {modals}
      </>
    )
  }

  const cards: StatCard[] = summaryCards.map((c) => ({
    label: c.label,
    value: c.value,
    caption: c.caption,
  }))

  return (
    <div className="mk-subt">
      <div className="opt-toolbar">
        <Button variant="primary" size="sm" onClick={() => setRecording(true)}>
          Record option
        </Button>
      </div>
      <SummaryBar cards={cards} />

      <OptionsSection
        title="Income overlays"
        sub="covered calls • cash-secured puts — premium you collect against holdings"
        positions={incomePositions}
        baseCurrency={baseCurrency}
        onCloseRow={setClosing}
        onDeleteRow={openDelete}
      />

      <OptionsSection
        title="Directional &amp; hedges"
        sub="long puts protect, long calls add upside — risk is capped at premium paid"
        positions={hedgePositions}
        baseCurrency={baseCurrency}
        onCloseRow={setClosing}
        onDeleteRow={openDelete}
      />

      <div className="mk-panel">
        <div
          className="mk-panel-body"
          style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}
        >
          <span className="mk-field-help" style={{ flex: 1, minWidth: 220 }}>
            Net open P&amp;L across all option positions is{' '}
            <b className={`money ${plClass(netMark)}`}>{fSigned(netMark, baseCurrency)}</b>. Premium
            decays in your favour on the short overlays as expiry nears.
          </span>
          <Button
            size="sm"
            variant="primary"
            className="fi-lucius"
            iconLeft={<Spark size={15} ariaHidden />}
            onClick={() => onAskLucius?.('Tell me about my options positions and assignment risk.')}
          >
            Ask Lucius about options
          </Button>
        </div>
      </div>
      {modals}
    </div>
  )
}
