import { useState } from 'react'
import { Button, Modal, TextField } from '../../ui'
import { useMarketSearch } from '../../tabs/automations/TickerPicker'
import { COPY } from './following.copy'
import { companyName } from './following.format'

interface Props {
  /** The holding as the filing names it, e.g. "ACME LTD". */
  filedName: string
  current: string | null
  watchesToo: boolean
  /** Saves the ticker ("" hands the holding back to the lookup); throws with a reason. */
  onSave: (symbol: string) => Promise<void>
  onClose: () => void
}

/** What to search first: the company as a person would type it ("Asml Hldg Nv" → "Asml"). */
function firstQuery(filedName: string): string {
  return companyName(filedName).split(/\s+/)[0] ?? ''
}

/** Set or correct the ticker for one holding of a filing: search the market (the command
 *  palette's search) and pick, or type it. The server checks it has a price before saving, so
 *  a typo is refused with a reason rather than copied into an order. */
export function TickerDialog({ filedName, current, watchesToo, onSave, onClose }: Props) {
  const [query, setQuery] = useState(current ?? firstQuery(filedName))
  const [picked, setPicked] = useState<string | null>(current)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const search = useMarketSearch(query, true)
  const results = search.results.filter((r) => r.asset_class !== 'crypto')
  const symbol = (picked ?? query).trim().toUpperCase()
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSave(symbol)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={current ? COPY.tickerFixTitle : COPY.tickerSetTitle}
      size="sm"
      footer={
        <div className="ui-confirm-actions">
          <Button size="sm" variant="ghost" onClick={onClose}>
            {COPY.cancel}
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={saving}
            disabled={!symbol && !current}
            onClick={() => void save()}
          >
            {picked ? COPY.tickerUse(picked) : COPY.save}
          </Button>
        </div>
      }
    >
      <form
        className="fol-ticker-form"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <p className="fol-ticker-filed">{COPY.tickerFiledAs(filedName)}</p>
        <TextField
          aria-label={COPY.tickerSearchLabel}
          placeholder={COPY.tickerPlaceholder}
          value={query}
          type="search"
          autoComplete="off"
          enterKeyHint="search"
          data-autofocus
          onChange={(e) => {
            setQuery(e.target.value)
            setPicked(null)
          }}
        />
        {/* The stocks that match, to pick from: ticker, company, where it trades. */}
        <ul
          className="fol-ticker-results"
          aria-label={COPY.tickerResultsLabel}
          aria-busy={search.loading}
        >
          {results.map((r) => (
            <li key={`${r.symbol}-${r.exchange}`}>
              <button
                type="button"
                className={`fol-ticker-result${picked === r.symbol ? ' is-on' : ''}`}
                aria-pressed={picked === r.symbol}
                onClick={() => setPicked(r.symbol)}
              >
                <span className="mk-hsym">{r.symbol}</span>
                <span className="mk-hnm">{r.name}</span>
                <span className="fol-ticker-exch">{r.exchange}</span>
              </button>
            </li>
          ))}
          {!search.loading && query.trim() && results.length === 0 && (
            <li className="fol-ticker-none">{COPY.tickerNoResults}</li>
          )}
        </ul>
        <p className="fol-ticker-help">
          {current ? COPY.tickerClearHelp : COPY.tickerHelp}
          {watchesToo && ` ${COPY.tickerWatchesToo}`}
        </p>
        {error && <p className="fol-ticker-error">{error}</p>}
      </form>
    </Modal>
  )
}
