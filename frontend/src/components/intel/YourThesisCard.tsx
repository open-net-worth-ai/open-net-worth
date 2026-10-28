import { useState } from 'react'
import { Pencil } from 'lucide-react'
import {
  Button,
  FormField,
  InfoPopover,
  Panel,
  SegmentedControl,
  Text,
  TextArea,
  TextField,
} from '../ui'
import { saveInvestmentThesis } from '../../api/investmentTheses'
import type { InvestmentThesis, InvestmentThesisFields } from '../../types/assetDetail'
import { fPrice } from '../../utils/format'

// "Your Thesis" — why you own (or would buy) this instrument and the prices that matter to you.
// One thesis per instrument (services/investment_theses); held vs watched only changes what is shown:
// a holding adds its role, breaks-if, stop and trim.

type Role = NonNullable<InvestmentThesis['role']>
type PriceKey = 'stop_below' | 'buy_below' | 'buy_above' | 'fair_value' | 'trim_above'

const ROLES: { value: Role; label: string; help: string }[] = [
  {
    value: 'core',
    label: 'Core',
    help: 'Long-term base of your portfolio. Normal swings stay quiet.',
  },
  {
    value: 'satellite',
    label: 'Satellite',
    help: 'An active bet around your core. Size and drawdown are watched closely.',
  },
  {
    value: 'speculative',
    label: 'Speculative',
    help: 'A small, high-risk bet. Flagged early if it grows.',
  },
]

const PRICE_LABELS: Record<PriceKey, string> = {
  stop_below: 'Stop below',
  trim_above: 'Trim above',
  buy_below: 'Buy at or under',
  buy_above: '…but not under',
  fair_value: 'Fair value',
}

interface Props {
  instrumentId: string
  ticker: string
  thesis: InvestmentThesis | null
  held: boolean
  price: number | null
  /** Max fraction digits for prices (undefined = the formatter's own, for crypto). */
  priceMaxFrac?: number
  onSaved: () => void
}

interface Draft {
  role: Role | null
  reason: string
  breaks_if: string
  prices: Record<PriceKey, string>
}

/** A typed price as a number, or null when blank or not a positive number ("12abc" too). */
const num = (s: string): number | null => {
  const text = s.replace(/,/g, '').trim()
  if (!text) return null
  const v = Number(text)
  return Number.isFinite(v) && v > 0 ? v : null
}

function draftOf(thesis: InvestmentThesis | null): Draft {
  const str = (v: number | null | undefined) => (v == null ? '' : String(v))
  return {
    role: thesis?.role ?? null,
    reason: thesis?.reason ?? '',
    breaks_if: thesis?.breaks_if ?? '',
    prices: {
      stop_below: str(thesis?.stop_below),
      buy_below: str(thesis?.buy_below),
      buy_above: str(thesis?.buy_above),
      fair_value: str(thesis?.fair_value),
      trim_above: str(thesis?.trim_above),
    },
  }
}

/** The first rule the draft breaks, and the field to outline for it. */
function draftError(d: Draft): { message: string; field: PriceKey } | null {
  // A typo must not save as "no level" — that would silently delete it.
  for (const k of Object.keys(PRICE_LABELS) as PriceKey[]) {
    if (d.prices[k].trim() && num(d.prices[k]) == null)
      return { message: `${PRICE_LABELS[k]} must be a positive number.`, field: k }
  }
  const below = num(d.prices.buy_below)
  const above = num(d.prices.buy_above)
  const stop = num(d.prices.stop_below)
  const trim = num(d.prices.trim_above)
  if (above != null && below == null)
    return { message: 'Set “Buy at or under” first.', field: 'buy_below' }
  if (above != null && below != null && above >= below)
    return { message: 'The floor must be below the top of the buy zone.', field: 'buy_above' }
  if (stop != null && trim != null && stop >= trim)
    return { message: 'Your stop must be below your trim level.', field: 'stop_below' }
  return null
}

/** The fields a save sends. PUT replaces the thesis, so held-only fields a watched card doesn't
 *  show are carried over unchanged rather than wiped. */
