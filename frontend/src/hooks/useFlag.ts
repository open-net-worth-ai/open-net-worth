// Feature flags (docs/FEATURE_FLAGS.md). The backend registry is the only place a flag is
// declared; this reads its resolved values through the query cache, which every write
// revalidates (a flag write publishes on GET /api/events), so a flip in Settings reaches every
// open screen. Switch at ONE seam: `useFlag('x') ? <New /> : <Old />`, never `if` sprinkled
// through a component.
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'
import type { QueryCacheResult } from '../state/queryCache'

export const FEATURE_FLAGS_KEY = '/api/feature-flags'

export type FeatureFlagSource = 'test' | 'demo' | 'env' | 'stored' | 'default'

export interface FeatureFlag {
  name: string
  description: string
  kind: 'release' | 'kill_switch'
  default: boolean
  enabled: boolean
  source: FeatureFlagSource
  stored: boolean | null
  env_var: string
  owner: string
  added: string
  remove_by: string
}

export interface FeatureFlagList {
  flags: FeatureFlag[]
  locked: boolean
}

export async function fetchFeatureFlags(): Promise<FeatureFlagList> {
  const r = await authFetch(FEATURE_FLAGS_KEY)
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as FeatureFlagList
}

export function useFeatureFlags(): QueryCacheResult<FeatureFlagList> {
  return useQueryCache(FEATURE_FLAGS_KEY, fetchFeatureFlags)
}

// The vitest seam — see `overrideFlagsForTests` below. Empty outside tests.
const testOverrides = new Map<string, boolean>()

/**
 * Whether `name` is on; `undefined` until the list has loaded. Render a placeholder (or the
 * old path) for `undefined` — never guess `true`. A name the loaded list does not declare
 * throws: a typo must not silently run the old path forever.
 */
export function useFlag(name: string): boolean | undefined {
  const overridden = testOverrides.has(name)
  const { data } = useQueryCache(FEATURE_FLAGS_KEY, fetchFeatureFlags, { enabled: !overridden })
  if (overridden) return testOverrides.get(name)
  if (!data) return undefined
  const flag = data.flags.find((f) => f.name === name)
  if (!flag) throw new Error(`Unknown feature flag "${name}" — declare it in core/feature_flags.py`)
  return flag.enabled
}

/** Force flags in a vitest test; returns the restore function (call it in `afterEach`). To
 *  prove both paths, wrap the affected tests in `describe.each([false, true])`. */
export function overrideFlagsForTests(values: Record<string, boolean>): () => void {
  const saved = new Map(testOverrides)
  for (const [k, v] of Object.entries(values)) testOverrides.set(k, v)
  return () => {
    testOverrides.clear()
    for (const [k, v] of saved) testOverrides.set(k, v)
  }
}
