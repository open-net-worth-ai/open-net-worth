// Starting points for the rules most books need. Each opens the editor pre-filled. A template
// never invents a label: it names the categories it would usually use, and only one that already
// exists in the owner's book is picked; otherwise the field stays blank for them to choose.
import type { TxnCondition } from '../txnConditions'
import { blankRuleForm, blankSplit, type RuleForm } from './ruleModel'

export interface RuleTemplate {
  key: string
  title: string
  blurb: string
  seed: () => RuleForm
  /** Likely names for the category it sets, best first. */
  category?: string[]
  /** Likely names for each split line's category, in line order. */
  splitCategories?: string[][]
}

const when = (...conditions: TxnCondition[]) => ({ conditions })
const payeeIs: TxnCondition = { field: 'payee', op: 'is', value: '' }

function form(patch: Partial<RuleForm>): RuleForm {
  return { ...blankRuleForm(), ...patch }
}

export const RULE_TEMPLATES: RuleTemplate[] = [
  {
    key: 'rename-payee',
    title: 'Clean up a payee',
    blurb: 'Bank text like “AMZN MKTP US*2K4” becomes one payee you recognise.',
    seed: () =>
      form({
        when: when({ field: 'description', op: 'contains', value: '' }),
        showActions: ['payee'],
      }),
  },
  {
    key: 'subscription',
    title: 'A subscription',
    blurb: 'A streaming or software payee always lands in the same category.',
    seed: () => form({ when: when(payeeIs) }),
    category: ['Subscriptions', 'Subscription', 'Streaming'],
  },
  {
    key: 'salary',
    title: 'Your salary',
    blurb: 'Income from your employer is labelled as salary.',
    seed: () => form({ when: when(payeeIs, { field: 'kind', op: 'is', value: 'income' }) }),
    category: ['Salary', 'Paycheck', 'Wages', 'Income'],
  },
  {
    key: 'fixed-bill',
    title: 'Rent or a fixed bill',
    blurb: 'The same payee at about the same amount every month.',
    seed: () => form({ when: when(payeeIs, { field: 'amount', op: 'approx', value: '' }) }),
    category: ['Rent', 'Mortgage', 'Housing', 'Bills'],
  },
  {
    key: 'shared-purchase',
    title: 'Split a shared purchase',
    blurb: 'Half of a warehouse-store run to groceries, the rest to household.',
    seed: () =>
      form({
        when: when(payeeIs),
        splits: [{ ...blankSplit('percent'), value: 50 }, blankSplit('remainder')],
      }),
    splitCategories: [
      ['Groceries', 'Grocery', 'Food'],
      ['Household', 'Home'],
    ],
  },
  {
    key: 'coffee',
    title: 'Coffee and snacks',
    blurb: 'Small coffee-shop spends land in one category.',
    seed: () =>
      form({
        when: when(
          { field: 'description', op: 'contains', value: 'coffee' },
          { field: 'amount', op: 'under', value: 15 },
        ),
      }),
    category: ['Coffee', 'Dining out', 'Eating out', 'Restaurants', 'Food'],
  },
  {
    key: 'fees',
    title: 'Bank and card fees',
    blurb: 'Anything that mentions a fee is gathered in one place.',
    seed: () => form({ when: when({ field: 'description', op: 'contains', value: 'fee' }) }),
    category: ['Bank fees', 'Fees', 'Bank charges'],
  },
  {
    key: 'work-card',
    title: 'Tag work spending',
    blurb: 'Everything on your work card is tagged so it is easy to claim back.',
    seed: () =>
      form({
        when: when({ field: 'account', op: 'is', value: '' }),
        showActions: ['tags'],
      }),
  },
]

const fold = (label: string) => label.trim().toLocaleLowerCase()

/** The first of `names` that is one of the owner's own categories, by name; '' when none is. */
function ownCategory(
  names: string[] | undefined,
  categories: Array<{ id: string; label: string }>,
) {
  for (const name of names ?? []) {
    const hit = categories.find((category) => fold(category.label) === fold(name))
    if (hit) return hit.id
  }
  return ''
}

/** A template's form with its categories resolved against the owner's book (never invented). */
export function seedTemplate(
  template: RuleTemplate,
  categories: Array<{ id: string; label: string }>,
): RuleForm {
  const seed = template.seed()
  const category = ownCategory(template.category, categories)
  const splits = seed.splits.map((line, index) => ({
    ...line,
    category: ownCategory(template.splitCategories?.[index], categories),
  }))
  return { ...seed, category, splits }
}