function fieldsOf(
  d: Draft,
  thesis: InvestmentThesis | null,
  held: boolean,
): InvestmentThesisFields {
  const pick = (k: PriceKey) => num(d.prices[k])
  return {
    role: held ? d.role : (thesis?.role ?? null),
    reason: d.reason.trim() || null,
    breaks_if: held ? d.breaks_if.trim() || null : (thesis?.breaks_if ?? null),
    stop_below: held ? pick('stop_below') : (thesis?.stop_below ?? null),
    trim_above: held ? pick('trim_above') : (thesis?.trim_above ?? null),
    buy_below: pick('buy_below'),
    buy_above: pick('buy_above'),
    fair_value: pick('fair_value'),
  }
}

interface Level {
  key: 'stop' | 'zone' | 'fair' | 'trim'
  label: string
  value: string
  at: number
}

function levelsOf(thesis: InvestmentThesis, held: boolean, fmt: (v: number) => string): Level[] {
  const out: Level[] = []
  if (held && thesis.stop_below != null)
    out.push({
      key: 'stop',
      label: 'Stop below',
      value: fmt(thesis.stop_below),
      at: thesis.stop_below,
    })
  if (thesis.buy_below != null)
    out.push({
      key: 'zone',
      label: 'Buy zone',
      value:
        thesis.buy_above != null
          ? `${fmt(thesis.buy_above)}–${fmt(thesis.buy_below)}`
          : `≤ ${fmt(thesis.buy_below)}`,
      at: thesis.buy_below,
    })
  if (thesis.fair_value != null)
    out.push({
      key: 'fair',
      label: 'Fair value',
      value: fmt(thesis.fair_value),
      at: thesis.fair_value,
    })
  if (held && thesis.trim_above != null)
    out.push({
      key: 'trim',
      label: 'Trim above',
      value: fmt(thesis.trim_above),
      at: thesis.trim_above,
    })
  return out
}

function distance(level: number, price: number): string {
  const d = (level / price - 1) * 100
  return `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)}%`
}

