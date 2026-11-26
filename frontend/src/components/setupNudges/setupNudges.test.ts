import { describe, expect, it } from 'vitest'
import type { Automation } from '../../api/automations'
import type { Connector } from '../tabs/connectors/types'
import { eligibleNudges, nudgeKey, pickNudge, withTouch } from './setupNudges'

const conn = (kind: string, status: string): Connector =>
  ({
    id: kind,
    kind,
    name: kind,
    enabled: true,
    status,
    pausable: true,
    config: {},
    secrets_public: {},
  }) as unknown as Connector

const auto = (name: string, instructions = ''): Automation =>
  ({ id: name, name, instructions, status: 'active' }) as unknown as Automation

const ALL_AUTOS = [
  auto('Daily briefing'),
  auto('Mail', 'using the Email finance digest skill'),
  auto('Weekly report'),
  auto('Monthly report'),
]
const NOW = new Date(2026, 10, 24, 10)
const daysAgo = (n: number) => new Date(2026, 10, 24 - n)

describe('eligibleNudges — each case follows its own status', () => {
  it('a fresh book offers Telegram, Gmail, the briefing and the reports — not the digest', () => {
    const cases = eligibleNudges([], []).map((n) => n.case)
    expect(cases).toEqual(['telegram', 'gmail', 'daily-briefing', 'reports'])
  })

  it('a working connector and existing Automations offer nothing', () => {
    const c = [conn('telegram', 'active'), conn('email', 'configured')]
    expect(eligibleNudges(c, ALL_AUTOS)).toEqual([])
  })

  it('a broken connector is offered as a fix, ahead of everything else', () => {
    const c = [conn('telegram', 'error'), conn('email', 'error')]
    const out = eligibleNudges(c, [])
    expect(out.map((n) => n.case).slice(0, 2)).toEqual(['telegram-error', 'email-error'])
    expect(out.map((n) => n.case)).not.toContain('telegram')
    expect(out.map((n) => n.case)).not.toContain('mail-digest')
  })

  it('the email digest appears only once email works', () => {
    const c = [conn('telegram', 'active'), conn('email', 'active')]
    const out = eligibleNudges(c, [
      auto('Daily briefing'),
      auto('Weekly report'),
      auto('Monthly report'),
    ])
    expect(out.map((n) => n.case)).toEqual(['mail-digest'])
    expect(out[0]!.primary.to).toBe('/automations?template=mail_digest')
  })

  it('reports offer only the missing one', () => {
    const out = eligibleNudges([], [auto('Weekly report')]).find((n) => n.case === 'reports')!
    expect(out.primary.to).toBe('/automations?template=monthly-report')
    expect(out.secondary).toBeUndefined()
  })

  it('says where a briefing lands, from Telegram status', () => {
    const here = eligibleNudges([], []).find((n) => n.case === 'daily-briefing')!
    const tg = eligibleNudges([conn('telegram', 'active')], []).find(
      (n) => n.case === 'daily-briefing',
    )!
    expect(here.body).toContain('here in chat')
    expect(tg.body).toContain('on Telegram')
  })

  it('a completed Automation does not count as set up', () => {
    const done = { ...auto('Daily briefing'), status: 'completed' } as Automation
    expect(eligibleNudges([], [done]).map((n) => n.case)).toContain('daily-briefing')
  })
})

describe('pickNudge — cadence', () => {
  const fresh = eligibleNudges([], [])

  it('shows the first eligible case when nothing was touched', () => {
    expect(pickNudge(fresh, [], NOW)?.case).toBe('telegram')
  })

  it('stays quiet for three days after any touch', () => {
    expect(pickNudge(fresh, [nudgeKey('telegram', daysAgo(1))], NOW)).toBeNull()
    expect(pickNudge(fresh, [nudgeKey('telegram', daysAgo(3))], NOW)?.case).toBe('gmail')
  })

  it('brings a dismissed case back after two weeks, and retires it after the second dismissal', () => {
    const once = [nudgeKey('telegram', daysAgo(14))]
    expect(pickNudge(fresh, once, NOW)?.case).toBe('telegram')
    const twice = [nudgeKey('telegram', daysAgo(30)), nudgeKey('telegram', daysAgo(14))]
    expect(pickNudge(fresh, twice, NOW)?.case).toBe('gmail')
  })

  it('a broken connector skips the quiet days', () => {
    const broken = eligibleNudges([conn('telegram', 'error')], [])
    expect(pickNudge(broken, [nudgeKey('gmail', daysAgo(0))], NOW)?.case).toBe('telegram-error')
  })

  it('ignores intro keys that are not nudges', () => {
    expect(pickNudge(fresh, ['assets', 'budget'], NOW)?.case).toBe('telegram')
  })

  it('one touch per case per day — a double click never spends the second dismissal', () => {
    const seen = withTouch(withTouch([], 'telegram', NOW), 'telegram', NOW)
    expect(seen).toEqual(['nudge-telegram-20261124'])
  })

  it('offers nothing but a fix for three days after first run', () => {
    const fresh = eligibleNudges([], [])
    const justSetUp = new Date(2026, 10, 23).toISOString()
    expect(pickNudge(fresh, [], NOW, justSetUp)).toBeNull()
    const broken = eligibleNudges([conn('telegram', 'error')], [])
    expect(pickNudge(broken, [], NOW, justSetUp)?.case).toBe('telegram-error')
    const settled = new Date(2026, 10, 20).toISOString()
    expect(pickNudge(fresh, [], NOW, settled)?.case).toBe('telegram')
    expect(pickNudge(fresh, [], NOW, null)?.case).toBe('telegram')
  })

  it('every key fits the profile validator', () => {
    for (const n of eligibleNudges([conn('telegram', 'error'), conn('email', 'error')], [])) {
      expect(nudgeKey(n.case, NOW)).toMatch(/^[a-z][a-z0-9_-]{0,39}$/)
    }
  })
})
