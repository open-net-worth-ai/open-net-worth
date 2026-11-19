import { useEffect, useRef, useState, type ComponentType } from 'react'
import { Check, Plus, Search, Tags, X } from '../../../ui/icons'
import { TextField } from '../../ui'
import { useIsCoarsePointer } from '../../../hooks/useIsCoarsePointer'
import type { Category } from './types'

export interface ReviewChoiceOption {
  id: string
  label: string
  color?: string
}

/**
 * A row of the likeliest choices as chips, then "Other" — the shape of every decision a card
 * asks for. Categories were the first; a transfer's destination account is the second, so the
 * chooser is generic and each caller names its caption, icon and search copy. `none` adds a
 * leading "nothing chosen" chip (Uncategorized) for a choice that may be empty; a transfer's
 * destination never is, so it passes none.
 *
 * "Other" turns the row INTO the picker, in place: the caption becomes the search field and
 * the chips give way to one list, ordered as the chips were (chosen, then likeliest, then
 * A–Z), so the likely ones are its top and not a second copy above it — two answers to the
 * same question on one card read as redundant (owner, 2026-09-20). It also hands the chips'
 * rows to the list, which is what lets a long list fit a phone.
 */
export function ReviewChipChooser({
  caption,
  options,
  selected,
  suggestedIds,
  onChange,
  icon: Icon,
  none,
  searchLabel,
  placeholder,
  resultsTestId = 'review-choice-results',
  limit = 4,
  onCreate,
}: {
  caption: string
  options: ReviewChoiceOption[]
  selected: string | null
  suggestedIds: string[]
  onChange: (id: string | null) => void
  icon: ComponentType<{ size?: number; 'aria-hidden'?: boolean | 'true' }>
  none?: string
  searchLabel: string
  placeholder: string
  resultsTestId?: string
  /** Chips before "Other". Four category names fit two rows on a phone; account names are
   *  longer, so a transfer offers three. */
  limit?: number
  /** Offered as "Create “name”" when the search matches no option by name. */
  onCreate?: (name: string) => void
}) {
  const [searching, setSearching] = useState(false)
  const [query, setQuery] = useState('')
  // On a touch screen, focusing the field on open throws the on-screen keyboard over the
  // chips the search was opened to browse. The keyboard belongs to a tap on the field itself.
  const coarsePointer = useIsCoarsePointer()
  const resultsRef = useRef<HTMLDivElement>(null)
  // Only a list that actually overflows gets the fade and the scroll-end clearance. CSS cannot
  // ask whether an element scrolls, and an unconditional padding shows as dead space under a
  // short list — which is exactly what it looked like.
  const [edges, setEdges] = useState({ top: false, bottom: false })
  // The card is height-capped, so the results open below its fold. Bring them into view on
  // open and on every keystroke — typing and seeing nothing reads as a broken search.
  useEffect(() => {
    if (searching) resultsRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [searching, query])
  useEffect(() => {
    const el = resultsRef.current
    if (!el) return
    const measure = () => {
      const scrollable = el.scrollHeight > el.clientHeight + 1
      setEdges({
        top: scrollable && el.scrollTop > 2,
        bottom: scrollable && el.scrollTop + el.clientHeight < el.scrollHeight - 2,
      })
    }
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => {
      el.removeEventListener('scroll', measure)
      observer.disconnect()
    }
  }, [searching, query])
  const close = () => {
    setSearching(false)
    setQuery('')
  }
  const pick = (id: string | null) => {
    onChange(id)
    close()
  }
  const likely = [...new Set([selected, ...suggestedIds])].filter((id): id is string => !!id)
  const choices = likely
    .slice(0, limit)
    .map((id) => options.find((option) => option.id === id))
    .filter((option): option is ReviewChoiceOption => !!option)
  const rank = new Map(likely.map((id, index) => [id, index]))
  const listed = [...options]
    .filter((option) => option.label.toLowerCase().includes(query.trim().toLowerCase()))
    .sort(
      (a, b) =>
        (rank.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.id) ?? Number.MAX_SAFE_INTEGER) ||
        a.label.localeCompare(b.label),
    )
  const typed = query.trim()
  const canCreate =
    !!onCreate &&
    !!typed &&
    !options.some((option) => option.label.toLowerCase() === typed.toLowerCase())
  if (searching) {
    return (
      <div className="acct-review-category-choices">
        <div className="acct-review-category-search">
          <div className="acct-review-category-search-row">
            <TextField
              aria-label={searchLabel}
              placeholder={placeholder}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') close()
              }}
              autoFocus={!coarsePointer}
            />
            <button
              type="button"
              className="acct-review-category-search-close"
              aria-label="Close search"
              onClick={close}
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
          <div
            ref={resultsRef}
            className="acct-review-category-results"
            data-fade-top={edges.top || undefined}
            data-fade-bottom={edges.bottom || undefined}
            data-testid={resultsTestId}
          >
            {listed.map((option) => (
              <button
                key={option.id}
                type="button"
                className="acct-review-choice"
                aria-pressed={selected === option.id}
                style={{ '--category-color': option.color } as React.CSSProperties}
                onClick={() => pick(option.id)}
              >
                {selected === option.id ? (
                  <Check size={14} aria-hidden="true" />
                ) : (
                  <Icon size={14} aria-hidden="true" />
                )}
                {option.label}
              </button>
            ))}
            {canCreate && (
              <button
                type="button"
                className="acct-review-choice is-create"
                onClick={() => {
                  onCreate?.(typed)
                  close()
                }}
              >
                <Plus size={14} aria-hidden="true" />
                Create “{typed}”
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="acct-review-category-choices">
      <span className="acct-review-category-caption">{caption}</span>
      <div className="acct-review-category-chips">
        {!selected && none && (
          <button
            type="button"
            className="acct-review-choice"
            aria-pressed="true"
            onClick={() => onChange(null)}
          >
            <Check size={14} />
            {none}
          </button>
        )}
        {choices.map((option) => (
          <button
            key={option.id}
            type="button"
            className="acct-review-choice"
            aria-pressed={selected === option.id}
            style={{ '--category-color': option.color } as React.CSSProperties}
            onClick={() => onChange(option.id)}
          >
            {selected === option.id ? (
              <Check size={14} aria-hidden="true" />
            ) : (
              <Icon size={14} aria-hidden="true" />
            )}
            {option.label}
          </button>
        ))}
        <button type="button" className="acct-review-choice" onClick={() => setSearching(true)}>
          <Search size={14} aria-hidden="true" />
          Other
        </button>
      </div>
    </div>
  )
}

export function ReviewCategoryChoices({
  categories,
  selected,
  suggestedIds,
  onChange,
  onCreate,
}: {
  categories: Category[]
  selected: string | null
  suggestedIds: string[]
  onChange: (id: string | null) => void
  onCreate?: (name: string) => void
}) {
  return (
    <ReviewChipChooser
      caption="Choose a category"
      options={categories}
      selected={selected}
      suggestedIds={suggestedIds}
      onChange={onChange}
      icon={Tags}
      none="Uncategorized"
      searchLabel="Search categories"
      placeholder="Find a category…"
      resultsTestId="review-category-results"
      onCreate={onCreate}
    />
  )
}
