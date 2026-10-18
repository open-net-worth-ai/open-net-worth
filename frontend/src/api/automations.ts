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
  /** Websites `fetch_page` may open without asking in this Automation's runs (owner-set only;
   *  subdomains included). Anything else Lucius tries to open waits for your approval. */
  fetch_hosts?: string[]
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
  /** Only `{kind: "lucius"}` validates server-side (see `services/automations/service.py`);
   *  omitting it defaults to the same value, so most templates never set this explicitly.
   *  Typed as loosely as `Automation.execution` so a full `Automation` can still seed a
   *  draft (duplicate/edit) without narrowing. */
  execution?: Record<string, unknown>
  permission_mode?: 'manual' | 'auto' | 'skip'
  always_allowed?: AutomationPermission[]
  /** Websites `fetch_page` may open without asking in this Automation's runs (owner-set only;
   *  subdomains included). Anything else Lucius tries to open waits for your approval. */
  fetch_hosts?: string[]
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
