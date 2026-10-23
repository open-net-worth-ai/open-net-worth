// A transaction trigger's conditions: each picks a field (account, kind, amount, payee,
// description, memo), an operation that fits it, and a value. Every condition must hold.
// Stored as `when.conditions`; an older trigger's flat keys are shown as conditions too and
// rewritten as conditions on the first edit. Account, Kind and Payee pick several values (any
// of / none of them); conditions that can never all hold are flagged under the one that closes
// the trap.
import type { AutomationOption } from '../../../api/automations'
import { Cluster, Tag } from '../../ui'
import { Input, Row, Select } from './automationFormControls'
import { conditionConflicts } from './txnConditionConflicts'
import {
  CONDITION_FIELDS,
  FLAT_FILTER_KEYS,
  KIND_OPTIONS,
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
import { Plus } from '../../../ui/icons'

export function TransactionConditionFields({
  when,
  accounts,
  payees,
  baseCurrency,
  onChange,
}: {
  when: Record<string, unknown>
  accounts: AutomationOption[]
  payees: AutomationOption[]
  baseCurrency?: string
  onChange: (patch: Record<string, unknown>, path: string) => void
}) {
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
  const conflicts = conditionConflicts(conditions)
  return (
    <>
      {conditions.map((condition, index) => (
        <ConditionRows
          key={index}
          index={index}
          condition={condition}
          accounts={accounts}
          payees={payees}
          amountCurrency={amountCurrency}
          warnings={conflicts.filter((c) => c.index === index).map((c) => c.message)}
          onChange={(next) => update(index, next)}
          onRemove={() => write(conditions.filter((_, i) => i !== index))}
        />
      ))}
      <Row label={conditions.length ? '' : 'Conditions'} htmlFor="automation-add-condition">
        <button
          id="automation-add-condition"
          type="button"
          className="a3-linkbtn"
          onClick={() => write([...conditions, newCondition('payee')])}
        >
          <Plus size={13} aria-hidden="true" /> Add condition
        </button>
      </Row>
    </>
  )
}

function ConditionRows({
  index,
  condition,
  accounts,
  payees,
  amountCurrency,
  warnings,
  onChange,
  onRemove,
}: {
  index: number
  condition: TxnCondition
  accounts: AutomationOption[]
  payees: AutomationOption[]
  amountCurrency: string
  warnings: string[]
  onChange: (next: TxnCondition) => void
  onRemove: () => void
}) {
  const id = `automation-condition-${index}`
  const setValue = (value: TxnCondition['value']) => onChange({ ...condition, value })
  const several = takesSeveral(condition)
  const options = pickOptions(condition.field, accounts, payees)
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
  return (
    <>
      <Row label={index === 0 ? 'Where' : 'and'} htmlFor={`${id}-field`}>
        <div className="a3-cond-pair">
          <Select
            id={`${id}-field`}
            value={condition.field}
            options={CONDITION_FIELDS}
            onChange={(field) => onChange(newCondition(field as ConditionField))}
          />
          <Select
            id={`${id}-op`}
            value={condition.op}
            options={opsFor(condition.field, picked.length > 1)}
            onChange={(op) => onChange({ ...condition, op })}
          />
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
        {several ? (
          <Select
            id={`${id}-value`}
            value=""
            options={options.filter(([key]) => !picked.includes(key))}
            placeholder={pickPlaceholder(condition.field, picked.length > 0)}
            onChange={(value) => value && setValue(storedValues([...picked, value]))}
          />
        ) : (
          <ConditionValue id={`${id}-value`} condition={condition} onChange={setValue} />
        )}
      </Row>
    </>
  )
}

const NOUN: Partial<Record<ConditionField, string>> = {
  account: 'an account',
  kind: 'a kind',
  payee: 'a payee',
}

function pickPlaceholder(field: ConditionField, more: boolean) {
  const noun = NOUN[field] ?? 'a value'
  return more ? `Or ${noun.replace(/^an? /, 'another ')}…` : `Choose ${noun}`
}

function pickOptions(
  field: ConditionField,
  accounts: AutomationOption[],
  payees: AutomationOption[],
): ReadonlyArray<readonly [string, string]> {
  if (field === 'kind') return KIND_OPTIONS
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
