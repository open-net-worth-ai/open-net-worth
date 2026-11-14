import { authFetch } from '../../../../api/client'
import { invalidateQueryCache, useQueryCache } from '../../../../state/queryCache'
import { detailOf, type RunSummary } from '../../imports/runs'

// Bank sync without AI (ADR 2026-11-13): a bank connector's rows booked by fixed rules, its
// schedule, read budget and review list. Every read goes through the shared query cache; a write
// here revalidates every bank-sync read.

export type BankSyncMode = 'off' | 'data' | 'lucius'

/** Where setup stands: tick accounts → bring in history → turn the sync on → done. */
export type BankSyncStage = 'accounts' | 'history' | 'sync' | 'done'

export interface BankSyncAccount {
  account_id: string
  name: string
  /** What the bank calls the account. */
  /** The bank it's at — one connector can hold several. */
  bank?: string
  bank_name: string
  mask: string
  sync: boolean
  /** Reads left in the bank's rolling 24 h limit; null when the bank sets none. */
  reads_left: number | null
  next_read_at: string | null
  /** Anything from this bank is booked here yet (its history came in). */
  imported: boolean
  /** The book account's currency. */
  currency?: string
  /** What the bank last said this account holds, beside the book's cleared balance (signed as
   *  the book holds it: a card's debt negative). `differs`: they don't agree. */
  bank_balance?: number | null
  cleared_balance?: number | null
  balance_state?: 'match' | 'differs' | null
  /** The book account's own last digits. */
  last_four?: string
  /** How many of its transactions came from this bank, counted in the book. */
  booked?: number
  /** How many of them are still pending at the bank (booked uncleared). */
  booked_pending?: number
  /** The dates of the first and last of them (ISO days). */
  booked_from?: string | null
  booked_to?: string | null
  /** When the bank's access for this app ends (European banks grant it for a set time). */
  access_ends_at?: string | null
  access_ended?: boolean
  /** It ends within two weeks: the page asks for a renewal now (the server decides when). */
  access_renew_soon?: boolean
}

export interface BankSyncStatus {
  provider: string
  label: string
  mode: BankSyncMode
  ready: boolean
  lucius_followup: boolean
  /** Its connector is set up. A bank that isn't is offered under "Add a data sync". */
  connected?: boolean
  interval_hours: number
  /** The schedules this bank may run on (a daily read limit rules out the frequent ones). */
  intervals?: number[]
  daily_reads: number | null
  last_run: RunSummary | null
  next_run_at: string | null
  last_status: string | null
  last_error: string | null
  running: boolean
  to_review: number
  lucius_automations: { id: string; name: string; status: string }[]
  accounts?: BankSyncAccount[]
  /** How far back a first import reaches (the history the bank serves). */
  history_days?: number
  stage?: BankSyncStage
}

export interface ModeResult extends BankSyncStatus {
  paused_automations: string[]
  resumed_automation: string | null
  needs_template: boolean
}

export type ReviewKind = 'modified' | 'vanished' | 'posted' | 'duplicate'

export interface BankRowView {
  date?: string
  amount?: number
  currency?: string
  description?: string
}

export interface BankReview {
  id: string
  provider: string
  provider_label: string
  kind: ReviewKind
  ref: string
  txn_id: string | null
  created_at: string
  /** Sent back with a decision: a newer version of the item is refused. */
  updated_at: string
  detail: {
    before?: BankRowView
    after?: BankRowView
    current?: BankRowView | null
    row?: BankRowView
    user_edited?: boolean
    reason?: string
  }
}

export interface BankRunCounts {
  booked?: number
  already_booked?: number
  pending_skipped?: number
  to_review?: number
  deleted_before?: number
  errors?: number
}

const BASE = '/api/bank-sync'
const isBankSyncKey = (key: string) => key.startsWith(BASE)

/** Re-read every bank sync's status — after a link change made outside this page (the
 *  connector's Accounts list), whose "Sync" line appears only once an account is linked. */
export function refreshBankSyncs(): void {
  invalidateQueryCache(isBankSyncKey)
}

async function readJson<T>(path: string, fallback: string): Promise<T | null> {
  const r = await authFetch(path)
  if (r.status === 404) return null // the flag is off, or this connector has no data sync
  if (!r.ok) throw new Error(await detailOf(r, fallback))
  return (await r.json()) as T
}

/** Every bank connector that can sync without AI; [] while the flag is off. */
export function useBankSyncs(enabled: boolean) {
  return useQueryCache<BankSyncStatus[]>(
    BASE,
    async () => {
      if (!enabled) return []
      const body = await readJson<{ connectors: BankSyncStatus[] }>(
        BASE,
        'Could not read the bank syncs.',
      )
      return body?.connectors ?? []
    },
    { enabled },
  )
}

/** One connector's sync; `data` is null when it has none (404). `poll` re-reads it every two
 *  seconds — only while a run is in progress, so an idle page costs nothing. */
export function useBankSync(provider: string, enabled = true, poll = false) {
  const key = `${BASE}/${provider}`
  return useQueryCache<BankSyncStatus | null>(
    key,
    async () => (enabled ? readJson<BankSyncStatus>(key, 'Could not read this bank sync.') : null),
    { enabled, refetchInterval: poll ? 2000 : undefined },
  )
}

export function useBankSyncRuns(provider: string) {
  const key = `${BASE}/${provider}/runs`
  return useQueryCache<RunSummary[]>(key, async () => {
    const body = await readJson<{ runs: RunSummary[] }>(key, 'Could not read the sync history.')
    return body?.runs ?? []
  })
}

export function useBankReviews(provider: string | null, enabled = true) {
  const key = provider ? `${BASE}/reviews?provider=${provider}` : `${BASE}/reviews`
  return useQueryCache<BankReview[]>(
    key,
    async () => {
      if (!enabled) return []
      const body = await readJson<{ items: BankReview[] }>(key, 'Could not read the review list.')
      return body?.items ?? []
    },
    { enabled },
  )
}

/** A bank-sync write. Resolves to the server's answer, or throws with what it said. */
export async function bankSyncSend<T = unknown>(
  path: string,
  method: 'PUT' | 'POST',
  body?: unknown,
): Promise<T> {
  const r = await authFetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  invalidateQueryCache(isBankSyncKey)
  if (!r.ok) throw new Error(await detailOf(r, 'That didn’t work. Try again in a moment.'))
  return (await r.json()) as T
}

/** What a mode change did to the connector's Lucius Automation, in one line (or null). */
export function modeNotice(result: ModeResult): string | null {
  if (result.paused_automations.length > 0) {
    const names = result.paused_automations.map((n) => `“${n}”`).join(', ')
    const word = result.paused_automations.length === 1 ? 'Automation' : 'Automations'
    return `Turned off the ${word} ${names}, so rows aren’t booked twice.`
  }
  // The switch already says on or off; only what it did to an Automation needs a word.
  return null
}

/** Where a bank's sync lives: its page in Automations → Data Sync. */
export const bankSyncSearch = (provider: string) => ({ view: 'sync', sync: `bank:${provider}` })
