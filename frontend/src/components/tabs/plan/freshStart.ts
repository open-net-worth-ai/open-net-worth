// plan/freshStart.ts — "Start fresh from <month>": the wire + copy half of the budget's
// blank-slate action (the UI half lives in useFreshStart.tsx).
//
// A fresh start does TWO things server-side, as one transaction: it clears the month's
// assignments and moves `budget_config.rollover_start`, the month the envelope carry walk
// starts from. No transaction and no envelope is altered. The before-image stays SERVER-SIDE;
// the write hands back only a one-shot `token`, and Undo posts that token — the client never
// holds, and therefore can never forge, the amounts being restored (ADR
// 2026-09-08-budget-fresh-start-moves-the-floor-not-the-data, amended the same day).
//
// "Resume earlier history" is the OTHER, smaller write: the floor only, via PUT budget-config.
// Assignments a fresh start cleared do NOT come back that way — the copy says so.
import { authFetch } from '../../../api/client'
import { monthLabel } from '../../../hooks/useBudget'
import { buildRtaLines, type RtaLine } from './rtaLines'

export const BUDGET_CONFIG_URL = '/api/planning/budget-config'

/** GET/PUT /api/planning/budget-config — mirrors services.budgets.read_budget_config. */
export interface BudgetConfig {
  /** The carry-walk floor (YYYY-MM); null = carry the whole history. */
  rollover_start: string | null
  budget_start: string | null
  pre_budget_baseline: number | null
}

/** GET /api/planning/fresh-start-preview — what starting fresh WOULD do; writes nothing.
 *  `ready_to_assign_after` is the real RTA fold run at the hypothetical floor, so the figure
 *  the dialog promises is the figure the month payload reports once confirmed. It is the SAME
 *  identity the RTA popover renders (ADR
 *  2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category) — no residual term,
 *  ever: `cash_now + card_credit − baseline − held_in_envelopes − held_outside_envelopes −
 *  card_spend_not_covered − assigned_in_future` (see `freshStartLedger`). */
export interface FreshStartPreview {
  month: string
  ready_to_assign_after: number
  /** On-budget cash as of the END of the month itself — the identity is measured at the cash,
   *  this month's own activity included, never the previous month's opening frame. */
  cash_now: number
  /** Every card's balance above zero at the end of the month — budget money beside `cash_now`
   *  (ADR 2026-11-02). Optional so an older server reads as 0. */
  card_credit?: number
  /** The SIGNED Σ of every envelope's Available at the hypothetical floor — normally 0
   *  (assigned is zeroed and nothing carries), but NOT always: a same-month refund into an
   *  envelope, or a same-month covered card swipe, leaves a POSITIVE row even so, and a
   *  same-month overspend leaves a NEGATIVE one the envelope holds until the roll. */
  held_in_envelopes: number
  /** What this month's uncategorized/unlinked CASH spending holds back (ADR 2026-09-10) — ≤ 0,
   *  and untouched by a fresh start in either half (it depends only on the month's own
   *  activity, which the write neither clears nor re-floors), but a term of the identity all
   *  the same, so the ledger cannot close without it. */
  held_outside_envelopes: number
  /** This month's card spending no envelope covered — 0 on a book with no cards, or where
   *  every swipe drew on a funded envelope. */
  card_spend_not_covered: number
  /** The pre-budget cutover capture — 0 on a book with no cutover. */
  baseline: number
  /** Present ONLY when a LATER month's own assignments clamp this month's displayed figure
   *  (`_display_rta`'s owner-decided rule: a dollar promised to a later month can't be promised
   *  twice) — absent, never zero, otherwise. */
  assigned_in_future?: number
}

/** POST /api/planning/fresh-start → `{token, month}`. The token NAMES the server-held
 *  before-image (floor + cleared assignments); it never carries it. One-shot — a second undo,
 *  or a token from an older fresh start, is a 409. */
export interface FreshStartHandle {
  token: string
  month: string
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T
  let detail = `Request failed (${response.status})`
  try {
    const body = await response.json()
    if (body?.detail) detail = String(body.detail)
  } catch {
    /* non-JSON error body */
  }
  throw new Error(detail)
}

export async function fetchBudgetConfig(): Promise<BudgetConfig> {
  return readJson<BudgetConfig>(await authFetch(BUDGET_CONFIG_URL))
}

