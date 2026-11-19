import { useState } from 'react'
import { Button, FormField, Modal, NumberField, TextArea, TextField } from '../../ui'
import { updatePaperPortfolio, type PaperMandate } from '../../../api/paperTrading'

type Limit =
  | 'max_position_pct'
  | 'max_sector_pct'
  | 'min_cash_pct'
  | 'max_positions'
  | 'stop_loss_pct'

// The section's intro says what each limit does; per-field info tips overflowed the dialog.
const LIMITS: { key: Limit; label: string; step: number | 'any' }[] = [
  { key: 'max_position_pct', label: 'Max per holding (%)', step: 'any' },
  { key: 'max_sector_pct', label: 'Max per sector (%)', step: 'any' },
  { key: 'min_cash_pct', label: 'Min cash (%)', step: 'any' },
  { key: 'max_positions', label: 'Max holdings', step: 1 },
  { key: 'stop_loss_pct', label: 'Stop loss (%)', step: 'any' },
]

type Draft = { goal: string; universe: string } & Record<Limit, string>

const draftOf = (m: PaperMandate | null): Draft => ({
  goal: m?.goal ?? '',
  universe: (m?.universe ?? []).join(', '),
  max_position_pct: m?.max_position_pct?.toString() ?? '',
  max_sector_pct: m?.max_sector_pct?.toString() ?? '',
  min_cash_pct: m?.min_cash_pct?.toString() ?? '',
  max_positions: m?.max_positions?.toString() ?? '',
  stop_loss_pct: m?.stop_loss_pct?.toString() ?? '',
})

/** The mandate the server stores for a draft; null when every field is blank. */
function mandateOf(d: Draft): PaperMandate | null {
  const out: PaperMandate = {}
  if (d.goal.trim()) out.goal = d.goal.trim()
  for (const { key } of LIMITS) if (d[key].trim() !== '') out[key] = Number(d[key])
  const symbols = d.universe
    .split(/[\s,]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
  if (symbols.length) out.universe = symbols
  return Object.keys(out).length ? out : null
}

/** Rename a paper portfolio and set its mandate: the goal in words and the limits every buy
 *  keeps. Rendered only while open, so each opening starts from what is saved. */
export function EditPortfolioDialog({
  id,
  name,
  mandate,
  onClose,
  onSaved,
}: {
  id: string
  name: string
  mandate: PaperMandate | null
  onClose: () => void
  onSaved: () => void
}) {
  const [value, setValue] = useState(name)
  const [draft, setDraft] = useState<Draft>(() => draftOf(mandate))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const next = value.trim()
  const nextMandate = mandateOf(draft)
  const mandateChanged = JSON.stringify(nextMandate) !== JSON.stringify(mandateOf(draftOf(mandate)))
  const ok = !!next && (next !== name || mandateChanged) && !busy
  const set = (key: keyof Draft) => (v: string) => setDraft((d) => ({ ...d, [key]: v }))

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await updatePaperPortfolio(id, {
        ...(next !== name ? { name: next } : {}),
        ...(mandateChanged ? { mandate: nextMandate } : {}),
      })
      onSaved()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Edit paper portfolio"
      size="md"
      footer={
        <>
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!ok}
            loading={busy}
            onClick={() => void save()}
          >
            Save
          </Button>
        </>
      }
    >
      <form
        className="paper-form"
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) void save()
        }}
      >
        <FormField label="Name" htmlFor="pp-edit-name">
          <TextField
            id="pp-edit-name"
            value={value}
            maxLength={80}
            autoFocus
            onChange={(e) => setValue(e.target.value)}
          />
        </FormField>
        <div className="paper-mandate-fields">
          <h3 className="paper-mandate-head">Mandate</h3>
          <p className="paper-next">
            What this portfolio is for and the limits it keeps. A buy that breaks a limit asks
            first; a stop loss only flags a holding. Blank means no limit. Lucius trades within it.
          </p>
          <FormField label="Goal" htmlFor="pp-edit-goal">
            <TextArea
              id="pp-edit-goal"
              rows={2}
              maxLength={600}
              placeholder="Beat the S&P 500 over a year with a few AI names."
              value={draft.goal}
              onChange={(e) => set('goal')(e.target.value)}
            />
          </FormField>
          <div className="paper-two">
            {LIMITS.map((l) => (
              <FormField key={l.key} label={l.label} htmlFor={`pp-edit-${l.key}`}>
                <NumberField
                  id={`pp-edit-${l.key}`}
                  min={0}
                  step={l.step}
                  value={draft[l.key]}
                  onChange={(e) => set(l.key)(e.target.value)}
                />
              </FormField>
            ))}
          </div>
          <FormField label="May buy only" htmlFor="pp-edit-universe">
            <TextField
              id="pp-edit-universe"
              placeholder="Symbols, separated by commas"
              value={draft.universe}
              onChange={(e) => set('universe')(e.target.value)}
            />
          </FormField>
        </div>
        {error && <div className="paper-next bad">{error}</div>}
      </form>
    </Modal>
  )
}
