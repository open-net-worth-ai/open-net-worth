// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { AutomationTrigger } from '../../../api/automations'
import { AutomationTriggerText } from './AutomationTriggerSummary'

const custom = (rrule: string): AutomationTrigger => ({ kind: 'schedule', type: 'rrule', rrule })

afterEach(() => cleanup())

describe('the trigger in words', () => {
  it('shows the words and the rule on lines of their own', () => {
    render(<AutomationTriggerText trigger={custom('FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0')} />)
    expect(screen.getByText('Every day at 12:00 AM and 12:00 PM').className).toBe(
      'a3-trigger-words',
    )
    expect(screen.getByText('FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0').className).toBe('a3-trigger-rule')
  })

  it('shows a rule it cannot read once', () => {
    const { container } = render(<AutomationTriggerText trigger={custom('FREQ=DAILY;COUNT=3')} />)
    expect(screen.getByText('Custom cadence • FREQ=DAILY;COUNT=3')).toBeTruthy()
    expect(container.querySelector('.a3-trigger-rule')).toBeNull()
  })

  it('has no second line for a schedule that is not custom', () => {
    const { container } = render(
      <AutomationTriggerText trigger={{ kind: 'schedule', type: 'daily', time: '09:00' }} />,
    )
    expect(screen.getByText('Every day at 9:00 AM')).toBeTruthy()
    expect(container.querySelector('.a3-trigger-rule')).toBeNull()
  })
})
