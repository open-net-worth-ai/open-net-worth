// Starting points for the rules most books need. Each opens the editor pre-filled; the payee,
// account and category are left for the owner to pick, because those are theirs, not ours.
import type { TxnCondition } from '../txnConditions'
import { blankRuleForm, blankSplit, type RuleForm } from './ruleModel'

export interface RuleTemplate {
  key: string
  title: string
  blurb: string
  seed: () => RuleForm
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
    blurb: 'A streaming or software payee gets its category and a subscription tag.',
    seed: () => form({ when: when(payeeIs), tags: ['subscription'], showActions: ['category'] }),
  },
  {
    key: 'salary',
    title: 'Your salary',
    blurb: 'Income from your employer is labelled as salary.',
    seed: () => form({ when: when(payeeIs, { field: 'kind', op: 'is', value: 'income' }) }),
  },
  {
    key: 'fixed-bill',
    title: 'Rent or a fixed bill',
    blurb: 'The same payee at about the same amount every month.',
    seed: () => form({ when: when(payeeIs, { field: 'amount', op: 'approx', value: '' }) }),
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
  },
  {
    key: 'fees',
    title: 'Bank and card fees',
    blurb: 'Anything that mentions a fee is gathered in one place.',
    seed: () => form({ when: when({ field: 'description', op: 'contains', value: 'fee' }) }),
  },
  {
    key: 'work-card',
    title: 'Tag work spending',
    blurb: 'Everything on your work card is tagged so it is easy to claim back.',
    seed: () =>
      form({
        when: when({ field: 'account', op: 'is', value: '' }),
        tags: ['work'],
      }),
  },
]
