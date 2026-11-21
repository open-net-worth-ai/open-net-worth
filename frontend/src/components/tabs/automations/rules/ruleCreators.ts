// Make a payee or category from a name typed in the Rule editor, the ledger's own way.
import type { AutomationOption } from '../../../../api/automations'
import { createCategory, resolveOrCreatePayeeId } from '../../accounts/transactionApi'
import { readConditions } from '../txnConditions'
import type { RuleForm } from './ruleModel'

export interface RuleChoices {
  accounts: AutomationOption[]
  payees: AutomationOption[]
  /** With each category's colour, so its chip looks the way the ledger shows it. */
  categories: Array<AutomationOption & { color?: string }>
}

/** Category id → its colour, for the editor's category chips. */
export function categoryColors(categories: RuleChoices['categories']): Record<string, string> {
  return Object.fromEntries(categories.filter((c) => c.color).map((c) => [c.id, c.color as string]))
}

/** A new category is income when the rule matches income, otherwise an expense. */
export function creators(form: RuleForm, choices: RuleChoices) {
  const income = readConditions(form.when).some(
    (c) => c.field === 'kind' && c.op === 'is' && c.value === 'income',
  )
  return {
    payee: (name: string) =>
      resolveOrCreatePayeeId(
        name,
        choices.payees.map((p) => ({ id: p.id, name: p.label })),
      ),
    category: async (name: string) =>
      (await createCategory(name, income ? 'income' : 'expense', choices.categories.length)).id,
  }
}
