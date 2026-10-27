/**
 * useAccountsLedger.ts — Data hook for the Accounts tab.
 *
 * Fetches accounts, transactions (with filters + infinite scroll), categories,
 * payees, and live FX rates. Exposes mutations wired to the real API.
 * Balances are server-authoritative: after any write, the affected account is
 * refetched rather than client-side adjusted.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  fetchCachedQuery,
  getCachedQueryData,
  invalidateQueryCache,
} from '../../../state/queryCache'
import { getBaseCurrency } from '../../../utils/format'
import { emitDataChanged } from '../../../lucius/bus'
import { PORTFOLIO_KEY as SHARED_PORTFOLIO_KEY, useNetWorth } from '../../../hooks/serverState'
import { accountClearedValue, accountValue, fromApiAccount, fromApiTxn } from './data'
import { retiredEnvelopeNotice } from './retiredPaymentEnvelope'
import type { RetiredPaymentEnvelope } from './retiredPaymentEnvelope'
import { TYPE_LABELS, TYPE_ORDER, TYPE_COLOR, LIABILITY_TYPES } from './categories'
import type {
  Account,
  Txn,
  Category,
  Payee,
  FxRates,
  ApiAccount,
  ApiTxn,
  ApiPayee,
  TxnWarning,
  AccountGroup,
  TxnFilters,
  TransactionCreate,
  TransactionUpdate,
  BulkUpdateData,
  AccountCreate,
  AccountUpdate,
  ReconcileData,
  ReconcileResult,
} from './types'

/** A created transaction plus any non-blocking tracking-start warnings from the write. */
export type CreatedTxn = Txn & { warnings?: TxnWarning[] | null }

const PAGE_SIZE = 75
const ACCOUNTS_KEY = '/api/accounts'
const CATEGORIES_KEY = '/api/categories'
const PAYEES_KEY = '/api/payees'
// ONE spelling per resource: a second literal for the same endpoint is a second cache entry,
// so the dedup silently breaks and this tab double-fetches. Imported, never re-declared.
const PORTFOLIO_KEY = SHARED_PORTFOLIO_KEY

async function cachedJson<T>(url: string, force = false): Promise<T> {
  return fetchCachedQuery(
    url,
    async () => {
      const res = await authFetch(url)
      if (!res.ok) throw new Error(`${url} fetch failed`)
      return res.json() as Promise<T>
    },
    { force },
  )
}

const DEFAULT_FILTERS: TxnFilters = {
  accountId: null,
  accountIds: [],
  search: '',
  qField: 'any',
  kind: [],
  category: [],
  payeeIds: [],
  tags: [],
  source: [],
  dateFrom: '',
  dateTo: '',
  cleared: null,
  needsReview: false,
  amounts: [],
  uncategorized: false,
  batchId: null,
}

// Holdings by account_id (market value in base currency)
type HoldingsMap = Record<string, number>

/** Market value held in each account, keyed by account_id — read straight off
 *  `GET /api/portfolio/rows`, which computes it.
 *
 *  This used to re-derive the map here: qty × price × quote_per_unit, FX-converted, with its
 *  own comment admitting it was "the same formula as usePortfolio.compute()". That made the
 *  Accounts tab a THIRD implementation of the app's valuation, free to drift from the other
 *  two — the failure mode that shipped a S$85,000 net-worth headline
 *  (ADR 2026-07-31-overview-aggregates-are-server-computed). */
function holdingsFromServer(payload: { holdings_by_account?: HoldingsMap } | null): HoldingsMap {
  return payload?.holdings_by_account ?? {}
}

// ── Hook return type ──────────────────────────────────────────────────────

/** One account named in an `AccountDeleteConflictError`'s `otherAccounts` — an account whose
 *  OWN history also gets wiped by a force-delete, via a transfer shared with the account being
 *  deleted. */
export interface AccountRef {
  id: string
  name: string
}

/** Thrown by `deleteAccount` on a 409 that a `force: true` retry CAN fix — the account has
 *  referencing transactions beyond its own opening balance. `count`/`otherAccounts` come off
 *  the response's STRUCTURED `detail` object (`{message, transaction_count, other_accounts}`)
 *  — never regexed out of the prose message: the backend's sentence embeds the free-text,
 *  user-chosen account NAME right before the count, so a name like "Fund 1 transaction
 *  reserve" would corrupt a regex parse (security review 2026-09-06, MEDIUM-3).
 *
 *  ONLY ever thrown when the body's `transaction_count` is a genuine number — a caller must be
 *  able to trust `instanceof AccountDeleteConflictError` alone as "safe to offer the force-
 *  delete escalation for". A DIFFERENT 409 shape (e.g. `account_net_worth_linked` — a linked
 *  loan/position/lot/option, which `force: true` cannot do anything about) throws
 *  `AccountDeleteBlockedError` instead; conflating the two used to render a "Force delete and
 *  remove your transactions" button for a refusal force could never satisfy (security review
 *  2026-09-06, second pass finding #2). */
export class AccountDeleteConflictError extends Error {
  count: number
  otherAccounts: AccountRef[]
  constructor(message: string, count: number, otherAccounts: AccountRef[] = []) {
    super(message)
    this.name = 'AccountDeleteConflictError'
    this.count = count
    this.otherAccounts = otherAccounts
  }
}

/** Thrown by `deleteAccount` on a 409 that `force: true` CANNOT fix — the account is still
 *  tied into net worth via a linked loan/open position/lot/option contract
 *  (`error: "account_net_worth_linked"`), or the error body's shape wasn't recognized at all
 *  (an older/unexpected server). The caller must NEVER offer a force-delete escalation for
 *  this — there's nothing `force=true` can do about a linked loan — just surface `message` as
 *  a plain error/toast. */
