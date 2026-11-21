// A transaction trigger's conditions — the shape stored in `when.conditions` and the editor's
// reading of it. Kept apart from the component so fast refresh sees a components-only file.
export type ConditionField = 'account' | 'kind' | 'amount' | 'payee' | 'description' | 'memo'
export interface TxnCondition {
  field: ConditionField
  op: string
  /** `between` stores `[low, high]` (either may be '' while it is being typed). */
  value: string | number | string[] | Array<number | ''>
}

export const CONDITION_FIELDS: ReadonlyArray<readonly [ConditionField, string]> = [
  ['account', 'Account'],
  ['kind', 'Kind'],
  ['amount', 'Amount'],
  ['payee', 'Payee'],
  ['description', 'Description'],
  ['memo', 'Memo'],
]
const IS_OPS = [
  ['is', 'is'],
  ['is_not', 'is not'],
] as const
const IS_ANY_OPS = [
  ['is', 'is any of'],
  ['is_not', 'is none of'],
] as const
const AMOUNT_OPS = [
  ['over', 'over'],
  ['under', 'under'],
  ['is', 'is'],
  ['between', 'between'],
  ['approx', 'is about (±7.5%)'],
] as const
const TEXT_OPS = [
  ['contains', 'contains'],
  ['not_contains', "doesn't contain"],
  ['starts_with', 'starts with'],
  ['regex', 'matches regex'],
] as const
export const KIND_OPTIONS: ReadonlyArray<readonly [string, string]> = [
  ['expense', 'Expense'],
  ['income', 'Income'],
  ['transfer', 'Transfer'],
  ['investment', 'Investment'],
  ['investment_buy', 'Investment: buy'],
  ['investment_sale', 'Investment: sale'],
  ['adjustment', 'Adjustment'],
]

/** `several`: the condition holds more than one value, so "is" reads as "is any of". */
export function opsFor(
  field: ConditionField,
  several = false,
): ReadonlyArray<readonly [string, string]> {
  if (field === 'amount') return AMOUNT_OPS
  if (field === 'description' || field === 'memo') return TEXT_OPS
  return several ? IS_ANY_OPS : IS_OPS
}

/** Account, Kind and Payee "is" / "is not" take several values: any of / none of them. */
export function takesSeveral(condition: TxnCondition): boolean {
  return (
    (condition.field === 'account' || condition.field === 'kind' || condition.field === 'payee') &&
    (condition.op === 'is' || condition.op === 'is_not')
  )
}

/** The picked values of an is / is-not condition — one stored plainly, several as a list. */
export function conditionValues(value: TxnCondition['value']): string[] {
  const items = Array.isArray(value) ? value : [value]
  return items.map((item) => String(item ?? '')).filter(Boolean)
}

/** The stored shape: nothing → '', one → the value, several → the list. */
export function storedValues(values: string[]): TxnCondition['value'] {
  if (values.length === 0) return ''
  return values.length === 1 ? values[0]! : values
}

export const CONDITION_OP_LABEL: Record<string, string> = Object.fromEntries([
  ...IS_OPS,
  ...AMOUNT_OPS,
  ...TEXT_OPS,
])

export function newCondition(field: ConditionField): TxnCondition {
  return { field, op: opsFor(field)[0]?.[0] ?? 'is', value: '' }
}

/** Keys an older trigger used for what are now conditions. The editor shows them as conditions
 *  and, on any edit, writes conditions in their place. */
export const FLAT_FILTER_KEYS = [
  'account',
  'account_id',
  'kind',
  'over',
  'max_amount',
  'contains',
] as const

/** Every condition the trigger applies: its older flat keys first (an account, a kind, an
 *  amount, the old free-text box read as "Description contains" — the only field it ever really
 *  matched), then the stored conditions. */
export function readConditions(when: Record<string, unknown>): TxnCondition[] {
  const out: TxnCondition[] = []
  const account = when.account ?? when.account_id
  if (account) out.push({ field: 'account', op: 'is', value: String(account) })
  if (typeof when.kind === 'string' && when.kind) {
    out.push({ field: 'kind', op: 'is', value: when.kind })
  }
  if (when.over !== undefined && when.over !== null && when.over !== '') {
    out.push({ field: 'amount', op: 'over', value: Number(when.over) })
  }
  if (when.max_amount !== undefined && when.max_amount !== null && when.max_amount !== '') {
    out.push({ field: 'amount', op: 'under', value: Number(when.max_amount) })
  }
  if (typeof when.contains === 'string' && when.contains.trim()) {
    out.push({ field: 'description', op: 'contains', value: when.contains })
  }
  const stored = Array.isArray(when.conditions) ? when.conditions : []
  for (const item of stored) {
    if (item && typeof item === 'object') out.push(item as TxnCondition)
  }
  return out
}

