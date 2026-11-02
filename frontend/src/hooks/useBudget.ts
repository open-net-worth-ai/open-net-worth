import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { authFetch } from '../api/client'
import { invalidateQueryCache, setCachedQueryData, useQueryCache } from '../state/queryCache'
import { useCategories, type CategoryMeta, STRUCTURAL_CATEGORY_KEYS } from './useCategories'

// Re-export so existing importers (BudgetTable etc.) don't need to change their import path.
export { STRUCTURAL_CATEGORY_KEYS, type CategoryMeta }
const EMPTY_CATEGORIES: CategoryMeta[] = []

// ── API contract types (mirror backend services/planning.budget_month) ──────────

export type TargetType = 'monthly' | 'weekly' | 'yearly' | 'by_date'

export interface BudgetTarget {
  type: TargetType
  amount: number
  date?: string | null
  /** Target deadlines (handoff delta #13, display-only): weekly's due weekday (0-6, Monday=0). */
  due_weekday?: number | null
  /** Monthly's due day (1-28, or -1 = last day of the month). */
  due_day?: number | null
  snooze_until?: string | null
  needs_whole_amount?: boolean
}

export interface BudgetCategoryRow {
  key: string
  label: string
  /** The envelope backing this row ("" for a no-envelope pseudo-row). P4 envelope rows. */
  envelope_id: string
  /** Every category folded into this row; always includes `key` (== categoryIds[0]);
   *  length 1 for a plain 1:1 row, >1 for a merged envelope. */
  category_ids: string[]
  /** The envelope's OWN colour (handoff item 5 — envelope owns its display colour), "" when
   *  unset. Display fallback (client-side): envelope color → first-member category color →
   *  a stable hash of the key. */
  color?: string
  assigned: number
  activity: number
  available: number
  /** Cumulative NET assigned through this month (contributions so far) — drives the "set aside
   *  $X by [date]" goal, whose progress is measured by contributions, not the current balance. */
  assigned_to_date?: number
  target: BudgetTarget | null
  needed_this_month: number
  status: 'none' | 'funded' | 'underfunded' | 'unbudgeted' | 'snoozed'
  /** Credit Card Payments (YNAB-engine credit_card_mode="payment_envelope", ADR
   *  2026-07-09-ynab-engine-credit-card-payment-envelopes.md) — present + non-null only on a
   *  card's payment-envelope row; `null` on every ordinary row. `available_for_payment` is the
   *  row's own raw, SIGNED `available` (never clamped — a card paid with unbudgeted money can
   *  legitimately go negative). `underfunded` here is a DIFFERENT quantity than the target-based
   *  `needed_this_month` above (no target backs a payment row) — never fold the two together.
   *  `owed` is the card's real debt (`max(0, -balance)`), sent in its own right rather than
   *  reconstructed from `underfunded + max(available_for_payment, 0)` — that inversion reads a
   *  paid-off card whose envelope still holds money as owing that money. */
  is_payment_envelope?: boolean
  owed?: number | null
  available_for_payment?: number | null
  underfunded?: number | null
}

export interface BudgetGroupSubtotal {
  assigned: number
  activity: number
  available: number
}

export interface BudgetGroup {
  id: string
  name: string
  collapsed: boolean
  /** True for an auto-managed group pinned to the top + locked ("Synced") in the UI — the
   *  Commitments group, and (credit_card_mode="payment_envelope") the Credit Card Payments
   *  group of per-card payment envelopes. */
  synced?: boolean
  /** True for the DERIVED "Uncategorized" group (server-synthesized, id "__unsorted__") that rolls
   *  up None-category expense activity — not a stored category, never editable/assignable.
   *  Consumers must filter it out of any drag/assign/rename UI and render it separately. */
  unsorted?: boolean
  /** True for the DERIVED "Unbudgeted" group (server-synthesized, id "__unbudgeted__" — RFC
   *  envelope⊥category full split) that lists real, user-labeled categories which carry
   *  activity but no linked envelope — one row per category (never rolled into a single blob
   *  like Uncategorized, since each already has its own real label). Consumers render it read-only,
   *  per-category, with a per-line "Add to envelope…" action; never fold it into the
   *  editable `model`. */
  unbudgeted?: boolean
  categories: BudgetCategoryRow[]
  subtotal: BudgetGroupSubtotal
}

export interface BudgetTotals {
  assigned: number
  activity: number
  available: number
  underfunded: number
}

export interface BudgetNav {
  current: string
  prev: string
  earliest: string
  can_go_back: boolean
}

