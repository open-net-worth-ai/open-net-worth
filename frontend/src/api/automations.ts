import { authFetch } from './client'

export type ScheduleType =
  | 'daily'
  | 'weekdays'
  | 'weekly'
  | 'monthly'
  | 'interval'
  | 'once'
  | 'rrule'
export type AutomationEventType = 'txn' | 'balance' | 'price'
export type AutomationEventLimit = 'each' | 'hour' | 'day'
export type AutomationRunStatus = 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled'

export type AutomationTrigger =
  | { kind: 'manual' }
  | {
      kind: 'schedule'
      type: ScheduleType
      time?: string
      day?: string | number
      seconds?: number
      at?: string
      rrule?: string
      dtstart?: string
      tzid?: string
    }
  | {
      kind: 'event'
      event: AutomationEventType
      when: Record<string, unknown>
      limit: AutomationEventLimit
    }

export interface Automation {
  id: string
  name: string
  description: string
  instructions: string
  /** `completed` = the schedule ran out (set by the server, never sent by the client). */
  status: 'active' | 'paused' | 'completed'
  trigger: AutomationTrigger
  execution: Record<string, unknown>
  permission_mode?: 'manual' | 'auto' | 'skip'
  always_allowed?: AutomationPermission[]
  /** permissions_v2 only: tools refused in this Automation whatever their global value (plain
   *  tool names). */
  blocked?: string[]
  model: string
  effort: string
  max_runs?: number | null
  run_count: number
  next_run_at: string | null
  last_run_at: string | null
  last_status: string | null
  created_at: string
  updated_at: string
  provenance: Record<string, unknown>
  stats: Record<string, unknown>
}

export interface AutomationPermission {
  src: string
  desc: string
  capability?: string
  tools?: string[]
}

export interface AutomationRunStep {
  label: string
  done?: boolean
  status?: string
}

export interface AutomationRun {
  id: string
  automation_id: string
  status: AutomationRunStatus
  session_id: string | null
  trigger: Record<string, unknown>
  execution: Record<string, unknown>
  model: string
  effort: string
  started_at: string
  finished_at: string | null
  summary: string
  error: string | null
  steps: AutomationRunStep[]
  // The checklist the run declared for itself (set_run_plan), in its own words. The card
  // shows this when it exists; `steps` is the tool-by-tool trail behind it.
  plan?: AutomationRunStep[]
  proposed_changes?: Record<string, unknown>[]
  pending_tool_ids: string[]
  context: Record<string, unknown>
  files: Array<string | { name: string; kind?: string }>
  working_dir: string | null
  input_tokens: number | null
  output_tokens: number | null
  cache_creation_tokens: number | null
  cache_read_tokens: number | null
  cost_usd: number | null
  stats: Record<string, unknown>
}

export interface AutomationListResponse {
  automations: Automation[]
  showing: number
  total: number
  note?: string
}

export interface AutomationRunsResponse {
  runs: AutomationRun[]
  showing: number
  total: number
  note?: string
}

/** One `running`/`waiting` run, or a `completed`/`failed`/`cancelled` run that finished within
 *  the server's recency window, joined with its automation's name — GET
 *  /api/automations/activity's Active section (the Lucius landing screen's Activity panel).
 *  `cancelled` covers BOTH a deliberate user Stop and a run cut short by a server
 *  restart/shutdown (ai/turns.py::shutdown() journals every in-flight turn `cancelled`, not
 *  `failed`) — this app doesn't distinguish the two at the status level, but either way it's a
 *  run that didn't finish clean, which is exactly what this panel exists to surface.
 *  `finished_at` is only set for the terminal statuses. */
export interface ActiveActivityItem {
  automation_id: string
  automation_name: string
  run_id: string
  /** The run's own conversation — opening this row switches the Lucius panel to it instead of
   *  leaving whatever draft conversation was showing. Null for a run that (rare/legacy) never
   *  got one. */
  session_id?: string | null
  status: 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled'
  started_at: string
  finished_at?: string | null
}

/** One `status="active"` automation with a future `next_run_at` — the same endpoint's
 *  Scheduled section. */
export interface ScheduledActivityItem {
  automation_id: string
  automation_name: string
  next_run_at: string
}

/** An automation whose LATEST run failed in the last 24 hours — the Lucius home's Needs
 *  attention. Leaves sooner when a later run (or a retry in the run's conversation) succeeds.
 *  `error` is already plain copy. */
export interface FailedActivityItem extends ActiveActivityItem {
  status: 'failed'
  error?: string | null
}

