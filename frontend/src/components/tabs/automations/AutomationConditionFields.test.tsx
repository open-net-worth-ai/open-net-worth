// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { AutomationTrigger } from '../../../api/automations'
import { TransactionConditionFields } from './AutomationConditionFields'
import {
  AutomationTriggerSummary,
  automationTriggerLabel,
  missingTriggerFields,
} from './AutomationTriggerSummary'
import { CONFLICTING_CONDITIONS } from './txnConditionConflicts'

afterEach(cleanup)

const PAYEES = [
  { id: 'pay_grab', label: 'Grab' },
  { id: 'pay_uber', label: 'Uber' },
]
const ACCOUNTS = [
  { id: 'acc_usd', label: 'USD card', currency: 'USD' },
  { id: 'acc_vnd', label: 'VND wallet', currency: 'VND' },
]
const CLEARED = {
  account: undefined,
  account_id: undefined,
  kind: undefined,
  over: undefined,
  max_amount: undefined,
  contains: undefined,
}

function renderList(when: Record<string, unknown>) {
  const onChange = vi.fn()
  render(
    <TransactionConditionFields
      when={when}
      accounts={ACCOUNTS}
      payees={PAYEES}
      baseCurrency="SGD"
      onChange={onChange}
    />,
  )
  return onChange
}

function el<T extends HTMLElement>(id: string) {
  return document.getElementById(id) as T
}

function optionTexts(id: string) {
  return [...el<HTMLSelectElement>(id).options].map((o) => o.textContent)
}

function txn(when: Record<string, unknown>): AutomationTrigger {
  return { kind: 'event', event: 'txn', when, limit: 'each' }
}

describe('transaction conditions', () => {
  it('starts empty, with only an Add condition link', () => {
    const onChange = renderList({})
    expect(el('automation-condition-0-field')).toBeNull()
    fireEvent.click(screen.getByText('Add condition'))
    expect(onChange).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [{ field: 'payee', op: 'is', value: '' }] },
      'trigger.when.conditions',
    )
  })

  it('every field is offered, each with the operations that fit it', () => {
    renderList({
      conditions: [
        { field: 'account', op: 'is', value: 'acc_usd' },
        { field: 'kind', op: 'is', value: 'expense' },
        { field: 'amount', op: 'over', value: 50 },
        { field: 'memo', op: 'regex', value: 'office$' },
      ],
    })
    expect(optionTexts('automation-condition-0-field')).toEqual([
      'Account',
      'Kind',
      'Amount',
      'Payee',
      'Description',
      'Memo',
    ])
    expect(optionTexts('automation-condition-0-op')).toEqual(['is', 'is not'])
    expect(optionTexts('automation-condition-1-op')).toEqual(['is', 'is not'])
    // A picked kind shows as a chip and leaves the list; the rest can still be added.
    expect(screen.getByRole('button', { name: 'Remove Expense' })).toBeTruthy()
    expect(optionTexts('automation-condition-1-value')).not.toContain('Expense')
    expect(optionTexts('automation-condition-1-value')).toEqual(
      expect.arrayContaining([
        'Income',
        'Transfer',
        'Investment',
        'Investment: buy',
        'Investment: sale',
        'Adjustment',
      ]),
    )
    expect(optionTexts('automation-condition-2-op')).toEqual(['over', 'under'])
    expect(optionTexts('automation-condition-3-op')).toEqual([
      'contains',
      "doesn't contain",
      'starts with',
      'matches regex',
    ])
    expect(el<HTMLInputElement>('automation-condition-3-value').value).toBe('office$')
  })

  it('the amount carries the currency it is compared in', () => {
    renderList({ conditions: [{ field: 'amount', op: 'over', value: 50 }] })
    expect(screen.getByText('SGD')).toBeTruthy()
    cleanup()
    renderList({
      conditions: [
        { field: 'account', op: 'is', value: 'acc_vnd' },
        { field: 'amount', op: 'over', value: 50 },
      ],
    })
    expect(screen.getByText('VND')).toBeTruthy()
  })

  it('changing a field starts a fresh condition for it; the others stay', () => {
    const second = { field: 'memo', op: 'contains', value: ['taxi'] }
    const onChange = renderList({
      conditions: [{ field: 'payee', op: 'is', value: 'pay_grab' }, second],
    })
    fireEvent.change(el('automation-condition-0-field'), { target: { value: 'amount' } })
    expect(onChange).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [{ field: 'amount', op: 'over', value: '' }, second] },
      'trigger.when.conditions',
    )
  })

  it('Remove drops just that condition', () => {
    const first = { field: 'payee', op: 'is', value: 'pay_grab' }
    const onChange = renderList({
      conditions: [first, { field: 'memo', op: 'contains', value: ['taxi'] }],
    })
    fireEvent.click(screen.getAllByText('Remove')[1]!)
    expect(onChange).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [first] },
      'trigger.when.conditions',
    )
  })

  it('an older trigger’s flat filters show as conditions and become conditions on edit', () => {
    const onChange = renderList({ account: 'acc_usd', over: 500, contains: 'grab' })
    expect(el<HTMLSelectElement>('automation-condition-0-field').value).toBe('account')
    expect(el<HTMLSelectElement>('automation-condition-1-field').value).toBe('amount')
    expect(el<HTMLSelectElement>('automation-condition-2-field').value).toBe('description')
    fireEvent.change(el('automation-condition-2-value'), { target: { value: 'grab, uber' } })
    expect(onChange).toHaveBeenLastCalledWith(
      {
        ...CLEARED,
        conditions: [
          { field: 'account', op: 'is', value: 'acc_usd' },
          { field: 'amount', op: 'over', value: 500 },
          { field: 'description', op: 'contains', value: 'grab, uber' },
        ],
      },
      'trigger.when.conditions',
    )
  })

  it('reads well on the card', () => {
    const options = { accounts: ACCOUNTS, instruments: [], payees: PAYEES, base_currency: 'SGD' }
    expect(
      automationTriggerLabel(
        txn({
          conditions: [
            { field: 'kind', op: 'is', value: 'expense' },
            { field: 'amount', op: 'over', value: 500 },
            { field: 'account', op: 'is', value: 'acc_usd' },
            { field: 'payee', op: 'is_not', value: 'pay_grab' },
          ],
        }),
        options,
      ),
    ).toBe('When an expense over USD 500 posts on USD card, payee is not Grab')
    expect(
      automationTriggerLabel(
        txn({ conditions: [{ field: 'description', op: 'contains', value: ['grab', 'uber'] }] }),
        options,
      ),
    ).toBe('When a transaction posts, description contains “grab, uber”')
    expect(
      automationTriggerLabel(
        txn({
          conditions: [
            { field: 'kind', op: 'is_not', value: 'transfer' },
            { field: 'amount', op: 'under', value: 20 },
          ],
        }),
        options,
      ),
    ).toBe('When a transaction under SGD 20 posts, kind is not transfer')
    expect(
      automationTriggerLabel(
        txn({
          conditions: [
            { field: 'kind', op: 'is', value: 'investment_sale' },
            { field: 'account', op: 'is', value: 'acc_usd' },
          ],
        }),
        options,
      ),
    ).toBe('When an investment sale posts on USD card')
    expect(
      automationTriggerLabel(
        txn({ conditions: [{ field: 'kind', op: 'is_not', value: 'investment_buy' }] }),
        options,
      ),
    ).toBe('When a transaction posts, kind is not investment: buy')
  })

  it('a balance is always in its account’s currency', () => {
    const options = { accounts: ACCOUNTS, instruments: [], base_currency: 'SGD' }
    expect(
      automationTriggerLabel(
        {
          kind: 'event',
          event: 'balance',
          when: { account: 'acc_vnd', direction: 'below', amount: 1000000 },
          limit: 'each',
        },
        options,
      ),
    ).toBe('When VND wallet drops below VND 1,000,000')
  })

  it('a condition with no value is flagged before saving', () => {
    expect(
      missingTriggerFields(txn({ conditions: [{ field: 'memo', op: 'contains', value: '' }] })),
    ).toEqual(['condition'])
    expect(missingTriggerFields(txn({}))).toEqual([])
  })
})