export interface BudgetMonth {
  month: string
  base_currency: string
  ready_to_assign: number
  /** Cash on hand: Σ balances of `cash` accounts as of this month's end, base ccy — cards are
   *  never in it (a card's debt lives entirely in its own payment envelope). The cash side of
   *  the identity (ADR 2026-09-09 + 2026-09-10): ready_to_assign = on_budget_cash
   *  + card_credit − pre_budget_baseline − held_in_envelopes − held_outside_envelopes
   *  − card_spend_not_covered − assigned_in_future_months (clamped). It is
   *  NOT an upper bound on Ready to Assign: held overspending can put the headline above it. */
  on_budget_cash?: number
  /** Every card's balance ABOVE ZERO as of this month's end, base ccy — an overpaid or refunded
   *  card holds the user's own money, which is budget money beside `on_budget_cash` (ADR
   *  2026-11-02-a-card-in-credit-is-budget-money). Added to it in the identity; a card that
   *  owes money adds nothing. Optional so an older payload reads as 0. */
  card_credit?: number
  /** Read-only flag: do any on-budget cash accounts hold a foreign currency? When true the RTA
   *  breakdown notes that Ready-to-Assign can shift as exchange rates move (foreign cash floats
   *  at the current rate). A plain note, not a computed amount (ADR 2026-07-09). */
  has_foreign_cash?: boolean
  /** The captured cutover baseline (0 for a fresh start — the current default). */
  pre_budget_baseline?: number
  /** Read-only echo of `budget_config.rollover_start` — the month the envelope carry walk
   *  starts from, null when the whole history carries. Written only via
   *  `PUT /api/planning/budget-config` (the Plan kebab's "Start fresh from <month>…"). */
  rollover_start?: string | null
  /** Σ over envelope ROWS of `available`, SIGNED. An envelope HOLDS its own overspend until
   *  month-end, so a red row counts NEGATIVE here and hands its room back to Ready to Assign
   *  until the roll clamps the carry at 0 and charges the hole to the next month. */
  held_in_envelopes: number
  /** What CASH spending with no envelope at all holds back — uncategorized spending plus
   *  every category linked to no envelope, each holding its own hole until the roll exactly as
   *  an envelope does (ADR 2026-09-10). SIGNED and ≤ 0, so it only ever hands Ready to Assign
   *  room BACK; the hole is charged at the month roll if still unresolved. CARD spend never
   *  feeds it — a card had no cash to lose, and its shortfall lives on the card instead. */
  held_outside_envelopes: number
  /** THIS month's card spending that no envelope covered — an in-month activity figure, never
   *  a running card balance. A prior month's uncovered swipe is never re-subtracted here: it
   *  stays on the card, surfaced by its payment row's own `underfunded`/`owed`. */
  card_spend_not_covered: number
  assigned_in_future_months: number
  /** Per-month breakdown of `assigned_in_future_months` (owner ask, 2026-09-15, matching real
   *  YNAB's "Assigned in Future Months" list) — every month strictly after this one that has
   *  ANY assignment data, sorted ascending, RAW (uncapped) per-month sums. Display-only: never
   *  fed back into the RTA identity, which still uses the clamped aggregate above. */
  assigned_in_future_by_month: Array<{ month: string; amount: number }>
  /** The SAME Ready-to-Assign identity, reframed as YNAB's own flow terms (design doc
   *  2026-09-13): carried-in from last month, this month's inflow, minus this month's
   *  Assigned, minus last month's still-open cash overspending (charged now). `null` at and
   *  below `rollover_start` — the floor month has no real predecessor for the walk to read. */
  flow?: {
    prev_month: string
    carried_in: number
    inflow: number
    cash_overspending_prev: number
  } | null
  /** THIS month's own cash overspending, still open right now — becomes next month's
   *  `flow.cash_overspending_prev` at the roll if still unresolved. Always present (even at
   *  the floor, where `flow` itself is null) since it needs no predecessor month. */
  cash_overspending?: number
  /** Positive income received this month with no category (None/blank). It is ALREADY
   *  counted in Ready to Assign — this is a purely informational nudge: give it a real
   *  label for cleaner reports/tax, or a spending category if it's actually an envelope refund. */
  uncategorized_income_base?: number
  groups: BudgetGroup[]
  totals: BudgetTotals
  nav: BudgetNav
  assigned_categories: string[]
}

// POST /api/planning/envelopes response (RFC envelope⊥category full split §Backend contract
// item 1) — a pure envelope, placed last in its group (or Others), no category attached.
export interface EnvelopeCreateResult {
  id: string
  name: string
  group_id: string
  order: number
  category_ids: string[]
  color?: string
}

// DELETE /api/planning/envelopes/{id} response (ADR 2026-09-26-deleting-an-envelope-deletes-it —
// the delete is now for REAL; `undo_token` names a server-held before-image a caller replays via
// `POST /api/planning/envelopes/undo-delete` instead of re-archiving + replaying amounts itself).
// `month`/`available`/`delta_rta` are the REALIZED figures for the `ym` the route was called
// with — the same shape `GET .../delete-preview` returns, read an instant before the write.
export interface DeleteEnvelopeResult {
  ok: boolean
  undo_token: string
  former_category_ids: string[]
  zeroed_assignments: Record<string, number>
  month: string
  available: number
  delta_rta: number
}

