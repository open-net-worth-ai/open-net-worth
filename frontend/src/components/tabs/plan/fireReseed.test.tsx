// @vitest-environment jsdom
import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  fireReseedAction,
  fireSeedKey,
  stableJson,
  useFireReseed,
  type FireSeedConfig,
} from './fireReseed'

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

describe('useFireReseed — the wiring the Outlook runs', () => {
  type Props = {
    config: FireSeedConfig
    configVersion: number
    fireVersion: number
    fireSaveVersion: number
    hasSavedBaseline: boolean
  }
  function mount(initial: Partial<Props> = {}) {
    const apply = vi.fn()
    const props: Props = {
      config: base,
      configVersion: 0,
      fireVersion: 0,
      fireSaveVersion: 0,
      hasSavedBaseline: true,
      ...initial,
    }
    const hook = renderHook((p: Props) => useFireReseed({ ...p, apply }), { initialProps: props })
    const advance = (next: Partial<Props>) => {
      Object.assign(props, next)
      hook.rerender({ ...props })
    }
    return { apply, advance, hook }
  }

  it('first load: a saved baseline wins; without one the config seeds, unsaved', () => {
    expect(mount({ hasSavedBaseline: true }).apply).not.toHaveBeenCalled()
    expect(mount({ hasSavedBaseline: false }).apply).toHaveBeenCalledWith(base, false)
  })

  it('an unrelated write (same values, no settings save) never re-seeds or re-saves', () => {
    const { apply, advance } = mount()
    for (let v = 1; v <= 5; v++) advance({ fireVersion: v, configVersion: v })
    expect(apply).not.toHaveBeenCalled()
  })

  it('waits for the fresh config: a version not yet fetched is not handled with stale values', () => {
    const { apply, advance } = mount()
    advance({ fireVersion: 1, config: { ...base, targetAge: 58 } }) // configVersion still 0
    expect(apply).not.toHaveBeenCalled()
    advance({ configVersion: 1 })
    expect(apply).toHaveBeenCalledWith({ ...base, targetAge: 58 }, true)
  })

  it('an explicit FIRE Settings save re-seeds and saves even when nothing changed', () => {
    const { apply, advance } = mount()
    advance({ fireVersion: 1, configVersion: 1, fireSaveVersion: 1 })
    expect(apply).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledWith(base, true)
  })

  it("the Outlook's own save is not news: its counter bump re-seeds nothing", () => {
    const { apply, advance, hook } = mount()
    hook.result.current.markOwnSave()
    advance({ fireVersion: 1, configVersion: 1, fireSaveVersion: 1 })
    expect(apply).not.toHaveBeenCalled()
    // a later, real settings save still re-seeds
    advance({ fireVersion: 2, configVersion: 2, fireSaveVersion: 2 })
    expect(apply).toHaveBeenCalledTimes(1)
  })
})

describe('stableJson', () => {
  it('ignores key order, so a server echo with reordered keys is the same baseline', () => {
    expect(stableJson({ a: 1, b: { y: [1, { q: 1, p: 2 }], x: 2 } })).toBe(
      stableJson({ b: { x: 2, y: [1, { p: 2, q: 1 }] }, a: 1 }),
    )
    expect(stableJson({ a: 1 })).not.toBe(stableJson({ a: 2 }))
    expect(stableJson(null)).toBe('null')
  })
})
