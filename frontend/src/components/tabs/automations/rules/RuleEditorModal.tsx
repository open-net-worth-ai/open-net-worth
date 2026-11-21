// The Rule editor (mounted only while open, so each opening starts from its own rule or seed): "If all/any of these match" conditions, "Then" actions, optional split lines,
// and a live look at what the Rule would do to the transactions already in the book. Nothing is
// written until Save (or Apply to N, which writes only the ticked transactions).
import { useMemo, useState } from 'react'
import type { Automation, RuleApplyResult } from '../../../../api/automations'
import { Button, Modal } from '../../../ui'
import { TransactionConditionFields } from '../AutomationConditionFields'
import { Select } from '../automationFormControls'
import type { AutomationOptions } from '../AutomationTriggerSummary'
import { RuleActionFields, type RuleChoices } from './RuleActionFields'
import { RulePreviewPanel } from './RulePreviewPanel'
import { RuleSplitFields } from './RuleSplitFields'
import {
  blankRuleForm,
  formFromAutomation,
  formToBody,
  formToDraft,
  ruleIssues,
  type RuleForm,
} from './ruleModel'
import './rules.css'

const MATCH_OPTIONS = [
  ['all', 'all'],
  ['any', 'any'],
] as const

export interface RuleEditorProps {
  onClose: () => void
  /** The Rule being edited; omitted for a new one. */
  rule?: Automation | null
  /** A starting point for a new Rule ("Create rule…" on a transaction). */
  seed?: RuleForm | null
  options: AutomationOptions
  categories: Array<{ id: string; label: string }>
  /** Where a NEW Rule lands in the order: after every other. */
  position: number
  onSubmit: (draft: ReturnType<typeof formToDraft>) => Promise<unknown>
  onApplied?: (result: RuleApplyResult) => void
}

export function RuleEditorModal({
  onClose,
  rule,
  seed,
  options,
  categories,
  position,
  onSubmit,
  onApplied,
}: RuleEditorProps) {
  const [form, setForm] = useState<RuleForm>(() =>
    rule ? formFromAutomation(rule) : (seed ?? blankRuleForm()),
  )
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const choices: RuleChoices = useMemo(
    () => ({
      accounts: options.accounts,
      payees: options.payees ?? [],
      // "split" is the ledger's marker for a split row, not a category a Rule can set — a Rule
      // splits through "Split into lines".
      categories: categories.filter((c) => c.id !== 'split'),
    }),
    [options.accounts, options.payees, categories],
  )
  const labels = useMemo(
    () => ({ ...choices, baseCurrency: options.base_currency }),
    [choices, options.base_currency],
  )
  const issues = ruleIssues(form)
  const slot = rule ? Number(rule.execution.position ?? position) : position
  const body = issues.previewable ? formToBody(form, slot) : null
  const patch = (next: Partial<RuleForm>) => setForm((current) => ({ ...current, ...next }))

  const submit = async () => {
    if (issues.blocking.length > 0) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await onSubmit(
        formToDraft(form, slot, labels, rule?.status === 'paused' ? 'paused' : 'active'),
      )
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'The rule could not be saved.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open
      size="xl"
      className="a3-editor-modal rl-editor"
      title={rule ? 'Edit rule' : 'New rule'}
      onClose={onClose}
      footer={
        <>
          <Button size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={issues.blocking.length > 0}
            loading={submitting}
            onClick={() => void submit()}
          >
            {rule ? 'Save changes' : 'Create rule'}
          </Button>
        </>
      }
    >
      <div className="a3-editor-stack">
        <div>
          <label className="a3-field-label" htmlFor="rule-name">
            Name
          </label>
          <input
            id="rule-name"
            className="rd-input"
            data-autofocus
            value={form.name}
            placeholder="Named from what it does when left blank"
            onChange={(event) => patch({ name: event.target.value })}
          />
        </div>
        <section aria-label="Conditions">
          <h3 className="rl-section-title">
            If{' '}
            <Select
              id="rule-match"
              value={form.match}
              options={MATCH_OPTIONS}
              onChange={(match) => patch({ match: match as 'all' | 'any' })}
            />{' '}
            of these match
          </h3>
          <div className="a3-trows rl-trows">
            <TransactionConditionFields
              when={form.when}
              match={form.match}
              accounts={options.accounts}
              payees={options.payees ?? []}
              baseCurrency={options.base_currency}
              onChange={(next) => patch({ when: { ...form.when, ...next } })}
            />
          </div>
        </section>
        <section aria-label="Actions">
          <h3 className="rl-section-title">Then</h3>
          <RuleActionFields form={form} choices={choices} onChange={patch} />
        </section>
        <section>
          <h3 className="rl-section-title">Split into lines</h3>
          <RuleSplitFields form={form} choices={choices} onChange={(splits) => patch({ splits })} />
        </section>
        {(issues.blocking.length > 0 || issues.hints.length > 0) && (
          <ul className="rl-issues" aria-label="Before you save">
            {issues.blocking.map((message) => (
              <li key={message} className="rl-issue blocking">
                {message}
              </li>
            ))}
            {issues.hints.map((message) => (
              <li key={message} className="rl-issue">
                {message}
              </li>
            ))}
          </ul>
        )}
        <RulePreviewPanel
          body={body}
          labels={labels}
          incompleteHint="Finish the rule above to see which transactions it matches."
          onApplied={onApplied}
        />
        {submitError && (
          <div className="a3-inline-error" role="alert">
            {submitError}
          </div>
        )}
      </div>
    </Modal>
  )
}
