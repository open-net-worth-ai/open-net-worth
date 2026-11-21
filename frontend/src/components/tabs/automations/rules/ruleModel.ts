// A Rule as the editor holds it, and its two-way mapping to the Automation row the server
// stores (docs/design/transaction-rules.md). Pure: no React, no fetching.
import type {
  Automation,
  RuleBody,
  RuleDraft,
  RuleExecution,
  RuleSplitAmount,
  RuleSplitLine,
} from '../../../../api/automations'
import { conditionConflicts } from '../txnConditionConflicts'
import {
  conditionIsComplete,
  newCondition,
  readConditions,
  transactionLabel,
} from '../txnConditions'

export const MAX_SPLIT_LINES = 10

export type SplitKind = RuleSplitAmount['type']

export interface SplitForm {
  kind: SplitKind
  /** The figure typed for a fixed or percent line; '' until typed. */
  value: number | ''
  category: string
  tags: string[]
  memo: string
}

export interface RuleForm {
  name: string
  match: 'all' | 'any'
  /** The trigger's `when`, minus `match` (kept whole so an older rule's keys survive an edit). */
  when: Record<string, unknown>
  category: string
  tags: string[]
  noteAppend: string
  memoAppend: string
  payee: string
  routeToAccount: string
  override: boolean
  stop: boolean
  splits: SplitForm[]
  /** The amount a transaction this Rule was made from carried — lets the editor say whether
   *  fixed split lines add up. Never sent. */
  knownAmount?: number
  /** Actions a template wants on screen before they have a value (e.g. "Set payee"). Never sent. */
  showActions?: string[]
}

export function blankRuleForm(): RuleForm {
  return {
    name: '',
    match: 'all',
    // One blank payee condition to fill in, so a new rule opens ready to write — not empty.
    when: { conditions: [newCondition('payee')] },
    category: '',
    tags: [],
    noteAppend: '',
    memoAppend: '',
    payee: '',
    routeToAccount: '',
    override: false,
    stop: false,
    splits: [],
  }
}

export function blankSplit(kind: SplitKind = 'remainder'): SplitForm {
  return { kind, value: '', category: '', tags: [], memo: '' }
}

export function positionOf(automation: Automation, index = 0): number {
  const execution = automation.execution
  const stored = execution.position ?? execution.priority
  return typeof stored === 'number' ? stored : index
}

/** The Rules of a list, in the order they run. */
export function rulesInOrder(automations: Automation[]): Automation[] {
  return automations
    .filter((a) => a.execution?.kind === 'transaction_transform')
    .map((rule, index) => ({ rule, index }))
    .sort((a, b) => positionOf(a.rule, a.index) - positionOf(b.rule, b.index) || a.index - b.index)
    .map(({ rule }) => rule)
}

export function nextPosition(rules: Automation[]): number {
  return rules.reduce((top, rule, index) => Math.max(top, positionOf(rule, index) + 1), 0)
}

function splitFormOf(line: RuleSplitLine): SplitForm {
  return {
    kind: line.amount.type,
    value: line.amount.type === 'remainder' ? '' : line.amount.value,
    category: line.category,
    tags: line.tags ?? [],
    memo: line.memo ?? '',
  }
}

export function formFromAutomation(automation: Automation): RuleForm {
  const trigger = automation.trigger
  const { match, ...when } = trigger.kind === 'event' ? trigger.when : { match: 'all' }
  const execution = automation.execution as Partial<RuleExecution>
  const set = execution.set ?? {}
  return {
    name: automation.name,
    match: match === 'any' ? 'any' : 'all',
    when,
    category: set.category ?? '',
    tags: set.tags ?? [],
    noteAppend: set.note_append ?? '',
    memoAppend: set.memo_append ?? '',
    payee: set.payee ?? '',
    routeToAccount: set.route_to_account ?? '',
    override: execution.override_existing === true,
    stop: execution.stop === true,
    splits: (execution.splits ?? []).map(splitFormOf),
  }
}

function splitLine(line: SplitForm): RuleSplitLine {
  const amount: RuleSplitAmount =
    line.kind === 'remainder'
      ? { type: 'remainder' }
      : { type: line.kind, value: Number(line.value) }
  return {
    amount,
    category: line.category,
    ...(line.tags.length ? { tags: line.tags } : {}),
    ...(line.memo.trim() ? { memo: line.memo.trim() } : {}),
  }
}

/** The Rule exactly as the contract stores it. */
export function formToBody(form: RuleForm, position: number): RuleBody {
  const conditions = readConditions(form.when).filter(conditionIsComplete)
  const set: RuleExecution['set'] = {}
  if (form.category) set.category = form.category
  if (form.tags.length) set.tags = form.tags
  if (form.noteAppend.trim()) set.note_append = form.noteAppend.trim()
  if (form.memoAppend.trim()) set.memo_append = form.memoAppend.trim()
  if (form.payee) set.payee = form.payee
  if (form.routeToAccount) set.route_to_account = form.routeToAccount
  const execution: RuleExecution = {
    kind: 'transaction_transform',
    position,
    stop: form.stop,
    override_existing: form.override,
    set,
    ...(form.splits.length ? { splits: form.splits.map(splitLine) } : {}),
  }
  const flat = Object.fromEntries(
    Object.entries(form.when).filter(([key]) => key !== 'conditions' && !FLAT_KEYS.has(key)),
  )
  return {
    trigger: {
      kind: 'event',
      event: 'txn',
      when: { ...flat, match: form.match, conditions },
    },
    execution,
  }
}

// An older rule's flat keys are shown as conditions and rewritten as `conditions` on save.
const FLAT_KEYS = new Set(['account', 'account_id', 'kind', 'over', 'max_amount', 'contains'])

