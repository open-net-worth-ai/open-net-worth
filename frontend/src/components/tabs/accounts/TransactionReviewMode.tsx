import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Check, ChevronLeft, Trash2 } from '../../../ui/icons'
import { Button, ConfirmDialog, Modal } from '../../ui'
import { fromApiTxn } from './data'
import { ReviewConfetti } from './ReviewConfetti'
import { TransactionReviewCard } from './TransactionReviewCard'
import { useReviewWaitingElsewhere } from './useReviewWaitingElsewhere'
import { startingReviewDraft, validateReviewDraft, type ReviewDraft } from './transactionReview'
import type { Account, ApiTxn, Category, Txn } from './types'

export type { ReviewDraft } from './transactionReview'

/** What the cards go through. `automation` — only rows an automation run created (the
 *  `?review=transactions` deep link); `all` — every row flagged for review, imports, shortcuts
 *  and chat-added rows included: the Accounts tab's "Start review" and Lucius's Needs
 *  attention card both open it (owner decisions 2026-09-20, 2026-10-01). */
export type ReviewScope = 'automation' | 'all'

function queueUrl(scope: ReviewScope, accountId: string | null): string {
  return `/api/transactions/recently-added?needs_review=true${
    scope === 'automation' ? '&automation_only=true' : ''
  }${accountId ? `&account_id=${encodeURIComponent(accountId)}` : ''}&limit=500`
}

interface ReviewQueueResponse {
  transactions?: ApiTxn[]
  total?: number
  has_more?: boolean
}

export interface TransactionReviewModeProps {
  open: boolean
  scope: ReviewScope
  /** One account's flagged rows only — Start review on an account page. */
  accountId?: string | null
  refreshSignal?: number
  accounts: Account[]
  categories: Category[]
  payeeNames: Record<string, string>
  /** False while the tab that owns the ledger is still loading: the queue may already be here,
   *  but a card needs the accounts, categories and payees the tab carries. Shown as loading
   *  until both are in, so the overlay can open on the tab's first frame instead of after its
   *  skeleton (owner report 2026-09-20). */
  ready?: boolean
  onClose: () => void
  onApprove: (txn: Txn, draft: ReviewDraft) => Promise<Txn>
  onReject: (txn: Txn) => Promise<void>
  /** Accepted but unused: the inline Ask Lucius surface is removed for now. */
  onAskLucius?: (txn: Txn, prompt: string) => Promise<void>
  onViewAll: (txn: Txn) => void
  onEditAll: (txn: Txn) => void
  /** Reopens the review across every account — offered once an account's own runs out. */
  onReviewEverywhere?: () => void
  /** A row changed from OUTSIDE the cards — the drawer's Save, Mark as reviewed or Delete —
   *  so the queue patches or drops it in place: a card marked reviewed in the drawer stayed on
   *  the deck, and a reload of the whole queue would flash the loading state (owner report
   *  2026-09-21). `seq` makes each change a new event even for the same row. */
  rowChanged?: { txn: Txn; seq: number } | null
}

const EMPTY_SUGGESTIONS: string[] = []

function accountId(txn: Txn): string | null {
  return txn.fromAccountId ?? txn.toAccountId
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'The transaction could not be updated.'
}

