// One wording for an investment thesis's fields, used by ThesisEditor (the one form, shown on the
// asset page's Your Thesis card and in the alerts screen's Why you're watching) and the card's
// read view.
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
