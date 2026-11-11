import { useState } from 'react'
import { useNarrow } from './useNarrow'
import { ErrorState, SegmentedControl, Select, TextField } from '../../ui'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { FilterSheet } from '../../tabs/investments/FilterSheet'
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

// Filter + sort + search fit side by side from here.
const WIDE_BAR_PX = 820

const keyOf = (e: PortfolioRef) => `${e.kind}:${e.source_ref}`

/** Famous portfolios, as cards to browse. Search sits in place; there is no dialog. */
export function Discover({ watched, onOpen }: Props) {
  const [filter, setFilter] = useState<Filter>('All')
  const [typed, setTyped] = useState('')
  const [sort, setSort] = useState<Sort>('featured')
  const isMobile = useIsMobile()
  // The compact row ([search | Filter]) whenever the bar itself is too narrow for the filter,
  // the sort and the search side by side, not only on a phone.
  const [barRef, narrow] = useNarrow<HTMLDivElement>(WIDE_BAR_PX)
  const compact = isMobile || narrow
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
  const filters: Filter[] = [
    'All',
    ...(watched.length ? (['Watching'] as Filter[]) : []),
    ...(Object.keys(TYPES) as Filter[]).filter((t) =>
      all.some((e) => e.kind === TYPES[t as keyof typeof TYPES]),
    ),
  ]

  const countOf = (f: Filter) =>
    f === 'All'
      ? all.length
      : f === 'Watching'
        ? watched.length
        : all.filter((e) => e.kind === TYPES[f]).length
  const search = (
    <TextField
      className="fol-find"
      type="search"
      aria-label={COPY.searchLabel}
      placeholder={COPY.gallerySearch}
      value={typed}
      onChange={(e) => setTyped(e.target.value)}
    />
  )
  return (
    <section className="fol-sec" aria-label={COPY.discoverTitle}>
      {/* One row, the controls together. Wide: filters, sort, search side by side at one
          height. Phone (Investments' pattern): [search (fills) | Filter], the filter and the
          sort in its sheet. */}
      <div ref={barRef} className="fol-discover-bar">
        {compact ? (
          <>
            {search}
            <FilterSheet
              presentation={isMobile ? 'sheet' : 'popover'}
              title={COPY.filterTitle}
              active={filter !== 'All' || sort !== 'featured'}
              groups={[
                {
                  title: COPY.showLabel,
                  options: filters.map((f) => ({ id: f, label: f, count: countOf(f) })),
                  value: filter,
                  onChange: (id) => setFilter(id as Filter),
                },
                {
                  title: COPY.sortTitle,
                  options: SORTS.map((o) => ({ id: o.value, label: o.label })),
                  value: sort,
                  onChange: (id) => {
                    if (isSort(id)) setSort(id)
                  },
                },
              ]}
            />
          </>
        ) : (
          <>
            <SegmentedControl
              options={filters.map((f) => ({
                value: f,
                label: (
                  <>
                    {f} <span className="fol-seg-n">{countOf(f)}</span>
                  </>
                ),
              }))}
              value={filter}
              onChange={setFilter}
              ariaLabel={COPY.filterLabel}
            />
            <Select
              className="fol-sort"
              aria-label={COPY.sortLabel}
              value={sort}
              options={SORTS.map((o) => ({ value: o.value, label: o.label }))}
              onChange={(e) => {
                if (isSort(e.target.value)) setSort(e.target.value)
              }}
            />
            {search}
          </>
        )}
      </div>
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
