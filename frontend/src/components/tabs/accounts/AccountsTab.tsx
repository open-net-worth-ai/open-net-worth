/**
 * AccountsTab.tsx — Accounts tab orchestrator.
 *
 * Ports the prototype `App` (design_handoffs/accounts_handoff/prototype/accounts/main.jsx):
 * owns state + the 4 mutations, wires the FE1/FE2/FE3 components to the live
 * `useAccountsLedger` hook. Balances are server-authoritative (no client applyBalance).
 * Consumes the existing app nav rail + LuciusDock (handoff-frozen) — does NOT render them.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { AccountType } from './categories'
import {
  markNativeImportReviewed,
  nativeImportAttachmentId,
  nativeActionCount,
  subscribeNativeImports,
  takeNativeImport,
  takeNativeTransaction,
} from '../../../platform/desktop'
import {
  subscribeStatementReview,
  takeStatementReview,
  trackBackgroundStatement,
  untrackBackgroundStatement,
} from './backgroundStatements'
import { authFetch } from '../../../api/client'
import { ErrorState, Modal, ConfirmDialog, TabSkeleton } from '../../ui'
import type { ConfirmDialogData } from '../../ui'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { usePointerResize } from '../../../hooks/usePointerResize'
import { localIso, fDate } from '../../../utils/format'
import './accounts.css'

import { useAccountsLedger, type CreatedTxn, AccountDeleteConflictError } from './useAccountsLedger'
import {
  fromApiAccount,
  fromApiTxn,
  fromApiSourceEntry,
  detailPatchToUpdate,
  txnIsInflow,
  selectionTotal,
} from './data'
import { buildTxnView } from './txnView'
import { resolveChipMeta } from '../../../lucius/chipMeta'
import type { Account, Txn, Category, TransactionUpdate, ApiAccount, ApiTxn } from './types'
import { sym, toBase, fBase } from './format'
import { tokensToFilterPatch, combineDateRange, addFilterToken } from './tokenFilters'

import { Sidebar } from './Sidebar'
import { Overview } from './Overview'
import { AccountHeader, type DueInfo } from './AccountHeader'
import { ArchivedAccountsModal } from './ArchivedAccountsModal'
import { Register } from './Register'
import { useColumnVisibility } from './columns'
import { BulkBar } from './BulkBar'
import { Filters, ActiveChips } from './SearchFilter'
import type { FilterState, FilterToken } from './SearchFilter'
import { useTransactionSurfaces } from './transactionSurfacesContext'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { emitDataChanged } from '../../../lucius/bus'
import { fetchCachedQuery, useQueryCache } from '../../../state/queryCache'
import AccountModal from './AccountModal'
import type { InstitutionOption } from './ds'
import type { InstitutionDeleteResult } from './InstitutionManager'
import { PayeesModal } from './PayeesModal'
// The categories admin surface is shared, not Plan's property — same rationale as Manage payees
// living here while every tab uses payees. One implementation, several entry points.
import { CategoriesManager } from '../plan/categoriesManager'
import { AddCategoryModal } from './AddCategoryModal'
import type { CatKind } from './AddCategoryModal'
import { ConfirmModal } from './ConfirmModal'
import { ImportModal, type ReopenOptions } from './ImportModal'
import { mapServerPreview } from './importHelpers'
import { Toast } from './Toast'
import type { AddData } from './AddRow'
import type { RowPatch } from './TxnRow'
import { DataInHero } from '../../dataIn/DataInHero'
import { ReviewConfetti } from './ReviewConfetti'
import { useChatComposerOptional } from '../../../state/chatComposer'
import { SCREEN_INTROS } from '../../intro/screenIntros'

const PALETTE = [
  '#e2554f',
  '#e0a032',
  '#34b08a',
  '#3b86dd',
  '#9a7fd0',
  '#22b8cf',
  '#d4537e',
  '#bd7a1a',
]
const SIDEBAR_MIN = 210,
  SIDEBAR_MAX = 460,
  SIDEBAR_DEFAULT = 286
// The register's columns need ~800px; collapse the sidebar before the register gets narrower than
// that, expand again with hysteresis so the boundary doesn't flicker.
const COLLAPSE_AT = 820,
  EXPAND_AT = 920

// ── date helpers (ported) ───────────────────────────────────────────────────
function startOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}
// startOfMonth/custom-range bounds are LOCAL midnights (`new Date(y, m, d)`) — formatting them
// via `.toISOString()` first converts to UTC, which rolls back a WHOLE DAY in any timezone
// ahead of UTC (e.g. Aug 1 00:00 SGT -> Jul 31 16:00 UTC), silently pulling the last day of
// the PREVIOUS month into "This month". Read the local calendar fields directly instead —
// never round-trip a local date through UTC.
const iso = localIso
function presetCutoff(preset: string): Date | null {
  const t = new Date()
  if (preset === 'this_month') return startOfMonth(t)
  if (preset === 'last_3') {
    const d = new Date(t)
    d.setMonth(d.getMonth() - 2)
    return startOfMonth(d)
  }
  if (preset === 'this_year') return new Date(t.getFullYear(), 0, 1)
  if (preset === 'last_3y') {
    const d = new Date(t)
    d.setFullYear(d.getFullYear() - 3)
    return d
  }
  return null
}
function daysUntil(day: number): number {
  const t = new Date()
  let target = new Date(t.getFullYear(), t.getMonth(), day)
  if (target < t) target = new Date(t.getFullYear(), t.getMonth() + 1, day)
  return Math.round((target.getTime() - t.getTime()) / 86400000)
}

// GET /api/accounts/{id}/statement response shape (backend/services/card_statements.py
// StatementSummary) — only the fields the due banner needs.
interface StatementSummary {
  currency: string
  days_until_due: number | null
  next_due_date?: string | null
  last_statement_balance: number
  paid: boolean
  partial: boolean
  payment_amount: number | null
  /** The ONE "amount still owed" figure (MATH-45) — last_statement_balance minus any
   *  payment, clamped to >= 0 by the backend. Never re-derive this client-side: an
   *  overpaid card must read 0, not a negative "owed" number. */
  remaining_amount: number
  /** "statement" when the figures are printed facts off an uploaded statement. */
  due_source?: 'statement' | 'config'
  statement_id?: string | null
  statement_minimum_payment?: number | null
  statement_period_start?: string | null
  statement_period_end?: string | null
  /** ISO close date of the newest derived cycle (the missing statement's close when
   *  statement_missing_for_cycle is set). */
  last_statement_close?: string | null
  /** True when a newer cycle closed but its statement isn't uploaded/linked — the figures
   *  are that cycle's ledger ESTIMATE and the banner nudges for the upload. */
  statement_missing_for_cycle?: boolean
}

const statementKey = (accountId: string) => `/api/accounts/${accountId}/statement`
/** Stands in for a non-credit selection, whose banner never fetches. */
const NO_STATEMENT_KEY = 'accounts:no-statement'

async function fetchStatementSummary(accountId: string): Promise<StatementSummary | null> {
  const res = await authFetch(statementKey(accountId))
  if (res.status === 204) return null
  if (!res.ok) throw new Error('Failed to load the statement summary')
  return res.json() as Promise<StatementSummary>
}

// txPrimaryAmount is still needed for CSV export's signed-amount column (unrelated to
// register filtering, which is now entirely server-side — see tokenFilters.ts).
function txPrimaryAmount(t: Txn): { mag: number; ccy: string } {
  if (t.kind === 'income')
    return { mag: Math.abs(t.toAmount ?? 0), ccy: t.toCurrency ?? t.baseCurrency }
  return {
    mag: Math.abs(t.fromAmount ?? t.toAmount ?? 0),
    ccy: t.fromCurrency ?? t.toCurrency ?? t.baseCurrency,
  }
}

const PENDING_SELECTION_KEY = 'accounts_pending_selection'

const now = new Date()
const DEFAULT_FILTER: FilterState = {
  preset: 'all',
  tokens: [],
  cfM: now.getMonth() + 1,
  cfY: now.getFullYear(),
  ctM: now.getMonth() + 1,
  ctY: now.getFullYear(),
}

