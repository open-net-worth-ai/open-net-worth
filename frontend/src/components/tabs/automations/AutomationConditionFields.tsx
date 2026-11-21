// A transaction trigger's conditions: each picks a field (account, kind, amount, payee,
// description, memo), an operation that fits it, and a value. Every condition must hold.
// Stored as `when.conditions`; an older trigger's flat keys are shown as conditions too and
// rewritten as conditions on the first edit. Account, Kind and Payee pick several values (any
// of / none of them); conditions that can never all hold are flagged under the one that closes
// the trap. A Rule also says whether ALL or ANY of them must hold (`match`); the flag-off Lucius
// trigger never passes it, so it stays "all". Conflicts are only a trap for "all".
import type { AutomationOption } from '../../../api/automations'
import { Cluster, Tag } from '../../ui'
import { slugTag } from '../accounts/categories'
import { ComboSelect } from './ComboSelect'
import { useConditionChoices } from './conditionChoices'
import { Input, Row, Select } from './automationFormControls'
import { conditionConflicts } from './txnConditionConflicts'
import {
  CONDITION_FIELDS,
  betweenBounds,
  FLAT_FILTER_KEYS,
  KIND_OPTIONS,
  UNCATEGORIZED,
  conditionAccount,
  conditionValueText,
  conditionValues,
  newCondition,
  opsFor,
  readConditions,
  storedValues,
  takesSeveral,
  type ConditionField,
  type TxnCondition,
} from './txnConditions'
import { Plus, X } from '../../../ui/icons'

export function TransactionConditionFields({
  when,
  accounts,
  payees,
  baseCurrency,
  match = 'all',
  layout = 'rows',
  categories,
  tags,
  onChange,
}: {
  when: Record<string, unknown>
  accounts: AutomationOption[]
  payees: AutomationOption[]
  /** The owner's categories and tags: with them, Category and Tags can be conditions too. */
  categories?: AutomationOption[]
  tags?: string[]
  baseCurrency?: string
  match?: 'all' | 'any'
  /** `inline`: each condition on one line, labels on top of nothing (the Rule editor). */
  layout?: 'rows' | 'inline'
  onChange: (patch: Record<string, unknown>, path: string) => void
}) {
  // Given by the Rule editor; otherwise read here, so a Lucius trigger offers them too.
  const loaded = useConditionChoices()
  const categoryChoices = categories ?? loaded.categories
  const tagChoices = tags ?? loaded.tags
  const conditions = readConditions(when)
  const write = (next: TxnCondition[]) =>
    onChange(
      {
        ...Object.fromEntries(FLAT_FILTER_KEYS.map((key) => [key, undefined])),
        conditions: next.length ? next : undefined,
      },
      'trigger.when.conditions',
    )
  const update = (index: number, next: TxnCondition) =>
    write(conditions.map((c, i) => (i === index ? next : c)))
  const account = conditionAccount(conditions)
  const amountCurrency =
    (account ? accounts.find((a) => a.id === account)?.currency : baseCurrency) ?? ''
  const conflicts = match === 'any' ? [] : conditionConflicts(conditions)
  const addButton = (
    <button
      id="automation-add-condition"
      type="button"
      className="a3-linkbtn"
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => write([...conditions, newCondition('payee')])}
    >
      <Plus size={13} aria-hidden="true" /> Add condition
    </button>
  )
  return (
    <>
      {conditions.map((condition, index) => (
        <ConditionRows
          key={index}
          index={index}
          match={match}
          inline={layout === 'inline'}
          condition={condition}
          accounts={accounts}
          payees={payees}
          categories={categoryChoices}
          tags={tagChoices}
          amountCurrency={amountCurrency}
          warnings={conflicts.filter((c) => c.index === index).map((c) => c.message)}
          onChange={(next) => update(index, next)}
          onRemove={() => write(conditions.filter((_, i) => i !== index))}
        />
      ))}
      {layout === 'inline' ? (
        addButton
      ) : (
        <Row label={conditions.length ? '' : 'Conditions'} htmlFor="automation-add-condition">
          {addButton}
        </Row>
      )}
    </>
  )
}