// GET /api/planning/envelopes/{id}/delete-preview response — what deleting the envelope would do
// to Ready to Assign for `month`, computed, never written (`services.planning.
// envelope_delete_preview`). `available` is the envelope's raw Available (what the OLD,
// undercounting confirm dialog used to show); `delta_rta` is the TRUE figure — everything
// assigned releases, only the un-covered share of a cash hole stays held elsewhere, so it can
// exceed `available` (docs/launch/BOARD.md: assign $100, spend $50 cash → delta_rta is $100, not
// $50). NOT a hook — the modal that renders it is shared with 50/30/20 (PropDialogs.tsx), which
// has no `useBudget()` of its own.
export interface EnvelopeDeletePreview {
  month: string
  envelope_id: string
  available: number
  delta_rta: number
}

export async function fetchEnvelopeDeletePreview(
  envelopeId: string,
  month: string,
): Promise<EnvelopeDeletePreview> {
  const r = await authFetch(
    `/api/planning/envelopes/${encodeURIComponent(envelopeId)}/delete-preview` +
      `?ym=${encodeURIComponent(month)}`,
  )
  if (!r.ok) throw new Error(`Could not preview this delete (HTTP ${r.status})`)
  return (await r.json()) as EnvelopeDeletePreview
}

// Age of Money — the rolling median days a unit of cash sits between arriving and being
// spent (mirror of services/planning.age_of_money). `forecastable: false` on a young/empty
// book → no fabricated number; render the "not enough history" state.
export interface AgeOfMoney {
  age_days: number | null
  forecastable: boolean
  samples: number
  reason: string
}

// Planning health strip (mirror of services/planning.planning_health = budget_health + AoM).
export interface PlanningHealth {
  month: string
  base_currency: string
  rta: number
  rta_status: 'to_assign' | 'all_assigned' | 'overassigned'
  underfunded_count: number
  underfunded_amount: number
  overspent_count: number
  total_available: number
  total_assigned: number
  age_of_money: AgeOfMoney
}

// ── Month helpers (YYYY-MM, the backend's nav key) ──────────────────────────────

export function thisMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function nextMonth(ym: string): string {
  const y = Number(ym.slice(0, 4))
  const m = Number(ym.slice(5, 7))
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, '0')}`
}

export function prevMonth(ym: string): string {
  const y = Number(ym.slice(0, 4))
  const m = Number(ym.slice(5, 7))
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`
}

const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

export function monthLabel(ym: string): string {
  const y = ym.slice(0, 4)
  const m = Number(ym.slice(5, 7))
  return `${MONTH_NAMES[m - 1] ?? ym} ${y}`
}

// ── Hook ────────────────────────────────────────────────────────────────────────

async function postJson(url: string, body: unknown): Promise<Response> {
  return authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// Surface a {detail} error body as a thrown Error so callers can show it.
async function ensureOk(r: Response): Promise<Response> {
  if (r.ok) return r
  let detail = `Request failed (${r.status})`
  try {
    const body = await r.json()
    if (body?.detail) detail = String(body.detail)
  } catch {
    /* non-JSON error body */
  }
  throw new Error(detail)
}

// One-time migration: flush any keys that were hidden via the old localStorage mechanism
// (plan-hidden-cats) to the real backend, then clear the localStorage entry.
async function migrateLocalStorageHidden(): Promise<void> {
  const LS_KEY = 'plan-hidden-cats'
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return
    const arr: unknown = JSON.parse(raw)
    if (!Array.isArray(arr) || arr.length === 0) {
      localStorage.removeItem(LS_KEY)
      return
    }
    const keys = arr.filter((x): x is string => typeof x === 'string')
    await Promise.all(
      keys.map((key) =>
        authFetch(`/api/categories/${encodeURIComponent(key)}/hide`, { method: 'POST' }).catch(
          () => {
            /* ignore — category may no longer exist or be structural */
          },
        ),
      ),
    )
    localStorage.removeItem(LS_KEY)
  } catch {
    // Migration is best-effort; a failure here is silent.
  }
}

// ── Pure optimistic-reorder helpers ──────────────────────────────────────────────────
// These mirror the backend planning reorder ops so a drag-and-drop repaints the table the
// instant the item is dropped; refresh() then reconciles against authoritative server state
// (and rolls back a rejected write). Pure + exported for unit testing.

// Reorder groups to match `order` (the reorderable group ids, Others excluded). Any group not
// named in `order` (the pinned "others" catch-all) keeps its relative position, appended after
// the ordered set — mirrors the backend keeping Others pinned last.
export function applyGroupOrder(groups: BudgetGroup[], order: string[]): BudgetGroup[] {
  const byId = new Map(groups.map((g) => [g.id, g]))
  const ordered = order.map((id) => byId.get(id)).filter((g): g is BudgetGroup => !!g)
  const placed = new Set(order)
  const rest = groups.filter((g) => !placed.has(g.id))
  return [...ordered, ...rest]
}

