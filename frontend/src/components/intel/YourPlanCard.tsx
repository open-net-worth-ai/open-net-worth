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
import { saveHoldingPlan } from '../../api/holdingPlans'
import type { HoldingPlan, HoldingPlanFields } from '../../types/assetDetail'
import { fPrice } from '../../utils/format'

// "Your Plan" — why you own (or would buy) this instrument and the prices that matter to you.
// One plan per instrument (services/holding_plans); held vs watched only changes what is shown:
// a holding adds its role, sell condition, stop and trim.

type Role = NonNullable<HoldingPlan['role']>
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
  plan: HoldingPlan | null
  held: boolean
  price: number | null
  /** Max fraction digits for prices (undefined = the formatter's own, for crypto). */
  priceMaxFrac?: number
  onSaved: () => void
}

interface Draft {
  role: Role | null
  thesis: string
  exit_if: string
  prices: Record<PriceKey, string>
}

/** A typed price as a number, or null when blank or not a positive number ("12abc" too). */
const num = (s: string): number | null => {
  const text = s.replace(/,/g, '').trim()
  if (!text) return null
  const v = Number(text)
  return Number.isFinite(v) && v > 0 ? v : null
}

function draftOf(plan: HoldingPlan | null): Draft {
  const str = (v: number | null | undefined) => (v == null ? '' : String(v))
  return {
    role: plan?.role ?? null,
    thesis: plan?.thesis ?? '',
    exit_if: plan?.exit_if ?? '',
    prices: {
      stop_below: str(plan?.stop_below),
      buy_below: str(plan?.buy_below),
      buy_above: str(plan?.buy_above),
      fair_value: str(plan?.fair_value),
      trim_above: str(plan?.trim_above),
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

/** The fields a save sends. PUT replaces the plan, so held-only fields a watched card doesn't
 *  show are carried over unchanged rather than wiped. */
function fieldsOf(d: Draft, plan: HoldingPlan | null, held: boolean): HoldingPlanFields {
  const pick = (k: PriceKey) => num(d.prices[k])
  return {
    role: held ? d.role : (plan?.role ?? null),
    thesis: d.thesis.trim() || null,
    exit_if: held ? d.exit_if.trim() || null : (plan?.exit_if ?? null),
    stop_below: held ? pick('stop_below') : (plan?.stop_below ?? null),
    trim_above: held ? pick('trim_above') : (plan?.trim_above ?? null),
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

function levelsOf(plan: HoldingPlan, held: boolean, fmt: (v: number) => string): Level[] {
  const out: Level[] = []
  if (held && plan.stop_below != null)
    out.push({ key: 'stop', label: 'Stop below', value: fmt(plan.stop_below), at: plan.stop_below })
  if (plan.buy_below != null)
    out.push({
      key: 'zone',
      label: 'Buy zone',
      value:
        plan.buy_above != null
          ? `${fmt(plan.buy_above)}–${fmt(plan.buy_below)}`
          : `≤ ${fmt(plan.buy_below)}`,
      at: plan.buy_below,
    })
  if (plan.fair_value != null)
    out.push({ key: 'fair', label: 'Fair value', value: fmt(plan.fair_value), at: plan.fair_value })
  if (held && plan.trim_above != null)
    out.push({ key: 'trim', label: 'Trim above', value: fmt(plan.trim_above), at: plan.trim_above })
  return out
}

function distance(level: number, price: number): string {
  const d = (level / price - 1) * 100
  return `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)}%`
}

function PriceBar({
  plan,
  levels,
  price,
  fmt,
}: {
  plan: HoldingPlan
  levels: Level[]
  price: number
  fmt: (v: number) => string
}) {
  const zoneLow = plan.buy_below != null ? (plan.buy_above ?? null) : null
  const points = [price, ...levels.map((l) => l.at), ...(zoneLow != null ? [zoneLow] : [])]
  const lo = Math.min(...points) * 0.9
  const hi = Math.max(...points) * 1.05
  const pct = (v: number) => `${(((v - lo) / (hi - lo)) * 100).toFixed(2)}%`
  const zoneFrom = plan.buy_below != null ? (zoneLow ?? Math.max(lo, plan.buy_below * 0.88)) : null
  const label = `Price ${fmt(price)} against your plan: ${levels.map((l) => `${l.label} ${l.value}`).join(', ')}`
  return (
    <div className="asd-plan-bar" role="img" aria-label={label}>
      <div className="asd-plan-track" />
      {plan.buy_below != null && zoneFrom != null && (
        <div
          className="asd-plan-zone"
          style={{
            left: pct(zoneFrom),
            width: `${(((plan.buy_below - zoneFrom) / (hi - lo)) * 100).toFixed(2)}%`,
          }}
        />
      )}
      {levels
        .filter((l) => l.key !== 'zone')
        .map((l) => (
          <div
            key={l.key}
            className={`asd-plan-tick asd-plan-tick--${l.key}`}
            style={{ left: pct(l.at) }}
          />
        ))}
      <div className="asd-plan-now" style={{ left: pct(price) }} />
      <div className="asd-plan-now-label" style={{ left: pct(price) }}>
        {fmt(price)}
      </div>
    </div>
  )
}

export function YourPlanCard({
  instrumentId,
  ticker,
  plan,
  held,
  price,
  priceMaxFrac,
  onSaved,
}: Props) {
  // The saved plan shows at once; the refetch's copy replaces it as soon as `plan` changes.
  const [justSaved, setJustSaved] = useState<{
    over: HoldingPlan | null
    plan: HoldingPlan | null
  } | null>(null)
  const current = justSaved && justSaved.over === plan ? justSaved.plan : plan
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft>(() => draftOf(plan))
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
      const saved = await saveHoldingPlan(instrumentId, fieldsOf(draft, current, held))
      setJustSaved({ over: plan, plan: saved })
      setEditing(false)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your plan. Try again.")
    } finally {
      setSaving(false)
    }
  }

  const role = held && current?.role ? ROLES.find((r) => r.value === current.role) : undefined
  const levels = current ? levelsOf(current, held, fmt) : []
  const hasContent =
    !!current && (!!current.thesis || (held && !!current.exit_if) || levels.length > 0)

  const actions = editing ? undefined : (
    <button type="button" className="asd-rail-manage" onClick={startEdit}>
      <Pencil size={12} aria-hidden="true" />
      {hasContent ? 'Edit' : 'Add'}
    </button>
  )

  const badge =
    role && !editing ? (
      <span className="asd-plan-role-wrap">
        <span className="asd-plan-role">{role.label}</span>
        <InfoPopover text={role.help} label={`What ${role.label} means`} />
      </span>
    ) : undefined

  return (
    <Panel
      title="Your Plan"
      badge={badge}
      actions={actions}
      className="asd-rail-panel asd-plan-card"
    >
      {editing ? (
        <div className="asd-plan-form">
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
          <FormField label={held ? 'Why you own it' : 'Why you’d buy it'} htmlFor="plan-thesis">
            <TextArea
              id="plan-thesis"
              rows={3}
              maxLength={280}
              value={draft.thesis}
              placeholder={
                held ? 'What has to keep being true?' : 'What would make it worth owning?'
              }
              onChange={(e) => setDraft({ ...draft, thesis: e.target.value })}
            />
          </FormField>
          {held && (
            <FormField label="You’d sell if" htmlFor="plan-exit">
              <TextArea
                id="plan-exit"
                rows={3}
                maxLength={280}
                value={draft.exit_if}
                placeholder="What would prove you wrong?"
                onChange={(e) => setDraft({ ...draft, exit_if: e.target.value })}
              />
            </FormField>
          )}
          <div className="asd-plan-grid">
            {(held
              ? (['stop_below', 'trim_above', 'buy_below', 'buy_above', 'fair_value'] as PriceKey[])
              : (['buy_below', 'buy_above', 'fair_value'] as PriceKey[])
            ).map((k) => (
              <FormField
                key={k}
                label={PRICE_LABELS[k]}
                htmlFor={`plan-${k}`}
                span={held && k === 'fair_value' ? 'full' : 1}
              >
                <TextField
                  id={`plan-${k}`}
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
          <div className="asd-plan-actions">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button variant="primary" size="sm" onClick={() => void save()} loading={saving}>
              Save plan
            </Button>
          </div>
        </div>
      ) : !hasContent ? (
        <Text size="sm" tone="muted">
          {held ? `Write down why you own ${ticker}.` : `Write down why you’d buy ${ticker}.`}
        </Text>
      ) : (
        <div className="asd-plan-body">
          {current?.thesis && <p className="asd-plan-thesis">{current.thesis}</p>}
          {held && current?.exit_if && (
            <div className="asd-plan-exit">
              <p className="asd-plan-exit-label">You’d sell if</p>
              <p className="asd-plan-thesis">{current.exit_if}</p>
            </div>
          )}
          {levels.length > 0 && (
            <div>
              {price != null && current && (
                <PriceBar plan={current} levels={levels} price={price} fmt={fmt} />
              )}
              <div className="asd-plan-levels">
                {levels.map((l) => (
                  <div key={l.key} className="asd-pos-row">
                    <span className="asd-pos-label">
                      <span className={`asd-plan-key asd-plan-key--${l.key}`} aria-hidden="true" />
                      {l.label}
                    </span>
                    <span className="asd-pos-value">
                      {l.value}
                      {price != null && (
                        <span className="asd-plan-dist">{distance(l.at, price)}</span>
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
