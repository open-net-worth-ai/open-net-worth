// The "Then" half of the Rule editor: only the actions this Rule uses, each a small block with its
// label on top, added from "Add action" and removed with ×. The two options under them say how a
// row that already has a category is treated, and whether Rules after this one still run.
import { useState } from 'react'
import type { AutomationOption } from '../../../../api/automations'
import { Plus, X } from '../../../../ui/icons'
import { Checkbox } from '../../../ui'
import { TagField } from '../../accounts/TagField'
import { AutomationMenu } from '../AutomationMenu'
import { Input, OptionSelect } from '../automationFormControls'
import { RuleSplitFields } from './RuleSplitFields'
import { blankSplit, type RuleForm } from './ruleModel'

export interface RuleChoices {
  accounts: AutomationOption[]
  payees: AutomationOption[]
  categories: AutomationOption[]
}

type ActionKey = 'category' | 'split' | 'tags' | 'payee' | 'note' | 'memo' | 'route'

const ACTIONS: ReadonlyArray<readonly [ActionKey, string]> = [
  ['category', 'Set category'],
  ['split', 'Split into lines'],
  ['tags', 'Add tags'],
  ['payee', 'Set payee'],
  ['note', 'Add to description'],
  ['memo', 'Add to memo'],
  ['route', 'Move to account'],
]

/** What clearing an action writes back to the form. */
const CLEARED: Record<ActionKey, Partial<RuleForm>> = {
  category: { category: '' },
  split: { splits: [] },
  tags: { tags: [] },
  payee: { payee: '' },
  note: { noteAppend: '' },
  memo: { memoAppend: '' },
  route: { routeToAccount: '' },
}

function actionInUse(form: RuleForm, key: ActionKey): boolean {
  switch (key) {
    case 'category':
      return Boolean(form.category)
    case 'split':
      return form.splits.length > 0
    case 'tags':
      return form.tags.length > 0
    case 'payee':
      return Boolean(form.payee)
    case 'note':
      return Boolean(form.noteAppend.trim())
    case 'memo':
      return Boolean(form.memoAppend.trim())
    case 'route':
      return Boolean(form.routeToAccount)
  }
}

// A split sets the category per line, so a Rule either sets one category or splits.
const EXCLUSIVE: Partial<Record<ActionKey, ActionKey>> = { category: 'split', split: 'category' }

export function RuleActionFields({
  form,
  choices,
  onChange,
}: {
  form: RuleForm
  choices: RuleChoices
  onChange: (patch: Partial<RuleForm>) => void
}) {
  // A brand-new rule opens with "Set category" ready — the action almost every rule has.
  const [shown, setShown] = useState<ActionKey[]>(() => {
    const wanted = new Set(form.showActions ?? [])
    const used = ACTIONS.map(([key]) => key).filter(
      (key) => actionInUse(form, key) || wanted.has(key),
    )
    return used.length > 0 ? used : ['category']
  })
  const visible = ACTIONS.filter(([key]) => shown.includes(key))
  const addable = ACTIONS.filter(([key]) => {
    const other = EXCLUSIVE[key]
    return !shown.includes(key) && !(other && shown.includes(other))
  })
  const add = (key: ActionKey) => {
    setShown((current) => [...current, key])
    if (key === 'split' && form.splits.length === 0) {
      onChange({ splits: [blankSplit('fixed'), blankSplit('remainder')] })
    }
  }
  const remove = (key: ActionKey) => {
    setShown((current) => current.filter((k) => k !== key))
    onChange(CLEARED[key])
  }
  const categorised = shown.includes('category') || shown.includes('split')

  return (
    <div className="rl-actions">
      {visible.length === 0 && (
        <p className="a3-muted rl-empty">Add what this rule does to a matching transaction.</p>
      )}
      {visible.map(([key, label]) => (
        <div className="rl-action" key={key}>
          <div className="rl-action-head">
            <label className="a3-field-label" htmlFor={`rule-${key}`}>
              {label}
            </label>
            <button
              type="button"
              className="rl-icon-btn"
              aria-label={`Remove ${label.toLocaleLowerCase()}`}
              onClick={() => remove(key)}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
          <ActionControl actionKey={key} form={form} choices={choices} onChange={onChange} />
        </div>
      ))}
      {addable.length > 0 && (
        <AutomationMenu
          label="Add action"
          triggerClassName="a3-linkbtn"
          popoverClassName="rl-add-pop"
          trigger={
            <>
              <Plus size={13} aria-hidden="true" /> Add action
            </>
          }
          items={addable.map(([key, label]) => ({ label, onSelect: () => add(key) }))}
        />
      )}
      {visible.length > 0 && (
        <div className="rl-options">
          {categorised && (
            <Checkbox
              id="rule-override"
              label="Replace a category already set"
              description="Otherwise a transaction that already has one keeps it."
              checked={form.override}
              onChange={(event) => onChange({ override: event.target.checked })}
            />
          )}
          <Checkbox
            id="rule-stop"
            label="Stop after this rule"
            description="Rules below it skip a transaction this one matched."
            checked={form.stop}
            onChange={(event) => onChange({ stop: event.target.checked })}
          />
        </div>
      )}
    </div>
  )
}

function ActionControl({
  actionKey,
  form,
  choices,
  onChange,
}: {
  actionKey: ActionKey
  form: RuleForm
  choices: RuleChoices
  onChange: (patch: Partial<RuleForm>) => void
}) {
  const id = `rule-${actionKey}`
  switch (actionKey) {
    case 'category':
      return (
        <OptionSelect
          id={id}
          value={form.category}
          options={choices.categories}
          placeholder="Choose a category"
          needed={!form.category}
          onChange={(category) => onChange({ category })}
        />
      )
    case 'split':
      return (
        <RuleSplitFields
          form={form}
          choices={choices}
          onChange={(splits) => onChange({ splits })}
        />
      )
    case 'tags':
      return (
        <TagField
          id={id}
          value={form.tags}
          placeholder="Search or create a tag…"
          onChange={(tags) => onChange({ tags })}
        />
      )
    case 'payee':
      return (
        <OptionSelect
          id={id}
          value={form.payee}
          options={choices.payees}
          placeholder="Choose a payee"
          needed={!form.payee}
          onChange={(payee) => onChange({ payee })}
        />
      )
    case 'note':
      return (
        <Input
          id={id}
          value={form.noteAppend}
          placeholder="Text added to the end, once"
          onChange={(noteAppend) => onChange({ noteAppend })}
        />
      )
    case 'memo':
      return (
        <Input
          id={id}
          value={form.memoAppend}
          placeholder="Text added to the end, once"
          onChange={(memoAppend) => onChange({ memoAppend })}
        />
      )
    case 'route':
      return (
        <>
          <OptionSelect
            id={id}
            value={form.routeToAccount}
            options={choices.accounts}
            placeholder="Choose an account"
            needed={!form.routeToAccount}
            onChange={(routeToAccount) => onChange({ routeToAccount })}
          />
          <span className="a3-muted rl-action-note">
            Applies to imported transactions while they are still in review.
          </span>
        </>
      )
  }
}
