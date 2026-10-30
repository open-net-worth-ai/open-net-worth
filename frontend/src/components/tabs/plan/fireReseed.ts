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
