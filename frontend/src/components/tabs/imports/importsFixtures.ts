// Synthetic runs for the Imports & sync tests: never a real plan, name or figure.

import type { Preview, RunRecord } from './runs'

export const PLANS = { budgets: [{ id: 'p1', name: 'Household', currency: 'USD' }] }

export const PREVIEW: Preview = {
  plan_name: 'Household',
  currency: 'USD',
  accounts: [
    {
      name: 'Checking',
      ynab_type: 'checking',
      type: 'cash',
      closed: false,
      opening_balance: 100,
      transactions: 4,
    },
  ],
  categories: 3,
  groups: 2,
  transactions: 4,
  splits: 1,
  transfers: 1,
  budget_months: 2,
  warnings: ['1 transfer(s) have no matching row in the other account.'],
  book_state: 'fresh',
  base_currency: 'SGD',
  currency_change: true,
  currency_blocked: false,
  money_data: [],
  prune: { categories: ['Vacation'], groups: [] },
}

let n = 0

export function run(over: Partial<RunRecord> = {}): RunRecord {
  n += 1
  return {
    id: `r${n}`,
    provider: 'ynab',
    kind: 'preview',
    trigger: 'manual',
    status: 'done',
    started_at: '2026-10-12T09:00:00+00:00',
    finished_at: '2026-10-12T09:02:14+00:00',
    plan_id: 'p1',
    plan_name: 'Household',
    outcome: 'Would bring across 1 accounts and 4 transactions',
    archived_at: null,
    phase: 'done',
    progress: { done: 0, total: 0 },
    error: null,
    log: [],
    counts: {},
    problems: [],
    warnings: [],
    book_state: 'fresh',
    backup_name: null,
    preview: PREVIEW,
    verification: null,
    ...over,
  }
}

export const MISMATCH = {
  check: 'Checking: transactions',
  unit: 'count' as const,
  ynab_value: 4,
  onw_value: 3,
  difference: -1,
  note: 'YNAB has 4',
}
export const MATCH = {
  check: 'Checking: balance',
  unit: 'money' as const,
  ynab_value: 90,
  onw_value: 90,
  difference: 0,
  note: '',
}
export const MATCH_TWO = { ...MATCH, check: 'Savings: balance' }

export function importRun(over: Partial<RunRecord> = {}): RunRecord {
  return run({
    kind: 'import',
    outcome: 'Created 1 accounts, 4 transactions; 2 of 3 checks match',
    backup_name: 'backup-2026-10-12',
    counts: {
      accounts: { created: 1, existing: 0, refused: 0, failed: 0 },
      transactions: { created: 3, existing: 0, refused: 1, failed: 0 },
    },
    problems: [
      {
        item: 'txn-abc',
        reason: '',
        hint: 'You deleted this transaction here earlier, so it was not booked again.',
      },
    ],
    log: [{ at: '2026-10-12T09:00:05+00:00', line: 'Accounts: 1 created, 0 already there.' }],
    verification: {
      ok: false,
      checks_total: 3,
      checks_matching: 2,
      mismatches: [MISMATCH],
      matches: [MATCH, MATCH_TWO],
    },
    ...over,
  })
}

export const SYNC_ITEM = {
  id: 'c_1',
  kind: 'conflict' as const,
  entity: 'txn' as const,
  title: 'Bistro',
  reason: 'Changed in both Open Net Worth and YNAB since the last sync.',
  onw: { date: '2026-10-01', account: 'Visa', payee: 'Bistro', memo: 'mine', amount: -60 },
  ynab: { date: '2026-10-01', account: 'Visa', payee: 'Bistro', memo: 'theirs', amount: -60 },
}

export function syncStatus(over: Record<string, unknown> = {}) {
  return {
    token_saved: true,
    enabled: true,
    interval: '1h',
    intervals: ['15m', '30m', '1h', '6h', 'daily', 'manual'],
    plan_id: 'p1',
    plan_name: 'Household',
    running: false,
    run_id: null,
    last_run: {
      trigger: 'scheduled',
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      ok: true,
      error: null,
      requests: 2,
      summary: '3 sent to YNAB, 1 pulled, 1 needs review',
      run_id: null,
    },
    next_run_at: new Date(Date.now() + 3600_000).toISOString(),
    conflicts: [SYNC_ITEM],
    ...over,
  }
}

const TOTALS = { sent: 2, pulled: 1, assigned: 1, conflicts: 1, deferred: 1, errors: 1 }

export function syncRun(over: Partial<RunRecord> = {}): RunRecord {
  return run({
    kind: 'sync',
    outcome: '2 sent to YNAB, 1 pulled',
    requests_used: 3,
    preview: null,
    totals: TOTALS,
    txn_changes: [
      {
        direction: 'sent',
        txn_id: 't1',
        date: '2026-10-02',
        payee: 'Bistro',
        account: 'Visa',
        amount: -60,
        currency: 'USD',
        action: 'updated',
        fields: [
          { name: 'Amount', before: '-60.00', after: '-62.50' },
          { name: 'Cleared', before: 'no', after: 'yes' },
        ],
      },
      {
        direction: 'sent',
        txn_id: 't2',
        date: '2026-10-03',
        payee: 'Cafe',
        account: 'Checking',
        amount: -4.5,
        currency: 'USD',
        action: 'created',
        fields: [],
      },
      {
        direction: 'pulled',
        txn_id: 't3',
        date: '2026-10-04',
        payee: 'Grocer',
        account: 'Checking',
        amount: -80.5,
        currency: 'USD',
        action: 'deleted',
        fields: [],
      },
    ],
    assigned_changes: [
      { direction: 'sent', category: 'Rent', month: '2026-10', before: 1200, after: 1300 },
    ],
    conflicts: [
      {
        id: 'c_1',
        description: 'Bistro',
        reason: 'Changed in both Open Net Worth and YNAB since the last sync.',
        onw_value: SYNC_ITEM.onw,
        ynab_value: SYNC_ITEM.ynab,
        status: 'open',
      },
    ],
    deferred: [{ item: 'Lunch', reason: 'Waiting for the next run (YNAB request limit).' }],
    errors: [{ item: 'Taxi', reason: 'YNAB did not accept it: bad date' }],
    not_stored: {},
    ...over,
  })
}