// Recompute a group's subtotal from its member rows so a cross-group move shows correct
// per-group totals immediately rather than briefly-stale numbers.
function subtotalOf(categories: BudgetCategoryRow[]): BudgetGroupSubtotal {
  return categories.reduce(
    (acc, c) => ({
      assigned: acc.assigned + c.assigned,
      activity: acc.activity + c.activity,
      available: acc.available + c.available,
    }),
    { assigned: 0, activity: 0, available: 0 },
  )
}

// Move `category` out of its current group into `groupId` at `index` (append when undefined),
// recomputing both affected groups' subtotals. Only called for cross-group moves.
export function applyMoveCategory(
  groups: BudgetGroup[],
  category: string,
  groupId: string,
  index?: number,
): BudgetGroup[] {
  let moved: BudgetCategoryRow | undefined
  const without = groups.map((g) => {
    if (!g.categories.some((c) => c.key === category)) return g
    moved = g.categories.find((c) => c.key === category)
    const categories = g.categories.filter((c) => c.key !== category)
    return { ...g, categories, subtotal: subtotalOf(categories) }
  })
  if (!moved) return groups
  return without.map((g) => {
    if (g.id !== groupId) return g
    const categories = [...g.categories]
    const at =
      index === undefined ? categories.length : Math.max(0, Math.min(index, categories.length))
    categories.splice(at, 0, moved as BudgetCategoryRow)
    return { ...g, categories, subtotal: subtotalOf(categories) }
  })
}

// Reorder one group's members to match `categories` (same set, new order). Any unnamed member
// is appended (defensive — should never happen) so a row is never silently dropped.
export function applyReorderCategories(
  groups: BudgetGroup[],
  groupId: string,
  categories: string[],
): BudgetGroup[] {
  return groups.map((g) => {
    if (g.id !== groupId) return g
    const byKey = new Map(g.categories.map((c) => [c.key, c]))
    const reordered = categories.map((k) => byKey.get(k)).filter((c): c is BudgetCategoryRow => !!c)
    const named = new Set(categories)
    const rest = g.categories.filter((c) => !named.has(c.key))
    return { ...g, categories: [...reordered, ...rest] }
  })
}