describe('several values', () => {
  const options = { accounts: ACCOUNTS, instruments: [], payees: PAYEES, base_currency: 'SGD' }
  const payeeIs = (value: unknown) => ({ field: 'payee', op: 'is', value })

  it('a second pick turns "is" into "is any of" and stores a list', () => {
    const onChange = renderList({ conditions: [payeeIs('pay_grab')] })
    expect(optionTexts('automation-condition-0-value')).toEqual(['Or another payee…', 'Uber'])
    fireEvent.change(el('automation-condition-0-value'), { target: { value: 'pay_uber' } })
    expect(onChange).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [payeeIs(['pay_grab', 'pay_uber'])] },
      'trigger.when.conditions',
    )
    cleanup()
    renderList({ conditions: [payeeIs(['pay_grab', 'pay_uber'])] })
    expect(optionTexts('automation-condition-0-op')).toEqual(['is any of', 'is none of'])
    expect(screen.getByRole('button', { name: 'Remove Grab' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove Uber' })).toBeTruthy()
  })

  it('removing a chip leaves the one value stored plainly, and the last leaves it empty', () => {
    const onChange = renderList({ conditions: [payeeIs(['pay_grab', 'pay_uber'])] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Grab' }))
    expect(onChange).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [payeeIs('pay_uber')] },
      'trigger.when.conditions',
    )
    cleanup()
    const again = renderList({ conditions: [payeeIs('pay_uber')] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Uber' }))
    expect(again).toHaveBeenLastCalledWith(
      { ...CLEARED, conditions: [payeeIs('')] },
      'trigger.when.conditions',
    )
    expect(missingTriggerFields(txn({ conditions: [payeeIs([])] }))).toEqual(['condition'])
  })

  it('reads as "or" on the card', () => {
    const label = (conditions: unknown[]) => automationTriggerLabel(txn({ conditions }), options)
    expect(label([payeeIs(['pay_grab', 'pay_uber'])])).toBe(
      'When a transaction posts, payee is Grab or Uber',
    )
    expect(label([{ field: 'kind', op: 'is', value: ['expense', 'income'] }])).toBe(
      'When an expense or income posts',
    )
    expect(label([{ field: 'account', op: 'is_not', value: ['acc_usd', 'acc_vnd'] }])).toBe(
      'When a transaction posts not on USD card or VND wallet',
    )
    // Two accounts share no currency but the base one.
    expect(
      label([
        { field: 'account', op: 'is', value: ['acc_usd', 'acc_vnd'] },
        { field: 'amount', op: 'over', value: 50 },
      ]),
    ).toBe('When a transaction over SGD 50 posts on USD card or VND wallet')
  })
})

describe('conditions that can never all be true', () => {
  const warns = (conditions: unknown[]) => {
    renderList({ conditions })
    return [...document.querySelectorAll('.a3-inline-error')].map((n) => n.textContent)
  }
  const blocked = (conditions: unknown[]) =>
    missingTriggerFields(txn({ conditions })).includes(CONFLICTING_CONDITIONS)

  it('two "Payee is" with different payees: warned, and the save is blocked', () => {
    const conditions = [
      { field: 'payee', op: 'is', value: 'pay_grab' },
      { field: 'payee', op: 'is', value: 'pay_uber' },
    ]
    expect(warns(conditions)).toEqual([
      'A transaction has one payee, so these can’t both be true — pick both in one condition instead.',
    ])
    expect(blocked(conditions)).toBe(true)
  })

  it('two "Kind is", "is" against "is not", and an empty amount range are caught', () => {
    const kinds = [
      { field: 'kind', op: 'is', value: 'expense' },
      { field: 'kind', op: 'is', value: 'income' },
    ]
    expect(warns(kinds)[0]).toMatch(/one kind, so these can’t both be true/)
    cleanup()
    const ruledOut = [
      { field: 'payee', op: 'is', value: 'pay_grab' },
      { field: 'payee', op: 'is_not', value: ['pay_grab', 'pay_uber'] },
    ]
    expect(warns(ruledOut)[0]).toMatch(/rule out every payee/)
    cleanup()
    const range = [
      { field: 'amount', op: 'over', value: 100 },
      { field: 'amount', op: 'under', value: 50 },
    ]
    expect(warns(range)).toEqual([
      'An amount can’t be over 100 and under 50 at once, so this can never match.',
    ])
    for (const c of [kinds, ruledOut, range]) expect(blocked(c)).toBe(true)
  })

  it('combinations that CAN match pass', () => {
    const fine = [
      // A transfer between the two accounts touches both.
      [
        { field: 'account', op: 'is', value: 'acc_usd' },
        { field: 'account', op: 'is', value: 'acc_vnd' },
      ],
      [
        { field: 'payee', op: 'is_not', value: 'pay_grab' },
        { field: 'payee', op: 'is_not', value: 'pay_uber' },
      ],
      [
        { field: 'amount', op: 'over', value: 50 },
        { field: 'amount', op: 'under', value: 100 },
      ],
      // Overlapping picks share Uber; an investment can be a buy.
      [
        { field: 'payee', op: 'is', value: ['pay_grab', 'pay_uber'] },
        { field: 'payee', op: 'is', value: 'pay_uber' },
      ],
      [
        { field: 'kind', op: 'is', value: 'investment' },
        { field: 'kind', op: 'is', value: 'investment_buy' },
      ],
      // A half-filled condition is "missing", not a conflict.
      [
        { field: 'payee', op: 'is', value: 'pay_grab' },
        { field: 'payee', op: 'is', value: '' },
      ],
    ]
    for (const conditions of fine) {
      expect(warns(conditions)).toEqual([])
      expect(blocked(conditions)).toBe(false)
      cleanup()
    }
  })

  it('the trigger summary says to fix them, not to add a field', () => {
    render(
      <AutomationTriggerSummary
        trigger={txn({
          conditions: [
            { field: 'amount', op: 'over', value: 100 },
            { field: 'amount', op: 'under', value: 50 },
          ],
        })}
      />,
    )
    expect(screen.getByText(/can never all be true/)).toBeTruthy()
    expect(screen.queryByText(/^Add /)).toBeNull()
  })
})

describe('Run', () => {
  it('explains under the field what each choice does with the matches', async () => {
    const { AutomationTriggerFields } = await import('./AutomationTriggerFields')
    for (const [limit, text] of [
      ['each', 'A run for every match, one after another.'],
      ['hour', 'anything later in the hour waits and comes in one run.'],
      ['day', 'anything later in the day waits and comes in one run.'],
    ] as const) {
      cleanup()
      render(
        <AutomationTriggerFields
          trigger={{ kind: 'event', event: 'txn', when: {}, limit }}
          onChange={() => {}}
        />,
      )
      expect(screen.getByText(text, { exact: false })).toBeTruthy()
    }
  })
})
