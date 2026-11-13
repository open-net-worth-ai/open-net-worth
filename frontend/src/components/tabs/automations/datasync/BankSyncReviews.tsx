import { useState } from 'react'
import { fAmountNative, fDate } from '../../../../utils/format'
import { Button, EmptyState, ErrorState, Loading } from '../../../ui'
import { bankSyncSend, useBankReviews, type BankReview, type BankRowView } from './bankSync'
import './bankSync.css'

// The bank sync's review list (ADR 2026-11-13): what the bank changed after booking, and rows it
// couldn't book without asking. Nothing here is applied until you choose.

const ACTIONS: Record<BankReview['kind'], { accept: string; dismiss: string }> = {
  modified: { accept: 'Use the bank’s version', dismiss: 'Keep mine' },
  posted: { accept: 'Use the posted amount', dismiss: 'Keep mine' },
  vanished: { accept: 'Delete it', dismiss: 'Keep it' },
  duplicate: { accept: 'Same transaction', dismiss: 'Ignore it' },
}

function row(v: BankRowView | null | undefined): string {
  if (!v) return 'gone from your book'
  const amount = typeof v.amount === 'number' ? fAmountNative(Math.abs(v.amount), v.currency) : ''
  const sign = typeof v.amount === 'number' && v.amount > 0 ? '+' : ''
  return [v.date ? fDate(v.date) : '', v.description || '', `${sign}${amount}`]
    .filter(Boolean)
    .join(' · ')
}

function headline(item: BankReview): string {
  const d = item.detail
  const bank = item.provider_label || 'The bank'
  if (item.kind === 'modified') return `${bank} changed a transaction you have booked.`
  if (item.kind === 'posted') return 'A pending transaction posted at a different amount.'
  if (item.kind === 'vanished') return `${bank} no longer lists a transaction you have booked.`
  return d.reason
    ? 'This looks like a transaction already in your book.'
    : 'Possibly already booked.'
}

export function BankSyncReviews({ provider }: { provider: string | null }) {
  const reviews = useBankReviews(provider)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (reviews.initialLoading) return <Loading label="Loading the review list…" />
  if (reviews.error && !reviews.data)
    return (
      <ErrorState
        title="The review list couldn’t load"
        message={reviews.error instanceof Error ? reviews.error.message : 'Try again.'}
        onRetry={() => void reviews.refresh()}
      />
    )
  const items = reviews.data ?? []

  async function decide(item: BankReview, action: 'accept' | 'dismiss' | 'book') {
    setBusy(`${item.id}:${action}`)
    setError(null)
    try {
      await bankSyncSend(`/reviews/${item.id}/${action}`, 'POST')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn’t work.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="a3-block" data-od-id="bank-sync-reviews">
      <div className="a3-block-h">
        To review
        <span className="count">{items.length}</span>
      </div>
      {error && (
        <div className="a3-action-error" role="alert">
          {error}
        </div>
      )}
      {items.length === 0 ? (
        <EmptyState
          title="Nothing to review"
          body="If the bank changes or withdraws something already booked, it waits here."
        />
      ) : (
        <ul className="bs-reviews" aria-label="Bank changes to review">
          {items.map((item) => (
            <ReviewRow key={item.id} item={item} busy={busy} onDecide={decide} />
          ))}
        </ul>
      )}
    </div>
  )
}

function ReviewRow({
  item,
  busy,
  onDecide,
}: {
  item: BankReview
  busy: string | null
  onDecide: (item: BankReview, action: 'accept' | 'dismiss' | 'book') => void
}) {
  const d = item.detail
  const labels = ACTIONS[item.kind]
  const bankSide = item.kind === 'duplicate' || item.kind === 'posted' ? d.row : d.after
  return (
    <li className="bs-review">
      <div className="bs-review-head">{headline(item)}</div>
      <dl className="bs-review-rows">
        <dt>In your book</dt>
        <dd>{row(d.current)}</dd>
        {item.kind !== 'vanished' && (
          <>
            <dt>From the bank</dt>
            <dd>{row(bankSide)}</dd>
          </>
        )}
      </dl>
      {d.user_edited && (
        <p className="bs-warn">You edited this transaction; the bank’s version would replace it.</p>
      )}
      <div className="bs-actions">
        <Button
          size="sm"
          variant="primary"
          disabled={busy !== null}
          loading={busy === `${item.id}:accept`}
          onClick={() => onDecide(item, 'accept')}
        >
          {labels.accept}
        </Button>
        {item.kind === 'duplicate' && (
          <Button
            size="sm"
            disabled={busy !== null}
            loading={busy === `${item.id}:book`}
            onClick={() => onDecide(item, 'book')}
          >
            Book it separately
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy !== null}
          loading={busy === `${item.id}:dismiss`}
          onClick={() => onDecide(item, 'dismiss')}
        >
          {labels.dismiss}
        </Button>
      </div>
    </li>
  )
}
