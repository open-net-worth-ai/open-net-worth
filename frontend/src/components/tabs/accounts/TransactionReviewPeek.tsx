import { Info, Pencil, Plus, Wallet } from '../../../ui/icons'
import { sourceLine, type ReviewDraft } from './transactionReview'
import { SourceIcon } from './pickers'
import { ReviewChoices, ReviewSummary } from './ReviewSummary'
import type { Account, Category, Txn } from './types'

/**
 * The rear card: a preview of the NEXT transaction, drawn on the same surface AND with the
 * same body as the front card — a swipe reveals a real card, not a reduced one. Hidden from
 * assistive tech (the front card is the one under review) and inert to input via CSS, so its
 * chips and the Edit affordance are decoration; the Edit is a span, never a second button.
 */
export function TransactionReviewPeek({
  txn,
  draft,
  account,
  accounts,
  categories,
  suggestedCategoryIds,
}: {
  txn: Txn
  draft: ReviewDraft
  account: Account | null
  accounts: Account[]
  categories: Category[]
  suggestedCategoryIds: string[]
}) {
  return (
    <div
      className="acct-review-card acct-review-card--rear"
      aria-hidden="true"
      // Focusable children (the category chips) stay out of the tab order — aria-hidden alone
      // hides them from the a11y tree but leaves them tabbable.
      inert
      data-testid="transaction-review-card-rear"
    >
      <div className="acct-review-facts" data-kind={draft.kind}>
        <div className="acct-review-card-head">
          <b>
            <Wallet size={16} aria-hidden="true" />
            {account?.name ?? 'Unknown account'}
          </b>
          <span className="acct-review-source">
            <SourceIcon source={txn.source} variant="mono" />
            <span className="acct-review-source-text">{sourceLine(txn)}</span>
          </span>
        </div>
        <ReviewSummary txn={txn} accounts={accounts} draft={draft} />
      </div>
      <ReviewChoices
        txn={txn}
        accounts={accounts}
        categories={categories}
        draft={draft}
        onDraftChange={() => {}}
        suggestedCategoryIds={suggestedCategoryIds}
      />
      {/* The front card's Tags and bottom row, as decoration — spans, never buttons. */}
      {draft.tags.length > 0 && (
        <div className="acct-review-tags-block">
          <span className="acct-review-category-caption">Tags</span>
          <span className="acct-review-tags">
            {draft.tags.map((tag) => (
              <span key={tag} className="acct-review-tag is-chosen">
                {tag}
              </span>
            ))}
            <span className="acct-review-tag is-add">
              <Plus size={12} aria-hidden="true" />
              Tag
            </span>
          </span>
        </div>
      )}
      <span className="acct-review-expand-row">
        <span className="acct-review-expand">
          <Info size={14} aria-hidden="true" />
          Details
        </span>
        <span className="acct-review-expand">
          <Pencil size={14} aria-hidden="true" />
          Edit
        </span>
        {draft.tags.length === 0 && (
          <span className="acct-review-expand">
            <Plus size={14} aria-hidden="true" />
            Tag
          </span>
        )}
      </span>
    </div>
  )
}
