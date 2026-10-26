/**
 * ReviewStrip.tsx — "N to review · Start review", the line under the balances on the Accounts
 * header and the All-accounts overview.
 */

import { WalletCards } from '../../../ui/icons'

export function ReviewStrip({
  count,
  onReview,
  onStartReview,
}: {
  count: number
  onReview: () => void
  onStartReview: () => void
}) {
  if (count <= 0) return null
  return (
    <span className="acct-recon-review">
      <button
        type="button"
        className="acct-recon-count as-btn"
        onClick={onReview}
        title="Show transactions that need review"
      >
        <span className="acct-recon-count-dot" aria-hidden="true" />
        {count} to review
      </button>
      <button
        type="button"
        className="acct-recon-start as-btn"
        onClick={onStartReview}
        title="Approve or skip each one in turn"
      >
        <WalletCards size={13} aria-hidden="true" />
        Start review
      </button>
    </span>
  )
}
