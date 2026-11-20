/**
 * ReviewTags.tsx — the review card's Tags: the chosen tags, and a cloud of your tags to tap (most
 * used first) with a search that narrows it or creates a new one. With the category, the only
 * thing the card itself edits; the rest is the drawer's (owner, 2026-11-19). One-handed: the
 * common case is a tap, never typing. Saved with the card's Approve.
 */

import { useMemo, useState } from 'react'
import { Plus, X } from '../../../ui/icons'
import { TextField } from '../../ui'
import { useIsCoarsePointer } from '../../../hooks/useIsCoarsePointer'
import { slugTag } from './categories'
import { tagItems, useTransactionTags } from './tagOptions'

/** How many of your tags the cloud offers before you search. */
const CLOUD_LIMIT = 10

/** Shown only once the row has tags, or the cloud is open: not everyone tags, so an untagged
 *  card offers just "+ Tag" in its bottom row (owner, 2026-11-19). `open` is the card's. */
export function ReviewTags({
  tags,
  onChange,
  open,
  onOpenChange,
}: {
  tags: string[]
  onChange: (tags: string[]) => void
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const setOpen = onOpenChange
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
      <div className="acct-review-tags-block">
        <span className="acct-review-category-caption">Tags</span>
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
  return <TagCloud tags={tags} chosen={chosen} onChange={onChange} onDone={() => setOpen(false)} />
}

/** The open Tags editor. Your tag list is already loaded: the review fetches it when it opens. */
function TagCloud({
  tags,
  chosen,
  onChange,
  onDone,
}: {
  tags: string[]
  chosen: React.ReactNode[]
  onChange: (tags: string[]) => void
  onDone: () => void
}) {
  const known = useTransactionTags()
  const coarsePointer = useIsCoarsePointer()
  const [query, setQuery] = useState('')
  const items = useMemo(() => tagItems(known, tags, query), [known, tags, query])
  const cloud = items.filter((it) => it.type === 'tag').slice(0, CLOUD_LIMIT)
  const create = items.find((it) => it.type === 'create')
  const add = (tag: string) => {
    const t = slugTag(tag)
    if (t && !tags.includes(t)) onChange([...tags, t])
    setQuery('')
  }
  return (
    <div className="acct-review-tags-block is-open" data-testid="review-tags-editor">
      {/* The category picker's own search row — field, then a round ✕ — so the two searches
          on the card read as one control (owner, 2026-11-20). The ✕ keeps a typed name, as
          the Done it replaces did: closing never throws a tag away. */}
      <div className="acct-review-category-search-row">
        <TextField
          className="acct-review-tags-search"
          aria-label="Find or create a tag"
          placeholder="Find or create a tag…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              const first = cloud[0]?.tag ?? create?.tag
              if (first) add(first)
            } else if (e.key === 'Escape') {
              e.stopPropagation()
              onDone()
            }
          }}
          autoFocus={!coarsePointer}
        />
        <button
          type="button"
          className="acct-review-category-search-close"
          aria-label="Done"
          aria-expanded="true"
          onClick={() => {
            if (query.trim()) add(query)
            onDone()
          }}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      {tags.length > 0 && <div className="acct-review-tags">{chosen}</div>}
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
          <span className="acct-review-tags-hint">
            {known.length ? 'No other tags match' : 'Type a name to create your first tag'}
          </span>
        )}
      </div>
    </div>
  )
}
