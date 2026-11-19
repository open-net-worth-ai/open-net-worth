import { useEffect, useRef, useState } from 'react'
import { ArrowLeftRight, Check, Info, Pencil, Plus, Wallet, X } from '../../../ui/icons'
import { sourceLine, validateReviewDraft, type ReviewDraft } from './transactionReview'
import type { Account, Category, Txn } from './types'
import type { CatKind } from './AddCategoryModal'
import { ReviewChoices, ReviewSummary } from './ReviewSummary'
import { TransactionReviewPeek } from './TransactionReviewPeek'
import { SourceIcon } from './pickers'
import { ReviewTags } from './ReviewTags'
import { useListFitsStage, useSmoothCardHeight } from './reviewCardHeight'

interface Props {
  txn: Txn
  account: Account | null
  accounts: Account[]
  categories: Category[]
  draft: ReviewDraft
  busy: boolean
  actionError: string | null
  hasMore: boolean
  hasNext: boolean
  /** A row behind the next one too: the deck keeps its edge hidden behind the rear card and
   *  slides it into the peek slot as the rear rises, so re-keying never pops an edge in. */
  hasAfterNext: boolean
  /** The row behind this one, previewed on the rear card with ITS OWN chip suggestions;
   *  null when only the cursor knows. */
  next: {
    txn: Txn
    draft: ReviewDraft
    account: Account | null
    suggestedCategoryIds: string[]
  } | null
  suggestedCategoryIds: string[]
  /** The last card's height, held by the parent across cards so the next one resizes from it. */
  lastCardHeight: () => number | null
  rememberCardHeight: (height: number) => void
  onDraftChange: (draft: ReviewDraft) => void
  onApprove: () => Promise<void>
  onSkip: () => void
  /** The full drawer, carrying this card's unsaved picks: for the rare edits the card itself
   *  does not offer (splits, receipts, the original charge). */
  onAllDetails: () => void
  /** Straight to the drawer's editor, with this card's unsaved picks filled in. */
  onEditAll: () => void
  /** "Create “name”" in the category search: makes the category (palette colour, like every
   *  other create) and resolves to it, so the card can pick it. */
  onCreateCategory?: (name: string, kind: CatKind) => Promise<Category>
}

/** Only a field you are typing in owns horizontal dragging (caret, selection), so only it blocks
 *  a swipe — plus links and native selects. Buttons do NOT: the category chips cover much of the
 *  card. And an untouched field doesn't either: the open card is mostly fields, and refusing
 *  every one of them left a phone almost nowhere to start a swipe (owner report 2026-11-19).
 *  A tap on either still reaches it — see `moved`. */
function startedOnControl(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  const field = target.closest('input, select, textarea, a')
  if (!field) return false
  return field.matches('select, a') || field === document.activeElement
}

// Commit distance: a third of the card's width, capped at 130px (about 113px on a phone-width
// card). Shorter drags snap back. The badge appears at SWIPE_BADGE_START of that distance; the
// colour wash tracks the whole distance. SWIPE_TRAVEL_MAX_PX caps how far the card follows the
// finger before release.
const SWIPE_COMMIT_FRACTION = 0.33
const SWIPE_COMMIT_MAX_PX = 130
const SWIPE_BADGE_START = 0.5
// A press only becomes a swipe past this much horizontal travel; below it, the press is still
// a tap and the chip under the finger gets its click.
const SWIPE_START_PX = 8
const SWIPE_TRAVEL_MAX_PX = 200

function swipeThreshold(card: HTMLDivElement | null): number {
  return Math.min(
    SWIPE_COMMIT_MAX_PX,
    (card?.getBoundingClientRect().width || 400) * SWIPE_COMMIT_FRACTION,
  )
}

