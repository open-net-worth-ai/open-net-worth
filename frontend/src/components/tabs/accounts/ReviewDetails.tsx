/**
 * ReviewDetails.tsx — the review card, tapped open: the fields people fix while reviewing, edited
 * in place and saved with the card's Approve (they are the same draft as the category). Reached
 * with the thumb: the card was only editable through Details/Edit in its top corner, which a
 * one-handed phone user could not reach (Discord feedback, 2026-11-19). Rarer edits — splits,
 * receipts, the original charge — stay in the drawer, one tap away through "All details".
 */

import { useMemo, useState } from 'react'
import { ChevronUp, Plus, X } from '../../../ui/icons'
import { slugTag } from './categories'
import { AddPayeeField, type PayeeOption } from './pickers'
import { tagItems, useTransactionTags } from './tagOptions'
import type { ReviewDraft } from './transactionReview'
import type { Txn } from './types'

/** How many of your tags the cloud offers before you search. */
const CLOUD_LIMIT = 10

/** Kinds whose rows carry a payee — a transfer, adjustment or investment never has one. */
const HAS_PAYEE = new Set(['expense', 'income'])

export function ReviewDetails({
  txn,
  draft,
  onDraftChange,
  payeeOptions,
  onAllDetails,
  onCollapse,
}: {
  txn: Txn
  draft: ReviewDraft
  onDraftChange: (draft: ReviewDraft) => void
  payeeOptions: PayeeOption[]
  onAllDetails: () => void
  onCollapse: () => void
}) {
  const set = (patch: Partial<ReviewDraft>) => onDraftChange({ ...draft, ...patch })
  return (
    <div className="acct-review-details" data-testid="review-details">
      {HAS_PAYEE.has(draft.kind) && (
        <div className="acct-review-detail-row">
          <span className="acct-review-detail-label">Payee</span>
          <div className="acct-review-detail-value">
            <AddPayeeField
              value={draft.payee}
              options={payeeOptions}
              accounts={[]}
              fromAccountId={null}
              onSelectTransfer={() => {}}
              advance={() => {}}
              onChange={(payee, defaultCategory) =>
                set({
                  payee,
                  payeeEdited: true,
                  // A payee's default category fills an empty pick, never overrides one.
                  ...(defaultCategory && !draft.category ? { category: defaultCategory } : {}),
                })
              }
            />
          </div>
        </div>
      )}
      <label className="acct-review-detail-row">
        <span className="acct-review-detail-label">Date</span>
        <input
          className="acct-review-detail-input"
          type="date"
          value={draft.date.slice(0, 10)}
          onChange={(e) => e.target.value && set({ date: e.target.value })}
        />
      </label>
      <label className="acct-review-detail-row">
        <span className="acct-review-detail-label">Memo</span>
        <input
          className="acct-review-detail-input"
          value={draft.memo}
          placeholder="Add a memo"
          onChange={(e) => set({ memo: e.target.value })}
        />
      </label>
      <ReviewTags tags={draft.tags} onChange={(tags) => set({ tags })} />
      {txn.description && (
        <div className="acct-review-detail-row">
          <span className="acct-review-detail-label">Bank text</span>
          <span className="acct-review-detail-static" title={txn.description}>
            {txn.description}
          </span>
        </div>
      )}
      <div className="acct-review-detail-foot">
        <button type="button" className="acct-review-detail-link" onClick={onAllDetails}>
          All details
        </button>
        <button
          type="button"
          className="acct-review-detail-less"
          aria-expanded="true"
          onClick={onCollapse}
        >
          <ChevronUp size={14} aria-hidden="true" />
          Less
        </button>
      </div>
    </div>
  )
}

/** The Tags row: the chosen tags, and — tapped open — a cloud of your tags to tap, most used
 *  first, with a search that narrows it or creates a new one. One-handed: the common case is a
 *  tap, never typing. */
export function ReviewTags({
  tags,
  onChange,
}: {
  tags: string[]
  onChange: (tags: string[]) => void
}) {
  const known = useTransactionTags()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const items = useMemo(() => tagItems(known, tags, query), [known, tags, query])
  const cloud = items.filter((it) => it.type === 'tag').slice(0, CLOUD_LIMIT)
  const create = items.find((it) => it.type === 'create')
  const add = (tag: string) => {
    const t = slugTag(tag)
    if (t && !tags.includes(t)) onChange([...tags, t])
    setQuery('')
  }
  const remove = (tag: string) => onChange(tags.filter((t) => t !== tag))

  const chosen = tags.map((tag) => (
    <button
      key={tag}
      type="button"
      className="acct-review-tag is-chosen"
      aria-label={`Remove tag ${tag}`}
      onClick={() => remove(tag)}
    >
      {tag}
      <X size={12} aria-hidden="true" />
    </button>
  ))

  if (!open) {
    return (
      <div className="acct-review-detail-row">
        <span className="acct-review-detail-label">Tags</span>
        <div className="acct-review-tags">
          {chosen}
          <button
            type="button"
            className="acct-review-tag is-add"
            aria-expanded="false"
            onClick={() => setOpen(true)}
          >
            <Plus size={12} aria-hidden="true" />
            Tag
          </button>
        </div>
      </div>
    )
  }
  return (
    <div className="acct-review-detail-row is-open" data-testid="review-tags-editor">
      <div className="acct-review-tags-head">
        <span className="acct-review-detail-label">Tags</span>
        <button
          type="button"
          className="acct-review-detail-link"
          aria-expanded="true"
          onClick={() => {
            if (query.trim()) add(query)
            setOpen(false)
          }}
        >
          Done
        </button>
      </div>
      {tags.length > 0 && <div className="acct-review-tags">{chosen}</div>}
      <input
        className="acct-review-detail-input"
        aria-label="Find or create a tag"
        placeholder="Find or create a tag"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            const first = cloud[0]?.tag ?? create?.tag
            if (first) add(first)
          } else if (e.key === 'Escape') {
            e.stopPropagation()
            setOpen(false)
          }
        }}
      />
      <div className="acct-review-tags" aria-label="Your tags" role="group">
        {cloud.map((it) => (
          <button
            key={it.tag}
            type="button"
            className="acct-review-tag"
            onClick={() => add(it.tag)}
          >
            {it.tag}
          </button>
        ))}
        {create && (
          <button
            type="button"
            className="acct-review-tag is-create"
            onClick={() => add(create.tag)}
          >
            <Plus size={12} aria-hidden="true" />
            Create “{create.tag}”
          </button>
        )}
        {!cloud.length && !create && (
          <span className="acct-review-detail-hint">
            {known.length ? 'No other tags match' : 'Type a name to create your first tag'}
          </span>
        )}
      </div>
    </div>
  )
}
