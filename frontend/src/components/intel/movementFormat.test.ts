import { describe, it, expect } from 'vitest'
import { generatedLabel, notableTitle } from './movementFormat'

// generatedLabel: the paragraph's own timestamp beside the sources pill. Relative inside 24h
// ("3 hours ago") — owner ask 2026-09-21 — absolute, with the date, beyond that. The clock
// keeps clockIn's non-breaking space ("7:02 AM" never splits).

const NOW = Date.parse('2026-09-21T16:35:00Z') // 12:35 PM ET
const ET = 'America/New_York'

describe('generatedLabel', () => {
  it('is relative inside 24 hours', () => {
    expect(generatedLabel('2026-09-21T16:34:40Z', '2026-09-21', ET, 'ET', NOW)).toBe(
      'Generated just now',
    )
    expect(generatedLabel('2026-09-21T16:23:00Z', '2026-09-21', ET, 'ET', NOW)).toBe(
      'Generated 12 min ago',
    )
    expect(generatedLabel('2026-09-21T15:34:00Z', '2026-09-21', ET, 'ET', NOW)).toBe(
      'Generated 1 hour ago',
    )
    expect(generatedLabel('2026-09-21T09:25:07Z', '2026-09-21', ET, 'ET', NOW)).toBe(
      'Generated 7 hours ago',
    )
    expect(generatedLabel('2026-09-20T17:00:00Z', '2026-09-20', ET, 'ET', NOW)).toBe(
      'Generated 23 hours ago',
    )
  })

  it('is absolute in the venue clock beyond 24 hours — with the date when not the session day', () => {
    expect(generatedLabel('2026-09-19T11:02:00Z', '2026-09-18', ET, 'ET', NOW)).toBe(
      'Generated Sep 19, 7:02\u00A0AM ET',
    )
    expect(generatedLabel('2026-09-18T20:05:00Z', '2026-09-18', ET, 'ET', NOW)).toBe(
      'Generated 4:05\u00A0PM ET',
    )
  })

  it('is empty without a stamp', () => {
    expect(generatedLabel(null, '2026-09-21', ET, 'ET', NOW)).toBe('')
    expect(generatedLabel('garbage', '2026-09-21', ET, 'ET', NOW)).toBe('')
  })
})

describe('notableTitle', () => {
  it('compares the move with the usual daily move', () => {
    expect(notableTitle(-7.5, 2.5)).toBe('About 3.0× its usual daily move (2.5%)')
  })
  it('falls back without history', () => {
    expect(notableTitle(6, null)).toBe('A notable move for this kind of asset')
    expect(notableTitle(null, 2)).toBe('A notable move')
  })
})