export function TransactionReviewCard(props: Props) {
  const { txn, account, draft, busy } = props
  const [createError, setCreateError] = useState<string | null>(null)
  // The tag cloud, open. Per card — the card is keyed by row, so the next one arrives closed.
  const [tagsOpen, setTagsOpen] = useState(false)
  const showTags = tagsOpen || draft.tags.length > 0
  // The draft as of now — a category create resolves after a request, and must not write back
  // a draft from before whatever the reviewer changed meanwhile.
  const draftRef = useRef(draft)
  draftRef.current = draft
  const createCategory = props.onCreateCategory
    ? (name: string) => {
        setCreateError(null)
        const kind: CatKind = draft.kind === 'income' ? 'income' : 'expense'
        props.onCreateCategory!(name, kind)
          .then((category) => props.onDraftChange({ ...draftRef.current, category: category.id }))
          .catch((e: unknown) =>
            setCreateError(
              e instanceof Error && e.message ? e.message : 'Could not create the category.',
            ),
          )
      }
    : undefined
  const selectedAccount = props.accounts.find((item) => item.id === draft.accountId) ?? account
  const validationError = validateReviewDraft(txn, draft, props.accounts)
  const [dragX, setDragX] = useState(0)
  const [thresholdPx, setThresholdPx] = useState(96)
  const [exit, setExit] = useState<'left' | 'right' | null>(null)
  const exitTimer = useRef<number | null>(null)
  const pendingExit = useRef<'left' | 'right' | null>(null)
  const cardRef = useRef<HTMLDivElement | null>(null)
  // Fit first: ResizeObservers fire in creation order, so the list's cap is already applied
  // when the height play measures its target — one glide to the fitted height, not a grow
  // and a shrink.
  useListFitsStage(cardRef)
  useSmoothCardHeight(cardRef, props.lastCardHeight, props.rememberCardHeight)
  const drag = useRef<{
    pointerId: number
    startX: number
    startY: number
    active: boolean
    dragging: boolean
  } | null>(null)
  // Set once a press turns into a real drag, so the click it would otherwise end with (on a
  // chip, on Edit) is swallowed instead of selecting something the swipe passed over.
  const moved = useRef(false)
  useEffect(
    () => () => {
      if (exitTimer.current != null) window.clearTimeout(exitTimer.current)
    },
    [],
  )
  const finishExit = async () => {
    const direction = pendingExit.current
    if (!direction) return
    pendingExit.current = null
    if (exitTimer.current != null) window.clearTimeout(exitTimer.current)
    exitTimer.current = null
    if (direction === 'left') props.onSkip()
    else await props.onApprove()
    setExit(null)
    setDragX(0)
  }
  const commitExit = (direction: 'left' | 'right') => {
    if (busy || exit || (direction === 'right' && validationError)) return
    pendingExit.current = direction
    setExit(direction)
    setDragX(direction === 'right' ? 900 : -900)
    // A fallback for a transitionend that never comes (reduced motion, a hidden tab); the
    // flight is 360ms, so this must never cut it short.
    exitTimer.current = window.setTimeout(() => void finishExit(), 400)
  }
  const pointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (busy || exit || startedOnControl(event.target)) return
    setThresholdPx(swipeThreshold(event.currentTarget))
    moved.current = false
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      active: true,
      dragging: false,
    }
    // Capture is claimed only once this becomes a drag (below): capturing on press would
    // redirect the pointer away from a chip and swallow an ordinary tap.
  }
  const pointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (!state?.active || state.pointerId !== event.pointerId) return
    const dx = event.clientX - state.startX
    const dy = event.clientY - state.startY
    if (!state.dragging) {
      if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > SWIPE_START_PX) {
        state.active = false
        return
      }
      if (Math.abs(dx) < SWIPE_START_PX) return
      state.dragging = true
      moved.current = true
      try {
        event.currentTarget.setPointerCapture?.(event.pointerId)
      } catch {
        // Synthetic pointer events used by automation have no browser-owned active pointer.
      }
    }
    setDragX(Math.max(-SWIPE_TRAVEL_MAX_PX, Math.min(SWIPE_TRAVEL_MAX_PX, dx)))
  }
  const pointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    drag.current = null
    if (!state?.active || !state.dragging || state.pointerId !== event.pointerId) return setDragX(0)
    const threshold = swipeThreshold(cardRef.current)
    const dx = event.clientX - state.startX
    if (dx >= threshold) commitExit('right')
    else if (dx <= -threshold) commitExit('left')
    else setDragX(0)
  }
  // The badge waits until the drag is halfway to the commit distance, then fades in toward it.
  // A right drag that cannot commit (validation) names nothing rather than promising Approve.
  const badgeStart = thresholdPx * SWIPE_BADGE_START
  const underway = Math.abs(dragX) >= badgeStart
  const swipeIntent =
    exit ?? (!underway ? null : dragX > 0 && !validationError ? 'right' : dragX < 0 ? 'left' : null)
  const swipeProgress = exit
    ? 1
    : Math.min(1, Math.max(0, (Math.abs(dragX) - badgeStart) / (thresholdPx - badgeStart)))
  // The colour wash starts with the very first pixel of drag and deepens toward the commit point.
  const washIntent = exit ?? (dragX > 0 && !validationError ? 'right' : dragX < 0 ? 'left' : null)
  const washOpacity = exit ? 1 : Math.min(1, Math.abs(dragX) / thresholdPx)
  return (
    <>
      <div
        className={`acct-review-deck${props.hasNext ? ' has-next' : ''}${dragX || exit ? ' is-peeking' : ''}${exit ? ' is-advancing' : ''}`}
      >
        {props.hasNext && props.hasAfterNext && (
          <div
            className="acct-review-card acct-review-card--rear acct-review-card--deep"
            aria-hidden="true"
            inert
            data-testid="transaction-review-card-deep"
          />
        )}
        {props.hasNext &&
          (props.next ? (
            <TransactionReviewPeek
              txn={props.next.txn}
              draft={props.next.draft}
              account={props.next.account}
              accounts={props.accounts}
              categories={props.categories}
              suggestedCategoryIds={props.next.suggestedCategoryIds}
            />
          ) : (
            <div
              className="acct-review-card acct-review-card--rear"
              aria-hidden="true"
              inert
              data-testid="transaction-review-card-rear"
            />
          ))}
        <div
          ref={cardRef}
          className={`acct-review-card${dragX && !exit ? ' is-dragging' : ''}${exit === 'left' ? ' is-exiting-left' : exit === 'right' ? ' is-exiting-right' : ''}`}
          data-testid="transaction-review-card"
          data-transaction-id={txn.id}
          style={
            {
              '--review-drag-x': `${dragX}px`,
              '--review-drag-rotation': `${dragX * 0.018}deg`,
            } as React.CSSProperties
          }
          onClickCapture={(event) => {
            if (!moved.current) return
            moved.current = false
            event.preventDefault()
            event.stopPropagation()
          }}
          onPointerDown={pointerDown}
          onPointerMove={pointerMove}
          onPointerUp={pointerUp}
          onPointerCancel={() => {
            drag.current = null
            setDragX(0)
          }}
          onTransitionEnd={(event) => {
            if (exit && event.propertyName === 'transform') void finishExit()
          }}
        >
          {washIntent && (
            <div
              className={`acct-review-swipe-wash ${washIntent === 'right' ? 'is-approve' : 'is-skip'}`}
              data-testid="review-swipe-wash"
              aria-hidden="true"
              style={{ opacity: 0.88 * washOpacity }}
            />
          )}
          {swipeIntent && (
            <div
              className={`acct-review-swipe-badge ${swipeIntent === 'right' ? 'is-approve' : 'is-skip'}`}
              data-testid="review-swipe-badge"
              aria-hidden="true"
              style={{ opacity: 0.6 + 0.4 * swipeProgress }}
            >
              <span>
                {swipeIntent === 'right' ? (
                  <Check size={26} strokeWidth={2.5} />
                ) : (
                  <X size={26} strokeWidth={2.5} />
                )}
              </span>
              <b>{swipeIntent === 'right' ? 'Approve' : 'Skip for now'}</b>
            </div>
          )}
          {/* Two grounds, no rule between them: the facts on a panel tinted by kind, the one
              decision (the category) on the plain surface it rests on — YNAB's card language. */}
          <div
            className="acct-review-facts"
            data-kind={draft.kind}
            // A tap on the facts opens the full transaction — the card itself edits only the
            // category and tags; payee, date, memo and the rest are the drawer's (owner,
            // 2026-11-19). Controls inside keep their own taps.
            onClick={(event) => {
              const target = event.target as Element
              if (target.closest('button, input, select, textarea, a, label')) return
              props.onAllDetails()
            }}
          >
            <div className="acct-review-card-head">
              <b>
                <Wallet size={16} aria-hidden="true" />
                {selectedAccount?.name ?? 'Unknown account'}
              </b>
              <span
                className="acct-review-source"
                title={
                  txn.via
                    ? `Added by the automation "${txn.via}". This does not mean it has been reviewed or cleared at your bank.`
                    : 'How this transaction was added. This does not mean it has been reviewed or cleared at your bank.'
                }
              >
                <SourceIcon source={txn.source} variant="mono" />
                <span className="acct-review-source-text">{sourceLine(txn)}</span>
              </span>
            </div>
            <ReviewSummary txn={txn} accounts={props.accounts} draft={draft} />
          </div>
          <ReviewChoices
            txn={txn}
            accounts={props.accounts}
            categories={props.categories}
            draft={draft}
            onDraftChange={props.onDraftChange}
            suggestedCategoryIds={props.suggestedCategoryIds}
            onCreateCategory={createCategory}
          />
          {/* The card's other decision: tags, tapped from your own — no typing needed. Only
              once there are some or the cloud is open; otherwise "+ Tag" waits in the row below. */}
          {showTags && (
            <ReviewTags
              tags={draft.tags}
              onChange={(tags) => props.onDraftChange({ ...draft, tags })}
              open={tagsOpen}
              onOpenChange={setTagsOpen}
            />
          )}
          {/* Everything else is the drawer's. Down here, in the thumb's reach; both carry the
              card's unsaved category and tags into it. */}
          <div className="acct-review-expand-row">
            <button type="button" className="acct-review-expand" onClick={props.onAllDetails}>
              <Info size={14} aria-hidden="true" />
              Details
            </button>
            <button type="button" className="acct-review-expand" onClick={props.onEditAll}>
              <Pencil size={14} aria-hidden="true" />
              Edit
            </button>
            {!showTags && (
              <button
                type="button"
                className="acct-review-expand"
                aria-expanded="false"
                onClick={() => setTagsOpen(true)}
              >
                <Plus size={14} aria-hidden="true" />
                Tag
              </button>
            )}
          </div>
          {createError && <p className="acct-review-validation">{createError}</p>}
          {validationError && <p className="acct-review-validation">{validationError}</p>}
        </div>
      </div>
      <p className="acct-review-swipe-hint" aria-hidden="true">
        <ArrowLeftRight size={14} />
        Left to skip · Right to approve
      </p>
      {props.actionError && (
        <div className="acct-review-action-error" role="alert">
          <span>{props.actionError}</span>
          <button
            type="button"
            disabled={!!validationError}
            onClick={() => {
              if (!validationError) void props.onApprove()
            }}
          >
            Retry approval
          </button>
        </div>
      )}
      {props.hasMore && (
        <p className="acct-review-bound">Showing the newest 500. More still need review.</p>
      )}
    </>
  )
}
