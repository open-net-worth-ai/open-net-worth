// The "Then" half of the Rule editor: what a matching transaction is changed to. Each action is
// a labelled row, left empty to do nothing; the toggles say how it treats a row that already
// has a category, and whether Rules after this one still run.
import type { AutomationOption } from '../../../../api/automations'
import { Checkbox, TagInput } from '../../../ui'
import { Input, OptionSelect, Row } from '../automationFormControls'
import type { RuleForm } from './ruleModel'

export interface RuleChoices {
  accounts: AutomationOption[]
  payees: AutomationOption[]
  categories: AutomationOption[]
}

export function RuleActionFields({
  form,
  choices,
  onChange,
}: {
  form: RuleForm
  choices: RuleChoices
  onChange: (patch: Partial<RuleForm>) => void
}) {
  const splitting = form.splits.length > 0
  return (
    <div className="a3-trows rl-trows">
      <Row label="Category" htmlFor="rule-category">
        <OptionSelect
          id="rule-category"
          value={form.category}
          options={choices.categories}
          placeholder={splitting ? 'Set per line below' : 'Leave as is'}
          onChange={(category) => onChange({ category })}
        />
      </Row>
      <Row label="Add tags" htmlFor="rule-tags">
        <div className="rl-tags">
          <TagInput
            id="rule-tags"
            value={form.tags}
            placeholder="Type a tag, press Enter"
            onChange={(tags) => onChange({ tags })}
          />
        </div>
      </Row>
      <Row label="Set payee" htmlFor="rule-payee">
        <OptionSelect
          id="rule-payee"
          value={form.payee}
          options={choices.payees}
          placeholder="Leave as is"
          onChange={(payee) => onChange({ payee })}
        />
      </Row>
      <Row label="Append to description" htmlFor="rule-note">
        <Input
          id="rule-note"
          value={form.noteAppend}
          placeholder="Added once, never repeated"
          onChange={(noteAppend) => onChange({ noteAppend })}
        />
      </Row>
      <Row label="Append to memo" htmlFor="rule-memo">
        <Input
          id="rule-memo"
          value={form.memoAppend}
          placeholder="Added once, never repeated"
          onChange={(memoAppend) => onChange({ memoAppend })}
        />
      </Row>
      <Row label="Route to account" htmlFor="rule-route">
        <OptionSelect
          id="rule-route"
          value={form.routeToAccount}
          options={choices.accounts}
          placeholder="Don't route"
          onChange={(routeToAccount) => onChange({ routeToAccount })}
        />
      </Row>
      <Row label="" htmlFor="rule-override">
        <Checkbox
          id="rule-override"
          label="Replace a category already set"
          description="Otherwise a transaction that already has a category keeps it."
          checked={form.override}
          onChange={(event) => onChange({ override: event.target.checked })}
        />
      </Row>
      <Row label="" htmlFor="rule-stop">
        <Checkbox
          id="rule-stop"
          label="Stop here"
          description="Rules below this one don't run on a transaction this Rule matched."
          checked={form.stop}
          onChange={(event) => onChange({ stop: event.target.checked })}
        />
      </Row>
    </div>
  )
}
