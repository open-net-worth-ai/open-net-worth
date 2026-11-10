// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { AutomationTrigger } from '../../../api/automations'
import { AutomationTriggerFields } from './AutomationTriggerFields'
import { automationTriggerLabel } from './AutomationTriggerSummary'

const migratedRuleTrigger: AutomationTrigger = {
  kind: 'event',
  event: 'txn',
  when: {
    description_contains: 'coffee',
    keywords: null,
    description_regex: null,
    memo_contains: null,
    memo_keywords: null,
    memo_regex: null,
    account_id: 'acc_card',
    kind: 'expense',
    direction: 'debit',
    currency: 'SGD',
    min_amount: 2.5,
    max_amount: 50,
  },
  limit: 'each',
}

const options = {
  accounts: [
    { id: 'acc_card', label: 'Meridian Card' },
    { id: 'acc_savings', label: 'Savings' },
  ],
  commitments: [],
  connectors: [],
  instruments: [],
  models: [],
}

afterEach(() => cleanup())

describe('migrated transaction Automation predicates', () => {
  it('shows unsupported predicates as exact Custom conditions, read-only', () => {
    const exactConditions =
      'description_contains="coffee" • account_id="acc_card" • kind="expense" • direction="debit" • currency="SGD" • min_amount=2.5 • max_amount=50'
    expect(automationTriggerLabel(migratedRuleTrigger, options)).toBe(
      `Custom transaction • ${exactConditions}`,
    )

    render(
      <AutomationTriggerFields
        trigger={migratedRuleTrigger}
        options={options}
        onChange={vi.fn()}
      />,
    )

    expect((screen.getByLabelText(/^Event/) as HTMLSelectElement).value).toBe('custom')
    expect(screen.getByText(exactConditions)).toBeTruthy()
    expect(screen.queryByText('Add condition')).toBeNull()
  })

  it('a direction is no longer editable, so a trigger carrying one is Custom', () => {
    const trigger: AutomationTrigger = {
      kind: 'event',
      event: 'txn',
      when: { over: 500, direction: 'out' },
      limit: 'each',
    }
    expect(automationTriggerLabel(trigger, options)).toBe(
      'Custom transaction • over=500 • direction="out"',
    )
  })

  it('keeps create canonical but makes a non-create operation exact and Custom', () => {
    const createTrigger: AutomationTrigger = {
      kind: 'event',
      event: 'txn',
      when: { operation: 'create', account: 'acc_card' },
      limit: 'each',
    }
    const deleteTrigger: AutomationTrigger = {
      ...createTrigger,
      when: { operation: 'delete', account: 'acc_card' },
    }

    expect(automationTriggerLabel(createTrigger, options)).toBe(
      'When a transaction posts on Meridian Card',
    )
    expect(automationTriggerLabel(deleteTrigger, options)).toBe(
      'Custom transaction • operation="delete" • account="acc_card"',
    )
  })
})
