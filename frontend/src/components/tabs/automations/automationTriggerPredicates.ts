// What the condition editor can show and write: anything else (an older rule's regex, a
// direction, a non-create operation…) makes the trigger Custom — shown exactly, edited via Lucius.
const EDITABLE_TRANSACTION_KEYS = new Set([
  'operation',
  'account',
  'account_id',
  'kind',
  'over',
  'max_amount',
  'contains',
  'conditions',
  'match',
])

export interface TransactionTriggerView {
  custom: boolean
  exactConditions: string
}

export function transactionTriggerView(when: Record<string, unknown>): TransactionTriggerView {
  const active = Object.entries(when).filter(([, value]) => predicateIsActive(value))
  const accountsConflict =
    predicateIsActive(when.account) &&
    predicateIsActive(when.account_id) &&
    String(when.account) !== String(when.account_id)
  const custom =
    accountsConflict ||
    active.some(([key, value]) =>
      key === 'operation' ? value !== 'create' : !EDITABLE_TRANSACTION_KEYS.has(key),
    )
  return {
    custom,
    exactConditions: active.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(' • '),
  }
}

function predicateIsActive(value: unknown) {
  return (
    value !== null &&
    value !== undefined &&
    value !== '' &&
    (!Array.isArray(value) || value.length > 0)
  )
}

/** The currency an event trigger's amount is in, as the backend compares it: a balance in its
 *  account's currency; a transaction amount in the trigger's own currency if it names one, else
 *  the chosen account's, else the base currency (the one unit every account shares). */
export function triggerAmountCurrency(
  event: string,
  when: Record<string, unknown>,
  options: {
    accounts: Array<{ id: string; currency?: string }>
    instruments?: Array<{ id: string; label: string; currency?: string }>
    base_currency?: string
  },
): string {
  if (event === 'price') return instrumentCurrency(options.instruments ?? [], when.symbol)
  const accountId = String(when.account ?? when.account_id ?? '')
  const accountCurrency = options.accounts.find((a) => a.id === accountId)?.currency
  if (event === 'balance') return accountCurrency ?? ''
  if (typeof when.currency === 'string' && when.currency) return when.currency.toUpperCase()
  return (accountId ? accountCurrency : options.base_currency) ?? ''
}

export function moneyLabel(currency: string, amount: unknown): string {
  const figure = Number(amount).toLocaleString()
  return currency ? `${currency} ${figure}` : figure
}

/** A price is in its ticker's own quote currency. `symbol` is an instrument id or a ticker. */
export function instrumentCurrency(
  instruments: Array<{ id: string; label: string; currency?: string }>,
  symbol: unknown,
): string {
  const key = String(symbol ?? '')
    .trim()
    .toLocaleLowerCase()
  if (!key) return ''
  const hit = instruments.find(
    (i) => i.id.toLocaleLowerCase() === key || i.label.toLocaleLowerCase() === key,
  )
  return hit?.currency ?? ''
}