function ConditionRows({
  index,
  match,
  inline,
  condition,
  accounts,
  payees,
  categories,
  tags,
  amountCurrency,
  warnings,
  onChange,
  onRemove,
}: {
  index: number
  match: 'all' | 'any'
  inline: boolean
  condition: TxnCondition
  accounts: AutomationOption[]
  payees: AutomationOption[]
  categories?: AutomationOption[]
  tags?: string[]
  amountCurrency: string
  warnings: string[]
  onChange: (next: TxnCondition) => void
  onRemove: () => void
}) {
  const id = `automation-condition-${index}`
  const setValue = (value: TxnCondition['value']) => onChange({ ...condition, value })
  const several = takesSeveral(condition)
  const options = pickOptions(condition.field, accounts, payees, categories, tags)
  const fields = CONDITION_FIELDS
  const picked = several ? conditionValues(condition.value) : []
  const below = (
    <>
      {(condition.op === 'contains' || condition.op === 'not_contains') && (
        <span className="a3-muted">Separate several with commas — any of them counts.</span>
      )}
      {picked.length > 0 && (
        <Cluster gap="--space-2">
          {picked.map((value) => {
            const label = options.find(([key]) => key === value)?.[1] ?? value
            return (
              <Tag
                key={value}
                removeLabel={`Remove ${label}`}
                onRemove={() => setValue(storedValues(picked.filter((v) => v !== value)))}
              >
                {label}
              </Tag>
            )
          })}
        </Cluster>
      )}
      {warnings.map((warning) => (
        <span key={warning} className="a3-inline-error" role="status">
          {warning}
        </span>
      ))}
    </>
  )
  const hasBelow =
    condition.op === 'contains' ||
    condition.op === 'not_contains' ||
    picked.length > 0 ||
    warnings.length > 0
  const fieldSelect = (
    <Select
      id={`${id}-field`}
      value={condition.field}
      options={fields}
      onChange={(field) => onChange(newCondition(field as ConditionField))}
    />
  )
  const opSelect = (
    <Select
      id={`${id}-op`}
      value={condition.op}
      options={opsFor(condition.field, picked.length > 1)}
      onChange={(op) => onChange({ ...condition, op, value: reshapeValue(condition, op) })}
    />
  )
  const valueControl =
    several && inline ? (
      // Several values are chips inside the one field; the list stays open to add another.
      <ComboSelect
        id={`${id}-value`}
        multiple
        value={picked}
        options={options}
        placeholder={pickPlaceholder(condition.field, false)}
        colors={condition.field === 'category' ? colorsOf(categories) : undefined}
        onChangeMany={(values) => setValue(storedValues(values))}
        onCreate={condition.field === 'tags' ? async (name) => slugTag(name) || null : undefined}
      />
    ) : several ? (
      <Select
        id={`${id}-value`}
        value=""
        options={options.filter(([key]) => !picked.includes(key))}
        placeholder={pickPlaceholder(condition.field, picked.length > 0)}
        onChange={(value) => value && setValue(storedValues([...picked, value]))}
      />
    ) : (
      <ConditionValue id={`${id}-value`} condition={condition} onChange={setValue} />
    )
  if (inline) {
    // The chips live inside the field here, so only the hint and any warning sit below.
    const inlineBelow =
      condition.op === 'contains' || condition.op === 'not_contains' || warnings.length ? (
        <>
          {(condition.op === 'contains' || condition.op === 'not_contains') && (
            <span className="a3-muted">Separate several with commas — any of them counts.</span>
          )}
          {warnings.map((warning) => (
            <span key={warning} className="a3-inline-error" role="status">
              {warning}
            </span>
          ))}
        </>
      ) : null
    return (
      <div className="rl-cond" role="group" aria-label={`Condition ${index + 1}`}>
        <div className="rl-cond-line">
          {fieldSelect}
          {opSelect}
          <span className="rl-cond-value">
            {valueControl}
            {condition.field === 'amount' && amountCurrency && (
              <span className="a3-trow-unit">{amountCurrency}</span>
            )}
          </span>
          <button
            type="button"
            className="rl-icon-btn"
            aria-label={`Remove condition ${index + 1}`}
            onClick={onRemove}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
        {inlineBelow && <div className="rl-cond-below">{inlineBelow}</div>}
      </div>
    )
  }
  return (
    <>
      <Row label={index === 0 ? 'Where' : match === 'any' ? 'or' : 'and'} htmlFor={`${id}-field`}>
        <div className="a3-cond-pair">
          {fieldSelect}
          {opSelect}
        </div>
        <button type="button" className="a3-linkbtn" onClick={onRemove}>
          Remove
        </button>
      </Row>
      <Row
        label=""
        htmlFor={`${id}-value`}
        unit={condition.field === 'amount' ? amountCurrency : undefined}
        below={hasBelow ? below : undefined}
      >
        {valueControl}
      </Row>
    </>
  )
}