export class AccountDeleteBlockedError extends Error {
  linkedAssetIds: string[]
  openPositionCount: number
  openLotCount: number
  openOptionCount: number
  constructor(
    message: string,
    linkedAssetIds: string[] = [],
    openPositionCount = 0,
    openLotCount = 0,
    openOptionCount = 0,
  ) {
    super(message)
    this.name = 'AccountDeleteBlockedError'
    this.linkedAssetIds = linkedAssetIds
    this.openPositionCount = openPositionCount
    this.openLotCount = openLotCount
    this.openOptionCount = openOptionCount
  }
}

/** Per-id summary returned by `POST /api/transactions/bulk-update`. `count` is the number of
 *  rows the server actually wrote — NOT `ids.length`, since it may refuse individual rows. */
export interface BulkUpdateResult {
  updated: string[]
  not_found: string[]
  skipped: Array<{ id: string; reason?: string }>
  count: number
}

export interface AccountsLedgerState {
  // data
  accounts: Account[]
  transactions: Txn[]
  categories: Category[]
  payees: Payee[]
  fx: FxRates
  baseCurrency: string
  // loading states
  loading: boolean
  loadingTxns: boolean
  /** The in-flight fetch will REPLACE the rows on screen with a different query's (a filter
   *  change) — not a background revalidation of the same one, nor a load-more append. */
  refiltering: boolean
  /** A further page of transactions is being appended to the register. */
  isLoadingMore: boolean
  error: string | null
  hasLoaded: boolean
  // filters + pagination
  filters: TxnFilters
  setFilters: (patch: Partial<TxnFilters>) => void
  hasMore: boolean
  /** Server-reported total txn count for the current account/date scope (not the loaded page count). */
  total: number
  /** Oldest / newest date in the FILTERED set (server-computed). */
  firstDate: string | null
  lastDate: string | null
  loadMore: () => void
  // derived selectors
  groups: AccountGroup[]
  netWorth: number
  cashFlow: { period: number; cleared: number; uncleared: number }
  /** Cash-accounts-only cleared/uncleared/total — for the All-accounts balance card. */
  cashBalance: { total: number; cleared: number; uncleared: number }
  // mutations
  createTxn: (data: TransactionCreate) => Promise<CreatedTxn>
  updateTxn: (id: string, data: TransactionUpdate) => Promise<Txn>
  deleteTxn: (id: string) => Promise<void>
  toggleCleared: (id: string, cleared: boolean) => Promise<Txn>
  /** "Unlink source" (CDS-6, ADR 2026-09-07) — the one deliberate removal path off an
   *  otherwise append-only `external_refs`. Takes the full row (not just its id) because the
   *  detach route replies with only `external_refs`, not a whole transaction; the rest of the
   *  row is unchanged, so it's patched onto the caller's own copy rather than refetched. */
  detachSourceRef: (txn: Txn, source: string, ref: string) => Promise<Txn>
  bulkUpdate: (ids: string[], data: BulkUpdateData) => Promise<BulkUpdateResult>
  bulkDelete: (ids: string[]) => Promise<void>
  saveAccount: (
    data: AccountCreate | AccountUpdate,
    id?: string,
    onNotice?: (message: string) => void,
  ) => Promise<Account>
  archiveAccount: (id: string) => Promise<void>
  restoreAccount: (id: string) => Promise<void>
  deleteAccount: (id: string, opts?: { force?: boolean }) => Promise<void>
  reconcile: (id: string, data: ReconcileData) => Promise<ReconcileResult>
  // refresh
  /** Returns a Promise so a caller that needs the refreshed data (e.g. a just-created category
   *  appearing in the categories list) can await it instead of racing a fire-and-forget reload. */
  refresh: () => Promise<void>
  refreshTxns: () => void
}

// ── Internal helpers ──────────────────────────────────────────────────────

// account_ids/kind/category/tag/payee_id/source are REPEATED query params server-side
// (backend/api/ledger.py's `Annotated[Optional[List[str]], Query()]` params — a single
// comma-joined value would arrive as ONE literal string containing commas, not a list, so a
// multi-select filter silently matched nothing past the first value). `append()` per value,
// never `join(',')` — a category key or free-form tag may legitimately contain a literal comma.
function appendEach(p: URLSearchParams, key: string, values: string[]): void {
  for (const v of values) p.append(key, v)
}

/** The pre-write row shape a bulk change produces LOCALLY, or `null` when the result is
 *  server-derived and must be refetched instead of guessed. Only fields the server sets
 *  verbatim belong here — never one the backend may re-derive (category runs the precedence
 *  chain, a move re-FXes the leg, a payee link resolves an entity). */
export function localBulkPatch(data: BulkUpdateData): Partial<Txn> | null {
  const patch: Partial<Txn> = {}
  for (const [key, value] of Object.entries(data)) {
    if (value === undefined) continue
    if (key === 'cleared') patch.cleared = value as boolean
    else if (key === 'needs_review') patch.needsReview = value as boolean
    else return null
  }
  return Object.keys(patch).length ? patch : null
}

/** The bulk-update endpoint returns `{updated, not_found, skipped, count}`. Tolerate a
 *  partial/legacy body: an absent `updated` means "all of them", which keeps an optimistic
 *  paint in place rather than reverting rows the server actually wrote. */
export function normalizeBulkResult(raw: unknown, ids: string[]): BulkUpdateResult {
  const r = (raw ?? {}) as Partial<Record<keyof BulkUpdateResult, unknown>>
  const updated = Array.isArray(r.updated) ? (r.updated as string[]) : ids
  const skipped = Array.isArray(r.skipped)
    ? (r.skipped as Array<{ id: string; reason?: string }>)
    : []
  return {
    updated,
    not_found: Array.isArray(r.not_found) ? (r.not_found as string[]) : [],
    skipped,
    count: typeof r.count === 'number' ? r.count : updated.length,
  }
}