export function useBudget(initialMonth?: string) {
  const [month, setMonth] = useState(initialMonth ?? thisMonth())
  // Visible categories — from the shared hook (no hidden items).
  const {
    categories: visibleCats,
    reload: reloadVisibleCats,
    loading: visibleCatsLoading,
  } = useCategories()
  // All categories including hidden — fetched separately to compute hiddenKeys.
  const monthUrl = `/api/planning/month?ym=${month}`
  const healthUrl = `/api/planning/health?ym=${month}`
  const monthAbortRef = useRef<AbortController | null>(null)
  const healthAbortRef = useRef<AbortController | null>(null)
  const monthQuery = useQueryCache(
    monthUrl,
    async () => {
      monthAbortRef.current?.abort()
      const controller = new AbortController()
      monthAbortRef.current = controller
      return authFetch(monthUrl, { signal: controller.signal })
        .then(ensureOk)
        .then((response) => response.json() as Promise<BudgetMonth>)
    },
    { staleTime: 0 },
  )
  const healthQuery = useQueryCache(
    healthUrl,
    async () => {
      healthAbortRef.current?.abort()
      const controller = new AbortController()
      healthAbortRef.current = controller
      const response = await authFetch(healthUrl, { signal: controller.signal })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<PlanningHealth>
    },
    { staleTime: 0 },
  )
  const allCategoriesUrl = '/api/categories?include_hidden=true'
  const allCategoriesQuery = useQueryCache(
    allCategoriesUrl,
    async () => {
      const response = await authFetch(allCategoriesUrl)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const result = await response.json()
      if (!Array.isArray(result)) throw new Error('Invalid categories response')
      return result as CategoryMeta[]
    },
    { staleTime: 0 },
  )
  const notesUrl = '/api/planning/notes'
  const notesQuery = useQueryCache(
    notesUrl,
    async () => {
      const response = await authFetch(notesUrl)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<{ notes: Record<string, string> }>
    },
    { staleTime: 0 },
  )
  // Bridge the month swap with the month we were just showing (owner-reported flakiness,
  // 2026-09-15). The cache is keyed by URL, and the URL carries `ym` — so stepping to another
  // month is a brand-new key with no data, and the tab fell all the way back to its
  // "Loading your budget…" empty state: the whole table, rail and toolbar were torn down and
  // rebuilt for one round-trip on every arrow press.
  //
  // Scope, deliberately narrow: the bridge only applies while the new key is in its INITIAL
  // load (`initialLoading` is already false once that key errors, so a failed month falls
  // through to its own error state instead of silently displaying its neighbour). A refresh of
  // the SAME month never takes this path at all — `monthQuery.data` is defined there. The cost
  // is that the previous month's figures paint under the new month's label for exactly one
  // round-trip, which is the standard keep-previous-data trade and what the owner asked for.
  const lastMonthDataRef = useRef<BudgetMonth | null>(null)
  if (monthQuery.data) lastMonthDataRef.current = monthQuery.data
  const data = monthQuery.data ?? (monthQuery.initialLoading ? lastMonthDataRef.current : null)
  /** True only while the bridge above is actually showing the PREVIOUS month's figures under
   *  the new month's label. Consumers must not present those figures as settled fact — the
   *  Budget tab dims the table + rail and suspends pointer events for the round-trip, which
   *  both says "not yet" and makes it impossible to assign against a stale number. */
  const monthSwitching = !monthQuery.data && monthQuery.initialLoading && !!data
  const health = healthQuery.data ?? null
  const categories = allCategoriesQuery.data ?? EMPTY_CATEGORIES
  const notes = notesQuery.data?.notes ?? {}
  const loading = monthQuery.initialLoading
  const error = monthQuery.error instanceof Error ? monthQuery.error.message : ''

  const refreshMonthQuery = monthQuery.refresh
  const refreshHealthQuery = healthQuery.refresh
  const refreshAllCategoriesQuery = allCategoriesQuery.refresh
  const refreshNotesQuery = notesQuery.refresh
  const reload = useCallback(
    (ym: string) => {
      if (ym === month) {
        void refreshMonthQuery()
        void refreshHealthQuery()
      } else {
        invalidateQueryCache(`/api/planning/month?ym=${ym}`)
        invalidateQueryCache(`/api/planning/health?ym=${ym}`)
      }
    },
    [month, refreshHealthQuery, refreshMonthQuery],
  )

  // Fetch all categories including hidden (to compute hiddenKeys and expose all rows in the
  // budget table, where hidden categories are still shown but visually muted).
  const reloadHiddenCats = useCallback(() => {
    void refreshAllCategoriesQuery()
  }, [refreshAllCategoriesQuery])

  // Derive hidden keys: keys present in the all-including-hidden list but absent from
  // the visible list (from useCategories). Re-runs whenever either list refreshes.
  const hiddenKeys = useMemo(() => {
    // Guard the race: if the visible list hasn't loaded yet, EVERY category would look
    // "hidden" (visKeys empty), flashing the whole budget table empty. Wait for it — the
    // safe interim is "nothing hidden" (show all), not "everything hidden" (show none).
    if (visibleCatsLoading) return new Set<string>()
    const visKeys = new Set(visibleCats.map((c) => c.id))
    return new Set(categories.filter((c) => !visKeys.has(c.id)).map((c) => c.id))
  }, [visibleCats, categories, visibleCatsLoading])

  const reloadCategories = useCallback(() => {
    reloadVisibleCats()
    reloadHiddenCats()
  }, [reloadVisibleCats, reloadHiddenCats])

  const reloadNotes = useCallback(() => {
    void refreshNotesQuery()
  }, [refreshNotesQuery])

  const setNote = useCallback(
    async (envelopeId: string, note: string) => {
      await authFetch(`/api/planning/categories/${encodeURIComponent(envelopeId)}/note`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note }),
      }).then(ensureOk)
      reloadNotes()
    },
    [reloadNotes],
  )

  useEffect(() => {
    // Load the full (including-hidden) category list on mount. The visible list is auto-fetched
    // by useCategories(). After migration, refresh both to pick up newly-hidden keys.
    void migrateLocalStorageHidden().then(() => reloadCategories())
  }, [reloadCategories])

  const syncCommitmentCategories = useCallback(async () => {
    await authFetch('/api/planning/sync-commitment-categories', { method: 'POST' })
    // Mount-time sync can mint a fresh commitment_* category the registry has never seen —
    // refresh BOTH the table and the categories registry (CONF-1), or catLabel() falls back
    // to the raw store key for the rest of the session and the EnvCatsBadge chip shows it.
    reload(month)
    reloadCategories()
  }, [month, reload, reloadCategories])

  const commitmentSyncedRef = useRef(false)

  useEffect(() => {
    // One-shot sync per mount: create real budget categories for each active commitment.
    // Idempotent; runs once so new Commitments group categories appear in the table.
    if (commitmentSyncedRef.current) return
    commitmentSyncedRef.current = true
    syncCommitmentCategories().catch(() => {
      /* best-effort */
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // After a category create/rename/delete: refresh BOTH the table and the registry.
  const refreshAll = useCallback(() => {
    reload(month)
    reloadCategories()
  }, [month, reload, reloadCategories])

  const refresh = useCallback(() => reload(month), [month, reload])

  // ── Money writes (each refreshes the table) ──────────────────────────────────
  // `envelopeId` is the budget row's identity (BudgetCategoryRow.key === .envelope_id —
  // docs/adr/2026-07-05-envelope-id-native.md).
  const assign = useCallback(
    async (envelopeId: string, amount: number) => {
      await postJson('/api/planning/assign', { month, envelope_id: envelopeId, amount }).then(
        ensureOk,
      )
      refresh()
    },
    [month, refresh],
  )

  const move = useCallback(
    async (fromEnvelopeId: string, toEnvelopeId: string, amount: number) => {
      await postJson('/api/planning/move', {
        month,
        from_envelope_id: fromEnvelopeId,
        to_envelope_id: toEnvelopeId,
        amount,
      }).then(ensureOk)
      refresh()
    },
    [month, refresh],
  )

  const setTarget = useCallback(
    async (
      envelopeId: string,
      type: '' | TargetType,
      amount: number,
      date?: string,
      snoozeUntil?: string | null,
      needsWholeAmount?: boolean,
      dueWeekday?: number | null,
      dueDay?: number | null,
    ) => {
      // snoozeUntil === undefined → don't touch snooze; null/'' → un-snooze; 'YYYY-MM' → snooze.
      const body: Record<string, unknown> = { type, amount, date: date ?? null }
      if (snoozeUntil !== undefined) body.snooze_until = snoozeUntil || null
      if (needsWholeAmount !== undefined) body.needs_whole_amount = needsWholeAmount
      // Target deadlines (handoff delta #13) — omit when the caller has nothing to say (the
      // wrong cadence for this field) so a weekly save never clobbers a monthly due_day et al.
      if (dueWeekday !== undefined) body.due_weekday = dueWeekday
      if (dueDay !== undefined) body.due_day = dueDay
      await authFetch(`/api/planning/targets/${encodeURIComponent(envelopeId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // ── Group CRUD ───────────────────────────────────────────────────────────────
  const createGroup = useCallback(
    async (name: string) => {
      await postJson('/api/planning/groups', { name }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  const renameGroup = useCallback(
    async (id: string, name?: string, collapsed?: boolean) => {
      const body: { name?: string; collapsed?: boolean } = {}
      if (name !== undefined) body.name = name
      if (collapsed !== undefined) body.collapsed = collapsed
      await authFetch(`/api/planning/groups/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  const deleteGroup = useCallback(
    async (id: string) => {
      await authFetch(`/api/planning/groups/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // ── Envelope lifecycle (P4 — merge/split/rename; envelope⊥category full split — create) ──
  // Non-optimistic: fire → refresh() reconciles. A merge/split/create reshapes which rows
  // exist (not just their numbers), so an optimistic local patch would be more code than it's
  // worth (the caller in useBudgetModel.ts still patches an optimistic row for createEnvelope
  // specifically, since "Add envelope" needs instant feedback — this hook stays non-optimistic).
  // Creates a PURE envelope — no category (RFC envelope⊥category full split): the group "+" /
  // group-menu "Add envelope" flow, superseding the old create-a-category-which-auto-mints-an-
  // envelope path. `group_id` is omitted (never sent as "") when the caller has none yet.
  const createEnvelope = useCallback(
    async (name: string, groupId?: string, color?: string): Promise<EnvelopeCreateResult> => {
      const r = await postJson('/api/planning/envelopes', {
        name,
        ...(groupId ? { group_id: groupId } : {}),
        ...(color ? { color } : {}),
      }).then(ensureOk)
      const env = (await r.json()) as EnvelopeCreateResult
      refresh()
      return env
    },
    [refresh],
  )

  const renameEnvelope = useCallback(
    async (envelopeId: string, name: string) => {
      await authFetch(`/api/planning/envelopes/${encodeURIComponent(envelopeId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // Envelope owns its display colour (handoff item 5) — the row dot PATCHes the envelope
  // directly (a zero-category envelope has no backing category to recolor against, so this
  // is the only path that works for every row; the Manage-categories dot keeps editing the
  // CATEGORY'S own colour via /api/categories/{key}/color, untouched here).
  const setEnvelopeColor = useCallback(
    async (envelopeId: string, color: string) => {
      await authFetch(`/api/planning/envelopes/${encodeURIComponent(envelopeId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ color }),
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // Delete envelope FOR REAL (ADR 2026-09-26-deleting-an-envelope-deletes-it) — non-synced only
  // (backend 409s otherwise). Detaches every member (→ Unbudgeted), zeroes env-keyed
  // assignments across ALL months (releasing available back to Ready to Assign; overspend
  // charges RTA), hard-deletes the row. `month` names which month's realized ΔRTA to ask the
  // server to echo back (`available`/`delta_rta` on the result) — the SAME figure `delete-
  // preview` showed the confirm dialog a moment earlier. The response's `undo_token` names a
  // server-held before-image — pass it to `undoDeleteEnvelope` to put it all back.
  const deleteEnvelope = useCallback(
    async (envelopeId: string, month: string): Promise<DeleteEnvelopeResult> => {
      const r = await authFetch(
        `/api/planning/envelopes/${encodeURIComponent(envelopeId)}?ym=${encodeURIComponent(month)}`,
        { method: 'DELETE' },
      ).then(ensureOk)
      const body = (await r.json()) as DeleteEnvelopeResult
      refresh()
      return body
    },
    [refresh],
  )

  // Undo a delete (ADR 2026-09-26-deleting-an-envelope-deletes-it) — replays the SERVER-HELD
  // before-image named by `token` exactly (same id; target/note/cap/assignments restored,
  // still-unlinked former categories re-linked). One-shot: a stale/second call 409s.
  const undoDeleteEnvelope = useCallback(
    async (token: string): Promise<void> => {
      await postJson('/api/planning/envelopes/undo-delete', { token }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  const mergeEnvelope = useCallback(
    async (sourceEnvelopeId: string, targetEnvelopeId: string) => {
      await postJson('/api/planning/envelopes/merge', {
        source_envelope_id: sourceEnvelopeId,
        target_envelope_id: targetEnvelopeId,
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // Unlink (handoff item 1 — semantics CHANGED from the P4 baseline): the category becomes
  // envelope-less (surfaces in Outside envelopes → Unbudgeted) instead of minting a fresh 1:1
  // envelope. Legal on a sole member too (1:1 rows now offer "Remove from envelope") — the
  // source envelope stays put (possibly zero-category) and keeps its assigned pool. Response
  // is `{ok, category_id, envelope_id}` — `envelope_id` is the SAME source envelope, there is
  // no `new_envelope_id` to read anymore.
  const splitCategory = useCallback(
    async (envelopeId: string, categoryId: string) => {
      await postJson(`/api/planning/envelopes/${encodeURIComponent(envelopeId)}/split`, {
        category_id: categoryId,
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // Link a category that currently belongs to NO envelope directly onto an existing one — the
  // Unbudgeted surface's per-line "Add to envelope…" (RFC envelope⊥category full split §Frontend:
  // "reuses the AddCategoriesModal flow inverted"). Unlike addCategoriesToEnvelope below, there
  // is no source envelope to split the category out of first — it isn't linked anywhere yet —
  // so this mirrors /split's body shape in reverse rather than composing split+merge.
  const addCategoryToEnvelope = useCallback(
    async (envelopeId: string, categoryId: string) => {
      await postJson(`/api/planning/envelopes/${encodeURIComponent(envelopeId)}/add-category`, {
        category_id: categoryId,
      }).then(ensureOk)
      refresh()
    },
    [refresh],
  )

  // ── "Add categories…" (the container-model direction: stand ON an envelope, pull
  // categories IN). Recomposed for the unlink-doesn't-mint semantics (handoff item 1):
  //   - a SOLE member (1:1 row) still folds its whole envelope in via merge — money follows,
  //     unchanged from the P4 baseline;
  //   - a member of ANOTHER multi-category envelope is unlinked first (split — that envelope
  //     keeps its pool untouched) and the now-envelope-less category is linked directly onto
  //     the target via add-category. There is no `new_envelope_id` round-trip anymore — split
  //     no longer mints a fresh envelope to merge from.
  // Sequential on purpose (every step is a hardened choke-point op); ONE refresh at the end.
  const addCategoriesToEnvelope = useCallback(
    async (
      targetEnvelopeId: string,
      picks: Array<{ categoryId: string; sourceEnvelopeId: string; soleMember: boolean }>,
    ) => {
      for (const p of picks) {
        if (p.soleMember) {
          await postJson('/api/planning/envelopes/merge', {
            source_envelope_id: p.sourceEnvelopeId,
            target_envelope_id: targetEnvelopeId,
          }).then(ensureOk)
        } else {
          // A category in no envelope has nothing to detach from — it's a plain add.
          if (p.sourceEnvelopeId)
            await postJson(
              `/api/planning/envelopes/${encodeURIComponent(p.sourceEnvelopeId)}/split`,
              { category_id: p.categoryId },
            ).then(ensureOk)
          await postJson(
            `/api/planning/envelopes/${encodeURIComponent(targetEnvelopeId)}/add-category`,
            { category_id: p.categoryId },
          ).then(ensureOk)
        }
      }
      refresh()
    },
    [refresh],
  )

  // Drag-and-drop reorders are OPTIMISTIC: we repaint `data` synchronously so the table holds
  // the dropped order the instant @dnd-kit releases its transform, then fire the PATCH and let
  // refresh() reconcile against server truth (which also rolls back a rejected write). Without
  // this, the dragged item snaps back to the old order for the duration of the round-trip and
  // only jumps to the new order on the refetch — the visible flicker.
  const reorderGroups = useCallback(
    async (order: string[]) => {
      setCachedQueryData<BudgetMonth>(monthUrl, (prev) =>
        prev ? { ...prev, groups: applyGroupOrder(prev.groups, order) } : data!,
      )
      await postJson('/api/planning/groups/reorder', { order }).then(ensureOk)
      refresh()
    },
    [data, monthUrl, refresh],
  )

  const moveCategory = useCallback(
    async (category: string, groupId: string, index?: number) => {
      setCachedQueryData<BudgetMonth>(monthUrl, (prev) =>
        prev
          ? { ...prev, groups: applyMoveCategory(prev.groups, category, groupId, index) }
          : data!,
      )
      await postJson('/api/planning/groups/move-category', {
        category,
        group_id: groupId,
        // Omit when undefined so the backend appends; pass the 0-based slot for a drop landing.
        ...(index === undefined ? {} : { index }),
      }).then(ensureOk)
      refresh()
    },
    [data, monthUrl, refresh],
  )

  // Reorder the categories WITHIN a single group (persists the new member order). `categories`
  // must be that group's full member set, reordered (the backend rejects adds/drops here).
  const reorderCategories = useCallback(
    async (groupId: string, categories: string[]) => {
      setCachedQueryData<BudgetMonth>(monthUrl, (prev) =>
        prev
          ? { ...prev, groups: applyReorderCategories(prev.groups, groupId, categories) }
          : data!,
      )
      await postJson(`/api/planning/groups/${encodeURIComponent(groupId)}/reorder-categories`, {
        categories,
      }).then(ensureOk)
      refresh()
    },
    [data, monthUrl, refresh],
  )

  // ── Category CRUD (UI↔Lucius parity with rename_categories/remove_categories tools) ──
  // These hit /api/categories (owned by api/ledger) and refresh both lists. There is no
  // "add" here — creating a category no longer implies a budget row (RFC envelope⊥category
  // full split): the group "+" / "Add envelope" flow above creates a pure envelope instead,
  // and Manage categories (categoriesManager.tsx) is classification-only, no create affordance.
  // With an opaque, immutable id, a rename is a pure relabel — the wire body matches
  // backend.api.ledger.CategoryRename ({new_label, merge_into}); there is no more "new_key"
  // (the id can never change) and this call site never merges.
  const renameCategory = useCallback(
    async (id: string, newLabel: string) => {
      await authFetch(`/api/categories/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_label: newLabel }),
      }).then(ensureOk)
      refreshAll()
    },
    [refreshAll],
  )

  const deleteCategory = useCallback(
    async (key: string, reassign = false, reassignTo?: string) => {
      const params = new URLSearchParams()
      if (reassign) params.set('reassign', 'true')
      if (reassignTo) params.set('reassign_to', reassignTo)
      const qs = params.toString()
      await authFetch(`/api/categories/${encodeURIComponent(key)}${qs ? `?${qs}` : ''}`, {
        method: 'DELETE',
      }).then(ensureOk)
      refreshAll()
    },
    [refreshAll],
  )

  // ── Category hide / unhide (server-persisted in profile.hidden_categories) ──────
  // After a hide/unhide: refresh the budget table (hidden cats are still in the
  // planning data — they're a view filter) and the categories registry (to update
  // the hiddenKeys set so the table re-renders immediately).
  const hideCategory = useCallback(
    async (key: string) => {
      await authFetch(`/api/categories/${encodeURIComponent(key)}/hide`, {
        method: 'POST',
      }).then(ensureOk)
      refreshAll()
    },
    [refreshAll],
  )

  const unhideCategory = useCallback(
    async (key: string) => {
      await authFetch(`/api/categories/${encodeURIComponent(key)}/unhide`, {
        method: 'POST',
      }).then(ensureOk)
      refreshAll()
    },
    [refreshAll],
  )

  // Fetch a preview of what auto-assign would do for the given mode, without
  // committing any writes. The caller converts the steps to a plan and runs it.
  const autoAssignPreview = useCallback(
    async (
      previewMonth: string,
      mode: string,
    ): Promise<{
      steps: Array<{
        envelope_id: string
        label: string
        current_assigned: number
        new_assigned: number
        delta: number
      }>
      total_delta: number
    }> => {
      const r = await postJson('/api/planning/auto-assign-preview', {
        month: previewMonth,
        mode,
      }).then(ensureOk)
      return r.json()
    },
    [],
  )

  return {
    month,
    setMonth,
    data,
    health,
    categories,
    hiddenKeys,
    notes,
    loading,
    monthSwitching,
    error,
    refresh,
    refreshAll,
    assign,
    move,
    setTarget,
    setNote,
    createGroup,
    renameGroup,
    deleteGroup,
    createEnvelope,
    renameEnvelope,
    setEnvelopeColor,
    deleteEnvelope,
    undoDeleteEnvelope,
    mergeEnvelope,
    splitCategory,
    addCategoriesToEnvelope,
    addCategoryToEnvelope,
    reorderGroups,
    moveCategory,
    reorderCategories,
    renameCategory,
    deleteCategory,
    hideCategory,
    unhideCategory,
    autoAssignPreview,
    syncCommitmentCategories,
  }
}
