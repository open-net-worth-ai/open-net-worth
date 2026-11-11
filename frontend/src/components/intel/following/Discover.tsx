import { useState } from 'react'
import { ErrorState } from '../../ui'
import { FilterChips, SearchBox, ToolSelect } from './Controls'
import type { DirectoryEntry } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { PortfolioCard } from './PortfolioCard'
import { useDirectory } from './useFollowing'

export type PortfolioRef = Pick<DirectoryEntry, 'kind' | 'source_ref' | 'name' | 'subtitle'>

interface Props {
  /** The portfolios the owner watches, shown first and as their own filter. */
  watched: PortfolioRef[]
  onOpen: (ref: PortfolioRef) => void
}

// Filters by what a portfolio IS (its source), never by an investing style we would have to guess.
const TYPES = { Funds: 'fund_13f', Politicians: 'house_member', ETFs: 'etf_daily' } as const
type Filter = 'All' | 'Watching' | keyof typeof TYPES
const SORTS = [
  { value: 'featured', label: 'Featured' },
  { value: 'return', label: '1Y return' },
  { value: 'latest', label: 'Latest activity' },
] as const
type Sort = (typeof SORTS)[number]['value']
const isSort = (v: string): v is Sort => SORTS.some((s) => s.value === v)

/** Highest first; a portfolio not priced yet sorts last rather than as zero. */
function bySort(sort: Sort) {
  const key = (e: DirectoryEntry): number | string | null =>
    sort === 'return' ? (e.return_12m ?? null) : (e.latest_at ?? null)
  return (a: DirectoryEntry, b: DirectoryEntry) => {
    const x = key(a)
    const y = key(b)
    if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1
    return x > y ? -1 : x < y ? 1 : 0
  }
}

const keyOf = (e: PortfolioRef) => `${e.kind}:${e.source_ref}`

/** Famous portfolios, as cards to browse. Search sits in place; there is no dialog. */
export function Discover({ watched, onOpen }: Props) {
  const [filter, setFilter] = useState<Filter>('All')
  const [typed, setTyped] = useState('')
  const [sort, setSort] = useState<Sort>('featured')
  const directory = useDirectory(typed)
  const searching = directory.q.length >= 2
  const listed = directory.data ?? []
  const watchedKeys = new Set(watched.map(keyOf))
  const extraWatched = watched
    .filter((w) => !listed.some((e) => keyOf(e) === keyOf(w)))
    .map((w) => ({ ...w, featured: false, followed: true }) as DirectoryEntry)
  const all = searching ? listed : [...extraWatched, ...listed]
  const shown = all
    .filter((e) =>
      filter === 'All'
        ? true
        : filter === 'Watching'
          ? watchedKeys.has(keyOf(e))
          : e.kind === TYPES[filter],
    )
    .sort(
      sort === 'featured'
        ? (a, b) => Number(watchedKeys.has(keyOf(b))) - Number(watchedKeys.has(keyOf(a)))
        : bySort(sort),
    )
  // Every filter, always: one with no match while searching shows 0 rather than vanishing.
  const filters: Filter[] = [
    'All',
    ...(watched.length ? (['Watching'] as Filter[]) : []),
    ...(Object.keys(TYPES) as Filter[]),
  ]

  const countOf = (f: Filter) =>
    f === 'All'
      ? all.length
      : f === 'Watching'
        ? watched.length
        : all.filter((e) => e.kind === TYPES[f]).length
  return (
    <section className="fol-sec" aria-label={COPY.discoverTitle}>
      {/* Investments' toolbar: the search first, filling the row, the sort beside it; the
          filters as pills under them. Nothing left of the search ever changes, so it never
          moves while you type. */}
      <div className="fol-discover-bar">
        <SearchBox
          value={typed}
          onChange={setTyped}
          label={COPY.searchLabel}
          placeholder={COPY.gallerySearch}
        />
        <ToolSelect
          label={COPY.sortLabel}
          value={sort}
          onChange={(v) => {
            if (isSort(v)) setSort(v)
          }}
          options={SORTS.map((o) => ({ value: o.value, label: o.label }))}
        />
      </div>
      <FilterChips
        label={COPY.filterLabel}
        value={filter}
        onChange={setFilter}
        options={filters.map((f) => ({ value: f, label: f, count: countOf(f) }))}
      />
      {!directory.data && directory.error != null && (
        <ErrorState
          title={COPY.searchErrorTitle}
          message={COPY.searchErrorBody}
          onRetry={() => void directory.refresh()}
        />
      )}
      {searching && directory.data && shown.length === 0 && (
        <p className="fol-by">{COPY.noMatch(directory.q)}</p>
      )}
      <div className="fol-cards">
        {shown.map((entry) => (
          <PortfolioCard key={keyOf(entry)} entry={entry} onOpen={() => onOpen(entry)} />
        ))}
      </div>
    </section>
  )
}