export function conditionValueText(value: TxnCondition['value']): string {
  return Array.isArray(value) ? value.join(', ') : String(value ?? '')
}

/** A `between` amount's `[low, high]`, '' for a bound not typed yet. */
export function betweenBounds(value: TxnCondition['value']): [number | '', number | ''] {
  const [low, high] = Array.isArray(value) ? value : []
  return [
    low === undefined ? '' : (low as number | ''),
    high === undefined ? '' : (high as number | ''),
  ]
}

export function conditionIsComplete(condition: TxnCondition): boolean {
  if (condition.op === 'between') return betweenBounds(condition.value).every((n) => n !== '')
  return conditionValueText(condition.value).trim() !== ''
}

/** The account an amount is measured on: the one "Account is" names, when it names exactly
 *  one. Several (any of them, or a transfer between two) share no currency but the base. */
export function conditionAccount(conditions: TxnCondition[]): string | null {
  const named = new Set(
    conditions
      .filter((c) => c.field === 'account' && c.op === 'is')
      .flatMap((c) => conditionValues(c.value)),
  )
  return named.size === 1 ? [...named][0]! : null
}

/** "Grab", "Grab or Uber", "Grab, Uber or Gojek". */
export function joinOr(items: string[]): string {
  if (items.length < 2) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} or ${items.at(-1)}`
}

const KIND_SUBJECT: Record<string, string> = {
  expense: 'an expense',
  income: 'income',
  transfer: 'a transfer',
  investment: 'an investment',
  investment_buy: 'an investment buy',
  investment_sale: 'an investment sale',
  adjustment: 'an adjustment',
}

interface LabelOptions {
  accounts: Array<{ id: string; label: string; currency?: string }>
  payees?: Array<{ id: string; label: string }>
  base_currency?: string
}

function nameOf(list: Array<{ id: string; label: string }>, id: unknown, fallback: string) {
  return list.find((item) => item.id === String(id))?.label ?? fallback
}

function namesOf(
  list: Array<{ id: string; label: string }>,
  value: TxnCondition['value'],
  fallback: string,
) {
  const values = conditionValues(value)
  return values.length ? joinOr(values.map((id) => nameOf(list, id, fallback))) : fallback
}

function kindName(kind: string) {
  return (KIND_OPTIONS.find(([k]) => k === kind)?.[1] ?? kind).toLowerCase()
}

const money = (currency: string, value: unknown) =>
  currency ? `${currency} ${Number(value).toLocaleString()}` : Number(value).toLocaleString()

function amountPhrase(c: TxnCondition, currency: string): string {
  if (c.op === 'between') {
    const [low, high] = betweenBounds(c.value)
    return `between ${money(currency, low)} and ${money(currency, high)}`
  }
  if (c.op === 'is') return `of ${money(currency, c.value)}`
  if (c.op === 'approx') return `of about ${money(currency, c.value)}`
  return `${c.op} ${money(currency, c.value)}`
}

/** "When an expense over USD 500 posts on USD card, payee is Grab or Uber". Amounts carry the
 *  currency they are compared in: the one "Account is" account's, else the base currency. */
export function transactionLabel(conditions: TxnCondition[], options: LabelOptions): string {
  const account = conditionAccount(conditions)
  const currency =
    (account ? options.accounts.find((a) => a.id === account)?.currency : options.base_currency) ??
    ''
  const kind = conditions.find((c) => c.field === 'kind' && c.op === 'is')
  const subjects = conditionValues(kind?.value ?? '').map((k) => KIND_SUBJECT[k] ?? kindName(k))
  let label = `When ${subjects.length ? joinOr(subjects) : 'a transaction'}`
  for (const c of conditions.filter((c) => c.field === 'amount')) {
    label += ` ${amountPhrase(c, currency)}`
  }
  label += ' posts'
  const rest: string[] = []
  for (const c of conditions) {
    if (c === kind || c.field === 'amount') continue
    if (c.field === 'account') {
      label += ` ${c.op === 'is' ? 'on' : 'not on'} ${namesOf(options.accounts, c.value, 'an account')}`
      continue
    }
    const value =
      c.field === 'payee'
        ? namesOf(options.payees ?? [], c.value, 'a payee')
        : c.field === 'kind'
          ? joinOr(conditionValues(c.value).map(kindName))
          : `“${conditionValueText(c.value)}”`
    rest.push(`${c.field} ${CONDITION_OP_LABEL[c.op] ?? c.op} ${value}`)
  }
  return rest.length ? `${label}, ${rest.join(', ')}` : label
}
