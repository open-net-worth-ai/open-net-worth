import { describe, expect, it } from 'vitest'
import {
  ALL_TEMPLATES,
  AUTOMATION_TEMPLATES,
  RECOMMENDED_TEMPLATES,
  TEMPLATE_ICONS,
  templateKeyForConnector,
} from './automationTemplates'
import {
  automationCustomRule,
  automationTriggerFullLabel,
  automationTriggerLabel,
} from './AutomationTriggerSummary'
import type { AutomationTrigger } from '../../../api/automations'

const byKey = (key: string) => ALL_TEMPLATES.find((t) => t.key === key)!
const mail = byKey('mail_ledger')
const digest = byKey('mail_digest')

describe('automation templates', () => {
  it('recommends the email digest, then the three reports', () => {
    expect(RECOMMENDED_TEMPLATES.map((t) => t.key)).toEqual([
      'mail_digest',
      'daily-briefing',
      'weekly-report',
      'monthly-report',
    ])
  })

  it('reads like something a non-technical person would write', () => {
    for (const template of RECOMMENDED_TEMPLATES) {
      expect(template.instructions).not.toMatch(
        /read-only|calendar day|idempot|\bskill for\b|run the/i,
      )
    }
  })

  it('each report runs its built-in skill', () => {
    expect(byKey('daily-briefing').trigger).toEqual({
      kind: 'schedule',
      type: 'daily',
      time: '09:00',
    })
    expect(byKey('daily-briefing').instructions).toContain('using the Daily briefing skill')
    expect(byKey('weekly-report').instructions).toContain('using the Weekly report skill')
    expect(byKey('monthly-report').instructions).toContain('using the Monthly report skill')
  })

  // The trigger is the only "when" (ADR 2026-10-11): timing written into the text goes stale
  // the moment someone changes the trigger — "over 500" in the words, 1,000 in the trigger.
  it('says what to do, never when — that is the trigger', () => {
    const timing =
      /\b(every (hour|day|morning|week|month|\d)|each (morning|day|week|month)|hourly|on the \d|(mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}\s?(am|pm)|when an? (charge|transaction))/i
    for (const template of ALL_TEMPLATES) {
      expect(template.instructions, template.key).not.toMatch(timing)
      expect(template.description ?? '', template.key).not.toMatch(timing)
    }
  })

  it('only uses the triggers automations still have', () => {
    for (const template of ALL_TEMPLATES) {
      if (template.trigger.kind === 'event') {
        expect(['txn', 'balance', 'price']).toContain(template.trigger.event)
      }
    }
  })

  it('has unique keys and an icon keyed to every template', () => {
    expect(new Set(ALL_TEMPLATES.map((t) => t.key)).size).toBe(ALL_TEMPLATES.length)
    for (const template of ALL_TEMPLATES) expect(TEMPLATE_ICONS[template.key]).toBeTruthy()
  })
})

describe('the email digest template', () => {
  it('runs at midnight and noon through the Email finance digest skill', () => {
    expect(digest.name).toBe('Email finance digest')
    expect(digest.trigger).toEqual({
      kind: 'schedule',
      type: 'rrule',
      rrule: 'FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0',
    })
    expect(automationTriggerLabel(digest.trigger)).toBe('Every day at 12:00 AM and 12:00 PM')
    expect(digest.execution).toEqual({ kind: 'lucius' })
    expect(digest.permission_mode).toBe('auto')
    expect(digest.instructions).toContain('using the Email finance digest skill')
    expect(digest.instructions).toContain('Log my bank, card and trade confirmations')
    expect(digest.instructions).not.toMatch(/message_id|idempot/i)
  })

  it('is what the Email connector automates', () => {
    expect(templateKeyForConnector('email')).toBe('mail_digest')
  })
})

describe('the schedule label for a custom cadence', () => {
  const trigger = (rrule: string): AutomationTrigger => ({ kind: 'schedule', type: 'rrule', rrule })

  it('reads a rule it understands, with the rule itself beside it where there is room', () => {
    const readable = trigger('FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=5')
    expect(automationTriggerLabel(readable)).toBe('Every 2 months on the 5th')
    expect(automationCustomRule(readable)).toBe('FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=5')
    expect(automationTriggerFullLabel(readable)).toBe(
      'Every 2 months on the 5th • FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=5',
    )
  })

  it('shows a rule it cannot read once, as written', () => {
    const raw = trigger('FREQ=DAILY;COUNT=3')
    expect(automationTriggerLabel(raw)).toBe('Custom cadence • FREQ=DAILY;COUNT=3')
    expect(automationCustomRule(raw)).toBeNull()
    expect(automationTriggerFullLabel(raw)).toBe('Custom cadence • FREQ=DAILY;COUNT=3')
  })

  it('adds nothing to a schedule that is not custom', () => {
    const daily: AutomationTrigger = { kind: 'schedule', type: 'daily', time: '09:00' }
    expect(automationCustomRule(daily)).toBeNull()
    expect(automationTriggerFullLabel(daily)).toBe('Every day at 9:00 AM')
  })
})

describe('the email-logging template', () => {
  it('stays on offer for logging alone', () => {
    expect(AUTOMATION_TEMPLATES.map((t) => t.key)).toContain('mail_ledger')
    expect(RECOMMENDED_TEMPLATES.map((t) => t.key)).not.toContain('mail_ledger')
  })

  it('runs every 12 hours through the Log transactions from email skill', () => {
    expect(mail.name).toBe('Auto-log transactions from email')
    expect(mail.trigger).toEqual({ kind: 'schedule', type: 'interval', seconds: 43200 })
    expect(mail.execution).toEqual({ kind: 'lucius' })
    expect(mail.permission_mode).toBe('auto')
    expect(mail.instructions).toContain('using the Log transactions from email skill')
    expect(mail.instructions).not.toMatch(/message_id|idempot/i)
  })
})
