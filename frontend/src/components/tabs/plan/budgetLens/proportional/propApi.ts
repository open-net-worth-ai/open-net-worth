// budgetLens/proportional/propApi.ts — the 50/30/20 screen's data: one month read — rows are the
// book's ENVELOPES, its groups are the book's Zero-Based GROUPS (owner, 2026-09-26: "same as
// Zero-Based"), each with an optional share of income — and the method's own operations. Envelope
// and group STRUCTURE (name, colour, categories, which group, the order) is shared with Zero-Based
// and goes through the planning routes (envelopeOps.ts), never here.
import { fetchJson } from '../../../../../hooks/serverState'
import { invalidateQueryCache } from '../../../../../state/queryCache'
import { applyBudgetVerb } from '../api'

export interface PropEnvelope {
  id: string
  label: string
  color: string
  /** The Zero-Based group this envelope sits in — its 50/30/20 group too. */
  group_id: string
  group: string
  category_ids: string[]
  cap: number | null
  /** Spent this month — positive = money out. */
  activity: number
  /** Out of the split on its own — still in its group, its spend uncounted. */
  excluded?: boolean
  history: {
    last: number
    avg3: number
    last_by_day: number
    avg3_by_day: number
    /** The median month, and the median of what each month added after this day — the running
     *  month's projection reads these, so one outlier month is never "usual". */
    median3?: number
    rest_median3?: number
  }
}

/** One of the book's Zero-Based groups, as 50/30/20 measures it. */
export interface PropGroup {
  id: string
  name: string
  /** Its share of income, in percent — null until one is set. */
  target: number | null
  /** Outside the split: its spend counts against neither income nor savings. */
  excluded: boolean
  /** Kept by the app (Commitments) — nothing moves in or out. */
  synced: boolean
  activity: number
}

export interface PropIncomeCategory {
  id: string
  label: string
  color: string
  counted: boolean
  last_month: number
}

export interface PropMonth {
  month: string
  status: 'current' | 'past' | 'future'
  day: number
  days_in_month: number
  configured: boolean
  groups: PropGroup[]
  /** Income less every counted group's spend and the spend no row owns; its target is what the
   *  group targets leave (null while none is set). */
  savings: { amount: number; target: number | null }
  income: {
    /** What the month is measured against. */
    amount: number
    source: 'profile' | 'actual'
    profile: number | null
    actual: number
  }
  income_categories: PropIncomeCategory[]
  /** Spend no row can own — subtracted from savings so it is never overstated — and, as rows,
   *  each category it came from. */
  unassigned: {
    uncategorized: number
    no_envelope: number
    categories?: { id: string; label: string; color: string | null; activity: number }[]
  }
  envelopes: PropEnvelope[]
}

export const propMonthKey = (ym: string) =>
  `/api/planning/proportional/month?ym=${encodeURIComponent(ym)}`

/** An envelope or category with no colour of its own reads neutral. The API sends `null` for
 *  one; normalizing here keeps every swatch, dot and icon from receiving an empty colour. */
const NO_COLOR = '#7c8595'

export async function fetchPropMonth(ym: string): Promise<PropMonth> {
  const m = await fetchJson<PropMonth>(propMonthKey(ym))
  return {
    ...m,
    envelopes: m.envelopes.map((e) => ({ ...e, color: e.color || NO_COLOR })),
    income_categories: m.income_categories.map((c) => ({ ...c, color: c.color || NO_COLOR })),
  }
}

/** Every write re-reads every month: a cap applies from its month ONWARD, and income, targets
 *  and groups are month-independent. */
export function invalidatePropMonths(): void {
  invalidateQueryCache((k) => k.startsWith('/api/planning/proportional/month'))
}

async function op(operation: string, params: Record<string, unknown>) {
  const out = await applyBudgetVerb('proportional', operation, params)
  invalidatePropMonths()
  return out
}

/** Set or clear (null) groups' shares of income. */
export const setGroupTargets = (targets: Record<string, number | null>) =>
  op('set_group_targets', { targets })
export const setGroupExcluded = (groupId: string, excluded: boolean) =>
  op('set_group_excluded', { group_id: groupId, excluded })
export const setCap = (envelopeId: string, month: string, amount: number) =>
  op('set_cap', { envelope_id: envelopeId, month, amount })
export const setCaps = (month: string, caps: Record<string, number>) =>
  op('set_caps', { month, caps })
export const setIncomeCategories = (categoryIds: string[] | null) =>
  op('set_income_categories', { category_ids: categoryIds })
export const copyTargetsToCaps = (month: string) => op('copy_targets_to_caps', { month })
export const configure = (groupTargets: Record<string, number> = {}, excludedGroups?: string[]) =>
  op('configure', {
    group_targets: groupTargets,
    ...(excludedGroups?.length ? { excluded_groups: excludedGroups } : {}),
  })
/** One envelope out of the split, or back in — it keeps its group. */
export const setEnvelopeExcluded = (envelopeId: string, excluded: boolean) =>
  op('set_envelope_excluded', { envelope_id: envelopeId, excluded })
