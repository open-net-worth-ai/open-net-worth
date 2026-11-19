import { useState } from 'react'
import { fCurrency, fDate, fTime } from '../../../../utils/format'
import { Button, ConfirmDialog, ErrorState } from '../../../ui'
import {
  bankSyncSend,
  useBankReviews,
  type BankReview,
  type BankRowView,
  type BankUndoneRow,
} from './bankSync'
import './bankSync.css'

// The bank sync's review list (ADR 2026-11-13): what the bank changed after booking, and rows it
// couldn't book without asking. Nothing here is applied until you choose.

const ACTIONS: Record<BankReview['kind'], { accept: string; dismiss: string }> = {
  modified: { accept: 'Use the bank’s version', dismiss: 'Keep mine' },
  posted: { accept: 'Use the posted amount', dismiss: 'Keep mine' },
  vanished: { accept: 'Delete it', dismiss: 'Keep it' },
  duplicate: { accept: 'Same transaction', dismiss: 'Ignore it' },
  undone: { accept: 'Book all again', dismiss: 'Forget them' },
  transfer_pair: { accept: 'Make it one transfer', dismiss: 'Keep them separate' },
}

type Decide = (item: BankReview, action: 'accept' | 'dismiss' | 'book', ref?: string) => void

// A long list shows its first few, so the sync's own settings stay in reach below it.
const SHOWN = 3

function money(v: BankRowView): string {
  if (typeof v.amount !== 'number') return ''
  const sign = v.amount > 0 ? '+' : ''
  const amount = `${sign}${fCurrency(Math.abs(v.amount), v.currency ?? '')}`
  const o = v.original_amount
  if (typeof o !== 'number' || !v.original_currency || v.original_currency === v.currency)
    return amount
  return `${amount} (${fCurrency(Math.abs(o), v.original_currency)})`
}

function row(v: BankRowView | null | undefined): string {
  if (!v) return 'gone from your book'
  return [v.date ? fDate(v.date) : '', v.description || '', money(v)].filter(Boolean).join(' · ')
}

function headline(item: BankReview): string {
  const d = item.detail
  const bank = item.provider_label || 'The bank'
  if (item.kind === 'modified') return `${bank} changed a transaction you have booked.`
  if (item.kind === 'posted') return 'A pending transaction posted at a different amount.'
  if (item.kind === 'vanished') return `${bank} no longer lists a transaction you have booked.`
  if (item.kind === 'transfer_pair')
    return 'These two look like one transfer between your accounts, such as a card payment.'
  if (d.undone) return 'You undid the sync that booked this, so it was not booked again.'
  return d.reason
    ? 'This looks like a transaction already in your book.'
    : 'Possibly already booked.'
}

