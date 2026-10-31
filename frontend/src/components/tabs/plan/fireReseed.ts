// fireReseed — when Outlook re-seeds its levers from a fresh /api/fire config.
//
// Outlook refetches /api/fire whenever its fireVersion prop advances. That prop is the FIRE
// Settings save counter PLUS the 'profile' data version, and the 'profile' version advances on
// every app-wide '*' data_changed event — any REST write anywhere, including Outlook's own
// PUT /api/plan/baseline. Treating every advance as "new settings" reset the what-if levers on
// each refetch (a dragged Retire pill snapped back to the plan age) and re-saved the baseline,
// whose write announced another change: a refetch/save loop about once a second.
//
// So a refetch re-seeds only when it is an explicit FIRE settings save, or when the config
// values actually changed (e.g. Lucius edited the profile's FIRE fields).

import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

export interface FireSeedConfig {
  monthlySaving: number
  expectedReturn: number
  volatility: number
  monthlyRetirementSpend: number
  withdrawalRate: number
  lifeExpectancy: number
  targetAge: number
}

/** Identity of the config values that seed the levers (other /api/fire fields don't). */
export function fireSeedKey(f: FireSeedConfig): string {
  return JSON.stringify([
    f.monthlySaving,
    f.expectedReturn,
    f.volatility,
    f.monthlyRetirementSpend,
    f.withdrawalRate,
    f.lifeExpectancy,
    f.targetAge,
  ])
}

export type FireReseed = 'skip' | 'seed' | 'seed-and-save'

export function fireReseedAction(input: {
  /** No config has been handled yet (the tab's first load). */
  firstSeed: boolean
  /** A saved plan baseline exists; on the first load it wins over the raw config. */
  hasSavedBaseline: boolean
  /** The FIRE Settings save counter advanced since the last handled config. */
  explicitSave: boolean
  /** The seeding values differ from the last handled config. */
  configChanged: boolean
}): FireReseed {
  if (input.firstSeed) return input.hasSavedBaseline ? 'skip' : 'seed'
  return input.explicitSave || input.configChanged ? 'seed-and-save' : 'skip'
}

/**
 * The Outlook's re-seed wiring. `apply` runs once per HANDLED config: a refetched config is
 * handled when `configVersion` catches up with `fireVersion` (it advances only alongside the
 * fresh `config`, so a still-stale config is never mistaken for the new one).
 *
 * `markOwnSave` is for the Outlook's own "Update my FIRE plan", called just before it bumps the
 * shared settings-save counter: it already saved the baseline and the profile, so that bump is
 * not news to re-seed from. The refetched values still re-seed once if they differ from the last
 * handled config — the same values the user just saved, so the levers don't move.
 */
export function useFireReseed(opts: {
  config: FireSeedConfig
  configVersion: number
  fireVersion: number
  fireSaveVersion: number
  hasSavedBaseline: boolean
  apply: (config: FireSeedConfig, save: boolean) => void
}): { markOwnSave: () => void } {
  const latest = useRef(opts)
  useLayoutEffect(() => {
    latest.current = opts
  })
  const seededVer = useRef(-1)
  const seededFireKey = useRef<string | null>(null)
  const seededSaveVer = useRef(opts.fireSaveVersion)
  const { configVersion, fireVersion } = opts
  useEffect(() => {
    if (configVersion !== fireVersion || seededVer.current === fireVersion) return
    const { config, fireSaveVersion, hasSavedBaseline, apply } = latest.current
    const firstSeed = seededVer.current === -1
    seededVer.current = fireVersion
    const fireKey = fireSeedKey(config)
    const action = fireReseedAction({
      firstSeed,
      hasSavedBaseline,
      explicitSave: fireSaveVersion !== seededSaveVer.current,
      configChanged: fireKey !== seededFireKey.current,
    })
    seededFireKey.current = fireKey
    seededSaveVer.current = fireSaveVersion
    if (action !== 'skip') apply(config, action === 'seed-and-save')
  }, [configVersion, fireVersion])
  const markOwnSave = useCallback(() => {
    seededSaveVer.current += 1
  }, [])
  return { markOwnSave }
}

/** Key-order-independent JSON, for comparing a stored object with one the server echoes back
 *  (Postgres JSONB reorders keys, so plain JSON.stringify can see a change that isn't one). */
export function stableJson(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : v,
  )
}