/** Category id → colour (Uncategorized takes the ledger's grey). */
function colorsOf(categories: AutomationOption[] = []): Record<string, string> {
  const colors: Record<string, string> = { [UNCATEGORIZED]: '#7c8595' }
  for (const option of categories as Array<AutomationOption & { color?: string }>) {
    if (option.color) colors[option.id] = option.color
  }
  return colors
}

/** A payee picked from the list and a payee name typed as text are different values: switching
 *  between them starts the value over. */
function reshapeValue(condition: TxnCondition, op: string): TxnCondition['value'] {
  const text = (o: string) => o === 'contains' || o === 'not_contains'
  if (condition.field === 'payee' && text(op) !== text(condition.op)) return ''
  return reshapeAmount(condition, op)
}

/** Switching an amount to or from `between` carries the first figure across. */
function reshapeAmount(condition: TxnCondition, op: string): TxnCondition['value'] {
  if (condition.field !== 'amount') return condition.value
  const stored = condition.value
  if (op === 'between')
    return Array.isArray(stored) ? stored : [stored === '' ? '' : Number(stored), '']
  return Array.isArray(stored) ? betweenBounds(stored)[0] : stored
}

const NOUN: Partial<Record<ConditionField, string>> = {
  account: 'an account',
  kind: 'a kind',
  payee: 'a payee',
  category: 'a category',
  tags: 'a tag',
}

function pickPlaceholder(field: ConditionField, more: boolean) {
  const noun = NOUN[field] ?? 'a value'
  return more ? `Or ${noun.replace(/^an? /, 'another ')}…` : `Choose ${noun}`
}

function pickOptions(
  field: ConditionField,
  accounts: AutomationOption[],
  payees: AutomationOption[],
  categories: AutomationOption[] = [],
  tags: string[] = [],
): ReadonlyArray<readonly [string, string]> {
  if (field === 'kind') return KIND_OPTIONS
  if (field === 'tags') return tags.map((tag) => [tag, tag] as const)
  if (field === 'category') {
    return [
      [UNCATEGORIZED, 'Uncategorized'] as const,
      ...categories.map((option) => [option.id, option.label] as const),
    ]
  }
  const list = field === 'account' ? accounts : field === 'payee' ? payees : []
  return list.map((option) => [option.id, option.label] as const)
}

function ConditionValue({
  id,
  condition,
  onChange,
}: {
  id: string
  condition: TxnCondition
  onChange: (value: TxnCondition['value']) => void
}) {
  if (condition.field === 'amount' && condition.op === 'between') {
    const [low, high] = betweenBounds(condition.value)
    const set = (next: [number | '', number | '']) => onChange(next)
    const num = (value: string) => (value === '' ? '' : Number(value))
    return (
      <span className="a3-cond-between">
        <Input
          id={`${id}-low`}
          type="number"
          min={0}
          value={low}
          placeholder="From"
          onChange={(value) => set([num(value), high])}
        />
        <span className="a3-muted">to</span>
        <Input
          id={id}
          type="number"
          min={0}
          value={high}
          placeholder="To"
          onChange={(value) => set([low, num(value)])}
        />
      </span>
    )
  }
  if (condition.field === 'amount') {
    return (
      <Input
        id={id}
        type="number"
        min={0}
        value={condition.value}
        onChange={(value) => onChange(value === '' ? '' : Number(value))}
      />
    )
  }
  return (
    <Input
      id={id}
      value={conditionValueText(condition.value)}
      placeholder={condition.op === 'regex' ? '^GRAB\\*' : 'grabfood, ubereats'}
      onChange={onChange}
    />
  )
}