export function BankSyncReviews({ provider }: { provider: string | null }) {
  const reviews = useBankReviews(provider)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [confirmAll, setConfirmAll] = useState(false)
  // Shown only when there is something to decide, as the budget apps' page does.
  if (reviews.initialLoading) return null
  if (reviews.error && !reviews.data)
    return (
      <ErrorState
        title="The review list couldn’t load"
        message={reviews.error instanceof Error ? reviews.error.message : 'Try again.'}
        onRetry={() => void reviews.refresh()}
      />
    )
  const items = reviews.data ?? []
  if (items.length === 0) return null

  const duplicates = items.filter((i) => i.kind === 'duplicate')
  const shown = showAll ? items : items.slice(0, SHOWN)

  // One at a time, as the cards' own buttons: each is checked by the server as it would be alone,
  // and the first refusal stops the rest and says how far it got.
  async function linkAll() {
    setConfirmAll(false)
    setBusy('all')
    setError(null)
    let linked = 0
    try {
      for (const item of duplicates) {
        await bankSyncSend(`/reviews/${item.id}/accept`, 'POST', { updated_at: item.updated_at })
        linked += 1
      }
    } catch (e) {
      const why = e instanceof Error ? e.message : 'That didn’t work.'
      setError(`Linked ${linked} of ${duplicates.length}. ${why}`)
    } finally {
      setBusy(null)
    }
  }

  async function decide(item: BankReview, action: 'accept' | 'dismiss' | 'book', ref?: string) {
    setBusy(`${item.id}:${action}${ref ? `:${ref}` : ''}`)
    setError(null)
    try {
      await bankSyncSend(`/reviews/${item.id}/${action}`, 'POST', {
        updated_at: item.updated_at,
        ...(ref ? { ref } : {}),
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That didn’t work.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="a3-block a3-attention" data-od-id="bank-sync-reviews">
      <div className="a3-block-h">
        Needs your review
        <span className="count">{items.length}</span>
      </div>
      <div className="a3-block-note">Nothing here changes until you choose.</div>
      {duplicates.length > 1 && (
        <div className="bs-actions">
          <Button
            size="sm"
            disabled={busy !== null}
            loading={busy === 'all'}
            onClick={() => setConfirmAll(true)}
          >
            Same transaction for all {duplicates.length}
          </Button>
        </div>
      )}
      {error && (
        <div className="a3-action-error" role="alert">
          {error}
        </div>
      )}
      <ul className="bs-reviews" aria-label="Bank changes to review">
        {shown.map((item) =>
          item.kind === 'undone' ? (
            <UndoneRun key={item.id} item={item} busy={busy} onDecide={decide} />
          ) : (
            <ReviewRow key={item.id} item={item} busy={busy} onDecide={decide} />
          ),
        )}
      </ul>
      {items.length > SHOWN && (
        <div className="bs-actions">
          <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Show fewer' : `Show all ${items.length}`}
          </Button>
        </div>
      )}
      <ConfirmDialog
        data={
          confirmAll
            ? {
                title: `Link all ${duplicates.length} to transactions already in your book?`,
                body:
                  'Each bank row is linked to the transaction it was matched to, and none is ' +
                  'booked again. Two identical charges really can be two payments: book any of ' +
                  'those separately first.',
                confirmLabel: 'Same transaction for all',
              }
            : null
        }
        onConfirm={() => void linkAll()}
        onCancel={() => setConfirmAll(false)}
      />
    </section>
  )
}

function ReviewRow({
  item,
  busy,
  onDecide,
}: {
  item: BankReview
  busy: string | null
  onDecide: Decide
}) {
  const d = item.detail
  const labels = ACTIONS[item.kind]
  const bankSide = item.kind === 'duplicate' || item.kind === 'posted' ? d.row : d.after
  return (
    <li className="bs-review">
      <div className="bs-review-head">{headline(item)}</div>
      <dl className="bs-review-rows">
        {!d.undone && (
          <>
            <dt>In your book</dt>
            <dd>{row(d.current)}</dd>
          </>
        )}
        {item.kind === 'transfer_pair' && (
          <>
            <dt>and</dt>
            <dd>{row(d.other)}</dd>
          </>
        )}
        {item.kind !== 'vanished' && item.kind !== 'transfer_pair' && (
          <>
            <dt>From the bank</dt>
            <dd>{row(bankSide)}</dd>
            {d.pair && (
              <>
                <dt>and</dt>
                <dd>{row(d.pair)}</dd>
              </>
            )}
          </>
        )}
      </dl>
      {d.user_edited && (
        <p className="bs-warn">You edited this transaction; the bank’s version would replace it.</p>
      )}
      <div className="bs-actions">
        {!d.undone && (
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null}
            loading={busy === `${item.id}:accept`}
            onClick={() => onDecide(item, 'accept')}
          >
            {labels.accept}
          </Button>
        )}
        {item.kind === 'duplicate' && (
          <Button
            size="sm"
            variant={d.undone ? 'primary' : undefined}
            disabled={busy !== null}
            loading={busy === `${item.id}:book`}
            onClick={() => onDecide(item, 'book')}
          >
            {d.undone ? 'Book again' : 'Book it separately'}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy !== null}
          loading={busy === `${item.id}:dismiss`}
          onClick={() => onDecide(item, 'dismiss')}
        >
          {d.undone ? 'Dismiss' : labels.dismiss}
        </Button>
      </div>
    </li>
  )
}

function undoneRow(e: BankUndoneRow): string {
  return e.pair ? `${row(e.row)} and ${row(e.pair)}` : row(e.row)
}

// A sync you undid: its rows held back from the next sync as ONE decision, however many there
// are — Book all again, Forget them, or open the list and book one row again.
function UndoneRun({
  item,
  busy,
  onDecide,
}: {
  item: BankReview
  busy: string | null
  onDecide: Decide
}) {
  const [open, setOpen] = useState(false)
  const rows = item.detail.rows ?? []
  const n = rows.length
  const first = rows[0]
  const one = n === 1 && first !== undefined
  const at = item.detail.run_at ? new Date(item.detail.run_at) : null
  const when = at && !isNaN(at.getTime()) ? ` from ${fDate(at)}, ${fTime(at)}` : ''
  const listId = `bs-undone-${item.id}`
  return (
    <li className="bs-review">
      <div className="bs-review-head">
        {one
          ? `You undid the sync${when}, so this wasn’t booked again.`
          : `You undid the sync${when}, so its ${n} transactions weren’t booked again.`}
      </div>
      {one ? (
        <dl className="bs-review-rows">
          <dt>From the bank</dt>
          <dd>{undoneRow(first)}</dd>
        </dl>
      ) : (
        <>
          <button
            type="button"
            className="bs-undone-toggle"
            aria-expanded={open}
            aria-controls={listId}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Hide them' : `Show the ${n} transactions`}
          </button>
          {open && (
            <ul
              className="bs-undone-rows"
              id={listId}
              aria-label="Transactions from the undone sync"
            >
              {rows.map((e) => (
                <li key={e.refs[0]} className="bs-undone-row">
                  <span>{undoneRow(e)}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy !== null}
                    loading={busy === `${item.id}:book:${e.refs[0]}`}
                    onClick={() => onDecide(item, 'book', e.refs[0])}
                  >
                    Book again
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <div className="bs-actions">
        <Button
          size="sm"
          variant="primary"
          disabled={busy !== null}
          loading={busy === `${item.id}:book`}
          onClick={() => onDecide(item, 'book')}
        >
          {one ? 'Book again' : `Book all ${n} again`}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy !== null}
          loading={busy === `${item.id}:dismiss`}
          onClick={() => onDecide(item, 'dismiss')}
        >
          {one ? 'Forget it' : 'Forget them'}
        </Button>
      </div>
    </li>
  )
}
