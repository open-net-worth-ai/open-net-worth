// One wording for an investment thesis's fields, shared by the asset page's Your Thesis card and
// the alerts screen's "Why you're watching" form — both edit the same record.

export type PriceKey = 'stop_below' | 'buy_below' | 'buy_above' | 'fair_value' | 'trim_above'

export const PRICE_LABELS: Record<PriceKey, string> = {
  stop_below: 'Stop below',
  trim_above: 'Trim above',
  buy_below: 'Buy at or under',
  buy_above: '…but not under',
  fair_value: 'Fair value',
}

/** A price field's label with the currency its figure is in, e.g. "Fair value (€)". */
export const priceLabel = (key: PriceKey, symbol?: string): string =>
  symbol ? `${PRICE_LABELS[key]} (${symbol})` : PRICE_LABELS[key]

export const reasonLabel = (held: boolean): string => (held ? 'Why you own it' : 'Why you’d buy it')

export const reasonExample = (held: boolean): string =>
  held
    ? 'e.g. Cloud revenue keeps growing 30%+ a year while margins hold.'
    : 'e.g. Leader in AI chips, but too expensive today.'

export const BREAKS_IF_LABEL = 'Thesis breaks if'
export const BREAKS_IF_EXAMPLE = 'e.g. Cloud growth slows two quarters in a row, or the CEO leaves.'

/** What a price box keeps of a keystroke or paste: digits, commas (thousands, dropped when read)
 *  and one decimal point — letters never reach the field. */
export const priceText = (s: string): string => {
  const [whole = '', ...rest] = s.replace(/[^\d.,]/g, '').split('.')
  return rest.length ? `${whole}.${rest.join('')}` : whole
}
