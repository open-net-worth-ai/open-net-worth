import { useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import {
  Button,
  FormField,
  SegmentedControl,
  TextArea,
  TextField,
  type TextAreaProps,
  type TextFieldProps,
} from '../ui'
import { getCurrencySymbol } from '../../utils/format'
import { saveInvestmentThesis } from '../../api/investmentTheses'
import type { InvestmentThesis, InvestmentThesisFields } from '../../types/assetDetail'
import {
  BREAKS_IF_EXAMPLE,
  BREAKS_IF_LABEL,
  formatPrice,
  groupPrice,
  parsePrice,
  PRICE_LABELS,
  priceText,
  reasonExample,
  reasonLabel,
  ROLES,
  type PriceKey,
  type Role,
} from './thesisFields'

// ThesisEditor — the one form for a stock's investment thesis. The asset page's Your Thesis card
// opens it in place of its read view; the alerts screen's Why you're watching shows it directly.
// Held vs watched only changes the fields: a holding adds its role, breaks-if, stop and trim.

interface Props {
  instrumentId: string
  thesis: InvestmentThesis | null
  held: boolean
  /** The instrument's quote currency — what every price is in. */
  currency?: string
  /** After a save: the stored thesis, or null when the save blanked it. */
  onSaved: (saved: InvestmentThesis | null) => void
  onCancel: () => void
}

interface Draft {
  role: Role | null
  reason: string
  breaks_if: string
  prices: Record<PriceKey, string>
}

/** A holding with no role yet opens on Core, so the control never shows an empty choice. */
function draftOf(thesis: InvestmentThesis | null, held: boolean): Draft {
  const str = (v: number | null | undefined) => (v == null ? '' : formatPrice(v))
  return {
    role: thesis?.role ?? (held ? 'core' : null),
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
    if (d.prices[k].trim() && parsePrice(d.prices[k]) == null)
      return { message: `${PRICE_LABELS[k]} must be a positive number.`, field: k }
  }
  const below = parsePrice(d.prices.buy_below)
  const above = parsePrice(d.prices.buy_above)
  const stop = parsePrice(d.prices.stop_below)
  const trim = parsePrice(d.prices.trim_above)
  if (above != null && below == null)
    return { message: 'Set “Buy at or under” first.', field: 'buy_below' }
  if (above != null && below != null && above >= below)
    return { message: 'The floor must be below the top of the buy zone.', field: 'buy_above' }
  if (stop != null && trim != null && stop >= trim)
    return { message: 'Your stop must be below your trim level.', field: 'stop_below' }
  return null
}

/** The fields a save sends. PUT replaces the thesis, so held-only fields a watched stock doesn't
 *  show are carried over unchanged rather than wiped. */
function fieldsOf(
  d: Draft,
  thesis: InvestmentThesis | null,
  held: boolean,
): InvestmentThesisFields {
  const pick = (k: PriceKey) => parsePrice(d.prices[k])
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

/** A price box with the ticker's currency symbol drawn inside it, like MoneyField's — FormField's
 *  aria wiring lands on the input itself. */
function PriceInput({ symbol, ...rest }: TextFieldProps & { symbol: string }) {
  const style = symbol ? ({ '--ui-money-prefix-chars': symbol.length } as CSSProperties) : undefined
  return (
    <div className="ui-money-field" style={style}>
      <div className="ui-money-field__box">
        {symbol && (
          <span className="ui-money-field__prefix" aria-hidden="true">
            {symbol}
          </span>
        )}
        <TextField inputMode="decimal" {...rest} />
      </div>
    </div>
  )
}

export function ThesisEditor({ instrumentId, thesis, held, currency, onSaved, onCancel }: Props) {
  // useId, not fixed ids: the card and the alerts dialog can both hold an editor on one page.
  const id = useId()
  const [draft, setDraft] = useState<Draft>(() => draftOf(thesis, held))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const localError = draftError(draft)
  const roleHelp = ROLES.find((r) => r.value === draft.role)?.help
  const symbol = currency ? getCurrencySymbol(currency) : ''

  async function save() {
    if (localError) return
    setSaving(true)
    setError(null)
    try {
      onSaved(await saveInvestmentThesis(instrumentId, fieldsOf(draft, thesis, held)))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save your thesis. Try again.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="asd-thesis-form">
      {held && (
        <FormField label="Role" info={roleHelp}>
          <SegmentedControl<Role>
            ariaLabel="Role"
            options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
            value={draft.role ?? ('' as Role)}
            onChange={(v) => setDraft({ ...draft, role: v })}
          />
        </FormField>
      )}
      <FormField label={reasonLabel(held)} htmlFor={`${id}-reason`}>
        <GrowingText
          id={`${id}-reason`}
          maxLength={280}
          value={draft.reason}
          placeholder={reasonExample(held)}
          onChange={(e) => setDraft({ ...draft, reason: e.target.value })}
        />
      </FormField>
      {held && (
        <FormField label={BREAKS_IF_LABEL} htmlFor={`${id}-breaks`}>
          <GrowingText
            id={`${id}-breaks`}
            maxLength={280}
            value={draft.breaks_if}
            placeholder={BREAKS_IF_EXAMPLE}
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
            htmlFor={`${id}-${k}`}
            span={held && k === 'fair_value' ? 'full' : 1}
          >
            <PriceInput
              symbol={symbol}
              id={`${id}-${k}`}
              value={draft.prices[k]}
              invalid={localError?.field === k}
              onChange={(e) =>
                setDraft({ ...draft, prices: { ...draft.prices, [k]: priceText(e.target.value) } })
              }
              onBlur={() =>
                setDraft((d) => ({ ...d, prices: { ...d.prices, [k]: groupPrice(d.prices[k]) } }))
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
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" onClick={() => void save()} loading={saving}>
          Save thesis
        </Button>
      </div>
    </div>
  )
}

/** A text box that grows with its text — a 280-character note fits without a scrollbar or a
 *  resize grip in the narrow rail. */
function GrowingText(props: TextAreaProps) {
  const box = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }, [props.value])
  return <TextArea ref={box} rows={2} className="asd-thesis-text" {...props} />
}
