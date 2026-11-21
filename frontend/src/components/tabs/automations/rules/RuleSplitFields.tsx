// "Split into lines": one transaction becomes several, each line a fixed amount, a percent of
// what is left after the fixed lines, or the remainder (which also takes any rounding). Lines
// are computed on the amount the transaction moved, in its own currency.
import { Plus, Trash2 } from '../../../../ui/icons'
import { TagInput } from '../../../ui'
import { OptionSelect, Select } from '../automationFormControls'
import {
  MAX_SPLIT_LINES,
  blankSplit,
  type RuleForm,
  type SplitForm,
  type SplitKind,
} from './ruleModel'
import type { RuleChoices } from './RuleActionFields'

const KINDS: ReadonlyArray<readonly [SplitKind, string]> = [
  ['fixed', 'Fixed amount'],
  ['percent', 'Percent of the rest'],
  ['remainder', 'The remainder'],
]

export function RuleSplitFields({
  form,
  choices,
  onChange,
}: {
  form: RuleForm
  choices: RuleChoices
  onChange: (splits: SplitForm[]) => void
}) {
  const lines = form.splits
  const update = (index: number, patch: Partial<SplitForm>) =>
    onChange(lines.map((line, i) => (i === index ? { ...line, ...patch } : line)))
  return (
    <section className="rl-splits" aria-label="Split into lines">
      {lines.length === 0 && (
        <p className="a3-muted rl-splits-empty">
          Split the matching transaction across categories — for example 30 to one and the rest to
          another.
        </p>
      )}
      {lines.map((line, index) => (
        <div className="rl-split" key={index} role="group" aria-label={`Line ${index + 1}`}>
          <span className="rl-split-n">{index + 1}</span>
          <div className="rl-split-fields">
            <div className="rl-split-top">
              <Select
                id={`rule-split-${index}-kind`}
                value={line.kind}
                options={KINDS}
                onChange={(kind) => update(index, { kind: kind as SplitKind })}
              />
              {line.kind !== 'remainder' && (
                <input
                  id={`rule-split-${index}-value`}
                  type="number"
                  min={0}
                  max={line.kind === 'percent' ? 100 : undefined}
                  className="a3-mini-select rl-split-value"
                  aria-label={line.kind === 'percent' ? 'Percent' : 'Amount'}
                  value={line.value}
                  onChange={(event) =>
                    update(index, {
                      value: event.target.value === '' ? '' : Number(event.target.value),
                    })
                  }
                />
              )}
              <span className="rl-split-unit">{line.kind === 'percent' ? '%' : ''}</span>
              <OptionSelect
                id={`rule-split-${index}-category`}
                value={line.category}
                options={choices.categories}
                placeholder="Category"
                needed={!line.category}
                onChange={(category) => update(index, { category })}
              />
              <button
                type="button"
                className="rl-icon-btn"
                aria-label={`Remove line ${index + 1}`}
                onClick={() => onChange(lines.filter((_, i) => i !== index))}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
            <div className="rl-split-bottom">
              <TagInput
                value={line.tags}
                placeholder="Tags for this line"
                onChange={(tags) => update(index, { tags })}
              />
              <input
                className="a3-mini-select"
                aria-label={`Line ${index + 1} memo`}
                placeholder="Memo for this line"
                value={line.memo}
                onChange={(event) => update(index, { memo: event.target.value })}
              />
            </div>
          </div>
        </div>
      ))}
      <button
        type="button"
        className="a3-linkbtn"
        disabled={lines.length >= MAX_SPLIT_LINES}
        onClick={() => onChange([...lines, blankSplit(lines.length === 0 ? 'fixed' : 'remainder')])}
      >
        <Plus size={13} aria-hidden="true" /> Add a line
      </button>
    </section>
  )
}
