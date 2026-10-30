import { describe, expect, it } from 'vitest'
import { fireReseedAction, fireSeedKey, type FireSeedConfig } from './fireReseed'

const base: FireSeedConfig = {
  monthlySaving: 4000,
  expectedReturn: 7,
  volatility: 15,
  monthlyRetirementSpend: 5000,
  withdrawalRate: 4,
  lifeExpectancy: 90,
  targetAge: 65,
}

describe('fireReseedAction', () => {
  it('first load: a saved baseline wins, otherwise seed from the config without saving', () => {
    const first = { firstSeed: true, explicitSave: false, configChanged: true }
    expect(fireReseedAction({ ...first, hasSavedBaseline: true })).toBe('skip')
    expect(fireReseedAction({ ...first, hasSavedBaseline: false })).toBe('seed')
  })

  it('a refetch caused by an unrelated write (same values, no settings save) leaves the levers alone', () => {
    // The loop: Outlook's own PUT /api/plan/baseline announced '*', which bumped fireVersion,
    // refetched the same config and re-seeded + re-saved, announcing '*' again.
    for (const hasSavedBaseline of [true, false]) {
      expect(
        fireReseedAction({
          firstSeed: false,
          hasSavedBaseline,
          explicitSave: false,
          configChanged: false,
        }),
      ).toBe('skip')
    }
  })

  it('changed values (a Lucius profile edit) re-seed and re-save the baseline', () => {
    expect(
      fireReseedAction({
        firstSeed: false,
        hasSavedBaseline: true,
        explicitSave: false,
        configChanged: true,
      }),
    ).toBe('seed-and-save')
  })

  it('an explicit FIRE Settings save re-seeds even when the values did not change', () => {
    expect(
      fireReseedAction({
        firstSeed: false,
        hasSavedBaseline: true,
        explicitSave: true,
        configChanged: false,
      }),
    ).toBe('seed-and-save')
  })
})

describe('fireSeedKey', () => {
  it('is stable for equal values and changes with any seeding field', () => {
    expect(fireSeedKey({ ...base })).toBe(fireSeedKey(base))
    for (const k of Object.keys(base) as (keyof FireSeedConfig)[]) {
      expect(fireSeedKey({ ...base, [k]: base[k] + 1 })).not.toBe(fireSeedKey(base))
    }
  })

  it('ignores fields that do not seed the levers', () => {
    const withExtra = { ...base, netWorth: 123, currentAge: 40 } as FireSeedConfig
    expect(fireSeedKey(withExtra)).toBe(fireSeedKey(base))
  })
})