export function TransactionReviewMode({
  open,
  scope,
  accountId: scopeAccountId = null,
  refreshSignal = 0,
  accounts,
  categories,
  payeeNames,
  ready = true,
  onClose,
  onApprove,
  onReject,
  onViewAll,
  onEditAll,
  onReviewEverywhere,
  rowChanged = null,
}: TransactionReviewModeProps) {
  const [queue, setQueue] = useState<Txn[]>([])
  // Everything this pass has LOADED, never shrunk by approve/skip. The chip suggestions are
  // counted from it: counting the live queue re-ranked them every time a row left, so the card
  // on screen gained or lost a chip — and changed height — while you were looking at it.
  const [loadedRows, setLoadedRows] = useState<Txn[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [skippedIds, setSkippedIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  // Tied to a row, not to "the current card": an approval is sent in the background, so a
  // failure surfaces on the row it belongs to once that row is back at the front.
  const [actionError, setActionError] = useState<{ id: string; message: string } | null>(null)
  // Reject only. Approving no longer blocks the UI — it advances first and saves after.
  const [busy, setBusy] = useState<'reject' | null>(null)
  const [rejecting, setRejecting] = useState<Txn | null>(null)
  const [rejectError, setRejectError] = useState<string | null>(null)
  // The edits made to ONE row, keyed by its id. The draft itself is derived below, in render:
  // setting it from an effect painted the next card with the LAST card's draft for a frame —
  // its payee, amount, kind and chips — before the effect corrected it (owner report 2026-09-20).
  const [edited, setEdited] = useState<{ id: string; draft: ReviewDraft } | null>(null)
  const [announcement, setAnnouncement] = useState('')
  // Rows this pass took off the deck (approved or rejected). Emptying a queue that had work in
  // it earns the confetti; opening onto an already-empty one does not.
  const [clearedCount, setClearedCount] = useState(0)
  const [inputMode, setInputMode] = useState<'keyboard' | 'pointer' | null>(null)
  // Outlives each keyed card, so the next one animates from the height the last one had.
  const cardHeight = useRef<number | null>(null)
  const lastCardHeight = useCallback(() => cardHeight.current, [])
  const rememberCardHeight = useCallback((height: number) => {
    cardHeight.current = height
  }, [])

  const visibleQueue = useMemo(
    () => queue.filter((txn) => !skippedIds.has(txn.id)),
    [queue, skippedIds],
  )
  const current = visibleQueue[0] ?? null
  const next = visibleQueue[1] ?? null
  // Per KIND, so the rear preview gets the next card's own set: handed the front card's, an
  // income behind an expense previewed expense chips that changed the moment it came to the
  // front — which read as chips loading late (owner report 2026-09-20).
  const suggestionsByKind = useMemo(() => {
    const counts = new Map<string, Map<string, number>>()
    for (const row of loadedRows) {
      if (!row.category) continue
      const forKind = counts.get(row.kind) ?? new Map<string, number>()
      forKind.set(row.category, (forKind.get(row.category) ?? 0) + 1)
      counts.set(row.kind, forKind)
    }
    const ranked = new Map<string, string[]>()
    for (const [kind, forKind] of counts) {
      ranked.set(
        kind,
        [...forKind].sort((a, b) => b[1] - a[1]).map(([id]) => id),
      )
    }
    return ranked
  }, [loadedRows])
  const suggestionsFor = useCallback(
    (kind: string | undefined) => (kind && suggestionsByKind.get(kind)) || EMPTY_SUGGESTIONS,
    [suggestionsByKind],
  )
  const suggestedCategoryIds = suggestionsFor(current?.kind)

  const loadQueue = useCallback(
    async (offset = 0, append = false) => {
      setLoading(true)
      setLoadError(null)
      try {
        const url = queueUrl(scope, scopeAccountId)
        const response = await authFetch(offset ? `${url}&offset=${offset}` : url)
        if (!response.ok) throw new Error((await response.text()) || 'Request failed')
        const body = (await response.json()) as ReviewQueueResponse
        const rows = (body.transactions ?? [])
          .map(fromApiTxn)
          .filter((txn) => txn.needsReview && !txn.deleted)
        setQueue((currentRows) => {
          if (!append) return rows
          const known = new Set(currentRows.map((row) => row.id))
          return [...currentRows, ...rows.filter((row) => !known.has(row.id))]
        })
        setLoadedRows((previous) => {
          if (!append) return rows
          const known = new Set(previous.map((row) => row.id))
          return [...previous, ...rows.filter((row) => !known.has(row.id))]
        })
        setTotal(body.total ?? rows.length)
        setHasMore(Boolean(body.has_more))
      } catch (error) {
        setLoadError(errorMessage(error))
      } finally {
        setLoading(false)
      }
    },
    [scope, scopeAccountId],
  )

  useEffect(() => {
    // Opening/refetching is the external queue synchronization boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (open) void loadQueue()
  }, [loadQueue, open, refreshSignal])

  useEffect(() => {
    // Skip is session-local: a fresh open intentionally starts a fresh pass — and so is an
    // unsaved pick. This component outlives the overlay (hosted once, app-wide), so a pick
    // carried into the next pass came back on its card as if saved, one swipe from being
    // approved (owner report 2026-09-25).
    if (!open) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSkippedIds(new Set())
    setEdited(null)
    setClearedCount(0)
  }, [open, scopeAccountId])

  // The ONE way a row's draft is read, for the front card and the preview behind it alike: the
  // preview read the saved row while the front read the pick, so a card showed one category
  // behind and another once it came forward (owner report 2026-09-25).
  const draftFor = useCallback(
    (row: Txn) => (edited?.id === row.id ? edited.draft : startingReviewDraft(row, payeeNames)),
    [edited, payeeNames],
  )
  const draft = useMemo(() => (current ? draftFor(current) : null), [current, draftFor])
  const setDraft = useCallback(
    (next: ReviewDraft) => {
      if (current) setEdited({ id: current.id, draft: next })
    },
    [current],
  )

  useEffect(() => {
    if (open && !loading && !loadError && !current && hasMore) {
      // The server cursor advances only after this visible batch is exhausted.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void loadQueue(skippedIds.size, true)
    }
  }, [current, hasMore, loadError, loadQueue, loading, open, skippedIds.size])

  const appliedChange = useRef(0)
  useEffect(() => {
    if (!rowChanged || rowChanged.seq === appliedChange.current) return
    appliedChange.current = rowChanged.seq
    const { txn } = rowChanged
    if (!txn.needsReview || txn.deleted) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setQueue((rows) => {
        if (!rows.some((row) => row.id === txn.id)) return rows
        setTotal((count) => Math.max(0, count - 1))
        setClearedCount((count) => count + 1)
        return rows.filter((row) => row.id !== txn.id)
      })
    } else {
      setQueue((rows) => rows.map((row) => (row.id === txn.id ? txn : row)))
      setLoadedRows((rows) => rows.map((row) => (row.id === txn.id ? txn : row)))
    }
    // The drawer's version of the row supersedes whatever the card had drafted for it.
    setEdited((state) => (state?.id === txn.id ? null : state))
  }, [rowChanged])

  // Functional updates throughout: several approvals can be in flight at once, so nothing may
  // read a queue captured in a closure.
  const removeCurrent = useCallback((id: string) => {
    setQueue((rows) => rows.filter((row) => row.id !== id))
    setTotal((count) => Math.max(0, count - 1))
    setClearedCount((count) => count + 1)
  }, [])

  /** A write that failed puts its row back at the FRONT, carrying the message, so the reviewer
   *  lands on the transaction that did not save instead of discovering it later. */
  const restoreFailed = useCallback((txn: Txn, message: string) => {
    setQueue((rows) => (rows.some((row) => row.id === txn.id) ? rows : [txn, ...rows]))
    setTotal((count) => count + 1)
    setClearedCount((count) => Math.max(0, count - 1))
    setActionError({ id: txn.id, message })
  }, [])

  // Optimistic: the next card is shown at once and the save runs behind it. Waiting for the
  // response made every approval as slow as the connection, for a write that almost never
  // fails — and when it does, `restoreFailed` brings the row back.
  const approve = useCallback(async () => {
    if (!current || !draft) return
    const txn = current
    const submitted = draft
    removeCurrent(txn.id)
    setActionError(null)
    setAnnouncement(`Approved. ${Math.max(0, visibleQueue.length - 1)} transactions remaining.`)
    try {
      await onApprove(txn, submitted)
    } catch (error) {
      restoreFailed(txn, errorMessage(error))
    }
  }, [current, draft, onApprove, removeCurrent, restoreFailed, visibleQueue.length])

  const skip = useCallback(() => {
    if (!current || busy) return
    setSkippedIds((ids) => new Set(ids).add(current.id))
    setAnnouncement(`Skipped. ${Math.max(0, visibleQueue.length - 1)} remaining in this pass.`)
  }, [busy, current, visibleQueue.length])

  const reject = async () => {
    if (!rejecting || busy) return
    const txn = rejecting
    setBusy('reject')
    setActionError(null)
    try {
      await onReject(txn)
      setRejecting(null)
      setRejectError(null)
      removeCurrent(txn.id)
    } catch (error) {
      setRejectError(errorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const control =
      event.target instanceof Element &&
      event.target.closest('input, select, textarea, button, a, label')
    if (control) return
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      void approve()
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      skip()
    }
  }

  const settling = loading || !ready
  const waitingElsewhere = useReviewWaitingElsewhere(
    open && onReviewEverywhere ? scopeAccountId : null,
    !settling && !loadError && (!current || !draft),
  )
  if (!open) return null
  const scopedName = scopeAccountId
    ? accounts.find((a) => a.id === scopeAccountId)?.name
    : undefined
  const skippedCount = queue.length - visibleQueue.length
  const displayTotal = hasMore ? Math.max(total, queue.length) : queue.length
  const account = current ? (accounts.find((item) => item.id === accountId(current)) ?? null) : null
  const nextPreview = next
    ? {
        txn: next,
        draft: draftFor(next),
        account: accounts.find((item) => item.id === accountId(next)) ?? null,
        suggestedCategoryIds: suggestionsFor(next.kind),
      }
    : null
  const validationError = current && draft ? validateReviewDraft(current, draft, accounts) : null
  // Laid out while loading too, but not shown: a footer that appeared WITH the first card
  // shifted the stage above it (the whole loading state, on a phone) the moment the data
  // landed — and a row of disabled actions is not something to look at while waiting. The
  // same for the subtitle line below (owner report 2026-09-21).
  const idle = settling || Boolean(busy)
  const reviewFooter =
    settling || (current && draft) ? (
      <div
        className={`acct-review-actions${settling ? ' is-settling' : ''}`}
        aria-hidden={settling || undefined}
      >
        <Button
          size="sm"
          variant="ghost"
          className="acct-review-skip"
          onClick={skip}
          disabled={idle}
        >
          <ChevronLeft size={16} />
          Skip
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="acct-review-reject"
          onClick={() => {
            setRejectError(null)
            setRejecting(current)
          }}
          disabled={idle}
        >
          <Trash2 size={15} />
          Reject
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={() => void approve()}
          disabled={idle || Boolean(validationError)}
        >
          <Check size={16} />
          Approve
        </Button>
      </div>
    ) : undefined

  return (
    <>
      <Modal
        open
        onClose={onClose}
        title="Review transactions"
        subtitle={settling ? 'Loading…' : `${displayTotal} remaining`}
        size="lg"
        className="acct-review-mode"
        overlayClassName="acct-review-overlay"
        // A tap on the scrim closes the review (owner, 2026-09-21); the phone's drag-to-dismiss
        // stays off — the cards own their gestures, and the phone layout has no scrim anyway
        // (the stage is the screen), so there the stage's own empty area is the tap target.
        closeOnBackdrop
        dragToDismiss={false}
        footer={reviewFooter}
      >
        <div
          className="acct-review-stage"
          aria-label="Transaction review keyboard shortcuts"
          tabIndex={0}
          data-autofocus
          data-input={inputMode ?? undefined}
          onPointerDownCapture={() => setInputMode('pointer')}
          onKeyDownCapture={() => setInputMode('keyboard')}
          onKeyDown={onKeyDown}
          onClick={(event) => {
            // The stage's own empty area only — never a card, a chip or the hint.
            if (event.target === event.currentTarget) onClose()
          }}
        >
          <span className="sr-only" aria-live="polite">
            {announcement}
          </span>
          {settling ? (
            <div className="acct-review-state" role="status" aria-live="polite">
              <span className="acct-review-spinner" />
              <b>Loading transactions…</b>
              <span>
                {scope === 'automation'
                  ? 'Finding transactions added by automations that still need your review.'
                  : 'Finding transactions that still need your review.'}
              </span>
            </div>
          ) : loadError ? (
            <div className="acct-review-state" role="alert">
              <b>Couldn’t load transactions for review</b>
              <span>{loadError}</span>
              <Button size="sm" variant="secondary" onClick={() => void loadQueue()}>
                Retry
              </Button>
            </div>
          ) : !current || !draft ? (
            <div className="acct-review-state acct-review-complete">
              {queue.length === 0 && clearedCount > 0 && !hasMore && <ReviewConfetti />}
              <span className="acct-review-complete-icon">
                <Check size={30} />
              </span>
              <b>{queue.length === 0 ? 'You’re all caught up' : 'That’s everything for now'}</b>
              <span>
                {queue.length === 0
                  ? scope === 'automation'
                    ? 'No automation transactions are waiting for review.'
                    : scopedName
                      ? `Nothing in ${scopedName} is waiting for review.`
                      : 'No transactions are waiting for review.'
                  : `${skippedCount} skipped transaction${skippedCount === 1 ? '' : 's'} still ${skippedCount === 1 ? 'needs' : 'need'} review.${hasMore ? ' More transactions are waiting beyond this batch.' : ''}`}
              </span>
              <div className="acct-review-complete-actions">
                {waitingElsewhere > 0 && onReviewEverywhere && (
                  <Button size="sm" variant="secondary" onClick={onReviewEverywhere}>
                    Review {waitingElsewhere} in other accounts
                  </Button>
                )}
                <Button size="sm" variant="primary" onClick={onClose}>
                  Done
                </Button>
              </div>
            </div>
          ) : (
            <TransactionReviewCard
              key={current.id}
              txn={current}
              account={account}
              accounts={accounts}
              categories={categories}
              draft={draft}
              busy={Boolean(busy)}
              actionError={actionError?.id === current.id ? actionError.message : null}
              hasMore={hasMore}
              hasNext={visibleQueue.length > 1 || hasMore}
              hasAfterNext={visibleQueue.length > 2 || hasMore}
              next={nextPreview}
              suggestedCategoryIds={suggestedCategoryIds}
              lastCardHeight={lastCardHeight}
              rememberCardHeight={rememberCardHeight}
              onDraftChange={setDraft}
              onApprove={approve}
              onSkip={skip}
              onViewAll={() => onViewAll(current)}
              onEditAll={() => onEditAll(current)}
            />
          )}
        </div>
      </Modal>

      <ConfirmDialog
        data={
          rejecting
            ? {
                title: 'Reject transaction?',
                body: (
                  <>
                    <b>
                      {(rejecting.payeeId && payeeNames[rejecting.payeeId]) ||
                        rejecting.description}
                    </b>{' '}
                    will be removed from the ledger. Its imported identity will be remembered so it
                    is not silently added again.
                    {rejectError && (
                      <span className="acct-review-reject-error" role="alert">
                        {rejectError}
                      </span>
                    )}
                  </>
                ),
                confirmLabel: 'Reject transaction',
                cancelLabel: 'Keep transaction',
                danger: true,
              }
            : null
        }
        onCancel={() => {
          setRejecting(null)
          setRejectError(null)
        }}
        onConfirm={() => void reject()}
      />
    </>
  )
}
