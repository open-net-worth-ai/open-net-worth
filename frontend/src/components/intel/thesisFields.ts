// One wording for an investment thesis's fields, used by ThesisEditor (the one form) and ThesisView
// (the one read view) — both shown on the asset page's Your Thesis card and in the alerts screen's
// Why you're watching.
import type { InvestmentThesis } from '../../types/assetDetail'

export type Role = NonNullable<InvestmentThesis['role']>

export const ROLES: { value: Role; label: string; help: string }[] = [
  { value: 'core', label: 'Core', help: 'The long-term base of your portfolio.' },
  { value: 'satellite', label: 'Satellite', help: 'An active bet around your core holdings.' },
  {
    value: 'speculative',
    label: 'Speculative',
    help: 'A small, high-risk bet you can afford to lose.',
  },
]

export type PriceKey = 'stop_below' | 'buy_below' | 'buy_above' | 'fair_value' | 'trim_above'

export const PRICE_LABELS: Record<PriceKey, string> = {
  stop_below: 'Stop below',
  trim_above: 'Trim above',
  buy_below: 'Buy at or under',
  buy_above: '…but not under',
  fair_value: 'Fair value',
}

/** The one line above a form's price fields saying what currency they're in — once, not on
 *  every label. */
export const pricesNote = (currency: string): string => `Prices in ${currency.toUpperCase()}`

export const reasonLabel = (held: boolean): string => (held ? 'Why you own it' : 'Why you’d buy it')

export const reasonExample = (held: boolean): string =>
  held
    ? 'e.g. Cloud revenue keeps growing 30%+ a year while margins hold. Holding 3+ years.'
    : 'e.g. Leader in AI chips, but too expensive today.'

export const BREAKS_IF_LABEL = 'Thesis breaks if'
export const BREAKS_IF_EXAMPLE = 'e.g. Cloud growth slows two quarters in a row, or the CEO leaves.'

/** What a price box keeps of a keystroke or paste: digits, commas (thousands, dropped when read)
 *  and one decimal point — letters never reach the field. */
export const priceText = (s: string): string => {
  const [whole = '', ...rest] = s.replace(/[^\d.,]/g, '').split('.')
  return rest.length ? `${whole}.${rest.join('')}` : whole
}

/** A typed price with thousands separators, for a box the user has left. Anything that isn't a
 *  plain positive number is returned as typed, so the error the editor shows still points at it. */
export const groupPrice = (s: string): string => {
  const text = s.replace(/,/g, '').trim()
  if (!/^\d+(\.\d*)?$/.test(text)) return s
  const [whole = '', frac] = text.split('.')
  const grouped = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return frac === undefined ? grouped : `${grouped}.${frac}`
}

export interface ThesisLevel {
  key: 'stop' | 'zone' | 'fair' | 'trim'
  label: string
  value: string
  at: number
}

/** The thesis's price levels in reading order; stop and trim belong to a holding only. */
export function thesisLevels(
  thesis: InvestmentThesis,
  held: boolean,
  fmt: (v: number) => string,
): ThesisLevel[] {
  const out: ThesisLevel[] = []
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

/** The role a holding's thesis names — watched stocks have none. */
export const thesisRole = (thesis: InvestmentThesis | null, held: boolean) =>
  held && thesis?.role ? ROLES.find((r) => r.value === thesis.role) : undefined

/** Whether a thesis has anything worth a read view — a role alone doesn't. */
export const thesisHasContent = (thesis: InvestmentThesis | null, held: boolean): boolean =>
  !!thesis &&
  (!!thesis.reason || (held && !!thesis.breaks_if) || thesisLevels(thesis, held, String).length > 0)
