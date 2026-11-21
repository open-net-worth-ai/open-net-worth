// The Rule editor (mounted only while open, so each opening starts from its own rule or seed):
// "If all/any of these match" conditions, "Then" the actions it uses, and a live look at what the Rule would do to the transactions already in the book. Nothing is
// written until Save (or Apply to N, which writes only the ticked transactions).
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Automation, RuleApplyResult } from '../../../../api/automations'
import { Button, Modal } from '../../../ui'
import { TransactionConditionFields } from '../AutomationConditionFields'
import { Select } from '../automationFormControls'
import { ComboDropdowns } from '../comboDropdowns'
import type { AutomationOptions } from '../AutomationTriggerSummary'
import { RuleActionFields, type RuleChoices } from './RuleActionFields'
import { RulePreviewPanel } from './RulePreviewPanel'
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
  // Centred where it opens, then its top edge stays put: rows added later grow it downward
  // (re-centring on every row made the whole dialog jump), and past the window the body scrolls.
  const stackRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // One frame late: the dialog layer rewrites the card's inline style as it mounts.
    const frame = requestAnimationFrame(() => {
      const card = stackRef.current?.closest<HTMLElement>('.ui-modal-card')
      if (!card || window.matchMedia('(max-width: 600px)').matches) return
      // The overlay already pads the card 24px; `gap` is its distance from the window's top, and
      // it may only grow until the gap below is the same — never touching the bottom edge.
      const pad = 24
      const gap = Math.max(pad, Math.round((window.innerHeight - card.offsetHeight) / 2))
      card.style.setProperty('margin-top', `${gap - pad}px`)
      card.style.setProperty('max-height', `calc(100vh - ${2 * gap}px)`)
    })
    return () => cancelAnimationFrame(frame)
  }, [])
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  // Problems are shown once Create is pressed, never while the rule is still being filled in.
  const [attempted, setAttempted] = useState(false)

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
    setAttempted(true)
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
      size="lg"
      className="a3-editor-modal rl-editor"
      title={rule ? 'Edit rule' : 'New rule'}
      onClose={onClose}
      footer={
        <>
          <Button size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" loading={submitting} onClick={() => void submit()}>
            {rule ? 'Save changes' : 'Create rule'}
          </Button>
        </>
      }
    >
      <ComboDropdowns.Provider value>
        <div className="a3-editor-stack" ref={stackRef}>
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
          <section className="rl-clause" aria-label="Conditions">
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
            <TransactionConditionFields
              layout="inline"
              when={form.when}
              match={form.match}
              accounts={options.accounts}
              payees={options.payees ?? []}
              baseCurrency={options.base_currency}
              onChange={(next) => patch({ when: { ...form.when, ...next } })}
            />
          </section>
          <section className="rl-clause" aria-label="Actions">
            <h3 className="rl-section-title">Then</h3>
            <RuleActionFields form={form} choices={choices} onChange={patch} />
          </section>
          {((attempted && issues.blocking.length > 0) || issues.hints.length > 0) && (
            <ul className="rl-issues" aria-label="Before you save">
              {attempted &&
                issues.blocking.map((message) => (
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
      </ComboDropdowns.Provider>
    </Modal>
  )
}