export function AccountsTab({
  showPayeesRoute = false,
  registerFilter,
  onRegisterFilterApplied,
  refresh,
  refreshSignal,
  selectedAccountId = null,
  onSelectAccount,
  importReviewDefault,
  onNavigate,
  addAccountType,
  onAddAccountConsumed,
  onAskLucius,
}: {
  showPayeesRoute?: boolean
  /** Deep-link from Plan (category-activity popover, or the Outside-envelopes "Review in
   *  Accounts" / "Label it" actions — handoff item 7): `uncategorized` composes with `kind`
   *  to isolate uncategorized income from Uncategorized expense (both share the blank-category
   *  bucket server-side). */
  registerFilter?: {
    category?: string | string[]
    month?: string
    uncategorized?: boolean
    kind?: string
    /** Dashboard Calendar widget deep-link: one exact day (ISO `YYYY-MM-DD`). */
    dateOn?: string
    /** Dashboard Top merchants deep-link: one payee, by name. */
    payee?: string
  } | null
  onRegisterFilterApplied?: () => void
  refresh?: () => void
  refreshSignal?: number
  /** Selected account id from the URL (?account=…) — refresh-safe + bookmarkable. */
  selectedAccountId?: string | null
  /** Persist a selection to the URL (the source of truth for which account is open). */
  onSelectAccount?: (id: string | null) => void
  /** Profile `import_review_default` — threaded into ImportModal's review-band default. */
  importReviewDefault?: 'skip' | 'merge'
  /** Tab navigation, supplied by the shell — same shape Overview takes. Deliberately a prop
   *  rather than useNavigate() here: this component is rendered bare in a dozen tests, and a
   *  router hook makes every one of them warn about a missing RouterProvider. */
  onNavigate?: (tab: string, search?: Record<string, string>) => void
  /** Deep-link (?add=<type>): open the add-account form on this type, then clear the param. */
  addAccountType?: AccountType | null
  onAddAccountConsumed?: () => void
  /** Hands a message to Lucius in a fresh conversation (the shell's one path for it). */
  onAskLucius?: (text: string) => void
}) {
  // Pass the outbound refresh so every write also refreshes app-wide net worth / other tabs / Lucius.
  const L = useAccountsLedger(refresh)
  const { accounts, transactions, categories, payees, fx, baseCurrency } = L

  // Reload when the AI mutates the ledger (RouterShell bumps refreshSignal on every portfolio/ledger
  // change). Skip the initial 0 — the hook already loads on mount.
  useEffect(() => {
    if (refreshSignal) L.refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal])

  // Clear any pending load-more debounce on unmount.
  useEffect(
    () => () => {
      if (loadTimer.current) clearTimeout(loadTimer.current)
    },
    [],
  )

  // ── UI state ───────────────────────────────────────────────────────────────
  // Selected account is URL-driven: init from ?account=… (refresh-safe) and sync on
  // back/forward/refresh. selectAccount() updates state instantly AND the URL.
  const [selectedId, setSelectedId] = useState<string | null>(selectedAccountId)
  const isMobile = useIsMobile()
  const chatComposer = useChatComposerOptional()
  const [accountSheetOpen, setAccountSheetOpen] = useState(false)
  // The review cards and the transaction drawer are hosted at the app level
  // (TransactionSurfaces.tsx) so they open in place from anywhere; this tab only asks for
  // them. "Start review" reviews everything flagged — the same set the register filter and
  // the count show; Lucius's door reviews automation rows only.
  const surfaces = useTransactionSurfaces()
  const [reviewTotal, setReviewTotal] = useState(0)
  // The count follows the ledger bus directly: the app-level surfaces' approvals announce
  // there (the shell's refreshSignal follows it too, a beat later).
  const ledgerVersion = useDataVersion('ledger')
  useEffect(() => {
    setSelectedId(selectedAccountId)
  }, [selectedAccountId])
  // Everything flagged, whatever wrote it — the number beside "Needs review" must be the
  // count of what that filter shows (owner decision 2026-09-20). Lucius's "added by
  // automations" keeps its own, narrower count.
  const loadReviewTotal = useCallback(async (isCurrent: () => boolean = () => true) => {
    try {
      const response = await authFetch('/api/transactions/recently-added?needs_review=true&limit=1')
      if (!response.ok) return
      const body = (await response.json()) as { total?: number }
      if (isCurrent()) setReviewTotal(body.total ?? 0)
    } catch {
      /* the count keeps its last value */
    }
  }, [])
  useEffect(() => {
    let active = true
    void loadReviewTotal(() => active)
    return () => {
      active = false
    }
  }, [refreshSignal, ledgerVersion, loadReviewTotal])
  const selectAccount = (id: string | null) => {
    setSelectedId(id)
    onSelectAccount?.(id)
  }
  // Mobile's "Ask Lucius" hands off to the /lucius ROUTE (RouterShell), which unmounts this
  // whole tab — `selectedIds` would otherwise vanish along with it. `bulkAskLucius` below stows
  // the selection here right before the handoff; this reads it back exactly once (removeItem
  // makes it one-shot, so an unrelated tab switch away and back never resurrects a stale pick).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(PENDING_SELECTION_KEY)
      if (!raw) return new Set()
      sessionStorage.removeItem(PENDING_SELECTION_KEY)
      return new Set(JSON.parse(raw) as string[])
    } catch {
      return new Set()
    }
  })
  const [filter, setFilter] = useState<FilterState>(DEFAULT_FILTER)
  // Register column visibility (localStorage-backed) — `hidden` flows to the Register (filters
  // `cols`), the toggle/reset to the toolbar's Columns picker (rendered by Filters).
  const columnVis = useColumnVisibility()
  const [adding, setAdding] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [acctModal, setAcctModal] = useState<{
    open: boolean
    mode: 'add' | 'edit'
    account: Account | null
    type?: AccountType
  }>({ open: false, mode: 'add', account: null })
  useEffect(() => {
    if (!addAccountType) return
    setAcctModal({ open: true, mode: 'add', account: null, type: addAccountType })
    onAddAccountConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addAccountType])
  // Memoized: the modal re-seeds its form whenever `prefill` changes identity.
  const addPrefill = useMemo(
    () => (acctModal.type ? { type: acctModal.type } : undefined),
    [acctModal.type],
  )
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [archivedAccounts, setArchivedAccounts] = useState<Account[]>([])
  const [archivedLoading, setArchivedLoading] = useState(false)
  const [archivedError, setArchivedError] = useState<string | null>(null)
  const [archivedSelected, setArchivedSelected] = useState<Account | null>(null)
  const [archivedTransactions, setArchivedTransactions] = useState<Txn[]>([])
  const [archivedTransactionsLoading, setArchivedTransactionsLoading] = useState(false)

  const loadArchivedAccounts = useCallback(async () => {
    setArchivedLoading(true)
    setArchivedError(null)
    try {
      const response = await authFetch('/api/accounts?include_archived=true')
      if (!response.ok) throw new Error('Could not load archived accounts')
      const rows = (await response.json()) as ApiAccount[]
      setArchivedAccounts(rows.filter((row) => row.archived).map((row) => fromApiAccount(row, 0)))
    } catch (error) {
      setArchivedError(error instanceof Error ? error.message : 'Could not load archived accounts')
    } finally {
      setArchivedLoading(false)
    }
  }, [])

  const openArchivedAccounts = useCallback(() => {
    setArchivedSelected(null)
    setArchivedTransactions([])
    setArchivedOpen(true)
    void loadArchivedAccounts()
  }, [loadArchivedAccounts])

  const openArchivedAccount = useCallback(async (account: Account) => {
    setArchivedSelected(account)
    setArchivedTransactions([])
    setArchivedTransactionsLoading(true)
    try {
      const response = await authFetch(
        `/api/transactions?account_id=${encodeURIComponent(account.id)}`,
      )
      if (!response.ok) throw new Error('Could not load transactions')
      const body = (await response.json()) as { transactions?: ApiTxn[] }
      setArchivedTransactions((body.transactions ?? []).map(fromApiTxn))
    } catch {
      setArchivedTransactions([])
    } finally {
      setArchivedTransactionsLoading(false)
    }
  }, [])
  const [institutions, setInstitutions] = useState<InstitutionOption[]>([])
  const refreshAccounts = L.refresh
  const loadInstitutions = useCallback(async () => {
    try {
      const response = await authFetch('/api/institutions')
      if (!response?.ok) return
      const body = (await response.json()) as { institutions?: unknown }
      if (!Array.isArray(body.institutions)) return
      setInstitutions(
        body.institutions.flatMap((value) => {
          if (!value || typeof value !== 'object') return []
          const institution = value as Record<string, unknown>
          return typeof institution.id === 'string' && typeof institution.name === 'string'
            ? [
                {
                  id: institution.id,
                  name: institution.name,
                  accountCount:
                    typeof institution.account_count === 'number' ? institution.account_count : 0,
                },
              ]
            : []
        }),
      )
    } catch {
      // The input remains usable when the optional autocomplete feed is unavailable.
    }
  }, [])
  useEffect(() => {
    void loadInstitutions()
  }, [loadInstitutions])
  const renameInstitution = useCallback(
    async (id: string, name: string): Promise<InstitutionOption> => {
      const response = await authFetch(`/api/institutions/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!response?.ok) throw new Error('Could not rename institution')
      const body = (await response.json()) as { id: string; name: string; account_count: number }
      const renamed = { id: body.id, name: body.name, accountCount: body.account_count }
      setInstitutions((current) => current.map((item) => (item.id === id ? renamed : item)))
      await refreshAccounts()
      return renamed
    },
    [refreshAccounts],
  )
  const deleteInstitution = useCallback(
    async (id: string): Promise<InstitutionDeleteResult> => {
      const response = await authFetch(`/api/institutions/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      })
      if (!response?.ok) throw new Error('Could not delete institution')
      const body = (await response.json()) as InstitutionDeleteResult
      setInstitutions((current) => current.filter((item) => item.id !== id))
      await refreshAccounts()
      return body
    },
    [refreshAccounts],
  )
  const [confirm, setConfirm] = useState<{
    title: string
    body: string
    confirmLabel: string
    danger?: boolean
    onConfirm: () => void
  } | null>(null)
  // Delete-account's two-step escalation (initial confirm, then a SECOND explicit confirm
  // to force-cascade past a 409) uses the shared ui/ConfirmDialog primitive rather than the
  // local ConfirmModal above — see deleteAccountFlow.
  const [deleteConfirm, setDeleteConfirm] = useState<{
    data: ConfirmDialogData
    onConfirm: () => void
  } | null>(null)
  const [importState, setImportState] = useState<{
    accountId: string | null
    initialFile?: File
    /** B20 async: reopen this statement's review by id instead of an upload. */
    resumeStatementId?: string
    resumeFilename?: string
    resumeUploadedAt?: string
  } | null>(null)
  const nativeImports = useSyncExternalStore(subscribeNativeImports, nativeActionCount)
  const [showPayees, setShowPayees] = useState(showPayeesRoute)
  const [catModal, setCatModal] = useState<{
    prefill: string
    kind: CatKind
    onDone: (c: Category) => void
  } | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const showToast = useCallback((m: string) => setToast(m), [])
  // A finished reconcile is the moment the book is proven against the bank — it earns the
  // same burst as clearing the review deck. Keyed so a second reconcile replays it.
  const [reconcileBurst, setReconcileBurst] = useState(0)
  useEffect(() => {
    if (!reconcileBurst) return
    const timer = window.setTimeout(() => setReconcileBurst(0), 4000)
    return () => window.clearTimeout(timer)
  }, [reconcileBurst])
  const [showCategories, setShowCategories] = useState(false)

  useEffect(() => {
    setShowPayees(showPayeesRoute)
  }, [showPayeesRoute])

  // ── sidebar resize + responsive collapse ────────────────────────────────────
  const [sideW, setSideW] = useState(() => {
    const v = +(localStorage.getItem('acctSideW') || '')
    return v >= SIDEBAR_MIN && v <= SIDEBAR_MAX ? v : SIDEBAR_DEFAULT
  })
  // Persist the sidebar collapse so a refresh keeps the user's choice (not re-expand). Pair it
  // with the manual-pin flag: a pinned state survives refresh and the auto-responsive logic
  // backs off; an unpinned (auto-managed) state is just re-derived from width on load.
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem('acctSideCollapsed') === '1',
  )
  const railPinned = useRef(localStorage.getItem('acctSidePinned') === '1')
  const shellRef = useRef<HTMLDivElement | null>(null)
  const mainRef = useRef<HTMLElement | null>(null)
  const loadTimer = useRef<ReturnType<typeof setTimeout> | null>(null) // debounce infinite-scroll load-more
  useEffect(() => {
    localStorage.setItem('acctSideW', String(sideW))
  }, [sideW])
  useEffect(() => {
    localStorage.setItem('acctSideCollapsed', collapsed ? '1' : '0')
  }, [collapsed])
  const sidebarResize = useRef<{ startX: number; startWidth: number } | null>(null)
  const onSidebarPointerDown = usePointerResize({
    onStart: (event) => {
      event.preventDefault()
      sidebarResize.current = { startX: event.clientX, startWidth: sideW }
    },
    onMove: (event) => {
      const drag = sidebarResize.current
      if (!drag) return
      setSideW(
        Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, drag.startWidth + event.clientX - drag.startX)),
      )
    },
    onEnd: () => {
      sidebarResize.current = null
    },
  })
  const pinCollapse = (v: boolean) => {
    railPinned.current = true
    localStorage.setItem('acctSidePinned', '1')
    setCollapsed(v)
  }
  const railCollapsed = isMobile || collapsed
  const openAccountNavigator = () => {
    if (isMobile) setAccountSheetOpen(true)
    else pinCollapse(false)
  }
  // Auto-collapse the account sidebar when the register's available width gets tight —
  // window narrows OR the Lucius panel opens — but never override a manual collapse/expand.
  useEffect(() => {
    const APP_NAV_W = 60 // the app's left nav rail
    // Real width the Lucius panel steals from the content (it overlays / the shell margins for it).
    // NOTE: .lx-side is always in the DOM (transformed off-screen when closed) — only count it when
    // actually open (.on / .app-shell.chat-open), else the sidebar would collapse even with Lucius shut.
    const luciusW = () => {
      const el = document.querySelector('.lx-side.on') as HTMLElement | null
      if (el) return el.getBoundingClientRect().width
      if (document.querySelector('.app-shell.chat-open')) {
        const v = parseInt(
          getComputedStyle(document.documentElement).getPropertyValue('--chat-width'),
          10,
        )
        return Number.isFinite(v) ? v : 400
      }
      return 0
    }
    const check = () => {
      if (railPinned.current) return
      // ALWAYS subtract the sidebar width (the register width *if expanded*) so the measure
      // does not depend on `collapsed` — otherwise collapse↔expand oscillates forever.
      const avail = window.innerWidth - APP_NAV_W - sideW - luciusW()
      setCollapsed((c) => (!c && avail < COLLAPSE_AT ? true : c && avail > EXPAND_AT ? false : c))
    }
    check()
    window.addEventListener('resize', check)
    // Catch the Lucius side panel opening/closing (it overlays, so window width is unchanged).
    const mo = new MutationObserver(check)
    mo.observe(document.body, { childList: true })
    const appShell = document.querySelector('.app-shell')
    if (appShell) mo.observe(appShell, { attributes: true, attributeFilter: ['class'] })
    const lxSide = document.querySelector('.lx-side')
    if (lxSide) mo.observe(lxSide, { attributes: true, attributeFilter: ['class', 'style'] })
    return () => {
      window.removeEventListener('resize', check)
      mo.disconnect()
    }
  }, [sideW, collapsed])

  // Measure the EXACT pinned title bar + filter heights so the column header stacks beneath them
  // with no gap/overlap (the header height differs between Overview and single-account views).
  useEffect(() => {
    const ds = document.documentElement.style
    const measure = () => {
      const main = mainRef.current
      if (!main) return
      const head = main.querySelector(':scope > .acct-head') as HTMLElement | null
      const filt = main.querySelector(':scope > .acct-sticky') as HTMLElement | null
      if (head)
        ds.setProperty('--acct-head-h', Math.round(head.getBoundingClientRect().height) + 'px')
      if (filt)
        ds.setProperty('--acct-filter-h', Math.round(filt.getBoundingClientRect().height) + 'px')
    }
    measure()
    const raf = requestAnimationFrame(measure)
    const main = mainRef.current
    const ro = new ResizeObserver(measure)
    if (main) {
      ro.observe(main)
      Array.from(main.children).forEach((c) => ro.observe(c))
    }
    window.addEventListener('resize', measure)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [selectedId, accounts.length, filter.tokens.length, collapsed, L.loadingTxns])

  // ── derived ──────────────────────────────────────────────────────────────────
  const selectedAccount = useMemo(
    () => accounts.find((a) => a.id === selectedId) || null,
    [accounts, selectedId],
  )
  // On mobile, AddRow's dense multi-column grid has no responsive layout (unlike every other
  // register row) — hand off to Lucius instead of showing it. Desktop keeps the inline form.
  const handleAddTxn = useCallback(() => {
    if (isMobile) {
      const text = selectedAccount
        ? `Add a transaction to ${selectedAccount.name}: `
        : `Add a transaction: `
      chatComposer?.setPendingText(text)
      window.dispatchEvent(new Event('lx-dock-open'))
    } else {
      setAdding(true)
    }
  }, [isMobile, selectedAccount, chatComposer])
  useEffect(() => {
    if (importState || !nativeImports) return
    if (takeNativeTransaction()) handleAddTxn()
    const initialFile = takeNativeImport()
    if (initialFile) setImportState({ accountId: null, initialFile })
  }, [handleAddTxn, importState, nativeImports])
  // A finished background read's "Review" (BackgroundStatementNotices, app-wide) hands the
  // statement over here; it opens as soon as no other import is on screen.
  const [statementReviewTick, setStatementReviewTick] = useState(0)
  useEffect(() => subscribeStatementReview(() => setStatementReviewTick((t) => t + 1)), [])
  useEffect(() => {
    if (importState) return
    const pending = takeStatementReview()
    if (pending) {
      setImportState({
        accountId: pending.accountId,
        resumeStatementId: pending.id,
        resumeFilename: pending.filename,
        resumeUploadedAt: pending.startedAt ? new Date(pending.startedAt).toISOString() : undefined,
      })
    }
  }, [statementReviewTick, importState])
  // Reviewing a statement — from the popup or the Statements list — answers its notice: stop
  // watching it so no stale "ready to review" pops up for a review already open.
  useEffect(() => {
    if (importState?.resumeStatementId) untrackBackgroundStatement(importState.resumeStatementId)
  }, [importState?.resumeStatementId])
  const payeeNameById = useMemo(
    () => Object.fromEntries(payees.map((p) => [p.id, p.name])),
    [payees],
  )
  const payeeName = useCallback(
    (id: string | null) => (id ? payeeNameById[id] || '' : ''),
    [payeeNameById],
  )
  const catMap = useMemo(
    () => Object.fromEntries(categories.map((c) => [c.id, c])) as Record<string, Category>,
    [categories],
  )

  const totals = useMemo(() => {
    const t: Record<string, number> = {
      cash: 0,
      fixed_deposit: 0,
      investment: 0,
      retirement: 0,
      receivable: 0,
      credit: 0,
      loan: 0,
      liabilities: 0,
    }
    for (const g of L.groups) t[g.key] = g.totalBase
    t.liabilities = (t.credit || 0) + (t.loan || 0)
    return t as {
      cash: number
      fixed_deposit: number
      investment: number
      retirement: number
      receivable: number
      credit: number
      loan: number
      liabilities: number
    }
  }, [L.groups])

  // count = the server-reported total for the current account/date scope (not the loaded page count).
  const stats = useMemo(
    () => ({
      count: L.total || transactions.filter((t) => !t.deleted).length,
      review: reviewTotal,
    }),
    [transactions, L.total, reviewTotal],
  )
  // CC1: the due banner must reflect the last CLOSED statement's balance minus any payment
  // already received — never the raw live account.balance, which is negative (liability sign)
  // and folds in brand-new post-payment spending from the still-open cycle.
  // Cached per card and prefetched for EVERY credit card each time the accounts list loads (on
  // open, and after any write — the list reloads then), so opening a card paints its banner in
  // the first frame. A late banner pushed the page down; a reserved box could only guess its
  // height — wrong when nothing was due, and far too short once it wrapped on a phone.
  const selectedCreditId = selectedAccount?.type === 'credit' ? selectedAccount.id : null
  const summaryQuery = useQueryCache(
    selectedCreditId ? statementKey(selectedCreditId) : NO_STATEMENT_KEY,
    selectedCreditId ? () => fetchStatementSummary(selectedCreditId) : async () => null,
    { enabled: selectedCreditId !== null },
  )
  const statementSummary = selectedCreditId ? (summaryQuery.data ?? null) : null
  useEffect(() => {
    for (const a of accounts) {
      if (a.type !== 'credit' || a.archived) continue
      fetchCachedQuery(statementKey(a.id), () => fetchStatementSummary(a.id)).catch(() => {})
    }
  }, [accounts])

  const dueInfo = useMemo((): DueInfo | null => {
    if (!selectedAccount || selectedAccount.type !== 'credit') return null
    // days_until_due is null when the account has no statement_close_day configured (the backend
    // can't derive a sound cycle boundary, so it can't compute a last-statement balance either) —
    // deliberately suppress the banner rather than guess an amount from a partial cycle config.
    if (!statementSummary || statementSummary.days_until_due == null) return null
    const missingForCycle = statementSummary.statement_missing_for_cycle === true
    // Backend-computed, already clamped >= 0 (MATH-45) — read it, never re-derive it here.
    const amountDue = statementSummary.remaining_amount
    if (amountDue <= 0) {
      // A newer cycle closed without its statement: even a zero/covered estimate must keep
      // the upload nudge visible — and NEVER congratulate off an unconfirmed estimate
      // (congrats is reserved for a paid statement that is still the current obligation).
      if (missingForCycle) {
        return {
          amountDue: 0,
          currency: statementSummary.currency,
          days: statementSummary.days_until_due,
          closeDays: null,
          partial: false,
          settled: false,
          source: statementSummary.due_source,
          missingForCycle: true,
          missingCloseDate: statementSummary.last_statement_close ?? null,
          dueDate: statementSummary.next_due_date ?? null,
        }
      }
      // Fully covered: celebrate a real payment; stay silent when there was nothing due.
      if (!statementSummary.paid || statementSummary.last_statement_balance <= 0) return null
      return {
        amountDue: 0,
        currency: statementSummary.currency,
        days: statementSummary.days_until_due,
        closeDays: null,
        partial: false,
        settled: true,
        source: statementSummary.due_source,
        statementId: statementSummary.statement_id ?? null,
        paidAmount: statementSummary.payment_amount ?? null,
        totalDue: statementSummary.last_statement_balance,
        periodStart: statementSummary.statement_period_start ?? null,
        periodEnd: statementSummary.statement_period_end ?? null,
      }
    }
    return {
      amountDue,
      currency: statementSummary.currency,
      days: statementSummary.days_until_due,
      closeDays:
        selectedAccount.statementCloseDay != null
          ? daysUntil(selectedAccount.statementCloseDay)
          : null,
      partial: statementSummary.partial,
      source: statementSummary.due_source,
      minPayment: statementSummary.statement_minimum_payment ?? null,
      statementId: statementSummary.statement_id ?? null,
      paidAmount: statementSummary.payment_amount ?? null,
      totalDue: statementSummary.last_statement_balance,
      dueDate: statementSummary.next_due_date ?? null,
      periodStart: statementSummary.statement_period_start ?? null,
      periodEnd: statementSummary.statement_period_end ?? null,
      missingForCycle,
      missingCloseDate: missingForCycle ? (statementSummary.last_statement_close ?? null) : null,
    }
  }, [selectedAccount, statementSummary])

  const suggest = useMemo(
    () => ({
      categories: categories.map((c) => ({ id: c.id, label: c.label, color: c.color })),
      payees: payees.map((p) => ({ name: p.name })),
      accounts: accounts.map((a) => ({ id: a.id, name: a.name })),
      tags: Array.from(new Set(transactions.flatMap((t) => t.tags || []))).sort(),
    }),
    [categories, payees, accounts, transactions],
  )

  const scopeSym = sym(selectedAccount ? selectedAccount.currency : baseCurrency)

  // ── payee resolution ──────────────────────────────────────────────────────────
  // A 'payee' token carries a display NAME (never an id) — resolve it before it's sent
  // to the server as payee_id. An unresolved name falls back to the raw string, which
  // can never equal a real payee_id, so the filter correctly matches nothing rather
  // than silently becoming a no-op.
  // `payees` is a BRAND NEW array reference every time useAccountsLedger's loadCore() runs
  // (refreshSignal bumps, any Payees-modal mutation, the error-state retry) — completely
  // unrelated to a filter/account/date change. resolvePayeeId only ever needs the LATEST
  // payees at call time, so it reads a ref (kept current every render) instead of closing
  // over `payees` directly — that lets the filter effect below drop `payees` from its
  // dependency array without going stale (REG2 follow-up: `payees` as a dep meant the scroll
  // reset — and the underlying setFilters/refetch — fired on every unrelated payee refresh).
  const payeesRef = useRef(payees)
  payeesRef.current = payees
  const resolvePayeeId = (name: string): string | null => {
    const n = name.trim().toLowerCase()
    if (!n) return null
    return payeesRef.current.find((p) => p.name.toLowerCase() === n)?.id ?? null
  }
  // A typed name with no existing match must still become a real link on an EDIT — it can't
  // "ride the note" the way a brand-new transaction can: update_transaction never auto-resolves
  // or auto-creates a payee from note text (only create_transaction does, and only outside its
  // own update-rebuild — see backend/services/ledger.py's `_skip_payee_resolve` branch, which is
  // explicitly "Never auto-resolve from the note on an edit"). So an unresolved name on an update
  // explicitly creates the payee here and awaits the REAL created id (never the addPayee() helper
  // below's fake 'pending-' placeholder, which is not a valid payee_id to persist on a transaction).
  const resolveOrCreatePayeeId = async (name: string): Promise<string | null> => {
    const trimmed = name.trim()
    if (!trimmed) return null
    const existing = resolvePayeeId(trimmed)
    if (existing) return existing
    const res = await authFetch('/api/payees', {
      method: 'POST',
      body: JSON.stringify({ name: trimmed }),
    })
    if (!res.ok) throw new Error(await res.text())
    const created = await res.json()
    L.refresh()
    return created.id as string
  }

  // ── filtering: every token is server-side — the register no longer re-filters the
  // loaded page client-side (REG1: that used to silently show the wrong "N of TOTAL"
  // once a filter's real matches spilled past the first loaded page). This effect is the
  // ONE "fresh view" trigger (filter/account/date changed) — it must NOT also fire for
  // unrelated re-renders (e.g. a payees refresh), which is why `payees` is read via
  // `payeesRef` above rather than listed as a dependency. ────────────
  useEffect(() => {
    let presetFrom = '',
      presetTo = ''
    if (filter.preset === 'custom') {
      presetFrom = iso(new Date(filter.cfY, filter.cfM - 1, 1))
      presetTo = iso(new Date(filter.ctY, filter.ctM, 0))
    } else {
      const cut = presetCutoff(filter.preset)
      if (cut) presetFrom = iso(cut)
    }
    const { dateFrom, dateTo } = combineDateRange(presetFrom, presetTo, filter.tokens)
    const patch = tokensToFilterPatch(filter.tokens, { resolvePayeeId })
    L.setFilters({ accountId: selectedId, dateFrom, dateTo, ...patch })
    // This is a fresh (non-append) view, not a loadMore append — the previously loaded scroll
    // position almost certainly no longer matches the new (often much shorter) result set, which
    // could otherwise leave the "near bottom" check in the .acct-main onScroll handler true and
    // spuriously fire loadMore() right after the filter change (REG2).
    if (mainRef.current) mainRef.current.scrollTop = 0
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.preset, filter.cfM, filter.cfY, filter.ctM, filter.ctY, filter.tokens, selectedId])

  // Skip the very first run — an effect fires on mount too, which would immediately wipe out
  // a selection just restored from PENDING_SELECTION_KEY (the mobile "Ask Lucius" round trip).
  // Every later change of `selectedId` (an actual account switch) still clears it as before.
  const skipFirstSelectionReset = useRef(true)
  useEffect(() => {
    if (skipFirstSelectionReset.current) {
      skipFirstSelectionReset.current = false
      return
    }
    setSelectedIds(new Set())
  }, [selectedId])

  // deep-link from Planning/Overview: apply a category (or uncategorized+kind) filter token
  useEffect(() => {
    if (
      !registerFilter?.category &&
      !registerFilter?.month &&
      !registerFilter?.uncategorized &&
      !registerFilter?.dateOn &&
      !registerFilter?.payee
    )
      return
    const { category: cat, month, uncategorized, kind, dateOn, payee } = registerFilter
    selectAccount(null) // a Plan deep-link is an all-accounts view
    setFilter((f) => {
      const next = { ...f }
      // FEB-07 — a merged envelope row's deep-link carries EVERY member category; push one
      // 'category' token per key (tokensToFilterPatch already ORs repeated category tokens, and
      // GET /api/transactions ORs repeated category= params), so the register shows the SAME
      // set of transactions the popover it was opened from just listed.
      if (cat) {
        const keys = Array.isArray(cat) ? cat : [cat]
        const catTokens = keys.map((k) => ({
          field: 'category' as const,
          value: k,
          label: 'Category: ' + (catMap[k]?.label || 'Uncategorized'),
        }))
        next.tokens = [...f.tokens.filter((t) => t.field !== 'category'), ...catTokens]
      }
      if (uncategorized) {
        next.tokens = [
          ...next.tokens.filter((t) => t.field !== 'uncategorized' && t.field !== 'type'),
          { field: 'uncategorized', value: true, label: 'Uncategorized' },
          ...(kind
            ? [
                {
                  field: 'type',
                  value: kind,
                  label: 'Type: ' + kind[0]!.toUpperCase() + kind.slice(1),
                },
              ]
            : []),
        ]
      }
      if (month) {
        const [y, m] = month.split('-').map(Number)
        if (y && m) {
          next.preset = 'custom'
          next.cfM = m
          next.cfY = y
          next.ctM = m
          next.ctY = y
        }
      }
      if (payee) {
        next.tokens = [
          ...next.tokens.filter((t) => t.field !== 'payee'),
          { field: 'payee', value: payee, label: 'Payee: ' + payee },
        ]
      }
      if (dateOn) {
        const label = 'On ' + fDate(dateOn, 'short', 'always')
        next.tokens = [
          ...next.tokens.filter(
            (t) => t.field !== 'date_on' && t.field !== 'date_after' && t.field !== 'date_before',
          ),
          { field: 'date_on', value: dateOn, label },
        ]
      }
      return next
    })
    onRegisterFilterApplied?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registerFilter])

  // The deep-link effect above races `categories` loading (a fresh tab mount starts it at
  // `[]`), so a category token's label can get baked in as the load-time 'Uncategorized'
  // fallback before catMap is populated. Self-heal: once catMap has the real label, patch any
  // mismatched category token in place — a no-op (same token reference) once labels agree, so
  // this doesn't loop or refire the fetch effect above beyond the one correction.
  useEffect(() => {
    if (!categories.length) return
    setFilter((f) => {
      let changed = false
      const tokens = f.tokens.map((t) => {
        if (t.field !== 'category') return t
        const label = 'Category: ' + (catMap[String(t.value)]?.label || 'Uncategorized')
        if (label === t.label) return t
        changed = true
        return { ...t, label }
      })
      return changed ? { ...f, tokens } : f
    })
  }, [catMap, categories.length])

  // Every filter (account/date/tokens) is now applied server-side (query_txns defaults
  // include_deleted=False, and the mutation callbacks below remove a deleted row from
  // `transactions` outright rather than flagging it) — `transactions` IS the current
  // filtered view already; `filtered` is kept as the name the rest of this component
  // (selection, export, the Register) already reads.
  const filtered = transactions

  const inScopeCount = transactions.length
  const allEmpty = inScopeCount === 0 && filter.tokens.length === 0 && filter.preset === 'all'

  // ── selection ────────────────────────────────────────────────────────────────
  const toggle = (id: string) =>
    setSelectedIds((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  const toggleAll = (ids: string[]) =>
    setSelectedIds((s) => {
      const allOn = ids.length > 0 && ids.every((id) => s.has(id))
      const n = new Set(s)
      ids.forEach((id) => (allOn ? n.delete(id) : n.add(id)))
      return n
    })
  const clearSelection = () => setSelectedIds(new Set())
  const selList = filtered.filter((t) => selectedIds.has(t.id))
  const selAcct = accounts.find((a) => a.id === selectedId)
  // Selection sums BY LEG — exactly the figures the rows display. One shared currency →
  // exact native sum (never drifts with FX); mixed → ≈ base approximation (owner 2026-07-16).
  const selTotal = selectionTotal(
    selList,
    selectedId ?? null,
    selAcct?.currency ?? null,
    baseCurrency,
    fx,
  )

  // ── filter token handlers ─────────────────────────────────────────────────────
  // A date token filters the fetched rows, which the preset bounds — widen to "All time"
  // so a date outside the current preset window still resolves.
  const addToken = (t: FilterToken) =>
    setFilter((f) => ({
      ...f,
      preset: t.field.startsWith('date_') ? 'all' : f.preset,
      tokens: addFilterToken(f.tokens, t),
    }))
  const removeToken = (i: number) =>
    setFilter((f) => ({ ...f, tokens: f.tokens.filter((_, idx) => idx !== i) }))
  const clearTokens = () => setFilter((f) => ({ ...f, tokens: [] }))
  const clearAll = () => setFilter((f) => ({ ...f, tokens: [], preset: 'all' }))
  // A named preset ("This month", "Last 3 months", …) is a FROM cutoff to now — the From/To
  // fields are always visible now (owner, 2026-08-24), so picking a chip must also write the
  // month/year it actually represents into them, or the boxes go stale the moment you leave
  // "Custom" and read as if nothing happened. 'custom' and 'all' leave cf*/ct* untouched:
  // 'custom' because the fields ARE the source of truth there, 'all' because it has no start.
  const setPreset = (p: string) =>
    setFilter((f) => {
      if (p === 'custom') return { ...f, preset: p }
      // 'all' stores nothing: the From/To fields DESCRIBE it, derived in Filters from
      // dataStart → today. Writing it here would count as a filter change and reset the
      // register's scroll position (REG2).
      if (p === 'all') return { ...f, preset: p }
      const cut = presetCutoff(p)
      const today = new Date()
      return {
        ...f,
        preset: p,
        ...(cut
          ? {
              cfM: cut.getMonth() + 1,
              cfY: cut.getFullYear(),
              ctM: today.getMonth() + 1,
              ctY: today.getFullYear(),
            }
          : {}),
      }
    })
  // The filtered set's real earliest date comes from the SAME response as `total`
  // (first_date on GET /api/transactions) — no extra request, no assumption about row
  // order, and it tracks whatever filters are active. The "To" side of the All-time display
  // is always today (see Filters) — `last_date` is still returned by the API, real and
  // filter-aware, just not used to drive this display.
  const dataStart = L.firstDate ? new Date(L.firstDate) : null

  // Seed the same range on FIRST load. DEFAULT_FILTER is module-level, so it can only carry
  // "now" — the fields read Aug 2026 → Aug 2026 until the user tapped a preset. Only ever
  // runs while the preset is 'all', where the custom fields are descriptive, not in effect.
  const setCustom = (k: 'cfM' | 'cfY' | 'ctM' | 'ctY', v: number) =>
    setFilter((f) => ({ ...f, [k]: v, preset: 'custom' }))
  const addReviewToken = () => addToken({ field: 'review', value: true, label: 'Needs review' })
  const addUnclearedToken = () => addToken({ field: 'cleared', value: false, label: 'Uncleared' })

  // ── mutations ──────────────────────────────────────────────────────────────────
  const inlineAdd = (data: AddData): boolean | void => {
    const amt = parseFloat(data.amount)
    if (!amt || amt <= 0 || !data.account) return false
    const acct = accounts.find((a) => a.id === data.account)
    const ccy = acct ? acct.currency : baseCurrency
    const payeeNm = (data.payee || '').trim(),
      descInput = (data.description || '').trim(),
      memo = (data.memo || '').trim()
    // Single survivor `description`: when a payee is unrecognized, its name still rides the
    // description so the backend can resolve/learn the payee (the merge rule joins payee-name +
    // description with " — "). `memo` never feeds payee resolution — it rides through untouched.
    const description = [payeeNm, descInput].filter(Boolean).join(' — ')
    void (async () => {
      try {
        let created: CreatedTxn | undefined
        if (data.transferTo) {
          if (data.transferTo === data.account) return
          const to = accounts.find((a) => a.id === data.transferTo)
          // Direction follows the +/− toggle: money IN to the open account makes the OTHER
          // account the from-leg. Ignoring it booked every inflow transfer backwards.
          const fromId = data.inflow ? data.transferTo : data.account
          const toId = data.inflow ? data.account : data.transferTo
          const fromCcy = data.inflow ? (to ? to.currency : ccy) : ccy
          const toCcy = data.inflow ? ccy : to ? to.currency : ccy
          created = await L.createTxn({
            date: data.date,
            kind: 'transfer',
            description: descInput || 'Transfer',
            memo,
            category: data.category || null,
            from_account_id: fromId,
            from_amount: amt,
            from_currency: fromCcy,
            to_account_id: toId,
            to_amount: amt,
            to_currency: toCcy,
            cleared: false,
            source: 'manual',
          })
        } else if (data.kind === 'income') {
          created = await L.createTxn({
            date: data.date,
            kind: 'income',
            description,
            memo,
            to_account_id: data.account,
            to_amount: amt,
            to_currency: ccy,
            category: data.category || null,
            payee_id: resolvePayeeId(payeeNm),
            cleared: false,
            source: 'manual',
          })
        } else if (data.kind === 'investment') {
          // Investment buy: single cash-out from-leg; category is optional (user's classification).
          created = await L.createTxn({
            date: data.date,
            kind: 'investment',
            description,
            memo,
            from_account_id: data.account,
            from_amount: amt,
            from_currency: ccy,
            category: data.category || null,
            payee_id: resolvePayeeId(payeeNm),
            cleared: false,
            source: 'manual',
          })
        } else {
          created = await L.createTxn({
            date: data.date,
            kind: 'expense',
            description,
            memo,
            from_account_id: data.account,
            from_amount: amt,
            from_currency: ccy,
            category: data.category || null,
            payee_id: resolvePayeeId(payeeNm),
            cleared: false,
            source: 'manual',
          })
        }
        const warn =
          created?.warnings?.find((w) => w.type === 'before_tracking_start') ??
          created?.warnings?.[0]
        showToast(warn ? warn.message : data.transferTo ? 'Transfer added' : 'Transaction added')
      } catch (e) {
        showToast('Could not add: ' + (e instanceof Error ? e.message : 'error'))
      }
    })()
    return true
  }

  const updateFromPatch = (id: string, patch: RowPatch) => {
    const keys = Object.keys(patch)
    if (keys.length === 1 && keys[0] === 'cleared') {
      void L.toggleCleared(id, !!patch.cleared)
      return
    }
    const u: TransactionUpdate = {}
    if (patch.date != null) u.date = patch.date
    if (patch.description != null) u.description = patch.description
    if (patch.memo != null) u.memo = patch.memo
    if (patch.category !== undefined) u.category = patch.category
    if (patch.cleared != null) u.cleared = patch.cleared
    if (patch.kind != null) u.kind = patch.kind
    if (patch.fromAccountId !== undefined) u.from_account_id = patch.fromAccountId
    if (patch.toAccountId !== undefined) u.to_account_id = patch.toAccountId
    if (patch.fromAmount !== undefined) u.from_amount = patch.fromAmount
    if (patch.fromCurrency !== undefined) u.from_currency = patch.fromCurrency
    if (patch.toAmount !== undefined) u.to_amount = patch.toAmount
    if (patch.toCurrency !== undefined) u.to_currency = patch.toCurrency
    void (async () => {
      try {
        // A new unrecognized name never rides the note here — see resolveOrCreatePayeeId's
        // comment: update_transaction never auto-resolves/creates a payee from note text.
        if (patch.payee !== undefined) u.payee_id = await resolveOrCreatePayeeId(patch.payee)
        await L.updateTxn(id, u)
      } catch (e) {
        showToast(e instanceof Error ? e.message : 'Update failed')
      }
    })()
  }

  // The detail-drawer editor emits a richer patch (amount/currency/payeeId/tags/orig*) than the inline
  // row path; map ALL of it to a TransactionUpdate so money/payee/tags edits actually persist.
  const saveDetailEdit = (id: string, patch: Record<string, unknown>): Promise<void> => {
    const u: TransactionUpdate = detailPatchToUpdate(patch)
    return (async () => {
      // The detail picker yields a payee id when chosen from the list, or a typed name. Map an
      // id straight through; resolve a name to its id, EXPLICITLY CREATING it (and awaiting the
      // real returned id) when it's brand new — see resolveOrCreatePayeeId's comment for why this
      // can't just ride the note the way a create can.
      const payeeVal = patch.payeeId
      if (payeeVal !== undefined) {
        if (!payeeVal) u.payee_id = null
        else if (payees.some((pp) => pp.id === payeeVal)) u.payee_id = payeeVal as string
        else u.payee_id = await resolveOrCreatePayeeId(payeeVal as string)
      }
      await L.updateTxn(id, u)
      showToast('Transaction updated')
    })().catch((e) => {
      showToast(e instanceof Error ? e.message : 'Update failed')
      throw e
    })
  }

  const onReconcile = (account: Account, real: number) => {
    void L.reconcile(account.id, { real_balance: real, against: 'cleared' })
      .then((r) => {
        showToast(r.adjustment ? 'Reconciled — adjustment booked' : 'Reconciled — in sync')
        setReconcileBurst((n) => n + 1)
      })
      .catch((e) => showToast(e instanceof Error ? e.message : 'Reconcile failed'))
  }

  const archive = (a: Account) =>
    setConfirm({
      title: 'Archive account',
      body: `"${a.name}" will be hidden from your accounts. Its transactions are kept.`,
      confirmLabel: 'Archive',
      danger: true,
      onConfirm: () => {
        void L.archiveAccount(a.id).then(() => {
          if (selectedId === a.id) selectAccount(null)
          showToast('Archived ' + a.name)
        })
        setConfirm(null)
      },
    })

  // Delete is permanent (unlike archive) and, on any real transaction history, needs a SECOND
  // explicit confirm before it escalates to force=true — never auto-escalated. See
  // useAccountsLedger's deleteAccount/AccountDeleteConflictError for the API contract.
  const deleteAccount = (a: Account, onDeleted?: () => void) => {
    const afterDelete = (msg: string) => {
      if (selectedId === a.id) selectAccount(null)
      onDeleted?.()
      showToast(msg)
    }
    const runDelete = (force: boolean) =>
      L.deleteAccount(a.id, { force }).then(
        () => afterDelete(force ? `Deleted ${a.name} and its transactions` : `Deleted ${a.name}`),
        (err: unknown) => {
          // ONLY AccountDeleteConflictError (a genuine transaction_count) ever offers the
          // force-delete escalation — AccountDeleteBlockedError (a linked loan/position/lot/
          // option, which force=true cannot fix) and any other error just toast the real
          // message instead (security review 2026-09-06, second pass finding #2: escalating
          // for ANY 409 used to show "Force delete your transactions" for a refusal force
          // could never satisfy).
          if (!force && err instanceof AccountDeleteConflictError) {
            const n = err.count
            // A transfer's OTHER leg lives on a different account's history — force-deleting
            // this one silently wipes that account's copy of the same row too, so the escalated
            // confirm names it rather than let the owner discover it after the fact.
            const otherNames = err.otherAccounts.map((o) => o.name)
            const otherNote =
              otherNames.length > 0
                ? ` This includes transfers also affecting ${otherNames.join(', ')}.`
                : ''
            setDeleteConfirm({
              data: {
                title: 'Delete account and its transactions?',
                body:
                  `"${a.name}" still has ${n} transaction(s). ` +
                  'Force deleting removes the account AND all of its transactions — this ' +
                  "can't be undone." +
                  otherNote,
                confirmLabel: `Force delete and remove ${n} transaction(s)`,
                danger: true,
              },
              onConfirm: () => {
                setDeleteConfirm(null)
                void runDelete(true)
              },
            })
            return
          }
          showToast(err instanceof Error ? err.message : 'Delete failed')
        },
      )

    setDeleteConfirm({
      data: {
        title: 'Delete account',
        body: `"${a.name}" will be permanently deleted. This can't be undone.`,
        confirmLabel: 'Delete account',
        danger: true,
      },
      onConfirm: () => {
        setDeleteConfirm(null)
        void runDelete(false)
      },
    })
  }

  // bulk
  const ids = () => Array.from(selectedIds)
  const afterBulk = (msg: string) => {
    clearSelection()
    showToast(msg)
  }
  // Every bulk write goes through here so a failure can NEVER be silent. `count` is the
  // SERVER's number, not the request's — it may refuse individual rows.
  //
  // The selection deliberately SURVIVES a status/label change: the rows are still on screen and
  // still the set the user is working on, so dropping the bulk bar after one action forces a
  // full re-select to do the next thing. Only an action that removes the rows from this view
  // (move, delete) clears it — see `keepSelection`.
  const runBulk = (
    targetIds: string[],
    data: Parameters<typeof L.bulkUpdate>[1],
    done: (n: number) => string,
    keepSelection = true,
  ) => {
    if (!keepSelection) clearSelection()
    void L.bulkUpdate(targetIds, data)
      .then((r) => showToast(done(r.count)))
      .catch((e) => showToast(e instanceof Error ? e.message : 'Bulk update failed'))
  }
  // Rows a given action would ACTUALLY change. Re-applying a flag a row already carries is a
  // redundant write through the per-txn choke point (an audit entry + a checkpoint capture
  // each), and it would inflate the toast to the full selection count. It also drives the
  // disabled state of the matching menu item.
  const actionable = {
    review: selList.filter((t) => t.needsReview).length,
    clear: selList.filter((t) => !t.cleared).length,
    unclear: selList.filter((t) => t.cleared).length,
  }
  const bulkCategory = (cat: string) =>
    runBulk(ids(), { category: cat }, (n) => 'Re-categorised ' + n)
  // Mobile's bulk bar hands categorization to Lucius instead of a flyout (see BulkBar) — one
  // mention chip per selected row, exactly like dragging a multi-selection onto the dock
  // (dragRightClick.tsx's `drop`): chips carry the txn id as `ref`, so Lucius knows precisely
  // which rows are in play without a wall of raw ids in the composer text.
  const bulkAskLucius = () => {
    if (!chatComposer) return
    // On mobile this hand-off navigates to the /lucius route, which unmounts this tab — stow
    // the selection so it's there when the user comes back (see the `selectedIds` initializer).
    try {
      sessionStorage.setItem(PENDING_SELECTION_KEY, JSON.stringify(ids()))
    } catch {
      // sessionStorage unavailable (private mode, etc.) — selection just won't survive the trip
    }
    selList.forEach((t) => {
      const view = buildTxnView(t, accounts, selectedId, payeeName(t.payeeId), fx, showAccount)
      const meta = resolveChipMeta('transaction', null, view.mentionSub)
      chatComposer.addMention(
        {
          type: 'transaction',
          ref: t.id,
          label: payeeName(t.payeeId) || t.description || '(no description)',
          ...meta,
        },
        { open: false },
      )
    })
    window.dispatchEvent(new Event('lx-dock-open'))
  }
  const bulkSetCleared = (v: boolean) => {
    const targetIds = selList.filter((t) => t.cleared !== v).map((t) => t.id)
    if (targetIds.length === 0) {
      showToast(v ? 'All selected are already cleared' : 'All selected are already uncleared')
      return
    }
    runBulk(targetIds, { cleared: v }, (n) => n + (v ? ' marked cleared' : ' marked uncleared'))
  }
  const bulkMarkReviewed = () => {
    const reviewIds = selList.filter((t) => t.needsReview).map((t) => t.id)
    if (reviewIds.length === 0) {
      showToast('All selected transactions were already reviewed')
      return
    }
    runBulk(reviewIds, { needs_review: false }, (n) => `${n} marked as reviewed`)
  }
  const bulkMemo = (text: string) =>
    runBulk(ids(), { description: text }, (n) => 'Description set on ' + n)
  // A move re-points the rows at another account, so they drop out of an account-scoped
  // register — keeping them "selected" would leave the bar counting rows that are no longer in
  // view. This is the one label change that clears.
  const bulkMove = (accountId: string) =>
    runBulk(ids(), { to_account_id: accountId }, (n) => 'Moved ' + n, false)
  // Duplicate creates one row per selection (no bulk-create choke for arbitrary rows), so it
  // reports how far it got — a mid-loop failure has already written the earlier copies.
  const bulkDuplicate = async () => {
    const list = selList
    let n = 0
    try {
      for (const t of list) {
        await L.createTxn(dupCreate(t))
        n++
      }
    } catch (e) {
      showToast(
        `Duplicated ${n} of ${list.length} — ` + (e instanceof Error ? e.message : 'failed'),
      )
      return
    }
    // The originals are untouched and still on screen, so they stay selected.
    showToast('Duplicated ' + n)
  }
  const bulkExport = () => {
    exportCsv(selList)
  }
  const bulkDelete = () =>
    setConfirm({
      title: 'Delete transactions',
      body: `${selectedIds.size} transaction(s) will be deleted from the register.`,
      confirmLabel: 'Delete ' + selectedIds.size,
      danger: true,
      onConfirm: () => {
        const n = selectedIds.size
        void L.bulkDelete(ids())
          .then(() => afterBulk(n + ' deleted'))
          .catch((e) => showToast(e instanceof Error ? e.message : 'Delete failed'))
        setConfirm(null)
      },
    })
  const dupCreate = (t: Txn) => ({
    date: t.date,
    kind: t.kind,
    description: t.description,
    memo: t.memo,
    category: t.category,
    payee_id: t.payeeId,
    from_account_id: t.fromAccountId,
    from_amount: t.fromAmount,
    from_currency: t.fromCurrency,
    to_account_id: t.toAccountId,
    to_amount: t.toAmount,
    to_currency: t.toCurrency,
    tags: t.tags,
    cleared: false,
    source: 'manual' as const,
  })

  const exportCsv = (list: Txn[]) => {
    const cell = (v: unknown) => {
      const s = v == null ? '' : String(v)
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
    }
    const header = [
      'Date',
      'Payee',
      'Category',
      'Description',
      'Memo',
      'Amount',
      'Currency',
      'Amount (base)',
      'Type',
      'Cleared',
      'Source',
    ]
    const lines = list.map((t) => {
      const prim = txPrimaryAmount(t)
      const sgn = txnIsInflow(t) ? 1 : -1
      const cat =
        t.category === 'split'
          ? 'Split'
          : t.category
            ? catMap[t.category]?.label || 'Uncategorized'
            : ''
      return [
        t.date,
        payeeName(t.payeeId),
        cat,
        t.description,
        t.memo,
        (sgn * prim.mag).toFixed(2),
        prim.ccy,
        (sgn * Math.abs(t.baseAmount)).toFixed(2),
        t.kind,
        t.cleared ? 'Yes' : 'No',
        t.source,
      ]
    })
    const csv = [header, ...lines].map((r) => r.map(cell).join(',')).join('\r\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download =
      (selectedAccount
        ? selectedAccount.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()
        : 'all-accounts') +
      '-transactions-' +
      iso(new Date()) +
      '.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1500)
    showToast('Exported ' + list.length + ' transaction' + (list.length === 1 ? '' : 's'))
  }

  // accounts
  const saveAccount = async (form: Parameters<typeof L.saveAccount>[0], id?: string) => {
    // `notice` outranks the plain "Saved" toast: a type flip away from `credit` destroys the
    // card's payment envelope and its assignment history on the spot, which is the only thing
    // worth reading on this save.
    const notices: string[] = []
    const acct = await L.saveAccount(form, id, (m) => notices.push(m))
    showToast(notices[0] ?? (id ? 'Saved ' + acct.name : 'Created ' + acct.name))
    if (!id) {
      selectAccount(acct.id)
      setAcctModal({ open: true, mode: 'edit', account: acct })
    } else {
      setAcctModal({ open: false, mode: 'add', account: null })
    }
    return acct
  }

  // The account editor reports the opening read-only and hands off to the row that owns it
  // (ADR 2026-07-28). Fetched by provenance rather than read off `transactions` — the register
  // is server-filtered and paged, so the Starting Balance row is usually NOT in the loaded page.
  const openStartingBalance = async (accountId: string) => {
    try {
      const res = await authFetch(
        `/api/transactions?account_id=${encodeURIComponent(accountId)}&source=opening&limit=1`,
      )
      const row = (await res.json())?.transactions?.[0]
      if (!row) {
        showToast('This account has no starting balance transaction')
        return
      }
      setAcctModal({ open: false, mode: 'add', account: null })
      surfaces.openTransaction(fromApiTxn(row))
    } catch {
      showToast("Couldn't open the starting balance transaction")
    }
  }

  // category creation
  const requestCategory = (prefill: string, kind: CatKind, onDone: (c: Category) => void) =>
    setCatModal({ prefill: prefill || '', kind: kind || 'expense', onDone })
  // The server mints the opaque id — no client-side key/slug derivation (category-opaque-ids
  // migration; POST /api/categories ignores any caller-supplied key and returns the real id).
  const createCategory = async (data: { name: string; kind: CatKind }): Promise<Category> => {
    const color = PALETTE[categories.length % PALETTE.length]
    // A category is a pure transaction label (envelope ⊥ category) — no budget group.
    const body: Record<string, unknown> = {
      label: data.name.trim(),
      kind: data.kind,
      color,
    }
    const res = await authFetch('/api/categories', { method: 'POST', body: JSON.stringify(body) })
    if (!res.ok) throw new Error((await res.text().catch(() => '')) || 'Failed to add category')
    const cat: Category = await res.json()
    // Await the reload so `categories` (and every picker deriving its options from it) already
    // contains the new category by the time the caller's onDone/cb fires — a fire-and-forget
    // refresh raced the picker's re-render, so the just-created category could be selected before
    // it existed in the options list (and stayed missing until an unrelated later refresh).
    await L.refresh()
    showToast('Category “' + cat.label + '” added')
    return cat
  }

  // import callbacks — BOTH routes (Lucius and manual mapping) hit the ONE server decision
  // pipeline. `mapping` (manual route, CSV/XLSX) TEACHES the server the confirmed column
  // mapping — saved as the source's format profile — before the same decision preview runs.
  const importPreview = async (
    file: File | null,
    url: string,
    accountId: string,
    mapping?: Record<string, string>,
    instructions?: string,
    remember?: boolean,
    forceReparse?: boolean,
    forceRelearn?: boolean,
    accountIds?: string[],
    progressToken?: string,
  ) => {
    const fd = new FormData()
    if (file) {
      fd.append('file', file)
      const attachmentId = nativeImportAttachmentId(file)
      if (attachmentId) fd.append('source_attachment_id', attachmentId)
    }
    if (url) fd.append('source_url', url)
    if (accountId) fd.append('account_id', accountId)
    // The FULL chip set (anchor first) — the server only routes against `account_id`, but it
    // remembers these on the source's format profile so the next import of the same layout
    // pre-selects the same wallets.
    if (accountIds && accountIds.length) fd.append('account_ids', JSON.stringify(accountIds))
    if (mapping && Object.keys(mapping).length) fd.append('mapping', JSON.stringify(mapping))
    if (instructions && instructions.trim()) fd.append('instructions', instructions.trim())
    if (instructions && instructions.trim() && remember === false)
      fd.append('remember_instructions', 'false')
    if (forceReparse) fd.append('force_reparse', 'true')
    if (forceRelearn) fd.append('force_relearn', 'true')
    // B20: polled via GET /api/statements/preview-progress/{token} while this call is in
    // flight — see hooks/useImportProgress.ts.
    if (progressToken) fd.append('progress_token', progressToken)
    const res = await authFetch('/api/statements/preview', { method: 'POST', body: fd })
    if (!res.ok) throw new Error(await res.text())
    const data = await res.json()
    return mapServerPreview(data)
  }
  // SI-67 prefill probe: fingerprint the picked file server-side (zero AI, no writes) so the
  // modal can pre-fill this source's saved instructions. Failure-tolerant by contract — any
  // error reads as an unknown source and the box just stays empty.
  const importSourcePeek = async (
    file: File,
  ): Promise<{
    known: boolean
    instructions: string
    formatLabel?: string | null
    accountIds?: string[]
  }> => {
    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await authFetch('/api/statements/source-peek', { method: 'POST', body: fd })
      if (!res.ok) return { known: false, instructions: '' }
      const data = await res.json()
      return {
        known: !!data.known,
        instructions: typeof data.instructions === 'string' ? data.instructions : '',
        formatLabel: data.format_label ?? null,
        // Raw ids — the modal filters them against the live account list, so an account
        // deleted since the last import simply drops out of the prefill.
        accountIds: Array.isArray(data.account_ids)
          ? data.account_ids.filter((x: unknown) => typeof x === 'string')
          : [],
      }
    } catch {
      return { known: false, instructions: '' }
    }
  }
  // B20 async: reopen a statement's review BY ID — no re-upload, reads the record's own
  // file server-side. Same response shape as importPreview (mapServerPreview).
  const importReopenPreview = async (
    statementId: string,
    accountId?: string,
    opts: ReopenOptions = {},
  ) => {
    const fd = new FormData()
    if (accountId) fd.append('account_id', accountId)
    if (opts.instructions) fd.append('instructions', opts.instructions)
    if (opts.rememberInstructions !== undefined)
      fd.append('remember_instructions', String(opts.rememberInstructions))
    if (opts.forceReparse) fd.append('force_reparse', 'true')
    if (opts.background) fd.append('mode', 'async')
    const res = await authFetch(`/api/statements/${statementId}/preview`, {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) throw new Error(await res.text())
    return mapServerPreview(await res.json())
  }
  // "Continue in background" (B20 async): the server-side parse outlives the connection
  // regardless of how the modal closes. Tracked app-wide, BackgroundStatementNotices says it is
  // reading and then, in the same pill, when it is ready — from any tab. Only an untrackable
  // read (no statement id) falls back to the register's own toast.
  const importContinueInBackground = (statementId: string | null, filename?: string) => {
    if (statementId) {
      trackBackgroundStatement({
        id: statementId,
        accountId: importState?.accountId ?? null,
        filename,
        startedAt: Date.now(),
      })
      return
    }
    showToast("We'll keep reading — we'll tell you when it's ready to review.")
  }
  // Decision-based commit (RFC D7): one atomic server batch against the cached preview parse
  // — the ONLY commit path out of the import modal, every format (the old client-built
  // /api/transactions/bulk workaround minted no external_refs, so re-imports double-booked).
  const importDecisionCommit = async (
    statementId: string,
    acctId: string,
    decisions: Array<Record<string, unknown>>,
    deleteIds: string[] = [],
  ) => {
    const res = await authFetch('/api/statements/commit', {
      method: 'POST',
      body: JSON.stringify({
        statement_id: statementId,
        account_id: acctId,
        decisions,
        default_action: 'create',
        delete_ids: deleteIds,
        // The owner's own Confirm on the review: book exactly what it showed and they decided.
        reviewed: true,
      }),
    })
    if (!res.ok) throw new Error(await res.text())
    const out = await res.json()
    // AWAIT a FULL refresh, not a fire-and-forget refreshTxns: a commit creates rows, merges
    // rows and (since staged deletes) removes rows, so it moves account BALANCES as much as the
    // register — reloading only transactions left the Balance Owed strip showing pre-import
    // figures. `refresh` also invalidates the /api/transactions? cache by key, which is what
    // makes the reload actually hit the server. Awaited so the Done screen's counts and the
    // refreshed register can never disagree.
    await L.refresh()
    // A REST write is not broadcast (ADR 2026-09-29), so only THIS ledger hook reloaded — the
    // Transactions register, Budget and Dashboard kept pre-import rows until a page reload
    // (owner, 2026-10-26). One import moves every screen, so tell every screen.
    emitDataChanged(['*'])
    // refusedDuplicates: rows the LEDGER refused because they duplicate something already
    // booked. Same contract as deleteErrors — the user asked for these and did not get them,
    // so the Done screen names them rather than letting "import 4" quietly become "3 imported".
    return {
      ...out,
      batchId: out.batch_id ?? null,
      deleteErrors: out.delete_errors ?? [],
      refusedDuplicates: out.refused_duplicates ?? [],
    } as {
      created: number
      merged: number
      duplicates: number
      skipped: number
      transfers?: number
      batchId?: string | null
      deleted?: number
      deleteErrors?: Array<{ id: string; error: string }>
      refusedDuplicates?: Array<{
        description?: string | null
        date?: string | null
        duplicate_of?: string | null
        reason?: string | null
      }>
    }
  }
  // "Mark all as reviewed" (SI-79 Done-screen action) — clears needs_review for every row
  // just booked as new in this import batch (never a merged/linked row — those never carry it).
  const markImportBatchReviewed = async (batchId: string) => {
    const res = await authFetch(`/api/transactions/batches/${batchId}/mark-reviewed`, {
      method: 'POST',
    })
    if (!res.ok) throw new Error(await res.text())
    const out = await res.json()
    L.refreshTxns()
    return out as { updated: number }
  }

  // Undo import (Booked screen): the whole import — added rows removed, linked entries restored.
  // A FULL refresh, like the commit's: balances and the review strip moved too.
  const undoImportBatch = async (batchId: string) => {
    const res = await authFetch(`/api/transactions/batches/${batchId}/rollback`, {
      method: 'POST',
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(body?.detail || 'Could not undo this import — try again.')
    }
    const out = await res.json()
    await L.refresh()
    emitDataChanged(['*'])
    return { deleted: out.deleted ?? 0, unmerged: out.unmerged ?? 0, restored: out.restored ?? 0 }
  }

  // payee ops
  const changePayee = (id: string, patch: Record<string, unknown>) => {
    const body: Record<string, unknown> = {}
    if (patch.name != null) body.name = patch.name
    if ('defaultCategories' in patch) body.default_categories = patch.defaultCategories
    if ('autocategorize' in patch) body.autocategorize = patch.autocategorize
    if ('hidden' in patch) body.hidden = patch.hidden
    // A rename can 409 (another payee already answers to that normalized name) — surface it,
    // otherwise the row silently reverts on the next refresh with no explanation.
    void authFetch(`/api/payees/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.text().catch(() => '')) || 'Could not update payee')
        L.refresh()
      })
      .catch((e) => {
        L.refresh()
        showToast(e instanceof Error ? e.message : 'Could not update payee')
      })
  }
  const mergePayee = (from: string, to: string) =>
    void authFetch(`/api/payees/${from}/merge`, {
      method: 'POST',
      body: JSON.stringify({ target_id: to }),
    }).then(() => {
      L.refresh()
      showToast('Payees merged')
    })
  const addPayee = (name: string): string => {
    const tmp = 'pending-' + Math.abs(hashStr(name)).toString(36)
    // Same uniqueness guard as rename: a duplicate name is a 409, not a created payee.
    void authFetch('/api/payees', { method: 'POST', body: JSON.stringify({ name }) })
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.text().catch(() => '')) || 'Could not create payee')
        L.refresh()
        showToast('Payee created')
      })
      .catch((e) => {
        L.refresh()
        showToast(e instanceof Error ? e.message : 'Could not create payee')
      })
    return tmp
  }
  const deletePayee = (id: string, reassignTo?: string) =>
    void authFetch(
      `/api/payees/${id}${reassignTo ? `?reassign_to=${encodeURIComponent(reassignTo)}` : ''}`,
      { method: 'DELETE' },
    ).then(() => {
      L.refresh()
      showToast(reassignTo ? 'Payee deleted — transactions reassigned' : 'Payee deleted')
    })
  const addPayeeRule = (payeeId: string, matchType: string, value: string) =>
    void authFetch(`/api/payees/${payeeId}/rules`, {
      method: 'POST',
      body: JSON.stringify({ match_type: matchType, value }),
    })
      .then(() => L.refresh())
      .catch((e) => showToast(e instanceof Error ? e.message : 'Could not add rule'))
  const removePayeeRule = (payeeId: string, index: number) =>
    void authFetch(`/api/payees/${payeeId}/rules/${index}`, { method: 'DELETE' })
      .then(() => L.refresh())
      .catch((e) => showToast(e instanceof Error ? e.message : 'Could not remove rule'))
  const removePayeeAlias = (payeeId: string, alias: string) =>
    void authFetch(`/api/payees/${payeeId}/aliases/${encodeURIComponent(alias)}`, {
      method: 'DELETE',
    })
      .then(() => {
        L.refresh()
        showToast('Auto-match removed')
      })
      .catch((e) => showToast(e instanceof Error ? e.message : 'Could not remove auto-match'))
  const viewPayee = (name: string) => {
    setShowPayees(false)
    selectAccount(null)
    addToken({ field: 'payee', value: name, label: 'Payee: ' + name })
  }
  // Statement Import's "View imported" deep-link. A multi-account statement routes rows
  // across several accounts, so this is an all-accounts view — same reasoning as viewPayee
  // above — filtered to exactly this one batch.
  const viewImportBatch = (batchId: string, label?: string) => {
    selectAccount(null)
    addToken({ field: 'importBatch', value: batchId, label: label || 'This import' })
  }
  const applyRulesHistory = () =>
    void authFetch('/api/payees/apply-rules', {
      method: 'POST',
      body: JSON.stringify({ confirm: true }),
    }).then(() => {
      L.refreshTxns()
      showToast('Applied rules to history')
    })
  const applyPayeeCategory = (payeeId: string) =>
    void authFetch(`/api/payees/${payeeId}/apply-category`, { method: 'POST' })
      .then(() => L.refreshTxns())
      .catch((e) => showToast(e instanceof Error ? e.message : 'Could not apply category'))
  const applyPayeeRulesExisting = (payeeId: string) =>
    void authFetch(`/api/payees/${payeeId}/apply-rules`, { method: 'POST' })
      .then(() => L.refreshTxns())
      .catch((e) => showToast(e instanceof Error ? e.message : 'Could not apply rules'))

  const loadActivity = async (id: string) => {
    const res = await authFetch(`/api/transactions/${id}/activity`)
    if (!res.ok) throw new Error('No activity')
    return res.json()
  }

  // Every source snapshot behind a booked (or merge-enriched) txn, from email (EM1) or a
  // bank/broker connector (connectors-as-data-sources RFC §B). 404 = no provenance of either
  // kind at all — a normal, non-error outcome (→ null), not a throw; any other non-ok status
  // is a genuine failure. A pruned/never-stashed snapshot arrives as an explicit `unavailable`
  // entry in the list, never a silent omission. Was GET .../source-emails ({emails: [...]});
  // renamed to .../sources ({entries: [...]}) with the connector union — no compat shim.
  const loadSourceEntries = async (id: string) => {
    const res = await authFetch(`/api/transactions/${id}/sources`)
    if (res.status === 404) return null
    if (!res.ok) throw new Error('No source entries')
    const body = (await res.json()) as { entries?: unknown[] }
    return (body.entries ?? []).map((e) => fromApiSourceEntry(e as Record<string, unknown>))
  }

  const payeeOptions = useMemo(
    () =>
      payees.map((p) => ({
        value: p.name,
        label: p.name,
        defaultCategory: p.defaultCategories?.expense ?? null,
      })),
    [payees],
  )
  const payeeNames = useMemo(
    () => Object.fromEntries(payees.map((p) => [p.id, p.name])) as Record<string, string>,
    [payees],
  )

  // ── render ────────────────────────────────────────────────────────────────────
  // The review overlay is a sibling of the shell in EVERY branch, at the same tree position:
  // rendered inside the loaded shell alone, it only appeared once the skeleton had gone, and
  // moving between branches would remount it (and refetch its queue). Its own loading state
  // covers the ledger's, so a deep link sees one overlay from the first frame.
  if (L.loading && !L.hasLoaded) {
    return (
      <>
        <div className="acct-shell acct-shell-state">
          <TabSkeleton variant="accounts" label="Loading accounts…" />
        </div>
      </>
    )
  }
  if (L.error && !L.hasLoaded) {
    return (
      <>
        <div className="acct-shell acct-shell-state">
          <ErrorState
            title="Couldn’t load accounts"
            message={L.error}
            onRetry={() => L.refresh()}
          />
        </div>
      </>
    )
  }

  const showAccount = selectedId === null

  return (
    <>
      <div
        className="acct-shell"
        ref={shellRef}
        style={{
          gridTemplateColumns: railCollapsed ? 'minmax(0,1fr)' : `${sideW}px minmax(0,1fr)`,
        }}
      >
        {!isMobile && !collapsed && (
          <Sidebar
            accounts={accounts}
            groups={L.groups}
            totals={totals}
            selectedId={selectedId}
            netWorth={L.netWorth}
            collapsed={collapsed}
            onToggleCollapse={() => pinCollapse(true)}
            onPointerDown={onSidebarPointerDown}
            onSelect={selectAccount}
            onAdd={() => setAcctModal({ open: true, mode: 'add', account: null })}
            onManagePayees={() => setShowPayees(true)}
          />
        )}

        <main
          ref={mainRef}
          className={'acct-main' + (railCollapsed ? ' is-collapsed' : '')}
          data-ctx-region="content"
          onScroll={() => {
            // Debounce: only load once scrolling SETTLES (not on every event), so a hard fling near the
            // bottom triggers a single load instead of a burst of pages.
            if (loadTimer.current) clearTimeout(loadTimer.current)
            loadTimer.current = setTimeout(() => {
              const el = mainRef.current
              // Prefetch when ~20 rows (40px each) from the bottom, so the next page is loaded well
              // before the last row is reached. loadMore self-guards (synchronous ref + hasMore).
              if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 20 * 40) L.loadMore()
            }, 160)
          }}
        >
          {accounts.length === 0 ? (
            <DataInHero
              title="Start with the account you use most"
              body="Add it here or with Lucius. An automation can then keep its transactions up to date and categorized for you."
              onAddAccount={() => setAcctModal({ open: true, mode: 'add', account: null })}
              luciusMessage={SCREEN_INTROS.accounts.lucius}
              onAskLucius={onAskLucius}
            />
          ) : (
            <>
              {selectedAccount ? (
                <>
                  <AccountHeader
                    account={selectedAccount}
                    dueInfo={dueInfo}
                    stats={stats}
                    menuOpen={menuOpen}
                    setMenuOpen={setMenuOpen}
                    collapsed={railCollapsed}
                    onReopen={openAccountNavigator}
                    onAddTxn={handleAddTxn}
                    onEdit={() => {
                      setMenuOpen(false)
                      setAcctModal({ open: true, mode: 'edit', account: selectedAccount })
                    }}
                    onArchive={() => {
                      setMenuOpen(false)
                      archive(selectedAccount)
                    }}
                    onDelete={() => {
                      setMenuOpen(false)
                      deleteAccount(selectedAccount)
                    }}
                    onManagePayees={() => {
                      setMenuOpen(false)
                      setShowPayees(true)
                    }}
                    onManageCategories={() => {
                      setMenuOpen(false)
                      setShowCategories(true)
                    }}
                    onAskLucius={onAskLucius}
                    onImport={() => {
                      setMenuOpen(false)
                      setImportState({ accountId: selectedAccount?.id ?? null })
                    }}
                    onReviewStatement={(statementId, filename, uploadedAt) => {
                      setMenuOpen(false)
                      setImportState({
                        accountId: null,
                        resumeStatementId: statementId,
                        resumeFilename: filename,
                        resumeUploadedAt: uploadedAt,
                      })
                    }}
                    onExport={() => {
                      setMenuOpen(false)
                      exportCsv(filtered)
                    }}
                    onReconcile={onReconcile}
                    onReview={addReviewToken}
                    onStartReview={() => surfaces.openReview('all')}
                  />
                </>
              ) : (
                <Overview
                  totals={totals}
                  groups={L.groups}
                  cashBalance={L.cashBalance}
                  stats={stats}
                  baseCurrency={baseCurrency}
                  collapsed={railCollapsed}
                  onReopen={openAccountNavigator}
                  onAddTxn={handleAddTxn}
                  onAdd={() => setAcctModal({ open: true, mode: 'add', account: null })}
                  onManagePayees={() => setShowPayees(true)}
                  onManageCategories={() => setShowCategories(true)}
                  onImport={() => setImportState({ accountId: null })}
                  onExport={() => exportCsv(filtered)}
                  onReview={addReviewToken}
                  onStartReview={() => surfaces.openReview('all')}
                  onShowUncleared={addUnclearedToken}
                  onArchivedAccounts={openArchivedAccounts}
                  archivedCount={archivedAccounts.length}
                  onPhoneShortcuts={
                    onNavigate ? () => onNavigate('settings', { sub: 'shortcuts' }) : undefined
                  }
                />
              )}

              <div className="acct-sticky">
                <div className="acct-filterblock">
                  <Filters
                    filter={filter}
                    dataStart={dataStart}
                    suggest={suggest}
                    sym={scopeSym}
                    addToken={addToken}
                    removeToken={removeToken}
                    clearTokens={clearTokens}
                    setPreset={setPreset}
                    setCustom={setCustom}
                    onAddTxn={handleAddTxn}
                    showAccount={showAccount}
                    columnVis={columnVis}
                  />
                  <ActiveChips
                    filter={filter}
                    removeToken={removeToken}
                    clearAll={clearAll}
                    setPreset={setPreset}
                    sym={scopeSym}
                  />
                </div>
              </div>

              <Register
                txns={filtered}
                total={L.total}
                accounts={accounts}
                showAccount={showAccount}
                selectedId={selectedId}
                selectedIds={selectedIds}
                onToggle={toggle}
                onToggleAll={toggleAll}
                onClearSelection={clearSelection}
                onSelectRange={(rangeIds) => setSelectedIds((s) => new Set([...s, ...rangeIds]))}
                onUpdate={updateFromPatch}
                onAdd={inlineAdd}
                adding={adding}
                onAdding={setAdding}
                onManagePayees={() => setShowPayees(true)}
                categories={categories}
                onCreateCategory={(p, k, cb) => requestCategory(p, k, (c) => cb(c.id))}
                payeeOptions={payeeOptions}
                payeeNames={payeeNames}
                rates={fx}
                hiddenCols={columnVis.hidden}
                onEdit={(t) => surfaces.openTransaction(t, 'edit')}
                onOpenDetail={(t) => surfaces.openTransaction(t, 'view')}
                onImport={() => setImportState({ accountId: null })}
                onAskLucius={onAskLucius}
                allEmpty={allEmpty}
                loadingTxns={L.loadingTxns}
                isLoadingMore={L.isLoadingMore}
                onAddTxn={handleAddTxn}
                onClearFilters={clearAll}
              />
            </>
          )}
        </main>

        {selectedIds.size > 0 && (
          <BulkBar
            count={selectedIds.size}
            total={selTotal}
            accounts={accounts}
            categories={categories}
            railCollapsed={railCollapsed}
            railWidth={sideW}
            onClear={clearSelection}
            onCategory={bulkCategory}
            onAskLucius={bulkAskLucius}
            onMarkCleared={() => bulkSetCleared(true)}
            onMarkUncleared={() => bulkSetCleared(false)}
            onMarkReviewed={bulkMarkReviewed}
            actionable={actionable}
            onDuplicate={() => void bulkDuplicate()}
            onMemo={bulkMemo}
            onMove={bulkMove}
            onExport={bulkExport}
            onDelete={bulkDelete}
          />
        )}

        {isMobile && (
          <Modal
            open={accountSheetOpen}
            onClose={() => setAccountSheetOpen(false)}
            title="Accounts"
            className="acct-side-sheet"
          >
            <Sidebar
              sheet
              accounts={accounts}
              groups={L.groups}
              totals={totals}
              selectedId={selectedId}
              netWorth={L.netWorth}
              collapsed={false}
              onSelect={(id) => {
                selectAccount(id)
                setAccountSheetOpen(false)
              }}
              onAdd={() => {
                setAccountSheetOpen(false)
                setAcctModal({ open: true, mode: 'add', account: null })
              }}
              onManagePayees={() => {
                setAccountSheetOpen(false)
                setShowPayees(true)
              }}
            />
          </Modal>
        )}

        <ArchivedAccountsModal
          open={archivedOpen}
          accounts={archivedAccounts}
          loading={archivedLoading}
          error={archivedError}
          selected={archivedSelected}
          transactions={archivedTransactions}
          loadingTransactions={archivedTransactionsLoading}
          onClose={() => setArchivedOpen(false)}
          onSelect={(account) => void openArchivedAccount(account)}
          onBack={() => setArchivedSelected(null)}
          onRestore={(account) => {
            void L.restoreAccount(account.id)
              .then(() => {
                setArchivedAccounts((rows) => rows.filter((row) => row.id !== account.id))
                setArchivedSelected(null)
                showToast(`Restored ${account.name}`)
              })
              .catch((error: unknown) => {
                showToast(error instanceof Error ? error.message : 'Could not restore account')
              })
          }}
          onDelete={(account) =>
            deleteAccount(account, () => {
              setArchivedAccounts((rows) => rows.filter((row) => row.id !== account.id))
              setArchivedSelected(null)
            })
          }
        />

        <AccountModal
          open={acctModal.open}
          mode={acctModal.mode}
          account={acctModal.account}
          prefill={addPrefill}
          institutions={institutions}
          onRenameInstitution={renameInstitution}
          onDeleteInstitution={deleteInstitution}
          onClose={(action) => {
            setAcctModal({ open: false, mode: 'add', account: null })
            if (action === 'connect') onNavigate?.('connectors')
          }}
          onSave={saveAccount}
          onEditOpening={
            acctModal.account ? () => void openStartingBalance(acctModal.account!.id) : undefined
          }
        />

        {confirm && (
          <ConfirmModal
            open
            title={confirm.title}
            body={confirm.body}
            confirmLabel={confirm.confirmLabel}
            danger={confirm.danger}
            onConfirm={confirm.onConfirm}
            onClose={() => setConfirm(null)}
          />
        )}
        <ConfirmDialog
          data={deleteConfirm?.data ?? null}
          onConfirm={() => deleteConfirm?.onConfirm()}
          onCancel={() => setDeleteConfirm(null)}
        />
        {importState && (
          <ImportModal
            open
            accounts={accounts}
            initialFile={importState.initialFile}
            defaultAccountId={importState.accountId || undefined}
            resumeStatementId={importState.resumeStatementId}
            resumeFilename={importState.resumeFilename}
            resumeUploadedAt={importState.resumeUploadedAt}
            onReopenPreview={importReopenPreview}
            onContinueInBackground={importContinueInBackground}
            categories={categories}
            catMap={catMap}
            onClose={() => setImportState(null)}
            onPreview={importPreview}
            onSourcePeek={importSourcePeek}
            onDecisionCommit={async (statementId, accountId, decisions, deleteIds, sourceFile) => {
              const result = await importDecisionCommit(
                statementId,
                accountId,
                decisions,
                deleteIds,
              )
              if (sourceFile && !(await markNativeImportReviewed(sourceFile))) {
                showToast('Import saved. The folder review status could not be updated.')
              }
              return result
            }}
            onMarkImportReviewed={markImportBatchReviewed}
            onUndoImport={undoImportBatch}
            payeeOptions={payeeOptions}
            payeeById={payeeNames}
            importReviewDefault={importReviewDefault}
            baseCurrency={baseCurrency}
            rates={fx}
            onSaveTxn={saveDetailEdit}
            onDeleteTxn={(id) => {
              showToast('Transaction deleted')
              return L.deleteTxn(id)
            }}
            onMarkTxnReviewed={(id) =>
              L.updateTxn(id, { needs_review: false }).then(() => {
                showToast('Marked as reviewed')
              })
            }
            onManagePayees={() => setShowPayees(true)}
            onCreateCategory={(p, k, cb) => requestCategory(p, k, (c) => cb(c.id))}
            onLoadTxnActivity={loadActivity}
            onLoadTxnSourceEntries={loadSourceEntries}
            onDetachTxnSourceRef={async (tx, source, ref) => {
              await L.detachSourceRef(tx, source, ref)
            }}
            onViewImportBatch={(batchId, label) => {
              setImportState(null)
              viewImportBatch(batchId, label)
            }}
          />
        )}
        {showCategories && (
          <CategoriesManager
            toast={showToast}
            onClose={() => setShowCategories(false)}
            onMutated={L.refresh}
          />
        )}
        {showPayees && (
          <PayeesModal
            open
            payees={payees}
            categories={categories}
            onClose={() => setShowPayees(false)}
            onChange={changePayee}
            onMerge={mergePayee}
            onView={viewPayee}
            onAdd={addPayee}
            onDelete={deletePayee}
            onApplyRules={applyRulesHistory}
            onAddRule={addPayeeRule}
            onRemoveRule={removePayeeRule}
            onApplyCategory={applyPayeeCategory}
            onApplyPayeeRules={applyPayeeRulesExisting}
            onRemoveAlias={removePayeeAlias}
          />
        )}
        {catModal && (
          <AddCategoryModal
            open
            prefill={catModal.prefill}
            kind={catModal.kind}
            onClose={() => setCatModal(null)}
            onSave={(data) => {
              void createCategory(data)
                .then((c) => catModal.onDone(c))
                .catch((e) => showToast(e instanceof Error ? e.message : 'Failed to add category'))
              setCatModal(null)
            }}
          />
        )}

        {reconcileBurst > 0 && (
          <div key={reconcileBurst} className="acct-reconcile-burst">
            <ReviewConfetti />
          </div>
        )}
        {/* Centred on the app window, the same line as every other floating pill (the
            background-read notices included) — not on the register (owner, 2026-10-25). */}
        <Toast message={toast} onDone={() => setToast(null)} />
      </div>
    </>
  )
}

function hashStr(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = (h << 5) - h + s.charCodeAt(i)
    h |= 0
  }
  return h
}

// silence unused-import guards for re-exported helpers kept for parity
void toBase
void fBase

export default AccountsTab
