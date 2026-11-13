import { useCallback, useEffect, useState } from 'react'
import { ErrorState, Skeleton } from '../../ui'
import { FilterChips, SearchBox, ToolSelect } from './Controls'
import type { DirectoryEntry } from '../../../api/followedPortfolios'
import { COPY } from './following.copy'
import { PortfolioCard } from './PortfolioCard'
import { preloadPortfolioPictures } from './portfolioPhotos'
import { useDirectory } from './useFollowing'
import { useSlow } from './useSlow'
import { FetchBanner } from './FirstLoad'

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

/** The server's rule, in the browser: a word of the name (or whose it is) starts with what was
 *  typed — "nv" is NVIDIA, never "Investment". */
function matches(term: string, e: PortfolioRef): boolean {
  const q = term.toLowerCase()
  return `${e.name} ${e.subtitle}`
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .some((w) => w && w.startsWith(q))
}

/** Famous portfolios, as cards to browse. Search sits in place; there is no dialog. */
export function Discover({ watched, onOpen }: Props) {
  const [filter, setFilter] = useState<Filter>('All')
  const [typed, setTyped] = useState('')
  const [sort, setSort] = useState<Sort>('featured')
  useEffect(preloadPortfolioPictures, [])
  // The cards still waiting long, so one banner says why instead of every card.
  const [slowCards, setSlowCards] = useState<ReadonlySet<string>>(new Set())
  const markSlow = useCallback((key: string, slow: boolean) => {
    setSlowCards((prev) => {
      if (prev.has(key) === slow) return prev
      const next = new Set(prev)
      if (slow) next.add(key)
      else next.delete(key)
      return next
    })
  }, [])
  // The featured list is always on hand, so a search shows its matches the moment you type; the
  // live search (SEC's filers, House members, ETFs) answers seconds later and adds what it finds.
  const featured = useDirectory('')
  const directory = useDirectory(typed)
  const term = typed.trim()
  const searching = term.length >= 2
  const local = searching ? (featured.data ?? []).filter((e) => matches(term, e)) : []
  const live = searching && directory.q === term ? (directory.data ?? []) : []
  // Still looking: the typing has not settled, or the live search has not answered it yet.
  const looking =
    searching && (directory.q !== term || (!directory.data && directory.error == null))
  const slowSearch = useSlow(looking)
  const listed = searching
    ? [...local, ...live.filter((e) => !local.some((l) => keyOf(l) === keyOf(e)))]
    : (featured.data ?? [])
  const watchedKeys = new Set(watched.map(keyOf))
  const extraWatched = watched
    .filter((w) => !listed.some((e) => keyOf(e) === keyOf(w)))
    .map((w) => ({ ...w, featured: false, followed: true, ready: true }) as DirectoryEntry)
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
      {!featured.data && featured.error != null && !searching && (
        <ErrorState
          title={COPY.searchErrorTitle}
          message={COPY.searchErrorBody}
          onRetry={() => void featured.refresh()}
        />
      )}
      {searching && !looking && directory.error != null && local.length === 0 && (
        <ErrorState
          title={COPY.searchErrorTitle}
          message={COPY.searchErrorBody}
          onRetry={() => void directory.refresh()}
        />
      )}
      {searching && !looking && directory.error == null && shown.length === 0 && (
        <p className="fol-by">{COPY.noMatch(term)}</p>
      )}
      <div className="fol-cards">
        {shown.map((entry) => (
          <PortfolioCard
            key={keyOf(entry)}
            entry={entry}
            onOpen={() => onOpen(entry)}
            onSlow={(slow) => markSlow(keyOf(entry), slow)}
          />
        ))}
        {/* What the live search may still add: cards to come, not an empty page. */}
        {looking &&
          [0, 1].map((i) => (
            <div key={`skel-${i}`} className="fol-card fol-card--skel" aria-hidden="true">
              <div className="fol-card-main">
                <Skeleton height={18} width="45%" />
                <Skeleton height={12} width="30%" />
                <div className="fol-card-skel">
                  <Skeleton height={28} width="55%" />
                  <Skeleton height={14} width="70%" />
                  <Skeleton height={14} width="60%" />
                </div>
              </div>
              <div className="fol-card-ring">
                <Skeleton circle width={160} height={160} />
              </div>
            </div>
          ))}
      </div>
      {slowSearch ? (
        <FetchBanner title={COPY.searchSlowTitle} sub={COPY.searchSlowSub} />
      ) : (
        slowCards.size > 0 && (
          <FetchBanner title={COPY.fetchCardsTitle(slowCards.size)} sub={COPY.fetchCardsSub} />
        )
      )}
    </section>
  )
}