export interface RuleLabels {
  accounts: Array<{ id: string; label: string; currency?: string }>
  payees: Array<{ id: string; label: string }>
  categories: Array<{ id: string; label: string }>
  baseCurrency?: string
}

const labelOf = (list: Array<{ id: string; label: string }>, id: string) =>
  list.find((item) => item.id === id)?.label ?? id

/** "When payee is Grab, amount over SGD 10 posts: set category Transport, add tags a, b." */
export function describeRule(form: RuleForm, labels: RuleLabels): string {
  const conditions = readConditions(form.when).filter(conditionIsComplete)
  const when = transactionLabel(conditions, {
    accounts: labels.accounts,
    payees: labels.payees,
    base_currency: labels.baseCurrency,
  })
  const joiner = form.match === 'any' ? ' (any of these)' : ''
  const actions: string[] = []
  if (form.category) actions.push(`set category to ${labelOf(labels.categories, form.category)}`)
  if (form.tags.length) actions.push(`add tags ${form.tags.join(', ')}`)
  if (form.payee) actions.push(`set payee to ${labelOf(labels.payees, form.payee)}`)
  if (form.noteAppend.trim()) actions.push(`append “${form.noteAppend.trim()}” to the description`)
  if (form.memoAppend.trim()) actions.push(`append “${form.memoAppend.trim()}” to the memo`)
  if (form.routeToAccount) {
    actions.push(`route to ${labelOf(labels.accounts, form.routeToAccount)}`)
  }
  if (form.splits.length) {
    actions.push(
      `split into ${form.splits.length} ${form.splits.length === 1 ? 'line' : 'lines'} (${form.splits
        .map((line) => labelOf(labels.categories, line.category) || 'category')
        .join(', ')})`,
    )
  }
  return `${when}${joiner}: ${actions.join(', ') || 'no action yet'}.`
}

export interface RuleIssues {
  /** Why it cannot be saved yet. */
  blocking: string[]
  /** Worth knowing; saving is fine. */
  hints: string[]
  /** A complete rule: the only kind the server can preview. */
  previewable: boolean
}

const round = (n: number) => Math.round(n * 100) / 100

export function ruleIssues(form: RuleForm): RuleIssues {
  const blocking: string[] = []
  const hints: string[] = []
  const conditions = readConditions(form.when)
  if (conditions.length === 0) blocking.push('Add at least one condition.')
  else if (!conditions.every(conditionIsComplete)) blocking.push('Finish every condition.')
  if (form.match === 'all') {
    for (const conflict of conditionConflicts(conditions)) blocking.push(conflict.message)
  }
  const hasSet =
    form.category ||
    form.tags.length ||
    form.noteAppend.trim() ||
    form.memoAppend.trim() ||
    form.payee ||
    form.routeToAccount
  if (!hasSet && form.splits.length === 0) blocking.push('Add at least one action.')
  const splitIssues = splitProblems(form)
  blocking.push(...splitIssues.blocking)
  hints.push(...splitIssues.hints)
  return { blocking, hints, previewable: blocking.length === 0 }
}

function splitProblems(form: RuleForm): { blocking: string[]; hints: string[] } {
  const blocking: string[] = []
  const hints: string[] = []
  const lines = form.splits
  if (lines.length === 0) return { blocking, hints }
  if (lines.length > MAX_SPLIT_LINES) blocking.push(`A Rule has at most ${MAX_SPLIT_LINES} lines.`)
  if (lines.some((line) => !line.category)) blocking.push('Every line needs a category.')
  for (const line of lines) {
    if (line.kind === 'remainder') continue
    const value = Number(line.value)
    if (line.value === '' || !(value > 0)) {
      blocking.push('Every fixed or percent line needs an amount above zero.')
      break
    }
    if (line.kind === 'percent' && value > 100) {
      blocking.push('A percent line can be at most 100.')
      break
    }
  }
  if (blocking.length) return { blocking, hints }
  const hasRemainder = lines.some((line) => line.kind === 'remainder')
  if (hasRemainder) return { blocking, hints }
  const fixed = round(
    lines.filter((l) => l.kind === 'fixed').reduce((sum, l) => sum + Number(l.value), 0),
  )
  const percentTotal = lines
    .filter((l) => l.kind === 'percent')
    .reduce((s, l) => s + Number(l.value), 0)
  if (percentTotal > 0 && Math.abs(percentTotal - 100) > 0.001) {
    hints.push(
      'Percent lines take a share of what is left after the fixed lines; with no remainder line they should add up to 100%, or a row is skipped.',
    )
  } else if (
    percentTotal === 0 &&
    form.knownAmount !== undefined &&
    fixed !== round(form.knownAmount)
  ) {
    hints.push(
      `The lines add up to ${fixed.toLocaleString()} but the amount is ${round(form.knownAmount).toLocaleString()}. Add a remainder line, or rows that don't add up are skipped.`,
    )
  } else if (percentTotal === 0) {
    hints.push(
      'No remainder line: a row is skipped unless the fixed lines add up to its amount. Add a remainder line to catch the rest.',
    )
  }
  return { blocking, hints }
}

/** The name when none is typed: the sentence's own words, trimmed. */
export function autoName(form: RuleForm, labels: RuleLabels): string {
  const sentence = describeRule(form, labels).replace(/\.$/, '')
  return sentence.length > 60 ? `${sentence.slice(0, 57)}…` : sentence
}

export function formToDraft(
  form: RuleForm,
  position: number,
  labels: RuleLabels,
  status?: 'active' | 'paused',
): RuleDraft {
  return {
    name: form.name.trim() || autoName(form, labels),
    instructions: describeRule(form, labels),
    ...formToBody(form, position),
    ...(status ? { status } : {}),
  }
}