function PriceBar({
  thesis,
  levels,
  price,
  fmt,
}: {
  thesis: InvestmentThesis
  levels: Level[]
  price: number
  fmt: (v: number) => string
}) {
  const zoneLow = thesis.buy_below != null ? (thesis.buy_above ?? null) : null
  const points = [price, ...levels.map((l) => l.at), ...(zoneLow != null ? [zoneLow] : [])]
  const lo = Math.min(...points) * 0.9
  const hi = Math.max(...points) * 1.05
  const pct = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(2)}%`
  const zoneFrom =
    thesis.buy_below != null ? (zoneLow ?? Math.max(lo, thesis.buy_below * 0.88)) : null
  const label = `Price ${fmt(price)} against your thesis: ${levels.map((l) => `${l.label} ${l.value}`).join(', ')}`
  return (
    <div className="asd-thesis-bar" role="img" aria-label={label}>
      <div className="asd-thesis-track" />
      {thesis.buy_below != null && zoneFrom != null && (
        <div
          className="asd-thesis-zone"
          style={{
            left: pct(zoneFrom),
            width: `${(((thesis.buy_below - zoneFrom) / (hi - lo)) * 100).toFixed(2)}%`,
          }}
        />
      )}
      {levels
        .filter((l) => l.key !== 'zone')
        .map((l) => (
          <div
            key={l.key}
            className={`asd-thesis-tick asd-thesis-tick--${l.key}`}
            style={{ left: pct(l.at) }}
          />
        ))}
      <div className="asd-thesis-now" style={{ left: pct(price) }} />
      <div className="asd-thesis-now-label" style={{ left: pct(price) }}>
        {fmt(price)}
      </div>
    </div>
  )
}

export function YourThesisCard({
  instrumentId,
  ticker,
  thesis,
  held,
  price,
  priceMaxFrac,
  onSaved,
}: Props) {
  // The saved thesis shows at once; the refetch's copy replaces it as soon as `thesis` changes.
  const [justSaved, setJustSaved] = useState<{
    over: InvestmentThesis | null
    thesis: InvestmentThesis | null
  } | null>(null)
  const current = justSaved && justSaved.over === thesis ? justSaved.thesis : thesis
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft>(() => draftOf(thesis))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fmt = (v: number) => fPrice(v, priceMaxFrac)
  const localError = editing ? draftError(draft) : null

  function startEdit() {
    setDraft(draftOf(current))
    setError(null)
    setEditing(true)
  }

  async function save() {
    if (localError) return
    setSaving(true)
    setError(null)
    try {
      const saved = await saveInvestmentThesis(instrumentId, fieldsOf(draft, current, held))
      setJustSaved({ over: thesis, thesis: saved })
      setEditing(false)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your thesis. Try again.")
    } finally {
      setSaving(false)
    }
  }

  const role = held && current?.role ? ROLES.find((r) => r.value === current.role) : undefined
  const levels = current ? levelsOf(current, held, fmt) : []
  const hasContent =
    !!current && (!!current.reason || (held && !!current.breaks_if) || levels.length > 0)

  const actions = editing ? undefined : (
    <button type="button" className="asd-rail-manage" onClick={startEdit}>
      <Pencil size={12} aria-hidden="true" />
      {hasContent ? 'Edit' : 'Add'}
    </button>
  )

  const badge =
    role && !editing ? (
      <span className="asd-thesis-role-wrap">
        <span className="asd-thesis-role">{role.label}</span>
        <InfoPopover text={role.help} label={`What ${role.label} means`} />
      </span>
    ) : undefined

  return (
    <Panel
      title="Your Thesis"
      badge={badge}
      actions={actions}
      className="asd-rail-panel asd-thesis-card"
    >
      {editing ? (
        <div className="asd-thesis-form">
          {held && (
            <FormField label="Role" info={role?.help}>
              <SegmentedControl<Role>
                ariaLabel="Role"
                options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
                value={draft.role ?? ('' as Role)}
                onChange={(v) => setDraft({ ...draft, role: v })}
              />
            </FormField>
          )}
          <FormField label={held ? 'Why you own it' : 'Why you’d buy it'} htmlFor="thesis-reason">
            <TextArea
              id="thesis-reason"
              rows={3}
              maxLength={280}
              value={draft.reason}
              placeholder={
                held ? 'What has to keep being true?' : 'What would make it worth owning?'
              }
              onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
            />
          </FormField>
          {held && (
            <FormField label="Thesis breaks if" htmlFor="thesis-breaks">
              <TextArea
                id="thesis-breaks"
                rows={3}
                maxLength={280}
                value={draft.breaks_if}
                placeholder="What would prove you wrong?"
                onChange={(e) => setDraft({ ...draft, breaks_if: e.target.value })}
              />
            </FormField>
          )}
          <div className="asd-thesis-grid">
            {(held
              ? (['stop_below', 'trim_above', 'buy_below', 'buy_above', 'fair_value'] as PriceKey[])
              : (['buy_below', 'buy_above', 'fair_value'] as PriceKey[])
            ).map((k) => (
              <FormField
                key={k}
                label={PRICE_LABELS[k]}
                htmlFor={`thesis-${k}`}
                span={held && k === 'fair_value' ? 'full' : 1}
              >
                <TextField
                  id={`thesis-${k}`}
                  inputMode="decimal"
                  placeholder="—"
                  value={draft.prices[k]}
                  invalid={localError?.field === k}
                  onChange={(e) =>
                    setDraft({ ...draft, prices: { ...draft.prices, [k]: e.target.value } })
                  }
                />
              </FormField>
            ))}
          </div>
          {(localError || error) && (
            <p className="ui-field__error" role="alert">
              {localError?.message ?? error}
            </p>
          )}
          <div className="asd-thesis-actions">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={() => void save()} loading={saving}>
              Save thesis
            </Button>
          </div>
        </div>
      ) : !hasContent ? (
        <Text size="sm" tone="muted">
          {held ? `Write down why you own ${ticker}.` : `Write down why you’d buy ${ticker}.`}
        </Text>
      ) : (
        <div className="asd-thesis-body">
          {current?.reason && <p className="asd-thesis-reason">{current.reason}</p>}
          {held && current?.breaks_if && (
            <div className="asd-thesis-breaks">
              <p className="asd-thesis-breaks-label">Thesis breaks if</p>
              <p className="asd-thesis-reason">{current.breaks_if}</p>
            </div>
          )}
          {levels.length > 0 && (
            <div>
              {price != null && current && (
                <PriceBar thesis={current} levels={levels} price={price} fmt={fmt} />
              )}
              <div className="asd-thesis-levels">
                {levels.map((l) => (
                  <div key={l.key} className="asd-pos-row">
                    <span className="asd-pos-label">
                      <span
                        className={`asd-thesis-key asd-thesis-key--${l.key}`}
                        aria-hidden="true"
                      />
                      {l.label}
                    </span>
                    <span className="asd-pos-value">
                      {l.value}
                      {price != null && (
                        <span className="asd-thesis-dist">{distance(l.at, price)}</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}
