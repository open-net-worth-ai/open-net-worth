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
import type { PayeeOption } from './pickers'
import { tagItems, useTransactionTags } from './tagOptions'
import type { ReviewDraft } from './transactionReview'
import type { Txn } from './types'

/** How many of your tags the cloud offers before you search. */
const CLOUD_LIMIT = 10
/** How many payees the list under the Payee field offers while you type. */
const PAYEE_LIMIT = 5

/** Kinds whose rows carry a payee — a transfer, adjustment or investment never has one. */
export const HAS_PAYEE = new Set(['expense', 'income'])

/** The draft after a payee pick or a typed name. A payee's default category fills an empty
 *  category, never overrides one. */
export function withPayee(
  draft: ReviewDraft,
  payee: string,
  defaultCategory?: string | null,
): ReviewDraft {
  return {
    ...draft,
    payee,
    payeeEdited: true,
    ...(defaultCategory && !draft.category ? { category: defaultCategory } : {}),
  }
}

export function ReviewDetails({
  txn,
  draft,
  onDraftChange,
  onAllDetails,
  onCollapse,
}: {
  txn: Txn
  draft: ReviewDraft
  onDraftChange: (draft: ReviewDraft) => void
  onAllDetails: () => void
  onCollapse: () => void
}) {
  const set = (patch: Partial<ReviewDraft>) => onDraftChange({ ...draft, ...patch })
  return (
    <div className="acct-review-details" data-testid="review-details">
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

/** Payees whose name contains `query`, names starting with it first — the list under the field.
 *  Exported for tests. */
export function payeeMatches(options: PayeeOption[], query: string): PayeeOption[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  return options
    .filter((o) => o.label.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        Number(!a.label.toLowerCase().startsWith(q)) -
          Number(!b.label.toLowerCase().startsWith(q)) || a.label.localeCompare(b.label),
    )
    .slice(0, PAYEE_LIMIT)
}

/** The open card's heading, editable: the payee IS the card's title, so it is edited there
 *  rather than repeated in a "Payee" row below it (owner, 2026-11-19). Suggestions are a list in
 *  the card under it, not a floating popover: the card moves with the swipe, and a popover
 *  anchored inside it was clipped — typing showed nothing to pick. A name that matches no payee
 *  is created when the card is approved. */
export function ReviewPayeeTitle({
  value,
  options,
  onChange,
}: {
  value: string
  options: PayeeOption[]
  onChange: (payee: string, defaultCategory?: string | null) => void
}) {
  const [typing, setTyping] = useState(false)
  const matches = typing ? payeeMatches(options, value) : []
  const exact = options.some((o) => o.label.toLowerCase() === value.trim().toLowerCase())
  const showCreate = typing && !!value.trim() && !exact
  const pick = (o: PayeeOption) => {
    onChange(o.label, o.defaultCategory)
    setTyping(false)
  }
  return (
    <>
      <h3 className="acct-review-title-edit">
        <input
          className="acct-review-title-input"
          aria-label="Payee"
          placeholder="No payee yet"
          value={value}
          onChange={(e) => {
            setTyping(true)
            onChange(e.target.value)
          }}
          onBlur={() => setTyping(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches[0]) {
              e.preventDefault()
              pick(matches[0])
            } else if (e.key === 'Escape' && typing) {
              e.stopPropagation()
              setTyping(false)
            }
          }}
        />
      </h3>
      {(matches.length > 0 || showCreate) && (
        <div className="acct-review-tags acct-review-payee-list" role="listbox" aria-label="Payees">
          {matches.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected="false"
              className="acct-review-tag"
              // mousedown, not click: the input's blur (which closes this list) fires first.
              onMouseDown={(e) => {
                e.preventDefault()
                pick(o)
              }}
            >
              {o.label}
            </button>
          ))}
          {showCreate && (
            <span className="acct-review-detail-hint">
              New payee “{value.trim()}”, created when you approve
            </span>
          )}
        </div>
      )}
    </>
  )
}