export function buildTxnQueryString(filters: TxnFilters, offset: number): string {
  const p = new URLSearchParams()
  if (filters.accountId) p.set('account_id', filters.accountId)
  appendEach(p, 'account_ids', filters.accountIds)
  if (filters.search) {
    p.set('q', filters.search)
    p.set('q_field', filters.qField)
  }
  appendEach(p, 'kind', filters.kind)
  appendEach(p, 'category', filters.category)
  if (filters.dateFrom) p.set('date_from', filters.dateFrom)
  if (filters.dateTo) p.set('date_to', filters.dateTo)
  if (filters.cleared != null) p.set('cleared', String(filters.cleared))
  if (filters.needsReview) p.set('needs_review', 'true')
  if (filters.uncategorized) p.set('uncategorized', 'true')
  if (filters.batchId) p.set('batch_id', filters.batchId)
  appendEach(p, 'tag', filters.tags)
  appendEach(p, 'payee_id', filters.payeeIds)
  appendEach(p, 'source', filters.source)
  if (filters.amounts.length) {
    p.set('amount', filters.amounts.map((a) => `${a.op}:${a.dir}:${a.value}`).join(','))
  }
  p.set('limit', String(PAGE_SIZE))
  p.set('offset', String(offset))
  return p.toString()
}

/**
 * Merge a TransactionUpdate (snake_case API patch) onto a view Txn for an OPTIMISTIC inline edit —
 * reflect the user's change in the row synchronously, before the PUT round-trip. Server-derived
 * fields (baseAmount, splits) are deliberately NOT touched here: the PUT response reconciles
 * the row to the server's authoritative shape (it may re-derive fx/base), which always wins.
 */
export function applyOptimisticTxnPatch(t: Txn, data: TransactionUpdate): Txn {
  const next: Txn = { ...t }
  if (data.date !== undefined) next.date = data.date
  if (data.kind !== undefined) next.kind = data.kind
  if (data.category !== undefined) next.category = data.category ?? null
  if (data.payee_id !== undefined) next.payeeId = data.payee_id ?? null
  if (data.description !== undefined) next.description = data.description ?? ''
  if (data.memo !== undefined) next.memo = data.memo ?? ''
  if (data.tags !== undefined) next.tags = data.tags ?? []
  if (data.cleared !== undefined) next.cleared = !!data.cleared
  if (data.needs_review !== undefined) next.needsReview = !!data.needs_review
  if (data.from_account_id !== undefined) next.fromAccountId = data.from_account_id ?? null
  if (data.to_account_id !== undefined) next.toAccountId = data.to_account_id ?? null
  if (data.from_amount !== undefined) next.fromAmount = data.from_amount ?? null
  if (data.from_currency !== undefined) next.fromCurrency = data.from_currency ?? null
  if (data.to_amount !== undefined) next.toAmount = data.to_amount ?? null
  if (data.to_currency !== undefined) next.toCurrency = data.to_currency ?? null
  if (data.original_amount !== undefined) next.origAmount = data.original_amount ?? null
  if (data.original_currency !== undefined) next.origCurrency = data.original_currency ?? null
  return next
}

// ── Hook ──────────────────────────────────────────────────────────────────

