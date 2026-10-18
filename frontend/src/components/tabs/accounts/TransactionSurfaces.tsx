// TransactionSurfaces — the review cards and the transaction drawer, hosted ONCE at the app
// level so they open in place from anywhere: Lucius's "N transactions added by automations",
// the Accounts tab's "Start review", a register row, a Plan-tab deep link. They used to be a
// mode of the Accounts tab — closed over its ledger hook for every write — so opening the
// review from Lucius meant navigating to Accounts, loading its skeleton, then opening
// (owner, 2026-09-22: "why do we have to navigate to accounts tab… it should open whenever
// needed"). Writes go through transactionApi.ts, which announces each one on the data bus;
// the register, the counts and Lucius's landing all refetch from that.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { authFetch } from '../../../api/client'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { fetchCachedQuery } from '../../../state/queryCache'
import { getBaseCurrency } from '../../../utils/format'
import { ConfirmModal } from './ConfirmModal'
import { AddCategoryModal, type CatKind } from './AddCategoryModal'
import TxnDetail from './TxnDetail'
import { BackgroundStatementNotices } from './BackgroundStatementNotices'
import { TransactionReviewMode, type ReviewScope } from './TransactionReviewMode'
import { TransactionSurfacesContext, type TransactionSurfaces } from './transactionSurfacesContext'
import { detailPatchToUpdate, fromApiAccount, fromApiTxn } from './data'
import { reviewDraftToUpdate, validateReviewDraft, type ReviewDraft } from './transactionReview'
import {
  createCategory,
  deleteTransaction,
  purgeTransaction,
  detachTransactionSourceRef,
  loadTransactionActivity,
  loadTransactionSourceEntries,
  loadTransactionStatementLinks,
  resolveOrCreatePayeeId,
  setTransactionCleared,
  updateTransaction,
} from './transactionApi'
import type {
  Account,
  ApiAccount,
  ApiPayee,
  ApiTxn,
  Category,
  FxRates,
  Payee,
  TransactionUpdate,
  Txn,
} from './types'

/** The reference data both surfaces read — the same cached queries the Accounts tab's ledger
 *  hook uses, so a book already loaded there costs nothing here. Warmed shortly after the app
 *  mounts (so the first open never waits on it) and refreshed whenever the bus says those
 *  domains changed. */