export interface AutomationActivity {
  active: ActiveActivityItem[]
  scheduled: ScheduledActivityItem[]
  /** Absent from a server that predates it. */
  failed?: FailedActivityItem[]
}

export interface AutomationOption {
  id: string
  label: string
  /** An account's currency (its balance and amounts), or an instrument's quote currency. */
  currency?: string
}

export interface AutomationOptionsResponse {
  accounts: AutomationOption[]
  instruments: AutomationOption[]
  payees: AutomationOption[]
  base_currency?: string
  models: AutomationOption[]
}

export interface AutomationDraft {
  name: string
  description?: string
  instructions: string
  trigger: AutomationTrigger
  /** `{kind: "lucius"}` (the default when omitted, so most templates never set it) or, with the
   *  `transaction_rules` flag on, a Rule's `{kind: "transaction_transform", ...}` (see
   *  `RuleExecution`). Typed as loosely as `Automation.execution` so a full `Automation` can
   *  still seed a draft (duplicate/edit) without narrowing. */
  execution?: Record<string, unknown>
  permission_mode?: 'manual' | 'auto' | 'skip'
  always_allowed?: AutomationPermission[]
  blocked?: string[]
  model?: string
  effort?: string
  status?: 'active' | 'paused'
  max_runs?: number | null
}

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message: string
    try {
      const body = (await response.json()) as { detail?: string; message?: string }
      message = body.detail || body.message || ''
    } catch {
      message = await response.text().catch(() => '')
    }
    throw new Error(message || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function listAutomations(): Promise<AutomationListResponse> {
  return asJson<AutomationListResponse>(await authFetch('/api/automations'))
}

export async function getAutomationOptions(): Promise<AutomationOptionsResponse> {
  return asJson<AutomationOptionsResponse>(await authFetch('/api/automations/options'))
}

export async function getAutomationActivity(): Promise<AutomationActivity> {
  return asJson<AutomationActivity>(await authFetch('/api/automations/activity'))
}

export async function createAutomation(draft: AutomationDraft): Promise<Automation> {
  const result = await asJson<Automation | { automation: Automation }>(
    await authFetch('/api/automations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft),
    }),
  )
  return 'automation' in result ? result.automation : result
}

export async function updateAutomation(
  automationId: string,
  patch: Partial<AutomationDraft> & { status?: 'active' | 'paused' },
): Promise<Automation> {
  const result = await asJson<Automation | { automation: Automation }>(
    await authFetch(`/api/automations/${encodeURIComponent(automationId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  )
  return 'automation' in result ? result.automation : result
}

export async function deleteAutomation(automationId: string): Promise<void> {
  await asJson<unknown>(
    await authFetch(`/api/automations/${encodeURIComponent(automationId)}`, { method: 'DELETE' }),
  )
}

export async function runAutomation(automationId: string): Promise<AutomationRun> {
  const result = await asJson<AutomationRun | { run: AutomationRun }>(
    await authFetch(`/api/automations/${encodeURIComponent(automationId)}/run`, { method: 'POST' }),
  )
  return 'run' in result ? result.run : result
}

export async function stopAutomationRun(
  automationId: string,
): Promise<{ stopped: boolean; reason?: string }> {
  // Stops by AUTOMATION id, not by a session id read out of the client's runs cache. That
  // cache goes stale: on 2026-08-10 one tap cancelled a run that had finished five seconds
  // earlier, and the next tap did nothing at all because the live run had no session_id in
  // the cache yet. The server resolves the live run from the runtime claim instead.
  return await asJson<{ stopped: boolean; reason?: string }>(
    await authFetch(`/api/automations/${encodeURIComponent(automationId)}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }),
  )
}

export async function listAutomationRuns(
  automationId: string,
  limit = 50,
  offset = 0,
): Promise<AutomationRunsResponse> {
  return asJson<AutomationRunsResponse>(
    await authFetch(
      `/api/automations/${encodeURIComponent(automationId)}/runs?limit=${limit}&offset=${offset}`,
    ),
  )
}

export async function getAutomationRun(
  automationId: string,
  runId: string,
): Promise<AutomationRun> {
  const result = await asJson<AutomationRun | { run: AutomationRun }>(
    await authFetch(
      `/api/automations/${encodeURIComponent(automationId)}/runs/${encodeURIComponent(runId)}`,
    ),
  )
  return 'run' in result ? result.run : result
}

// ── Rules (flag `transaction_rules`) ────────────────────────────────────────────────────────
// A Rule is one Automation row — `execution.kind === 'transaction_transform'` — created, edited
// and deleted through the routes above. docs/design/transaction-rules.md is the contract.