export async function fetchFreshStartPreview(ym: string): Promise<FreshStartPreview> {
  return readJson<FreshStartPreview>(
    await authFetch(`/api/planning/fresh-start-preview?ym=${encodeURIComponent(ym)}`),
  )
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** The floor-only write, behind "Resume earlier history" (`null`) — NOT the inverse of a fresh
 *  start any more, since it restores no assignments. */
export async function putRolloverStart(rolloverStart: string | null): Promise<BudgetConfig> {
  return readJson<BudgetConfig>(
    await authFetch(BUDGET_CONFIG_URL, {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify({ rollover_start: rolloverStart }),
    }),
  )
}

/** Start fresh: one server transaction, clearing the month's assignments and moving the floor.
 *  Returns the handle Undo needs — a token, not the data. */
export async function postFreshStart(ym: string): Promise<FreshStartHandle> {
  return readJson<FreshStartHandle>(
    await authFetch('/api/planning/fresh-start', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ ym }),
    }),
  )
}

/** Undo a fresh start by token. The server replays its OWN before-image — nothing about the
 *  amounts is client-supplied. 204, no body. */
export async function postFreshStartUndo(token: string): Promise<void> {
  const res = await authFetch('/api/planning/fresh-start/undo', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ token }),
  })
  if (res.ok) return
  await readJson(res) // throws with the server's own detail
}

// ── Copy (pure — the labels the menu, dialog, toast and state line share) ───────────────

/** "September 2026" → "Sep 2026" (the compact form the state line uses). */
export function shortMonthLabel(ym: string): string {
  const [name, year] = monthLabel(ym).split(' ')
  return year ? `${(name ?? '').slice(0, 3)} ${year}` : monthLabel(ym)
}

/** "September" — the month name on its own, for the dialog's copy. */
export function monthName(ym: string): string {
  return monthLabel(ym).split(' ')[0] ?? ym
}

/** The closing paragraph — every consequence of confirming, in one breath. */
export function freshStartConsequencesLabel(ym: string): string {
  return (
    `Envelopes start over from ${monthName(ym)} — this month's assignments are cleared. ` +
    "This month's spending still shows in its envelopes. Earlier months hide until you " +
    'resume history. No transactions change — undo any time.'
  )
}

/** The inverse action's body. It restores the floor and NOTHING else, which the user has to be
 *  told: Undo (7s, right after confirming) is what brings cleared assignments back. */
export function resumeHistoryLabel(ym: string): string {
  return (
    `Envelopes carry their balances from before ${monthLabel(ym)} again and Ready to Assign ` +
    'is recomputed — no transactions change. Assignments cleared by the fresh start are not ' +
    'restored.'
  )
}

/** The promised Ready to Assign showing its own arithmetic, through the SAME builder the RTA
 *  popover uses (`rtaLines.buildRtaLines`) — one order, one set of labels, one hideability rule,
 *  so a term can never read differently depending on which surface you opened. The rows sum to
 *  `ready_to_assign_after` to the penny, EXACTLY, on every book (ADR
 *  2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category), never a residual
 *  approximation.
 *
 *  There is no "Already assigned" row: a fresh start CLEARS the month's assignments, so at the
 *  new floor there is nothing assigned to subtract. "Held in envelopes" is a DIFFERENT fact —
 *  what a same-month refund, a same-month covered card swipe, or a same-month overspend leaves
 *  in the rows even after the clear — normally 0, and shown AT 0 because it is half of what the
 *  identity means. An earlier version of this ledger omitted it on the assumption it was always
 *  0, which happened to hold on the books it was tried against but was not true by
 *  construction.
 *
 *  `assigned_in_future` is absent, never zero, when no later month clamps the figure — the same
 *  row the builder hides at zero, so the two spellings collapse to one. */
export function freshStartLedger(p: FreshStartPreview): RtaLine[] {
  return buildRtaLines({
    onBudgetCash: p.cash_now,
    cardCredit: p.card_credit ?? 0,
    heldInEnvelopes: p.held_in_envelopes,
    heldOutsideEnvelopes: p.held_outside_envelopes,
    cardSpendNotCovered: p.card_spend_not_covered,
    baseline: p.baseline,
    assignedInFuture: p.assigned_in_future ?? 0,
    rta: p.ready_to_assign_after,
  })
}

/** The kebab item. Already started fresh from THIS month → past tense, no ellipsis (the
 *  dialog behind it offers the inverse, "Resume earlier history"). */
export function freshStartMenuLabel(ym: string, rolloverStart: string | null): string {
  return rolloverStart === ym
    ? `Started fresh from ${monthLabel(ym)}`
    : `Start fresh from ${monthLabel(ym)}…`
}

/** The undo pill's message — the full sentence in both directions (owner decision: the pill
 *  widens on a phone rather than the copy shrinking; see `.pl-undo` in plan.css). */
export function freshStartToastLabel(ym: string, rolloverStart: string | null): string {
  return rolloverStart === null
    ? 'Carrying earlier history again'
    : `Budget starts fresh from ${monthLabel(ym)}`
}