function useReferenceData(active: boolean) {
  const [refs, setRefs] = useState<{
    accounts: Account[]
    categories: Category[]
    payees: Payee[]
  } | null>(null)
  const version = useDataVersion(['ledger', 'accounts', 'payees', 'categories'])
  const loaded = useRef(false)
  // Warm-up: a beat after mount, off the first paint's critical path.
  const [warm, setWarm] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setWarm(true), 1500)
    return () => window.clearTimeout(timer)
  }, [])
  useEffect(() => {
    if (!active && !warm) return
    let live = true
    const json = <T,>(url: string, force: boolean) =>
      fetchCachedQuery<T>(
        url,
        async () => {
          const res = await authFetch(url)
          if (!res.ok) throw new Error(`${url} fetch failed`)
          return res.json() as Promise<T>
        },
        { force },
      )
    const force = loaded.current
    void Promise.all([
      json<ApiAccount[]>('/api/accounts', force),
      json<Category[]>('/api/categories', force),
      json<ApiPayee[] | { payees?: ApiPayee[] }>('/api/payees', force),
    ])
      .then(([accounts, categories, payeeJson]) => {
        if (!live) return
        const raw: ApiPayee[] = Array.isArray(payeeJson) ? payeeJson : (payeeJson.payees ?? [])
        setRefs({
          accounts: accounts.map((a) => fromApiAccount(a)),
          categories,
          payees: raw.map((p) => ({
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
        })
        loaded.current = true
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [active, warm, version])
  return refs
}

/** A row known only by id, until the server's answer replaces it: every field at rest, the
 *  caller's label (if any) as the description so the title reads meanwhile. */
function placeholderTxn(id: string, hint?: string): Txn {
  return {
    id,
    date: '',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: hint ?? '',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: null,
    fromAmount: null,
    fromCurrency: null,
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 0,
    baseCurrency: getBaseCurrency(),
    cleared: false,
    source: 'manual',
    createdAt: '',
    deleted: false,
    needsReview: false,
    externalRefs: [],
  }
}

function readFxRates(): FxRates {
  const base = getBaseCurrency()
  try {
    return { [base]: 1.0, ...JSON.parse(localStorage.getItem('wealthFX') || '{}') }
  } catch {
    return { [base]: 1.0 }
  }
}

export function TransactionSurfacesProvider({
  children,
  onManagePayees,
  onOpenAccounts,
}: {
  children: ReactNode
  /** The payees manager lives on the Accounts tab; the drawer's link goes there. */
  onManagePayees: () => void
  /** Takes the owner to the Accounts tab — where a finished background statement read's
   *  "Review" (BackgroundStatementNotices) opens its import review. */
  onOpenAccounts?: () => void
}) {
  const [reviewScope, setReviewScope] = useState<ReviewScope | null>(null)
  // `txn: null` while a row opened by id is still on its way; `hint` is what the caller
  // knew of it (a receipt's label), shown as its title meanwhile. `error`: a write made for the
  // drawer failed — said in the drawer itself; these surfaces show no toast bar (owner,
  // 2026-09-28), and one under the drawer and the review cards was rarely seen anyway.
  const [detail, setDetail] = useState<{
    id: string
    txn: Txn | null
    mode: 'view' | 'edit'
    hint?: string
    error?: string
  } | null>(null)
  const [confirm, setConfirm] = useState<{ txn: Txn } | null>(null)
  const [purgeConfirm, setPurgeConfirm] = useState<{ txn: Txn } | null>(null)
  const [catModal, setCatModal] = useState<{
    prefill: string
    kind: CatKind
    onDone: (id: string) => void
  } | null>(null)
  // A row the drawer changed while the review is open, for the queue to patch or drop in place.
  const [rowChange, setRowChange] = useState<{ txn: Txn; seq: number } | null>(null)
  const changeSeq = useRef(0)
  const noteRowChange = useCallback((txn: Txn) => {
    changeSeq.current += 1
    setRowChange({ txn, seq: changeSeq.current })
  }, [])
  /** `id` null: whichever drawer is open (the add-category modal belongs to it). */
  const noteDetailFailure = useCallback((id: string | null, e: unknown, fallback: string) => {
    const error = e instanceof Error && e.message ? e.message : fallback
    setDetail((d) => (d && (id === null || d.id === id) ? { ...d, error } : d))
  }, [])
  const clearDetailFailure = useCallback(
    () => setDetail((d) => (d?.error ? { ...d, error: undefined } : d)),
    [],
  )

  const active = reviewScope !== null || detail !== null
  const refs = useReferenceData(active)
  const accounts = refs?.accounts ?? EMPTY_ACCOUNTS
  const categories = refs?.categories ?? EMPTY_CATEGORIES
  const payees = refs?.payees ?? EMPTY_PAYEES
  const payeeNames = useMemo(
    () => Object.fromEntries(payees.map((p) => [p.id, p.name])) as Record<string, string>,
    [payees],
  )
  const payeeOptions = useMemo(
    () =>
      payees.map((p) => ({
        value: p.name,
        label: p.name,
        defaultCategory: p.defaultCategories?.expense ?? null,
      })),
    [payees],
  )
  const fx = useMemo(() => readFxRates(), [])

  const openReview = useCallback((scope: ReviewScope) => setReviewScope(scope), [])
  const openTransaction = useCallback(
    (txn: Txn, mode: 'view' | 'edit' = 'view') => setDetail({ id: txn.id, txn, mode }),
    [],
  )
  const openTransactionById = useCallback(
    (id: string, hint?: string) => {
      setDetail({ id, txn: null, mode: 'view', hint })
      void authFetch(`/api/transactions/${encodeURIComponent(id)}`)
        .then((res) => (res.ok ? (res.json() as Promise<ApiTxn>) : null))
        .then((row) => {
          if (!row) throw new Error('This transaction could not be loaded.')
          const txn = fromApiTxn(row)
          setDetail((d) => (d && d.id === id ? { ...d, txn } : d))
        })
        .catch((e: unknown) => noteDetailFailure(id, e, 'This transaction could not be loaded.'))
    },
    [noteDetailFailure],
  )
  const surfaces = useMemo<TransactionSurfaces>(
    () => ({
      openReview,
      openTransaction,
      openTransactionById,
      reviewOpen: reviewScope !== null,
    }),
    [openReview, openTransaction, openTransactionById, reviewScope],
  )

  const approve = useCallback(
    async (txn: Txn, draft: ReviewDraft): Promise<Txn> => {
      const invalid = validateReviewDraft(txn, draft, accounts)
      if (invalid) throw new Error(invalid)
      const update: TransactionUpdate = reviewDraftToUpdate(txn, draft, accounts)
      if (
        draft.kind !== txn.kind &&
        (draft.kind === 'transfer' || draft.kind === 'investment' || draft.kind === 'adjustment')
      ) {
        update.payee_id = null
      } else if (draft.payeeEdited) {
        // The card only links an EXISTING payee; creating one is the editor's job.
        const wanted = draft.payee.trim().toLowerCase()
        const payeeId = wanted
          ? (payees.find((p) => p.name.toLowerCase() === wanted)?.id ?? null)
          : null
        if (wanted && !payeeId) {
          throw new Error('Choose an existing payee, or use Edit all details to create one.')
        }
        update.payee_id = payeeId
      }
      return updateTransaction(txn.id, update)
    },
    [accounts, payees],
  )

  const saveDetail = useCallback(
    async (id: string, patch: Record<string, unknown>) => {
      // A refused save rejects to the drawer, which shows it beside its Save button — no
      // toast here on top of that, which said the same thing twice.
      const update = detailPatchToUpdate(patch)
      if (patch.payeeId !== undefined) {
        const value = patch.payeeId
        if (!value) update.payee_id = null
        else if (payees.some((p) => p.id === value)) update.payee_id = value as string
        else update.payee_id = await resolveOrCreatePayeeId(value as string, payees)
      }
      const updated = await updateTransaction(id, update)
      setDetail((d) => (d && d.id === id ? { ...d, txn: updated, error: undefined } : d))
      noteRowChange(updated)
    },
    [noteRowChange, payees],
  )

  return (
    <TransactionSurfacesContext.Provider value={surfaces}>
      {children}
      <BackgroundStatementNotices onOpenAccounts={onOpenAccounts} />
      <TransactionReviewMode
        open={reviewScope !== null}
        scope={reviewScope ?? 'all'}
        accounts={accounts}
        categories={categories}
        payeeNames={payeeNames}
        ready={refs !== null}
        onClose={() => setReviewScope(null)}
        onApprove={approve}
        onReject={(txn) => deleteTransaction(txn.id)}
        onViewAll={(txn) => openTransaction(txn, 'view')}
        onEditAll={(txn) => openTransaction(txn, 'edit')}
        rowChanged={rowChange}
      />
      {detail && (
        <TxnDetail
          // Opened by id: the drawer itself opens at once, its sections loading by that id,
          // hero and rows in its skeleton until the row lands — nothing in between.
          txn={detail.txn ?? placeholderTxn(detail.id, detail.hint)}
          loading={!detail.txn}
          actionError={detail.error ?? null}
          liftAboveModal={reviewScope !== null}
          initialMode={detail.mode}
          accounts={accounts}
          categories={categories}
          payeeOptions={payeeOptions}
          payeeById={payeeNames}
          baseCurrency={getBaseCurrency()}
          rates={fx}
          onClose={() => setDetail(null)}
          onSave={saveDetail}
          onDelete={(txn) => setConfirm({ txn })}
          onPurge={(txn) => setPurgeConfirm({ txn })}
          onMarkReviewed={(txn) => {
            clearDetailFailure()
            void updateTransaction(txn.id, { needs_review: false })
              .then((updated) => {
                setDetail((d) => (d && d.id === txn.id ? { ...d, txn: updated } : d))
                noteRowChange(updated)
              })
              .catch((e) => noteDetailFailure(txn.id, e, 'Update failed'))
          }}
          onToggleCleared={(txn, cleared) => {
            clearDetailFailure()
            void setTransactionCleared(txn.id, cleared)
              .then((updated) => {
                setDetail((d) => (d && d.id === txn.id ? { ...d, txn: updated } : d))
                noteRowChange(updated)
              })
              .catch((e) => noteDetailFailure(txn.id, e, 'Update failed'))
          }}
          onManagePayees={() => {
            setDetail(null)
            onManagePayees()
          }}
          onCreateCategory={(prefill, kind, cb) =>
            setCatModal({ prefill: prefill || '', kind: kind || 'expense', onDone: cb })
          }
          onLoadActivity={loadTransactionActivity}
          onLoadSourceEntries={loadTransactionSourceEntries}
          onDetachSourceRef={async (txn, source, ref) => {
            const updated = await detachTransactionSourceRef(txn, source, ref)
            setDetail((d) => (d && d.id === txn.id ? { ...d, txn: updated } : d))
          }}
          onLoadStatementLinks={loadTransactionStatementLinks}
        />
      )}
      {confirm && (
        <ConfirmModal
          open
          title="Delete transaction"
          body={`“${payeeNames[confirm.txn.payeeId ?? ''] || confirm.txn.description || 'This transaction'}” will be removed.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => {
            const txn = confirm.txn
            setConfirm(null)
            clearDetailFailure()
            // The drawer stays until the delete lands: a refused one is said in it, and the
            // transaction it shows still exists.
            void deleteTransaction(txn.id)
              .then(() => {
                setDetail((d) => (d && d.id === txn.id ? null : d))
                noteRowChange({ ...txn, deleted: true })
              })
              .catch((e) => noteDetailFailure(txn.id, e, 'Delete failed'))
          }}
          onClose={() => setConfirm(null)}
        />
      )}
      {purgeConfirm && (
        <ConfirmModal
          open
          title="Delete permanently?"
          body={`“${payeeNames[purgeConfirm.txn.payeeId ?? ''] || purgeConfirm.txn.description || 'This transaction'}” and its edit history will be erased, with receipts attached only to it. This can't be undone, not even by Lucius's undo. Backups made before now keep it until they expire.`}
          confirmLabel="Delete permanently"
          danger
          onConfirm={() => {
            const txn = purgeConfirm.txn
            setPurgeConfirm(null)
            clearDetailFailure()
            void purgeTransaction(txn.id)
              .then(() => {
                setDetail((d) => (d && d.id === txn.id ? null : d))
                noteRowChange({ ...txn, deleted: true })
              })
              .catch((e) => noteDetailFailure(txn.id, e, 'Delete failed'))
          }}
          onClose={() => setPurgeConfirm(null)}
        />
      )}
      {catModal && (
        <AddCategoryModal
          open
          prefill={catModal.prefill}
          kind={catModal.kind}
          onClose={() => setCatModal(null)}
          onSave={(data) => {
            const done = catModal.onDone
            setCatModal(null)
            clearDetailFailure()
            void createCategory(data.name, data.kind, categories.length)
              .then((c) => done(c.id))
              .catch((e) => noteDetailFailure(null, e, 'Failed to add category'))
          }}
        />
      )}
    </TransactionSurfacesContext.Provider>
  )
}

const EMPTY_ACCOUNTS: Account[] = []
const EMPTY_CATEGORIES: Category[] = []
const EMPTY_PAYEES: Payee[] = []