export type RuleSplitAmount =
  | { type: 'fixed'; value: number }
  | { type: 'percent'; value: number }
  | { type: 'remainder' }

export interface RuleSplitLine {
  amount: RuleSplitAmount
  category: string
  tags?: string[]
  memo?: string
}

export interface RuleSet {
  category?: string
  tags?: string[]
  note_append?: string
  memo_append?: string
  payee?: string
  route_to_account?: string
}

export interface RuleExecution {
  kind: 'transaction_transform'
  position: number
  stop: boolean
  override_existing: boolean
  set: RuleSet
  splits?: RuleSplitLine[]
}

export interface RuleTrigger {
  kind: 'event'
  event: 'txn'
  when: Record<string, unknown>
}

/** What preview and apply take: a Rule as it would be saved, saved or not. */
export interface RuleBody {
  trigger: RuleTrigger
  execution: RuleExecution
}

export interface RuleAfter {
  category: string | null
  tags: string[] | null
  description: string
  memo: string
  payee_id: string | null
  /** null when the row is not split. */
  splits: unknown[] | null
}

export interface RuleMatch {
  transaction_id: string
  date: string
  description: string
  memo: string
  payee_id: string | null
  payee_name: string | null
  account_id: string | null
  account_name: string | null
  amount: number
  currency: string
  kind: string
  category: string | null
  tags: string[]
  /** null when the row is not split. */
  splits: unknown[] | null
  /** null = the Rule would change nothing on this row. */
  after: RuleAfter | null
  skipped_reason: string | null
}

export interface RulePreviewResponse {
  total: number
  /** Matches the Rule would actually change (not skipped, not already as it says). */
  changeable: number
  matches: RuleMatch[]
}

export interface RuleApplyResult {
  applied: number
  unchanged: number
  skipped: Array<{ transaction_id: string; reason: string }>
  checkpoint_id: string | null
}

export function isRuleAutomation(automation: Pick<Automation, 'execution'>): boolean {
  return automation.execution?.kind === 'transaction_transform'
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  return asJson<T>(
    await authFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

/** `ids` is EVERY Rule id in its new order; resolves to the Rules in that order. */
export async function reorderRules(ids: string[]): Promise<Automation[]> {
  const result = await postJson<{ automations: Automation[] }>('/api/automations/rules/reorder', {
    ids,
  })
  return result.automations
}

export function previewRule(rule: RuleBody, limit = 50): Promise<RulePreviewResponse> {
  return postJson<RulePreviewResponse>('/api/automations/rules/preview', { rule, limit })
}

export function applyRule(rule: RuleBody, transactionIds: string[]): Promise<RuleApplyResult> {
  return postJson<RuleApplyResult>('/api/automations/rules/apply', {
    rule,
    transaction_ids: transactionIds,
  })
}

/** Applies the Rule to EVERY transaction it matches today, past the preview's first page.
 * `excludeIds` are rows the owner unticked; `expectedTotal` is the preview's count — the server
 * refuses (409) if the matches moved since, so nothing unseen is written. */
export function applyRuleToAll(
  rule: RuleBody,
  excludeIds: string[],
  expectedTotal: number,
): Promise<RuleApplyResult> {
  return postJson<RuleApplyResult>('/api/automations/rules/apply-all', {
    rule,
    exclude_ids: excludeIds,
    expected_total: expectedTotal,
  })
}

/** Undoes one apply or run: restores the book's data to the checkpoint it returned. */
export async function undoRuleApply(checkpointId: string): Promise<void> {
  await postJson<unknown>(`/api/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
    mode: 'data',
  })
}

/** Re-runs every active Rule, in order, on the given ledger rows. */
export function runRules(transactionIds: string[]): Promise<RuleApplyResult> {
  return postJson<RuleApplyResult>('/api/automations/rules/run', {
    transaction_ids: transactionIds,
  })
}

/** A Rule as created or edited: `instructions` is the sentence the server shows and Lucius reads. */
export interface RuleDraft extends RuleBody {
  name: string
  instructions: string
  status?: 'active' | 'paused'
}

export async function createRule(draft: RuleDraft): Promise<Automation> {
  const result = await postJson<Automation | { automation: Automation }>('/api/automations', draft)
  return 'automation' in result ? result.automation : result
}

export async function updateRule(
  automationId: string,
  patch: Partial<RuleDraft>,
): Promise<Automation> {
  const result = await asJson<Automation | { automation: Automation }>(
    await authFetch(`/api/automations/${encodeURIComponent(automationId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  )
  return 'automation' in result ? result.automation : result
}