export function useAccountsLedger(onMutate?: () => void): AccountsLedgerState {
  // Fired after every write so the caller can refresh app-wide derived data (global net worth, other
  // tabs, Lucius). Kept in a ref so the mutation callbacks stay stable.
  const onMutateRef = useRef(onMutate)
  onMutateRef.current = onMutate
  const fireMutate = () => {
    invalidateQueryCache(
      (key) =>
        key === ACCOUNTS_KEY ||
        key === CATEGORIES_KEY ||
        key === PAYEES_KEY ||
        key === PORTFOLIO_KEY ||
        key.startsWith('/api/transactions?'),
    )
    onMutateRef.current?.()
  }
  const [accounts, setAccounts] = useState<Account[]>(() => {
    const raw = getCachedQueryData<ApiAccount[]>(ACCOUNTS_KEY)
    const holdings = holdingsFromServer(
      getCachedQueryData<{ holdings_by_account?: HoldingsMap }>(PORTFOLIO_KEY) ?? null,
    )
    return raw?.map((a) => fromApiAccount(a, holdings[a.id] ?? 0)) ?? []
  })
  const initialTxnData = getCachedQueryData<
    | {
        items?: ApiTxn[]
        transactions?: ApiTxn[]
        total?: number
        has_more?: boolean
        first_date?: string | null
        last_date?: string | null
      }
    | ApiTxn[]
  >(`/api/transactions?${buildTxnQueryString(DEFAULT_FILTERS, 0)}`)
  const initialTxnItems = initialTxnData
    ? Array.isArray(initialTxnData)
      ? initialTxnData
      : (initialTxnData.items ?? initialTxnData.transactions ?? [])
    : []
  const [transactions, setTransactions] = useState<Txn[]>(() => initialTxnItems.map(fromApiTxn))
  const [categories, setCategories] = useState<Category[]>(
    () => getCachedQueryData<Category[]>(CATEGORIES_KEY) ?? [],
  )
  const [payees, setPayees] = useState<Payee[]>(() => {
    const value = getCachedQueryData<ApiPayee[] | { payees?: ApiPayee[] }>(PAYEES_KEY)
    const raw = Array.isArray(value) ? value : (value?.payees ?? [])
    return raw.map((p) => ({
      id: p.id,
      name: p.name,
      aliases: p.aliases ?? [],
      defaultCategories: p.default_categories ?? {},
      autocategorize: p.autocategorize ?? false,
      hidden: p.hidden ?? false,
      txnCount: p.txn_count ?? 0,
      lastUsed: p.last_used ?? null,
      rules: p.rules ?? [],
    }))
  })
  const [fx] = useState<FxRates>(() => {
    const base = getBaseCurrency()
    try {
      return { [base]: 1.0, ...JSON.parse(localStorage.getItem('wealthFX') || '{}') }
    } catch {
      return { [base]: 1.0 }
    }
  })

  const [loading, setLoading] = useState(
    () => getCachedQueryData<ApiAccount[]>(ACCOUNTS_KEY) === undefined,
  )
  const [loadingTxns, setLoadingTxns] = useState(false)
  const [refiltering, setRefiltering] = useState(false)
  // Distinct from `loadingTxns` (which also covers the initial/filtered load, where rows are
  // absent and a full-list spinner is correct): this is ONLY true for an in-flight `loadMore`
  // append, so the list can show a small trailing "loading more" row instead of nothing.
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasLoaded, setHasLoaded] = useState(
    () => getCachedQueryData<ApiAccount[]>(ACCOUNTS_KEY) !== undefined,
  )
  const [hasMore, setHasMore] = useState(
    () => !Array.isArray(initialTxnData) && initialTxnData?.has_more === true,
  )
  const [offset, setOffset] = useState(0)
  // Server-reported total for the current query (account + date scope) — the true txn count, not the
  // loaded page count.
  const [total, setTotal] = useState(() =>
    !Array.isArray(initialTxnData) && typeof initialTxnData?.total === 'number'
      ? initialTxnData.total
      : initialTxnItems.length,
  )
  // The FILTERED set's date span, straight from the same response that carries `total`
  // (GET /api/transactions -> first_date/last_date). Not derivable on the client: it only
  // ever holds one page, newest-first, so its own earliest row is just the oldest of the
  // newest N.
  const [firstDate, setFirstDate] = useState<string | null>(null)
  const [lastDate, setLastDate] = useState<string | null>(null)

  const [filters, setFiltersState] = useState<TxnFilters>(DEFAULT_FILTERS)
  const holdingsRef = useRef<HoldingsMap>({})
  // Synchronous in-flight guard for load-more — `loadingTxns` state lags a render, so rapid calls
  // would slip past it and double-load. This ref flips immediately.
  const loadingMoreRef = useRef(false)
  // Request-sequencing guard for loadTxns — shared across every call site (the filter effect,
  // loadMore, bulkUpdate, reconcile, refresh, refreshTxns). Without it, an older in-flight fetch
  // (e.g. the initial unfiltered mount fetch) resolving AFTER a newer one (e.g. a just-applied
  // date filter) would silently clobber the correct state with stale data.
  const loadTxnsAbortRef = useRef<AbortController | null>(null)
  // Which query the rows on screen answer. A remount paints the cached default rows and
  // revalidates that same query — no visible "reloading" for it (owner report 2026-10-27:
  // every return to the tab from Lucius flashed the refetch bar).
  const shownQueryRef = useRef<string | null>(
    initialTxnData ? buildTxnQueryString(DEFAULT_FILTERS, 0) : null,
  )

  // ── Core data fetch (accounts, categories, payees, FX) ─────────────────

  const loadCore = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [rawAccounts, cats, payeeJson, portfolio] = await Promise.all([
        cachedJson<ApiAccount[]>(ACCOUNTS_KEY, silent),
        cachedJson<Category[]>(CATEGORIES_KEY, silent),
        cachedJson<ApiPayee[] | { payees?: ApiPayee[] }>(PAYEES_KEY, silent),
        cachedJson<{ holdings_by_account?: HoldingsMap }>(PORTFOLIO_KEY, silent),
      ])

      // Holdings map: account_id → base market value, computed server-side. The shell-scoped
      // PortfolioProvider owns live-price/FX refreshes; this tab shares that cached read rather
      // than starting a second vendor round trip — or, as it used to, valuing the positions
      // itself with its own copy of the formula.
      holdingsRef.current = holdingsFromServer(portfolio)

      // Accounts
      setAccounts(rawAccounts.map((a) => fromApiAccount(a, holdingsRef.current[a.id] ?? 0)))

      // Categories
      setCategories(cats)

      // Payees (endpoint returns { payees: [...] } in registry mode; tolerate a bare array too)
      {
        const raw: ApiPayee[] = Array.isArray(payeeJson) ? payeeJson : (payeeJson.payees ?? [])
        setPayees(
          raw.map((p) => ({
            id: p.id,
            name: p.name,
            aliases: p.aliases ?? [],
            defaultCategories: p.default_categories ?? {},
            autocategorize: p.autocategorize ?? false,
            hidden: p.hidden ?? false,
            txnCount: p.txn_count ?? 0,
            lastUsed: p.last_used ?? null,
            rules: p.rules ?? [],
          })),
        )
      }

      setHasLoaded(true)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }, [])

  // ── Transactions fetch (filters + pagination) ────────────────────────────

  const loadTxns = useCallback(
    async (currentFilters: TxnFilters, currentOffset: number, append = false, force = false) => {
      // Supersede any in-flight request — only the LAST loadTxns call's response may ever land in
      // state, regardless of which one resolves first (REG2: an out-of-order response otherwise
      // silently overwrites a correctly-filtered result with a stale one).
      loadTxnsAbortRef.current?.abort()
      const ac = new AbortController()
      loadTxnsAbortRef.current = ac
      setLoadingTxns(true)
      const queryKey = buildTxnQueryString(currentFilters, 0)
      if (!append) setRefiltering(queryKey !== shownQueryRef.current)
      try {
        const qs = buildTxnQueryString(currentFilters, currentOffset)
        const url = `/api/transactions?${qs}`
        // The endpoint returns { transactions, total, has_more } when limit is set (always, here);
        // tolerate { items } and a bare array too.
        const data = await fetchCachedQuery<
          | {
              items?: ApiTxn[]
              transactions?: ApiTxn[]
              total?: number
              has_more?: boolean
              first_date?: string | null
              last_date?: string | null
            }
          | ApiTxn[]
        >(
          url,
          async () => {
            const res = await authFetch(url)
            if (!res.ok) throw new Error('Failed to load transactions')
            return res.json()
          },
          // A post-write reload MUST force: fetchCachedQuery hands back the cached entry while
          // it is inside staleTime, so an unforced reload right after a bulk write re-applies
          // the PRE-write rows and the change appears not to have happened.
          { force: append || force },
        )
        // A newer loadTxns call started while this one was in flight — its response (already applied
        // or about to be) is the one that counts. Drop this stale one instead of applying it.
        if (ac.signal.aborted) return
        const items: ApiTxn[] = Array.isArray(data) ? data : (data.items ?? data.transactions ?? [])
        const mapped = items.map(fromApiTxn)
        setTransactions((prev) => (append ? [...prev, ...mapped] : mapped))
        if (!append) shownQueryRef.current = queryKey
        setHasMore(
          !Array.isArray(data) && typeof data.has_more === 'boolean'
            ? data.has_more
            : items.length === PAGE_SIZE,
        )
        if (!Array.isArray(data) && typeof data.total === 'number') setTotal(data.total)
        if (!Array.isArray(data)) {
          setFirstDate(data.first_date ?? null)
          setLastDate(data.last_date ?? null)
        }
      } catch (e) {
        // A deliberate abort (this request was superseded) is not a real error — surfacing it would
        // flash an error toast every time the user changes a filter while a fetch is still in flight.
        if (ac.signal.aborted) return
        setError(e instanceof Error ? e.message : 'Failed to load transactions')
      } finally {
        if (!ac.signal.aborted) {
          setLoadingTxns(false)
          setRefiltering(false)
        }
      }
    },
    [],
  )

  // ── Initial load ─────────────────────────────────────────────────────────

  useEffect(() => {
    loadCore()
  }, [loadCore])

  // Abort any in-flight txn fetch on unmount so its response can never try to set state on an
  // unmounted hook instance.
  useEffect(() => () => loadTxnsAbortRef.current?.abort(), [])

  useEffect(() => {
    if (hasLoaded) {
      setOffset(0)
      loadTxns(filters, 0, false)
    }
  }, [filters, hasLoaded, loadTxns])

  // ── Filter setter ─────────────────────────────────────────────────────────

  const setFilters = useCallback((patch: Partial<TxnFilters>) => {
    setFiltersState((prev) => ({ ...prev, ...patch }))
  }, [])

  // ── Load more (infinite scroll) ───────────────────────────────────────────

  const loadMore = useCallback(() => {
    if (loadingMoreRef.current || !hasMore) return
    loadingMoreRef.current = true
    setIsLoadingMore(true)
    const nextOffset = offset + PAGE_SIZE
    setOffset(nextOffset)
    loadTxns(filters, nextOffset, true).finally(() => {
      loadingMoreRef.current = false
      setIsLoadingMore(false)
    })
  }, [hasMore, offset, filters, loadTxns])

  // ── Derived selectors ─────────────────────────────────────────────────────

  const groups = useMemo<AccountGroup[]>(() => {
    const active = accounts.filter((a) => !a.archived)
    return TYPE_ORDER.map((key): AccountGroup | null => {
      const accts = active.filter((a) => a.type === key)
      if (!accts.length) return null
      const totalBase = accts.reduce((sum, a) => {
        const val = accountValue(a)
        return sum + (LIABILITY_TYPES.has(key) ? -Math.abs(val) : val)
      }, 0)
      return {
        key,
        label: TYPE_LABELS[key] ?? key,
        color: TYPE_COLOR[key] ?? '#6b7484',
        accounts: accts,
        totalBase,
      }
    }).filter((g): g is AccountGroup => g !== null)
  }, [accounts])

  // Net worth has ONE implementation — `ledger.net_worth()` via GET /api/net-worth
  // (ADR 2026-07-31-single-source-net-worth). This used to fold it here from account balances,
  // a FOURTH copy, and it disagreed: it REPLACED a securities account's cash balance with its
  // holdings instead of adding market value to balances, and counted unvested grant units as
  // held. In one reported case that read S$268,340 against an authoritative S$221,150.
  const netWorthQuery = useNetWorth()
  const netWorth = netWorthQuery.data?.net_worth ?? 0

  // Balance-card breakdown (Cleared + Uncleared = Working): the total of the ACCOUNTS on this
  // tab, deliberately NOT net worth — owned assets (Assets tab) aren't accounts and belong
  // nowhere in this sum; `netWorth` above is a wider figure for the sidebar's own label. Working
  // is a re-sum of account balances rather than the prototype's income/expense sum (which would
  // never add up to the working balance); `uncleared` is its remainder, so the three numbers
  // reconcile by construction. Holdings are settled → counted as cleared.
  const cashFlow = useMemo(() => {
    const acctFilter = filters.accountId
    const accts = accounts.filter((a) => !a.archived && (!acctFilter || a.id === acctFilter))
    const sgn = (a: (typeof accts)[number], v: number) =>
      a.kind === 'liability' ? -Math.abs(v) : v
    let working = 0
    let cleared = 0
    for (const a of accts) {
      working += sgn(a, accountValue(a))
      cleared += sgn(a, accountClearedValue(a))
    }
    return { period: working, cleared, uncleared: working - cleared }
  }, [accounts, filters.accountId])

  // Cash on hand: cleared vs uncleared, `type === 'cash'` accounts ONLY (bank, e-wallet,
  // physical cash). Deliberately excludes fixed deposits, credit cards, loans, investments and
  // retirement — those swamp `cashFlow` above into an always-negative, unreadable figure; this
  // is the number someone actually wants when they open the Accounts tab: how much spendable
  // money is there. Never liability-signed — `cash` is always an asset type, so there is
  // nothing to flip.
  const cashBalance = useMemo(() => {
    const accts = accounts.filter((a) => !a.archived && a.type === 'cash')
    let total = 0
    let cleared = 0
    for (const a of accts) {
      total += a.balanceBase
      cleared += a.clearedBalanceBase
    }
    return { total, cleared, uncleared: total - cleared }
  }, [accounts])

  // ── Mutations ─────────────────────────────────────────────────────────────

  const refreshAccount = useCallback(async (id: string) => {
    const res = await authFetch(`/api/accounts/${id}`)
    if (res.ok) {
      const raw: ApiAccount = await res.json()
      const updated = fromApiAccount(raw, holdingsRef.current[id] ?? 0)
      setAccounts((prev) => prev.map((a) => (a.id === id ? updated : a)))
    }
  }, [])

  // Re-read ALL account balances after a ledger write. Deriving the touched accounts from the
  // request payload is not sound — a cleared toggle, an amount-only edit and a delete name no
  // account at all, yet each moves cleared/uncleared/working — so the balance card would freeze
  // until an unrelated mutation happened to refresh it. One list GET covers every case (including a
  // txn moved between accounts, where both sides change). fireMutate() is NOT a substitute: it
  // invalidates the query cache and pokes the app-wide refresh, neither of which writes this state.
  const refreshAccounts = useCallback(async () => {
    const raw = await cachedJson<ApiAccount[]>(ACCOUNTS_KEY, true)
    setAccounts(raw.map((a) => fromApiAccount(a, holdingsRef.current[a.id] ?? 0)))
  }, [])

  const createTxn = useCallback(
    async (data: TransactionCreate): Promise<CreatedTxn> => {
      const res = await authFetch('/api/transactions', {
        method: 'POST',
        body: JSON.stringify(data),
      })
      if (!res.ok) throw new Error(await res.text())
      const raw: ApiTxn = await res.json()
      const txn: CreatedTxn = fromApiTxn(raw)
      if (raw.warnings) txn.warnings = raw.warnings
      setTransactions((prev) => [txn, ...prev])
      await refreshAccounts()
      fireMutate()
      return txn
    },
    [refreshAccounts],
  )

  const updateTxn = useCallback(
    async (id: string, data: TransactionUpdate): Promise<Txn> => {
      // OPTIMISTIC: reflect the edit in the row synchronously, before the PUT round-trip, so an inline
      // edit doesn't feel like it "takes a while". Snapshot the pre-edit row for rollback. Abort any
      // in-flight list fetch first: a fetch that started BEFORE this edit carries the pre-edit row and
      // would otherwise land on top of the optimistic value — loadTxns's own sequencing guard drops a
      // superseded response, so aborting is how the edit path cooperates with it. The success path
      // re-fetches post-write via fireMutate below.
      loadTxnsAbortRef.current?.abort()
      let snapshot: Txn | undefined
      setTransactions((prev) =>
        prev.map((t) => {
          if (t.id !== id) return t
          snapshot = t
          return applyOptimisticTxnPatch(t, data)
        }),
      )
      try {
        const res = await authFetch(`/api/transactions/${id}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        })
        if (!res.ok) throw new Error(await res.text())
        const raw: ApiTxn = await res.json()
        const txn = fromApiTxn(raw)
        // Reconcile: the server may normalize/re-derive fx/base — the authoritative row wins.
        setTransactions((prev) => prev.map((t) => (t.id === id ? txn : t)))
        await refreshAccounts()
        fireMutate()
        return txn
      } catch (e) {
        // Roll back to the pre-edit row and surface the failure to the caller (AccountsTab toasts it).
        if (snapshot) {
          const snap = snapshot
          setTransactions((prev) => prev.map((t) => (t.id === id ? snap : t)))
        }
        throw e
      }
    },
    [refreshAccounts],
  )

  const deleteTxn = useCallback(
    async (id: string): Promise<void> => {
      const res = await authFetch(`/api/transactions/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await res.text())
      setTransactions((prev) => prev.filter((t) => t.id !== id))
      await refreshAccounts()
      fireMutate()
    },
    [refreshAccounts],
  )

  const toggleCleared = useCallback(
    async (id: string, cleared: boolean): Promise<Txn> => {
      const res = await authFetch(`/api/transactions/${id}/cleared`, {
        method: 'PATCH',
        body: JSON.stringify({ cleared }),
      })
      if (!res.ok) throw new Error(await res.text())
      const raw: ApiTxn = await res.json()
      const txn = fromApiTxn(raw)
      setTransactions((prev) => prev.map((t) => (t.id === id ? txn : t)))
      await refreshAccounts()
      fireMutate()
      return txn
    },
    [refreshAccounts],
  )

  const detachSourceRef = useCallback(
    async (txn: Txn, source: string, ref: string): Promise<Txn> => {
      const res = await authFetch(`/api/transactions/${txn.id}/refs/detach`, {
        method: 'POST',
        body: JSON.stringify({ source, ref }),
      })
      if (!res.ok) throw new Error(await res.text())
      const body: { external_refs?: Array<{ source?: string | null; ref: string }> } =
        await res.json()
      const updated: Txn = {
        ...txn,
        externalRefs: (body.external_refs ?? []).map((e) => ({
          source: e.source ?? null,
          ref: e.ref,
        })),
      }
      setTransactions((prev) => prev.map((t) => (t.id === txn.id ? updated : t)))
      fireMutate()
      return updated
    },
    [],
  )

  const bulkUpdate = useCallback(
    async (ids: string[], data: BulkUpdateData): Promise<BulkUpdateResult> => {
      const post = async (): Promise<BulkUpdateResult> => {
        const res = await authFetch('/api/transactions/bulk-update', {
          method: 'POST',
          body: JSON.stringify({ ids, ...data }),
        })
        if (!res.ok) throw new Error(await res.text())
        return normalizeBulkResult(await res.json(), ids)
      }

      const patch = localBulkPatch(data)
      if (!patch) {
        // Server-derived change (category precedence, payee link, a cross-account move that
        // re-FXes the leg) — we can't predict the stored row, so refetch rather than guess.
        const result = await post()
        await Promise.all([loadTxns(filters, 0, false, true), refreshAccounts()])
        setOffset(0)
        fireMutate()
        return result
      }

      // Optimistic: a flag flip is fully predictable, so paint it now and reconcile after. The
      // register is NOT refetched on success — that would reset pagination back to page 0 and
      // drop every extra page the user had scrolled in.
      const target = new Set(ids)
      const before = new Map<string, Txn>()
      const restore = (only?: Set<string>) =>
        setTransactions((prev) =>
          prev.map((t) => (only && !only.has(t.id) ? t : (before.get(t.id) ?? t))),
        )
      // The pre-write rows are captured inside the updater rather than off a render-phase ref.
      // StrictMode may invoke this twice, but both runs see the same `prev` and write the same
      // id → row pairs, so the snapshot is idempotent.
      setTransactions((prev) =>
        prev.map((t) => {
          if (!target.has(t.id)) return t
          before.set(t.id, t)
          return { ...t, ...patch }
        }),
      )
      let result: BulkUpdateResult
      try {
        result = await post()
      } catch (e) {
        restore()
        throw e
      }
      // The server may legitimately refuse a row (an adjustment row takes no category, a
      // transfer no payee) — those come back under not_found/skipped, and their optimistic
      // paint has to come back off even though the request itself succeeded.
      const refused = new Set([...result.not_found, ...result.skipped.map((s) => s.id)])
      if (refused.size) restore(refused)
      // A review flip changes WHICH rows a "Needs review" register holds and the "N to review"
      // count — the optimistic paint alone left 69 reviewed rows sitting in that view with the
      // count unchanged (owner, 2026-10-26). Refetch the list (a reviewed row must leave the
      // view, so page 0 is right) and tell every screen, the count included.
      if ('needs_review' in data) {
        await loadTxns(filters, 0, false, true)
        setOffset(0)
        emitDataChanged(['ledger'])
      }
      void refreshAccounts()
      fireMutate()
      return result
    },
    [filters, loadTxns, refreshAccounts],
  )

  const bulkDelete = useCallback(
    async (ids: string[]): Promise<void> => {
      const res = await authFetch('/api/transactions/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
      if (!res.ok) throw new Error(await res.text())
      setTransactions((prev) => prev.filter((t) => !ids.includes(t.id)))
      await refreshAccounts()
      fireMutate()
    },
    [refreshAccounts],
  )

  const saveAccount = useCallback(
    async (
      data: AccountCreate | AccountUpdate,
      id?: string,
      onNotice?: (message: string) => void,
    ): Promise<Account> => {
      const method = id ? 'PUT' : 'POST'
      const url = id ? `/api/accounts/${id}` : '/api/accounts'
      const res = await authFetch(url, { method, body: JSON.stringify(data) })
      if (!res.ok) throw new Error(await res.text())
      const raw: ApiAccount & {
        retired_payment_envelopes?: RetiredPaymentEnvelope[]
        payment_envelope_sync_warning?: { message: string; retryable: boolean }
      } = await res.json()
      // Changing `type` away from `credit` retires that card's payment envelope and zeroes its
      // assigned amount in every month, right now — and flipping back mints a new one, so the
      // history does not return. The server reports what went; say so rather than letting the
      // user find an empty envelope in the Budget tab later.
      const retired = raw.retired_payment_envelopes ?? []
      if (retired.length && onNotice) onNotice(retiredEnvelopeNotice(retired))
      if (raw.payment_envelope_sync_warning && onNotice) {
        onNotice(raw.payment_envelope_sync_warning.message)
      }
      const acct = fromApiAccount(raw, holdingsRef.current[raw.id] ?? 0)
      if (id) {
        setAccounts((prev) => prev.map((a) => (a.id === id ? acct : a)))
      } else {
        setAccounts((prev) => [...prev, acct])
      }
      fireMutate()
      return acct
    },
    [],
  )

  // Soft-archive (PUT archived:true) — keeps transactions. DELETE is REFUSED (409) by the backend
  // for any account that still has ANY reference other than its own opening balance, so
  // archiving must never use it.
  const archiveAccount = useCallback(async (id: string): Promise<void> => {
    const res = await authFetch(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ archived: true }),
    })
    if (!res.ok) throw new Error(await res.text())
    const raw: ApiAccount = await res.json()
    setAccounts((prev) =>
      prev.map((a) => (a.id === id ? fromApiAccount(raw, holdingsRef.current[raw.id] ?? 0) : a)),
    )
    fireMutate()
  }, [])

  const restoreAccount = useCallback(async (id: string): Promise<void> => {
    const res = await authFetch(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ archived: false }),
    })
    if (!res.ok) throw new Error(await res.text())
    const raw: ApiAccount = await res.json()
    const restored = fromApiAccount(raw, holdingsRef.current[raw.id] ?? 0)
    setAccounts((prev) => {
      const exists = prev.some((account) => account.id === id)
      return exists
        ? prev.map((account) => (account.id === id ? restored : account))
        : [...prev, restored]
    })
    fireMutate()
  }, [])

  // Hard delete (DELETE /api/accounts/:id). The backend deletes outright when nothing (or only
  // the account's own opening balance) references it; otherwise it 409s naming the referencing
  // transaction count unless `force: true` is passed, in which case it cascades — deleting the
  // account AND every transaction that referenced it. On a 409 this throws
  // AccountDeleteConflictError (reading count/otherAccounts off the STRUCTURED error body — see
  // that class's docstring for why prose-parsing was wrong) so the caller can offer the
  // escalated force-delete confirmation rather than a dead end.
  const deleteAccount = useCallback(
    async (id: string, opts?: { force?: boolean }): Promise<void> => {
      const qs = opts?.force ? '?force=true' : ''
      const res = await authFetch(`/api/accounts/${id}${qs}`, { method: 'DELETE' })
      if (!res.ok) {
        let detail: unknown = ''
        try {
          detail = (await res.json())?.detail
        } catch {
          detail = await res.text().catch(() => '')
        }
        if (res.status === 409) {
          if (detail && typeof detail === 'object') {
            const d = detail as {
              error?: string
              message?: string
              transaction_count?: number
              other_accounts?: AccountRef[]
              linked_asset_ids?: string[]
              open_position_count?: number
              open_lot_count?: number
              open_option_count?: number
            }
            // ONLY the transaction-count shape is something force=true can act on — gate on
            // the field itself, not on `res.status === 409` alone, so an account_net_worth_
            // linked refusal (or any other/older 409 shape) never becomes force-escalatable
            // (security review 2026-09-06, second pass finding #2).
            if (typeof d.transaction_count === 'number') {
              throw new AccountDeleteConflictError(
                d.message || 'Account still has transactions',
                d.transaction_count,
                Array.isArray(d.other_accounts) ? d.other_accounts : [],
              )
            }
            throw new AccountDeleteBlockedError(
              d.message || 'Account cannot be deleted',
              Array.isArray(d.linked_asset_ids) ? d.linked_asset_ids : [],
              typeof d.open_position_count === 'number' ? d.open_position_count : 0,
              typeof d.open_lot_count === 'number' ? d.open_lot_count : 0,
              typeof d.open_option_count === 'number' ? d.open_option_count : 0,
            )
          }
          throw new AccountDeleteBlockedError(
            typeof detail === 'string' && detail ? detail : 'Account still has transactions',
          )
        }
        const message = typeof detail === 'string' ? detail : ''
        throw new Error(message || `Delete failed (${res.status})`)
      }
      setAccounts((prev) => prev.filter((a) => a.id !== id))
      fireMutate()
    },
    [],
  )

  const reconcile = useCallback(
    async (id: string, data: ReconcileData): Promise<ReconcileResult> => {
      const res = await authFetch(`/api/accounts/${id}/reconcile`, {
        method: 'POST',
        body: JSON.stringify(data),
      })
      if (!res.ok) {
        const text = await res.text()
        // A 409 means the shown balance was stale — re-read it so a retry asks the right figure.
        if (res.status === 409) await refreshAccount(id)
        let detail: unknown
        try {
          detail = JSON.parse(text)?.detail
        } catch {
          detail = undefined
        }
        throw new Error(typeof detail === 'string' && detail ? detail : text)
      }
      const result: ReconcileResult = await res.json()
      await refreshAccount(id)
      // The reconcile may create an adjustment txn — reload txns
      await loadTxns(filters, 0, false)
      setOffset(0)
      fireMutate()
      return result
    },
    [refreshAccount, filters, loadTxns],
  )

  const refresh = useCallback(async () => {
    invalidateQueryCache((key) => key.startsWith('/api/transactions?'))
    setOffset(0)
    await Promise.all([loadCore(true), loadTxns(filters, 0, false)])
  }, [loadCore, filters, loadTxns])

  // force=true is REQUIRED, not defensive: every caller is a POST-WRITE reload (import commit,
  // apply-rules, payee re-category). Without it fetchCachedQuery serves the entry cached inside
  // staleTime — the PRE-write rows — and the write appears not to have happened. Owner repro
  // 2026-08-01: six rows deleted at import commit stayed on screen until a manual page reload,
  // while the ledger on disk already had deleted=true on all six. ``refresh`` above dodged this
  // only because it invalidates the cache by key first; this path had neither guard.
  const refreshTxns = useCallback(() => {
    loadTxns(filters, 0, false, true)
    setOffset(0)
  }, [filters, loadTxns])

  // ── Return ────────────────────────────────────────────────────────────────

  return {
    accounts,
    transactions,
    categories,
    payees,
    fx,
    baseCurrency: getBaseCurrency(),
    loading,
    loadingTxns,
    refiltering,
    isLoadingMore,
    error,
    hasLoaded,
    filters,
    setFilters,
    hasMore,
    total,
    firstDate,
    lastDate,
    loadMore,
    groups,
    netWorth,
    cashFlow,
    cashBalance,
    createTxn,
    updateTxn,
    deleteTxn,
    toggleCleared,
    detachSourceRef,
    bulkUpdate,
    bulkDelete,
    saveAccount,
    archiveAccount,
    restoreAccount,
    deleteAccount,
    reconcile,
    refresh,
    refreshTxns,
  }
}
