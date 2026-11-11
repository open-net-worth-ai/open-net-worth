import { useState } from 'react'
import { Button, Modal, TextField } from '../../ui'
import { COPY } from './following.copy'

interface Props {
  /** The holding as the filing names it, e.g. "ACME LTD". */
  filedName: string
  current: string | null
  watchesToo: boolean
  /** Saves the ticker ("" hands the holding back to the lookup); throws with a reason. */
  onSave: (symbol: string) => Promise<void>
  onClose: () => void
}

/** Set or correct the ticker for one holding of a filing. The server checks it has a price
 *  before saving, so a typo is refused with a reason rather than copied into an order. */
export function TickerDialog({ filedName, current, watchesToo, onSave, onClose }: Props) {
  const [symbol, setSymbol] = useState(current ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSave(symbol.trim().toUpperCase())
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
            disabled={!symbol.trim() && !current}
            onClick={() => void save()}
          >
            {COPY.save}
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
          aria-label={COPY.tickerLabel}
          placeholder={COPY.tickerPlaceholder}
          value={symbol}
          maxLength={12}
          type="search"
          autoComplete="off"
          autoCapitalize="characters"
          enterKeyHint="done"
          data-autofocus
          onChange={(e) => setSymbol(e.target.value)}
        />
        <p className="fol-ticker-help">
          {current ? COPY.tickerClearHelp : COPY.tickerHelp}
          {watchesToo && ` ${COPY.tickerWatchesToo}`}
        </p>
        {error && <p className="fol-ticker-error">{error}</p>}
      </form>
    </Modal>
  )
}
